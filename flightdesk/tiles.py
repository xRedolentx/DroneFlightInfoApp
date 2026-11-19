"""Optional online basemap tiles, behind an explicit owner consent.

Why a local proxy instead of letting the browser fetch tiles:

* **The browser never leaves loopback.** The page keeps `img-src 'self'` and
  `Referrer-Policy: no-referrer`, so the privacy assertions in the test suite
  and the browser gate still mean what they say. Tiles are fetched by the local
  server and served from the loopback origin.
* **A proper User-Agent is possible.** The OpenStreetMap tile policy requires a
  clear, unique agent naming the application; a browser cannot set one, and its
  default agent is not identifiable as this app.
* **Caching is ours to guarantee.** The policy requires honouring the server's
  caching headers or caching for at least seven days. A browser cache can be
  cleared at any moment; a local cache cannot.

OpenStreetMap's own policy (operations.osmfoundation.org/policies/tiles/)
permits interactive viewing by a human, requires visible attribution, forbids
bulk download and prefetching, and permits a caching proxy only if it identifies
itself and honours caching. This module does all three: it never fetches a tile
that is not being displayed, it caches for at least the policy minimum, and it
identifies itself.

Nothing here runs unless the owner records consent in Settings.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import db

# One provider, deliberately. The tile URL template lives in the provider entry
# rather than being hard-coded at the call site, so another OSM-derived provider
# can be added without a code change to the fetch path.
PROVIDERS: dict[str, dict[str, Any]] = {
    "osm": {
        "key": "osm",
        "label": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "host": "tile.openstreetmap.org",
        "attribution": "© OpenStreetMap contributors",
        "attribution_url": "https://www.openstreetmap.org/copyright",
        "policy_url": "https://operations.osmfoundation.org/policies/tiles/",
        "max_zoom": 19,
        "tile_size": 256,
        # The policy asks for a contactable identity. Left blank by default
        # rather than inventing an address; the owner can add one in Settings.
        "contact": "",
    },
}
DEFAULT_PROVIDER = "osm"

# Policy minimum. A tile younger than this is reused without asking the server.
MIN_TTL_S = 7 * 24 * 3600
MAX_TILE_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 15
CACHE_LIMIT_BYTES = 200 * 1024 * 1024


def _consent(conn: sqlite3.Connection) -> dict[str, Any]:
    value = db.get_setting(conn, "external_consent", {})
    return value if isinstance(value, dict) else {}


def consented(conn: sqlite3.Connection) -> bool:
    return bool(_consent(conn).get("map"))


def provider_key(conn: sqlite3.Connection) -> str:
    key = db.get_setting(conn, "map_provider", DEFAULT_PROVIDER)
    return key if key in PROVIDERS else DEFAULT_PROVIDER


def provider(conn: sqlite3.Connection) -> dict[str, Any]:
    key = provider_key(conn)
    entry = dict(PROVIDERS[key])
    stored_contact = db.get_setting(conn, "map_contact", "")
    if isinstance(stored_contact, str) and stored_contact.strip():
        entry["contact"] = stored_contact.strip()
    return entry


def user_agent(entry: dict[str, Any]) -> str:
    """A distinct, stable agent naming this application.

    The tile policy blocks generic library agents (`python-urllib/x.y`,
    `curl/x.y`) because the operator cannot be identified. This one names the
    app and carries the optional contact the owner supplied.
    """
    from . import __version__

    base = f"FlightDesk/{__version__} (local personal flight log)"
    contact = entry.get("contact") or ""
    if contact:
        return f"{base} (contact: {contact})"
    return base


def cache_dir(root: Path, entry: dict[str, Any]) -> Path:
    return Path(root) / "tiles" / str(entry["key"])


def _tile_path(root: Path, entry: dict[str, Any], z: int, x: int, y: int) -> Path:
    return cache_dir(root, entry) / str(z) / str(x) / f"{y}.png"


def _as_int(value: Any) -> int | None:
    """Accept only a plain non-negative integer, whether a str or an int.

    `bool` is rejected explicitly: `True` is an `int` in Python and would
    otherwise sail through as a zoom level of 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def valid_tile(entry: dict[str, Any], z: Any, x: Any, y: Any) -> tuple[int, int, int] | None:
    """Bounds-check a tile coordinate.

    The endpoint takes three numbers from the browser, so they are pinned to
    integers inside the pyramid for the configured provider. This is what stops
    the proxy becoming a fetch-anything service.
    """
    zi, xi, yi = _as_int(z), _as_int(x), _as_int(y)
    if zi is None or xi is None or yi is None:
        return None
    if zi < 0 or zi > int(entry.get("max_zoom", 19)):
        return None
    span = 1 << zi
    if not (0 <= xi < span) or not (0 <= yi < span):
        return None
    return zi, xi, yi


