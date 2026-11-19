"""Loopback-only HTTP server with origin, host and session protection."""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import (archive, backup, capture, climate, db, demo, keychain, maintenance,
               paths, report, schedule, security, tiles, usb, views)
from .importer import Importer

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 512 * 1024 * 1024
UPLOAD_LIMIT_FILE = 512 * 1024 * 1024
UPLOAD_LIMIT_ZIP = 2 * 1024 * 1024 * 1024
STREAMED_UPLOAD_PATH = "/api/import/upload"
TILES_PREFIX = "/api/tiles/"
ROUTES: dict[tuple[str, re.Pattern], Callable] = {}


def route(method: str, pattern: str):
    def register(fn):
        ROUTES[(method, re.compile(f"^{pattern}$"))] = fn
        return fn
    return register


@dataclass
class Ctx:
    app: "App"
    handler: BaseHTTPRequestHandler
    params: dict[str, str]
    query: dict[str, list[str]]
    body: bytes
    conn: sqlite3.Connection

    def json_body(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def q(self, key: str, default: str | None = None) -> str | None:
        values = self.query.get(key)
        return values[0] if values else default

    def qint(self, key: str) -> int | None:
        raw = self.q(key)
        if raw is None or not re.fullmatch(r"-?\d+", raw):
            return None
        return int(raw)

    def as_int(self, name: str) -> int | None:
        raw = self.params.get(str(name), "")
        return int(raw) if re.fullmatch(r"-?\d+", raw or "") else None


class App:
    def __init__(self, root: Path):
        self.root = Path(root)
        paths.ensure_layout(self.root)
        self.db_file = paths.db_path(self.root)
        conn = db.init_db(self.db_file)
        conn.close()
        self.session = security.Session()
        # A capability for tile images only. An <img> (and an SVG <image>) cannot
        # send the X-FD-Token header, so those requests would always fail the
        # double-submit check. Rather than weaken that check, tiles carry their
        # own secret in the path: unknown to any other page, rotated every run,
        # and useless for anything except fetching a tile through this proxy.
        self.tile_token = secrets.token_urlsafe(24)
        # Exports need their own capability for the same reason as tiles: an
        # <a download> cannot send the X-FD-Token header. Crucially it also lets
        # the browser start the download inside the click itself. Fetching the
        # file first and clicking afterwards expires the user gesture, and the
        # browser then treats every download after the first few as an
        # automatic download and blocks it — which is what made the redacted
        # export appear to do nothing.
        self.export_token = secrets.token_urlsafe(24)
        self.port = 0
        self.watcher_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.db_file)

    def start_watcher(self) -> None:
        if self.watcher_thread is not None:
            return

        def loop() -> None:
            while not self.stop_event.is_set():
                conn = None
                try:
                    conn = self.connect()
                    config = db.get_setting(conn, "usb_watcher", {"enabled": False})
                    if config.get("enabled"):
                        usb.scan(conn, self.root)
                except Exception:
                    pass
                finally:
                    if conn is not None:
                        conn.close()
                interval = 45
                try:
                    conn2 = self.connect()
                    interval = int(db.get_setting(conn2, "usb_watcher", {}).get("interval_s", 45))
                    conn2.close()
                except Exception:
                    pass
                self.stop_event.wait(max(20, interval))

        self.watcher_thread = threading.Thread(target=loop, name="fd-usb-watcher", daemon=True)
        self.watcher_thread.start()


# ------------------------------------------------------------------ helpers

