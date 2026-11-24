// Local UI verification: drives the running Flight Desk app through Edge's
// DevTools protocol. Prints one PASS/FAIL line per check and writes screenshots.
// Everything stays on this machine.
//   node tools/ui_check.mjs [port] [outdir] [zip] [app-url]
//
// `app-url` is optional. Without it the gate uses whatever loopback page the
// browser is already on, and refuses to run if there is none. It used to fall
// back to the first page it saw, which could be `about:blank`: the derived
// origin was then the string "null", every navigation went to `null/`, and the
// run printed ~70 failures that described an error page rather than the app.
import { writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join as joinPath } from "node:path";

const argv = process.argv.slice(2);
const flags = new Set(argv.filter((a) => a.startsWith("--")));
const positional = argv.filter((a) => !a.startsWith("--"));
const debugPort = positional[0] || "9222";
const outDir = positional[1] || "ui-shots";
const zipPath = positional[2] && positional[2] !== "-" ? positional[2] : "";
const appUrlArg = positional[3] || "";
// `--import-only` runs just the import section (an ordinary root that holds real
// logs is the only place a ZIP may land now that demo roots refuse them).
// `--expect-refusal` asserts the opposite: a demo root must refuse the ZIP.
// `--ordinary` marks a non-demo root, where demo-only checks are skipped.
const importOnly = flags.has("--import-only");
const expectRefusal = flags.has("--expect-refusal");
const ordinaryRoot = flags.has("--ordinary");
const identityOnly = flags.has("--identity-only");
mkdirSync(outDir, { recursive: true });

const isLoopbackApp = (url) => /^http:\/\/127\.0\.0\.1(:\d+)?\//.test(url || "");

const targets = await fetch(`http://127.0.0.1:${debugPort}/json`).then((r) => r.json());
// A fresh Edge profile can expose its own pages (sync confirmation, new tab)
// beside the app. Attach to the loopback app page, never to the first page that
// happens to be listed, or every check silently fails.
const pages = targets.filter((t) => t.type === "page");
const page = pages.find((t) => isLoopbackApp(t.url));
if (!page && !appUrlArg) {
  console.error("FAIL no loopback app page target (saw: " +
    pages.map((t) => t.url || "(no url)").join(", ") + ")");
  console.error("Open the app in the browser you are debugging, or pass its URL " +
    "as the fifth argument: node tools/ui_check.mjs <port> <outdir> <zip> " +
    "http://127.0.0.1:<app-port>/");
  process.exit(2);
}
const pageTarget = page || pages[0];
if (!pageTarget) {
  console.error("FAIL the debugger exposes no page target at all");
  process.exit(2);
}
const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
let nextId = 1;
const pending = new Map();
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
});
function call(method, params = {}) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}
await new Promise((resolve) => ws.addEventListener("open", resolve));
await call("Page.enable");
await call("Runtime.enable");
await call("Network.enable");

// Every request the page makes must stay on loopback: the offline proof.
const externalRequests = [];
// Tile requests are counted separately: with consent off there must be none.
const tileRequests = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.method === "Network.requestWillBeSent") {
    const url = msg.params?.request?.url || "";
    if (url.includes("/api/tiles/")) tileRequests.push(url.slice(0, 80));
    if (!/^(http:\/\/127\.0\.0\.1|http:\/\/localhost|data:|about:)/.test(url)) {
      externalRequests.push(url.slice(0, 80));
    }
  }
});

