"""Stage 3: loopback server security, live API and offline asset checks."""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
import unittest
from pathlib import Path

from support import TempRoot, log_bytes

from flightdesk import db, demo
from flightdesk.server import build_server


class ServerCase(unittest.TestCase):
    demo_seeded = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.t = TempRoot()
        if cls.demo_seeded:
            demo.seed_demo(cls.t.root)
        cls.server, cls.app = build_server(cls.t.root)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = cls.app.session.token
        cls.cookie = f"fd_session={cls.token}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.t.close()

    def request(self, method: str, path: str, body: bytes | None = None,
                *, token: bool = True, cookie: bool = True,
                origin: str | None = None, host: str | None = None,
                extra: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        headers = {}
        if host:
            headers["Host"] = host
        if token:
            headers["X-FD-Token"] = self.token
        if cookie:
            headers["Cookie"] = self.cookie
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if extra:
            headers.update(extra)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        conn.close()
        return status, payload


class SecurityTest(ServerCase):
    def test_foreign_host_header_is_refused(self) -> None:
        status, _ = self.request("GET", "/api/state", host="evil.example:80")
        self.assertEqual(status, 403)

    def test_foreign_origin_is_refused(self) -> None:
        status, _ = self.request("POST", "/api/settings", b"{}", origin="https://evil.example")
        self.assertEqual(status, 403)

    def test_missing_session_is_refused(self) -> None:
        status, _ = self.request("GET", "/api/state", token=False, cookie=False)
        self.assertEqual(status, 403)

    def test_missing_csrf_header_is_refused(self) -> None:
        status, _ = self.request("POST", "/api/settings", b"{}", token=False)
        self.assertEqual(status, 403)

    def test_loopback_origin_is_accepted(self) -> None:
        status, payload = self.request("POST", "/api/settings", b"{}",
                                       origin=f"http://127.0.0.1:{self.app.port}")
        self.assertEqual(status, 200, payload)

    def test_index_sets_cookie_and_embeds_token(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=20)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{self.app.port}"})
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        headers = dict(response.getheaders())
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertIn("HttpOnly", headers.get("Set-Cookie", ""))
        self.assertIn(self.token, body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

    def test_unknown_route_is_json_404(self) -> None:
        status, payload = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["code"], "no_route")


class StaticAssetTest(unittest.TestCase):
    def test_no_external_resources_are_referenced(self) -> None:
        web = Path(__file__).resolve().parent.parent / "flightdesk" / "web"
        patterns = [
            re.compile(r"""<script[^>]+src=["']https?://""", re.I),
            re.compile(r"""<link[^>]+href=["']https?://""", re.I),
            re.compile(r"""@import\s+url\(["']?https?://""", re.I),
            re.compile(r"""fetch\(\s*["']https?://"""),
            re.compile(r"""new\s+WebSocket\(\s*["']wss?://"""),
        ]
        for path in web.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), f"external resource in {path.name}")

    def test_bundled_files_exist(self) -> None:
        web = Path(__file__).resolve().parent.parent / "flightdesk" / "web"
        for name in ("index.html", "app.css", "app.js", "charts.js", "track.js"):
            self.assertTrue((web / name).is_file(), name)

    def test_owner_can_prove_the_cache_offline(self) -> None:
        # The brief's key check needs a reparse the owner can run; without a
        # control the check could only be done with a shell command.
        web = Path(__file__).resolve().parent.parent / "flightdesk" / "web"
        app_js = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="key-reparse-cache"', app_js)
        self.assertIn('scope: "cached"', app_js)
        self.assertIn('id="key-unlock-all"', app_js)

    def test_browser_scripts_parse(self) -> None:
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        web = Path(__file__).resolve().parent.parent / "flightdesk" / "web"
        for name in ("app.js", "charts.js", "track.js"):
            proc = subprocess.run([node, "--check", str(web / name)],
                                  capture_output=True, timeout=60)
            self.assertEqual(proc.returncode, 0,
                             f"{name}: {proc.stderr.decode('utf-8', 'replace')[:400]}")