def _no_store(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header(
        "Content-Security-Policy",
        # Inline style attributes are allowed because the UI uses a few layout
        # utilities and width bars. No user-controlled HTML reaches the DOM
        # unescaped, and script execution stays restricted to bundled files.
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'",
    )


def _send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    _no_store(handler)
    handler.end_headers()
    handler.wfile.write(data)


def _send_text(handler, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8",
               extra: dict[str, str] | None = None) -> None:
    data = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    _no_store(handler)
    handler.end_headers()
    handler.wfile.write(data)


def _file_path(rel: str) -> Path | None:
    base = WEB_DIR.resolve()
    target = (base / rel).resolve()
    if base not in target.parents and target != base:
        return None
    return target if target.is_file() else None


# ------------------------------------------------------------------ routes

@route("GET", r"/api/health")
def health(ctx: Ctx) -> None:
    _send_json(ctx.handler, {
        "ok": True,
        "schema": db.SCHEMA.split("PRAGMA")[0].count("CREATE TABLE"),
        "root": str(ctx.app.root),
        "writes_blocked": False,
    })


@route("GET", r"/api/state")
def api_state(ctx: Ctx) -> None:
    payload = views.state(ctx.conn)
    payload["db_path"] = str(ctx.app.db_file)
    # The page needs this to build download links that the browser can follow
    # without a header (and therefore without losing the click gesture).
    payload["export_token"] = ctx.app.export_token
    # A destination the owner can accept without ever opening a dialog.
    payload["backup_default"] = str(paths.app_home() / "backups")
    if isinstance(payload.get("map"), dict):
        # The page needs this capability to build tile URLs.
        payload["map"]["tile_token"] = ctx.app.tile_token
    _send_json(ctx.handler, payload)


@route("POST", r"/api/settings")
def api_settings(ctx: Ctx) -> None:
    body = ctx.json_body()
    with db.tx(ctx.conn):
        for key in ("unit_preference", "last_flight_id", "last_filters", "privacy_mode",
                    "pilot_default", "pilots", "external_consent", "data_mode",
                    "real_inputs_selected", "map_mode", "map_provider", "map_contact"):
            if key in body:
                db.set_setting(ctx.conn, key, body[key])
        db.audit(ctx.conn, "owner", "settings", None, None, sorted(body))
    _send_json(ctx.handler, {"ok": True})


@route("GET", r"/api/flights")
def api_flights(ctx: Ctx) -> None:
    def one(name: str) -> Any:
        return ctx.q(name)

    filters = {
        "search": one("search"),
        "from": one("from"),
        "to": one("to"),
        "pilot": one("pilot"),
        "tag": one("tag"),
        "status": one("status"),
    }
    aircraft = ctx.qint("aircraft")
    battery = ctx.qint("battery")
    if aircraft is not None:
        row = db.one(ctx.conn, "SELECT name FROM equipment WHERE id=?", (aircraft,))
        filters["aircraft"] = aircraft
        filters["aircraft_name"] = row["name"] if row else None
    if battery is not None:
        filters["battery"] = battery
    _send_json(ctx.handler, views.flights(ctx.conn, filters))


@route("GET", r"/api/flights/(\d+)")
def api_flight(ctx: Ctx) -> None:
    flight_id = ctx.as_int(1)
    detail = views.flight_detail(ctx.conn, flight_id)
    if detail is None:
        _send_json(ctx.handler, {"ok": False, "code": "not_found"}, 404)
        return
    _send_json(ctx.handler, detail)


@route("POST", r"/api/flights/(\d+)/edit")
def api_flight_edit(ctx: Ctx) -> None:
    flight_id = int(ctx.params["1"])
    body = ctx.json_body()
    sets = []
    values: list[Any] = []
    if "pilot" in body and isinstance(body["pilot"], str):
        sets.append("pilot=?")
        values.append(body["pilot"][:120])
    if "notes" in body and isinstance(body["notes"], str):
        sets.append("notes=?")
        values.append(body["notes"][:4000])
    if "tags" in body and isinstance(body["tags"], list):
        tags = [str(t)[:40] for t in body["tags"][:20] if str(t).strip()]
        sets.append("tags_json=?")
        values.append(json.dumps(tags))
    if not sets:
        _send_json(ctx.handler, {"ok": False, "code": "no_fields"}, 400)
        return
    with db.tx(ctx.conn):
        ctx.conn.execute(f"UPDATE flights SET {', '.join(sets)},updated_at=? WHERE id=?",
                         (*values, db.now_utc(), flight_id))
        db.audit(ctx.conn, "owner", "flight_edit", "flight", str(flight_id),
                 {"fields": [s.split("=")[0] for s in sets]})
    _send_json(ctx.handler, {"ok": True})


@route("POST", r"/api/flights/(\d+)/reassign")
def api_flight_reassign(ctx: Ctx) -> None:
    body = ctx.json_body()
    kind = str(body.get("kind") or "")
    equipment_id = body.get("equipment_id")
    equipment_id = int(equipment_id) if isinstance(equipment_id, int) else None
    _send_json(ctx.handler, capture.reassign_flight(ctx.conn, int(ctx.params["1"]),
                                                    kind, equipment_id))


# ---------------------------------------------------------------- equipment

@route("GET", r"/api/equipment")
def api_equipment(ctx: Ctx) -> None:
    include = ctx.q("archived") in ("1", "true")
    rows = views.equipment_list(ctx.conn, include)
    statuses = {s["equipment_id"]: s for s in maintenance.all_statuses(
        ctx.conn, include_archived=True)}
    for row in rows:
        row["status_summary"] = sorted(
            {s["status"] for s in maintenance.all_statuses(ctx.conn, include_archived=True)
             if s["equipment_id"] == row["id"]},
            key=lambda v: maintenance.STATUS_ORDER.get(v, 0), reverse=True,
        )
    _send_json(ctx.handler, {"rows": rows})


@route("GET", r"/api/equipment/(\d+)")
def api_equipment_detail(ctx: Ctx) -> None:
    detail = views.equipment_detail(ctx.conn, int(ctx.params["1"]))
    if detail is None:
        _send_json(ctx.handler, {"ok": False, "code": "not_found"}, 404)
        return
    _send_json(ctx.handler, detail)


@route("POST", r"/api/equipment/(\d+)/edit")
def api_equipment_edit(ctx: Ctx) -> None:
    body = ctx.json_body()
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else body
    allowed = {k: v for k, v in fields.items() if isinstance(v, (str, type(None)))}
    _send_json(ctx.handler, capture.owner_edit_equipment(
        ctx.conn, int(ctx.params["1"]), allowed))


@route("POST", r"/api/equipment/(\d+)/serial")
def api_equipment_serial(ctx: Ctx) -> None:
    body = ctx.json_body()
    serial = str(body.get("serial") or "").strip()
    _send_json(ctx.handler, capture.assign_serial(
        ctx.conn, serial, int(ctx.params["1"]), override=bool(body.get("override", True))))


@route("POST", r"/api/equipment/(\d+)/reset-from-log")
def api_reset_from_log(ctx: Ctx) -> None:
    equipment_id = int(ctx.params["1"])
    eq = db.one(ctx.conn, "SELECT * FROM equipment WHERE id=?", (equipment_id,))
    if eq is None:
        _send_json(ctx.handler, {"ok": False, "code": "not_found"}, 404)
        return
    serial = eq["serial"]
    if not serial:
        _send_json(ctx.handler, {"ok": False, "code": "no_serial"}, 400)
        return
    now = db.now_utc()
    with db.tx(ctx.conn):
        ctx.conn.execute("DELETE FROM serial_links WHERE serial=? AND override=1", (serial,))
        ctx.conn.execute(
            "INSERT OR IGNORE INTO serial_links(serial,equipment_id,source,override,updated_at) "
            "VALUES(?,?,'log',0,?)", (serial, equipment_id, now))
        db.audit(ctx.conn, "owner", "serial_reset", "equipment", str(equipment_id), {})
    _send_json(ctx.handler, {"ok": True})


# -------------------------------------------------------------- maintenance

@route("GET", r"/api/maintenance")
def api_maintenance(ctx: Ctx) -> None:
    include = ctx.q("archived") in ("1", "true")
    payload = maintenance.overview(ctx.conn)
    if include:
        payload["statuses"] = maintenance.all_statuses(ctx.conn, include_archived=True)
    _send_json(ctx.handler, payload)


@route("GET", r"/api/rules/(\d+)")
def api_rule(ctx: Ctx) -> None:
    _send_json(ctx.handler, maintenance.rule_detail(ctx.conn, int(ctx.params["1"])))


@route("POST", r"/api/rules/(\d+)/service")
def api_rule_service(ctx: Ctx) -> None:
    rule_id = int(ctx.params["1"])
    body = ctx.json_body()
    action = str(body.get("action") or "completed")
    equipment_id = body.get("equipment_id")
    rule = db.one(ctx.conn, "SELECT equipment_id FROM rules WHERE id=?", (rule_id,))
    if rule is None:
        _send_json(ctx.handler, {"ok": False, "code": "not_found"}, 404)
        return
    equipment_id = int(equipment_id) if isinstance(equipment_id, int) else int(rule["equipment_id"])
    if action == "oneoff":
        _send_json(ctx.handler, maintenance.oneoff_service(
            ctx.conn, equipment_id, str(body.get("notes") or ""),
            linked_rule_id=rule_id if body.get("link_rule") else None,
            occurred_at=body.get("occurred_at"),
        ))
        return
    _send_json(ctx.handler, maintenance.complete_service(
        ctx.conn, equipment_id, rule_id, str(body.get("notes") or ""),
        occurred_at=body.get("occurred_at"),
        kind="skipped" if action == "skipped" else "completed",
    ))


@route("POST", r"/api/rules/(\d+)/policy")
def api_rule_policy(ctx: Ctx) -> None:
    body = ctx.json_body()
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if "days_limit" in body:
        value = body["days_limit"]
        fields["days_limit"] = None if value in (None, "", 0) else int(value)
    if "due_soon_pct" in body:
        try:
            fields["due_soon_pct"] = max(0.1, min(1.0, float(body["due_soon_pct"])))
        except (TypeError, ValueError):
            pass
    for key in ("flights_limit", "air_minutes_limit", "charges_limit"):
        if key in body and body[key] is not None:
            try:
                fields[key] = float(body[key])
            except (TypeError, ValueError):
                pass
    _send_json(ctx.handler, maintenance.set_rule_policy(ctx.conn, int(ctx.params["1"]), **fields))


@route("POST", r"/api/services/(\d+)/undo")
def api_service_undo(ctx: Ctx) -> None:
    _send_json(ctx.handler, maintenance.undo_service(ctx.conn, int(ctx.params["1"])))


@route("POST", r"/api/services/oneoff")
def api_service_oneoff(ctx: Ctx) -> None:
    body = ctx.json_body()
    equipment_id = body.get("equipment_id")
    if not isinstance(equipment_id, int):
        _send_json(ctx.handler, {"ok": False, "code": "equipment_required"}, 400)
        return
    linked = body.get("linked_rule_id")
    _send_json(ctx.handler, maintenance.oneoff_service(
        ctx.conn, equipment_id, str(body.get("notes") or ""),
        linked_rule_id=int(linked) if isinstance(linked, int) else None,
        occurred_at=body.get("occurred_at"),
    ))


@route("POST", r"/api/coverage/resolve")
def api_coverage(ctx: Ctx) -> None:
    body = ctx.json_body()
    flight_id = body.get("flight_id")
    snapshot_id = body.get("snapshot_id")
    if not isinstance(flight_id, int) or not isinstance(snapshot_id, int):
        _send_json(ctx.handler, {"ok": False, "code": "ids_required"}, 400)
        return
    _send_json(ctx.handler, maintenance.resolve_coverage(
        ctx.conn, flight_id, snapshot_id, str(body.get("decision") or ""),
        str(body.get("note") or "")))


# ------------------------------------------------------------------- import

@route("GET", r"/api/import/history")
def api_import_history(ctx: Ctx) -> None:
    _send_json(ctx.handler, {
        "batches": views.import_history(ctx.conn),
        "pending": views.pending_files(ctx.conn),
        "unresolved": views.unresolved_flights(ctx.conn),
    })


def _batch_response(ctx: Ctx, batch) -> None:
    payload = batch.as_dict()
    payload["state"] = views.state(ctx.conn)
    codes: dict[str, int] = {}
    for item in payload.get("items") or []:
        code = item.get("code")
        if code:
            codes[code] = codes.get(code, 0) + 1
    payload["codes"] = codes
    _send_json(ctx.handler, payload)


def _stream_body_to(temp_dir: Path, handler, limit: int) -> tuple[Path | None, str | None]:
    """Copy the request body to a temp file without holding it in memory."""
    length_raw = handler.headers.get("Content-Length")
    try:
        length = int(length_raw or 0)
    except ValueError:
        return None, "bad_length"
    if length <= 0:
        return None, "empty_body"
    if length > limit:
        return None, "body_too_large"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / f"upload-{os.getpid()}-{time.time_ns()}.part"
    remaining = length
    try:
        with open(target, "wb") as fh:
            while remaining > 0:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    return None, "upload_interrupted"
                fh.write(chunk)
                remaining -= len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        target.unlink(missing_ok=True)
        return None, "write_failed"
    return target, None


@route("POST", r"/api/import/upload")
def api_import_upload(ctx: Ctx) -> None:
    raw_name = ctx.handler.headers.get("X-FD-Filename") or ""
    member = ctx.handler.headers.get("X-FD-Member") or raw_name
    try:
        name = urllib.parse.unquote(raw_name)
        member_path = urllib.parse.unquote(member)
    except Exception:
        _send_json(ctx.handler, {"ok": False, "code": "bad_filename"}, 400)
        return
    name = Path(name.replace("\\", "/")).name
    if not name:
        _send_json(ctx.handler, {"ok": False, "code": "bad_filename"}, 400)
        return
    is_zip = name.lower().endswith(".zip")
    if not is_zip and not archive.is_flight_filename(name):
        _send_json(ctx.handler, {"ok": False, "code": "not_a_flight_log"}, 400)
        return
    temp, error = _stream_body_to(ctx.app.root / "tmp", ctx.handler,
                                  UPLOAD_LIMIT_ZIP if is_zip else UPLOAD_LIMIT_FILE)
    if temp is None:
        status = 413 if error == "body_too_large" else 400
        _send_json(ctx.handler, {"ok": False, "code": error}, status)
        return
    kind = "manual"
    low = member_path.lower()
    if "controller" in low:
        kind = "extras"
    elif "flight_logs" in low:
        kind = "archive"
    importer = Importer(ctx.app.root, ctx.conn)
    try:
        if is_zip:
            batch = importer.import_zip(temp)
        else:
            batch = importer.import_single(
                temp, source_kind=kind, display_name=name,
                member_path=member_path, source_label="Drag and drop",
            )
    finally:
        temp.unlink(missing_ok=True)
    _batch_response(ctx, batch)


@route("POST", r"/api/pick")
def api_pick(ctx: Ctx) -> None:
    """Ask Windows for one path and hand it back without acting on it.

    The owner can then see and edit the path in a text field. A file dialog
    owned by the server can fail to surface; typing the path must always work.
    """
    body = ctx.json_body()
    kind = "file" if body.get("kind") == "file" else "folder"
    picked = security.pick_path(kind)
    if not picked:
        _send_json(ctx.handler, {"ok": True, "cancelled": True})
        return
    _send_json(ctx.handler, {"ok": True, "path": picked})


@route("POST", r"/api/import/path")
def api_import_path(ctx: Ctx) -> None:
    body = ctx.json_body()
    raw = str(body.get("path") or "")
    if not raw:
        picked = security.pick_path("file" if body.get("pick") == "file" else "folder")
        if not picked:
            _send_json(ctx.handler, {"ok": True, "cancelled": True})
            return
        raw = picked
    target = Path(raw).expanduser()
    importer = Importer(ctx.app.root, ctx.conn)
    if target.is_dir():
        batch = importer.import_folder(target)
    elif target.is_file():
        batch = importer.import_single(target)
    else:
        _send_json(ctx.handler, {"ok": False, "code": "path_not_found"}, 400)
        return
    _batch_response(ctx, batch)


@route("POST", r"/api/import/demo")
def api_import_demo(ctx: Ctx) -> None:
    result = demo.seed_demo(ctx.app.root)
    _send_json(ctx.handler, result)


@route("POST", r"/api/reparse")
def api_reparse(ctx: Ctx) -> None:
    body = ctx.json_body()
    raw_id = body.get("raw_id")
    importer = Importer(ctx.app.root, ctx.conn)
    if isinstance(raw_id, int):
        _send_json(ctx.handler, importer.reparse_raw(raw_id))
        return
    if body.get("scope") == "cached":
        # One bounded check the owner can run with the network off: decode an
        # encrypted record using only its cached keychain, which is what makes
        # an offline reparse possible.
        row = db.one(
            ctx.conn,
            "SELECT r.id, r.format_version FROM raw_files r "
            "JOIN keychains k ON k.raw_sha256=r.sha256 AND k.status='cached' "
            "WHERE r.format_version>=13 AND r.status='imported' ORDER BY r.id DESC LIMIT 1",
        )
        if row is None:
            _send_json(ctx.handler, {"ok": False, "code": "no_cached_keychain"})
            return
        outcome = importer.reparse_raw(int(row["id"]))
        _send_json(ctx.handler, {
            "ok": bool(outcome.get("ok")),
            "version": row["format_version"],
            "status": outcome.get("status"),
            "new_flight": outcome.get("new_flight"),
            "keychain_source": "cache",
        })
        return
    rows = db.all_rows(ctx.conn, "SELECT id FROM raw_files WHERE status!='imported' LIMIT 500")
    changed = 0
    decoded = 0
    for row in rows:
        outcome = importer.reparse_raw(int(row["id"]))
        changed += 1
        decoded += int(bool(outcome.get("ok")))
    _send_json(ctx.handler, {"ok": True, "attempted": changed, "decoded": decoded})


# ---------------------------------------------------------------------- key

@route("GET", r"/api/key")
def api_key_state(ctx: Ctx) -> None:
    summary = keychain.cache_summary(ctx.conn)
    _send_json(ctx.handler, {
        "configured": keychain.has_key(ctx.app.root),
        "cache": summary,
        "pending": views.pending_files(ctx.conn),
        "endpoint": keychain.ENDPOINT,
        "decoder": (paths.app_home() / "vendor" / "flightdesk-decode" /
                    "flightdesk-decode.exe").exists(),
    })


@route("POST", r"/api/key")
def api_key_set(ctx: Ctx) -> None:
    body = ctx.json_body()
    key = body.get("key")
    if not isinstance(key, str):
        _send_json(ctx.handler, {"ok": False, "code": "key_required"}, 400)
        return
    result = keychain.set_key(ctx.app.root, key)
    with db.tx(ctx.conn):
        db.audit(ctx.conn, "owner", "key_set", None, None, {"ok": result.get("ok")})
    _send_json(ctx.handler, result)


@route("DELETE", r"/api/key")
def api_key_clear(ctx: Ctx) -> None:
    result = keychain.clear_key(ctx.app.root)
    with db.tx(ctx.conn):
        db.audit(ctx.conn, "owner", "key_clear", None, None, {})
    _send_json(ctx.handler, result)


@route("POST", r"/api/key/unlock")
def api_key_unlock(ctx: Ctx) -> None:
    body = ctx.json_body()
    api_key = keychain.load_key(ctx.app.root)
    targets: list[int] = []
    if isinstance(body.get("raw_id"), int):
        targets = [int(body["raw_id"])]
    elif body.get("all"):
        targets = [int(r["id"]) for r in db.all_rows(
            ctx.conn, "SELECT id FROM raw_files WHERE status='pending_key' LIMIT 500")]
    results = []
    for raw_id in targets:
        results.append({"raw_id": raw_id,
                        **keychain.unlock_raw(ctx.conn, ctx.app.root, raw_id, api_key)})
    decoded = sum(1 for r in results if r.get("ok"))
    codes = sorted({str(r.get("code")) for r in results
                    if not r.get("ok") and r.get("code")})
    _send_json(ctx.handler, {
        # Nothing pending is a finished state, not a failure: clicking Decode
        # pending twice must not look like an error.
        "ok": bool(results) and decoded == len(results),
        "nothing_pending": not targets,
        "attempted": len(results),
        "decoded": decoded,
        "failed": len(results) - decoded,
        "codes": codes,
        "results": results,
        "key_configured": keychain.has_key(ctx.app.root),
    })


# ---------------------------------------------------------------------- usb

@route("GET", r"/api/usb")
def api_usb(ctx: Ctx) -> None:
    _send_json(ctx.handler, usb.status(ctx.conn))


@route("POST", r"/api/usb/scan")
def api_usb_scan(ctx: Ctx) -> None:
    result = usb.scan(ctx.conn, ctx.app.root)
    payload = result.as_dict()
    payload["state"] = views.state(ctx.conn)
    payload["status_detail"] = usb.status(ctx.conn)
    _send_json(ctx.handler, payload)


@route("POST", r"/api/usb/watcher")
def api_usb_watcher(ctx: Ctx) -> None:
    body = ctx.json_body()
    value = usb.set_watcher(ctx.conn, bool(body.get("enabled")),
                            int(body.get("interval_s") or 45))
    _send_json(ctx.handler, {"ok": True, "watcher": value})


@route("POST", r"/api/usb/logon")
def api_usb_logon(ctx: Ctx) -> None:
    body = ctx.json_body()
    if not body.get("confirm"):
        _send_json(ctx.handler, {"ok": False, "code": "confirm_required",
                                 "message": "This installs a per-user logon task. Send confirm:true."},
                   status=400)
        return
    install = bool(body.get("install", True))
    result = (usb.install_logon_task(ctx.conn, Path(__file__).resolve().parent.parent /
                                     "FlightDesk.cmd")
              if install else usb.remove_logon_task(ctx.conn))
    _send_json(ctx.handler, result)


# ------------------------------------------------------------------ capture

@route("POST", r"/api/capture/preview")
def api_capture_preview(ctx: Ctx) -> None:
    body = ctx.json_body()
    text = ""
    if isinstance(body.get("path"), str) and body["path"]:
        target = Path(body["path"]).expanduser()
        if not target.is_file():
            _send_json(ctx.handler, {"ok": False, "code": "capture_not_found"}, 400)
            return
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _send_json(ctx.handler, {"ok": False, "code": "capture_unreadable"}, 400)
            return
    elif isinstance(body.get("text"), str):
        text = body["text"]
    elif body.get("pick"):
        picked = security.pick_path("file")
        if not picked:
            _send_json(ctx.handler, {"ok": True, "cancelled": True})
            return
        try:
            text = Path(picked).read_text(encoding="utf-8", errors="replace")
        except OSError:
            _send_json(ctx.handler, {"ok": False, "code": "capture_unreadable"}, 400)
            return
    if not text.strip():
        _send_json(ctx.handler, {"ok": False, "code": "capture_empty"}, 400)
        return
    doc = capture.parse_capture(text)
    preview = capture.preview_capture(ctx.conn, doc)
    preview["ok"] = True
    _send_json(ctx.handler, preview)


@route("POST", r"/api/capture/apply")
def api_capture_apply(ctx: Ctx) -> None:
    body = ctx.json_body()
    text = ""
    if isinstance(body.get("path"), str) and body["path"]:
        target = Path(body["path"]).expanduser()
        if not target.is_file():
            _send_json(ctx.handler, {"ok": False, "code": "capture_not_found"}, 400)
            return
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _send_json(ctx.handler, {"ok": False, "code": "capture_unreadable"}, 400)
            return
    elif isinstance(body.get("text"), str):
        text = body["text"]
    if not text.strip():
        _send_json(ctx.handler, {"ok": False, "code": "capture_empty"}, 400)
        return
    doc = capture.parse_capture(text)
    result = capture.apply_capture(ctx.conn, doc,
                                   str(body.get("label") or "AirData equipment capture"))
    result["preview"] = {k: v for k, v in capture.preview_capture(ctx.conn, doc).items()
                         if k in ("service_events", "resolved_services", "grouped_services")}
    _send_json(ctx.handler, result)


# ------------------------------------------------------------------ map tiles

@route("GET", r"/api/tiles/([A-Za-z0-9_\-]{16,})/(\d+)/(\d+)/(\d+)\.png")
def api_tile(ctx: Ctx) -> None:
    token = ctx.params.get("1") or ""
    if not hmac.compare_digest(token, ctx.app.tile_token):
        _send_json(ctx.handler, {"ok": False, "code": "bad_tile_token"}, 403)
        return
    result = tiles.fetch(ctx.conn, ctx.app.root, ctx.params.get("2"),
                         ctx.params.get("3"), ctx.params.get("4"))
    if not result.get("ok"):
        code = str(result.get("code") or "tile_unavailable")
        status = 403 if code == "map_consent_required" else 404
        if code == "rate_limited":
            status = 429
        _send_json(ctx.handler, {"ok": False, "code": code}, status)
        return
    data = result["bytes"]
    ctx.handler.send_response(200)
    ctx.handler.send_header("Content-Type", result.get("content_type") or "image/png")
    ctx.handler.send_header("Content-Length", str(len(data)))
    ctx.handler.send_header("X-Content-Type-Options", "nosniff")
    # Deliberately NOT _no_store: the tile policy requires honouring caching, and
    # a no-store header would re-download every pan from a donation-funded
    # server. The browser cache mirrors the local cache lifetime.
    ctx.handler.send_header("Cache-Control", f"public, max-age={tiles.MIN_TTL_S}")
    ctx.handler.send_header("X-Tile-Cache", "hit" if result.get("cached") else "miss")
    ctx.handler.end_headers()
    ctx.handler.wfile.write(data)


@route("GET", r"/api/map")
def api_map(ctx: Ctx) -> None:
    payload = tiles.status(ctx.conn, ctx.app.root)
    payload["tile_token"] = ctx.app.tile_token
    _send_json(ctx.handler, payload)


@route("POST", r"/api/map/cache")
def api_map_cache(ctx: Ctx) -> None:
    body = ctx.json_body()
    if body.get("action") != "clear":
        _send_json(ctx.handler, {"ok": False, "code": "unknown_action"}, 400)
        return
    entry = tiles.provider(ctx.conn)
    removed = tiles.clear_cache(ctx.app.root, entry)
    db.audit(ctx.conn, "owner", "map_cache_clear", "tiles", entry["key"],
             {"removed": removed})
    _send_json(ctx.handler, {"ok": True, "removed": removed,
                             **tiles.status(ctx.conn, ctx.app.root)})


# ------------------------------------------------------------------ recon

@route("GET", r"/api/local-image")
def api_local_image(ctx: Ctx) -> None:
    """Serve one local reference image the owner names explicitly.

    Nothing is uploaded and nothing is listed. Only common image types, only
    an existing regular file, and only through the authenticated loopback
    origin."""
    raw = ctx.q("path")
    if not raw:
        _send_json(ctx.handler, {"ok": False, "code": "path_required"}, 400)
        return
    target = Path(raw).expanduser()
    try:
        target = target.resolve(strict=True)
    except (OSError, RuntimeError):
        _send_json(ctx.handler, {"ok": False, "code": "not_found"}, 404)
        return
    if not target.is_file() or target.suffix.lower() not in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif"
    }:
        _send_json(ctx.handler, {"ok": False, "code": "not_an_image"}, 400)
        return
    try:
        data = target.read_bytes()
    except OSError:
        _send_json(ctx.handler, {"ok": False, "code": "unreadable"}, 400)
        return
    if len(data) > 40 * 1024 * 1024:
        _send_json(ctx.handler, {"ok": False, "code": "too_large"}, 413)
        return
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    ctx.handler.send_response(200)
    ctx.handler.send_header("Content-Type", ctype)
    ctx.handler.send_header("Content-Length", str(len(data)))
    ctx.handler.send_header("Cache-Control", "no-store")
    ctx.handler.send_header("X-Content-Type-Options", "nosniff")
    ctx.handler.send_header("Content-Security-Policy", "default-src 'none'")
    ctx.handler.end_headers()
    ctx.handler.wfile.write(data)


