/* Web Mercator tile maths for the optional online basemap.
 *
 * Tiles are fetched from the local server (`/api/tiles/...`), never from a
 * third-party host: the page keeps `img-src 'self'` and `no-referrer`, and the
 * server holds the consent gate, the User-Agent the tile policy asks for, and
 * the on-disk cache. Nothing here decides whether tiles may be used.
 */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var TILE = 256;
  var MAX_LAT = 85.05112878;
  var MAX_ZOOM = 19;

  function clampLat(lat) {
    return Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
  }

  /** Web Mercator pixel position at a zoom level, origin at the top-left. */
  function pixel(lat, lon, z) {
    var scale = TILE * Math.pow(2, z);
    var x = ((lon + 180) / 360) * scale;
    var r = (clampLat(lat) * Math.PI) / 180;
    var y = ((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * scale;
    return [x, y];
  }

  function extent(b, z) {
    var a = pixel(b.minLat, b.minLon, z);
    var c = pixel(b.maxLat, b.maxLon, z);
    return { x: Math.abs(c[0] - a[0]), y: Math.abs(a[1] - c[1]) };
  }

  /**
   * Highest zoom at which the whole track fits the panel, plus the pixel origin
   * of the panel's top-left corner. Integer zoom only: tiles exist per level.
   */
  function fit(b, W, H, pad) {
    var availW = Math.max(1, W - pad * 2);
    var availH = Math.max(1, H - pad * 2);
    var z = 0;
    for (var t = 1; t <= MAX_ZOOM; t += 1) {
      var e = extent(b, t);
      if (e.x <= availW && e.y <= availH) z = t;
      else break;
    }
    var mid = pixel(b.lat0, b.lon0, z);
    return { z: z, originX: mid[0] - W / 2, originY: mid[1] - H / 2 };
  }

  /** Latitude/longitude to panel pixels for a fitted view. */
  function projector(z, originX, originY) {
    return function (lat, lon) {
      var p = pixel(lat, lon, z);
      return [p[0] - originX, p[1] - originY];
    };
  }

  /** The inverse, so a drag can be expressed in the same units. */
  function unprojector(z, originX, originY) {
    return function (px, py) {
      var scale = TILE * Math.pow(2, z);
      var x = px + originX;
      var y = py + originY;
      var lon = (x / scale) * 360 - 180;
      var n = Math.PI - (2 * Math.PI * y) / scale;
      var lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
      return [lat, lon];
    };
  }

  /**
   * Clamp a view to the world and to the provider's zoom range.
   * Keeping the centre inside the valid latitude band stops a drag from
   * scrolling the map into empty space above or below the planet.
   */
  function normaliseView(view, b, W, H, pad) {
    var base = fit(b, W, H, pad);
    if (!view || typeof view.z !== "number" || !isFinite(view.z)) return base;
    var z = Math.max(0, Math.min(MAX_ZOOM, Math.round(view.z)));
    var span = TILE * Math.pow(2, z);
    var cx = Number(view.centerX), cy = Number(view.centerY);
    if (!isFinite(cx)) cx = base.originX + W / 2;
    if (!isFinite(cy)) cy = base.originY + H / 2;
    cx = Math.max(0, Math.min(span, cx));
    // Pixel y of the mercator limit at this zoom, so the view cannot leave the map.
    var top = pixel(MAX_LAT, -180, z)[1];
    var bottom = pixel(-MAX_LAT, -180, z)[1];
    cy = Math.max(top, Math.min(bottom, cy));
    return { z: z, originX: cx - W / 2, originY: cy - H / 2, centerX: cx, centerY: cy };
  }

  /**
   * One pinch/wheel/cursor step, keeping the point under the pointer still.
   * Integer zoom only, because tiles exist per level.
   */
  function zoomAt(view, delta, atX, atY, b, W, H, pad) {
    var current = normaliseView(view, b, W, H, pad);
    var z = Math.max(0, Math.min(MAX_ZOOM, current.z + delta));
    if (z === current.z) return current;
    // Keep the map coordinate under the pointer at the same screen point.
    var anchorX = current.originX + atX;
    var anchorY = current.originY + atY;
    var factor = Math.pow(2, z - current.z);
    var cx = anchorX * factor - atX + W / 2;
    var cy = anchorY * factor - atY + H / 2;
    return normaliseView({ z: z, centerX: cx, centerY: cy }, b, W, H, pad);
  }

  /** Drag in screen pixels. */
  function panBy(view, dx, dy, b, W, H, pad) {
    var current = normaliseView(view, b, W, H, pad);
    return normaliseView(
      { z: current.z, centerX: current.centerX - dx, centerY: current.centerY - dy },
      b, W, H, pad);
  }

  /**
   * Tiles covering exactly the visible panel. No prefetch, no look-ahead beyond
   * the viewport: the tile policy forbids fetching tiles a user is not viewing.
   */
  function tilesFor(z, originX, originY, W, H) {
    var span = Math.pow(2, z);
    var x0 = Math.floor(originX / TILE), x1 = Math.floor((originX + W - 1) / TILE);
    var y0 = Math.floor(originY / TILE), y1 = Math.floor((originY + H - 1) / TILE);
    var out = [];
    for (var tx = x0; tx <= x1; tx += 1) {
      for (var ty = y0; ty <= y1; ty += 1) {
        if (ty < 0 || ty >= span) continue;
        // Wrap longitudes around the antimeridian; latitude does not wrap.
        var wx = ((tx % span) + span) % span;
        out.push({
          z: z, x: wx, y: ty,
          px: tx * TILE - originX,
          py: ty * TILE - originY,
        });
      }
    }
    return out;
  }

  function imageNode(tile, token) {
    var img = document.createElementNS(NS, "image");
    img.setAttribute("href", "/api/tiles/" + token + "/" + tile.z + "/" + tile.x +
      "/" + tile.y + ".png");
    img.setAttribute("x", String(tile.px));
    img.setAttribute("y", String(tile.py));
    img.setAttribute("width", String(TILE));
    img.setAttribute("height", String(TILE));
    img.setAttribute("preserveAspectRatio", "none");
    return img;
  }

  /** One immutable signature for a tiled view, used to avoid rebuilding on scrub. */
  function viewKey(z, originX, originY, W, H) {
    return [z, Math.round(originX), Math.round(originY), W, H].join("/");
  }

  window.FDMap = {
    TILE: TILE,
    MAX_ZOOM: MAX_ZOOM,
    MAX_LAT: MAX_LAT,
    pixel: pixel,
    extent: extent,
    fit: fit,
    projector: projector,
    unprojector: unprojector,
    normaliseView: normaliseView,
    zoomAt: zoomAt,
    panBy: panBy,
    tilesFor: tilesFor,
    imageNode: imageNode,
    viewKey: viewKey,
  };
})();
