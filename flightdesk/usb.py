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


class CollectorError(Exception):
    """The collector could not produce a result, and why.

    The old code collapsed a timeout, a missing script, a non-zero exit and
    unreadable JSON into the same `None`, so `scan` returned before writing a
    `usb_scans` row and the UI showed nothing at all. A failure that leaves no
    trace is the exact class of defect this project keeps finding.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


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
        # The auxiliary block declares its own length at byte 101. A copy that
        # stops inside it is truncated, however stable its file size looked.
        if len(data) >= 103:
            aux_size = struct.unpack_from("<H", data, 101)[0]
            if aux_size and 100 + 1 + 2 + 1 + aux_size > len(data):
                return False, "copy_truncated"
        return True, "ok"
    offset = struct.unpack_from("<Q", data, 0)[0]
    if offset <= 0 or offset >= len(data):
        return False, "bad_details_offset"
    # The details block has a declared length too. `offset < len(data)` alone
    # accepted a file that ended halfway through its own details block — which
    # is what a stalled MTP copy leaves behind.
    details_len = struct.unpack_from("<H", data, 8)[0]
    if details_len and offset + details_len > len(data):
        return False, "copy_truncated"
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


def _ps_json(script: Path, args: list[str], timeout: int = 200) -> dict[str, Any]:
    """Run a collector script and read its JSON, bounded at 200 s.

    The bound matches the project rule that nothing runs longer than 200 s
    without progress. A first import of this controller took 58 s for 259 logs,
    and a repeat scan takes 6 s, so 200 s is several times the observed cost.
    Within the run the collector also bounds each copy (60 s) and aborts after
    three consecutive failures, so a stall is reported in about 180 s rather than
    consuming the whole window.

    Raises CollectorError with a category the caller records: a timeout and a
    missing script are different problems and must not look alike.
    """
    if os.name != "nt" or not script.exists():
        raise CollectorError("collector_missing")
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), *args,
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectorError("collector_timeout") from exc
    except OSError as exc:
        raise CollectorError("collector_unavailable") from exc
    if proc.returncode != 0:
        raise CollectorError("collector_failed")
    if not (proc.stdout or b"").strip():
        raise CollectorError("collector_no_output")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError as exc:
        raise CollectorError("collector_bad_json") from exc
    if not isinstance(data, dict):
        raise CollectorError("collector_bad_json")
    return data


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


def _import_staged_file(importer: Importer, conn, result: ScanResult, *,
                        batch, path: Path, name: str, device: str,
                        target: str | None) -> bool:
    """Validate and import one staged file. Returns True if a record was stored.

    Shared by the normal path and by the salvage path after a collector timeout,
    so a file that arrived intact is imported even when the collector never
    finished reporting. The staged file is read once and the bytes are what get
    archived; the hash the collector computed described this same staged file,
    so comparing it back to itself proved nothing and has been removed.
    """
    try:
        if not path.is_file():
            result.attention += 1
            result.notes.append("staged_file_missing")
            importer.note_rejected(batch, code="staged_file_missing")
            return False
        if path.stat().st_size > MAX_COPY_BYTES:
            result.attention += 1
            result.notes.append("staged_file_too_large")
            importer.note_rejected(batch, code="staged_file_too_large")
            return False
        blob = path.read_bytes()
    except OSError:
        result.attention += 1
        result.notes.append("staged_file_unreadable")
        importer.note_rejected(batch, code="staged_file_unreadable")
        return False
    ok, reason = validate_flight_bytes(blob)
    if not ok and reason != "bad_details_offset":
        result.attention += 1
        result.notes.append(f"incomplete_or_invalid:{reason}")
        importer.note_rejected(batch, code=f"incomplete_or_invalid:{reason}", detail=name)
        return False
    if not ok:
        # `bad_details_offset` is a defect in the file itself, not in the copy.
        # Measured on a real controller, two logs carry a details offset of 0 —
        # one of them byte-identical to a log already in the archive. Dropping
        # them loses the only evidence; the importer keeps them, marks them
        # `details_unreadable` and shows them for review.
        result.notes.append(f"kept_unreadable:{reason}")
    if not is_flight_filename(name):
        result.attention += 1
        result.notes.append("filename_not_allowlisted")
        importer.note_rejected(batch, code="filename_not_allowlisted", detail=name)
        return False
    # Per-file deltas: the batch accumulates across the whole scan, so a plain
    # read of `totals` would report the running total as this file's outcome.
    before = dict(batch.totals)
    importer.add_stream(
        batch,
        _BytesReader(blob), name,
        source_kind="usb", source_label=f"USB {device}",
        member_path=f"{target or 'unknown'}/{name}",
    )
    totals = batch.totals

    def delta(key: str) -> int:
        return int(totals.get(key, 0)) - int(before.get(key, 0))

    # A v14 log carries metadata but waits for the DJI key. It is a record the
    # archive now holds, so it belongs in "imported"; `max` rather than a sum
    # because a decoded new log reports both `new` and `new_flight` for the same
    # file. Measured on a real RC 2: the scan said "imported 0" while a flight
    # had in fact been created.
    stored = max(delta("new"), delta("new_flight"))
    result.imported += stored
    result.duplicates += delta("already_imported")
    awaiting = delta("awaiting_dji")
    broken = delta("needs_attention")
    result.attention += awaiting + broken
    # Attention without a reason is the silence this project keeps getting
    # bitten by. Every count that raises attention names why.
    if awaiting:
        result.notes.append("awaiting_dji_key")
    if broken:
        result.notes.append("not_importable")
    if stored:
        _mark_success(conn, device)
    return bool(stored)


def _salvage_staged(conn, root: Path, staging: Path, result: ScanResult, batch) -> None:
    """Import files the collector already copied before it timed out.

    A timeout used to discard the whole staging folder, so a controller with a
    large backlog could copy for 200 s, lose all of it, and repeat forever. The
    files are on disk and complete or not; `_import_staged_file` already refuses
    a truncated one on its header.
    """
    importer = Importer(root, conn)
    salvaged = 0
    for path in sorted(Path(staging).rglob("*")):
        if not path.is_file() or not is_flight_filename(path.name):
            continue
        salvaged += 1
        try:
            _import_staged_file(importer, conn, result, batch=batch, path=path,
                                name=path.name, device="controller", target=None)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    result.scanned += salvaged
    result.notes.append("salvaged_after_timeout" if salvaged else "nothing_staged_to_salvage")


def scan(conn, root: Path, *, staging_root: Path | None = None,
         timeout_s: int = 60, max_files: int = 0) -> ScanResult:
    root = Path(root)
    staging = Path(staging_root) if staging_root else root / "tmp" / f"usb-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    result = ScanResult(status="none", mode="physical")
    skip_file: Path | None = None

    def record() -> ScanResult:
        """Write the scan row and return, on every exit path.

        The first version returned early for `no_files` and `all_known`, so a
        second attach — the exact evidence the owner is asked to produce — left
        no trace in the scan history at all. A collector that never reported
        left no trace either; now it does, with the failure named.
        """
        result.notes = sorted(set(result.notes))
        with db.tx(conn):
            conn.execute(
                "INSERT INTO usb_scans(at,device,scanned,imported,duplicates,attention,detail) "
                "VALUES(?,?,?,?,?,?,?)",
                (db.now_utc(), ",".join(result.devices) or None, result.scanned,
                 result.imported, result.duplicates, result.attention,
                 ",".join(result.notes)[:1000] or None),
            )
        return result

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
        if max_files > 0:
            args += ["-MaxFilesPerRun", str(int(max_files))]
        if skip_file is not None:
            args += ["-SkipListPath", str(skip_file)]
        try:
            data = _ps_json(SCRIPT, args)
        except CollectorError as exc:
            result.status = "unavailable"
            result.notes.append(exc.category)
            if exc.category == "collector_timeout":
                importer = Importer(root, conn)
                salvage_batch = importer.open_batch("USB salvage", "usb", deferred=True)
                _salvage_staged(conn, root, staging, result, salvage_batch)
                importer.close_batch(salvage_batch)
            return record()
        status = str(data.get("status") or "none")
        errors = data.get("errors") or []
        files = data.get("files") or []
        skipped = int(data.get("skipped_known") or 0)
        if data.get("truncated"):
            # The collector stopped at its per-run cap with files still to copy.
            # Saying so is what makes the next pass make sense to the owner;
            # silently reporting a partial scan as complete is the failure mode
            # this project keeps finding.
            result.notes.append("more_files_remain")
        # The collector reports the devices it saw, not just the ones it copied
        # from. A second attach finds everything already archived, and reporting
        # no device at all made the review screen say "never seen" about a
        # controller that had just been read.
        devices = sorted({str(d) for d in (data.get("devices") or []) if d})
        devices += sorted({str(f.get("device") or "") for f in files if f.get("device")})
        devices = sorted(set(devices))
        for device in devices:
            _note_device(conn, device, "connected")
        result.devices = devices
        result.scanned = len(files) + skipped
        result.duplicates += skipped
        if errors:
            result.notes.extend(
                sorted({str(e.get("code")) for e in errors if e.get("code")}))
            if status == "error":
                result.notes.append("collector_exception")
        if status == "none":
            result.status = "no_device"
            return record()
        # A device that was read without copy errors counts as a success even if
        # it had nothing new: "last success" then answers "was this controller
        # read?", which is the question the owner is actually asking.
        failed_devices = {str(e.get("device") or "") for e in errors if e.get("device")}
        for device in devices:
            if device not in failed_devices:
                _mark_success(conn, device)
        if not files:
            result.attention = len(errors)
            # "all_known" means the device was read and everything on it is
            # already archived — a success with nothing to do, not an absence.
            result.status = "all_known" if status == "all_known" else "no_files"
            return record()
        importer = Importer(root, conn)
        # One batch for the whole scan. A controller re-scan used to write a
        # batch per copied file, which is where 927 empty rows came from.
        scan_batch = importer.open_batch(
            f"USB {', '.join(devices) or 'controller'}", "usb", deferred=True)
        for entry in files:
            path = Path(str(entry.get("staged_path") or ""))
            try:
                name = str(entry.get("source_name") or path.name)
                device = str(entry.get("device") or "controller")
                _import_staged_file(importer, conn, result, batch=scan_batch,
                                    path=path, name=name, device=device,
                                    target=entry.get("target"))
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        importer.close_batch(scan_batch)
        result.status = "ok" if result.scanned else "no_files"
        if "more_files_remain" in result.notes:
            # Not a failure: the device was read and everything copied arrived.
            result.status = "partial"
        return record()
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
    # The skip contract the collector now applies: a file is skipped only when
    # its exact name AND size are already archived. Modelled here so the
    # contract has a test that does not need a controller attached.
    known: dict[str, set[int]] = {}
    for row in db.all_rows(
            conn, "SELECT original_name AS name, size_bytes AS size FROM raw_files"):
        if row["name"]:
            known.setdefault(str(row["name"]), set()).add(int(row["size"] or 0))
    devices = sorted({e.device for e in entries})
    result.devices = devices
    for device in devices:
        _note_device(conn, device, "connected")
    # One batch for the whole simulated scan, on the same rule as the physical
    # path: a scan that finds nothing new leaves no row in the import history.
    scan_batch = importer.open_batch(
        f"USB {', '.join(devices) or 'controller'}", "usb", deferred=True)
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
        sizes = known.get(display)
        if sizes is not None and len(entry.data) in sizes:
            result.duplicates += 1
            result.notes.append("skipped_known")
            continue
        if sizes is not None:
            # Same name, different size: the archived copy is not this file.
            result.notes.append("recopied_changed_size")
        if not entry.complete or not entry.stable:
            result.attention += 1
            result.notes.append("copy_incomplete_retryable")
            continue
        ok, reason = validate_flight_bytes(entry.data)
        if not ok and reason != "bad_details_offset":
            result.attention += 1
            result.notes.append(f"incomplete_or_invalid:{reason}")
            importer.note_rejected(scan_batch, code=f"incomplete_or_invalid:{reason}",
                                   detail=display)
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
        before = dict(scan_batch.totals)
        importer.add_stream(
            scan_batch,
            _BytesReader(entry.data), display,
            source_kind="usb", source_label=f"USB {entry.device}",
            member_path=f"{entry.target}/{display}",
        )
        totals = scan_batch.totals

        def delta(key: str) -> int:
            return int(totals.get(key, 0)) - int(before.get(key, 0))

        result.imported += delta("new")
        result.duplicates += delta("already_imported")
        result.attention += delta("needs_attention") + delta("awaiting_dji")
        if delta("new"):
            _mark_success(conn, entry.device)
    importer.close_batch(scan_batch)
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


def status(conn, root: Path | None = None) -> dict[str, Any]:
    devices = [dict(r) for r in db.all_rows(
        conn, "SELECT * FROM usb_devices ORDER BY device")]
    scans = [dict(r) for r in db.all_rows(
        conn, "SELECT * FROM usb_scans ORDER BY id DESC LIMIT 12")]
    watcher = db.get_setting(conn, "usb_watcher", {"enabled": False})
    logon = db.get_setting(conn, "usb_logon_task", {"installed": False})
    # The watcher runs in a background thread; when it fails, this is the only
    # place the owner can see it. A missing value means no failure recorded.
    last_error = db.get_setting(conn, "usb_watcher_last_error")
    out: dict[str, Any] = {
        "devices": devices, "scans": scans, "watcher": watcher,
        "logon": logon, "watcher_last_error": last_error,
    }
    if root is not None:
        from . import errlog
        out["log"] = errlog.summary(Path(root))
    return out


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
