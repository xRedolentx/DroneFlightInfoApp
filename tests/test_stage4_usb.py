"""Stage 4: read-only controller collection contract (simulated adapter)."""
from __future__ import annotations

import json
import struct
import unittest

from support import TempCase, log_bytes
from flightdesk import db, usb
from flightdesk.usb import SimEntry, simulate, validate_flight_bytes


def entry(device="Smart Controller", target="short", name="DJIFlightRecord_2026-05-01_[12-00-00].txt",
          days=0, complete=True, stable=True, storage="Internal shared storage"):
    return SimEntry(device=device, target=target, storage=storage, name=name,
                    data=log_bytes(11, days=days), complete=complete, stable=stable)


class ValidationTest(unittest.TestCase):
    def test_valid_log_passes_structure_check(self) -> None:
        ok, reason = validate_flight_bytes(log_bytes(11))
        self.assertTrue(ok, reason)

    def test_truncated_copy_fails(self) -> None:
        ok, reason = validate_flight_bytes(log_bytes(11)[:110])
        self.assertFalse(ok)
        self.assertIn(reason, {"too_short", "bad_details_offset"})

    def test_a_zero_details_offset_is_identified(self) -> None:
        data = bytearray(log_bytes(11))
        struct.pack_into("<Q", data, 0, 0)
        ok, reason = validate_flight_bytes(bytes(data))
        self.assertFalse(ok)
        self.assertEqual(reason, "bad_details_offset")


class UnreadableLogTest(TempCase):
    """A log whose details block cannot be read is kept, not silently dropped.

    Measured on a real Smart Controller: two of 259 logs store a details offset
    of 0. One of them is byte-identical to a log already in the archive, so the
    defect is in the file the aircraft wrote, not in the export or the copy.
    """

    def zero_offset_entry(self, name="DJIFlightRecord_2025-05-16_[10-14-39].txt"):
        data = bytearray(log_bytes(11))
        struct.pack_into("<Q", data, 0, 0)
        return SimEntry(device="Smart Controller", target="sc", storage="s",
                        name=name, data=bytes(data))

    def test_the_bytes_are_archived_and_the_flight_is_flagged(self) -> None:
        result = simulate(self.conn, self.root, [self.zero_offset_entry()])
        self.assertEqual(result.imported, 1, result.notes)
        self.assertEqual(result.attention, 0)
        self.assertIn("kept_unreadable:bad_details_offset", result.notes)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0], 1)
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["excluded_reason"], "details_unreadable")
        self.assertIsNone(row["aircraft_name"])
        self.assertIsNone(row["max_height_m"])

    def test_a_second_attach_does_not_duplicate_it(self) -> None:
        simulate(self.conn, self.root, [self.zero_offset_entry()])
        again = simulate(self.conn, self.root, [self.zero_offset_entry()])
        self.assertEqual(again.imported, 0)
        self.assertEqual(again.duplicates, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)

    def test_a_truncated_log_is_still_refused_at_the_door(self) -> None:
        short = SimEntry(device="Smart Controller", target="sc", storage="s",
                         name="DJIFlightRecord_2025-05-16_[10-14-39].txt",
                         data=log_bytes(11)[:110])
        result = simulate(self.conn, self.root, [short])
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.attention, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0], 0)


