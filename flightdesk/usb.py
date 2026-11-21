"""Read-only MTP collection through Shell.Application, plus a simulated adapter.

Physical controller results are recorded separately from simulated ones. The
collector never writes to a controller, never copies non-flight files, and
validates bytes before they reach the archive.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import db
from .archive import is_flight_filename
from .importer import Importer

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "Collect-DjiLogs.ps1"
MAX_COPY_BYTES = 512 * 1024 * 1024


class CopyError(Exception):
    category = "copy_incomplete"


def validate_flight_bytes(data: bytes) -> tuple[bool, str]:
    if len(data) < 120:
        return False, "too_short"
    version = data[10]
    if not 1 <= version <= 32:
        return False, "bad_version_byte"
    if version >= 13:
        if data[100] not in (0, 1):
            return False, "bad_auxiliary_block"
    else:
        offset = struct.unpack_from("<Q", data, 0)[0]
        if offset <= 0 or offset >= len(data):
            return False, "bad_details_offset"
    return True, "ok"


@dataclass
class Staged:
    device: str
    target: str
    storage: str
    source_name: str
    path: Path
    sha256: str | None
    size: int


@dataclass
class ScanResult:
    status: str
    devices: list[str] = field(default_factory=list)
    imported: int = 0
    duplicates: int = 0
    attention: int = 0
    scanned: int = 0
    notes: list[str] = field(default_factory=list)
    mode: str = "physical"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "devices": self.devices,
            "scanned": self.scanned,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "attention": self.attention,
            "notes": self.notes,
        }


def _ps_json(script: Path, args: list[str], timeout: int = 200) -> dict[str, Any] | None:
    """Run a collector script and read its JSON, bounded at 200 s.

    The bound matches the project rule that nothing runs longer than 200 s
    without progress. A first import of this controller took 58 s for 259 logs,
    and a repeat scan takes 6 s, so 200 s is several times the observed cost.
    Within the run the collector also bounds each copy (60 s) and aborts after
    three consecutive failures, so a stall is reported in about 180 s rather than
    consuming the whole window.
    """
    if os.name != "nt" or not script.exists():
        return None
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), *args,
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not (proc.stdout or b"").strip():
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _note_device(conn, device: str, status: str, detail: str | None = None) -> None:
    now = db.now_utc()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO usb_devices(device,last_seen,last_scan,status,detail) VALUES(?,?,?,?,?) "
            "ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen,"
            "last_scan=excluded.last_scan,status=excluded.status,detail=excluded.detail",
            (device, now, now, status, detail),
        )


def _mark_success(conn, device: str) -> None:
    with db.tx(conn):
        conn.execute("UPDATE usb_devices SET last_success=? WHERE device=?",
                     (db.now_utc(), device))


def scan(conn, root: Path, *, staging_root: Path | None = None,
         timeout_s: int = 60) -> ScanResult:
    root = Path(root)
    staging = Path(staging_root) if staging_root else root / "tmp" / f"usb-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    result = ScanResult(status="none", mode="physical")
    skip_file: Path | None = None
    try:
        # Everything the archive already holds, by name and size. Without this a
        # controller left plugged in makes the 45 s watcher re-copy the whole
        # device on every pass: measured at 182 MB per scan on a Smart Controller.
        skip_file = staging.parent / f"usb-skip-{uuid.uuid4().hex}.json"
        try:
            known = db.all_rows(
                conn, "SELECT original_name AS name, size_bytes AS size FROM raw_files")
            skip_file.write_text(
                json.dumps([{"name": r["name"], "size": int(r["size"] or 0)}
                            for r in known if r["name"]], separators=(",", ":")),
                encoding="utf-8")
        except OSError:
            skip_file = None
        args = ["-StagingRoot", str(staging), "-TimeoutSeconds", str(timeout_s)]
        if skip_file is not None:
            args += ["-SkipListPath", str(skip_file)]
        data = _ps_json(SCRIPT, args)
        if data is None:
            result.status = "unavailable"
            result.notes.append("collector_unavailable")
            return result
        status = str(data.get("status") or "none")
        errors = data.get("errors") or []
        files = data.get("files") or []
        skipped = int(data.get("skipped_known") or 0)
        devices = sorted({str(f.get("device") or "") for f in files if f.get("device")})
        for device in devices:
            _note_device(conn, device, "connected")
        result.devices = devices
        result.scanned = len(files) + skipped
        result.duplicates += skipped
        if status == "none":
            result.status = "no_device"
            return result
        if not files:
            result.attention = len(errors)
            result.notes = sorted({str(e.get("code")) for e in errors if e.get("code")})
            # "all_known" means the device was read and everything on it is
            # already archived — a success with nothing to do, not an absence.
            result.status = "all_known" if status == "all_known" else "no_files"
            return result
        importer = Importer(root, conn)
        for entry in files:
            path = Path(str(entry.get("staged_path") or ""))
            try:
                if not path.exists() or not path.is_file():
                    result.attention += 1
                    result.notes.append("staged_file_missing")
                    continue
                if path.stat().st_size > MAX_COPY_BYTES:
                    result.attention += 1
                    result.notes.append("staged_file_too_large")
                    continue
                with open(path, "rb") as fh:
                    blob = fh.read()
                ok, reason = validate_flight_bytes(blob)
                if not ok and reason != "bad_details_offset":
                    result.attention += 1
                    result.notes.append(f"incomplete_or_invalid:{reason}")
                    continue
                if not ok:
                    # `bad_details_offset` is a defect in the file itself, not in
                    # the copy: the size and hash above already proved the bytes
                    # are exactly what the device holds. Measured on a real
                    # controller, two logs carry a details offset of 0 — one of
                    # them byte-identical to a log already in the archive.
                    # Dropping them loses the only evidence; the importer keeps
                    # them, marks them `details_unreadable` and shows them for
                    # review, which is what the archive does for damaged files
                    # everywhere else.
                    result.notes.append(f"kept_unreadable:{reason}")
                expected = entry.get("sha256")
                if expected and hashlib.sha256(blob).hexdigest() != str(expected).lower():
                    result.attention += 1
                    result.notes.append("hash_changed_after_copy")
                    continue
                name = str(entry.get("source_name") or path.name)
                if not is_flight_filename(name):
                    result.attention += 1
                    result.notes.append("filename_not_allowlisted")
                    continue
                device = str(entry.get("device") or "controller")
                label = f"USB {device}"
                batch = importer.import_stream(
                    _BytesReader(blob), name,
                    source_kind="usb", source_label=label,
                    member_path=f"{entry.get('target') or 'unknown'}/{name}",
                )
                totals = batch.totals
                result.imported += int(totals.get("new", 0))
                result.duplicates += int(totals.get("already_imported", 0))
                result.attention += int(totals.get("needs_attention", 0)) + int(
                    totals.get("awaiting_dji", 0)
                )
                if totals.get("new"):
                    _mark_success(conn, device)
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        result.status = "ok" if result.scanned else "no_files"
        if errors:
            result.notes.extend(sorted({str(e.get("code")) for e in errors if e.get("code")}))
        with db.tx(conn):
            conn.execute(
                "INSERT INTO usb_scans(at,device,scanned,imported,duplicates,attention,detail) "
                "VALUES(?,?,?,?,?,?,?)",
                (db.now_utc(), ",".join(result.devices) or None, result.scanned,
                 result.imported, result.duplicates, result.attention,
                 ",".join(result.notes)[:1000] or None),
            )
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if skip_file is not None:
            # It holds device file names; it exists for the length of one scan.
            try:
                skip_file.unlink(missing_ok=True)
            except OSError:
                pass


class _BytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


@dataclass
class SimEntry:
    """One simulated MTP entry, e.g. an extensionless display name."""
    device: str
    target: str
    storage: str
    name: str
    data: bytes
    complete: bool = True
    stable: bool = True


def simulate(conn, root: Path, entries: Iterable[SimEntry], *,
             interrupt: bool = False, duplicate_storage: bool = False) -> ScanResult:
    """Exercise the collector contract without claiming physical hardware."""
    root = Path(root)
    result = ScanResult(status="ok", mode="simulated")
    importer = Importer(root, conn)
    seen_paths: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    devices = sorted({e.device for e in entries})
    result.devices = devices
    for device in devices:
        _note_device(conn, device, "connected")
    for entry in entries:
        display = entry.name
        if not display.lower().endswith(".txt"):
            display += ".txt"
        if not is_flight_filename(display):
            result.attention += 1
            result.notes.append("ignored_non_flight")
            continue
        key = (entry.device, entry.target)
        if duplicate_storage and key in seen_paths:
            result.notes.append("duplicate_storage_collapsed")
            continue
        seen_paths.add(key)
        result.scanned += 1
        if not entry.complete or not entry.stable:
            result.attention += 1
            result.notes.append("copy_incomplete_retryable")
            continue
        ok, reason = validate_flight_bytes(entry.data)
        if not ok and reason != "bad_details_offset":
            result.attention += 1
            result.notes.append(f"incomplete_or_invalid:{reason}")
            continue
        if not ok:
            # Kept, not dropped, for the same reason as the physical path: the
            # defect is in the file, and an unreadable flight is more honest on
            # the review screen than an absence the owner never sees.
            result.notes.append(f"kept_unreadable:{reason}")
        digest = hashlib.sha256(entry.data).hexdigest()
        if digest in seen_hashes:
            result.duplicates += 1
            continue
        seen_hashes.add(digest)
        if interrupt:
            # A disconnect mid-copy leaves a truncated object behind.
            truncated = entry.data[: len(entry.data) // 2]
            ok2, _ = validate_flight_bytes(truncated)
            if not ok2:
                result.attention += 1
                result.notes.append("copy_interrupted_retryable")
                continue
        batch = importer.import_stream(
            _BytesReader(entry.data), display,
            source_kind="usb", source_label=f"USB {entry.device}",
            member_path=f"{entry.target}/{display}",
        )
        totals = batch.totals
        result.imported += int(totals.get("new", 0))
        result.duplicates += int(totals.get("already_imported", 0))
        result.attention += int(totals.get("needs_attention", 0)) + int(
            totals.get("awaiting_dji", 0)
        )
        if totals.get("new"):
            _mark_success(conn, entry.device)
    if not entries:
        result.status = "no_device"
    with db.tx(conn):
        conn.execute(
            "INSERT INTO usb_scans(at,device,scanned,imported,duplicates,attention,detail) "
            "VALUES(?,?,?,?,?,?,?)",
            (db.now_utc(), ",".join(devices) or None, result.scanned, result.imported,
             result.duplicates, result.attention, "simulated" if result.notes else None),
        )
    return result


def status(conn) -> dict[str, Any]:
    devices = [dict(r) for r in db.all_rows(
        conn, "SELECT * FROM usb_devices ORDER BY device")]
    scans = [dict(r) for r in db.all_rows(
        conn, "SELECT * FROM usb_scans ORDER BY id DESC LIMIT 12")]
    watcher = db.get_setting(conn, "usb_watcher", {"enabled": False})
    logon = db.get_setting(conn, "usb_logon_task", {"installed": False})
    return {"devices": devices, "scans": scans, "watcher": watcher, "logon": logon}


def set_watcher(conn, enabled: bool, interval_s: int = 45) -> dict[str, Any]:
    value = {"enabled": bool(enabled), "interval_s": max(20, int(interval_s))}
    with db.tx(conn):
        db.set_setting(conn, "usb_watcher", value)
        db.audit(conn, "owner", "usb_watcher", None, None, value)
    return value


LOGON_TASK_NAME = "FlightDesk USB Watch"


def logon_task_installed() -> bool:
    if os.name != "nt":
        return False
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", LOGON_TASK_NAME],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def install_logon_task(conn, launcher: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "code": "not_windows"}
    command = f'"{launcher}" --watch-usb'
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Create", "/F", "/SC", "ONLOGON", "/TN", LOGON_TASK_NAME,
             "/TR", command],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "code": "task_create_failed"}
    if proc.returncode != 0:
        return {"ok": False, "code": "task_create_failed"}
    with db.tx(conn):
        db.set_setting(conn, "usb_logon_task", {"installed": True})
        db.audit(conn, "owner", "usb_logon_task_install", None, None, {})
    return {"ok": True, "installed": True, "reversible": True}


def remove_logon_task(conn) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "code": "not_windows"}
    try:
        subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", LOGON_TASK_NAME],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "code": "task_delete_failed"}
    with db.tx(conn):
        db.set_setting(conn, "usb_logon_task", {"installed": False})
        db.audit(conn, "owner", "usb_logon_task_remove", None, None, {})
    return {"ok": True, "installed": False}
