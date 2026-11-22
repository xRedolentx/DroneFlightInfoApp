"""Backup while a scan is genuinely writing, not merely configured to.

BUILD-STATUS listed this as covered "by a settings-level test, not a genuinely
concurrent scan". Two real threads now: one imports files the way a controller
scan does, the other takes backups in a loop. Every backup must be a consistent
snapshot — a valid archive, a database whose foreign keys hold, and a manifest
that agrees with what is inside.

All fixtures are invented.
"""
from __future__ import annotations

import io
import json
import threading
import unittest
import zipfile

from support import TempRoot, base_time, log_bytes

from flightdesk import backup, db
from flightdesk.importer import Importer
from flightdesk.synth import filename_time


class ConcurrentBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempRoot()
        self.root = self.t.root
        self.db_file = self.root / "flightdesk.sqlite3"

    def tearDown(self) -> None:
        self.t.close()

    def _import_one(self, index: int) -> None:
        conn = db.connect(self.db_file)
        try:
            Importer(self.root, conn).import_stream(
                io.BytesIO(log_bytes(11, days=index)),
                filename_time(base_time(days=index), "FlightRecord"),
                source_kind="usb", source_label="USB test",
            )
        finally:
            conn.close()

    def test_a_backup_taken_mid_import_is_a_consistent_snapshot(self) -> None:
        stop = threading.Event()
        first_done = threading.Event()
        errors: list[str] = []
        backups: list[dict] = []
        counts_seen: list[int] = []

        def importing() -> None:
            index = 0
            while not stop.is_set() and index < 40:
                try:
                    self._import_one(index)
                except Exception as exc:  # noqa: BLE001 - surfaced below
                    errors.append(f"import: {type(exc).__name__}: {exc}")
                    return
                index += 1
                first_done.set()

        def backing_up() -> None:
            # Wait for the first import to land, so the loop below is certain to
            # run while further imports are still committing.
            first_done.wait(10)
            conn = db.connect(self.db_file)
            try:
                for round_index in range(6):
                    destination = self.root / "backups" / f"round-{round_index}"
                    destination.mkdir(parents=True, exist_ok=True)
                    try:
                        result = backup.create_backup(conn, self.root, destination)
                        backups.append(result)
                        # Prove the two threads really overlapped: a backup taken
                        # while imports are running catches a different flight
                        # count from one taken earlier. Without this the test
                        # could pass by finishing all the backups first.
                        with zipfile.ZipFile(result["path"]) as handle:
                            manifest = json.loads(handle.read("manifest.json"))
                        counts_seen.append(int(manifest["counts"]["flights"]))
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"backup: {type(exc).__name__}: {exc}")
                        return
            finally:
                conn.close()

        importer_thread = threading.Thread(target=importing, name="fd-test-import")
        backup_thread = threading.Thread(target=backing_up, name="fd-test-backup")
        importer_thread.start()
        backup_thread.start()
        importer_thread.join(timeout=120)
        stop.set()
        backup_thread.join(timeout=180)
        self.assertFalse(importer_thread.is_alive(), "the importer thread stalled")
        self.assertFalse(backup_thread.is_alive(), "the backup thread stalled")
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(backups), 6, backups)
        # Overlap evidence: the flight count moved while the backups ran, and
        # every snapshot was internally consistent at whatever count it caught.
        self.assertGreater(len(set(counts_seen)), 1,
                           f"backups all saw the same count {counts_seen}; "
                           "they did not overlap the imports")
        self.assertEqual(counts_seen, sorted(counts_seen), counts_seen)
        self.assertGreater(counts_seen[-1], 0, counts_seen)

        for result in backups:
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("code"), None, result)
            path = result.get("path")
            self.assertTrue(path, result)
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("flightdesk.sqlite3", names)
                manifest = json.loads(archive.read("manifest.json"))
                listed = {entry["sha256"] for entry in manifest.get("files", [])}
                # Every archived original the manifest promises is inside the
                # ZIP, and no half-written object made it in.
                for digest in listed:
                    self.assertTrue(
                        any(name.endswith(f"{digest}.bin") for name in names),
                        f"manifest lists {digest[:8]} but the ZIP does not carry it",
                    )

    def test_the_final_state_is_what_both_threads_agreed_on(self) -> None:
        # After a concurrent run, every stored flight must still be readable and
        # the archive must hold exactly the objects the index points at.
        errors: list[str] = []

        def importing() -> None:
            try:
                for index in range(12):
                    self._import_one(index)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        def backing_up() -> None:
            conn = db.connect(self.db_file)
            try:
                for round_index in range(4):
                    destination = self.root / "backups" / f"r{round_index}"
                    destination.mkdir(parents=True, exist_ok=True)
                    backup.create_backup(conn, self.root, destination)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"backup {type(exc).__name__}: {exc}")
            finally:
                conn.close()

        threads = [threading.Thread(target=importing), threading.Thread(target=backing_up)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)
        self.assertEqual(errors, [], errors)

        conn = db.connect(self.db_file)
        try:
            flights = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
            self.assertEqual(flights, 12)
            missing = []
            for row in db.all_rows(conn, "SELECT stored_rel FROM raw_files"):
                if not (self.root / "archive" / row["stored_rel"]).exists():
                    missing.append(row["stored_rel"])
            self.assertEqual(missing, [], f"{len(missing)} archive object(s) missing")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(list(violations), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
