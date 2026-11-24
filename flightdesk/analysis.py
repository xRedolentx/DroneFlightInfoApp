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


# Fields that cannot be negative at all. Altitude above sea level is absent from
# this set on purpose: flying below sea level is real (the Dead Sea, Death
# Valley), and -80 m is a reading while -4.9e31 m is not.
NON_NEGATIVE_METRICS = {
    "duration_s",
    "recorder_duration_s",
    "airborne_time_s",
    "total_distance_m",
    "path_length_m",
    "max_home_distance_m",
    "max_height_m",
    "max_h_speed_mps",
}


def drop_implausible(m: "FlightMetrics") -> None:
    """Clear measured values that cannot be a flight reading, and say so.

    Two tests, each earned by a real record:

    * **magnitude** — a misaligned details block yields a 191-hour duration or a
      height of -4.9e31 as readily as a positive one, and `value > limit` let
      every negative through.
    * **sign** — a distance of -1 m is inside every magnitude limit and is still
      not a measurement.
    """
    for field_name, limit in PLAUSIBLE_LIMITS.items():
        value = getattr(m, field_name, None)
        if value is None:
            continue
        try:
            number = float(value)
            implausible = abs(number) > limit
            if field_name in NON_NEGATIVE_METRICS and number < 0:
                implausible = True
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
    battery_temp_dropouts: int = 0
    signal_min: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    home_alt_m: float | None = None
    sample_count: int = 0
    valid_fix_count: int = 0
    gap_count: int = 0
    gaps: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


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

    # Air time means time off the ground, not time with the motors running.
    # Measured against AirData: the log-duration total is 24h21m and the
    # not-on-ground total is 24h11m, against AirData's displayed 24h20m log /
    # 24h10m air. Counting motor-on instead gave 24h19m, which is nearly the
    # log duration and made "air time" and "log time" the same number twice.
    # Older logs that never report the flag fall back to the motor-or-height
    # rule and say so.
    ground_flag_present = any(s.get("on_ground") is not None for s in clean)
    motor_flag_present = any(s.get("motor_on") is not None for s in clean)
    height_present = any(isinstance(s.get("height_m"), (int, float)) for s in clean)
    airborne_known = ground_flag_present or motor_flag_present or height_present
    airborne_unknown_dt = 0.0
    if not ground_flag_present:
        m.flags.append("airborne_from_motor" if (motor_flag_present or height_present)
                       else "airborne_unknown")

    def airborne_state(sample: dict[str, Any]) -> bool | None:
        """Off-ground state of one sample: True, False, or unknown.

        The interval is attributed to the state at its end. A single interval
        that spans take-off or landing is therefore counted with the later
        state: at most one sample interval of ambiguity per transition, which
        on real logs is about a second, and which the archive-wide calibration
        against AirData (24h10m vs 24h10m) absorbed.

        A missing flag is unknown, not ground and not air. The fallback order is
        the explicit ground flag, then the motor flag, then height above take-off.
        """
        if sample.get("on_ground") is not None:
            return sample.get("on_ground") is False
        if sample.get("motor_on") is not None:
            return bool(sample.get("motor_on"))
        height = sample.get("height_m")
        if isinstance(height, (int, float)) and math.isfinite(float(height)):
            return float(height) > 0.5
        return None

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
            state = airborne_state(s)
            if state is True:
                airborne_s += dt
            elif state is None:
                airborne_unknown_dt += dt

        if has_fix:
            prev_valid = s
        prev = s

    if gap_open is not None:
        close_gap(float(clean[-1]["t"]))
    m.path_length_m = round(path_m, 2) if m.valid_fix_count > 1 else None
    m.max_home_distance_m = round(max_home_m, 2) if m.valid_fix_count else None
    # Air time comes from the ground/motor/height evidence, not from the GPS
    # route: a flight can have valid timing with no fix at all. When no sample
    # carried any of that evidence the air time is unknown, not zero.
    if airborne_known and len(clean) > 1:
        m.airborne_time_s = round(airborne_s, 3)
        if airborne_unknown_dt > 0:
            m.flags.append("airborne_partial")
    else:
        m.airborne_time_s = None
        m.flags.append("airborne_unknown")
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
    #
    # A single 0.0 among real readings is a different problem: one real aircraft
    # (an older Mavic) drops the temperature field to 0 for a few frames while
    # the rest of the flight reads 35-53 C. Taking that as a minimum would
    # report a freezing battery that never happened, so zero readings far from
    # the flight's own median are treated as dropouts.
    non_zero = [t for t in temps if abs(t) > 1e-9]
    if non_zero:
        centre = _median(non_zero)
        if len(non_zero) >= 3 and abs(centre) > 15.0 and len(non_zero) < len(temps):
            dropped = len(temps) - len(non_zero)
            m.flags.append("battery_temp_dropouts")
            m.battery_temp_dropouts = dropped
            temps = non_zero
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

    if not speeds:
        m.flags.append("missing_speed")
    if not signals:
        m.flags.append("missing_signal")
    if not alts and not heights:
        m.flags.append("missing_altitude")
    drop_implausible(m)
    m.flags = sorted(set(m.flags))
    return m