@route("POST", r"/api/maintenance/sources")
def api_maintenance_sources(ctx: Ctx) -> None:
    """Add the DoneDoctor checks and attach AirData's own task lists.

    Deliberately a separate, explicit action: the AirData counters were verified
    against the archive, so nothing here rewrites them.
    """
    seeded = schedule.seed_donedoctor_rules(ctx.conn)
    items = schedule.backfill_airdata_items(ctx.conn)
    _send_json(ctx.handler, {"ok": True, "seeded": seeded, "checklists": items})


@route("GET", r"/api/battery-temperature")
def api_battery_temperature(ctx: Ctx) -> None:
    payload = climate.fleet_summary(ctx.conn)
    payload["ok"] = True
    _send_json(ctx.handler, payload)


@route("POST", r"/api/battery-temperature/limits")
def api_battery_temperature_limits(ctx: Ctx) -> None:
    body = ctx.json_body()
    result = climate.save_limits(ctx.conn, body if isinstance(body, dict) else {})
    if result.get("ok"):
        db.audit(ctx.conn, "owner", "battery_temp_limits", "settings", None,
                 {k: result["limits"][k] for k in ("enabled", "cold_c", "hot_c")})
    _send_json(ctx.handler, result)


@route("GET", r"/api/recon")
def api_recon(ctx: Ctx) -> None:
    _send_json(ctx.handler, report.reconciliation(ctx.conn, persist=ctx.q("persist") != "0"))


