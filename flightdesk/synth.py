"""Synthetic DJI TXT fixture builder (invented data only).

The layout follows the public dji-log-parser v0.5.7 details block so the pinned
parser can be exercised. No bytes come from a real flight.
"""
from __future__ import annotations

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
    aircraft_sn: str = "SYN-M2P-0001",
    camera_sn: str = "SYNCAM0000000001",
    rc_sn: str = "SYNRC00000000001",
    battery_sn: str = "SYN-BAT-1001",
    product_type: int = 42,
) -> bytes:
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware")
    b = bytearray(DETAILS_LEN)
    struct.pack_into("<20s", b, 0, _fixed("", 20))
    struct.pack_into("<20s", b, 20, _fixed("", 20))
    struct.pack_into("<20s", b, 40, _fixed("", 20))
    struct.pack_into("<20s", b, 60, _fixed("", 20))
    struct.pack_into("<B", b, 80, 0)
    struct.pack_into("<B", b, 81, 1)
    struct.pack_into("<B", b, 82, 0)
    struct.pack_into("<i", b, 83, 0)
    struct.pack_into("<i", b, 87, 0)
    struct.pack_into("<q", b, 91, int(start_time.timestamp() * 1000))
    struct.pack_into("<d", b, 99, float(longitude))
    struct.pack_into("<d", b, 107, float(latitude))
    struct.pack_into("<f", b, 115, float(total_distance_m))
    struct.pack_into("<i", b, 119, int(round(total_time_s * 1000)))
    struct.pack_into("<f", b, 123, float(max_height_m))
    struct.pack_into("<f", b, 127, float(max_h_speed))
    struct.pack_into("<f", b, 131, float(max_v_speed))
    struct.pack_into("<q", b, 243, 0)
    struct.pack_into("<f", b, 267, 0.0)
    struct.pack_into("<B", b, 271, int(product_type))
    struct.pack_into("<q", b, 272, int(start_time.timestamp()))
    struct.pack_into("<32s", b, 280, _fixed(aircraft_name, 32))
    struct.pack_into("<16s", b, 312, _fixed(aircraft_sn, 16))
    struct.pack_into("<16s", b, 328, _fixed(camera_sn, 16))
    struct.pack_into("<16s", b, 344, _fixed(rc_sn, 16))
    struct.pack_into("<16s", b, 360, _fixed(battery_sn, 16))
    struct.pack_into("<B", b, 376, 6)
    struct.pack_into("<3B", b, 377, 1, 2, 3)
    return bytes(b)


def build_log(*, version: int, details: bytes, records: bytes = b"") -> bytes:
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


AIRCRAFT = {
    "mavic2": {
        "name": "Mavic 2 Pro",
        "sn": "SYN-M2P-0001",
        "battery": "SYN-BAT-1001",
        "product": 42,
    },
    "air3s": {
        "name": "Air 3S",
        "sn": "SYN-A3S-0002",
        "battery": "SYN-BAT-2002",
        "product": 77,
    },
    "mini3": {
        "name": "Mini 3 Pro",
        "sn": "SYN-M3P-0003",
        "battery": "SYN-BAT-3001",
        "product": 103,
    },
}


def make_log_bytes(
    *,
    version: int = 11,
    start: _dt.datetime,
    aircraft: str = "mavic2",
    battery_sn: str | None = None,
    duration_s: float = 302.0,
    distance_m: float = 154.0,
    max_height_m: float = 45.0,
    max_h_speed: float = 15.0,
    lat: float = 45.6789,
    lon: float = 12.3456,
) -> bytes:
    profile = AIRCRAFT[aircraft]
    details = build_details(
        start_time=start,
        latitude=lat,
        longitude=lon,
        total_distance_m=distance_m,
        total_time_s=duration_s,
        max_height_m=max_height_m,
        max_h_speed=max_h_speed,
        aircraft_name=profile["name"],
        aircraft_sn=profile["sn"],
        battery_sn=battery_sn or profile["battery"],
        product_type=profile["product"],
    )
    return build_log(version=version, details=details)


def write_log(path: str | os.PathLike, data: bytes) -> str:
    os.makedirs(os.path.dirname(os.fspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return os.fspath(path)


def filename_time(start: _dt.datetime, prefix: str = "DJIFlightRecord") -> str:
    return f"{prefix}_{start.strftime('%Y-%m-%d_[%H-%M-%S]')}.txt"


def make_v13_metadata_log(
    *,
    start: _dt.datetime,
    aircraft: str = "air3s",
    battery_sn: str | None = None,
    duration_s: float = 302.0,
    distance_m: float = 46.9,
    max_height_m: float = 45.1,
    details_blob: bytes | None = None,
) -> bytes:
    """A v13/v14 container with an XOR auxiliary details block and no records.

    The pinned parser reads genuine metadata from this without a DJI key.
    """
    from .parser_adapter import xor_decode

    profile = AIRCRAFT[aircraft]
    if details_blob is None:
        details_blob = build_details(
            start_time=start,
            latitude=45.6789,
            longitude=12.3456,
            total_distance_m=distance_m / 1000.0,  # v13/v14 store kilometres
            total_time_s=duration_s,
            max_height_m=max_height_m,
            aircraft_name=profile["name"],
            aircraft_sn=profile["sn"],
            battery_sn=battery_sn or profile["battery"],
            product_type=profile["product"],
        )
    plain = bytes([0]) + struct.pack("<H", len(details_blob)) + details_blob + struct.pack("<H", 0)
    seed = 0x5A
    encoded = xor_decode(plain, seed, 0)
    size = 1 + len(plain)
    info = b"\x00" + struct.pack("<H", size) + bytes([seed]) + encoded
    version_block = b"\x01" + struct.pack("<H", 3) + struct.pack("<HB", 13, 3)
    records_offset = PREFIX_LEN + len(info) + len(version_block)
    prefix = bytearray(PREFIX_LEN)
    struct.pack_into("<Q", prefix, 0, records_offset)
    struct.pack_into("<H", prefix, 8, DETAILS_LEN)
    struct.pack_into("<B", prefix, 10, 13)
    struct.pack_into("<B", prefix, 11, 0)
    struct.pack_into("<Q", prefix, 12, 0)
    return bytes(prefix) + info + version_block


def make_v13_keychain_log(
    *,
    start: _dt.datetime,
    entries: list[tuple[int, bytes]] | None = None,
    separator_after: int = 1,
) -> bytes:
    """A v13 container with plaintext-feature KeyStorage records for tests."""
    from .parser_adapter import xor_decode

    entries = entries or [(1, b"first-ciphertext"), (2, b"second-ciphertext")]
    base = make_v13_metadata_log(start=start)
    # Recover the info/version blocks and append records after them.
    info_total = 3 + struct.unpack_from("<H", base, 101)[0]
    version_block = base[100 + info_total:100 + info_total + 6]
    records = b""
    for index, (feature, data) in enumerate(entries):
        payload = struct.pack("<HH", feature, len(data)) + data
        seed = 0x33
        enc = xor_decode(payload, seed, 56)
        size = 1 + len(payload)
        records += bytes([56]) + struct.pack("<H", size) + bytes([seed]) + enc + b"\xff"
        if index + 1 == separator_after:
            records += bytes([50]) + struct.pack("<H", 0) + b"\xff"
    return base[:100 + info_total + len(version_block)] + records
