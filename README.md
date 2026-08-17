# punch.trade

[![CI](https://github.com/honeyamn10-source/punch.trade/actions/workflows/ci.yml/badge.svg)](https://github.com/honeyamn10-source/punch.trade/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.3.0-blue)](backend/app/version.py)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-208%20passing-brightgreen)](backend/tests)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A self-hosted quantitative trading workstation: strategy engine, honest
backtesting and research, risk-gated execution, and a Chrome extension that
overlays live signals on the chart pages you already use.

> **Non-custodial by design.** Orders execute through *your* broker account
> with *your* tokens. punch.trade never holds money — it stores encrypted
> access tokens at rest and proxies orders. No cloud service, no third
> parties, no data leaving your machine.

```
market data ──▶ strategy engine ──▶ signal ──▶ WebSocket ──▶ extension overlay
                       │
                   backtest            backtest       PUNCH button
                   (same code)                          │
                       │                                 ▼
                  win rate / drawdown          broker API (your account)
                                              entry + TP + SL as one bracket
```

## Features

- **Signal engine** — 9 declarative strategies (RSI reversal, MACD,
  Bollinger, Donchian, VWAP, golden cross, Stochastic Reversal, ADX Trend
  Rider, …) over a pure-Python indicator library. One evaluation path for
  live bars and backtests, so the numbers you see are the numbers you trade.
- **Honest backtesting** — execution-cost model (commission, slippage,
  spread), next-open entry with no lookahead, multi-level take-profit with
  partial fills, SL-first intrabar ordering, and one-position-one-trade PnL
  accounting (a TP1-then-stop is a **loss**, never a win).
- **Research layer** — chronological train/val/test splits, adaptive
  walk-forward consistency, parameter-stability perturbation, seeded
  Monte-Carlo bootstrap, per-regime performance, and a composite quality
  gate that decides whether a strategy may go live (never win-rate-only).
- **Risk gate on every order** — `research`/`paper`/`live` modes with
  explicit ephemeral arming, signal TTL, idempotency keys, max position /
  max qty / daily-loss limits, stale-feed detection, a circuit breaker on
  consecutive losses, and a broker-reconciliation gate for live orders.
- **Execution** — order-state ledger with typed reconciliation
  (unknown orders, untracked positions), stale-order detection, and
  one-position-one-trade closed-trade booking across 36+ brokers:
  Zerodha Kite, Binance (spot + testnet), and anything behind OpenAlgo.
- **Chrome extension** — signal overlay + one-tap bracket orders
  (entry + TP + SL) on Kite/Binance charts and a local demo page.
- **Full dashboard** — live WebSocket signal stream, positions, execution
  ledger, per-strategy research dossiers with local AI analysis, risk
  controls, strategy lifecycle ladder, and system health.
- **Secure by default** — header-only API auth, dashboard sessions
  (SHA-256 hashes at rest, CSRF-protected), per-IP rate limits, Fernet
  vault for broker credentials with one-click key rotation, sanitized
  structured logs, and security headers on every response.
- **Observable** — every response carries `X-Request-Id`; errors use a
  uniform `{error:{code,message,requestId}}` envelope; events land in a
  sanitized JSONL log; deep health + metrics under `/api/v1`.

## Quick start (zero cost, 5 minutes)

```bash
git clone https://github.com/honeyamn10-source/punch.trade.git
cd punch.trade/backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

1. Open `http://127.0.0.1:8000/demo` — a fake broker page for testing.
2. Load the extension: `chrome://extensions` → Developer mode → **Load
   unpacked** → the `extension/` folder.
3. Signals land on the chart via WebSocket; hit **PUNCH** to place a
   paper bracket order.

Dashboard: `http://127.0.0.1:8000/dashboard`.

## Connect real brokers

| Broker | Coverage | Setup |
|---|---|---|
| **Zerodha Kite** | NSE/BSE, BO bracket orders, real historical data | Free API key at `developers.zerodha.com` → login URL → connect in the popup |
| **Binance** | Spot + testnet, public OHLCV polling | API keys at binance.com; testnet checkbox for fake money |
| **OpenAlgo** | 34+ Indian brokers (Angel One, Fyers, Dhan, Upstox, …) | Self-host OpenAlgo, connect in the popup |

Real orders require `PUNCH_MODE=live`, a non-default `PUNCH_TOKEN`, and
explicit per-broker arming (never persisted).

## Documentation

| Doc | Covers |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | system overview, data flow |
| [API Reference](docs/API.md) | endpoints, auth, error envelope, WebSocket |
| [Security](docs/SECURITY.md) | threat model, sessions, CSRF, secrets |
| [Risk](docs/RISK.md) | modes, limits, circuit breaker, reconciliation |
| [Backtesting](docs/BACKTESTING.md) | execution-cost model, honesty rules |
| [Research](docs/RESEARCH.md) | dossiers, walk-forward, bootstrap, gates |
| [Strategies](docs/STRATEGIES.md) | configs, lifecycle ladder, scoring |
| [Signals](docs/SIGNALS.md) | generation, deterministic ids, state machine |
| [Execution](docs/EXECUTION.md) | ledger, reconciliation, closed trades |
| [Storage](docs/STORAGE.md) | SQLite, migrations, legacy import |
| [AI Analyst](docs/AI_ANALYST.md) | local Qwen analyst rules |
| [Dashboard](docs/DASHBOARD.md) | SPA sections, auth, refresh |
| [Observability](docs/OBSERVABILITY.md) | event log, request ids, health |
| [Operations](docs/OPERATIONS.md) | daily ritual, troubleshooting |
| [Deployment](docs/DEPLOYMENT.md) | install, env vars, going live |
| [Testing](docs/TESTING.md) | test layout, isolation, CI |
| [CI & Smoke](docs/CI_AND_SMOKE.md) | CI workflow + smoke script |

## Tech stack

- **Backend** — Python 3.11+, FastAPI + Uvicorn, SQLite (WAL), WebSockets
- **Brokers** — `kiteconnect`, `ccxt`, OpenAlgo REST
- **Crypto** — Fernet (vault at rest), SHA-256 (session hashes)
- **Extension** — Chrome Manifest V3
- **Quality** — ruff (lint + format), pytest + coverage, GitHub Actions CI

## Repository layout

```
backend/                FastAPI server (REST + WebSocket signal feed)
  app/
    engine.py           StrategyRunner — bar-driven, per-symbol dedup state
    backtest.py         honest execution-cost backtester
    research.py         chronological research dossiers
    trades.py           CompletedTrade / Fill model
    pnl.py              single source of truth for PnL + metrics
    signals.py          signal state machine
    strategy_status.py  lifecycle ladder + composite score
    execution.py        order ledger + reconciliation + closed-trade booking
    risk.py             modes, arming, limits, breaker, reconciliation gate
    security.py         sessions, CSRF, rate limits, sanitizer, headers
    db.py               SQLite store (WAL, migrations, legacy import)
    obs.py              event log, request tracing, counters
    ai/                 local Qwen analyst (offline-safe, whitelist-only)
    indicators.py       SMA / EMA / RSI / MACD / Bollinger / Donchian / VWAP
    strategies.py       declarative strategy configs (9 shipped)
    feed.py             live feeds: paper / binance / kite
    broker/             paper · kite · ccxt_bt · openalgo adapters
    vault.py            Fernet-encrypted broker token storage
    api.py              REST + WS + error envelope + /api/v1 surface
  static/dashboard.html full dashboard
  tests/                pytest suite (208 tests)
  scripts/              smoke.ps1 — boot + end-to-end smoke test
extension/              Chrome MV3 extension (overlay + popup)
docs/                   17 documentation guides
.github/workflows/ci.yml
```

## Quality

```bash
cd backend
python -m pytest tests -q     # 208 tests, per-test DB + data isolation
ruff check .                  # lint
ruff format --check .         # formatting
```

Runs automatically on every push and pull request via GitHub Actions
(lint + tests + coverage artifact). See `docs/TESTING.md`.

## Security & compliance

- Broker tokens are Fernet-encrypted at rest; the key (`data/.secret`) is
  git-ignored — **back it up or the vault is unrecoverable**. Rotate it at
  any time via `POST /api/vault/rotate-key` (or the popup button).
- The extension holds only your punch.trade session token — never broker
  credentials.
- Every order passes the risk gate; live execution additionally requires
  reconciliation with the broker.
- Built for private use by people you trust. Signals with entry/TP/SL may
  be regulated advice in your jurisdiction (SEBI, SEC, FCA, MAS, …).
  Do not monetize or publicize without local counsel. Trading involves
  substantial risk of loss; past backtest performance does not predict
  future results.

## License

[MIT](LICENSE) — see the LICENSE file for details.
