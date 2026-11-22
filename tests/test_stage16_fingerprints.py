"""A log with no readable details must not share an identity with another one.

Found by the independent review: `_fingerprint` hashed `"||0.0"` for a log whose
details block cannot be read, so every such log received the same fingerprint
and the second one was linked to the first as a "variant". Flights 216 and 283
survived only because 216 still carried a fingerprint from an older build.

Also covered: the fingerprint went stale after a reparse because the metrics
update never rewrote it. All fixtures are invented.
"""
from __future__ import annotations

import struct
import unittest

from support import TempCase, log_bytes

from flightdesk import db, fingerprints
from flightdesk.importer import _fingerprint


def zero_offset_log(*, distance: float = 46.9) -> bytes:
    """A v11 log whose stored details offset is 0 — the unreadable shape."""
    data = bytearray(log_bytes(11, duration=302.0, distance=distance))
    struct.pack_into("<Q", data, 0, 0)
    return bytes(data)


class UnreadableIdentityTest(TempCase):
    def test_two_unreadable_logs_a_month_apart_are_two_flights(self) -> None:
        first = zero_offset_log(distance=46.9)
        second = bytearray(zero_offset_log(distance=46.9))
        # Byte-different from the first so the content hash does not dedupe it.
        struct.pack_into("<B", second, 20, 7)
        self.import_bytes(bytes(first), "DJIFlightRecord_2025-05-16_[10-14-39].txt")
        self.import_bytes(bytes(second), "DJIFlightRecord_2025-06-25_[09-00-00].txt")

        counts = self.counts()
        self.assertEqual(counts["flights"], 2, counts)
        self.assertEqual(counts["raw"], 2, counts)
        roles = [r["role"] for r in db.all_rows(
            self.conn, "SELECT role FROM flight_files ORDER BY rowid")]
        self.assertEqual(roles, ["primary", "primary"], roles)
        # Neither record is attributed to the other.
        rows = db.all_rows(self.conn, "SELECT * FROM flights ORDER BY id")
        self.assertEqual({r["filename_time"] for r in rows},
                         {"2025-05-16T10:14:39", "2025-06-25T09:00:00"})
        for row in rows:
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(row["excluded_reason"], "details_unreadable")

    def test_the_same_unreadable_file_twice_is_still_one_flight(self) -> None:
        # Re-scanning the same controller must not manufacture a flight per pass.
        data = zero_offset_log()
        name = "DJIFlightRecord_2025-05-16_[10-14-39].txt"
        self.import_bytes(data, name)
        totals = self.import_bytes(data, name)
        self.assertEqual(totals["already_imported"], 1, totals)
        self.assertEqual(self.counts()["flights"], 1)

    def test_an_unreadable_log_gets_no_fingerprint_without_a_date(self) -> None:
        # No log start and no name-derived date: nothing to identify it by.
        self.import_bytes(zero_offset_log(), "DJIFlightRecord.txt")
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertIsNone(row["start_time_utc"])
        self.assertIsNone(row["filename_time"])
        self.assertIsNone(row["fingerprint"])
        # The details guard names the reason, which is the more specific one.
        self.assertIn(row["excluded_reason"], ("no_start_time", "details_unreadable"))

    def test_a_fingerprint_from_a_name_never_matches_a_decoded_start(self) -> None:
        # Same aircraft, same second, but one is a filename claim and the other
        # a decoded start. They are not the same evidence, so not the same id.
        self.assertNotEqual(
            _fingerprint("SN", "2025-05-16T10:14:39+00:00", 0.0, "2025-05-16T10:14:39"),
            _fingerprint("SN", None, 0.0, "2025-05-16T10:14:39"),
        )


