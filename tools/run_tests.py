"""Run the suite in tiers, and say what actually ran.

    python tools\\run_tests.py             # all runnable tiers, human summary
    python tools\\run_tests.py --json      # machine-readable
    python tools\\run_tests.py --tier domain

Why tiers exist: on a machine without the pinned parser, 78 tests fail and 14
error — not because the app is broken but because those tests decode real
DJI-format logs. Reporting that as "the suite is red" hides which defects are
real. A tier whose prerequisite is absent is reported as **NOT RUN**, never as a
pass, and release readiness fails while a mandatory tier could not run.

The module lists below were measured, not guessed: every module was run with
`FLIGHTDESK_DJI_LOG` pointed at a path that does not exist, and the parser tier
is exactly the set that failed or errored. Re-measure if a module changes what
it imports.

The browser gate is a separate tier (tools\\ui_gate.ps1); it needs a live Edge and
a server, so it is not part of this process.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOMAIN = [  # parser-independent: they inject parsed outcomes or test pure logic
    "test_airtime_semantics", "test_backup_completeness", "test_derived_refresh",
    "test_env_check", "test_identity_conservative", "test_identity_review",
    "test_launcher", "test_restore_protection", "test_stage10_temperature",
    "test_stage11_schedule", "test_stage6_analysis", "test_stage8_map",
    "test_stage8_map_view", "test_stage9_warnings", "test_tier_runner",
    "test_ui_small_fixes", "test_watcher_lifecycle",
]

PARSER = [  # exercise the pinned dji-log binary on synthetic DJI-format logs
    "test_archive_integrity", "test_demo_isolation", "test_diag_privacy",
    "test_reparse_tool", "test_stage15_bad_details", "test_stage16_fingerprints",
    "test_stage17_usb_contract", "test_stage18_raw_export", "test_stage19_ui_text",
    "test_stage1_import", "test_stage20_batching", "test_stage21_concurrent_backup",
    "test_stage22_airtime", "test_stage2_equipment", "test_stage3_web",
    "test_stage4_usb", "test_stage5_key", "test_stage7_backup",
    "test_usb_completeness",
]

TIERS = {
    "domain": {"modules": DOMAIN, "requires": [], "mandatory": True},
    "parser": {"modules": PARSER, "requires": ["pinned_parser"], "mandatory": True},
    "browser": {"modules": [], "requires": ["live_browser"],
                "mandatory": False, "external": "tools\\ui_gate.ps1"},
}


def prerequisites() -> dict[str, bool]:
    from flightdesk import paths
    parser = paths.parser_exe()
    return {
        "pinned_parser": parser.is_file(),
        "windows": os.name == "nt",
        "dpapi": os.name == "nt",
        "live_browser": False,  # never true here: the gate is a separate command
    }


def _run_modules(modules: list[str]) -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    loader = unittest.TestLoader()
    totals = {"run": 0, "failures": 0, "errors": 0, "skipped": 0, "modules": {}}
    for name in modules:
        try:
            suite = loader.loadTestsFromName(name)
        except Exception as exc:  # a module that will not import is a failure
            totals["modules"][name] = {"run": 0, "failures": 0, "errors": 1,
                                       "detail": f"import failed: {type(exc).__name__}"}
            totals["errors"] += 1
            continue
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        entry = {"run": result.testsRun, "failures": len(result.failures),
                 "errors": len(result.errors), "skipped": len(result.skipped)}
        totals["modules"][name] = entry
        for key in ("run", "failures", "errors", "skipped"):
            totals[key] += entry[key]
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tier", action="append", default=None,
                        help="run only these tiers (repeatable)")
    args = parser.parse_args(argv)

    available = prerequisites()
    wanted = args.tier or list(TIERS)
    report = {"os": platform.platform(), "prerequisites": available, "tiers": {},
              "release_ready": True, "blockers": []}

    for tier_name in wanted:
        tier = TIERS.get(tier_name)
        if tier is None:
            report["blockers"].append(f"unknown_tier:{tier_name}")
            report["release_ready"] = False
            continue
        missing = [req for req in tier["requires"] if not available.get(req)]
        if tier.get("external"):
            report["tiers"][tier_name] = {
                "state": "NOT RUN", "reason": f"run {tier['external']} separately",
                "mandatory": tier["mandatory"]}
            if tier["mandatory"] and missing:
                report["blockers"].append(tier_name + ":" + ",".join(missing))
            continue
        if missing:
            report["tiers"][tier_name] = {
                "state": "NOT RUN", "reason": "missing " + ", ".join(missing),
                "mandatory": tier["mandatory"]}
            if tier["mandatory"]:
                report["release_ready"] = False
                report["blockers"].append(
                    f"{tier_name}:not_run_missing_{'_'.join(missing)}")
            continue
        result = _run_modules(tier["modules"])
        state = "PASS" if result["failures"] == 0 and result["errors"] == 0 else "FAIL"
        report["tiers"][tier_name] = {"state": state, **result,
                                      "mandatory": tier["mandatory"]}
        if state == "FAIL" and tier["mandatory"]:
            report["release_ready"] = False
            report["blockers"].append(f"{tier_name}:failed")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"OS: {report['os']}")
        for name, item in report["tiers"].items():
            if item.get("state") == "NOT RUN":
                print(f"  {name:<8} NOT RUN — {item['reason']}")
            else:
                print(f"  {name:<8} {item['state']} — {item['run']} tests, "
                      f"{item['failures']} failures, {item['errors']} errors, "
                      f"{item['skipped']} skipped")
        print("RELEASE READINESS: " + ("PASS" if report["release_ready"] else "FAIL"))
        for blocker in report["blockers"]:
            print("  blocked by: " + blocker)
    return 0 if report["release_ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
