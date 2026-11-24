"""Consistent local backup, validation and restore-into-a-new-root."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
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
ARCHIVE_PREFIX = "archive/"
# The DJI key lives beside the database, not in it, so it has to be carried
# explicitly. It is stored as a Windows DPAPI blob, so what travels is a blob
# that only this Windows user on this machine can decrypt — useful for a restore
# here, useless anywhere else. The manifest says so in plain words.
KEY_MEMBER = "keychains/dji_key.bin"
# Every member a backup may contain. Anything else is refused rather than
# installed: a backup that carries an unexpected file is not a backup this
# program understands.
ALLOWED_TOP_LEVEL = frozenset({MANIFEST, DB_NAME, KEY_MEMBER})
REQUIRED_MEMBERS = (MANIFEST, DB_NAME)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    return _canonical_member(name) is not None


def _canonical_member(name: object) -> str | None:
    """The one spelling of a member name, or None when it must be refused.

    Backslashes, drive letters, absolute paths, `..` and `.` segments are how a
    ZIP hides a second copy of a path (`archive/ab/x.bin` and
    `archive\\ab\\.\\x.bin`). Two member names that canonicalise equal are one
    file, and a backup that lists both is ambiguous.
    """
    if not isinstance(name, str) or not name:
        return None
    raw = name.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return None
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or "\x00" in part:
            return None
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


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
            # Timestamp plus PID is not unique across processes that share a
            # second; add a random tag until a free name is found.
            for _ in range(50):
                candidate = destination / (
                    f"{BACKUP_PREFIX}{stamp}-{os.getpid()}-{uuid.uuid4().hex[:6]}.zip")
                if not candidate.exists():
                    target = candidate
                    break
            else:
                return {"ok": False, "code": "destination_unavailable"}
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
        # Creation validates the completed artifact with the same rules a
        # restore uses, so a backup is never reported as good on the strength
        # of having been written without raising.
        self_check = validate_backup(target)
        if not self_check.get("ok"):
            with db.tx(conn):
                conn.execute(
                    "INSERT INTO backup_runs(created_at,destination,manifest_sha256,status,note) "
                    "VALUES(?,?,?,?,?)",
                    (db.now_utc(), str(target), zip_sha, "failed",
                     "self_validation_failed:" + ",".join(self_check.get("errors", [])[:5])),
                )
            return {"ok": False, "code": "backup_validation_failed",
                    "path": str(target), "errors": self_check.get("errors", []),
                    "warnings": self_check.get("warnings", [])}
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
            "warnings": warnings + list(self_check.get("warnings", [])),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _manifest_entries(manifest: dict, errors: list[str]) -> dict[str, dict]:
    """Validate archive entries before indexing them.

    A malformed entry is dropped rather than trusted: an entry with a wrong
    type, a bad path, a duplicate name or a duplicate hash is recorded as an
    error and otherwise ignored.
    """
    out: dict[str, dict] = {}
    entries = manifest.get("archive")
    if not isinstance(entries, list):
        errors.append("manifest_archive_missing")
        return out
    seen_sha: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            errors.append("manifest_entry_malformed")
            continue
        rel = _canonical_member(item.get("rel"))
        sha = item.get("sha256")
        size = item.get("size")
        if (rel is None or not isinstance(sha, str) or len(sha) != 64
                or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            errors.append("manifest_entry_malformed")
            continue
        if rel in out:
            errors.append("manifest_duplicate_entry")
            continue
        if sha in seen_sha and seen_sha[sha] != rel:
            errors.append("manifest_duplicate_identity")
        seen_sha[sha] = rel
        out[rel] = {"rel": rel, "sha256": sha, "size": size}
    return out


def _hash_zf_member(zf: zipfile.ZipFile, name: str, errors: list[str]) -> str | None:
    h = hashlib.sha256()
    try:
        with zf.open(name) as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except (KeyError, OSError, zipfile.BadZipFile):
        errors.append("archive_member_unreadable")
        return None
    return h.hexdigest()


def _open_backup(path: Path) -> tuple[zipfile.ZipFile | None, dict | None]:
    try:
        return zipfile.ZipFile(path), None
    except (zipfile.BadZipFile, OSError) as exc:
        return None, {"ok": False, "errors": [f"unreadable_backup:{type(exc).__name__}"],
                      "warnings": []}


def validate_backup(path: Path, *, max_members: int = 20000,
                    max_total: int = 16 * 1024 * 1024 * 1024) -> dict[str, Any]:
    """Prove the backup is complete, not merely plausible.

    "Valid" means every original the database references is present exactly
    once, with the expected length and hash; the manifest, the ZIP members and
    the database agree; and the database itself is intact. The three
    inventories are reconciled against each other rather than each being
    checked in isolation — the reviewed build iterated only the members that
    happened to be present, so a backup missing an original validated clean.

    Hashes detect internal consistency, not authenticity: a backup rewritten
    wholesale, with matching hashes and a matching manifest, is consistent.
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    manifest: dict | None = None
    counts: dict[str, int] = {}
    schema: int | None = None
    if not path.is_file():
        return {"ok": False, "errors": ["backup_not_found"], "warnings": []}
    zf, failure = _open_backup(path)
    if zf is None:
        return failure  # type: ignore[return-value]
    tmp_db: Path | None = None
    try:
        with zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            if len(infos) > max_members:
                return {"ok": False, "errors": ["too_many_members"], "warnings": []}
            if sum(info.file_size for info in infos) > max_total:
                return {"ok": False, "errors": ["backup_too_large"], "warnings": []}

            # 1. Normalise member names. An alias or a duplicate name means two
            #    entries could install to one path; neither is accepted.
            members: dict[str, zipfile.ZipInfo] = {}
            lowered: set[str] = set()
            for info in infos:
                canon = _canonical_member(info.filename)
                if canon is None:
                    return {"ok": False, "errors": ["unsafe_member_path"],
                            "warnings": []}
                if canon != info.filename.replace("\\", "/"):
                    errors.append("member_path_alias")
                key = canon.lower()
                if key in lowered:
                    errors.append("duplicate_member_name")
                    continue
                lowered.add(key)
                members[canon] = info

            for required in REQUIRED_MEMBERS:
                if required not in members:
                    errors.append("missing_manifest_or_db")
                    return {"ok": False, "errors": sorted(set(errors)),
                            "warnings": warnings}

            unknown = [name for name in members
                       if name not in ALLOWED_TOP_LEVEL
                       and not name.startswith(ARCHIVE_PREFIX)]
            if unknown:
                errors.append("unknown_member")

            # 2. Manifest: shape first, then content.
            try:
                manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
            except (UnicodeDecodeError, ValueError, KeyError):
                return {"ok": False, "errors": sorted(set(errors + ["malformed_manifest"])),
                        "warnings": warnings}
            if not isinstance(manifest, dict):
                return {"ok": False, "errors": sorted(set(errors + ["malformed_manifest"])),
                        "warnings": warnings}
            if manifest.get("app") != "FlightDesk":
                errors.append("wrong_app")
            db_info = manifest.get("db")
            if not isinstance(db_info, dict) or not isinstance(db_info.get("sha256"), str):
                errors.append("manifest_db_entry_missing")
                db_info = {}

            # 3. The database: stream to a private temporary file, hashing as it
            #    goes, so a large database is never held in memory.
            fd, name = tempfile.mkstemp(prefix="fd-validate-", suffix=".sqlite3")
            tmp_db = Path(name)
            db_hash = hashlib.sha256()
            db_size = 0
            with os.fdopen(fd, "wb") as out, zf.open(DB_NAME) as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    db_hash.update(chunk)
                    db_size += len(chunk)
                    out.write(chunk)
            if db_hash.hexdigest() != db_info.get("sha256"):
                errors.append("database_checksum_mismatch")
            if db_info.get("size") is not None and db_info.get("size") != db_size:
                errors.append("database_size_mismatch")

            db_rows: dict[str, tuple[str, int]] = {}
            try:
                conn = db.connect(tmp_db, readonly=True)
                try:
                    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        errors.append("integrity_check_failed")
                    if conn.execute("PRAGMA foreign_key_check").fetchall():
                        errors.append("foreign_key_violation")
                    schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
                    counts = db.table_counts(conn)
                    for row in conn.execute(
                            "SELECT stored_rel,sha256,size_bytes FROM raw_files"):
                        db_rows[str(row["stored_rel"])] = (str(row["sha256"]),
                                                           int(row["size_bytes"] or 0))
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                errors.append(f"database_unreadable:{type(exc).__name__}")

            manifest_schema = manifest.get("schema_version")
            if not isinstance(manifest_schema, int) or isinstance(manifest_schema, bool):
                errors.append("manifest_schema_missing")
            elif schema is not None and manifest_schema != schema:
                errors.append("manifest_schema_mismatch")
            if schema is not None and schema > int(SCHEMA_VERSION):
                errors.append("backup_schema_newer_than_app")
            elif schema is not None and schema < int(SCHEMA_VERSION):
                warnings.append(f"backup_schema_older_than_app:{schema}<{SCHEMA_VERSION}")

            declared_counts = manifest.get("counts")
            if not isinstance(declared_counts, dict):
                errors.append("manifest_counts_missing")
            elif counts and isinstance(declared_counts.get("raw_files"), int):
                if declared_counts["raw_files"] != counts.get("raw_files"):
                    errors.append("manifest_count_mismatch")

            # 4. Reconcile manifest entries, ZIP members and database rows.
            entries = _manifest_entries(manifest, errors)
            archive_members = {name[len(ARCHIVE_PREFIX):]: info
                               for name, info in members.items()
                               if name.startswith(ARCHIVE_PREFIX)}
            for rel, item in entries.items():
                info = archive_members.get(rel)
                if info is None:
                    errors.append("archive_member_missing")
                    continue
                if item["size"] != info.file_size:
                    errors.append("archive_size_mismatch")
                digest = _hash_zf_member(zf, ARCHIVE_PREFIX + rel, errors)
                if digest is not None and digest != item["sha256"]:
                    errors.append("archive_checksum_mismatch")
            for rel in archive_members:
                if rel not in entries:
                    errors.append("archive_member_not_in_manifest")
            for rel, (sha, size) in db_rows.items():
                item = entries.get(rel)
                if item is None:
                    errors.append("original_missing_from_manifest")
                    continue
                if rel not in archive_members:
                    errors.append("original_missing_from_archive")
                    continue
                if item["sha256"] != sha:
                    errors.append("original_hash_mismatch")
                if item["size"] != size:
                    errors.append("original_size_mismatch")
            for rel in entries:
                if rel not in db_rows:
                    # Kept, hashed and installed, but nothing references it. Not
                    # a reason to refuse an otherwise complete backup.
                    warnings.append("archive_member_not_referenced")

            # 5. The DJI key. A declared key that is absent makes the backup
            #    incomplete; an intact blob this Windows account cannot decrypt
            #    is only a warning, because the data and the blob are both whole
            #    and the key can be re-entered after a restore elsewhere.
            key_info = manifest.get("key") if isinstance(manifest.get("key"), dict) else {}
            if KEY_MEMBER in members:
                if manifest.get("key_included") is False:
                    errors.append("key_present_but_manifest_says_no")
                key_bytes = zf.read(KEY_MEMBER)
                actual = hashlib.sha256(key_bytes).hexdigest()
                if key_info.get("sha256") and actual != key_info["sha256"]:
                    errors.append("key_checksum_mismatch")
                if isinstance(key_info.get("size"), int) and key_info["size"] != len(key_bytes):
                    errors.append("key_size_mismatch")
                try:
                    security.dpapi_unprotect(key_bytes)
                except (security.SecretError, ValueError, OSError):
                    warnings.append("key_not_decryptable_on_this_account")
            elif manifest.get("key_included"):
                errors.append("key_missing_from_backup")
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "errors": sorted(set(errors + [f"unreadable_backup:{type(exc).__name__}"])),
                "warnings": warnings}
    finally:
        if tmp_db is not None:
            try:
                tmp_db.unlink(missing_ok=True)
            except OSError:
                pass
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "manifest": manifest if manifest is not None else {},
        "counts": counts,
        "schema_version": schema,
    }


