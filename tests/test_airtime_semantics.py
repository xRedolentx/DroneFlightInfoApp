"""P07/F10–F11: air time means off-ground, and captured limits are honoured.

Missing `isOnGround` was coerced to False, so a stationary record counted as
airborne. Maintenance summed the whole log duration while the dashboard used
air time. The first capture created default rules before applying the captured
thresholds, and the existing-row path never updated them.
"""
from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from support import TempCase

from flightdesk import capture, db, maintenance
from flightdesk.analysis import compute_metrics
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome, sample_from_frame


def frame(t: float, ground=None, motor=None, height=None, lat=45.0, lon=12.0) -> dict:
    osd: dict = {"flyTime": t}
    if ground is not None:
        osd["isOnGround"] = ground
    if motor is not None:
        osd["isMotorOn"] = motor
    if height is not None:
        osd["height"] = height
    if lat is not None:
        osd["latitude"] = lat
        osd["longitude"] = lon
    return {"osd": osd}


class AdapterTriStateTest(unittest.TestCase):
    def test_missing_ground_flag_is_unknown_not_false(self) -> None:
        sample = sample_from_frame(frame(10))
        self.assertIsNone(sample["on_ground"])
        self.assertIsNone(sample["motor_on"])

    def test_explicit_flags_are_kept(self) -> None:
        self.assertIs(sample_from_frame(frame(10, ground=True))["on_ground"], True)
        self.assertIs(sample_from_frame(frame(10, ground=False))["on_ground"], False)
        self.assertIs(sample_from_frame(frame(10, motor=0))["motor_on"], False)

    def test_string_and_numeric_flags(self) -> None:
        self.assertIs(sample_from_frame(frame(10, ground="false"))["on_ground"], False)
        self.assertIs(sample_from_frame(frame(10, ground=1))["on_ground"], True)


class AdapterToAnalysisTest(unittest.TestCase):
    def _samples(self, *frames) -> list[dict]:
        return [s for s in (sample_from_frame(f) for f in frames) if s is not None]

    def test_stationary_without_ground_flag_is_not_airborne(self) -> None:
        # Two frames ten seconds apart, motors off, no ground flag: the old
        # adapter turned the missing flag into False and counted ten seconds of
        # air time.
        samples = self._samples(frame(0, motor=False, height=0),
                                frame(10, motor=False, height=0))
        metrics = compute_metrics(samples)
        self.assertEqual(metrics.airborne_time_s, 0.0)

    def test_no_telemetry_state_at_all_is_unknown_air_time(self) -> None:
        samples = self._samples(frame(0, lat=None), frame(10, lat=None))
        metrics = compute_metrics(samples)
        self.assertIsNone(metrics.airborne_time_s)
        self.assertIn("airborne_unknown", metrics.flags)

    def test_ground_flag_counts_air_without_a_gps_fix(self) -> None:
        samples = self._samples(frame(0, ground=True, lat=None),
                                frame(10, ground=False, lat=None),
                                frame(20, ground=False, lat=None),
                                frame(30, ground=True, lat=None))
        metrics = compute_metrics(samples)
        self.assertEqual(metrics.airborne_time_s, 20.0)
        # Route figures stay unavailable; air time does not depend on them.
        self.assertIsNone(metrics.path_length_m)
        self.assertEqual(metrics.valid_fix_count, 0)

    def test_mixed_known_and_unknown_intervals_are_partial(self) -> None:
        # Known ground, then an unknown stretch, then known air.
        samples = self._samples(frame(0, ground=True),
                                frame(10, ground=True),
                                frame(20, lat=45.0),
                                frame(30, ground=False),
                                frame(40, ground=False))
        metrics = compute_metrics(samples)
        self.assertEqual(metrics.airborne_time_s, 20.0)
        self.assertIn("airborne_partial", metrics.flags)


def airborne_samples(air_from: float, air_to: float, total: float = 600.0,
                     step: float = 10.0) -> list[dict]:
    out = []
    t = 0.0
    while t <= total:
        on_ground = not (air_from <= t < air_to)
        out.append({
            "t": t, "lat": 45.0 + t * 1e-6, "lon": 12.0, "height_m": 0.0 if on_ground else 50.0,
            "alt_m": 100.0, "battery_pct": 90.0, "battery_c": 25.0,
            "on_ground": on_ground, "motor_on": not on_ground, "signal": 80,
            "gps_num": 12, "home_lat": 45.0, "home_lon": 12.0,
        })
        t += step
    return out


