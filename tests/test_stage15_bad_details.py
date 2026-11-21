"""A details block that is not where it claims to be must not be trusted.

Found on one real v11 log: its header stored a details offset of 0, so the file
header was read as if it were the details block. The record then carried a
mojibake aircraft name and serials, a max height of -4.9e31 m and a total
distance of 1.96e-33 m, and was marked `authoritative`. The pinned DJI parser
reports the same garbage for that file, so nothing downstream could tell.

These are the guards. All fixtures are invented.
"""
from __future__ import annotations

import struct
import unittest

from support import TempCase, log_bytes

from flightdesk import analysis, db, parser_adapter as pa


def zero_offset_log() -> bytes:
    """A v11 log whose stored details offset is 0 — the real failure's shape."""
    data = bytearray(log_bytes(11, duration=302.0, distance=46.9))
    struct.pack_into("<Q", data, 0, 0)
    return bytes(data)


def misaligned_identity_log() -> bytes:
    """A details block whose whole identity region is unprintable bytes.

    The offsets are intact; the bytes there are not text. Real bytes read from
    the wrong offset decode to U+FFFD and control characters, and they do so in
    every one of the five fixed fields — that is the alignment signal.
    """
    data = bytearray(log_bytes(11))
    struct.pack_into("<32s", data, 100 + 280, bytes(range(1, 33)))
    struct.pack_into("<16s", data, 100 + 312, bytes(range(0x80, 0x90)))
    struct.pack_into("<16s", data, 100 + 328, bytes(range(0x90, 0xA0)))
    struct.pack_into("<16s", data, 100 + 344, bytes(range(0xA0, 0xB0)))
    struct.pack_into("<16s", data, 100 + 360, bytes(range(0xB0, 0xC0)))
    return bytes(data)


def one_bad_field_log() -> bytes:
    """A block with one unreadable field and four good ones.

    Not an alignment failure: the aircraft name is junk, the serials are not.
    The junk field is dropped and the readable ones are kept.
    """
    data = bytearray(log_bytes(11))
    struct.pack_into("<32s", data, 100 + 280, bytes(range(1, 33)))
    return bytes(data)


class DetailsOffsetTest(TempCase):
    def test_a_zero_offset_is_not_a_details_block(self) -> None:
        path = self.root / "zero.txt"
        path.write_bytes(zero_offset_log())
        self.assertIsNone(pa.read_details(path, 11))

    def test_an_offset_inside_the_header_is_rejected(self) -> None:
        path = self.root / "tiny.txt"
        data = bytearray(log_bytes(11))
        struct.pack_into("<Q", data, 0, 8)
        path.write_bytes(bytes(data))
        self.assertIsNone(pa.read_details(path, 11))

    def test_an_offset_past_the_end_is_rejected(self) -> None:
        path = self.root / "past.txt"
        data = bytearray(log_bytes(11))
        struct.pack_into("<Q", data, 0, len(data) + 1000)
        path.write_bytes(bytes(data))
        self.assertIsNone(pa.read_details(path, 11))

    def test_a_good_synthetic_log_still_reads(self) -> None:
        # The guard must not reject a well-formed block.
        path = self.root / "good.txt"
        path.write_bytes(log_bytes(11))
        details = pa.read_details(path, 11)
        self.assertIsNotNone(details)
        self.assertEqual(details["aircraft_name"], "Mavic 2 Pro")
        self.assertEqual(details["aircraft_sn"], "SYN-M2P-0001")
        self.assertAlmostEqual(details["total_distance_m"], 46.9, places=1)


class IdentityAlignmentTest(TempCase):
    def test_unprintable_identity_rejects_the_whole_block(self) -> None:
        path = self.root / "misaligned.txt"
        path.write_bytes(misaligned_identity_log())
        self.assertIsNone(pa.read_details(path, 11))

    def test_one_bad_field_drops_only_that_field(self) -> None:
        # A single unreadable name is not an alignment failure. Dropping the
        # whole block would throw away three good serials.
        path = self.root / "one-bad.txt"
        path.write_bytes(one_bad_field_log())
        details = pa.read_details(path, 11)
        self.assertIsNotNone(details)
        self.assertIsNone(details["aircraft_name"])
        self.assertEqual(details["aircraft_sn"], "SYN-M2P-0001")
        self.assertEqual(details["battery_sn"], "SYN-BAT-1001")

    def test_printable_identity_is_kept(self) -> None:
        self.assertEqual(pa._printable_identity("SYN-M2P-0001"), "SYN-M2P-0001")
        self.assertEqual(pa._printable_identity("  Mavic 2 Pro "), "Mavic 2 Pro")

    def test_unprintable_identity_is_dropped(self) -> None:
        for bad in ("\x19\ufffd-o\ufffd\ufffd", "", "   ", "\x00\x01", None, 42):
            self.assertIsNone(pa._printable_identity(bad), repr(bad))


