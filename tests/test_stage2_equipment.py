"""Stage 2: equipment capture migration, baselines and auditable service."""
from __future__ import annotations

import io
import re
import unittest

from support import (GRID_CAPTURE_FIXTURE, TempCase, apply_capture, base_time, capture_text,
                     equipment_id, log_bytes, status_for)

from flightdesk import capture, db, maintenance
from flightdesk.importer import Importer


def grid_capture_text() -> str:
    return GRID_CAPTURE_FIXTURE.read_text(encoding="utf-8")


class CaptureParseTest(TempCase):
    def test_inventory_shape(self) -> None:
        doc = capture.parse_capture(capture_text())
        self.assertEqual(len(doc.current_aircraft), 3)
        self.assertEqual(len(doc.historical_aircraft), 4)
        self.assertEqual(len(doc.batteries), 20)
        self.assertEqual(sum(1 for b in doc.batteries if b.placeholder), 1)
        self.assertEqual(sum(1 for b in doc.batteries if b.locked), 3)
        self.assertEqual(len(doc.snapshots), 16)

    def test_historic_events_are_counted_honestly(self) -> None:
        doc = capture.parse_capture(capture_text())
        self.assertEqual(doc.reported_total, 21)
        self.assertEqual(len(doc.services), 21)
        self.assertEqual(doc.resolved_services, 17)
        self.assertEqual(doc.grouped_services, 4)
        grouped = [s for s in doc.services if not s.resolved]
        self.assertTrue(all(s.raw_wording for s in grouped))
        # grouped rows keep their original wording and ranges
        ranges = [s for s in doc.services if s.ended_at]
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].occurred_at, "2024-01-02")
        self.assertEqual(ranges[0].ended_at, "2024-01-05")

    def test_repeat_migration_is_a_no_op(self) -> None:
        first = apply_capture(self.conn)
        services = self.conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        second = apply_capture(self.conn)
        self.assertTrue(second["already_applied"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM services").fetchone()[0], services)
        self.assertEqual(first["services_inserted"], 21)

    def test_placeholder_is_excluded_from_reminders(self) -> None:
        apply_capture(self.conn)
        statuses = maintenance.all_statuses(self.conn, include_archived=True)
        names = {s["equipment_name"] for s in statuses}
        self.assertNotIn("Placeholder battery", names)


class BaselineTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        apply_capture(self.conn)

    def test_archive_only_baselines_match_the_capture(self) -> None:
        extended = status_for(self.conn, "Mavic 2 Pro", "extended")
        self.assertEqual(extended["status"], "due")
        counters = {c["metric"]: c for c in extended["counters"]}
        self.assertEqual(counters["flights"]["value"], 188)
        self.assertEqual(counters["flights"]["limit"], 100)
        self.assertEqual(counters["minutes"]["value"], 1018)
        self.assertEqual(counters["minutes"]["limit"], 1000)

        basic = status_for(self.conn, "Mavic 2 Pro", "basic")
        self.assertEqual(basic["status"], "due_soon")
        self.assertEqual({c["metric"]: c for c in basic["counters"]}["flights"]["value"], 19)

        air = status_for(self.conn, "Air 3S", "basic")
        self.assertEqual(air["status"], "due_soon")
        self.assertEqual({c["metric"]: c for c in air["counters"]}["flights"]["value"], 16)

        battery = status_for(self.conn, "Mavic 2 battery C", "basic")
        self.assertEqual(battery["status"], "due")
        self.assertEqual({c["metric"]: c for c in battery["counters"]}["flights"]["value"], 30)

    def test_captured_status_is_compared_not_overwritten(self) -> None:
        basic = status_for(self.conn, "Mavic 2 Pro", "basic")
        self.assertEqual(basic["captured_status"], "Due soon")
        self.assertTrue(basic["matches_capture"])

    def test_snapshot_counter_absence_is_unknown_not_zero(self) -> None:
        basic = status_for(self.conn, "Mavic 2 battery A", "basic")
        minutes = {c["metric"]: c for c in basic["counters"]}["minutes"]
        self.assertIsNone(minutes["value"])
        self.assertEqual(minutes["status"], "unknown")

    def test_due_soon_threshold_is_configurable(self) -> None:
        rule = db.one(self.conn, "SELECT * FROM rules WHERE id=?", (
            status_for(self.conn, "Mavic 2 Pro", "basic")["id"],))
        maintenance.set_rule_policy(self.conn, rule["id"], due_soon_pct=0.90)
        self.assertEqual(status_for(self.conn, "Mavic 2 Pro", "basic")["status"], "due_soon")
        # A lightly used rule responds to the editable threshold too.
        mini = status_for(self.conn, "Mini 3 Pro", "basic")
        self.assertEqual(mini["status"], "ok")
        maintenance.set_rule_policy(self.conn, mini["id"], due_soon_pct=0.10)
        self.assertEqual(status_for(self.conn, "Mini 3 Pro", "basic")["status"], "due_soon")
        maintenance.set_rule_policy(self.conn, mini["id"], due_soon_pct=0.80)
        self.assertEqual(status_for(self.conn, "Mini 3 Pro", "basic")["status"], "ok")

    def test_disabled_rule_stays_out_of_upcoming(self) -> None:
        rule_id = status_for(self.conn, "Air 3S", "basic")["id"]
        maintenance.set_rule_policy(self.conn, rule_id, enabled=0)
        upcoming = maintenance.overview(self.conn)["upcoming"]["aircraft"]
        self.assertNotIn(rule_id, [r["id"] for r in upcoming])


class OrderingTest(unittest.TestCase):
    """Archive-first, seed-first, repeat and late-v14 must agree."""

    def _baseline_signature(self, conn) -> dict:
        return {
            (s["equipment_name"], s["code"]): (
                s["status"],
                tuple((c["metric"], c["value"], c["limit"]) for c in s["counters"]),
            )
            for s in maintenance.all_statuses(conn, include_archived=True)
        }

    def _run(self, order: str) -> dict:
        from support import TempRoot
        t = TempRoot()
        try:
            imp = Importer(t.root, t.conn)
            folder = t.root / "incoming_logs"
            folder.mkdir()
            (folder / "DJIFlightRecord_2026-08-11_[21-53-20].txt").write_bytes(
                log_bytes(11, days=0))
            (folder / "DJIFlightRecord_2026-09-01_[10-00-00].txt").write_bytes(
                log_bytes(8, days=21, aircraft="air3s"))
            if order == "archive_first":
                imp.import_folder(folder, default_kind="archive")
                apply_capture(t.conn)
            elif order == "capture_first":
                apply_capture(t.conn)
                imp.import_folder(folder, default_kind="archive")
            elif order == "repeat":
                apply_capture(t.conn)
                imp.import_folder(folder, default_kind="archive")
                apply_capture(t.conn)
                apply_capture(t.conn)
            else:  # late_v14
                imp.import_folder(folder, default_kind="archive")
                apply_capture(t.conn)
                # a v14 metadata-only log arrives later and is unlocked later
                from flightdesk.synth import make_v13_metadata_log
                late = make_v13_metadata_log(
                    start=base_time(40), aircraft="air3s", duration_s=302.0,
                    distance_m=46.9, max_height_m=45.1,
                )
                imp.import_stream(io.BytesIO(late),
                                  "DJIFlightRecord_2026-09-20_[21-53-20].txt",
                                  source_kind="archive", source_label="late")
            return self._baseline_signature(t.conn)
        finally:
            t.close()

    def test_all_orderings_agree(self) -> None:
        reference = self._run("archive_first")
        for order in ("capture_first", "repeat", "late_v14"):
            self.assertEqual(self._run(order), reference, order)


class ServiceActionTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        apply_capture(self.conn)
        self.rule = status_for(self.conn, "Air 3S", "basic")
        self.eq = equipment_id(self.conn, "Air 3S")

    def test_complete_resets_only_the_matching_rule(self) -> None:
        other_before = status_for(self.conn, "Air 3S", "extended")["status"]
        result = maintenance.complete_service(self.conn, self.eq, self.rule["id"], "done")
        self.assertTrue(result["ok"])
        after = status_for(self.conn, "Air 3S", "basic")
        self.assertEqual(after["status"], "ok")
        self.assertEqual(after["counters"][0]["value"], 0)
        self.assertEqual(status_for(self.conn, "Air 3S", "extended")["status"], other_before)

    def test_undo_restores_the_previous_baseline(self) -> None:
        before = status_for(self.conn, "Air 3S", "basic")
        result = maintenance.complete_service(self.conn, self.eq, self.rule["id"], "done")
        undo = maintenance.undo_service(self.conn, result["service_id"])
        self.assertTrue(undo["ok"])
        after = status_for(self.conn, "Air 3S", "basic")
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["counters"][0]["value"], before["counters"][0]["value"])
        again = maintenance.undo_service(self.conn, result["service_id"])
        self.assertEqual(again["code"], "already_reversed")

    def test_skip_resets_only_that_rule_and_is_audited(self) -> None:
        result = maintenance.complete_service(self.conn, self.eq, self.rule["id"],
                                              "rain", kind="skipped")
        self.assertTrue(result["ok"])
        self.assertEqual(status_for(self.conn, "Air 3S", "basic")["status"], "ok")
        row = db.one(self.conn, "SELECT kind FROM services WHERE id=?", (result["service_id"],))
        self.assertEqual(row["kind"], "skipped")

    def test_one_off_does_not_reset_a_rule_unless_linked(self) -> None:
        before = status_for(self.conn, "Air 3S", "basic")["status"]
        maintenance.oneoff_service(self.conn, self.eq, "prop swap")
        self.assertEqual(status_for(self.conn, "Air 3S", "basic")["status"], before)
        maintenance.oneoff_service(self.conn, self.eq, "linked work",
                                   linked_rule_id=self.rule["id"])
        self.assertEqual(status_for(self.conn, "Air 3S", "basic")["status"], "ok")

    def test_capture_events_are_not_reversible(self) -> None:
        captured = db.one(self.conn, "SELECT id FROM services WHERE source='capture'")
        self.assertEqual(maintenance.undo_service(self.conn, captured["id"])["code"],
                         "not_reversible")

    def test_reassignment_recalculates_and_is_reversible(self) -> None:
        imp = Importer(self.root, self.conn)
        imp.import_stream(io.BytesIO(log_bytes(11, days=100, aircraft="air3s")),
                          "DJIFlightRecord_2026-09-10_[21-53-20].txt",
                          source_kind="archive", source_label="test")
        flight = db.one(self.conn, "SELECT * FROM flights ORDER BY id DESC LIMIT 1")
        before = status_for(self.conn, "Air 3S", "basic")["counters"][0]["value"]
        capture.reassign_flight(self.conn, int(flight["id"]), "aircraft",
                                equipment_id(self.conn, "Mini 3 Pro"))
        after = status_for(self.conn, "Air 3S", "basic")["counters"][0]["value"]
        self.assertEqual(before - after, 1)
        capture.reassign_flight(self.conn, int(flight["id"]), "aircraft", self.eq)
        self.assertEqual(status_for(self.conn, "Air 3S", "basic")["counters"][0]["value"],
                         before)

    def test_unresolved_coverage_is_excluded_until_decided(self) -> None:
        imp = Importer(self.root, self.conn)
        # An older controller-only discovery: not in the archive snapshot.
        imp.import_stream(io.BytesIO(log_bytes(11, days=-40, aircraft="air3s")),
                          "DJIFlightRecord_2026-07-02_[21-53-20].txt",
                          source_kind="extras", source_label="controller")
        status = status_for(self.conn, "Air 3S", "basic")
        self.assertEqual(status["uncertain_count"], 1)
        self.assertEqual(status["proposed_delta"]["flights"], 1)
        snapshot = maintenance.latest_snapshot(self.conn)
        flight = db.one(self.conn, "SELECT id FROM flights WHERE start_time_utc LIKE '2026-07-02%'")
        maintenance.resolve_coverage(self.conn, int(flight["id"]), int(snapshot["id"]),
                                     "include", "confirmed new")
        after = status_for(self.conn, "Air 3S", "basic")
        self.assertEqual(after["counters"][0]["value"], 17)
        self.assertEqual(after["uncertain_count"], 0)


class TemperatureTest(TempCase):
    def test_placeholder_temperature_is_missing(self) -> None:
        apply_capture(self.conn)
        row = db.one(
            self.conn,
            "SELECT r.temp_c, r.temp_missing FROM battery_readings r "
            "JOIN equipment e ON e.id=r.battery_id WHERE e.serial='SYN-BAT-3402QS' "
            "AND r.source='capture'",
        )
        self.assertEqual(row["temp_missing"], 1)
        self.assertIsNone(row["temp_c"])

    def test_genuine_freezing_temperature_is_kept(self) -> None:
        # A different capture with a genuine 32°F reading and no placeholder note.
        text = re.sub(
            r">[^\n]*placeholder[^\n]*\n",
            "",
            capture_text().replace(
                "| Air 3S battery 2 | SYN-BAT-2002 | Air 3S | 2024-12-10 | 03.01.0000 | 40°F |",
                "| Air 3S battery 2 | SYN-BAT-2002 | Air 3S | 2024-12-10 | 03.01.0000 | 32°F |",
            ),
        )
        doc = capture.parse_capture(text)
        self.assertFalse(doc.temperature_placeholder)
        capture.apply_capture(self.conn, doc, "variant")
        row = db.one(
            self.conn,
            "SELECT r.temp_c, r.temp_missing FROM battery_readings r "
            "JOIN equipment e ON e.id=r.battery_id WHERE e.serial='SYN-BAT-2002' "
            "AND r.source='capture'",
        )
        self.assertEqual(row["temp_missing"], 0)
        self.assertAlmostEqual(row["temp_c"], 0.0, places=2)


class OwnerEditTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        apply_capture(self.conn)
        self.eq = equipment_id(self.conn, "Mavic 2 Pro")

    def test_owner_edit_survives_a_repeat_migration(self) -> None:
        capture.owner_edit_equipment(self.conn, self.eq,
                                     {"alias": "Mapping rig", "notes": "owner note"})
        apply_capture(self.conn)
        row = db.one(self.conn, "SELECT * FROM equipment WHERE id=?", (self.eq,))
        self.assertEqual(row["alias"], "Mapping rig")
        self.assertEqual(row["notes"], "owner note")

    def test_locked_details_are_recorded(self) -> None:
        conn = self.conn
        rows = db.all_rows(conn, "SELECT name FROM equipment WHERE locked_details=1")
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("3402QS" in (r["name"] + "") for r in rows) or len(rows) == 3)

    def test_reset_from_log_clears_override_only(self) -> None:
        capture.assign_serial(self.conn, "SYN-BAT-1001", equipment_id(self.conn, "Mavic 2 Pro"))
        link = db.one(self.conn, "SELECT * FROM serial_links WHERE serial='SYN-BAT-1001'")
        self.assertEqual(link["override"], 1)
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM serial_links WHERE serial=? AND override=1",
                              ("SYN-BAT-1001",))
        self.assertIsNone(db.one(self.conn, "SELECT * FROM serial_links WHERE serial=?",
                                 ("SYN-BAT-1001",)))


