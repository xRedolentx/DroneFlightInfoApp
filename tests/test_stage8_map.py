"""Optional online basemap: consent gate, tile policy compliance, caching.

The rules under test come from OpenStreetMap's tile usage policy: no use
without identification, honour caching for at least seven days, never fetch a
tile a user is not looking at, and never become an open fetch-anything proxy.
"""
from __future__ import annotations

import math
import unittest
from unittest import mock

from support import TempCase

from flightdesk import db, tiles


class _Resp:
    """A minimal urlopen() stand-in that records what was asked for."""

    def __init__(self, data: bytes = b"\x89PNG\r\n\x1a\nfake",
                 content_type: str = "image/png") -> None:
        self._data = data
        self.headers = {"Content-Type": content_type}

    def read(self, limit: int = -1) -> bytes:
        return self._data if limit < 0 else self._data[:limit]

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc) -> None:
        return None


class ConsentTest(TempCase):
    def test_no_consent_means_no_request(self) -> None:
        with mock.patch.object(tiles.urllib.request, "urlopen") as opener:
            result = tiles.fetch(self.conn, self.root, 12, 1200, 1500)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "map_consent_required")
        opener.assert_not_called()

    def test_consent_is_what_unlocks_it(self) -> None:
        db.set_setting(self.conn, "external_consent", {"map": True})
        with mock.patch.object(tiles.urllib.request, "urlopen",
                               return_value=_Resp()) as opener:
            result = tiles.fetch(self.conn, self.root, 12, 1200, 1500)
        self.assertTrue(result["ok"], result)
        opener.assert_called_once()

    def test_withdrawing_consent_stops_requests_again(self) -> None:
        db.set_setting(self.conn, "external_consent", {"map": True})
        with mock.patch.object(tiles.urllib.request, "urlopen", return_value=_Resp()):
            self.assertTrue(tiles.fetch(self.conn, self.root, 12, 1200, 1500)["ok"])
        db.set_setting(self.conn, "external_consent", {"map": False})
        with mock.patch.object(tiles.urllib.request, "urlopen") as opener:
            result = tiles.fetch(self.conn, self.root, 13, 2400, 3000)
        self.assertEqual(result["code"], "map_consent_required")
        opener.assert_not_called()

    def test_weather_consent_does_not_imply_map_consent(self) -> None:
        db.set_setting(self.conn, "external_consent", {"weather": True})
        self.assertFalse(tiles.consented(self.conn))


