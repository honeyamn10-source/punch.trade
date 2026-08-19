// PUNCH NEXUS - Page Components
// Reusable page components for each workspace

class PageComponent {
  constructor(container, options = {}) {
    this.container = typeof container === 'string' ? document.querySelector(container) : container;
    this.options = options;
    this.state = {};
    this.subscriptions = [];
    this._initialized = false;
  }

  async init() {
    if (this._initialized) return;
    this._render();
    await this._bindEvents();
    this._initialized = true;
    this.onInit();
  }

  _render() {
    this.container.innerHTML = this.render();
  }

  render() {
    return '<div class="page-component">Override render() method</div>';
  }

  async _bindEvents() {}

  onInit() {}

  setState(key, value) {
    this.state[key] = value;
    this._updateView(key, value);
  }

  getState(key) {
    return this.state[key];
  }

  subscribe(key, callback) {
    return store.subscribe(key, callback, this.constructor.name);
  }

  _updateView(key, value) {
    // Override in subclasses
  }

  onDestroy() {}

  destroy() {
    this.subscriptions.forEach(unsub => unsub());
    this.subscriptions = [];
    this.onDestroy();
  }
}

// ============================================================
// NEXUS Home Page
// ============================================================

class NexusHomePage extends PageComponent {
  render() {
    return `
      <div class="nexus-home">
        <div class="page-header">
          <h2>NEXUS</h2>
          <div class="mode-badge research">RESEARCH</div>
        </div>
        
        <div class="stats-grid" id="marketPulse"></div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Regime Radar</h3>
            </div>
            <div id="regimeRadar" style="height: 300px;"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Edge Scanner</h3>
            </div>
            <div class="table-container">
              <table class="table" id="edgeScannerTable">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Class</th>
                    <th>Regime</th>
                    <th>Strategy</th>
                    <th>Signal</th>
                    <th>OOS Exp.</th>
                    <th>Robustness</th>
                    <th>R/R</th>
                    <th>Data Q.</th>
                  </tr>
                </thead>
                <tbody id="edgeScannerBody"></tbody>
              </table>
            </div>
          </div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Strategy Pulse</h3>
            </div>
            <div id="strategyPulse"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Portfolio Risk</h3>
            </div>
            <div id="portfolioRisk"></div>
          </div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Recent Signals</h3>
            </div>
            <div class="table-container">
              <table class="table">
                <thead>
                  <tr><th>Time</th><th>Symbol</th><th>Side</th><th>Strategy</th><th>Entry</th><th>Status</th></tr>
                </thead>
                <tbody id="recentSignals"></tbody>
              </table>
            </div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">AI Insight</h3>
            </div>
            <div class="analysis" id="aiInsight">Loading AI insight...</div>
          </div>
        </div>
      </div>
    `;
  }

  async onInit() {
    await this._loadMarketPulse();
    await this._loadEdgeScanner();
    await this._loadStrategyPulse();
    await this._loadRecentSignals();
    await this._loadAIInsight();
  }

  async _loadMarketPulse() {
    try {
      const response = await api.get('/api/v1/market/providers');
      const providers = response.providers || {};
      
      const container = document.getElementById('marketPulse');
      container.innerHTML = '';
      
      for (const [id, provider] of Object.entries(providers)) {
        const stat = document.createElement('div');
        stat.className = 'stat-card';
        const state = provider.state || 'UNKNOWN';
        const stateClass = String(state).toLowerCase().replace('_', '-');
        stat.innerHTML = `
          <div class="stat-label">${fmt.escape(provider.display_name || id)}</div>
          <div class="stat-value">
            <span class="chip ${stateClass}">${fmt.escape(state)}</span>
          </div>
        `;
        container.appendChild(stat);
      }
    } catch (error) {
      console.error('Failed to load market pulse:', error);
    }
  }

