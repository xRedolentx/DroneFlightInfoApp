"""Immutable, content-addressed raw archive plus safe ZIP member handling."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator

CHUNK = 1024 * 1024
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024 * 1024 * 1024
FLIGHT_NAME_RE = re.compile(r"^(D?JI)?FlightRecord_.*(\.txt)?$", re.IGNORECASE)
METADATA_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


class ArchiveError(Exception):
    category = "archive_error"


class ZipUnsafe(ArchiveError):
    category = "zip_unsafe"


class TooLarge(ArchiveError):
    category = "too_large"


class DiskFull(ArchiveError):
    category = "disk_full"


@dataclass(frozen=True)
class Stored:
    sha256: str
    size: int
    rel: str


def _is_metadata(part: str) -> bool:
    return part in METADATA_NAMES or part.startswith("._") or part.startswith(".")


def is_flight_filename(name: str) -> bool:
    base = PurePosixPath(name.replace("\\", "/")).name
    return bool(FLIGHT_NAME_RE.match(base)) and not _is_metadata(base)


def normalize_member_path(name: str) -> str:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ZipUnsafe("absolute member path")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ZipUnsafe("escaping member path")
    if not parts:
        raise ZipUnsafe("empty member path")
    return "/".join(parts)


def iter_zip_members(
    zf: zipfile.ZipFile,
    *,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total: int = MAX_TOTAL_UNCOMPRESSED,
    max_members: int = MAX_ARCHIVE_MEMBERS,
) -> Iterator[tuple[str, zipfile.ZipInfo]]:
    """Yield safe, eligible, non-directory members. Metadata is skipped."""
    total = 0
    seen: set[str] = set()
    count = 0
    for info in zf.infolist():
        count += 1
        if count > max_members:
            raise TooLarge("too many zip members")
        if info.is_dir():
            continue
        try:
            norm = normalize_member_path(info.filename)
        except ZipUnsafe:
            raise
        if any(_is_metadata(p) for p in norm.split("/")):
            continue
        if norm in seen:
            raise ZipUnsafe("duplicate normalized member")
        seen.add(norm)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ZipUnsafe("symlink member")
        if info.flag_bits & 0x1:
            raise ZipUnsafe("encrypted zip member")
        if info.file_size > max_member_bytes:
            raise TooLarge("member exceeds size bound")
        total += info.file_size
        if total > max_total:
            raise TooLarge("archive exceeds size bound")
        if not is_flight_filename(norm):
            continue
        yield norm, info


def classify_member(norm: str) -> str:
    lower = norm.lower()
    if "controller-test" in lower or "controller_test" in lower or "/controller" in lower:
        return "extras"
    if "flight_logs" in lower or "flightlogs" in lower:
        return "archive"
    return "archive"


def store_stream(src: BinaryIO, root: Path, *, max_bytes: int = MAX_MEMBER_BYTES) -> Stored:
    """Persist src under the archive. Hash is computed while writing; the
    destination is content-addressed and moved into place atomically."""
    root = Path(root)
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"ingest-{uuid.uuid4().hex}.part"
    h = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                try:
                    chunk = src.read(CHUNK)
                except OSError as exc:  # source read failure
                    raise ArchiveError("source read failed") from exc
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge("file exceeds size bound")
                h.update(chunk)
                try:
                    out.write(chunk)
                except OSError as exc:
                    if getattr(exc, "errno", None) == 28:
                        raise DiskFull("no space left while writing") from exc
                    raise
            out.flush()
            os.fsync(out.fileno())
        digest = h.hexdigest()
        rel = f"{digest[:2]}/{digest[2:4]}/{digest}.bin"
        dest = root / "archive" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.stat().st_size != size:
                raise ArchiveError("existing archive object has different size")
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, dest)
        return Stored(sha256=digest, size=size, rel=rel)
    except TooLarge:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if getattr(exc, "errno", None) == 28:
            raise DiskFull("no space left") from exc
        raise
    finally:
        tmp.unlink(missing_ok=True)


def store_file(path: Path, root: Path, *, max_bytes: int = MAX_MEMBER_BYTES) -> Stored:
    with open(path, "rb") as fh:
        return store_stream(fh, root, max_bytes=max_bytes)


def archive_path(root: Path, rel: str) -> Path:
    p = (Path(root) / "archive" / rel).resolve()
    base = (Path(root) / "archive").resolve()
    if base not in p.parents and p != base:
        raise ArchiveError("archive path escapes root")
    return p


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_object(root: Path, rel: str, sha256: str) -> bool:
    p = archive_path(root, rel)
    if not p.exists():
        return False
    return hash_file(p) == sha256


def iter_folder(path: Path) -> Iterator[Path]:
    """Deterministic folder walk; never follows symlinks, skips metadata."""
    path = Path(path)
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if not _is_metadata(d) and not (Path(dirpath) / d).is_symlink()
        )
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() or _is_metadata(name):
                continue
            yield p
