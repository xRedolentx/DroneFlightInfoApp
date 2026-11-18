"""Local filesystem layout. Everything private lives under the data root."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "FlightDesk"
PARSER_VERSION = "0.5.7"
PARSER_SOURCE = (
    "https://github.com/lvauvillier/dji-log-parser/releases/download/"
    "v0.5.7/dji-log-x86_64-pc-windows-msvc.zip"
)
PARSER_SHA256 = "C036D9314BDFA72A9C13E93B956CCA89747637C4D5C2A0CABED64A7544242DFD"


def app_home() -> Path:
    override = os.environ.get("FLIGHTDESK_HOME")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_NAME


def default_data_root() -> Path:
    override = os.environ.get("FLIGHTDESK_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return app_home() / "data"


def parser_exe() -> Path:
    override = os.environ.get("FLIGHTDESK_DJI_LOG")
    if override:
        return Path(override).expanduser().resolve()
    return app_home() / "vendor" / "dji-log" / PARSER_VERSION / "dji-log.exe"


def parser_manifest() -> Path:
    return parser_exe().parent / "MANIFEST.json"


def ensure_layout(root: Path) -> dict[str, Path]:
    root = Path(root)
    paths = {
        "root": root,
        "archive": root / "archive",
        "tmp": root / "tmp",
        "backups": root / "backups",
        "exports": root / "exports",
        "logs": root / "logs",
        "keychains": root / "keychains",
    }
    for key, path in paths.items():
        if key == "root":
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    return paths


def db_path(root: Path) -> Path:
    return Path(root) / "flightdesk.sqlite3"


def state_path(root: Path) -> Path:
    return Path(root) / "server.json"
