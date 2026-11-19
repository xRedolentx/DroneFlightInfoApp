"""One parser adapter: pinned dji-log 0.5.7 CLI plus a small details reader.

The adapter never prints raw filenames, stderr or record dumps. It returns
normalized values and a coarse error category; local detail is kept in SQLite.
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths
from .archive import CHUNK

MAX_JSON_BYTES = 512 * 1024 * 1024
PARSER_TIMEOUT_S = 300
DETAILS_LEN = 380


class ParserUnavailable(Exception):
    category = "parser_unavailable"


class ParserFailed(Exception):
    def __init__(self, category: str, detail: str = ""):
        super().__init__(category)
        self.category = category
        self.detail = detail[:4000]


@dataclass
class ParseOutcome:
    version: int | None
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    error_category: str | None = None
    error_detail: str | None = None
    parser_version: str = paths.PARSER_VERSION
    parser_source: str = paths.PARSER_SOURCE
    meta: dict[str, Any] = field(default_factory=dict)


def detect_version(path: Path) -> int | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return None
    if len(head) < 11:
        return None
    version = head[10]
    if 1 <= version <= 32:
        return version
    return None


def _cstr(raw: bytes) -> str:    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


MASK64 = (1 << 64) - 1
CRC64_POLY = 0x95AC9329AC4BC9B5
XOR_MAGIC = 0x123456789ABCDEF0


def crc64_jones(data: bytes, crc: int = 0) -> int:
    """CRC-64/Jones, reflected, init supplied, xorout 0 (crc64 2.0.0)."""
    value = crc & MASK64
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value >> 1) ^ CRC64_POLY) if (value & 1) else (value >> 1)
            value &= MASK64
    return value


def xor_key(seed: int, record_type: int = 0) -> bytes:
    init = (seed + record_type) & 0xFF
    mixed = (XOR_MAGIC * seed) & MASK64
    return crc64_jones(struct.pack("<Q", mixed), init).to_bytes(8, "little")


def xor_decode(data: bytes, seed: int, record_type: int = 0) -> bytes:
    key = xor_key(seed, record_type)
    return bytes(b ^ key[i % 8] for i, b in enumerate(data))


def _parse_details_bytes(b: bytes, version: int) -> dict[str, Any] | None:
    if len(b) < DETAILS_LEN:
        return None
    try:
        epoch_ms = struct.unpack_from("<q", b, 91)[0]
        lon = struct.unpack_from("<d", b, 99)[0]
        lat = struct.unpack_from("<d", b, 107)[0]
        distance = struct.unpack_from("<f", b, 115)[0]
        total_ms = struct.unpack_from("<i", b, 119)[0]
        max_height = struct.unpack_from("<f", b, 123)[0]
        max_h_speed = struct.unpack_from("<f", b, 127)[0]
        max_v_speed = struct.unpack_from("<f", b, 131)[0]
        takeoff_alt = struct.unpack_from("<f", b, 267)[0]
        product_type = b[271]
        aircraft_name = _cstr(b[280:312])
        aircraft_sn = _cstr(b[312:328])
        camera_sn = _cstr(b[328:344])
        rc_sn = _cstr(b[344:360])
        battery_sn = _cstr(b[360:376])
        app_platform = b[376]
        app_version = ".".join(str(x) for x in b[377:380])
    except (struct.error, IndexError):
        return None

    start_utc = None
    if 0 < epoch_ms < 4102444800000:  # before year 2100
        try:
            start_utc = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        except (OverflowError, OSError, ValueError):
            start_utc = None

    def sane_coord(v: float, limit: float) -> float | None:
        return v if math.isfinite(v) and abs(v) <= limit and abs(v) > 1e-9 else None

    def sane_pos(v: float) -> float | None:
        return v if math.isfinite(v) and v >= 0 else None

    return {
        "start_time_utc": start_utc,
        "longitude": sane_coord(lon, 180.0),
        "latitude": sane_coord(lat, 90.0),
        "total_distance_m": distance_metres(sane_pos(distance), version),
        "total_time_s": round(total_ms / 1000.0, 3) if total_ms >= 0 else None,
        "max_height_m": max_height if math.isfinite(max_height) else None,
        "max_h_speed_mps": sane_pos(max_h_speed),
        "max_v_speed_mps": sane_pos(max_v_speed),
        "takeoff_altitude_m": takeoff_alt if math.isfinite(takeoff_alt) else None,
        "product_type": int(product_type),
        "aircraft_name": aircraft_name or None,
        "aircraft_sn": aircraft_sn or None,
        "camera_sn": camera_sn or None,
        "rc_sn": rc_sn or None,
        "battery_sn": battery_sn or None,
        "app_platform": int(app_platform),
        "app_version": app_version,
    }


# v13 and v14 record the flight's total distance in kilometres; v6-v12 record
# metres. Measured from the archive itself: for v8 and v11 the log's own figure
# matches the distance computed from the decoded frames (ratio 1.0000), while for
# v14 it is exactly a thousand times smaller (ratio 1000.006 across all 39
# records). Read unconverted, 39 real flights contributed 0.02 km instead of
# 21.3 km to the archive total.
DISTANCE_IN_KM_FROM_VERSION = 13


def distance_metres(value: Any, version: int | None) -> float | None:
    """Normalise a total-distance reading to metres for the format version."""
    if value is None:
        return None
    try:
        metres = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(metres) or metres < 0:
        return None
    if version is not None and version >= DISTANCE_IN_KM_FROM_VERSION:
        metres *= 1000.0
    return metres


def _aux_info(path: Path) -> tuple[bytes, int] | None:
    """Read the v13+ Auxiliary Info block at offset 100 (XOR, no key)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(100)
            head = fh.read(3)
            if len(head) < 3 or head[0] != 0:
                return None
            size = struct.unpack_from("<H", head, 1)[0]
            if size < 2 or size > 2_000_000:
                return None
            seed_raw = fh.read(1)
            if not seed_raw:
                return None
            encoded = fh.read(size - 1)
            if len(encoded) != size - 1:
                return None
    except OSError:
        return None
    decoded = xor_decode(encoded, seed_raw[0], 0)
    if len(decoded) < 3:
        return None
    info_len = struct.unpack_from("<H", decoded, 1)[0]
    if 3 + info_len > len(decoded):
        return None
    return decoded[3:3 + info_len], 3 + size


