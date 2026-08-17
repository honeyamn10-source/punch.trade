# Dashboard

`backend/static/dashboard.html`, served at `http://127.0.0.1:8000/dashboard`.
A single-file SPA (vanilla JS, no build step).

## Sections

- **Signals** — live WebSocket stream with status chips, age, targets, and a
  one-tap Execute button (posts `signalId` → idempotent). Aggregate stats
  (total / active / executed / rejected).
- **Positions** — open positions + recent fills (paper).
- **Execution** — order ledger (state machine statuses), closed trades
  (one position = one trade, net PnL), reconcile button with mismatch details,
  live reconciliation gate indicator.
- **Research + AI** — strategy picker; runs the research dossier (quality
  gate passed/score + reasons, metrics, walk-forward consistency, bootstrap,
  regime breakdown) and the local AI assessment with model + elapsed time.
- **Risk** — mode / armed / consecutive losses / breaker / reconciliation
  gate / max positions; fixed-fractional sizing calculator; breaker reset
  (danger).
- **Strategies** — lifecycle ladder (status, score, reason, promotions) and
  the honest leaderboard (win rate, net %, max DD, sharpe, PF, trades).
- **System** — SQLite storage (engine, WAL, schema, per-table counts),
  operational metrics (requests, errors, ledger aggregates), feed health per
  symbol (bars, last-bar age, quality, staleness, errors), deep health.

## Auth

- Primary: API token stored in `localStorage`, sent as `X-Punch-Token` on
  every call and in the WS auth message.
- Dashboard session: "Session login" calls `/api/system/login` → httpOnly
  `punch_session` cookie (12 h, revocable) + `punch_csrf` cookie; "Logout"
  revokes with the CSRF header. CSRF applies only to cookie-authenticated
  requests; header-token calls are not CSRF-relevant.
- Errors surface the typed envelope code + message (e.g. `RATE_LIMITED`).

## Behavior

15 s auto-refresh (paused when the tab is hidden), WS reconnect with backoff,
WebSocket `signal_update`/`position` events trigger live refreshes of the
relevant panels. Everything is read-only except explicit buttons
(Execute / Reconcile / Research / AI / Size / Reset breaker / Stop).