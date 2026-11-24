"""P04/F05–F06: a restore owns no foreign path, and a newer schema is not opened.

The reviewed restore deleted an unrelated `<target>.restore-tmp` directory,
protected only the default root, and exposed `allow_existing` through the web
route. Opening a database marked version 999 relabelled it version 1 and ran DDL
on it first.
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import TempCase, log_bytes

from flightdesk import backup, db, paths


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RestoreTargetTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        self.dest = Path(tempfile.mkdtemp(prefix="fd-p04-"))
        result = backup.create_backup(self.conn, self.root, self.dest, "p04")
        self.assertTrue(result["ok"], result)
        self.backup = Path(result["path"])
        self.work = Path(tempfile.mkdtemp(prefix="fd-p04-targets-"))

    def tearDown(self) -> None:
        shutil = __import__("shutil")
        shutil.rmtree(self.dest, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)
        super().tearDown()

    def test_a_custom_serving_root_is_protected(self) -> None:
        # The app is serving from a custom root; the default root is elsewhere.
        attempt = backup.restore_backup(self.backup, self.root,
                                        allow_existing=True, protected=[self.root])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "target_is_active_root")
        # The running archive is untouched.
        self.assertTrue((self.root / "archive").is_dir())
        self.assertEqual(self.counts()["raw"], 1)

    def test_the_active_pointer_root_is_protected(self) -> None:
        home = self.work / "home"
        home.mkdir()
        active = self.work / "activated"
        active.mkdir()
        (home / "active_data_root.txt").write_text(str(active), encoding="utf-8")
        with mock.patch.object(paths, "app_home", return_value=home):
            attempt = backup.restore_backup(self.backup, active)
        self.assertFalse(attempt["ok"], attempt)
        self.assertEqual(attempt["code"], "target_is_active_root")

    def test_an_ancestor_or_descendant_of_a_protected_root_is_refused(self) -> None:
        inside = self.root / "nested" / "restore"
        attempt = backup.restore_backup(self.backup, inside, protected=[self.root])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "target_inside_active_root")
        ancestor = self.root.parent
        attempt = backup.restore_backup(self.backup, ancestor, protected=[self.root])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "target_contains_active_root")

    def test_an_unrelated_staging_directory_survives(self) -> None:
        target = self.work / "fresh"
        squatter = target.with_name(target.name + ".restore-tmp")
        squatter.mkdir()
        marker = squatter / "someone-elses.txt"
        marker.write_text("keep me", encoding="utf-8")

        result = backup.restore_backup(self.backup, target, protected=[])

        self.assertTrue(result["ok"], result)
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")
        self.assertTrue((target / "flightdesk.sqlite3").is_file())

    def test_a_live_restore_lock_blocks_a_second_restore(self) -> None:
        target = self.work / "locked"
        lock = target.with_name("." + target.name + ".restore.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("another process", encoding="utf-8")
        attempt = backup.restore_backup(self.backup, target, protected=[])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "restore_in_progress")
        self.assertFalse(target.exists())

    def test_a_nonempty_target_is_refused_without_allow_existing(self) -> None:
        target = self.work / "occupied"
        target.mkdir()
        (target / "mine.txt").write_text("mine", encoding="utf-8")
        attempt = backup.restore_backup(self.backup, target, protected=[])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "target_not_empty")
        self.assertEqual((target / "mine.txt").read_text(encoding="utf-8"), "mine")

    def test_activation_refuses_a_bare_database_file(self) -> None:
        bare = self.work / "bare"
        bare.mkdir()
        (bare / "flightdesk.sqlite3").write_bytes(b"")
        self.assertEqual(backup.activate_data_root(bare)["code"], "no_database_in_target")

    def test_activation_refuses_an_incomplete_archive(self) -> None:
        target = self.work / "restored"
        self.assertTrue(backup.restore_backup(self.backup, target, protected=[])["ok"])
        # Remove one referenced original: activation must notice.
        for original in (target / "archive").rglob("*.bin"):
            original.unlink()
        result = backup.activate_data_root(target)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["code"], "target_archive_incomplete")
        self.assertGreaterEqual(result["missing"], 1)

    def test_activation_refuses_a_newer_schema(self) -> None:
        target = self.work / "future"
        self.assertTrue(backup.restore_backup(self.backup, target, protected=[])["ok"])
        conn = sqlite3.connect(str(target / "flightdesk.sqlite3"))
        conn.execute("PRAGMA user_version=999")
        conn.commit()
        conn.close()
        result = backup.activate_data_root(target)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["code"], "target_schema_newer_than_app")

    def test_activation_accepts_a_restored_root(self) -> None:
        target = self.work / "good"
        self.assertTrue(backup.restore_backup(self.backup, target, protected=[])["ok"])
        home = self.work / "home2"
        result = backup.activate_data_root(target, home=home)
        self.assertTrue(result["ok"], result)
        self.assertTrue((home / "active_data_root.txt").is_file())


class SchemaGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fd-schema-")
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fresh(self) -> Path:
        path = self.dir / "flightdesk.sqlite3"
        conn = db.init_db(path)
        conn.close()
        return path

    def test_a_newer_schema_is_refused_and_left_untouched(self) -> None:
        path = self._fresh()
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA user_version=999")
        conn.commit()
        conn.close()
        before = _sha(path)
        with self.assertRaises(db.SchemaTooNew) as raised:
            db.init_db(path)
        self.assertEqual(raised.exception.found, 999)
        self.assertEqual(raised.exception.code, "schema_newer_than_app")
        self.assertEqual(_sha(path), before)
        # Still 999, not silently relabelled to 1.
        conn = sqlite3.connect(str(path))
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 999)
        finally:
            conn.close()

    def test_a_migration_failure_does_not_advance_the_version(self) -> None:
        path = self.dir / "fresh.sqlite3"
        with mock.patch.object(db, "_add_rule_columns",
                               side_effect=sqlite3.OperationalError("simulated")):
            with self.assertRaises(sqlite3.OperationalError):
                db.init_db(path)
        conn = sqlite3.connect(str(path))
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            conn.close()

    def test_a_current_schema_opens(self) -> None:
        path = self._fresh()
        conn = db.init_db(path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            conn.close()

    def test_an_older_schema_is_migrated_forward(self) -> None:
        path = self.dir / "old.sqlite3"
        conn = sqlite3.connect(str(path))
        conn.executescript("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
                           " updated_at TEXT NOT NULL);")
        conn.commit()
        conn.close()
        conn = db.init_db(path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("flights", names)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
