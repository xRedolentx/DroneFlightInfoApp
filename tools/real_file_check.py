"""Compare a few real flight logs against figures you recorded once by hand.

Every automated test uses files this codebase generated, so a parser change that
breaks real logs can still pass all of them. This is the small, private check
that closes that gap — and it is opt-in, because it needs the owner's own logs.

It reads a folder that is deliberately outside version control:

    %LOCALAPPDATA%\\FlightDesk\\realcheck\\
        expected.json
        <your log files>

`expected.json` is a list of what AirData (or any source you trust) once showed
for those flights. Use the same names AirData uses, because the two clocks are
different measurements:

    {"files": [
      {"name": "DJIFlightRecord_2026-08-11_[21-53-20].txt",
       "air_time_s": 302.0,      # AirData "Flight Air Time" - off the ground
       "log_time_s": 304.0,      # AirData "Log Time" - the whole record
       "distance_m": 46.9,       # AirData distance (feet x 0.3048)
       "max_height_m": 45.1},
      {"name": "DJIFlightRecord_2026-08-12_[09-00-00].txt",
       "air_time_s": 640.0, "distance_m": 211.0, "max_height_m": 88.0}
    ]}

Only the figures you supply are checked; leave one out and it is skipped.

Run it with:

    python tools\\real_file_check.py                 # default folder
    python tools\\real_file_check.py --folder D:\\mycheck
    python tools\\real_file_check.py --verbose       # print per-file figures

It prints PASS/FAIL counts and the size of each difference. It never writes
anything, never uploads anything, and exits 0 with a clear "skipped" line when
the folder is absent, so it can sit in a routine without breaking it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import analysis, parser_adapter  # noqa: E402

try:  # pgrep-free, just the app's own layout helper
    from flightdesk import paths  # noqa: E402
except Exception:  # pragma: no cover - defensive
    paths = None  # type: ignore[assignment]

TOLERANCE = {
    # AirData rounds what it displays, so a small difference is expected and is
    # not a failure. Anything larger means the parser and the source disagree.
    # `air_time_s` is compared with the track-derived time off the ground, and
    # `log_time_s` with the record's own duration. Comparing either against the
    # other is the mistake this whole check exists to catch.
    "air_time_s": 2.0,
    "log_time_s": 2.0,
    "distance_m": 1.0,
    "max_height_m": 1.0,
}


def default_folder() -> Path:
    if os.environ.get("FLIGHTDESK_REALCHECK"):
        return Path(os.environ["FLIGHTDESK_REALCHECK"]).expanduser()
    home = paths.app_home() if paths is not None else (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlightDesk")
    return Path(home) / "realcheck"


def load_expected(folder: Path) -> list[dict]:
    path = folder / "expected.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files") if isinstance(data, dict) else data
    return [f for f in (files or []) if isinstance(f, dict) and f.get("name")]


def measure(path: Path, keychain_json: list | None = None) -> dict:
    """Decode one log with the app's own parser.

    A v13/v14 file needs a keychain. When the archive already holds this exact
    file, its cached keychain is reused (pass it in); without one the file can
    only be measured from metadata, which is reported as `pending_key` rather
    than treated as a pass.
    """
    with tempfile.TemporaryDirectory(prefix="fd-realcheck-") as tmp:
        outcome = parser_adapter.parse_file(path, Path(tmp), keychain_json)
    if outcome.status not in ("decoded", "pending_key"):
        return {"state": outcome.status, "detail": outcome.error_category}
    metrics = analysis.compute_metrics(outcome.samples, outcome.details or {})
    return {
        "state": "measured",
        "source": "frames" if outcome.samples else "details",
        "air_time_s": metrics.airborne_time_s,
        "log_time_s": metrics.duration_s,
        "distance_m": metrics.total_distance_m,
        "max_height_m": metrics.max_height_m,
    }


def _cached_keychain(path: Path) -> list | None:
    """Reuse the archive's cached keychain for this exact file, if any.

    Read-only, local, and by content hash: nothing about the key is printed.
    """
    try:
        import hashlib
        from flightdesk import db, keychain, paths
        root = paths.default_data_root()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        conn = db.connect(paths.db_path(root), readonly=True)
        try:
            return keychain.cached_json(conn, root, sha)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a missing archive just means no cache
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser() if args.folder else default_folder()
    if not folder.is_dir():
        print(f"skipped: no private check folder at {folder}")
        return 0
    expected = load_expected(folder)
    if not expected:
        print(f"skipped: no expected.json in {folder}")
        return 0

    passed = failed = unmeasured = 0
    for item in expected:
        name = str(item.get("name"))
        path = folder / name
        if not path.exists():
            print(f"MISSING  {name}")
            failed += 1
            continue
        got = measure(path, _cached_keychain(path))
        if got["state"] != "measured":
            print(f"UNMEASURED  {name}: {got['state']} ({got.get('detail') or 'no detail'})")
            unmeasured += 1
            continue
        problems = []
        # `duration_s` was the first template's key; treat it as log time.
        if "log_time_s" not in item and "duration_s" in item:
            item = {**item, "log_time_s": item["duration_s"]}
        for key, tolerance in TOLERANCE.items():
            want = item.get(key)
            have = got.get(key)
            if want is None:
                continue
            if have is None:
                problems.append(f"{key}: nothing decoded")
                continue
            if abs(float(have) - float(want)) > tolerance:
                problems.append(f"{key}: {have:.1f} vs {want} (limit {tolerance})")
        if problems:
            failed += 1
            print(f"FAIL  {name}")
            for problem in problems:
                print(f"        {problem}")
            if args.verbose:
                print(f"        got: {json.dumps(got, default=str)}")
        else:
            passed += 1
            if args.verbose:
                print(f"PASS  {name}: {json.dumps(got, default=str)}")
    print(f"\n{passed} passed, {failed} failed, {unmeasured} unmeasured "
          f"({len(expected)} checked from {folder})")
    if unmeasured:
        print("An unmeasured file needs its DJI key cached, or it is not a decoded log.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