const failures = [];
const evaluateErrors = [];
let total = 0;
function check(name, condition, detail = "") {
  total += 1;
  const ok = Boolean(condition);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? " :: " + detail : ""}`);
  if (!ok) failures.push(name);
}
function skip(name, why) {
  console.log(`SKIP ${name} :: ${why}`);
}
// A check whose subject only exists in a demo root: report it as skipped, with
// the reason, rather than as a pass it did not earn or a failure it did not cause.
function demoCheck(name, condition, detail = "") {
  if (ordinaryRoot) {
    skip(name, "needs a demo root (--ordinary run)");
    return;
  }
  check(name, condition, detail);
}
async function evaluate(expression) {
  const res = await call("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (res.result?.exceptionDetails) {
    // Returning a marker object here used to crash the run later, on
    // `.slice()` of the marker, which hid every check after it. A failed
    // evaluation is a failed check: return null, record why, keep going.
    const text = res.result.exceptionDetails.exception?.description ||
      res.result.exceptionDetails.text || "evaluate failed";
    evaluateErrors.push(text.split("\n")[0].slice(0, 160));
    return null;
  }
  return res.result?.result?.value;
}
function text(value) {
  return value === null || value === undefined ? "" : String(value);
}
async function waitFor(expression, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(expression)) return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}
async function shot(name, width, height) {
  await call("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: width < 600,
  });
  await new Promise((r) => setTimeout(r, 700));
  const res = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  const file = `${outDir}/${name}.png`;
  writeFileSync(file, Buffer.from(res.result.data, "base64"));
  return file;
}
async function goto(hash) {
  // Setting the hash to the value it already holds fires no hashchange, so a
  // second goto to the same route left the old DOM in place: a check then
  // inspected the page as it was before the action it was testing. Measured by
  // the Q05 check, which re-navigated to #/flights after an upload.
  await evaluate(`(() => {
    const target = ${JSON.stringify(hash)};
    if (location.hash === target) {
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    } else {
      location.hash = target;
    }
    return true;
  })()`);
  await new Promise((r) => setTimeout(r, 900));
}

// Drive whichever app instance the browser is already on. The port is not
// fixed: a scratch root runs on its own port, and hardcoding one silently
// pointed every check at a dead origin. An explicit URL wins when one is given.
const appOrigin = appUrlArg
  ? new URL(appUrlArg).origin
  : new URL(pageTarget.url).origin;
if (!/^http:\/\/127\.0\.0\.1(:\d+)?$/.test(appOrigin)) {
  console.error(`FAIL could not resolve a loopback app origin (got ${JSON.stringify(appOrigin)} ` +
    `from ${JSON.stringify(pageTarget.url)}); pass the app URL as the fifth argument`);
  process.exit(2);
}

// --- desktop dashboard -----------------------------------------------------
if (!importOnly && !identityOnly) {
await call("Emulation.setDeviceMetricsOverride", {
  width: 1536, height: 1000, deviceScaleFactor: 1, mobile: false,
});
await call("Page.navigate", { url: `${appOrigin}/` });
await waitFor("!!document.querySelector('.flight-list li a')", 20000);
check("dashboard renders flight rows",
  await evaluate("document.querySelectorAll('.flight-list li a').length > 0"));
// A full root must open on the list. Landing on the welcome panel here is how
// a wrong field name (`state.app.flights`) hid behind demo-seeded roots.
check("a root with records opens on the flight list",
  await evaluate("!/Welcome to Flight Desk/.test(document.getElementById('view').innerText)"));
check("three summary tiles", await evaluate("document.querySelectorAll('.tile').length === 3"));
check("media counts are gone from the dashboard",
  await evaluate("!/\\bphotos?\\b|video clips/i.test(document.getElementById('tiles').innerText)"));
check("three-column records at 1536",
  await evaluate("getComputedStyle(document.querySelector('.records')).gridTemplateColumns.split(' ').length === 3"),
  await evaluate("getComputedStyle(document.querySelector('.records')).gridTemplateColumns"));
// On a demo root the header must say so; on an ordinary root it must not.
// Asserting "synthetic" unconditionally would be wrong on a real archive.
const headerMode = await evaluate(
  "(document.querySelector('[data-testid=data-mode]')||{}).textContent || ''");
if (ordinaryRoot) {
  check("an ordinary root is not labelled synthetic", headerMode !== "synthetic", headerMode);
} else {
  check("synthetic data is labelled", headerMode === "synthetic", headerMode);
}
const listWidth = await evaluate("document.querySelector('.pane-list').getBoundingClientRect().width");
const workWidth = await evaluate("document.querySelector('.pane-detail').getBoundingClientRect().width");
check("list is about a fifth of the workspace",
  listWidth / (listWidth + workWidth) > 0.14 && listWidth / (listWidth + workWidth) < 0.30,
  `${Math.round(listWidth)}/${Math.round(workWidth)}`);
await shot("stage3-01-flights-1536", 1536, 1000);

// --- filters ---------------------------------------------------------------
await evaluate(`(() => {
  const sel = document.querySelector('#f-pilot');
  sel.value = sel.options[1].value;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})()`);
await new Promise((r) => setTimeout(r, 1200));
const filtered = await evaluate("document.querySelectorAll('.flight-list li a').length");
const tileCount = await evaluate("document.querySelectorAll('.tile-value')[0].textContent");
check("pilot filter narrows list and tiles together",
  Number(tileCount) === filtered, `tiles=${tileCount} rows=${filtered}`);
check("filters persist to the server",
  await evaluate("JSON.parse(localStorage.getItem('fd.filters')||'{}').pilot !== undefined"));
await evaluate(`(() => { const b = document.getElementById('f-clear'); b.click(); return true; })()`);
await new Promise((r) => setTimeout(r, 1000));
check("clear filters restores the full list",
  await evaluate("document.querySelectorAll('.flight-list li a').length > 3"));

// --- flight detail + charts ------------------------------------------------
const targetId = await evaluate(`(() => {
  const rows = [...document.querySelectorAll('.record')];
  const withGap = rows.find(r => r.querySelector('.tag.amber'));
  const chosen = withGap || rows[0];
  chosen.click();
  return location.hash;
})()`);
await waitFor("!!document.querySelector('#track svg, #track .empty')", 15000);
check("flight record opens its own detail", /\#\/flights\/\d+/.test(String(targetId)), String(targetId));
check("track renders over a local grid",
  await evaluate("!!document.querySelector('#track svg')"));
check("track states no tiles are fetched",
  await evaluate("/no tiles/.test((document.querySelector('#track svg text:last-of-type')||{}).textContent||'')"));
check("warnings panel states its provenance",
  await evaluate("(() => { const p = document.getElementById('warnings-panel');" +
    "if (!p) return false; const t = p.innerText;" +
    "return /no fault flag|faults were|fault was|encrypted|not been decoded|start-up|" +
    "could not be read/i.test(t); })()"));
check("battery temperature is judged against the configured limits",
  await evaluate("(() => { const p = document.getElementById('warnings-panel');" +
    "if (!p) return false; const t = p.innerText;" +
    "if (!/Battery temperature/i.test(t)) return false;" +
    "return /Within the configured limits|°C limit|no battery temperature|limits are turned off/i.test(t);" +
    "})()"));
check("no map tile is requested before consent", tileRequests.length === 0,
  `${tileRequests.length} tile requests`);
check("a map offer exists but tiles are off by default",
  await evaluate("!!document.getElementById('map-consent') && " +
    "!document.getElementById('map-toggle')"));
check("zoom controls appear only once the map is allowed",
  await evaluate("!document.getElementById('map-zoom-in') && " +
    "!document.getElementById('map-zoom-out')"));
check("charts include a missing-interval marker",
  await evaluate("document.querySelectorAll('.chart-missing').length > 0"));
check("metric stacks flank the track",
  await evaluate("!!document.querySelector('[data-left]') && !!document.querySelector('[data-right]')"));
const before = await evaluate("document.querySelector('#readout').textContent");
await evaluate(`(() => { const r = document.getElementById('scrub'); r.value = '600';
  r.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
await new Promise((r) => setTimeout(r, 500));
const after = await evaluate("document.querySelector('#readout').textContent");
check("scrubbing updates the readout", before !== after, `${before} -> ${after}`);
await shot("stage3-03-flight-overview-1536", 1536, 1000);

// --- tabs ------------------------------------------------------------------
for (const section of ["Details", "Equipment", "Power", "Sensors", "Controls", "Weather"]) {
  await evaluate(`(() => { const b = [...document.querySelectorAll('#tabs button')]
    .find(x => x.textContent === ${JSON.stringify(section)}); if (b) b.click(); return true; })()`);
  await new Promise((r) => setTimeout(r, 500));
  if (section === "Weather") {
    check("weather states observations are absent",
      await evaluate("/No weather observations/.test(document.body.textContent)"));
    check("weather asks before any external provider",
      await evaluate("/off by default|no request has been made/i.test(document.body.textContent)"));
  }
  if (section === "Power") {
    check("power view marks missing channels honestly",
      await evaluate("/Not provided by this adapter|no samples for this metric/i.test(document.body.textContent) || document.querySelectorAll('.chart-block').length > 0"));
  }
}

// --- keyboard navigation ---------------------------------------------------
await goto("#/flights");
await waitFor("!!document.querySelector('.record')");
const firstHash = await evaluate(`(() => { document.querySelector('.record').click(); return location.hash; })()`);
await waitFor("!!document.querySelector('#tabs')");
const idBefore = await evaluate("Number(location.hash.split('/')[2])");
await evaluate("document.body.focus(); true");
// The list opens on the newest flight, so the older one is to the left.
await call("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37, nativeVirtualKeyCode: 37 });
await call("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 });
await new Promise((r) => setTimeout(r, 900));
const idAfter = await evaluate("Number((location.hash.split('/')[2])||0)");
check("arrow keys move between flights", idAfter !== idBefore, `${idBefore} -> ${idAfter}`);
await call("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 });
await call("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
await new Promise((r) => setTimeout(r, 900));
check("arrow back returns to the previous flight",
  await evaluate(`Number(location.hash.split('/')[2]) === ${idBefore}`));
check("section is preserved across arrow navigation",
  await evaluate("/overview|details|equipment|power|sensors|controls|weather/.test(location.hash)"));

// --- battery -> service -> return -----------------------------------------
await goto("#/equipment/battery");
await waitFor("!!document.querySelector('.eq-list li a')");
await evaluate("document.querySelector('.eq-list li a').click(); true");
await waitFor("!!document.querySelector('[data-rule-card]')");
check("battery page shows maintenance rules",
  await evaluate("document.querySelectorAll('[data-rule-card]').length > 0"));
await evaluate(`(() => { const b = document.querySelector('[data-act=rule]'); b.click(); return true; })()`);
await new Promise((r) => setTimeout(r, 900));
check("the rule dialog can retune the schedule", await evaluate("(() => {" +
  "const inputs = document.querySelectorAll('[data-limit]');" +
  "return inputs.length >= 5 && !!document.getElementById('rule-save'); })()"));
check("the rule dialog can backdate a service", await evaluate("!!document.getElementById('rule-backdate')"));
check("the rule dialog shows the stored thresholds",
  await evaluate("(() => { const f = [...document.querySelectorAll('[data-limit]')]" +
    ".find(x => x.dataset.limit === 'flights_limit');" +
    "return f && Number(f.value) > 0; })()"));
check("rule dialog shows counters and baseline",
  await evaluate("/Counters/.test(document.body.textContent) && /Baseline/.test(document.body.textContent)"));
await evaluate("(() => { const b = document.querySelector('#modal [data-close]'); if (b) b.click(); return true; })()");
await shot("stage3-19-battery-1536", 1536, 1000);

// --- maintenance complete / undo ------------------------------------------
await goto("#/maintenance/upcoming");
await waitFor("!!document.querySelector('[data-act=complete]')");
check("upcoming groups batteries and aircraft",
  await evaluate("/Batteries/.test(document.body.textContent) && /Aircraft/.test(document.body.textContent)"));
check("two cards across",
  await evaluate("getComputedStyle(document.querySelector('.cards-2')).gridTemplateColumns.split(' ').length === 2"));
check("over-limit value is visible",
  await evaluate("/\\d+ \\/ \\d+/.test(document.body.textContent)"));
await shot("stage3-21-maintenance-1536", 1536, 1000);

// --- schedule sources: AirData counters kept, DoneDoctor checks added ------
check("the page names the schedule source", await evaluate("/Schedule source: AirData/.test(document.body.textContent)"));
await evaluate("document.getElementById('add-source-rules').click(); true");
await new Promise((r) => setTimeout(r, 3000));
check("DoneDoctor checks are added and labelled",
  await evaluate("/DoneDoctor/.test(document.body.textContent)"));
check("each rule carries its task checklist",
  await evaluate("!!document.querySelector('details summary')"));
check("AirData counters are still shown alongside",
  await evaluate("/Schedule source: AirData/.test(document.body.textContent)"));
await goto("#/maintenance/rules");
await waitFor("/DoneDoctor/.test(document.body.textContent)", 15000);
check("the calendar trigger appears as days on the rule list",
  await evaluate("(() => { const rows = [...document.querySelectorAll('table.data tr')];" +
    "const dd = rows.filter(r => /DoneDoctor/.test(r.innerText));" +
    "return dd.some(r => /days:/.test(r.innerText)); })()"));
await goto("#/maintenance/upcoming");
await waitFor("!!document.querySelector('[data-act=complete]')", 15000);

await evaluate(`(() => { const b = document.querySelector('[data-act=complete]'); b.click(); return true; })()`);
await waitFor("!!document.getElementById('note-text')");
await evaluate(`(() => { document.getElementById('note-text').value = 'ui check';
  const save = [...document.querySelectorAll('#modal-actions button')].find(x => x.textContent === 'Save');
  save.click(); return true; })()`);
await new Promise((r) => setTimeout(r, 1500));
await goto("#/maintenance/completed");
await waitFor("!!document.querySelector('[data-act=undo]')");
check("completed service can be undone", await evaluate("!!document.querySelector('[data-act=undo]')"));
await evaluate("document.querySelector('[data-act=undo]').click(); true");
await new Promise((r) => setTimeout(r, 1200));
check("undo removes the owner record from completed",
  await evaluate("!/ui check/.test(document.body.textContent)"));

// --- Q04: a double-clicked save must record one service --------------------
{
  await goto("#/maintenance/upcoming");
  await waitFor("!!document.querySelector('[data-act=complete]')", 20000);
  await evaluate("(() => { document.querySelector('[data-act=complete]').click(); return true; })()");
  await waitFor("!!document.getElementById('note-text')");
  const tripleClicked = await evaluate(`(() => {
    document.getElementById('note-text').value = 'ui-double-click';
    const save = [...document.querySelectorAll('#modal-actions button')]
      .find(x => x.textContent === 'Save');
    if (!save) return false;
    save.click(); save.click(); save.click();
    return true;
  })()`);
  check("Q04 the save control is clickable", tripleClicked);
  await new Promise((r) => setTimeout(r, 2000));
  await goto("#/maintenance/completed");
  await waitFor("/Completed|Services|No services/i.test(document.body.textContent)", 20000);
  const dupRows = await evaluate(
    "[...document.querySelectorAll('tr, .card-sq')].filter(el => " +
    "/ui-double-click/.test(el.innerText)).length");
  check("Q04 three rapid clicks record exactly one service", dupRows === 1, String(dupRows));
  // Leave the record as we found it.
  await evaluate(`(() => { const rows = [...document.querySelectorAll('tr, .card-sq')]
    .filter(el => /ui-double-click/.test(el.innerText));
    const undo = rows[0] && rows[0].querySelector('[data-act=undo]');
    if (undo) undo.click(); return true; })()`);
  await new Promise((r) => setTimeout(r, 1200));
}

// --- Q02: a slow old response must not repaint over a newer route ----------
{
  await goto("#/flights");
  await waitFor("!!document.querySelector('.record')", 20000);
  const pair = await evaluate(`(async () => {
    const t = document.querySelector('meta[name=fd-token]').content;
    const d = await (await fetch('/api/flights', {headers: {'X-FD-Token': t}})).json();
    const rows = d.rows || [];
    const a = rows[0];
    const b = rows.find(r => (r.aircraft_name || '') !== (a.aircraft_name || '')) || rows[1];
    return {a: a && a.id, b: b && b.id, bName: (b && b.aircraft_name) || ''};
  })()`);
  if (pair && pair.a && pair.b) {
    // Two navigations in one task: the first request is in flight when the
    // second route is set. Only the newer one may render.
    await evaluate(`(() => { location.hash = '#/flights/${pair.a}';
      location.hash = '#/flights/${pair.b}'; return true; })()`);
    await waitFor(`Number(location.hash.split('/')[2]) === ${pair.b}`, 8000);
    await new Promise((r) => setTimeout(r, 1800));
    const shown = await evaluate("document.getElementById('view').innerText");
    check("Q02 the newest navigation wins over a slower older response",
      String(shown || "").includes(pair.bName) && !!pair.bName,
      `expected ${pair.bName || "(no name)"}`);
  } else {
    skip("Q02 stale-response guard", "needs two flights with different aircraft");
  }
}

// --- Q03: Settings must show the worker's real state -------------------------
{
  await goto("#/settings");
  await waitFor("!!document.getElementById('usb-check')", 20000);
  check("Q03 Settings says whether the watcher worker is running",
    await evaluate("/Worker: (running|stopped)/.test(document.body.textContent)"),
    String(await evaluate("(() => { const m = document.body.textContent.match(/Worker: [a-z]+/); " +
      "return m ? m[0] : 'not shown'; })()")));
  check("Q03 a verify / re-read control exists",
    await evaluate("!!document.getElementById('usb-verify')"));
  check("Q03 the watcher toggle is present",
    await evaluate("!!document.getElementById('usb-watch')"));
}

skip("Q05 an unknown air time reads 'unknown'",
  "checked in the identity pass, which imports a record with no readable " +
  "duration; the demo root cannot produce one");

// --- units -----------------------------------------------------------------
await goto("#/flights");
await waitFor("!!document.querySelector('.record')");
const metricText = await evaluate("document.body.textContent");
await evaluate("document.body.dataset.units = 'imperial'; true");
await evaluate(`(() => { localStorage.setItem('fd-unit', 'imperial'); return true; })()`);
await evaluate(`(async () => { await fetch('/api/settings', {method:'POST',
  headers:{'Content-Type':'application/json','X-FD-Token': document.querySelector('meta[name=fd-token]').content},
  body: JSON.stringify({unit_preference:'imperial'})}); location.reload(); return true; })()`);
await waitFor("!!document.querySelector('.record')", 20000);
const imperialText = await evaluate("document.body.textContent");
check("imperial preference changes displayed units",
  /mph|ft|mi/.test(imperialText) && imperialText !== metricText);
check("unit button reflects imperial",
  await evaluate("document.getElementById('unit-toggle').textContent === 'Imperial'"));
await evaluate(`(async () => { await fetch('/api/settings', {method:'POST',
  headers:{'Content-Type':'application/json','X-FD-Token': document.querySelector('meta[name=fd-token]').content},
  body: JSON.stringify({unit_preference:'metric'})}); location.reload(); return true; })()`);
await waitFor("!!document.querySelector('.record')", 20000);

// --- privacy --------------------------------------------------------------
await goto("#/equipment/battery/9");
await waitFor("!!document.querySelector('.eq-list li a')");
await evaluate("document.getElementById('privacy-toggle').click(); true");
await new Promise((r) => setTimeout(r, 1400));
check("privacy mode hides identifying fields",
  await evaluate("document.body.dataset.privacy === 'on'"));
const hiddenSerial = await evaluate(`(() => {
  const el = document.querySelector('.private');
  if (!el) return 'no-private-node';
  return getComputedStyle(el).visibility;
})()`);
check("privacy mode actually hides a serial", hiddenSerial === 'hidden', String(hiddenSerial));
check("privacy mode shows a banner",
  await evaluate("getComputedStyle(document.querySelector('.privacy-banner')).display !== 'none'"));
await evaluate("document.getElementById('privacy-toggle').click(); true");
await new Promise((r) => setTimeout(r, 1200));
check("privacy mode can be switched back off",
  await evaluate("document.body.dataset.privacy === 'off'"));

// --- mobile ----------------------------------------------------------------
await call("Emulation.setDeviceMetricsOverride", {
  width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
});
await goto("#/flights");
await waitFor("!!document.querySelector('.flight-list li a')");
check("mobile has a list/detail switcher",
  await evaluate("getComputedStyle(document.querySelector('.mobile-switch')).display !== 'none'"));
check("mobile hides the detail pane in list mode",
  await evaluate("getComputedStyle(document.querySelector('.pane-detail')).display === 'none'"));
check("mobile uses one card column",
  await evaluate("getComputedStyle(document.querySelector('.records')).gridTemplateColumns.split(' ').length === 1"));
check("no horizontal overflow at 390",
  await evaluate("document.documentElement.scrollWidth <= 391"),
  String(await evaluate("document.documentElement.scrollWidth")));
await shot("stage3-04-mobile-390", 390, 844);
await evaluate(`(() => { const b = [...document.querySelectorAll('[data-mobile]')].find(x => x.dataset.mobile === 'detail'); b.click(); return true; })()`);
await new Promise((r) => setTimeout(r, 500));
check("mobile detail switch works",
  await evaluate("document.body.dataset.mobileView === 'detail'"));

// --- exports and local reference image (header-authenticated) ---------------
const exportResult = await evaluate(`(async () => {
  const token = document.querySelector('meta[name=fd-token]').content;
  const withHeader = await fetch('/api/export/equipment.csv', { headers: { 'X-FD-Token': token } });
  const without = await fetch('/api/export/equipment.csv');
  const redacted = await fetch('/api/export/flights.csv?redact=1', { headers: { 'X-FD-Token': token } });
  return {
    ok: withHeader.status,
    blocked: without.status,
    redacted: redacted.status,
    type: withHeader.headers.get('Content-Type') || '',
  };
})()`);
check("CSV export works with the session header", exportResult.ok === 200, JSON.stringify(exportResult));
check("CSV export is refused without the session header", exportResult.blocked === 403);
check("redacted export also downloads", exportResult.redacted === 200);
check("export is served as CSV", /text\/csv/.test(exportResult.type), exportResult.type);

{
  // Write a tiny local PNG so this check never depends on another one.
  const refFile = `${outDir}/reference-probe.png`;
  writeFileSync(refFile, Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
    "base64"));
  const refImage = await evaluate(`(async () => {
    const token = document.querySelector('meta[name=fd-token]').content;
    const path = ${JSON.stringify(refFile)};
    const withHeader = await fetch('/api/local-image?path=' + encodeURIComponent(path),
      { headers: { 'X-FD-Token': token } });
    const without = await fetch('/api/local-image?path=' + encodeURIComponent(path));
    return {
      status: withHeader.status,
      type: withHeader.headers.get('Content-Type') || '',
      blocked: without.status,
    };
  })()`);
  check("local reference image is served with the session header",
    refImage.status === 200 && /image\/png/.test(refImage.type), JSON.stringify(refImage));
  check("local reference image is refused without the session header",
    refImage.blocked === 403, String(refImage.blocked));
}

// --- import: real ZIP through the real picker -------------------------------
}
if (identityOnly) {
  console.log("SKIP import checks (identity-only run)");
} else if (!zipPath) {
  console.log("SKIP import checks (no synthetic ZIP path supplied)");
} else {
  await goto("#/import");
  await waitFor("!!document.getElementById('import-input-zip')", 20000);
  check("import page renders its pickers",
    await evaluate("!!document.getElementById('import-input-files') && " +
      "!!document.getElementById('import-input-folder')"));

  async function uploadZip(path) {
    const doc = await call("DOM.getDocument", { depth: -1 });
    const node = await call("DOM.querySelector", {
      nodeId: doc.result.root.nodeId, selector: "#import-input-zip",
    });
    await call("DOM.setFileInputFiles", { files: [path], nodeId: node.result.nodeId });
    await evaluate(`(() => { const i = document.getElementById('import-input-zip');
      i.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
  }

  await uploadZip(zipPath);
  if (expectRefusal) {
    // P01 contract, clicked: a demo root must refuse a real log import and say
    // why, rather than quietly mixing real flights into invented history.
    const refusedNotice = await waitFor(
      "/Nothing was imported/.test(document.getElementById('batch-result').innerText)",
      30000);
    check("a demo root refuses a real ZIP import", refusedNotice,
      text(await evaluate("document.getElementById('batch-result').innerText"))
        .slice(0, 140).replace(/\s+/g, " "));
    const refusedNew = await evaluate(`(() => {
      const cells = [...document.querySelectorAll('#batch-result .result-cell')];
      const cell = cells.find(c => /New files/.test(c.innerText));
      return cell ? Number(cell.querySelector('.n').textContent) : -1;
    })()`);
    check("the refused ZIP imported nothing", refusedNew === 0, String(refusedNew));
    check("the refusal explains the separate-root rule",
      await evaluate("/separate data root|demo data/i.test(" +
        "document.getElementById('batch-result').innerText)"));
    skip("the rest of the import checks", "import is refused on a demo root by design");
  } else {
  const appeared = await waitFor(
    "document.querySelectorAll('#batch-result .result-cell').length >= 6", 60000);
  check("ZIP upload shows a batch summary", appeared);
  const firstSummary = text(await evaluate(
    "document.getElementById('batch-result').innerText.replace(/\\s+/g, ' ').trim()"));
  const discovered = await evaluate(`(() => {
    const cells = [...document.querySelectorAll('#batch-result .result-cell')];
    const cell = cells.find(c => /Discovered/.test(c.innerText));
    return cell ? Number(cell.querySelector('.n').textContent) : -1;
  })()`);
  // Count *eligible* members from the ZIP's central directory: DJI flight
  // logs only. macOS metadata and non-log files must be excluded, not counted.
  let expectedMembers = -1;
  let totalMembers = -1;
  try {
    const zipBytes = readFileSync(zipPath);
    const names = [];
    for (let i = 0; i + 46 <= zipBytes.length; i++) {
      if (zipBytes.readUInt32LE(i) !== 0x02014b50) continue;
      const nameLen = zipBytes.readUInt16LE(i + 28);
      names.push(zipBytes.toString("utf8", i + 46, i + 46 + nameLen));
    }
    totalMembers = names.length;
    expectedMembers = names.filter((name) => {
      const base = name.split("/").pop() || "";
      const parts = name.split("/");
      if (parts.some((p) => p === "__MACOSX" || p.startsWith("._"))) return false;
      return /^(D?JI)?FlightRecord_.*\.txt$/i.test(base);
    }).length;
  } catch (err) {
    expectedMembers = -1;
  }
  check("every eligible member is accounted for",
    expectedMembers > 0 ? discovered === expectedMembers : discovered > 0,
    `discovered=${discovered} eligible=${expectedMembers} total=${totalMembers}`);
  if (totalMembers > expectedMembers) {
    check("non-log ZIP members are excluded from the batch",
      discovered < totalMembers, `${discovered} of ${totalMembers}`);
  }
  const counts = await evaluate(`(() => {
    const out = {};
    [...document.querySelectorAll('#batch-result .result-cell')].forEach((cell) => {
      const label = cell.querySelector('.l').textContent.trim();
      out[label] = Number(cell.querySelector('.n').textContent);
    });
    return out;
  })()`);
  const freshFlights = counts["New flights"] || 0;
  const newFiles = counts["New files"] || 0;
  const repeats = counts["Already imported"] || 0;
  check("summary names the outcome",
    newFiles > 0 || repeats > 0, firstSummary.slice(0, 120));
  check("new files imply new flights when the archive was empty",
    freshFlights <= newFiles && (freshFlights > 0 || repeats > 0),
    JSON.stringify(counts));
  check("repeat files never create flights",
    repeats === 0 || freshFlights === 0, JSON.stringify(counts));
  const firstWasFresh = freshFlights > 0;

  await new Promise((r) => setTimeout(r, 2500));
  const laterSummary = text(await evaluate(
    "(document.getElementById('batch-result')||{}).innerText?.replace(/\\s+/g, ' ').trim() || ''"));
  check("import summary survives the page refresh",
    laterSummary.length > 0 && laterSummary === firstSummary,
    laterSummary.slice(0, 80));
  if (firstWasFresh) {
    check("summary links to the new flights",
      await evaluate("!!document.querySelector('#batch-result [data-go=\"#/flights\"]')"));
  } else {
    console.log("SKIP new-flights link (this ZIP was already imported in an earlier run)");
  }

  await uploadZip(zipPath);
  await waitFor("document.querySelectorAll('#batch-result .result-cell').length >= 6", 60000);
  await new Promise((r) => setTimeout(r, 1800));
  const secondNew = await evaluate(`(() => {
    const cells = [...document.querySelectorAll('#batch-result .result-cell')];
    const cell = cells.find(c => /New files/.test(c.innerText));
    return cell ? Number(cell.querySelector('.n').textContent) : -1;
  })()`);
  // On a fresh root the first pass imported everything, so the repeat adds
  // nothing; on an already-seeded root the first pass was itself the repeat.
  check("a repeated ZIP import adds no files", secondNew === 0, String(secondNew));

  const badZip = `${outDir}/not-a-zip.zip`;
  writeFileSync(badZip, Buffer.from("this is not a zip archive at all", "utf8"));
  await uploadZip(badZip);
  const refused = await waitFor(
    "/Nothing was imported/.test(document.getElementById('batch-result').innerText)",
    30000);
  check("a bad ZIP says why nothing was imported", refused,
    text(await evaluate("document.getElementById('batch-result').innerText"))
      .slice(0, 120).replace(/\s+/g, " "));
  }
}

// --- equipment capture through the real file input --------------------------
if (!importOnly && !identityOnly) {
{
  await goto("#/settings");
  await waitFor("!!document.getElementById('capture-choose')", 20000);
  check("equipment capture offers a real chooser",
    await evaluate("!!document.getElementById('capture-input')"));
  check("apply is disabled until a preview exists",
    await evaluate("document.getElementById('capture-apply').disabled"));

  const captureDoc = await call("DOM.getDocument", { depth: -1 });
  const captureNode = await call("DOM.querySelector", {
    nodeId: captureDoc.result.root.nodeId, selector: "#capture-input",
  });
  await call("DOM.setFileInputFiles", {
    files: [`${process.cwd()}/fixtures/synthetic/AirData-Equipment-Capture-SYNTHETIC.md`],
    nodeId: captureNode.result.nodeId,
  });
  await evaluate(`(() => { const i = document.getElementById('capture-input');
    i.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
  await new Promise((r) => setTimeout(r, 600));
  check("chosen capture file name is shown",
    await evaluate("/AirData-Equipment-Capture/.test(" +
      "document.getElementById('capture-path').value)"));
  check("apply is still disabled before preview",
    await evaluate("document.getElementById('capture-apply').disabled"));
  await evaluate("document.getElementById('capture-preview').click(); true");
  const previewed = await waitFor(
    "document.querySelectorAll('#capture-out .result-cell').length >= 6", 30000);
  check("capture preview reports counts", previewed,
    (await evaluate("document.getElementById('capture-out').innerText")).slice(0, 100)
      .replace(/\s+/g, " "));
  check("capture preview separates resolved from grouped rows",
    await evaluate("/individually resolved/.test(document.getElementById('capture-out').innerText)")
      || await evaluate("/kept as groups/.test(document.getElementById('capture-out').innerText)"));
  check("apply becomes available after preview",
    await evaluate("!document.getElementById('capture-apply').disabled"));
}

// --- a backup runs from a typed folder, not only a native dialog ----------
{
  await goto("#/settings");
  await waitFor("!!document.getElementById('backup-dest')", 20000);
  check("backup destination can be typed",
    await evaluate("!!document.getElementById('backup-dest')"));
  check("backup folder chooser exists as a convenience",
    await evaluate("!!document.getElementById('backup-pick')"));
  const backupDir = joinPath(process.env.TEMP || tmpdir(), "flightdesk-uicheck-backups");
  mkdirSync(backupDir, { recursive: true });
  await evaluate(`(() => { const i = document.getElementById('backup-dest');
    i.value = ${JSON.stringify(backupDir)}; return true; })()`);
  await evaluate("document.getElementById('backup-now').click(); true");
  // Reading the backup list needs the same double-submit pair every API call
  // needs: the session cookie AND the X-FD-Token header. A bare fetch from the
  // page is refused with 403 by design, so the first version of this check
  // reported a working backup as a failure.
  const backupList = "fetch('/api/backups', {headers:{'X-FD-Token': " +
    "document.querySelector('meta[name=fd-token]').content}}).then(r => r.json())";
  const wrote = await waitFor(
    backupList + ".then(d => Array.isArray(d.rows) && " +
    "d.rows.some(x => x.status === 'ok'))", 120000);
  check("backup is written to the typed folder", wrote,
    String(await evaluate(backupList +
      ".then(d => JSON.stringify(d.rows[0] || {}).slice(0,80))")));
}

// --- exports and destination pickers ---------------------------------------
{
  await goto("#/settings");
  await waitFor("document.querySelectorAll('[data-export]').length >= 5", 20000);
  check("every export button carries its own download link",
    await evaluate("Array.from(document.querySelectorAll('[data-export]'))" +
      ".every(b => b.dataset.export && b.dataset.filename)"));
  // Click the redacted export: it was the one that appeared to do nothing.
  await evaluate("(() => { const b = [...document.querySelectorAll('[data-export]')]" +
    ".find(x => /no locations/i.test(x.textContent)); if (b) b.click(); return true; })()");
  await new Promise((r) => setTimeout(r, 1500));
  check("the redacted export reports that it was requested",
    await evaluate("/Download requested: FlightDesk-flights-redacted\\.csv/.test(" +
      "(document.getElementById('toast')||{}).textContent || '')"),
    String(await evaluate("(document.getElementById('toast')||{}).textContent")));
  check("a backup folder is suggested so the dialog is optional",
    await evaluate("(() => { const v = (document.getElementById('backup-dest')||{}).value || '';" +
      "return /backups$/i.test(v); })()"));
  await evaluate("document.getElementById('backup-pick').click(); true");
  await new Promise((r) => setTimeout(r, 1200));
  check("choosing a folder always produces a visible message",
    await evaluate("/Opening a Windows dialog|could not be shown|No path chosen/.test(" +
      "(document.getElementById('backup-note')||{}).textContent || '')"));
}

// --- folder picker is browser-owned ----------------------------------------
{
  await goto("#/import");
  await waitFor("!!document.getElementById('import-input-folder')", 20000);
  check("folder chooser is browser-owned",
    await evaluate(`(() => {
      const i = document.getElementById('import-input-folder');
      return !!i && (i.webkitdirectory === true || i.hasAttribute('directory'));
    })()`));
}

// --- empty state -----------------------------------------------------------
await goto("#/flights?x=1");
await evaluate(`(() => { const s = document.getElementById('f-search');
  if (s) { s.value = 'zzzz-no-such-flight'; s.dispatchEvent(new Event('input', { bubbles: true })); }
  return true; })()`);
await new Promise((r) => setTimeout(r, 1400));
check("empty state explains the filter result",
  await evaluate("/No flights match/.test(document.body.textContent)"));
await evaluate(`(() => { const b = document.getElementById('f-clear'); if (b) b.click(); return true; })()`);
}

// --- identity review, clicked (P11a) ----------------------------------------
if (identityOnly) {
  const identityFile = process.env.FD_IDENTITY_FILE || "";
  if (!identityFile) {
    check("identity: a crafted unreadable log was supplied", false,
      "set FD_IDENTITY_FILE");
  } else {
    await call("Emulation.setDeviceMetricsOverride", {
      width: 1366, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await goto("#/flights");
    await waitFor("!!document.querySelector('#view')", 20000);
    // Upload through the app's own route: this is the same path a dropped file
    // takes, not a direct database write.
    const b64 = readFileSync(identityFile).toString("base64");
    const upload = await evaluate(`(async () => {
      const token = document.querySelector('meta[name=fd-token]').content;
      const bin = atob(${JSON.stringify(b64)});
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const name = encodeURIComponent('DJIFlightRecord_2025-05-16_[12-00-00].txt');
      const r = await fetch('/api/import/upload', {method: 'POST', headers: {
        'X-FD-Token': token, 'X-FD-Filename': name, 'X-FD-Member': name,
        'Content-Type': 'application/octet-stream'}, body: arr});
      return await r.json();
    })()`);
    check("identity: an unreadable log is kept as a record, not dropped",
      upload && upload.totals && (upload.totals.new || 0) >= 1,
      JSON.stringify(upload && upload.totals));

    // Q05: this record has no readable duration and no measured air time. The
    // flight list must say "unknown", never a bare dash the owner could read as
    // zero.
    await goto("#/flights");
    await waitFor("document.querySelectorAll('.record').length > 0", 20000);
    const unknownAir = await evaluate(`(() => {
      for (const row of document.querySelectorAll('.record')) {
        const air = row.querySelector('.metrics span');
        if (air && /unknown/i.test(air.innerText)) return air.innerText.trim();
      }
      return null;
    })()`);
    check("Q05 a record with unmeasurable time reads 'unknown'",
      Boolean(unknownAir), String(unknownAir));
    check("Q05 it is not presented as zero",
      !/^\s*0\s*s?\s+unknown/i.test(String(unknownAir || "")), String(unknownAir));
    await shot("identity-00-unknown-time-1366", 1366, 900);

    await goto("#/import");
    const offered = await waitFor(
      "!!document.querySelector('[data-act=review-identity]')", 20000);
    check("identity: an unresolved record offers Review identity", offered);
    check("identity: the old dead-end label is gone",
      await evaluate("!/Keep \\/ merge/.test(document.body.innerText)"));
    await shot("identity-01-import-1366", 1366, 900);

    await evaluate("(() => { document.querySelector('[data-act=review-identity]').click(); return true; })()");
    const opened = await waitFor("!!document.getElementById('identity-note')", 15000);
    check("identity: the review opens a real dialog", opened);
    const modalText = await evaluate("document.getElementById('modal').innerText");
    check("identity: the dialog shows provenance and options",
      /Originals:/.test(modalText || "") && /separate flights/i.test(modalText || ""),
      String(modalText || "").slice(0, 90).replace(/\s+/g, " "));
    await shot("identity-02-review-1366", 1366, 900);

    const decided = await evaluate(`(() => {
      const b = [...document.querySelectorAll('#modal-actions button')]
        .find(x => /separate flights/i.test(x.textContent));
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check("identity: 'These are separate flights' is offered", decided);
    await new Promise((r) => setTimeout(r, 1800));
    check("identity: the decision is acknowledged",
      await evaluate("/separate flight|no readable measurements|Kept out/i.test(" +
        "(document.getElementById('toast')||{}).textContent || '')"),
      String(await evaluate("(document.getElementById('toast')||{}).textContent")));

    // The decision persists: reopening now offers Undo.
    const reopened = await waitFor(
      "!!document.querySelector('[data-act=review-identity]')", 15000);
    if (reopened) {
      await evaluate("(() => { document.querySelector('[data-act=review-identity]').click(); return true; })()");
      const hasUndo = await waitFor(
        "(() => [...document.querySelectorAll('#modal-actions button')]" +
        ".some(x => /Undo decision/i.test(x.textContent)))()", 15000);
      check("identity: the stored decision can be undone", hasUndo);
      if (hasUndo) {
        await evaluate(`(() => {
          const b = [...document.querySelectorAll('#modal-actions button')]
            .find(x => /Undo decision/i.test(x.textContent));
          b.click(); return true;
        })()`);
        await new Promise((r) => setTimeout(r, 1500));
        check("identity: undo is acknowledged",
          await evaluate("/undone/i.test(" +
            "(document.getElementById('toast')||{}).textContent || '')"));
      }
    }
  }
}

console.log(`SUMMARY ${failures.length === 0 ? "PASS" : "FAIL"} checks=${total} failures=${failures.length}`);
check("no request left loopback", externalRequests.length === 0,
  externalRequests.slice(0, 3).join(" | "));
if (evaluateErrors.length) {
  // A page-side exception is a finding, not noise: report it once, deduped.
  console.log("EVALUATE_ERRORS " + [...new Set(evaluateErrors)].slice(0, 5).join(" | "));
}
if (failures.length) console.log("FAILED_CHECKS " + failures.join(", "));
console.log(`SUMMARY_FINAL ${failures.length === 0 ? "PASS" : "FAIL"} checks=${total} failures=${failures.length}`);
ws.close();
process.exit(failures.length ? 1 : 0);
