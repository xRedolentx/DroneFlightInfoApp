"""Fingerprint repair and the multi-file report that goes with it.

The fingerprint is a derived identity: `aircraft | start | duration`. It went
stale after a reparse (the metrics update never rewrote it), and before the
2026-11-21 fix an unreadable log was given the fingerprint of `"||0.0"`, so two
unrelated unreadable logs shared one identity.

This module recomputes the stored fingerprint from the columns the row actually
holds, and reports — without touching anything — any flight that claims more
than one raw file whose file names disagree about the date. That disagreement
is not automatically wrong: a re-saved log keeps its start time in the name, so
two names for one flight should be identical. When they differ, the link
deserves a look.

Nothing here writes unless `apply=True`, and nothing here touches the live data
root on its own. `tools/repair_fingerprints.py` is the dry-run first.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db
from .importer import _fingerprint, _parse_iso, parse_filename_time


def _row_fingerprint(row: sqlite3.Row) -> str | None:
    return _fingerprint(
        row["aircraft_sn"],
        row["start_time_utc"],
        row["duration_s"],
        row["filename_time"],
        row["aircraft_name"],
    )


def recompute(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Recompute every stored fingerprint. Report what changed."""
    rows = db.all_rows(conn, "SELECT * FROM flights ORDER BY id")
    changes: list[dict[str, Any]] = []
    for row in rows:
        new = _row_fingerprint(row)
        old = row["fingerprint"]
        if new != old:
            changes.append({"id": int(row["id"]), "old": old, "new": new})
    if apply and changes:
        with db.tx(conn):
            for change in changes:
                conn.execute(
                    "UPDATE flights SET fingerprint=? WHERE id=?",
                    (change["new"], change["id"]),
                )
    # Two flights must never end up sharing a fingerprint: the importer's
    # lookup takes the first row it finds, so a duplicate would silently hide
    # one flight behind another.
    seen: dict[str, int] = {}
    collisions: list[dict[str, Any]] = []
    for row in db.all_rows(conn, "SELECT id,fingerprint FROM flights WHERE fingerprint IS NOT NULL"):
        fp = str(row["fingerprint"])
        if fp in seen:
            collisions.append({"fingerprint": fp, "ids": [seen[fp], int(row["id"])]})
        else:
            seen[fp] = int(row["id"])
    return {
        "checked": len(rows),
        "changed": len(changes),
        "applied": bool(apply),
        "changes": changes,
        "collisions": collisions,
        "multi_file_mismatches": multi_file_mismatches(conn),
    }


def _classify(spread_s: float) -> str:
    """What a filename-date disagreement probably means.

    Measured on the live archive: most multi-file flights disagree by one or two
    seconds, or by exactly two hours, because the two controllers write the name
    in different time zones. Those are naming differences, not evidence that two
    different flights were merged — the link itself was made on the decoded start
    times, which agree to within three seconds. Only a spread that is neither
    tiny nor a whole number of hours deserves a second look.
    """
    if spread_s == 0:
        return "same_instant"
    if spread_s <= 60:
        return "clock_skew"
    nearest_hour = round(spread_s / 3600.0) * 3600
    if nearest_hour and abs(spread_s - nearest_hour) <= 60:
        return "timezone_offset"
    return "different"


def multi_file_mismatches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Flights with more than one raw file whose name-derived dates disagree.

    Reported, never repaired: deciding which files belong together needs the
    owner's knowledge of what was flown, not a heuristic. Each row carries the
    spread in seconds and a `kind`, so the caller can separate a timezone
    difference from a genuine contradiction.
    """
    rows = db.all_rows(
        conn,
        """SELECT ff.flight_id AS flight_id, rf.id AS raw_id, rf.original_name AS name
             FROM flight_files ff JOIN raw_files rf ON rf.id=ff.raw_file_id
            ORDER BY ff.flight_id, rf.id""",
    )
    by_flight: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_flight.setdefault(int(row["flight_id"]), []).append(
            {
                "raw_id": int(row["raw_id"]),
                "name": row["name"],
                "filename_time": parse_filename_time(row["name"] or ""),
            }
        )
    out: list[dict[str, Any]] = []
    for flight_id, files in sorted(by_flight.items()):
        if len(files) < 2:
            continue
        stamps = {f["filename_time"] for f in files if f["filename_time"]}
        parsed = [dt for dt in (_parse_iso(s) for s in stamps) if dt is not None]
        if len(parsed) < 2:
            continue
        spread = (max(parsed) - min(parsed)).total_seconds()
        out.append({"flight_id": flight_id, "files": files,
                    "spread_seconds": spread, "kind": _classify(spread)})
    return out
