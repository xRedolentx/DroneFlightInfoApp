"""Read models for the local web UI. No rendering, only JSON-safe structures."""
from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import climate, db, maintenance
from .analysis import track_segments

FLIGHT_FIELDS = (
    "id", "identity_status", "aircraft_name", "aircraft_sn", "battery_sn", "camera_sn",
    "rc_sn", "start_time_utc", "start_time_offset_min", "start_time_precision",
    "filename_time", "pilot", "tags_json", "notes", "duration_s", "recorder_duration_s",
    "airborne_time_s", "total_distance_m", "path_length_m", "max_home_distance_m",
    "max_height_m", "max_altitude_m", "max_h_speed_mps", "home_lat", "home_lon",
    "home_alt_m", "battery_start_pct", "battery_end_pct", "battery_temp_max_c",
    "battery_temp_min_c", "signal_min",
    "sample_count", "gap_count", "metrics_source", "parser_version", "excluded_reason",
    "aircraft_equipment_id", "battery_equipment_id", "created_at", "updated_at",
)


def _flight_row(row: sqlite3.Row) -> dict[str, Any]:
    data = {k: row[k] for k in FLIGHT_FIELDS if k in row.keys()}
    try:
        data["tags"] = json.loads(row["tags_json"] or "[]")
    except (TypeError, ValueError):
        data["tags"] = []
    # A count for the list badge; None when faults were not recorded at all, so
    # an undecoded flight never displays as "0 warnings".
    raw = row["warnings_json"] if "warnings_json" in row.keys() else None
    if raw is None:
        data["warnings_count"] = None
    else:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            # Start-up transients are recorded but do not raise a badge: the
            # badge is for something worth opening the flight for.
            data["warnings_count"] = sum(
                1 for i in parsed if i.get("counted", True)
            )
        else:
            data["warnings_count"] = None
    return data


def pilots(conn: sqlite3.Connection) -> list[str]:
    stored = db.get_setting(conn, "pilots", [])
    seen = set()
    out = []
    for name in list(stored) + [
        r["pilot"] for r in db.all_rows(
            conn, "SELECT DISTINCT pilot FROM flights WHERE pilot IS NOT NULL ORDER BY pilot"
        )
    ]:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    default = db.get_setting(conn, "pilot_default")
    if default and default not in seen:
        out.insert(0, default)
    return out


def _data_mode(conn: sqlite3.Connection) -> str:
    explicit = db.get_setting(conn, "data_mode")
    if explicit:
        return explicit
    if db.get_setting(conn, "demo_seeded", False):
        return "synthetic"
    if conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]:
        return "unknown"
    return "empty"


def state(conn: sqlite3.Connection) -> dict[str, Any]:
    from . import keychain, paths, tiles
    counts = db.table_counts(conn)
    root = Path(db_path_of(conn)).parent if db_path_of(conn) else None
    raw_status = {
        r["status"]: r["n"]
        for r in db.all_rows(conn, "SELECT status,COUNT(*) AS n FROM raw_files GROUP BY status")
    }
    unresolved = int(conn.execute(
        "SELECT COUNT(*) FROM flights WHERE identity_status='unresolved'").fetchone()[0])
    map_info = tiles.status(conn, root) if root else {"consented": False}
    return {
        "counts": counts,
        "raw_status": raw_status,
        "unresolved_flights": unresolved,
        "pending_key": raw_status.get("pending_key", 0),
        "pilots": pilots(conn),
        "unit_preference": db.get_setting(conn, "unit_preference", "metric"),
        "data_mode": _data_mode(conn),
        "real_inputs_selected": bool(db.get_setting(conn, "real_inputs_selected", False)),
        "key_configured": keychain.has_key(root) if root else False,
        "keychain_cache": keychain.cache_summary(conn),
        "demo_seeded": bool(db.get_setting(conn, "demo_seeded", False)),
        "last_flight_id": db.get_setting(conn, "last_flight_id"),
        "filters": db.get_setting(conn, "last_filters", {}),
        "parser": {"version": paths.PARSER_VERSION, "source": paths.PARSER_SOURCE},
        "privacy": bool(db.get_setting(conn, "privacy_mode", False)),
        "external_services": db.get_setting(conn, "external_consent", {}),
        "map": map_info,
        "map_mode": bool(db.get_setting(conn, "map_mode", False)),
        "capture_applied": bool(conn.execute(
            "SELECT COUNT(*) FROM service_snapshots").fetchone()[0]),
    }


