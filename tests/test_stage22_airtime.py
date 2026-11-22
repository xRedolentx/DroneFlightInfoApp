"""Air time means time off the ground, and it is not the log duration.

Calibration against AirData, measured 2026-11-22 on the real archive:

    our archive-scoped log duration   24h21m   AirData "Total Log Time"   24h20m
    our archive-scoped air time       24h10m   AirData "Total Air Time"   24h10m

Before this change the app counted "motors on" as airborne, which gave 24h19m —
almost exactly the log duration, so the two figures were the same number wearing
two names. The 10-minute difference the checkpoint called unexplained was not
unexplained: it was two different quantities being compared.

All fixtures are invented.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from support import TempCase, log_bytes

from flightdesk import db, report, views
from flightdesk.analysis import compute_metrics

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "flightdesk" / "web" / "app.js").read_text(encoding="utf-8")


def sample(t, *, on_ground=None, motor=True, height=10.0, lat=45.0, lon=9.0):
    data = {
        "t": float(t), "lat": lat, "lon": lon, "height_m": height, "alt_m": 100.0 + height,
        "speed_mps": 5.0, "battery_pct": 90.0, "battery_c": 25.0, "signal": 80,
        "gps_num": 12, "gps_level": 4, "motor_on": motor,
        "home_lat": 45.0, "home_lon": 9.0, "home_alt_m": 100.0,
        "voltage": 15.6, "cells": None,
    }
    if on_ground is not None:
        data["on_ground"] = on_ground
    return data


class AirborneDefinitionTest(unittest.TestCase):
    def test_ground_time_is_excluded_even_when_the_motors_are_on(self) -> None:
        # The shape that was getting this wrong: motors armed from the first
        # sample, but the aircraft sits on the ground before and after the
        # flight. Each interval is attributed to the state at its end, so two
        # ground samples at each end make the excluded time unambiguous.
        samples = [
            sample(0, on_ground=True),
            sample(10, on_ground=True),
            sample(20, on_ground=False),
            sample(40, on_ground=False),
            sample(50, on_ground=True),
        ]
        m = compute_metrics(samples, {"total_time_s": 50.0})
        self.assertAlmostEqual(m.duration_s, 50.0)
        # 10-20 and 20-40 are the airborne intervals; 0-10 and 40-50 are not.
        self.assertAlmostEqual(m.airborne_time_s, 30.0, delta=0.1)

    def test_a_flight_that_never_leaves_the_ground_has_no_air_time(self) -> None:
        samples = [sample(t, on_ground=True) for t in (0, 10, 20)]
        m = compute_metrics(samples, {"total_time_s": 20.0})
        self.assertEqual(m.airborne_time_s, 0.0)
        self.assertAlmostEqual(m.duration_s, 20.0)

    def test_a_legacy_log_without_the_flag_falls_back_and_says_so(self) -> None:
        # No on_ground anywhere: the motor-or-height rule still applies, and the
        # metrics carry a flag so nobody mistakes it for the same measurement.
        samples = [sample(t, motor=True, height=20.0) for t in (0, 10, 20)]
        m = compute_metrics(samples, {"total_time_s": 20.0})
        self.assertAlmostEqual(m.airborne_time_s, 20.0, delta=0.1)
        self.assertIn("airborne_from_motor", m.flags)

    def test_the_flag_is_absent_when_the_log_reports_ground_state(self) -> None:
        samples = [sample(t, on_ground=(t == 0)) for t in (0, 10, 20)]
        m = compute_metrics(samples, {"total_time_s": 20.0})
        self.assertNotIn("airborne_from_motor", m.flags)


class TotalsTest(TempCase):
    def _two_flights(self) -> None:
        self.import_bytes(log_bytes(11, days=0), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.import_bytes(log_bytes(11, days=1), "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        rows = db.all_rows(self.conn, "SELECT id FROM flights ORDER BY id")
        self.conn.execute("UPDATE flights SET duration_s=600.0, airborne_time_s=540.0 WHERE id=?",
                          (rows[0]["id"],))
        self.conn.execute("UPDATE flights SET duration_s=300.0, airborne_time_s=270.0 WHERE id=?",
                          (rows[1]["id"],))
        self.conn.commit()

    def test_the_tile_total_is_air_time_and_log_time_is_reported_beside_it(self) -> None:
        self._two_flights()
        totals = views.flights(self.conn, {})["totals"]
        self.assertAlmostEqual(totals["air_minutes"], 810.0 / 60.0, places=2)
        self.assertAlmostEqual(totals["log_minutes"], 900.0 / 60.0, places=2)
        self.assertTrue(totals["air_complete"])
        self.assertEqual(totals["air_missing"], 0)

    def test_a_flight_without_samples_is_counted_as_missing_not_as_zero(self) -> None:
        self._two_flights()
        self.conn.execute("UPDATE flights SET airborne_time_s=NULL WHERE id=1")
        self.conn.commit()
        totals = views.flights(self.conn, {})["totals"]
        self.assertFalse(totals["air_complete"])
        self.assertEqual(totals["air_missing"], 1)
        # The total is what is known, not the known plus a zero for the gap.
        self.assertAlmostEqual(totals["air_minutes"], 270.0 / 60.0, places=2)


class ReportTest(TempCase):
    def test_the_archive_comparison_uses_air_time_and_keeps_log_time(self) -> None:
        self.import_bytes(log_bytes(11, days=0), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.conn.execute(
            "UPDATE flights SET duration_s=600.0, airborne_time_s=540.0, total_distance_m=1609.344"
        )
        self.conn.commit()
        decision = report.reconciliation(self.conn, persist=False)
        group = decision["groups"]["archive"]
        self.assertAlmostEqual(group["air_minutes"], 9.0, places=2)
        self.assertAlmostEqual(group["log_minutes"], 10.0, places=2)
        self.assertEqual(group["air_time"], "9m00s")
        self.assertEqual(group["log_time"], "10m00s")

    def test_the_csv_carries_both_clocks(self) -> None:
        self.import_bytes(log_bytes(11, days=0), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.conn.execute(
            "UPDATE flights SET duration_s=600.0, airborne_time_s=540.0"
        )
        self.conn.commit()
        text = report.flights_csv(self.conn, redact=False)
        header = text.splitlines()[0]
        self.assertIn("air_time_s", header)
        self.assertIn("air_minutes", header)
        self.assertIn("log_minutes", header)


class WordingTest(unittest.TestCase):
    def test_the_dashboard_names_both_clocks(self) -> None:
        self.assertIn('tile("Air time"', APP_JS)
        self.assertIn('"log time "', APP_JS)
        self.assertIn('metricCard("Log time"', APP_JS)
        self.assertIn('kv("Air time (off the ground)"', APP_JS)
        self.assertIn('kv("Log time (record duration)"', APP_JS)

    def test_a_record_card_never_calls_the_log_duration_air(self) -> None:
        self.assertIn("flightAir(f)", APP_JS)
        self.assertNotIn("fmtDuration(f.duration_s) + \"</b> air</span>\"", APP_JS)


if __name__ == "__main__":
    unittest.main()