def _cache_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for file in path.rglob("*.png"):
        try:
            total += file.stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


def _trim_cache(root: Path, entry: dict[str, Any]) -> None:
    """Keep the cache bounded, evicting the least recently used tiles."""
    path = cache_dir(root, entry)
    count, total = _cache_size(path)
    if total <= CACHE_LIMIT_BYTES:
        return
    files = []
    for file in path.rglob("*.png"):
        try:
            stat = file.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, file))
    files.sort()
    for _, size, file in files:
        if total <= CACHE_LIMIT_BYTES * 0.8:
            break
        try:
            file.unlink()
            total -= size
        except OSError:
            continue


def status(conn: sqlite3.Connection, root: Path | None = None) -> dict[str, Any]:
    entry = provider(conn)
    count, total = _cache_size(cache_dir(root, entry)) if root else (0, 0)
    return {
        "consented": consented(conn),
        "provider": entry["key"],
        "label": entry["label"],
        "attribution": entry["attribution"],
        "attribution_url": entry["attribution_url"],
        "policy_url": entry["policy_url"],
        "max_zoom": entry["max_zoom"],
        "contact": entry["contact"],
        "cached_tiles": count,
        "cache_bytes": total,
        "report_issue_url": "https://www.openstreetmap.org/fixthemap",
    }


def fetch(conn: sqlite3.Connection, root: Path, z: Any, x: Any, y: Any) -> dict[str, Any]:
    """Return one tile's bytes, from cache when possible.

    Never reaches the network unless the owner has consented, and never fetches
    a tile the caller is not displaying.
    """
    if not consented(conn):
        return {"ok": False, "code": "map_consent_required"}
    entry = provider(conn)
    coords = valid_tile(entry, z, x, y)
    if coords is None:
        return {"ok": False, "code": "bad_tile_coordinate"}
    zi, xi, yi = coords

    path = _tile_path(root, entry, zi, xi, yi)
    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
            if age < MIN_TTL_S:
                data = path.read_bytes()
                if data:
                    return {"ok": True, "bytes": data, "cached": True,
                            "content_type": "image/png"}
        except OSError:
            pass

    url = str(entry["url"]).format(z=zi, x=xi, y=yi)
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent(entry),
        "Accept": "image/png,image/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            data = response.read(MAX_TILE_BYTES + 1)
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {"ok": False, "code": "rate_limited"}
        if exc.code == 403:
            return {"ok": False, "code": "blocked_by_provider"}
        return {"ok": False, "code": f"http_{exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "code": "offline"}
    if len(data) > MAX_TILE_BYTES:
        return {"ok": False, "code": "tile_too_large"}
    if ctype and not ctype.startswith("image/"):
        return {"ok": False, "code": "not_an_image"}
    if not data:
        return {"ok": False, "code": "empty_tile"}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".part")
        temp.write_bytes(data)
        os.replace(temp, path)
    except OSError:
        # A cache miss on a read-only root still serves the tile.
        return {"ok": True, "bytes": data, "cached": False, "content_type": "image/png"}
    _trim_cache(root, entry)
    return {"ok": True, "bytes": data, "cached": False, "content_type": "image/png"}


def clear_cache(root: Path, entry: dict[str, Any]) -> int:
    path = cache_dir(root, entry)
    removed = 0
    if not path.exists():
        return 0
    for file in path.rglob("*.png"):
        try:
            file.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def degrees_to_tile(lat: float, lon: float, z: int, size: int = 256) -> tuple[float, float]:
    """Web Mercator pixel position, used by tests to keep the axis order honest."""
    scale = size * (1 << z)
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale
    return x, y
