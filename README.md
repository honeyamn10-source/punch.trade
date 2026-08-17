# punch.trade

A self-hosted signal platform: strategy engine + backtester + real broker
execution (Zerodha Kite, Binance, and 34+ Indian brokers via OpenAlgo) with a
Chrome extension that overlays live signals and one-tap bracket orders on the
chart pages you already use.

```
market data ──> strategy engine ──> Signal ──> WebSocket ──> extension overlay
                       │                                │
                   backtest                      PUNCH button
                  (same code)                         │
                       │                                v
                   win rate / drawdown        broker API (user's own account)
                                              entry + TP + SL as one bracket
```

Key design decisions (from the original architecture brief):

- **Non-custodial**: orders execute through *your* broker account with *your*
  tokens. The backend never holds money; it holds encrypted access tokens
  (Fernet, at rest) and proxies orders.
- **One evaluation path**: the same `StrategyRunner` drives live bars and
  backtests, so win-rate/drawdown numbers are not fiction.
- **Declarative strategies**: configs reference a fixed indicator/condition
  library — no arbitrary code execution, safe to share.
- **Adapter pattern**: one internal interface, one adapter per broker.
- **Multi-level take-profit**: signals carry TP1/TP2/TP3; the backtester and
  paper broker close equal fractions per level (SL always booked first in
  backtests). Brokers with single-bracket support use TP1.

## Repo layout

```
backend/          FastAPI server (REST + WebSocket signal feed)
  app/
    engine.py     StrategyRunner — bar-driven, per-symbol dedup state
    backtest.py   honest execution-cost backtester (next-open entry, no lookahead)
    research.py   chronological research dossiers (splits, walk-forward, bootstrap)
    trades.py     CompletedTrade / Fill model — one position = one trade
    pnl.py        single source of truth for PnL + metrics
    signals.py    signal state machine (CANDIDATE..CLOSED/REJECTED/EXPIRED)
    strategy_status.py  lifecycle ladder + composite score (never win-rate-only)
    execution.py  order ledger + reconciliation + closed-trade booking
    risk.py       modes, arming, limits, circuit breaker, reconciliation gate
    security.py   sessions (hash-only), CSRF, rate limits, sanitizer, headers
    db.py         SQLite store (WAL, migrations, legacy archive-then-import)
    obs.py        event log, request tracing, counters
    ai/           local Qwen analyst (auto-detect, whitelist-only, offline-safe)
    indicators.py SMA / EMA / RSI / MACD / Bollinger / Donchian / VWAP (no deps)
    strategies.py declarative strategy configs (9 shipped)
    feed.py       live feeds: paper / binance (CCXT polling) / kite (ticks)
    broker/       paper.py · kite.py · ccxt_bt.py · openalgo.py
    vault.py      Fernet-encrypted broker token storage
    api.py        REST + WS + error envelope + /api/v1 surface
  static/dashboard.html  full dashboard (signals, research+AI, risk, execution)
  tests/          pytest suite (181 tests; see docs/TESTING.md)
  scripts/        smoke.ps1 — boot + end-to-end endpoint smoke test
extension/        Chrome MV3 extension (overlay + popup)
docs/             17 markdown docs (see index below)
.github/workflows/ci.yml
```

## Documentation (docs/)

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | system overview, data flow |
| [API.md](docs/API.md) | endpoints, auth, error envelope, WebSocket |
| [SECURITY.md](docs/SECURITY.md) | threat model, sessions, CSRF, secrets |
| [RISK.md](docs/RISK.md) | modes, limits, breaker, reconciliation |
| [BACKTESTING.md](docs/BACKTESTING.md) | execution-cost model, honesty rules |
| [RESEARCH.md](docs/RESEARCH.md) | dossiers, walk-forward, bootstrap, gates |
| [STRATEGIES.md](docs/STRATEGIES.md) | configs, lifecycle ladder, scores |
| [SIGNALS.md](docs/SIGNALS.md) | generation, deterministic ids, state machine |
| [EXECUTION.md](docs/EXECUTION.md) | ledger, reconciliation, closed trades |
| [STORAGE.md](docs/STORAGE.md) | SQLite, migrations, legacy import |
| [AI_ANALYST.md](docs/AI_ANALYST.md) | local Qwen analyst rules |
| [DASHBOARD.md](docs/DASHBOARD.md) | SPA sections, auth, refresh |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | event log, request ids, health/metrics |
| [OPERATIONS.md](docs/OPERATIONS.md) | daily ritual, troubleshooting |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | install, env vars, going live |
| [TESTING.md](docs/TESTING.md) | test layout, isolation, CI |
| [CI_AND_SMOKE.md](docs/CI_AND_SMOKE.md) | CI workflow + smoke script |
| [AUDIT.md](docs/AUDIT.md) | security review findings (history) |

## Quick start (zero cost, 5 minutes)

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # or source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

1. Open `http://127.0.0.1:8000/demo` — a fake broker page.
2. Load the extension: `chrome://extensions` → Developer mode → **Load unpacked**
   → `extension/` folder.
3. The overlay appears top-right of the chart. Signals land over WebSocket;
   hit **PUNCH** to place a bracket order (entry + TP + SL) on the paper broker.

Dashboard: `http://127.0.0.1:8000/dashboard` — the zing.trade-style window on
everything: win rate, net PnL, equity curve, live signals, open/closed
positions, order audit log, broker connections, a strategy **leaderboard**
(backtested vs the paper feed, ranked by Sharpe — every losing exit included,
no cherry-picking), and on-demand backtests per strategy.

