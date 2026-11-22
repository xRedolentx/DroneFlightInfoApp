"""A small rotating error log under `<root>/logs/`.

The app used to swallow every watcher exception (`except Exception: pass`) and
answer every route failure with a bare `internal_error`, so a broken controller
path looked exactly like a controller with nothing on it. The owner had no
place to look. This module is that place: one file, bounded, rotated, and
scrubbed so it never becomes a second copy of the private data.

What is never written: the DJI key, serials, coordinates, or a user file path
outside the data root. The app's own source paths do appear in tracebacks —
without them a traceback is unreadable, and they are not the owner's data.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOGGER_NAME = "flightdesk.error"
_LOGGERS: dict[str, logging.Logger] = {}

# A Windows or POSIX absolute path, and any long run of key-like characters.
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s\"'<>|]+|/(?:[\w.\-]+/)+[\w.\-]+)")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def _scrub(text: str, root: Path) -> str:
    """Remove paths outside the data root and anything that looks like a key."""
    root_text = str(root).rstrip("\\/")

    def path_repl(match: re.Match) -> str:
        found = match.group(0)
        if found.startswith(root_text) or "flightdesk" in found.replace("\\", "/").lower():
            return found
        return "<path>"

    text = _PATH_RE.sub(path_repl, text)
    return _TOKEN_RE.sub("<redacted>", text)


def get_logger(root: Path, *, name: str = LOGGER_NAME) -> logging.Logger:
    key = f"{name}:{Path(root)}"
    logger = _LOGGERS.get(key)
    if logger is not None:
        return logger
    logger = logging.getLogger(key)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            log_dir = Path(root) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / "flightdesk.log", maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT, encoding="utf-8", delay=True,
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            # A read-only root must not turn a log write into a crash.
            logger.addHandler(logging.NullHandler())
    _LOGGERS[key] = logger
    return logger


def record(root: Path, operation: str, exc: BaseException, *,
           logger: logging.Logger | None = None) -> str:
    """Log one exception under a named operation. Returns the short category.

    The category is what the UI shows ("OSError"), never the message, because a
    message can carry a path or a value that must not reach the screen.
    """
    log = logger or get_logger(root)
    category = type(exc).__name__
    try:
        log.error("%s failed: %s: %s", operation, category,
                  _scrub(str(exc), Path(root)), exc_info=exc)
    except Exception:  # noqa: BLE001 - logging must never raise into the caller
        pass
    return category


def note(root: Path, message: str, *, logger: logging.Logger | None = None) -> None:
    log = logger or get_logger(root)
    try:
        log.info("%s", _scrub(message, Path(root)))
    except Exception:  # noqa: BLE001
        pass


def close_logger(root: Path, *, name: str = LOGGER_NAME) -> None:
    """Release the file handle. Tests delete their temporary roots; a Windows
    open handle makes that fail, which is a real cost of a long-lived handler."""
    key = f"{name}:{Path(root)}"
    logger = _LOGGERS.pop(key, None)
    if logger is None:
        return
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
        logger.removeHandler(handler)


def summary(root: Path) -> dict[str, Any]:
    """Size and location, for the Settings screen. No file contents."""
    path = Path(root) / "logs" / "flightdesk.log"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "exists": path.exists(), "bytes": size,
            "max_bytes": MAX_BYTES, "backups": BACKUP_COUNT}
