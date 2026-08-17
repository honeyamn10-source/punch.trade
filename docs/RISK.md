# Risk engine

Every order passes the pre-trade gate in `backend/app/risk.py` before it
reaches a broker adapter. Rejections are **typed** — `detail.code` is
stable for clients, `detail.message` is human-readable.

## Execution modes

| Mode | Behaviour |
|------|-----------|
| `research` | all order execution blocked; signals still stream |
| `paper` | only the paper broker may execute (default) |
| `live` | paper + real brokers, but each real broker must be **armed** first |

- Set: `POST /api/system/mode {"mode": ...}` (or `PUNCH_MODE` env).
- Arm: `POST /api/system/arm {"broker": "kite"}` — LIVE mode only, broker
  must be connected. Arming is **never persisted**: restart → disarmed.
- Emergency stop: `POST /api/system/stop` → research mode + disarm all.
- Tripwire: LIVE mode is refused while `PUNCH_TOKEN` is the demo token.

## Pre-trade checklist (order of evaluation)

1. `MODE_BLOCKED` — research mode
2. `BROKER_NOT_ALLOWED` / `NOT_ARMED` — real broker without LIVE+arm
3. `FEED_STALE` — no bar for the symbol within the staleness window
4. `SIGNAL_NOT_FOUND` / `SIGNAL_EXPIRED` — signal must exist and be fresh
   (TTL `PUNCH_SIGNAL_TTL`, default 300 s)
5. `DUPLICATE_ORDER` (idempotent replay, HTTP 200 + `duplicate: true`)
6. `INVALID_QTY` / `MAX_QTY` / `MAX_POSITIONS` / `DAILY_LOSS_LIMIT` /
   `INVALID_PRICE` — from `risk.enforce_limits`

Idempotency keys: `signalId` (preferred) or `clientRequestId`; keys are
re-indexed from `data/orders.json` on restart, so retries after a restart
cannot double-execute.

## Limits (env, defaults in parentheses)

- `PUNCH_MAX_POSITIONS` (5) — open positions per broker
- `PUNCH_MAX_QTY` (10000) — per order
- `PUNCH_DAILY_LOSS_PCT` (5.0) — daily realized loss, % of the paper
  ledger (today's closes, qty-weighted)
- `PUNCH_FEED_STALE_AFTER` (30 s paper / 180 s live feeds)

## Honesty notes

- The engine's market feed is the **paper (synthetic) feed**. Real-broker
  data feeds are not wired into strategy evaluation yet — LIVE mode
  currently executes signals derived from the simulated market. Treat
  LIVE arming as a dry-run of the execution plumbing until the real feed
  phase lands (see AUDIT.md AUD-006/AUD-007 and the roadmap).
- Daily-loss accounting only covers paper-ledger closes. Live-broker
  reconciliation is a pending phase (AUD-006) — until then, the gate is
  conservative by refusing anything it cannot verify.