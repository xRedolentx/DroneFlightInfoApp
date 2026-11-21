"""DJI keychain exchange, OS-protected key storage and per-file cache.

Only the official DJI keychain endpoint is contacted, and only after the owner
explicitly asks. The API key is sent in the request header, never in a URL,
process argument, log or backup. Frames are decoded by the local pinned shim.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import db, security
from .parser_adapter import detect_version, read_aux_version, xor_decode

ENDPOINT = "https://dev.dji.com/openapi/v1/flight-records/keychains"
KEY_FILE = "keychains/dji_key.bin"
FEATURE_POINTS = {
    1: "FR_Standardization_Feature_Base_1",
    2: "FR_Standardization_Feature_Vision_2",
    3: "FR_Standardization_Feature_Waypoint_3",
    4: "FR_Standardization_Feature_Agriculture_4",
    5: "FR_Standardization_Feature_AirLink_5",
    6: "FR_Standardization_Feature_AfterSales_6",
    7: "FR_Standardization_Feature_DJIFlyCustom_7",
    8: "FR_Standardization_Feature_Plaintext_8",
    9: "FR_Standardization_Feature_FlightHub_9",
    10: "FR_Standardization_Feature_Gimbal_10",
    11: "FR_Standardization_Feature_RC_11",
    12: "FR_Standardization_Feature_Camera_12",
    13: "FR_Standardization_Feature_Battery_13",
    14: "FR_Standardization_Feature_FlySafe_14",
    15: "FR_Standardization_Feature_Security_15",
}


def key_path(root: Path) -> Path:
    return Path(root) / KEY_FILE


def has_key(root: Path) -> bool:
    return key_path(root).exists()


def set_key(root: Path, key: str) -> dict[str, Any]:
    value = (key or "").strip()
    if len(value) < 8 or len(value) > 512 or any(ch.isspace() for ch in value):
        return {"ok": False, "code": "invalid_key_format"}
    security.atomic_private_write(key_path(root), security.dpapi_protect(value.encode("utf-8")))
    return {"ok": True, "configured": True}


def clear_key(root: Path) -> dict[str, Any]:
    try:
        key_path(root).unlink(missing_ok=True)
    except OSError:
        return {"ok": False, "code": "delete_failed"}
    return {"ok": True, "configured": False}


def load_key(root: Path) -> str | None:
    path = key_path(root)
    if not path.exists():
        return None
    try:
        return security.dpapi_unprotect(path.read_bytes()).decode("utf-8")
    except (OSError, security.SecretError, UnicodeDecodeError):
        return None


def extract_request(path: Path) -> dict[str, Any] | None:
    """Rebuild the pinned parser's KeychainsRequest without touching records."""
    version = detect_version(path)
    if version is None or version < 13:
        return None
    aux = read_aux_version(path)
    if aux is None:
        return None
    records_offset = aux["records_offset"]
    try:
        file_size = path.stat().st_size
        if records_offset <= 0 or records_offset >= file_size:
            return None
        with open(path, "rb") as fh:
            fh.seek(records_offset)
            keychains: list[list[dict[str, str]]] = []
            current: list[dict[str, str]] = []
            while fh.tell() < file_size:
                type_raw = fh.read(1)
                if not type_raw:
                    break
                record_type = type_raw[0]
                if record_type == 0xFF:
                    break
                size_raw = fh.read(2)
                if len(size_raw) < 2:
                    break
                size = struct.unpack("<H", size_raw)[0]
                if size > 2_000_000:
                    break
                block = fh.read(size)
                if len(block) < size:
                    break
                end = fh.read(1)
                if record_type == 50:  # KeyStorageRecover
                    keychains.append(current)
                    current = []
                    continue
                if record_type != 56 or size < 5:
                    continue
                seed = block[0]
                payload = xor_decode(block[1:], seed, record_type)
                if len(payload) < 4:
                    continue
                feature_raw, length = struct.unpack_from("<HH", payload, 0)
                if length > len(payload) - 4:
                    continue
                feature = FEATURE_POINTS.get(feature_raw, FEATURE_POINTS[8])
                current.append({
                    "featurePoint": feature,
                    "aesCiphertext": base64.b64encode(payload[4:4 + length]).decode("ascii"),
                })
                if end != b"\xff":
                    # The next byte is not our terminator; stop rather than
                    # walking into arbitrary media payloads.
                    break
            if current:
                keychains.append(current)
    except OSError:
        return None
    keychains = [kc for kc in keychains if kc]
    if not keychains:
        return None
    return {
        "version": int(aux["version"]),
        "department": int(aux["department"]),
        "keychainsArray": keychains,
    }


