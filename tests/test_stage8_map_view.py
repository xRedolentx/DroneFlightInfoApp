"""Pan and zoom behaviour for the optional basemap.

The map maths lives in the browser, so these checks run it under Node and
assert the handful of invariants a user can feel: the point under the cursor
stays put, dragging moves the map with the pointer, zooming cannot leave the
planet, and the tile service is never asked for more than the viewport.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_JS = ROOT / "flightdesk" / "web" / "map.js"


def _node() -> str | None:
    return shutil.which("node")


def _run(script: str) -> dict:
    node = _node()
    if not node:
        raise unittest.SkipTest("node not installed")
    preamble = (
        "const window = {};\n"
        f"globalThis.window = window;\n"
        "const fs = require('fs');\n"
        f"eval(fs.readFileSync({json.dumps(str(MAP_JS))}, 'utf8'));\n"
        "const FDMap = window.FDMap;\n"
    )
    proc = subprocess.run([node, "-e", preamble + script],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-800:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


BOUNDS = {
    "lat0": 45.5, "lon0": 12.3,
    "minLat": 45.49, "maxLat": 45.51,
    "minLon": 12.29, "maxLon": 12.31,
}


class FitTest(unittest.TestCase):
    def test_fit_keeps_the_track_inside_the_panel(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const v = FDMap.fit(b, 900, 400, 26);
          const p = FDMap.projector(v.z, v.originX, v.originY);
          const corners = [
            p(b.minLat, b.minLon), p(b.maxLat, b.maxLon),
            p(b.minLat, b.maxLon), p(b.maxLat, b.minLon),
          ];
          console.log(JSON.stringify({{
            z: v.z,
            inside: corners.every(c => c[0] >= 0 && c[0] <= 900 && c[1] >= 0 && c[1] <= 400),
            xs: corners.map(c => c[0]), ys: corners.map(c => c[1]),
          }}));
        """)
        self.assertTrue(out["inside"], out)
        self.assertGreater(out["z"], 0)

    def test_fit_is_zoom_limited_for_a_huge_track(self) -> None:
        # A continent-sized track must not ask for a zoom beyond the provider's.
        out = _run("""
          const b = {lat0: 0, lon0: 0, minLat: -60, maxLat: 60, minLon: -170, maxLon: 170};
          const v = FDMap.fit(b, 900, 400, 26);
          console.log(JSON.stringify({z: v.z, max: FDMap.MAX_ZOOM}));
        """)
        self.assertLessEqual(out["z"], out["max"])