@route("POST", r"/api/recon/select-real")
def api_recon_select(ctx: Ctx) -> None:
    body = ctx.json_body()
    with db.tx(ctx.conn):
        db.set_setting(ctx.conn, "real_inputs_selected", bool(body.get("selected", True)))
        db.set_setting(ctx.conn, "data_mode", "real" if body.get("selected") else "synthetic")
        db.audit(ctx.conn, "owner", "real_inputs_selected", None, None,
                 {"selected": bool(body.get("selected"))})
    _send_json(ctx.handler, report.reconciliation(ctx.conn))


# ----------------------------------------------------------------- backup

@route("POST", r"/api/backup")
def api_backup(ctx: Ctx) -> None:
    body = ctx.json_body()
    dest = body.get("destination")
    if not isinstance(dest, str) or not dest:
        picked = security.pick_path("folder")
        if not picked:
            _send_json(ctx.handler, {"ok": True, "cancelled": True})
            return
        dest = picked
    result = backup.create_backup(ctx.conn, ctx.app.root, Path(dest),
                                  str(body.get("note") or ""),
                                  allow_cloud=bool(body.get("allow_cloud")))
    _send_json(ctx.handler, result)


@route("GET", r"/api/backups")
def api_backups(ctx: Ctx) -> None:
    rows = db.all_rows(ctx.conn, "SELECT * FROM backup_runs ORDER BY id DESC LIMIT 20")
    _send_json(ctx.handler, {"rows": [dict(r) for r in rows],
                             "running": backup.is_backup_running(ctx.app.root)})


