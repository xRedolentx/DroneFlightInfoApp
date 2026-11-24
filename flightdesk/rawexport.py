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
    content hash to the name it was written under. A hash already exported is
    skipped only after the destination file has been hashed and matched; the
    manifest alone never justifies a skip.
  * **Verify after copy.** The bytes are hashed again from the destination; a
    mismatch is reported, not silently left behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import db, errlog

MANIFEST_NAME = "flightdesk-export.json"
MANIFEST_VERSION = 1
# Names Windows reserves regardless of extension. A file called `con.txt` cannot
# be created there, so an export must not write one.
_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{stem}{n}" for stem in ("com", "lpt") for n in range(1, 10)
}
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
    stem = core[:-4].rstrip(" .") or "flight"
    if stem.lower() in _RESERVED_NAMES:
        stem = "_" + stem
    return stem + ".txt"


def _safe_rel(rel: object) -> str | None:
    """Accept only a plain relative path inside the destination."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    raw = rel.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _load_manifest(dest: Path) -> dict[str, Any]:
    """Read the manifest, keeping only well-formed entries.

    The manifest records what this program wrote; it is not permission to
    overwrite whatever is on disk now. Malformed or escaping entries are
    dropped, so their files are treated as someone else's.
    """
    path = dest / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": MANIFEST_VERSION, "files": {}}
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"version": MANIFEST_VERSION, "files": {}}
    files: dict[str, dict[str, Any]] = {}
    for sha, entry in data["files"].items():
        if not isinstance(sha, str) or len(sha) < 8 or not isinstance(entry, dict):
            continue
        rel = _safe_rel(entry.get("path"))
        if rel is None:
            continue
        size = entry.get("size")
        files[sha] = {"path": rel, "size": size if isinstance(size, int) else None}
    version = data.get("version")
    return {"version": version if isinstance(version, int) else MANIFEST_VERSION,
            "files": files}


def _save_manifest(dest: Path, manifest: dict[str, Any]) -> None:
    path = dest / MANIFEST_NAME
    fd, name = tempfile.mkstemp(prefix=f".{MANIFEST_NAME}.", suffix=".part",
                                dir=str(dest))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=1, sort_keys=True))
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _claimed(manifest: dict[str, Any]) -> dict[str, str]:
    """Exported relative name -> sha that owns it."""
    out: dict[str, str] = {}
    for sha, entry in manifest.get("files", {}).items():
        name = entry.get("path") if isinstance(entry, dict) else None
        if name:
            out[str(name).lower()] = str(sha)
    return out


def _sha_claim(manifest: dict[str, Any], sha: str) -> str | None:
    entry = manifest.get("files", {}).get(sha)
    if isinstance(entry, dict):
        rel = entry.get("path")
        if isinstance(rel, str):
            return rel
    return None


def _inside(dest: Path, target: Path) -> bool:
    """Does target resolve to somewhere inside dest? Follows junctions/symlinks."""
    try:
        base = dest.resolve()
        resolved = target.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def _folder_of(rel: str) -> str:
    parent = str(PurePosixPath(rel).parent)
    return "" if parent == "." else parent


def _alloc_rel(dest: Path, folder: str, base: str, sha: str,
               taken: set[str]) -> str | None:
    """Pick a destination name that is free, contained, and not already taken.

    The base name is tried first, then the hash-suffixed form, then numbered
    suffixes. Nothing already on disk is ever chosen, so an owner's file and an
    occupied suffixed name are both preserved.
    """
    stem = base[:-4] if base.lower().endswith(".txt") else base
    names = [base, f"{stem}__{sha[:8]}.txt"]
    names.extend(f"{stem}__{sha[:8]}-{n}.txt" for n in range(2, 100))
    for name in names:
        rel = f"{folder}/{name}" if folder else name
        if rel.lower() in taken:
            continue
        target = dest / rel
        if target.exists() or not _inside(dest, target):
            continue
        return rel
    for _ in range(8):
        name = f"{stem}__{sha[:8]}-{uuid.uuid4().hex[:8]}.txt"
        rel = f"{folder}/{name}" if folder else name
        if rel.lower() in taken:
            continue
        target = dest / rel
        if not target.exists() and _inside(dest, target):
            return rel
    return None


def _reusable_rel(dest: Path, manifest: dict[str, Any], sha: str, folder: str,
                  base: str, taken: set[str]) -> str | None:
    """The manifest's name for this sha, if it still fits and still exists."""
    claim = _sha_claim(manifest, sha)
    if not claim or claim.lower() in taken:
        return None
    if _folder_of(claim) != folder:
        return None
    if PurePosixPath(claim).name.lower() != base.lower():
        return None
    target = dest / claim
    if not target.exists() or not _inside(dest, target):
        return None
    return claim


