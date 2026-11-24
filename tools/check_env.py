"""Check that this machine can run Flight Desk, without changing anything.

    python tools\\check_env.py          # human-readable
    python tools\\check_env.py --json   # machine-readable, sanitized

Exit 0 when every mandatory component is present, 2 when one is missing. Nothing
is installed or downloaded: a check and an install are deliberately different
commands, so a normal launch never starts a network fetch.

The pinned parser checksum is for the upstream ZIP, not the extracted EXE. Pass
`--parser-zip <file>` to verify a downloaded archive against it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flightdesk import paths  # noqa: E402

MIN_PYTHON = (3, 11)
MIN_SQLITE = (3, 35)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parser_version(exe: Path) -> str | None:
    try:
        proc = subprocess.run([str(exe), "--version"], capture_output=True, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[0] if proc.stdout else None


def check(parser_zip: Path | None = None) -> dict:
    checks: list[dict] = []
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    checks.append({"name": "python", "ok": py_ok,
                   "detail": platform.python_version(),
                   "needed": ">= %d.%d" % MIN_PYTHON})
    sqlite_ver = sqlite3.sqlite_version
    sqlite_tuple = tuple(int(x) for x in sqlite_ver.split(".")[:2])
    checks.append({"name": "sqlite", "ok": sqlite_tuple >= MIN_SQLITE,
                   "detail": sqlite_ver, "needed": ">= %d.%d" % MIN_SQLITE})
    exe = paths.parser_exe()
    version = _parser_version(exe) if exe.is_file() else None
    checks.append({"name": "parser", "ok": bool(version), "detail": version,
                   "path": str(exe), "needed": paths.PARSER_VERSION})
    shim = paths.app_home() / "vendor" / "flightdesk-decode" / "flightdesk-decode.exe"
    checks.append({"name": "decode-shim", "ok": shim.is_file(),
                   "detail": shim.name if shim.is_file() else None})
    if parser_zip is not None:
        ok = parser_zip.is_file() and _sha(parser_zip) == paths.PARSER_SHA256
        checks.append({"name": "parser-zip-checksum", "ok": ok,
                       "detail": "matches" if ok else "does not match the pinned value"})
    pwsh = shutil.which("powershell.exe") or shutil.which("pwsh")
    checks.append({"name": "powershell", "ok": bool(pwsh),
                   "detail": pwsh, "needed": "for the USB collector"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "os": platform.platform(), "app_home": str(paths.app_home())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--parser-zip", default=None)
    args = parser.parse_args(argv)
    result = check(Path(args.parser_zip) if args.parser_zip else None)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"OS: {result['os']}")
        for item in result["checks"]:
            mark = "ok  " if item["ok"] else "MISSING"
            detail = item.get("detail") or item.get("path") or ""
            print(f"  [{mark}] {item['name']}: {detail}"
                  + (f" (needs {item['needed']})" if not item["ok"] and item.get("needed") else ""))
        print("All mandatory components present." if result["ok"]
              else "Some components are missing; see MISSING above.")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
