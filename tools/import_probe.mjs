// Reproduce / verify the Import page with a real ZIP through the real file input.
//   node tools/import_probe.mjs <debugPort> <zipPath> [appUrl]
const port = process.argv[2] || "9222";
const zipPath = process.argv[3];
const appUrl = (process.argv[4] || "http://127.0.0.1:8787").replace(/\/$/, "");

const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
// Edge may open extra pages (sync dialog, first-run). Always prefer the app.
const page = targets.find((t) => t.type === "page" && /^http:\/\/127\.0\.0\.1/.test(t.url || ""))
  || targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
let nextId = 1;
const pending = new Map();
const consoleErrors = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
  if (msg.method === "Runtime.exceptionThrown") {
    consoleErrors.push(msg.params?.exceptionDetails?.text || "exception");
  }
  if (msg.method === "Runtime.consoleAPICalled" && msg.params?.type === "error") {
    consoleErrors.push((msg.params.args || []).map((a) => a.value || a.description).join(" "));
  }
});
function call(method, params = {}) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await new Promise((r) => ws.addEventListener("open", r));
await call("Page.enable");
await call("Runtime.enable");
await call("DOM.enable");
await call("Emulation.setDeviceMetricsOverride", {
  width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false,
});
async function evaluate(expression) {
  const res = await call("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (res.result?.exceptionDetails) return { __error: res.result.exceptionDetails.text };
  return res.result?.result?.value;
}
async function waitFor(expression, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await evaluate(expression)) return true;
    await sleep(200);
  }
  return false;
}

// fresh page at the import view, with an empty import state
await call("Page.navigate", { url: appUrl + "/#/import" });
await waitFor("!!document.getElementById('pick-zip')", 20000);
const rendered = await evaluate("!!document.getElementById('batch-result')");
console.log("import_view_rendered=" + rendered);
await sleep(600);

// Attach the ZIP to the file input the "Choose files" button drives.
await evaluate(`(() => {
  let input = document.querySelector('input[type=file]');
  if (!input) { input = document.createElement('input'); input.type = 'file';
    input.style.display = 'none'; document.body.appendChild(input); }
  input.id = 'probe-input';
  return input.id;
})()`);
const doc = await call("DOM.getDocument", { depth: -1 });
const node = await call("DOM.querySelector", {
  nodeId: doc.result.root.nodeId, selector: "#probe-input",
});
await call("DOM.setFileInputFiles", {
  files: [zipPath], nodeId: node.result.nodeId,
});
// DOM.setFileInputFiles does not fire change; dispatch it the way a user would.
await evaluate(`(() => {
  const input = document.getElementById('probe-input');
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})()`);

await sleep(1500);
const duringProgress = await evaluate(
  "document.querySelector('#progress > span').style.width");
console.log("progress_width_during=" + duringProgress);

await sleep(9000);
const state = await evaluate(`(() => {
  const host = document.getElementById('batch-result');
  const view = document.getElementById('view');
  return {
    batch_text: host ? host.innerText.trim().slice(0, 300) : 'NO_BATCH_NODE',
    view_has_result: !!document.querySelector('.result-cell'),
    toast: (document.getElementById('toast') || {}).textContent || '',
    history_rows: document.querySelectorAll('table.data tbody tr').length,
    errors_in_page: (document.body.innerText.match(/zip_unsafe|zip_unreadable|internal_error|upload_failed|attention|Needs attention/g) || []).slice(0, 5),
  };
})()`);
console.log("after_import=" + JSON.stringify(state));
console.log("console_errors=" + JSON.stringify(consoleErrors.slice(0, 5)));
ws.close();
process.exit(0);
