# Execution Layer

`app/execution.py` is the bridge between "we decided to trade" and "the broker
did something": an **order ledger**, **reconciliation**, and **closed-trade
booking**. Its core invariant: one position = one trade.

## Order state machine

```
PENDING ──> SUBMITTED ──> FILLED
                 │──> REJECTED
                 │──> CANCELLED
SUBMITTED ──timeout(60s)──> UNKNOWN   (must be resolved by reconciliation)
UNKNOWN ──> FILLED | CANCELLED
```

Transitions are typed (`transition()` raises on illegal moves). `PENDING →
FILLED` is legal because the paper broker fills atomically. The ledger is
keyed by the **broker's order id** (paper uses the position id), so paper
close events match their order.

## Reconciliation

`reconcile(broker, adapter)` compares the ledger against the broker's own
position view. Mismatches are typed:

- `BROKER_UNAVAILABLE` — broker API failed, state cannot be proven
- `UNTRACKED_POSITION` — broker holds a position the ledger never asked for
- `UNKNOWN_ORDER` — a ledger order is in the UNKNOWN state

While mismatches exist, `risk.set_reconciliation_ok(False)` — the risk engine
refuses **new LIVE orders** (paper stays usable). Reconciliation state is
exposed via `POST /api/execution/reconcile`, `GET /api/execution/reconciliation`
and `/api/risk/state`.

## Closed trades

`record_closed_trade(position_id, events)` folds one position's exit events
(TP1/TP2/TP3 partial fills + the final close) into **one** `CompletedTrade`
(`app/trades.py`) — the PnL math happens only in `CompletedTrade.close`
(central source, see PNL.md via BACKTESTING.md). Fills carry reasons
`ENTRY/TP1/TP2/TP3/STOP/MANUAL/END_OF_TEST/LIQUIDATION`; a position without a
final close event is ignored (never booked). Each booked trade:

- is appended to `data/trades.json` (audit)
- is persisted to the SQLite `trades` table (write-through)
- feeds `risk.record_trade_result` (circuit breaker)

## Endpoints

- `GET /api/execution/ledger` — the full order ledger
- `GET /api/execution/trades` — closed trades (newest last)
- `POST /api/execution/reconcile` — reconcile one broker
- `GET /api/execution/reconciliation` — reconcile all connected brokers
- `GET /api/v1/system/metrics` — ledger/trade aggregates

## Restart safety

Startup restores the ledger and trades from SQLite (`execution.restore()`);
legacy audit rows are normalized (missing status → UNKNOWN). Idempotency keys
(signal/client request) are re-seeded from the durable order store so a retried
request after a restart cannot double-execute.