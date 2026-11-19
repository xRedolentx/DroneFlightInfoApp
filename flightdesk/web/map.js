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
    pixel: pixel,
    extent: extent,
    fit: fit,
    projector: projector,
    tilesFor: tilesFor,
    imageNode: imageNode,
    viewKey: viewKey,
  };
})();
