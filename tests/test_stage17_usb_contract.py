"""The controller contract: skip on name+size, and never fail silently.

Two defects from the independent review:

  * The collector skipped any file whose *name* was already archived, so an
    incomplete copy on the controller could never be replaced. Skipping is now
    name AND size.
  * `usb.scan` returned before writing a `usb_scans` row whenever the collector
    produced nothing, and the watcher swallowed every exception with `pass`. A
    timeout and a missing script now record themselves, and files already copied
    before a timeout are imported instead of discarded.

All fixtures are invented.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import TempCase, TempRoot, base_time, log_bytes

from flightdesk import db, errlog, usb, views
from flightdesk.server import build_server
from flightdesk.synth import filename_time


class ScanFailureTest(TempCase):
    def _rows(self):
        return db.all_rows(self.conn, "SELECT * FROM usb_scans ORDER BY id")

    def test_a_collector_timeout_leaves_a_scan_row(self) -> None:
        with mock.patch.object(usb, "_ps_json",
                               side_effect=usb.CollectorError("collector_timeout")):
            result = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "stage-t")
        self.assertEqual(result.status, "unavailable")
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("collector_timeout", rows[0]["detail"])

    def test_a_missing_collector_is_recorded_and_named(self) -> None:
        with mock.patch.object(usb, "_ps_json",
                               side_effect=usb.CollectorError("collector_missing")):
            usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "stage-m")
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("collector_missing", rows[0]["detail"])
        self.assertNotIn("timeout", rows[0]["detail"] or "")

    def test_files_copied_before_a_timeout_are_imported_not_discarded(self) -> None:
        # The collector copies each file into its own GUID folder. A timeout used
        # to delete the whole staging tree; a log that had arrived intact was
        # lost and the next pass repeated the same 200 seconds.
        staging = self.root / "tmp" / "stage-salvage"
        (staging / "a1b2c3").mkdir(parents=True)
        name = filename_time(base_time(), "FlightRecord")
        (staging / "a1b2c3" / name).write_bytes(log_bytes(11))
        with mock.patch.object(usb, "_ps_json",
                               side_effect=usb.CollectorError("collector_timeout")):
            result = usb.scan(self.conn, self.root, staging_root=staging)
        self.assertEqual(result.imported, 1, result.as_dict())
        self.assertEqual(self.counts()["flights"], 1)
        self.assertEqual(rows := self._rows(), rows)
        self.assertIn("salvaged_after_timeout", rows[0]["detail"])
        self.assertFalse((staging / "a1b2c3" / name).exists())

    def test_a_truncated_file_is_not_salvaged(self) -> None:
        # Salvage must not become a back door that accepts a half-copy.
        staging = self.root / "tmp" / "stage-trunc"
        (staging / "deadbeef").mkdir(parents=True)
        name = filename_time(base_time(), "FlightRecord")
        (staging / "deadbeef" / name).write_bytes(log_bytes(11)[:200])
        with mock.patch.object(usb, "_ps_json",
                               side_effect=usb.CollectorError("collector_timeout")):
            result = usb.scan(self.conn, self.root, staging_root=staging)
        self.assertEqual(result.imported, 0, result.as_dict())
        self.assertEqual(self.counts()["flights"], 0)
        self.assertIn("incomplete_or_invalid:copy_truncated", ",".join(result.notes))

    def test_status_reports_the_last_watcher_problem(self) -> None:
        with db.tx(self.conn):
            db.set_setting(self.conn, "usb_watcher_last_error",
                           {"at": db.now_utc(), "category": "OSError"})
        status = usb.status(self.conn, self.root)
        self.assertEqual(status["watcher_last_error"]["category"], "OSError")
        self.assertIn("flightdesk.log", status["log"]["path"])


class SkipContractTest(TempCase):
    """The simulated adapter mirrors the collector's skip rule."""

    def _entry(self, data: bytes, name: str) -> usb.SimEntry:
        return usb.SimEntry(device="DJI RC 2", target="FlightRecord", storage="Internal",
                            name=name, data=data)

    def test_the_same_name_and_size_is_skipped(self) -> None:
        data = log_bytes(11)
        name = filename_time(base_time(), "DJIFlightRecord")
        usb.simulate(self.conn, self.root, [self._entry(data, name)])
        result = usb.simulate(self.conn, self.root, [self._entry(data, name)])
        self.assertEqual(result.duplicates, 1)
        self.assertIn("skipped_known", result.notes)
        self.assertEqual(self.counts()["raw"], 1)

    def test_the_same_name_with_a_smaller_size_is_copied_again(self) -> None:
        # The archived copy carries extra trailing bytes; the device holds a
        # shorter but still valid log under the same name. A name-only skip
        # would have kept the wrong file for ever.
        name = filename_time(base_time(), "DJIFlightRecord")
        bigger = log_bytes(11) + b"\x00" * 64
        smaller = log_bytes(11)
        self.assertEqual(len(bigger), len(smaller) + 64)
        usb.simulate(self.conn, self.root, [self._entry(bigger, name)])
        self.assertEqual(self.counts()["raw"], 1)
        result = usb.simulate(self.conn, self.root, [self._entry(smaller, name)])
        self.assertIn("recopied_changed_size", result.notes)
        self.assertEqual(self.counts()["raw"], 2)
        # Both files are the same flight: a variant, not a second flight.
        self.assertEqual(self.counts()["flights"], 1)
        roles = sorted(r["role"] for r in db.all_rows(
            self.conn, "SELECT role FROM flight_files"))
        self.assertEqual(roles, ["primary", "variant"], roles)

    def test_the_validator_rejects_a_file_that_stops_before_its_details(self) -> None:
        data = log_bytes(11)
        ok, reason = usb.validate_flight_bytes(data[:250])
        self.assertFalse(ok)
        self.assertIn(reason, ("too_short", "bad_details_offset", "copy_truncated"))


class ErrorLogTest(unittest.TestCase):
    def test_a_route_that_raises_writes_a_line_to_the_log(self) -> None:
        t = TempRoot()
        try:
            server, app = build_server(t.root)
            import http.client
            import threading
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with mock.patch.object(views, "state", side_effect=RuntimeError("boom")):
                    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=20)
                    conn.request("GET", "/api/state", headers={
                        "Host": f"127.0.0.1:{app.port}",
                        "X-FD-Token": app.session.token,
                        "Cookie": f"fd_session={app.session.token}",
                    })
                    response = conn.getresponse()
                    body = response.read()
                    conn.close()
                self.assertEqual(response.status, 500, body[:200])
                self.assertIn(b"internal_error", body)
                text = (t.root / "logs" / "flightdesk.log").read_text(encoding="utf-8")
                self.assertIn("route GET /api/state", text)
                self.assertIn("RuntimeError", text)
            finally:
                server.shutdown()
                server.server_close()
                errlog.close_logger(t.root)
        finally:
            t.close()

    def test_the_log_scrubs_paths_outside_the_data_root_and_key_like_tokens(self) -> None:
        root = Path("C:/Users/owner/AppData/Local/FlightDesk/data")
        text = errlog._scrub(
            "failed on C:\\Users\\owner\\Pictures\\secret-flight.txt "
            "with key 0123456789abcdef0123456789abcdef", root)
        self.assertNotIn("secret-flight.txt", text)
        self.assertIn("<path>", text)
        self.assertNotIn("0123456789abcdef0123456789abcdef", text)
        self.assertIn("<redacted>", text)


if __name__ == "__main__":
    unittest.main()
