"""End-to-end rehearsal against a *fresh* data root using only local code.

Steps, all through the application modules (no model file reads of private
data):
  1. import a folder through the real importer
  2. re-import the same folder (must add nothing)
  3. apply the equipment capture
  4. create and validate a backup
  5. restore into a brand-new root and verify references
  6. export CSVs and a reconciliation report
  7. verify idempotency of the capture and of a second backup

Prints aggregate counts only.
"""
from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import backup, capture, db, maintenance, report  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402
from flightdesk.paths import ensure_layout  # noqa: E402
from flightdesk.synth import make_log_bytes, filename_time  # noqa: E402
import datetime as dt  # noqa: E402


def step(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="fd-rehearsal-"))
    try:
        source = base / "supplied" / "flight_logs"
        source.mkdir(parents=True)
        start = dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc)
        for index in range(4):
            data = make_log_bytes(
                version=11 if index % 2 else 8,
                start=start + dt.timedelta(days=index * 5),
                aircraft=("mavic2", "air3s", "mini3", "mavic2")[index],
                duration_s=302.0 + index * 60,
                distance_m=46.9 + index * 90,
                max_height_m=45.1 + index * 5,
            )
            (source / filename_time(start + dt.timedelta(days=index * 5),
                                    "DJIFlightRecord")).write_bytes(data)
        extras = base / "supplied" / "controller-test"
        extras.mkdir(parents=True)
        (extras / "DJIFlightRecord_2026-05-01_[12-00-00].txt").write_bytes(
            make_log_bytes(version=11, start=dt.datetime(2026, 5, 1, 12, tzinfo=dt.timezone.utc),
                           aircraft="air3s", duration_s=298.0, distance_m=70.0)
        )

        live = base / "live"
        ensure_layout(live)
        conn = db.init_db(live / "flightdesk.sqlite3")
        importer = Importer(live, conn)
        first = importer.import_folder(source, default_kind="archive")
        second = importer.import_folder(extras, default_kind="extras")
        step("fresh import accepts every file",
             first.totals["new"] == 4 and second.totals["new"] == 1,
             f"archive={first.totals['new']} extras={second.totals['new']}")
        repeat = importer.import_folder(source, default_kind="archive")
        step("re-import adds nothing",
             repeat.totals["new"] == 0 and repeat.totals["already_imported"] == 4,
             f"new={repeat.totals['new']} dup={repeat.totals['already_imported']}")

        doc = capture.parse_capture(
            (ROOT / "fixtures" / "synthetic" / "AirData-Equipment-Capture-SYNTHETIC.md")
            .read_text(encoding="utf-8"))
        seed = capture.apply_capture(conn, doc, "Rehearsal capture")
        step("capture migration is distinguishable",
             seed["services_inserted"] == 21 and seed["grouped_services"] == 4,
             f"services={seed['services_inserted']} grouped={seed['grouped_services']}")
        again = capture.apply_capture(conn, doc, "Rehearsal capture")
        step("repeat capture migration is a no-op", again["already_applied"])

        dest = base / "backups"
        result = backup.create_backup(conn, live, dest, "rehearsal")
        step("backup created and hashed", bool(result.get("ok")), result.get("code", ""))
        check = backup.validate_backup(Path(result["path"]))
        step("backup validates", bool(check.get("ok")), ",".join(check.get("errors", []))[:80])

        restored = base / "restored"
        outcome = backup.restore_backup(Path(result["path"]), restored)
        step("restore into a new root", bool(outcome.get("ok")), outcome.get("code", ""))
        rconn = db.init_db(restored / "flightdesk.sqlite3")
        fk = rconn.execute("PRAGMA foreign_key_check").fetchall()
        integrity = rconn.execute("PRAGMA integrity_check").fetchone()[0]
        step("restored database passes integrity and references",
             integrity == "ok" and not fk, f"fk={len(fk)}")
        before = report.reconciliation(conn, persist=False)
        after = report.reconciliation(rconn, persist=False)
        step("restored totals equal the live totals",
             before["groups"]["combined"]["raw_files"] == after["groups"]["combined"]["raw_files"]
             and before["groups"]["combined"]["logical_flights"]
             == after["groups"]["combined"]["logical_flights"])
        statuses_live = {(s["equipment_name"], s["code"]): s["status"]
                         for s in maintenance.all_statuses(conn, include_archived=True)}
        statuses_restored = {(s["equipment_name"], s["code"]): s["status"]
                             for s in maintenance.all_statuses(rconn, include_archived=True)}
        step("restored maintenance statuses match", statuses_live == statuses_restored,
             f"rules={len(statuses_live)}")

        flights_csv = report.flights_csv(conn)
        redacted = report.flights_csv(conn, redact=True)
        step("exports produced with and without identifiers",
             "aircraft_serial" in flights_csv and "aircraft_serial" not in redacted)
        recon_csv = report.report_csv(before)
        step("reconciliation report is readable", "combined" in recon_csv)

        # a second backup must not disturb the first
        second_backup = backup.create_backup(conn, live, dest, "rehearsal 2")
        step("second backup is independent",
             second_backup["path"] != result["path"] and second_backup["ok"])

        print(json.dumps({
            "distinct_files": before["groups"]["combined"]["raw_files"],
            "logical_flights": before["groups"]["combined"]["logical_flights"],
            "rules": len(statuses_live),
            "services": conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
            "backups": len(list(dest.glob("*.zip"))),
        }))
        conn.close()
        rconn.close()
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