class ZoomTest(unittest.TestCase):
    def test_zoom_keeps_the_point_under_the_pointer_still(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const W = 900, H = 400, pad = 26;
          const start = FDMap.fit(b, W, H, pad);
          const atX = 300, atY = 150;
          const before = [start.originX + atX, start.originY + atY];
          const after = FDMap.zoomAt(start, 1, atX, atY, b, W, H, pad);
          const factor = Math.pow(2, after.z - start.z);
          // The same map point must land back under the same screen point.
          const newScreenX = before[0] * factor - after.originX;
          const newScreenY = before[1] * factor - after.originY;
          console.log(JSON.stringify({{
            dz: after.z - start.z,
            dx: Math.abs(newScreenX - atX),
            dy: Math.abs(newScreenY - atY),
          }}));
        """)
        self.assertEqual(out["dz"], 1)
        self.assertLess(out["dx"], 0.001, out)
        self.assertLess(out["dy"], 0.001, out)

    def test_zoom_stops_at_the_provider_limit(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          let v = FDMap.fit(b, 900, 400, 26);
          for (let i = 0; i < 40; i += 1) {{
            v = FDMap.zoomAt(v, 1, 450, 200, b, 900, 400, 26);
          }}
          const high = v.z;
          for (let i = 0; i < 60; i += 1) {{
            v = FDMap.zoomAt(v, -1, 450, 200, b, 900, 400, 26);
          }}
          console.log(JSON.stringify({{high: high, low: v.z, max: FDMap.MAX_ZOOM}}));
        """)
        self.assertEqual(out["high"], out["max"])
        self.assertEqual(out["low"], 0)

    def test_zooming_out_never_shows_the_edge_of_the_world(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          let v = FDMap.zoomAt(FDMap.fit(b, 900, 400, 26), -6, 450, 200, b, 900, 400, 26);
          console.log(JSON.stringify({{cy: v.centerY, ok: v.centerY > 0}}));
        """)
        self.assertTrue(out["ok"])


class PanTest(unittest.TestCase):
    def test_drag_moves_the_map_with_the_pointer(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const W = 900, H = 400, pad = 26;
          const start = FDMap.fit(b, W, H, pad);
          const moved = FDMap.panBy(start, 100, 0, b, W, H, pad);
          // Dragging right must reveal ground to the left: origin decreases.
          console.log(JSON.stringify({{
            dx: moved.originX - start.originX,
            dz: moved.z - start.z,
          }}));
        """)
        self.assertLess(out["dx"], 0)
        self.assertEqual(out["dz"], 0)  # panning must not change the zoom

    def test_pan_cannot_leave_the_world(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          let v = FDMap.fit(b, 900, 400, 26);
          for (let i = 0; i < 200; i += 1) {{
            v = FDMap.panBy(v, -5000, -5000, b, 900, 400, 26);
          }}
          const span = FDMap.TILE * Math.pow(2, v.z);
          console.log(JSON.stringify({{
            cx: v.centerX, cy: v.centerY, span: span,
            inX: v.centerX >= 0 && v.centerX <= span,
            inY: v.centerY >= 0 && v.centerY <= span,
          }}));
        """)
        self.assertTrue(out["inX"], out)
        self.assertTrue(out["inY"], out)

    def test_a_missing_view_falls_back_to_the_fit(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const a = FDMap.normaliseView(null, b, 900, 400, 26);
          const c = FDMap.normaliseView({{z: 'x'}}, b, 900, 400, 26);
          const fit = FDMap.fit(b, 900, 400, 26);
          console.log(JSON.stringify({{
            za: a.z, zc: c.z, zf: fit.z,
            sameX: Math.abs(a.originX - fit.originX) < 0.001,
          }}));
        """)
        self.assertEqual(out["za"], out["zf"])
        self.assertEqual(out["zc"], out["zf"])
        self.assertTrue(out["sameX"])


class TileCoverageTest(unittest.TestCase):
    def test_only_the_visible_viewport_is_requested(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const W = 900, H = 400, pad = 26;
          const v = FDMap.fit(b, W, H, pad);
          const tiles = FDMap.tilesFor(v.z, v.originX, v.originY, W, H);
          const neededCols = Math.ceil((W + FDMap.TILE) / FDMap.TILE);
          const neededRows = Math.ceil((H + FDMap.TILE) / FDMap.TILE);
          const inView = tiles.every(t =>
            t.px > -FDMap.TILE && t.px < W && t.py > -FDMap.TILE && t.py < H);
          console.log(JSON.stringify({{
            count: tiles.length,
            inView: inView,
            ceiling: neededCols * neededRows,
            maxTile: Math.max(...tiles.map(t => t.x)),
            span: Math.pow(2, v.z),
          }}));
        """)
        self.assertTrue(out["inView"], out)
        self.assertLessEqual(out["count"], out["ceiling"])
        self.assertLess(out["maxTile"], out["span"])

    def test_zoomed_in_asks_for_fewer_tiles_than_zoomed_out(self) -> None:
        out = _run(f"""
          const b = {json.dumps(BOUNDS)};
          const W = 900, H = 400, pad = 26;
          let v = FDMap.fit(b, W, H, pad);
          const inCount = FDMap.tilesFor(v.z, v.originX, v.originY, W, H).length;
          v = FDMap.zoomAt(v, -3, W/2, H/2, b, W, H, pad);
          const outCount = FDMap.tilesFor(v.z, v.originX, v.originY, W, H).length;
          console.log(JSON.stringify({{inCount: inCount, outCount: outCount}}));
        """)
        self.assertLessEqual(out["inCount"], out["outCount"])


if __name__ == "__main__":
    unittest.main()
