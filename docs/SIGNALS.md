# Signals

## Generation

The engine evaluates strategies on **closed candles only** (`market.Candle`,
`closed=True` semantics) unless a strategy declares intrabar capability. Every
signal gets a **deterministic id**:

```
sha1(strategy_id | version | symbol | timeframe | close_time | side)[:16]
```

Deterministic ids mean feed reconnects, dashboard reconnects and process
restarts can never duplicate a signal (deduped in `api.on_signal`).

## State machine

`app/signals.py` owns the lifecycle:

```
CANDIDATE ──> ACTIVE ──> EXECUTED ──> CLOSED
   │            │
   │            ├──> PARTIAL      (one or more TP levels filled)
   │            ├──> EXPIRED      (TTL elapsed, see config.SIGNAL_TTL_SECONDS)
   │            └──> INVALIDATED  (signal revoked)
   └──> REJECTED (execution refused by the risk engine)
```

Transitions are validated (`Signal.with_status`) — illegal transitions raise.
`expired_at` is set at expiry so consumers can display remaining validity.

## Delivery

- REST: `GET /api/signals/last` (and `/api/v1/signals/last`)
- WebSocket: `/ws/signals` — `type: signal` events, `type: signal_update`
  broadcasts for EXECUTED / REJECTED / status changes; first message after
  auth is a `snapshot` of recent signals
- Optional Telegram push when `PUNCH_TELEGRAM_BOT_TOKEN` + `PUNCH_TELEGRAM_CHAT_ID` are set
- Persistence: JSONL audit (`data/signals.json`) + SQLite `signals` table (write-through)

## Execution

`POST /api/orders` with `signalId` is idempotent per signal (replay returns the
original record with `duplicate: true`). The risk engine can reject with a
typed code — `SIGNAL_NOT_FOUND`, `SIGNAL_EXPIRED`, `MODE_BLOCKED`,
`FEED_STALE`, `MAX_POSITIONS`, `MAX_QTY`, `DAILY_LOSS_LIMIT`, `BREAKER_OPEN`,
`RECONCILIATION_FAILED`, `NOT_ARMED` — and the signal is marked REJECTED with
that code. Successful execution marks the signal EXECUTED and broadcasts.