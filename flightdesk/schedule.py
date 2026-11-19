"""Maintenance schedules and their provenance.

Every rule in this app must be able to say where its numbers came from, so
provenance is stored rather than assumed. Two sources are in use:

* **AirData** — the counters the archive was already verified against (Basic 20
  flights / 200 min, Extended 100 / 1000, Full 200 / 2000; batteries 20 charges /
  20 flights / 200 min and 160 / 160 / 1600). These are AirData's own invention,
  not a manufacturer schedule.
* **DoneDoctor** — a published consumer-drone schedule of "every 10–15 flights"
  for cleaning and inspection and "every 2–3 months" for propellers, sensor
  cleaning, IMU/compass calibration and a full battery cycle. This is the source
  of the calendar triggers, which AirData models as "Time-Based Days" but leaves
  disabled.

No manufacturer service interval is applied, because none has been verified for
the aircraft in this archive (Mavic 2, Air 3S, Mini 3 Pro, Mavic 3 Pro). If one is
ever added, it must name its aircraft and its date, the way these two sources do.

AirData's task checklists are reproduced from the owner's own screenshots, so
the wording is the source's, not this app's invention.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db

AIRDATA_SOURCE = "AirData"
DONEDOCTOR_SOURCE = "DoneDoctor"


def _items(*lines: str) -> str:
    return json.dumps([line for line in lines if line], separators=(",", ":"))


# AirData's "Recommended Service Items", from the owner's captures.
AIRCRAFT_SERVICE_ITEMS: dict[str, str] = {
    "basic": _items(
        "Look for cracks in the outer shell, especially around the motors",
        "Check that all screws are in place and tight",
        "Inspect the propellers for broken pieces, bent blades or other damage",
    ),
    "extended": _items(
        "Perform IMU calibration on a completely flat surface",
        "Perform compass calibration",
        "Perform gimbal calibration",
    ),
    "full": _items(
        "Exterior: check every screw, using a torch and small screwdriver",
        "Exterior: check stickers are secure; a loose sticker can obstruct a motor",
        "Exterior: verify the registration label is legible where required",
        "Exterior: with propellers fitted, rotate each one and confirm free movement",
        "Exterior: with propellers off, start the motors and check for vibration or wobble",
        "Sensors and vents: verify all vents are clean and open",
        "Sensors and vents: verify sonar and proximity sensors are clean",
        "Gimbal: check the vibration absorbers for cracks or stiffness",
        "Gimbal: check the retaining pins; at least two must be fitted",
        "Gimbal: inspect the lens for damage",
        "Gimbal: inspect the arm and wiring for damage",
        "Battery contacts: remove the battery and check the gold plates",
        "Battery contacts: clean stains with isopropyl alcohol on a cotton bud",
        "Battery contacts: check the power and data sockets for burning",
    ),
}

BATTERY_SERVICE_ITEMS: dict[str, str] = {
    "basic": _items(
        "Slow cycle: fly until the pack reaches 25-30%",
        "Allow the pack to cool completely to room temperature",
        "Refit and discharge to 8%, or until the pack will not switch on",
        "Never discharge fully to 0% - that can cause permanent damage",
        "Allow to cool completely again, then recharge normally",
    ),
    "full": _items(
        "Exterior: check for swelling or deformity of the outer shell",
        "Exterior: look for cracks in the plastic enclosure",
        "Exterior: verify the grip is firm and the latches return to position",
        "Contacts: check the gold plates and clean any stains with isopropyl alcohol",
        "Contacts: check the power and data sockets for burning or damage",
    ),
}

# DoneDoctor's two intervals, expressed in this app's terms. The upper bound of
# each published range becomes the limit and the lower bound the due-soon point,
# so "10-15 flights" reads as: due at 15, due soon at 10.
DONEDOCTOR_RULES: tuple[dict[str, Any], ...] = (
    {
        "code": "donedoctor_frequent",
        "label": "DoneDoctor · every 10-15 flights",
        "flights_limit": 15,
        "due_soon_pct": 0.667,   # 10 of 15
        "items": (
            "Clean the body, motors and ventilation slots thoroughly",
            "Update firmware",
            "Check landing gear and housings for cracks",
            "Back up footage, then format the microSD card",
            "Test motor operation",
        ),
    },
    {
        "code": "donedoctor_quarterly",
        "label": "DoneDoctor · every 2-3 months",
        "days_limit": 90,        # upper bound of the published range
        "due_soon_pct": 0.667,   # 60 days = the lower bound
        "items": (
            "Replace worn propellers",
            "Deep-clean the sensors",
            "Calibrate the IMU and compass per the app instructions",
            "Run a full charge-discharge cycle and check cell health",
        ),
    },
)


def seed_donedoctor_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add the DoneDoctor calendar and short-interval checks to every drone.

    Additive and idempotent: existing AirData rules are never touched, so the
    counters and due statuses verified against the archive stay exactly as they
    were. Archived equipment is skipped, and placeholders never gain rules.
    """
    now = db.now_utc()
    aircraft = db.all_rows(
        conn,
        "SELECT id, name FROM equipment WHERE kind='aircraft' AND archived=0 "
        "AND placeholder=0 ORDER BY name",
    )
    created = 0
    skipped = 0
    for equipment in aircraft:
        for spec in DONEDOCTOR_RULES:
            existing = db.one(
                conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                (equipment["id"], spec["code"]),
            )
            if existing is not None:
                skipped += 1
                continue
            with db.tx(conn):
                conn.execute(
                    "INSERT INTO rules(equipment_id,code,label,enabled,due_soon_pct,"
                    "flights_limit,air_minutes_limit,charges_limit,days_limit,"
                    "service_items_json,source,created_at,updated_at) "
                    "VALUES(?,?,?,1,?,?,NULL,NULL,?,?,?,?,?)",
                    (
                        equipment["id"], spec["code"], spec["label"],
                        float(spec["due_soon_pct"]), spec.get("flights_limit"),
                        spec.get("days_limit"), _items(*spec["items"]),
                        DONEDOCTOR_SOURCE, now, now,
                    ),
                )
            created += 1
    if created:
        db.audit(conn, "owner", "seed_source_rules", "rules", DONEDOCTOR_SOURCE,
                 {"created": created, "equipment": len(aircraft)})
    return {"ok": True, "created": created, "already_present": skipped,
            "equipment": len(aircraft), "source": DONEDOCTOR_SOURCE}


