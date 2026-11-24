"""The launcher: clicking FlightDesk.cmd lands on the home page, once.

Two failures this covers, both seen from the owner's side of the click:

  * the address changed on every launch, so a bookmark or a restored tab
    pointed at a port that no longer answered;
  * a second click started a second server on another free port instead of
    bringing the running page back, which looks like the icon doing nothing.

Everything here is loopback-only and synthetic.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import TempRoot  # noqa: F401  (puts the repo on sys.path)

from flightdesk import __main__ as launcher
from flightdesk import paths
from flightdesk.server import build_server, write_state


class HomeUrl(unittest.TestCase):
    def test_lands_on_the_flights_page(self) -> None:
        self.assertEqual(launcher.home_url(8765), "http://127.0.0.1:8765/#/flights")

    def test_loopback_only(self) -> None:
        self.assertTrue(launcher.home_url(8765).startswith("http://127.0.0.1:"))


class LiveInstance(unittest.TestCase):
    """Detection never mistakes a stale state file for a running app."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fd-launch-")
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()
        self._env = mock.patch.dict(os.environ, {"FLIGHTDESK_HOME": str(self.home)})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def state(self, **values) -> None:
        (self.home / "server.json").write_text(json.dumps(values), encoding="utf-8")

    def test_no_state_file(self) -> None:
        self.assertIsNone(launcher.live_instance(self.root))

    def test_unreadable_state_file(self) -> None:
        (self.home / "server.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(launcher.live_instance(self.root))

    def test_dead_process_is_not_reused(self) -> None:
        self.state(pid=999_999, port=8765, root=str(self.root))
        self.assertIsNone(launcher.live_instance(self.root))

    def test_different_root_is_not_reused(self) -> None:
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        self.state(pid=os.getpid(), port=8765, root=str(other))
        self.assertIsNone(launcher.live_instance(self.root))

    def test_live_pid_with_nothing_listening_is_not_reused(self) -> None:
        self.state(pid=os.getpid(), port=1, root=str(self.root))
        self.assertIsNone(launcher.live_instance(self.root))

    def test_explicit_port_never_redirects(self) -> None:
        self.state(pid=os.getpid(), port=8765, root=str(self.root))
        self.assertIsNone(launcher.live_instance(self.root, port=8799))

    def test_running_server_is_reused(self) -> None:
        temp = TempRoot()
        server, app = build_server(temp.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            write_state(temp.root, app.port)
            self.assertEqual(launcher.live_instance(temp.root), app.port)
        finally:
            server.shutdown()
            server.server_close()
            temp.close()


class ServeEntry(unittest.TestCase):
    """`serve` opens the home page and never starts a second copy."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fd-serve-")
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()
        self._env = mock.patch.dict(os.environ, {
            "FLIGHTDESK_HOME": str(self.home),
            "FLIGHTDESK_DATA_ROOT": str(self.root),
        })
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def start_instance(self):
        """A real server on a synthetic root, recorded as the running app.

        The data root comes from the environment, so the recorded state and the
        root `serve` resolves are the same directory — otherwise the reuse check
        is being tested against a different folder and means nothing.
        """
        temp = TempRoot()
        server, app = build_server(temp.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        os.environ["FLIGHTDESK_DATA_ROOT"] = str(temp.root)
        write_state(temp.root, app.port)
        return temp, server, app

    def stop_instance(self, temp, server) -> None:
        server.shutdown()
        server.server_close()
        temp.close()

    def test_fresh_start_opens_the_home_page(self) -> None:
        class FakeServer:
            def serve_forever(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        class FakeApp:
            port = 8799
            stop_event = threading.Event()

            def start_watcher(self) -> None:
                return None

            def ensure_watcher_running(self) -> str:
                return "started"

        with mock.patch("flightdesk.server.build_server",
                        return_value=(FakeServer(), FakeApp())), \
                mock.patch.object(launcher.threading, "Timer") as timer:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(launcher.main(["serve"]), 0)

        self.assertIn("http://127.0.0.1:8799/#/flights", out.getvalue())
        timer.assert_called_once()
        self.assertIs(timer.call_args.args[1], launcher._open_browser)
        self.assertEqual(timer.call_args.kwargs["args"], (launcher.home_url(8799),))

    def test_second_click_reuses_the_running_app(self) -> None:
        temp, server, app = self.start_instance()
        try:
            recorded = (self.home / "server.json").read_text(encoding="utf-8")
            opened: list[str] = []
            with mock.patch("flightdesk.server.build_server") as build, \
                    mock.patch.object(launcher.webbrowser, "open",
                                      side_effect=lambda url: opened.append(url) or True):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(launcher.main(["serve"]), 0)
            build.assert_not_called()
            self.assertEqual(opened, [launcher.home_url(app.port)])
            self.assertIn("already running", out.getvalue())
            self.assertEqual((self.home / "server.json").read_text(encoding="utf-8"),
                             recorded)
        finally:
            self.stop_instance(temp, server)

    def test_no_browser_flag_prints_the_address_only(self) -> None:
        temp, server, app = self.start_instance()
        try:
            with mock.patch.object(launcher.webbrowser, "open") as open_mock:
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(launcher.main(["serve", "--no-browser"]), 0)
            open_mock.assert_not_called()
            self.assertIn(launcher.home_url(app.port), out.getvalue())
        finally:
            self.stop_instance(temp, server)

    def test_a_browser_that_will_not_open_says_so(self) -> None:
        with mock.patch.object(launcher.webbrowser, "open", return_value=False):
            out = io.StringIO()
            with redirect_stdout(out):
                launcher._open_browser("http://127.0.0.1:8799/#/flights")
        self.assertIn("http://127.0.0.1:8799/#/flights", out.getvalue())
        self.assertIn("Could not open a browser", out.getvalue())


if __name__ == "__main__":
    unittest.main()
