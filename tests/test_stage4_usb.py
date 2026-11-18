"""Stage 4: read-only controller collection contract (simulated adapter)."""
from __future__ import annotations

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