def db_path_of(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return row["file"] if row and row["file"] else ""


def flights(conn: sqlite3.Connection, filters: dict[str, Any]) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    search = (filters.get("search") or "").strip()
    if search:
        where.append(
            "(COALESCE(aircraft_name,'') LIKE ? OR COALESCE(pilot,'') LIKE ? "
            "OR COALESCE(notes,'') LIKE ? OR COALESCE(tags_json,'') LIKE ? "
            "OR COALESCE(start_time_utc,'') LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like])
    if filters.get("from"):
        where.append("COALESCE(start_time_utc,'') >= ?")
        params.append(str(filters["from"]))
    if filters.get("to"):
        where.append("COALESCE(start_time_utc,'') <= ?")
        params.append(str(filters["to"]) + "T23:59:59+99:99")
    if filters.get("aircraft"):
        where.append("(aircraft_equipment_id=? OR aircraft_name=?)")
        params.extend([filters["aircraft"], filters.get("aircraft_name")])
    if filters.get("battery"):
        where.append("battery_equipment_id=?")
        params.append(filters["battery"])
    if filters.get("pilot"):
        where.append("pilot=?")
        params.append(filters["pilot"])
    if filters.get("tag"):
        where.append("tags_json LIKE ?")
        params.append(f'%"{filters["tag"]}"%')
    if filters.get("status") == "unresolved":
        where.append("identity_status='unresolved'")
    elif filters.get("status") == "resolved":
        where.append("identity_status='authoritative'")
    clause = " AND ".join(where)
    rows = db.all_rows(
        conn, f"SELECT * FROM flights WHERE {clause} ORDER BY COALESCE(start_time_utc,'') DESC",
        tuple(params),
    )
    auth = [r for r in rows if r["identity_status"] == "authoritative"]
    unresolved = [r for r in rows if r["identity_status"] != "authoritative"]
    air_s = sum(float(r["duration_s"] or 0) for r in auth)
    distance_m = sum(float(r["total_distance_m"] or 0) for r in auth)
    aircraft = db.all_rows(
        conn,
        "SELECT id,name,archived,placeholder FROM equipment WHERE kind='aircraft' "
        "ORDER BY archived, name",
    )
    batteries = db.all_rows(
        conn,
        "SELECT id,name,archived,placeholder FROM equipment WHERE kind='battery' "
        "ORDER BY archived, name",
    )
    tags: set[str] = set()
    for row in rows:
        try:
            tags.update(json.loads(row["tags_json"] or "[]"))
        except (TypeError, ValueError):
            pass
    return {
        "rows": [_flight_row(r) for r in rows],
        "totals": {
            "flights": len(auth),
            "unresolved": len(unresolved),
            "air_minutes": round(air_s / 60.0, 2),
            "distance_m": round(distance_m, 1),
            "aircraft": len({r["aircraft_sn"] or r["aircraft_name"] for r in auth if r["aircraft_sn"] or r["aircraft_name"]}),
            "batteries": len({r["battery_sn"] for r in auth if r["battery_sn"]}),
        },
        "facets": {
            "aircraft": [dict(a) for a in aircraft],
            "batteries": [dict(b) for b in batteries],
            "pilots": pilots(conn),
            "tags": sorted(tags),
        },
        "filters": filters,
    }


def telemetry(conn: sqlite3.Connection, flight_id: int) -> dict[str, Any]:
    row = db.one(conn, "SELECT * FROM flight_telemetry WHERE flight_id=?", (flight_id,))
    if row is None:
        return {"samples": [], "gaps": [], "source": None, "count": 0}
    try:
        raw = gzip.decompress(row["samples_blob"])
        samples = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, gzip.BadGzipFile):
        samples = []
    try:
        gaps = json.loads(row["gaps_json"] or "[]")
    except ValueError:
        gaps = []
    return {"samples": samples, "gaps": gaps, "source": row["source"],
            "count": row["sample_count"]}


def flight_detail(conn: sqlite3.Connection, flight_id: int) -> dict[str, Any] | None:
    row = db.one(conn, "SELECT * FROM flights WHERE id=?", (flight_id,))
    if row is None:
        return None
    files = db.all_rows(
        conn,
        """SELECT r.id,r.sha256,r.size_bytes,r.original_name,r.format_version,r.status,
                  r.error_category,r.parser_version,r.first_imported_at,
                  GROUP_CONCAT(DISTINCT sm.source_kind || '|' || sm.source_label) AS memberships,
                  ff.role, ff.evidence_json
           FROM flight_files ff JOIN raw_files r ON r.id=ff.raw_file_id
           LEFT JOIN source_members sm ON sm.raw_file_id=r.id
           WHERE ff.flight_id=? GROUP BY r.id""",
        (flight_id,),
    )
    air = db.one(conn, "SELECT * FROM equipment WHERE id=?", (row["aircraft_equipment_id"],)) \
        if row["aircraft_equipment_id"] else None
    battery = db.one(conn, "SELECT * FROM equipment WHERE id=?", (row["battery_equipment_id"],)) \
        if row["battery_equipment_id"] else None
    tel = telemetry(conn, flight_id)
    samples = tel["samples"]
    segments = track_segments(samples, tel["gaps"])
    neighbours = db.all_rows(
        conn,
        "SELECT id,start_time_utc FROM flights WHERE COALESCE(start_time_utc,'')!='' "
        "ORDER BY start_time_utc",
    )
    ids = [r["id"] for r in neighbours]
    index = ids.index(flight_id) if flight_id in ids else None
    prev_id = ids[index - 1] if index not in (None, 0) else None
    next_id = ids[index + 1] if index is not None and index + 1 < len(ids) else None
    return {
        "flight": _flight_row(row),
        "files": [
            {**dict(f), "memberships": sorted({
                p for p in (f["memberships"] or "").split(",") if "|" in p
            })} for f in files
        ],
        "equipment": {
            "aircraft": dict(air) if air else None,
            "battery": dict(battery) if battery else None,
        },
        "telemetry": {"source": tel["source"], "count": tel["count"],
                      "samples": samples, "gaps": tel["gaps"],
                      "segments": segments},
        "neighbours": {"prev": prev_id, "next": next_id},
        "warnings": _flight_warnings(row),
        "temperature": climate.classify(
            row["battery_temp_max_c"], row["battery_temp_min_c"],
            climate.limits(conn),
        ),
    }


def _flight_warnings(row: sqlite3.Row) -> dict[str, Any]:
    """The fault record for one flight, with its provenance stated.

    Three outcomes are deliberately distinct, because they mean different
    things and only one of them is reassuring:

    * ``recorded``  - the log was decoded and carried fault flags (or none).
    * ``unavailable`` - no frames were stored, so nothing could be inspected.
    * ``pending``   - the record has not been decoded yet (needs the DJI key).

    Collapsing these into "no warnings" would let an undecoded record look like
    a clean flight.
    """
    raw = row["warnings_json"] if "warnings_json" in row.keys() else None
    if raw is None:
        metrics = (row["metrics_source"] or "") if "metrics_source" in row.keys() else ""
        if metrics == "metadata_only":
            return {"state": "pending", "items": [],
                    "note": "This record is encrypted; faults appear once the DJI key decodes it."}
        return {"state": "unavailable", "items": [],
                "note": "No frame data was stored for this record, so faults could not be read."}
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return {"state": "unavailable", "items": [],
                "note": "The stored fault record could not be read."}
    if not isinstance(items, list):
        return {"state": "unavailable", "items": [],
                "note": "The stored fault record could not be read."}
    counted = [i for i in items if i.get("counted", True)]
    startup = [i for i in items if not i.get("counted", True)]
    if not items:
        note = "No fault flag was raised during this flight."
    elif not counted:
        note = ("Only start-up transients were seen \u2014 a single-frame compass flag "
                "in the first seconds, which is the known DJI behaviour at motor start.")
    else:
        note = ""
    return {"state": "recorded", "items": items,
            "count": len(counted),
            "startup_transients": len(startup),
            "note": note}


def equipment_list(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    rows = db.all_rows(
        conn,
        "SELECT * FROM equipment WHERE placeholder=0" +
        ("" if include_archived else " AND archived=0") +
        " ORDER BY archived, kind DESC, name",
    )
    return [dict(r) for r in rows]


def equipment_detail(conn: sqlite3.Connection, equipment_id: int) -> dict[str, Any] | None:
    eq = db.one(conn, "SELECT * FROM equipment WHERE id=?", (equipment_id,))
    if eq is None:
        return None
    column = "aircraft_equipment_id" if eq["kind"] == "aircraft" else "battery_equipment_id"
    flights = db.all_rows(
        conn,
        f"SELECT * FROM flights WHERE {column}=? ORDER BY COALESCE(start_time_utc,'') DESC",
        (equipment_id,),
    )
    readings = db.all_rows(
        conn,
        "SELECT * FROM battery_readings WHERE battery_id=? ORDER BY COALESCE(at,'') DESC",
        (equipment_id,),
    )
    children = db.all_rows(
        conn, "SELECT id,name,serial,archived FROM equipment WHERE parent_id=? ORDER BY name",
        (equipment_id,),
    )
    services = db.all_rows(
        conn,
        "SELECT * FROM services WHERE equipment_id=? ORDER BY COALESCE(occurred_at,'') DESC",
        (equipment_id,),
    )
    status = maintenance.equipment_status(conn, equipment_id)
    charge = maintenance.battery_cycle_stats(conn, equipment_id, None) \
        if eq["kind"] == "battery" else None
    last_reading = None
    for r in readings:
        if r["temp_c"] is not None or r["charge_pct"] is not None:
            last_reading = dict(r)
            break
    return {
        "equipment": dict(eq),
        "rules": status.get("rules", []),
        "flights": [_flight_row(f) for f in flights],
        "readings": [dict(r) for r in readings],
        "last_reading": last_reading,
        "children": [dict(c) for c in children],
        "services": [dict(s) for s in services],
        "charge_history": charge,
    }


def import_history(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    rows = db.all_rows(
        conn,
        "SELECT * FROM import_batches ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    out = []
    for row in rows:
        try:
            totals = json.loads(row["totals_json"] or "{}")
        except ValueError:
            totals = {}
        out.append({**dict(row), "totals": totals})
    return out


def pending_files(conn: sqlite3.Connection) -> list[dict]:
    rows = db.all_rows(
        conn,
        "SELECT id,sha256,format_version,status,error_category,size_bytes,first_imported_at,"
        "original_name FROM raw_files WHERE status IN "
        "('pending_key','unsupported','damaged','needs_attention') "
        "ORDER BY first_imported_at DESC LIMIT 200",
    )
    return [dict(r) for r in rows]


def unresolved_flights(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = db.all_rows(
        conn,
        "SELECT * FROM flights WHERE identity_status='unresolved' "
        "ORDER BY COALESCE(start_time_utc,'') DESC LIMIT ?",
        (limit,),
    )
    return [_flight_row(r) for r in rows]
