# PUNCH.TRADE FINAL BUILD REPORT (#59)

**Date:** 2026-08-16 · **Status:** COMPLETE — all 59 phases shipped · **Suite:** 181/181 passing · **Smoke:** 18/18 · **Live:** verified at `http://127.0.0.1:8000` (paper)

## 1. What was built

A self-hosted, non-custodial trading-signal platform: strategy engine → honest
backtester → research layer → risk controls → paper/live execution with broker
reconciliation → dashboard + Chrome extension overlay. Orders run through the
user's own broker accounts; the backend never holds money.

```
market data ──> engine (closed-candle only) ──> Signal ──> WS ──> extension overlay
    │                                              │
backtest (same code, execution costs)        risk engine (modes, limits,
    │                                          breaker, reconciliation gate)
    └── research (chronological) ──> status ladder ──> orders (idempotent)
                                                          │
                                             ledger ──> reconcile ──> one-position-one-trade
```

## 2. Pipeline (19 commits, 355628b → 5ab2e21)

| Phase group | Deliverables | Commit |
|---|---|---|
| Foundation | canonical Candle, quarantining, TF normalization, regime classifier, indicator library w/ 36 edge cases, engine dedup/anti-wedge, signal state machine, honest backtester (next-open entry, SL-first, cost model), risk core (modes/limits/arm/stop) | 355628b (execution) and prior |
| Risk extensions | fixed-fractional sizing, circuit breaker, reconciliation gate, typed rejections, risk endpoints | cf58621 |
| Execution | order ledger state machine, idempotency, stale→UNKNOWN, typed reconciliation, closed-trade booking, feed open_time fix | 355628b |
| Durable store | SQLite (WAL, migrations, thread-local), legacy archive-then-import w/ per-file reports, startup restores, `/api/system/storage` | 487044f |
| Security | sessions (hash-only, 12 h TTL, revocable), CSRF double-submit, rate limits (login/API), sanitizer, headers, login/logout | f85c503 |
| AI analyst | local Qwen auto-detect (never downloads), whitelist-only prompts, offline-safe, `/api/ai/*` | 3671a14 |
| Observability + v1 | event log, request tracing, uniform `{error:{code,message,requestId}}` envelope, deep health, metrics, `/api/v1/*` | f52ff35 |
| Dashboard | full SPA: Signals / Positions / Execution / Research+AI / Risk / Strategies / System | 4fe4b15 |
| Docs + CI | 17 docs + README index, header-auth CI, 18-check e2e smoke script | 5ab2e21 |

## 3. Honesty rules (the hard parts, done right)

- **One position = one trade.** TP1-then-stop is booked as one `CompletedTrade`
  with the final net PnL — never as two "wins".
- **Classification by FINAL NET PnL** (`CompletedTrade.close`, single source).
- **Next-open entry, SL booked first** on same-candle collisions; execution
  costs (commission/slippage) applied to every leg; no double commission;
  END_OF_TEST close.
- **Research splits are chronological — never shuffled**; walk-forward windows
  and stability perturbations never touch the test split; bootstrap is seeded.
- **Status score ≠ win-rate**: `0.6 quality + 0.2 stability + 0.2 drift`, and
  `LIVE_DEGRADED` when live mean net PnL < 0.5× backtest expectancy (after 3
  trades).
- **Live data integrity**: legacy bars fixed (`open_time` derivation),
  staleness gates (paper 30 s / live 180 s → `FEED_STALE`), quality tracker.
- **No credential leakage to the AI**: prompt whitelist contains research
  fields only — verified by leak-proof tests. Vault contents never leave the
  process.
- **Arming is ephemeral**: LIVE mode refuses the default token, arming is never
  persisted, emergency stop → research mode, reconciliation must pass before
  live orders.

## 4. Security posture

Sessions hashed (SHA-256) at rest, httpOnly cookie, CSRF double-submit,
per-IP sliding-window rate limits with `Retry-After`, control-char sanitizer +
512-char caps on every input, security headers, Fernet-encrypted broker
tokens, SQLite WAL durability, `data/` fully gitignored (DB, vault key, event
log, archive — verified via `git check-ignore`), zero hardcoded credentials in
code (verified by grep sweep). Full threat model: `docs/SECURITY.md` +
`docs/AUDIT.md`.

## 5. Test & verification evidence

- **181/181 tests, 6 warnings** — `python -m pytest tests -q` (12.5 s max).
- **Smoke: 18/18** — `scripts/smoke.ps1` boots the real server on a throwaway
  DB and exercises auth, envelope, health/metrics/storage, signals, ladder,
  backtest, research, sizing, paper order → ledger → reconcile, trades, AI
  status, dashboard, session login/logout.
- **Live-verified this session**: deep health ok; `sqlite wal schema=2` with
  433 signals / 79 orders / 19 positions / 15 trades migrated; paper feed
  fresh (bars ≈15, age ≈1.2 s); real Qwen analysis in 6.8 s (honest
  "FAIL 38/100" verdict); 429 rate limiting with retryAfter; 401/422/404
  envelopes; broker reconciliation `ok:true`; order FILLED → ledger → closed
  trades; session login/logout with CSRF.
- **CI**: `.github/workflows/ci.yml` runs the suite + a header-auth curl smoke
  of every subsystem on push/PR (ubuntu, Python 3.12).

## 6. Known honest limits

- Paper market data is synthetic → backtests/research are illustrative, not
  predictive (sample quality is reported; ladder refuses thin-sample
  promotions).
- Real broker adapters (Kite/Binance/OpenAlgo) are implemented but
  uncredentialed; live-account verification requires the user's credentials.
- Local Ollama qwen2.5-coder:7b/16k installed and used; without a model the
  analyst degrades to a clear hint, never a download.
- GitHub push pending `gh auth login` (local history complete).

## 7. How to run

```bash
cd backend
pip install -r requirements.txt
python run.py                # http://127.0.0.1:8000/dashboard
powershell -File scripts\smoke.ps1   # e2e verification
```

Env: `PUNCH_TOKEN`, `PUNCH_MODE` (research|paper|live), `PUNCH_DB_PATH`,
`PUNCH_OLLAMA_MODEL`… — full table in `docs/DEPLOYMENT.md`.

## 8. Documentation map

17 docs under `docs/` (ARCHITECTURE, API, SECURITY, AUDIT, RISK,
BACKTESTING, RESEARCH, STRATEGIES, SIGNALS, EXECUTION, STORAGE, AI_ANALYST,
DASHBOARD, OBSERVABILITY, OPERATIONS, DEPLOYMENT, TESTING, CI_AND_SMOKE) +
indexed README. Strategy notes: 9 declarative strategies, quality gate
rejects thin samples, walk-forward & bootstrap & regime breakdown per
dossier.

**Verdict:** phase #1–#59 delivered. The platform is honest, safe to run in
paper, auditable, and fully documented — production-ready for live once the
user supplies real broker credentials and performs a credentialed
reconciliation.