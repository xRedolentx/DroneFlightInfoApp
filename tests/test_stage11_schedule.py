"""Maintenance schedules: the calendar trigger, provenance and task lists.

AirData's counter rules stay exactly as they were — they are the baseline the
archive was verified against. What is added is what the owner's screenshots
showed AirData itself models but leaves disabled: a "Time-Based Days" trigger,
and a per-rule task checklist. The calendar intervals come from DoneDoctor.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from support import TempCase

from flightdesk import maintenance, schedule
from flightdesk import db


def _equipment(conn, name: str = "Drone A", kind: str = "aircraft",
               archived: int = 0, placeholder: int = 0) -> int:
    now = db.now_utc()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO equipment(kind,name,archived,placeholder,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (kind, name, archived, placeholder, now, now),
        )
    return int(cur.lastrowid)


def _rule(conn, equipment_id: int, code: str, *, days=None, flights=None,
          minutes=None, charges=None, due_soon=0.667, created_at=None,
          source=None, items=None) -> int:
    now = db.now_utc()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO rules(equipment_id,code,label,enabled,due_soon_pct,"
            "flights_limit,air_minutes_limit,charges_limit,days_limit,"
            "service_items_json,source,created_at,updated_at) "
            "VALUES(?,?,?,1,?,?,?,?,?,?,?,?,?)",
            (equipment_id, code, code.title(), due_soon, flights, minutes, charges,
             days, items, source, created_at or now, now),
        )
    return int(cur.lastrowid)


def _age_days(conn, days: int) -> str:
    """A timestamp that many days ago, as the schema stores it."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


class CalendarTriggerTest(TempCase):
    def _status(self, days_old: int, limit: int = 90):
        eq = _equipment(self.conn)
        rid = _rule(self.conn, eq, "quarterly", days=limit,
                    created_at=_age_days(self.conn, days_old))
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        return maintenance.rule_status(self.conn, rule)

    def test_a_fresh_rule_is_not_due(self) -> None:
        status = self._status(10)
        days = [c for c in status["counters"] if c["metric"] == "days"][0]
        self.assertEqual(days["status"], "ok")
        self.assertEqual(status["status"], "ok")

    def test_due_soon_at_the_lower_bound_of_the_range(self) -> None:
        # "every 2-3 months" => due soon from 60 days.
        status = self._status(62)
        self.assertEqual(status["status"], "due_soon")
        self.assertEqual(status["fired_metric"], "days")

    def test_due_at_the_limit(self) -> None:
        status = self._status(91)
        self.assertEqual(status["status"], "due")
        self.assertEqual(status["fired_metric"], "days")

    def test_the_day_counter_reports_its_own_basis(self) -> None:
        status = self._status(30)
        days = [c for c in status["counters"] if c["metric"] == "days"][0]
        self.assertEqual(days["limit"], 90)
        self.assertIn(days["basis"], ("rule_created", "no_baseline", "service", "capture"))

    def test_a_rule_without_a_calendar_limit_has_no_day_counter(self) -> None:
        eq = _equipment(self.conn)
        rid = _rule(self.conn, eq, "basic", flights=20, minutes=200)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        self.assertFalse([c for c in status["counters"] if c["metric"] == "days"])

    def test_completing_a_service_resets_the_clock(self) -> None:
        eq = _equipment(self.conn)
        rid = _rule(self.conn, eq, "quarterly", days=90,
                    created_at=_age_days(self.conn, 95))
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        self.assertEqual(maintenance.rule_status(self.conn, rule)["status"], "due")
        maintenance.complete_service(self.conn, eq, rid, notes="done")
        after = maintenance.rule_status(self.conn, db.one(
            self.conn, "SELECT * FROM rules WHERE id=?", (rid,)))
        days = [c for c in after["counters"] if c["metric"] == "days"][0]
        self.assertLessEqual(days["value"], 1)
        self.assertEqual(after["status"], "ok")

    def test_both_triggers_can_fire_and_the_worst_wins(self) -> None:
        eq = _equipment(self.conn)
        rid = _rule(self.conn, eq, "mixed", days=90, flights=15,
                    created_at=_age_days(self.conn, 95))
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        metrics = {c["metric"]: c["status"] for c in status["counters"]}
        self.assertEqual(metrics["days"], "due")
        self.assertEqual(status["status"], "due")


