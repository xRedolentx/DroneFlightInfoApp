"""Battery temperature limits, and the compass start-up transient.

Two things are pinned here. First, that a stored temperature is actually
compared against a limit and reported honestly, including the case where no
reading exists. Second, that the known DJI compass quirk at motor start does not
count as a fault, while a compass flag in mid-flight still does.
"""
from __future__ import annotations

import json
import unittest

from support import TempCase

from flightdesk import analysis, climate, db, views


def _flight(conn, *, hi, lo, battery_id=None, when="2026-08-11T21:53:20+00:00") -> int:
    now = db.now_utc()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO flights(identity_status,start_time_utc,duration_s,"
            "battery_temp_max_c,battery_temp_min_c,battery_equipment_id,"
            "created_at,updated_at) VALUES('authoritative',?,302.0,?,?,?,?,?)",
            (when, hi, lo, battery_id, now, now),
        )
    return int(cur.lastrowid)


class LimitPolicyTest(TempCase):
    def test_defaults_are_the_apps_own_and_documented(self) -> None:
        policy = climate.limits(self.conn)
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["cold_c"], 0.0)
        self.assertEqual(policy["hot_c"], 40.0)

    def test_saved_limits_are_used(self) -> None:
        result = climate.save_limits(self.conn, {"cold_c": 5, "hot_c": 45})
        self.assertTrue(result["ok"])
        policy = climate.limits(self.conn)
        self.assertEqual(policy["cold_c"], 5.0)
        self.assertEqual(policy["hot_c"], 45.0)

    def test_a_nonsensical_pair_is_refused(self) -> None:
        result = climate.save_limits(self.conn, {"cold_c": 60, "hot_c": 10})
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "hot_below_cold")
        # The old policy survives a rejected change.
        self.assertEqual(climate.limits(self.conn)["hot_c"], 40.0)

    def test_absurd_values_are_ignored_not_stored(self) -> None:
        climate.save_limits(self.conn, {"hot_c": 9999})
        self.assertEqual(climate.limits(self.conn)["hot_c"], 40.0)

    def test_explicitly_disabling_the_check(self) -> None:
        climate.save_limits(self.conn, {"enabled": False})
        verdict = climate.classify(99.0, -99.0, climate.limits(self.conn))
        self.assertEqual(verdict["state"], "disabled")
        self.assertEqual(verdict["status"], "unknown")


class ClassifyTest(unittest.TestCase):
    POLICY = {"enabled": True, "cold_c": 0.0, "hot_c": 40.0, "margin_c": 10.0}

    def test_within_limits(self) -> None:
        verdict = climate.classify(35.0, 12.0, self.POLICY)
        self.assertEqual(verdict["status"], "ok")
        self.assertEqual(verdict["reasons"], [])

    def test_hot_is_a_caution_just_over_the_limit(self) -> None:
        verdict = climate.classify(44.0, 20.0, self.POLICY)
        self.assertEqual(verdict["status"], "caution")
        self.assertEqual(verdict["reasons"][0]["kind"], "hot")

    def test_hot_is_a_warning_far_over_the_limit(self) -> None:
        verdict = climate.classify(53.6, 20.0, self.POLICY)
        self.assertEqual(verdict["status"], "warning")
        self.assertAlmostEqual(verdict["reasons"][0]["over_by_c"], 13.6)

    def test_cold_is_reported_too(self) -> None:
        verdict = climate.classify(20.0, -5.0, self.POLICY)
        self.assertEqual(verdict["status"], "caution")
        self.assertEqual(verdict["reasons"][0]["kind"], "cold")

    def test_both_extremes_can_fire_together(self) -> None:
        verdict = climate.classify(60.0, -20.0, self.POLICY)
        kinds = {r["kind"] for r in verdict["reasons"]}
        self.assertEqual(kinds, {"hot", "cold"})
        self.assertEqual(verdict["status"], "warning")

    def test_no_reading_is_not_reported_as_ok(self) -> None:
        verdict = climate.classify(None, None, self.POLICY)
        self.assertEqual(verdict["state"], "no_reading")
        self.assertEqual(verdict["status"], "unknown")


class FlightWiringTest(TempCase):
    def test_a_hot_flight_says_so_in_its_record(self) -> None:
        # 53.6 C is 13.6 over the 40 C limit, past the 10 C margin, so warning.
        fid = _flight(self.conn, hi=53.6, lo=12.0)
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["temperature"]["status"], "warning")
        self.assertIn("53.6", detail["temperature"]["reasons"][0]["label"])

    def test_a_slightly_hot_flight_is_only_a_caution(self) -> None:
        fid = _flight(self.conn, hi=44.0, lo=12.0)
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["temperature"]["status"], "caution")

    def test_a_cool_flight_says_it_is_within_limits(self) -> None:
        fid = _flight(self.conn, hi=30.0, lo=15.0)
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["temperature"]["status"], "ok")

    def test_a_flight_without_a_reading_is_not_called_ok(self) -> None:
        fid = _flight(self.conn, hi=None, lo=None)
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["temperature"]["state"], "no_reading")


