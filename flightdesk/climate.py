"""Battery temperature limits: has a pack been too hot or too cold?

Flight Desk stores the temperature each flight recorded, but a stored number
only helps if something compares it to a limit. This module holds that
comparison.

**Where the numbers come from.** No temperature limit is published in the DJI
log — the frames carry a reading and nothing else — so any threshold is a local
policy decision, not a fact read from your data. The defaults below are
deliberately conservative and every one of them is editable in Settings. The UI
says so plainly, because a limit presented as the manufacturer's would be a
claim this app cannot support.

Two levels are used, matching the warning severity already used for flight
faults:

* **caution** — outside the comfortable band, worth knowing.
* **warning** — far outside it, the point where performance and pack life suffer.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db

# The app's own defaults. Editable in Settings; never presented as DJI's spec.
DEFAULT_LIMITS: dict[str, Any] = {
    "enabled": True,
    "cold_c": 0.0,      # below this the pack is cold for flight
    "hot_c": 40.0,      # above this the pack is running hot
    "margin_c": 10.0,   # how far past a limit becomes a warning rather than a caution
}

LIMIT_MIN_C = -60.0
LIMIT_MAX_C = 120.0


def limits(conn: sqlite3.Connection) -> dict[str, Any]:
    """The active policy, always complete and always in range."""
    stored = db.get_setting(conn, "battery_temp_limits", None)
    out = dict(DEFAULT_LIMITS)
    if isinstance(stored, dict):
        for key in ("enabled",):
            if key in stored and isinstance(stored[key], bool):
                out[key] = stored[key]
        for key in ("cold_c", "hot_c", "margin_c"):
            if key in stored:
                try:
                    value = float(stored[key])
                except (TypeError, ValueError):
                    continue
                if LIMIT_MIN_C <= value <= LIMIT_MAX_C:
                    out[key] = value
    # A nonsensical pair is not applied: hot must be above cold.
    if out["hot_c"] <= out["cold_c"]:
        out = dict(DEFAULT_LIMITS)
    if out["margin_c"] < 0:
        out["margin_c"] = DEFAULT_LIMITS["margin_c"]
    return out


def save_limits(conn: sqlite3.Connection, incoming: dict[str, Any]) -> dict[str, Any]:
    """Validate and store a new policy. Returns the policy now in force."""
    merged = dict(limits(conn))
    if "enabled" in incoming and isinstance(incoming["enabled"], bool):
        merged["enabled"] = incoming["enabled"]
    for key in ("cold_c", "hot_c", "margin_c"):
        if key in incoming and incoming[key] is not None:
            try:
                value = float(incoming[key])
            except (TypeError, ValueError):
                continue
            if LIMIT_MIN_C <= value <= LIMIT_MAX_C:
                merged[key] = value
    if merged["hot_c"] <= merged["cold_c"]:
        return {"ok": False, "code": "hot_below_cold", **limits(conn)}
    with db.tx(conn):
        db.set_setting(conn, "battery_temp_limits", merged)
    return {"ok": True, "limits": merged}


def classify(high_c: float | None, low_c: float | None,
             policy: dict[str, Any]) -> dict[str, Any]:
    """Judge one flight's recorded extremes against the policy.

    ``unknown`` is a distinct answer from ``ok``: a record with no temperature
    reading has not been shown to be within limits, and must not be reported as
    if it had.
    """
    if not policy.get("enabled", True):
        return {"state": "disabled", "status": "unknown", "reasons": []}
    if high_c is None and low_c is None:
        return {"state": "no_reading", "status": "unknown", "reasons": [],
                "note": "This record carries no battery temperature reading."}
    hot = float(policy["hot_c"])
    cold = float(policy["cold_c"])
    margin = float(policy.get("margin_c", DEFAULT_LIMITS["margin_c"]))
    reasons: list[dict[str, Any]] = []
    status = "ok"
    if high_c is not None and high_c > hot:
        severity = "warning" if high_c >= hot + margin else "caution"
        reasons.append({
            "kind": "hot",
            "value_c": round(float(high_c), 1),
            "limit_c": hot,
            "over_by_c": round(float(high_c) - hot, 1),
            "severity": severity,
            "label": f"Battery reached {high_c:.1f} °C, above the {hot:g} °C limit",
        })
    if low_c is not None and low_c < cold:
        severity = "warning" if low_c <= cold - margin else "caution"
        reasons.append({
            "kind": "cold",
            "value_c": round(float(low_c), 1),
            "limit_c": cold,
            "under_by_c": round(cold - float(low_c), 1),
            "severity": severity,
            "label": f"Battery fell to {low_c:.1f} °C, below the {cold:g} °C limit",
        })
    for reason in reasons:
        if reason["severity"] == "warning":
            status = "warning"
    if status != "warning" and reasons:
        status = "caution"
    return {"state": "judged", "status": status, "reasons": reasons}


def battery_summary(conn: sqlite3.Connection, battery_id: int) -> dict[str, Any]:
    """How one battery has fared against the limits across every flight.

    Only bites when a limit has actually been exceeded, so an ordinary battery
    does not gain a permanent alarm.
    """
    policy = limits(conn)
    rows = db.all_rows(
        conn,
        "SELECT f.id, f.start_time_utc, f.battery_temp_max_c hi, f.battery_temp_min_c lo "
        "FROM flights f WHERE f.battery_equipment_id=? AND f.identity_status='authoritative' "
        "ORDER BY f.start_time_utc DESC",
        (battery_id,),
    )
    judged: list[dict[str, Any]] = []
    hot_flights = cold_flights = 0
    peak: float | None = None
    coldest: float | None = None
    with_reading = 0
    for row in rows:
        if row["hi"] is None and row["lo"] is None:
            continue
        with_reading += 1
        if row["hi"] is not None:
            peak = row["hi"] if peak is None else max(peak, row["hi"])
        if row["lo"] is not None:
            coldest = row["lo"] if coldest is None else min(coldest, row["lo"])
        verdict = classify(row["hi"], row["lo"], policy)
        if verdict["reasons"]:
            hot = any(r["kind"] == "hot" for r in verdict["reasons"])
            cold = any(r["kind"] == "cold" for r in verdict["reasons"])
            hot_flights += int(hot)
            cold_flights += int(cold)
            judged.append({"flight_id": row["id"], "at": row["start_time_utc"],
                           "status": verdict["status"], "reasons": verdict["reasons"]})
    return {
        "limits": policy,
        "flights_with_reading": with_reading,
        "hottest_c": round(peak, 1) if peak is not None else None,
        "coldest_c": round(coldest, 1) if coldest is not None else None,
        "flights_too_hot": hot_flights,
        "flights_too_cold": cold_flights,
        "excursions": judged[:20],
        "excursion_count": len(judged),
    }


def fleet_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every battery that has ever been outside the limits, worst first."""
    policy = limits(conn)
    out: list[dict[str, Any]] = []
    for row in db.all_rows(
        conn,
        "SELECT id, name FROM equipment WHERE kind='battery' AND placeholder=0 "
        "AND archived=0 ORDER BY name",
    ):
        summary = battery_summary(conn, int(row["id"]))
        if summary["flights_too_hot"] or summary["flights_too_cold"]:
            out.append({
                "battery_id": row["id"],
                "name": row["name"],
                "too_hot": summary["flights_too_hot"],
                "too_cold": summary["flights_too_cold"],
                "hottest_c": summary["hottest_c"],
                "coldest_c": summary["coldest_c"],
            })
    out.sort(key=lambda r: (-(r["too_hot"] + r["too_cold"]), r["name"]))
    return {"limits": policy, "batteries": out, "count": len(out)}