Backtests (real numbers on real data once a broker is connected):

```powershell
$h = @{ "X-Punch-Token" = "punch-demo-token" }
Invoke-RestMethod -Method Post -ContentType "application/json" -Headers $h `
  -Body '{"broker":"paper","interval":"5m","days":30}' `
  "http://127.0.0.1:8000/api/strategies/rsi-reversal/backtest"
```

Every REST call needs the `X-Punch-Token` header (never a URL query
string); the WebSocket requires an `{"type":"auth","token":...}` message
within 5 s of connecting. See `docs/SECURITY.md`.

Tests: `python -m pytest backend/tests -q` (also runs in CI on push).

Quality gates before every release:

```powershell
cd backend
python -m pytest tests -q            # full suite (208 tests)
ruff check .                          # lint
ruff format --check .                 # formatting
```

Runs on GitHub Actions too (`.github/workflows/ci.yml`): lint + tests with a
coverage artifact on every push to `master` / pull request.

## Connect real brokers (all free)

### Zerodha Kite (India, NSE/BSE)
1. Get an API key + secret at `developers.zerodha.com` (free; requires a Zerodha
   account).
2. In the extension popup → *Connect a real broker* → enter API key → **Get
   login URL** → log in → paste `request_token` from the redirect URL + secret
   → **Connect Kite**.
3. Live: the backend subscribes to the Kite ticker websocket and builds candles.
   Backtests use real NSE historical data. Bracket orders are placed as
   `product=BO` — one request carries entry + take-profit + stop-loss.

### Binance (global crypto)
1. Create API keys at binance.com (spot trading) — or use the **testnet**
   checkbox for fake money.
2. Paste in the popup → **Connect Binance**.
3. Live signals: the backend polls public OHLCV (no account needed). The BTC
   strategy ships by default; add strategies with `*USDT` symbols for others.

### OpenAlgo (Angel One, Fyers, Dhan, Upstox, …)
1. Self-host OpenAlgo (`pip install openalgo`, it's free), configure broker
   keys inside OpenAlgo.
2. Popup → OpenAlgo host + API key + broker → **Connect**.
3. punch.trade then routes execution through OpenAlgo's unified API (34+
   brokers), including GTT take-profit/stop-loss legs where supported.

Execution always routes through the selected broker — switch in the popup
(`paper` / `kite` / `binance` / `openalgo`). **Every order passes the risk
gate** (`docs/RISK.md`): modes `research`/`paper`/`live`, explicit arming
for real brokers (never persisted), signal TTL, idempotency keys, position
and daily-loss limits, stale-feed detection, emergency stop
(`POST /api/system/stop`). Real orders require `PUNCH_TOKEN` to be a
non-default value and LIVE mode + arming.

## Hosting (free tiers)

- **Local pilot**: run `python run.py` on your machine; friends load the
  extension with your server URL. No Chrome Web Store needed (Load unpacked).
- **24/7 server**: Oracle Cloud *Always Free* — includes a reserved static IP,
  which also satisfies SEBI's static-IP requirement for algo API access. Deploy
  with `pip install -r requirements.txt` + a systemd unit / `uvicorn`.
- **Simplest managed**: Render or Fly.io free tier (note: bind port 8000,
  change `HOST`/`PORT` in `app/config.py`; HTTPS recommended before real tokens
  travel over the internet).

## Security & compliance notes

- Broker access tokens are Fernet-encrypted at rest (`data/.secret` key is
  git-ignored; back it up or lose the vault). The extension only ever holds the
  punch.trade session token — never broker credentials.
- Signals and orders are appended to `data/signals.json` / `data/orders.json`
  — your audit trail.
- This is built for private use by you and people you trust. Signals with
  entry/TP/SL are personalized advice in most jurisdictions — SEBI (India),
  SEC (US), FCA (UK), MAS (SG) all regulate this area. Do not monetize or
  publicize without local counsel per market.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/strategies` | strategy list |
| `GET /api/strategies/leaderboard` | all strategies backtested + ranked (Sharpe, win rate, PF, DD) |
| `POST /api/strategies/{id}/backtest` | real backtest stats (win rate, drawdown, Sharpe, PF, avg win/loss) |
| `POST /api/orders` | risk-gated, idempotent bracket order (entry+TP+SL) |
| `GET /api/positions` · `GET /api/fills` | reconciliation / audit |
| `GET /api/signals/last` · `GET /api/signals/history` | signal feed / audit trail |
| `GET /api/analytics` | qty-weighted PnL, equity curve, win rate |
| `GET /api/candles?symbol=&limit=` | live bars for the chart panel |
| `POST /api/broker/{kite,binance,openalgo}/connect` | broker onboarding |
| `GET /api/system/status` | mode, arming, feed health, uptime |
| `POST /api/system/mode` · `POST /api/system/arm` · `POST /api/system/stop` | execution gates |
| `WS /ws/signals` | auth-message handshake → snapshot + live signal/position feed |

All REST endpoints require the `X-Punch-Token` header (default
`punch-demo-token`, set `PUNCH_TOKEN` in the environment). Full docs in
`docs/ARCHITECTURE.md`, `docs/RISK.md`, `docs/SECURITY.md`, `docs/AUDIT.md`.