"""Stage 7: backup, restore rehearsal, exports and reconciliation stability."""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import TempCase, apply_capture, base_time, log_bytes

from flightdesk import backup, db, report
from flightdesk.paths import ensure_layout


class BackupTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.imp.import_stream(io.BytesIO(log_bytes(11)), "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        apply_capture(self.conn)
        self.dest = Path(tempfile.mkdtemp(prefix="fd-backup-"))

    def tearDown(self) -> None:
        super().tearDown()
        import shutil
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_backup_validates_and_restores_into_a_new_root(self) -> None:
        result = backup.create_backup(self.conn, self.root, self.dest, "unit test")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["counts"]["raw_files"], 1)
        validation = backup.validate_backup(Path(result["path"]))
        self.assertTrue(validation["ok"], validation["errors"])
        target = Path(tempfile.mkdtemp(prefix="fd-restore-")) / "restored"
        restored = backup.restore_backup(Path(result["path"]), target)
        self.assertTrue(restored["ok"], restored)
        conn = db.init_db(target / "flightdesk.sqlite3")
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM services").fetchone()[0], 21)
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk, [])
        finally:
            conn.close()
        import shutil
        shutil.rmtree(target.parent, ignore_errors=True)

    def test_restore_refuses_the_live_root(self) -> None:
        from unittest import mock
        result = backup.create_backup(self.conn, self.root, self.dest)
        self.assertTrue(result["ok"])
        with mock.patch("flightdesk.paths.default_data_root", return_value=Path(self.root)):
            attempt = backup.restore_backup(Path(result["path"]), Path(self.root))
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "refuses_live_root")

    def test_restore_refuses_a_populated_target(self) -> None:
        result = backup.create_backup(self.conn, self.root, self.dest)
        attempt = backup.restore_backup(Path(result["path"]), Path(self.root))
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["code"], "target_not_empty")

    def test_cloud_sync_destination_requires_confirmation(self) -> None:
        cloud = self.dest / "OneDrive" / "backups"
        refused = backup.create_backup(self.conn, self.root, cloud)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["code"], "cloud_sync_destination")
        allowed = backup.create_backup(self.conn, self.root, cloud, allow_cloud=True)
        self.assertTrue(allowed["ok"])
        self.assertTrue(allowed["warnings"])

    def test_interrupted_backup_leaves_no_lock(self) -> None:
        lock = self.root / "tmp" / "backup.lock"
        lock.write_text("12345", encoding="utf-8")
        blocked = backup.create_backup(self.conn, self.root, self.dest)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "backup_in_progress")
        lock.unlink()
        ok = backup.create_backup(self.conn, self.root, self.dest)
        self.assertTrue(ok["ok"])
        self.assertFalse(lock.exists())

    def test_interrupted_backup_leaves_no_partial_archive(self) -> None:
        import zipfile
        from unittest import mock

        def exploding_write(self, *args, **kwargs):
            raise OSError(28, "simulated disk full")

        with mock.patch.object(zipfile.ZipFile, "write", exploding_write):
            result = backup.create_backup(self.conn, self.root, self.dest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "disk_full")
        # nothing that looks like a backup may remain in the destination
        leftovers = [p.name for p in self.dest.iterdir()]
        self.assertEqual(leftovers, [])
        # the lock must be released so the next attempt can run
        self.assertFalse((self.root / "tmp" / "backup.lock").exists())
        follow = backup.create_backup(self.conn, self.root, self.dest)
        self.assertTrue(follow["ok"], follow)
        self.assertTrue(backup.validate_backup(Path(follow["path"]))["ok"])

    def test_interrupted_backup_is_recorded_as_failed(self) -> None:
        import zipfile
        from unittest import mock

        with mock.patch.object(zipfile.ZipFile, "write",
                               side_effect=OSError(28, "simulated disk full")):
            backup.create_backup(self.conn, self.root, self.dest)
        row = db.one(self.conn, "SELECT * FROM backup_runs ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "failed")

    def test_corrupted_backup_is_rejected(self) -> None:
        result = backup.create_backup(self.conn, self.root, self.dest)
        path = Path(result["path"])
        data = bytearray(path.read_bytes())
        data[-200:-100] = b"\x00" * 100
        broken = self.dest / "broken.zip"
        broken.write_bytes(bytes(data))
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertTrue(validation["errors"])

    def test_manifest_records_hashes_and_schema(self) -> None:
        import json
        import zipfile
        result = backup.create_backup(self.conn, self.root, self.dest)
        with zipfile.ZipFile(result["path"]) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest["app"], "FlightDesk")
        self.assertFalse(manifest["key_included"])
        self.assertEqual(len(manifest["archive"]), 1)
        self.assertTrue(manifest["archive"][0]["sha256"])

    def test_backup_with_watcher_state_present(self) -> None:
        from flightdesk import usb
        usb.set_watcher(self.conn, True, 60)
        result = backup.create_backup(self.conn, self.root, self.dest)
        self.assertTrue(result["ok"])


