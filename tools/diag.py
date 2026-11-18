"""Aggregate-only diagnostic runner for real local inputs.

Contract: this prints counts by source, format version and status, plus
coarse error categories. It never prints filenames, member paths, serials,
coordinates, record values or raw stderr. Anything it cannot summarise safely
is counted, not quoted.

Usage:
    python tools/diag.py "D:\\private\\handoff.zip" "D:\\private\\extracted"
    python tools/diag.py "D:\\private\\folder" --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import archive, db  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402
from flightdesk.paths import ensure_layout  # noqa: E402

PERMITTED_KEYS = (
    "discovered", "new", "already_imported", "awaiting_dji", "unsupported",
    "needs_attention", "new_flight",
)


def _error_category(exc: BaseException) -> str:
    return getattr(exc, "category", type(exc).__name__)


STATUS_TO_OUTCOME = {
    "imported": "new",
    "pending_key": "awaiting_dji",
    "unsupported": "unsupported",
    "damaged": "needs_attention",
    "needs_attention": "needs_attention",
}


def _summarise(conn, batch, source_kind: str) -> dict:
    """Per-source counts straight from the index, so a mixed ZIP cannot
    report one batch total under two sources."""
    rows = conn.execute(
        "SELECT r.format_version, r.status, COUNT(*) AS n FROM raw_files r "
        "JOIN source_members m ON m.raw_file_id=r.id "
        "WHERE m.source_kind=? GROUP BY r.format_version, r.status",
        (source_kind,),
    ).fetchall()
    by_version = Counter()
    by_status = Counter()
    totals = {key: 0 for key in PERMITTED_KEYS}
    for row in rows:
        version = f"v{row['format_version']}" if row["format_version"] is not None else "unknown"
        by_version[version] += row["n"]
        by_status[row["status"]] += row["n"]
        totals["discovered"] += row["n"]
        totals[STATUS_TO_OUTCOME.get(row["status"], "needs_attention")] += row["n"]
    flights = conn.execute(
        "SELECT COUNT(DISTINCT f.id) FROM flights f "
        "JOIN flight_files ff ON ff.flight_id=f.id "
        "JOIN source_members m ON m.raw_file_id=ff.raw_file_id "
        "WHERE m.source_kind=? AND f.identity_status='authoritative'",
        (source_kind,),
    ).fetchone()[0]
    totals["new_flight"] = int(flights)
    return {
        "source": source_kind,
        "totals": totals,
        "by_version": dict(sorted(by_version.items())),
        "by_status": dict(sorted(by_status.items())),
        "note": batch.note,
    }


def run(paths: list[str], *, workdir: Path) -> dict:
    data_root = workdir / "data"
    ensure_layout(data_root)
    conn = db.init_db(data_root / "flightdesk.sqlite3")
    importer = Importer(data_root, conn)
    results = []
    errors: Counter = Counter()
    for raw_path in paths:
        target = Path(raw_path).expanduser()
        if not target.exists():
            errors["path_not_found"] += 1
            continue
        try:
            if target.is_dir():
                kind = "extras" if "controller" in target.name.lower() else "archive"
                batch = importer.import_folder(target, default_kind=kind)
                results.append(_summarise(conn, batch, kind))
            elif target.suffix.lower() == ".zip":
                batch = importer.import_zip(target)
                for kind in ("archive", "extras"):
                    summary = _summarise(conn, batch, kind)
                    if summary["totals"]["discovered"]:
                        results.append(summary)
            else:
                batch = importer.import_single(target, source_kind="manual")
                results.append(_summarise(conn, batch, "manual"))
        except (archive.ArchiveError, zipfile.BadZipFile, OSError) as exc:
            errors[_error_category(exc)] += 1
    distinct = conn.execute("SELECT COUNT(DISTINCT sha256) FROM raw_files").fetchone()[0]
    flights = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    unresolved = conn.execute(
        "SELECT COUNT(*) FROM flights WHERE identity_status='unresolved'"
    ).fetchone()[0]
    # second pass proves idempotency without adding records
    second = None
    before = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    if len(paths) == 1 and Path(paths[0]).is_dir():
        batch = importer.import_folder(Path(paths[0]).expanduser())
        after = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        second = {"new": int(batch.totals.get("new", 0)),
                  "already_imported": int(batch.totals.get("already_imported", 0)),
                  "flights_added": after - before}
    summary = {
        "ok": True,
        "sources_reported": len(results),
        "distinct_files": int(distinct),
        "logical_flights": int(flights),
        "unresolved_flights": int(unresolved),
        "errors": dict(errors),
        "second_pass": second,
        "groups": results,
    }
    conn.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate-only import diagnostics")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    workdir = Path(tempfile.mkdtemp(prefix="flightdesk-diag-"))
    try:
        summary = run(args.paths, workdir=workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"distinct_files={summary['distinct_files']} "
              f"logical_flights={summary['logical_flights']} "
              f"unresolved={summary['unresolved_flights']}")
        for group in summary["groups"]:
            totals = " ".join(f"{k}={v}" for k, v in group["totals"].items() if v)
            print(f"source={group['source']} {totals or 'no_eligible_files'}")
            if group["by_version"]:
                print("  versions=" + ",".join(f"{k}:{v}" for k, v in group["by_version"].items()))
            if group["by_status"]:
                print("  statuses=" + ",".join(f"{k}:{v}" for k, v in group["by_status"].items()))
        if summary["errors"]:
            print("errors=" + ",".join(f"{k}:{v}" for k, v in summary["errors"].items()))
        if summary["second_pass"]:
            sp = summary["second_pass"]
            print(f"second_pass new={sp['new']} already_imported={sp['already_imported']} "
                  f"flights_added={sp['flights_added']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
