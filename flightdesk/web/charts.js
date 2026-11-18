/* Local SVG charts. No external libraries, no network. */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        node.setAttribute(k, attrs[k]);
      });
    }
    return node;
  }

  function extent(values) {
    var lo = null, hi = null;
    values.forEach(function (v) {
      if (v === null || v === undefined || !isFinite(v)) return;
      if (lo === null || v < lo) lo = v;
      if (hi === null || v > hi) hi = v;
    });
    if (lo === null) return [0, 1];
    if (lo === hi) return [lo - 1, hi + 1];
    return [lo, hi];
  }

  function fmt(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var d = digits === undefined ? 0 : digits;
    return Number(v).toFixed(d);
  }

  /**
   * Draw a line chart with explicit gaps.
   * spec: {series:[{key,label,color,values:[num|null],unit,digits}], times:[], xDomain:[a,b],
   *        missing:[{start,end}], cursor:number|null, height:number}
   */
  function line(container, spec) {
    container.innerHTML = "";
    var times = spec.times || [];
    var series = spec.series || [];
    if (!times.length || !series.length) {
      var empty = document.createElement("div");
      empty.className = "empty small";
      empty.textContent = spec.emptyText || "No samples for this metric.";
      container.appendChild(empty);
      return null;
    }
    var W = 900, H = spec.height || 148, padL = 46, padR = 10, padT = 12, padB = 20;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": spec.ariaLabel || "Telemetry chart" });
    var xd = spec.xDomain || extent(times);
    if (xd[0] === xd[1]) { xd = [xd[0], xd[0] + 1]; }

    var all = [];
    series.forEach(function (s) { all = all.concat(s.values); });
    var yd = spec.yDomain || extent(all);
    if (yd[0] === yd[1]) { yd = [yd[0] - 1, yd[1] + 1]; }
    var span = yd[1] - yd[0];
    yd = [yd[0] - span * 0.08, yd[1] + span * 0.12];

    function X(t) { return padL + (W - padL - padR) * ((t - xd[0]) / (xd[1] - xd[0])); }
    function Y(v) { return padT + (H - padT - padB) * (1 - (v - yd[0]) / (yd[1] - yd[0])); }

    // missing intervals
    (spec.missing || []).forEach(function (gap) {
      var x1 = Math.max(padL, X(gap.start));
      var x2 = Math.min(W - padR, X(gap.end));
      if (x2 <= x1) return;
      svg.appendChild(el("rect", { x: x1, y: padT, width: x2 - x1,
        height: H - padT - padB, class: "chart-missing", opacity: 0.7 }));
    });

    // horizontal grid + labels
    for (var i = 0; i <= 3; i++) {
      var v = yd[0] + (yd[1] - yd[0]) * (i / 3);
      var y = Y(v);
      svg.appendChild(el("line", { x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: "#e6ecef", "stroke-width": 1 }));
      var label = el("text", { x: padL - 6, y: y + 3, "text-anchor": "end",
        "font-size": 9.5, fill: "#8796a1" });
      label.textContent = fmt(v, Math.abs(v) < 10 ? 1 : 0);
      svg.appendChild(label);
    }

    series.forEach(function (s) {
      var d = "";
      var open = false;
      for (var idx = 0; idx < times.length; idx++) {
        var v = s.values[idx];
        if (v === null || v === undefined || !isFinite(v)) { open = false; continue; }
        var px = X(times[idx]), py = Y(v);
        d += (open ? "L" : "M") + px.toFixed(2) + " " + py.toFixed(2);
        open = true;
      }
      if (!d) return;
      svg.appendChild(el("path", { d: d, fill: "none", stroke: s.color,
        "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    });

    // x axis labels
    [0, 0.5, 1].forEach(function (frac) {
      var t = xd[0] + (xd[1] - xd[0]) * frac;
      var text = el("text", { x: X(t), y: H - 5,
        "text-anchor": frac === 0 ? "start" : (frac === 1 ? "end" : "middle"),
        "font-size": 9.5, fill: "#8796a1" });
      text.textContent = spec.xFormat ? spec.xFormat(t) : fmt(t, 0) + "s";
      svg.appendChild(text);
    });

    var cursorLine = el("line", { x1: 0, y1: padT, x2: 0, y2: H - padB,
      stroke: "#0f8b8d", "stroke-width": 1, opacity: 0.85, visibility: "hidden" });
    svg.appendChild(cursorLine);

    if (spec.cursor !== null && spec.cursor !== undefined && isFinite(spec.cursor)) {
      cursorLine.setAttribute("x1", X(spec.cursor));
      cursorLine.setAttribute("x2", X(spec.cursor));
      cursorLine.setAttribute("visibility", "visible");
    }

    svg.addEventListener("pointerdown", function (ev) {
      if (!spec.onSeek) return;
      var rect = svg.getBoundingClientRect();
      var frac = (ev.clientX - rect.left) / rect.width;
      var px = padL + (W - padL - padR) * Math.min(1, Math.max(0, frac));
      var ratio = (px - padL) / (W - padL - padR);
      spec.onSeek(xd[0] + ratio * (xd[1] - xd[0]));
    });

    container.appendChild(svg);
    return { svg: svg, X: X, Y: Y, xDomain: xd };
  }

  /** Compact sparkline for battery health trends. */
  function spark(container, values, color) {
    container.innerHTML = "";
    var W = 120, H = 26;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H });
    var clean = values.filter(function (v) { return v !== null && isFinite(v); });
    if (clean.length < 2) {
      var t = el("text", { x: 0, y: 16, "font-size": 9, fill: "#8796a1" });
      t.textContent = "insufficient readings";
      svg.appendChild(t);
      container.appendChild(svg);
      return;
    }
    var lo = Math.min.apply(null, clean), hi = Math.max.apply(null, clean);
    if (lo === hi) { lo -= 1; hi += 1; }
    var d = "";
    var open = false;
    values.forEach(function (v, i) {
      if (v === null || !isFinite(v)) { open = false; return; }
      var x = (i / Math.max(1, values.length - 1)) * W;
      var y = H - 3 - ((v - lo) / (hi - lo)) * (H - 6);
      d += (open ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      open = true;
    });
    svg.appendChild(el("path", { d: d, fill: "none", stroke: color || "#0f8b8d",
      "stroke-width": 1.4 }));
    container.appendChild(svg);
  }

  window.FDCharts = { line: line, spark: spark, extent: extent };
})();
