"""Recompute stored flight fingerprints; report multi-file links that disagree.

Dry run by default: it changes nothing and prints what it would change. Pass
`--apply` to write the recomputed fingerprints (nothing else is touched).

    python tools\\repair_fingerprints.py                  # report only
    python tools\\repair_fingerprints.py --apply          # rewrite fingerprints
    python tools\\repair_fingerprints.py --show-names     # include file names

Why this exists: the stored fingerprint went stale after a reparse, and before
2026-11-21 every unreadable log shared the fingerprint of "||0.0". Two unrelated
unreadable logs then merged into one flight. The fix stops new ones; this puts
the stored values right.

File names are withheld unless --show-names is passed, so a pasted report stays
free of the owner's log names. Everything printed is ids, counts and dates.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import db, fingerprints, paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None,
                        help="data root (default: the app's data root)")
    parser.add_argument("--apply", action="store_true",
                        help="write the recomputed fingerprints")
    parser.add_argument("--show-names", action="store_true",
                        help="print log file names in the report")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else paths.default_data_root()
    db_file = paths.db_path(root)
    if not db_file.exists():
        print(f"No database at {db_file}")
        return 2
    conn = db.connect(db_file, readonly=not args.apply)
    try:
        report = fingerprints.recompute(conn, apply=args.apply)
    finally:
        conn.close()

    print(f"Root: {root}")
    print(f"Checked {report['checked']} flights; "
          f"{report['changed']} fingerprint(s) differ from what is stored.")
    if report["changed"] and not report["applied"]:
        print("Nothing was written. Re-run with --apply to store the new values.")
    if report["changes"]:
        for change in report["changes"][:20]:
            print(f"  flight {change['id']}: {str(change['old'])[:12] or '(none)'} -> "
                  f"{str(change['new'])[:12] or '(none)'}")
        if len(report["changes"]) > 20:
            print(f"  … and {len(report['changes']) - 20} more")

    collisions = report["collisions"]
    print(f"Flights sharing one fingerprint: {len(collisions)}")
    for item in collisions[:20]:
        print(f"  {item['fingerprint'][:12]}: flights {item['ids']}")

    mismatches = report["multi_file_mismatches"]
    by_kind: dict[str, int] = {}
    for item in mismatches:
        by_kind[item.get("kind", "different")] = by_kind.get(item.get("kind", "different"), 0) + 1
    print(f"Flights whose file names disagree about the date: {len(mismatches)}")
    for kind in ("same_instant", "clock_skew", "timezone_offset", "different"):
        if by_kind.get(kind):
            print(f"  {kind}: {by_kind[kind]}")
    if by_kind.get("clock_skew") or by_kind.get("timezone_offset"):
        print("  A second or a whole number of hours is a naming difference between")
        print("  controllers, not evidence of a wrong link: the link was made on the")
        print("  decoded start times, which agree to within three seconds.")
    genuinely = [m for m in mismatches if m.get("kind") == "different"]
    if genuinely:
        print(f"  Needing a look ({len(genuinely)}):")
    for item in genuinely[:40]:
        dates = sorted({str(f["filename_time"]) for f in item["files"]})
        print(f"  flight {item['flight_id']}: {len(item['files'])} files, "
              f"spread {int(item['spread_seconds'])} s, dates {', '.join(dates)}")
        if args.show_names:
            for file in item["files"]:
                print(f"      raw {file['raw_id']}  {file['name']}")
    if genuinely:
        print("These are reported, not repaired: which files belong together is the "
              "owner's call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
