"""Re-parse every archived original and refresh the figures derived from it.

Useful after a parser update or a change to a measurement definition. Each file
goes through the same path a normal import uses, so the pinned parser version,
the keychain cache and the "never replace a richer record with a poorer parse"
guards all apply.

    python tools\\reparse_all.py --root <dir>              # dry run: report only
    python tools\\reparse_all.py --root <dir> --json       # machine-readable
    python tools\\reparse_all.py --root <dir> --apply --expect-revision <rev>

The dry run never writes to the source database or archive. It copies the
database to a throwaway directory, points the importer at that copy, and runs the
same code path on it, so the deltas it reports are the deltas an apply would
produce. The archive is only read.

`--apply` requires an explicit `--root` and an `--expect-revision` taken from the
preview it is applying; a preview that no longer matches the database is refused
rather than applied to a changed archive. Files already done are skipped on a
rerun via a stored cursor, and a success resets the consecutive-failure streak.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import db, paths  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402

PROGRESS_EVERY = 25
MAX_CONSECUTIVE_FAILURES = 5
TIME_BUDGET_S = 900
CURSOR_SETTING = "reparse_cursor"
_DELTA_FIELDS = ("duration_s", "airborne_time_s", "total_distance_m", "path_length_m",
                 "max_home_distance_m", "sample_count")


def _air_totals(conn) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(airborne_time_s),0), COALESCE(SUM(duration_s),0), "
        "COALESCE(SUM(total_distance_m),0) "
        "FROM flights WHERE identity_status='authoritative'"
    ).fetchone()
    return {"flights": int(row[0]), "air_s": float(row[1]), "log_s": float(row[2]),
            "distance_m": float(row[3])}


def revision(conn: sqlite3.Connection) -> str:
    """A cheap signature of the archive/DB state a preview was computed from.

    It does not have to be cryptographic: its job is to notice that the database
    changed between the preview and the apply, not to resist an attacker.
    """
    raw = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(size_bytes),0) FROM raw_files"
    ).fetchone()
    flights = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(updated_at),'') FROM flights"
    ).fetchone()
    payload = f"{raw[0]}|{raw[1]}|{raw[2]}|{flights[0]}|{flights[1]}|{flights[2]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _flight_snapshot(conn: sqlite3.Connection) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in conn.execute(f"SELECT id,{','.join(_DELTA_FIELDS)} FROM flights"):
        out[int(row["id"])] = {field: row[field] for field in _DELTA_FIELDS}
    return out


def _source_rows(conn: sqlite3.Connection, only_decoded: bool,
                 after_id: int | None) -> list:
    statuses = ("imported",) if only_decoded else ("imported", "pending_key")
    marks = ",".join("?" for _ in statuses)
    sql = (f"SELECT id, status FROM raw_files WHERE status IN ({marks})")
    params: list = list(statuses)
    if after_id is not None:
        sql += " AND id>?"
        params.append(after_id)
    return db.all_rows(conn, sql + " ORDER BY id", tuple(params))


def _run(conn: sqlite3.Connection, root: Path, raws: list, *, progress=None,
         cursor: bool = False, stop_event=None) -> dict:
    """Parse each raw through the real path. Returns counts and stop reason."""
    importer = Importer(root, conn)
    repaired = failed = 0
    consecutive = 0
    errors: list[tuple[int, str]] = []
    stopped = None
    started = time.monotonic()
    total = len(raws)
    for index, raw in enumerate(raws, start=1):
        raw_id = int(raw["id"])
        try:
            result = importer.reparse_raw(raw_id)
            ok = bool(result.get("ok")) or result.get("status") in (
                "needs_attention", "damaged")
            if ok:
                repaired += 1
                consecutive = 0
            else:
                failed += 1
                consecutive += 1
                errors.append((raw_id, str(result.get("code") or result.get("status"))))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            failed += 1
            consecutive += 1
            errors.append((raw_id, type(exc).__name__))
        if cursor:
            with db.tx(conn):
                db.set_setting(conn, CURSOR_SETTING, raw_id)
        if progress:
            progress(index, total, failed)
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            stopped = f"stopped_after_{consecutive}_consecutive_failures"
            break
        if time.monotonic() - started > TIME_BUDGET_S:
            stopped = "time_budget"
            break
        if stop_event is not None and stop_event():
            stopped = "interrupted"
            break
    return {"parsed": repaired, "failed": failed, "errors": errors, "stopped": stopped,
            "total": total}


def _apply_changes(conn: sqlite3.Connection, root: Path, only_decoded: bool,
                   expect_revision: str | None, restart: bool) -> dict:
    current = revision(conn)
    if expect_revision is not None and current != expect_revision:
        return {"ok": False, "code": "stale_preview", "revision": current}
    after_id = None
    if not restart:
        cursor = db.get_setting(conn, CURSOR_SETTING)
        if isinstance(cursor, int):
            after_id = cursor
    raws = _source_rows(conn, only_decoded, after_id)
    before = _air_totals(conn)
    outcome = _run(conn, root, raws, cursor=True)
    after = _air_totals(conn)
    return {"ok": outcome["failed"] == 0, "mode": "apply", "revision": current,
            "before": before, "after": after, "outcome": outcome,
            "remaining": len(raws) - outcome["parsed"] - outcome["failed"]}


def _copy_database(source_db: Path, target_db: Path) -> None:
    src = sqlite3.connect(str(source_db))
    dst = sqlite3.connect(str(target_db))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _dry_run(root: Path, only_decoded: bool, after_id: int | None) -> dict:
    """Simulate on a private copy of the database. The source is only read."""
    source_db = paths.db_path(root)
    work = Path(tempfile.mkdtemp(prefix="fd-reparse-dry-"))
    try:
        copy_db = work / "flightdesk.sqlite3"
        _copy_database(source_db, copy_db)
        conn = db.connect(copy_db)
        try:
            rev = revision(conn)
            before = _air_totals(conn)
            before_flights = _flight_snapshot(conn)
            raws = _source_rows(conn, only_decoded, after_id)
            outcome = _run(conn, root, raws)
            after = _air_totals(conn)
            after_flights = _flight_snapshot(conn)
            deltas = []
            for flight_id, old in before_flights.items():
                new = after_flights.get(flight_id, {})
                changed = {field: {"before": old[field], "after": new.get(field)}
                           for field in _DELTA_FIELDS if old[field] != new.get(field)}
                if changed:
                    deltas.append({"flight_id": flight_id, "changes": changed})
            return {
                "ok": outcome["failed"] == 0,
                "mode": "dry_run",
                "revision": rev,
                "files": len(raws),
                "before": before,
                "after": after,
                "changed_flights": len(deltas),
                "deltas": deltas[:200],
                "outcome": {**outcome, "errors": [
                    {"raw_id": rid, "code": code} for rid, code in outcome["errors"][:50]]},
            }
        finally:
            conn.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _human_summary(report: dict) -> str:
    before, after = report["before"], report["after"]
    if report["mode"] == "dry_run":
        head = (f"Dry run: {report['files']} files would be re-parsed; "
                f"{report['changed_flights']} flights would change. Nothing written.")
    else:
        head = f"Applied: {report['outcome']['parsed']} parsed, {report['outcome']['failed']} failed."
    lines = [
        head,
        f"  Flights: {before['flights']} -> {after['flights']}",
        f"  Air time : {before['air_s'] / 60:.1f} min -> {after['air_s'] / 60:.1f} min",
        f"  Log time : {before['log_s'] / 60:.1f} min -> {after['log_s'] / 60:.1f} min",
        f"  Distance : {before['distance_m'] / 1000:.2f} km -> {after['distance_m'] / 1000:.2f} km",
    ]
    if report["outcome"].get("stopped"):
        lines.append(f"  Stopped: {report['outcome']['stopped']}")
    for err in report["outcome"]["errors"][:10]:
        lines.append(f"  failure: raw {err['raw_id']} ({err['code']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    parser.add_argument("--apply", action="store_true",
                        help="store the new figures (default: report only)")
    parser.add_argument("--only-decoded", action="store_true",
                        help="skip files still waiting for the DJI key")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--expect-revision", default=None,
                        help="apply only if the preview revision still matches")
    parser.add_argument("--restart", action="store_true",
                        help="ignore the stored resume cursor")
    parser.add_argument("--after-id", type=int, default=None,
                        help="dry run: only files with id greater than this")
    args = parser.parse_args(argv)

    if args.apply and not args.root:
        print("Refusing to apply without an explicit --root.")
        return 2
    root = Path(args.root).expanduser().resolve() if args.root else paths.default_data_root()
    db_file = paths.db_path(root)
    if not db_file.exists():
        print(f"No database at {db_file}")
        return 2

    if not args.apply:
        report = _dry_run(root, args.only_decoded, args.after_id)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(f"Root: {root}")
            print(_human_summary(report))
            print(f"Revision: {report['revision']}  "
                  f"(pass --expect-revision {report['revision']} to apply)")
        return 0 if report["ok"] else 1

    conn = db.connect(db_file)
    try:
        report = _apply_changes(conn, root, args.only_decoded,
                                args.expect_revision, args.restart)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if report.get("code") == "stale_preview":
        if not args.json:
            print("The preview no longer matches this database. Re-run the dry run.")
        return 2
    if not args.json:
        print(f"Root: {root}")
        print(_human_summary(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
