# Architecture

punch.trade is a self-hosted trading workstation: a deterministic signal
engine, a paper/live broker layer, a dashboard, and a Chrome overlay for
one-tap execution. It runs locally at `http://127.0.0.1:8000` (loopback
only).

## Data flow

```
market feed ──completed candle──▶ ingest_bar(symbol, bar)
                                    ├─▶ StrategyRunner.on_bar(series) ──▶ Signal
                                    └─▶ paper broker on_bar (TP/SL monitor)
Signal ──▶ hub.broadcast (WS) + JSONL ledger (data/signals.json)
Order  ──▶ POST /api/orders ──risk gate──▶ broker adapter ──▶ order ledger
Close  ──▶ on_position_close ──▶ analytics ledger (data/positions.json)
```

- Indicators are computed once per completed candle, conditions checked at
  the latest bar only. Strategies run on **closed candles**.
- The same `StrategyRunner` is used live and in the backtester — one code
  path for both, so backtest numbers describe live behaviour (with the
  fill-model caveats in BACKTESTING.md).
- Multi-TP: signals carry up to 3 target levels; the paper broker closes an
  equal fraction per level (SL-first, conservative).

## Modules (backend/app)

| Module      | Responsibility |
|-------------|----------------|
| `config.py` | env-driven config + `validate_config()` startup self-check |
| `feed.py`   | LiveFeed: paper timer / binance poll / kite ticker → completed candles; per-symbol `last_ts` health |
| `engine.py` | StrategyRunner state machine, Signal (id, targets, SL, `expiresAt`), dedup |
| `strategies.py` | 11 strategies + indicator/condition registry |
| `indicators.py` | SMA/EMA/RSI/MACD/Bollinger/Donchian/VWAP/ATR/Stochastic/ADX |
| `backtest.py` | replay of StrategyRunner with conservative fills + metrics |
| `risk.py`    | execution modes, arming, emergency stop, typed pre-trade gate |
| `api.py`     | REST + WS (header auth, WS auth handshake), broker manager, ledgers, analytics, leaderboard, `/api/system/*`, `/api/candles` |
| `broker/`    | paper / kite / ccxt-binance / openalgo adapters |
| `vault.py`   | encrypted broker-session store (`.secret` key, `data/vault.json`) |
| `proxy.py`   | `/v1` OpenAI-compatible → Ollama native proxy (thinking disabled) |

## API surface (v0.3.0)

REST auth: `X-Punch-Token: <token>` header (never query strings). WS auth:
`{"type":"auth","token":...}` within 5s of connect, else close 4401.

- `GET  /api/health` — unauthenticated liveness
- `GET  /api/strategies`, `GET /api/strategies/leaderboard`
- `POST /api/strategies/{id}/backtest`
- `POST /api/orders` — risk-gated, idempotent (signalId/clientRequestId)
- `GET  /api/positions`, `GET /api/fills`, `GET /api/orders/history`
- `GET  /api/signals/last`, `GET /api/signals/history`
- `GET  /api/analytics` — qty-weighted PnL (multi-TP honest)
- `GET  /api/candles?symbol=&limit=`
- `POST /api/broker/{kite,binance,openalgo}/connect`, `GET /api/broker/status`
- `GET  /api/system/status`, `POST /api/system/mode`, `POST /api/system/arm`,
  `POST /api/system/stop`
- `WS   /ws/signals` — auth → `auth_ok` → `snapshot` + `signal` + `position`
- `/dashboard`, `/demo`, `/static/*`, `/v1/*` (Ollama proxy)

## Clients

- `backend/static/dashboard.html` — workstation UI (Overview, Strategies,
  Positions, Orders, Brokers, System), lightweight-charts candlesticks.
- `extension/` — MV3 overlay: `background.js` (WS + order proxy, token in
  headers/messages only), `content.js` (overlay on Kite/Binance/demo),
  `popup.js` (settings).
- `tests/` — `test_core.py` (engine/indicators/backtest/paper) +
  `test_safety.py` (risk/auth/idempotency/expiry/health/PnL). Run with
  `python -m pytest tests -q` from `backend/`.

## Persistence

JSONL append-only ledgers under `data/` (gitignored): `signals.json`,
`orders.json`, `positions.json`. Broker sessions in the encrypted
`vault.json`. Restart re-indexes order idempotency keys and restores the
analytics ledger. SQLite migration is planned (see AUDIT.md).

## Known limits

- The engine feed is the paper (synthetic) feed; real-broker data feeds
  are not yet wired into strategy evaluation — see RISK.md.
- Kite/Binance/OpenAlgo adapters are implemented but
  **REAL BROKER VERIFICATION REQUIRED**.