class CoordinateTest(TempCase):
    def setUp(self) -> None:
        super().setUp()
        db.set_setting(self.conn, "external_consent", {"map": True})

    def test_out_of_range_coordinates_are_refused_before_any_fetch(self) -> None:
        cases = [
            (-1, 0, 0), (20, 0, 0), (2, 4, 3), (2, 3, 4),
            ("x", 0, 0), (None, 0, 0), (True, 0, 0),
        ]
        for z, x, y in cases:
            with mock.patch.object(tiles.urllib.request, "urlopen") as opener:
                result = tiles.fetch(self.conn, self.root, z, x, y)
            self.assertFalse(result["ok"], (z, x, y))
            opener.assert_not_called()

    def test_provider_host_is_fixed_so_the_proxy_cannot_be_pointed_elsewhere(self) -> None:
        entry = tiles.provider(self.conn)
        self.assertEqual(entry["host"], "tile.openstreetmap.org")
        self.assertEqual(entry["url"],
                         "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        # Unknown provider keys fall back rather than accepting an arbitrary URL.
        db.set_setting(self.conn, "map_provider", "http://evil.example/{z}")
        self.assertEqual(tiles.provider(self.conn)["host"], "tile.openstreetmap.org")


class PolicyTest(TempCase):
    """The requirements the tile service states as mandatory."""

    def setUp(self) -> None:
        super().setUp()
        db.set_setting(self.conn, "external_consent", {"map": True})

    def test_user_agent_names_the_app_and_is_not_a_library_default(self) -> None:
        entry = tiles.provider(self.conn)
        agent = tiles.user_agent(entry)
        self.assertIn("FlightDesk", agent)
        for generic in ("python-urllib", "python-requests", "curl", "Go-http-client",
                        "okhttp", "Java/"):
            self.assertNotIn(generic, agent)

    def test_contact_is_carried_when_the_owner_supplies_one(self) -> None:
        db.set_setting(self.conn, "map_contact", "owner@example.org")
        agent = tiles.user_agent(tiles.provider(self.conn))
        self.assertIn("owner@example.org", agent)

    def test_tiles_are_cached_for_at_least_seven_days(self) -> None:
        self.assertGreaterEqual(tiles.MIN_TTL_S, 7 * 24 * 3600)

    def test_second_view_is_served_from_cache_without_a_request(self) -> None:
        with mock.patch.object(tiles.urllib.request, "urlopen",
                               return_value=_Resp()) as opener:
            first = tiles.fetch(self.conn, self.root, 14, 8000, 9000)
            second = tiles.fetch(self.conn, self.root, 14, 8000, 9000)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(opener.call_count, 1)

    def test_a_non_image_response_is_not_stored(self) -> None:
        with mock.patch.object(tiles.urllib.request, "urlopen",
                               return_value=_Resp(b"<html>", "text/html")):
            result = tiles.fetch(self.conn, self.root, 15, 100, 100)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "not_an_image")
        self.assertEqual(tiles.status(self.conn, self.root)["cached_tiles"], 0)

    def test_an_oversized_tile_is_refused(self) -> None:
        big = b"x" * (tiles.MAX_TILE_BYTES + 10)
        with mock.patch.object(tiles.urllib.request, "urlopen",
                               return_value=_Resp(big)):
            result = tiles.fetch(self.conn, self.root, 15, 101, 101)
        self.assertEqual(result["code"], "tile_too_large")

    def test_offline_is_reported_not_crashed(self) -> None:
        with mock.patch.object(tiles.urllib.request, "urlopen",
                               side_effect=OSError("no network")):
            result = tiles.fetch(self.conn, self.root, 15, 102, 102)
        self.assertEqual(result["code"], "offline")

    def test_rate_limiting_is_reported_as_such(self) -> None:
        import urllib.error

        error = urllib.error.HTTPError("u", 429, "slow down", {}, None)
        with mock.patch.object(tiles.urllib.request, "urlopen", side_effect=error):
            result = tiles.fetch(self.conn, self.root, 15, 103, 103)
        self.assertEqual(result["code"], "rate_limited")

    def test_cache_can_be_cleared(self) -> None:
        with mock.patch.object(tiles.urllib.request, "urlopen", return_value=_Resp()):
            tiles.fetch(self.conn, self.root, 16, 200, 200)
        self.assertEqual(tiles.status(self.conn, self.root)["cached_tiles"], 1)
        removed = tiles.clear_cache(self.root, tiles.provider(self.conn))
        self.assertEqual(removed, 1)
        self.assertEqual(tiles.status(self.conn, self.root)["cached_tiles"], 0)

    def test_status_exposes_the_attribution_the_ui_must_show(self) -> None:
        info = tiles.status(self.conn, self.root)
        self.assertIn("OpenStreetMap", info["attribution"])
        self.assertTrue(info["attribution_url"].startswith("https://"))
        self.assertTrue(info["policy_url"].startswith("https://"))
        self.assertIn("fixthemap", info["report_issue_url"])


class ProjectionTest(unittest.TestCase):
    """The axis order that puts a track on the right tile."""

    def test_mercator_axis_order(self) -> None:
        # Zoom 0 is one 256 px tile covering the whole world.
        x0, y0 = tiles.degrees_to_tile(0.0, -180.0, 0)
        x1, y1 = tiles.degrees_to_tile(0.0, 180.0, 0)
        self.assertAlmostEqual(x0, 0.0, places=6)
        self.assertAlmostEqual(x1, 256.0, places=6)
        self.assertAlmostEqual(y0, 128.0, places=6)  # equator is the vertical middle

    def test_north_is_up(self) -> None:
        _, north = tiles.degrees_to_tile(60.0, 0.0, 5)
        _, south = tiles.degrees_to_tile(-60.0, 0.0, 5)
        self.assertLess(north, south)

    def test_zoom_multiplies_the_world(self) -> None:
        x1, y1 = tiles.degrees_to_tile(45.0, 7.0, 6)
        x2, y2 = tiles.degrees_to_tile(45.0, 7.0, 7)
        self.assertAlmostEqual(x2, x1 * 2, places=6)
        self.assertAlmostEqual(y2, y1 * 2, places=6)

    def test_extreme_latitudes_are_clamped_not_infinite(self) -> None:
        _, y = tiles.degrees_to_tile(89.9, 0.0, 10)
        self.assertTrue(math.isfinite(y))


if __name__ == "__main__":
    unittest.main()
