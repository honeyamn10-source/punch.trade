# Operations

## Modes

| Mode | Signals | Paper orders | Real orders |
|---|---|---|---|
| `research` | yes | **blocked** (409 `MODE_BLOCKED`) | blocked |
| `paper` (default) | yes | yes (paper broker) | blocked |
| `live` | yes | yes | only for **armed** brokers |

`POST /api/system/stop` is the emergency stop: mode → `research`, everything
disarmed, signals keep streaming. Restart restores the configured mode but
**never re-arms** brokers — arming is deliberately ephemeral.

## Risk controls (all enforced at order time by `app/risk.py`)

- signal TTL (`SIGNAL_TTL_SECONDS`) → `SIGNAL_EXPIRED`
- stale feed (paper 30 s / live 180 s) → `FEED_STALE`
- max open positions / max qty → `MAX_POSITIONS` / `MAX_QTY`
- daily realized-loss % (paper ledger) → `DAILY_LOSS_LIMIT`
- circuit breaker: 3 consecutive losses open it; a win closes it; manual
  reset via `POST /api/risk/breaker/reset` (the only other way)
- reconciliation gate: LIVE orders blocked while the ledger disagrees with
  the broker (`RECONCILIATION_FAILED`)
- fixed-fractional sizing helper: `POST /api/risk/sizing` →
  qty = equity × riskPct / |entry − stop|, capped at `MAX_QTY`

## Daily ritual (paper / live)

1. Check `/api/v1/system/health` (db + feed ok).
2. Look at `/api/strategies/status` — promote only RESEARCHED strategies.
3. Reconcile (`/api/execution/reconcile`), review mismatches.
4. Review `/api/execution/trades` — every trade is one completed position,
   classified by **final net PnL** (TP1-then-stop = LOSS, never a win).
5. Read the AI assessment (`/api/ai/analyze/{id}`) before changing anything.

## Known honest limits

- Paper-market data is synthetic; backtests and research on it are
  illustrative, not predictive (the research layer reports sample quality
  and the status ladder refuses promotions on thin samples).
- Real broker verification (Kite / Binance / OpenAlgo) requires credentials;
  integration is implemented but not credentialed here.
- The AI analyst needs a local qwen2.5 model; without one it degrades to a
  clear "install me" hint — the app never downloads models.

## Logs

- `data/logs/access.log` / `data/logs/error.log` — uvicorn access + error
  logs (rotating, 5 MB × 3 backups) plus console.
- `data/logs/events.jsonl` — structured, sanitized event log (requests,
  signals, orders, errors) with `requestId` for correlation; every HTTP
  response carries the same id in `X-Request-Id`.
- `data/` also holds `punch.db` (SQLite, WAL), the encrypted `vault.json`,
  the key file `.secret`, and archived legacy JSONL under `data/archive/`.

## Credential hygiene

- Broker credentials live only in the Fernet-encrypted vault (`vault.json`,
  key in `.secret`, both never shipped). The API never returns stored
  secrets.
- Rotate the at-rest key on demand: `POST /api/vault/rotate-key` (or the
  popup button). Broker sessions stay valid; only the wrapping key changes.
- Renew short-lived broker sessions via the popup *Connect* flows; arming is
  always ephemeral (never persisted).

## Troubleshooting

- **Orders 409 FEED_STALE** — feed not started yet; wait for bars
  (`/api/system/status` → feeds).
- **429 RATE_LIMITED** — slow down; the envelope carries `retryAfter`.
- **Login 401** — wrong `X-Punch-Token`; check `PUNCH_TOKEN`.
- **`close_time <= open_time` quarantined bars** — provider data quirk;
  bars are counted as `invalid` and never fed to strategies (auditable).
- **DB locked / busy** — another process holds the file (e.g. a second
  backend instance); only one instance may run.