"""P06/F08–F09: one accepted revision refreshes summary, track and battery.

The reviewed build refused parser telemetry when the stored downsampled count
was greater than or equal to the new count, so a corrected track of equal length
was dropped while the summary beside it changed. Equipment reassignment accepted
a battery as the aircraft, and did not move the log-derived battery history.
"""
from __future__ import annotations

import gzip
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from support import TempCase, log_bytes  # noqa: F401

from flightdesk import capture, db
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome


def samples_at(lat: float, count: int = 6, start_lat: float = 45.0) -> list[dict]:
    out = []
    for i in range(count):
        out.append({
            "t": i * 2.0,
            "lat": lat + i * 1e-5,
            "lon": 12.0 + i * 1e-5,
            "height_m": 10.0 + i,
            "alt_m": 100.0 + i,
            "battery_pct": 90.0 - i,
            "battery_c": 25.0,
            "on_ground": i == 0,
            "signal": 80,
            "gps_num": 12,
            "home_lat": lat,
            "home_lon": 12.0,
        })
    return out


def outcome(samples=None, serial="SN-1", name="Aircraft", start="2026-01-01T00:00:00+00:00",
            duration=120.0, distance=10.0, battery="BAT-1") -> ParseOutcome:
    return ParseOutcome(
        11, "decoded",
        details={"start_time_utc": start, "aircraft_sn": serial, "aircraft_name": name,
                 "battery_sn": battery, "total_time_s": duration,
                 "total_distance_m": distance},
        samples=samples or [],
    )


class DerivedRefreshTest(TempCase):
    def _ingest(self, raw: bytes, parsed: ParseOutcome,
                filename: str = "FlightRecord_2026-01-01_[00-00-00].txt"):
        with patch("flightdesk.importer.parse_file", return_value=parsed):
            return Importer(self.root, self.conn).import_stream(
                io.BytesIO(raw), filename, source_kind="manual", source_label="t").totals

    def _reparse(self, raw_id: int, parsed: ParseOutcome):
        with patch("flightdesk.importer.parse_file", return_value=parsed):
            return Importer(self.root, self.conn).reparse_raw(raw_id)

    def _track_first_lat(self) -> float:
        row = db.one(self.conn, "SELECT samples_blob FROM flight_telemetry")
        data = json.loads(gzip.decompress(row["samples_blob"]))
        return data[0]["lat"]

    # ------------------------------------------------ consistent refresh
    def test_equal_length_correction_changes_summary_and_track(self) -> None:
        self._ingest(b"a", outcome(samples=samples_at(1.0)))
        self._ingest(b"b", outcome(samples=samples_at(2.0)))
        self.assertEqual(self.counts()["flights"], 1)
        home = db.one(self.conn, "SELECT home_lat FROM flights")["home_lat"]
        self.assertAlmostEqual(home, 2.0, places=3)
        # The track and the summary agree: this is the F08 defect.
        self.assertAlmostEqual(self._track_first_lat(), 2.0, places=3)

    def test_a_poorer_variant_does_not_replace_a_richer_track(self) -> None:
        self._ingest(b"rich", outcome(samples=samples_at(1.0, count=40)))
        rich_lat = self._track_first_lat()
        rich_samples = db.one(self.conn, "SELECT sample_count FROM flight_telemetry")["sample_count"]
        # Same strong identity, different bytes, far fewer samples.
        self._ingest(b"poor", outcome(samples=samples_at(9.0, count=4)))
        self.assertEqual(self.counts()["flights"], 1)
        self.assertEqual(db.one(self.conn, "SELECT sample_count FROM flight_telemetry")
                         ["sample_count"], rich_samples)
        self.assertAlmostEqual(self._track_first_lat(), rich_lat, places=5)

    def test_failure_mid_refresh_rolls_back_every_derived_table(self) -> None:
        self._ingest(b"a", outcome(samples=samples_at(1.0)))
        before = db.one(self.conn, "SELECT home_lat,sample_count,duration_s FROM flights")
        before_reading = self.conn.execute(
            "SELECT COUNT(*) FROM battery_readings").fetchone()[0]

        def explode(conn, flight_id):
            raise sqlite3.OperationalError("simulated failure during refresh")

        with patch("flightdesk.capture.refresh_log_battery_reading", explode):
            with self.assertRaises(sqlite3.OperationalError):
                self._reparse(1, outcome(samples=samples_at(2.0)))

        after = db.one(self.conn, "SELECT home_lat,sample_count,duration_s FROM flights")
        self.assertEqual(after["home_lat"], before["home_lat"])
        self.assertEqual(after["sample_count"], before["sample_count"])
        self.assertEqual(after["duration_s"], before["duration_s"])
        # The track did not change either.
        self.assertAlmostEqual(self._track_first_lat(), 1.0, places=5)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM battery_readings").fetchone()[0], before_reading)

    def test_a_null_correction_does_not_leave_a_stale_fingerprint(self) -> None:
        # A flight imported with no duration at all must carry the
        # unknown-duration fingerprint, not a hash of some placeholder.
        self._ingest(b"a", outcome(samples=[], duration=None))
        row = db.one(self.conn, "SELECT duration_s,fingerprint FROM flights")
        self.assertIsNone(row["duration_s"])
        from flightdesk.importer import _fingerprint
        self.assertEqual(row["fingerprint"],
                         _fingerprint("SN-1", "2026-01-01T00:00:00+00:00", None,
                                      None, "Aircraft"))
        self.assertNotEqual(row["fingerprint"],
                            _fingerprint("SN-1", "2026-01-01T00:00:00+00:00", 120.0,
                                         None, "Aircraft"))