class StaleFingerprintTest(TempCase):
    def test_a_reparse_that_changes_duration_updates_the_fingerprint(self) -> None:
        self.import_bytes(log_bytes(11, duration=302.0, distance=46.9),
                          "FlightRecord_2026-08-11_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM flights")
        # A fingerprint that was stored for the old (wrong) duration.
        stale = _fingerprint("SYN-M2P-0001", "2026-08-11T21:53:20+00:00", 401.0,
                             "2026-08-11T21:53:20")
        self.conn.execute("UPDATE flights SET duration_s=?, fingerprint=? WHERE id=?",
                          (401.0, stale, row["id"]))
        self.conn.commit()
        raw_id = int(db.one(self.conn, "SELECT id FROM raw_files")["id"])
        self.t.importer().reparse_raw(raw_id)
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertAlmostEqual(row["duration_s"], 302.0, places=1)
        self.assertNotEqual(row["fingerprint"], stale)
        self.assertEqual(
            row["fingerprint"],
            _fingerprint("SYN-M2P-0001", "2026-08-11T21:53:20+00:00", 302.0,
                         "2026-08-11T21:53:20"),
        )


class RepairReportTest(TempCase):
    def test_recompute_reports_without_writing_by_default(self) -> None:
        self.import_bytes(log_bytes(11), "FlightRecord_2026-08-11_[21-53-20].txt")
        self.conn.execute("UPDATE flights SET fingerprint='stale'")
        self.conn.commit()
        report = fingerprints.recompute(self.conn, apply=False)
        self.assertEqual(report["changed"], 1, report)
        self.assertFalse(report["applied"])
        row = db.one(self.conn, "SELECT fingerprint FROM flights")
        self.assertEqual(row["fingerprint"], "stale")

        report = fingerprints.recompute(self.conn, apply=True)
        self.assertEqual(report["changed"], 1, report)
        row = db.one(self.conn, "SELECT fingerprint FROM flights")
        self.assertNotEqual(row["fingerprint"], "stale")

    def test_a_flight_with_conflicting_name_dates_is_reported(self) -> None:
        first = log_bytes(11, duration=302.0, distance=46.9)
        self.import_bytes(first, "FlightRecord_2026-08-11_[21-53-20].txt")
        # A byte-different variant under a name that claims another date.
        variant = bytearray(first)
        struct.pack_into("<B", variant, 20, 9)
        self.import_bytes(bytes(variant), "FlightRecord_2026-09-30_[08-00-00].txt")
        self.assertEqual(self.counts()["flights"], 1)
        self.assertEqual(self.counts()["raw"], 2)
        report = fingerprints.recompute(self.conn)
        mismatches = report["multi_file_mismatches"]
        self.assertEqual(len(mismatches), 1, mismatches)
        self.assertEqual(len(mismatches[0]["files"]), 2)
        self.assertEqual(mismatches[0]["kind"], "different")
        self.assertGreater(mismatches[0]["spread_seconds"], 3600)
        self.assertEqual(
            sorted(str(f["filename_time"]) for f in mismatches[0]["files"]),
            ["2026-08-11T21:53:20", "2026-09-30T08:00:00"],
        )

    def test_a_timezone_difference_is_not_called_a_mismatch(self) -> None:
        # Two controllers write the same flight two hours apart in the name.
        first = log_bytes(11, duration=302.0, distance=46.9)
        self.import_bytes(first, "FlightRecord_2026-08-11_[21-53-20].txt")
        variant = bytearray(first)
        variant[20] = 11
        self.import_bytes(bytes(variant), "FlightRecord_2026-08-11_[19-53-21].txt")
        report = fingerprints.recompute(self.conn)
        mismatches = report["multi_file_mismatches"]
        self.assertEqual(len(mismatches), 1, mismatches)
        self.assertEqual(mismatches[0]["kind"], "timezone_offset")

    def test_a_clean_archive_reports_nothing(self) -> None:
        self.import_bytes(log_bytes(11), "FlightRecord_2026-08-11_[21-53-20].txt")
        report = fingerprints.recompute(self.conn)
        self.assertEqual(report["changed"], 0, report)
        self.assertEqual(report["collisions"], [])
        self.assertEqual(report["multi_file_mismatches"], [])


if __name__ == "__main__":
    unittest.main()
