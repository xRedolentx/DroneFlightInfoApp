"""Per-flight fault warnings: what counts as a fault, and how it is summarised.

The real archive was used to decide what belongs here. Several OSD fields are
constant and benign (``isImuPreheated`` is True on every frame of every flight;
``imuInitFailReason`` reads ``MonitorError`` throughout), so treating "field
present and truthy" as a fault would raise an alarm on every flight forever.
Only flags whose true value is an actual fault are kept.
"""
from __future__ import annotations

import json
import unittest

from support import TempCase

from flightdesk import analysis, db, views
from flightdesk.parser_adapter import frame_warnings, sample_from_frame

SETTLE = 1.5


def frame(**osd) -> dict:
    base = {"flyTime": 10.0, "latitude": 45.0, "longitude": 9.0, "height": 20.0}
    base.update(osd)
    return {"osd": base, "battery": {"chargeLevel": 90}, "home": {"latitude": 45.0, "longitude": 9.0}}


def samples(*flags) -> list[dict]:
    """Samples at 1 s intervals; each entry is the warn dict for that second."""
    out = []
    for i, warn in enumerate(flags):
        s = {"t": float(i)}
        if warn:
            s["warn"] = warn
        out.append(s)
    return out


class ExtractionTest(unittest.TestCase):
    def test_a_clean_frame_reports_nothing(self) -> None:
        self.assertEqual(frame_warnings(frame()), {})

    def test_each_fault_flag_is_recognised(self) -> None:
        for flag in ("isCompassError", "isMotorBlocked", "isNotEnoughForce",
                     "isPropellerCatapult", "isVibrating", "isBarometerDeadInAir",
                     "isAcceletorOverRange", "waveError"):
            self.assertEqual(frame_warnings(frame(**{flag: True})), {flag: 1}, flag)

    def test_benign_constants_never_raise_a_warning(self) -> None:
        # These appear on every frame of every real flight and mean nothing is wrong.
        self.assertEqual(
            frame_warnings(frame(isImuPreheated=True, imuInitFailReason="MonitorError",
                                 nonGpsCause="Already", isGpsUsed=True,
                                 isVisionUsed=True, voltageWarning=0)),
            {})

    def test_voltage_warning_only_when_the_level_is_raised(self) -> None:
        self.assertEqual(frame_warnings(frame(voltageWarning=0)), {})
        self.assertEqual(frame_warnings(frame(voltageWarning=1)), {"voltageWarning": 1})
        self.assertEqual(frame_warnings(frame(voltageWarning=3)), {"voltageWarning": 3})

    def test_sd_card_states(self) -> None:
        for good in ("Normal", "None"):
            f = frame()
            f["camera"] = {"sdCardState": good}
            self.assertEqual(frame_warnings(f), {}, good)
        f = frame()
        f["camera"] = {"sdCardState": "Full"}
        self.assertEqual(frame_warnings(f), {"sdCard": "Full"})

    def test_gimbal_stuck_is_a_fault_but_travel_limits_are_not(self) -> None:
        f = frame()
        f["gimbal"] = {"isStuck": True, "isPitchAtLimit": True, "isYawAtLimit": True}
        self.assertEqual(frame_warnings(f), {"gimbalStuck": 1})

    def test_motor_start_failure_carries_its_cause(self) -> None:
        self.assertEqual(frame_warnings(frame(motorStartFailedCause="None")), {})
        self.assertEqual(frame_warnings(frame(motorStartFailedCause="Stuck")),
                         {"motorStartFailed": "Stuck"})

    def test_sample_from_frame_omits_the_key_entirely_when_clean(self) -> None:
        clean = sample_from_frame(frame())
        self.assertNotIn("warn", clean)
        flagged = sample_from_frame(frame(isCompassError=True))
        self.assertEqual(flagged["warn"], {"isCompassError": 1})


