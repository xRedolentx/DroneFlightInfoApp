"""Consistent local backup, validation and restore-into-a-new-root."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__, db, paths, security

BACKUP_PREFIX = "FlightDesk-backup-"
LOCK_NAME = "backup.lock"
MANIFEST = "manifest.json"
DB_NAME = "flightdesk.sqlite3"
# The DJI key lives beside the database, not in it, so it has to be carried
# explicitly. It is stored as a Windows DPAPI blob, so what travels is a blob
# that only this Windows user on this machine can decrypt — useful for a restore
# here, useless anywhere else. The manifest says so in plain words.
KEY_MEMBER = "keychains/dji_key.bin"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return False
    return not (len(normalized) > 1 and normalized[1] == ":")


def _lock(root: Path) -> Path:
    lock = Path(root) / "tmp" / LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
            if age < 2 * 3600:
                raise RuntimeError("backup_in_progress")
        except OSError:
            pass
        lock.unlink(missing_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return lock


def is_backup_running(root: Path) -> bool:
    lock = Path(root) / "tmp" / LOCK_NAME
    if not lock.exists():
        return False
    try:
        return time.time() - lock.stat().st_mtime < 2 * 3600
    except OSError:
        return False


def create_backup(conn: sqlite3.Connection, root: Path, destination: Path,
                  note: str = "", allow_cloud: bool = False) -> dict[str, Any]:
    root = Path(root)
    destination = Path(destination)
    warnings: list[str] = []
    marker = security.cloud_sync_marker(destination)
    if marker:
        warnings.append(f"destination is inside a {marker} sync folder")
        if not allow_cloud:
            return {"ok": False, "code": "cloud_sync_destination",
                    "message": "choose a non-synced folder or confirm the warning",
                    "warnings": warnings}
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"ok": False, "code": "destination_unavailable"}
    try:
        lock = _lock(root)
    except RuntimeError:
        return {"ok": False, "code": "backup_in_progress"}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = root / "tmp" / f"backup-{stamp}-{os.getpid()}"
    archive_dir = staging / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Consistent SQLite snapshot, including WAL pages.
        src = db.connect(paths.db_path(root))
        backup_db = staging / DB_NAME
        dst = sqlite3.connect(str(backup_db))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        check = db.connect(backup_db)
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        schema = check.execute("PRAGMA user_version").fetchone()[0]
        raw_rows = db.all_rows(check, "SELECT sha256,size_bytes,stored_rel FROM raw_files")
        counts = db.table_counts(check)
        check.close()
        if integrity != "ok":
            return {"ok": False, "code": "snapshot_integrity_failed"}
        archive_manifest = []
        for row in raw_rows:
            rel = row["stored_rel"]
            src_path = root / "archive" / rel
            if not src_path.exists():
                return {"ok": False, "code": "missing_archive_object"}
            dst_path = staging / "archive" / rel
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            if _sha256_file(dst_path) != row["sha256"]:
                return {"ok": False, "code": "archive_copy_checksum_failed"}
            archive_manifest.append({
                "rel": rel, "sha256": row["sha256"], "size": row["size_bytes"],
            })
        key_manifest = None
        src_key = root / KEY_MEMBER
        if src_key.is_file():
            dst_key = staging / KEY_MEMBER
            dst_key.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_key, dst_key)
            key_manifest = {
                "name": KEY_MEMBER,
                "sha256": _sha256_file(dst_key),
                "size": dst_key.stat().st_size,
                "protection": "dpapi_user",
            }
            if _sha256_file(dst_key) != _sha256_file(src_key):
                return {"ok": False, "code": "key_copy_checksum_failed"}
        manifest = {
            "app": "FlightDesk",
            "app_version": __version__,
            "schema_version": schema,
            "created_at": db.now_utc(),
            "db": {"name": DB_NAME, "sha256": _sha256_file(backup_db),
                   "size": backup_db.stat().st_size},
            "archive": archive_manifest,
            "counts": counts,
            "key_included": key_manifest is not None,
            "key": key_manifest,
            "keychains_note": (
                "Cached keychain blobs are in the database but are DPAPI-bound to "
                "this Windows user and machine; after a reinstall, reauthorize DJI."
            ),
            "key_note": (
                "The DJI key is included as its DPAPI-protected blob. It can be "
                "decrypted only by this Windows user on this machine, so a restore "
                "here needs no re-entry and a restore elsewhere must re-enter the "
                "key. Anyone who can read this ZIP as this user can therefore use "
                "the key."
            ) if key_manifest else None,
            "note": note,
        }
        manifest_path = staging / MANIFEST
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest_sha = _sha256_file(manifest_path)
        target = destination / f"{BACKUP_PREFIX}{stamp}.zip"
        if target.exists():
            target = destination / f"{BACKUP_PREFIX}{stamp}-{os.getpid()}.zip"
        # Write beside the destination and rename only once complete, so an
        # interrupted or disk-full backup can never leave a truncated ZIP
        # that looks like a real backup.
        partial = target.with_name(target.name + ".part")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.write(manifest_path, MANIFEST)
                zf.write(backup_db, DB_NAME)
                for item in archive_manifest:
                    zf.write(staging / "archive" / item["rel"], f"archive/{item['rel']}")
                if key_manifest is not None:
                    zf.write(staging / KEY_MEMBER, KEY_MEMBER)
            os.replace(partial, target)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            code = "disk_full" if getattr(exc, "errno", None) == 28 else "backup_write_failed"
            with db.tx(conn):
                conn.execute(
                    "INSERT INTO backup_runs(created_at,destination,status,note) "
                    "VALUES(?,?,?,?)",
                    (db.now_utc(), str(target), "failed", code),
                )
            return {"ok": False, "code": code, "warnings": warnings}
        zip_sha = _sha256_file(target)
        (target.with_suffix(target.suffix + ".sha256")).write_text(
            f"{zip_sha}  {target.name}\n", encoding="utf-8"
        )
        with db.tx(conn):
            conn.execute(
                "INSERT INTO backup_runs(created_at,destination,manifest_sha256,status,note) "
                "VALUES(?,?,?,?,?)",
                (db.now_utc(), str(target), zip_sha, "ok", note),
            )
        return {
            "ok": True,
            "path": str(target),
            "sha256": zip_sha,
            "size": target.stat().st_size,
            "counts": counts,
            "key_included": key_manifest is not None,
            "manifest_sha256": manifest_sha,
            "warnings": warnings,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def validate_backup(path: Path, *, max_members: int = 20000,
                    max_total: int = 16 * 1024 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return {"ok": False, "errors": ["backup_not_found"], "warnings": []}
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if len(names) > max_members:
                return {"ok": False, "errors": ["too_many_members"], "warnings": []}
            total = sum(i.file_size for i in zf.infolist())
            if total > max_total:
                return {"ok": False, "errors": ["backup_too_large"], "warnings": []}
            for name in names:
                if not _safe_member(name):
                    return {"ok": False, "errors": ["unsafe_member_path"], "warnings": []}
            if MANIFEST not in names or DB_NAME not in names:
                return {"ok": False, "errors": ["missing_manifest_or_db"], "warnings": []}
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
            if manifest.get("app") != "FlightDesk":
                errors.append("wrong_app")
            if int(manifest.get("schema_version", -1)) != SCHEMA_VERSION:
                warnings.append(
                    f"schema {manifest.get('schema_version')} vs app {SCHEMA_VERSION}"
                )
            db_info = manifest.get("db") or {}
            db_bytes = zf.read(DB_NAME)
            if hashlib.sha256(db_bytes).hexdigest() != db_info.get("sha256"):
                errors.append("database_checksum_mismatch")
            archive_map = {item["rel"]: item for item in manifest.get("archive", [])}
            for name in names:
                if name.startswith("archive/") and not name.endswith("/"):
                    rel = name[len("archive/"):]
                    item = archive_map.get(rel)
                    if item is None:
                        warnings.append("archive_member_not_in_manifest")
                        continue
                    h = hashlib.sha256()
                    with zf.open(name) as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                    if h.hexdigest() != item.get("sha256"):
                        errors.append("archive_checksum_mismatch")
            key_info = manifest.get("key") or {}
            if KEY_MEMBER in names:
                key_bytes = zf.read(KEY_MEMBER)
                if key_info.get("sha256") and hashlib.sha256(key_bytes).hexdigest() != key_info["sha256"]:
                    errors.append("key_checksum_mismatch")
                if manifest.get("key_included") is False:
                    warnings.append("key_present_but_manifest_says_no")
                # An included key that this account cannot decrypt is not a
                # broken backup: the data is intact and the key is intact as
                # bytes. It is a restore that will need the key re-entered.
                try:
                    security.dpapi_unprotect(key_bytes)
                except (security.SecretError, ValueError, OSError):
                    warnings.append("key_not_decryptable_on_this_account")
            elif manifest.get("key_included"):
                warnings.append("key_missing_from_backup")
            tmp = Path(os.environ.get("TEMP", ".")) / f"fd-validate-{os.getpid()}-{time.time_ns()}.sqlite3"
            try:
                tmp.write_bytes(db_bytes)
                c = db.connect(tmp, readonly=True)
                integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
                fk = c.execute("PRAGMA foreign_key_check").fetchall()
                schema = c.execute("PRAGMA user_version").fetchone()[0]
                counts = db.table_counts(c)
                c.close()
                if integrity != "ok":
                    errors.append("integrity_check_failed")
                if fk:
                    errors.append("foreign_key_violation")
                if int(manifest.get("counts", {}).get("raw_files", -1)) != counts["raw_files"]:
                    errors.append("manifest_count_mismatch")
            finally:
                tmp.unlink(missing_ok=True)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"unreadable_backup:{type(exc).__name__}"],
                "warnings": warnings}
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "manifest": manifest,
        "counts": counts if "counts" in locals() else {},
        "schema_version": schema if "schema" in locals() else None,
    }


def restore_backup(path: Path, target_root: Path, *,
                   allow_existing: bool = False) -> dict[str, Any]:
    validation = validate_backup(path)
    if not validation.get("ok"):
        return {"ok": False, "code": "validation_failed", "validation": validation}
    target_root = Path(target_root).resolve()
    live = paths.default_data_root().resolve()
    if target_root == live:
        return {"ok": False, "code": "refuses_live_root"}
    if target_root.exists() and any(target_root.iterdir()) and not allow_existing:
        return {"ok": False, "code": "target_not_empty"}
    staging = target_root.with_name(target_root.name + ".restore-tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if not _safe_member(info.filename):
                    return {"ok": False, "code": "unsafe_member_path"}
            zf.extractall(staging)
        manifest = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
        if _sha256_file(staging / DB_NAME) != (manifest.get("db") or {}).get("sha256"):
            return {"ok": False, "code": "restored_db_checksum_failed"}
        for item in manifest.get("archive", []):
            p = staging / "archive" / item["rel"]
            if not p.exists() or _sha256_file(p) != item.get("sha256"):
                return {"ok": False, "code": "restored_archive_checksum_failed"}
        # Move the existing root aside rather than deleting it. Deleting first
        # leaves a window where a crash loses the only copy: the target is gone
        # and the staged copy is not yet in place. Renaming is reversible, so an
        # interruption at any point leaves either the old root or the new one.
        previous: Path | None = None
        if target_root.exists():
            previous = target_root.with_name(
                target_root.name + ".previous-" + uuid.uuid4().hex[:8])
            os.replace(target_root, previous)
        try:
            os.replace(staging, target_root)
        except OSError:
            if previous is not None and not target_root.exists():
                os.replace(previous, target_root)
            raise
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return {
        "ok": True,
        "target_root": str(target_root),
        "counts": validation.get("counts", {}),
        "warnings": validation.get("warnings", []),
    }


def activate_data_root(target_root: Path, home: Path | None = None) -> dict[str, Any]:
    target_root = Path(target_root).resolve()
    if not (target_root / DB_NAME).exists():
        return {"ok": False, "code": "no_database_in_target"}
    home = Path(home) if home else paths.app_home()
    home.mkdir(parents=True, exist_ok=True)
    pointer = home / "active_data_root.txt"
    security.atomic_private_write(pointer, str(target_root).encode("utf-8"))
    return {"ok": True, "active_data_root": str(target_root),
            "restart_required": True}
