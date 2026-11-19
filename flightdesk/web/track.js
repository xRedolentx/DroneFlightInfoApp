/* Offline flight track: local coordinate grid, gap-aware segments, cursor. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";

  // The tile layer is expensive to rebuild and pointless to rebuild on every
  // scrub tick, so identical views re-append the same nodes.
  var tileCache = { key: "", nodes: [] };

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  // Local planar approximation, metres, centred on the track.
  function project(lat, lon, lat0, lon0) {
    var x = (lon - lon0) * 111320 * Math.cos(lat0 * Math.PI / 180);
    var y = (lat - lat0) * 110540;
    return [x, y];
  }

  function bounds(segments, home) {
    var xs = [], ys = [];
    segments.forEach(function (seg) {
      seg.forEach(function (s) {
        if (s.lat === null || s.lon === null) return;
        xs.push(s.lat); ys.push(s.lon);
      });
    });
    if (home && home.lat !== null && home.lon !== null) {
      xs.push(home.lat); ys.push(home.lon);
    }
    if (!xs.length) return null;
    var minLat = Math.min.apply(null, xs), maxLat = Math.max.apply(null, xs);
    var minLon = Math.min.apply(null, ys), maxLon = Math.max.apply(null, ys);
    var padLat = Math.max((maxLat - minLat) * 0.12, 0.00012);
    var padLon = Math.max((maxLon - minLon) * 0.12, 0.00012);
    return {
      lat0: (minLat + maxLat) / 2, lon0: (minLon + maxLon) / 2,
      minLat: minLat - padLat, maxLat: maxLat + padLat,
      minLon: minLon - padLon, maxLon: maxLon + padLon,
    };
  }

  /** Nearest sample to a time value, within the same segment ordering. */
  function nearest(samples, t) {
    if (!samples || !samples.length) return null;
    var lo = 0, hi = samples.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if ((samples[mid].t || 0) < t) lo = mid + 1; else hi = mid;
    }
    var best = samples[lo];
    if (lo > 0) {
      var prev = samples[lo - 1];
      if (Math.abs((prev.t || 0) - t) <= Math.abs((best.t || 0) - t)) best = prev;
    }
    return best;
  }

  function render(container, spec) {
    container.innerHTML = "";
    var segments = (spec.segments || []).filter(function (s) { return s.length > 0; });
    var W = 900, H = spec.height || 320, pad = 26;
    // Match the viewBox to the panel's real box, otherwise the SVG letterboxes
    // and a basemap leaves a blank band inside the frame.
    var boxW = container.clientWidth || 0;
    var boxH = container.clientHeight || 0;
    if (boxW > 0 && boxH > 0) H = Math.max(200, Math.round(W * (boxH / boxW)));
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": spec.ariaLabel || "Flight track over a local coordinate grid" });

    var b = bounds(segments, spec.home);
    if (!b) {
      var note = document.createElement("div");
      note.className = "empty";
      note.style.padding = "70px 16px";
      note.innerHTML = "<h3>No GPS track in this record</h3>" +
        "<p class=\"small muted\">The parser read the log, but it contains no usable position fixes. " +
        "Nothing is interpolated to fill the gap.</p>";
      container.appendChild(note);
      return null;
    }

    var spanX = Math.max(1, (b.maxLon - b.minLon) * 111320 * Math.cos(b.lat0 * Math.PI / 180));
    var spanY = Math.max(1, (b.maxLat - b.minLat) * 110540);
    var scale = Math.min((W - pad * 2) / spanX, (H - pad * 2) / spanY);
    var cx = W / 2, cy = H / 2;

    var mapMode = !!(spec.basemap && spec.basemap.enabled && window.FDMap);
    var P;
    var zoomUsed = null;
    var gridCount = 0;

    if (mapMode) {
      // Real basemap: Web Mercator, tiles covering exactly this panel.
      var fitted = window.FDMap.fit(b, W, H, pad);
      zoomUsed = fitted.z;
      P = window.FDMap.projector(fitted.z, fitted.originX, fitted.originY);
      var key = window.FDMap.viewKey(fitted.z, fitted.originX, fitted.originY, W, H);
      if (tileCache.key === key && tileCache.nodes.length) {
        tileCache.nodes.forEach(function (node) { svg.appendChild(node); });
      } else {
        var nodes = [];
        window.FDMap.tilesFor(fitted.z, fitted.originX, fitted.originY, W, H)
          .forEach(function (t) {
            var node = window.FDMap.imageNode(t, spec.basemap.tileToken);
            svg.appendChild(node);
            nodes.push(node);
          });
        tileCache = { key: key, nodes: nodes };
      }
    } else {
      P = function (lat, lon) {
        var p = project(lat, lon, b.lat0, b.lon0);
        return [cx + p[0] * scale, cy - p[1] * scale];
      };

      // local metre grid
      var stepM = spanX > 4000 ? 1000 : spanX > 1500 ? 500 : spanX > 600 ? 200 : spanX > 200 ? 100 : 50;
      for (var gx = -Math.ceil(spanX / stepM) * stepM; gx <= spanX; gx += stepM) {
        var x = cx + gx * scale;
        if (x < pad || x > W - pad) continue;
        gridCount++;
        svg.appendChild(el("line", { x1: x, y1: pad, x2: x, y2: H - pad,
          stroke: "#e7eef1", "stroke-width": 1 }));
        var lbl = el("text", { x: x + 3, y: H - pad - 4, "font-size": 8.5, fill: "#9aa8b2" });
        lbl.textContent = gx === 0 ? "0" : Math.abs(gx) + "m";
        svg.appendChild(lbl);
      }
      for (var gy = -Math.ceil(spanY / stepM) * stepM; gy <= spanY; gy += stepM) {
        var y = cy - gy * scale;
        if (y < pad || y > H - pad) continue;
        gridCount++;
        svg.appendChild(el("line", { x1: pad, y1: y, x2: W - pad, y2: y,
          stroke: "#e7eef1", "stroke-width": 1 }));
        var lbl2 = el("text", { x: pad + 3, y: y - 3, "font-size": 8.5, fill: "#9aa8b2" });
        lbl2.textContent = gy === 0 ? "0" : Math.abs(gy) + "m";
        svg.appendChild(lbl2);
      }
      var scaleNote = el("text", { x: W - pad, y: H - 8, "text-anchor": "end",
        "font-size": 9, fill: "#8796a1" });
      scaleNote.textContent = "local grid · " + stepM + " m · north up · no tiles";
      svg.appendChild(scaleNote);
    }

    // path segments (never joined across a telemetry gap)
    segments.forEach(function (seg) {
      var d = "";
      seg.forEach(function (s, i) {
        if (s.lat === null || s.lon === null) return;
        var p = P(s.lat, s.lon);
        d += (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1);
      });
      if (!d) return;
      svg.appendChild(el("path", { d: d, fill: "none", stroke: "#0f8b8d",
        "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round",
        opacity: 0.95 }));
    });

    // gap markers
    (spec.gaps || []).forEach(function (gap) {
      var last = null, first = null;
      segments.forEach(function (seg) {
        seg.forEach(function (s) {
          if (s.t !== null && s.t <= gap.start) last = s;
          if (s.t !== null && s.t >= gap.end && !first) first = s;
        });
      });
      if (last && first) {
        var p1 = P(last.lat, last.lon), p2 = P(first.lat, first.lon);
        var mid = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
        svg.appendChild(el("line", { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1],
          stroke: "#b9761a", "stroke-width": 1.4, "stroke-dasharray": "3 4", opacity: 0.85 }));
        var dot = el("circle", { cx: mid[0], cy: mid[1], r: 4.5, fill: "#fff",
          stroke: "#b9761a", "stroke-width": 1.4 });
        dot.appendChild(el("title")).textContent = "Telemetry gap";
        svg.appendChild(dot);
      }
    });

    // home marker
    if (spec.home && spec.home.lat !== null && spec.home.lon !== null) {
      var hp = P(spec.home.lat, spec.home.lon);
      var home = el("circle", { cx: hp[0], cy: hp[1], r: 4.2, fill: "#fff",
        stroke: "#17262f", "stroke-width": 1.8 });
      home.appendChild(el("title")).textContent = "Home point";
      svg.appendChild(home);
    }

    // start / end
    var flat = [];
    segments.forEach(function (seg) { flat = flat.concat(seg); });
    if (flat.length) {
      var p0 = P(flat[0].lat, flat[0].lon);
      var pn = P(flat[flat.length - 1].lat, flat[flat.length - 1].lon);
      svg.appendChild(el("circle", { cx: p0[0], cy: p0[1], r: 3.6, fill: "#0f8b8d" }));
      var end = el("rect", { x: pn[0] - 3.4, y: pn[1] - 3.4, width: 6.8, height: 6.8,
        fill: "#17262f", rx: 1 });
      end.appendChild(el("title")).textContent = "End of record";
      svg.appendChild(end);
    }

    var cursor = el("circle", { cx: -20, cy: -20, r: 5, fill: "#fff", stroke: "#b23b3b",
      "stroke-width": 2, visibility: "hidden" });
    svg.appendChild(cursor);

    if (spec.cursorSample) {
      var cs = spec.cursorSample;
      if (cs.lat !== null && cs.lon !== null) {
        var cp = P(cs.lat, cs.lon);
        cursor.setAttribute("cx", cp[0]);
        cursor.setAttribute("cy", cp[1]);
        cursor.setAttribute("visibility", "visible");
      }
    }

    if (mapMode && spec.basemap) {
      // Attribution must be visible, not hidden behind a toggle or off-screen.
      var attrW = 210, attrH = 15;
      var attrX = W - attrW - 6, attrY = H - attrH - 5;
      svg.appendChild(el("rect", { x: attrX, y: attrY, width: attrW, height: attrH,
        rx: 3, fill: "#ffffff", opacity: "0.85" }));
      var link = el("a", { href: spec.basemap.attributionUrl || "#",
        target: "_blank", rel: "noopener noreferrer" });
      var attr = el("text", { x: W - 12, y: H - 9, "text-anchor": "end",
        "font-size": 9.5, fill: "#3d5a66" });
      attr.textContent = spec.basemap.attribution || "© OpenStreetMap contributors";
      link.appendChild(attr);
      svg.appendChild(link);
    }

    container.appendChild(svg);
    return { svg: svg, project: P, bounds: b, gridCount: gridCount,
             zoom: zoomUsed, tiles: mapMode ? svg.querySelectorAll("image").length : 0 };
  }

  window.FDTrack = { render: render, nearest: nearest, project: project };
})();