class SimulatedAdapterTest(TempCase):
    def test_hidden_extension_is_handled(self) -> None:
        s = entry(name="DJIFlightRecord_2026-05-01_[12-00-00]")  # no .txt
        result = simulate(self.conn, self.root, [s])
        self.assertEqual(result.imported, 1)
        row = db.one(self.conn, "SELECT original_name FROM raw_files")
        self.assertTrue(row["original_name"].endswith(".txt"))

    def test_duplicate_storage_is_collapsed(self) -> None:
        result = simulate(self.conn, self.root, [entry()], duplicate_storage=True)
        self.assertEqual(result.imported, 1)
        # A second identical entry on the same storage is not copied twice.
        result2 = simulate(self.conn, self.root, [entry(), entry()],
                           duplicate_storage=True)
        self.assertEqual(result2.imported, 0)

    def test_both_devices_together(self) -> None:
        entries = [
            entry(device="Smart Controller", target="sc"),
            entry(device="DJI RC 2", target="rc2", name="FlightRecord_2026-06-02_[08-00-00].txt",
                  days=3, storage="Internal shared storage"),
        ]
        result = simulate(self.conn, self.root, entries)
        self.assertEqual(result.imported, 2)
        kinds = {r["source_label"] for r in db.all_rows(
            self.conn, "SELECT DISTINCT source_label FROM source_members")}
        self.assertEqual(kinds, {"USB Smart Controller", "USB DJI RC 2"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 2)

    def test_interrupted_copy_is_retryable_and_never_marked_imported(self) -> None:
        result = simulate(self.conn, self.root, [entry(complete=False)])
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.attention, 1)
        self.assertIn("copy_incomplete_retryable", result.notes)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0], 0)

    def test_unstable_read_is_retried_not_accepted(self) -> None:
        result = simulate(self.conn, self.root, [entry(stable=False)])
        self.assertEqual(result.imported, 0)
        self.assertIn("copy_incomplete_retryable", result.notes)

    def test_reconnect_and_second_plugin_add_nothing(self) -> None:
        first = simulate(self.conn, self.root, [entry()])
        self.assertEqual(first.imported, 1)
        second = simulate(self.conn, self.root, [entry()])
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)

    def test_watcher_restart_converges(self) -> None:
        simulate(self.conn, self.root, [entry()])
        # Simulate a watcher restart: a new connection and a new scan.
        conn = db.connect(self.root / "flightdesk.sqlite3")
        try:
            before = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
            again = simulate(conn, self.root, [entry()])
            after = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(before, after)
        self.assertEqual(again.imported, 0)
        self.assertEqual(again.duplicates, 1)

    def test_non_flight_files_are_ignored(self) -> None:
        entries = [
            SimEntry(device="DJI RC 2", target="x", storage="s",
                     name="dji.go.v5.sync.json", data=b"{}"),
            SimEntry(device="DJI RC 2", target="x", storage="s",
                     name="DJI_0001.DAT", data=b"\x00" * 200),
            entry(device="DJI RC 2"),
        ]
        result = simulate(self.conn, self.root, entries)
        self.assertEqual(result.imported, 1)
        self.assertIn("ignored_non_flight", result.notes)

    def test_device_status_is_recorded(self) -> None:
        simulate(self.conn, self.root, [entry(),
                                          entry(device="DJI RC 2", target="rc2", days=2)])
        rows = db.all_rows(self.conn, "SELECT * FROM usb_devices ORDER BY device")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["last_success"] for r in rows))
        payload = usb.status(self.conn)
        self.assertEqual(len(payload["devices"]), 2)
        self.assertTrue(payload["scans"])

    def test_manual_import_during_scan_converges(self) -> None:
        data = log_bytes(11, days=9)
        self.imp.import_stream(__import__("io").BytesIO(data),
                               "DJIFlightRecord_2026-08-20_[21-53-20].txt",
                               source_kind="manual", source_label="manual")
        s = SimEntry(device="Smart Controller", target="sc", storage="s",
                     name="DJIFlightRecord_2026-08-20_[21-53-20].txt", data=data)
        result = simulate(self.conn, self.root, [s])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)
        self.assertEqual(result.duplicates, 1)

    def test_watcher_toggle_is_reversible(self) -> None:
        usb.set_watcher(self.conn, True, 60)
        self.assertTrue(db.get_setting(self.conn, "usb_watcher")["enabled"])
        usb.set_watcher(self.conn, False, 60)
        self.assertFalse(db.get_setting(self.conn, "usb_watcher")["enabled"])


if __name__ == "__main__":
    unittest.main()


class SkipKnownTest(TempCase):
    """A plugged-in controller must not be re-copied on every watcher pass.

    The watcher rescans every 45 s. Without a skip list each pass would re-copy
    everything on the device - measured at 182 MB per scan on a Smart Controller.
    """

    def test_the_scan_tells_the_collector_what_is_already_archived(self) -> None:
        from unittest import mock
        self.import_bytes(log_bytes(11, days=1),
                          "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        seen = {}

        def fake(script, args, timeout=300):
            seen["args"] = args
            index = args.index("-SkipListPath")
            with open(args[index + 1], encoding="utf-8") as fh:
                seen["skip"] = json.load(fh)
            return {"status": "all_known", "files": [], "errors": [], "skipped_known": 1}

        with mock.patch.object(usb, "_ps_json", side_effect=fake):
            result = usb.scan(self.conn, self.root)

        self.assertIn("-SkipListPath", seen["args"])
        self.assertEqual(seen["skip"][0]["name"], "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        self.assertGreater(seen["skip"][0]["size"], 0)
        self.assertEqual(result.status, "all_known")
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.imported, 0)
        # The list holds device file names, so it does not outlive the scan.
        skip_paths = list((self.root / "tmp").glob("usb-skip-*.json"))
        self.assertEqual(skip_paths, [])

    def test_a_device_with_nothing_known_is_still_reported_as_such(self) -> None:
        from unittest import mock
        with mock.patch.object(usb, "_ps_json",
                               return_value={"status": "none", "files": [], "errors": []}):
            result = usb.scan(self.conn, self.root)
        self.assertEqual(result.status, "no_device")

