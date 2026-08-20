// PUNCH NEXUS - Main Application Bootstrap
// Initializes store, API, WebSocket, router, and page components

(function () {
  'use strict';

  const App = {
    config: {
      apiBase: '/api',
      wsUrl: '/ws',
      version: '0.4.0',
      name: 'PUNCH NEXUS'
    },

    initialized: false,
    pages: new Map(),
    activePage: null,
    ws: null,
    _wsConnected: false,

    async init() {
      if (this.initialized) return;
      this.initialized = true;

      console.log(`[${this.config.name}] Initializing v${this.config.version}`);

      // 1. Configure API client
      const token = localStorage.getItem('punch-token') || '';
      api.setToken(token);
      this._bindApiInterceptors();

      // 2. Initialize WebSocket
      this._initWebSocket();

      // 3. Initialize Router
      this._initRouter();

      // 4. Bind global UI events
      this._bindGlobalEvents();

      // 5. Load initial page
      await this._navigateToPage(this._getInitialPage());

      // 6. Start polling
      this._startPolling();

      // 7. Update clock
      this._startClock();

      console.log(`[${this.config.name}] Ready`);
    },

    // ============================================================
    // API
    // ============================================================

    _bindApiInterceptors() {
      api.addRequestInterceptor((config) => {
        const token = localStorage.getItem('punch-token');
        if (token) {
          config.headers['X-Punch-Token'] = token;
        }
        return config;
      });

      api.addErrorInterceptor((error) => {
        if (error.status === 401) {
          // Token may have expired; clear it and let user re-auth
          localStorage.removeItem('punch-token');
          api.clearToken();
          this._updateConnectionStatus(false, 'auth');
        }
        this._showToast(error.message, 'error');
      });
    },

    setToken(token) {
      localStorage.setItem('punch-token', token);
      api.setToken(token);
    },

    // ============================================================
    // WebSocket
    // ============================================================

    _initWebSocket() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${proto}//${location.host}/ws/signals`;

      this.ws = new WebSocketManager({
        url: wsUrl,
        reconnectInterval: 3000,
        maxReconnectAttempts: 20,
        heartbeatInterval: 15000
      });

      this.ws.onOpen(() => {
        this._wsConnected = true;
        this._updateConnectionStatus(true, 'ws');
        // Auth handshake: the endpoint closes 4401 without it within 5s
        const token = localStorage.getItem('punch-token') || '';
        this.ws.send({ type: 'auth', token }).catch(() => {});
        this._subscribeToChannels();
      });

      this.ws.onClose(() => {
        this._wsConnected = false;
        this._updateConnectionStatus(false, 'ws');
      });

      this.ws.on('signal', (data) => this._onWsSignal(data));
      this.ws.on('fill', (data) => this._onWsFill(data));
      this.ws.on('quote', (data) => this._onWsQuote(data));
      this.ws.on('order', (data) => this._onWsOrder(data));
      this.ws.on('system', (data) => this._onWsSystem(data));
      this.ws.on('provider', (data) => this._onWsProvider(data));

      // Update HUD latency periodically
      setInterval(() => {
        const el = document.getElementById('hudWsLatency');
        if (el) {
          el.textContent = this.ws.isConnected() ? 'connected' : 'offline';
          el.className = 'status-dot ' + (this.ws.isConnected() ? 'on' : '');
        }
      }, 2000);

      this.ws.connect().catch(() => {
        // Connection will retry automatically
      });
    },

    _subscribeToChannels() {
      const channels = ['signals', 'fills', 'orders', 'quotes', 'system', 'providers'];
      channels.forEach(ch => {
        this.ws.send({ type: 'subscribe', channel: ch }).catch(() => {});
      });
    },

    _onWsSignal(data) {
      const tape = document.getElementById('tapeEntries');
      if (tape) {
        this._addTapeEntry(tape, 'SIGNAL', data.symbol, `${data.strategyId} ${data.side} @ ${data.entry}`);
      }
      this._appendToTable('recentSignals', [data]);
    },

    _onWsFill(data) {
      const tape = document.getElementById('tapeEntries');
      if (tape) {
        this._addTapeEntry(tape, 'FILL', data.symbol, `${data.qty} @ ${data.price}`);
      }
    },

    _onWsQuote(data) {
      // Update market pulse / HUD
      const el = document.getElementById(`quote-${data.symbol}`);
      if (el) {
        el.textContent = fmt.price(data.price);
      }
    },

    _onWsOrder(data) {
      const tape = document.getElementById('tapeEntries');
      if (tape) {
        this._addTapeEntry(tape, 'ORDER', data.symbol, data.status || 'SUBMITTED');
      }
    },

    _onWsSystem(data) {
      const tape = document.getElementById('tapeEntries');
      if (tape) {
        this._addTapeEntry(tape, 'SYSTEM', '', data.message || data.type || '');
      }
      this._showToast(data.message || data.type || 'System event', 'info');
    },

    _onWsProvider(data) {
      // Update provider status displays
      const el = document.getElementById('providerStatus');
      if (el) {
        el.textContent = data.state || 'UNKNOWN';
      }
    },

    _addTapeEntry(tape, type, symbol, detail) {
      const entry = document.createElement('div');
      entry.className = 'tape-entry';
      entry.innerHTML = `
        <span class="tape-time">${fmt.time(Date.now())}</span>
        <span class="tape-type ${type.toLowerCase()}">${fmt.escape(type)}</span>
        ${symbol ? `<span class="tape-symbol">${fmt.escapeSymbol(symbol)}</span>` : ''}
        <span class="tape-details">${fmt.escape(detail || '')}</span>
      `;
      tape.prepend(entry);

      // Limit tape entries
      while (tape.children.length > 100) {
        tape.lastElementChild.remove();
      }
    },

    // ============================================================
    // Router
    // ============================================================

    _initRouter() {
      window.addEventListener('routeChange', (e) => {
        const route = e.detail.route;
        this._onRouteChanged(route);
      });
    },

    async _navigateToPage(pageName) {
      // Update nav active states
      document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.page === pageName);
      });

      // Update workspace title
      const titles = {
        'nexus': 'NEXUS HOME',
        'atlas': 'MARKET ATLAS',
        'radar': 'EDGE RADAR',
        'forge': 'STRATEGY FORGE',
        'lab': 'STRATEGY LAB',
        'scenario': 'SCENARIO LAB',
        'battle': 'BATTLEBOARD',
        'portfolio': 'PORTFOLIO X-RAY',
        'paper': 'PAPER INCUBATOR',
        'execution': 'EXECUTION DECK',
        'risk': 'RISK SHIELD',
        'ai': 'QWEN QUANT',
        'system': 'SYSTEM',
        'providers': 'PROVIDERS'
      };
      const titleEl = document.getElementById('workspaceTitle');
      if (titleEl) titleEl.textContent = titles[pageName] || pageName.toUpperCase();

      const content = document.getElementById('workspaceContent');
      content.innerHTML = '';

      this.activePage = pageName;

      // Load page content
      const pageConfig = this._getPageConfig(pageName);
      if (!pageConfig) {
        content.innerHTML = '<div class="empty-state"><h3>Page not found</h3></div>';
        return;
      }

      // Lazy-load page components
      if (pageConfig.loader) {
        try {
          const mod = await pageConfig.loader();
          const PageClass = mod.default || mod;
          const page = new PageClass(content, {});
          await page.init();
          this.pages.set(pageName, page);
        } catch (error) {
          console.error(`Failed to load page ${pageName}:`, error);
          content.innerHTML = `<div class="empty-state"><h3>Failed to load</h3><p>${fmt.escape(error.message)}</p></div>`;
        }
      } else if (pageConfig.html) {
        content.innerHTML = pageConfig.html;
        if (pageConfig.init) {
          await pageConfig.init(content);
        }
      }
    },

    _getPageConfig(pageName) {
      const configs = {
        'nexus': {
          loader: () => Promise.resolve(window.NexusHomePage)
        },
        'lab': {
          loader: () => Promise.resolve(window.StrategyLabPage)
        },
        'scenario': {
          loader: () => Promise.resolve(window.ScenarioLabPage)
        },
        'risk': {
          loader: () => Promise.resolve(window.RiskShieldPage)
        },
        'signals': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">Live Signal Stream</h3></div>
              <div class="table-container">
                <table class="table">
                  <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>TP</th><th>SL</th><th>Strategy</th><th>Status</th></tr></thead>
                  <tbody id="recentSignals"></tbody>
                </table>
              </div>
            </div>
          `,
          init: async () => {
            const response = await api.get('/api/signals/history', { limit: 20 });
            const tbody = document.getElementById('recentSignals');
            const signals = response.signals || [];
            if (signals.length) {
              tbody.innerHTML = signals.map(s => `
                <tr>
                  <td class="mono">${fmt.time(s.ts)}</td>
                  <td class="mono">${fmt.escapeSymbol(s.symbol)}</td>
                  <td><span class="chip ${s.side === 'LONG' ? 'ok' : 'danger'}">${fmt.escape(s.side)}</span></td>
                  <td class="mono">${s.entry != null ? fmt.price(s.entry) : '—'}</td>
                  <td class="mono">${s.targetPrice != null ? fmt.price(s.targetPrice) : '—'}</td>
                  <td class="mono">${s.stopLoss != null ? fmt.price(s.stopLoss) : '—'}</td>
                  <td>${fmt.escape(s.strategyId || '—')}</td>
                  <td><span class="chip ${s.status === 'FILLED' ? 'ok' : 'warn'}">${fmt.escape(s.status || 'NEW')}</span></td>
                </tr>
              `).join('');
            } else {
              tbody.innerHTML = '<tr><td colspan="8" class="muted">No signals yet</td></tr>';
            }
          }
        },
        'positions': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">Open Positions</h3></div>
              <div class="table-container">
                <table class="table">
                  <thead><tr><th>ID</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Opened</th></tr></thead>
                  <tbody id="positionsBody"></tbody>
                </table>
              </div>
            </div>
          `,
          init: async () => {
            const response = await api.get('/api/positions');
            const tbody = document.getElementById('positionsBody');
            const positions = response.positions || [];
            if (positions.length) {
              tbody.innerHTML = positions.map(p => {
                const pnl = (p.pnl || 0);
                const pnlClass = pnl >= 0 ? 'pos' : 'neg';
                return `
                  <tr>
                    <td class="mono">${fmt.escape(p.id || '—')}</td>
                    <td class="mono">${fmt.escapeSymbol(p.symbol)}</td>
                    <td><span class="chip ${p.side === 'LONG' ? 'ok' : 'danger'}">${fmt.escape(p.side)}</span></td>
                    <td class="mono">${fmt.quantity(p.qty)}</td>
                    <td class="mono">${p.entry != null ? fmt.price(p.entry) : '—'}</td>
                    <td class="mono">${p.exit != null ? fmt.price(p.exit) : '—'}</td>
                    <td class="mono ${pnlClass}">${fmt.pnl(pnl).text}</td>
                    <td>${fmt.datetime(p.openedAt)}</td>
                  </tr>
                `;
              }).join('');
            } else {
              tbody.innerHTML = '<tr><td colspan="8" class="muted">No open positions</td></tr>';
            }
          }
        },
        'execution': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">Execution Deck</h3></div>
              <div class="table-container">
                <table class="table">
                  <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Broker</th></tr></thead>
                  <tbody id="ordersBody"></tbody>
                </table>
              </div>
            </div>
          `,
          init: async () => {
            const response = await api.get('/api/orders/history', { limit: 50 });
            const tbody = document.getElementById('ordersBody');
            const orders = response.orders || [];
            if (orders.length) {
              tbody.innerHTML = orders.map(o => `
                <tr>
                  <td class="mono">${fmt.time(o.ts)}</td>
                  <td class="mono">${fmt.escapeSymbol(o.symbol)}</td>
                  <td><span class="chip ${o.side === 'BUY' ? 'ok' : 'danger'}">${fmt.escape(o.side)}</span></td>
                  <td class="mono">${fmt.quantity(o.qty)}</td>
                  <td class="mono">${o.price != null ? fmt.price(o.price) : '—'}</td>
                  <td><span class="chip ${o.status === 'FILLED' ? 'ok' : 'warn'}">${fmt.escape(o.status)}</span></td>
                  <td>${fmt.escape(o.broker || 'paper')}</td>
                </tr>
              `).join('');
            } else {
              tbody.innerHTML = '<tr><td colspan="7" class="muted">No orders</td></tr>';
            }
          }
        },
        'risk': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">Risk Shield</h3></div>
              <div class="stats-grid" id="riskStats"></div>
              <div class="stats-grid" id="riskLimits"></div>
              <h3 class="section-title">Exposure</h3>
              <div class="table-container">
                <table class="table">
                  <thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Side</th><th>Status</th></tr></thead>
                  <tbody id="exposureBody"></tbody>
                </table>
              </div>
            </div>
          `,
          init: async () => {
            const [riskRes, posRes] = await Promise.all([
              api.get('/api/risk/state'),
              api.get('/api/positions')
            ]);
            const stats = document.getElementById('riskStats');
            const r = riskRes;
            stats.innerHTML = `
              <div class="stat-card">
                <div class="stat-label">Mode</div>
                <div class="stat-value">${fmt.escape(r.mode || 'paper')}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Armed</div>
                <div class="stat-value"><span class="chip ${r.armed ? 'ok' : 'warn'}">${r.armed ? 'ARMED' : 'SAFE'}</span></div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Circuit Breaker</div>
                <div class="stat-value"><span class="chip ${r.breakerOpen ? 'danger' : 'ok'}">${r.breakerOpen ? 'OPEN' : 'CLOSED'}</span></div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Consecutive Losses</div>
                <div class="stat-value">${r.consecutiveLosses ?? 0}</div>
              </div>
            `;
            const limits = document.getElementById('riskLimits');
            limits.innerHTML = `
              <div class="stat-card">
                <div class="stat-label">Max Daily Loss</div>
                <div class="stat-value">${fmt.percent((r.maxDailyLossPct || 0) / 100)}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Risk / Trade</div>
                <div class="stat-value">${fmt.percent((r.riskPerTradePct || 0) / 100)}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Max Positions</div>
                <div class="stat-value">${r.maxPositions ?? '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Max Qty</div>
                <div class="stat-value">${r.maxQty ?? '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Breaker Losses</div>
                <div class="stat-value">${r.circuitBreakerLosses ?? '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Reconciled</div>
                <div class="stat-value"><span class="chip ${r.reconciliationOk ? 'ok' : 'warn'}">${r.reconciliationOk ? 'OK' : 'PENDING'}</span></div>
              </div>
            `;
            const tbody = document.getElementById('exposureBody');
            const positions = (posRes.positions || []).filter(p => p.status === 'open');
            if (positions.length) {
              tbody.innerHTML = positions.map(p => `
                <tr>
                  <td class="mono">${fmt.escapeSymbol(p.symbol)}</td>
                  <td class="mono">${fmt.quantity(p.qty)}</td>
                  <td class="mono">${p.entry != null ? fmt.price(p.entry) : '—'}</td>
                  <td><span class="chip ${p.side === 'LONG' ? 'ok' : 'danger'}">${fmt.escape(p.side)}</span></td>
                  <td>${fmt.escape(p.status || 'open')}</td>
                </tr>
              `).join('');
            } else {
              tbody.innerHTML = '<tr><td colspan="5" class="muted">No open positions</td></tr>';
            }
          }
        },
        'system': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">System Flight Recorder</h3></div>
              <div class="stats-grid" id="systemStats"></div>
              <h3 class="section-title">Counters</h3>
              <div class="table-container">
                <table class="table">
                  <thead><tr><th>Counter</th><th>Value</th></tr></thead>
                  <tbody id="counterBody"></tbody>
                </table>
              </div>
            </div>
          `,
          init: async () => {
            const response = await api.get('/api/v1/system/metrics');
            const stats = document.getElementById('systemStats');
            const m = response;
            stats.innerHTML = `
              <div class="stat-card">
                <div class="stat-label">Uptime</div>
                <div class="stat-value">${fmt.duration((m.uptimeSec || 0) * 1000)}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Live Signals</div>
                <div class="stat-value">${m.signals ? m.signals.live : '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Orders (ledger)</div>
                <div class="stat-value">${m.orders ? m.orders.ledger : '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Trades (closed)</div>
                <div class="stat-value">${m.trades ? m.trades.closed : '—'}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Risk Breaker</div>
                <div class="stat-value"><span class="chip ${m.risk && m.risk.breakerOpen ? 'danger' : 'ok'}">${m.risk && m.risk.breakerOpen ? 'OPEN' : 'CLOSED'}</span></div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Armed</div>
                <div class="stat-value"><span class="chip ${m.risk && m.risk.armed ? 'ok' : 'warn'}">${m.risk && m.risk.armed ? 'ARMED' : 'SAFE'}</span></div>
              </div>
            `;
            const counters = m.counters || {};
            const tbody = document.getElementById('counterBody');
            const rows = Object.entries(counters);
            if (rows.length) {
              tbody.innerHTML = rows.map(([k, v]) => `
                <tr><td class="mono">${fmt.escape(k)}</td><td class="mono">${fmt.escape(v)}</td></tr>
              `).join('');
            } else {
              tbody.innerHTML = '<tr><td colspan="2" class="muted">No counters</td></tr>';
            }
          }
        },
        'settings': {
          html: `
            <div class="panel">
              <div class="panel-header"><h3 class="panel-title">Settings</h3></div>
              <h3 class="section-title">API Token</h3>
              <div class="form-group form-inline">
                <input type="password" id="tokenInput" class="form-input mono" placeholder="X-Punch-Token" autocomplete="off">
                <button id="saveToken" class="btn btn-primary">Save</button>
              </div>
              <p class="hint">Stored locally in your browser only.</p>
              <h3 class="section-title">Session</h3>
              <button id="logoutBtn" class="btn btn-danger">Logout</button>
            </div>
          `,
          init: async () => {
            const tokenInput = document.getElementById('tokenInput');
            tokenInput.value = localStorage.getItem('punch-token') || '';
            document.getElementById('saveToken').addEventListener('click', () => {
              const token = tokenInput.value.trim();
              App.setToken(token);
              App._showToast('Token saved', 'success');
            });
            document.getElementById('logoutBtn').addEventListener('click', async () => {
              try {
                await api.post('/api/system/logout');
              } catch (e) {
                // Session may already be invalid; clear locally regardless
              }
              localStorage.removeItem('punch-token');
              api.clearToken();
              App._showToast('Logged out', 'info');
            });
          }
        }
      };
      return configs[pageName] || null;
    },

    _onRouteChanged(route) {
      if (route && route.name) {
        this._navigateToPage(route.name.replace(/^\//, ''));
      }
    },

    _getInitialPage() {
      return this._getPageFromHash() || 'nexus';
    },

    _getPageFromHash() {
      const hash = location.hash.replace(/^#\/?/, '');
      if (hash) return hash;
      return null;
    },

    // ============================================================
    // Global UI
    // ============================================================

    _bindGlobalEvents() {
      // Nav buttons
      document.querySelectorAll('.nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
          this._navigateToPage(btn.dataset.page);
        });
      });

      // Token save
      const tokenInput = document.getElementById('tokenInput');
      const saveToken = document.getElementById('saveToken');
      if (tokenInput && saveToken) {
        tokenInput.value = localStorage.getItem('punch-token') || '';
        saveToken.addEventListener('click', () => {
          this.setToken(tokenInput.value.trim());
          this._showToast('Token saved', 'success');
        });
      }

      // Command palette (Ctrl+K)
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          this._toggleCommandPalette();
        }
        if (e.key === 'Escape') {
          this._closeCommandPalette();
        }
      });

      // Tape filter buttons
      document.querySelectorAll('.tape-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          document.querySelectorAll('.tape-filter-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          this._filterTape(btn.dataset.filter);
        });
      });

      // Collapse toggles
      document.getElementById('toggleContext')?.addEventListener('click', () => {
        document.querySelector('.context-panel')?.classList.toggle('collapsed');
      });
      document.getElementById('toggleNav')?.addEventListener('click', () => {
        document.querySelector('.nav-rail')?.classList.toggle('collapsed');
      });
      document.getElementById('toggleTape')?.addEventListener('click', () => {
        document.querySelector('.execution-tape')?.classList.toggle('collapsed');
      });
    },

    _filterTape(filter) {
      document.querySelectorAll('.tape-entry').forEach(entry => {
        const type = entry.querySelector('.tape-type')?.textContent.toLowerCase();
        entry.style.display = (!filter || type === filter) ? '' : 'none';
      });
    },

    _toggleCommandPalette() {
      const palette = document.getElementById('commandPalette');
      if (palette) {
        palette.classList.toggle('open');
        if (palette.classList.contains('open')) {
          document.getElementById('commandInput')?.focus();
        }
      }
    },

    _closeCommandPalette() {
      const palette = document.getElementById('commandPalette');
      if (palette) palette.classList.remove('open');
    },

    _showToast(message, type = 'info') {
      const container = document.getElementById('toastContainer');
      if (!container) return;
      const toast = document.createElement('div');
      toast.className = `toast toast-${type}`;
      toast.textContent = message;
      container.appendChild(toast);
      setTimeout(() => toast.classList.add('show'), 10);
      setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
      }, 4000);
    },

    _updateConnectionStatus(connected, source) {
      const dot = document.getElementById('connDot');
      const text = document.getElementById('connText');
      if (dot && text) {
        dot.classList.toggle('on', connected);
        text.textContent = connected ? 'connected' : 'disconnected';
      }
    },

    // ============================================================
    // Polling
    // ============================================================

    _startPolling() {
      setInterval(() => {
        this._pollProviders();
      }, 30000);
      this._pollProviders();
    },

    async _pollProviders() {
      try {
        const response = await api.get('/api/v1/market/providers');
        const providers = response.providers || {};
        const container = document.getElementById('providerStatus');
        if (container) {
          container.innerHTML = Object.entries(providers).map(([id, p]) => `
            <div class="status-item" title="${fmt.escape(p.display_name || id)}">
              <span class="status-dot ${p.state === 'READY' ? 'on' : p.state === 'DEGRADED' ? 'warn' : ''}"></span>
              <span>${fmt.escape(id)}</span>
              <span class="chip ${p.state === 'READY' ? 'ok' : 'warn'}">${fmt.escape(p.state)}</span>
            </div>
          `).join('');
        }
      } catch (error) {
        // Silent - will retry on next poll
      }
    },

    _startClock() {
      const update = () => {
        const el = document.getElementById('hudClock');
        if (el) el.textContent = fmt.time(Date.now());
      };
      update();
      setInterval(update, 1000);
    },

    // ============================================================
    // Table helpers
    // ============================================================

    _appendToTable(tableId, rows) {
      const tbody = document.getElementById(tableId);
      if (!tbody || !rows || !rows.length) return;

      // Remove empty state if present
      const empty = tbody.querySelector('.muted');
      if (empty) tbody.innerHTML = '';

      for (const row of rows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class="mono">${fmt.time(row.ts)}</td>
          <td class="mono">${fmt.escapeSymbol(row.symbol)}</td>
          <td><span class="chip ${row.side === 'LONG' || row.side === 'BUY' ? 'ok' : 'danger'}">${fmt.escape(row.side)}</span></td>
          <td>${fmt.escape(row.strategyId || '—')}</td>
          <td class="mono">${row.entry != null ? fmt.price(row.entry) : '—'}</td>
          <td><span class="chip ${row.status === 'FILLED' ? 'ok' : 'warn'}">${fmt.escape(row.status || 'NEW')}</span></td>
        `;
        tbody.prepend(tr);
        while (tbody.children.length > 100) {
          tbody.lastElementChild.remove();
        }
      }
    }
  };

  // Global instance
  window.App = App;

  // Bootstrap when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => App.init());
  } else {
    App.init();
  }
})();