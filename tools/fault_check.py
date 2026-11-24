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

    # ---- a restore interrupted mid-extraction must not destroy a target ----
    # The target holds a marker file that a destructive implementation would
    # delete before the staged copy is in place.
    target = base / "restore-target"
    target.mkdir(exist_ok=True)
    (target / "existing-marker.txt").write_text("do not lose me", encoding="utf-8")

    def interrupted_extract(*args, **kwargs):
        raise OSError(5, "simulated disconnect while extracting the backup")

    try:
        with mock.patch.object(backup.shutil, "copyfileobj", interrupted_extract):
            interrupted = backup.restore_backup(Path(follow["path"]), target,
                                                allow_existing=True)
    except Exception as exc:  # an interruption may raise rather than return
        interrupted = {"ok": False, "code": "raised:" + type(exc).__name__}
    print("interrupted_restore_ok=" + str(interrupted.get("ok")))
    print("interrupted_restore_code=" + str(interrupted.get("code")))
    print("target_still_present=" + str(target.exists()))
    marker = target / "existing-marker.txt"
    print("target_marker_kept=" + str(marker.exists()
                                      and marker.read_text(encoding="utf-8") == "do not lose me"))
    print("no_half_written_target=" + str(not (target / "manifest.json").exists()))
    stale = [p.name for p in base.iterdir()
             if ".restore-tmp" in p.name or ".restore-" in p.name]
    print("stale_staging_dirs=" + str(stale))

    # ---- and a clean restore after that must still work --------------------
    clean_target = base / "restore-clean"
    retry = backup.restore_backup(Path(follow["path"]), clean_target)
    print("retry_ok=" + str(retry.get("ok")))
    print("retry_has_db=" + str((clean_target / "flightdesk.sqlite3").exists()))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