def read_aux_version(path: Path) -> dict[str, int] | None:
    aux = _aux_info(path)
    if aux is None:
        return None
    _, info_total = aux
    try:
        with open(path, "rb") as fh:
            prefix = fh.read(12)
            fh.seek(100 + info_total)
            head = fh.read(3)
            if len(head) < 3 or head[0] != 1:
                return None
            size = struct.unpack_from("<H", head, 1)[0]
            body = fh.read(max(3, size))
    except (OSError, struct.error):
        return None
    if len(body) < 3:
        return None
    version, department = struct.unpack_from("<HB", body, 0)
    stored = struct.unpack_from("<Q", prefix, 0)[0] if len(prefix) >= 8 else 0
    computed = 100 + info_total + 3 + max(3, size)
    return {
        "version": int(version),
        "department": int(department),
        "records_offset": int(stored) if stored else int(computed),
        "aux_info_size": int(info_total),
    }


def read_details(path: Path, version: int) -> dict[str, Any] | None:
    """Read genuine metadata for v6-v14. v13/v14 details are XOR, not AES."""
    if version >= 13:
        aux = _aux_info(path)
        if aux is None:
            return None
        return _parse_details_bytes(aux[0], version)
    if not 6 <= version <= 12:
        return None
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            prefix = fh.read(100)
            if len(prefix) < 12:
                return None
            offset = 100 if version == 12 else struct.unpack_from("<Q", prefix, 0)[0]
            if offset < 0 or offset + DETAILS_LEN > size:
                return None
            fh.seek(offset)
            b = fh.read(DETAILS_LEN)
    except OSError:
        return None
    return _parse_details_bytes(b, version)


def parser_status() -> dict[str, Any]:
    exe = paths.parser_exe()
    return {
        "available": exe.exists(),
        "version": paths.PARSER_VERSION,
        "source": paths.PARSER_SOURCE,
        "sha256_expected": paths.PARSER_SHA256,
    }


