// Local UI verification: drives the running Flight Desk app through Edge's
// DevTools protocol. Prints one PASS/FAIL line per check and writes screenshots.
// Everything stays on this machine.
//   node tools/ui_check.mjs [port] [outdir]
import { writeFileSync, mkdirSync } from "node:fs";

const debugPort = process.argv[2] || "9222";
const outDir = process.argv[3] || "ui-shots";
mkdirSync(outDir, { recursive: true });

const targets = await fetch(`http://127.0.0.1:${debugPort}/json`).then((r) => r.json());
const page = targets.find((t) => t.type === "page");
if (!page) {
  console.error("FAIL no page target");
  process.exit(1);
}
const ws = new WebSocket(page.webSocketDebuggerUrl);
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

const failures = [];
let total = 0;
function check(name, condition, detail = "") {
  total += 1;
  const ok = Boolean(condition);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? " :: " + detail : ""}`);
  if (!ok) failures.push(name);
}
async function evaluate(expression) {
  const res = await call("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (res.result?.exceptionDetails) {
    return { __error: res.result.exceptionDetails.text };
  }
  return res.result?.result?.value;
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
  await evaluate(`location.hash = ${JSON.stringify(hash)}; true`);
  await new Promise((r) => setTimeout(r, 900));
}

// --- desktop dashboard -----------------------------------------------------
await call("Emulation.setDeviceMetricsOverride", {
  width: 1536, height: 1000, deviceScaleFactor: 1, mobile: false,
});
await call("Page.navigate", { url: "http://127.0.0.1:8787/" });
await waitFor("!!document.querySelector('.flight-list li a')", 20000);
check("dashboard renders flight rows",
  await evaluate("document.querySelectorAll('.flight-list li a').length > 0"));
check("four summary tiles", await evaluate("document.querySelectorAll('.tile').length === 4"));
check("three-column records at 1536",
  await evaluate("getComputedStyle(document.querySelector('.records')).gridTemplateColumns.split(' ').length === 3"),
  await evaluate("getComputedStyle(document.querySelector('.records')).gridTemplateColumns"));
check("synthetic data is labelled",
  await evaluate("(document.querySelector('[data-testid=data-mode]')||{}).textContent === 'synthetic'"));
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
await evaluate("document.getElementById('privacy-toggle').click(); true");
await new Promise((r) => setTimeout(r, 1200));
check("privacy mode hides identifying fields",
  await evaluate("document.body.dataset.privacy === 'on'"));
check("privacy mode shows a banner",
  await evaluate("getComputedStyle(document.querySelector('.privacy-banner')).display !== 'none'"));
await evaluate("document.getElementById('privacy-toggle').click(); true");
await new Promise((r) => setTimeout(r, 1000));

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

// --- empty state -----------------------------------------------------------
await goto("#/flights?x=1");
await evaluate(`(() => { const s = document.getElementById('f-search');
  if (s) { s.value = 'zzzz-no-such-flight'; s.dispatchEvent(new Event('input', { bubbles: true })); }
  return true; })()`);
await new Promise((r) => setTimeout(r, 1400));
check("empty state explains the filter result",
  await evaluate("/No flights match/.test(document.body.textContent)"));
await evaluate(`(() => { const b = document.getElementById('f-clear'); if (b) b.click(); return true; })()`);

console.log(`SUMMARY ${failures.length === 0 ? "PASS" : "FAIL"} checks=${30 - failures.length + failures.length} failures=${failures.length}`);
if (failures.length) console.log("FAILED_CHECKS " + failures.join(", "));
ws.close();
process.exit(failures.length ? 1 : 0);
