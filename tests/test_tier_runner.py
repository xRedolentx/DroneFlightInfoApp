"""P13: the test runner must account for every module and never fake a pass."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import unittest
from unittest.mock import patch

import support  # noqa: F401 - puts the repository root on sys.path
from tools import run_tests

TESTS = pathlib.Path(__file__).resolve().parent


def _run_tiered(available: dict, tiers: list[str] | None, result: dict) -> tuple[int, dict]:
    """Run main() with the suite patched out, so a test never runs the suite."""
    argv = ["--json"] + (["--tier", tiers[0]] if tiers else [])
    buf = io.StringIO()
    with patch.object(run_tests, "_run_modules", return_value=result), \
            patch.object(run_tests, "prerequisites", return_value=available), \
            contextlib.redirect_stdout(buf):
        code = run_tests.main(argv)
    return code, json.loads(buf.getvalue())


class CoverageTest(unittest.TestCase):
    def test_every_test_module_belongs_to_exactly_one_tier(self) -> None:
        # A module that falls outside every tier would be silently unrun, which
        # is the same failure mode as a test that cannot fail.
        on_disk = {p.stem for p in TESTS.glob("test_*.py")}
        in_tiers = run_tests.DOMAIN + run_tests.PARSER
        self.assertEqual(len(in_tiers), len(set(in_tiers)), "a module is listed twice")
        self.assertEqual(set(in_tiers), on_disk,
                         f"unassigned: {sorted(on_disk - set(in_tiers))} "
                         f"missing: {sorted(set(in_tiers) - on_disk)}")

    def test_both_tiers_are_mandatory(self) -> None:
        self.assertTrue(run_tests.TIERS["domain"]["mandatory"])
        self.assertTrue(run_tests.TIERS["parser"]["mandatory"])


class ReadinessTest(unittest.TestCase):
    FAKE_PASS = {"run": 3, "failures": 0, "errors": 0, "skipped": 0, "modules": {}}

    def _run(self, available: dict, tiers: list[str] | None = None,
             result: dict | None = None) -> tuple[int, dict]:
        return _run_tiered(available, tiers, result or self.FAKE_PASS)

    def test_a_missing_parser_is_not_run_and_fails_readiness(self) -> None:
        code, report = self._run({"pinned_parser": False, "windows": True,
                                  "dpapi": True, "live_browser": False})
        self.assertEqual(code, 3)
        self.assertEqual(report["tiers"]["parser"]["state"], "NOT RUN")
        self.assertFalse(report["release_ready"])
        self.assertIn("parser:not_run_missing_pinned_parser", report["blockers"])

    def test_the_domain_tier_runs_even_when_the_parser_is_missing(self) -> None:
        code, report = self._run({"pinned_parser": False, "windows": True,
                                  "dpapi": True, "live_browser": False})
        self.assertEqual(report["tiers"]["domain"]["state"], "PASS")
        self.assertEqual(report["tiers"]["parser"]["state"], "NOT RUN")
        self.assertEqual(code, 3)

    def test_the_browser_tier_is_never_reported_as_run(self) -> None:
        code, report = self._run({"pinned_parser": True, "windows": True,
                                  "dpapi": True, "live_browser": False},
                                 tiers=["browser"])
        self.assertEqual(report["tiers"]["browser"]["state"], "NOT RUN")
        self.assertIn("ui_gate.ps1", report["tiers"]["browser"]["reason"])
        self.assertEqual(code, 0)  # not mandatory in this process

    def test_a_tier_that_passes_reports_pass(self) -> None:
        code, report = self._run({"pinned_parser": True, "windows": True,
                                  "dpapi": True, "live_browser": False},
                                 tiers=["domain"])
        self.assertEqual(code, 0)
        self.assertEqual(report["tiers"]["domain"]["state"], "PASS")
        self.assertEqual(report["tiers"]["domain"]["run"], 3)
        self.assertTrue(report["release_ready"])


class FailureTest(unittest.TestCase):
    def test_a_failing_tier_fails_readiness(self) -> None:
        fake = {"run": 3, "failures": 1, "errors": 0, "skipped": 0, "modules": {}}
        code, report = _run_tiered({"pinned_parser": True, "windows": True,
                                    "dpapi": True, "live_browser": False},
                                   ["domain"], fake)
        self.assertEqual(code, 3)
        self.assertEqual(report["tiers"]["domain"]["state"], "FAIL")
        self.assertIn("domain:failed", report["blockers"])


if __name__ == "__main__":
    unittest.main()