@route("POST", r"/api/backup/validate")
def api_backup_validate(ctx: Ctx) -> None:
    body = ctx.json_body()
    path = body.get("path")
    if not isinstance(path, str) or not path:
        picked = security.pick_path("file")
        if not picked:
            _send_json(ctx.handler, {"ok": True, "cancelled": True})
            return
        path = picked
    result = backup.validate_backup(Path(path))
    result.pop("manifest", None) if result.get("ok") is False else None
    _send_json(ctx.handler, result)


@route("POST", r"/api/backup/restore")
def api_backup_restore(ctx: Ctx) -> None:
    body = ctx.json_body()
    path = body.get("path")
    target = body.get("target")
    if not isinstance(path, str) or not path:
        picked = security.pick_path("file")
        if not picked:
            _send_json(ctx.handler, {"ok": True, "cancelled": True})
            return
        path = picked
    if not isinstance(target, str) or not target:
        _send_json(ctx.handler, {"ok": False, "code": "target_required"}, 400)
        return
    result = backup.restore_backup(Path(path), Path(target),
                                   allow_existing=bool(body.get("allow_existing")))
    _send_json(ctx.handler, result)


@route("POST", r"/api/backup/activate")
def api_backup_activate(ctx: Ctx) -> None:
    body = ctx.json_body()
    target = body.get("target")
    if not isinstance(target, str) or not target:
        _send_json(ctx.handler, {"ok": False, "code": "target_required"}, 400)
        return
    if not body.get("confirm"):
        _send_json(ctx.handler, {"ok": False, "code": "confirm_required"}, 400)
        return
    result = backup.activate_data_root(Path(target))
    _send_json(ctx.handler, result)