class SeedingTest(TempCase):
    def test_donedoctor_rules_are_added_to_each_drone(self) -> None:
        _equipment(self.conn, "Drone A")
        _equipment(self.conn, "Drone B")
        result = schedule.seed_donedoctor_rules(self.conn)
        self.assertEqual(result["created"], 4)  # two rules x two drones
        rows = db.all_rows(self.conn, "SELECT code FROM rules ORDER BY code")
        codes = [r["code"] for r in rows]
        self.assertEqual(codes.count("donedoctor_frequent"), 2)
        self.assertEqual(codes.count("donedoctor_quarterly"), 2)

    def test_seeding_twice_adds_nothing(self) -> None:
        _equipment(self.conn, "Drone A")
        schedule.seed_donedoctor_rules(self.conn)
        second = schedule.seed_donedoctor_rules(self.conn)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["already_present"], 2)

    def test_archived_and_placeholder_equipment_get_no_rules(self) -> None:
        _equipment(self.conn, "Retired", archived=1)
        _equipment(self.conn, "Unreadable", placeholder=1)
        result = schedule.seed_donedoctor_rules(self.conn)
        self.assertEqual(result["created"], 0)

    def test_batteries_are_not_given_drone_rules(self) -> None:
        _equipment(self.conn, "Bat A", kind="battery")
        self.assertEqual(schedule.seed_donedoctor_rules(self.conn)["created"], 0)

    def test_seeding_preserves_existing_airdata_counters(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "basic", flights=20, minutes=200, due_soon=0.8,
                    source="AirData")
        schedule.seed_donedoctor_rules(self.conn)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        self.assertEqual(rule["flights_limit"], 20)
        self.assertEqual(rule["air_minutes_limit"], 200)
        self.assertAlmostEqual(rule["due_soon_pct"], 0.8)
        self.assertEqual(rule["source"], "AirData")

    def test_the_published_ranges_become_limit_and_due_soon(self) -> None:
        # "10-15 flights": due at 15, due soon at 10 (67%).
        frequent = [s for s in schedule.DONEDOCTOR_RULES
                    if s["code"] == "donedoctor_frequent"][0]
        self.assertEqual(frequent["flights_limit"], 15)
        self.assertAlmostEqual(frequent["due_soon_pct"], 10 / 15, places=2)
        # "2-3 months": due at 90 days, due soon at 60.
        quarterly = [s for s in schedule.DONEDOCTOR_RULES
                     if s["code"] == "donedoctor_quarterly"][0]
        self.assertEqual(quarterly["days_limit"], 90)
        self.assertAlmostEqual(quarterly["due_soon_pct"], 60 / 90, places=2)

    def test_every_donedoctor_rule_carries_its_tasks(self) -> None:
        for spec in schedule.DONEDOCTOR_RULES:
            self.assertTrue(spec["items"], spec["code"])
        self.assertIn("format the microSD card",
                      " ".join(schedule.DONEDOCTOR_RULES[0]["items"]))
        self.assertIn("Calibrate the IMU and compass",
                      " ".join(schedule.DONEDOCTOR_RULES[1]["items"]))


class SourceGuardTest(unittest.TestCase):
    """Only two schedule sources are sanctioned.

    The owner rejected a manufacturer document that described a different class
    of aircraft, so the intervals in use must stay exactly the two verified
    sources. This fails if a third one is quietly introduced.
    """

    def test_only_the_two_sources_are_used(self) -> None:
        self.assertEqual(schedule.AIRDATA_SOURCE, "AirData")
        self.assertEqual(schedule.DONEDOCTOR_SOURCE, "DoneDoctor")
        codes = {spec["code"] for spec in schedule.DONEDOCTOR_RULES}
        self.assertEqual(codes, {"donedoctor_frequent", "donedoctor_quarterly"})

    def test_no_rule_carries_a_multi_year_or_engine_hour_interval(self) -> None:
        # Intervals from commercial heavy-lift documentation have no place here.
        for spec in schedule.DONEDOCTOR_RULES:
            self.assertLessEqual(spec.get("days_limit", 0), 365, spec["code"])
            self.assertLessEqual(spec.get("flights_limit", 0), 50, spec["code"])
            self.assertNotIn("hours", json.dumps(spec).lower())


