"""Maintenance rules, snapshot baselines and auditable service actions."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import db

STATUS_ORDER = {"due": 3, "due_soon": 2, "unknown": 1, "ok": 0}
STATUS_LABELS = {"due": "Due", "due_soon": "Due soon", "unknown": "Unknown", "ok": "OK"}
SERVICE_KINDS = ("completed", "skipped", "oneoff")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def latest_snapshot(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return db.one(
        conn,
        "SELECT * FROM service_snapshots ORDER BY covered_through DESC, id DESC LIMIT 1",
    )


def _snapshot_end(snapshot: sqlite3.Row | None) -> datetime | None:
    if snapshot is None:
        return None
    return _parse_dt(snapshot["covered_through"] + "T23:59:59+00:00")


def latest_rule_service(conn: sqlite3.Connection, rule: sqlite3.Row) -> sqlite3.Row | None:
    return db.one(
        conn,
        "SELECT * FROM services WHERE reversed_by_id IS NULL AND occurred_at IS NOT NULL "
        "AND (kind IN ('completed','skipped') OR (kind='oneoff' AND linked_rule_id IS NOT NULL)) "
        "AND equipment_id=? AND (rule_id=? OR linked_rule_id=?) "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (rule["equipment_id"], rule["id"], rule["id"]),
    )


def _flight_rows(conn: sqlite3.Connection, equipment_id: int, kind: str) -> list[dict[str, Any]]:
    column = "aircraft_equipment_id" if kind == "aircraft" else "battery_equipment_id"
    rows = db.all_rows(
        conn,
        f"""SELECT f.id, f.start_time_utc, f.duration_s, f.identity_status,
                   GROUP_CONCAT(DISTINCT sm.source_kind) AS kinds
            FROM flights f
            JOIN flight_files ff ON ff.flight_id=f.id
            JOIN source_members sm ON sm.raw_file_id=ff.raw_file_id
            WHERE f.{column}=? AND f.identity_status='authoritative'
              AND f.start_time_utc IS NOT NULL
            GROUP BY f.id ORDER BY f.start_time_utc""",
        (equipment_id,),
    )
    return [dict(r) for r in rows]


def _flight_kinds(row: dict[str, Any]) -> set[str]:
    raw = row.get("kinds") or ""
    return {k for k in str(raw).split(",") if k}


def _archive_only(row: dict[str, Any]) -> bool:
    kinds = _flight_kinds(row)
    return "archive" in kinds and not (kinds & {"extras", "manual", "usb"})


def _resolutions(conn: sqlite3.Connection, snapshot_id: int, flight_id: int) -> str | None:
    row = db.one(
        conn,
        "SELECT decision FROM cover_resolutions WHERE flight_id=? AND snapshot_id=?",
        (flight_id, snapshot_id),
    )
    return row["decision"] if row else None


def battery_cycle_stats(conn: sqlite3.Connection, battery_id: int,
                        after_iso: str | None) -> dict[str, Any]:
    rows = db.all_rows(
        conn,
        "SELECT at,cycles FROM battery_readings WHERE battery_id=? AND cycles IS NOT NULL "
        "ORDER BY at,id",
        (battery_id,),
    )
    if len(rows) < 2:
        return {"available": False, "reason": "missing_cycle_history"}
    after = _parse_dt(after_iso) if after_iso else None
    usable = [r for r in rows if _parse_dt(r["at"]) and (after is None or _parse_dt(r["at"]) >= after)]
    if len(usable) < 2:
        return {"available": False, "reason": "insufficient_cycle_history"}
    total = 0
    replacements = 0
    prev = int(usable[0]["cycles"])
    for row in usable[1:]:
        cur = int(row["cycles"])
        if cur < prev:
            replacements += 1
        else:
            total += cur - prev
        prev = cur
    return {
        "available": True,
        "charges": total,
        "replacements": replacements,
        "readings": len(usable),
        "from": usable[0]["at"],
        "to": usable[-1]["at"],
    }


def _status_for(value: float | None, limit: float | None,
                due_soon_pct: float) -> tuple[str, float | None]:
    if value is None or limit is None or limit <= 0:
        return "unknown", None
    ratio = value / limit
    if value >= limit:
        return "due", ratio
    if value >= due_soon_pct * limit:
        return "due_soon", ratio
    return "ok", ratio


def _service_items(rule: sqlite3.Row) -> list[str]:
    from .schedule import service_items

    return service_items(rule)


def _rule_source(rule: sqlite3.Row) -> str:
    from .schedule import rule_source

    return rule_source(rule)


def _days_since_baseline(conn: sqlite3.Connection, rule: sqlite3.Row,
                         base_at: "datetime | None") -> tuple[int | None, str]:
    """Days elapsed since this rule's baseline, and where that baseline came from.

    The baseline is whatever the counters use: the last completed or skipped
    service, else the captured snapshot date, else the rule's own creation. A
    missing baseline is reported as unknown rather than as zero days.
    """
    start = base_at
    reason = "service"
    if start is None:
        for field, why in (("captured_baseline_at", "capture"), ("created_at", "rule_created")):
            if field not in rule.keys():
                continue
            parsed = _parse_dt(rule[field])
            if parsed is not None:
                start = parsed
                reason = why
                break
    if start is None:
        return None, "no_baseline"
    now = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    days = (now - start).days
    return (max(0, days), reason)


def _normalize_captured(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if "soon" in v:
        return "due_soon"
    if "due" in v or "over" in v:
        return "due"
    if v in ("ok", "good", "fine", "clear"):
        return "ok"
    return None


def rule_status(conn: sqlite3.Connection, rule: sqlite3.Row,
                snapshot: sqlite3.Row | None = None) -> dict[str, Any]:
    if snapshot is None:
        snapshot = latest_snapshot(conn)
    equipment = db.one(conn, "SELECT * FROM equipment WHERE id=?", (rule["equipment_id"],))
    if equipment is None:
        return {"id": rule["id"], "status": "unknown", "counters": [], "error": "equipment_missing"}
    kind = equipment["kind"]
    snap_end = _snapshot_end(snapshot)
    service = latest_rule_service(conn, rule)
    service_dt = _parse_dt(service["occurred_at"]) if service else None
    snap_counters = db.all_rows(
        conn,
        "SELECT * FROM snapshot_counters WHERE snapshot_id=? AND equipment_id=? AND rule_code=?",
        (snapshot["id"], rule["equipment_id"], rule["code"]),
    ) if snapshot is not None else []
    snap_by_metric = {r["metric"]: r for r in snap_counters}

    basis = "all_logged"
    base_at: datetime | None = None
    base_values: dict[str, float] = {"flights": 0.0, "minutes": 0.0, "charges": 0.0}
    basis_rounded = False

    # A rule added after the capture was taken cannot be covered by it: the
    # snapshot describes services and counters that existed at capture time.
    # Counting the whole history for such a rule would report "overdue since
    # records began" for a check nobody has ever performed, so its clock starts
    # when the rule was added and the card says so.
    # Timestamps cannot tell these apart: the capture writes its rules and its
    # snapshot in the same transaction, so both carry "now". Provenance can: a
    # rule whose source is AirData came from the capture and is covered by it.
    rule_created = _parse_dt(rule["created_at"]) if "created_at" in rule.keys() else None
    rule_postdates_capture = _rule_source(rule) != "AirData"

    if service is not None and (snap_end is None or (service_dt and service_dt > snap_end)):
        basis = "service"
        base_at = service_dt
    elif rule_postdates_capture:
        basis = "rule_added"
        base_at = rule_created
    elif snapshot is not None:
        if snap_by_metric:
            basis = "snapshot"
            base_at = snap_end
            basis_rounded = any(int(r["rounded"]) for r in snap_by_metric.values())
            for metric, row in snap_by_metric.items():
                base_values[metric] = float(row["value"])
        else:
            basis = "snapshot_missing_counter"
            base_at = snap_end
    elif service is not None:
        basis = "service"
        base_at = service_dt

    flights = _flight_rows(conn, int(rule["equipment_id"]), kind)
    after: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    covered = 0
    included_uncertain = 0
    if basis in ("snapshot", "snapshot_missing_counter"):
        for row in flights:
            dt = _parse_dt(row["start_time_utc"])
            if dt is None:
                continue
            if base_at is not None and dt > base_at:
                after.append(row)
            elif snapshot is not None and _archive_only(row):
                covered += 1
            else:
                decision = _resolutions(conn, int(snapshot["id"]), int(row["id"])) if snapshot else None
                if decision == "include":
                    included_uncertain += 1
                    after.append(row)
                elif decision == "covered":
                    covered += 1
                else:
                    uncertain.append(row)
    elif base_at is not None:
        after = [r for r in flights if (_parse_dt(r["start_time_utc"]) or datetime.min.replace(tzinfo=timezone.utc)) > base_at]
    else:
        after = flights

    def minutes(rows: list[dict[str, Any]]) -> tuple[float, int]:
        total = 0.0
        missing = 0
        for row in rows:
            if row.get("duration_s") is None:
                missing += 1
            else:
                total += float(row["duration_s"]) / 60.0
        return total, missing

    after_minutes, missing_duration = minutes(after)
    uncertain_minutes, _ = minutes(uncertain)
    cycle = battery_cycle_stats(conn, int(rule["equipment_id"]), _iso(base_at) if base_at else None) \
        if kind == "battery" else {"available": False, "reason": "not_a_battery"}
    charge_basis = "cycle_counter" if cycle.get("available") else "logged_flights"

    values: dict[str, float | None] = {
        "flights": base_values["flights"] + len(after),
        "minutes": base_values["minutes"] + after_minutes,
        "charges": None,
    }
    if kind == "battery":
        if cycle.get("available"):
            values["charges"] = base_values["charges"] + float(cycle["charges"])
        else:
            values["charges"] = base_values["charges"] + len(after)
    if basis == "snapshot_missing_counter":
        # A snapshot exists but did not publish this rule's counter. Do not
        # turn the covered history into a silent zero.
        for metric in ("flights", "minutes", "charges"):
            values[metric] = None
    elif basis == "snapshot":
        # Per-metric absence is still absence: the snapshot covered history
        # we cannot reconstruct, so an unpublished counter is unknown.
        for metric in ("flights", "minutes", "charges"):
            if metric not in snap_by_metric:
                values[metric] = None
    if basis == "all_logged" and not flights:
        for metric in ("flights", "minutes", "charges"):
            values[metric] = 0.0 if metric != "charges" or kind == "battery" else None

    limits = {
        "flights": rule["flights_limit"],
        "minutes": rule["air_minutes_limit"],
        "charges": rule["charges_limit"],
    }
    days_limit = rule["days_limit"] if "days_limit" in rule.keys() else None
    due_soon_pct = float(rule["due_soon_pct"] or 0.8)
    counters = []
    fired = None
    fired_ratio = -1.0
    for metric in ("flights", "minutes", "charges"):
        limit = limits[metric]
        if limit is None and values.get(metric) is None:
            continue
        value = values.get(metric)
        status, ratio = _status_for(value, limit, due_soon_pct)
        if ratio is not None and ratio > fired_ratio:
            fired_ratio = ratio
            fired = metric
        counters.append({
            "metric": metric,
            "value": value,
            "limit": limit,
            "ratio": ratio,
            "status": status,
            "rounded": bool(basis == "snapshot" and metric in snap_by_metric
                            and int(snap_by_metric[metric]["rounded"])),
            "captured_raw": snap_by_metric[metric]["raw_text"] if metric in snap_by_metric else None,
            "missing_duration": missing_duration if metric == "minutes" else 0,
        })
    # The calendar trigger. AirData models this as "Time-Based Days"; it is
    # measured from the same baseline as the counters, so a completed service
    # resets the clock as well as the odometer.
    if days_limit:
        days_value, days_basis = _days_since_baseline(conn, rule, base_at)
        status, ratio = _status_for(days_value, float(days_limit), due_soon_pct)
        if ratio is not None and ratio > fired_ratio:
            fired_ratio = ratio
            fired = "days"
        counters.append({
            "metric": "days",
            "value": days_value,
            "limit": days_limit,
            "ratio": ratio,
            "status": status,
            "rounded": False,
            "captured_raw": None,
            "missing_duration": 0,
            "basis": days_basis,
        })
    if values.get("minutes") is not None and missing_duration:
        counters_minutes = next((c for c in counters if c["metric"] == "minutes"), None)
        if counters_minutes:
            counters_minutes["partial"] = True

    captured_status = _normalize_captured(rule["captured_status"])
    # A missing counter does not make a rule "unknown" if another counter is
    # known: it is shown as its own unknown row and flagged separately.
    known = [c for c in counters if c["status"] != "unknown"]
    worst = (max(known, key=lambda c: STATUS_ORDER[c["status"]])["status"]
             if known else "unknown")
    has_unknown = any(c["status"] == "unknown" for c in counters)

    baseline_info = {
        "basis": basis,
        "at": _iso(base_at) if base_at else None,
        "service_id": int(service["id"]) if service and basis == "service" else None,
        "snapshot_id": int(snapshot["id"]) if snapshot and basis.startswith("snapshot") else None,
        "captured_text": rule["captured_baseline_text"],
        "rounded": basis_rounded,
        "charge_basis": charge_basis,
        "cycle": cycle,
    }
    last_service = None
    if service is not None:
        last_service = {
            "id": int(service["id"]),
            "kind": service["kind"],
            "at": service["occurred_at"],
            "notes": service["notes"],
            "source": service["source"],
        }
    elif rule["captured_baseline_text"]:
        last_service = {
            "id": None,
            "kind": "snapshot",
            "at": rule["captured_baseline_at"],
            "notes": "Captured baseline: " + (rule["captured_baseline_text"] or ""),
            "source": "capture",
        }
    return {
        "id": int(rule["id"]),
        "equipment_id": int(rule["equipment_id"]),
        "equipment_name": equipment["name"],
        "equipment_kind": kind,
        "equipment_serial": equipment["serial"],
        "archived": bool(equipment["archived"]),
        "placeholder": bool(equipment["placeholder"]),
        "code": rule["code"],
        "label": rule["label"],
        "enabled": bool(rule["enabled"]),
        "due_soon_pct": due_soon_pct,
        "status": worst,
        "fired_metric": fired,
        "counters": counters,
        "has_unknown_counter": has_unknown,
        "captured_status": rule["captured_status"],
        "captured_counter_text": rule["captured_counter_text"],
        "captured_status_normalized": captured_status,
        "matches_capture": captured_status == worst if captured_status else None,
        # The raw thresholds, so an editing form can show what is actually
        # stored. Without these a form would open blank and saving it would
        # erase every counter it could not see.
        "flights_limit": rule["flights_limit"],
        "air_minutes_limit": rule["air_minutes_limit"],
        "charges_limit": rule["charges_limit"],
        "days_limit": days_limit,
        "service_items": _service_items(rule),
        "source": _rule_source(rule),
        "baseline": baseline_info,
        "last_service": last_service,
        "covered_flights": covered,
        "new_flights": len(after),
        "uncertain_count": len(uncertain),
        "uncertain_minutes": uncertain_minutes,
        "included_uncertain": included_uncertain,
        "proposed_delta": {
            "flights": len(uncertain),
            "minutes": round(uncertain_minutes, 3),
            "charges": len(uncertain) if kind == "battery" else 0,
        },
        "explanation": _explain(worst, counters, fired, last_service, basis),
    }


def _explain(worst: str, counters: list[dict], fired: str | None,
             last_service: dict | None, basis: str) -> str:
    if worst == "unknown":
        if basis == "snapshot_missing_counter":
            return ("The capture has no counter for this rule, so the starting count is "
                    "unknown rather than zero.")
        return "No counter is available yet; nothing is treated as zero."
    if worst == "ok":
        if any(c["status"] == "unknown" for c in counters):
            return "Known counters are below the due-soon threshold; one counter is unknown."
        return "All enabled counters are below the due-soon threshold."
    metric = next((c for c in counters if c["metric"] == fired), counters[0] if counters else None)
    if metric is None:
        return STATUS_LABELS[worst]
    unit = {"flights": "flights", "minutes": "air minutes", "charges": "charges",
            "days": "days"}.get(metric["metric"], metric["metric"])
    text = f"{metric['value']:.0f} of {metric['limit']:.0f} {unit}"
    if last_service and last_service.get("at"):
        text += f"; baseline {last_service['at']}"
    return text


def all_statuses(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[dict[str, Any]]:
    snapshot = latest_snapshot(conn)
    rows = db.all_rows(
        conn,
        "SELECT r.* FROM rules r JOIN equipment e ON e.id=r.equipment_id "
        "WHERE e.placeholder=0" + ("" if include_archived else " AND e.archived=0") +
        " ORDER BY e.kind DESC, e.name, r.code",
    )
    return [rule_status(conn, r, snapshot) for r in rows]


def equipment_status(conn: sqlite3.Connection, equipment_id: int) -> dict[str, Any]:
    snapshot = latest_snapshot(conn)
    eq = db.one(conn, "SELECT * FROM equipment WHERE id=?", (equipment_id,))
    if eq is None:
        return {"ok": False, "code": "not_found"}
    rules = db.all_rows(conn, "SELECT * FROM rules WHERE equipment_id=? ORDER BY code",
                        (equipment_id,))
    return {
        "ok": True,
        "equipment": dict(eq),
        "rules": [rule_status(conn, r, snapshot) for r in rules],
    }


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    statuses = all_statuses(conn)
    upcoming = [s for s in statuses if s["enabled"] and s["status"] in ("due", "due_soon")]
    aircraft = [s for s in upcoming if s["equipment_kind"] == "aircraft"]
    batteries = [s for s in upcoming if s["equipment_kind"] == "battery"]
    services = db.all_rows(
        conn,
        """SELECT s.*, e.name AS equipment_name, e.kind AS equipment_kind,
                  r.label AS rule_label
           FROM services s LEFT JOIN equipment e ON e.id=s.equipment_id
           LEFT JOIN rules r ON r.id=s.rule_id
           WHERE s.reversed_by_id IS NULL
           ORDER BY COALESCE(s.occurred_at,'') DESC, s.id DESC LIMIT 200""",
    )
    completed = []
    for row in services:
        if row["kind"] == "undo":
            continue
        completed.append({
            "id": row["id"],
            "equipment_id": row["equipment_id"],
            "equipment_name": row["equipment_name"],
            "equipment_kind": row["equipment_kind"],
            "rule_label": row["rule_label"],
            "kind": row["kind"],
            "resolved": bool(row["resolved"]),
            "occurred_at": row["occurred_at"],
            "notes": row["notes"],
            "source": row["source"],
            "counter_text": row["counter_text"],
            "raw_wording": row["raw_wording"],
            "time_range_text": row["time_range_text"],
            "operator": row["operator"],
            "can_undo": row["source"] == "owner" and row["reversed_by_id"] is None and row["kind"] != "undo",
        })
    unresolved = [s for s in completed if not s["resolved"]]
    snapshot = latest_snapshot(conn)
    counts = {"due": 0, "due_soon": 0, "ok": 0, "unknown": 0}
    for s in statuses:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {
        "statuses": statuses,
        "upcoming": {"aircraft": aircraft, "batteries": batteries},
        "completed": completed,
        "unresolved_groups": unresolved,
        "counts": counts,
        "snapshot": dict(snapshot) if snapshot else None,
        "captured_mismatches": [
            s for s in statuses
            if s["captured_status_normalized"] and s["matches_capture"] is False
        ],
    }


def rule_detail(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any]:
    rule = db.one(conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
    if rule is None:
        return {"ok": False, "code": "not_found"}
    status = rule_status(conn, rule)
    history = db.all_rows(
        conn,
        "SELECT * FROM services WHERE equipment_id=? AND "
        "(rule_id=? OR linked_rule_id=? OR (rule_id IS NULL AND kind='oneoff')) "
        "ORDER BY COALESCE(occurred_at,'') DESC, id DESC",
        (rule["equipment_id"], rule_id, rule_id),
    )
    status["history"] = [dict(h) for h in history]
    return {"ok": True, **status}


def _insert_owner_service(
    conn: sqlite3.Connection,
    equipment_id: int,
    rule_id: int | None,
    kind: str,
    notes: str,
    occurred_at: str | None,
    linked_rule_id: int | None = None,
) -> int:
    now = db.now_utc()
    at = occurred_at or now
    precision = "second" if "T" in at else "date"
    external = "owner:" + uuid.uuid4().hex
    cur = conn.execute(
        "INSERT INTO services(equipment_id,rule_id,kind,occurred_at,occurred_precision,"
        "notes,source,resolved,external_key,linked_rule_id,created_at) "
        "VALUES(?,?,?,?,?,?, 'owner',1,?,?,?)",
        (equipment_id, rule_id, kind, at, precision, notes or None, external,
         linked_rule_id, now),
    )
    db.audit(conn, "owner", "service_" + kind, "equipment", str(equipment_id),
             {"rule_id": rule_id, "linked_rule_id": linked_rule_id, "at": at})
    return int(cur.lastrowid)


def complete_service(conn: sqlite3.Connection, equipment_id: int, rule_id: int,
                     notes: str = "", occurred_at: str | None = None,
                     kind: str = "completed") -> dict[str, Any]:
    if kind not in ("completed", "skipped"):
        return {"ok": False, "code": "bad_kind"}
    rule = db.one(conn, "SELECT * FROM rules WHERE id=? AND equipment_id=?",
                  (rule_id, equipment_id))
    if rule is None:
        return {"ok": False, "code": "rule_mismatch"}
    with db.tx(conn):
        service_id = _insert_owner_service(conn, equipment_id, rule_id, kind, notes, occurred_at)
    return {"ok": True, "service_id": service_id, "status": rule_status(conn, rule)}


def oneoff_service(conn: sqlite3.Connection, equipment_id: int, notes: str = "",
                   linked_rule_id: int | None = None,
                   occurred_at: str | None = None) -> dict[str, Any]:
    eq = db.one(conn, "SELECT id FROM equipment WHERE id=?", (equipment_id,))
    if eq is None:
        return {"ok": False, "code": "not_found"}
    if linked_rule_id is not None:
        rule = db.one(conn, "SELECT id FROM rules WHERE id=? AND equipment_id=?",
                      (linked_rule_id, equipment_id))
        if rule is None:
            return {"ok": False, "code": "rule_mismatch"}
    with db.tx(conn):
        service_id = _insert_owner_service(
            conn, equipment_id, None, "oneoff", notes, occurred_at, linked_rule_id
        )
    return {"ok": True, "service_id": service_id, "affects_rule": linked_rule_id is not None}


def undo_service(conn: sqlite3.Connection, service_id: int) -> dict[str, Any]:
    row = db.one(conn, "SELECT * FROM services WHERE id=?", (service_id,))
    if row is None:
        return {"ok": False, "code": "not_found"}
    if row["source"] != "owner" or row["kind"] == "undo":
        return {"ok": False, "code": "not_reversible"}
    if row["reversed_by_id"] is not None:
        return {"ok": False, "code": "already_reversed"}
    now = db.now_utc()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO services(equipment_id,rule_id,kind,occurred_at,occurred_precision,"
            "notes,source,resolved,external_key,reverses_id,created_at) "
            "VALUES(?,?,?,?,?,'Undo of an owner service','owner',1,?,?,?)",
            (row["equipment_id"], row["rule_id"], "undo", now, "second",
             "undo:" + uuid.uuid4().hex, service_id, now),
        )
        undo_id = int(cur.lastrowid)
        conn.execute("UPDATE services SET reversed_by_id=? WHERE id=?", (undo_id, service_id))
        db.audit(conn, "owner", "service_undo", "services", str(service_id),
                 {"undo_id": undo_id})
    return {"ok": True, "undo_id": undo_id}


def resolve_coverage(conn: sqlite3.Connection, flight_id: int, snapshot_id: int,
                     decision: str, note: str = "") -> dict[str, Any]:
    if decision not in ("include", "covered"):
        return {"ok": False, "code": "bad_decision"}
    flight = db.one(conn, "SELECT id FROM flights WHERE id=?", (flight_id,))
    snap = db.one(conn, "SELECT id FROM service_snapshots WHERE id=?", (snapshot_id,))
    if flight is None or snap is None:
        return {"ok": False, "code": "not_found"}
    with db.tx(conn):
        conn.execute(
            "INSERT INTO cover_resolutions(flight_id,snapshot_id,decision,note,created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(flight_id,snapshot_id) DO UPDATE SET "
            "decision=excluded.decision,note=excluded.note,created_at=excluded.created_at",
            (flight_id, snapshot_id, decision, note, db.now_utc()),
        )
        db.audit(conn, "owner", "coverage_resolve", "flight", str(flight_id),
                 {"snapshot_id": snapshot_id, "decision": decision})
    return {"ok": True}


LIMIT_FIELDS = ("flights_limit", "air_minutes_limit", "charges_limit", "days_limit")


def _clean_limit(value: Any) -> tuple[bool, Any]:
    """A limit must be a positive number, or None to switch that trigger off.

    An empty string from a form field means "not used", which is a legitimate
    choice: it is how a counter-only rule turns its calendar trigger off.
    """
    if value is None or value == "":
        return True, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, None
    if number != number or number in (float("inf"), float("-inf")):
        return False, None
    if number <= 0:
        return False, None
    # Integer counters stay integers; air minutes and days may be fractional.
    if float(number).is_integer():
        return True, int(number)
    return True, number


def set_rule_policy(conn: sqlite3.Connection, rule_id: int, **fields: Any) -> dict[str, Any]:
    """Change a rule's thresholds. Rejects nonsense rather than storing it."""
    rule = db.one(conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
    if rule is None:
        return {"ok": False, "code": "not_found"}
    updates: dict[str, Any] = {}
    rejected: list[str] = []

    if "enabled" in fields:
        updates["enabled"] = 1 if fields["enabled"] else 0
    if "due_soon_pct" in fields:
        try:
            pct = float(fields["due_soon_pct"])
        except (TypeError, ValueError):
            pct = -1.0
        if 0.0 < pct <= 1.0:
            updates["due_soon_pct"] = pct
        else:
            rejected.append("due_soon_pct")
    for name in LIMIT_FIELDS:
        if name not in fields:
            continue
        ok, value = _clean_limit(fields[name])
        if not ok:
            rejected.append(name)
        else:
            updates[name] = value

    if rejected:
        return {"ok": False, "code": "invalid_value", "fields": rejected}
    if not updates:
        return {"ok": False, "code": "no_fields"}

    # A rule with no trigger at all can never fire, so refuse that outcome.
    merged = {name: updates.get(name, rule[name] if name in rule.keys() else None)
              for name in LIMIT_FIELDS}
    if not any(v for v in merged.values()):
        return {"ok": False, "code": "no_triggers"}

    with db.tx(conn):
        sets = ",".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE rules SET {sets},updated_at=? WHERE id=?",
                     (*updates.values(), db.now_utc(), rule_id))
        db.audit(conn, "owner", "rule_policy", "rules", str(rule_id), updates)
    updated = db.one(conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
    return {"ok": True, "changes": updates, "status": rule_status(conn, updated)}
