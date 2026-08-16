// punch.trade popup — settings + broker connection.

const $ = (id) => document.getElementById(id);
const DEFAULTS = { server: "http://127.0.0.1:8000", token: "punch-demo-token", broker: "paper" };

async function msg(type, extra = {}) {
  return chrome.runtime.sendMessage({ type, ...extra });
}

function setMsg(el, text, ok) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

async function refreshState() {
  const resp = await msg("getState");
  if (!resp || !resp.ok) return;
  const s = resp.result;
  $("server").value = s.server;
  $("token").value = s.token;
  $("broker").value = s.broker;
  $("dot").classList.toggle("on", s.connected);
  $("connState").textContent = s.connected ? "live" : "offline";
}

async function refreshStatus() {
  const resp = await msg("apiGet", { path: "/api/broker/status" });
  const el = $("status");
  if (!resp || !resp.ok) { el.innerHTML = `<div class="sf">${resp.error || "failed"}</div>`; return; }
  el.innerHTML = Object.entries(resp.result)
    .map(([name, st]) => `<div class="status-row"><span>${name}</span>
      <span class="${st.connected ? "st" : "sf"}">${st.connected ? (st.account || "ok") : "down"}</span></div>`)
    .join("");
}

async function refreshPositions() {
  const resp = await msg("apiGet", { path: "/api/positions" });
  const el = $("positions");
  if (!resp || !resp.ok) { el.innerHTML = `<div class="sf">${resp.error || "failed"}</div>`; return; }
  const ps = resp.result.positions || [];
  el.innerHTML = ps.length
    ? ps.map((p) => `<div>${p.symbol} · ${p.qty} × ${p.side} — ${(p.pnl_pct || 0).toFixed(2)}%</div>`).join("")
    : "<div>no open positions</div>";
}

$("save").onclick = async () => {
  await chrome.storage.sync.set({
    server: $("server").value.trim() || DEFAULTS.server,
    token: $("token").value.trim() || DEFAULTS.token,
    broker: $("broker").value,
  });
  await msg("settingsChanged");
  setMsg($("saveMsg"), "Saved — reconnecting", true);
  setTimeout(() => { $("saveMsg").textContent = ""; refreshState(); }, 800);
};

$("refreshStatus").onclick = refreshStatus;
$("refreshPositions").onclick = refreshPositions;

$("kiteUrl").onclick = async () => {
  const key = $("kiteKey").value.trim();
  if (!key) return setMsg($("kiteMsg"), "Enter your API key first", false);
  const resp = await msg("apiPost", { path: "/api/broker/kite/login-url", body: { api_key: key } });
  if (!resp || !resp.ok) return setMsg($("kiteMsg"), resp.error || "failed", false);
  chrome.tabs.create({ url: resp.result.url });
  setMsg($("kiteMsg"), "Logged in — copy request_token from the redirect URL", true);
};

$("kiteConnect").onclick = async () => {
  const resp = await msg("apiPost", {
    path: "/api/broker/kite/connect",
    body: { api_key: $("kiteKey").value.trim(), api_secret: $("kiteSecret").value.trim(),
            request_token: $("kiteToken").value.trim() },
  });
  if (!resp || !resp.ok) return setMsg($("kiteMsg"), resp.error || "failed", false);
  setMsg($("kiteMsg"), "Kite connected", true);
  refreshStatus();
};

$("bnConnect").onclick = async () => {
  const resp = await msg("apiPost", {
    path: "/api/broker/binance/connect",
    body: { api_key: $("bnKey").value.trim(), api_secret: $("bnSecret").value.trim(),
            testnet: $("bnTestnet").checked },
  });
  if (!resp || !resp.ok) return setMsg($("bnMsg"), resp.error || "failed", false);
  setMsg($("bnMsg"), "Binance connected", true);
  refreshStatus();
};

$("oaConnect").onclick = async () => {
  const resp = await msg("apiPost", {
    path: "/api/broker/openalgo/connect",
    body: { host: $("oaHost").value.trim(), apikey: $("oaKey").value.trim(),
            broker: $("oaBroker").value.trim() || "zerodha" },
  });
  if (!resp || !resp.ok) return setMsg($("oaMsg"), resp.error || "failed", false);
  setMsg($("oaMsg"), "OpenAlgo connected", true);
  refreshStatus();
};

refreshState();
refreshStatus();
refreshPositions();