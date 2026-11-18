"""Fault injection: what does an interrupted backup actually leave behind?

Runs entirely on a throwaway data root. Prints facts only.
"""
from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flightdesk import backup, db  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402
from flightdesk.paths import ensure_layout  # noqa: E402
from flightdesk.synth import make_log_bytes  # noqa: E402
import datetime as dt  # noqa: E402


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="fd-fault-"))
    root = base / "data"
    ensure_layout(root)
    conn = db.init_db(root / "flightdesk.sqlite3")
    Importer(root, conn).import_stream(
        io.BytesIO(make_log_bytes(
            version=11,
            start=dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc),
        )),
        "DJIFlightRecord_2026-08-11_[21-53-20].txt",
        source_kind="archive", source_label="fault",
    )
    dest = base / "dest"
    dest.mkdir()

    real_write = zipfile.ZipFile.write

    def exploding_write(self, *args, **kwargs):
        raise OSError(28, "simulated disk full while writing the backup")

    with mock.patch.object(zipfile.ZipFile, "write", exploding_write):
        result = backup.create_backup(conn, root, dest, "fault injection")
    print("backup_returned=" + str(result.get("ok")))
    print("backup_code=" + str(result.get("code")))

    left = sorted(p.name for p in dest.iterdir())
    print("destination_entries=" + str(left))
    for path in dest.iterdir():
        if path.suffix == ".zip":
            ok = True
            try:
                with zipfile.ZipFile(path) as zf:
                    ok = zf.testzip() is None
            except zipfile.BadZipFile:
                ok = False
            print(f"stray_zip={path.name} valid={ok} bytes={path.stat().st_size}")
    print("lock_present=" + str((root / "tmp" / "backup.lock").exists()))
    print("staging_dirs=" + str(len(list((root / "tmp").glob("backup-*")))))

    # The next backup must still work after the failure.
    with mock.patch.object(zipfile.ZipFile, "write", real_write):
        follow = backup.create_backup(conn, root, dest, "after fault")
    print("followup_ok=" + str(follow.get("ok")))
    print("followup_valid=" + str(backup.validate_backup(Path(follow["path"]))["ok"])
          if follow.get("ok") else "followup_valid=n/a")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
