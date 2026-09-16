# API Reference

Base URL `http://127.0.0.1:8000`. All endpoints under `/api` and `/api/v1`
require the header `X-Punch-Token` (the API token; `punch-demo-token` by
default — LIVE mode refuses the default token). The token travels **only** in
headers or the WS auth message, never in URLs.

## Error envelope

Every error is a uniform JSON object:

```json
{"error": {"code": "FEED_STALE", "message": "market feed for X is stale - no bar in 91s (limit 30s)", "requestId": "934cc7c61a284e13"}}
```

Typed codes are preserved (e.g. `SIGNAL_EXPIRED`, `MODE_BLOCKED`,
`RATE_LIMITED` with `retryAfter`); validation failures are `VALIDATION_ERROR`
(422); unknown server failures are `HTTP_500` with a request id for log
correlation. Every response carries `X-Request-Id`.

## Endpoints

### System
| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | shallow liveness (no auth) |
| GET | `/api/v1/system/health` | deep: db + feed + brokers + uptime |
| GET | `/api/system/status` | mode, armed, breaker, feed health, ledger flags, version |
| GET | `/api/v1/system/metrics` | counters, ledger/trade aggregates, risk state |
| GET | `/api/system/storage` | SQLite engine, WAL, schema, counts |
| POST | `/api/system/login` | dashboard session (rate-limited 5/min/IP) |
| POST | `/api/system/logout` | revoke session (requires session cookie + CSRF) |
| POST | `/api/system/stop` | emergency stop → research mode, disarm all |

### Market data
| Method | Path | Notes |
|---|---|---|
| GET | `/api/candles?symbol=&limit=` | latest bars |
| GET | `/api/analytics` | qty-weighted realized PnL from closed positions |
| GET | `/api/v1/market/candles?symbol=&timeframe=&limit=` | bars; crypto bars carry `funding` (perp settlement rate at/before bar open, when available) |

### Signals & strategies
| Method | Path |
|---|---|
| GET | `/api/signals/last` |
| GET | `/api/signals/history?limit=&offset=` |
| GET | `/api/strategies` |
| GET | `/api/strategies/status` |
| GET | `/api/strategies/leaderboard` |
| POST | `/api/strategies/{id}/backtest` |
| POST | `/api/research/{id}` |

Paginated list endpoints (`limit` capped at 500, default 100 for signals,
200 for ledger/order history) return `{total, limit, offset}` meta alongside
the records.

### Trading
| Method | Path | Notes |
|---|---|---|
| POST | `/api/orders` | `{broker, signalId?, clientRequestId?, symbol?, side, qty, entry?, targetPrice?, stopLoss?}` — idempotent per signal/clientRequestId |
| GET | `/api/positions` | open positions per broker |
| GET | `/api/fills` | recent fills |
| GET | `/api/execution/ledger?limit=&offset=` | order ledger |
| GET | `/api/execution/trades` | closed trades |
| POST | `/api/execution/reconcile` | reconcile a broker; gates live orders |
| GET | `/api/execution/reconciliation` | reconcile all connected brokers |

### Risk
| Method | Path | Notes |
|---|---|---|
| GET | `/api/risk/state` | mode, armed, breaker, recon gate, limits |
| GET | `/api/risk/shield` | risk state incl. `shieldOn` |
| POST | `/api/risk/shield` | `{on: bool}` — shield toggles the pre-trade gate; while on, orders → `SHIELD_ACTIVE` 409 |
| POST | `/api/risk/sizing` | `{equity, riskPct, entry, stop}` → `{qty, riskAmount, riskPerShare}` |
| POST | `/api/risk/breaker/reset` | manual circuit-breaker reset |
| POST | `/api/risk/arm` | `{broker}` — arm a real broker in LIVE mode |
| POST | `/api/system/stop` | emergency stop |

### Research / analysis
| Method | Path | Notes |
|---|---|---|
| GET | `/api/stress/scenarios` | 17 named stress scenarios (type, severity, probability) |
| POST | `/api/stress/run` | `{metrics: {netReturn, maxDrawdownPct, sharpe, costBps, volatility}, scenarioTypes?, portfolioState?}` → per-scenario pass/fail + totals |
| POST | `/api/v1/analysis/monte-carlo` | `{returns: [...], iterations?, initialEquity?, seed?, method?}` → equity/drawdown distributions, bootstrap expectancy, `real_edge` (needs ≥10 returns) |

### AI
| Method | Path |
|---|---|
| GET | `/api/ai/status` |
| POST | `/api/ai/analyze/{strategy_id}` |

### Brokers
| Method | Path | Notes |
|---|---|---|
| POST | `/api/broker/kite/login-url` / `/api/broker/kite/connect` | Kite session |
| POST | `/api/broker/{name}/save` / `/api/broker/{name}/connect` | vault-stored credentials (Fernet) |
| GET | `/api/broker/status` | connected adapters |
| POST | `/api/broker/{name}/orders/close` | manual close |
| POST | `/api/vault/rotate-key` | re-encrypt vault under a fresh key |

### Versioned aliases
`/api/v1/strategies`, `/api/v1/signals/last`, `/api/v1/risk/state`,
`/api/v1/execution/trades`, `/api/v1/system/storage`, `/api/v1/system/status`,
`/api/v1/strategies/status`, `/api/v1/strategies/leaderboard`,
`/api/v1/ai/status`, `/api/v1/ai/analyze/{id}`, `/api/v1/orders`,
`/api/v1/backtest/{id}`, `/api/v1/research/{id}`, `/api/v1/execution/reconcile`.

## WebSocket

`/ws/signals` — first message must be `{"type":"auth","token":"..."}`; then
`{"type":"subscribe","channel":"signals"}`. Events: `signal`, `signal_update`,
`position`, `snapshot`. Rate limits: API 240/min/IP, login 5/min/IP (429 +
`Retry-After`).