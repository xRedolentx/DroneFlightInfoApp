"""P09/F13: enabling the watcher starts real work in the serving process.

The reviewed build stored `enabled: true` and never started a worker, so an
enabled setting produced no scans. A logon launch against an already-running
ordinary server also exited claiming watching had started.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TempRoot

from flightdesk import db, usb
from flightdesk.server import App, build_server


class FakeCollector:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_next = 0
        self.started = threading.Event()

    def __call__(self, conn, root, **kwargs):
        self.calls += 1
        self.started.set()
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("simulated collector failure")
        return usb.ScanResult(status="no_device", mode="physical")


def wait_for(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class WatcherLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempRoot()
        self.root = self.t.root
        self.collector = FakeCollector()
        self._patch = patch("flightdesk.server.usb.scan", self.collector)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        from flightdesk import errlog
        errlog.close_logger(self.root)
        self.t.close()

    def _app(self) -> App:
        return App(self.root)

    def test_enabling_starts_a_worker_that_scans(self) -> None:
        app = self._app()
        try:
            with _tx(app) as conn:
                usb.set_watcher(conn, True, 45)
            app.ensure_watcher_running()
            app._watcher_wake.set()
            self.assertTrue(self.collector.started.wait(3.0),
                            "worker never ran a scan")
            status = app.watcher_status()
            self.assertTrue(status["running"])
            self.assertTrue(wait_for(lambda: app.watcher_status()["last_success"]))
        finally:
            app.stop_watcher()

    def test_disabled_worker_does_not_scan(self) -> None:
        app = self._app()
        try:
            with _tx(app) as conn:
                usb.set_watcher(conn, False, 45)
            app.ensure_watcher_running()
            time.sleep(0.3)
            self.assertEqual(self.collector.calls, 0)
        finally:
            app.stop_watcher()

    def test_repeated_enable_makes_one_worker(self) -> None:
        app = self._app()
        try:
            self.assertEqual(app.ensure_watcher_running(), "started")
            first = app.watcher_thread
            self.assertEqual(app.ensure_watcher_running(), "already_running")
            self.assertIs(app.watcher_thread, first)
        finally:
            app.stop_watcher()

    def test_disable_stops_the_worker(self) -> None:
        app = self._app()
        try:
            app.ensure_watcher_running()
            self.assertTrue(app.watcher_status()["running"])
            app.stop_watcher()
            self.assertFalse(app.watcher_status()["running"])
        finally:
            app.stop_watcher()

    def test_manual_and_scheduled_scans_do_not_overlap(self) -> None:
        app = self._app()
        try:
            self.assertTrue(app._scan_lock.acquire(blocking=False))
            payload, busy = app.run_scan(reason="watcher")
            self.assertTrue(busy)
            self.assertEqual(payload["code"], "scan_in_progress")
            self.assertEqual(self.collector.calls, 0)
            app._scan_lock.release()
            payload, busy = app.run_scan(reason="manual")
            self.assertFalse(busy)
            self.assertEqual(self.collector.calls, 1)
        finally:
            app.stop_watcher()

    def test_a_failure_is_visible_and_a_retry_works(self) -> None:
        app = self._app()
        try:
            self.collector.fail_next = 1
            payload, busy = app.run_scan(reason="manual")
            self.assertFalse(busy)
            status = app.watcher_status()
            self.assertIsNotNone(status["last_error"])
            # A later attempt succeeds and records the success.
            payload, busy = app.run_scan(reason="manual")
            self.assertFalse(busy)
            self.assertEqual(payload["status"], "no_device")
            self.assertIsNotNone(app.watcher_status()["last_success"])
        finally:
            app.stop_watcher()

    def test_enabled_setting_survives_a_restart(self) -> None:
        app = self._app()
        try:
            with _tx(app) as conn:
                usb.set_watcher(conn, True, 45)
        finally:
            app.stop_watcher()
        app2 = self._app()
        try:
            with _tx(app2) as conn:
                self.assertTrue(db.get_setting(conn, "usb_watcher")["enabled"])
            app2.ensure_watcher_running()
            app2._watcher_wake.set()
            self.assertTrue(self.collector.started.wait(3.0))
        finally:
            app2.stop_watcher()


def _tx(app: App):
    """A fresh connection, closed by the caller block. The callee owns its tx."""
    class _Ctx:
        def __enter__(self):
            self.conn = app.connect()
            return self.conn

        def __exit__(self, exc_type, exc, tb):
            self.conn.close()
            return False

    return _Ctx()


class WatcherApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempRoot()
        self.collector = FakeCollector()
        self._patch = patch("flightdesk.server.usb.scan", self.collector)
        self._patch.start()
        self.server, self.app = build_server(self.t.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.app.session.token

    def tearDown(self) -> None:
        self.app.stop_watcher()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=20)
        self._patch.stop()
        from flightdesk import errlog
        errlog.close_logger(self.t.root)
        self.t.close()

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
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

    def test_the_toggle_controls_a_live_worker(self) -> None:
        status, body = self._call("POST", "/api/usb/watcher", {"enabled": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["watcher"]["enabled"])
        self.assertTrue(wait_for(lambda: self.collector.calls >= 1),
                        "enabling did not start a scan")
        self.assertTrue(body["runtime"]["running"])

        status, body = self._call("POST", "/api/usb/watcher", {"enabled": False})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["watcher"]["enabled"])
        self.assertFalse(body["runtime"]["running"])

    def test_configured_state_is_reported_separately_from_runtime(self) -> None:
        self._call("POST", "/api/usb/watcher", {"enabled": True})
        status, body = self._call("GET", "/api/usb")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["watcher"]["enabled"])   # configured
        self.assertIn("runtime", body)                 # executing
        self.assertTrue(body["runtime"]["running"])


if __name__ == "__main__":
    unittest.main()