def _publish_no_clobber(tmp: Path, target: Path) -> None:
    """Move tmp into place, raising FileExistsError instead of replacing.

    `os.replace` overwrites, which is exactly what an additive export must not
    do: a file that appeared between the existence check and this move would be
    lost. Windows `os.rename` refuses an occupied target; POSIX needs a hard
    link, which fails the same way and is atomic.
    """
    if os.name == "nt":
        os.rename(tmp, target)
        return
    os.link(tmp, target)
    tmp.unlink(missing_ok=True)


def plan(conn: sqlite3.Connection, dest: Path, *, variants: bool,
         group_by_year: bool) -> list[dict[str, Any]]:
    """Advisory preview of names an export would write. No bytes are verified."""
    dest = Path(dest)
    manifest = _load_manifest(dest)
    items = select(conn, variants=variants)
    out: list[dict[str, Any]] = []
    taken: set[str] = set()
    for item in items:
        base = _safe_name(item["name"])
        folder = item["year"] if group_by_year else ""
        rel = _reusable_rel(dest, manifest, item["sha256"], folder, base, taken)
        if rel is None:
            rel = _alloc_rel(dest, folder, base, item["sha256"], taken)
        if rel is None:
            rel = f"{folder}/{base}" if folder else base
        taken.add(rel.lower())
        out.append({**item, "path": rel})
    return out


def _export_one(root: Path, dest: Path, item: dict[str, Any],
                manifest: dict[str, Any], taken: set[str],
                report: dict[str, Any], group_by_year: bool) -> None:
    sha = item["sha256"]
    base = _safe_name(item["name"])
    folder = item["year"] if group_by_year else ""
    rel = _reusable_rel(dest, manifest, sha, folder, base, taken)
    if rel is None:
        rel = _alloc_rel(dest, folder, base, sha, taken)
    if rel is None:
        report["problems"].append({"sha256": sha[:12], "code": "no_free_name"})
        return
    target = dest / rel
    if target.exists():
        if _hash_file(target) == sha:
            taken.add(rel.lower())
            report["skipped"] += 1
            report["verified"] += 1
            return
        # The bytes are not what this name claims. Keep the file and write a
        # verified copy under a fresh name; never replace it.
        report["preserved"] += 1
        report["conflicts"].append({"sha256": sha[:12], "path": rel})
        replacement = _alloc_rel(dest, folder, base, sha, taken)
        if replacement is None:
            report["problems"].append({"sha256": sha[:12], "code": "no_free_name"})
            return
        rel = replacement
        target = dest / rel
    taken.add(rel.lower())
    source = Path(root) / "archive" / item["stored_rel"]
    if not source.exists():
        report["problems"].append({"sha256": sha[:12], "code": "source_missing"})
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part",
                                    dir=str(target.parent))
    os.close(fd)
    temp = Path(temp_name)
    published = False
    try:
        shutil.copyfile(source, temp)
        if _hash_file(temp) != sha:
            report["problems"].append({"sha256": sha[:12], "code": "hash_mismatch"})
            return
        for _ in range(8):
            try:
                _publish_no_clobber(temp, target)
                published = True
                break
            except FileExistsError:
                # Something appeared at the target after the existence check.
                report["preserved"] += 1
                report["conflicts"].append({"sha256": sha[:12], "path": rel})
                replacement = _alloc_rel(dest, folder, base, sha, taken)
                if replacement is None:
                    report["problems"].append(
                        {"sha256": sha[:12], "code": "no_free_name"})
                    return
                rel = replacement
                target = dest / rel
                taken.add(rel.lower())
        if not published:
            report["problems"].append({"sha256": sha[:12], "code": "target_occupied"})
            return
        manifest["files"][sha] = {"path": rel, "size": item["size"]}
        report["copied"] += 1
    finally:
        temp.unlink(missing_ok=True)


def run(conn: sqlite3.Connection, root: Path, dest: Path, *, variants: bool,
        group_by_year: bool,
        progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Copy the selection into `dest`. Additive only; returns a report.

    `skipped` means the destination was hashed and matched, not merely sized.
    A destination file whose bytes differ is counted in `preserved` and
    `conflicts`, and a new copy is written beside it.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(dest)
    items = select(conn, variants=variants)
    report: dict[str, Any] = {"copied": 0, "skipped": 0, "verified": 0,
                              "preserved": 0, "conflicts": [], "problems": [],
                              "total": len(items), "folder": str(dest)}
    taken: set[str] = set()
    total = len(items)
    for index, item in enumerate(items, start=1):
        try:
            _export_one(root, dest, item, manifest, taken, report, group_by_year)
        except OSError as exc:
            report["problems"].append({"sha256": item["sha256"][:12],
                                       "code": type(exc).__name__})
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

        # The running check, the state reset and the thread start share one
        # lock: two requests arriving together used to both pass the check and
        # both start a job, and the second silently replaced the first's state.
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._state = {"state": "running", "done": 0, "total": 0, "copied": 0,
                           "skipped": 0, "problems": [], "at": db.now_utc(),
                           "auto": auto}
            thread = threading.Thread(target=body, name="fd-raw-export", daemon=True)
            self._thread = thread
            thread.start()
        return True
