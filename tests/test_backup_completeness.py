"""P03/F04: 'valid' means every referenced original is present exactly once.

The reviewed validator iterated the members that happened to be in the ZIP and
compared a manifest count, so a backup missing an original the database still
referenced validated clean — and restored into a root with a dangling
reference. These tests reconcile the three inventories against each other.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import TempCase, apply_capture, log_bytes

from flightdesk import backup, db


def _members(path: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as zf:
        return [(info.filename, zf.read(info.filename)) for info in zf.infolist()]


def _rebuild(source: Path, target: Path, transform) -> Path:
    """Rewrite a ZIP, letting `transform(name, data)` return data or None."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in _members(source):
            kept = transform(name, data)
            if kept is not None:
                out.writestr(name, kept)
    return target


class BackupCompletenessTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        # Two originals, so "remove one" is distinguishable from "empty".
        self.imp.import_stream(io.BytesIO(log_bytes(11)),
                               "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        self.imp.import_stream(io.BytesIO(log_bytes(11, days=1)),
                               "DJIFlightRecord_2026-08-12_[21-53-20].txt",
                               source_kind="archive", source_label="test")
        apply_capture(self.conn)
        self.dest = Path(tempfile.mkdtemp(prefix="fd-f04-"))
        result = backup.create_backup(self.conn, self.root, self.dest, "f04")
        self.assertTrue(result["ok"], result)
        self.backup = Path(result["path"])

    def tearDown(self) -> None:
        shutil = __import__("shutil")
        shutil.rmtree(self.dest, ignore_errors=True)
        super().tearDown()

    def _archive_members(self) -> list[str]:
        return [n for n, _ in _members(self.backup) if n.startswith("archive/")]

    # --------------------------------------------------------------- positive
    def test_a_complete_backup_validates_and_restores(self) -> None:
        validation = backup.validate_backup(self.backup)
        self.assertTrue(validation["ok"], validation["errors"])
        self.assertGreaterEqual(len(self._archive_members()), 2)
        manifest = validation["manifest"]
        self.assertEqual(len(manifest["archive"]), validation["counts"]["raw_files"])
        target = Path(tempfile.mkdtemp(prefix="fd-f04-restore-")) / "root"
        restored = backup.restore_backup(self.backup, target)
        self.assertTrue(restored["ok"], restored)
        self.assertTrue((target / "archive").is_dir())

    # ------------------------------------------------------- missing original
    def test_removing_an_archive_member_is_rejected(self) -> None:
        victim = self._archive_members()[0]
        broken = _rebuild(self.backup, self.dest / "no-member.zip",
                          lambda n, d: None if n == victim else d)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("archive_member_missing", validation["errors"])
        # And it must not restore.
        target = Path(tempfile.mkdtemp(prefix="fd-f04-r-")) / "root"
        restored = backup.restore_backup(broken, target)
        self.assertFalse(restored["ok"])
        self.assertEqual(restored["code"], "validation_failed")

    def test_removing_from_zip_and_manifest_but_keeping_the_db_row_is_rejected(self) -> None:
        # This is the exact case the review reproduced: validation and restore
        # both said ok while a referenced original was gone.
        victim = self._archive_members()[0]
        rel = victim[len("archive/"):]

        def drop(name: str, data: bytes):
            if name == victim:
                return None
            if name == "manifest.json":
                manifest = json.loads(data.decode("utf-8"))
                manifest["archive"] = [e for e in manifest["archive"] if e["rel"] != rel]
                return json.dumps(manifest).encode("utf-8")
            return data

        broken = _rebuild(self.backup, self.dest / "ghost.zip", drop)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"], validation)
        self.assertIn("original_missing_from_manifest", validation["errors"])
        target = Path(tempfile.mkdtemp(prefix="fd-f04-r-")) / "root"
        restored = backup.restore_backup(broken, target)
        self.assertFalse(restored["ok"])
        self.assertEqual(restored["code"], "validation_failed")

    def test_an_extra_archive_member_not_in_the_manifest_is_rejected(self) -> None:
        extra = self.dest / "extra.zip"
        with zipfile.ZipFile(self.backup) as src, zipfile.ZipFile(extra, "w") as out:
            for info in src.infolist():
                out.writestr(info, src.read(info.filename))
            out.writestr("archive/ff/ee/unexpected.bin", b"payload")
        validation = backup.validate_backup(extra)
        self.assertFalse(validation["ok"])
        self.assertIn("archive_member_not_in_manifest", validation["errors"])

    def test_an_unknown_top_level_member_is_refused(self) -> None:
        extra = self.dest / "stray.zip"
        with zipfile.ZipFile(self.backup) as src, zipfile.ZipFile(extra, "w") as out:
            for info in src.infolist():
                out.writestr(info, src.read(info.filename))
            out.writestr("setup.exe", b"MZ")
        validation = backup.validate_backup(extra)
        self.assertFalse(validation["ok"])
        self.assertIn("unknown_member", validation["errors"])

    def test_a_path_alias_member_is_refused(self) -> None:
        victim = self._archive_members()[0]
        alias = "archive/./" + victim[len("archive/"):]
        extra = self.dest / "alias.zip"
        with zipfile.ZipFile(self.backup) as src, zipfile.ZipFile(extra, "w") as out:
            for info in src.infolist():
                out.writestr(info, src.read(info.filename))
            with zipfile.ZipFile(self.backup) as again:
                out.writestr(alias, again.read(victim))
        validation = backup.validate_backup(extra)
        self.assertFalse(validation["ok"])
        self.assertIn("member_path_alias", validation["errors"])

    def test_a_duplicate_manifest_entry_is_rejected(self) -> None:
        def duplicate(name: str, data: bytes):
            if name != "manifest.json":
                return data
            manifest = json.loads(data.decode("utf-8"))
            manifest["archive"].append(dict(manifest["archive"][0]))
            return json.dumps(manifest).encode("utf-8")

        broken = _rebuild(self.backup, self.dest / "dupe.zip", duplicate)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("manifest_duplicate_entry", validation["errors"])

    def test_a_malformed_manifest_is_rejected_not_crashed(self) -> None:
        broken = _rebuild(self.backup, self.dest / "bad-manifest.zip",
                          lambda n, d: b"{not json" if n == "manifest.json" else d)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("malformed_manifest", validation["errors"])

    def test_a_manifest_entry_of_the_wrong_type_is_rejected(self) -> None:
        def wrong_types(name: str, data: bytes):
            if name != "manifest.json":
                return data
            manifest = json.loads(data.decode("utf-8"))
            manifest["archive"][0]["size"] = "not-an-int"
            return json.dumps(manifest).encode("utf-8")

        broken = _rebuild(self.backup, self.dest / "types.zip", wrong_types)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("manifest_entry_malformed", validation["errors"])

    def test_a_wrong_archive_size_is_rejected(self) -> None:
        def wrong_size(name: str, data: bytes):
            if name != "manifest.json":
                return data
            manifest = json.loads(data.decode("utf-8"))
            manifest["archive"][0]["size"] += 1
            return json.dumps(manifest).encode("utf-8")

        broken = _rebuild(self.backup, self.dest / "size.zip", wrong_size)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("archive_size_mismatch", validation["errors"])

    def test_a_wrong_archive_hash_is_rejected(self) -> None:
        def wrong_hash(name: str, data: bytes):
            if name != "manifest.json":
                return data
            manifest = json.loads(data.decode("utf-8"))
            manifest["archive"][0]["sha256"] = "0" * 64
            return json.dumps(manifest).encode("utf-8")

        broken = _rebuild(self.backup, self.dest / "hash.zip", wrong_hash)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("archive_checksum_mismatch", validation["errors"])

    def test_a_corrupt_database_is_rejected(self) -> None:
        def corrupt(name: str, data: bytes):
            if name != "flightdesk.sqlite3":
                return data
            body = bytearray(data)
            for i in range(100, min(len(body), 5000)):
                body[i] = 0
            return bytes(body)

        broken = _rebuild(self.backup, self.dest / "corrupt-db.zip", corrupt)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertTrue(validation["errors"])

    def test_the_manifest_count_must_match_the_database(self) -> None:
        def lie(name: str, data: bytes):
            if name != "manifest.json":
                return data
            manifest = json.loads(data.decode("utf-8"))
            manifest["counts"]["raw_files"] = manifest["counts"]["raw_files"] + 5
            return json.dumps(manifest).encode("utf-8")

        broken = _rebuild(self.backup, self.dest / "count.zip", lie)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"])
        self.assertIn("manifest_count_mismatch", validation["errors"])

    def test_a_newer_schema_is_refused(self) -> None:
        # A real future-schema database: bump user_version in the DB and record
        # the same value in the manifest, so the manifest is consistent and only
        # the unsupported version is wrong.
        import sqlite3

        with zipfile.ZipFile(self.backup) as zf:
            db_bytes = zf.read("flightdesk.sqlite3")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        tmp = self.dest / "bump.sqlite3"
        tmp.write_bytes(db_bytes)
        conn = sqlite3.connect(str(tmp))
        conn.execute("PRAGMA user_version=9999")
        conn.commit()
        conn.close()
        new_db = tmp.read_bytes()
        manifest["schema_version"] = 9999
        manifest["db"]["sha256"] = hashlib.sha256(new_db).hexdigest()
        manifest["db"]["size"] = len(new_db)
        broken = self.dest / "future.zip"
        with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as out:
            for name, data in _members(self.backup):
                if name == "flightdesk.sqlite3":
                    out.writestr(name, new_db)
                elif name == "manifest.json":
                    out.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out.writestr(name, data)
        validation = backup.validate_backup(broken)
        self.assertFalse(validation["ok"], validation)
        self.assertIn("backup_schema_newer_than_app", validation["errors"])


if __name__ == "__main__":
    unittest.main()