def service_items(rule: sqlite3.Row) -> list[str]:
    """The checklist attached to a rule, or an empty list."""
    if "service_items_json" not in rule.keys():
        return []
    raw = rule["service_items_json"]
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(i) for i in items] if isinstance(items, list) else []


def rule_source(rule: sqlite3.Row) -> str:
    if "source" in rule.keys() and rule["source"]:
        return str(rule["source"])
    return AIRDATA_SOURCE

def backfill_airdata_items(conn: sqlite3.Connection) -> dict[str, Any]:
    """Attach AirData's own task checklists to the rules seeded from the capture.

    The capture carried counters and statuses but no checklist, so every rule
    created from it has an empty one. The text comes from the owner's AirData
    screenshots, and only fills a gap: a rule that already has items, or that
    came from another source, is left alone.
    """
    updated = 0
    for row in db.all_rows(
        conn,
        "SELECT r.id, r.code, r.service_items_json, e.kind FROM rules r "
        "JOIN equipment e ON e.id=r.equipment_id",
    ):
        if row["service_items_json"]:
            continue
        table = BATTERY_SERVICE_ITEMS if row["kind"] == "battery" else AIRCRAFT_SERVICE_ITEMS
        items = table.get(row["code"])
        if not items:
            continue
        with db.tx(conn):
            conn.execute(
                "UPDATE rules SET service_items_json=?, source=COALESCE(source,?), "
                "updated_at=? WHERE id=?",
                (items, AIRDATA_SOURCE, db.now_utc(), row["id"]),
            )
        updated += 1
    return {"ok": True, "updated": updated}