def ensure_manifest() -> dict[str, Any]:
    """Record the exact parser artifact once. No network."""
    exe = paths.parser_exe()
    manifest_path = paths.parser_manifest()
    if not exe.exists():
        return parser_status()
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if data.get("version") == paths.PARSER_VERSION and data.get("exe_size") == exe.stat().st_size:
                return data
        except (OSError, ValueError):
            pass
    from .archive import hash_file

    data = {
        "version": paths.PARSER_VERSION,
        "source": paths.PARSER_SOURCE,
        "distribution_sha256": paths.PARSER_SHA256,
        "exe_sha256": hash_file(exe),
        "exe_size": exe.stat().st_size,
        "interface": "CLI JSON via --output; details read locally for v6-v12",
    }
    try:
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    return data


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return float(v)
    return None


# Discrete fault flags the logs raise, with what each one means. Only flags
# whose *true* value is a fault belong here.
#
# Deliberately excluded, because on the real archive they are constant and
# benign rather than faults — including them would raise an alert on every
# flight forever:
#   isImuPreheated      True on every frame once the IMU is ready (68,970/68,970)
#   imuInitFailReason   "MonitorError" on every frame in the real logs
#   nonGpsCause         "Already..." on every frame; it names a state, not a failure
#   isGpsUsed/isVisionUsed  which sensors are in use, not faults
#   gimbal at-limit flags   normal at the ends of travel
FAULT_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("isCompassError", "Compass error", "warning"),
    ("isMotorBlocked", "Motor blocked", "warning"),
    ("isNotEnoughForce", "Not enough force (obstruction or propeller problem)", "warning"),
    ("isPropellerCatapult", "Propeller catapult", "warning"),
    ("isOutOfLimit", "Out of the configured flight limit", "caution"),
    ("isVibrating", "Airframe vibrating", "caution"),
    ("isBarometerDeadInAir", "Barometer failed in flight", "warning"),
    ("isAcceletorOverRange", "Accelerometer out of range", "caution"),
    ("waveError", "Vision / obstacle sensor error", "caution"),
)
SD_CARD_FAULTS = {"Full", "FormatNeeded", "NoSDCard", "SDCardError"}


def frame_warnings(frame: dict) -> dict[str, Any]:
    """The fault conditions this frame is raising, keyed by a stable code.

    Only non-empty results are kept, so a clean flight costs nothing in the
    stored telemetry.
    """
    osd = frame.get("osd") or {}
    out: dict[str, Any] = {}
    for key, _label, _severity in FAULT_FLAGS:
        if osd.get(key) is True:
            out[key] = 1
    level = osd.get("voltageWarning")
    if isinstance(level, (int, float)) and level > 0:
        out["voltageWarning"] = int(level)
    gimbal = frame.get("gimbal") or {}
    if gimbal.get("isStuck") is True:
        out["gimbalStuck"] = 1
    sd = (frame.get("camera") or {}).get("sdCardState")
    if isinstance(sd, str) and sd in SD_CARD_FAULTS:
        out["sdCard"] = sd
    cause = osd.get("motorStartFailedCause")
    if isinstance(cause, str) and cause not in ("", "None", "none"):
        out["motorStartFailed"] = cause
    return out


def sample_from_frame(frame: dict) -> dict | None:
    osd = frame.get("osd") or {}
    t = _num(_first(osd, "flyTime", "fly_time"))
    if t is None:
        return None
    lat = _num(osd.get("latitude"))
    lon = _num(osd.get("longitude"))
    if lat is not None and lon is not None and abs(lat) < 1e-7 and abs(lon) < 1e-7:
        lat = lon = None
    if lat is not None and abs(lat) > 90:
        lat = None
    if lon is not None and abs(lon) > 180:
        lon = None
    x = _num(osd.get("xSpeed")) or 0.0
    y = _num(osd.get("ySpeed")) or 0.0
    speed = math.hypot(x, y) if (x or y) else None
    battery = frame.get("battery") or {}
    rc = frame.get("rc") or {}
    home = frame.get("home") or {}
    up = _num(rc.get("uplinkSignal"))
    down = _num(rc.get("downlinkSignal"))
    signal = min([v for v in (up, down) if v is not None]) if (up is not None or down is not None) else None
    temp = _num(battery.get("temperature"))
    sample: dict[str, Any] = {
        "t": round(t, 3),
        "lat": lat,
        "lon": lon,
        "height_m": _num(osd.get("height")),
        "alt_m": _num(osd.get("altitude")),
        "speed_mps": speed,
        "battery_pct": _num(battery.get("chargeLevel")),
        "battery_c": temp,
        "signal": signal,
        "gps_num": _num(osd.get("gpsNum")),
        "gps_level": _num(osd.get("gpsLevel")),
        "on_ground": bool(osd.get("isOnGround")),
        "motor_on": bool(osd.get("isMotorOn")),
        "home_lat": _num(home.get("latitude")),
        "home_lon": _num(home.get("longitude")),
        "home_alt_m": _num(home.get("altitude")),
        "voltage": _num(battery.get("voltage")),
        "cells": battery.get("cellVoltages") if isinstance(battery.get("cellVoltages"), list) else None,
    }
    warnings = frame_warnings(frame)
    if warnings:
        sample["warn"] = warnings
    return sample