def fetch(request: dict[str, Any], api_key: str,
          endpoint: str = ENDPOINT, timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json", "Api-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {"ok": False, "code": "invalid_key", "message": "DJI rejected the key"}
        if exc.code == 429:
            return {"ok": False, "code": "rate_limited", "message": "DJI asked us to slow down"}
        return {"ok": False, "code": f"http_{exc.code}", "message": "keychain request failed"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "code": "offline", "message": "network unavailable"}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "code": "invalid_response", "message": "unreadable DJI response"}
    result = payload.get("result") if isinstance(payload, dict) else None
    code = result.get("code") if isinstance(result, dict) else None
    if code != 0:
        return {"ok": False, "code": "api_error", "message": "DJI returned an error"}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return {"ok": False, "code": "missing_keychains", "message": "DJI returned no keychains"}
    return {"ok": True, "keychains": data}


def store_cache(conn: sqlite3.Connection, root: Path, raw_sha: str,
                keychains: list, source: str) -> None:
    blob = security.dpapi_protect(json.dumps(keychains, separators=(",", ":")).encode("utf-8"))
    now = db.now_utc()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO keychains(raw_sha256,protected_blob,source,status,keychain_count,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(raw_sha256) DO UPDATE SET protected_blob=excluded.protected_blob,"
            "source=excluded.source,status='cached',keychain_count=excluded.keychain_count,"
            "updated_at=excluded.updated_at",
            (raw_sha, blob, source, "cached", len(keychains), now, now),
        )


def cached_json(conn: sqlite3.Connection, root: Path, raw_sha: str) -> list | None:
    row = db.one(conn, "SELECT protected_blob,status FROM keychains WHERE raw_sha256=?",
                 (raw_sha,))
    if row is None or row["status"] != "cached":
        return None
    try:
        data = json.loads(security.dpapi_unprotect(row["protected_blob"]).decode("utf-8"))
    except (security.SecretError, UnicodeDecodeError, ValueError, OSError):
        return None
    return data if isinstance(data, list) else None


def cache_summary(conn: sqlite3.Connection) -> dict[str, int]:
    total = int(conn.execute("SELECT COUNT(*) FROM keychains").fetchone()[0])
    cached = int(conn.execute("SELECT COUNT(*) FROM keychains WHERE status='cached'").fetchone()[0])
    pending = int(conn.execute("SELECT COUNT(*) FROM raw_files WHERE status='pending_key'").fetchone()[0])
    return {"cached": cached, "total": total, "pending": pending}


def unlock_raw(conn: sqlite3.Connection, root: Path, raw_id: int,
               api_key: str | None = None) -> dict[str, Any]:
    raw = db.one(conn, "SELECT * FROM raw_files WHERE id=?", (raw_id,))
    if raw is None:
        return {"ok": False, "code": "not_found"}
    try:
        from .importer import Importer
    except Exception:
        return {"ok": False, "code": "internal_error"}
    importer = Importer(root, conn)
    if cached_json(conn, root, raw["sha256"]) is None:
        if not api_key:
            return {"ok": False, "code": "key_required"}
        from .archive import archive_path
        request = extract_request(archive_path(root, raw["stored_rel"]))
        if request is None:
            return {"ok": False, "code": "unsupported_keychain_layout"}
        result = fetch(request, api_key)
        if not result.get("ok"):
            db.audit(conn, "owner", "keychain_fetch_failed", "raw_file", str(raw_id),
                     {"code": result.get("code")})
            return {"ok": False, "code": result.get("code"), "message": result.get("message")}
        store_cache(conn, root, raw["sha256"], result["keychains"], "dji_official")
        # A successful fetch is an outbound call to DJI carrying the owner's API
        # key. It must leave a trace, or the archive has no record that the
        # credential was used and when. Counts only: never the key, never the
        # keychain contents.
        db.audit(conn, "owner", "keychain_fetched", "raw_file", str(raw_id),
                 {"endpoint": ENDPOINT, "keychains": len(result.get("keychains") or [])})
    outcome = importer.reparse_raw(raw_id)
    return outcome
