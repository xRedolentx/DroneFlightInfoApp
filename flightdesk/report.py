"""Reconciliation, maintenance discrepancies and readable CSV exports."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db, maintenance

MILES_M = 1609.344
ARCHIVE_EXPECTED = {
    "files": 264,
    "air_minutes": 24 * 60 + 10,
    "miles": 108.0,
    "photos": 749,
    "videos": 459,
}


def reference_record(conn: sqlite3.Connection) -> dict[str, Any]:
    """The AirData comparison the brief supplies, kept as reference data.

    Stored with a source, scope and date so a later session can tell what was
    compared against what, without treating it as measured data.
    """
    stored = db.get_setting(conn, "reference_archive")
    if stored:
        return stored
    record = {
        "source": "AirData archive, recorded in the build brief",
        "source_label": "data/extracted/flight_logs (+ data/controller-test)",
        "scope": "archive",
        "recorded_at": "2026-11-17",
        "precision": "displayed air time to the minute; distance to the mile",
        "files": dict(ARCHIVE_EXPECTED),
        "by_version": {"v8": 7, "v11": 218, "v14": 39},
        "controller_extras": {"v11": 4, "v14": 1},
        "notes": (
            "Counts files, not distinct flights. Photos and videos are counts only, "
            "so a video count of 459 is compared only where a counter exists."
        ),
    }
    with db.tx(conn):
        db.set_setting(conn, "reference_archive", record)
    return record


def _raw_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.all_rows(
        conn,
        """SELECT r.id, r.sha256, r.format_version, r.status, r.size_bytes,
                  GROUP_CONCAT(DISTINCT sm.source_kind || '|' || sm.source_label) AS memberships
           FROM raw_files r LEFT JOIN source_members sm ON sm.raw_file_id=r.id
           GROUP BY r.id""",
    )
    out = []
    for row in rows:
        members = set()
        raw = row["memberships"] or ""
        for part in raw.split(","):
            if "|" in part:
                members.add(part)
        kinds = {m.split("|", 1)[0] for m in members}
        labels = {m.split("|", 1)[1] for m in members if "|" in m}
        if "archive" in kinds:
            group = "archive"
        elif any(label.startswith("USB") for label in labels):
            group = "later_controller"
        elif "extras" in kinds:
            group = "supplied_controller"
        elif "manual" in kinds:
            group = "manual"
        else:
            group = "other"
        out.append({**dict(row), "report_group": group, "memberships": sorted(members)})
    return out


def _flight_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.all_rows(
        conn,
        """SELECT f.*, GROUP_CONCAT(DISTINCT sm.source_kind || '|' || sm.source_label) AS memberships
           FROM flights f
           LEFT JOIN flight_files ff ON ff.flight_id=f.id
           LEFT JOIN source_members sm ON sm.raw_file_id=ff.raw_file_id
           GROUP BY f.id
           ORDER BY f.start_time_utc""",
    )
    out = []
    for row in rows:
        raw = row["memberships"] or ""
        members = {p for p in raw.split(",") if "|" in p}
        kinds = {m.split("|", 1)[0] for m in members}
        labels = {m.split("|", 1)[1] for m in members}
        if "archive" in kinds:
            group = "archive"
        elif any(label.startswith("USB") for label in labels):
            group = "later_controller"
        elif "extras" in kinds:
            group = "supplied_controller"
        elif "manual" in kinds:
            group = "manual"
        else:
            group = "other"
        out.append({**dict(row), "report_group": group})
    return out


def _summary(raws: list[dict], flights: list[dict]) -> dict[str, Any]:
    by_version: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for raw in raws:
        version = str(raw["format_version"] if raw["format_version"] is not None else "unknown")
        by_version[version] = by_version.get(version, 0) + 1
        by_status[raw["status"]] = by_status.get(raw["status"], 0) + 1
    auth = [f for f in flights if f["identity_status"] == "authoritative"]
    unresolved = [f for f in flights if f["identity_status"] == "unresolved"]
    air_s = sum(float(f["duration_s"] or 0) for f in auth)
    distance_m = sum(float(f["total_distance_m"] or 0) for f in auth)
    photos = sum(int(f["photo_count"]) for f in auth if f["photo_count"] is not None
                 and f["media_source"] == "log_details")
    video_s = sum(int(f["video_time_s"]) for f in auth if f["video_time_s"] is not None
                  and f["media_source"] == "log_details")
    metadata_only = sum(1 for f in auth if f["metrics_source"] == "metadata_only")
    return {
        "raw_files": len(raws),
        "distinct_hashes": len({r["sha256"] for r in raws}),
        "by_version": dict(sorted(by_version.items())),
        "by_status": dict(sorted(by_status.items())),
        "decoded": sum(1 for r in raws if r["status"] == "imported"),
        "logical_flights": len(auth),
        "unresolved_flights": len(unresolved),
        "metadata_only_flights": metadata_only,
        "air_minutes": round(air_s / 60.0, 3),
        "air_time": _hms(air_s),
        "distance_m": round(distance_m, 2),
        "miles": round(distance_m / MILES_M, 3),
        "photos": photos,
        "video_seconds": video_s,
        "videos_count": None,
        "decoded_coverage": round(
            (sum(1 for r in raws if r["status"] == "imported") / len(raws)) if raws else 0.0, 4
        ),
    }


def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def reconciliation(conn: sqlite3.Connection, *, persist: bool = True) -> dict[str, Any]:
    raws = _raw_groups(conn)
    flights = _flight_groups(conn)
    groups = {
        "archive": [r for r in raws if r["report_group"] == "archive"],
        "supplied_controller": [r for r in raws if r["report_group"] == "supplied_controller"],
        "later_controller": [r for r in raws if r["report_group"] == "later_controller"],
        "manual": [r for r in raws if r["report_group"] == "manual"],
        "other": [r for r in raws if r["report_group"] == "other"],
    }
    summaries = {}
    for name, subset in groups.items():
        ids = {r["id"] for r in subset}
        subset_flights = [f for f in flights if f["report_group"] == name]
        if name == "archive":
            subset_flights = [f for f in flights if f["report_group"] == "archive"]
        summaries[name] = _summary(subset, subset_flights)
    summaries["combined"] = _summary(raws, flights)

    real_inputs = bool(db.get_setting(conn, "real_inputs_selected", False))
    data_mode = db.get_setting(conn, "data_mode", "synthetic" if db.get_setting(conn, "demo_seeded") else "unknown")
    reference = reference_record(conn)
    expected = dict(ARCHIVE_EXPECTED)
    measured = summaries["archive"]
    diffs = None
    if real_inputs:
        diffs = {
            "files": measured["raw_files"] - expected["files"],
            "air_minutes": round(measured["air_minutes"] - expected["air_minutes"], 3),
            "miles": round(measured["miles"] - expected["miles"], 3),
            "photos": measured["photos"] - expected["photos"],
            "videos": None,
        }
    excluded = {
        "pending_key": sum(1 for r in raws if r["status"] == "pending_key"),
        "unsupported": sum(1 for r in raws if r["status"] == "unsupported"),
        "damaged": sum(1 for r in raws if r["status"] == "damaged"),
        "needs_attention": sum(1 for r in raws if r["status"] == "needs_attention"),
        "unresolved_flights": sum(1 for f in flights if f["identity_status"] == "unresolved"),
        "same_time_candidates": sum(
            1 for f in flights if f["excluded_reason"] == "same_time_weak_match"
        ),
    }
    statuses = maintenance.all_statuses(conn, include_archived=True)
    discrepancies = []
    for status in statuses:
        if status["captured_status_normalized"] and status["matches_capture"] is False:
            discrepancies.append({
                "equipment": status["equipment_name"],
                "rule": status["code"],
                "captured": status["captured_status"],
                "computed": status["status"],
                "fired": status["fired_metric"],
                "baseline": status["baseline"]["basis"],
                "note": "captured status and computed status differ",
            })
    snapshot = maintenance.latest_snapshot(conn)
    report = {
        "generated_at": db.now_utc(),
        "data_mode": data_mode,
        "real_inputs_selected": real_inputs,
        "reference": reference,
        "expected_archive": expected if real_inputs else {
            **expected, "status": "reference only; select real inputs to compare"
        },
        "signed_differences": diffs,
        "groups": summaries,
        "excluded": excluded,
        "maintenance_discrepancies": discrepancies,
        "snapshot": {
            "id": snapshot["id"] if snapshot else None,
            "captured_at": snapshot["captured_at"] if snapshot else None,
            "covered_through": snapshot["covered_through"] if snapshot else None,
            "source_label": snapshot["source_label"] if snapshot else None,
        },
        "semantics": {
            "photos": "sum of DJI details capture counts; media files are not in this archive",
            "videos": "video count unavailable from details; video seconds only",
            "air_minutes": "from DJI details total time; track-derived airborne time is separate",
            "distance": "from DJI details total distance; path length is separate",
        },
    }
    baseline = None
    if persist:
        existing = db.one(
            conn, "SELECT * FROM recon_runs WHERE scope='archive' ORDER BY id LIMIT 1"
        )
        if existing is not None:
            try:
                baseline = json.loads(existing["payload_json"])
            except ValueError:
                baseline = None
        else:
            compact = {
                "created_at": report["generated_at"],
                "raw_files": measured["raw_files"],
                "air_minutes": measured["air_minutes"],
                "miles": measured["miles"],
                "photos": measured["photos"],
                "video_seconds": measured["video_seconds"],
            }
            with db.tx(conn):
                conn.execute(
                    "INSERT INTO recon_runs(created_at,scope,payload_json) VALUES(?,?,?)",
                    (db.now_utc(), "archive", json.dumps(compact, separators=(",", ":"))),
                )
            baseline = compact
    report["baseline"] = baseline
    if baseline:
        report["baseline_delta"] = {
            "raw_files": measured["raw_files"] - baseline.get("raw_files", 0),
            "air_minutes": round(measured["air_minutes"] - baseline.get("air_minutes", 0), 3),
            "miles": round(measured["miles"] - baseline.get("miles", 0), 3),
        }
    return report


def report_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["section", "label", "value", "note"])
    w.writerow(["meta", "generated_at", report.get("generated_at"), ""])
    w.writerow(["meta", "data_mode", report.get("data_mode"), ""])
    for group, summary in report.get("groups", {}).items():
        for key in ("raw_files", "distinct_hashes", "decoded", "logical_flights",
                    "unresolved_flights", "metadata_only_flights", "air_minutes",
                    "air_time", "distance_m", "miles", "photos", "video_seconds"):
            w.writerow([group, key, summary.get(key), ""])
        w.writerow([group, "decoded_coverage", f"{summary.get('decoded_coverage', 0):.2%}", ""])
    for key, value in report.get("excluded", {}).items():
        w.writerow(["excluded", key, value, "not silently counted as zero"])
    for item in report.get("maintenance_discrepancies", []):
        w.writerow(["maintenance", f"{item['equipment']} · {item['rule']}",
                    f"{item['captured']} → {item['computed']}", item["note"]])
    if report.get("signed_differences"):
        for key, value in report["signed_differences"].items():
            w.writerow(["difference", key, value, "measured minus reference"])
    return out.getvalue()


def flights_csv(conn: sqlite3.Connection, redact: bool = False) -> str:
    rows = db.all_rows(
        conn,
        """SELECT f.*, ea.name AS aircraft_equipment_name,
                  eb.name AS battery_equipment_name
           FROM flights f
           LEFT JOIN equipment ea ON ea.id=f.aircraft_equipment_id
           LEFT JOIN equipment eb ON eb.id=f.battery_equipment_id
           ORDER BY f.start_time_utc""",
    )
    out = io.StringIO()
    w = csv.writer(out)
    columns = [
        ("flight_id", lambda r: r["id"]),
        ("start_time_utc", lambda r: r["start_time_utc"]),
        ("aircraft", lambda r: r["aircraft_equipment_name"] or r["aircraft_name"]),
        ("aircraft_serial", lambda r: r["aircraft_sn"]),
        ("battery", lambda r: r["battery_equipment_name"]),
        ("battery_serial", lambda r: r["battery_sn"]),
        ("pilot", lambda r: r["pilot"]),
        ("tags", lambda r: r["tags_json"]),
        ("duration_s", lambda r: r["duration_s"]),
        ("air_minutes", lambda r: round(float(r["duration_s"] or 0) / 60.0, 3)),
        ("distance_m", lambda r: r["total_distance_m"]),
        ("max_height_m", lambda r: r["max_height_m"]),
        ("max_home_m", lambda r: r["max_home_distance_m"]),
        ("path_length_m", lambda r: r["path_length_m"]),
        ("max_speed_mps", lambda r: r["max_h_speed_mps"]),
        ("photos", lambda r: r["photo_count"]),
        ("video_seconds", lambda r: r["video_time_s"]),
        ("identity", lambda r: r["identity_status"]),
        ("metrics_source", lambda r: r["metrics_source"]),
        ("home_lat", lambda r: r["home_lat"]),
        ("home_lon", lambda r: r["home_lon"]),
    ]
    if redact:
        hidden = {"aircraft_serial", "battery_serial", "home_lat", "home_lon"}
        columns = [c for c in columns if c[0] not in hidden]
    w.writerow([c[0] for c in columns])
    for row in rows:
        w.writerow([c[1](row) for c in columns])
    return out.getvalue()


def equipment_csv(conn: sqlite3.Connection, redact: bool = False) -> str:
    rows = db.all_rows(conn, "SELECT * FROM equipment ORDER BY kind, name")
    out = io.StringIO()
    w = csv.writer(out)
    header = ["equipment_id", "kind", "name", "serial", "alias", "archived", "placeholder",
              "purchase_date", "firmware", "operational_state", "locked", "notes"]
    if redact:
        header.remove("serial")
    w.writerow(header)
    for row in rows:
        values = [row["id"], row["kind"], row["name"], row["serial"], row["alias"],
                  row["archived"], row["placeholder"], row["purchase_date"], row["firmware"],
                  row["operational_state"], row["locked_details"], row["notes"]]
        if redact:
            values = [v for k, v in zip(
                ["equipment_id", "kind", "name", "serial", "alias", "archived",
                 "placeholder", "purchase_date", "firmware", "operational_state",
                 "locked", "notes"], values) if k != "serial"]
        w.writerow(values)
    return out.getvalue()


def services_csv(conn: sqlite3.Connection, redact: bool = False) -> str:
    rows = db.all_rows(
        conn,
        """SELECT s.*, e.name AS equipment_name, e.serial AS equipment_serial,
                  r.label AS rule_label
           FROM services s LEFT JOIN equipment e ON e.id=s.equipment_id
           LEFT JOIN rules r ON r.id=s.rule_id
           ORDER BY COALESCE(s.occurred_at,''), s.id""",
    )
    out = io.StringIO()
    w = csv.writer(out)
    header = ["service_id", "date", "equipment", "serial", "rule", "kind", "source",
              "resolved", "counter", "operator", "notes", "reversed"]
    if redact:
        header.remove("serial")
    w.writerow(header)
    for row in rows:
        values = [row["id"], row["occurred_at"], row["equipment_name"],
                  row["equipment_serial"], row["rule_label"], row["kind"], row["source"],
                  row["resolved"], row["counter_text"], row["operator"], row["notes"],
                  bool(row["reversed_by_id"])]
        if redact:
            values = [v for k, v in zip(
                ["service_id", "date", "equipment", "serial", "rule", "kind", "source",
                 "resolved", "counter", "operator", "notes", "reversed"], values)
                if k != "serial"]
        w.writerow(values)
    return out.getvalue()
