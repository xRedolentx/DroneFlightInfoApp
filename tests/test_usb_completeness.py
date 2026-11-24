"""P10/F14: a plausible prefix is not a complete transfer.

For an unknown-size MTP source the reviewed collector accepted three seconds of
stable length plus a complete auxiliary header as proof the copy had finished. A
120-byte v14 header with no records passed validation, and a name+size skip
could not notice changed bytes of the same length.
"""
from __future__ import annotations

import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TempCase, base_time, log_bytes

from flightdesk import db, usb
from flightdesk.synth import filename_time


def staged_entry(staging: Path, name: str, data: bytes, **extra) -> dict:
    folder = staging / "guid"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    entry = {"staged_path": str(path), "source_name": name, "device": "DJI RC 2",
             "target": "FlightRecord"}
    entry.update(extra)
    return entry


class TransferCompletenessTest(TempCase):
    def _scan(self, entries, **kwargs):
        payload = {"status": "ok", "devices": ["DJI RC 2"], "files": entries,
                   "skipped_known": 0, "errors": []}
        with patch.object(usb, "_ps_json", return_value=payload):
            return usb.scan(self.conn, self.root, staging_root=self.root / "tmp" / "stage", **kwargs)

    def test_an_unknown_size_without_matching_reads_is_unverified(self) -> None:
        staging = self.root / "tmp" / "stage"
        name = filename_time(base_time(), "FlightRecord")
        entry = staged_entry(staging, name, log_bytes(11), size_known=False)
        result = self._scan([entry])
        self.assertEqual(result.imported, 0, result.as_dict())
        self.assertEqual(self.counts()["raw"], 0)
        self.assertEqual(self.counts()["flights"], 0)
        self.assertIn("unverified_transfer:unverified_unknown_size", result.notes)
        # The bytes are kept for a retry, not deleted.
        kept = list((self.root / "tmp" / "usb-unverified").iterdir())
        self.assertEqual(len(kept), 1, kept)

    def test_an_unknown_size_with_matching_reads_is_imported(self) -> None:
        staging = self.root / "tmp" / "stage"
        name = filename_time(base_time(), "FlightRecord")
        entry = staged_entry(staging, name, log_bytes(11), size_known=False,
                             sha256_match=True)
        result = self._scan([entry])
        self.assertEqual(result.imported, 1, result.as_dict())
        self.assertEqual(self.counts()["raw"], 1)

    def test_a_known_size_complete_copy_is_imported(self) -> None:
        staging = self.root / "tmp" / "stage"
        name = filename_time(base_time(), "FlightRecord")
        entry = staged_entry(staging, name, log_bytes(11), size_known=True, complete=True)
        result = self._scan([entry])
        self.assertEqual(result.imported, 1, result.as_dict())

    def test_an_incomplete_entry_is_retained_and_retryable(self) -> None:
        staging = self.root / "tmp" / "stage"
        name = filename_time(base_time(), "FlightRecord")
        bad = staged_entry(staging, name, log_bytes(11), size_known=True, complete=False)
        result = self._scan([bad])
        self.assertEqual(result.imported, 0, result.as_dict())
        self.assertIn("unverified_transfer:incomplete_transfer", result.notes)
        # A later scan of the same file, now complete, imports it — the earlier
        # refusal did not enter the known set.
        good = staged_entry(staging, name, log_bytes(11), size_known=True, complete=True)
        second = self._scan([good])
        self.assertEqual(second.imported, 1, second.as_dict())

    def test_verify_mode_bypasses_the_name_size_skip(self) -> None:
        name = filename_time(base_time(), "DJIFlightRecord")
        data = log_bytes(11)
        first = usb.simulate(self.conn, self.root,
                             [usb.SimEntry(device="RC", target="F", storage="Internal",
                                           name=name, data=data)])
        self.assertEqual(first.imported, 1, first.as_dict())
        # Same name and size: normally skipped as known.
        normal = usb.simulate(self.conn, self.root,
                              [usb.SimEntry(device="RC", target="F", storage="Internal",
                                            name=name, data=data)])
        self.assertIn("skipped_known", normal.notes)
        # With verification, the bytes are re-read and hashed.
        verified = usb.simulate(self.conn, self.root,
                                [usb.SimEntry(device="RC", target="F", storage="Internal",
                                              name=name, data=data)], verify=True)
        self.assertNotIn("skipped_known", verified.notes)
        self.assertGreaterEqual(verified.duplicates + verified.imported, 1)


class OffsetClassificationTest(unittest.TestCase):
    def _v11(self, offset: int, details_len: int = 100) -> bytes:
        body = bytearray(log_bytes(11))
        struct.pack_into("<Q", body, 0, offset)
        struct.pack_into("<H", body, 8, details_len)
        return bytes(body)

    def test_a_zero_offset_is_a_damaged_original_to_keep(self) -> None:
        ok, reason = usb.validate_flight_bytes(self._v11(0))
        self.assertFalse(ok)
        self.assertEqual(reason, "bad_details_offset")

    def test_an_offset_past_the_received_prefix_is_a_truncated_copy(self) -> None:
        data = self._v11(len(log_bytes(11)) + 5000)
        ok, reason = usb.validate_flight_bytes(data)
        self.assertFalse(ok)
        self.assertEqual(reason, "copy_truncated")

    def test_a_header_only_v14_log_is_not_proof_of_completion(self) -> None:
        # The exact probe: a 120-byte v14 header with no records passes the
        # cheap structural check, which is why it cannot be the sole evidence.
        blob = bytearray(120)
        blob[10] = 14
        blob[100] = 0
        ok, _reason = usb.validate_flight_bytes(bytes(blob))
        self.assertTrue(ok)  # structurally plausible...
        # ...but an unknown-size entry still needs a matching second read.
        self.assertEqual(usb._entry_complete({"size_known": False})[0], False)
        self.assertEqual(usb._entry_complete({"size_known": False,
                                              "sha256_match": True})[0], True)


if __name__ == "__main__":
    unittest.main()
