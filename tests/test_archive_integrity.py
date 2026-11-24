"""P02/F03: a corrupt archive object is not a duplicate.

The reviewed build compared only the stored length. A same-length corruption
therefore passed, the intact bytes just received were discarded, and the import
reported an exact duplicate. These tests pin the content-hash check and the
fail-closed evidence handling around it.
"""
from __future__ import annotations

import io
import unittest

from support import TempCase, log_bytes

from flightdesk import archive, db


class ArchiveIntegrityTest(TempCase):
    name = "FlightRecord_2026-08-11_[21-53-20].txt"

    def setUp(self) -> None:
        super().setUp()
        self.data = log_bytes(11)

    def _import(self, data: bytes | None = None):
        return self.imp.import_stream(io.BytesIO(self.data if data is None else data),
                                      self.name, source_kind="manual",
                                      source_label="test")

    def _object(self):
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        return raw, archive.archive_path(self.root, raw["stored_rel"])

    def _quarantine(self) -> list:
        folder = archive.quarantine_dir(self.root)
        return sorted(folder.iterdir()) if folder.is_dir() else []

    def test_a_healthy_duplicate_stays_one_row_and_one_object(self) -> None:
        self._import()
        again = self._import()
        self.assertEqual(again.totals["already_imported"], 1)
        self.assertEqual(again.totals["new"], 0)
        self.assertEqual(self.counts(), {"flights": 1, "raw": 1})
        raw, path = self._object()
        self.assertTrue(archive.verify_object(self.root, raw["stored_rel"], raw["sha256"]))
        self.assertTrue(path.is_file())
        # A healthy duplicate creates no quarantine noise.
        self.assertEqual(self._quarantine(), [])

    def test_same_size_corruption_fails_loudly_and_keeps_both_copies(self) -> None:
        self._import()
        _raw, path = self._object()
        corrupt = b"X" * len(self.data)
        self.assertEqual(len(corrupt), len(self.data))
        path.write_bytes(corrupt)

        again = self._import()

        self.assertEqual(again.totals["new"], 0)
        self.assertEqual(again.note, "archive_failed")
        self.assertIn("archive_object_corrupt", [item["code"] for item in again.items])
        # The archive object is left exactly as found: a repair is the owner's
        # decision, not a silent replacement during an import.
        self.assertEqual(path.read_bytes(), corrupt)
        # Both the corrupt object and the good bytes just received are kept.
        kept = self._quarantine()
        self.assertEqual(len(kept), 2, [p.name for p in kept])
        self.assertEqual({p.read_bytes() for p in kept}, {corrupt, self.data})
        self.assertTrue(any(p.name.endswith("-corrupt.bin") for p in kept))
        self.assertTrue(any(p.name.endswith("-incoming.bin") for p in kept))
        # No second flight and no second raw row appeared.
        self.assertEqual(self.counts(), {"flights": 1, "raw": 1})

    def test_a_different_size_corruption_is_handled_the_same_way(self) -> None:
        self._import()
        _raw, path = self._object()
        path.write_bytes(b"short")
        again = self._import()
        self.assertIn("archive_object_corrupt", [item["code"] for item in again.items])
        self.assertEqual(path.read_bytes(), b"short")
        kept = self._quarantine()
        self.assertEqual(len(kept), 2)
        self.assertEqual({p.read_bytes() for p in kept}, {b"short", self.data})

    def test_a_retry_after_corruption_still_fails_closed(self) -> None:
        # Whatever else happens, a corrupt object never becomes a silent
        # duplicate on the next import.
        self._import()
        _raw, path = self._object()
        path.write_bytes(b"Y" * len(self.data))
        for _ in range(2):
            again = self._import()
            self.assertIn("archive_object_corrupt",
                          [item["code"] for item in again.items])
        self.assertEqual(self.counts()["raw"], 1)


if __name__ == "__main__":
    unittest.main()