class ApiTest(ServerCase):
    demo_seeded = True

    def get_json(self, path: str) -> dict:
        status, payload = self.request("GET", path)
        self.assertEqual(status, 200, payload[:300])
        return json.loads(payload)

    def post_json(self, path: str, body: dict) -> dict:
        status, payload = self.request("POST", path, json.dumps(body).encode())
        self.assertEqual(status, 200, payload[:300])
        return json.loads(payload)

    def test_state_reports_counts_and_units(self) -> None:
        state = self.get_json("/api/state")
        self.assertTrue(state["demo_seeded"])
        self.assertGreaterEqual(state["counts"]["flights"], 13)
        self.assertIn(state["unit_preference"], {"metric", "imperial"})

    def test_flights_filters_match_totals(self) -> None:
        everything = self.get_json("/api/flights")
        filtered = self.get_json("/api/flights?pilot=Alex%20Example")
        self.assertLessEqual(len(filtered["rows"]), len(everything["rows"]))
        self.assertEqual(filtered["totals"]["flights"],
                         len([r for r in filtered["rows"]
                              if r["identity_status"] == "authoritative"]))
        self.assertGreaterEqual(everything["totals"]["flights"],
                                filtered["totals"]["flights"])
        search = self.get_json("/api/flights?search=Air%203S")
        self.assertTrue(all("Air 3S" in (r["aircraft_name"] or "") for r in search["rows"]))
        empty = self.get_json("/api/flights?search=zzzz-no-such-flight")
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["totals"]["flights"], 0)

    def test_flight_detail_pairs_track_and_metrics(self) -> None:
        listing = self.get_json("/api/flights")
        tracked = [r for r in listing["rows"] if r["sample_count"]]
        self.assertTrue(tracked, "demo should include synthetic tracks")
        detail = self.get_json(f"/api/flights/{tracked[0]['id']}")
        self.assertTrue(detail["telemetry"]["samples"])
        self.assertEqual(detail["telemetry"]["source"], "synthetic")
        self.assertTrue(detail["files"])
        self.assertIn("prev", detail["neighbours"])
        self.assertIn("next", detail["neighbours"])

    def test_arrow_navigation_visits_every_flight(self) -> None:
        listing = self.get_json("/api/flights")
        ids = [r["id"] for r in sorted(listing["rows"], key=lambda r: r["start_time_utc"] or "")]
        detail = self.get_json(f"/api/flights/{ids[0]}")
        seen = [ids[0]]
        while detail["neighbours"]["next"]:
            detail = self.get_json(f"/api/flights/{detail['neighbours']['next']}")
            seen.append(detail["flight"]["id"])
            if len(seen) > 50:
                break
        self.assertEqual(seen, ids)

    def test_decode_pending_reports_nothing_pending_as_finished(self) -> None:
        # Clicking Decode pending twice used to answer "could not decode"
        # because an empty target list counted as failure.
        result = self.post_json("/api/key/unlock", {"all": True})
        self.assertTrue(result["nothing_pending"])
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["codes"], [])

    def test_reparse_from_cache_says_when_there_is_no_cache(self) -> None:
        # On a root with no decoded encrypted record there is nothing cached to
        # re-decode, and the app must say so rather than fail silently.
        result = self.post_json("/api/reparse", {"scope": "cached"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "no_cached_keychain")

    def test_equipment_and_maintenance_endpoints(self) -> None:
        equipment = self.get_json("/api/equipment")
        self.assertTrue(equipment["rows"])
        battery = [r for r in equipment["rows"] if r["kind"] == "battery"][0]
        detail = self.get_json(f"/api/equipment/{battery['id']}")
        self.assertEqual(detail["equipment"]["id"], battery["id"])
        overview = self.get_json("/api/maintenance")
        self.assertTrue(overview["statuses"])
        rule = overview["statuses"][0]
        rule_detail = self.get_json(f"/api/rules/{rule['id']}")
        self.assertTrue(rule_detail["ok"])

    def test_service_actions_round_trip_through_the_api(self) -> None:
        overview = self.get_json("/api/maintenance")
        target = [s for s in overview["statuses"]
                  if s["status"] in ("due", "due_soon") and not s["archived"]][0]
        result = self.post_json(f"/api/rules/{target['id']}/service",
                                {"action": "completed", "notes": "api test"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"]["status"], "ok")
        undo = self.post_json(f"/api/services/{result['service_id']}/undo", {})
        self.assertTrue(undo["ok"])
        after = self.get_json(f"/api/rules/{target['id']}")
        self.assertEqual(after["status"], target["status"])

    def test_upload_imports_and_deduplicates(self) -> None:
        data = log_bytes(11, days=200, aircraft="mini3")
        headers = {"X-FD-Filename": "DJIFlightRecord_2027-02-27_%5B21-53-20%5D.txt",
                   "X-FD-Member": "DJIFlightRecord_2027-02-27_%5B21-53-20%5D.txt",
                   "Content-Type": "application/octet-stream",
                   "Content-Length": str(len(data))}
        status, payload = self.request("POST", "/api/import/upload", data, extra=headers)
        self.assertEqual(status, 200, payload[:200])
        first = json.loads(payload)
        self.assertEqual(first["totals"]["new"], 1)
        status, payload = self.request("POST", "/api/import/upload", data, extra=headers)
        second = json.loads(payload)
        self.assertEqual(second["totals"]["already_imported"], 1)
        self.assertEqual(second["totals"]["new"], 0)

    def _upload(self, name: str, data: bytes) -> tuple[int, dict]:
        headers = {
            "X-FD-Filename": __import__("urllib.parse").parse.quote(name),
            "X-FD-Member": __import__("urllib.parse").parse.quote(name),
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        }
        status, payload = self.request("POST", "/api/import/upload", data, extra=headers)
        return status, json.loads(payload)

    def _zip_bytes(self, entries: dict) -> bytes:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_zip_upload_reports_each_member(self) -> None:
        from support import log_bytes
        payload = self._zip_bytes({
            "data/extracted/flight_logs/DJIFlightRecord_2027-05-01_[10-00-00].txt":
                log_bytes(11, days=400),
            "data/extracted/flight_logs/DJIFlightRecord_2027-05-02_[10-00-00].txt":
                log_bytes(8, days=401),
            "data/controller-test/DJIFlightRecord_2027-04-01_[10-00-00].txt":
                log_bytes(11, days=390, aircraft="air3s"),
            "__MACOSX/._junk": b"x",
        })
        status, body = self._upload("handoff.zip", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["totals"]["discovered"], 3)
        self.assertEqual(body["totals"]["new"], 3)
        self.assertEqual(body["totals"]["new_flight"], 3)

    def test_non_zip_named_zip_is_explained(self) -> None:
        status, body = self._upload("handoff.zip", b"definitely not a zip")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["note"], "zip_unreadable")
        self.assertEqual(body["totals"]["new"], 0)
        self.assertIn("zip_unreadable", body["codes"])

    def test_zip_without_flight_logs_is_explained(self) -> None:
        payload = self._zip_bytes({"data/extracted/readme.md": b"nothing here"})
        status, body = self._upload("empty.zip", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["note"], "no_eligible_files")
        self.assertEqual(body["totals"]["discovered"], 0)

    def test_zip_with_escaping_member_imports_nothing(self) -> None:
        from support import log_bytes
        payload = self._zip_bytes({"../escape.txt": log_bytes(11, days=420)})
        status, body = self._upload("evil.zip", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["note"], "zip_unsafe")
        self.assertEqual(body["totals"]["new"], 0)

    def test_second_upload_of_the_same_zip_is_idempotent(self) -> None:
        from support import log_bytes
        payload = self._zip_bytes({
            "data/extracted/flight_logs/DJIFlightRecord_2027-06-01_[10-00-00].txt":
                log_bytes(11, days=500),
        })
        first_status, first = self._upload("one.zip", payload)
        self.assertEqual(first_status, 200)
        self.assertEqual(first["totals"]["new"], 1)
        second_status, second = self._upload("one.zip", payload)
        self.assertEqual(second_status, 200)
        self.assertEqual(second["totals"]["new"], 0)
        self.assertEqual(second["totals"]["already_imported"], 1)
        self.assertEqual(second["totals"]["new_flight"], 0)

    def test_rejects_non_flight_upload(self) -> None:
        headers = {"X-FD-Filename": "notes.txt", "X-FD-Member": "notes.txt",
                   "Content-Length": "3"}
        status, payload = self.request("POST", "/api/import/upload", b"abc", extra=headers)
        self.assertEqual(status, 400)

    def test_exports_and_reconciliation(self) -> None:
        status, payload = self.request("GET", "/api/export/flights.csv")
        self.assertEqual(status, 200)
        self.assertIn(b"flight_id", payload)
        status, redacted = self.request("GET", "/api/export/flights.csv?redact=1")
        self.assertEqual(status, 200)
        self.assertNotIn(b"SYN-M2P-0001", redacted)
        recon = self.get_json("/api/recon")
        for key in ("archive", "supplied_controller", "later_controller", "combined"):
            self.assertIn(key, recon["groups"])
        self.assertFalse(recon["real_inputs_selected"])

    def test_key_endpoints_report_state_without_leaking(self) -> None:
        status, payload = self.request("GET", "/api/key")
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertIn("configured", data)
        self.assertEqual(data["cache"]["cached"], 0)
        self.assertNotIn("aesKey", payload.decode("utf-8"))
        unlock = self.post_json("/api/key/unlock", {"all": True})
        self.assertIn("results", unlock)

    def test_usb_status_is_readable(self) -> None:
        data = self.get_json("/api/usb")
        self.assertIn("devices", data)
        self.assertIn("watcher", data)

    def test_controller_scan_reports_absence_cleanly(self) -> None:
        result = self.post_json("/api/usb/scan", {})
        self.assertIn(result["status"], {"no_device", "no_files", "unavailable"})
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["mode"], "physical")

    def test_picker_backend_imports_a_folder_path(self) -> None:
        import tempfile
        from pathlib import Path
        from support import log_bytes
        with tempfile.TemporaryDirectory(prefix="fd-picker-") as tmp:
            folder = Path(tmp) / "flight_logs"
            folder.mkdir()
            (folder / "DJIFlightRecord_2027-03-04_[09-00-00].txt").write_bytes(
                log_bytes(11, days=300, aircraft="air3s"))
            result = self.post_json("/api/import/path", {"path": str(folder)})
        self.assertEqual(result["totals"]["new"], 1)
        self.assertEqual(result["totals"]["new_flight"], 1)

    def test_picker_backend_rejects_a_missing_path(self) -> None:
        status, payload = self.request(
            "POST", "/api/import/path",
            json.dumps({"path": "C:/definitely/not/here"}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["code"], "path_not_found")

    def test_capture_preview_and_apply_with_an_explicit_path(self) -> None:
        from pathlib import Path
        fixture = (Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" /
                   "AirData-Equipment-Capture-SYNTHETIC.md")
        preview = self.post_json("/api/capture/preview", {"path": str(fixture)})
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["service_events"], 21)
        self.assertEqual(preview["grouped_services"], 4)
        applied = self.post_json("/api/capture/apply", {"path": str(fixture)})
        self.assertIn("already_applied", applied)

    def test_real_input_toggle_enables_signed_differences(self) -> None:
        plain = self.post_json("/api/recon/select-real", {"selected": True})
        self.assertTrue(plain["real_inputs_selected"])
        self.assertIsNotNone(plain["signed_differences"])
        back = self.post_json("/api/recon/select-real", {"selected": False})
        self.assertFalse(back["real_inputs_selected"])
        self.assertIsNone(back["signed_differences"])


if __name__ == "__main__":
    unittest.main()