class SummaryTest(unittest.TestCase):
    def test_a_sustained_flag_is_one_row_not_many(self) -> None:
        rows = analysis.summarise_warnings(
            samples(None, {"isCompassError": 1}, {"isCompassError": 1},
                    {"isCompassError": 1}, None))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "isCompassError")
        self.assertEqual(rows[0]["frames"], 3)
        self.assertAlmostEqual(rows[0]["start_t"], 1.0)
        self.assertAlmostEqual(rows[0]["end_t"], 3.0)
        self.assertAlmostEqual(rows[0]["seconds"], 2.0)

    def test_two_separate_occurrences_stay_separate(self) -> None:
        rows = analysis.summarise_warnings(
            samples({"voltageWarning": 1}, None, {"voltageWarning": 1}, None))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["start_t"] for r in rows], [0.0, 2.0])

    def test_a_flag_still_true_at_the_end_is_closed_at_the_last_sample(self) -> None:
        rows = analysis.summarise_warnings(samples(None, {"waveError": 1}, {"waveError": 1}))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["end_t"], 2.0)
        self.assertAlmostEqual(rows[0]["seconds"], 1.0)

    def test_severity_and_label_are_human_readable(self) -> None:
        rows = analysis.summarise_warnings(samples({"isNotEnoughForce": 1}))
        self.assertEqual(rows[0]["severity"], "warning")
        self.assertIn("Not enough force", rows[0]["label"])
        rows = analysis.summarise_warnings(samples({"isOutOfLimit": 1}))
        self.assertEqual(rows[0]["severity"], "caution")

    def test_peak_value_is_kept_for_a_levelled_warning(self) -> None:
        rows = analysis.summarise_warnings(
            samples({"voltageWarning": 1}, {"voltageWarning": 3}, None))
        self.assertEqual(rows[0]["peak"], 1)  # first value seen
        self.assertIn("level 1", rows[0]["label"])

    def test_clean_samples_produce_no_rows(self) -> None:
        self.assertEqual(analysis.summarise_warnings(samples(None, None, None)), [])
        self.assertEqual(analysis.summarise_warnings([]), [])

    def test_rows_are_json_serialisable(self) -> None:
        rows = analysis.summarise_warnings(samples({"sdCard": "Full"}, None))
        json.dumps(rows)  # must not raise
        self.assertEqual(rows[0]["code"], "sdCard")


class ProvenanceTest(TempCase):
    """Three states must never collapse into 'no warnings'."""

    def _flight(self, warnings_json, metrics_source="frames") -> int:
        now = db.now_utc()
        with db.tx(self.conn):
            cur = self.conn.execute(
                "INSERT INTO flights(identity_status,start_time_utc,duration_s,"
                "metrics_source,warnings_json,created_at,updated_at) "
                "VALUES('authoritative','2026-08-11T21:53:20+00:00',302.0,?,?,?,?)",
                (metrics_source, warnings_json, now, now),
            )
        return int(cur.lastrowid)

    def test_recorded_with_faults(self) -> None:
        fid = self._flight(json.dumps([{"code": "voltageWarning", "label": "x",
                                        "severity": "warning", "start_t": 12.0,
                                        "end_t": 18.0, "seconds": 6.0,
                                        "frames": 6, "peak": 1, "value": None}]))
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["warnings"]["state"], "recorded")
        self.assertEqual(len(detail["warnings"]["items"]), 1)
        self.assertEqual(detail["flight"]["warnings_count"], 1)

    def test_recorded_and_clean_is_not_the_same_as_unrecorded(self) -> None:
        clean = self._flight("[]")
        unrecorded = self._flight(None)
        clean_detail = views.flight_detail(self.conn, clean)
        unrecorded_detail = views.flight_detail(self.conn, unrecorded)
        self.assertEqual(clean_detail["warnings"]["state"], "recorded")
        self.assertEqual(clean_detail["warnings"]["items"], [])
        self.assertEqual(unrecorded_detail["warnings"]["state"], "unavailable")
        self.assertIsNone(views.flight_detail(self.conn, unrecorded)["flight"]["warnings_count"])
        self.assertEqual(views.flight_detail(self.conn, clean)["flight"]["warnings_count"], 0)

    def test_an_encrypted_record_says_faults_are_pending(self) -> None:
        fid = self._flight(None, metrics_source="metadata_only")
        detail = views.flight_detail(self.conn, fid)
        self.assertEqual(detail["warnings"]["state"], "pending")
        self.assertIn("DJI key", detail["warnings"]["note"])

    def test_unreadable_json_does_not_claim_cleanliness(self) -> None:
        fid = self._flight("{not json")
        self.assertEqual(views.flight_detail(self.conn, fid)["warnings"]["state"],
                         "unavailable")


if __name__ == "__main__":
    unittest.main()
