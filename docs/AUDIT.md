# punch.trade — Repository Audit

Date: 2026-08-16 · Commit audited: `94ecc4d` (working tree clean) · 19/19 tests passing.

Severity scale: **CRITICAL** (loss of money / loss of trust) · **HIGH** (correctness or
security gap that must be fixed before real use) · **MEDIUM** (should fix soon) ·
**LOW** (polish) · **INFO** (observation).

## Executive Summary

punch.trade is a functional signal engine prototype: 11 deterministic strategies run
against a live candle feed, emit signals over REST + WebSocket, and execute paper
orders with multi-TP partial fills. The core engine and backtester share one code path,
which is the single most important design property — it is sound and must be preserved.

The gap between "prototype" and "workstation" is concentrated in five areas:

1. **Execution safety (CRITICAL).** `/api/orders` will place an order on *any*
   connected broker with zero pre-trade checks. Real brokers (Kite/Binance/OpenAlgo)
   are re-connected automatically from the vault on startup. There is no execution
   mode, no arming step, no limits, no signal freshness check.
2. **Authentication (HIGH).** The API token travels in query strings of every REST
   and WebSocket URL (leaks into browser history, proxy logs, extensions UI).
3. **Data honesty (HIGH).** The dashboard equity curve and net PnL sum per-fraction
   `pnl_pct` events from multi-TP partial closes without qty weighting — a 3-level
   position overstates PnL by ~3x. Live broker positions are never reconciled, so
   real fills never reach the ledger.
4. **Resilience (HIGH).** Feed errors are swallowed silently; there is no staleness
   detection, so strategies can keep firing on frozen data. Audit files are appended
   with silent failure.
5. **Operation (MEDIUM).** No system health endpoint, no startup self-check, no
   config validation, no logging discipline, no order idempotency.

No fabricated or manipulated results were found. Backtest/paper/live share the same
`StrategyRunner`; backtest fills are conservative (SL-first, one TP level per bar,
fractions sum to one unit so its equity math is consistent).

## Architecture Map

```
backend/run.py                 uvicorn entrypoint (127.0.0.1:8000)
backend/app/
  config.py                    env-driven config (token, bars, slippage, telegram)
  feed.py                      LiveFeed: paper timer / binance poll / kite ticker
                               -> completed candles -> ingest_bar()
  engine.py                    StrategyRunner: per-symbol idle/active state machine,
                               Signal (id, targets, SL), dedup, condition eval
  strategies.py                11 strategies + compute_indicator/condition_met
  indicators.py                SMA/EMA/RSI/MACD/Bollinger/Donchian/VWAP/ATR/Stoch/ADX
  backtest.py                  replay of StrategyRunner, conservative fills, metrics
  api.py                       REST + WS hub, broker manager, vault restore,
                               signal/order/position JSONL ledgers, analytics,
                               leaderboard (60s cache), /api/candles, Ollama proxy
  broker/                      paper / kite / ccxt_bt(binance) / openalgo adapters
  vault.py                     encrypted broker-session store (.secret key)
  proxy.py                     /v1 OpenAI-compatible -> Ollama native (unauthenticated,
                               loopback only, documented)
backend/static/dashboard.html  workstation UI (tabs, leaderboard, lightweight-charts)
extension/                     MV3 overlay: background proxy, content overlay, popup
data/ (gitignored)             signals.json, orders.json, positions.json, .secret, vault.json
tests/test_core.py             19 tests (indicators, engine, backtest, paper broker)
```

Data flow: `feed -> ingest_bar -> engine.on_bar(series) -> Signal -> hub.broadcast`
and JSONL audit append. Orders: `POST /api/orders -> broker adapter -> order record`.

## Test Results

`python -m pytest tests -q` → **19 passed in 0.04s** (indicators incl. new
ATR/Stochastic/ADX, engine dedup, golden-cross two-series condition, multi-TP
backtest, paper broker partial closes).

## Findings

### CRITICAL

- **AUD-001 — No risk engine / execution mode.** `place_order` (api.py:354) routes
  to any adapter returned by `BrokerManager.get`. Kite/Binance/OpenAlgo adapters are
  auto-restored from the vault at startup (api.py:160-175). One API call = real
  order. No RESEARCH/PAPER/LIVE modes, no arming, no emergency stop, no position or
  daily-loss limits, no signal validation. *Fix: `app/risk.py` gate with typed
  rejections; LIVE requires explicit arming; arm state never persisted.*

- **AUD-002 — Token in URL query strings.** Every REST endpoint takes
  `token: Optional[str] = None` from the query string (api.py:235-485) and the
  WebSocket requires `?token=` (api.py:491). Extension background.js:33,72,84,92 and
  dashboard.html:213,303,430 embed it in URLs. Leaks via history/logs. *Fix:
  `X-Punch-Token` header for REST; WS `auth` message handshake with timeout.*

- **AUD-003 — No signal lifecycle.** `Signal` has no expiry (engine.py:23-38).
  `POST /api/orders` does not require or validate a `signalId`; the fallback path
  recomputes entry/targets from the current close (api.py:359-374). A signal emitted
  hours ago remains executable; manual orders need no signal at all. *Fix: signal
  TTL, signal_id validation, stale-signal rejection.*

- **AUD-004 — No order idempotency.** Identical `POST /api/orders` (double-click,
  network retry, extension reconnect) creates duplicate positions. *Fix:
  `signalId`/`clientRequestId` keys, duplicate detection against the order ledger.*

### HIGH