class BatterySummaryTest(TempCase):
    def _battery(self, name: str = "Bat-A") -> int:
        now = db.now_utc()
        with db.tx(self.conn):
            cur = self.conn.execute(
                "INSERT INTO equipment(kind,name,created_at,updated_at) "
                "VALUES('battery',?,?,?)", (name, now, now))
        return int(cur.lastrowid)

    def test_only_excursions_are_counted(self) -> None:
        bid = self._battery()
        _flight(self.conn, hi=30.0, lo=20.0, battery_id=bid, when="2026-01-01T10:00:00+00:00")
        _flight(self.conn, hi=52.0, lo=20.0, battery_id=bid, when="2026-02-01T10:00:00+00:00")
        _flight(self.conn, hi=20.0, lo=-8.0, battery_id=bid, when="2026-03-01T10:00:00+00:00")
        summary = climate.battery_summary(self.conn, bid)
        self.assertEqual(summary["flights_with_reading"], 3)
        self.assertEqual(summary["flights_too_hot"], 1)
        self.assertEqual(summary["flights_too_cold"], 1)
        self.assertEqual(summary["hottest_c"], 52.0)
        self.assertEqual(summary["coldest_c"], -8.0)

    def test_a_clean_battery_gains_no_permanent_alarm(self) -> None:
        bid = self._battery("Bat-Clean")
        _flight(self.conn, hi=30.0, lo=20.0, battery_id=bid)
        summary = climate.battery_summary(self.conn, bid)
        self.assertEqual(summary["excursion_count"], 0)
        self.assertEqual(climate.fleet_summary(self.conn)["count"], 0)

    def test_fleet_summary_lists_offenders_worst_first(self) -> None:
        warm = self._battery("Bat-Warm")
        hot = self._battery("Bat-Hot")
        _flight(self.conn, hi=45.0, lo=20.0, battery_id=warm, when="2026-01-01T10:00:00+00:00")
        for day in ("02", "03"):
            _flight(self.conn, hi=55.0, lo=20.0, battery_id=hot,
                    when=f"2026-01-{day}T10:00:00+00:00")
        fleet = climate.fleet_summary(self.conn)
        self.assertEqual(fleet["count"], 2)
        self.assertEqual(fleet["batteries"][0]["name"], "Bat-Hot")
        self.assertEqual(fleet["batteries"][0]["too_hot"], 2)

    def test_limits_are_reported_with_the_summary(self) -> None:
        bid = self._battery()
        summary = climate.battery_summary(self.conn, bid)
        self.assertEqual(summary["limits"]["hot_c"], 40.0)
        self.assertIn("cold_c", summary["limits"])


class CompassQuirkTest(unittest.TestCase):
    def _samples(self, warn, t=0.0, frames=1):
        out = [{"t": 0.0}]
        for i in range(frames):
            out.append({"t": t + i, "warn": warn})
        out.append({"t": t + frames + 30.0})
        return out

    def test_a_single_frame_compass_flag_at_start_is_a_transient(self) -> None:
        rows = analysis.summarise_warnings(self._samples({"isCompassError": 1}, t=0.0))
        compass = [r for r in rows if r["code"] == "isCompassError"]
        self.assertEqual(len(compass), 1)
        self.assertEqual(compass[0]["severity"], "startup")
        self.assertFalse(compass[0]["counted"])

    def test_a_sustained_compass_error_is_still_a_fault(self) -> None:
        rows = analysis.summarise_warnings(
            self._samples({"isCompassError": 1}, t=60.0, frames=20))
        compass = [r for r in rows if r["code"] == "isCompassError"]
        self.assertTrue(compass[0]["counted"])
        self.assertNotEqual(compass[0]["severity"], "startup")

    def test_a_compass_flag_late_in_the_flight_is_a_fault(self) -> None:
        rows = analysis.summarise_warnings(
            self._samples({"isCompassError": 1}, t=200.0, frames=1))
        compass = [r for r in rows if r["code"] == "isCompassError"]
        self.assertTrue(compass[0]["counted"])

    def test_other_faults_are_never_downgraded(self) -> None:
        rows = analysis.summarise_warnings(self._samples({"isMotorBlocked": 1}, t=0.0))
        self.assertTrue(rows[0]["counted"])
        self.assertNotEqual(rows[0]["severity"], "startup")

    def test_the_badge_counts_only_real_faults(self) -> None:
        rows = analysis.summarise_warnings(self._samples({"isCompassError": 1}, t=0.0))
        counted = sum(1 for r in rows if r.get("counted", True))
        self.assertEqual(counted, 0)


class TemperatureDropoutTest(unittest.TestCase):
    """The older Mavic reports 0 for a few frames inside a warm flight."""

    def _sample(self, t, temp):
        return {"t": float(t), "lat": 45.0, "lon": 9.0, "battery_c": temp}

    def test_zero_dropouts_do_not_become_a_freezing_minimum(self) -> None:
        samples = [self._sample(0, 0.0), self._sample(1, 0.0),
                   self._sample(2, 46.2), self._sample(3, 40.0), self._sample(4, 35.0)]
        m = analysis.compute_metrics(samples, {})
        self.assertAlmostEqual(m.battery_temp_min_c, 35.0)
        self.assertAlmostEqual(m.battery_temp_max_c, 46.2)
        self.assertIn("battery_temp_dropouts", m.flags)
        self.assertEqual(m.battery_temp_dropouts, 2)

    def test_a_genuinely_cold_flight_is_kept(self) -> None:
        samples = [self._sample(0, -1.0), self._sample(1, 0.0),
                   self._sample(2, 0.5), self._sample(3, -0.5)]
        m = analysis.compute_metrics(samples, {})
        self.assertAlmostEqual(m.battery_temp_min_c, -1.0)
        self.assertNotIn("battery_temp_dropouts", m.flags)

    def test_all_zero_readings_remain_unknown(self) -> None:
        samples = [self._sample(i, 0.0) for i in range(5)]
        m = analysis.compute_metrics(samples, {})
        self.assertIsNone(m.battery_temp_max_c)
        self.assertIn("battery_temp_absent", m.flags)


if __name__ == "__main__":
    unittest.main()
