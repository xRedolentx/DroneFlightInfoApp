"""Resolve an unresolved flight's identity, with evidence and a stored decision.

The reviewed build's "Keep / merge" button only navigated to the details page:
there was no mutation behind it. This module gives the owner the evidence and a
deliberate, reversible choice. It deliberately does not implement an automatic
merge: deciding that two records are one flight needs the owner's knowledge, and
a wrong merge discards an original's separate history.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import db, paths
from .importer import _parse_iso

DECISIONS = ("separate", "exclude", "include")
_LABELS = {
    "separate": "These are separate flights",
    "exclude": "Leave excluded, with a reason",
    "include": "Count this record",
}


def _flight(conn: sqlite3.Connection, flight_id: int) -> sqlite3.Row | None:
    return db.one(conn, "SELECT * FROM flights WHERE id=?", (flight_id,))


def _evidence(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    files = db.all_rows(
        conn,
        "SELECT rf.id, rf.original_name, rf.sha256, rf.size_bytes, ff.role "
        "FROM flight_files ff JOIN raw_files rf ON rf.id=ff.raw_file_id "
        "WHERE ff.flight_id=? ORDER BY ff.role, rf.id",
        (int(row["id"]),),
    )
    return {
        "id": int(row["id"]),
        "identity_status": row["identity_status"],
        "excluded_reason": row["excluded_reason"],
        "aircraft_name": row["aircraft_name"],
        "aircraft_sn_present": bool(row["aircraft_sn"]),
        "start_time_utc": row["start_time_utc"],
        "duration_s": row["duration_s"],
        "total_distance_m": row["total_distance_m"],
        "pilot": row["pilot"],
        "notes": row["notes"],
        "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
        "sample_count": int(row["sample_count"] or 0),
        "files": [
            {"raw_id": int(f["id"]), "name": f["original_name"],
             "sha256_prefix": str(f["sha256"])[:12], "size": int(f["size_bytes"] or 0),
             "role": f["role"]}
            for f in files
        ],
    }


def candidates(conn: sqlite3.Connection, flight_id: int) -> list[dict[str, Any]]:
    """Other flights this one could plausibly be the same as.

    Matching is conservative and local: the same fingerprint, or the same
    aircraft identity with a start within three seconds. It is a shortlist for a
    human, not a merge proposal.
    """
    row = _flight(conn, flight_id)
    if row is None:
        return []
    peers: dict[int, sqlite3.Row] = {}
    if row["fingerprint"]:
        for other in db.all_rows(
                conn, "SELECT * FROM flights WHERE fingerprint=? AND id!=?",
                (row["fingerprint"], flight_id)):
            peers[int(other["id"])] = other
    start = _parse_iso(row["start_time_utc"])
    if start is not None:
        for other in db.all_rows(
                conn,
                "SELECT * FROM flights WHERE id!=? AND identity_status!='excluded' AND ("
                " (aircraft_sn IS NOT NULL AND aircraft_sn=?) OR "
                " (aircraft_sn IS NULL AND aircraft_name IS NOT NULL AND aircraft_name=?)) "
                "ORDER BY ABS(julianday(start_time_utc)-julianday(?)) LIMIT 8",
                (flight_id, row["aircraft_sn"], row["aircraft_name"], row["start_time_utc"])):
            other_start = _parse_iso(other["start_time_utc"])
            if other_start is None:
                continue
            if abs((other_start - start).total_seconds()) <= 3.0:
                peers[int(other["id"])] = other
    out = []
    for peer in peers.values():
        evidence = _evidence(conn, peer)
        evidence["same_fingerprint"] = bool(row["fingerprint"]
                                            and peer["fingerprint"] == row["fingerprint"])
        out.append(evidence)
    return sorted(out, key=lambda item: item["start_time_utc"] or "")


def review(conn: sqlite3.Connection, flight_id: int) -> dict[str, Any]:
    row = _flight(conn, flight_id)
    if row is None:
        return {"ok": False, "code": "not_found"}
    stored = db.one(conn, "SELECT * FROM identity_decisions WHERE flight_id=?",
                    (flight_id,))
    return {
        "ok": True,
        "flight": _evidence(conn, row),
        "candidates": candidates(conn, flight_id),
        "decision": ({"decision": stored["decision"], "note": stored["note"],
                      "updated_at": stored["updated_at"]} if stored else None),
        "options": [{"value": value, "label": _LABELS[value]} for value in DECISIONS],
    }


def _has_usable_evidence(row: sqlite3.Row) -> bool:
    """Can this record stand as its own flight?

    A start time is the minimum. A record whose details block could not be read
    has no measurements, and dismissing its warning must not manufacture any: it
    stays excluded whatever the owner says about identity, until a parse
    succeeds.
    """
    if row["excluded_reason"] == "details_unreadable":
        return False
    return bool(row["start_time_utc"] or row["filename_time"])


def decide(conn: sqlite3.Connection, flight_id: int, choice: str,
           note: str | None = None) -> dict[str, Any]:
    if choice not in DECISIONS:
        return {"ok": False, "code": "bad_decision"}
    row = _flight(conn, flight_id)
    if row is None:
        return {"ok": False, "code": "not_found"}
    now = db.now_utc()
    with db.tx(conn):
        existing = db.one(conn, "SELECT * FROM identity_decisions WHERE flight_id=?",
                          (flight_id,))
        previous_status = (existing["previous_status"] if existing
                           else row["identity_status"])
        previous_reason = (existing["previous_reason"] if existing
                           else row["excluded_reason"])
        if choice == "separate":
            if _has_usable_evidence(row):
                conn.execute(
                    "UPDATE flights SET identity_status='authoritative', "
                    "excluded_reason=NULL, updated_at=? WHERE id=?", (now, flight_id))
            # else: leave it unresolved; the decision says "separate", not
            # "trustworthy".
        elif choice == "exclude":
            conn.execute(
                "UPDATE flights SET identity_status='excluded', "
                "excluded_reason='owner_excluded', updated_at=? WHERE id=?", (now, flight_id))
        elif choice == "include":
            if _has_usable_evidence(row):
                conn.execute(
                    "UPDATE flights SET identity_status='authoritative', "
                    "excluded_reason=NULL, updated_at=? WHERE id=?", (now, flight_id))
            else:
                return {"ok": False, "code": "insufficient_evidence"}
        conn.execute(
            "INSERT INTO identity_decisions(flight_id,decision,note,previous_status,"
            "previous_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(flight_id) DO UPDATE SET decision=excluded.decision,"
            "note=excluded.note,updated_at=excluded.updated_at",
            (flight_id, choice, note, previous_status, previous_reason, now, now),
        )
        db.audit(conn, "owner", "identity_decision", "flight", str(flight_id),
                 {"decision": choice})
    return review(conn, flight_id)


def undo(conn: sqlite3.Connection, flight_id: int) -> dict[str, Any]:
    """Remove the owner's decision and restore the status it replaced."""
    stored = db.one(conn, "SELECT * FROM identity_decisions WHERE flight_id=?",
                    (flight_id,))
    if stored is None:
        return {"ok": False, "code": "no_decision"}
    with db.tx(conn):
        conn.execute(
            "UPDATE flights SET identity_status=?, excluded_reason=?, updated_at=? "
            "WHERE id=?",
            (stored["previous_status"], stored["previous_reason"], db.now_utc(), flight_id))
        conn.execute("DELETE FROM identity_decisions WHERE flight_id=?", (flight_id,))
        db.audit(conn, "owner", "identity_undo", "flight", str(flight_id), {})
    return review(conn, flight_id)


def has_decision(conn: sqlite3.Connection, flight_id: int) -> str | None:
    row = db.one(conn, "SELECT decision FROM identity_decisions WHERE flight_id=?",
                 (flight_id,))
    return row["decision"] if row else None