def _warning_label(code: str, value: Any) -> tuple[str, str]:
    """Human label and severity for one warning code."""
    from .parser_adapter import FAULT_FLAGS

    for key, label, severity in FAULT_FLAGS:
        if code == key:
            return label, severity
    if code == "voltageWarning":
        return f"Battery voltage warning (level {value})", "warning"
    if code == "gimbalStuck":
        return "Gimbal stuck", "caution"
    if code == "sdCard":
        return f"SD card problem ({value})", "caution"
    if code == "motorStartFailed":
        return f"Motor start failed ({value})", "warning"
    return code, "caution"


def summarise_warnings(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn per-sample fault flags into one row per fault occurrence.

    A flag that stays true across many frames is one event, not hundreds: the
    rows report when it started, when it stopped, and how long it lasted, so a
    pilot reads "voltage warning, 14 s, at 03:12" rather than a frame count.
    """
    clean = [s for s in samples if s.get("t") is not None]
    clean.sort(key=lambda s: float(s["t"]))
    open_runs: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    def close(code: str) -> None:
        """Finish a run at the last sample that actually raised it.

        Not at the first clean sample: that would claim the fault was present
        for one interval longer than it was observed.
        """
        run = open_runs.pop(code, None)
        if run is None:
            return
        run["seconds"] = round(max(0.0, float(run["end_t"]) - float(run["start_t"])), 3)
        rows.append(run)

    for sample in clean:
        t = float(sample["t"])
        active = sample.get("warn") or {}
        if not isinstance(active, dict):
            active = {}
        for code in list(open_runs):
            if code not in active:
                close(code)
        for code, value in active.items():
            if code in open_runs:
                run = open_runs[code]
                run["end_t"] = round(t, 3)
                run["frames"] = int(run["frames"]) + 1
                if run.get("peak") is None and isinstance(value, (int, float)):
                    run["peak"] = value
                continue
            label, severity = _warning_label(code, value)
            open_runs[code] = {
                "code": code,
                "label": label,
                "severity": severity,
                "start_t": round(t, 3),
                "end_t": round(t, 3),
                "frames": 1,
                "peak": value if isinstance(value, (int, float)) else None,
                "value": None if isinstance(value, (int, float)) else value,
            }
    if clean:
        # Anything still true at the end of the record lasted to the last sample.
        for code in list(open_runs):
            close(code)
    rows.sort(key=lambda r: (r["start_t"], r["code"]))
    for row in rows:
        if row.get("peak") is None:
            row.pop("peak", None)
        # A single-frame compass flag at the very start of a flight is the known
        # DJI start-up quirk, not interference. Measured on the real archive: 22
        # of 33 compass flags fire at t=0, every one of them lasts a single
        # frame, and they are spread across four different aircraft. Kept and
        # labelled rather than discarded, but not counted as a fault.
        if (row["code"] == "isCompassError" and int(row.get("frames") or 0) <= 1
                and float(row.get("start_t") or 0.0) < 30.0):
            row["severity"] = "startup"
            row["label"] = "Compass flagged at start-up (transient, single frame)"
            row["counted"] = False
        else:
            row["counted"] = True
    return rows


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
