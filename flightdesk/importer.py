"""Offline import: archive first, then index, then derive one logical flight.

Identity rules:
  * SHA-256 identifies an exact file.
  * A logical flight is merged only on strong corroboration (identical
    fingerprint, or same aircraft + start within 3 s + duration within 1 s +
    distance within 1 %/5 m).
  * A weak same-time match is retained but marked unresolved and excluded
    from authoritative totals.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from . import archive, db
from .analysis import compute_metrics, downsample
from .archive import DiskFull, Stored, ZipUnsafe
from .parser_adapter import ParseOutcome, detect_version, parse_file

FILE_TIME_PATTERNS = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})_\[(\d{2})-(\d{2})-(\d{2})\]"),
    re.compile(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})"),
]
OUTCOME_LABELS = {
    "imported": "new",
    "duplicate": "already_imported",
    "pending_key": "awaiting_dji",
    "unsupported": "unsupported",
    "damaged": "needs_attention",
    "needs_attention": "needs_attention",
}
TOTAL_KEYS = ("discovered", "new", "already_imported", "awaiting_dji",
              "unsupported", "needs_attention", "new_flight")


@dataclass
class BatchResult:
    batch_id: str
    totals: dict[str, Any] = field(
        default_factory=lambda: {key: 0 for key in TOTAL_KEYS}
    )
    items: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "totals": self.totals,
            "items": self.items,
            "note": self.note,
        }


def parse_filename_time(name: str) -> str | None:
    for pat in FILE_TIME_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        try:
            dt = datetime(*(int(x) for x in m.groups()), tzinfo=None)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fingerprint(aircraft_sn: str | None, start: str | None, duration: float | None) -> str:
    raw = f"{aircraft_sn or ''}|{start or ''}|{round(duration or 0, 1)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _metrics_source(outcome: ParseOutcome) -> str:
    if outcome.samples:
        return "frames"
    if outcome.meta.get("keychain_decode"):
        return "keychain_details"
    if outcome.status == "pending_key":
        return "metadata_only"
    return "details"


def _status_for(outcome: ParseOutcome) -> str:
    return {
        "decoded": "imported",
        "pending_key": "pending_key",
        "unsupported": "unsupported",
        "damaged": "damaged",
        "needs_attention": "needs_attention",
    }.get(outcome.status, "needs_attention")


def resolve_equipment(
    conn: sqlite3.Connection, kind: str, serial: str | None, name: str | None
) -> int | None:
    """Link a decoded serial to existing canonical equipment. Never invents
    equipment and never overrides a manual link."""
    if not serial:
        return None
    link = db.one(conn, "SELECT * FROM serial_links WHERE serial=?", (serial,))
    if link is not None:
        return int(link["equipment_id"])
    row = db.one(
        conn,
        "SELECT * FROM equipment WHERE kind=? AND archived=0 AND "
        "(serial=? OR name=? OR alias=?) ORDER BY (serial=?) DESC, id LIMIT 1",
        (kind, serial, name, name, serial),
    )
    if row is None:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO serial_links(serial,equipment_id,source,override,updated_at) "
        "VALUES(?,?,?,0,?)",
        (serial, row["id"], "log", db.now_utc()),
    )
    return int(row["id"])


def _store_telemetry(conn, flight_id: int, samples, metrics, source: str) -> None:
    if not samples:
        return
    kept = downsample(samples, 1400)
    payload = json.dumps(kept, separators=(",", ":")).encode("utf-8")
    blob = gzip.compress(payload, 6)
    existing = db.one(
        conn, "SELECT source,sample_count FROM flight_telemetry WHERE flight_id=?", (flight_id,)
    )
    if existing is not None:
        if source == "parser" and int(existing["sample_count"]) >= len(kept):
            return
        if source != "parser" and existing["source"] == "parser":
            return
    conn.execute(
        "INSERT INTO flight_telemetry(flight_id,source,sample_count,samples_blob,gaps_json,created_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(flight_id) DO UPDATE SET source=excluded.source,"
        "sample_count=excluded.sample_count,samples_blob=excluded.samples_blob,"
        "gaps_json=excluded.gaps_json,created_at=excluded.created_at",
        (
            flight_id,
            source,
            len(kept),
            blob,
            json.dumps(metrics.gaps, separators=(",", ":")),
            db.now_utc(),
        ),
    )


def _flight_values(
    outcome: ParseOutcome,
    metrics,
    details: dict,
    *,
    start_utc: str | None,
    filename_time: str | None,
    fp: str,
    aircraft_equipment_id: int | None,
    battery_equipment_id: int | None,
    identity: str,
    excluded_reason: str | None,
) -> tuple:
    return (
        identity,
        details.get("aircraft_sn"),
        details.get("aircraft_name"),
        details.get("battery_sn"),
        details.get("camera_sn"),
        details.get("rc_sn"),
        start_utc,
        None,
        "second" if details.get("start_time_utc") else (
            "filename_second" if filename_time else "unknown"
        ),
        filename_time,
        metrics.duration_s,
        metrics.recorder_duration_s,
        metrics.airborne_time_s,
        metrics.total_distance_m,
        metrics.path_length_m,
        metrics.max_home_distance_m,
        metrics.max_height_m,
        metrics.max_altitude_m,
        metrics.max_h_speed_mps,
        metrics.home_lat,
        metrics.home_lon,
        metrics.home_alt_m,
        metrics.battery_start_pct,
        metrics.battery_end_pct,
        metrics.battery_temp_max_c,
        metrics.battery_temp_min_c,
        metrics.signal_min,
        metrics.sample_count,
        metrics.gap_count,
        _metrics_source(outcome),
        outcome.parser_version,
        fp,
        aircraft_equipment_id,
        battery_equipment_id,
        excluded_reason,
        "[]",
    )


FLIGHT_INSERT = """INSERT INTO flights(
    identity_status,aircraft_sn,aircraft_name,battery_sn,camera_sn,rc_sn,
    start_time_utc,start_time_offset_min,start_time_precision,filename_time,
    duration_s,recorder_duration_s,airborne_time_s,total_distance_m,path_length_m,
    max_home_distance_m,max_height_m,max_altitude_m,max_h_speed_mps,
    home_lat,home_lon,home_alt_m,battery_start_pct,battery_end_pct,
    battery_temp_max_c,battery_temp_min_c,signal_min,
    sample_count,gap_count,metrics_source,parser_version,
    fingerprint,aircraft_equipment_id,battery_equipment_id,excluded_reason,
    tags_json,created_at,updated_at
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) """


def _update_flight_metrics(
    conn: sqlite3.Connection,
    flight_id: int,
    details: dict,
    metrics,
    outcome: ParseOutcome,
    start_utc: str | None,
    filename_time: str | None,
) -> None:
    conn.execute(
        """UPDATE flights SET aircraft_sn=COALESCE(?,aircraft_sn),
            aircraft_name=COALESCE(?,aircraft_name), battery_sn=COALESCE(?,battery_sn),
            start_time_utc=COALESCE(?,start_time_utc),
            start_time_precision=CASE WHEN ? IS NOT NULL THEN 'second' ELSE start_time_precision END,
            filename_time=COALESCE(filename_time,?),
            duration_s=?, recorder_duration_s=?, airborne_time_s=?, total_distance_m=?,
            path_length_m=?, max_home_distance_m=?, max_height_m=?, max_altitude_m=?,
            max_h_speed_mps=?, home_lat=?, home_lon=?, home_alt_m=?,
            battery_start_pct=?, battery_end_pct=?, battery_temp_max_c=?,
            battery_temp_min_c=?, signal_min=?,
            sample_count=?, gap_count=?, metrics_source=?, parser_version=?,
            aircraft_equipment_id=COALESCE(aircraft_equipment_id,?),
            battery_equipment_id=COALESCE(battery_equipment_id,?), updated_at=?
        WHERE id=?""",
        (
            details.get("aircraft_sn"),
            details.get("aircraft_name"),
            details.get("battery_sn"),
            start_utc,
            details.get("start_time_utc"),
            filename_time,
            metrics.duration_s,
            metrics.recorder_duration_s,
            metrics.airborne_time_s,
            metrics.total_distance_m,
            metrics.path_length_m,
            metrics.max_home_distance_m,
            metrics.max_height_m,
            metrics.max_altitude_m,
            metrics.max_h_speed_mps,
            metrics.home_lat,
            metrics.home_lon,
            metrics.home_alt_m,
            metrics.battery_start_pct,
            metrics.battery_end_pct,
            metrics.battery_temp_max_c,
            metrics.battery_temp_min_c,
            metrics.signal_min,
            metrics.sample_count,
            metrics.gap_count,
            _metrics_source(outcome),
            outcome.parser_version,
            resolve_equipment(conn, "aircraft", details.get("aircraft_sn"), details.get("aircraft_name")),
            resolve_equipment(conn, "battery", details.get("battery_sn"), None),
            db.now_utc(),
            flight_id,
        ),
    )
    if outcome.samples:
        _store_telemetry(conn, flight_id, outcome.samples, metrics, source="parser")


def _insert_or_link_flight(
    conn: sqlite3.Connection,
    raw_id: int,
    outcome: ParseOutcome,
    *,
    filename: str,
    source_kind: str,
    member_path: str,
) -> tuple[int, bool, str]:
    details = outcome.details or {}
    metrics = compute_metrics(outcome.samples, details)
    start_utc = details.get("start_time_utc")
    filename_time = parse_filename_time(filename)
    fp = _fingerprint(details.get("aircraft_sn"), start_utc, metrics.duration_s)
    aircraft_sn = details.get("aircraft_sn")
    aircraft_name = details.get("aircraft_name")
    battery_sn = details.get("battery_sn")
    now = db.now_utc()

    candidate = db.one(conn, "SELECT * FROM flights WHERE fingerprint=? LIMIT 1", (fp,))
    match = "fingerprint" if candidate is not None else "new"
    # A raw file that is already linked keeps its flight. Otherwise a metrics
    # correction that changes the fingerprint would make a reparse look like a
    # brand new flight and duplicate the record.
    if candidate is None:
        linked_flight = db.one(
            conn, "SELECT flight_id FROM flight_files WHERE raw_file_id=? LIMIT 1", (raw_id,)
        )
        if linked_flight is not None:
            candidate = db.one(conn, "SELECT * FROM flights WHERE id=?",
                               (int(linked_flight["flight_id"]),))
            if candidate is not None:
                match = "existing"
    if candidate is None and (aircraft_sn or aircraft_name):
        rows = db.all_rows(
            conn,
            "SELECT * FROM flights WHERE identity_status!='unresolved' AND "
            "((aircraft_sn IS NOT NULL AND aircraft_sn=?) OR "
            " (aircraft_sn IS NULL AND ? IS NOT NULL AND aircraft_name=?)) "
            "ORDER BY ABS(julianday(start_time_utc)-julianday(?)) LIMIT 6",
            (aircraft_sn, aircraft_name, aircraft_name, start_utc or "1970-01-01T00:00:00+00:00"),
        )
        for row in rows:
            a = _parse_iso(row["start_time_utc"])
            b = _parse_iso(start_utc)
            if a is None or b is None:
                continue
            if abs((a - b).total_seconds()) > 3.0:
                continue
            strong = (
                (
                    row["duration_s"] is None
                    or metrics.duration_s is None
                    or abs(float(row["duration_s"]) - float(metrics.duration_s)) <= 1.0
                )
                and (
                    row["total_distance_m"] is None
                    or metrics.total_distance_m is None
                    or abs(float(row["total_distance_m"]) - float(metrics.total_distance_m))
                    <= max(
                        5.0,
                        0.01
                        * max(float(row["total_distance_m"]), float(metrics.total_distance_m)),
                    )
                )
            )
            if strong:
                candidate = row
                match = "strong"
                break
            if candidate is None:
                candidate = row
                match = "weak"

    if candidate is not None and match in ("fingerprint", "strong", "existing"):
        flight_id = int(candidate["id"])
        conn.execute(
            "INSERT OR IGNORE INTO flight_files(flight_id,raw_file_id,role,evidence_json,linked_at) "
            "VALUES(?,?,?,?,?)",
            (
                flight_id,
                raw_id,
                "variant",
                json.dumps({"match": match, "source_kind": source_kind}),
                now,
            ),
        )
        stored_samples = int(candidate["sample_count"] or 0)
        if outcome.samples and len(outcome.samples) < stored_samples:
            # A poorer parse must never replace a richer one.
            return flight_id, False, match
        if (not outcome.samples) and stored_samples > 0:
            return flight_id, False, match
        if (not outcome.samples) and not outcome.details:
            return flight_id, False, match
        # Equal weight is enough to refresh: this is the path a reparse takes
        # when a parser improves, and it must backfill the same logical flight
        # instead of keeping figures computed by the old parser.
        _update_flight_metrics(conn, flight_id, details, metrics, outcome, start_utc, filename_time)
        return flight_id, False, match

    identity = "authoritative"
    excluded_reason = None
    if match == "weak":
        identity = "unresolved"
        excluded_reason = "same_time_weak_match"
    if not start_utc and not filename_time:
        identity = "unresolved"
        excluded_reason = "no_start_time"

    aircraft_equipment_id = resolve_equipment(conn, "aircraft", aircraft_sn, aircraft_name)
    battery_equipment_id = resolve_equipment(conn, "battery", battery_sn, None)
    cur = conn.execute(
        FLIGHT_INSERT,
        _flight_values(
            outcome,
            metrics,
            details,
            start_utc=start_utc,
            filename_time=filename_time,
            fp=fp,
            aircraft_equipment_id=aircraft_equipment_id,
            battery_equipment_id=battery_equipment_id,
            identity=identity,
            excluded_reason=excluded_reason,
        )
        + (now, now),
    )
    flight_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO flight_files(flight_id,raw_file_id,role,evidence_json,linked_at) VALUES(?,?,?,?,?)",
        (
            flight_id,
            raw_id,
            "primary",
            json.dumps({"match": match, "source_kind": source_kind, "member": member_path}),
            now,
        ),
    )
    _store_telemetry(conn, flight_id, outcome.samples, metrics, source="parser")
    if battery_equipment_id and metrics.battery_start_pct is not None:
        conn.execute(
            "INSERT OR IGNORE INTO battery_readings"
            "(battery_id,flight_id,at,charge_pct,temp_c,cycles,voltage,source,temp_missing) "
            "VALUES(?,?,?,?,?,?,?,?,0)",
            (
                battery_equipment_id,
                flight_id,
                start_utc,
                metrics.battery_start_pct,
                metrics.battery_temp_max_c,
                None,
                None,
                "log",
            ),
        )
    return flight_id, True, match


def _cached_keychains(conn: sqlite3.Connection, root: Path, sha256: str) -> list | None:
    try:
        from .keychain import cached_json
        return cached_json(conn, root, sha256)
    except Exception:
        return None


class Importer:
    def __init__(self, root: Path, conn: sqlite3.Connection):
        self.root = Path(root)
        self.conn = conn

    # ---------------------------------------------------------------- batches
    def begin_batch(self, source_label: str, source_kind: str,
                    batch_id: str | None = None) -> str:
        batch_id = batch_id or uuid.uuid4().hex
        with db.tx(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO import_batches(id,started_at,source_label,source_kind,status) "
                "VALUES(?,?,?,?, 'running')",
                (batch_id, db.now_utc(), source_label, source_kind),
            )
        return batch_id

    def finish_batch(self, result: BatchResult) -> None:
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE import_batches SET finished_at=?, status=?, totals_json=?, note=? WHERE id=?",
                (
                    db.now_utc(),
                    "done" if not result.note else "attention",
                    json.dumps(result.totals, separators=(",", ":")),
                    result.note,
                    result.batch_id,
                ),
            )

    def _item(
        self,
        result: BatchResult,
        raw_id: int | None,
        outcome: str,
        code: str | None = None,
        detail: str | None = None,
        *,
        flight_id: int | None = None,
        new_flight: bool = False,
    ) -> None:
        key = OUTCOME_LABELS.get(outcome, "needs_attention")
        result.totals["discovered"] = result.totals.get("discovered", 0) + 1
        result.totals[key] = result.totals.get(key, 0) + 1
        if new_flight:
            result.totals["new_flight"] = result.totals.get("new_flight", 0) + 1
        self.conn.execute(
            "INSERT INTO import_items(batch_id,raw_file_id,outcome,code,detail,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (result.batch_id, raw_id, outcome, code, (detail or "")[:500], db.now_utc()),
        )
        result.items.append(
            {"outcome": outcome, "code": code, "raw_id": raw_id, "flight_id": flight_id}
        )

    # ---------------------------------------------------------------- ingest
    def _ingest(
        self,
        src: BinaryIO,
        filename: str,
        *,
        source_kind: str,
        source_label: str,
        member_path: str,
        result: BatchResult,
    ) -> None:
        try:
            stored = archive.store_stream(src, self.root)
        except DiskFull:
            self._item(result, None, "needs_attention", "disk_full",
                       "no space for the original; nothing was indexed")
            result.note = "disk_full"
            return
        except (archive.ArchiveError, OSError) as exc:
            self._item(result, None, "needs_attention",
                       getattr(exc, "category", "io_error"), type(exc).__name__)
            result.note = "archive_failed"
            return
        self._index_stored(stored, filename, source_kind, source_label, member_path, result)

    def _index_stored(
        self,
        stored: Stored,
        filename: str,
        source_kind: str,
        source_label: str,
        member_path: str,
        result: BatchResult,
    ) -> None:
        now = db.now_utc()
        existing = db.one(self.conn, "SELECT * FROM raw_files WHERE sha256=?", (stored.sha256,))
        if existing is not None:
            with db.tx(self.conn):
                self.conn.execute(
                    "INSERT OR IGNORE INTO source_members"
                    "(raw_file_id,source_kind,source_label,member_path,first_seen_at) VALUES(?,?,?,?,?)",
                    (existing["id"], source_kind, source_label, member_path, now),
                )
                self._item(result, int(existing["id"]), "duplicate", "exact_hash")
            return

        path = archive.archive_path(self.root, stored.rel)
        outcome = parse_file(path, self.root, _cached_keychains(self.conn, self.root, stored.sha256))
        status = _status_for(outcome)
        version = outcome.version if outcome.version is not None else detect_version(path)
        with db.tx(self.conn):
            raced = db.one(self.conn, "SELECT * FROM raw_files WHERE sha256=?", (stored.sha256,))
            if raced is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO source_members"
                    "(raw_file_id,source_kind,source_label,member_path,first_seen_at) VALUES(?,?,?,?,?)",
                    (raced["id"], source_kind, source_label, member_path, now),
                )
                self._item(result, int(raced["id"]), "duplicate", "exact_hash")
                return
            cur = self.conn.execute(
                """INSERT INTO raw_files(sha256,size_bytes,stored_rel,original_name,format_version,
                    status,error_category,error_detail,parser_version,parser_source,
                    first_imported_at,last_parsed_at,decode_meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stored.sha256,
                    stored.size,
                    stored.rel,
                    filename,
                    version,
                    status,
                    outcome.error_category,
                    outcome.error_detail,
                    outcome.parser_version,
                    outcome.parser_source,
                    now,
                    now if outcome.status == "decoded" else None,
                    json.dumps(outcome.meta, separators=(",", ":")),
                ),
            )
            raw_id = int(cur.lastrowid)
            self.conn.execute(
                "INSERT OR IGNORE INTO source_members"
                "(raw_file_id,source_kind,source_label,member_path,first_seen_at) VALUES(?,?,?,?,?)",
                (raw_id, source_kind, source_label, member_path, now),
            )
            flight_id = None
            new_flight = False
            match = None
            if outcome.status == "decoded" or (
                outcome.status == "pending_key"
                and outcome.details
                and (outcome.details.get("start_time_utc") or parse_filename_time(filename))
            ):
                flight_id, new_flight, match = _insert_or_link_flight(
                    self.conn,
                    raw_id,
                    outcome,
                    filename=filename,
                    source_kind=source_kind,
                    member_path=member_path,
                )
            self._item(
                result,
                raw_id,
                status,
                outcome.error_category,
                flight_id=flight_id,
                new_flight=new_flight,
            )
            if match:
                result.items[-1]["match"] = match

    # ---------------------------------------------------------------- sources
    def import_stream(
        self,
        src: BinaryIO,
        filename: str,
        *,
        source_kind: str,
        source_label: str,
        member_path: str | None = None,
        batch_id: str | None = None,
    ) -> BatchResult:
        batch_id = self.begin_batch(source_label, source_kind, batch_id)
        result = BatchResult(batch_id=batch_id)
        self._ingest(
            src,
            filename,
            source_kind=source_kind,
            source_label=source_label,
            member_path=member_path or filename,
            result=result,
        )
        self.finish_batch(result)
        return result

    def import_single(self, path: Path, *, source_kind: str = "manual",
                      display_name: str | None = None,
                      member_path: str | None = None,
                      source_label: str = "Single file") -> BatchResult:
        """Import one file. display_name keeps the owner's real filename when the
        bytes arrived as a stream with a temporary path."""
        path = Path(path)
        name = display_name or path.name
        if (display_name is None) and path.suffix.lower() == ".zip":
            return self.import_zip(path)
        batch_id = self.begin_batch(source_label, source_kind)
        result = BatchResult(batch_id=batch_id)
        if not path.is_file():
            self._item(result, None, "needs_attention", "not_a_file")
            self.finish_batch(result)
            return result
        with open(path, "rb") as fh:
            self._ingest(
                fh,
                name,
                source_kind=source_kind,
                source_label=source_label,
                member_path=member_path or name,
                result=result,
            )
        self.finish_batch(result)
        return result

    def import_folder(self, path: Path, *, default_kind: str | None = None) -> BatchResult:
        path = Path(path)
        if default_kind is None:
            default_kind = "extras" if "controller" in path.name.lower() else "archive"
        batch_id = self.begin_batch("Folder", default_kind)
        result = BatchResult(batch_id=batch_id)
        if not path.is_dir():
            self._item(result, None, "needs_attention", "not_a_folder")
            self.finish_batch(result)
            return result
        count = 0
        total = 0
        for file in archive.iter_folder(path):
            if not archive.is_flight_filename(file.name):
                continue
            count += 1
            try:
                total += file.stat().st_size
            except OSError:
                continue
            if count > archive.MAX_ARCHIVE_MEMBERS or total > archive.MAX_TOTAL_UNCOMPRESSED:
                result.note = "archive_size_bound"
                break
            rel = str(file.relative_to(path)).replace("\\", "/")
            kind = default_kind
            low = rel.lower()
            if "controller-test" in low or "controller_test" in low:
                kind = "extras"
            elif "flight_logs" in low:
                kind = "archive"
            try:
                with open(file, "rb") as fh:
                    self._ingest(
                        fh,
                        file.name,
                        source_kind=kind,
                        source_label="Folder",
                        member_path=rel,
                        result=result,
                    )
            except OSError as exc:
                self._item(result, None, "needs_attention",
                           getattr(exc, "category", "io_error"), type(exc).__name__)
        if count == 0:
            result.note = "no_eligible_files"
        self.finish_batch(result)
        return result

    def import_zip(self, path: Path, *, batch_id: str | None = None) -> BatchResult:
        path = Path(path)
        batch_id = self.begin_batch("ZIP archive", "zip", batch_id)
        result = BatchResult(batch_id=batch_id)
        try:
            with zipfile.ZipFile(path) as zf:
                members = list(archive.iter_zip_members(zf))
                if not members:
                    result.note = "no_eligible_files"
                for norm, info in members:
                    kind = archive.classify_member(norm)
                    with zf.open(info, "r") as fh:
                        self._ingest(
                            fh,
                            Path(norm).name,
                            source_kind=kind,
                            source_label="ZIP archive",
                            member_path=norm,
                            result=result,
                        )
        except ZipUnsafe as exc:
            result.note = "zip_unsafe"
            self._item(result, None, "needs_attention", exc.category,
                       "archive rejected; no members imported")
        except archive.TooLarge as exc:
            result.note = "archive_size_bound"
            self._item(result, None, "needs_attention", exc.category,
                       "archive rejected; no members imported")
        except (zipfile.BadZipFile, OSError) as exc:
            result.note = "zip_unreadable"
            self._item(result, None, "needs_attention", "zip_unreadable", type(exc).__name__)
        self.finish_batch(result)
        return result

    # ---------------------------------------------------------------- reparse
    def reparse_raw(self, raw_id: int) -> dict[str, Any]:
        raw = db.one(self.conn, "SELECT * FROM raw_files WHERE id=?", (raw_id,))
        if raw is None:
            return {"ok": False, "code": "not_found"}
        path = archive.archive_path(self.root, raw["stored_rel"])
        outcome = parse_file(path, self.root, _cached_keychains(self.conn, self.root, raw["sha256"]))
        status = _status_for(outcome)
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE raw_files SET status=?,error_category=?,error_detail=?,"
                "last_parsed_at=?,parser_version=?,parser_source=? WHERE id=?",
                (
                    status,
                    outcome.error_category,
                    outcome.error_detail,
                    db.now_utc(),
                    outcome.parser_version,
                    outcome.parser_source,
                    raw_id,
                ),
            )
            new_flight = False
            # A record that is still waiting for the key can still need its
            # stored fields corrected, so it reparses too. Nothing is lost: a
            # parse with fewer samples never replaces a richer stored flight.
            if outcome.status == "decoded" or (
                outcome.status == "pending_key" and outcome.details
            ):
                _, new_flight, _ = _insert_or_link_flight(
                    self.conn,
                    raw_id,
                    outcome,
                    filename=raw["original_name"],
                    source_kind="reparse",
                    member_path=raw["original_name"],
                )
            db.audit(
                self.conn,
                "local",
                "reparse",
                "raw_file",
                str(raw_id),
                {"status": status, "new_flight": new_flight},
            )
        return {
            "ok": outcome.status == "decoded",
            "status": status,
            "new_flight": new_flight,
            "code": outcome.error_category,
        }
