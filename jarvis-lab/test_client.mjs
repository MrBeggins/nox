// Minimal IPC test client for jarvis-app.
// Connects to the WebSocket IPC server, sends a text command, prints every event,
// and exits when it sees an assistant_reply / error (or after a timeout).
// Uses Node's built-in global WebSocket (Node >= 22).

const URL = "ws://127.0.0.1:9712";
const phrase = process.argv[2] || "расскажи короткий факт о космосе";
const timeoutMs = Number(process.argv[3] || 90000);

console.log(`[client] connecting to ${URL} ...`);
const ws = new WebSocket(URL);

const done = (code) => { try { ws.close(); } catch {} process.exit(code); };
const timer = setTimeout(() => { console.log("[client] TIMEOUT waiting for reply"); done(2); }, timeoutMs);

ws.addEventListener("open", () => {
  console.log("[client] connected. sending text command:", JSON.stringify(phrase));
  ws.send(JSON.stringify({ action: "text_command", text: phrase }));
});

ws.addEventListener("message", (ev) => {
  const raw = typeof ev.data === "string" ? ev.data : ev.data.toString();
  console.log("[event]", raw);
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }
  if (msg.event === "assistant_reply") {
    console.log("\n[client] >>> ASSISTANT REPLY RECEIVED <<<\n" + msg.text);
    clearTimeout(timer); done(0);
  } else if (msg.event === "error") {
    console.log("\n[client] >>> ERROR EVENT <<<\n" + msg.message);
    clearTimeout(timer); done(3);
  }
});

ws.addEventListener("error", (e) => { console.log("[client] ws error:", e.message || e); });
ws.addEventListener("close", () => { console.log("[client] connection closed"); });
