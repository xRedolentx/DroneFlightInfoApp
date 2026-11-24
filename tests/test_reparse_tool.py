"""P08/F12: the repair tool's dry run computes without writing.

The reviewed tool opened a read-only connection and called the mutating
importer, so it exited with a readonly error instead of a preview, never reset
its consecutive-failure counter, and always restarted from raw id 1.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TempCase, log_bytes

from flightdesk import db
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome
from tools import reparse_all


def outcome(duration: float = 120.0) -> ParseOutcome:
    return ParseOutcome(
        11, "decoded",
        details={"start_time_utc": "2026-01-01T00:00:00+00:00", "aircraft_sn": "SN-1",
                 "aircraft_name": "Aircraft", "total_time_s": duration,
                 "total_distance_m": 10.0},
        samples=[],
    )


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed(test: TempCase, count: int = 3) -> None:
    imp = Importer(test.root, test.conn)
    for i in range(count):
        imp.import_stream(
            io.BytesIO(log_bytes(11, days=i)), f"FlightRecord_2026-01-0{i + 1}_[00-00-00].txt",
            source_kind="manual", source_label="t")
    test.conn.commit()


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = reparse_all.main(argv)
    return code, buf.getvalue()


class DryRunTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        _seed(self, 3)
        self.db_file = self.root / "flightdesk.sqlite3"
        self.db_before = _sha_file(self.db_file)
        self.archive_before = {
            p: _sha_file(p) for p in (self.root / "archive").rglob("*.bin")
        }

    def test_dry_run_reports_deltas_and_writes_nothing(self) -> None:
        # Pretend the parser now measures a longer duration.
        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            code, text = _run(["--root", str(self.root), "--json"])
        report = json.loads(text)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["files"], 3)
        self.assertGreaterEqual(report["changed_flights"], 1)
        self.assertNotEqual(report["before"], report["after"])
        # The source database and every original are untouched.
        self.assertEqual(_sha_file(self.db_file), self.db_before)
        self.assertEqual({p: _sha_file(p) for p in (self.root / "archive").rglob("*.bin")},
                         self.archive_before)

    def test_apply_requires_an_explicit_root(self) -> None:
        code, text = _run(["--apply"])
        self.assertEqual(code, 2)
        self.assertIn("explicit --root", text)

    def test_apply_with_the_preview_revision_matches_it(self) -> None:
        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            _, text = _run(["--root", str(self.root), "--json"])
        preview = json.loads(text)
        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            code, text = _run(["--root", str(self.root), "--apply", "--json",
                               "--expect-revision", preview["revision"]])
        applied = json.loads(text)
        self.assertEqual(code, 0, applied)
        self.assertEqual(applied["mode"], "apply")
        self.assertEqual(applied["after"], preview["after"])
        # And the real database now holds the previewed figure.
        conn = db.connect(self.db_file, readonly=True)
        try:
            duration = conn.execute("SELECT duration_s FROM flights").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(duration, 180.0)

    def test_a_stale_preview_is_refused(self) -> None:
        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            code, text = _run(["--root", str(self.root), "--apply", "--json",
                               "--expect-revision", "not-the-revision"])
        report = json.loads(text)
        self.assertEqual(code, 2, report)
        self.assertEqual(report["code"], "stale_preview")
        self.assertEqual(_sha_file(self.db_file), self.db_before)

    def test_a_success_resets_the_consecutive_failure_streak(self) -> None:
        failures = {"n": 0}
        original = Importer.reparse_raw

        def alternating(self, raw_id):
            failures["n"] += 1
            if failures["n"] % 2 == 1:
                raise RuntimeError("simulated per-file failure")
            return original(self, raw_id)

        with patch.object(Importer, "reparse_raw", alternating):
            code, text = _run(["--root", str(self.root), "--json", "--apply",
                               "--restart"])
        report = json.loads(text)
        # Ten-ish raw calls, alternating, must not stop at five consecutive:
        # a success in between resets the counter.
        self.assertIsNone(report["outcome"]["stopped"], report)
        self.assertGreater(report["outcome"]["failed"], 0)

    def test_resume_skips_completed_files(self) -> None:
        with db.tx(self.conn):
            db.set_setting(self.conn, reparse_all.CURSOR_SETTING, 2)
        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            code, text = _run(["--root", str(self.root), "--json", "--apply"])
        report = json.loads(text)
        self.assertEqual(code, 0, report)
        # Ids 1 and 2 are behind the cursor; only id 3 remains.
        self.assertEqual(report["outcome"]["total"], 1)

        with patch("flightdesk.importer.parse_file", return_value=outcome(180.0)):
            code, text = _run(["--root", str(self.root), "--json", "--apply"])
        report = json.loads(text)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["outcome"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
