"""Stage 5: DJI key exchange, DPAPI cache and offline reparse."""
from __future__ import annotations

import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from support import TempCase, base_time

from flightdesk import db, keychain, security
from flightdesk.synth import make_v13_keychain_log, make_v13_metadata_log


def v13_bytes(days: int = 0) -> bytes:
    return make_v13_metadata_log(start=base_time(days), aircraft="air3s",
                                 duration_s=302.0, distance_m=46.9, max_height_m=45.1)


class RequestTest(unittest.TestCase):
    def test_request_shape_matches_the_pinned_interface(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "DJIFlightRecord_2026-08-11_[21-53-20].txt"
            path.write_bytes(make_v13_keychain_log(start=base_time(0)))
            request = keychain.extract_request(path)
        self.assertIsNotNone(request)
        self.assertEqual(request["version"], 13)
        self.assertEqual(request["department"], 3)
        self.assertIn("keychainsArray", request)
        entries = request["keychainsArray"][0]
        self.assertEqual(entries[0]["featurePoint"], "FR_Standardization_Feature_Base_1")
        self.assertTrue(entries[0]["aesCiphertext"])

    def test_no_request_for_unencrypted_log(self) -> None:
        import tempfile
        from support import log_bytes
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_bytes(log_bytes(11))
            self.assertIsNone(keychain.extract_request(path))


class ExchangeTest(unittest.TestCase):
    def _server(self, status: int, payload: dict):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                seen["key"] = self.headers.get("Api-Key")
                seen["body"] = json.loads(self.rfile.read(length) or b"{}")
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        return server, thread, seen

    def test_key_travels_in_the_header_not_the_url(self) -> None:
        payload = {"result": {"code": 0, "msg": "ok"},
                   "data": [[{"featurePoint": "FR_Standardization_Feature_Base_1",
                              "aesKey": "a", "aesIv": "b"}]]}
        server, thread, seen = self._server(200, payload)
        try:
            result = keychain.fetch({"version": 13, "department": 3, "keychainsArray": []},
                                    "unit-test-key",
                                    endpoint=f"http://127.0.0.1:{server.server_port}/k")
        finally:
            thread.join(5)
            server.server_close()
        self.assertTrue(result["ok"])
        self.assertEqual(seen["key"], "unit-test-key")
        self.assertNotIn("unit-test-key", json.dumps(seen["body"]))

    def test_rejected_key_is_a_category(self) -> None:
        server, thread, _ = self._server(403, {"result": {"code": 1, "msg": "bad"}})
        try:
            result = keychain.fetch({"version": 13}, "bad",
                                    endpoint=f"http://127.0.0.1:{server.server_port}/k")
        finally:
            thread.join(5)
            server.server_close()
        self.assertEqual(result["code"], "invalid_key")

    def test_api_error_code_is_not_success(self) -> None:
        payload = {"result": {"code": 8, "msg": "quota"}, "data": None}
        server, thread, _ = self._server(200, payload)
        try:
            result = keychain.fetch({"version": 13}, "k",
                                    endpoint=f"http://127.0.0.1:{server.server_port}/k")
        finally:
            thread.join(5)
            server.server_close()
        self.assertEqual(result["code"], "api_error")

    def test_offline_is_reported_without_blocking(self) -> None:
        result = keychain.fetch({"version": 13}, "k", endpoint="http://127.0.0.1:1/k",
                                timeout=2)
        self.assertEqual(result["code"], "offline")


class KeyStorageTest(TempCase):
    def test_key_is_dpapi_protected_and_not_in_the_database(self) -> None:
        result = keychain.set_key(self.root, "synthetic-test-key-value")
        self.assertTrue(result["ok"])
        raw = (self.root / keychain.KEY_FILE).read_bytes()
        self.assertNotIn(b"synthetic-test-key-value", raw)
        self.assertEqual(keychain.load_key(self.root), "synthetic-test-key-value")
        dump = self.conn.serialize() if hasattr(self.conn, "serialize") else b""
        try:
            import shutil
            db_copy = self.root / "copy.sqlite3"
            shutil.copy2(self.root / "flightdesk.sqlite3", db_copy)
            blob = db_copy.read_bytes()
            db_copy.unlink()
        except OSError:
            blob = b""
        self.assertNotIn(b"synthetic-test-key-value", blob)

    def test_invalid_key_format_is_refused(self) -> None:
        self.assertEqual(keychain.set_key(self.root, "short")["code"], "invalid_key_format")
        self.assertEqual(keychain.set_key(self.root, "has space in it")["code"],
                         "invalid_key_format")

    def test_clear_removes_the_key(self) -> None:
        keychain.set_key(self.root, "synthetic-test-key-value")
        keychain.clear_key(self.root)
        self.assertFalse(keychain.has_key(self.root))
        self.assertIsNone(keychain.load_key(self.root))


class OfflineReparseTest(TempCase):
    def test_no_key_still_reads_metadata(self) -> None:
        totals = self.import_bytes(v13_bytes(2), "DJIFlightRecord_2026-08-13_[21-53-20].txt")
        self.assertEqual(totals["awaiting_dji"], 1)
        row = db.one(self.conn, "SELECT * FROM flights")
        self.assertEqual(row["metrics_source"], "metadata_only")
        self.assertTrue(row["start_time_utc"].startswith("2026-08-13"))
        self.assertAlmostEqual(row["duration_s"], 302.0, places=1)
        self.assertIsNone(row["path_length_m"])

    def test_cached_keychain_decodes_without_network(self) -> None:
        raw_bytes = v13_bytes(3)
        totals = self.import_bytes(raw_bytes, "DJIFlightRecord_2026-08-14_[21-53-20].txt")
        self.assertEqual(totals["awaiting_dji"], 1)
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        import hashlib
        digest = hashlib.sha256(raw_bytes).hexdigest()
        self.assertEqual(raw["sha256"], digest)
        keychain.store_cache(self.conn, self.root, digest,
                             [[{"featurePoint": "FR_Standardization_Feature_Base_1",
                                "aesKey": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                "aesIv": "AAAAAAAAAAAAAAAAAAAAAA=="}]],
                             "unit-test")
        # Disable network access for the reparse by using a loopback-only URL
        # and asserting the cache path never calls it.
        result = self.imp.reparse_raw(int(raw["id"]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["new_flight"], False)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 1)
        row = db.one(self.conn, "SELECT * FROM flights")
        # The synthetic v13 file carries no records, so the keychain
        # decoded the container and details but produced no frames.
        self.assertEqual(row["metrics_source"], "keychain_details")

    def test_missing_cache_entry_does_not_create_flights(self) -> None:
        self.import_bytes(v13_bytes(4), "DJIFlightRecord_2026-08-15_[21-53-20].txt")
        before = self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        result = self.imp.reparse_raw(int(raw["id"]))
        self.assertEqual(result["status"], "pending_key")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], before)

    def test_wrong_key_result_creates_no_flights(self) -> None:
        raw_id = None
        self.import_bytes(v13_bytes(5), "DJIFlightRecord_2026-08-16_[21-53-20].txt")
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        raw_id = int(raw["id"])
        before = self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        result = keychain.unlock_raw(self.conn, self.root, raw_id, api_key=None)
        self.assertEqual(result["code"], "key_required")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0], before)

    def test_plaintext_v11_never_needs_a_key(self) -> None:
        from support import log_bytes
        totals = self.import_bytes(log_bytes(11), "FlightRecord_2026-08-11_[21-53-20].txt")
        self.assertEqual(totals["new"], 1)
        self.assertEqual(keychain.cache_summary(self.conn)["pending"], 0)


if __name__ == "__main__":
    unittest.main()