def _run_cli(source: Path, root: Path) -> tuple[dict, dict]:
    exe = paths.parser_exe()
    if not exe.exists():
        raise ParserUnavailable("pinned parser binary is not installed")
    tmp_dir = Path(root) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"parse-{uuid.uuid4().hex}.json"
    proc = None
    try:
        kwargs = {"capture_output": True, "timeout": PARSER_TIMEOUT_S}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run([str(exe), str(source), "-o", str(out)], **kwargs)
        stderr = (proc.stderr or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            low = stderr.lower()
            if "api key" in low or "keychain" in low:
                raise ParserFailed("pending_key", stderr)
            if "unable to parse file" in low or "missingaux" in low:
                raise ParserFailed("damaged", stderr)
            raise ParserFailed("parser_error", stderr)
        if not out.exists():
            raise ParserFailed("damaged", "parser produced no output")
        if out.stat().st_size > MAX_JSON_BYTES:
            raise ParserFailed("too_large", "parser output exceeded bound")
        with open(out, "rb") as fh:
            payload = fh.read()
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ParserFailed("damaged", f"invalid parser JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
            raise ParserFailed("damaged", "parser JSON missing frames")
        return data, {"stderr_len": len(stderr)}
    except subprocess.TimeoutExpired as exc:
        raise ParserFailed("timeout", "parser exceeded the time bound") from exc
    except OSError as exc:
        raise ParserFailed("parser_error", f"{type(exc).__name__}") from exc
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        if proc is not None and proc.stdout:
            proc.stdout = b""


def _run_shim(source: Path, keychain_json: list) -> tuple[dict | None, str | None]:
    exe = os.environ.get("FLIGHTDESK_DECODE_EXE")
    exe_path = Path(exe) if exe else paths.app_home() / "vendor" / "flightdesk-decode" / "flightdesk-decode.exe"
    if not exe_path.exists():
        return None, "decoder_unavailable"
    request = json.dumps({"file": str(source), "keychains": keychain_json})
    try:
        kwargs = {"input": request.encode("utf-8"), "capture_output": True,
                  "timeout": PARSER_TIMEOUT_S}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run([str(exe_path)], **kwargs)
    except subprocess.TimeoutExpired:
        return None, "decoder_timeout"
    except OSError:
        return None, "decoder_start_failed"
    if proc.returncode != 0:
        return None, f"decoder_exit_{proc.returncode}"
    if len(proc.stdout or b"") > MAX_JSON_BYTES:
        return None, "decoder_output_too_large"
    try:
        data = json.loads((proc.stdout or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "decoder_invalid_json"
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        return None, "decoder_missing_frames"
    return data, None


# What the v13/v14 metadata block can be trusted to supply without the key.
# The brief lists date, aircraft, battery serial and duration; anything else
# read from the encrypted payload is a measurement we cannot verify, and a
# wrong number recorded as a reading is worse than an absent one.
METADATA_ONLY_KEYS = (
    "start_time_utc",
    "total_time_s",
    "aircraft_name",
    "aircraft_sn",
    "camera_sn",
    "rc_sn",
    "battery_sn",
    "product_type",
    "app_platform",
    "app_version",
)


def metadata_only_details(details: dict[str, Any]) -> dict[str, Any]:
    """Keep identity fields and duration; drop measurements the encrypted
    payload alone can settle. Measured evidence: 39 real v14 records read this
    way carried a distance roughly a thousand times too small for their own
    duration, which silently entered the archive total as near-zero."""
    return {k: details[k] for k in METADATA_ONLY_KEYS if k in details}


def parse_file(path: Path, root: Path, keychain_json: list | None = None) -> ParseOutcome:
    version = detect_version(path)
    meta: dict[str, Any] = {"size_bytes": path.stat().st_size if path.exists() else 0}
    if version is None:
        return ParseOutcome(version=None, status="damaged", error_category="bad_header",
                            error_detail="version byte missing or empty file", meta=meta)
    if version >= 13:
        details = read_details(path, version) or {}
        meta["metadata_only"] = bool(details)
        if not keychain_json:
            return ParseOutcome(
                version=version,
                status="pending_key",
                details=metadata_only_details(details),
                error_category="encrypted_requires_dji_key",
                error_detail="records require the official DJI keychain; metadata read without key",
                meta=meta,
            )
        data, error = _run_shim(path, keychain_json)
        if data is None:
            return ParseOutcome(
                version=version,
                status="pending_key",
                details=metadata_only_details(details),
                error_category=error or "decoder_unavailable",
                error_detail="keychain present but the local decoder could not produce frames",
                meta=meta,
            )
        meta["keychain_decode"] = True
        details_json = data.get("details") if isinstance(data.get("details"), dict) else {}
        merged = dict(details)
        for local_key, cli_key in (
            ("total_time_s", "totalTime"),
            ("total_distance_m", "totalDistance"),
            ("max_height_m", "maxHeight"),
            ("max_h_speed_mps", "maxHorizontalSpeed"),
        ):
            if merged.get(local_key) is None and details_json.get(cli_key) is not None:
                # The decoded payload uses the same unit for this version, so
                # the same conversion applies to whichever source supplied it.
                merged[local_key] = (
                    distance_metres(details_json[cli_key], version)
                    if local_key == "total_distance_m" else details_json[cli_key]
                )
        merged["aircraft_name"] = merged.get("aircraft_name") or details_json.get("aircraftName")
        merged["aircraft_sn"] = merged.get("aircraft_sn") or details_json.get("aircraftSn")
        merged["camera_sn"] = merged.get("camera_sn") or details_json.get("cameraSn")
        merged["rc_sn"] = merged.get("rc_sn") or details_json.get("rcSn")
        samples = []
        for frame in data.get("frames") or []:
            if isinstance(frame, dict):
                s = sample_from_frame(frame)
                if s is not None:
                    samples.append(s)
        return ParseOutcome(
            version=version,
            status="decoded",
            details=merged,
            samples=samples,
            meta=meta,
        )
    details = read_details(path, version) or {}
    try:
        data, cli_meta = _run_cli(path, root)
    except ParserUnavailable as exc:
        return ParseOutcome(version=version, status="needs_attention",
                            details=details, error_category="parser_unavailable",
                            error_detail=str(exc), meta=meta)
    except ParserFailed as exc:
        status = "pending_key" if exc.category == "pending_key" else (
            "unsupported" if exc.category == "too_large" else "damaged"
        )
        return ParseOutcome(version=version, status=status, details=details,
                            error_category=exc.category, error_detail=exc.detail, meta=meta)
    meta.update(cli_meta)
    details_json = data.get("details") if isinstance(data.get("details"), dict) else {}
    # The normalized export omits start time, location and battery serial; fill
    # those from the local details reader. Values present in both must agree.
    merged = dict(details)
    for key in ("totalTime", "totalDistance", "maxHeight", "maxHorizontalSpeed"):
        if key in details_json and details_json[key] is not None:
            merged.setdefault("_cli_" + key, details_json[key])
    merged["aircraft_name"] = merged.get("aircraft_name") or details_json.get("aircraftName")
    merged["aircraft_sn"] = merged.get("aircraft_sn") or details_json.get("aircraftSn")
    merged["camera_sn"] = merged.get("camera_sn") or details_json.get("cameraSn")
    merged["rc_sn"] = merged.get("rc_sn") or details_json.get("rcSn")
    samples = []
    for frame in data.get("frames") or []:
        if isinstance(frame, dict):
            s = sample_from_frame(frame)
            if s is not None:
                samples.append(s)
    return ParseOutcome(
        version=version,
        status="decoded",
        details=merged,
        samples=samples,
        meta=meta,
    )
