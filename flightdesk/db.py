"""SQLite schema and small transaction helpers.

The database is the index; the immutable byte archive is the source of truth
for originals. Every writing path takes the same BEGIN IMMEDIATE transaction
and relies on uniqueness constraints for idempotency.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import SCHEMA_VERSION

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_files (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    stored_rel TEXT NOT NULL,
    original_name TEXT NOT NULL,
    format_version INTEGER,
    media_kind TEXT NOT NULL DEFAULT 'flight_txt',
    status TEXT NOT NULL DEFAULT 'imported',
    error_category TEXT,
    error_detail TEXT,
    parser_version TEXT,
    parser_source TEXT,
    first_imported_at TEXT NOT NULL,
    last_parsed_at TEXT,
    decode_meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_files(status);
CREATE INDEX IF NOT EXISTS idx_raw_version ON raw_files(format_version);

CREATE TABLE IF NOT EXISTS source_members (
    raw_file_id INTEGER NOT NULL REFERENCES raw_files(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_label TEXT NOT NULL,
    member_path TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (raw_file_id, source_kind, member_path)
);
CREATE INDEX IF NOT EXISTS idx_member_kind ON source_members(source_kind);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY,
    identity_status TEXT NOT NULL DEFAULT 'authoritative',
    aircraft_sn TEXT,
    aircraft_name TEXT,
    battery_sn TEXT,
    camera_sn TEXT,
    rc_sn TEXT,
    start_time_utc TEXT,
    start_time_offset_min INTEGER,
    start_time_precision TEXT,
    filename_time TEXT,
    pilot TEXT,
    tags_json TEXT,
    notes TEXT,
    duration_s REAL,
    recorder_duration_s REAL,
    airborne_time_s REAL,
    total_distance_m REAL,
    path_length_m REAL,
    max_home_distance_m REAL,
    max_height_m REAL,
    max_altitude_m REAL,
    max_h_speed_mps REAL,
    home_lat REAL,
    home_lon REAL,
    home_alt_m REAL,
    battery_start_pct REAL,
    battery_end_pct REAL,
    battery_temp_max_c REAL,
    battery_temp_min_c REAL,
    signal_min REAL,
    sample_count INTEGER DEFAULT 0,
    gap_count INTEGER DEFAULT 0,
    warnings_json TEXT,
    metrics_source TEXT,
    parser_version TEXT,
    fingerprint TEXT,
    aircraft_equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
    battery_equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flights_time ON flights(start_time_utc);
CREATE INDEX IF NOT EXISTS idx_flights_aircraft ON flights(aircraft_sn);
CREATE INDEX IF NOT EXISTS idx_flights_status ON flights(identity_status);
CREATE INDEX IF NOT EXISTS idx_flights_aircraft_eq ON flights(aircraft_equipment_id);
CREATE INDEX IF NOT EXISTS idx_flights_battery_eq ON flights(battery_equipment_id);

CREATE TABLE IF NOT EXISTS flight_files (
    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    raw_file_id INTEGER NOT NULL REFERENCES raw_files(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'primary',
    evidence_json TEXT,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (flight_id, raw_file_id)
);

CREATE TABLE IF NOT EXISTS flight_telemetry (
    flight_id INTEGER PRIMARY KEY REFERENCES flights(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    samples_blob BLOB NOT NULL,
    gaps_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    serial TEXT,
    alias TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    placeholder INTEGER NOT NULL DEFAULT 0,
    parent_id INTEGER REFERENCES equipment(id),
    purchase_date TEXT,
    firmware TEXT,
    operational_state TEXT,
    notes TEXT,
    source_ref TEXT,
    locked_details INTEGER NOT NULL DEFAULT 0,
    edited_fields_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(kind, name)
);
CREATE INDEX IF NOT EXISTS idx_equipment_kind ON equipment(kind);

CREATE TABLE IF NOT EXISTS serial_links (
    serial TEXT PRIMARY KEY,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    override INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    due_soon_pct REAL NOT NULL DEFAULT 0.80,
    flights_limit INTEGER,
    air_minutes_limit REAL,
    charges_limit INTEGER,
    captured_status TEXT,
    captured_counter_text TEXT,
    captured_baseline_text TEXT,
    captured_baseline_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(equipment_id, code)
);

CREATE TABLE IF NOT EXISTS service_snapshots (
    id INTEGER PRIMARY KEY,
    source_label TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    covered_through TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_counters (
    snapshot_id INTEGER NOT NULL REFERENCES service_snapshots(id) ON DELETE CASCADE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    rounded INTEGER NOT NULL DEFAULT 0,
    raw_text TEXT,
    covered_through TEXT,
    PRIMARY KEY (snapshot_id, equipment_id, rule_code, metric)
);

CREATE TABLE IF NOT EXISTS snapshot_flights (
    snapshot_id INTEGER NOT NULL REFERENCES service_snapshots(id) ON DELETE CASCADE,
    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (snapshot_id, flight_id)
);

CREATE TABLE IF NOT EXISTS cover_resolutions (
    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    snapshot_id INTEGER NOT NULL REFERENCES service_snapshots(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (flight_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
    rule_id INTEGER REFERENCES rules(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT,
    occurred_precision TEXT DEFAULT 'date',
    ended_at TEXT,
    notes TEXT,
    source TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 1,
    external_key TEXT UNIQUE,
    group_key TEXT,
    raw_wording TEXT,
    time_range_text TEXT,
    counter_text TEXT,
    operator TEXT,
    linked_rule_id INTEGER REFERENCES rules(id) ON DELETE SET NULL,
    reverses_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
    reversed_by_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
    provenance_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_services_equipment ON services(equipment_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_services_kind ON services(kind);

CREATE TABLE IF NOT EXISTS battery_readings (
    id INTEGER PRIMARY KEY,
    battery_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    flight_id INTEGER REFERENCES flights(id) ON DELETE SET NULL,
    at TEXT,
    charge_pct REAL,
    temp_c REAL,
    cycles INTEGER,
    voltage REAL,
    source TEXT NOT NULL,
    temp_missing INTEGER NOT NULL DEFAULT 0,
    UNIQUE(flight_id, battery_id, source)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source_label TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    totals_json TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS import_items (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    raw_file_id INTEGER REFERENCES raw_files(id) ON DELETE SET NULL,
    outcome TEXT NOT NULL,
    code TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keychains (
    raw_sha256 TEXT PRIMARY KEY,
    protected_blob BLOB NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    keychain_count INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS backup_runs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    destination TEXT NOT NULL,
    manifest_sha256 TEXT,
    status TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS usb_scans (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    device TEXT,
    scanned INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    attention INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS usb_devices (
    device TEXT PRIMARY KEY,
    last_seen TEXT,
    last_success TEXT,
    last_scan TEXT,
    status TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS recon_runs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path)
    if readonly:
        uri = f"file:{p.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: Path | str) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    _retire_media_columns(conn)
    _add_warning_columns(conn)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={int(SCHEMA_VERSION)}")
    return conn


def _retire_media_columns(conn: sqlite3.Connection) -> None:
    """Drop the photo/video columns from databases created before they were
    removed from the product.

    Photo and video counts were never reliable — the reference claimed more
    files than it held — and they were the least useful field in the app, so
    they are gone rather than left as dead columns a later reader would trust.
    Idempotent: a fresh database never has them.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(flights)")}
    for column in ("photo_count", "video_time_s", "media_source"):
        if column in existing:
            try:
                conn.execute(f"ALTER TABLE flights DROP COLUMN {column}")
            except sqlite3.Error:
                # Older SQLite, or the column is referenced by an index/view.
                # Leaving it is safe: nothing reads it any more.
                pass
    conn.commit()


def _add_warning_columns(conn: sqlite3.Connection) -> None:
    """Add the per-flight warning record to databases created before it.

    ALTER TABLE ... ADD COLUMN is idempotent here because the column is checked
    first. Existing rows keep NULL, which reads as "no warnings recorded" rather
    than "a clean flight" — the UI distinguishes the two.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(flights)")}
    if "warnings_json" not in existing:
        try:
            conn.execute("ALTER TABLE flights ADD COLUMN warnings_json TEXT")
        except sqlite3.Error:
            pass
    conn.commit()


@contextlib.contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Serializable-enough write transaction (BEGIN IMMEDIATE)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = one(conn, "SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, separators=(",", ":")), now_utc()),
    )


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    entity: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log(at,actor,action,entity,entity_id,payload_json) VALUES(?,?,?,?,?,?)",
        (now_utc(), actor, action, entity, None if entity_id is None else str(entity_id),
         json.dumps(payload or {}, separators=(",", ":"))),
    )


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        "raw_files", "flights", "equipment", "rules", "services",
        "service_snapshots", "flights", "battery_readings", "import_batches",
    ]
    out: dict[str, int] = {}
    for name in dict.fromkeys(names):
        out[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
    return out