class MaintenanceAirTimeTest(TempCase):
    def _seed(self, samples, duration=600.0, aircraft="SN-A", limit_minutes=5):
        now = db.now_utc()
        with db.tx(self.conn):
            self.conn.execute(
                "INSERT INTO equipment(kind,name,serial,created_at,updated_at) "
                "VALUES('aircraft','Test Aircraft',?,?,?)", (aircraft, now, now))
        parsed = ParseOutcome(11, "decoded",
                              details={"start_time_utc": "2026-01-01T00:00:00+00:00",
                                       "aircraft_sn": aircraft, "aircraft_name": "Test Aircraft",
                                       "total_time_s": duration, "total_distance_m": 10.0},
                              samples=samples)
        with patch("flightdesk.importer.parse_file", return_value=parsed):
            Importer(self.root, self.conn).import_stream(
                io.BytesIO(b"a"), "FlightRecord_2026-01-01_[00-00-00].txt",
                source_kind="manual", source_label="t")
        equipment_id = db.one(self.conn, "SELECT id FROM equipment")["id"]
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE flights SET aircraft_equipment_id=?", (equipment_id,))
            rule_id = self.conn.execute(
                "INSERT INTO rules(equipment_id,code,label,air_minutes_limit,source,"
                "created_at,updated_at) VALUES(?,'basic','Basic',?,'AirData',?,?)",
                (equipment_id, limit_minutes, now, now)).lastrowid
        return int(rule_id)

    def test_log_duration_does_not_become_air_minutes(self) -> None:
        # 600 s log, 60 s off the ground: one air minute, not ten.
        rule_id = self._seed(airborne_samples(100.0, 160.0), duration=600.0,
                             limit_minutes=5)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
        status = maintenance.rule_status(self.conn, rule)
        minutes = next(c for c in status["counters"] if c["metric"] == "minutes")
        self.assertAlmostEqual(minutes["value"], 1.0, places=1)
        self.assertEqual(status["status"], "ok")

    def test_unknown_air_time_makes_the_counter_partial_not_zero(self) -> None:
        # No ground/motor/height evidence at all -> unknown air time.
        blank = [{"t": float(i), "lat": 45.0, "lon": 12.0} for i in range(0, 60, 10)]
        rule_id = self._seed(blank, duration=600.0, limit_minutes=5)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
        status = maintenance.rule_status(self.conn, rule)
        minutes = next(c for c in status["counters"] if c["metric"] == "minutes")
        self.assertIsNone(minutes["value"])
        self.assertEqual(status["status"], "unknown")


class CapturedLimitsTest(TempCase):
    def _capture_doc(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" /
                "AirData-Equipment-Capture-SYNTHETIC.md").read_text(encoding="utf-8")
        return capture.parse_capture(text), text

    def test_first_capture_applies_the_captured_limit(self) -> None:
        doc, _ = self._capture_doc()
        snap = next(x for x in doc.snapshots if x.counters
                    and any(y["metric"] == "flights" for y in x.counters))
        counter = next(x for x in snap.counters if x["metric"] == "flights")
        counter["limit"] = 7
        capture.apply_capture(self.conn, doc, "SYNTHETIC")
        equipment = capture._find_equipment(self.conn, snap.equipment_name, snap.serial)
        rule = db.one(self.conn, "SELECT flights_limit FROM rules WHERE equipment_id=? AND code=?",
                      (equipment["id"], snap.rule))
        self.assertEqual(rule["flights_limit"], 7)

    def test_a_later_capture_preserves_an_owner_edited_limit(self) -> None:
        doc, _ = self._capture_doc()
        capture.apply_capture(self.conn, doc, "SYNTHETIC")
        snap = next(x for x in doc.snapshots if x.counters
                    and any(y["metric"] == "flights" for y in x.counters))
        equipment = capture._find_equipment(self.conn, snap.equipment_name, snap.serial)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE equipment_id=? AND code=?",
                      (equipment["id"], snap.rule))
        # The owner sets a policy of their own.
        result = maintenance.set_rule_policy(
            self.conn, int(rule["id"]), flights_limit=33)
        self.assertTrue(result["ok"], result)
        # A second, different capture restates the AirData value.
        from pathlib import Path
        text2 = (Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" /
                 "AirData-Equipment-Capture-SYNTHETIC.md").read_text(encoding="utf-8") + "\n"
        doc2 = capture.parse_capture(text2)
        capture.apply_capture(self.conn, doc2, "SYNTHETIC-2")
        after = db.one(self.conn, "SELECT flights_limit,owner_limits FROM rules WHERE id=?",
                       (rule["id"],))
        self.assertEqual(after["flights_limit"], 33)
        self.assertEqual(after["owner_limits"], 1)


class CalendarBaselineTest(TempCase):
    def test_an_old_service_captured_recently_does_not_move_the_clock(self) -> None:
        now = db.now_utc()
        with db.tx(self.conn):
            equipment_id = self.conn.execute(
                "INSERT INTO equipment(kind,name,serial,created_at,updated_at) "
                "VALUES('aircraft','Calendar Craft','SN-CAL',?,?)", (now, now)).lastrowid
            rule_id = self.conn.execute(
                "INSERT INTO rules(equipment_id,code,label,days_limit,source,"
                "created_at,updated_at) VALUES(?,'quarterly','Quarterly',90,'AirData',?,?)",
                (equipment_id, now, now)).lastrowid
            snapshot_id = self.conn.execute(
                "INSERT INTO service_snapshots(source_label,source_hash,captured_at,"
                "covered_through,source_kind,created_at) "
                "VALUES('SYN','cal-hash',?,?,'capture',?)",
                (now, "2026-06-01", now)).lastrowid
            self.conn.execute(
                "INSERT INTO snapshot_counters(snapshot_id,equipment_id,rule_code,metric,"
                "value,unit,rounded) VALUES(?,?, 'quarterly','days',0,'days',0)",
                (snapshot_id, equipment_id))
            # An old service the owner records today, before the snapshot date.
            self.conn.execute(
                "INSERT INTO services(equipment_id,rule_id,kind,occurred_at,source,created_at) "
                "VALUES(?,?,'completed','2025-01-01','test',?)",
                (equipment_id, rule_id, now))
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rule_id,))
        status = maintenance.rule_status(self.conn, rule)
        # The snapshot is newer than the service, so the snapshot basis wins;
        # the service date is not conflated with the capture date.
        self.assertNotEqual(status.get("basis"), "service")


if __name__ == "__main__":
    unittest.main()