class ReconciliationTest(TempCase):
    def test_groups_stay_separate_and_do_not_silently_zero(self) -> None:
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="archive")
        self.imp.import_stream(io.BytesIO(log_bytes(11, days=20, aircraft="air3s")),
                               "DJIFlightRecord_2026-08-31_[21-53-20].txt",
                               source_kind="extras", source_label="controller")
        data = report.reconciliation(self.conn)
        self.assertEqual(data["groups"]["archive"]["raw_files"], 1)
        self.assertEqual(data["groups"]["supplied_controller"]["raw_files"], 1)
        self.assertEqual(data["groups"]["combined"]["raw_files"], 2)
        # synthetic data must never claim a match against the real archive
        self.assertFalse(data["real_inputs_selected"])
        self.assertIsNone(data["signed_differences"])

    def test_history_reference_stays_stable_after_new_imports(self) -> None:
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="archive")
        first = report.reconciliation(self.conn)
        self.assertIsNotNone(first["baseline"])
        self.imp.import_stream(io.BytesIO(log_bytes(11, days=30, aircraft="mini3")),
                               "DJIFlightRecord_2026-09-10_[21-53-20].txt",
                               source_kind="archive", source_label="archive")
        second = report.reconciliation(self.conn)
        self.assertEqual(first["baseline"], second["baseline"])
        self.assertEqual(second["baseline_delta"]["raw_files"], 1)

    def test_pending_file_is_reported_not_counted_as_zero(self) -> None:
        from flightdesk.synth import make_v13_metadata_log
        self.imp.import_stream(io.BytesIO(make_v13_metadata_log(start=base_time(3))),
                               "DJIFlightRecord_2026-08-14_[21-53-20].txt",
                               source_kind="archive", source_label="archive")
        data = report.reconciliation(self.conn)
        self.assertEqual(data["excluded"]["pending_key"], 1)
        self.assertEqual(data["groups"]["archive"]["decoded"], 0)
        self.assertEqual(data["groups"]["archive"]["decoded_coverage"], 0.0)

    def test_exports_mark_fields_and_redaction_removes_identifiers(self) -> None:
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="archive")
        full = report.flights_csv(self.conn)
        redacted = report.flights_csv(self.conn, redact=True)
        self.assertIn("SYN-M2P-0001", full)
        self.assertNotIn("SYN-M2P-0001", redacted)
        self.assertIn("aircraft", redacted)
        eq_full = report.equipment_csv(self.conn)
        eq_red = report.equipment_csv(self.conn, redact=True)
        self.assertNotIn("SYN-BAT-1001", eq_red)
        svc = report.services_csv(self.conn)
        self.assertIn("service_id", svc.splitlines()[0])
        text = report.report_csv(report.reconciliation(self.conn, persist=False))
        self.assertIn("combined", text)