class RuleEditingTest(TempCase):
    """The owner can retune a schedule from the screen, and cannot wreck it."""

    def _rule(self, **kw):
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "basic", flights=20, minutes=200, **kw)
        return eq, rid

    def test_status_exposes_the_thresholds_so_a_form_can_show_them(self) -> None:
        eq, rid = self._rule()
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        self.assertEqual(status["flights_limit"], 20)
        self.assertEqual(status["air_minutes_limit"], 200)
        self.assertIsNone(status["charges_limit"])
        self.assertIsNone(status["days_limit"])

    def test_a_limit_can_be_lowered(self) -> None:
        eq, rid = self._rule()
        result = maintenance.set_rule_policy(self.conn, rid, flights_limit=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(db.one(self.conn, "SELECT flights_limit f FROM rules WHERE id=?",
                                (rid,))["f"], 10)

    def test_a_trigger_can_be_switched_off_with_null(self) -> None:
        eq, rid = self._rule()
        result = maintenance.set_rule_policy(self.conn, rid, air_minutes_limit=None)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(db.one(self.conn, "SELECT air_minutes_limit m FROM rules WHERE id=?",
                                 (rid,))["m"])
        # The other trigger survives.
        self.assertEqual(db.one(self.conn, "SELECT flights_limit f FROM rules WHERE id=?",
                                (rid,))["f"], 20)

    def test_the_last_trigger_cannot_be_removed(self) -> None:
        eq, rid = self._rule()
        maintenance.set_rule_policy(self.conn, rid, air_minutes_limit=None)
        result = maintenance.set_rule_policy(self.conn, rid, flights_limit=None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "no_triggers")

    def test_nonsense_values_are_refused_not_stored(self) -> None:
        eq, rid = self._rule()
        for bad in ("abc", -5, 0, float("inf")):
            result = maintenance.set_rule_policy(self.conn, rid, flights_limit=bad)
            self.assertFalse(result["ok"], bad)
            self.assertEqual(result["code"], "invalid_value")
        self.assertEqual(db.one(self.conn, "SELECT flights_limit f FROM rules WHERE id=?",
                                (rid,))["f"], 20)

    def test_due_soon_percentage_is_bounded(self) -> None:
        eq, rid = self._rule()
        self.assertFalse(maintenance.set_rule_policy(
            self.conn, rid, due_soon_pct=1.5)["ok"])
        self.assertTrue(maintenance.set_rule_policy(
            self.conn, rid, due_soon_pct=0.5)["ok"])
        self.assertAlmostEqual(
            db.one(self.conn, "SELECT due_soon_pct p FROM rules WHERE id=?", (rid,))["p"],
            0.5)

    def test_a_counter_can_be_given_a_calendar_trigger(self) -> None:
        eq, rid = self._rule()
        result = maintenance.set_rule_policy(self.conn, rid, days_limit=90)
        self.assertTrue(result["ok"], result)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        self.assertTrue([c for c in status["counters"] if c["metric"] == "days"])

    def test_editing_limits_does_not_touch_history(self) -> None:
        eq, rid = self._rule()
        maintenance.complete_service(self.conn, eq, rid, notes="done")
        before = db.one(self.conn, "SELECT COUNT(*) n FROM services")["n"]
        maintenance.set_rule_policy(self.conn, rid, flights_limit=12)
        self.assertEqual(db.one(self.conn, "SELECT COUNT(*) n FROM services")["n"], before)

    def test_an_edit_is_audited(self) -> None:
        eq, rid = self._rule()
        maintenance.set_rule_policy(self.conn, rid, flights_limit=12)
        row = db.one(self.conn, "SELECT * FROM audit_log WHERE action='rule_policy' "
                                "ORDER BY rowid DESC LIMIT 1")
        self.assertIsNotNone(row)


class BackdateTest(TempCase):
    """Recording work that was already done."""

    def test_a_past_service_sets_a_past_baseline(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "quarterly", days=90)
        maintenance.complete_service(self.conn, eq, rid, notes="props replaced",
                                     occurred_at="2025-08-15")
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        days = [c for c in status["counters"] if c["metric"] == "days"][0]
        # More than a year later, a 90-day check must read as due.
        self.assertGreater(days["value"], 90)
        self.assertEqual(status["status"], "due")

    def test_a_backdated_service_is_distinguishable_in_history(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "quarterly", days=90)
        maintenance.complete_service(self.conn, eq, rid, notes="props replaced",
                                     occurred_at="2025-08-15")
        row = db.one(self.conn, "SELECT * FROM services ORDER BY id DESC LIMIT 1")
        self.assertEqual(row["occurred_at"], "2025-08-15")
        self.assertEqual(row["source"], "owner")

    def test_a_recent_backdate_leaves_a_rule_not_due(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "quarterly", days=90)
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat()
        maintenance.complete_service(self.conn, eq, rid, occurred_at=recent)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        self.assertEqual(maintenance.rule_status(self.conn, rule)["status"], "ok")


class ChecklistTest(TempCase):
    def test_airdata_lists_are_attached_without_overwriting(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        basic = _rule(self.conn, eq, "basic", flights=20, minutes=200)
        keep = _rule(self.conn, eq, "extended", flights=100, minutes=1000,
                     items=json.dumps(["owner's own wording"]))
        result = schedule.backfill_airdata_items(self.conn)
        self.assertGreaterEqual(result["updated"], 1)
        filled = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (basic,))
        self.assertTrue(schedule.service_items(filled))
        untouched = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (keep,))
        self.assertEqual(schedule.service_items(untouched), ["owner's own wording"])

    def test_items_reach_the_status_payload(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "basic", flights=20, minutes=200,
                    items=json.dumps(["check screws", "check props"]), source="AirData")
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        status = maintenance.rule_status(self.conn, rule)
        self.assertEqual(status["service_items"], ["check screws", "check props"])
        self.assertEqual(status["source"], "AirData")

    def test_source_defaults_to_airdata_for_old_rows(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "basic", flights=20)
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        self.assertEqual(schedule.rule_source(rule), "AirData")

    def test_broken_json_does_not_crash_a_rule(self) -> None:
        eq = _equipment(self.conn, "Drone A")
        rid = _rule(self.conn, eq, "basic", flights=20, items="{not json")
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (rid,))
        self.assertEqual(schedule.service_items(rule), [])


if __name__ == "__main__":
    unittest.main()
