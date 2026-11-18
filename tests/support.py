"""Shared helpers for synthetic-only tests."""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import capture, db, maintenance  # noqa: E402
from flightdesk.importer import Importer  # noqa: E402
from flightdesk.paths import ensure_layout  # noqa: E402
from flightdesk.synth import filename_time, make_log_bytes, make_v13_keychain_log  # noqa: E402

CAPTURE_FIXTURE = ROOT / "fixtures" / "synthetic" / "AirData-Equipment-Capture-SYNTHETIC.md"


class TempRoot:
    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="fd-test-")
        self.root = Path(self._dir.name)
        ensure_layout(self.root)
        self.conn = db.init_db(self.root / "flightdesk.sqlite3")

    def importer(self) -> Importer:
        return Importer(self.root, self.conn)

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self._dir.cleanup()


def base_time(days: int = 0, minutes: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 11, 21, 53, 20, tzinfo=dt.timezone.utc) + dt.timedelta(
        days=days, minutes=minutes
    )


def log_bytes(version: int = 11, *, days: int = 0, aircraft: str = "mavic2",
              battery: str | None = None, duration: float = 302.0,
              distance: float = 46.9, height: float = 45.1,
              photos: int = 0, video: int = 0) -> bytes:
    return make_log_bytes(
        version=version, start=base_time(days), aircraft=aircraft, battery_sn=battery,
        duration_s=duration, distance_m=distance, max_height_m=height,
        photo_num=photos, video_time_s=video,
    )


def write_folder(folder: Path, entries: dict[str, bytes]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in entries.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def build_zip(path: Path, entries: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def capture_text() -> str:
    return CAPTURE_FIXTURE.read_text(encoding="utf-8")


def apply_capture(conn, label: str = "Synthetic AirData capture") -> dict:
    doc = capture.parse_capture(capture_text())
    return capture.apply_capture(conn, doc, label)


def status_for(conn, equipment_name: str, code: str) -> dict:
    for item in maintenance.all_statuses(conn, include_archived=True):
        if item["equipment_name"] == equipment_name and item["code"] == code:
            return item
    raise AssertionError(f"rule not found: {equipment_name}/{code}")


def equipment_id(conn, name: str) -> int:
    row = db.one(conn, "SELECT id FROM equipment WHERE name=? COLLATE NOCASE", (name,))
    if row is None:
        raise AssertionError(f"equipment not found: {name}")
    return int(row["id"])


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TempRoot()
        self.conn = self.t.conn
        self.root = self.t.root
        self.imp = self.t.importer()

    def tearDown(self) -> None:
        self.t.close()

    def import_bytes(self, data: bytes, name: str, kind: str = "archive") -> dict:
        batch = self.imp.import_stream(io.BytesIO(data), name, source_kind=kind,
                                       source_label="test")
        return batch.totals

    def counts(self) -> dict:
        return {
            "flights": self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0],
            "raw": self.conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0],
        }


def json_roundtrip(value):
    return json.loads(json.dumps(value))
