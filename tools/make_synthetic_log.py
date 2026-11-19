"""Generate *synthetic* DJI TXT logs with a real v7-v11 details block.

The bytes are not copied from any real flight. Only the container layout is
modelled on the public dji-log-parser source (v0.5.7) so the pinned parser can
be exercised offline. All names, serials, dates and routes are invented.

Usage:
  python tools/make_synthetic_log.py OUTDIR [--version 8] [--count 1]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import struct

DETAILS_LEN = 380
PREFIX_LEN = 100


def _fixed(text: str, size: int) -> bytes:
    raw = text.encode("utf-8")[:size]
    return raw + b"\x00" * (size - len(raw))


def build_details(
    *,
    start_time: _dt.datetime,
    latitude: float,
    longitude: float,
    total_distance_m: float,
    total_time_s: float,
    max_height_m: float,
    max_h_speed: float = 15.0,
    max_v_speed: float = 3.0,
    aircraft_name: str = "Synthetic Airframe",
    aircraft_sn: str = "SYN0000000000001",
    camera_sn: str = "SYNCAM0000000001",
    rc_sn: str = "SYNRC00000000001",
    battery_sn: str = "SYNBAT0000000001",
    product_type: int = 42,
) -> bytes:
    """Build a 380-byte Details block matching the public v0.5.7 layout."""
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware")
    b = bytearray(DETAILS_LEN)
    struct.pack_into("<20s", b, 0, _fixed("", 20))
    struct.pack_into("<20s", b, 20, _fixed("", 20))
    struct.pack_into("<20s", b, 40, _fixed("", 20))
    struct.pack_into("<20s", b, 60, _fixed("", 20))
    struct.pack_into("<B", b, 80, 0)  # is_favorite
    struct.pack_into("<B", b, 81, 1)  # is_new
    struct.pack_into("<B", b, 82, 0)  # needs_upload
    struct.pack_into("<i", b, 83, 0)  # record_line_count
    struct.pack_into("<i", b, 87, 0)  # checksum
    epoch_ms = int(start_time.timestamp() * 1000)
    struct.pack_into("<q", b, 91, epoch_ms)
    struct.pack_into("<d", b, 99, float(longitude))
    struct.pack_into("<d", b, 107, float(latitude))
    struct.pack_into("<f", b, 115, float(total_distance_m))
    struct.pack_into("<i", b, 119, int(round(total_time_s * 1000)))
    struct.pack_into("<f", b, 123, float(max_height_m))
    struct.pack_into("<f", b, 127, float(max_h_speed))
    struct.pack_into("<f", b, 131, float(max_v_speed))
    # moment picture buffers/locations stay zero
    struct.pack_into("<q", b, 243, 0)  # analysis_offset
    struct.pack_into("<f", b, 267, 0.0)  # take_off_altitude
    struct.pack_into("<B", b, 271, int(product_type))
    struct.pack_into("<q", b, 272, int(start_time.timestamp()))  # activation ts
    struct.pack_into("<32s", b, 280, _fixed(aircraft_name, 32))
    struct.pack_into("<16s", b, 312, _fixed(aircraft_sn, 16))
    struct.pack_into("<16s", b, 328, _fixed(camera_sn, 16))
    struct.pack_into("<16s", b, 344, _fixed(rc_sn, 16))
    struct.pack_into("<16s", b, 360, _fixed(battery_sn, 16))
    struct.pack_into("<B", b, 376, 6)  # DJIFly
    struct.pack_into("<3B", b, 377, 1, 2, 3)
    return bytes(b)


def build_log(
    *,
    version: int,
    details: bytes,
    records: bytes = b"",
) -> bytes:
    """Prefix + XOR-record area + details. details_offset is where details start."""
    if not 7 <= version <= 11:
        raise ValueError("this generator models the unencrypted v7-v11 layout")
    if len(details) != DETAILS_LEN:
        raise ValueError("details block must be 380 bytes")
    detail_offset = PREFIX_LEN + len(records)
    prefix = bytearray(PREFIX_LEN)
    struct.pack_into("<Q", prefix, 0, detail_offset)
    struct.pack_into("<H", prefix, 8, DETAILS_LEN)
    struct.pack_into("<B", prefix, 10, version)
    struct.pack_into("<B", prefix, 11, 0)
    struct.pack_into("<Q", prefix, 12, 0)
    return bytes(prefix) + records + details


def default_flight(seed: int = 0) -> dict:
    """Invented flight summary, deterministic per seed."""
    base = _dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=_dt.timezone.utc)
    start = base + _dt.timedelta(days=seed * 3, minutes=seed * 7)
    return {
        "start_time": start,
        "latitude": 47.6205 + seed * 0.0011,
        "longitude": -122.3493 - seed * 0.0013,
        "total_distance_m": 46.9 + seed * 11.0,
        "total_time_s": 302.0 + seed * 13.0,
        "max_height_m": 45.1 + seed * 2.0,
        "max_h_speed": 6.5 + seed * 0.4,
        "max_v_speed": 2.0,
        "aircraft_name": "Synthetic Airframe",
        "aircraft_sn": "SYN0000000000001" if seed % 2 == 0 else "SYN0000000000002",
        "camera_sn": "SYNCAM0000000001",
        "rc_sn": "SYNRC00000000001",
        "battery_sn": "SYNBAT0000000001",
    }


def write_log(outdir: str, version: int, seed: int = 0, name: str | None = None) -> str:
    os.makedirs(outdir, exist_ok=True)
    f = default_flight(seed)
    data = build_log(version=version, details=build_details(**f))
    if name is None:
        stamp = f["start_time"].strftime("%Y-%m-%d_[%H-%M-%S]")
        name = f"FlightRecord_{stamp}.txt" if version < 12 else f"DJIFlightRecord_{stamp}.txt"
    path = os.path.join(outdir, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--version", type=int, default=8, choices=range(7, 12))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    for i in range(args.count):
        write_log(args.outdir, args.version, seed=args.seed + i)
    print(f"synthetic_logs_written={args.count} version={args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