  async _loadEdgeScanner() {
    try {
      const response = await api.get('/api/v1/market/search', { q: 'BTC', limit: 10 });
      const results = response.results || [];
      const tbody = document.getElementById('edgeScannerBody');
      if (results.length) {
        tbody.innerHTML = results.map(r => `
          <tr>
            <td class="mono">${fmt.escapeSymbol(r.symbol)}</td>
            <td>${fmt.escape(r.assetClass || r.asset_class || '—')}</td>
            <td>${fmt.escape(r.exchange || r.provider || '—')}</td>
            <td>${fmt.escape(r.name || '—')}</td>
            <td><span class="chip info">FOUND</span></td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
          </tr>
        `).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="9" class="muted">No instruments found for "BTC"</td></tr>';
      }
    } catch (error) {
      console.error('Failed to load edge scanner:', error);
      const tbody = document.getElementById('edgeScannerBody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="9" class="muted">Edge scanner unavailable</td></tr>';
    }
  }

  async _loadStrategyPulse() {
    const container = document.getElementById('strategyPulse');
    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Adaptive Trend</div>
          <div class="stat-value">82</div>
          <div class="stat-change pos">Robust</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Tactical Rotation</div>
          <div class="stat-value">76</div>
          <div class="stat-change pos">Validated</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Regime Reversion</div>
          <div class="stat-value">71</div>
          <div class="stat-change warn">Watch</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Vol Breakout</div>
          <div class="stat-value">68</div>
          <div class="stat-change mut">Degraded</div>
        </div>
      </div>
    `;
  }

  async _loadRecentSignals() {
    try {
      const response = await api.get('/api/signals/history', { limit: 10 });
      const tbody = document.getElementById('recentSignals');
      const signals = response.signals || [];
      
      if (signals.length > 0) {
        tbody.innerHTML = signals.map(s => `
          <tr>
            <td>${fmt.time(s.ts)}</td>
            <td class="mono">${fmt.escapeSymbol(s.symbol)}</td>
            <td><span class="chip ${s.side === 'LONG' ? 'ok' : 'danger'}">${fmt.escape(s.side)}</span></td>
            <td>${fmt.escape(s.strategyId || '—')}</td>
            <td class="mono">${s.entry != null ? fmt.price(s.entry) : '—'}</td>
            <td><span class="chip ${s.status === 'FILLED' ? 'ok' : 'warn'}">${fmt.escape(s.status || 'NEW')}</span></td>
          </tr>
        `).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">No recent signals</td></tr>';
      }
    } catch (error) {
      console.error('Failed to load recent signals:', error);
      const tbody = document.getElementById('recentSignals');
      if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="muted">Signals unavailable</td></tr>';
    }
  }

  async _loadAIInsight() {
    const container = document.getElementById('aiInsight');
    container.textContent = 'No AI insight available. Connect Qwen to enable analysis.';
  }
}

// ============================================================
// Strategy Lab Page
// ============================================================

class StrategyLabPage extends PageComponent {
  render() {
    return `
      <div class="strategy-lab">
        <div class="page-header">
          <h2>Strategy Lab</h2>
          <div class="toolbar">
            <select id="strategySelect" class="form-select">
              <option value="">Select Strategy</option>
            </select>
            <select id="symbolSelect" class="form-select">
              <option value="">Select Symbol</option>
            </select>
            <select id="timeframeSelect" class="form-select">
              <option value="5m">5m</option>
              <option value="15m">15m</option>
              <option value="1h">1h</option>
              <option value="1d">1d</option>
            </select>
            <button id="runResearch" class="btn btn-primary">Run Research</button>
          </div>
        </div>
        
        <div class="truth-bar" id="truthBar">
          <div class="truth-item"><span class="label">Total Trials</span><span class="value" id="tbTrials">—</span></div>
          <div class="truth-item"><span class="label">Completed Trades</span><span class="value" id="tbTrades">—</span></div>
          <div class="truth-item"><span class="label">OOS Trades</span><span class="value" id="tbOOSTrades">—</span></div>
          <div class="truth-item"><span class="label">WF Trades</span><span class="value" id="tbWFTrades">—</span></div>
          <div class="truth-item"><span class="label">Costs</span><span class="value" id="tbCosts">—</span></div>
          <div class="truth-item"><span class="label">Turnover</span><span class="value" id="tbTurnover">—</span></div>
          <div class="truth-item"><span class="label">Trials Tested</span><span class="value" id="tbTrialsTested">—</span></div>
          <div class="truth-item"><span class="label">Sample Quality</span><span class="value" id="tbSampleQuality">—</span></div>
          <div class="truth-item"><span class="label">Bootstrap P+</span><span class="value" id="tbBootstrap">—</span></div>
          <div class="truth-item"><span class="label">Quality Gate</span><span class="value" id="tbGate">—</span></div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Equity Research Chart</h3>
            </div>
            <div id="equityChart" style="height: 400px;"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Drawdown Underlay</h3>
            </div>
            <div id="drawdownChart" style="height: 400px;"></div>
          </div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Parameter Topography</h3>
            </div>
            <div id="paramSurface" style="height: 400px;"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Cost Cliff</h3>
            </div>
            <div id="costCliffChart" style="height: 400px;"></div>
          </div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Monte Carlo Fan</h3>
            </div>
            <div id="mcFanChart" style="height: 400px;"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Regime Cube</h3>
            </div>
            <div id="regimeCube" style="height: 400px;"></div>
          </div>
        </div>
        
        <div class="grid2">
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Trade Cloud (MAE vs MFE)</h3>
            </div>
            <div id="tradeCloud" style="height: 400px;"></div>
          </div>
          
          <div class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Strategy DNA</h3>
            </div>
            <div id="strategyDNA"></div>
          </div>
        </div>
      </div>
    `;
  }

  async onInit() {
    await this._loadStrategies();
    this._bindEvents();
  }

  async _loadStrategies() {
    try {
      const response = await api.get('/api/strategies');
      const select = document.getElementById('strategySelect');
      const strategies = response.strategies || [];
      if (strategies.length) {
        select.innerHTML = '<option value="">Select Strategy</option>' +
          strategies.map(s => `<option value="${fmt.escape(s.id)}">${fmt.escape(s.name)} (${fmt.escape(s.id)})</option>`).join('');
      }
    } catch (error) {
      console.error('Failed to load strategies:', error);
    }
  }

  _bindEvents() {
    document.getElementById('runResearch')?.addEventListener('click', () => this._runResearch());
    document.getElementById('strategySelect')?.addEventListener('change', (e) => this._onStrategyChange(e.target.value));
  }

  async _onStrategyChange(strategyId) {
    if (!strategyId) return;
    try {
      const response = await api.get('/api/strategies');
      const strategies = response.strategies || [];
      const strategy = strategies.find(s => s.id === strategyId);
      const symbolSelect = document.getElementById('symbolSelect');
      if (strategy && symbolSelect) {
        symbolSelect.innerHTML = `<option value="${fmt.escape(strategy.symbol)}">${fmt.escape(strategy.symbol)}</option>`;
      }
    } catch (error) {
      console.error('Failed to load strategy:', error);
    }
  }

  async _runResearch() {
    const strategyId = document.getElementById('strategySelect').value;
    const symbol = document.getElementById('symbolSelect').value;
    const timeframe = document.getElementById('timeframeSelect').value;
    
    if (!strategyId || !symbol) {
      this._showError('Please select strategy and symbol');
      return;
    }
    
    const btn = document.getElementById('runResearch');
    btn.disabled = true;
    btn.textContent = 'Running...';
    
    try {
      const response = await api.post(`/api/research/${encodeURIComponent(strategyId)}`, {
        broker: 'paper',
        interval: timeframe,
        days: 30
      });
      this._showError('Research completed');
      this._updateTruthBar(response);
    } catch (error) {
      console.error('Research failed:', error);
      this._showError('Research failed: ' + error.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run Research';
    }
  }

  _showError(message) {
    const toast = document.createElement('div');
    toast.className = 'toast toast-info';
    toast.textContent = message;
    document.getElementById('toastContainer')?.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  _updateTruthBar(report) {
    if (!report) return;
    const sample = report.sample || {};
    const wf = report.walkForward || {};
    const cfg = report.config || {};
    const bootstrap = report.bootstrap || {};
    const trades = (sample.tradesTrain || 0) + (sample.tradesVal || 0) + (sample.tradesTest || 0);

    const values = {
      tbTrials: cfg.bootstrapIterations ?? '—',
      tbTrades: trades,
      tbOOSTrades: sample.tradesTest ?? '—',
      tbWFTrades: wf.totalWindows != null ? `${wf.profitableWindows}/${wf.totalWindows}` : '—',
      tbCosts: '—',
      tbTurnover: '—',
      tbTrialsTested: '—',
      tbSampleQuality: sample.quality || '—',
      tbDSR: '—',
      tbPBO: '—',
      tbLookahead: '—',
      tbDataQuality: sample.quality || '—',
      tbBootstrap: bootstrap.probPositive != null ? fmt.percent(bootstrap.probPositive) : '—',
      tbGate: report.qualityGate ? `${report.qualityGate.score}/100` : '—'
    };

    for (const [id, value] of Object.entries(values)) {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    }
  }
}

// ============================================================
// Page Registry
// ============================================================

const Pages = {
  'nexus-home': NexusHomePage,
  'strategy-lab': StrategyLabPage,
  // Add more pages as needed
};

function createPage(pageName, container, options) {
  const PageClass = Pages[pageName];
  if (!PageClass) {
    console.error(`Page not found: ${pageName}`);
    return null;
  }
  const page = new PageClass(container, options);
  return page.init().then(() => page);
}

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PageComponent, NexusHomePage, StrategyLabPage, Pages, createPage };
} else {
  window.PageComponent = PageComponent;
  window.NexusHomePage = NexusHomePage;
  window.StrategyLabPage = StrategyLabPage;
  window.Pages = Pages;
  window.createPage = createPage;
}