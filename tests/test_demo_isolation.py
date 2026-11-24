"""P01 / F01: synthetic demo data must not touch an ordinary archive.

The reviewed build let `seed_demo` run in any root and rewrite every flight's
pilot and tags, and let real imports land in a demo root. These tests pin the
isolation at the backend boundary — not by hiding a button.
"""
from __future__ import annotations

import http.client
import io
import json
import threading
import unittest
from pathlib import Path

from support import TempRoot, apply_capture, log_bytes

from flightdesk import db, demo, paths
from flightdesk.importer import Importer
from flightdesk.server import build_server


def raw_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0])


def flight_meta(conn) -> list[tuple]:
    return [
        (row["pilot"], row["tags_json"], row["notes"])
        for row in db.all_rows(conn, "SELECT pilot,tags_json,notes FROM flights ORDER BY id")
    ]


class DemoIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempRoot()
        self.root = self.t.root
        self.conn = self.t.conn

    def tearDown(self) -> None:
        self.t.close()

    def _import_one(self, days: int = 0, aircraft: str = "mavic2") -> dict:
        imp = Importer(self.root, self.conn)
        batch = imp.import_stream(
            io.BytesIO(log_bytes(11, days=days, aircraft=aircraft)),
            f"DJIFlightRecord_2027-01-0{days + 1}_[10-00-00].txt",
            source_kind="manual", source_label="test",
        )
        return batch.totals

    # ------------------------------------------------------------ populated
    def test_seed_refused_on_a_populated_archive_keeps_every_annotation(self) -> None:
        self._import_one()
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE flights SET pilot='Owner pilot',tags_json='[\"Owner tag\"]',"
                "notes='Owner note'"
            )
        apply_capture(self.conn)
        before_meta = flight_meta(self.conn)
        before = db.table_counts(self.conn)

        result = demo.seed_demo(self.root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "root_not_empty")
        self.assertIn("flights", result["found"])
        self.assertEqual(flight_meta(self.conn), before_meta)
        self.assertEqual(db.table_counts(self.conn), before)
        self.assertFalse(db.get_setting(self.conn, "demo_seeded", False))
        self.assertFalse(db.get_setting(self.conn, "demo_seed_state"))
        # No demo annotation replaced the owner's.
        demo_pilots = [r[0] for r in self.conn.execute(
            "SELECT pilot FROM flights WHERE pilot IN ('Demo Pilot','Alex Example')")]
        self.assertEqual(demo_pilots, [])

    def test_capture_only_root_is_protected(self) -> None:
        # A capture imports equipment, rules and services with no flights at
        # all. An empty flight table must not be read as an empty root.
        apply_capture(self.conn)
        before = db.table_counts(self.conn)
        result = demo.seed_demo(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "root_not_empty")
        self.assertIn("service_snapshots", result["found"])
        self.assertEqual(db.table_counts(self.conn), before)

    def test_archive_files_without_rows_are_protected(self) -> None:
        stray = self.root / "archive" / "ab" / "stray.txt"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"an original the index has not seen")
        result = demo.seed_demo(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "root_not_empty")
        self.assertIn("archive_files", result["found"])
        self.assertTrue(stray.exists())

    # ------------------------------------------------------------------ demo
    def test_clean_demo_seeds_idempotently(self) -> None:
        first = demo.seed_demo(self.root)
        self.assertTrue(first["ok"], first)
        self.assertFalse(first["already_seeded"])
        counts = db.table_counts(self.conn)
        after_first = raw_count(self.conn)

        second = demo.seed_demo(self.root)
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_seeded"])
        self.assertEqual(second["counts"], counts)
        self.assertEqual(raw_count(self.conn), after_first)
        self.assertEqual(db.data_mode(self.conn), "synthetic")

    def test_demo_tags_only_the_flights_it_created(self) -> None:
        # A resumed seed must not retag a flight imported since the first pass.
        demo.seed_demo(self.root)
        with db.tx(self.conn):
            db.set_setting(self.conn, "demo_seeded", False)
            db.set_setting(self.conn, "demo_seed_state", "partial")
        extra = Importer(self.root, self.conn, allow_synthetic=True)
        extra.import_stream(
            io.BytesIO(log_bytes(11, days=900, aircraft="air3s")),
            "DJIFlightRecord_2029-01-01_[10-00-00].txt",
            source_kind="manual", source_label="test",
        )
        extra_id = int(self.conn.execute("SELECT MAX(id) FROM flights").fetchone()[0])
        with db.tx(self.conn):
            self.conn.execute(
                "UPDATE flights SET pilot='Owner pilot',tags_json='[\"Owner tag\"]' "
                "WHERE id=?", (extra_id,)
            )
        resumed = demo.seed_demo(self.root)
        self.assertTrue(resumed["ok"], resumed)
        self.assertTrue(resumed.get("resumed"))
        row = db.one(
            self.conn,
            "SELECT pilot,tags_json FROM flights WHERE id=?", (extra_id,),
        )
        self.assertEqual(row["pilot"], "Owner pilot")
        self.assertEqual(row["tags_json"], '["Owner tag"]')

    def test_real_import_into_a_demo_root_is_refused_and_recorded(self) -> None:
        demo.seed_demo(self.root)
        before_raw = raw_count(self.conn)
        before_archive = sorted(p.name for p in (self.root / "archive").rglob("*") if p.is_file())
        imp = Importer(self.root, self.conn)
        result = imp.import_stream(
            io.BytesIO(log_bytes(11, days=700, aircraft="mini3")),
            "DJIFlightRecord_2027-09-09_[10-00-00].txt",
            source_kind="manual", source_label="test",
        )
        self.assertEqual(result.note, Importer.DEMO_REFUSAL)
        self.assertEqual(result.totals["new"], 0)
        self.assertEqual(raw_count(self.conn), before_raw)
        self.assertEqual(
            sorted(p.name for p in (self.root / "archive").rglob("*") if p.is_file()),
            before_archive,
        )
        # The refusal is visible in the import history, not silent.
        row = db.one(self.conn, "SELECT note FROM import_batches ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(row["note"], Importer.DEMO_REFUSAL)

    def test_batch_import_into_a_demo_root_is_refused(self) -> None:
        demo.seed_demo(self.root)
        imp = Importer(self.root, self.conn)
        batch = imp.open_batch("USB scan", "usb", deferred=True)
        self.assertEqual(batch.note, Importer.DEMO_REFUSAL)
        self.assertTrue(batch.items)
        before = raw_count(self.conn)
        imp.add_stream(batch, io.BytesIO(log_bytes(11, days=800)),
                       "DJIFlightRecord_2027-10-10_[10-00-00].txt",
                       source_kind="usb", source_label="USB scan")
        imp.close_batch(batch)
        self.assertEqual(raw_count(self.conn), before)

    def test_concurrent_seed_and_import_cannot_contaminate(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: dict[str, dict] = {}

        def seed() -> None:
            barrier.wait()
            outcomes["seed"] = demo.seed_demo(self.root)

        def import_real() -> None:
            conn = db.init_db(paths.db_path(self.root))
            try:
                barrier.wait()
                imp = Importer(self.root, conn)
                batch = imp.import_stream(
                    io.BytesIO(log_bytes(11, days=600, aircraft="air3s")),
                    "DJIFlightRecord_2027-11-11_[10-00-00].txt",
                    source_kind="manual", source_label="concurrent",
                )
                outcomes["import"] = {"note": batch.note, "new": batch.totals["new"]}
            finally:
                conn.close()

        threads = [threading.Thread(target=seed), threading.Thread(target=import_real)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        conn = db.init_db(paths.db_path(self.root))
        try:
            self.assertIn("seed", outcomes)
            self.assertIn("import", outcomes)
            seeded = bool(outcomes["seed"].get("ok")) or bool(
                outcomes["seed"].get("already_seeded"))
            imported = int(outcomes["import"]["new"])
            if imported:
                # The import won the race: seeding must have been refused.
                self.assertFalse(seeded)
                self.assertEqual(outcomes["seed"]["code"], "root_not_empty")
                pilots = [row[0] for row in conn.execute(
                    "SELECT pilot FROM flights WHERE pilot IS NOT NULL")]
                self.assertEqual(pilots, [])
            else:
                # The seed won: the import must have been refused, not merged.
                self.assertTrue(seeded)
                self.assertEqual(outcomes["import"]["note"], Importer.DEMO_REFUSAL)
        finally:
            conn.close()


class _WebCase(unittest.TestCase):
    demo_seeded = False

    def setUp(self) -> None:
        self.t = TempRoot()
        self.root = self.t.root
        if self.demo_seeded:
            demo.seed_demo(self.root)
        self.t.conn.close()
        self.conn = None
        self.server, self.app = build_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.app.session.token

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=20)
        self.t.close()

    def request(self, method: str, path: str, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        headers = {"X-FD-Token": self.token, "Cookie": f"fd_session={self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        conn.close()
        return status, payload


class DemoEntryPointTests(_WebCase):
    """The welcome and Import entry points obey the same backend rule."""

    def test_empty_root_offers_and_accepts_the_demo(self) -> None:
        status, payload = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        state = json.loads(payload)
        self.assertTrue(state["demo"]["can_seed"])
        self.assertFalse(state["demo"]["separate_root_required"])
        status, payload = self.request("POST", "/api/import/demo", b"{}")
        self.assertEqual(status, 200, payload[:300])
        self.assertTrue(json.loads(payload)["ok"])
        state = json.loads(self.request("GET", "/api/state")[1])
        self.assertFalse(state["demo"]["can_seed"])


class PopulatedRootEntryPointTests(_WebCase):
    def setUp(self) -> None:
        super().setUp()
        conn = db.init_db(paths.db_path(self.root))
        try:
            Importer(self.root, conn).import_stream(
                io.BytesIO(log_bytes(11, days=0)), "DJIFlightRecord_2027-01-01_[10-00-00].txt",
                source_kind="manual", source_label="test")
        finally:
            conn.close()

    def test_populated_root_refuses_the_demo_and_says_why(self) -> None:
        state = json.loads(self.request("GET", "/api/state")[1])
        self.assertFalse(state["demo"]["can_seed"])
        self.assertFalse(state["demo"]["separate_root_required"])
        status, payload = self.request("POST", "/api/import/demo", b"{}")
        self.assertEqual(status, 409, payload[:300])
        body = json.loads(payload)
        self.assertEqual(body["code"], "root_not_empty")
        self.assertEqual(state["counts"]["flights"], 1)


class DemoRootRefusesUploadsTests(_WebCase):
    demo_seeded = True

    def test_upload_into_a_demo_root_is_refused_with_a_visible_reason(self) -> None:
        data = log_bytes(11, days=950, aircraft="mini3")
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        headers = {
            "X-FD-Token": self.token, "Cookie": f"fd_session={self.token}",
            "X-FD-Filename": "DJIFlightRecord_2027-12-12_%5B10-00-00%5D.txt",
            "X-FD-Member": "DJIFlightRecord_2027-12-12_%5B10-00-00%5D.txt",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        }
        conn.request("POST", "/api/import/upload", data, headers=headers)
        response = conn.getresponse()
        body = json.loads(response.read())
        status = response.status
        conn.close()
        self.assertEqual(status, 409, body)
        self.assertEqual(body["note"], "demo_root_requires_separate_root")
        self.assertEqual(body["totals"]["new"], 0)
        self.assertEqual(sum(body["codes"].values()), 1)


if __name__ == "__main__":
    unittest.main()
