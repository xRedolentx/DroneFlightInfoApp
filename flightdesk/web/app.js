/* Flight Desk local UI. No network beyond this loopback origin. */
(function () {
  "use strict";

  var TOKEN = (document.querySelector('meta[name="fd-token"]') || {}).content || "";
  var view = document.getElementById("view");
  var toastEl = document.getElementById("toast");
  var modalEl = document.getElementById("modal");
  var state = { app: null, filters: {}, flight: null, section: "overview", cursor: null,
                playing: false, metric: "power", mobileView: "list",
                lastImport: null, lastImportStatus: "", lastImportTone: "" };

  // ------------------------------------------------------------------ utils

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function isMetric() { return (state.app && state.app.unit_preference) !== "imperial"; }

  function fmtDuration(seconds, forceSeconds) {
    if (seconds === null || seconds === undefined || !isFinite(seconds)) return "—";
    var s = Math.round(Number(seconds));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return h + "h " + String(m).padStart(2, "0") + "m";
    if (m > 0) return m + "m " + String(sec).padStart(2, "0") + "s";
    return sec + "s";
  }

  function fmtDistance(m, digits) {
    if (m === null || m === undefined || !isFinite(m)) return "—";
    if (isMetric()) {
      return m >= 1000 ? (m / 1000).toFixed(digits === undefined ? 2 : digits) + " km"
                       : Math.round(m) + " m";
    }
    var ft = m * 3.28084;
    return ft >= 5280 ? (ft / 5280).toFixed(2) + " mi" : Math.round(ft) + " ft";
  }

  function distUnit(m, digits) {
    if (m === null || m === undefined) return { v: "—", u: "" };
    if (isMetric()) return m >= 1000
      ? { v: (m / 1000).toFixed(2), u: "km" } : { v: Math.round(m), u: "m" };
    var ft = m * 3.28084;
    return ft >= 5280 ? { v: (ft / 5280).toFixed(2), u: "mi" } : { v: Math.round(ft), u: "ft" };
  }

  function altUnit(m) {
    if (m === null || m === undefined || !isFinite(m)) return { v: "—", u: "" };
    return isMetric() ? { v: Math.round(m), u: "m" } : { v: Math.round(m * 3.28084), u: "ft" };
  }

  function speedUnit(mps) {
    if (mps === null || mps === undefined || !isFinite(mps)) return { v: "—", u: "" };
    return isMetric() ? { v: (mps * 3.6).toFixed(1), u: "km/h" }
                      : { v: (mps * 2.23694).toFixed(1), u: "mph" };
  }

  function tempUnit(c) {
    if (c === null || c === undefined || !isFinite(c)) return { v: "—", u: "" };
    return isMetric() ? { v: c.toFixed(1), u: "°C" }
                      : { v: (c * 9 / 5 + 32).toFixed(1), u: "°F" };
  }

  function fmtDate(iso) {
    if (!iso) return "Unknown date";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var opts = { year: "numeric", month: "short", day: "2-digit" };
    var date = d.toLocaleDateString(undefined, opts);
    var time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    var off = -d.getTimezoneOffset();
    var sign = off >= 0 ? "+" : "-";
    var oh = String(Math.floor(Math.abs(off) / 60)).padStart(2, "0");
    var om = String(Math.abs(off) % 60).padStart(2, "0");
    return date + " · " + time + " (UTC" + sign + oh + ":" + om + ")";
  }

  function dateOnly(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "2-digit" });
  }

  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toastEl.classList.remove("on"); }, 3400);
  }

  // Downloads need the session header, which a plain link cannot send. Fetch
  // the bytes with the header, then hand the browser a local blob URL.
  function download(path, filename) {
    return fetch(path, { headers: { "X-FD-Token": TOKEN }, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 2000);
        toast("Saved " + filename);
      })
      .catch(function (err) { toast("Export failed: " + err.message); });
  }

  function exportButton(label, path, filename) {
    return '<button type="button" class="secondary" data-export="' + esc(path) +
      '" data-filename="' + esc(filename) + '">' + esc(label) + "</button>";
  }

  function wireExports(root) {
    root.querySelectorAll("[data-export]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        download(btn.dataset.export, btn.dataset.filename);
      });
    });
  }

  // -------------------------------------------------------------------- api

  function api(path, options) {
    options = options || {};
    var headers = { "X-FD-Token": TOKEN };
    if (options.headers) Object.keys(options.headers).forEach(function (k) {
      headers[k] = options.headers[k];
    });
    var init = { method: options.method || "GET", headers: headers,
                 credentials: "same-origin" };
    if (options.body !== undefined) {
      if (options.raw) { init.body = options.body; }
      else { headers["Content-Type"] = "application/json";
             init.body = JSON.stringify(options.body); }
    }
    return fetch(path, init).then(function (response) {
      var ct = response.headers.get("Content-Type") || "";
      if (ct.indexOf("application/json") >= 0) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.code || ("HTTP " + response.status));
          return data;
        });
      }
      return response.text().then(function (text) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return text;
      });
    });
  }

  function refreshState() {
    return api("/api/state").then(function (data) {
      state.app = data;
      applyChrome();
      return data;
    });
  }

  function applyChrome() {
    var units = isMetric() ? "metric" : "imperial";
    document.body.dataset.units = units;
    var unitBtn = document.getElementById("unit-toggle");
    if (unitBtn) unitBtn.textContent = isMetric() ? "Metric" : "Imperial";
    var privacyBtn = document.getElementById("privacy-toggle");
    var on = !!(state.app && state.app.privacy);
    document.body.dataset.privacy = on ? "on" : "off";
    if (privacyBtn) privacyBtn.setAttribute("aria-pressed", on ? "true" : "false");
    var mode = document.querySelector('[data-testid="data-mode"]');
    if (mode && state.app) {
      var real = state.app.real_inputs_selected;
      mode.textContent = real ? "your records" : "synthetic";
      mode.classList.toggle("real", real);
    }
    document.body.dataset.mobileView = state.mobileView;
  }

  function setNav(section) {
    document.querySelectorAll(".mainnav a").forEach(function (a) {
      var active = a.dataset.nav === section;
      if (active) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  // ---------------------------------------------------------------- widgets

  function tile(label, value, unit, sub) {
    return '<div class="tile"><div class="tile-label">' + esc(label) + '</div>' +
      '<div class="tile-value">' + esc(value) +
      (unit ? '<span class="tile-unit">' + esc(unit) + "</span>" : "") + "</div>" +
      (sub ? '<div class="tile-sub">' + esc(sub) + "</div>" : "") + "</div>";
  }

  function statusBadge(status) {
    var labels = { due: "Due", due_soon: "Due soon", ok: "OK", unknown: "Unknown" };
    return '<span class="badge ' + status + '">' + (labels[status] || status) + "</span>";
  }

  function counterBlock(counter) {
    var unit = counter.metric === "flights" ? "flights"
      : counter.metric === "minutes" ? "air minutes" : "charges";
    if (counter.value === null || counter.value === undefined) {
      return '<div class="counter unknown"><div class="counter-top">' +
        '<span class="label">' + esc(unit) + '</span>' +
        '<span class="value">unknown</span></div>' +
        '<div class="bar"><span style="width:0"></span></div>' +
        '<div class="tiny faint">' +
        (counter.missing_duration ? "duration missing on some flights" : "counter unavailable") +
        "</div></div>";
    }
    var ratio = counter.ratio === null ? 0 : Math.min(1, counter.ratio);
    var over = counter.limit !== null && counter.value > counter.limit;
    var valueText = (counter.metric === "minutes")
      ? Math.round(counter.value) + " / " + Math.round(counter.limit) + " min"
      : Math.round(counter.value) + " / " + Math.round(counter.limit) + " " + unit;
    if (counter.limit === null) valueText = Math.round(counter.value) + " " + unit;
    return '<div class="counter ' + counter.status + (over ? " over" : "") + '">' +
      '<div class="counter-top"><span class="label">' + esc(unit) + " · " +
      esc(counter.status.replace("_", " ")) + "</span>" +
      '<span class="value">' + esc(valueText) + "</span></div>" +
      '<div class="bar"><span style="width:' + (ratio * 100).toFixed(1) + '%"></span></div>' +
      (counter.rounded ? '<div class="tiny faint">as captured (rounded)</div>' : "") +
      (counter.partial ? '<div class="tiny faint">partial: some flights have no duration</div>' : "") +
      (over ? '<div class="tiny faint">over limit by ' +
        Math.round(counter.value - counter.limit) + "</div>" : "") +
      "</div>";
  }

  function ruleCard(status, options) {
    options = options || {};
    var counters = status.counters.map(counterBlock).join("");
    var baseline = status.baseline || {};
    var last = status.last_service;
    var explain = status.explanation || "";
    var captured = status.captured_status
      ? '<div class="tiny faint">captured: ' + esc(status.captured_status) + "</div>" : "";
    var mismatch = status.captured_status_normalized && status.matches_capture === false
      ? '<div class="tiny" style="color:var(--amber)">differs from the capture</div>' : "";
    var actions = options.readonly ? "" :
      '<div class="row" style="margin-top:8px">' +
      '<button type="button" class="secondary" data-act="complete" data-rule="' + status.id +
      '" data-equipment="' + status.equipment_id + '">Complete</button>' +
      '<button type="button" class="secondary" data-act="skip" data-rule="' + status.id +
      '" data-equipment="' + status.equipment_id + '">Skip</button>' +
      '<button type="button" class="link" data-act="rule" data-rule="' + status.id + '">Rule</button>' +
      "</div>";
    return '<div class="card-sq" data-rule-card="' + status.id + '">' +
      '<div class="rule-line"><h3>' + esc(status.equipment_name) + " · " +
      esc(status.label) + "</h3>" + statusBadge(status.status) + "</div>" +
      '<div class="small muted" style="margin:3px 0 6px">' + esc(explain) + "</div>" +
      counters +
      (last ? '<div class="tiny faint" style="margin-top:6px">Last service: ' +
        esc(last.kind) + (last.at ? " · " + esc(dateOnly(last.at)) : "") +
        (last.notes ? " · " + esc(last.notes) : "") + "</div>" : "") +
      '<div class="tiny faint">Baseline: ' + esc(baseline.basis || "none") +
      (baseline.at ? " · " + esc(dateOnly(baseline.at)) : "") +
      (status.covered_flights ? " · covers " + status.covered_flights + " archived flights" : "") +
      "</div>" +
      captured + mismatch +
      (status.uncertain_count ? '<div class="notice warn tiny" style="margin-top:6px">' +
        status.uncertain_count + " flight(s) are excluded from this counter until coverage is " +
        'resolved. <button type="button" class="link" data-act="coverage" data-rule="' +
        status.id + '">Review</button></div>' : "") +
      actions + "</div>";
  }

  // ----------------------------------------------------------------- router

  function parseHash() {
    var raw = (location.hash || "#/flights").replace(/^#/, "");
    var parts = raw.split("/").filter(function (p) { return p.length; });
    return { parts: parts, raw: raw };
  }

  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  function render() {
    var r = parseHash();
    var head = r.parts[0] || "flights";
    setNav(head === "flights" ? "flights" : head);
    if (head === "flights") {
      if (r.parts[1]) return renderFlightDetail(parseInt(r.parts[1], 10),
                                                r.parts[2] || "overview");
      return renderFlights();
    }
    if (head === "equipment") {
      if (r.parts[1] === "battery" || r.parts[1] === "aircraft") {
        return renderEquipmentList(r.parts[1], r.parts[2] ? parseInt(r.parts[2], 10) : null);
      }
      return renderEquipmentList("battery", null);
    }
    if (head === "maintenance") return renderMaintenance(r.parts[1] || "upcoming");
    if (head === "import") return renderImport();
    if (head === "settings") return renderSettings();
    if (head === "compare") return renderCompare();
    return renderFlights();
  }

  // ------------------------------------------------------------ flights view

  function loadFilters() {
    try {
      var saved = localStorage.getItem("fd.filters");
      if (saved) return JSON.parse(saved);
    } catch (e) { /* ignore */ }
    return (state.app && state.app.filters) || {};
  }

  function saveFilters(filters) {
    try { localStorage.setItem("fd.filters", JSON.stringify(filters)); } catch (e) { /* ignore */ }
    api("/api/settings", { method: "POST", body: { last_filters: filters } }).catch(function () {});
  }

  function queryFromFilters(filters) {
    var params = new URLSearchParams();
    Object.keys(filters).forEach(function (key) {
      var value = filters[key];
      if (value !== "" && value !== null && value !== undefined) params.set(key, value);
    });
    return params.toString();
  }

  function renderFlights() {
    var filters = state.filters && Object.keys(state.filters).length ? state.filters : loadFilters();
    state.filters = filters;
    view.innerHTML = '<div class="privacy-banner">Privacy display is on — serials, locations and ' +
      "route geometry are hidden.</div>" +
      '<div class="mobile-switch"><button type="button" class="secondary" data-mobile="list">List</button>' +
      '<button type="button" class="secondary" data-mobile="detail">Record</button></div>' +
      '<div class="workspace">' +
      '<section class="panel pane-list" aria-label="Flight list"><div class="panel-head">' +
      "<h2>Flights</h2><span class=\"tiny faint\" id=\"list-count\"></span></div>" +
      '<ul class="flight-list" id="flight-list"></ul></section>' +
      '<section class="pane-detail" aria-label="Flight workspace" id="flight-workspace"></section>' +
      "</div>";
    var ws = document.getElementById("flight-workspace");
    ws.innerHTML = '<div class="panel"><div class="filters" id="filters"></div>' +
      '<div id="tiles" class="tiles" style="padding:14px 14px 0;margin:0"></div>' +
      '<div id="records" class="records"></div></div>';
    renderFiltersInto(document.getElementById("filters"), filters);
    loadFlightData(filters);
    bindAct(view);
  }

  function renderFiltersInto(target, filters) {
    api("/api/flights?" + queryFromFilters(filters)).then(function (data) {
      var facets = data.facets;
      var aircraftOptions = ['<option value="">All aircraft</option>'].concat(
        facets.aircraft.map(function (a) {
          return '<option value="' + a.id + '"' +
            (String(filters.aircraft) === String(a.id) ? " selected" : "") + ">" +
            esc(a.name) + (a.archived ? " (archived)" : "") + "</option>";
        })).join("");
      var batteryOptions = ['<option value="">All batteries</option>'].concat(
        facets.batteries.map(function (b) {
          return '<option value="' + b.id + '"' +
            (String(filters.battery) === String(b.id) ? " selected" : "") + ">" +
            esc(b.name) + "</option>";
        })).join("");
      var pilotOptions = ['<option value="">All pilots</option>'].concat(
        facets.pilots.map(function (p) {
          return '<option value="' + esc(p) + '"' +
            (filters.pilot === p ? " selected" : "") + ">" + esc(p) + "</option>";
        })).join("");
      var tagOptions = ['<option value="">All tags</option>'].concat(
        facets.tags.map(function (t) {
          return '<option value="' + esc(t) + '"' +
            (filters.tag === t ? " selected" : "") + ">" + esc(t) + "</option>";
        })).join("");
      target.innerHTML =
        '<div class="field" style="grid-column:span 2"><label for="f-search">Search</label>' +
        '<input id="f-search" type="search" placeholder="Aircraft, pilot, note, tag" value="' +
        esc(filters.search || "") + '"></div>' +
        '<div class="field"><label for="f-from">From</label><input id="f-from" type="date" value="' +
        esc(filters.from || "") + '"></div>' +
        '<div class="field"><label for="f-to">To</label><input id="f-to" type="date" value="' +
        esc(filters.to || "") + '"></div>' +
        '<div class="field"><label for="f-aircraft">Drone</label><select id="f-aircraft">' +
        aircraftOptions + "</select></div>" +
        '<div class="field"><label for="f-battery">Battery</label><select id="f-battery">' +
        batteryOptions + "</select></div>" +
        '<div class="field"><label for="f-pilot">Pilot</label><select id="f-pilot">' +
        pilotOptions + "</select></div>" +
        '<div class="field"><label for="f-tag">Tag</label><select id="f-tag">' + tagOptions +
        "</select></div>" +
        '<div class="field"><label for="f-status">Identity</label><select id="f-status">' +
        '<option value="">All</option><option value="resolved"' +
        (filters.status === "resolved" ? " selected" : "") + ">Resolved</option>" +
        '<option value="unresolved"' + (filters.status === "unresolved" ? " selected" : "") +
        ">Needs review</option></select></div>" +
        '<div class="field" style="align-content:end"><button type="button" class="secondary" ' +
        'id="f-clear">Clear filters</button></div>';
      var searchTimer = null;
      function apply(patch) {
        Object.keys(patch).forEach(function (k) { filters[k] = patch[k]; });
        saveFilters(filters);
        renderFiltersInto(target, filters);
        loadFlightData(filters);
      }
      target.querySelector("#f-search").addEventListener("input", function (ev) {
        clearTimeout(searchTimer);
        var value = ev.target.value;
        searchTimer = setTimeout(function () { apply({ search: value }); }, 220);
      });
      ["from", "to"].forEach(function (key) {
        target.querySelector("#f-" + key).addEventListener("change", function (ev) {
          apply({ [key]: ev.target.value });
        });
      });
      [["aircraft", "#f-aircraft"], ["battery", "#f-battery"], ["pilot", "#f-pilot"],
       ["tag", "#f-tag"], ["status", "#f-status"]].forEach(function (pair) {
        target.querySelector(pair[1]).addEventListener("change", function (ev) {
          apply({ [pair[0]]: ev.target.value });
        });
      });
      target.querySelector("#f-clear").addEventListener("click", function () {
        state.filters = {};
        saveFilters({});
        renderFiltersInto(target, state.filters);
        loadFlightData(state.filters);
      });
      renderResults(data);
    }).catch(function (err) { toast("Could not load flights: " + err.message); });
  }

  function renderResults(data) {
    var totals = data.totals;
    document.getElementById("tiles").innerHTML =
      tile("Flights", totals.flights, "", totals.unresolved ?
        totals.unresolved + " need review" : "authoritative only") +
      tile("Air time", (totals.air_minutes / 60).toFixed(1), "h",
        Math.round(totals.air_minutes) + " air minutes") +
      tile("Distance", fmtDistance(totals.distance_m, 1), "", "from log details") +
      tile("Media", totals.photos === null ? "—" : String(totals.photos),
        totals.photos === null ? "" : "photos",
        totals.videos === null ? "counts unavailable" : totals.videos + " flights with video");
    var list = document.getElementById("flight-list");
    var records = document.getElementById("records");
    document.getElementById("list-count").textContent = data.rows.length + " shown";
    if (!data.rows.length) {
      list.innerHTML = "";
      records.innerHTML = '<div class="empty" style="grid-column:1/-1"><h3>No flights match</h3>' +
        '<p class="small muted">Clear the filters or import a log from the Import page.</p>' +
        '<button type="button" class="primary" data-go="#/import">Import flights</button></div>';
      bindGo(records);
      return;
    }
    list.innerHTML = data.rows.map(function (f) {
      var active = String(state.app && state.app.last_flight_id) === String(f.id);
      return '<li><a class="flight-item" href="#/flights/' + f.id + '"' +
        (active ? ' aria-current="true"' : "") + ">" +
        '<span class="when">' + esc(f.aircraft_name || "Unknown aircraft") + "</span>" +
        '<span class="meta">' + esc(dateOnly(f.start_time_utc || f.filename_time)) + " · " +
        esc(fmtDuration(f.duration_s)) + "</span>" +
        '<span class="meta">' + esc(f.pilot || "No pilot") +
        (f.identity_status !== "authoritative" ? " · needs review" : "") + "</span>" +
        "</a></li>";
    }).join("");
    records.innerHTML = data.rows.map(function (f) {
      var speed = speedUnit(f.max_h_speed_mps);
      var height = altUnit(f.max_height_m);
      return '<a class="record" href="#/flights/' + f.id + '">' +
        '<div class="top"><span class="title">' + esc(f.aircraft_name || "Unknown") + "</span>" +
        '<span class="when">' + esc(dateOnly(f.start_time_utc || f.filename_time)) + "</span></div>" +
        '<div class="metrics"><span><b>' + esc(fmtDuration(f.duration_s)) + "</b> air</span>" +
        "<span><b>" + esc(fmtDistance(f.total_distance_m)) + "</b> path</span>" +
        "<span><b>" + esc(height.v) + "</b> " + esc(height.u) + " max alt</span></div>" +
        '<div class="metrics"><span><b>' + esc(speed.v) + "</b> " + esc(speed.u) + " max</span>" +
        (f.battery_end_pct !== null ? "<span>battery <b>" +
          Math.round(f.battery_end_pct) + "%</b></span>" : "") +
        (f.gap_count ? "<span class=\"tag amber\">" + f.gap_count + " gap(s)</span>" : "") +
        (f.identity_status !== "authoritative"
          ? '<span class="tag amber">needs review</span>' : "") +
        "</div></a>";
    }).join("");
    bindGo(records);
  }

  function loadFlightData(filters) {
    return api("/api/flights?" + queryFromFilters(filters)).then(renderResults)
      .catch(function (err) { toast("Could not load flights: " + err.message); });
  }

  function bindGo(root) {
    root.querySelectorAll("[data-go]").forEach(function (btn) {
      btn.addEventListener("click", function () { go(btn.dataset.go); });
    });
  }

  // ------------------------------------------------------------- flight view

  var SECTIONS = [
    ["overview", "Overview"], ["details", "Details"], ["equipment", "Equipment"],
    ["power", "Power"], ["sensors", "Sensors"], ["controls", "Controls"], ["weather", "Weather"],
  ];

  function renderFlightDetail(id, section) {
    if (!id) return renderFlights();
    state.section = section;
    api("/api/flights/" + id).then(function (data) {
      state.flight = data;
      api("/api/settings", { method: "POST", body: { last_flight_id: id } }).catch(function () {});
      drawFlight(data, section);
    }).catch(function (err) {
      view.innerHTML = '<div class="panel"><div class="empty"><h3>Flight not found</h3>' +
        '<p class="small muted">' + esc(err.message) + "</p>" +
        '<button type="button" class="primary" data-go="#/flights">Back to flights</button></div></div>';
      bindGo(view);
    });
  }

  function drawFlight(data, section) {
    var f = data.flight;
    var tel = data.telemetry;
    var samples = tel.samples || [];
    var equipment = data.equipment;
    var speed = speedUnit(f.max_h_speed_mps);
    var height = altUnit(f.max_height_m);
    var home = height;
    var homeDist = distUnit(f.max_home_distance_m);
    var tempMax = tempUnit(f.battery_temp_max_c);
    var overviewHtml =
      '<div class="track-layout">' +
      '<div class="metric-stack" data-left="1">' +
      metricCard("Air time", fmtDuration(f.duration_s), "", "log details") +
      metricCard("Path distance", fmtDistance(f.total_distance_m), "", "total distance") +
      metricCard("Max home distance", homeDist.v, homeDist.u, "from home point") +
      metricCard("Max altitude", height.v, height.u, "above takeoff") +
      "</div>" +
      '<div class="track-wrap" id="track"></div>' +
      '<div class="metric-stack" data-right="1">' +
      metricCard("Max speed", speed.v, speed.u, "horizontal") +
      metricCard("Battery", (f.battery_start_pct !== null ? Math.round(f.battery_start_pct) : "—") +
        "% → " + (f.battery_end_pct !== null ? Math.round(f.battery_end_pct) : "—") + "%", "",
        "start → end") +
      metricCard("Battery temp", tempMax.v, tempMax.u, "max during flight") +
      metricCard("Airborne", fmtDuration(f.airborne_time_s), "", "from samples") +
      "</div></div>" +
      '<div class="row between" style="margin-top:10px">' +
      '<div class="legend small muted">' +
      '<span><i style="background:#0f8b8d"></i>track</span>' +
      '<span><i style="background:#b9761a"></i>telemetry gap</span>' +
      '<span><i style="background:#17262f"></i>home</span>' +
      (tel.source ? "<span>source: " + esc(tel.source) + "</span>" : "") +
      "</div>" +
      '<div class="row"><button type="button" class="secondary" id="fit-track">Fit track</button>' +
      '<button type="button" class="secondary" id="play-track">Play</button></div></div>';

    var scrub =
      '<div class="panel" style="margin-top:14px"><div class="panel-body">' +
      '<div class="row between" style="margin-bottom:6px">' +
      '<div class="row"><span class="tiny faint" id="readout">Scrub the charts or the track</span></div>' +
      '<div class="metric-select" id="metric-select">' +
      ["power", "altitude", "speed", "signal"].map(function (m) {
        return '<button type="button" class="secondary" data-metric="' + m + '"' +
          (state.metric === m ? ' aria-pressed="true"' : "") + ">" +
          m.charAt(0).toUpperCase() + m.slice(1) + "</button>";
      }).join("") + "</div></div>" +
      '<input type="range" id="scrub" min="0" max="1000" value="0" style="width:100%" ' +
      'aria-label="Flight time scrubber">' +
      '<div id="charts" class="stack" style="margin-top:8px"></div>' +
      "</div></div>";

    var detailsHtml = '<div class="panel"><div class="panel-body"><dl class="kv">' +
      kv("Start", fmtDate(f.start_time_utc || f.filename_time)) +
      kv("Timestamp precision", f.start_time_precision || "unknown") +
      kv("Filename time", f.filename_time || "not in the filename") +
      kv("Air duration", fmtDuration(f.duration_s)) +
      kv("Recorder duration", fmtDuration(f.recorder_duration_s)) +
      kv("Airborne time", fmtDuration(f.airborne_time_s)) +
      kv("Path length", fmtDistance(f.path_length_m)) +
      kv("Total distance (details)", fmtDistance(f.total_distance_m)) +
      kv("Max home distance", fmtDistance(f.max_home_distance_m)) +
      kv("Height above takeoff", fmtDistance(f.max_height_m)) +
      kv("Altitude (ASL)", fmtDistance(f.max_altitude_m)) +
      kv("Max speed", speed.v + " " + speed.u) +
      kv("Battery start → end", (f.battery_start_pct || "—") + "% → " +
        (f.battery_end_pct || "—") + "%") +
      kv("Battery temperature", tempMax.v + " " + tempMax.u) +
      kv("Minimum signal", f.signal_min === null ? "unknown" : f.signal_min) +
      kv("Samples", f.sample_count) +
      kv("Telemetry gaps", f.gap_count) +
      kv("Metrics source", f.metrics_source || "unknown") +
      kv("Parser", "dji-log " + (f.parser_version || "?")) +
      kv("Identity", f.identity_status === "authoritative" ? "resolved" :
        "unresolved — excluded from totals") +
      "</dl>" +
      '<div class="notice info small" style="margin-top:12px">' +
      "Path distance is the distance flown. Maximum home distance is how far the aircraft got " +
      "from its home point — they are different measurements. Height above takeoff is not " +
      "altitude above sea level. Airborne time is not recorder duration.</div>" +
      '<h3 style="margin-top:14px">Source files</h3>' +
      '<table class="data"><thead><tr><th>Version</th><th>Status</th><th>Size</th>' +
      "<th>Imported</th><th>Sources</th></tr></thead><tbody>" +
      data.files.map(function (file) {
        return "<tr><td>v" + esc(file.format_version || "?") + "</td><td>" +
          esc(file.status) + "</td><td class=\"num\">" +
          Math.round(file.size_bytes / 1024) + " KB</td><td>" +
          esc(dateOnly(file.first_imported_at)) + "</td><td class=\"tiny faint\">" +
          esc(file.memberships.join(", ") || "—") + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";

    var mediaNote = f.photo_count !== null || f.video_time_s !== null
      ? '<div class="notice small" style="margin-top:10px">Counts come from the log details. ' +
        "Photos and videos are not stored in this archive; clicking a count never claims files " +
        "were recovered.</div>" : "";

    var equipmentHtml = '<div class="panel"><div class="panel-body">' +
      '<div class="cards-2">' +
      equipmentCard("Aircraft", equipment.aircraft) +
      equipmentCard("Battery", equipment.battery) +
      "</div>" + mediaNote +
      '<div class="notice small" style="margin-top:12px">Serial numbers are read from the log. ' +
      "Manual overrides are kept; “reset from log” removes the override.</div>" +
      "</div></div>";

    var sectionHtml;
    if (section === "details") sectionHtml = detailsHtml;
    else if (section === "equipment") sectionHtml = equipmentHtml;
    else if (section === "power" || section === "sensors" || section === "controls"
             || section === "weather") sectionHtml = "";
    else sectionHtml = overviewHtml + scrub;

    view.innerHTML = '<div class="privacy-banner">Privacy display is on — serials, locations ' +
      "and route geometry are hidden.</div>" +
      '<div class="workspace">' +
      '<section class="panel pane-list" aria-label="Flights"><div class="panel-head">' +
      "<h2>Flights</h2><a class=\"tiny\" href=\"#/flights\">All flights</a></div>" +
      '<ul class="flight-list" id="flight-list"></ul></section>' +
      '<section class="panel pane-detail" aria-label="Selected flight"><div class="detail">' +
      '<nav class="rail" id="rail" aria-label="Flight sections"></nav>' +
      '<div class="detail-main">' +
      '<div class="detail-head"><div><h1>' + esc(f.aircraft_name || "Unknown aircraft") + "</h1>" +
      '<div class="small muted">' + esc(fmtDate(f.start_time_utc || f.filename_time)) + " · " +
      esc(f.pilot || "No pilot") + "</div>" +
      '<div class="tiny faint">' + (isMetric() ? "Metric" : "Imperial") + " · " +
      esc(f.identity_status) + "</div></div>" +
      '<div class="row"><button type="button" class="secondary" id="prev-flight"' +
      (data.neighbours.prev ? "" : " disabled") + ' aria-label="Previous flight">←</button>' +
      '<button type="button" class="secondary" id="next-flight"' +
      (data.neighbours.next ? "" : " disabled") + ' aria-label="Next flight">→</button></div></div>' +
      '<div class="tabs" id="tabs" role="tablist"></div>' +
      '<div id="section-body">' + sectionHtml + "</div>" +
      "</div></div></section></div>";

    document.getElementById("tabs").innerHTML = SECTIONS.map(function (pair) {
      return '<button type="button" role="tab" data-section="' + pair[0] + '"' +
        (pair[0] === section ? ' aria-selected="true"' : ' aria-selected="false"') + ">" +
        pair[1] + "</button>";
    }).join("");
    document.getElementById("rail").innerHTML = SECTIONS.map(function (pair) {
      return '<a href="#/flights/' + f.id + "/" + pair[0] + '"' +
        (pair[0] === section ? ' aria-current="true"' : "") + ">" + pair[1] + "</a>";
    }).join("");

    // list
    var listData = { rows: [] };
    api("/api/flights?" + queryFromFilters(state.filters || {})).then(function (list) {
      document.getElementById("flight-list").innerHTML = list.rows.map(function (row) {
        return '<li><a class="flight-item" href="#/flights/' + row.id + '"' +
          (row.id === f.id ? ' aria-current="true"' : "") + ">" +
          '<span class="when">' + esc(row.aircraft_name || "Unknown") + "</span>" +
          '<span class="meta">' + esc(dateOnly(row.start_time_utc || row.filename_time)) +
          " · " + esc(fmtDuration(row.duration_s)) + "</span></a></li>";
      }).join("");
    });

    document.querySelectorAll("#tabs button").forEach(function (btn) {
      btn.setAttribute("aria-controls", "section-body");
      btn.addEventListener("click", function () {
        go("#/flights/" + f.id + "/" + btn.dataset.section);
      });
    });
    var prevBtn = document.getElementById("prev-flight");
    var nextBtn = document.getElementById("next-flight");
    if (prevBtn) prevBtn.addEventListener("click", function () {
      if (data.neighbours.prev) go("#/flights/" + data.neighbours.prev + "/" + section);
    });
    if (nextBtn) nextBtn.addEventListener("click", function () {
      if (data.neighbours.next) go("#/flights/" + data.neighbours.next + "/" + section);
    });

    if (section === "overview") wireOverview(data);
    if (section === "power" || section === "sensors" || section === "controls"
        || section === "weather") {
      renderMetricSection(data, section);
    }
    applyChrome();
  }

  function metricCard(label, value, unit, sub) {
    return '<div class="metric-card"><div class="k">' + esc(label) + '</div>' +
      '<div class="v">' + esc(value) + (unit ? '<span class="u">' + esc(unit) + "</span>" : "") +
      "</div>" + (sub ? '<div class="s">' + esc(sub) + "</div>" : "") + "</div>";
  }

  function kv(label, value) {
    return "<dt>" + esc(label) + "</dt><dd>" + esc(value === null || value === undefined
      ? "—" : value) + "</dd>";
  }

  function serial(value) {
    if (!value) return "unknown";
    return '<span class="private">' + esc(value) + "</span>";
  }

  function batteryTrend(equipment, readings) {
    if (equipment.kind !== "battery") return "";
    var ordered = (readings || []).slice().reverse();
    var temps = ordered.map(function (r) {
      return r.temp_missing || r.temp_c === null ? null : r.temp_c;
    });
    var coverage = temps.filter(function (v) { return v !== null; }).length;
    if (ordered.length === 0) {
      return '<p class="tiny faint">No health trend yet: no readings are stored for this pack.</p>';
    }
    return '<div class="health-trend"><div class="row between tiny">' +
      '<span class="faint">Temperature trend</span>' +
      '<span class="faint">' + coverage + "/" + ordered.length + ' readings</span></div>' +
      '<div class="spark" data-spark="temp"></div>' +
      '<div class="tiny faint">Identity: ' + serial(equipment.serial) +
      ' · temperatures marked missing are gaps, not zeros.</div></div>';
  }

  function paintSparks(root, readings) {
    var ordered = (readings || []).slice().reverse();
    var temps = ordered.map(function (r) {
      return r.temp_missing || r.temp_c === null ? null : r.temp_c;
    });
    root.querySelectorAll('[data-spark="temp"]').forEach(function (node) {
      window.FDCharts.spark(node, temps, "#b9763b");
    });
  }

  function equipmentCard(kind, item) {
    if (!item) {
      return '<div class="card-sq"><h3>' + esc(kind) + '</h3><p class="small muted">' +
        "Not linked to canonical equipment. The log serial is shown on the Details tab." +
        "</p></div>";
    }
    var privateCls = kind === "Battery" ? "" : "";
    return '<div class="card-sq"><div class="rule-line"><h3>' + esc(kind) + "</h3>" +
      (item.archived ? '<span class="tag">archived</span>' : "") + "</div>" +
      '<p class="small"><a href="#/equipment/' + (item.kind === "battery" ? "battery" : "aircraft") +
      "/" + item.id + '">' + esc(item.alias || item.name) + "</a></p>" +
      '<div class="tiny faint private">Serial ' + esc(item.serial || "unknown") + "</div>" +
      '<div class="tiny faint">Firmware ' + esc(item.firmware || "—") + " · " +
      esc(item.operational_state || "state unknown") + "</div>" +
      '<button type="button" class="link small" data-act="reset-serial" data-equipment="' +
      item.id + '">Reset from log</button></div>';
  }

  function wireOverview(data) {
    var f = data.flight;
    var tel = data.telemetry;
    var samples = tel.samples || [];
    var trackEl = document.getElementById("track");
    if (!trackEl) return;
    var cursorTime = samples.length ? samples[0].t : 0;
    state.cursor = cursorTime;

    function drawTrack() {
      var sample = window.FDTrack.nearest(samples, state.cursor);
      window.FDTrack.render(trackEl, {
        segments: tel.segments,
        gaps: tel.gaps,
        home: f.home_lat !== null ? { lat: f.home_lat, lon: f.home_lon } : null,
        cursorSample: sample,
      });
    }
    drawTrack();

    var range = document.getElementById("scrub");
    if (range) {
      range.addEventListener("input", function () {
        var max = samples.length ? samples[samples.length - 1].t : 1;
        state.cursor = (Number(range.value) / 1000) * (max || 1);
        updateReadout(data);
        drawTrack();
      });
    }
    var fit = document.getElementById("fit-track");
    if (fit) fit.addEventListener("click", function () {
      drawTrack();
      toast("Track fitted to the recorded positions");
    });
    var play = document.getElementById("play-track");
    if (play) {
      play.addEventListener("click", function () {
        if (!samples.length) return;
        state.playing = !state.playing;
        play.textContent = state.playing ? "Pause" : "Play";
        var last = samples[samples.length - 1].t || 1;
        var step = last / 220;
        function tick() {
          if (!state.playing) return;
          state.cursor += step;
          if (state.cursor > last) { state.cursor = last; state.playing = false; play.textContent = "Play"; }
          if (range) range.value = String(Math.round((state.cursor / last) * 1000));
          updateReadout(data);
          drawTrack();
          if (state.playing) setTimeout(tick, 40);
        }
        if (state.playing) tick();
      });
    }
    document.querySelectorAll("#metric-select button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.metric = btn.dataset.metric;
        renderMetricSection(data, state.metric === "power" ? "power" :
          state.metric === "altitude" ? "sensors" : "controls");
      });
    });
    renderCharts(data);
    updateReadout(data);

    document.querySelectorAll('[data-act="reset-serial"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/equipment/" + btn.dataset.equipment + "/reset-from-log",
          { method: "POST", body: {} })
          .then(function () { toast("Serial link reset from the log"); render(); })
          .catch(function (e) { toast("Reset failed: " + e.message); });
      });
    });
  }

  function updateReadout(data) {
    var el = document.getElementById("readout");
    if (!el) return;
    var samples = data.telemetry.samples || [];
    var s = window.FDTrack.nearest(samples, state.cursor);
    if (!s) { el.textContent = "No samples in this record"; return; }
    var parts = ["t+" + fmtDuration(s.t)];
    if (s.height_m !== null && s.height_m !== undefined) {
      var h = altUnit(s.height_m);
      parts.push("height " + h.v + " " + h.u);
    }
    if (s.speed_mps !== null && s.speed_mps !== undefined) {
      var sp = speedUnit(s.speed_mps);
      parts.push(sp.v + " " + sp.u);
    }
    if (s.battery_pct !== null && s.battery_pct !== undefined) parts.push(Math.round(s.battery_pct) + "%");
    if (s.signal !== null && s.signal !== undefined) parts.push("signal " + s.signal);
    el.textContent = parts.join(" · ");
  }

  function renderCharts(data) {
    var host = document.getElementById("charts");
    if (!host) return;
    var samples = data.telemetry.samples || [];
    var times = samples.map(function (s) { return s.t; });
    var gaps = data.telemetry.gaps || [];
    if (!samples.length) {
      host.innerHTML = '<div class="chart-block"><div class="chart-head"><h3>Telemetry</h3>' +
        '<span class="tiny faint">no samples</span></div><div class="chart-body"></div>' +
        '<div class="panel-body small muted">This record has no sample stream. ' +
        "Details totals above are still from the log; nothing is interpolated.</div></div>";
      return;
    }
    var defs = [
      { key: "height", label: "Height above takeoff", unit: isMetric() ? "m" : "ft",
        color: "#0f8b8d", pick: function (s) { return !isMetric() && s.height_m !== null
          ? s.height_m * 3.28084 : s.height_m; } },
      { key: "speed", label: "Ground speed", unit: isMetric() ? "km/h" : "mph",
        color: "#3b7ea1", pick: function (s) { return s.speed_mps === null ? null
          : (isMetric() ? s.speed_mps * 3.6 : s.speed_mps * 2.23694); } },
      { key: "battery", label: "Battery", unit: "%", color: "#b9761a",
        pick: function (s) { return s.battery_pct; } },
      { key: "signal", label: "Signal", unit: "level", color: "#6d5aa8",
        pick: function (s) { return s.signal; } },
    ];
    host.innerHTML = defs.map(function (d) {
      var present = samples.some(function (s) {
        var v = d.pick(s);
        return v !== null && v !== undefined && isFinite(v);
      });
      if (!present) {
        return '<div class="chart-block"><div class="chart-head"><h3>' + esc(d.label) +
          '</h3><span class="tag">not in this record</span></div>' +
          '<div class="panel-body tiny faint">The adapter supplies no ' + esc(d.unit) +
          " values for this flight. Nothing is filled in.</div></div>";
      }
      return '<div class="chart-block"><div class="chart-head"><h3>' + esc(d.label) +
        '</h3><div class="legend"><span><i style="background:' + d.color + '"></i>' +
        esc(d.unit) + "</span>" +
        (gaps.length ? '<span><i style="background:#f3f5f7;border:1px solid #dde5ea"></i>' +
          "missing interval</span>" : "") + "</div></div>" +
        '<div class="chart-body" data-chart="' + d.key + '"></div></div>';
    }).join("");
    defs.forEach(function (d) {
      var target = host.querySelector('[data-chart="' + d.key + '"]');
      if (!target) return;
      window.FDCharts.line(target, {
        times: times,
        series: [{ key: d.key, label: d.label, color: d.color,
                   values: samples.map(d.pick) }],
        missing: gaps,
        cursor: state.cursor,
        yDomain: d.key === "battery" ? [0, 100] : null,
        ariaLabel: d.label + " over flight time",
        onSeek: function (t) {
          state.cursor = t;
          var range = document.getElementById("scrub");
          var max = times[times.length - 1] || 1;
          if (range) range.value = String(Math.round((t / max) * 1000));
          updateReadout(data);
          renderCharts(data);
        },
      });
    });
  }

  function renderMetricSection(data, section) {
    var body = document.getElementById("section-body");
    var f = data.flight;
    var samples = data.telemetry.samples || [];
    var gaps = data.telemetry.gaps || [];
    var titles = { power: "Power", sensors: "Sensors", controls: "Controls", weather: "Weather" };
    if (section === "weather") {
      body.innerHTML = '<div class="panel"><div class="panel-head"><h2>Weather</h2>' +
        '<span class="tag">no observations</span></div><div class="panel-body">' +
        '<div class="notice warn">No weather observations are stored for this flight.</div>' +
        '<p class="small muted" style="margin-top:10px">Historical weather needs an external ' +
        "provider. Nothing is requested until you accept it: the provider would receive a " +
        "rounded launch location and the flight time. That consent is per provider and is off " +
        "by default — records stay local either way.</p>" +
        '<div class="cards-2" style="margin-top:12px">' +
        '<div class="card-sq"><h3>Ground observations</h3>' +
        '<p class="small muted">Absent. Not estimated from the log.</p></div>' +
        '<div class="card-sq"><h3>Altitude profile</h3>' +
        '<p class="small muted">Absent. The track below is from the log, not a weather model.</p>' +
        "</div></div>" +
        '<div class="row" style="margin-top:12px"><button type="button" class="secondary" ' +
        'id="weather-consent">Enable an external weather provider…</button></div></div></div>';
      var btn = document.getElementById("weather-consent");
      if (btn) btn.addEventListener("click", function () {
        openModal("External weather provider",
          "<p>This is off by default and no request has been made.</p>" +
          "<p class=\"small muted\">A weather provider would receive a rounded launch location " +
          "and the flight time. Flight Desk would mark those values as estimates and date them. " +
          "You can reverse this at any time in Settings.</p>" +
          "<p class=\"small\"><b>No provider is configured in this build.</b> The toggle records " +
          "your consent choice only; nothing is transmitted.</p>",
          [{ label: "Keep local only", cls: "secondary" },
           { label: "Record consent for later", cls: "primary", action: function () {
             api("/api/settings", { method: "POST", body: { external_consent: { weather: true } } })
               .then(function () { toast("Consent recorded; no data sent"); closeModal(); });
           } }]);
      });
      return;
    }
    if (!samples.length) {
      body.innerHTML = '<div class="panel"><div class="panel-head"><h2>' + titles[section] +
        '</h2><span class="tag">not in this record</span></div><div class="panel-body">' +
        '<div class="notice">This record does not contain ' + section +
        " channels. The adapter never substitutes zeros for missing data.</div></div></div>";
      return;
    }
    var defs = {
      power: [
        { key: "battery", label: "Battery charge", unit: "%", color: "#b9761a",
          pick: function (s) { return s.battery_pct; } },
        { key: "voltage", label: "Pack voltage", unit: "V", color: "#8a5a1a",
          pick: function (s) { return s.voltage; } },
        { key: "temp", label: "Battery temperature", unit: isMetric() ? "°C" : "°F",
          color: "#b23b3b", pick: function (s) {
            if (s.battery_c === null || s.battery_c === undefined) return null;
            return isMetric() ? s.battery_c : s.battery_c * 9 / 5 + 32; } },
      ],
      sensors: [
        { key: "height", label: "Height above takeoff", unit: isMetric() ? "m" : "ft",
          color: "#0f8b8d", pick: function (s) {
            if (s.height_m === null || s.height_m === undefined) return null;
            return isMetric() ? s.height_m : s.height_m * 3.28084; } },
        { key: "alt", label: "Altitude (ASL)", unit: isMetric() ? "m" : "ft",
          color: "#3b7ea1", pick: function (s) {
            if (s.alt_m === null || s.alt_m === undefined) return null;
            return isMetric() ? s.alt_m : s.alt_m * 3.28084; } },
        { key: "gps", label: "GPS satellites", unit: "count", color: "#6d5aa8",
          pick: function (s) { return s.gps_num; } },
      ],
      controls: [
        { key: "speed", label: "Ground speed", unit: isMetric() ? "km/h" : "mph",
          color: "#3b7ea1", pick: function (s) {
            if (s.speed_mps === null || s.speed_mps === undefined) return null;
            return isMetric() ? s.speed_mps * 3.6 : s.speed_mps * 2.23694; } },
        { key: "signal", label: "RC signal", unit: "level", color: "#6d5aa8",
          pick: function (s) { return s.signal; } },
      ],
    };
    var chosen = defs[section];
    var present = chosen.filter(function (d) {
      return samples.some(function (s) { var v = d.pick(s); return v !== null && v !== undefined; });
    });
    var absent = chosen.filter(function (d) { return present.indexOf(d) < 0; });
    body.innerHTML = '<div class="panel"><div class="panel-head"><h2>' + titles[section] +
      "</h2><div class=\"legend\">" + present.map(function (d) {
        return '<span><i style="background:' + d.color + '"></i>' + esc(d.label) + " (" +
          esc(d.unit) + ")</span>";
      }).join("") + "</div></div>" +
      present.map(function (d) {
        return '<div class="chart-block" style="margin:10px 12px"><div class="chart-body" ' +
          'data-chart="' + section + "-" + d.key + '"></div></div>';
      }).join("") +
      (absent.length ? '<div class="panel-body"><div class="notice small">Not provided by this ' +
        "adapter: " + absent.map(function (d) { return esc(d.label); }).join(", ") +
        ". Missing channels stay unknown.</div></div>" : "") +
      '<div class="panel-body tiny faint">Derived values use only recorded samples. ' +
      "No score is synthesised where the log has no evidence.</div></div>";
    var times = samples.map(function (s) { return s.t; });
    present.forEach(function (d) {
      window.FDCharts.line(body.querySelector('[data-chart="' + section + "-" + d.key + '"]'), {
        times: times,
        series: [{ key: d.key, label: d.label, color: d.color, values: samples.map(d.pick) }],
        missing: gaps,
        cursor: state.cursor,
        ariaLabel: d.label + " over flight time",
        onSeek: function (t) {
          state.cursor = t;
          updateReadout(data);
          renderMetricSection(data, section);
        },
      });
    });
  }

  // ----------------------------------------------------------- equipment view

  function renderEquipmentList(kind, id) {
    api("/api/equipment?archived=" + (kind === "aircraft" ? "1" : "1")).then(function (data) {
      var rows = data.rows.filter(function (r) { return r.kind === (kind === "aircraft"
        ? "aircraft" : "battery"); });
      var listHtml = rows.map(function (r) {
        return '<li><a href="#/equipment/' + (r.kind === "battery" ? "battery" : "aircraft") +
          "/" + r.id + '"' + (r.id === id ? ' aria-current="true"' : "") + ">" +
          '<span class="name">' + esc(r.alias || r.name) + "</span>" +
          '<span class="sub">' + esc(r.serial || "no serial") +
          (r.archived ? " · archived" : "") + "</span></a></li>";
      }).join("");
      view.innerHTML = '<div class="privacy-banner">Privacy display is on.</div>' +
        '<div class="mobile-switch"><button class="secondary" data-mobile="list" type="button">' +
        'List</button><button class="secondary" data-mobile="detail" type="button">Detail</button></div>' +
        '<div class="split"><section class="panel pane-list"><div class="panel-head">' +
        '<h2>' + (kind === "aircraft" ? "Drones" : "Batteries") + "</h2>" +
        '<a class="tiny" href="#/equipment/' + (kind === "aircraft" ? "battery" : "aircraft") +
        '">Switch</a></div><ul class="eq-list">' + listHtml + "</ul></section>" +
        '<section class="pane-detail" id="eq-detail"></section></div>';
      bindMobileSwitch();
      if (id) loadEquipment(id);
      else document.getElementById("eq-detail").innerHTML =
        '<div class="panel"><div class="empty"><h3>Pick a record</h3>' +
        '<p class="small muted">Choose from the list to see usage, flight history and service.</p>' +
        "</div></div>";
    });
  }

  function loadEquipment(id) {
    var host = document.getElementById("eq-detail");
    api("/api/equipment/" + id).then(function (data) {
      var e = data.equipment;
      var fl = data.flights;
      var airS = fl.reduce(function (acc, f) { return acc + (f.duration_s || 0); }, 0);
      var charges = data.charge_history;
      var rules = data.rules.map(function (r) { return ruleCard(r, { readonly: false }); }).join("");
      var readings = data.readings.slice(0, 12);
      host.innerHTML = '<div class="panel"><div class="panel-head"><h2>' +
        esc(e.alias || e.name) + "</h2>" +
        (e.archived ? '<span class="tag">archived</span>' : "") + "</div>" +
        '<div class="panel-body">' +
        '<div class="cards-2">' +
        '<div class="card-sq"><h3>Overview</h3><dl class="kv" style="grid-template-columns:110px 1fr">' +
        '<dt>Serial</dt><dd>' + serial(e.serial) + "</dd>" +
        kv("Purchased", e.purchase_date || "—") +
        kv("Firmware", e.firmware || "—") + kv("State", e.operational_state || "—") +
        kv("Flights", String(fl.length)) + kv("Air time", fmtDuration(airS)) +
        kv("Placeholder", e.placeholder ? "yes" : "no") +
        kv("Locked details", e.locked_details ? "yes" : "no") + "</dl>" +
        '<div class="row" style="margin-top:8px"><button type="button" class="secondary" ' +
        'data-act="edit-eq" data-equipment="' + e.id + '">Edit details</button>' +
        (e.placeholder ? "" : '<button type="button" class="secondary" data-act="oneoff" ' +
          'data-equipment="' + e.id + '">One-off service</button>') + "</div></div>" +
        '<div class="card-sq"><h3>Usage</h3>' +
        (e.kind === "battery"
          ? (charges && charges.available
            ? '<p class="small">Logged charge-cycle delta: <b>' + charges.charges +
              "</b> across " + charges.readings + " readings" +
              (charges.replacements ? " · " + charges.replacements + " counter reset(s)" : "") +
              '</p><p class="tiny faint">From ' + esc(dateOnly(charges.from)) + " to " +
              esc(dateOnly(charges.to)) + "</p>"
            : '<p class="small muted">No charge-cycle history recorded. Charge counters fall back ' +
              "to logged flights and say so.</p>")
          : "") +
        batteryTrend(e, data.readings) +
        '<p class="small">' + (e.kind === "battery" ? "Battery health readings: "
          + data.readings.length : "Linked batteries: " + data.children.length) + "</p>" +
        (e.kind === "battery" && data.last_reading ? '<p class="tiny faint">Last reading ' +
          esc(dateOnly(data.last_reading.at)) + " · " +
          (data.last_reading.temp_missing ? "temperature missing" :
            (data.last_reading.temp_c !== null
              ? tempUnit(data.last_reading.temp_c).v + " " + tempUnit(data.last_reading.temp_c).u
              : "no temperature")) + "</p>"
          : '<p class="tiny faint">' + (e.kind === "battery"
              ? "No battery readings stored." : "No battery is linked to this aircraft yet.") +
            "</p>") +
        (e.kind === "aircraft" && data.children.length
          ? '<p class="tiny faint">' + data.children.length + " linked item(s)</p>" : "") +
        "</div></div>" +
        '<h3 style="margin:16px 0 8px">Maintenance</h3>' +
        (rules ? '<div class="cards-2">' + rules + "</div>" :
          '<div class="notice small">No maintenance rules apply to this item.</div>') +
        '<h3 style="margin:16px 0 8px">Flight history</h3>' +
        (fl.length ? '<table class="data"><thead><tr><th>Date</th><th class="num">Air</th>' +
          '<th class="num">Distance</th><th>Pilot</th></tr></thead><tbody>' +
          fl.map(function (f) {
            return '<tr><td><a href="#/flights/' + f.id + '">' +
              esc(dateOnly(f.start_time_utc || f.filename_time)) + "</a></td>" +
              '<td class="num">' + esc(fmtDuration(f.duration_s)) + "</td>" +
              '<td class="num">' + esc(fmtDistance(f.total_distance_m)) + "</td>" +
              "<td>" + esc(f.pilot || "—") + "</td></tr>";
          }).join("") + "</tbody></table>"
          : '<div class="notice small">No flights are linked to this item yet.</div>') +
        '<h3 style="margin:16px 0 8px">Service record</h3>' +
        (data.services.length ? '<table class="data"><thead><tr><th>Date</th><th>Kind</th>' +
          "<th>Notes</th><th>Source</th></tr></thead><tbody>" +
          data.services.map(function (s) {
            return "<tr><td>" + esc(dateOnly(s.occurred_at)) + "</td><td>" + esc(s.kind) +
              "</td><td>" + esc(s.notes || s.raw_wording || "—") + "</td><td>" +
              esc(s.source) + "</td></tr>";
          }).join("") + "</tbody></table>"
          : '<div class="notice small">No service events recorded.</div>') +
        "</div></div>";
      wireRuleActions(host, function () { loadEquipment(id); });
      paintSparks(host, data.readings);
      bindAct(host);
      applyChrome();
    }).catch(function (err) {
      host.innerHTML = '<div class="panel"><div class="empty"><h3>Not found</h3><p>' +
        esc(err.message) + "</p></div></div>";
    });
  }

  // --------------------------------------------------------- maintenance view

  function renderMaintenance(tab) {
    api("/api/maintenance").then(function (data) {
      var tabs = [["upcoming", "Upcoming"], ["completed", "Completed"], ["list", "Service list"]];
      var body = "";
      if (tab === "upcoming") {
        var up = data.upcoming;
        body = '<h3 style="margin-bottom:8px">Batteries</h3>' +
          (up.batteries.length ? '<div class="cards-2">' +
            up.batteries.map(function (s) { return ruleCard(s); }).join("") + "</div>"
            : '<div class="notice small">No battery service is due.</div>') +
          '<h3 style="margin:18px 0 8px">Aircraft</h3>' +
          (up.aircraft.length ? '<div class="cards-2">' +
            up.aircraft.map(function (s) { return ruleCard(s); }).join("") + "</div>"
            : '<div class="notice small">No aircraft service is due.</div>') +
          (data.unresolved_groups.length ? '<h3 style="margin:18px 0 8px">Needs clarification</h3>' +
            '<div class="cards-2">' + data.unresolved_groups.map(function (s) {
              return '<div class="card-sq"><div class="rule-line"><h3>' +
                esc(s.equipment_name || "Grouped row") + "</h3><span class=\"tag amber\">" +
                "grouped</span></div>" +
                '<p class="small muted">' + esc(s.raw_wording || s.notes || "") + "</p>" +
                (s.time_range_text ? '<p class="tiny faint">Range: ' +
                  esc(s.time_range_text) + "</p>" : "") +
                '<p class="tiny faint">These events are kept exactly as captured. They are not ' +
                "split into invented per-item timestamps.</p></div>";
            }).join("") + "</div>" : "") +
          (data.captured_mismatches.length ? '<div class="notice warn" style="margin-top:14px">' +
            data.captured_mismatches.length + " rule(s) differ from the captured status. " +
            "The capture is kept as evidence; the computed value is shown above.</div>" : "");
      } else if (tab === "completed") {
        body = data.completed.length
          ? '<div class="panel"><table class="data"><thead><tr><th>Date</th><th>Equipment</th>' +
            "<th>Rule</th><th>Kind</th><th>Notes</th><th></th></tr></thead><tbody>" +
            data.completed.map(function (s) {
              return "<tr><td>" + esc(s.occurred_at || "—") + "</td><td>" +
                esc(s.equipment_name || "—") + "</td><td>" + esc(s.rule_label || "—") +
                "</td><td>" + esc(s.kind) + (s.resolved ? "" : ' <span class="tag amber">grouped</span>') +
                "</td><td class=\"small\">" + esc(s.notes || s.raw_wording || "") + "</td>" +
                "<td>" + (s.can_undo ? '<button type="button" class="link" data-act="undo" ' +
                  'data-service="' + s.id + '">Undo</button>' : "") + "</td></tr>";
            }).join("") + "</tbody></table></div>"
          : '<div class="panel"><div class="empty"><h3>Nothing completed yet</h3>' +
            '<p class="small muted">Complete a rule from the Upcoming list.</p></div></div>';
      } else {
        body = '<div class="panel"><div class="panel-head"><h2>Rule list</h2>' +
          '<span class="tiny faint">' + data.statuses.length + " rules</span></div>" +
          '<table class="data"><thead><tr><th>Equipment</th><th>Rule</th><th>Status</th>' +
          '<th>Counters</th><th>Baseline</th></tr></thead><tbody>' +
          data.statuses.filter(function (s) { return !s.archived; }).map(function (s) {
            return "<tr><td>" + esc(s.equipment_name) + "</td><td>" + esc(s.label) +
              "</td><td>" + statusBadge(s.status) + "</td>" +
              "<td class=\"small\">" + s.counters.map(function (c) {
                var unit = c.metric === "minutes" ? "min" : c.metric;
                return esc(unit) + ": " + (c.value === null ? "unknown"
                  : Math.round(c.value) + "/" + (c.limit === null ? "—" : Math.round(c.limit)));
              }).join("<br>") + "</td><td class=\"tiny faint\">" +
              esc(s.baseline.basis) + (s.baseline.at ? " · " + esc(dateOnly(s.baseline.at)) : "") +
              "</td></tr>";
          }).join("") + "</tbody></table></div>";
      }
      view.innerHTML = '<div class="panel" style="margin-bottom:14px"><div class="panel-head">' +
        '<h2>Maintenance</h2><div class="row tiny faint">' +
        esc((data.snapshot && data.snapshot.source_label) || "no capture applied") +
        (data.snapshot ? " · covered through " + esc(data.snapshot.covered_through) : "") +
        "</div></div>" +
        '<div class="tabs" style="margin:0 14px" role="tablist">' + tabs.map(function (t) {
          return '<button type="button" role="tab" data-tab="' + t[0] + '"' +
            (t[0] === tab ? ' aria-selected="true"' : ' aria-selected="false"') + ">" +
            t[1] + "</button>";
        }).join("") + "</div>" +
        '<div class="panel-body">' + body + "</div></div>";
      view.querySelectorAll("[data-tab]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          go("#/maintenance/" + btn.dataset.tab);
        });
      });
      wireRuleActions(view, function () { renderMaintenance(tab); });
      bindAct(view);
      applyChrome();
    }).catch(function (err) { toast("Maintenance unavailable: " + err.message); });
  }

  function wireRuleActions(root, after) {
    root.querySelectorAll('[data-act="complete"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        openNote("Complete service",
          "Record a completed service for this rule. This sets a new baseline.",
          function (note) {
            return api("/api/rules/" + btn.dataset.rule + "/service", {
              method: "POST",
              body: { action: "completed", notes: note, equipment_id: Number(btn.dataset.equipment) },
            }).then(function () { toast("Service completed"); after(); });
          });
      });
    });
    root.querySelectorAll('[data-act="skip"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        openNote("Skip this service",
          "Skip resets this rule only. The reason is kept in the audit trail.",
          function (note) {
            return api("/api/rules/" + btn.dataset.rule + "/service", {
              method: "POST",
              body: { action: "skipped", notes: note, equipment_id: Number(btn.dataset.equipment) },
            }).then(function () { toast("Service skipped"); after(); });
          });
      });
    });
    root.querySelectorAll('[data-act="rule"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/rules/" + btn.dataset.rule).then(function (rule) {
          var counters = rule.counters.map(function (c) {
            return "<li>" + esc(c.metric) + ": " + (c.value === null ? "unknown"
              : Math.round(c.value)) + " / " + (c.limit === null ? "—" : Math.round(c.limit)) +
              " (" + esc(c.status) + ")</li>";
          }).join("");
          var history = (rule.history || []).map(function (h) {
            return "<li>" + esc(h.occurred_at || "—") + " · " + esc(h.kind) + " · " +
              esc(h.notes || "") + "</li>";
          }).join("");
          openModal("Rule · " + rule.equipment_name + " " + rule.label,
            "<p class=\"small muted\">" + esc(rule.explanation) + "</p>" +
            "<h4>Counters</h4><ul class=\"small\">" + counters + "</ul>" +
            "<h4>Baseline</h4><p class=\"small\">" + esc(rule.baseline.basis) +
            (rule.baseline.at ? " from " + esc(rule.baseline.at) : "") + "</p>" +
            "<h4>History</h4><ul class=\"small\">" + (history || "<li>none</li>") + "</ul>",
            [{ label: "Close", cls: "secondary" }]);
        });
      });
    });
    root.querySelectorAll('[data-act="undo"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/services/" + btn.dataset.service + "/undo", { method: "POST", body: {} })
          .then(function (res) {
            if (res.ok) { toast("Service reversed"); after(); }
            else toast("Cannot undo: " + res.code);
          });
      });
    });
    root.querySelectorAll('[data-act="coverage"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/maintenance").then(function (data) {
          api("/api/flights?status=resolved").then(function (list) {
            var group = data.statuses.filter(function (s) { return s.id === Number(btn.dataset.rule); })[0];
            openModal("Unresolved coverage",
              "<p class=\"small\">" + group.uncertain_count + " flight(s) are not counted " +
              "because the capture may already include them. Decide per flight; the snapshot " +
              "is retained either way.</p><div id=\"cover-list\"></div>",
              [{ label: "Close", cls: "secondary" }]);
            var host = document.getElementById("cover-list");
            var candidates = list.rows.filter(function (f) {
              return f.identity_status === "authoritative";
            }).slice(0, 12);
            host.innerHTML = candidates.map(function (f) {
              return '<div class="row" style="border-bottom:1px solid var(--line);padding:6px 0">' +
                '<span class="small grow">' + esc(dateOnly(f.start_time_utc)) + " · " +
                esc(f.aircraft_name || "") + " · " + esc(fmtDuration(f.duration_s)) + "</span>" +
                '<button type="button" class="secondary" data-cover="include" data-flight="' +
                f.id + '">Count it</button>' +
                '<button type="button" class="secondary" data-cover="covered" data-flight="' +
                f.id + '">Already covered</button></div>';
            }).join("") || "<p class=\"small muted\">No candidate flights.</p>";
            host.querySelectorAll("[data-cover]").forEach(function (b) {
              b.addEventListener("click", function () {
                api("/api/coverage/resolve", { method: "POST", body: {
                  flight_id: Number(b.dataset.flight),
                  snapshot_id: data.snapshot ? data.snapshot.id : 0,
                  decision: b.dataset.cover,
                } }).then(function () { toast("Coverage decision saved"); after(); closeModal(); });
              });
            });
          });
        });
      });
    });
    root.querySelectorAll('[data-act="oneoff"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        openNote("One-off service",
          "One-off work does not reset a recurring rule unless you link it.",
          function (note) {
            return api("/api/services/oneoff", { method: "POST", body: {
              equipment_id: Number(btn.dataset.equipment), notes: note } })
              .then(function () { toast("One-off service recorded"); after(); });
          });
      });
    });
    root.querySelectorAll('[data-act="edit-eq"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.dataset.equipment;
        api("/api/equipment/" + id).then(function (data) {
          var e = data.equipment;
          openModal("Edit " + (e.alias || e.name),
            '<div class="stack"><div class="field"><label for="eq-alias">Alias</label>' +
            '<input id="eq-alias" type="text" value="' + esc(e.alias || "") + '"></div>' +
            '<div class="field"><label for="eq-purchase">Purchase date</label>' +
            '<input id="eq-purchase" type="date" value="' + esc(e.purchase_date || "") + '"></div>' +
            '<div class="field"><label for="eq-firmware">Firmware</label>' +
            '<input id="eq-firmware" type="text" value="' + esc(e.firmware || "") + '"></div>' +
            '<div class="field"><label for="eq-state">Operational state</label>' +
            '<select id="eq-state">' + ["Active", "Spare", "Retired", "In repair", ""].map(
              function (v) {
                return '<option value="' + v + '"' +
                  ((e.operational_state || "") === v ? " selected" : "") + ">" +
                  (v || "—") + "</option>";
              }).join("") + "</select></div>" +
            '<div class="field"><label for="eq-notes">Notes</label>' +
            '<textarea id="eq-notes">' + esc(e.notes || "") + "</textarea></div>" +
            '<div class="notice small">Editing a field marks it as a manual override; ' +
            "a re-import will not undo it.</div></div>",
            [{ label: "Cancel", cls: "secondary" }, { label: "Save", cls: "primary",
               action: function () {
                 return api("/api/equipment/" + id + "/edit", { method: "POST", body: {
                   fields: {
                     alias: document.getElementById("eq-alias").value,
                     purchase_date: document.getElementById("eq-purchase").value,
                     firmware: document.getElementById("eq-firmware").value,
                     operational_state: document.getElementById("eq-state").value,
                     notes: document.getElementById("eq-notes").value,
                   } } }).then(function () { toast("Saved"); closeModal(); after(); });
               } }]);
        });
      });
    });
  }

  // --------------------------------------------------------------- import

  function renderImport() {
    api("/api/import/history").then(function (data) {
      view.innerHTML = '<div class="workspace" style="grid-template-columns:minmax(0,1fr) 340px">' +
        '<section class="stack">' +
        '<div class="panel"><div class="panel-head"><h2>Import</h2>' +
        '<span class="tiny faint">offline · local only</span></div>' +
        '<div class="panel-body">' +
        '<div class="dropzone" id="drop" tabindex="0" role="button" ' +
        'aria-label="Drop flight logs, a folder or the handoff ZIP here">' +
        "<h2>Drop flight logs, a folder, or the handoff ZIP</h2>" +
        '<p class="small muted">Single DJI TXT files, an AirData export folder, or the original ' +
        "ZIP. Originals are archived before anything is indexed.</p>" +
        '<div class="row" style="justify-content:center;margin-top:10px">' +
        '<button type="button" class="primary" id="pick-files">Choose files…</button>' +
        '<button type="button" class="secondary" id="pick-zip">Choose ZIP…</button>' +
        '<button type="button" class="secondary" id="pick-folder">Choose folder…</button>' +
        "</div>" +
        '<p class="tiny faint" style="margin-top:8px">Files are read in this browser and ' +
        'posted only to this local server. <button type="button" class="link" ' +
        'id="pick-windows">Use the Windows dialog instead</button></p></div>' +
        '<div class="progress" id="progress"><span></span></div>' +
        '<div id="import-status" class="small muted" role="status" aria-live="polite" ' +
        'style="margin-top:6px' +
        (state.lastImportTone === "bad" ? ";color:var(--red)" : "") + '">' +
        esc(state.lastImportStatus || "") + "</div>" +
        '<div id="batch-result">' + renderBatchSummary(state.lastImport) + "</div>" +
        '<div class="row" style="margin-top:12px"><label class="small muted" for="import-pilot">' +
        'Pilot for new flights</label><select id="import-pilot" style="max-width:200px">' +
        (state.app.pilots.length ? state.app.pilots : ["No pilots yet"]).map(function (p) {
          return "<option>" + esc(p) + "</option>";
        }).join("") + "</select></div>" +
        '<div class="notice small" style="margin-top:12px">If synthetic demo data is not ' +
        'loaded, you can <button type="button" class="link" id="seed-demo">load an invented ' +
        "demo set</button> to explore the screens. It is labelled synthetic everywhere.</div>" +
        "</div></div>" +
        '<div class="panel"><div class="panel-head"><h2>Awaiting DJI key</h2>' +
        '<button type="button" class="link tiny" id="unlock-all">Unlock with saved key' +
        (state.app.pending_key ? " · " + state.app.pending_key + " waiting" : "") +
        "</button></div>" + pendingTable(data.pending) + "</div>" +
        '<div class="panel"><div class="panel-head"><h2>Import history</h2></div>' +
        '<table class="data"><thead><tr><th>Started</th><th>Source</th><th>Status</th>' +
        '<th class="num">New</th><th class="num">Already</th><th class="num">Waiting</th>' +
        '<th class="num">Attention</th></tr></thead><tbody>' +
        (data.batches.map(function (b) {
          return "<tr><td>" + esc(dateOnly(b.started_at)) + " " +
            esc((b.started_at || "").slice(11, 16)) + "</td><td>" + esc(b.source_kind) +
            "</td><td>" + esc(b.status) + (b.note ? " · " + esc(b.note) : "") + "</td>" +
            '<td class="num">' + (b.totals.new || 0) + '</td><td class="num">' +
            (b.totals.already_imported || 0) + '</td><td class="num">' +
            (b.totals.awaiting_dji || 0) + '</td><td class="num">' +
            (b.totals.needs_attention || 0) + "</td></tr>";
        }).join("") || '<tr><td colspan="7" class="muted">No imports yet.</td></tr>') +
        "</tbody></table></div>" +
        '<div class="panel"><div class="panel-head"><h2>Needs review</h2></div>' +
        (data.unresolved.length ? '<table class="data"><thead><tr><th>Date</th><th>Aircraft</th>' +
          "<th>Reason</th><th>Action</th></tr></thead><tbody>" +
          data.unresolved.map(function (f) {
            return '<tr><td><a href="#/flights/' + f.id + '">' +
              esc(dateOnly(f.start_time_utc || f.filename_time)) + "</a></td><td>" +
              esc(f.aircraft_name || "—") + "</td><td class=\"small\">" +
              esc(f.excluded_reason || "unresolved") + "</td><td>" +
              '<button type="button" class="link" data-act="merge" data-flight="' + f.id +
              '">Keep / merge</button></td></tr>';
          }).join("") + "</tbody></table>"
          : '<div class="panel-body small muted">No unresolved flights.</div>') +
        "</div></section>" +
        '<aside class="stack"><div class="panel"><div class="panel-head"><h2>What happens</h2></div>' +
        '<div class="panel-body small muted"><ol style="padding-left:18px;margin:0">' +
        "<li>Original bytes are hashed and written to the local archive.</li>" +
        "<li>Only then is the index updated, in one transaction.</li>" +
        "<li>A file seen before is reported as already imported.</li>" +
        "<li>Encrypted logs keep their metadata and wait for your DJI key.</li>" +
        "</ol></div></div>" +
        '<div class="panel"><div class="panel-head"><h2>Recent status</h2></div>' +
        '<div class="panel-body small">' + Object.keys(state.app.raw_status).map(function (k) {
          return "<div class=\"row between\"><span>" + esc(k) + "</span><b>" +
            state.app.raw_status[k] + "</b></div>";
        }).join("") + "</div></div></aside></div>";

      wireImport();
      applyChrome();
    });
  }

  function pendingTable(rows) {
    if (!rows.length) {
      return '<div class="panel-body small muted">Nothing is waiting on a key.</div>';
    }
    return '<table class="data"><thead><tr><th>Version</th><th>Status</th><th>Reason</th>' +
      "<th></th></tr></thead><tbody>" + rows.map(function (r) {
        return "<tr><td>v" + esc(r.format_version || "?") + "</td><td>" + esc(r.status) +
          "</td><td class=\"small\">" + esc(r.error_category || "—") + "</td><td>" +
          '<button type="button" class="link" data-act="unlock" data-raw="' + r.id +
          '">Unlock</button></td></tr>';
      }).join("") + "</tbody></table>";
  }

  var importing = false;

  function wireImport() {
    var drop = document.getElementById("drop");
    var progress = document.querySelector("#progress > span");
    function setProgress(frac) {
      if (progress) progress.style.width = Math.round(frac * 100) + "%";
    }
    function busy(on, label, tone) {
      importing = on;
      ["pick-files", "pick-zip", "pick-folder", "pick-windows"].forEach(function (id) {
        var b = document.getElementById(id);
        if (b) b.disabled = on;
      });
      if (label !== undefined) setImportStatus(label, tone);
    }
    async function postFile(item) {
      var file = item.file;
      var headers = {
        "X-FD-Filename": encodeURIComponent(file.name),
        "X-FD-Member": encodeURIComponent(item.member || file.name),
        "Content-Type": "application/octet-stream",
      };
      var response;
      try {
        response = await fetch("/api/import/upload", {
          method: "POST", headers: Object.assign({ "X-FD-Token": TOKEN }, headers),
          body: file, credentials: "same-origin",
        });
      } catch (err) {
        return { ok: false, code: "upload_failed" };
      }
      var text = await response.text();
      var payload;
      try {
        payload = JSON.parse(text);
      } catch (err) {
        payload = { ok: false, code: "internal_error" };
      }
      payload.http_status = response.status;
      return payload;
    }
    function summarise(result) {
      var totals = result.totals || {};
      if (result.note || result.code) {
        return "Nothing was imported from that file.";
      }
      var parts = [];
      if (totals.new_flight) parts.push(totals.new_flight + " new flight(s)");
      if (totals.new) parts.push(totals.new + " new file(s)");
      if (totals.already_imported) parts.push(totals.already_imported + " already imported");
      if (totals.awaiting_dji) parts.push(totals.awaiting_dji + " awaiting the DJI key");
      if (totals.needs_attention) parts.push(totals.needs_attention + " need attention");
      return parts.length ? "Done: " + parts.join(", ") + "." : "Done: nothing new.";
    }
    async function handleFiles(items) {
      if (importing) { toast("An import is already running"); return; }
      if (!items.length) return;
      busy(true, "Reading " + items.length + " file(s)\u2026", "warn");
      var totals = { discovered: 0, new: 0, new_flight: 0, already_imported: 0,
                     awaiting_dji: 0, unsupported: 0, needs_attention: 0 };
      var codes = {};
      var failed = [];
      var notes = [];
      for (var i = 0; i < items.length; i++) {
        setProgress(i / items.length);
        setImportStatus("Reading file " + (i + 1) + " of " + items.length + "\u2026", "warn");
        var res = await postFile(items[i]);
        if (res && res.totals) {
          Object.keys(res.totals).forEach(function (k) {
            totals[k] = (totals[k] || 0) + (res.totals[k] || 0);
          });
          Object.keys(res.codes || {}).forEach(function (k) {
            codes[k] = (codes[k] || 0) + res.codes[k];
          });
          if (res.note) notes.push(res.note);
        } else {
          totals.discovered += 1;
          totals.needs_attention += 1;
          var code = (res && res.code) || "internal_error";
          codes[code] = (codes[code] || 0) + 1;
          failed.push(items[i].file.name);
        }
      }
      setProgress(1);
      var result = { totals: totals, codes: codes, note: notes[0] || null,
                     failed_files: failed };
      var status = summarise(result);
      rememberImport(result, status, (result.note || failed.length) ? "bad" : "");
      busy(false, status, state.lastImportTone);
      showBatch(result);
      refreshState().then(function () { renderImport(); })
        .catch(function () { /* the summary is already on screen */ });
    }
    function browserInput(options) {
      var input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      if (options.id) input.id = options.id;
      if (options.accept) input.accept = options.accept;
      if (options.directory) {
        input.webkitdirectory = true;
        input.setAttribute("directory", "");
        input.setAttribute("mozdirectory", "");
      }
      input.style.display = "none";
      document.body.appendChild(input);
      input.addEventListener("change", function () {
        var items = Array.prototype.slice.call(input.files).map(function (f) {
          return { file: f, member: f.webkitRelativePath || f.name };
        });
        input.value = "";
        if (items.length) handleFiles(items);
      });
      return input;
    }
    function collectFromDataTransfer(dt) {
      var items = [];
      var entries = [];
      if (dt.items && dt.items.length && dt.items[0].webkitGetAsEntry) {
        for (var i = 0; i < dt.items.length; i++) {
          var entry = dt.items[i].webkitGetAsEntry();
          if (entry) entries.push(entry);
        }
      }
      function walk(entry, prefix) {
        return new Promise(function (resolve) {
          if (entry.isFile) {
            entry.file(function (file) {
              items.push({ file: file, member: prefix + file.name });
              resolve();
            }, function () { resolve(); });
          } else if (entry.isDirectory) {
            var reader = entry.createReader();
            var all = [];
            function readBatch() {
              reader.readEntries(function (batch) {
                if (!batch.length) {
                  Promise.all(all.map(function (e) {
                    return walk(e, prefix + entry.name + "/");
                  })).then(resolve);
                  return;
                }
                all = all.concat(Array.prototype.slice.call(batch));
                readBatch();
              }, function () { resolve(); });
            }
            readBatch();
          } else resolve();
        });
      }
      if (entries.length) {
        return Promise.all(entries.map(function (e) { return walk(e, ""); }))
          .then(function () { return items; });
      }
      var files = Array.prototype.slice.call(dt.files || []);
      return Promise.resolve(files.map(function (f) { return { file: f, member: f.name }; }));
    }
    if (drop) {
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
      });
      drop.addEventListener("drop", function (e) {
        collectFromDataTransfer(e.dataTransfer).then(function (items) {
          if (items.length) handleFiles(items);
        });
      });
      drop.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          var b = document.getElementById("pick-files");
          if (b) b.click();
        }
      });
    }
    var filesInput = browserInput({ id: "import-input-files", accept: ".txt,.zip" });
    var zipInput = browserInput({ id: "import-input-zip", accept: ".zip,application/zip" });
    var folderInput = browserInput({ id: "import-input-folder", directory: true });
    var pickFiles = document.getElementById("pick-files");
    if (pickFiles) pickFiles.addEventListener("click", function () { filesInput.click(); });
    var pickZip = document.getElementById("pick-zip");
    if (pickZip) pickZip.addEventListener("click", function () { zipInput.click(); });
    var pickFolder = document.getElementById("pick-folder");
    if (pickFolder) pickFolder.addEventListener("click", function () { folderInput.click(); });
    // Optional fallback: a dialog owned by the local server process. It is
    // kept for browsers without folder uploads, and it says so when it no-ops.
    var pickWindows = document.getElementById("pick-windows");
    if (pickWindows) pickWindows.addEventListener("click", function () {
      if (importing) return;
      busy(true, "Waiting for the Windows dialog\u2026 if you do not see it, check the taskbar.",
           "warn");
      api("/api/import/path", { method: "POST", body: { pick: "folder" } })
        .then(function (res) {
          if (res.cancelled) {
            busy(false, "The Windows dialog returned nothing. Use the buttons above instead.",
                 "warn");
            return;
          }
          rememberImport(res, summarise(res), "");
          busy(false, state.lastImportStatus, "");
          showBatch(res);
          refreshState().then(function () { renderImport(); });
        })
        .catch(function (e) {
          busy(false, "Windows dialog unavailable (" + e.message + "). Use the buttons above.",
               "bad");
        });
    });
    var seed = document.getElementById("seed-demo");
    if (seed) seed.addEventListener("click", function () {
      api("/api/import/demo", { method: "POST", body: {} }).then(function (res) {
        if (res.ok) { toast("Synthetic demo data loaded"); refreshState().then(render); }
        else toast("Demo not loaded");
      });
    });
    var unlockAll = document.getElementById("unlock-all");
    if (unlockAll) unlockAll.addEventListener("click", function () {
      api("/api/key/unlock", { method: "POST", body: { all: true } })
        .then(function (res) {
          toast(res.ok ? "Decoded with the saved key" : "Needs a valid key");
          refreshState().then(function () { renderImport(); });
        });
    });
    document.querySelectorAll('[data-act="unlock"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/key/unlock", { method: "POST", body: { raw_id: Number(btn.dataset.raw) } })
          .then(function (res) {
            toast(res.ok ? "Decoded" : "Still waiting: " + (res.code || "key"));
            refreshState().then(function () { renderImport(); });
          });
      });
    });
    document.querySelectorAll('[data-act="merge"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        go("#/flights/" + btn.dataset.flight + "/details");
      });
    });
  }

  var IMPORT_REASONS = {
    zip_unsafe: "The ZIP was rejected before anything was imported: a member path would " +
      "escape the archive, or a member is not allowed (symlink, encrypted entry or bad path).",
    archive_size_bound: "The ZIP is larger than the safety limit, so it was rejected. " +
      "Nothing was imported from it.",
    zip_unreadable: "That file could not be read as a ZIP. If it is an .exe self-extractor, " +
      "extract it first, then use Choose folder.",
    no_eligible_files: "No DJI flight logs were found inside. Flight Desk only takes " +
      "FlightRecord_*.txt and DJIFlightRecord_*.txt, and it ignores .DAT, .json and " +
      "system files.",
    not_a_flight_log: "That file is not a DJI flight log. Pick a FlightRecord_*.txt, a " +
      "DJIFlightRecord_*.txt, or the handoff ZIP.",
    upload_interrupted: "The upload stopped before the whole file arrived. Nothing was " +
      "imported; try again.",
    body_too_large: "The file is larger than this build accepts in one upload.",
    write_failed: "The file could not be written to the local temp folder. Check free space.",
    disk_full: "There is not enough free disk space to archive the original. Nothing was " +
      "indexed for that file.",
    internal_error: "The local server hit an unexpected error. Nothing was half-imported; " +
      "check the import history and try again.",
    upload_failed: "The browser could not reach the local server. Is Flight Desk still running?",
    bad_filename: "The browser sent a file name the server could not read.",
    empty_body: "The browser sent an empty file.",
  };

  function renderBatchSummary(result) {
    if (!result) {
      return '<div class="empty" style="padding:14px 8px"><p class="small muted">' +
        "No batch yet. Choose the handoff ZIP (or a folder of logs) above; the result " +
        "appears here and stays while you move around the app.</p></div>";
    }
    var totals = result.totals || {};
    var labels = [["discovered", "Discovered"], ["new_flight", "New flights"],
                  ["new", "New files"], ["already_imported", "Already imported"],
                  ["awaiting_dji", "Awaiting DJI"], ["needs_attention", "Needs attention"]];
    var html = '<div class="result-grid">' + labels.map(function (pair) {
      return '<div class="result-cell"><div class="n">' + (totals[pair[0]] || 0) +
        '</div><div class="l">' + pair[1] + "</div></div>";
    }).join("") + "</div>";
    var note = result.note || result.code;
    var reason = note ? IMPORT_REASONS[note] : null;
    if (reason) {
      html += '<div class="notice bad small" style="margin-top:10px"><b>Nothing was imported ' +
        "from that file.</b> " + esc(reason) + "</div>";
    } else if (note) {
      html += '<div class="notice warn small" style="margin-top:10px">Import note: ' +
        esc(note) + "</div>";
    }
    var codes = result.codes || {};
    var failedFiles = result.failed_files || [];
    if (failedFiles.length) {
      html += '<div class="notice warn small" style="margin-top:10px">' +
        failedFiles.length + " file(s) could not be read: " +
        esc(failedFiles.slice(0, 4).join(", ")) +
        (failedFiles.length > 4 ? ", and more" : "") + "</div>";
    }
    if ((totals.unsupported || 0) > 0 || codes.unsupported) {
      html += '<div class="notice warn small" style="margin-top:10px">Some files use a format ' +
        "this adapter cannot decode. They are kept; nothing was counted as zero.</div>";
    }
    if ((totals.needs_attention || 0) > 0) {
      html += '<div class="notice warn small" style="margin-top:10px">Some files need ' +
        "attention. Retry from the import history after fixing the cause; a retry never " +
        "duplicates an accepted file.</div>";
    }
    if ((totals.new_flight || 0) > 0) {
      html += '<div class="row" style="margin-top:10px"><button type="button" ' +
        'class="primary" data-go="#/flights">Show the new flights</button></div>';
    }
    return html;
  }

  function rememberImport(result, status, tone) {
    state.lastImport = result || null;
    state.lastImportStatus = status || "";
    state.lastImportTone = tone || "";
    try {
      if (result) {
        sessionStorage.setItem("fd.lastImport", JSON.stringify(result));
        sessionStorage.setItem("fd.lastImportStatus", state.lastImportStatus);
        sessionStorage.setItem("fd.lastImportTone", state.lastImportTone);
      } else {
        sessionStorage.removeItem("fd.lastImport");
        sessionStorage.removeItem("fd.lastImportStatus");
        sessionStorage.removeItem("fd.lastImportTone");
      }
    } catch (err) { /* private mode: the summary simply does not persist */ }
  }

  function restoreImport() {
    try {
      var raw = sessionStorage.getItem("fd.lastImport");
      if (!raw) return;
      state.lastImport = JSON.parse(raw);
      state.lastImportStatus = sessionStorage.getItem("fd.lastImportStatus") || "";
      state.lastImportTone = sessionStorage.getItem("fd.lastImportTone") || "";
    } catch (err) {
      state.lastImport = null;
    }
  }

  function showBatch(result) {
    var host = document.getElementById("batch-result");
    if (!host) return;
    host.innerHTML = renderBatchSummary(result);
    bindGo(host);
  }

  function setImportStatus(text, tone) {
    var el = document.getElementById("import-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = tone === "bad" ? "var(--red)" : (tone === "warn" ? "var(--amber)" : "");
  }

  // --------------------------------------------------------------- settings

  function renderSettings() {
    api("/api/key").then(function (keyData) {
      api("/api/usb").then(function (usbData) {
        api("/api/backups").then(function (backups) {
          view.innerHTML = '<div class="stack" style="max-width:1000px">' +
            section("DJI key", '<p class="small muted">Encrypted logs (v13 and later) need a ' +
              "keychain from DJI's official OpenAPI. Flight Desk sends one request to " +
              "<code>" + esc(keyData.endpoint) + "</code> with your developer key, never to any " +
              "other service. The key is stored with Windows DPAPI for your user account, never " +
              "in the database, a URL, a process argument or a backup.</p>" +
              '<div class="row" style="margin-top:10px"><span class="badge ' +
              (keyData.configured ? "ok" : "unknown") + '">' +
              (keyData.configured ? "key stored" : "no key") + "</span>" +
              '<span class="tiny faint">cached keychains: ' + keyData.cache.cached +
              " · pending files: " + keyData.cache.pending + "</span></div>" +
              '<div class="row" style="margin-top:10px"><input type="password" id="key-input" ' +
              'placeholder="DJI developer key" style="max-width:320px" autocomplete="off">' +
              '<button type="button" class="primary" id="key-save">Save key</button>' +
              '<button type="button" class="secondary" id="key-clear">Remove key</button>' +
              '<button type="button" class="secondary" id="key-unlock-all">Decode pending</button></div>' +
              '<div class="row" style="margin-top:8px"><button type="button" class="secondary" ' +
              'id="key-reparse-cache">Re-decode one record from cache</button>' +
              '<span class="tiny faint">Use with the network off to prove the cached ' +
              "keychain is enough.</span></div>" +
              '<div id="key-reparse-out" class="small" style="margin-top:6px"></div>' +
              '<div class="notice small" style="margin-top:10px">Cached keychains are protected ' +
              "by DPAPI, so they survive app restarts on this machine but not a Windows " +
              "reinstall. After a reinstall, re-enter the key — or reauthorize a new app at " +
              "developer.dji.com if the old app was removed.</div>") +
            section("USB controllers", '<p class="small muted">Read-only. Flight Desk never ' +
              "writes to a controller. Both devices appear under portable devices in Explorer " +
              "without drive letters.</p>" +
              '<div class="row" style="margin-top:10px"><button type="button" class="primary" ' +
              'id="usb-check">Check controllers now</button>' +
              '<button type="button" class="secondary" id="usb-watch">' +
              (usbData.watcher.enabled ? "Disable watcher" : "Enable watcher") + "</button></div>" +
              '<div class="row" style="margin-top:8px"><label class="small"><input type="checkbox" ' +
              'id="usb-logon"' + (usbData.logon.installed ? " checked" : "") + "> Start at " +
              "logon (reversible per-user task)</label></div>" +
              '<div id="usb-result" class="small" style="margin-top:10px"></div>' +
              usbTable(usbData)) +
            section("Backup and export", '<p class="small muted">A backup is a consistent ' +
              "SQLite snapshot plus every archived original and a hash manifest. Keep it off " +
              "cloud-sync folders.</p>" +
              '<div class="row" style="margin-top:10px"><button type="button" class="primary" ' +
              'id="backup-now">Create backup…</button>' +
              '<button type="button" class="secondary" id="backup-validate">Validate a backup…</button>' +
              "</div>" +
              '<div class="field" style="margin-top:10px"><label for="backup-dest">Backup folder' +
              '</label><div class="row"><input id="backup-dest" type="text" ' +
              'placeholder="C:\\Users\\you\\FlightDeskBackups" style="flex:1;min-width:220px">' +
              '<button type="button" class="secondary" id="backup-pick">Choose folder…</button>' +
              "</div><p class=\"tiny faint\">Type the folder, or use Choose folder. Leave it " +
              "empty to be asked.</p></div>" +
              '<div class="row" style="margin-top:10px">' +
              exportButton("Flights CSV", "/api/export/flights.csv", "FlightDesk-flights.csv") +
              exportButton("Equipment CSV", "/api/export/equipment.csv",
                "FlightDesk-equipment.csv") +
              exportButton("Services CSV", "/api/export/services.csv",
                "FlightDesk-services.csv") +
              exportButton("Flights (no locations or serials)",
                "/api/export/flights.csv?redact=1", "FlightDesk-flights-redacted.csv") +
              "</div>" +
              (backups.rows.length ? '<table class="data" style="margin-top:12px"><thead><tr>' +
                "<th>Created</th><th>Status</th><th>Destination</th></tr></thead><tbody>" +
                backups.rows.map(function (b) {
                  return "<tr><td>" + esc(dateOnly(b.created_at)) + "</td><td>" +
                    esc(b.status) + "</td><td class=\"tiny faint\">" +
                    esc(b.destination.split("\\").slice(-1)[0]) + "</td></tr>";
                }).join("") + "</tbody></table>" : "") +
              '<div class="notice small" style="margin-top:12px">Restore always extracts into a ' +
              "new local data root first and validates checksums and foreign keys before " +
              "opening. Switching the live root needs explicit confirmation and keeps the " +
              "previous copy.</div>" +
              '<div class="row" style="margin-top:10px"><button type="button" class="secondary" ' +
              'id="restore-run">Restore into a new root…</button></div>') +
            section("Reconciliation", '<p class="small muted">Archive, controller extras and ' +
              "later discoveries are counted separately, with signed differences and excluded " +
              "files reported next to totals.</p>" +
              '<div class="row" style="margin-top:10px"><button type="button" class="secondary" ' +
              'id="recon-run">Generate report</button>' +
              exportButton("Reconciliation CSV", "/api/export/recon.csv",
                "FlightDesk-reconciliation.csv") + "</div>" +
              '<div id="recon-out" style="margin-top:12px"></div>') +
            section("Equipment capture", '<p class="small muted">Parse an equipment capture ' +
              "(markdown headings and pipe tables). Preview first: it shows what is individually " +
              "resolved and what stays grouped.</p>" +
              '<div class="field" style="margin-top:10px"><label for="capture-path">Capture file</label>' +
              '<div class="row"><input id="capture-path" type="text" readonly ' +
              'placeholder="No file chosen" style="flex:1;min-width:220px">' +
              '<button type="button" class="secondary" id="capture-choose">Choose file…</button>' +
              '</div></div>' +
              '<div class="row" style="margin-top:10px">' +
              '<button type="button" class="secondary" id="capture-preview">Preview</button>' +
              '<button type="button" class="primary" id="capture-apply" disabled>Apply previewed ' +
              'capture</button></div>' +
              '<details style="margin-top:10px"><summary class="small muted">Or paste the ' +
              'capture text</summary>' +
              '<div class="field" style="margin-top:8px"><label for="capture-text">Capture text' +
              '</label><textarea id="capture-text" rows="8" ' +
              'placeholder="Paste the markdown capture here"></textarea></div>' +
              '<div class="row" style="margin-top:6px"><button type="button" class="secondary" ' +
              'id="capture-preview-text">Preview pasted text</button></div></details>' +
              '<div id="capture-out" style="margin-top:12px"></div>') +
            section("Privacy and data root", '<p class="small muted">Data root: <code>' +
              esc(state.app.db_path.replace(/\\[^\\]+$/, "")) + "</code></p>" +
              '<p class="small muted">Privacy display hides serials, coordinates and blurs the ' +
              "track for screen sharing. It changes presentation only; records stay local.</p>" +
              '<div class="row" style="margin-top:8px"><button type="button" class="secondary" ' +
              'id="real-toggle">' + (state.app.real_inputs_selected
                ? "Mark records as synthetic" : "Mark records as my real inputs") + "</button>" +
              '<a class="secondary" style="padding:6px 12px;border:1px solid var(--line-strong);' +
              'border-radius:4px" href="#/compare">Local reference comparison</a></div>') +
            "</div>";
          wireSettings();
          wireExports(view);
          bindAct(view);
        });
      });
    });
  }

  function section(title, body) {
    return '<div class="panel"><div class="panel-head"><h2>' + esc(title) + "</h2></div>" +
      '<div class="panel-body">' + body + "</div></div>";
  }

  function usbTable(data) {
    if (!data.devices.length) {
      return '<div class="notice small" style="margin-top:10px">No controller has been seen yet. ' +
        "Plug one in, unlock it, choose file transfer, then use Check controllers now.</div>";
    }
    return '<table class="data" style="margin-top:10px"><thead><tr><th>Device</th><th>Last seen</th>' +
      "<th>Last success</th><th>Status</th></tr></thead><tbody>" + data.devices.map(function (d) {
        return "<tr><td>" + esc(d.device) + "</td><td>" + esc(dateOnly(d.last_seen)) +
          "</td><td>" + (d.last_success ? esc(dateOnly(d.last_success)) : "never") +
          "</td><td>" + esc(d.status || "—") + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function wireSettings() {
    var save = document.getElementById("key-save");
    if (save) save.addEventListener("click", function () {
      var input = document.getElementById("key-input");
      api("/api/key", { method: "POST", body: { key: input.value } }).then(function (res) {
        toast(res.ok ? "Key stored with Windows protection" : "Key rejected: " + res.code);
        input.value = "";
        refreshState().then(renderSettings);
      });
    });
    var clear = document.getElementById("key-clear");
    if (clear) clear.addEventListener("click", function () {
      api("/api/key", { method: "DELETE" }).then(function () {
        toast("Key removed"); refreshState().then(renderSettings);
      });
    });
    var unlock = document.getElementById("key-unlock-all");
    if (unlock) unlock.addEventListener("click", function () {
      api("/api/key/unlock", { method: "POST", body: { all: true } }).then(function (res) {
        toast(res.ok ? "Decoded pending logs" : "Could not decode: " + (res.results[0] || {}).code);
        refreshState().then(renderSettings);
      });
    });
    var reparseOne = document.getElementById("key-reparse-cache");
    if (reparseOne) reparseOne.addEventListener("click", function () {
      var out = document.getElementById("key-reparse-out");
      if (out) out.textContent = "Re-decoding from cache…";
      api("/api/reparse", { method: "POST", body: { scope: "cached" } })
        .then(function (res) {
          var text = res.ok
            ? "Re-decoded a v" + res.version + " record from its cached keychain " +
              "(no network needed)."
            : (res.code === "no_cached_keychain"
                ? "No cached keychain yet \u2014 decode a pending record first."
                : "Could not re-decode: " + (res.code || res.status));
          toast(text);
          if (out) out.textContent = text;
        });
    });
    var check = document.getElementById("usb-check");
    if (check) check.addEventListener("click", function () {
      var out = document.getElementById("usb-result");
      out.textContent = "Checking controllers…";
      api("/api/usb/scan", { method: "POST", body: {} }).then(function (res) {
        out.innerHTML = '<div class="notice small">' + esc(res.status) + " · scanned " +
          res.scanned + " · new " + res.imported + " · duplicates " + res.duplicates +
          " · attention " + res.attention + "</div>";
        refreshState().then(renderSettings);
      }).catch(function (e) { out.textContent = "Scan failed: " + e.message; });
    });
    var watch = document.getElementById("usb-watch");
    if (watch) watch.addEventListener("click", function () {
      api("/api/usb").then(function (data) {
        api("/api/usb/watcher", { method: "POST",
          body: { enabled: !data.watcher.enabled } }).then(function () {
          refreshState().then(renderSettings);
        });
      });
    });
    var logon = document.getElementById("usb-logon");
    if (logon) logon.addEventListener("change", function () {
      if (!logon.checked) {
        api("/api/usb/logon", { method: "POST", body: { install: false, confirm: true } })
          .then(function () { toast("Logon task removed"); refreshState().then(renderSettings); });
        return;
      }
      openModal("Start at logon?",
        "<p class=\"small\">This installs one per-user scheduled task named " +
        "<b>FlightDesk USB Watch</b> so a plugged-in controller is seen while the browser is " +
        "closed. Nothing else runs. You can remove it here at any time.</p>",
        [{ label: "Cancel", cls: "secondary", action: function () {
            closeModal(); refreshState().then(renderSettings); } },
         { label: "Install task", cls: "primary", action: function () {
            return api("/api/usb/logon", { method: "POST", body: { install: true, confirm: true } })
              .then(function (res) {
                toast(res.ok ? "Logon task installed" : "Task install failed: " + res.code);
                closeModal(); refreshState().then(renderSettings);
              }); } }]);
    });
    var backupPick = document.getElementById("backup-pick");
    if (backupPick) backupPick.addEventListener("click", function () {
      api("/api/pick", { method: "POST", body: { kind: "folder" } }).then(function (res) {
        if (res.cancelled) return;
        var input = document.getElementById("backup-dest");
        if (input && res.path) input.value = res.path;
        if (!res.path) toast("No folder chosen \u2014 type the path instead");
      });
    });
    var backupNow = document.getElementById("backup-now");
    if (backupNow) backupNow.addEventListener("click", function () {
      var typed = (document.getElementById("backup-dest") || {}).value || "";
      api("/api/backup", { method: "POST", body: typed.trim() ? { destination: typed.trim() } : {} })
        .then(function (res) {
        if (res.cancelled) return;
        if (res.ok) toast("Backup written · " + res.counts.raw_files + " originals verified");
        else if (res.code === "cloud_sync_destination") {
          openModal("Cloud-sync folder",
            "<p class=\"small\">This destination looks like a cloud-sync folder. A live flight " +
            "database should not sit on a synced share. Choose another folder, or allow it here.</p>",
            [{ label: "Choose again", cls: "secondary", action: function () { closeModal(); } },
             { label: "Allow this time", cls: "primary", action: function () {
                return api("/api/backup", { method: "POST", body: { destination: res.path || "",
                  allow_cloud: true } }).then(function () { closeModal(); renderSettings(); }); } }]);
        } else toast("Backup failed: " + res.code);
        refreshState().then(renderSettings);
      });
    });
    var validate = document.getElementById("backup-validate");
    if (validate) validate.addEventListener("click", function () {
      openModal("Validate a backup",
        '<p class="small">Type or choose the backup ZIP. Nothing is restored or changed.</p>' +
        '<div class="field"><label for="validate-path">Backup file</label>' +
        '<div class="row"><input id="validate-path" type="text" placeholder="C:\\Users\\you\\FlightDeskBackups\\FlightDesk-backup-....zip" style="flex:1;min-width:220px">' +
        '<button type="button" class="secondary" id="validate-pick">Choose file…</button></div></div>',
        [{ label: "Cancel", cls: "secondary" },
         { label: "Validate", cls: "primary", action: function () {
            var path = (document.getElementById("validate-path") || {}).value || "";
            return api("/api/backup/validate", { method: "POST", body: path.trim()
                ? { path: path.trim() }
                : {} }).then(function (res) {
              if (res.cancelled) return;
              if (res.ok) {
                toast("Backup validates · " + (res.counts.raw_files || 0) + " originals · schema " +
                  res.schema_version);
              } else {
                openModal("Backup failed validation",
                  '<p class="small">' + esc((res.errors || []).join(", ")) + "</p>",
                  [{ label: "Close", cls: "secondary" }]);
              }
            }); } }]);
      var vpick = document.getElementById("validate-pick");
      if (vpick) vpick.addEventListener("click", function () {
        api("/api/pick", { method: "POST", body: { kind: "file" } }).then(function (res) {
          if (res.cancelled) return;
          var input = document.getElementById("validate-path");
          if (input && res.path) input.value = res.path;
          if (!res.path) toast("No file chosen \u2014 type the path instead");
        });
      });
    });
    var restore = document.getElementById("restore-run");
    if (restore) restore.addEventListener("click", function () {
      openModal("Restore into a new data root",
        '<p class="small">Type or choose the backup ZIP, then a new empty folder. The live ' +
        "data root is never used for this rehearsal.</p>" +
        '<div class="field"><label for="restore-source">Backup file</label>' +
        '<div class="row"><input id="restore-source" type="text" placeholder="C:\\Users\\you\\FlightDeskBackups\\FlightDesk-backup-....zip" style="flex:1;min-width:220px">' +
        '<button type="button" class="secondary" id="restore-pick">Choose file…</button></div></div>' +
        '<div class="field"><label for="restore-target">New data root</label>' +
        '<input id="restore-target" type="text" placeholder="C:\\Users\\you\\FlightDeskRestored">' +
        '<div class="row" style="margin-top:6px"><button type="button" class="secondary" ' +
        'id="restore-target-pick">Choose folder…</button></div></div>',
        [{ label: "Cancel", cls: "secondary" },
         { label: "Choose backup and restore", cls: "primary", action: function () {
            var target = document.getElementById("restore-target").value;
            var source = (document.getElementById("restore-source") || {}).value || "";
            return api("/api/backup/restore", { method: "POST", body: source.trim()
                ? { path: source.trim(), target: target }
                : { target: target } })
              .then(function (res) {
                if (res.cancelled) return;
                if (res.ok) {
                  toast("Restored and validated · opening the copy needs one more step");
                  openModal("Restored copy",
                    "<p class=\"small\">The restore validated: hashes, integrity and foreign " +
                    "keys all passed. Flight Desk has not switched to it.</p>" +
                    "<p class=\"small muted\">Switching only changes a pointer; your current " +
                    "data root is left exactly as it is, so you can switch back.</p>",
                    [{ label: "Keep current data", cls: "secondary", action: function () {
                        closeModal(); } },
                     { label: "Use restored copy on restart", cls: "primary",
                       action: function () {
                         return api("/api/backup/activate", { method: "POST", body: {
                           target: target, confirm: true } }).then(function (out) {
                           toast(out.ok ? "Restart Flight Desk to open the restored copy"
                                        : "Could not switch: " + out.code);
                           closeModal();
                         }); } }]);
                } else {
                  toast("Restore failed: " + (res.code || (res.validation || {}).errors));
                }
              }); } }]);
      var pick = document.getElementById("restore-pick");
      if (pick) pick.addEventListener("click", function () {
        api("/api/pick", { method: "POST", body: { kind: "file" } }).then(function (res) {
          if (res.cancelled) return;
          var input = document.getElementById("restore-source");
          if (input && res.path) input.value = res.path;
          if (!res.path) toast("No file chosen \u2014 type the path instead");
        });
      });
      var targetPick = document.getElementById("restore-target-pick");
      if (targetPick) targetPick.addEventListener("click", function () {
        api("/api/pick", { method: "POST", body: { kind: "folder" } }).then(function (res) {
          if (res.cancelled) return;
          var input = document.getElementById("restore-target");
          if (input && res.path) input.value = res.path;
          if (!res.path) toast("No folder chosen \u2014 type the path instead");
        });
      });
    });
    var recon = document.getElementById("recon-run");
    if (recon) recon.addEventListener("click", function () {
      api("/api/recon").then(function (data) {
        var groups = data.groups;
        document.getElementById("recon-out").innerHTML =
          '<table class="data"><thead><tr><th>View</th><th class="num">Files</th>' +
          '<th class="num">Flights</th><th class="num">Air</th><th class="num">Miles</th>' +
          '<th class="num">Photos</th><th class="num">Decoded</th></tr></thead><tbody>' +
          ["archive", "supplied_controller", "later_controller", "combined"].map(function (k) {
            var g = groups[k];
            return "<tr><td>" + esc(k.replace("_", " ")) + '</td><td class="num">' +
              g.raw_files + '</td><td class="num">' + g.logical_flights +
              '</td><td class="num">' + g.air_time + '</td><td class="num">' +
              g.miles.toFixed(1) + '</td><td class="num">' +
              (g.photos || "—") + '</td><td class="num">' +
              (g.decoded_coverage * 100).toFixed(0) + "%</td></tr>";
          }).join("") + "</tbody></table>" +
          // A partial total must not read as a complete one.
          (groups.archive && groups.archive.distance_records_unknown
            ? '<div class="notice small" style="margin-top:10px">Distance is known for ' +
              groups.archive.distance_records_known + " of " +
              groups.archive.logical_flights + " records; " +
              groups.archive.distance_records_unknown +
              " await the DJI key, so the miles above are a partial total.</div>"
            : "") +
          (data.real_inputs_selected
            ? '<div class="notice small" style="margin-top:10px">Signed differences vs the ' +
              "reference: " + Object.keys(data.signed_differences || {}).map(function (k) {
                return esc(k) + " " + (data.signed_differences[k] > 0 ? "+" : "") +
                  data.signed_differences[k];
              }).join(" · ") + "</div>"
            : '<div class="notice small" style="margin-top:10px">Synthetic data: no comparison ' +
              "with the real archive is claimed. Select your real inputs to enable it.</div>") +
          '<div class="notice small" style="margin-top:10px">Excluded: ' +
          Object.keys(data.excluded).map(function (k) {
            return esc(k) + " " + data.excluded[k];
          }).join(" · ") + "</div>";
      });
    });
    var captureFile = "";
    var capturePreviewed = false;
    var captureInput = document.createElement("input");
    captureInput.type = "file";
    captureInput.id = "capture-input";
    captureInput.accept = ".md,.markdown,.txt";
    captureInput.style.display = "none";
    document.body.appendChild(captureInput);
    var captureChoose = document.getElementById("capture-choose");
    if (captureChoose) captureChoose.addEventListener("click", function () {
      captureInput.click();
    });
    captureInput.addEventListener("change", function () {
      var file = captureInput.files && captureInput.files[0];
      var pathBox = document.getElementById("capture-path");
      if (!file) return;
      captureFile = file.name;
      capturePreviewed = false;
      if (pathBox) pathBox.value = file.name + " (" + Math.max(1, Math.round(file.size / 1024)) +
        " KB)";
      var applyBtn = document.getElementById("capture-apply");
      if (applyBtn) applyBtn.disabled = true;
      setCaptureOut("File chosen. Press Preview to check what will be imported.");
    });
    function setCaptureOut(html, tone) {
      var out = document.getElementById("capture-out");
      if (!out) return;
      out.innerHTML = tone === "bad"
        ? '<div class="notice bad small">' + esc(html) + "</div>"
        : (tone === "raw" ? html : '<div class="notice small">' + esc(html) + "</div>");
    }
    async function readCaptureText() {
      var pasted = document.getElementById("capture-text");
      if (pasted && pasted.value.trim()) return pasted.value;
      if (!captureFile) return null;
      var file = captureInput.files && captureInput.files[0];
      if (!file) return null;
      return await file.text();
    }
    async function previewCapture() {
      var text = await readCaptureText();
      if (text === null) {
        setCaptureOut("Choose the capture file first, or paste its text.", "bad");
        return;
      }
      setCaptureOut("Reading the capture…");
      try {
        var res = await api("/api/capture/preview", { method: "POST", body: { text: text } });
        if (!res.ok) { setCaptureOut("Could not read that capture: " + res.code, "bad"); return; }
        capturePreviewed = true;
        var applyBtn = document.getElementById("capture-apply");
        if (applyBtn) applyBtn.disabled = false;
        var rows = [
          [res.current_aircraft, "current aircraft"],
          [res.historical_aircraft, "historical aircraft (archived)"],
          [res.batteries, "battery entries"],
          [res.placeholders, "placeholders"],
          [res.service_events, "historical service rows"],
          [res.resolved_services, "individually resolved"],
          [res.grouped_services, "kept as groups"],
        ];
        var html = '<div class="result-grid">' + rows.map(function (pair) {
          return '<div class="result-cell"><div class="n">' + pair[0] +
            '</div><div class="l">' + pair[1] + "</div></div>";
        }).join("") + "</div>";
        if (res.already_applied) {
          html += '<div class="notice small" style="margin-top:10px">This exact capture is ' +
            "already applied. Applying again would change nothing.</div>";
        }
        if (res.warnings && res.warnings.length) {
          html += '<div class="notice warn small" style="margin-top:10px">' +
            esc(res.warnings.join(" \u00b7 ")) + "</div>";
        }
        html += '<div class="notice small" style="margin-top:10px">Grouped rows keep the ' +
          "capture's wording and are never split into invented per-item records.</div>";
        setCaptureOut(html, "raw");
      } catch (err) {
        setCaptureOut("Preview failed: " + err.message, "bad");
      }
    }
    var capPrev = document.getElementById("capture-preview");
    if (capPrev) capPrev.addEventListener("click", previewCapture);
    var capPrevText = document.getElementById("capture-preview-text");
    if (capPrevText) capPrevText.addEventListener("click", previewCapture);
    var capApply = document.getElementById("capture-apply");
    if (capApply) capApply.addEventListener("click", async function () {
      if (!capturePreviewed) {
        setCaptureOut("Preview the capture before applying it.", "bad");
        return;
      }
      var text = await readCaptureText();
      if (text === null) return;
      setCaptureOut("Applying…");
      try {
        var res = await api("/api/capture/apply",
          { method: "POST", body: { text: text, label: captureFile || "Pasted capture" } });
        if (res.already_applied) {
          setCaptureOut("Already applied; nothing changed.");
        } else {
          setCaptureOut("Applied: " + res.equipment_created + " equipment, " +
            res.rules_created + " rules, " + res.services_inserted + " services (" +
            res.grouped_services + " kept grouped).");
        }
        refreshState().then(function () { renderSettings(); });
      } catch (err) {
        setCaptureOut("Apply failed: " + err.message, "bad");
      }
    });
    var real = document.getElementById("real-toggle");
    if (real) real.addEventListener("click", function () {
      api("/api/recon/select-real", { method: "POST",
        body: { selected: !state.app.real_inputs_selected } })
        .then(function () { refreshState().then(renderSettings); });
    });
  }

  // ------------------------------------------------------------- compare view

  function renderCompare() {
    view.innerHTML = '<div class="panel"><div class="panel-head"><h2>Local reference comparison</h2>' +
      '<span class="tiny faint">1275 × 850 recommended</span></div>' +
      '<div class="panel-body">' +
      '<p class="small muted">Pair a local reference image with the live app at the same aspect ' +
      "ratio. Both sides stay on this machine: the image is served only to this authenticated " +
      "loopback page, and nothing is uploaded or logged.</p>" +
      '<div class="field"><label for="ref-path">Local reference image path</label>' +
      '<input id="ref-path" type="text" placeholder="C:\\Users\\you\\Pictures\\reference-01.png">' +
      '</div>' +
      '<div class="row" style="margin-top:10px"><label class="small"><input type="checkbox" ' +
      'id="ref-fit" checked> Fit both sides to this width</label>' +
      '<button type="button" class="secondary" id="ref-load">Load reference</button></div>' +
      '<div id="ref-out" style="margin-top:14px"></div></div></div>';
    var btn = document.getElementById("ref-load");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var path = document.getElementById("ref-path").value.trim();
      var out = document.getElementById("ref-out");
      if (!path) {
        out.innerHTML = '<div class="notice small">Enter the full path of a reference image. ' +
          "Flight Desk never scans folders for images.</div>";
        return;
      }
      var src = "/api/local-image?path=" + encodeURIComponent(path);
      out.innerHTML = '<div class="cards-2" style="align-items:start">' +
        '<div><h3 style="margin-bottom:6px">Reference</h3>' +
        '<div style="border:1px solid var(--line);border-radius:8px;overflow:hidden;' +
        'background:#fff"><img id="ref-img" alt="Local reference screenshot" ' +
        'style="display:block;width:100%;height:auto" src="' + src + '"></div>' +
        '<p class="tiny faint" id="ref-status">Loading reference…</p></div>' +
        '<div><h3 style="margin-bottom:6px">Live app (synthetic data)</h3>' +
        '<div style="border:1px solid var(--line);border-radius:8px;overflow:hidden;' +
        'background:#fff;aspect-ratio:3/2"><iframe id="ref-live" src="#/flights" ' +
        'title="Live app rendering" style="width:100%;height:100%;border:0"></iframe></div>' +
        '<p class="tiny faint">Compare navigation, spacing and chart forms, not the invented ' +
        "values.</p></div></div>";
      var img = document.getElementById("ref-img");
      var status = document.getElementById("ref-status");
      // The image endpoint also needs the session header, so fetch it as a blob.
      fetch(src, { headers: { "X-FD-Token": TOKEN }, credentials: "same-origin" })
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          img.addEventListener("load", function () {
            status.textContent = "Reference loaded locally · " + img.naturalWidth + " × " +
              img.naturalHeight + " px";
          });
          img.src = url;
        })
        .catch(function () {
          status.textContent = "Could not read that image path. Check the file name and type.";
          img.remove();
        });
    });
  }

  // ----------------------------------------------------------------- modal

  function openModal(title, html, buttons) {
    modalEl.innerHTML = '<div class="modal-card" role="dialog" aria-modal="true" ' +
      'aria-label="' + esc(title) + '"><div class="panel-head"><h2>' + esc(title) +
      '</h2><button type="button" class="link" data-close="1">Close</button></div>' +
      '<div class="panel-body">' + html + '<div class="row" style="margin-top:14px;' +
      'justify-content:flex-end" id="modal-actions"></div></div></div>';
    modalEl.hidden = false;
    var actions = document.getElementById("modal-actions");
    (buttons || []).forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = b.cls === "primary" ? "primary" : "secondary";
      btn.textContent = b.label;
      btn.addEventListener("click", function () {
        if (b.action) {
          var result = b.action();
          if (result && result.catch) result.catch(function (e) { toast(e.message); });
        } else closeModal();
      });
      actions.appendChild(btn);
    });
    modalEl.querySelector("[data-close]").addEventListener("click", closeModal);
    modalEl.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });
    var focusable = modalEl.querySelector("input, textarea, button");
    if (focusable) focusable.focus();
  }

  function closeModal() {
    modalEl.hidden = true;
    modalEl.innerHTML = "";
  }

  function openNote(title, hint, save) {
    openModal(title, '<p class="small muted">' + esc(hint) + "</p>" +
      '<div class="field"><label for="note-text">Note</label>' +
      '<textarea id="note-text" placeholder="What was done, or why it was skipped"></textarea></div>' +
      '<div class="field"><label for="note-date">Date</label>' +
      '<input id="note-date" type="date" value="' + new Date().toISOString().slice(0, 10) + '"></div>',
      [{ label: "Cancel", cls: "secondary" },
       { label: "Save", cls: "primary", action: function () {
         var note = document.getElementById("note-text").value;
         var at = document.getElementById("note-date").value;
         return save(note, at).then(function () { closeModal(); })
           .catch(function (e) { toast("Could not save: " + e.message); });
       } }]);
    save._dateField = true;
  }

  // ----------------------------------------------------------- misc binding

  function bindAct(root) {
    root.querySelectorAll("[data-mobile]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.mobileView = btn.dataset.mobile;
        document.body.dataset.mobileView = state.mobileView;
      });
    });
  }

  function bindMobileSwitch() { bindAct(view); }

  // ------------------------------------------------------------------ boot

  document.getElementById("unit-toggle").addEventListener("click", function () {
    var next = isMetric() ? "imperial" : "metric";
    api("/api/settings", { method: "POST", body: { unit_preference: next } })
      .then(function () { return refreshState(); }).then(render);
  });

  document.getElementById("privacy-toggle").addEventListener("click", function () {
    api("/api/settings", { method: "POST", body: { privacy_mode: !state.app.privacy } })
      .then(function () { return refreshState(); }).then(function () {
        toast(state.app.privacy ? "Privacy display on" : "Privacy display off");
        render();
      });
  });

  document.addEventListener("keydown", function (e) {
    if (e.target.matches("input, textarea, select")) return;
    if (modalEl.hidden === false) return;
    var parts = parseHash().parts;
    if (parts[0] !== "flights" || !parts[1] || !state.flight) return;
    if (e.key === "ArrowLeft" && state.flight.neighbours.prev) {
      e.preventDefault();
      go("#/flights/" + state.flight.neighbours.prev + "/" + (parts[2] || "overview"));
    }
    if (e.key === "ArrowRight" && state.flight.neighbours.next) {
      e.preventDefault();
      go("#/flights/" + state.flight.neighbours.next + "/" + (parts[2] || "overview"));
    }
  });

  window.addEventListener("hashchange", render);
  window.addEventListener("resize", function () {
    if (parseHash().parts[0] === "flights" && parseHash().parts[1] && state.section === "overview") {
      // charts redraw at their own viewBox scale; nothing to recompute
    }
  });

  restoreImport();

  refreshState().then(function () {
    var bootParts = parseHash().parts;
    var atFlights = bootParts.length === 0 ||
      (bootParts[0] === "flights" && !bootParts[1]);
    // A deep link (for example #/import) is an explicit request: render it
    // rather than replacing it with the first-run welcome panel.
    if (!state.app.flights && !state.app.demo_seeded && atFlights) {
      // first run: offer the synthetic demo rather than an empty screen
      view.innerHTML = '<div class="panel" style="max-width:720px;margin:40px auto">' +
        '<div class="panel-head"><h2>Welcome to Flight Desk</h2></div>' +
        '<div class="panel-body"><p>This is a local, offline-first replacement for the AirData ' +
        "screens you use. No records have been imported yet.</p>" +
        '<div class="row" style="margin-top:12px"><button type="button" class="primary" ' +
        'data-go="#/import">Import my logs</button>' +
        '<button type="button" class="secondary" id="welcome-demo">Explore invented demo data' +
        "</button></div>" +
        '<p class="tiny faint" style="margin-top:14px">Demo data is synthetic and labelled as ' +
        "such. Your own files are read only by local code; the assistant never sees them.</p>" +
        "</div></div>";
      bindGo(view);
      var demoBtn = document.getElementById("welcome-demo");
      if (demoBtn) demoBtn.addEventListener("click", function () {
        api("/api/import/demo", { method: "POST", body: {} }).then(function () {
          refreshState().then(function () { location.hash = "#/flights"; render(); });
        });
      });
      return;
    }
    render();
  });
})();
