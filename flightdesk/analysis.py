"""Derived measurements from ordered samples, with explicit gaps.

Canonical units: metres, metres/second, seconds, degrees Celsius, percent.
Display conversion happens in the browser only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

EARTH_R_M = 6371008.8
DEFAULT_MAX_GAP_S = 10.0
MAX_PLAUSIBLE_SPEED_MPS = 60.0

# A value beyond these limits is not a reading. A corrupt or
# version-mismatched details block can yield a 191-hour duration or a
# 128 km/s speed; those become unknown instead of entering a record.
PLAUSIBLE_LIMITS = {
    "duration_s": 43200.0,          # 12 h
    "recorder_duration_s": 43200.0,
    "airborne_time_s": 43200.0,
    "total_distance_m": 500000.0,    # 500 km
    "path_length_m": 500000.0,
    "max_home_distance_m": 500000.0,
    "max_height_m": 1500.0,
    "max_altitude_m": 9000.0,
    "max_h_speed_mps": 100.0,        # 360 km/h
}


def drop_implausible(m: "FlightMetrics") -> None:
    """Clear measured values that cannot be a flight reading, and say so."""
    for field_name, limit in PLAUSIBLE_LIMITS.items():
        value = getattr(m, field_name, None)
        if value is None:
            continue
        try:
            implausible = float(value) > limit
        except (TypeError, ValueError):
            implausible = True
        if implausible:
            setattr(m, field_name, None)
            m.flags.append("implausible_" + field_name)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class FlightMetrics:
    duration_s: float | None = None
    recorder_duration_s: float | None = None
    airborne_time_s: float | None = None
    total_distance_m: float | None = None
    path_length_m: float | None = None
    max_home_distance_m: float | None = None
    max_height_m: float | None = None
    max_altitude_m: float | None = None
    max_h_speed_mps: float | None = None
    battery_start_pct: float | None = None
    battery_end_pct: float | None = None
    battery_temp_max_c: float | None = None
    battery_temp_min_c: float | None = None
    signal_min: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    home_alt_m: float | None = None
    sample_count: int = 0
    valid_fix_count: int = 0
    gap_count: int = 0
    gaps: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    media_photo_events: int = 0
    media_video_events: int = 0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def compute_metrics(
    samples: list[dict[str, Any]],
    details: dict[str, Any] | None = None,
    *,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> FlightMetrics:
    details = details or {}
    m = FlightMetrics()
    clean = [s for s in samples if s.get("t") is not None]
    clean.sort(key=lambda s: float(s["t"]))
    m.sample_count = len(clean)
    if not clean:
        m.duration_s = details.get("total_time_s")
        m.total_distance_m = details.get("total_distance_m")
        m.max_height_m = details.get("max_height_m")
        m.max_h_speed_mps = details.get("max_h_speed_mps")
        m.flags.append("no_samples")
        drop_implausible(m)
        m.flags = sorted(set(m.flags))
        return m

    times = [float(s["t"]) for s in clean]
    m.recorder_duration_s = round(times[-1] - times[0], 3) if len(times) > 1 else 0.0
    if details.get("total_time_s") is not None:
        m.duration_s = float(details["total_time_s"])
    else:
        m.duration_s = m.recorder_duration_s
        m.flags.append("duration_from_recorder")
    if details.get("total_distance_m") is not None:
        m.total_distance_m = float(details["total_distance_m"])
    if details.get("max_height_m") is not None:
        m.max_height_m = float(details["max_height_m"])
    if details.get("max_h_speed_mps") is not None:
        m.max_h_speed_mps = float(details["max_h_speed_mps"])

    # A fixed 10 s threshold is wrong for sparse or heavily downsampled
    # streams; adapt to the actual cadence but never below the default.
    deltas = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
    gap_threshold = max(max_gap_s, 5.0 * _median(deltas))

    home = None
    for s in clean:
        if s.get("home_lat") is not None and s.get("home_lon") is not None:
            home = (float(s["home_lat"]), float(s["home_lon"]), s.get("home_alt_m"))
            break
    first_fix = next(
        (s for s in clean if s.get("lat") is not None and s.get("lon") is not None), None
    )
    if home is None and first_fix is not None:
        home = (float(first_fix["lat"]), float(first_fix["lon"]), first_fix.get("alt_m"))
    if home is not None:
        m.home_lat, m.home_lon, m.home_alt_m = home

    path_m = 0.0
    max_home_m = 0.0
    airborne_s = 0.0
    prev = None
    prev_valid = None
    gap_open: dict[str, Any] | None = None

    def close_gap(end_t: float) -> None:
        nonlocal gap_open
        if gap_open is None:
            return
        gap_open["end"] = round(end_t, 3)
        if gap_open["end"] > gap_open["start"]:
            m.gaps.append(gap_open)
        gap_open = None

    for s in clean:
        t = float(s["t"])
        has_fix = s.get("lat") is not None and s.get("lon") is not None
        dt = (t - float(prev["t"])) if prev is not None else 0.0
        if prev is not None and dt < 0:
            m.flags.append("unordered_samples")
            dt = 0.0

        if has_fix:
            m.valid_fix_count += 1

        if not has_fix:
            if gap_open is None and prev is not None:
                gap_open = {"start": float(prev["t"]), "reason": "missing_fix"}
                m.flags.append("missing_fix")
        elif prev_valid is not None:
            d = haversine_m(
                float(prev_valid["lat"]), float(prev_valid["lon"]),
                float(s["lat"]), float(s["lon"]),
            )
            elapsed = t - float(prev_valid["t"])
            if elapsed > gap_threshold:
                # The gap spans the interval with no samples; both boundary
                # samples stay on their own segments.
                if gap_open is not None:
                    close_gap(t)
                if elapsed > 0:
                    m.gaps.append({"start": round(float(prev_valid["t"]), 3),
                                   "end": round(t, 3), "reason": "time_gap"})
            elif d > max(MAX_PLAUSIBLE_SPEED_MPS * max(elapsed, 1.0), 250.0):
                if gap_open is not None:
                    close_gap(t)
                m.gaps.append({"start": round(float(prev_valid["t"]), 3),
                               "end": round(t, 3), "reason": "poor_fix"})
                m.flags.append("poor_fix")
            else:
                path_m += d
                if gap_open is not None:
                    close_gap(t)

        if has_fix and home is not None:
            max_home_m = max(
                max_home_m,
                haversine_m(home[0], home[1], float(s["lat"]), float(s["lon"])),
            )

        if prev is not None and 0 < dt <= gap_threshold:
            height = s.get("height_m")
            if bool(s.get("motor_on")) or (isinstance(height, (int, float)) and height > 0.5):
                airborne_s += dt

        if has_fix:
            prev_valid = s
        prev = s

    if gap_open is not None:
        close_gap(float(clean[-1]["t"]))
    m.path_length_m = round(path_m, 2) if m.valid_fix_count > 1 else None
    m.max_home_distance_m = round(max_home_m, 2) if m.valid_fix_count else None
    m.airborne_time_s = round(airborne_s, 3) if m.valid_fix_count else None
    m.gap_count = len(m.gaps)

    charges = [s.get("battery_pct") for s in clean if s.get("battery_pct") is not None]
    if charges:
        m.battery_start_pct = float(charges[0])
        m.battery_end_pct = float(charges[-1])
    temps = [float(s["battery_c"]) for s in clean if s.get("battery_c") is not None]
    # Some logs report 0 for every sample instead of omitting the field. A
    # flight that never moves off exactly 0.0 is not reporting a temperature,
    # so leave it unknown rather than turning it into a reading. A genuinely
    # freezing flight varies around zero and is kept.
    if temps and any(abs(t) > 1e-9 for t in temps):
        m.battery_temp_max_c = max(temps)
        m.battery_temp_min_c = min(temps)
    elif temps:
        m.flags.append("battery_temp_absent")
    signals = [float(s["signal"]) for s in clean if s.get("signal") is not None]
    if signals:
        m.signal_min = min(signals)
    alts = [float(s["alt_m"]) for s in clean if s.get("alt_m") is not None]
    if alts:
        m.max_altitude_m = max(alts)
    heights = [float(s["height_m"]) for s in clean if s.get("height_m") is not None]
    if m.max_height_m is None and heights:
        m.max_height_m = max(heights)
    speeds = [float(s["speed_mps"]) for s in clean if s.get("speed_mps") is not None]
    if speeds and m.max_h_speed_mps is None:
        m.max_h_speed_mps = max(speeds)

    m.media_photo_events = sum(1 for s in clean if s.get("is_photo"))
    m.media_video_events = sum(1 for s in clean if s.get("is_video"))
    if not speeds:
        m.flags.append("missing_speed")
    if not signals:
        m.flags.append("missing_signal")
    if not alts and not heights:
        m.flags.append("missing_altitude")
    drop_implausible(m)
    m.flags = sorted(set(m.flags))
    return m


def downsample(samples: list[dict[str, Any]], max_points: int = 1400) -> list[dict[str, Any]]:
    clean = [s for s in samples if s.get("t") is not None]
    clean.sort(key=lambda s: float(s["t"]))
    if len(clean) <= max_points:
        return clean
    stride = max(1, math.ceil(len(clean) / max_points))
    out = clean[::stride]
    if out[-1] is not clean[-1]:
        out.append(clean[-1])
    return out


def track_segments(samples: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> list[list[dict]]:
    """Split a track at gaps so the UI never draws across telemetry holes."""
    ordered = [s for s in samples if s.get("t") is not None]
    ordered.sort(key=lambda s: float(s["t"]))
    ordered_gaps = sorted(gaps or [], key=lambda g: float(g.get("start", 0)))
    segments: list[list[dict]] = []
    current: list[dict] = []
    index = 0
    last_t: float | None = None
    for s in ordered:
        t = float(s["t"])
        has_fix = s.get("lat") is not None and s.get("lon") is not None
        if not has_fix:
            if current:
                segments.append(current)
                current = []
            last_t = t
            continue
        if last_t is not None:
            while index < len(ordered_gaps) and float(
                ordered_gaps[index].get("end", ordered_gaps[index].get("start", 0))
            ) <= last_t:
                index += 1
            if index < len(ordered_gaps):
                gap = ordered_gaps[index]
                start = float(gap.get("start", 0))
                end = float(gap.get("end", start))
                if start >= last_t and end <= t:
                    if current:
                        segments.append(current)
                        current = []
                    index += 1
        current.append(s)
        last_t = t
    if current:
        segments.append(current)
    return [seg for seg in segments if seg]
