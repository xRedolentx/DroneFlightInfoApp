"""Copy archived originals out to an ordinary folder, under their real names.

The archive is content-addressed (`archive/ab/cd/<sha256>.bin`), which is right
for integrity and useless anywhere else. This module puts the original DJI logs
somewhere another tool can read them, and makes re-running it cheap.

Rules that matter, and why:

  * **Copy, never link.** Another program editing a linked file would write
    straight through into the immutable archive.
  * **Only ever add.** Nothing in the destination is deleted or overwritten,
    including a file the owner put there. A name collision is resolved by
    suffixing, never by replacing.
  * **A manifest, so the second run is fast.** `flightdesk-export.json` maps a
    content hash to the name it was written under. A hash already exported, with
    its file still present at the recorded size, is skipped.
  * **Verify after copy.** The bytes are hashed again from the destination; a
    mismatch is reported, not silently left behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from . import db, errlog

MANIFEST_NAME = "flightdesk-export.json"
MANIFEST_VERSION = 1
DEFAULT_SETTINGS = {
    "folder": "",
    "variants": False,
    "group_by_year": False,
    "auto_sync": False,
}


def settings(conn: sqlite3.Connection) -> dict[str, Any]:
    value = dict(DEFAULT_SETTINGS)
    stored = db.get_setting(conn, "raw_export", {})
    if isinstance(stored, dict):
        value.update({k: stored[k] for k in DEFAULT_SETTINGS if k in stored})
    return value


def save_settings(conn: sqlite3.Connection, incoming: dict[str, Any]) -> dict[str, Any]:
    value = settings(conn)
    if "folder" in incoming:
        value["folder"] = str(incoming.get("folder") or "").strip()
    for flag in ("variants", "group_by_year", "auto_sync"):
        if flag in incoming:
            value[flag] = bool(incoming[flag])
    with db.tx(conn):
        db.set_setting(conn, "raw_export", value)
        db.audit(conn, "owner", "raw_export_settings", None, None,
                 {"variants": value["variants"], "group_by_year": value["group_by_year"],
                  "auto_sync": value["auto_sync"], "folder_set": bool(value["folder"])})
    return value


def _year(flight_row: sqlite3.Row | None, raw_row: sqlite3.Row) -> str:
    for value in ((flight_row["start_time_utc"] if flight_row is not None else None),
                  (flight_row["filename_time"] if flight_row is not None else None)):
        if value and len(str(value)) >= 4 and str(value)[:4].isdigit():
            return str(value)[:4]
    name = str(raw_row["original_name"] or "")
    for index in (0, len(name) - 20):
        candidate = name[max(0, index):max(0, index) + 4]
        if candidate.isdigit() and candidate[:2] in ("20", "19"):
            return candidate
    return "unknown"


def select(conn: sqlite3.Connection, *, variants: bool) -> list[dict[str, Any]]:
    """Choose which raw files to export.

    Default: one file per flight — its `primary` link, or failing that the
    largest variant, which is the one most likely to carry full telemetry.
    Variants: every raw file, including ones never linked to a flight.
    """
    raw = {int(r["id"]): r for r in db.all_rows(conn, "SELECT * FROM raw_files")}
    if variants:
        chosen = [{"raw": r, "flight": None} for r in raw.values()]
    else:
        links = db.all_rows(
            conn,
            """SELECT ff.flight_id AS flight_id, ff.raw_file_id AS raw_id, ff.role AS role
                 FROM flight_files ff ORDER BY ff.rowid""",
        )
        flights = {int(f["id"]): f for f in
                   db.all_rows(conn, "SELECT id,start_time_utc,filename_time FROM flights")}
        by_flight: dict[int, list[sqlite3.Row]] = {}
        for link in links:
            by_flight.setdefault(int(link["flight_id"]), []).append(link)
        chosen = []
        for flight_id, group in sorted(by_flight.items()):
            best = max(
                group,
                key=lambda link: (
                    1 if str(link["role"]) == "primary" else 0,
                    int(raw[int(link["raw_id"])]["size_bytes"] or 0),
                    -int(link["raw_id"]),
                ),
            )
            raw_id = int(best["raw_id"])
            if raw_id in raw:
                chosen.append({"raw": raw[raw_id], "flight": flights.get(flight_id)})

    out: list[dict[str, Any]] = []
    seen_sha: set[str] = set()
    for item in chosen:
        row = item["raw"]
        sha = str(row["sha256"])
        if sha in seen_sha:
            continue
        seen_sha.add(sha)
        out.append({
            "sha256": sha,
            "name": str(row["original_name"] or f"{sha[:12]}.txt"),
            "size": int(row["size_bytes"] or 0),
            "stored_rel": str(row["stored_rel"]),
            "year": _year(item["flight"], row),
        })
    return out


def _safe_name(name: str) -> str:
    core = Path(str(name or "")).name.strip() or "flight.txt"
    # The archive accepts names with brackets; Windows does too, but a colon or
    # a null byte would make the export unusable, so they are replaced.
    for bad in ('<', '>', ':', '"', '/', '\\', '|', '?', '*', '\x00'):
        core = core.replace(bad, "_")
    if not core.lower().endswith(".txt"):
        core += ".txt"
    return core


def _load_manifest(dest: Path) -> dict[str, Any]:
    path = dest / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": MANIFEST_VERSION, "files": {}}


def _save_manifest(dest: Path, manifest: dict[str, Any]) -> None:
    path = dest / MANIFEST_NAME
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _claimed(manifest: dict[str, Any]) -> dict[str, str]:
    """Exported relative name -> sha that owns it."""
    out: dict[str, str] = {}
    for sha, entry in manifest.get("files", {}).items():
        name = entry.get("path") if isinstance(entry, dict) else None
        if name:
            out[str(name).lower()] = str(sha)
    return out


def plan(conn: sqlite3.Connection, dest: Path, *, variants: bool,
         group_by_year: bool) -> list[dict[str, Any]]:
    dest = Path(dest)
    manifest = _load_manifest(dest)
    owners = _claimed(manifest)
    items = select(conn, variants=variants)
    out: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for item in items:
        base = _safe_name(item["name"])
        folder = item["year"] if group_by_year else ""
        rel = f"{folder}/{base}" if folder else base
        owner = owners.get(rel.lower())
        if owner is not None and owner != item["sha256"]:
            rel = _suffixed(folder, base, item["sha256"])
        elif rel.lower() in assigned:
            rel = _suffixed(folder, base, item["sha256"])
        # A file the owner already put there is not ours to replace.
        target = dest / rel
        if target.exists() and owners.get(rel.lower()) != item["sha256"]:
            rel = _suffixed(folder, base, item["sha256"])
        assigned.add(rel.lower())
        out.append({**item, "path": rel})
    return out


def _suffixed(folder: str, base: str, sha: str) -> str:
    stem = base[:-4] if base.lower().endswith(".txt") else base
    name = f"{stem}__{sha[:8]}.txt"
    return f"{folder}/{name}" if folder else name


def run(conn: sqlite3.Connection, root: Path, dest: Path, *, variants: bool,
        group_by_year: bool,
        progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Copy the selection into `dest`. Additive only; returns a report."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(dest)
    items = plan(conn, dest, variants=variants, group_by_year=group_by_year)
    report: dict[str, Any] = {"copied": 0, "skipped": 0, "problems": [],
                              "total": len(items), "folder": str(dest)}
    total = len(items)
    for index, item in enumerate(items, start=1):
        sha = item["sha256"]
        rel = item["path"]
        target = dest / rel
        entry = manifest["files"].get(sha)
        try:
            if (isinstance(entry, dict) and entry.get("path") == rel
                    and target.exists() and target.stat().st_size == item["size"]):
                report["skipped"] += 1
                if progress:
                    progress(index, total)
                continue
            source = Path(root) / "archive" / item["stored_rel"]
            if not source.exists():
                report["problems"].append({"sha256": sha[:12], "code": "source_missing"})
                if progress:
                    progress(index, total)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            shutil.copyfile(source, part)
            digest = _hash_file(part)
            if digest != sha:
                part.unlink(missing_ok=True)
                report["problems"].append({"sha256": sha[:12], "code": "hash_mismatch"})
                if progress:
                    progress(index, total)
                continue
            os.replace(part, target)
            manifest["files"][sha] = {"path": rel, "size": item["size"]}
            report["copied"] += 1
        except OSError as exc:
            report["problems"].append({"sha256": sha[:12], "code": type(exc).__name__})
            errlog.record(root, "raw_export_copy", exc)
        if progress:
            progress(index, total)
    manifest["version"] = MANIFEST_VERSION
    try:
        _save_manifest(dest, manifest)
    except OSError as exc:
        report["problems"].append({"sha256": "-", "code": "manifest_write_failed"})
        errlog.record(root, "raw_export_manifest", exc)
    return report


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Job:
    """One export at a time, in a background thread, with a small status.

    A first export of a few hundred files must not freeze the page, and the
    page still needs to say what is happening.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle", "done": 0, "total": 0,
                                       "copied": 0, "skipped": 0, "problems": [],
                                       "at": None, "auto": False}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, db_file: Path, root: Path, dest: Path, *, variants: bool,
              group_by_year: bool, auto: bool = False) -> bool:
        if self.running():
            return False
        with self._lock:
            self._state = {"state": "running", "done": 0, "total": 0, "copied": 0,
                           "skipped": 0, "problems": [], "at": db.now_utc(),
                           "auto": auto}

        def progress(done: int, total: int) -> None:
            with self._lock:
                self._state["done"] = done
                self._state["total"] = total

        def body() -> None:
            conn = None
            try:
                conn = db.connect(db_file)
                report = run(conn, root, dest, variants=variants,
                             group_by_year=group_by_year, progress=progress)
                with self._lock:
                    self._state.update({
                        "state": "done", "copied": report["copied"],
                        "skipped": report["skipped"], "total": report["total"],
                        "problems": report["problems"], "at": db.now_utc(),
                    })
            except Exception as exc:  # noqa: BLE001
                category = errlog.record(root, "raw_export", exc)
                with self._lock:
                    self._state.update({"state": "error", "category": category,
                                        "at": db.now_utc()})
            finally:
                if conn is not None:
                    conn.close()

        self._thread = threading.Thread(target=body, name="fd-raw-export", daemon=True)
        self._thread.start()
        return True
