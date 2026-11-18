"""Command line entry point: serve the app, seed demo data, run diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import db, paths


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
    paths.ensure_layout(root)
    server, app = build_server(root, args.port)
    write_state(root, app.port)
    url = f"http://127.0.0.1:{app.port}/"
    print(f"Flight Desk is running at {url}")
    print(f"Data root: {root}")
    if args.watch_usb:
        app.start_watcher()
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
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
