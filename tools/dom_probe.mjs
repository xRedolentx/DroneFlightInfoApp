// Local DOM probe: connects to an already-running Edge/Chrome debug port and
// evaluates a small expression. Development aid only; no network, no upload.
//   node tools/dom_probe.mjs 9222 "<expression>"
const port = process.argv[2] || "9222";
const expression = process.argv[3] || "document.title";

const list = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
// Edge may open extra pages (sync dialog, first-run). Always prefer the app.
const page = list.find((t) => t.type === "page" && /^http:\/\/127\.0\.0\.1/.test(t.url || ""))
  || list.find((t) => t.type === "page");
if (!page) {
  console.error("no page target");
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

function call(method, params) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

await new Promise((resolve) => ws.addEventListener("open", resolve));
const result = await call("Runtime.evaluate", {
  expression,
  returnByValue: true,
  awaitPromise: true,
});
if (result.result && result.result.exceptionDetails) {
  console.error(JSON.stringify(result.result.exceptionDetails).slice(0, 500));
  process.exit(2);
}
console.log(JSON.stringify(result.result?.result?.value ?? null));
ws.close();
process.exit(0);
