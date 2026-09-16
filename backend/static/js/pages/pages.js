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
// Scenario Lab Page
// ============================================================

class ScenarioLabPage extends PageComponent {
  render() {
    return `
      <div class="scenario-lab">
        <div class="page-header">
          <h2>Scenario Lab</h2>
          <div class="toolbar">
            <button id="loadScenarios" class="btn">Refresh Scenarios</button>
          </div>
        </div>

        <div class="grid2">
          <div class="panel">
            <div class="panel-header"><h3 class="panel-title">Stress Scenarios</h3></div>
            <div class="table-container">
              <table class="table">
                <thead>
                  <tr><th>Scenario</th><th>Type</th><th>Severity</th><th>P(occur)</th></tr>
                </thead>
                <tbody id="scenarioList"></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header"><h3 class="panel-title">Stress Run — Strategy Metrics</h3></div>
            <div class="form-inline">
              <label>Net Return %</label>
              <input id="mNetReturn" type="number" step="0.1" value="10" />
              <label>Max DD %</label>
              <input id="mMaxDd" type="number" step="0.1" value="15" />
              <label>Sharpe</label>
              <input id="mSharpe" type="number" step="0.1" value="1.0" />
              <label>Cost bps</label>
              <input id="mCostBps" type="number" step="0.1" value="12" />
            </div>
            <div class="form-inline">
              <label>Volatility</label>
              <input id="mVol" type="number" step="0.1" value="30" />
              <label>Win Rate %</label>
              <input id="mWinRate" type="number" step="0.1" value="45" />
              <button id="runStress" class="btn btn-primary">Run Stress Battery</button>
            </div>
            <div id="stressReport" class="analysis" style="margin-top: 12px;">—</div>
          </div>
        </div>

        <div class="grid2">
          <div class="panel">
            <div class="panel-header"><h3 class="panel-title">Monte Carlo — Returns Input</h3></div>
            <div class="form-inline">
              <textarea id="mcReturns" rows="4" class="form-input mono" placeholder="comma-separated per-trade returns, e.g. 0.02,-0.01,0.015,..."></textarea>
            </div>
            <div class="form-inline">
              <button id="runMonteCarlo" class="btn btn-primary">Run Monte Carlo</button>
            </div>
            <div id="mcReport" class="analysis" style="margin-top: 12px;">—</div>
          </div>
        </div>
      </div>
    `;
  }

  async onInit() {
    await this._loadScenarios();
    document.getElementById('loadScenarios')?.addEventListener('click', () => this._loadScenarios());
    document.getElementById('runStress')?.addEventListener('click', () => this._runStress());
    document.getElementById('runMonteCarlo')?.addEventListener('click', () => this._runMonteCarlo());
  }

  async _loadScenarios() {
    try {
      const response = await api.get('/api/stress/scenarios');
      const tbody = document.getElementById('scenarioList');
      const scenarios = response.scenarios || [];
      tbody.innerHTML = scenarios.map(s => `
        <tr>
          <td>${fmt.escape(s.name)}</td>
          <td class="mono">${fmt.escape(s.type)}</td>
          <td>${fmt.escape(s.severity)}</td>
          <td>${(s.probability * 100).toFixed(3)}%</td>
        </tr>
      `).join('') || '<tr><td colspan="4" class="muted">No scenarios</td></tr>';
    } catch (error) {
      console.error('Failed to load scenarios:', error);
    }
  }

  _num(id) {
    const v = parseFloat(document.getElementById(id)?.value);
    return Number.isFinite(v) ? v : 0;
  }

  async _runStress() {
    const btn = document.getElementById('runStress');
    btn.disabled = true;
    btn.textContent = 'Running...';
    try {
      const metrics = {
        net_return: this._num('mNetReturn') / 100,
        max_drawdown_pct: this._num('mMaxDd'),
        sharpe: this._num('mSharpe'),
        cost_bps: this._num('mCostBps'),
        volatility: this._num('mVol'),
        win_rate: this._num('mWinRate') / 100
      };
      const response = await api.post('/api/stress/run', { metrics });
      this._renderStressReport(response);
    } catch (error) {
      console.error('Stress run failed:', error);
      document.getElementById('stressReport').textContent = 'Stress run failed: ' + error.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run Stress Battery';
    }
  }

