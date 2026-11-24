"""Exporting the archived originals to an ordinary folder.

The archive stores `archive/ab/cd/<sha256>.bin`, which is right for integrity
and unusable anywhere else. This is the additive, incremental copy that puts the
logs back under their real names. All fixtures are invented.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import TempCase, TempRoot, log_bytes

from flightdesk import db, errlog, paths, rawexport
from flightdesk.server import build_server


class RawExportTest(TempCase):
    def _export(self, dest, **kwargs):
        options = {"variants": False, "group_by_year": False}
        options.update(kwargs)
        return rawexport.run(self.conn, self.root, dest, **options)

    def test_first_export_writes_one_file_per_flight_under_its_real_name(self) -> None:
        self.import_bytes(log_bytes(11, days=0),
                          "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.import_bytes(log_bytes(11, days=1),
                          "DJIFlightRecord_2026-08-12_[21-53-20].txt")
        dest = self.root / "out"
        report = self._export(dest)
        self.assertEqual(report["copied"], 2, report)
        names = sorted(p.name for p in dest.iterdir() if p.suffix == ".txt")
        self.assertEqual(names, [
            "DJIFlightRecord_2026-08-11_[21-53-20].txt",
            "DJIFlightRecord_2026-08-12_[21-53-20].txt",
        ])
        # The bytes are the archived bytes, not a re-encode.
        sample = dest / names[0]
        self.assertEqual(sample.read_bytes(), log_bytes(11, days=0))

    def test_second_export_copies_nothing_and_says_so(self) -> None:
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = self.root / "out"
        self.assertEqual(self._export(dest)["copied"], 1)
        again = self._export(dest)
        self.assertEqual(again["copied"], 0, again)
        self.assertEqual(again["skipped"], 1, again)

    def test_a_name_collision_gets_the_sha_suffix(self) -> None:
        # Two different flights saved under one name — a re-saved log from a
        # second controller, for instance.
        self.import_bytes(log_bytes(11, days=0), "FlightRecord_2026-08-11_[21-53-20].txt")
        self.import_bytes(log_bytes(11, days=40), "FlightRecord_2026-08-11_[21-53-20].txt")
        self.assertEqual(self.counts()["flights"], 2)
        dest = self.root / "out"
        report = self._export(dest)
        self.assertEqual(report["copied"], 2, report)
        names = sorted(p.name for p in dest.iterdir() if p.suffix == ".txt")
        self.assertEqual(len(names), 2)
        self.assertEqual(len({n.lower() for n in names}), 2)
        self.assertTrue(any("__" in n for n in names), names)

    def test_variants_exports_every_raw_file(self) -> None:
        base = log_bytes(11)
        self.import_bytes(base, "FlightRecord_2026-08-11_[21-53-20].txt")
        variant = bytearray(base)
        variant[20] = 9
        self.import_bytes(bytes(variant), "FlightRecord_2026-08-11_[21-53-20].txt")
        self.assertEqual(self.counts()["flights"], 1)
        self.assertEqual(self.counts()["raw"], 2)
        dest = self.root / "out"
        self.assertEqual(self._export(dest, variants=False)["copied"], 1)
        dest2 = self.root / "out2"
        self.assertEqual(self._export(dest2, variants=True)["copied"], 2)

    def test_a_file_deleted_from_the_folder_is_copied_again(self) -> None:
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = self.root / "out"
        self._export(dest)
        target = dest / "DJIFlightRecord_2026-08-11_[21-53-20].txt"
        self.assertTrue(target.exists())
        target.unlink()
        report = self._export(dest)
        self.assertEqual(report["copied"], 1, report)
        self.assertTrue(target.exists())

    def test_a_file_the_owner_put_there_is_never_touched(self) -> None:
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = self.root / "out"
        dest.mkdir(parents=True)
        mine = dest / "notes.txt"
        mine.write_text("my own notes", encoding="utf-8")
        # And a file that happens to have the exported name but is not ours.
        clash = dest / "DJIFlightRecord_2026-08-11_[21-53-20].txt"
        clash.write_text("not the flight", encoding="utf-8")
        report = self._export(dest)
        self.assertEqual(report["copied"], 1, report)
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own notes")
        self.assertEqual(clash.read_text(encoding="utf-8"), "not the flight")
        names = sorted(p.name for p in dest.iterdir() if p.suffix == ".txt")
        self.assertEqual(len(names), 3, names)
        self.assertTrue(any("__" in n for n in names), names)

    def test_group_by_year_puts_files_under_the_flight_year(self) -> None:
        self.import_bytes(log_bytes(11, days=0),
                          "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = self.root / "out"
        report = self._export(dest, group_by_year=True)
        self.assertEqual(report["copied"], 1, report)
        self.assertTrue((dest / "2026" / "DJIFlightRecord_2026-08-11_[21-53-20].txt").exists())

    def test_the_manifest_records_path_and_size(self) -> None:
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        dest = self.root / "out"
        self._export(dest)
        manifest = json.loads((dest / rawexport.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], rawexport.MANIFEST_VERSION)
        self.assertEqual(len(manifest["files"]), 1)
        entry = next(iter(manifest["files"].values()))
        self.assertEqual(entry["path"], "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        self.assertGreater(entry["size"], 0)

    def test_a_truncated_export_is_reported_not_left_behind(self) -> None:
        # The archive object is intact; the copy step is what is checked here.
        self.import_bytes(log_bytes(11), "DJIFlightRecord_2026-08-11_[21-53-20].txt")
        raw = db.one(self.conn, "SELECT * FROM raw_files")
        stored = self.root / "archive" / raw["stored_rel"]
        stored.write_bytes(stored.read_bytes()[:-10])  # simulate damage on disk
        dest = self.root / "out"
        report = self._export(dest)
        self.assertEqual(report["copied"], 0, report)
        self.assertEqual(len(report["problems"]), 1, report)
        self.assertEqual(report["problems"][0]["code"], "hash_mismatch")
        self.assertEqual([p for p in dest.iterdir() if p.suffix == ".part"], [])

    def test_settings_round_trip_and_defaults(self) -> None:
        self.assertEqual(rawexport.settings(self.conn)["variants"], False)
        saved = rawexport.save_settings(self.conn, {"folder": "C:/tmp/logs",
                                                    "variants": True,
                                                    "auto_sync": True})
        self.assertEqual(saved["folder"], "C:/tmp/logs")
        self.assertTrue(saved["variants"])
        self.assertTrue(rawexport.settings(self.conn)["auto_sync"])
        # The audit row never carries the path itself.
        row = db.one(self.conn, "SELECT payload_json FROM audit_log "
                                "WHERE action='raw_export_settings' ORDER BY id DESC")
        self.assertNotIn("tmp/logs", row["payload_json"])


class RawExportPreservationTest(TempCase):
    """P02/F02: an export is additive. Nothing in the folder is replaced."""

    def setUp(self) -> None:
        super().setUp()
        self.dest = self.root / "out"
        self.dest.mkdir(parents=True, exist_ok=True)
        self.data = log_bytes(11)
        self.name = "FlightRecord_2026-08-11_[21-53-20].txt"
        self.import_bytes(self.data, self.name)
        self.sha = db.one(self.conn, "SELECT sha256 FROM raw_files")["sha256"]

    def _export(self, **kwargs):
        options = {"variants": False, "group_by_year": False}
        options.update(kwargs)
        return rawexport.run(self.conn, self.root, self.dest, **options)

    def _txt_names(self) -> list[str]:
        return sorted(p.name for p in self.dest.iterdir() if p.suffix == ".txt")

    def test_a_modified_export_of_different_length_is_preserved(self) -> None:
        self.assertEqual(self._export()["copied"], 1)
        target = self.dest / self.name
        target.write_bytes(b"owner edited this")
        report = self._export()
        self.assertEqual(target.read_bytes(), b"owner edited this")
        self.assertEqual(report["copied"], 1, report)
        self.assertEqual(report["preserved"], 1, report)
        self.assertEqual(len(self._txt_names()), 2, self._txt_names())
        fresh = [p for p in self.dest.iterdir() if p.suffix == ".txt" and p != target][0]
        self.assertEqual(fresh.read_bytes(), self.data)

    def test_a_modified_export_of_the_same_length_is_detected(self) -> None:
        # Size alone proves nothing. The reviewed build skipped this as "already
        # exported" while the owner's edited bytes sat in the folder.
        self.assertEqual(self._export()["copied"], 1)
        target = self.dest / self.name
        edited = bytearray(self.data)
        edited[30] ^= 0xFF
        target.write_bytes(bytes(edited))
        report = self._export()
        self.assertEqual(target.read_bytes(), bytes(edited))
        self.assertEqual(report["copied"], 1, report)
        self.assertEqual(report["preserved"], 1, report)
        self.assertEqual(report["skipped"], 0, report)
        copies = [p for p in self.dest.iterdir() if p.suffix == ".txt" and p != target]
        self.assertEqual([p.read_bytes() for p in copies], [self.data])

    def test_an_occupied_suffixed_name_is_preserved(self) -> None:
        quiet = self.dest / self.name
        quiet.write_text("owner first", encoding="utf-8")
        suffixed = self.dest / (
            Path(self.name).stem + "__" + self.sha[:8] + ".txt")
        suffixed.write_text("owner second", encoding="utf-8")
        report = self._export()
        self.assertEqual(quiet.read_text(encoding="utf-8"), "owner first")
        self.assertEqual(suffixed.read_text(encoding="utf-8"), "owner second")
        self.assertEqual(report["copied"], 1, report)
        self.assertEqual(len(self._txt_names()), 3, self._txt_names())
        written = [p for p in self.dest.iterdir() if p.suffix == ".txt"
                   and p not in (quiet, suffixed)][0]
        self.assertEqual(written.read_bytes(), self.data)

    def test_an_unrelated_part_file_is_never_overwritten(self) -> None:
        stray = self.dest / "something.part"
        stray.write_bytes(b"another program's temp")
        report = self._export()
        self.assertEqual(stray.read_bytes(), b"another program's temp")
        self.assertEqual(report["copied"], 1, report)
        self.assertEqual([p for p in self.dest.iterdir() if p.suffix == ".part"], [stray])

    def test_a_target_appearing_during_publication_is_preserved(self) -> None:
        original = rawexport._publish_no_clobber
        planted: list[Path] = []

        def racing(tmp, target):
            if not planted:
                planted.append(target)
                target.write_text("appeared late", encoding="utf-8")
            return original(tmp, target)

        rawexport._publish_no_clobber = racing
        try:
            report = self._export()
        finally:
            rawexport._publish_no_clobber = original
        self.assertEqual(planted[0].read_text(encoding="utf-8"), "appeared late")
        self.assertEqual(report["copied"], 1, report)
        written = [p for p in self.dest.iterdir() if p.suffix == ".txt"
                   and p != planted[0]]
        self.assertEqual([p.read_bytes() for p in written], [self.data])

    def test_two_concurrent_exports_never_lose_bytes(self) -> None:
        import threading
        results: list[dict] = []

        def export() -> None:
            conn = db.connect(paths.db_path(self.root))
            try:
                results.append(rawexport.run(conn, self.root, self.dest,
                                             variants=True, group_by_year=False))
            finally:
                conn.close()

        threads = [threading.Thread(target=export) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(results), 2)
        names = self._txt_names()
        self.assertGreaterEqual(len(names), 1)
        for path in self.dest.iterdir():
            if path.suffix == ".txt":
                self.assertEqual(path.read_bytes(), self.data, path.name)
        self.assertEqual([p for p in self.dest.iterdir() if p.suffix == ".part"], [])

    def test_a_manifest_entry_that_escapes_is_ignored(self) -> None:
        victim = self.root / "victim.txt"
        victim.write_text("outside the export folder", encoding="utf-8")
        (self.dest / rawexport.MANIFEST_NAME).write_text(json.dumps({
            "version": 1,
            "files": {self.sha: {"path": "../victim.txt", "size": len(self.data)}},
        }), encoding="utf-8")
        report = self._export()
        self.assertEqual(victim.read_text(encoding="utf-8"), "outside the export folder")
        self.assertEqual(report["copied"], 1, report)
        self.assertTrue((self.dest / self.name).exists())

    def test_the_job_refuses_two_starts_in_a_row(self) -> None:
        import time
        job = rawexport.Job()
        first = job.start(paths.db_path(self.root), self.root, self.dest,
                          variants=False, group_by_year=False)
        second = job.start(paths.db_path(self.root), self.root, self.dest,
                           variants=False, group_by_year=False)
        self.assertTrue(first)
        self.assertFalse(second)
        for _ in range(200):
            if not job.running():
                break
            time.sleep(0.05)
        self.assertFalse(job.running())
        self.assertEqual(job.snapshot()["state"], "done")


class RawExportApiTest(unittest.TestCase):
    """The path the Settings button actually takes."""

    def test_export_runs_through_the_http_routes(self) -> None:
        import http.client
        import threading
        import time

        t = TempRoot()
        try:
            server, app = build_server(t.root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def call(method: str, path: str, body: dict | None = None) -> dict:
                conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
                payload = b"" if body is None else json.dumps(body).encode()
                conn.request(method, path, payload, {
                    "Host": f"127.0.0.1:{app.port}",
                    "X-FD-Token": app.session.token,
                    "Cookie": f"fd_session={app.session.token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                })
                response = conn.getresponse()
                data = json.loads(response.read() or b"{}")
                conn.close()
                return data

            try:
                from flightdesk.importer import Importer
                Importer(t.root, t.conn).import_stream(
                    __import__("io").BytesIO(log_bytes(11)),
                    "DJIFlightRecord_2026-08-11_[21-53-20].txt",
                    source_kind="archive", source_label="test")
                dest = t.root / "exported"
                started = call("POST", "/api/raw-export/start",
                               {"folder": str(dest), "variants": False})
                self.assertTrue(started.get("ok"), started)
                for _ in range(100):
                    state = call("GET", "/api/raw-export")["job"]
                    if state.get("state") in ("done", "error"):
                        break
                    time.sleep(0.1)
                self.assertEqual(state["state"], "done", state)
                self.assertEqual(state["copied"], 1, state)
                exported = sorted(p.name for p in dest.iterdir() if p.suffix == ".txt")
                self.assertEqual(exported, ["DJIFlightRecord_2026-08-11_[21-53-20].txt"])
                payload = call("GET", "/api/raw-export")
                self.assertEqual(payload["settings"]["folder"], str(dest))
                self.assertEqual(payload["counts"]["flights"], 1)
                # `/open` is deliberately not called with a real folder here: it
                # launches Explorer, and a test must not open a window on the
                # owner's desktop. Its refusal path is covered below.
            finally:
                server.shutdown()
                server.server_close()
                errlog.close_logger(t.root)
        finally:
            t.close()

    def test_start_without_a_folder_is_refused_with_a_reason(self) -> None:
        import http.client
        import threading

        t = TempRoot()
        try:
            server, app = build_server(t.root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=20)
                payload = b"{}"
                conn.request("POST", "/api/raw-export/start", payload, {
                    "Host": f"127.0.0.1:{app.port}",
                    "X-FD-Token": app.session.token,
                    "Cookie": f"fd_session={app.session.token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                })
                response = conn.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertEqual(body.get("code"), "no_folder")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                errlog.close_logger(t.root)
        finally:
            t.close()


if __name__ == "__main__":
    unittest.main()