- **AUD-005 — PnL double-counting in analytics.** Equity curve (api.py:452-455) and
  `netPnlPct` (api.py:466) sum per-event `pnl_pct` from multi-TP partial closes
  without qty weighting. A qty-3 position closed in three TP1-fractions of 1 share
  books +1%/+2%/+3% → equity += 6% though the position made 2%. Backtest equity is
  correct (fractions of one unit, backtest.py:53-57). *Fix: weight events by
  `qty/qty_total` (paper broker must record `qty_total`).*

- **AUD-006 — Live broker positions never reconciled.** `closed_positions` is fed
  only by the paper broker's `on_bar` (feed.py:93-95). A Kite/Binance position
  closed by the market never enters the ledger or analytics. *Fix: periodic
  position polling per adapter (phase 2), or explicit "not reconciled" labeling.*

- **AUD-007 — Feed staleness invisible.** Binance loop swallows all errors
  (feed.py:143-156); paper loop suppresses history-seed failures (feed.py:111-114).
  No last-bar timestamp tracking, no health endpoint, no order gating on stale data.
  *Fix: per-symbol last-ingest timestamps, `/api/system/health`, stale-feed order
  rejection.*

- **AUD-008 — Order input validation missing.** `OrderReq` (api.py:123-131)
  accepts qty=0/-5, non-positive prices, empty symbols, any side string. *Fix:
  pydantic field constraints.*

- **AUD-009 — Silent failure everywhere.** `_append_json` swallows write errors
  (api.py:219-224) — the audit trail can silently lose records. Broker status and
  analytics catch-all `except: pass` (api.py:458-460). *Fix: at minimum log every
  swallowed error; typed domain errors.*

### MEDIUM

- **AUD-010 — Backtest ignores fees/slippage/gaps.** Live paper fills model
  slippage (paper.py:74) but backtest fills at exact levels with no costs
  (backtest.py:34-57). Optimistic vs. live by a constant slippage amount. Documented
  in backtest.py but not surfaced in metrics. *Fix: expose fee/slippage parameters
  (default 0) and label results; document in docs/BACKTESTING.md.*

- **AUD-011 — Backtest metrics are per-event, not per-trade.** With multi-TP, one
  position produces several events; Sharpe/win-rate mix position-level and
  event-level units. Non-annualized Sharpe on tiny samples has no confidence
  bounds. *Fix: report both per-event and per-position stats; minimum-sample
  warnings.*

- **AUD-012 — No strategy versions / signal schema version.** Strategy JSON and
  signal dicts are unversioned; changing a strategy silently changes historical
  comparability. *Fix: `strategy["version"]`, signal `schema_version`.*

- **AUD-013 — No logging discipline.** `print()` only; uvicorn output goes to
  files in the OS temp dir without rotation. *Fix: `logging` with rotating
  handler; structured startup log.*

- **AUD-014 — Config unvalidated.** Bad env values (negative BAR_SECONDS, token="")
  fail at runtime, not startup. `PUNCH_TOKEN` defaults to `punch-demo-token`
  (config.py:10). *Fix: startup self-check + validation; refuse to run with the
  default token if any broker other than paper is connected or LIVE is requested.*

- **AUD-015 — Extension token stored in chrome.storage.sync.** Plaintext, synced
  to the Google account (background.js:10, popup.js:4). *Fix: storage.local +
  per-session pairing (phase 2).*

- **AUD-016 — No rate limiting / request size limits.** Brute-force surface for
  the token (loopback-bound, low exposure today). *Fix: simple per-IP rate limit
  on /api/*.*

- **AUD-017 — Strategy "active" state can wedge.** Exit condition must fire to
  return to idle (engine.py:78-81); a stalled indicator leaves the strategy
  permanently muted. *Fix: exit-by-timeout fallback.*

- **AUD-018 — Kite/Binance/OpenAlgo adapters are unverified live.** `place_bracket`
  product-BO path and DOM/API specifics have never run against real accounts.
  *Mark "IMPLEMENTED — REAL BROKER VERIFICATION REQUIRED" in docs until exercised.*

### LOW / INFO

- **AUD-019 — /v1 Ollama proxy unauthenticated.** Loopback-bound and documented;
  acceptable for local use, must stay 127.0.0.1-only (proxy.py:14-15).
- **AUD-020 — `/api/health` exposes broker list without token.** Minor disclosure;
  acceptable for loopback.
- **AUD-021 — Demo page and dashboard contain no untrusted HTML injection** (esc()
  used in overlay); static mounts are limited to /static and /demo, /dashboard;
  data/ is not served. Good.
- **AUD-022 — No HTTP tests.** Tests are unit-level; REST/WS endpoints and the
  order path have no coverage. *Fix: FastAPI TestClient suite (in progress).*
- **AUD-023 — Vault key file permissions** on Windows rely on user profile ACLs;
  acceptable for local single-user, documented.
- **AUD-024 — Hub broadcasts use fire-and-forget tasks**; failures are silent
  (api.py:68-73). Acceptable but should log.

## Priority Fix Plan (from master prompt §262, first tranche)

1. ~~Audit~~ (this document)
2. Risk engine + modes + arming + emergency stop (AUD-001)
3. Order hardening: expiry, idempotency, validation (AUD-003/004/008)
4. Auth overhaul: header + WS handshake (AUD-002)
5. Feed health + /api/system/health (AUD-007)
6. PnL weighting fix (AUD-005)
7. Startup self-check + config validation (AUD-014)
8. Test suite for all of the above (AUD-022)
9. Docs: ARCHITECTURE / SECURITY / RISK / BACKTESTING
10. Later phases: reconciliation (AUD-006), per-trade metrics (AUD-011),
    strategy versions (AUD-012), logging (AUD-013), rate limits (AUD-016),
    storage.local pairing (AUD-015), SQLite migration, session auth,
    AI Lab (Qwen via Ollama, read-only), dashboard workstation redesign.