# ----------------------------------------------------------------- export

@route("GET", r"/api/export/(flights|equipment|services|recon)\.csv")
def api_export(ctx: Ctx) -> None:
    kind = ctx.params["1"]
    redact = ctx.q("redact") in ("1", "true")
    if kind == "flights":
        text = report.flights_csv(ctx.conn, redact)
    elif kind == "equipment":
        text = report.equipment_csv(ctx.conn, redact)
    elif kind == "services":
        text = report.services_csv(ctx.conn, redact)
    else:
        text = report.report_csv(report.reconciliation(ctx.conn, persist=False))
    filename = f"FlightDesk-{kind}{'-redacted' if redact else ''}.csv"
    _send_text(ctx.handler, text, ctype="text/csv; charset=utf-8",
               extra={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "FlightDesk"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    app: App

    def log_message(self, fmt: str, *args) -> None:  # never log paths or queries
        return

    def _host_ok(self) -> bool:
        return security.host_allowed(self.headers.get("Host"), self.app.port)

    def _export_token_ok(self, path: str, query: str) -> bool:
        """A download URL is authorised by its own per-run capability.

        Browsers cannot attach a header to a download, so the token travels in
        the query string. It is per-run, loopback-only, and only accepted on the
        export routes.
        """
        if not path.startswith("/api/export/"):
            return False
        supplied = (urllib.parse.parse_qs(query).get("token") or [""])[0]
        return bool(supplied) and hmac.compare_digest(supplied, self.app.export_token)

    def _session_ok(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        match = re.search(r"fd_session=([A-Za-z0-9_\-]+)", cookie)
        header = self.headers.get("X-FD-Token")
        return bool(
            match and self.app.session.matches(match.group(1))
            and header and self.app.session.matches(header)
        )

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return security.origin_allowed(origin, self.app.port)

    def _reject(self, status: int, code: str) -> None:
        _send_json(self, {"ok": False, "code": code}, status)

    def _serve_static(self, path: str) -> None:
        rel = path.lstrip("/")
        if rel == "":
            rel = "index.html"
        if rel.startswith("assets/"):
            rel = rel[len("assets/"):]
        if rel in ("", "index.html"):
            index = WEB_DIR / "index.html"
            if not index.exists():
                self._reject(500, "ui_missing")
                return
            html = index.read_text(encoding="utf-8")
            html = html.replace("__FD_TOKEN__", self.app.session.token)
            html = html.replace("__FD_APP__", "Flight Desk")
            _send_text(self, html, ctype="text/html; charset=utf-8",
                       extra={"Set-Cookie": self.app.session.cookie_header()})
            return
        target = _file_path(rel)
        if target is None:
            self._reject(404, "not_found")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        if target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        _no_store(self)
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            return b""
        if size < 0 or size > MAX_BODY:
            raise ValueError("body_too_large")
        return self.rfile.read(size)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._reject(403, "host_not_allowed")
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if method != "GET" and not self._origin_ok():
            self._reject(403, "origin_not_allowed")
            return
        if not path.startswith("/api/"):
            if method != "GET":
                self._reject(405, "method_not_allowed")
                return
            self._serve_static(path)
            return
        if not path.startswith(TILES_PREFIX) and not self._session_ok():
            # Exports carry their own capability in the query string; see App.
            if not (path.startswith("/api/export/") and self._export_token_ok(path, parsed.query)):
                self._reject(403, "session_required")
                return
        try:
            # The upload route streams its own body to disk; reading it here
            # would double the memory cost of a large handoff ZIP.
            if path == STREAMED_UPLOAD_PATH and method == "POST":
                body = b""
                self.close_connection = True
            else:
                body = self._read_body()
        except ValueError:
            self._reject(413, "body_too_large")
            return
        query = urllib.parse.parse_qs(parsed.query)
        conn = self.app.connect()
        try:
            for (route_method, pattern), handler in ROUTES.items():
                if route_method != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                ctx = Ctx(app=self.app, handler=self, params=match.groupdict(),
                          query=query, body=body, conn=conn)
                if match.groups():
                    ctx.params = {str(i + 1): g for i, g in enumerate(match.groups())}
                handler(ctx)
                return
            self._reject(404, "no_route")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            # The browser owner sees a category; the transcript never sees a
            # traceback or private value.
            self._reject(500, "internal_error")
        finally:
            conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        # A browser tab closing mid-request must not print a traceback (and
        # certainly not a private path) to the console.
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054):
            return
        return super().handle_error(request, client_address)


def free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("no loopback port available")


def build_server(root: Path, port: int | None = None) -> tuple[Server, App]:
    app = App(root)
    chosen = port or free_port()
    app.port = chosen
    server = Server(("127.0.0.1", chosen), Handler)
    Handler.app = app
    return server, app


def write_state(root: Path, port: int) -> None:
    state = {"port": port, "pid": os.getpid(), "root": str(root),
             "started": db.now_utc()}
    security.atomic_private_write(
        paths.app_home() / "server.json", json.dumps(state).encode("utf-8")
    )
