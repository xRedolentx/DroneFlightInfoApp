"""Build a clearly-labelled synthetic demo data root for screenshots and tests."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import shutil
from pathlib import Path

from . import capture, db, maintenance
from .analysis import compute_metrics, downsample
from .importer import Importer
from .paths import db_path, ensure_layout
from .synth import filename_time, make_log_bytes, write_log


def _samples(start: dt.datetime, *, duration: float = 302.0, base_lat: float = 45.6789,
             base_lon: float = 12.3456, gap: tuple[float, float] | None = (80, 120),
             radius_m: float = 95.0):
    """A smooth invented out-and-back loop, sampled every two seconds."""
    out = []
    steps = int(duration)
    for i in range(0, steps, 2):
        t = float(i)
        if gap and gap[0] <= t <= gap[1]:
            continue
        progress = i / max(steps, 1)
        # Two thirds of the flight is a wide arc out; the last third returns.
        angle = 6.28318 * (0.75 * progress)
        r = radius_m * (0.35 + 0.65 * math.sin(math.pi * min(1.0, progress / 0.66)))
        east = r * math.cos(angle)
        north = r * math.sin(angle) * 0.62
        lat = base_lat + north / 110540.0
        lon = base_lon + east / (111320.0 * math.cos(math.radians(base_lat)))
        height = max(0.0, min(45.0, 12.0 + 33.0 * math.sin(math.pi * min(1.0, progress / 0.66))))
        battery = 94.0 - (13.0 * progress)
        temp = 24.0 + 4.2 * progress
        out.append({
            "t": t,
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "height_m": round(height, 2),
            "alt_m": round(100.0 + height, 2),
            "speed_mps": round(4.0 + 2.6 * abs(math.sin(6.0 * progress)), 2),
            "battery_pct": round(battery, 1),
            "battery_c": round(temp, 2),
            "signal": None if i % 61 == 0 else 88 - (i % 13),
            "gps_num": 14,
            "gps_level": 4,
            "on_ground": i == 0,
            "motor_on": i > 0,
            "home_lat": base_lat,
            "home_lon": base_lon,
            "home_alt_m": 100.0,
            "voltage": 15.6,
            "cells": None,
            "is_photo": i == 50,
            "is_video": i in (30, 31, 32),
        })
    return out


def _inject_samples(conn, flight_id: int, samples: list[dict]) -> None:
    flight = db.one(conn, "SELECT * FROM flights WHERE id=?", (flight_id,))
    if flight is None:
        return
    details = {
        "total_time_s": flight["duration_s"],
        "total_distance_m": flight["total_distance_m"],
        "max_height_m": flight["max_height_m"],
        "max_h_speed_mps": flight["max_h_speed_mps"],
    }
    m = compute_metrics(samples, details)
    kept = downsample(samples, 1400)
    conn.execute(
        "UPDATE flights SET path_length_m=?, max_home_distance_m=?, max_altitude_m=?, "
        "airborne_time_s=?, recorder_duration_s=?, battery_start_pct=?, battery_end_pct=?, "
        "battery_temp_max_c=?, signal_min=?, sample_count=?, gap_count=?, updated_at=? "
        "WHERE id=?",
        (m.path_length_m, m.max_home_distance_m, m.max_altitude_m, m.airborne_time_s,
         m.recorder_duration_s, m.battery_start_pct, m.battery_end_pct,
         m.battery_temp_max_c, m.signal_min, m.sample_count, m.gap_count,
         db.now_utc(), flight_id),
    )
    conn.execute(
        "INSERT INTO flight_telemetry(flight_id,source,sample_count,samples_blob,gaps_json,created_at) "
        "VALUES(?, 'synthetic', ?, ?, ?, ?) ON CONFLICT(flight_id) DO UPDATE SET "
        "source='synthetic',sample_count=excluded.sample_count,samples_blob=excluded.samples_blob,"
        "gaps_json=excluded.gaps_json,created_at=excluded.created_at",
        (flight_id, len(kept), gzip.compress(json.dumps(kept).encode("utf-8"), 6),
         json.dumps(m.gaps, separators=(",", ":")), db.now_utc()),
    )


def seed_demo(root: Path) -> dict:
    root = Path(root)
    ensure_layout(root)
    conn = db.init_db(db_path(root))
    if db.get_setting(conn, "demo_seeded"):
        return {"ok": True, "already_seeded": True, "counts": db.table_counts(conn)}
    incoming = root / "tmp" / "demo_incoming"
    archive_dir = incoming / "flight_logs"
    extras_dir = incoming / "controller-test"
    if incoming.exists():
        shutil.rmtree(incoming)
    archive_dir.mkdir(parents=True, exist_ok=True)
    extras_dir.mkdir(parents=True, exist_ok=True)
    base = dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc)

    flights = [
        ("mavic2", base, "SYN-BAT-1001", 302.0, 46.9, 45.1, 6.5, 3, 20),
        ("mavic2", dt.datetime(2026, 9, 2, 17, 10, 5, tzinfo=dt.timezone.utc),
         "SYN-BAT-1002", 640.0, 211.0, 88.0, 8.2, 12, 41),
        ("mavic2", dt.datetime(2026, 7, 11, 14, 2, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-3402QS", 415.0, 120.0, 70.0, 7.1, 0, 12),
        ("mavic2", dt.datetime(2026, 3, 5, 10, 30, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-1004", 510.0, 180.0, 96.0, 9.0, 6, 0),
        ("air3s", dt.datetime(2026, 10, 3, 9, 15, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-2001", 720.0, 300.0, 110.0, 11.0, 24, 62),
        ("air3s", dt.datetime(2026, 8, 22, 16, 45, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-2002", 360.0, 95.0, 50.0, 6.0, 9, 0),
        ("air3s", dt.datetime(2026, 6, 18, 12, 5, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-2003", 280.0, 60.0, 40.0, 5.0, 4, 0),
        ("air3s", dt.datetime(2026, 1, 9, 11, 0, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-2001", 500.0, 150.0, 75.0, 7.5, 0, 18),
        ("mini3", dt.datetime(2026, 5, 14, 18, 20, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-3001", 330.0, 88.0, 42.0, 6.2, 5, 0),
        ("mini3", dt.datetime(2026, 4, 2, 8, 40, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-3002", 250.0, 55.0, 30.0, 4.8, 2, 0),
        ("mini3", dt.datetime(2025, 12, 30, 15, 15, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-3003", 410.0, 105.0, 55.0, 5.5, 0, 9),
        ("mini3", dt.datetime(2025, 9, 12, 13, 0, 0, tzinfo=dt.timezone.utc),
         "SYN-BAT-3001", 220.0, 48.0, 28.0, 4.0, 1, 0),
    ]
    for i, (aircraft, start, battery, duration, dist, height, speed, photos, video) in enumerate(flights):
        version = 11 if i % 2 == 0 else 8
        data = make_log_bytes(
            version=version, start=start, aircraft=aircraft, battery_sn=battery,
            duration_s=duration, distance_m=dist, max_height_m=height,
            max_h_speed=speed, photo_num=photos, video_time_s=video,
        )
        name = filename_time(start, "DJIFlightRecord" if version >= 10 else "FlightRecord")
        write_log(archive_dir / name, data)

    extra = make_log_bytes(
        version=11,
        start=dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.timezone.utc),
        aircraft="mavic2", battery_sn="SYN-BAT-1002", duration_s=298.0,
        distance_m=70.0, max_height_m=38.0,
    )
    write_log(extras_dir / "DJIFlightRecord_2026-05-01_[12-00-00].txt", extra)

    importer = Importer(root, conn)
    importer.import_folder(archive_dir, default_kind="archive")
    importer.import_folder(extras_dir, default_kind="extras")
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / \
        "AirData-Equipment-Capture-SYNTHETIC.md"
    doc = capture.parse_capture(fixture.read_text(encoding="utf-8"))
    capture.apply_capture(conn, doc, "Synthetic AirData capture")

    with db.tx(conn):
        db.set_setting(conn, "pilot_default", "Demo Pilot")
        db.set_setting(conn, "pilots", ["Demo Pilot", "Alex Example"])
        db.set_setting(conn, "demo_seeded", True)
        db.set_setting(conn, "unit_preference", "metric")
        rows = db.all_rows(conn, "SELECT id,aircraft_sn FROM flights ORDER BY start_time_utc")
        for i, row in enumerate(rows):
            pilot = "Demo Pilot" if i % 2 == 0 else "Alex Example"
            tags = ["mapping"] if i % 3 == 0 else (["practice"] if i % 3 == 1 else [])
            conn.execute("UPDATE flights SET pilot=?,tags_json=? WHERE id=?",
                         (pilot, json.dumps(tags), row["id"]))
        # a visible synthetic track on three flights
        picks = db.all_rows(
            conn,
            "SELECT id FROM flights WHERE start_time_utc IN (?,?,?) ORDER BY start_time_utc",
            (
                "2026-08-11T21:53:20+00:00",
                "2026-10-03T09:15:00+00:00",
                "2026-05-14T18:20:00+00:00",
            ),
        )
        for i, row in enumerate(picks):
            _inject_samples(conn, int(row["id"]), _samples(
                base, duration=302.0 + i * 100, gap=(80, 120) if i == 0 else None
            ))
        # charge-cycle history, including a replacement decrease for battery A
        battery_a = db.one(conn, "SELECT id FROM equipment WHERE serial='SYN-BAT-1001'")
        battery_b = db.one(conn, "SELECT id FROM equipment WHERE serial='SYN-BAT-1002'")
        if battery_a:
            for at, cycles in (
                ("2025-01-01T00:00:00+00:00", 30),
                ("2025-06-01T00:00:00+00:00", 55),
                ("2026-01-01T00:00:00+00:00", 80),
            ):
                conn.execute(
                    "INSERT OR IGNORE INTO battery_readings(battery_id,flight_id,at,charge_pct,"
                    "temp_c,cycles,voltage,source,temp_missing) VALUES(?,NULL,?,NULL,?,?,NULL,'log',0)",
                    (battery_a["id"], at, 21.0, cycles),
                )
        if battery_b:
            conn.execute(
                "INSERT OR IGNORE INTO battery_readings(battery_id,flight_id,at,charge_pct,"
                "temp_c,cycles,voltage,source,temp_missing) VALUES(?,NULL,?,NULL,?,?,NULL,'log',0)",
                (battery_b["id"], "2026-02-01T00:00:00+00:00", 20.0, 40),
            )
    statuses = maintenance.all_statuses(conn)
    counts = db.table_counts(conn)
    conn.close()
    return {
        "ok": True,
        "already_seeded": False,
        "flights": counts["flights"],
        "raw_files": counts["raw_files"],
        "equipment": counts["equipment"],
        "statuses": {s["equipment_name"] + "·" + s["code"]: s["status"] for s in statuses},
        "tracked": 3,
    }
