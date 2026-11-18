"""Stage 1: offline import, archive integrity, identity and idempotency."""
from __future__ import annotations

import io
import threading
import unittest
import zipfile
from pathlib import Path

from support import TempCase, base_time, build_zip, log_bytes, write_folder

from flightdesk import archive, db
from flightdesk.importer import Importer


class RealSummaryTest(TempCase):
    def test_v8_and_v11_produce_real_summaries(self) -> None:
        for version, offset in ((8, 0), (11, 1)):
            totals = self.import_bytes(
                log_bytes(version, days=offset, duration=302.0 + offset, distance=46.9),
                f"FlightRecord_2026-08-{11 + offset:02d}_[21-53-20].txt",
            )
            self.assertEqual(totals["new"], 1, totals)
        rows = db.all_rows(self.conn, "SELECT * FROM flights ORDER BY id")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["metrics_source"], "details")
            self.assertAlmostEqual(row["total_distance_m"], 46.9, places=1)
            self.assertIsNotNone(row["start_time_utc"])
            self.assertEqual(row["aircraft_name"], "Mavic 2 Pro")
            self.assertEqual(row["start_time_precision"], "second")
        self.assertAlmostEqual(rows[0]["duration_s"], 302.0, places=1)
        self.assertAlmostEqual(rows[1]["duration_s"], 303.0, places=1)

    def test_original_bytes_are_preserved_and_hashed(self) -> None:
        data = log_bytes(11)
        self.import_bytes(data, "FlightRecord_2026-08-11_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM raw_files")
        stored = archive.archive_path(self.root, row["stored_rel"])
        self.assertTrue(stored.exists())
        self.assertEqual(archive.hash_file(stored), row["sha256"])
        self.assertEqual(stored.read_bytes(), data)

    def test_filename_time_is_kept_next_to_parsed_time(self) -> None:
        data = log_bytes(11)
        self.import_bytes(data, "FlightRecord_2026-08-11_[19-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertEqual(row["filename_time"], "2026-08-11T19:53:20")
        self.assertNotEqual(row["filename_time"], row["start_time_utc"][:19])


class IdempotencyTest(TempCase):
    def test_second_import_adds_no_flights(self) -> None:
        folder = write_folder(self.root / "incoming", {
            "FlightRecord_2026-08-11_[21-53-20].txt": log_bytes(11, days=0),
            "FlightRecord_2026-08-12_[21-53-20].txt": log_bytes(8, days=1),
        })
        first = self.imp.import_folder(folder)
        self.assertEqual(first.totals["new"], 2)
        before = self.counts()
        second = self.imp.import_folder(folder)
        after = self.counts()
        self.assertEqual(second.totals["new"], 0)
        self.assertEqual(second.totals["already_imported"], 2)
        self.assertEqual(before, after)

    def test_renamed_exact_duplicate_is_one_flight(self) -> None:
        data = log_bytes(11)
        self.import_bytes(data, "FlightRecord_2026-08-11_[21-53-20].txt")
        totals = self.import_bytes(data, "FlightRecord_2026-08-11_[23-53-20].txt")
        self.assertEqual(totals["already_imported"], 1)
        self.assertEqual(self.counts()["flights"], 1)
        members = db.all_rows(self.conn, "SELECT member_path FROM source_members")
        self.assertEqual(len(members), 2)

    def test_combined_import_twice_accounts_for_every_file(self) -> None:
        logs = {
            f"data/extracted/flight_logs/FlightRecord_2026-08-{11 + i}_[21-53-20].txt":
                log_bytes(11 if i % 2 else 8, days=i)
            for i in range(5)
        }
        extras = {
            "data/controller-test/DJIFlightRecord_2026-05-01_[12-00-00].txt":
                log_bytes(11, days=30, aircraft="air3s"),
        }
        zpath = build_zip(self.root / "handoff.zip", {**logs, **extras,
                                                      "__MACOSX/._junk": b"x"})
        first = self.imp.import_zip(zpath)
        self.assertEqual(first.totals["discovered"], 6)
        self.assertEqual(first.totals["new"], 6)
        before = self.counts()
        second = self.imp.import_zip(zpath)
        self.assertEqual(second.totals["already_imported"], 6)
        self.assertEqual(before, self.counts())
        kinds = {r["source_kind"] for r in db.all_rows(
            self.conn, "SELECT DISTINCT source_kind FROM source_members")}
        self.assertEqual(kinds, {"archive", "extras"})

    def test_race_between_manual_and_watcher_converges(self) -> None:
        data = log_bytes(11, days=2)
        errors: list[BaseException] = []
        barrier = threading.Barrier(4)

        def run(kind: str) -> None:
            importer = Importer(self.root, db.connect(self.root / "flightdesk.sqlite3"))
            try:
                barrier.wait(timeout=10)
                importer.import_stream(io.BytesIO(data), "FlightRecord_2026-08-13_[21-53-20].txt",
                                       source_kind=kind, source_label="race")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                importer.conn.close()

        threads = [threading.Thread(target=run, args=(k,))
                   for k in ("manual", "usb", "manual", "usb")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertFalse(errors, [type(e).__name__ for e in errors])
        self.assertEqual(self.counts()["raw"], 1)
        self.assertEqual(self.counts()["flights"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM import_items WHERE outcome='duplicate'")
            .fetchone()[0], 3)


class IdentityTest(TempCase):
    def test_same_time_different_aircraft_stays_two_flights(self) -> None:
        start = base_time(5)
        name = "DJIFlightRecord_2026-08-16_[21-53-20].txt"
        self.import_bytes(log_bytes(11, days=5, aircraft="mavic2"), name)
        self.import_bytes(log_bytes(11, days=5, aircraft="air3s"), name)
        rows = db.all_rows(self.conn, "SELECT * FROM flights ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["aircraft_sn"] for r in rows},
                         {"SYN-M2P-0001", "SYN-A3S-0002"})
        self.assertTrue(all(r["identity_status"] == "authoritative" for r in rows))

    def test_weak_same_time_match_is_flagged_not_merged(self) -> None:
        name = "DJIFlightRecord_2026-08-16_[21-53-20].txt"
        self.import_bytes(log_bytes(11, days=5, duration=300.0, distance=100.0), name)
        totals = self.import_bytes(
            log_bytes(11, days=5, duration=900.0, distance=900.0), name)
        self.assertEqual(totals["new"], 1)
        rows = db.all_rows(self.conn, "SELECT * FROM flights ORDER BY id")
        self.assertEqual(len(rows), 2)
        unresolved = [r for r in rows if r["identity_status"] == "unresolved"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["excluded_reason"], "same_time_weak_match")

    def test_strong_corroboration_merges_and_keeps_both_variants(self) -> None:
        name = "DJIFlightRecord_2026-08-16_[21-53-20].txt"
        self.import_bytes(log_bytes(11, days=5, duration=302.0), name)
        # Same aircraft, start and distance; duration differs by 0.6 s, which is
        # inside the 1 s corroboration window but outside the fingerprint.
        totals = self.import_bytes(
            log_bytes(8, days=5, duration=302.6), "FlightRecord_2026-08-16_[19-53-20].txt")
        self.assertEqual(totals["new"], 1)          # a new file was accepted
        self.assertEqual(totals["new_flight"], 0)   # but it is not a new flight
        rows = db.all_rows(self.conn, "SELECT * FROM flights")
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flight_files").fetchone()[0], 2)

    def test_negative_offset_shift_is_candidate_only(self) -> None:
        # Same flight, filename two hours off; the parser time must win.
        self.import_bytes(log_bytes(11, days=7), "FlightRecord_2026-08-18_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertTrue(row["start_time_utc"].startswith("2026-08-18T21:53:20"))
        self.assertEqual(row["start_time_offset_min"], None)
        self.assertNotEqual(row["start_time_utc"], row["filename_time"])


class ContainerTest(TempCase):
    def test_macos_metadata_is_ignored(self) -> None:
        zpath = build_zip(self.root / "h.zip", {
            "flight_logs/FlightRecord_2026-08-11_[21-53-20].txt": log_bytes(11),
            "__MACOSX/._FlightRecord.txt": b"\x00\x05mac",
            ".DS_Store": b"\x00\x00",
            "flight_logs/._hidden.txt": b"\x00\x05x",
        })
        batch = self.imp.import_zip(zpath)
        self.assertEqual(batch.totals["discovered"], 1)

    def test_duplicate_member_names_are_accepted(self) -> None:
        # Real exports can contain the same member path twice. Both byte
        # streams are legal; the content hash dedupes them, not the path.
        zpath = self.root / "dup.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("flight_logs/FlightRecord_2026-08-11_[21-53-20].txt", log_bytes(11))
            zf.writestr("flight_logs/FlightRecord_2026-08-11_[21-53-20].txt", log_bytes(11))
        batch = self.imp.import_zip(zpath)
        self.assertNotEqual(batch.note, "zip_unsafe")
        self.assertEqual(batch.totals["discovered"], 2)
        self.assertEqual(batch.totals["new"], 1)
        self.assertEqual(batch.totals["already_imported"], 1)
        self.assertEqual(self.counts()["flights"], 1)

    def test_escaping_member_is_rejected(self) -> None:
        zpath = build_zip(self.root / "evil.zip", {
            "../outside.txt": log_bytes(11),
        })
        batch = self.imp.import_zip(zpath)
        self.assertEqual(batch.note, "zip_unsafe")
        self.assertEqual(self.counts()["raw"], 0)

    def test_absolute_member_is_rejected(self) -> None:
        zpath = build_zip(self.root / "abs.zip", {"C:/Windows/x.txt": log_bytes(11)})
        batch = self.imp.import_zip(zpath)
        self.assertEqual(batch.note, "zip_unsafe")

    def test_oversized_member_is_rejected_before_extraction(self) -> None:
        zpath = build_zip(self.root / "big.zip", {
            "flight_logs/FlightRecord_big.txt": b"\x00" * (archive.MAX_MEMBER_BYTES + 1),
        })
        batch = self.imp.import_zip(zpath)
        self.assertIn(batch.note, {"archive_size_bound", "zip_unsafe"})
        self.assertEqual(self.counts()["raw"], 0)

    def test_hidden_extension_display_name_is_accepted(self) -> None:
        # Explorer can hide ".txt"; the collector appends it before validation.
        self.assertTrue(archive.is_flight_filename("DJIFlightRecord_2026-05-01_[12-00-00]"))
        self.assertTrue(archive.is_flight_filename("DJIFlightRecord_2026-05-01_[12-00-00].txt"))
        self.assertTrue(archive.is_flight_filename("FlightRecord_2026-05-01_[12-00-00].TXT"))
        self.assertFalse(archive.is_flight_filename("dji.go.v4.sync.json"))
        self.assertFalse(archive.is_flight_filename("DJI_0001.DAT"))

    def test_folder_import_records_source_membership(self) -> None:
        folder = write_folder(self.root / "controller-test", {
            "DJIFlightRecord_2026-05-01_[12-00-00].txt": log_bytes(11, days=40),
            "account.json": b"{}",
        })
        batch = self.imp.import_folder(folder)
        self.assertEqual(batch.totals["new"], 1)
        kinds = [r["source_kind"] for r in db.all_rows(
            self.conn, "SELECT source_kind FROM source_members")]
        self.assertEqual(kinds, ["extras"])


class FailureTest(TempCase):
    def test_damaged_file_is_retained_with_a_category(self) -> None:
        totals = self.import_bytes(b"\x00" * 64, "FlightRecord_broken.txt")
        self.assertEqual(totals["needs_attention"], 1)
        row = db.one(self.conn, "SELECT * FROM raw_files")
        self.assertEqual(row["status"], "damaged")
        self.assertTrue(row["error_category"])
        self.assertEqual(self.counts()["flights"], 0)

    def test_parser_failure_keeps_original(self) -> None:
        blob = bytearray(log_bytes(11))
        # Corrupt the details offset so the parser cannot read the block.
        blob[0:8] = (999999).to_bytes(8, "little")
        self.import_bytes(bytes(blob), "FlightRecord_2026-08-11_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM raw_files")
        self.assertIn(row["status"], {"damaged", "needs_attention"})
        self.assertTrue(archive.archive_path(self.root, row["stored_rel"]).exists())

    def test_disk_full_keeps_the_index_clean(self) -> None:
        from unittest import mock
        import flightdesk.archive as archive_mod

        def failing_replace(src, dst, *args, **kwargs):
            raise OSError(28, "simulated disk full")

        with mock.patch.object(archive_mod.os, "replace", failing_replace):
            totals = self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.assertEqual(totals["needs_attention"], 1)
        self.assertEqual(totals["new"], 0)
        self.assertEqual(totals["new_flight"], 0)
        # no raw file and no flight were indexed for the failed write
        self.assertEqual(self.counts()["raw"], 0)
        self.assertEqual(self.counts()["flights"], 0)
        row = db.one(self.conn, "SELECT * FROM import_batches ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(row["status"], "attention")
        # The archive layer maps ENOSPC to a precise reason, not a generic one.
        self.assertEqual(row["note"], "disk_full")
        # A half-written temp file must not be left behind either.
        self.assertEqual(list((self.root / "tmp").glob("ingest-*.part")), [])

    def test_disk_full_during_ingest_is_a_stated_reason(self) -> None:
        from unittest import mock
        from flightdesk.archive import DiskFull
        import flightdesk.importer as importer_mod

        with mock.patch.object(importer_mod.archive, "store_stream",
                               side_effect=DiskFull("no space")):
            totals = self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.assertEqual(totals["needs_attention"], 1)
        self.assertEqual(self.counts()["raw"], 0)
        row = db.one(self.conn, "SELECT * FROM import_batches ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(row["note"], "disk_full")

    def test_restart_does_not_lose_queued_work(self) -> None:
        data = log_bytes(11, days=3)
        self.imp.import_stream(io.BytesIO(data), "FlightRecord_2026-08-14_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        self.conn.close()
        self.conn = db.init_db(self.root / "flightdesk.sqlite3")
        self.t.conn = self.conn
        self.imp = Importer(self.root, self.conn)
        again = self.imp.import_stream(io.BytesIO(data), "FlightRecord_2026-08-14_[21-53-20].txt",
                                       source_kind="archive", source_label="test")
        self.assertEqual(again.totals["already_imported"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)

    def test_reparse_backfills_metrics_on_the_same_flight(self) -> None:
        """A parser improvement reaches stored flights through reparse, so an
        equal-weight reparse has to refresh the metrics of the flight it
        matches rather than returning early with the old numbers."""
        self.import_bytes(log_bytes(version=11), "FlightRecord_2026-08-11_[21-53-20].txt")
        flight = db.one(self.conn, "SELECT * FROM flights")
        with db.tx(self.conn):
            # A stale fingerprint is the hard case: the corrected metrics no
            # longer reproduce it, and the same raw file must still resolve to
            # the flight it is already linked to.
            self.conn.execute(
                "UPDATE flights SET duration_s=1.0, path_length_m=999.0, fingerprint='stale' "
                "WHERE id=?",
                (flight["id"],))
        raw = db.one(self.conn, "SELECT id FROM raw_files LIMIT 1")
        result = Importer(self.root, self.conn).reparse_raw(int(raw["id"]))
        self.assertTrue(result["ok"])
        self.assertFalse(result["new_flight"])
        after = db.one(self.conn, "SELECT * FROM flights WHERE id=?", (flight["id"],))
        self.assertAlmostEqual(after["duration_s"], flight["duration_s"], places=3)
        self.assertAlmostEqual(after["path_length_m"], flight["path_length_m"], places=3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