class NumericNoiseTest(TempCase):
    def test_a_denormal_distance_is_not_a_measurement(self) -> None:
        self.assertIsNone(pa.distance_metres(1.962284e-33, 11))

    def test_a_real_distance_survives(self) -> None:
        self.assertAlmostEqual(pa.distance_metres(154.0, 11), 154.0)
        self.assertEqual(pa.distance_metres(0.0, 11), 0.0)
        self.assertAlmostEqual(pa.distance_metres(21.3, 14), 21300.0)

    def test_a_negative_height_is_implausible(self) -> None:
        m = analysis.FlightMetrics()
        m.max_height_m = -4.937904900108082e31
        m.duration_s = 302.0
        analysis.drop_implausible(m)
        self.assertIsNone(m.max_height_m)
        self.assertIn("implausible_max_height_m", m.flags)
        self.assertEqual(m.duration_s, 302.0)

    def test_a_negative_distance_and_speed_are_implausible(self) -> None:
        m = analysis.FlightMetrics()
        m.total_distance_m = -1.0
        m.max_h_speed_mps = -128216.87
        analysis.drop_implausible(m)
        self.assertIsNone(m.total_distance_m)
        self.assertIsNone(m.max_h_speed_mps)

    def test_a_below_sea_level_altitude_is_still_a_reading(self) -> None:
        # Death Valley and the Dead Sea are real places; -80 m is plausible.
        m = analysis.FlightMetrics()
        m.max_altitude_m = -80.0
        analysis.drop_implausible(m)
        self.assertEqual(m.max_altitude_m, -80.0)


class ImportTest(TempCase):
    def test_a_zero_offset_log_imports_without_invented_facts(self) -> None:
        totals = self.import_bytes(zero_offset_log(),
                                  "DJIFlightRecord_2025-05-16_[10-14-39].txt")
        self.assertEqual(totals["new"], 1, totals)
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertIsNone(row["aircraft_name"])
        self.assertIsNone(row["aircraft_sn"])
        self.assertIsNone(row["battery_sn"])
        self.assertIsNone(row["max_height_m"])
        self.assertIsNone(row["total_distance_m"])
        self.assertIsNone(row["duration_s"])
        # The date still comes from the file name, and says so.
        self.assertEqual(row["start_time_precision"], "filename_second")
        self.assertEqual(row["filename_time"], "2025-05-16T10:14:39")
        self.assertEqual(row["sample_count"], 0)
        # A flight nobody can read is kept, excluded, and labelled.
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["excluded_reason"], "details_unreadable")
        self.assertEqual(row["metrics_source"], "unavailable")

    def test_a_misaligned_details_block_imports_without_invented_facts(self) -> None:
        totals = self.import_bytes(misaligned_identity_log(),
                                  "DJIFlightRecord_2025-05-17_[10-00-00].txt")
        self.assertEqual(totals["new"], 1, totals)
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertIsNone(row["aircraft_name"])
        self.assertIsNone(row["aircraft_sn"])

    def test_a_healthy_log_is_unaffected(self) -> None:
        self.import_bytes(log_bytes(11, duration=302.0, distance=46.9),
                          "FlightRecord_2026-08-11_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertEqual(row["aircraft_name"], "Mavic 2 Pro")
        self.assertEqual(row["battery_sn"], "SYN-BAT-1001")
        self.assertAlmostEqual(row["total_distance_m"], 46.9, places=1)
        self.assertAlmostEqual(row["duration_s"], 302.0, places=1)
        self.assertAlmostEqual(row["max_height_m"], 45.1, places=1)
        self.assertEqual(row["identity_status"], "authoritative")

    def test_a_reparse_replaces_a_record_built_from_garbage(self) -> None:
        # The shape the old build stored for this kind of file. A reparse must
        # replace it, not leave it because the new parse has fewer fields.
        self.import_bytes(zero_offset_log(),
                          "DJIFlightRecord_2025-05-16_[10-14-39].txt")
        garbage = "\x19\ufffd-o\ufffd\ufffd\u0018\ufffd\ufffd"
        self.conn.execute(
            "UPDATE flights SET identity_status='authoritative', aircraft_name=?,"
            " aircraft_sn=?, battery_sn=?, max_height_m=?, total_distance_m=?,"
            " metrics_source='details'",
            (garbage, garbage, garbage, -4.937904900108082e31, 1.962284e-33),
        )
        self.conn.commit()
        raw_id = db.one(self.conn, "SELECT id FROM raw_files")["id"]
        self.assertIsNotNone(db.one(
            self.conn, "SELECT id FROM flights WHERE aircraft_name=?", (garbage,)))

        result = self.t.importer().reparse_raw(int(raw_id))
        self.assertTrue(result.get("ok"), result)

        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertIsNone(row["aircraft_name"])
        self.assertIsNone(row["aircraft_sn"])
        self.assertIsNone(row["battery_sn"])
        self.assertIsNone(row["max_height_m"])
        self.assertIsNone(row["total_distance_m"])
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["excluded_reason"], "details_unreadable")

    def test_a_reparse_never_degrades_a_good_record(self) -> None:
        # The guard this change touches must still protect real parse output.
        self.import_bytes(log_bytes(11, duration=302.0, distance=46.9),
                          "FlightRecord_2026-08-11_[21-53-20].txt")
        raw_id = db.one(self.conn, "SELECT id FROM raw_files")["id"]
        self.t.importer().reparse_raw(int(raw_id))
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertEqual(row["aircraft_name"], "Mavic 2 Pro")
        self.assertAlmostEqual(row["total_distance_m"], 46.9, places=1)
        self.assertEqual(row["identity_status"], "authoritative")


if __name__ == "__main__":
    unittest.main()
