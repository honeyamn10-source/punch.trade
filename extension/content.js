// punch.trade content script.
//
// Attaches a Shadow DOM overlay to the broker's chart container. Site
// adapters (one per broker) own the "where is the chart + what symbol"
// logic, because every broker's DOM is different — this is the part that
// is per-broker integration work, not config.
//
// Broker pages are SPAs: the chart renders after JS and re-renders on
// symbol switch, so we watch the DOM with a MutationObserver and
// re-attach when the chart element appears or the URL changes.

(() => {
  if (window.__punchTradeLoaded) return;
  window.__punchTradeLoaded = true;

  // ------------------------------------------------------------- sites --
  const ADAPTERS = [
    {
      name: "demo",
      hosts: ["127.0.0.1", "localhost"],
      pathRe: /\/demo/,
      selectors: ["[data-chart-container]"],
      symbol: () => (location.hash.replace("#", "") || "RELIANCE").toUpperCase(),
    },
    {
      // Selectors are best-effort — broker DOMs change. If the overlay
      // doesn't attach, inspect the page and add the current selector.
      name: "kite",
      hosts: ["kite.zerodha.com"],
      selectors: ["[data-testid='chart-container']", ".chart-container",
                  "#chart-container", "[data-chart-container]"],
      symbol: () => {
        const m = location.pathname.match(/\/chart\/\w+\/([A-Z0-9]+)/);
        if (m) return m[1];
        const t = (document.title || "").match(/([A-Z]{3,})\s*[-–]\s*Price/i);
        return t ? t[1].toUpperCase() : "";
      },
    },
    {
      name: "binance",
      hosts: [/\.binance\.com$/],
      selectors: ["[data-scrollable]", "#tvchart", ".chart-container",
                  "[data-testid='trading-chart']"],
      symbol: () => {
        const m = location.pathname.match(/\/trade\/(\w+)/);
        if (m) return m[1].replace("_", "/");
        const h = location.hash.match(/\/spot\/(\w+)/);
        if (h) return h[1].replace("_", "/");
        return "";
      },
    },
  ];

  const adapter = ADAPTERS.find((a) => {
    const host = location.hostname.toLowerCase();
    return a.hosts.some((h) => (h instanceof RegExp ? h.test(host) : h === host)) &&
           (!a.pathRe || a.pathRe.test(location.pathname));
  });
  if (!adapter) return;

  // ----------------------------------------------------------- overlay --
  const STYLES = `
    :host { all: initial; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    .pt-root {
      position: absolute; top: 8px; right: 8px; z-index: 2147483646;
      width: 300px; max-height: 70%; overflow-y: auto;
      font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
      font-size: 12px; color: #e6edf3; background: rgba(13,17,23,0.94);
      border: 1px solid #30363d; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }
    .pt-head {
      display: flex; align-items: center; gap: 8px; padding: 8px 10px;
      border-bottom: 1px solid #30363d; font-weight: 700; font-size: 13px;
    }
    .pt-dot { width: 8px; height: 8px; border-radius: 50%; background: #f85149; }
    .pt-dot.on { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
    .pt-broker { margin-left: auto; font-weight: 400; color: #8b949e; font-size: 11px; }
    .pt-sec { padding: 6px 10px; color: #8b949e; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }
    .pt-card {
      margin: 0 8px 8px; padding: 8px 10px; border: 1px solid #30363d;
      border-radius: 8px; background: #161b22;
    }
    .pt-card .sym { font-weight: 700; font-size: 13px; color: #58a6ff; }
    .pt-card .strat { color: #8b949e; font-size: 10px; margin-left: 6px; }
    .pt-card .side { font-weight: 700; color: #3fb950; }
    .pt-rows { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 4px; margin: 6px 0; }
    .pt-rows div { background: #0d1117; border-radius: 6px; padding: 4px 6px; }
    .pt-rows .k { color: #8b949e; font-size: 9px; }
    .pt-rows .v { font-family: ui-monospace, monospace; font-size: 11px; }
    .pt-punch {
      width: 100%; padding: 6px; border: 0; border-radius: 6px; cursor: pointer;
      background: #1f6feb; color: #fff; font-weight: 700; font-size: 12px;
    }
    .pt-punch:disabled { opacity: 0.5; cursor: not-allowed; }
    .pt-pos { display: flex; justify-content: space-between; padding: 4px 10px; font-size: 11px; }
    .pt-pos .pnl { font-family: ui-monospace, monospace; }
    .pnl-pos { color: #3fb950; } .pnl-neg { color: #f85149; }
    .pt-toast {
      position: absolute; bottom: 8px; right: 8px; z-index: 2147483647;
      padding: 6px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;
      background: #1f6feb; color: #fff; opacity: 0; transition: opacity 0.25s;
      font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    }
    .pt-toast.err { background: #f85149; }
    .pt-toast.show { opacity: 1; }
    .pt-empty { padding: 10px; color: #8b949e; text-align: center; font-size: 11px; }
  `;

  class Overlay {
    constructor(chartEl) {
      this.chartEl = chartEl;
      this.host = document.createElement("div");
      this.host.style.cssText = "position:static;all:initial;";
      this.shadow = this.host.attachShadow({ mode: "open" });
      this.shadow.innerHTML = `<style>${STYLES}</style>
        <div class="pt-root">
          <div class="pt-head">
            <span class="pt-dot" id="dot"></span>
            <span>punch.trade</span>
            <span class="pt-broker" id="broker"></span>
          </div>
          <div class="pt-sec">Live signals</div>
          <div id="signals"><div class="pt-empty">Waiting for signals…</div></div>
          <div class="pt-sec">Positions</div>
          <div id="positions"><div class="pt-empty">No open positions</div></div>
        </div>
        <div class="pt-toast" id="toast"></div>`;
      chartEl.appendChild(this.host);
      this.dot = this.shadow.getElementById("dot");
      this.brokerEl = this.shadow.getElementById("broker");
      this.signalsEl = this.shadow.getElementById("signals");
      this.positionsEl = this.shadow.getElementById("positions");
      this.toastEl = this.shadow.getElementById("toast");
      this.signals = [];
      this.positions = [];
    }

    setConnection(on) { this.dot.classList.toggle("on", !!on); }
    setBroker(name) { this.brokerEl.textContent = name || ""; }

    toast(text, isErr) {
      this.toastEl.textContent = text;
      this.toastEl.classList.toggle("err", !!isErr);
      this.toastEl.classList.add("show");
      clearTimeout(this._toastT);
      this._toastT = setTimeout(() => this.toastEl.classList.remove("show"), 3500);
    }

    addSignal(sig) {
      this.signals.unshift(sig);
      this.signals = this.signals.slice(0, 5);
      this.renderSignals();
    }

    renderSignals() {
      if (!this.signals.length) {
        this.signalsEl.innerHTML = '<div class="pt-empty">Waiting for signals…</div>';
        return;
      }
      this.signalsEl.innerHTML = "";
      for (const s of this.signals) {
        const card = document.createElement("div");
        card.className = "pt-card";
        const when = new Date(s.ts * 1000).toLocaleTimeString();
        card.innerHTML = `
          <div><span class="sym">${s.symbol}</span><span class="strat">${s.strategyName}</span>
          <span style="float:right;color:#8b949e;font-size:10px">${when}</span></div>
          <div class="pt-rows">
            <div><div class="k">ENTRY</div><div class="v">${s.entry}</div></div>
            <div><div class="k">TARGETS</div><div class="v">${(s.targets || [s.targetPrice]).map((t) => t).join(" / ")}</div></div>
            <div><div class="k">STOP</div><div class="v">${s.stopLoss}</div></div>
          </div>
          <button class="pt-punch">PUNCH — ${s.side.toUpperCase()} @ ${s.entry}</button>`;
        const btn = card.querySelector(".pt-punch");
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          btn.textContent = "Sending…";
          try {
            const resp = await chrome.runtime.sendMessage({ type: "execute", signal: s, qty: 1 });
            if (!resp || !resp.ok) throw new Error(resp && resp.error ? resp.error : "no response");
            this.toast("Order accepted: " + (resp.result.result && resp.result.result.orderId || resp.result.status || ""));
          } catch (e) {
            this.toast("Order failed: " + e.message, true);
            btn.disabled = false;
            btn.textContent = "PUNCH — " + s.side.toUpperCase() + " @ " + s.entry;
          }
        });
        this.signalsEl.appendChild(card);
      }
    }

    setPositions(positions) {
      this.positions = positions || [];
      if (!this.positions.length) {
        this.positionsEl.innerHTML = '<div class="pt-empty">No open positions</div>';
        return;
      }
      this.positionsEl.innerHTML = "";
      for (const p of this.positions) {
        const row = document.createElement("div");
        row.className = "pt-pos";
        const cls = (p.pnl_pct || 0) >= 0 ? "pnl-pos" : "pnl-neg";
        row.innerHTML = `<span>${p.symbol} · ${p.qty} × ${p.side}</span>
          <span class="pnl ${cls}">${(p.pnl_pct || 0).toFixed(2)}%</span>`;
        this.positionsEl.appendChild(row);
      }
    }
  }

  // --------------------------------------------------------- lifecycle --
  let overlay = null;
  let lastUrl = location.href;

  function findChart() {
    for (const sel of adapter.selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function attach() {
    const chart = findChart();
    if (chart && !overlay) {
      overlay = new Overlay(chart);
      chrome.runtime.sendMessage({ type: "getState" }, (resp) => {
        if (resp && resp.ok) {
          overlay.setConnection(resp.result.connected);
          overlay.setBroker(resp.result.broker);
        }
      });
    }
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (!overlay) return;
    switch (msg.type) {
      case "connection":
        overlay.setConnection(msg.data.connected);
        break;
      case "snapshot":
        overlay.setBroker(Object.keys(msg.data.brokers).join(", "));
        for (const s of msg.data.signals.slice().reverse()) overlay.addSignal(s);
        overlay.setPositions(msg.data.positions);
        break;
      case "signal":
        overlay.addSignal(msg.data);
        break;
      case "position":
        overlay.setPositions(overlay.positions.filter((p) => p.id !== msg.data.id));
        overlay.toast(`${msg.data.symbol} closed @ ${msg.data.exit} (${msg.data.pnl_pct}%)`);
        break;
    }
  });

  const observer = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (overlay) { overlay.host.remove(); overlay = null; }
    }
    attach();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  attach();
})();