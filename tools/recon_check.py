"""Check reconciliation arithmetic against known synthetic inputs.

Three archive flights of exactly 10 air minutes and 1 mile each, plus one
controller extra. The archive view must report 3 files, 30 minutes and 3 miles.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flightdesk import db, report  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402
from flightdesk.paths import ensure_layout  # noqa: E402
from flightdesk.synth import make_log_bytes  # noqa: E402


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="fd-recon-check-"))
    root = base / "data"
    ensure_layout(root)
    conn = db.init_db(root / "flightdesk.sqlite3")
    importer = Importer(root, conn)
    start = dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc)
    for index in range(3):
        importer.import_stream(
            io.BytesIO(make_log_bytes(
                version=11 if index % 2 else 8,
                start=start + dt.timedelta(days=index),
                duration_s=600.0,
                distance_m=1609.344,
            )),
            f"DJIFlightRecord_{index}.txt",
            source_kind="archive",
            source_label="archive",
        )
    importer.import_stream(
        io.BytesIO(make_log_bytes(version=11, start=start + dt.timedelta(days=9),
                                  aircraft="air3s")),
        "DJIFlightRecord_extra.txt",
        source_kind="extras",
        source_label="controller",
    )
    data = report.reconciliation(conn)
    archive = data["groups"]["archive"]
    checks = [
        ("archive files", archive["raw_files"], 3),
        ("archive flights", archive["logical_flights"], 3),
        ("archive air minutes", round(archive["air_minutes"], 3), 30.0),
        ("archive miles", round(archive["miles"], 3), 3.0),
        ("controller extra counted separately",
         data["groups"]["supplied_controller"]["raw_files"], 1),
        ("combined", data["groups"]["combined"]["raw_files"], 4),
        ("decoded coverage", archive["decoded_coverage"], 1.0),
        ("reference is recorded with a scope", data["reference"]["scope"], "archive"),
        ("no signed difference on synthetic data", data["signed_differences"], None),
    ]
    failed = 0
    for label, got, want in checks:
        ok = got == want
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {label} :: {got!r}")
    conn.close()
    print(f"SUMMARY {'PASS' if failed == 0 else 'FAIL'} checks={len(checks)} failures={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