class EquipmentReassignmentTest(TempCase):
    def _seed_equipment(self) -> None:
        now = db.now_utc()
        with db.tx(self.conn):
            self.conn.execute(
                "INSERT INTO equipment(kind,name,serial,created_at,updated_at) "
                "VALUES('aircraft','Aircraft','SN-1',?,?)", (now, now))
            self.conn.execute(
                "INSERT INTO equipment(kind,name,serial,created_at,updated_at) "
                "VALUES('battery','Battery A','BAT-1',?,?)", (now, now))

    def _flight_with_battery(self, battery_sn: str = "BAT-1") -> tuple[int, int]:
        self._seed_equipment()
        with patch("flightdesk.importer.parse_file",
                   return_value=outcome(samples=samples_at(1.0), battery=battery_sn)):
            Importer(self.root, self.conn).import_stream(
                io.BytesIO(b"a"), "FlightRecord_2026-01-01_[00-00-00].txt",
                source_kind="manual", source_label="t")
        flight_id = db.one(self.conn, "SELECT id FROM flights")["id"]
        battery_id = db.one(
            self.conn, "SELECT id FROM equipment WHERE kind='battery' LIMIT 1")["id"]
        return int(flight_id), int(battery_id)

    def test_aircraft_cannot_be_reassigned_to_a_battery(self) -> None:
        flight_id, battery_id = self._flight_with_battery()
        result = capture.reassign_flight(self.conn, flight_id, "aircraft", battery_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "wrong_equipment_kind")
        row = db.one(self.conn, "SELECT aircraft_equipment_id FROM flights WHERE id=?",
                     (flight_id,))
        self.assertNotEqual(row["aircraft_equipment_id"], battery_id)

    def test_a_nonexistent_or_boolean_id_is_refused(self) -> None:
        flight_id, _battery = self._flight_with_battery()
        self.assertEqual(
            capture.reassign_flight(self.conn, flight_id, "battery", 999999)["code"],
            "equipment_not_found")
        self.assertEqual(
            capture.reassign_flight(self.conn, flight_id, "battery", True)["code"],
            "bad_equipment_id")

    def test_battery_history_moves_but_manual_readings_stay(self) -> None:
        flight_id, battery_a = self._flight_with_battery()
        now = db.now_utc()
        battery_b = self.conn.execute(
            "INSERT INTO equipment(kind,name,serial,created_at,updated_at) "
            "VALUES('battery','Spare','BAT-2',?,?)", (now, now)).lastrowid
        # A manual reading on the flight must not be touched by reassignment.
        self.conn.execute(
            "INSERT INTO battery_readings(battery_id,flight_id,at,charge_pct,source) "
            "VALUES(?,?,?,?, 'manual')", (battery_a, flight_id, now, 55.0))
        self.conn.commit()

        result = capture.reassign_flight(self.conn, flight_id, "battery", int(battery_b))
        self.assertTrue(result["ok"], result)

        log_rows = self.conn.execute(
            "SELECT battery_id FROM battery_readings WHERE flight_id=? AND source='log'",
            (flight_id,)).fetchall()
        self.assertEqual([int(r["battery_id"]) for r in log_rows], [int(battery_b)])
        manual = self.conn.execute(
            "SELECT battery_id FROM battery_readings WHERE flight_id=? AND source='manual'",
            (flight_id,)).fetchall()
        self.assertEqual([int(r["battery_id"]) for r in manual], [battery_a])

    def test_clearing_the_assignment_is_coherent(self) -> None:
        flight_id, _battery = self._flight_with_battery()
        result = capture.reassign_flight(self.conn, flight_id, "battery", None)
        self.assertTrue(result["ok"], result)
        row = db.one(self.conn, "SELECT battery_equipment_id FROM flights WHERE id=?",
                     (flight_id,))
        self.assertIsNone(row["battery_equipment_id"])
        remaining = self.conn.execute(
            "SELECT COUNT(*) FROM battery_readings WHERE flight_id=? AND source='log'",
            (flight_id,)).fetchone()[0]
        self.assertEqual(remaining, 0)


class VariantOrderTest(TempCase):
    def test_source_selection_is_stable_across_reversed_import_order(self) -> None:
        def run(first: bytes, second: bytes) -> tuple[float, int]:
            t = type(self)()
            t.setUp()
            try:
                with patch("flightdesk.importer.parse_file",
                           return_value=outcome(samples=samples_at(1.0, count=40))):
                    Importer(t.root, t.conn).import_stream(
                        io.BytesIO(first), "FlightRecord_2026-01-01_[00-00-00].txt",
                        source_kind="manual", source_label="t")
                with patch("flightdesk.importer.parse_file",
                           return_value=outcome(samples=samples_at(1.0, count=8))):
                    Importer(t.root, t.conn).import_stream(
                        io.BytesIO(second), "FlightRecord_2026-01-01_[00-00-00].txt",
                        source_kind="manual", source_label="t")
                lat = json.loads(gzip.decompress(
                    db.one(t.conn, "SELECT samples_blob FROM flight_telemetry")
                    ["samples_blob"]))[0]["lat"]
                flights = db.table_counts(t.conn)["flights"]
                return round(lat, 5), flights
            finally:
                t.tearDown()

        # One flight in both orders; the richer track is accepted either way.
        self.assertEqual(run(b"rich", b"poor")[1], 1)
        self.assertEqual(run(b"poor", b"rich")[1], 1)


if __name__ == "__main__":
    unittest.main()