class RestoreInterruptionTest(TempCase):
    """A restore that dies part-way must not cost the owner their data.

    The previous implementation deleted the target root before moving the
    staged copy into place, so a crash in between lost both. The target is now
    moved aside and restored if the switch fails.
    """

    def _backup(self):
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = Path(tempfile.mkdtemp(prefix="fd-restore-src-"))
        result = backup.create_backup(self.conn, self.root, dest, "restore test")
        self.assertTrue(result.get("ok"), result)
        return Path(result["path"])

    def test_an_interrupted_extraction_leaves_the_target_untouched(self) -> None:
        import zipfile
        from unittest import mock

        archive = self._backup()
        target = Path(tempfile.mkdtemp(prefix="fd-target-"))
        marker = target / "existing-marker.txt"
        marker.write_text("do not lose me", encoding="utf-8")

        def explode(self, *args, **kwargs):
            raise OSError(5, "simulated interruption")

        with mock.patch.object(zipfile.ZipFile, "extractall", explode):
            with self.assertRaises(OSError):
                backup.restore_backup(archive, target, allow_existing=True)

        self.assertTrue(target.exists())
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not lose me")
        # Nothing half-restored was left where the owner would trust it.
        self.assertFalse((target / "manifest.json").exists())
        self.assertFalse(list(target.parent.glob("*.restore-tmp")))

    def test_a_restore_that_cannot_switch_puts_the_old_root_back(self) -> None:
        from unittest import mock

        archive = self._backup()
        target = Path(tempfile.mkdtemp(prefix="fd-target2-"))
        (target / "existing-marker.txt").write_text("keep", encoding="utf-8")

        real_replace = os.replace
        calls = {"n": 0}

        def failing_replace(src, dst):
            # Let the first move (target aside) happen, fail the swap.
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(13, "simulated failure while switching roots")
            return real_replace(src, dst)

        with mock.patch.object(os, "replace", failing_replace):
            with self.assertRaises(OSError):
                backup.restore_backup(archive, target, allow_existing=True)

        self.assertTrue(target.exists())
        self.assertTrue((target / "existing-marker.txt").exists())

    def test_a_restore_after_an_interruption_still_works(self) -> None:
        import zipfile
        from unittest import mock

        archive = self._backup()
        target = Path(tempfile.mkdtemp(prefix="fd-target3-"))

        def explode(self, *args, **kwargs):
            raise OSError(5, "simulated interruption")

        with mock.patch.object(zipfile.ZipFile, "extractall", explode):
            with self.assertRaises(OSError):
                backup.restore_backup(archive, target, allow_existing=True)

        fresh = Path(tempfile.mkdtemp(prefix="fd-target4-"))
        result = backup.restore_backup(archive, fresh)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue((fresh / "flightdesk.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()

class DjIKeyInBackupTest(TempCase):
    """The DJI key travels with the backup, as its DPAPI blob.

    Requested by the owner on 2026-11-21. The key lives beside the database
    rather than inside it, so it has to be carried explicitly or a restored copy
    silently loses it.
    """

    def setUp(self) -> None:
        super().setUp()
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        self.dest = Path(tempfile.mkdtemp(prefix="fd-keybackup-"))

    def tearDown(self) -> None:
        super().tearDown()
        import shutil
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_the_key_is_included_and_survives_a_restore(self) -> None:
        from flightdesk import keychain
        self.assertTrue(keychain.set_key(self.root, "TEST-KEY-1234567890")["ok"])
        result = backup.create_backup(self.conn, self.root, self.dest, "with key")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["key_included"])

        import json as _json
        import zipfile
        with zipfile.ZipFile(result["path"]) as zf:
            self.assertIn("keychains/dji_key.bin", zf.namelist())
            manifest = _json.loads(zf.read("manifest.json"))
        self.assertTrue(manifest["key_included"])
        self.assertEqual(manifest["key"]["protection"], "dpapi_user")
        self.assertIn("only by this Windows user", manifest["key_note"])

        validation = backup.validate_backup(Path(result["path"]))
        self.assertTrue(validation["ok"], validation["errors"])
        self.assertNotIn("key_missing_from_backup", validation["warnings"])

        target = Path(tempfile.mkdtemp(prefix="fd-keyrestore-")) / "restored"
        restored = backup.restore_backup(Path(result["path"]), target)
        self.assertTrue(restored["ok"], restored)
        self.assertTrue((target / "keychains" / "dji_key.bin").is_file())
        self.assertEqual(keychain.load_key(target), "TEST-KEY-1234567890")

    def test_a_backup_without_a_key_says_so(self) -> None:
        result = backup.create_backup(self.conn, self.root, self.dest, "no key")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["key_included"])
        validation = backup.validate_backup(Path(result["path"]))
        self.assertTrue(validation["ok"], validation["errors"])

    def test_a_tampered_key_is_refused(self) -> None:
        from flightdesk import keychain
        keychain.set_key(self.root, "TEST-KEY-1234567890")
        result = backup.create_backup(self.conn, self.root, self.dest, "with key")
        # Rebuild the ZIP with a different key blob, leaving the manifest alone.
        import zipfile
        original = Path(result["path"])
        tampered = original.with_name("tampered.zip")
        with zipfile.ZipFile(original) as src, zipfile.ZipFile(tampered, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "keychains/dji_key.bin":
                    data = data + b"\x00"
                dst.writestr(item, data)
        validation = backup.validate_backup(tampered)
        self.assertFalse(validation["ok"])
        self.assertIn("key_checksum_mismatch", validation["errors"])

    def test_a_key_from_another_account_is_a_warning_not_a_failure(self) -> None:
        # The data is what matters; an undecryptable key is recoverable by
        # re-entering it, so it must not block a restore.
        import json as _json
        import zipfile
        from unittest import mock
        from flightdesk import security

        result = backup.create_backup(self.conn, self.root, self.dest, "no key")
        rebuilt = Path(result["path"]).with_name("foreign-key.zip")
        blob = b"\x01\x02\x03not-a-dpapi-blob"
        with zipfile.ZipFile(result["path"]) as src, zipfile.ZipFile(rebuilt, "w") as dst:
            manifest = _json.loads(src.read("manifest.json"))
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "manifest.json":
                    manifest["key_included"] = True
                    manifest["key"] = {"name": "keychains/dji_key.bin",
                                       "sha256": __import__("hashlib").sha256(blob).hexdigest(),
                                       "size": len(blob), "protection": "dpapi_user"}
                    data = _json.dumps(manifest).encode()
                dst.writestr(item, data)
            dst.writestr("keychains/dji_key.bin", blob)

        validation = backup.validate_backup(rebuilt)
        self.assertTrue(validation["ok"], validation["errors"])
        self.assertIn("key_not_decryptable_on_this_account", validation["warnings"])

    def test_a_missing_key_is_flagged_when_the_manifest_promises_one(self) -> None:
        import json as _json
        import zipfile
        from flightdesk import keychain

        keychain.set_key(self.root, "TEST-KEY-1234567890")
        result = backup.create_backup(self.conn, self.root, self.dest, "with key")
        stripped = Path(result["path"]).with_name("stripped.zip")
        with zipfile.ZipFile(result["path"]) as src, zipfile.ZipFile(stripped, "w") as dst:
            for item in src.infolist():
                if item.filename == "keychains/dji_key.bin":
                    continue
                dst.writestr(item, src.read(item.filename))
        validation = backup.validate_backup(stripped)
        self.assertTrue(validation["ok"], validation["errors"])
        self.assertIn("key_missing_from_backup", validation["warnings"])
