"""Write a synthetic handoff ZIP for import verification (invented data only).

Layout mirrors the shape the importer expects:
    data/extracted/flight_logs/<v8 and v11 logs>
    data/controller-test/<one v11 log>
    __MACOSX/._junk                      (must be ignored)

Usage:
    python tools/make_synthetic_handoff.py OUT.zip [--logs 6] [--broken]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk.synth import make_log_bytes  # noqa: E402

START = dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc)


def build(path: Path, logs: int = 6, broken: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(logs):
            stamp = (START + dt.timedelta(days=i * 3)).strftime("%Y-%m-%d_[%H-%M-%S]")
            zf.writestr(
                f"data/extracted/flight_logs/DJIFlightRecord_{stamp}.txt",
                make_log_bytes(
                    version=11 if i % 2 == 0 else 8,
                    start=START + dt.timedelta(days=i * 3),
                    aircraft=("mavic2", "air3s", "mini3")[i % 3],
                    duration_s=302.0 + i * 41,
                    distance_m=46.9 + i * 33,
                    max_height_m=45.1 + i * 3,
                    photo_num=i * 2,
                    video_time_s=i * 5,
                ),
            )
        zf.writestr(
            "data/controller-test/DJIFlightRecord_2026-05-01_[12-00-00].txt",
            make_log_bytes(
                version=11,
                start=dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.timezone.utc),
                aircraft="air3s",
                duration_s=298.0,
                distance_m=70.0,
            ),
        )
        zf.writestr("__MACOSX/._junk", b"metadata")
        zf.writestr("data/extracted/flight_logs/notes.json", b'{"ignored": true}')
        if broken:
            zf.writestr("data/extracted/flight_logs/DJIFlightRecord_broken.txt", b"\x00" * 64)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--logs", type=int, default=6)
    parser.add_argument("--broken", action="store_true")
    args = parser.parse_args()
    path = build(Path(args.out), logs=args.logs, broken=args.broken)
    with zipfile.ZipFile(path) as zf:
        members = len(zf.namelist())
    print(f"synthetic_handoff_written bytes={path.stat().st_size} members={members}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