  _renderStressReport(report) {
    if (!report) return;
    const rows = (report.scenarios || []).map(s => `
      <tr>
        <td>${fmt.escape(s.name)}</td>
        <td class="mono">${fmt.escape(s.type)}</td>
        <td><span class="chip ${s.passed ? 'ok' : 'danger'}">${s.passed ? 'PASS' : 'FAIL'}</span></td>
        <td>${fmt.escape(s.notes)}</td>
      </tr>
    `).join('');
    document.getElementById('stressReport').innerHTML = `
      <strong>Stress Report:</strong> ${report.passed}/${report.total_scenarios} pass
      (rate ${(report.pass_rate * 100).toFixed(0)}%)
      <span class="muted"> | worst DD ${report.worst_max_drawdown_pct.toFixed(1)}% |
      worst Sharpe ${report.worst_sharpe.toFixed(2)} |
      worst return ${(report.worst_net_return * 100).toFixed(1)}%</span>
      <table class="table" style="margin-top: 8px;">
        <thead><tr><th>Scenario</th><th>Type</th><th>Result</th><th>Notes</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  async _runMonteCarlo() {
    const btn = document.getElementById('runMonteCarlo');
    btn.disabled = true;
    btn.textContent = 'Running...';
    try {
      const raw = document.getElementById('mcReturns')?.value || '';
      const returns = raw.split(',').map(s => parseFloat(s.trim())).filter(Number.isFinite);
      if (returns.length < 10) {
        document.getElementById('mcReport').textContent = 'Need >= 10 returns';
        return;
      }
      const response = await api.post('/api/v1/analysis/monte-carlo', { returns, iterations: 500 });
      const a = response.analysis || {};
      const e = response.expectancy || {};
      document.getElementById('mcReport').innerHTML = `
        <strong>Ending Equity</strong> mean ${fmt.price(a.ending_equity?.mean)} |
        median ${fmt.price(a.ending_equity?.median)} |
        p5 ${fmt.price(a.ending_equity?.p5)} |
        p95 ${fmt.price(a.ending_equity?.p95)}
        <br/><strong>Max DD</strong> mean ${(a.max_drawdown?.mean || 0).toFixed(1)}% |
        P(DD>20%) ${((a.max_drawdown?.prob_over_20pct || 0) * 100).toFixed(0)}%
        <br/><strong>Expectancy</strong> p50 ${fmt.percent(e.expectancy_p50)} |
        prob positive ${fmt.percent(e.prob_positive)}
      `;
    } catch (error) {
      console.error('Monte Carlo failed:', error);
      document.getElementById('mcReport').textContent = 'Monte Carlo failed: ' + error.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run Monte Carlo';
    }
  }
}

// ============================================================
// Risk Shield Page
// ============================================================

class RiskShieldPage extends PageComponent {
  render() {
    return `
      <div class="risk-shield">
        <div class="page-header">
          <h2>Risk Shield</h2>
          <div class="toolbar">
            <button id="refreshRisk" class="btn">Refresh</button>
          </div>
        </div>

        <div class="stats-grid" id="riskStats"></div>

        <div class="grid2">
          <div class="panel">
            <div class="panel-header"><h3 class="panel-title">Shield Controls</h3></div>
            <div class="form-inline">
              <button id="toggleShield" class="btn btn-primary">Engage Shield</button>
              <button id="emergencyStop" class="btn btn-danger">Emergency Stop</button>
              <button id="resetBreaker" class="btn">Reset Breaker</button>
            </div>
            <div id="shieldStatus" class="analysis" style="margin-top: 12px;">—</div>
          </div>

          <div class="panel">
            <div class="panel-header"><h3 class="panel-title">Position Limits</h3></div>
            <div class="table-container">
              <table class="table">
                <tbody id="limitsTable"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  async onInit() {
    await this._loadRisk();
    document.getElementById('refreshRisk')?.addEventListener('click', () => this._loadRisk());
    document.getElementById('toggleShield')?.addEventListener('click', () => this._toggleShield());
    document.getElementById('emergencyStop')?.addEventListener('click', () => this._emergencyStop());
    document.getElementById('resetBreaker')?.addEventListener('click', () => this._resetBreaker());
  }

  async _loadRisk() {
    try {
      const [stateRes, shieldRes] = await Promise.all([
        api.get('/api/risk/state'),
        api.get('/api/risk/shield')
      ]);
      this._renderState(stateRes, shieldRes);
    } catch (error) {
      console.error('Failed to load risk state:', error);
    }
  }

  _renderState(state, shield) {
    const container = document.getElementById('riskStats');
    const chips = [
      { label: 'Mode', value: state.mode, cls: state.mode === 'live' ? 'danger' : 'info' },
      { label: 'Shield', value: shield.shieldOn ? 'ENGAGED' : 'OFF', cls: shield.shieldOn ? 'danger' : 'ok' },
      { label: 'Breaker', value: state.breakerOpen ? 'OPEN' : 'OK', cls: state.breakerOpen ? 'danger' : 'ok' },
      { label: 'Consecutive Losses', value: state.consecutiveLosses, cls: 'muted' },
      { label: 'Reconciliation', value: state.reconciliationOk ? 'OK' : 'MISMATCH', cls: state.reconciliationOk ? 'ok' : 'danger' },
      { label: 'Armed Brokers', value: (state.armed || []).join(', ') || 'none', cls: 'muted' }
    ];
    container.innerHTML = chips.map(c => `
      <div class="stat-card">
        <div class="stat-label">${fmt.escape(c.label)}</div>
        <div class="stat-value"><span class="chip ${c.cls}">${fmt.escape(c.value)}</span></div>
      </div>
    `).join('');

    const limits = [
      ['Max Open Positions', state.maxPositions],
      ['Max Qty', state.maxQty],
      ['Max Daily Loss %', state.maxDailyLossPct],
      ['Risk per Trade %', state.riskPerTradePct],
      ['Circuit Breaker Losses', state.circuitBreakerLosses]
    ];
    document.getElementById('limitsTable').innerHTML = limits.map(([k, v]) => `
      <tr><td>${fmt.escape(k)}</td><td class="mono">${fmt.escape(v)}</td></tr>
    `).join('');

    const btn = document.getElementById('toggleShield');
    btn.textContent = shield.shieldOn ? 'Lift Shield' : 'Engage Shield';
    btn.classList.toggle('btn-danger', shield.shieldOn);
    btn.classList.toggle('btn-primary', !shield.shieldOn);
    document.getElementById('shieldStatus').textContent = shield.shieldOn
      ? 'Shield ENGAGED — all new orders are blocked until lifted.'
      : 'Shield off — normal risk rules apply (breaker, daily loss, position limits).';
  }

  async _toggleShield() {
    try {
      const res = await api.get('/api/risk/shield');
      const next = !res.shieldOn;
      await api.post('/api/risk/shield', { on: next });
      await this._loadRisk();
    } catch (error) {
      console.error('Shield toggle failed:', error);
    }
  }

  async _emergencyStop() {
    try {
      await api.post('/api/system/stop');
      await this._loadRisk();
    } catch (error) {
      console.error('Emergency stop failed:', error);
    }
  }

  async _resetBreaker() {
    try {
      await api.post('/api/risk/breaker/reset');
      await this._loadRisk();
    } catch (error) {
      console.error('Breaker reset failed:', error);
    }
  }
}

// ============================================================
// Page Registry
// ============================================================

const Pages = {
  'nexus-home': NexusHomePage,
  'strategy-lab': StrategyLabPage,
  'scenario-lab': ScenarioLabPage,
  'risk-shield': RiskShieldPage,
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
  module.exports = { PageComponent, NexusHomePage, StrategyLabPage, ScenarioLabPage, RiskShieldPage, Pages, createPage };
} else {
  window.PageComponent = PageComponent;
  window.NexusHomePage = NexusHomePage;
  window.StrategyLabPage = StrategyLabPage;
  window.ScenarioLabPage = ScenarioLabPage;
  window.RiskShieldPage = RiskShieldPage;
  window.Pages = Pages;
  window.createPage = createPage;
}