def restore_backup(path: Path, target_root: Path, *,
                   allow_existing: bool = False) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {"ok": False, "code": "backup_not_found"}
    # Work from a private copy so validation and extraction cannot end up
    # reading different files if the source is replaced between the two steps.
    fd, name = tempfile.mkstemp(prefix="fd-restore-src-", suffix=".zip")
    os.close(fd)
    source = Path(name)
    staging: Path | None = None
    try:
        shutil.copyfile(path, source)
        validation = validate_backup(source)
        if not validation.get("ok"):
            return {"ok": False, "code": "validation_failed", "validation": validation}
        manifest = validation.get("manifest") or {}
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
        # Extract exactly the members the validator approved — nothing that was
        # not in the manifest, and nothing discovered while unpacking.
        wanted = [MANIFEST, DB_NAME]
        wanted.extend(ARCHIVE_PREFIX + item["rel"] for item in manifest.get("archive", []))
        if manifest.get("key_included"):
            wanted.append(KEY_MEMBER)
        with zipfile.ZipFile(source) as zf:
            available = {info.filename for info in zf.infolist() if not info.is_dir()}
            for member in wanted:
                if member not in available:
                    return {"ok": False, "code": "restored_member_missing",
                            "validation": validation}
                destination = staging / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(destination, "wb") as out:
                    shutil.copyfileobj(src, out)
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
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
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
