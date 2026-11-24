"""P05/F07: identity is conservative and an original keeps one owner.

The reviewed importer looked for a fingerprint before it looked for an existing
link, so a reparse could give one original two flights. It also hashed the
aircraft serial only, so two logs with no serial, one start time and one
duration but different aircraft names merged.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TempCase, log_bytes

from flightdesk import db, importer
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome


def outcome(start: str = "2026-01-01T00:00:00+00:00", serial=None,
            duration=600.0, distance=10.0, name="Synthetic aircraft",
            samples=None) -> ParseOutcome:
    return ParseOutcome(
        11, "decoded",
        details={"start_time_utc": start, "aircraft_sn": serial,
                 "aircraft_name": name, "total_time_s": duration,
                 "total_distance_m": distance},
        samples=samples or [],
    )


class IdentityTest(TempCase):
    def _ingest(self, raw: bytes, parsed: ParseOutcome,
                filename: str = "FlightRecord_2026-01-01_[00-00-00].txt") -> dict:
        with patch("flightdesk.importer.parse_file", return_value=parsed):
            return Importer(self.root, self.conn).import_stream(
                io.BytesIO(raw), filename, source_kind="manual", source_label="test",
            ).totals

    def _flights(self) -> list:
        return db.all_rows(self.conn, "SELECT * FROM flights ORDER BY id")

    def _links(self) -> list[tuple[int, int]]:
        return [(int(r["flight_id"]), int(r["raw_file_id"]))
                for r in self.conn.execute(
                    "SELECT flight_id,raw_file_id FROM flight_files "
                    "ORDER BY flight_id,raw_file_id")]

    # -------------------------------------------------- evidence requirements
    def test_missing_serial_different_names_do_not_merge(self) -> None:
        self._ingest(b"a", outcome(serial=None, name="Aircraft A"))
        self._ingest(b"b", outcome(serial=None, name="Aircraft B"))
        self.assertEqual(len(self._flights()), 2)
        self.assertEqual(self.counts()["raw"], 2)

    def test_missing_serial_same_name_still_links(self) -> None:
        self._ingest(b"a", outcome(serial=None, name="Aircraft A"))
        self._ingest(b"b", outcome(serial=None, name="Aircraft A"))
        self.assertEqual(len(self._flights()), 1)
        self.assertEqual(self.counts()["raw"], 2)

    def test_serial_on_one_side_only_is_not_proof(self) -> None:
        self._ingest(b"a", outcome(serial="SN-1", name="Aircraft A"))
        self._ingest(b"b", outcome(serial=None, name="Aircraft A"))
        # Different aircraft evidence: a second record, never a merge.
        self.assertEqual(len(self._flights()), 2)
        self.assertEqual(self.counts()["raw"], 2)

    def test_filename_identity_does_not_merge_different_aircraft(self) -> None:
        a = importer._fingerprint(None, None, 300.0, "2026-01-01T00:00:00", "Aircraft A")
        b = importer._fingerprint(None, None, 300.0, "2026-01-01T00:00:00", "Aircraft B")
        self.assertIsNotNone(a)
        self.assertNotEqual(a, b)
        self.assertEqual(
            a, importer._fingerprint(None, None, 300.0, "2026-01-01T00:00:00", "Aircraft A"))

    def test_undecodable_files_invent_no_flight(self) -> None:
        self._ingest(b"a", ParseOutcome(11, "damaged", details={}, samples=[]))
        self.assertEqual(len(self._flights()), 0)
        self.assertEqual(self.counts()["raw"], 1)

    def test_missing_metrics_make_a_match_weak_not_strong(self) -> None:
        self._ingest(b"a", outcome(serial="SN-1", duration=600.0, distance=10.0))
        # Same aircraft and start, but no distance: absence is not corroboration.
        self._ingest(b"b", outcome(serial="SN-1", duration=600.0, distance=None))
        rows = self._flights()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["identity_status"] for r in rows),
                         ["authoritative", "unresolved"])

    def test_equivalent_timestamp_representations_share_a_fingerprint(self) -> None:
        a = importer._fingerprint("SN-1", "2026-01-01T00:00:00+00:00", 600.0,
                                  None, "Aircraft")
        b = importer._fingerprint("SN-1", "2026-01-01T00:00:00Z", 600.0,
                                  None, "Aircraft")
        c = importer._fingerprint("SN-1", None, 600.0, "2026-01-01T00:00:00", "Aircraft")
        self.assertEqual(a, b)
        # A name-derived instant is still tagged apart from a decoded one.
        self.assertNotEqual(a, c)

    def test_a_fingerprint_needs_an_aircraft_identity(self) -> None:
        self.assertIsNone(importer._fingerprint(None, "2026-01-01T00:00:00+00:00",
                                                600.0, None, None))

    # ------------------------------------------------------------ ownership
    def test_reparse_does_not_give_one_original_two_flights(self) -> None:
        self._ingest(b"a", outcome(start="2026-01-01T00:00:00+00:00", serial="SN-1"))
        self._ingest(b"b", outcome(start="2026-01-02T00:00:00+00:00", serial="SN-1"))
        self.assertEqual(len(self._flights()), 2)
        # Reparse raw 1 as if it were flight 2.
        with patch("flightdesk.importer.parse_file",
                   return_value=outcome(start="2026-01-02T00:00:00+00:00", serial="SN-1")):
            Importer(self.root, self.conn).reparse_raw(1)
        links = self._links()
        raw_one_owners = [flight for flight, raw in links if raw == 1]
        self.assertEqual(len(raw_one_owners), 1, links)
        # And the conflict is recorded for review, not silently ignored.
        conflict = db.one(self.conn, "SELECT * FROM identity_conflicts")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["code"], "reparse_conflict")

    def test_existing_link_wins_over_a_fingerprint_match(self) -> None:
        self._ingest(b"a", outcome(serial="SN-1"))
        self._ingest(b"b", outcome(start="2026-01-03T00:00:00+00:00", serial="SN-1"))
        flight_of_raw1 = db.one(
            self.conn, "SELECT flight_id FROM flight_files WHERE raw_file_id=1")["flight_id"]
        with patch("flightdesk.importer.parse_file",
                   return_value=outcome(start="2026-01-03T00:00:00+00:00", serial="SN-1")):
            result = Importer(self.root, self.conn).reparse_raw(1)
        self.assertTrue(result.get("ok"), result)
        still = db.one(self.conn, "SELECT flight_id FROM flight_files WHERE raw_file_id=1")
        self.assertEqual(int(still["flight_id"]), int(flight_of_raw1))

    def test_genuine_variants_share_one_flight(self) -> None:
        base = bytearray(b"one")
        variant = bytearray(b"two")
        # Same strong evidence, different bytes: one flight, two originals.
        self._ingest(bytes(base), outcome(serial="SN-1", duration=600.0, distance=10.0))
        self._ingest(bytes(variant), outcome(serial="SN-1", duration=600.0, distance=10.0))
        self.assertEqual(len(self._flights()), 1)
        self.assertEqual(self.counts()["raw"], 2)
        self.assertEqual(len(self._links()), 2)

    def test_simultaneous_import_of_identical_bytes_is_one_original(self) -> None:
        self._ingest(b"same", outcome(serial="SN-1"))
        self._ingest(b"same", outcome(serial="SN-1"))
        self.assertEqual(self.counts()["raw"], 1)
        self.assertEqual(len(self._flights()), 1)

    def test_owner_annotations_and_hashes_survive_reparse(self) -> None:
        self._ingest(b"a", outcome(serial="SN-1"))
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE flights SET pilot='Owner', tags_json='[\"mine\"]', notes='keep'")
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        with patch("flightdesk.importer.parse_file", return_value=outcome(serial="SN-1")):
            Importer(self.root, self.conn).reparse_raw(1)
        row = db.one(self.conn, "SELECT pilot,tags_json,notes FROM flights")
        self.assertEqual((row["pilot"], row["tags_json"], row["notes"]),
                         ("Owner", '["mine"]', "keep"))
        after = db.one(self.conn, "SELECT * FROM raw_files")
        self.assertEqual(after["sha256"], raw["sha256"])


class OwnershipMigrationTest(unittest.TestCase):
    """A legacy database that already has a double-owned original."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fd-owner-")
        self.root = Path(self._tmp.name)
        self.path = self.root / "flightdesk.sqlite3"
        conn = db.init_db(self.path)
        try:
            with patch("flightdesk.importer.parse_file",
                       return_value=outcome(serial="SN-1")):
                imp = Importer(self.root, conn)
                imp.import_stream(io.BytesIO(b"a"), "FlightRecord_2026-01-01_[00-00-00].txt",
                                  source_kind="manual", source_label="t")
            with patch("flightdesk.importer.parse_file",
                       return_value=outcome(start="2026-01-02T00:00:00+00:00", serial="SN-1")):
                imp.import_stream(io.BytesIO(b"b"), "FlightRecord_2026-01-02_[00-00-00].txt",
                                  source_kind="manual", source_label="t")
            # Simulate the reviewed bug: raw 1 linked to both flights.
            conn.execute("DROP INDEX IF EXISTS idx_flight_files_raw")
            conn.execute(
                "INSERT OR IGNORE INTO flight_files(flight_id,raw_file_id,role,linked_at) "
                "SELECT id, 1, 'variant', '2026-01-01T00:00:00+00:00' FROM flights "
                "WHERE id=(SELECT MAX(id) FROM flights)")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_conflict_is_recorded_and_nothing_is_deleted(self) -> None:
        conn = db.init_db(self.path)
        try:
            owners = conn.execute(
                "SELECT flight_id FROM flight_files WHERE raw_file_id=1 "
                "ORDER BY flight_id").fetchall()
            self.assertEqual(len(owners), 2)
            conflicts = conn.execute(
                "SELECT * FROM identity_conflicts WHERE code='raw_multiply_owned'").fetchall()
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(json.loads(conflicts[0]["evidence_json"])["flight_ids"], [1, 2])
            # The unique index is not forced while the conflict stands.
            index = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_flight_files_raw'").fetchone()
            self.assertIsNone(index)
        finally:
            conn.close()

    def test_a_new_database_enforces_one_owner(self) -> None:
        path = self.root / "clean.sqlite3"
        conn = db.init_db(path)
        try:
            index = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_flight_files_raw'").fetchone()
            self.assertIsNotNone(index)
            with patch("flightdesk.importer.parse_file", return_value=outcome(serial="SN-1")):
                Importer(self.root, conn).import_stream(
                    io.BytesIO(b"x"), "FlightRecord_2026-01-01_[00-00-00].txt",
                    source_kind="manual", source_label="t")
            raw_id = conn.execute("SELECT id FROM raw_files").fetchone()["id"]
            flight_id = conn.execute("SELECT id FROM flights").fetchone()["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO flight_files(flight_id,raw_file_id,linked_at) VALUES(?,?,?)",
                    (flight_id + 1, raw_id, "2026-01-01T00:00:00+00:00"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
