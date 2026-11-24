"""P11a/F15: a real, reviewable identity decision instead of a dead-end button.

The reviewed "Keep / merge" control only opened the details page: there was no
mutation behind it. This pins the evidence view, the keep-separate decision, its
persistence across reparse, and its reversibility.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TempCase

from flightdesk import db, identity
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome
from flightdesk.server import build_server


def outcome(start="2026-01-01T00:00:00+00:00", serial="SN-1", distance=10.0,
            duration=600.0, name="Aircraft", details_rejected=False) -> ParseOutcome:
    return ParseOutcome(
        11, "decoded",
        details={"start_time_utc": start, "aircraft_sn": serial, "aircraft_name": name,
                 "total_time_s": duration, "total_distance_m": distance},
        samples=[],
        meta={"details_rejected": details_rejected} if details_rejected else {},
    )


class IdentityReviewTest(TempCase):
    def _import(self, raw: bytes, parsed: ParseOutcome, day: int = 1) -> None:
        with patch("flightdesk.importer.parse_file", return_value=parsed):
            Importer(self.root, self.conn).import_stream(
                io.BytesIO(raw), f"FlightRecord_2026-01-0{day}_[00-00-00].txt",
                source_kind="manual", source_label="t")

    def _unresolved_id(self) -> int:
        row = db.one(self.conn,
                     "SELECT id FROM flights WHERE identity_status='unresolved' LIMIT 1")
        return int(row["id"])

    def setUp(self) -> None:
        super().setUp()
        # A strong record, then a same-aircraft same-start record missing its
        # distance: a weak match, kept and excluded.
        self._import(b"a", outcome(distance=10.0), day=1)
        self._import(b"b", outcome(distance=None), day=1)
        self.target = self._unresolved_id()

    def test_review_shows_evidence_and_candidates(self) -> None:
        data = identity.review(self.conn, self.target)
        self.assertTrue(data["ok"])
        self.assertEqual(data["flight"]["excluded_reason"], "same_time_weak_match")
        self.assertEqual(len(data["flight"]["files"]), 1)
        self.assertTrue(data["candidates"])
        self.assertEqual({o["value"] for o in data["options"]},
                         {"separate", "exclude", "include"})

    def test_keep_separate_makes_the_record_authoritative(self) -> None:
        before_raw = self.conn.execute("SELECT sha256 FROM raw_files ORDER BY id").fetchall()
        result = identity.decide(self.conn, self.target, "separate", "two flights")
        self.assertTrue(result["ok"], result)
        row = db.one(self.conn, "SELECT identity_status,excluded_reason FROM flights WHERE id=?",
                     (self.target,))
        self.assertEqual(row["identity_status"], "authoritative")
        self.assertIsNone(row["excluded_reason"])
        self.assertEqual(result["decision"]["decision"], "separate")
        # The originals are untouched.
        after_raw = self.conn.execute("SELECT sha256 FROM raw_files ORDER BY id").fetchall()
        self.assertEqual([r[0] for r in before_raw], [r[0] for r in after_raw])

    def test_exclude_persists_across_reparse(self) -> None:
        identity.decide(self.conn, self.target, "exclude", "not mine")
        raw_id = db.one(self.conn, "SELECT raw_file_id FROM flight_files WHERE flight_id=?",
                        (self.target,))["raw_file_id"]
        with patch("flightdesk.importer.parse_file", return_value=outcome(distance=None)):
            Importer(self.root, self.conn).reparse_raw(int(raw_id))
        row = db.one(self.conn, "SELECT identity_status,excluded_reason FROM flights WHERE id=?",
                     (self.target,))
        self.assertEqual(row["identity_status"], "excluded")
        self.assertEqual(row["excluded_reason"], "owner_excluded")

    def test_include_is_refused_without_usable_evidence(self) -> None:
        # A record whose details cannot be read has no measurements; dismissing
        # its warning must not fabricate any.
        self._import(b"c", outcome(details_rejected=True, distance=None), day=2)
        target = int(db.one(
            self.conn,
            "SELECT id FROM flights WHERE excluded_reason='details_unreadable' LIMIT 1")["id"])
        result = identity.decide(self.conn, target, "include", "trust me")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "insufficient_evidence")
        row = db.one(self.conn, "SELECT identity_status FROM flights WHERE id=?", (target,))
        self.assertEqual(row["identity_status"], "unresolved")

    def test_a_decision_is_reversible(self) -> None:
        identity.decide(self.conn, self.target, "separate", "kept")
        undone = identity.undo(self.conn, self.target)
        self.assertTrue(undone["ok"], undone)
        row = db.one(self.conn, "SELECT identity_status,excluded_reason FROM flights WHERE id=?",
                     (self.target,))
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["excluded_reason"], "same_time_weak_match")
        self.assertIsNone(undone["decision"])

    def test_an_invalid_decision_is_refused(self) -> None:
        result = identity.decide(self.conn, self.target, "merge")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "bad_decision")


class IdentityApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempCase()
        self.t.setUp()
        with patch("flightdesk.importer.parse_file", return_value=outcome(distance=10.0)):
            Importer(self.t.root, self.t.conn).import_stream(
                io.BytesIO(b"a"), "FlightRecord_2026-01-01_[00-00-00].txt",
                source_kind="manual", source_label="t")
        with patch("flightdesk.importer.parse_file", return_value=outcome(distance=None)):
            Importer(self.t.root, self.t.conn).import_stream(
                io.BytesIO(b"b"), "FlightRecord_2026-01-01_[00-00-00].txt",
                source_kind="manual", source_label="t")
        self.t.conn.close()
        self.server, self.app = build_server(self.t.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.app.session.token

    def tearDown(self) -> None:
        from flightdesk import errlog
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=20)
        errlog.close_logger(self.t.root)
        self.t.tearDown()

    def _call(self, method: str, path: str, body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=20)
        payload = b"" if body is None else json.dumps(body).encode()
        headers = {"X-FD-Token": self.token, "Cookie": f"fd_session={self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        status = response.status
        conn.close()
        return status, data

    def test_the_route_round_trips_a_decision(self) -> None:
        listing = self._call("GET", "/api/flights")[1]
        unresolved = [r for r in listing["rows"] if r["identity_status"] == "unresolved"]
        self.assertTrue(unresolved)
        target = unresolved[0]["id"]
        status, review = self._call("GET", f"/api/identity/{target}")
        self.assertEqual(status, 200, review)
        self.assertTrue(review["ok"])
        status, decided = self._call("POST", f"/api/identity/{target}",
                                     {"decision": "separate", "note": "route test"})
        self.assertEqual(status, 200, decided)
        self.assertEqual(decided["flight"]["identity_status"], "authoritative")
        status, undone = self._call("POST", f"/api/identity/{target}/undo", {})
        self.assertEqual(status, 200, undone)
        self.assertEqual(undone["flight"]["identity_status"], "unresolved")


class StaticLabelTest(unittest.TestCase):
    def test_the_dead_end_label_is_gone(self) -> None:
        web = Path(__file__).resolve().parent.parent / "flightdesk" / "web"
        app_js = (web / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Keep / merge", app_js)
        self.assertIn("Review identity", app_js)
        self.assertIn("/api/identity/", app_js)


if __name__ == "__main__":
    unittest.main()
