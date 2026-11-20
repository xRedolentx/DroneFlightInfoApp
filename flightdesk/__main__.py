"""Command line entry point: serve the app, seed demo data, run diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from . import db, paths

# Where the launcher lands. The app defaults to this view anyway, but naming it
# here means a double-click always arrives at the same page — even if the
# history in the tab, or the default, points somewhere else.
HOME_HASH = "#/flights"


def home_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/{HOME_HASH}"


def _pid_alive(pid: int) -> bool:
    """Is this process still running? os.kill(pid, 0) is not usable on Windows:
    any signal number other than CTRL_C/CTRL_BREAK terminates the process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _answers_as_flightdesk(port: int) -> bool:
    """A live socket is not enough: the port may have been reused by something
    else. The page's own meta tag is a cheap way to know what is answering."""
    if not 0 < port < 65536:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
            head = resp.read(8192).decode("utf-8", "replace")
    except Exception:
        return False
    return 'name="fd-token"' in head


def live_instance(root: Path, *, port: int | None = None) -> int | None:
    """Port of a Flight Desk already serving this data root, else None.

    Double-clicking the launcher a second time used to start a second server on
    another free port, so the address changed on every click and the first
    window looked abandoned. If the app is already up for this root, the click
    should just bring the page back. An explicit --port is a request for a new
    server and is never redirected.
    """
    if port:
        return None
    try:
        state = json.loads((paths.app_home() / "server.json").read_text(encoding="utf-8"))
        pid = int(state.get("pid") or 0)
        candidate = int(state.get("port") or 0)
        state_root = Path(str(state.get("root") or ""))
    except (OSError, ValueError, TypeError):
        return None
    if not pid or not candidate:
        return None
    try:
        if state_root.resolve() != Path(root).resolve():
            return None
    except OSError:
        return None
    if not _pid_alive(pid) or not _answers_as_flightdesk(candidate):
        return None
    return candidate


def _open_browser(url: str) -> None:
    """Opening the page is the whole point of the click, so a browser that will
    not start must say so rather than leave a blank-looking launch."""
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print(f"Could not open a browser automatically. Open this address: {url}")


def _root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    pointer = paths.app_home() / "active_data_root.txt"
    if pointer.exists():
        try:
            candidate = Path(pointer.read_text(encoding="utf-8").strip())
            if candidate.exists():
                return candidate
        except OSError:
            pass
    return paths.default_data_root()


def serve(args: argparse.Namespace) -> int:
    from .server import build_server, write_state

    root = _root(args.root)
    already = live_instance(root, port=args.port)
    if already is not None:
        url = home_url(already)
        print(f"Flight Desk is already running at {url}")
        print("Opening that window; this launcher is not starting a second copy.")
        if not args.no_browser:
            _open_browser(url)
        return 0
    paths.ensure_layout(root)
    server, app = build_server(root, args.port)
    write_state(root, app.port)
    url = home_url(app.port)
    print(f"Flight Desk is running at {url}")
    print(f"Data root: {root}")
    if args.watch_usb:
        app.start_watcher()
    if not args.no_browser:
        threading.Timer(0.6, _open_browser, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_event.set()
        server.server_close()
    return 0


def seed(args: argparse.Namespace) -> int:
    from .demo import seed_demo

    root = _root(args.root)
    result = seed_demo(root)
    print(json.dumps({"ok": result.get("ok"),
                      "already_seeded": result.get("already_seeded"),
                      "flights": result.get("flights"),
                      "raw_files": result.get("raw_files"),
                      "equipment": result.get("equipment")}))
    return 0


def diag(args: argparse.Namespace) -> int:
    """Aggregate-only diagnostics. Never prints names, paths or record contents."""
    from tools.diag import main as diag_main

    return diag_main(args.paths, version_check=args.versions)


def selftest(args: argparse.Namespace) -> int:
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(Path(__file__).resolve().parent.parent / "tests"))
    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    print(json.dumps({
        "ok": result.wasSuccessful(),
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
    }))
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flightdesk", description="Flight Desk")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="run the local web app")
    p_serve.add_argument("--root", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument("--watch-usb", action="store_true")
    p_serve.set_defaults(func=serve)

    p_seed = sub.add_parser("seed-demo", help="create a synthetic demo data root")
    p_seed.add_argument("--root", default=None)
    p_seed.set_defaults(func=seed)

    p_diag = sub.add_parser("diag", help="aggregate-only import diagnostics")
    p_diag.add_argument("paths", nargs="+")
    p_diag.add_argument("--versions", action="store_true")
    p_diag.set_defaults(func=diag)

    p_test = sub.add_parser("selftest", help="run the test suite")
    p_test.set_defaults(func=selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return serve(argparse.Namespace(root=None, port=None, no_browser=False,
                                        watch_usb=False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
