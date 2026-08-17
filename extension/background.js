// punch.trade background service worker.
//
// MV3 service workers can be killed and restarted at any time, so:
//  - all state lives in chrome.storage, never in memory alone
//  - the WebSocket gets a reconnect loop with exponential backoff
//  - the extension never holds broker credentials — only the short-lived
//    punch.trade session token, and every order is proxied through the
//    backend (which holds the broker access token encrypted).

const DEFAULTS = { server: "http://127.0.0.1:8000", token: "punch-demo-token", broker: "paper" };

let ws = null;
let retryMs = 1000;
let connected = false;

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function forward(message) {
  // relay to every tab where the content script is injected
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, message);
    } catch (_) { /* no content script on this tab */ }
  }
}

function connect() {
  getSettings().then(({ server, token }) => {
    const url = server.replace(/^http/, "ws") + "/ws/signals";
    try { ws = new WebSocket(url); } catch (_) { scheduleReconnect(); return; }

    ws.onopen = () => {
      // token travels in the auth message, never in the URL
      ws.send(JSON.stringify({ type: "auth", token }));
    };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }
      if (msg.type === "auth_ok") {
        connected = true;
        retryMs = 1000;
        forward({ type: "connection", data: { connected: true } });
        return;
      }
      forward(msg);
    };
    ws.onclose = () => {
      connected = false;
      forward({ type: "connection", data: { connected: false } });
      scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  });
}

function scheduleReconnect() {
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, 30000);
}

async function execute(signal, qty) {
  const { server, token, broker } = await getSettings();
  const body = {
    broker,
    strategyId: signal.strategyId || null,
    signalId: signal.id || null,
    clientRequestId: crypto.randomUUID(),
    symbol: signal.symbol,
    side: signal.side,
    qty,
    entry: signal.entry,
    targetPrice: signal.targetPrice,
    stopLoss: signal.stopLoss,
  };
  const res = await fetch(server + "/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Punch-Token": token },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    const msg = data.detail && typeof data.detail === "object"
      ? data.detail.message : (data.detail || "Order rejected (" + res.status + ")");
    throw new Error(msg);
  }
  return data;
}

async function apiGet(path) {
  const { server, token } = await getSettings();
  const res = await fetch(server + path, { headers: { "X-Punch-Token": token } });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail?.message || data.detail || "Request failed (" + res.status + ")");
  return data;
}

async function apiPost(path, body) {
  const { server, token } = await getSettings();
  const res = await fetch(server + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Punch-Token": token },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail?.message || data.detail || "Request failed (" + res.status + ")");
  return data;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "getState":
        const s = await getSettings();
        return { connected, ...s };
      case "execute":
        return await execute(msg.signal, msg.qty || 1);
      case "apiGet":
        return await apiGet(msg.path);
      case "apiPost":
        return await apiPost(msg.path, msg.body);
      case "settingsChanged":
        if (ws) { try { ws.close(); } catch (_) {} }
        connect();
        return { ok: true };
    }
  })()
    .then((result) => sendResponse({ ok: true, result }))
    .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
  return true; // async response
});

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();