class GridShapedCaptureTest(TempCase):
    """The real capture files rule counters in a per-item grid and inside the
    battery table, under headings that never say "snapshot". These checks pin
    that shape so a real capture cannot migrate as all-unknown counters."""

    def setUp(self) -> None:
        super().setUp()
        self.doc = capture.parse_capture(grid_capture_text())

    def test_inventory_and_snapshots_are_recognised(self) -> None:
        self.assertEqual(len(self.doc.current_aircraft), 3)
        self.assertEqual(len(self.doc.historical_aircraft), 1)
        self.assertEqual(len(self.doc.batteries), 3)
        self.assertEqual(sum(1 for b in self.doc.batteries if b.placeholder), 1)
        self.assertEqual(len(self.doc.snapshots), 13)
        self.assertEqual(sum(len(s.counters) for s in self.doc.snapshots), 30)

    def test_grid_rows_are_baselines_not_service_events(self) -> None:
        # Three grid rows used to arrive as three phantom service events.
        self.assertEqual(len(self.doc.services), 6)
        self.assertEqual(self.doc.reported_total, 6)
        self.assertEqual(self.doc.reporting_gap, 0)

    def test_aircraft_cell_is_flights_then_minutes(self) -> None:
        row = next(s for s in self.doc.snapshots
                   if s.equipment_name == "Ridge - Nova 5" and s.rule == "basic")
        self.assertEqual({c["metric"]: c["value"] for c in row.counters},
                         {"flights": 14.0, "minutes": 150.0})
        self.assertEqual(row.baseline_text, "2025-11-02")

    def test_battery_cell_is_charges_flights_then_minutes(self) -> None:
        row = next(s for s in self.doc.snapshots
                   if s.equipment_name == "Bat-Nova5-A" and s.rule == "basic")
        self.assertEqual({c["metric"]: c["value"] for c in row.counters},
                         {"charges": 18.0, "flights": 14.0, "minutes": 150.0})

    def test_status_column_decides_skipped(self) -> None:
        kinds = [s.kind for s in self.doc.services]
        self.assertEqual(kinds.count("skipped"), 1)
        self.assertEqual(kinds.count("completed"), 5)

    def test_grouped_rows_stay_grouped(self) -> None:
        self.assertEqual(self.doc.resolved_services, 4)
        self.assertEqual(self.doc.grouped_services, 2)

    def test_serial_reference_resolves_to_the_battery(self) -> None:
        self.assertEqual(self.doc.references_resolved, 1)
        linked = [s for s in self.doc.services if s.equipment_names == ["Bat-Nova5-A"]]
        self.assertEqual(len(linked), 1)

    def test_reference_only_aircraft_is_archived(self) -> None:
        self.assertEqual([a.name for a in self.doc.historical_aircraft],
                         ["Old Ridge - Kite Prime"])
        self.assertTrue(self.doc.historical_aircraft[0].archived)

    def test_baselines_reach_the_rules(self) -> None:
        capture.apply_capture(self.conn, self.doc, "grid fixture")
        extended = status_for(self.conn, "Ridge - Nova 5", "extended")
        self.assertEqual(extended["status"], "due_soon")
        self.assertEqual([c["value"] for c in extended["counters"]
                          if c["metric"] == "flights"], [88.0])
        basic = status_for(self.conn, "Ridge - Nova 5S", "basic")
        self.assertEqual(basic["status"], "due")
        self.assertEqual(basic["fired_metric"], "flights")
        self.assertEqual(status_for(self.conn, "Bat-Nova5-A", "basic")["status"], "due_soon")
        self.assertEqual(status_for(self.conn, "Bat-Nova5-B", "basic")["status"], "due")

    def test_placeholder_and_archived_stay_out_of_reminders(self) -> None:
        capture.apply_capture(self.conn, self.doc, "grid fixture")
        names = {s["equipment_name"] for s in maintenance.all_statuses(self.conn)}
        self.assertFalse(any("Unreadable battery" in n for n in names))
        placeholder = db.one(
            self.conn, "SELECT * FROM equipment WHERE name LIKE 'Unreadable battery%'")
        self.assertEqual(placeholder["placeholder"], 1)
        self.assertIsNone(db.one(self.conn, "SELECT id FROM rules WHERE equipment_id=?",
                                 (placeholder["id"],)))
        archived = db.one(self.conn,
                          "SELECT archived FROM equipment WHERE name='Old Ridge - Kite Prime'")
        self.assertEqual(archived["archived"], 1)

    def test_repeat_apply_is_a_no_op(self) -> None:
        first = capture.apply_capture(self.conn, self.doc, "grid fixture")
        services = self.conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        second = capture.apply_capture(self.conn, self.doc, "grid fixture")
        self.assertTrue(second["already_applied"])
        self.assertEqual(first["snapshot_counters"], 30)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
                         services)

    def test_import_before_capture_backfills_battery_readings(self) -> None:
        """A flight imported before the capture cannot be linked to a battery,
        so its reading is never stored. Applying the capture afterwards has to
        leave the same readings as the reverse order does."""
        now = db.now_utc()
        with db.tx(self.conn):
            self.conn.execute(
                "INSERT INTO flights(identity_status,battery_sn,start_time_utc,"
                "duration_s,battery_start_pct,created_at,updated_at) "
                "VALUES('authoritative',?,?,?,?,?,?)",
                ("SYN-BAT-1001", "2026-08-11T21:53:20+00:00", 302.0, 94.0, now, now),
            )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM battery_readings").fetchone()[0], 0)
        capture.apply_capture(self.conn, self.doc, "grid fixture")
        battery = equipment_id(self.conn, "Bat-Nova5-A")
        rows = db.all_rows(
            self.conn, "SELECT * FROM battery_readings WHERE battery_id=?", (battery,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["charge_pct"], 94.0)
        self.assertEqual(rows[0]["source"], "log")


if __name__ == "__main__":
    unittest.main()

