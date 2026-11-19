"""Stage 6 + shared measurement tests: gaps, units, missing channels."""
from __future__ import annotations

import unittest

from support import TempCase

from flightdesk.analysis import compute_metrics, downsample, track_segments


def sample(t, lat=None, lon=None, height=None, alt=None, speed=None, battery=None,
           temp=None, signal=None, motor=True, home=(45.0, 9.0)):
    return {
        "t": t, "lat": lat, "lon": lon, "height_m": height, "alt_m": alt,
        "speed_mps": speed, "battery_pct": battery, "battery_c": temp,
        "signal": signal, "gps_num": 12, "gps_level": 4, "on_ground": not motor,
        "motor_on": motor, "home_lat": home[0], "home_lon": home[1],
        "home_alt_m": 100.0, "voltage": 15.5, "cells": None,
    }


class MetricTest(unittest.TestCase):
    def test_path_length_and_home_distance_are_different_measures(self) -> None:
        samples = [
            sample(0, 45.0, 9.0, height=0),
            sample(10, 45.001, 9.0, height=10),
            sample(20, 45.001, 9.001, height=20),
            sample(30, 45.0, 9.001, height=10),
            sample(40, 45.0, 9.0, height=0),
        ]
        m = compute_metrics(samples, {"total_time_s": 40.0})
        self.assertGreater(m.path_length_m, 300)
        self.assertLess(m.max_home_distance_m, m.path_length_m)
        # diagonal of one lat-second and one lon-second near 45 degrees
        self.assertAlmostEqual(m.max_home_distance_m, 136.0, delta=12.0)

    def test_height_above_takeoff_is_not_altitude(self) -> None:
        samples = [sample(0, 45.0, 9.0, height=0, alt=100),
                   sample(10, 45.0001, 9.0, height=40, alt=140)]
        m = compute_metrics(samples, {})
        self.assertAlmostEqual(m.max_height_m, 40.0, delta=0.5)
        self.assertAlmostEqual(m.max_altitude_m, 140.0, delta=0.5)

    def test_long_gap_is_not_drawn_as_a_straight_line(self) -> None:
        samples = [
            sample(0, 45.0, 9.0, height=10),
            sample(5, 45.0005, 9.0, height=10),
            sample(120, 45.02, 9.02, height=10),   # 115 s hole
            sample(125, 45.0205, 9.02, height=10),
        ]
        m = compute_metrics(samples, {}, max_gap_s=10)
        self.assertEqual(m.gap_count, 1)
        self.assertAlmostEqual(m.path_length_m, 111.0, delta=12.0)
        segments = track_segments(samples, m.gaps)
        self.assertEqual(len(segments), 2)
        self.assertEqual(sum(len(s) for s in segments), 4)

    def test_implausible_jump_is_flagged_not_counted(self) -> None:
        samples = [
            sample(0, 45.0, 9.0, height=10),
            sample(2, 46.5, 9.0, height=10),  # ~166 km in 2 s
        ]
        m = compute_metrics(samples, {}, max_gap_s=10)
        self.assertIn("poor_fix", m.flags)
        self.assertEqual(m.gap_count, 1)
        self.assertEqual(m.path_length_m, 0.0)

    def test_missing_fix_creates_a_gap_and_keeps_other_metrics(self) -> None:
        samples = [
            sample(0, 45.0, 9.0, height=10, battery=90, temp=25, signal=80),
            sample(5, None, None, height=20, battery=88, temp=26, signal=75),
            sample(10, 45.0002, None, height=25, battery=86, temp=27, signal=70),
            sample(15, 45.0003, 9.0003, height=20, battery=84, temp=28, signal=65),
        ]
        m = compute_metrics(samples, {})
        self.assertGreaterEqual(m.gap_count, 1)
        self.assertEqual(m.valid_fix_count, 2)
        self.assertIsNotNone(m.battery_temp_max_c)
        self.assertAlmostEqual(m.battery_temp_max_c, 28.0)
        self.assertEqual(m.signal_min, 65.0)

    def test_missing_channels_are_reported_as_missing(self) -> None:
        samples = [sample(0, 45.0, 9.0, height=10),
                   sample(10, 45.0001, 9.0, height=12)]
        m = compute_metrics(samples, {})
        self.assertIn("missing_signal", m.flags)
        self.assertIsNone(m.signal_min)
        self.assertIsNone(m.battery_temp_max_c)

    def test_all_zero_temperature_is_unknown_not_a_reading(self) -> None:
        # Some logs write 0 for every sample instead of omitting the field.
        samples = [sample(0, 45.0, 9.0, height=10, temp=0.0),
                   sample(10, 45.0001, 9.0, height=12, temp=0.0)]
        m = compute_metrics(samples, {})
        self.assertIsNone(m.battery_temp_max_c)
        self.assertIsNone(m.battery_temp_min_c)
        self.assertIn("battery_temp_absent", m.flags)

    def test_genuine_freezing_temperature_is_kept(self) -> None:
        samples = [sample(0, 45.0, 9.0, height=10, temp=-0.4),
                   sample(10, 45.0001, 9.0, height=12, temp=0.3)]
        m = compute_metrics(samples, {})
        self.assertAlmostEqual(m.battery_temp_max_c, 0.3)
        self.assertAlmostEqual(m.battery_temp_min_c, -0.4)
        self.assertNotIn("battery_temp_absent", m.flags)

    def test_no_samples_still_rejects_an_impossible_duration(self) -> None:
        # The details-only path must apply the same plausibility limits.
        m = compute_metrics([], {"total_time_s": 687808.0})
        self.assertIn("no_samples", m.flags)
        self.assertIsNone(m.duration_s)
        self.assertIn("implausible_duration_s", m.flags)

    def test_no_samples_falls_back_to_details_and_says_so(self) -> None:
        m = compute_metrics([], {"total_time_s": 302.0, "total_distance_m": 46.9,
                                 "max_height_m": 45.1})
        self.assertIn("no_samples", m.flags)
        self.assertEqual(m.duration_s, 302.0)
        self.assertEqual(m.total_distance_m, 46.9)
        self.assertIsNone(m.path_length_m)

    def test_implausible_measurements_become_unknown(self) -> None:
        # A corrupt details block once produced a 191-hour flight.
        m = compute_metrics([sample(0, 45.0, 9.0, height=10, speed=5.0)],
                            {"total_time_s": 687808.0})
        self.assertIsNone(m.duration_s)
        self.assertIn("implausible_duration_s", m.flags)
        fast = compute_metrics([sample(0, 45.0, 9.0, height=10, speed=128216.0)], {})
        self.assertIsNone(fast.max_h_speed_mps)
        self.assertIn("implausible_max_h_speed_mps", fast.flags)
        normal = compute_metrics([sample(0, 45.0, 9.0, height=10, speed=12.0)],
                                 {"total_time_s": 302.0})
        self.assertAlmostEqual(normal.duration_s, 302.0)
        self.assertAlmostEqual(normal.max_h_speed_mps, 12.0)

    def test_duration_kinds_stay_separate(self) -> None:
        samples = [sample(0, 45.0, 9.0, height=0, motor=False),
                   sample(60, 45.0, 9.0, height=0, motor=False),
                   sample(120, 45.001, 9.0, height=30, motor=True),
                   sample(180, 45.002, 9.0, height=30, motor=True)]
        m = compute_metrics(samples, {"total_time_s": 200.0})
        self.assertEqual(m.duration_s, 200.0)
        self.assertEqual(m.recorder_duration_s, 180.0)
        self.assertAlmostEqual(m.airborne_time_s, 120.0, delta=0.1)


class DownsampleTest(unittest.TestCase):
    def test_downsample_keeps_endpoints_and_order(self) -> None:
        samples = [sample(float(i), 45.0 + i * 1e-5, 9.0) for i in range(5000)]
        kept = downsample(samples, 400)
        self.assertLessEqual(len(kept), 401)
        self.assertEqual(kept[0]["t"], 0.0)
        self.assertEqual(kept[-1]["t"], 4999.0)
        self.assertEqual([s["t"] for s in kept], sorted(s["t"] for s in kept))


class UnitsTest(TempCase):
    def test_canonical_units_are_metric_in_storage(self) -> None:
        profile = {"total_time_s": 302.0, "total_distance_m": 46.9, "max_height_m": 45.1}
        m = compute_metrics([sample(0, 45.0, 9.0, height=1.0, temp=28.0)], profile)
        self.assertEqual(m.duration_s, 302.0)
        self.assertEqual(m.max_height_m, 45.1)
        self.assertEqual(m.battery_temp_max_c, 28.0)


if __name__ == "__main__":
    unittest.main()
