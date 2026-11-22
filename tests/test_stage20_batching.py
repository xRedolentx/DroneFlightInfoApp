"""One import batch per scan, and none at all when nothing was new.

Measured on the live archive: 1,082 batches, 927 of them containing no new
flight, because every re-scanned controller file wrote its own batch. The import
history is the owner's record of "what did I just import"; a wall of empty rows
answers that question badly.

All fixtures are invented.
"""
from __future__ import annotations

import unittest

from support import TempCase, base_time, log_bytes

from flightdesk import db, usb
from flightdesk.synth import filename_time


def _entry(name: str, data: bytes, device: str = "Smart Controller") -> usb.SimEntry:
    return usb.SimEntry(device=device, target="FlightRecord", storage="Internal",
                        name=name, data=data)


class BatchPerScanTest(TempCase):
    def _batch_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0])

    def _item_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM import_items").fetchone()[0])

    def test_a_scan_that_imports_three_files_writes_one_batch(self) -> None:
        entries = [_entry(filename_time(base_time(days=d), "FlightRecord"),
                          log_bytes(11, days=d)) for d in (0, 1, 2)]
        result = usb.simulate(self.conn, self.root, entries)
        self.assertEqual(result.imported, 3, result.as_dict())
        self.assertEqual(self._batch_count(), 1)
        self.assertEqual(self._item_count(), 3)
        batch = db.one(self.conn, "SELECT * FROM import_batches")
        totals = db.get_setting.__self__ if False else None  # noqa: F841 - keep import light
        import json
        stored = json.loads(batch["totals_json"])
        self.assertEqual(stored["new"], 3, stored)
        self.assertEqual(batch["status"], "done")

    def test_a_second_scan_that_finds_nothing_new_writes_no_batch(self) -> None:
        entries = [_entry(filename_time(base_time(days=d), "FlightRecord"),
                          log_bytes(11, days=d)) for d in (0, 1, 2)]
        usb.simulate(self.conn, self.root, entries)
        self.assertEqual(self._batch_count(), 1)
        again = usb.simulate(self.conn, self.root, entries)
        self.assertEqual(again.duplicates, 3, again.as_dict())
        self.assertEqual(again.imported, 0, again.as_dict())
        # The import history is unchanged: a scan with nothing new is not an
        # import. The scan itself is still recorded in usb_scans.
        self.assertEqual(self._batch_count(), 1)
        self.assertEqual(self._item_count(), 3)
        scans = int(self.conn.execute("SELECT COUNT(*) FROM usb_scans").fetchone()[0])
        self.assertEqual(scans, 2)

    def test_a_scan_with_one_new_file_and_two_known_writes_one_batch(self) -> None:
        first = [_entry(filename_time(base_time(days=d), "FlightRecord"),
                        log_bytes(11, days=d)) for d in (0, 1)]
        usb.simulate(self.conn, self.root, first)
        second = first + [_entry(filename_time(base_time(days=7), "FlightRecord"),
                                 log_bytes(11, days=7))]
        result = usb.simulate(self.conn, self.root, second)
        self.assertEqual(result.imported, 1, result.as_dict())
        self.assertEqual(self._batch_count(), 2)
        batch = db.one(self.conn, "SELECT * FROM import_batches ORDER BY rowid DESC LIMIT 1")
        import json
        stored = json.loads(batch["totals_json"])
        # The batch records what reached the importer. The two files the
        # collector skipped by name+size never got there, so the scan row (not
        # the batch) is what reports "3 scanned, 2 already held".
        self.assertEqual(stored["discovered"], 1, stored)
        self.assertEqual(stored["new"], 1, stored)
        scan = db.one(self.conn, "SELECT * FROM usb_scans ORDER BY id DESC LIMIT 1")
        self.assertEqual(scan["scanned"], 3, dict(scan))
        self.assertEqual(scan["duplicates"], 2, dict(scan))

    def test_a_file_that_needs_attention_still_writes_a_batch(self) -> None:
        # "Nothing new" must not hide a scan that found something wrong.
        # A copy that stops before its own header is the clearest such file.
        broken = log_bytes(11)[:100]
        result = usb.simulate(self.conn, self.root,
                              [_entry(filename_time(base_time(), "FlightRecord"), broken)])
        self.assertEqual(result.attention, 1, result.as_dict())
        self.assertEqual(self._batch_count(), 1)

    def test_a_physical_scan_uses_one_batch_for_many_files(self) -> None:
        from unittest import mock

        staged = self.root / "tmp" / "many"
        staged.mkdir(parents=True, exist_ok=True)
        files = []
        for days in (0, 1, 2):
            data = log_bytes(11, days=days)
            path = staged / filename_time(base_time(days=days), "FlightRecord")
            path.write_bytes(data)
            import hashlib
            files.append({"device": "DJI RC 2", "target": "FlightRecord",
                          "source_name": path.name, "staged_path": str(path),
                          "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        payload = {"status": "ok", "devices": ["DJI RC 2"], "errors": [], "files": files}
        with mock.patch.object(usb, "_ps_json", return_value=payload):
            result = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "stage")
        self.assertEqual(result.imported, 3, result.as_dict())
        self.assertEqual(self._batch_count(), 1)

    def test_a_physical_rescan_with_nothing_new_writes_no_batch(self) -> None:
        from unittest import mock

        staged = self.root / "tmp" / "rescan"
        staged.mkdir(parents=True, exist_ok=True)
        import hashlib
        data = log_bytes(11, days=3)
        path = staged / filename_time(base_time(days=3), "FlightRecord")
        path.write_bytes(data)
        payload = {"status": "ok", "devices": ["DJI RC 2"], "errors": [], "files": [{
            "device": "DJI RC 2", "target": "FlightRecord", "source_name": path.name,
            "staged_path": str(path), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}]}
        with mock.patch.object(usb, "_ps_json", return_value=payload):
            first = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s1")
        self.assertEqual(first.imported, 1, first.as_dict())
        # The device hands back the same bytes under the same name.
        path.write_bytes(data)
        with mock.patch.object(usb, "_ps_json", return_value=payload):
            second = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s2")
        self.assertEqual(second.imported, 0, second.as_dict())
        self.assertEqual(second.duplicates, 1, second.as_dict())
        self.assertEqual(self._batch_count(), 1)


class PartialRunTest(TempCase):
    """A capped collector run reports that it stopped on purpose."""

    def _payload(self, truncated: bool) -> dict:
        import hashlib
        staged = self.root / "tmp" / ("cap" + str(truncated))
        staged.mkdir(parents=True, exist_ok=True)
        data = log_bytes(11, days=9)
        path = staged / filename_time(base_time(days=9), "FlightRecord")
        path.write_bytes(data)
        return {"status": "ok", "devices": ["DJI RC 2"], "errors": [],
                "truncated": truncated, "files": [{
                    "device": "DJI RC 2", "target": "FlightRecord",
                    "source_name": path.name, "staged_path": str(path),
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}

    def test_a_capped_run_says_more_files_remain(self) -> None:
        from unittest import mock
        with mock.patch.object(usb, "_ps_json", return_value=self._payload(True)):
            result = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s1",
                              max_files=25)
        self.assertEqual(result.status, "partial", result.as_dict())
        self.assertIn("more_files_remain", result.notes)
        self.assertEqual(result.imported, 1, result.as_dict())
        row = db.one(self.conn, "SELECT * FROM usb_scans ORDER BY id DESC LIMIT 1")
        self.assertIn("more_files_remain", row["detail"] or "")

    def test_an_uncapped_run_does_not_claim_to_be_partial(self) -> None:
        from unittest import mock
        with mock.patch.object(usb, "_ps_json", return_value=self._payload(False)):
            result = usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s2")
        self.assertEqual(result.status, "ok", result.as_dict())
        self.assertNotIn("more_files_remain", result.notes)

    def test_the_cap_argument_is_only_passed_when_set(self) -> None:
        from unittest import mock
        seen = []

        def fake(script, args, timeout=200):
            seen.append(list(args))
            raise usb.CollectorError("collector_unavailable")

        with mock.patch.object(usb, "_ps_json", side_effect=fake):
            usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s3")
            usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "s4", max_files=25)
        self.assertFalse(any("-MaxFilesPerRun" in a for a in seen[0]), seen[0])
        self.assertTrue(any("-MaxFilesPerRun" in a for a in seen[1]), seen[1])


class BatchApiTest(TempCase):
    """The importer's own batch contract, without a controller in the way."""

    def test_a_deferred_batch_persists_only_when_it_has_content(self) -> None:
        imp = self.t.importer()
        batch = imp.open_batch("test", "manual", deferred=True)
        imp.add_stream(batch, __import__("io").BytesIO(log_bytes(11)),
                       "FlightRecord_2026-08-11_[21-53-20].txt",
                       source_kind="manual", source_label="test")
        imp.close_batch(batch)
        self.assertTrue(batch.persisted)
        self.assertEqual(
            int(self.conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]), 1)

        duplicate = imp.open_batch("test", "manual", deferred=True)
        imp.add_stream(duplicate, __import__("io").BytesIO(log_bytes(11)),
                       "FlightRecord_2026-08-11_[21-53-20].txt",
                       source_kind="manual", source_label="test")
        imp.close_batch(duplicate)
        self.assertFalse(duplicate.persisted)
        self.assertEqual(duplicate.totals["already_imported"], 1)
        self.assertEqual(
            int(self.conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]), 1)

    def test_keep_when_empty_forces_the_row(self) -> None:
        imp = self.t.importer()
        imp.add_stream(imp.open_batch("seed", "manual"), __import__("io").BytesIO(log_bytes(11)),
                       "FlightRecord_2026-08-11_[21-53-20].txt",
                       source_kind="manual", source_label="seed")
        batch = imp.open_batch("test", "manual", deferred=True)
        imp.add_stream(batch, __import__("io").BytesIO(log_bytes(11)),
                       "FlightRecord_2026-08-11_[21-53-20].txt",
                       source_kind="manual", source_label="test")
        imp.close_batch(batch, keep_when_empty=True)
        self.assertTrue(batch.persisted)
        self.assertEqual(
            int(self.conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]), 2)

    def test_a_single_file_import_still_writes_its_own_batch(self) -> None:
        self.import_bytes(log_bytes(11), "FlightRecord_2026-08-11_[21-53-20].txt")
        row = db.one(self.conn, "SELECT * FROM import_batches")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "done")
        self.assertEqual(
            int(self.conn.execute("SELECT COUNT(*) FROM import_items").fetchone()[0]), 1)


if __name__ == "__main__":
    unittest.main()
