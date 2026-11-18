"""Fail-closed test for the aggregate-only diagnostic runner.

Runs tools/diag.py against a folder whose filenames and serials are obviously
private, then asserts that none of those strings appear in its output. This is
the mechanism that keeps private inputs out of the transcript.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from flightdesk.synth import make_log_bytes  # noqa: E402
import datetime as dt  # noqa: E402

SECRET_NAME = "HomeAddressKevin_2026"
SECRET_SERIAL = "KEVINPRIVATESERIAL1"


class DiagPrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="fd-diag-test-")
        self.base = Path(self.tmp.name)
        folder = self.base / "private_flight_logs"
        folder.mkdir(parents=True)
        start = dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc)
        self.paths_written = []
        for index in range(3):
            data = make_log_bytes(
                version=11 if index % 2 else 8,
                start=start + dt.timedelta(days=index),
            )
            path = folder / f"DJIFlightRecord_{SECRET_NAME}_record_{index}.txt"
            path.write_bytes(data)
            self.paths_written.append(path)
        # a damaged file must also stay anonymous in the summary
        (folder / f"DJIFlightRecord_{SECRET_NAME}_broken.txt").write_bytes(b"\x00" * 64)
        self.folder = folder

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, extra: list[str]) -> str:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "diag.py"), str(self.folder), *extra],
            capture_output=True, timeout=600,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT),
                 "PYTHONUTF8": "1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace")[:400])
        return proc.stdout.decode("utf-8", "replace")

    def test_text_output_contains_no_private_values(self) -> None:
        out = self._run([])
        self.assertIn("distinct_files=", out)
        for secret in (SECRET_NAME, SECRET_SERIAL, str(self.base)):
            self.assertNotIn(secret, out)
        self.assertNotIn(".txt", out)

    def test_json_output_is_restricted_to_permitted_fields(self) -> None:
        out = self._run(["--json"])
        payload = json.loads(out)
        allowed = {"ok", "sources_reported", "distinct_files", "logical_flights",
                   "unresolved_flights", "errors", "second_pass", "groups"}
        self.assertTrue(set(payload) <= allowed, set(payload) - allowed)
        for group in payload["groups"]:
            self.assertLessEqual(
                set(group),
                {"source", "totals", "by_version", "by_status", "note"},
            )
            for key in ("totals",):
                self.assertLessEqual(
                    set(group[key]),
                    {"discovered", "new", "already_imported", "awaiting_dji",
                     "unsupported", "needs_attention", "new_flight"},
                )
        text = json.dumps(payload)
        for secret in (SECRET_NAME, SECRET_SERIAL, str(self.base)):
            self.assertNotIn(secret, text)

    def test_counts_are_reported_and_second_pass_is_idempotent(self) -> None:
        payload = json.loads(self._run(["--json"]))
        self.assertEqual(payload["distinct_files"], 4)   # three logs plus one damaged file
        self.assertEqual(payload["logical_flights"], 3)
        self.assertEqual(payload["second_pass"]["flights_added"], 0)
        statuses = payload["groups"][0]["by_status"]
        self.assertEqual(statuses.get("damaged"), 1)
        self.assertEqual(statuses.get("imported"), 3)


if __name__ == "__main__":
    unittest.main()
