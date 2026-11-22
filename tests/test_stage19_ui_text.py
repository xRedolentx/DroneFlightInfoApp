"""On-screen text and layout claims that the code must actually keep.

These are cheap, but they are the kind of thing that drifts silently: a sentence
that contradicts the code beside it, a badge that labels an empty archive
"synthetic", a metric labelled "path" that is not the path. Each assertion here
failed against the code the independent review read.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from support import TempCase, log_bytes

from flightdesk import demo, views

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "flightdesk" / "web" / "app.js").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


class TextTest(unittest.TestCase):
    def test_the_key_section_does_not_claim_the_key_is_never_in_a_backup(self) -> None:
        self.assertNotIn("process argument or a backup", APP_JS)
        self.assertIn("included in a \" +", APP_JS)

    def test_readme_names_the_key_where_the_code_puts_it(self) -> None:
        # keychain.KEY_FILE is "keychains/dji_key.bin", resolved under the data root.
        self.assertIn("FlightDesk\\data\\keychains\\dji_key.bin", README)
        self.assertNotIn("The DJI key is stored separately at", README)

    def test_readme_mentions_the_opt_in_basemap(self) -> None:
        self.assertIn("OpenStreetMap", README)
        self.assertIn("stays off until you allow it", README)

    def test_no_owner_facing_text_promises_nothing_is_cached(self) -> None:
        lowered = APP_JS.lower()
        self.assertNotIn("nothing is saved for offline use", lowered)
        self.assertNotIn("saved for offline use", lowered)
        self.assertIn("no bulk download", lowered)
        self.assertIn("for 7 days", lowered)

    def test_no_owner_facing_text_refers_to_an_assistant(self) -> None:
        haystack = (APP_JS + README).lower()
        self.assertNotIn("assistant", haystack)
        self.assertNotIn("never sees them", haystack)

    def test_the_synthetic_badge_needs_demo_data(self) -> None:
        self.assertIn('demo_seeded ? "synthetic"', APP_JS)
        self.assertIn("mode.hidden = !label", APP_JS)


class LayoutTest(unittest.TestCase):
    def test_the_vertical_section_rail_is_gone(self) -> None:
        self.assertNotIn('id="rail"', APP_JS)
        self.assertNotIn('<nav class="rail"', APP_JS)

    def test_the_detail_pane_is_one_column(self) -> None:
        css = (ROOT / "flightdesk" / "web" / "app.css").read_text(encoding="utf-8")
        self.assertNotIn("grid-template-columns: 34px", css)
        self.assertNotIn(".rail a", css)

    def test_the_flight_rail_does_not_repeat_the_record_metrics(self) -> None:
        # Navigation only: a date and an aircraft. Duration and pilot belong to
        # the record card beside it.
        self.assertNotIn("fmtDuration(f.duration_s) + \"</span>\"", APP_JS)
        self.assertIn('esc(dateOnly(f.start_time_utc || f.filename_time)) + "</span>"', APP_JS)

    def test_maintenance_cards_put_provenance_behind_details(self) -> None:
        self.assertIn('<summary class="tiny faint">Details</summary>', APP_JS)

    def test_maintenance_card_pluralises_one_flight(self) -> None:
        self.assertIn('status.covered_flights === 1 ? " archived flight"', APP_JS)

    def test_settings_copy_is_plain_owner_language(self) -> None:
        for phrase in ("These are my real records", "Compare with my AirData figures",
                       "Turn your network off first"):
            self.assertIn(phrase, APP_JS)


class DistanceLabelTest(unittest.TestCase):
    def test_a_record_card_no_longer_calls_the_log_total_a_path(self) -> None:
        self.assertNotIn("</b> path</span>", APP_JS)
        self.assertIn("flightDistance(f)", APP_JS)
        self.assertIn("path_complete", APP_JS)


class DistanceTotalsTest(TempCase):
    def test_totals_show_path_length_and_whether_every_flight_has_one(self) -> None:
        self.import_bytes(log_bytes(11, days=0), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.import_bytes(log_bytes(11, days=1), "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        self.conn.execute("UPDATE flights SET path_length_m=258.0")
        self.conn.commit()
        totals = views.flights(self.conn, {})["totals"]
        self.assertAlmostEqual(totals["path_m"], 516.0, places=1)
        self.assertTrue(totals["path_complete"])
        # The log figure is still reported, and separately.
        self.assertGreater(totals["distance_m"], 0)

    def test_one_flight_without_a_path_makes_the_tile_fall_back(self) -> None:
        self.import_bytes(log_bytes(11, days=0), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.import_bytes(log_bytes(11, days=1), "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        self.conn.execute("UPDATE flights SET path_length_m=258.0 WHERE id=1")
        self.conn.commit()
        totals = views.flights(self.conn, {})["totals"]
        self.assertFalse(totals["path_complete"])


class DemoTracksTest(TempCase):
    def test_demo_tracks_carry_a_warnings_record(self) -> None:
        # The screens said "Airborne ... from samples" and, one tab over, "no
        # frame data was stored". The seeder stored telemetry but not its summary.
        demo.seed_demo(self.root)
        rows = self.conn.execute(
            "SELECT warnings_json FROM flights WHERE sample_count > 0").fetchall()
        self.assertTrue(rows, "demo seeded no sampled flight")
        for row in rows:
            self.assertIsNotNone(row["warnings_json"])


if __name__ == "__main__":
    unittest.main()
