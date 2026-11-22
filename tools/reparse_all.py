"""Re-parse every archived original and refresh the figures derived from it.

Useful after a parser update or a change to a measurement definition. Each file
goes through the same path a normal import uses, so the pinned parser version,
the keychain cache and the "never replace a richer record with a poorer parse"
guards all apply.

    python tools\\reparse_all.py                 # report what would change
    python tools\\reparse_all.py --apply         # write
    python tools\\reparse_all.py --apply --only-decoded

Defaults to `--apply` being OFF: nothing is written until asked. A dry run still
parses every file, so its cost is the same; it simply does not store the result.

Prints progress every 25 files so a long run is visibly moving, and stops with a
summary if the failure count passes a threshold rather than grinding through a
broken parser.
"""
from __future__ import annotations

import argparse
import sys
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


def _air_totals(conn) -> tuple[int, float, float]:
    row = conn.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(airborne_time_s),0), COALESCE(SUM(duration_s),0) "
        "FROM flights WHERE identity_status='authoritative'"
    ).fetchone()
    return int(row[0]), float(row[1]), float(row[2])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    parser.add_argument("--apply", action="store_true",
                        help="store the new figures (default: report only)")
    parser.add_argument("--only-decoded", action="store_true",
                        help="skip files still waiting for the DJI key")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else paths.default_data_root()
    db_file = paths.db_path(root)
    if not db_file.exists():
        print(f"No database at {db_file}")
        return 2

    conn = db.connect(db_file, readonly=not args.apply)
    try:
        before = _air_totals(conn)
        statuses = ("imported",) if args.only_decoded else ("imported", "pending_key")
        marks = ",".join("?" for _ in statuses)
        raws = db.all_rows(
            conn,
            f"SELECT id, status FROM raw_files WHERE status IN ({marks}) ORDER BY id",
            statuses,
        )
        total = len(raws)
        print(f"Root: {root}")
        print(f"Files to re-parse: {total} "
              f"({'storing results' if args.apply else 'dry run, nothing written'})")
        importer = Importer(root, conn)
        repaired = failed = consecutive = 0
        errors: list[str] = []
        started = time.monotonic()
        for index, raw in enumerate(raws, start=1):
            try:
                result = importer.reparse_raw(int(raw["id"]))
                if result.get("ok") or result.get("status") in ("needs_attention", "damaged"):
                    repaired += 1
                else:
                    failed += 1
                    consecutive += 1
                    errors.append(f"raw {raw['id']}: {result.get('code') or result.get('status')}")
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                failed += 1
                consecutive += 1
                errors.append(f"raw {raw['id']}: {type(exc).__name__}: {exc}")
            if index % PROGRESS_EVERY == 0 or index == total:
                elapsed = time.monotonic() - started
                print(f"  {index}/{total} parsed · {failed} failed · {elapsed:.0f}s")
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                print(f"Stopping after {consecutive} consecutive failures.")
                break
            if time.monotonic() - started > TIME_BUDGET_S:
                print(f"Stopping at the {TIME_BUDGET_S}s budget with "
                      f"{total - index} files left.")
                print("Re-run to continue: the work already stored is kept.")
                break
        after = _air_totals(conn)
        print(f"Flights: {before[0]} -> {after[0]}")
        print(f"Air time (off ground): {before[1] / 60:.1f} min -> {after[1] / 60:.1f} min")
        print(f"Log time (record)    : {before[2] / 60:.1f} min -> {after[2] / 60:.1f} min")
        print(f"Parsed {repaired}, failed {failed}")
        for line in errors[:10]:
            print("  " + line)
        if not args.apply:
            print("Nothing was written. Re-run with --apply to store these figures.")
        return 0 if failed == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
