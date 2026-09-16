# Testing

All tests are plain pytest against the FastAPI app. Run:

```bash
cd backend
python -m pytest tests -q
```

## Layout

| File | Covers |
|---|---|
| `test_market_indicators.py` | canonical Candle model, validation/quarantine, timeframe normalization, quality tracker, regime classifier, 36 indicator edge cases (flat series, non-finite input, window boundaries) |
| `test_signals.py` | deterministic ids, state machine transitions, expiry, anti-wedge timeout |
| `test_backtest.py` | honest execution-cost backtester: next-open entry, SL-first same-candle, per-level TP fills, no double commission, END_OF_TEST; hand-computed metric examples (+100/−50/+25/−25 → PF 1.6667, expectancy 12.5) |
| `test_research.py` | chronological splits (never shuffled), adaptive walk-forward, parameter stability, seeded bootstrap, regime breakdown, quality gate |
| `test_strategy_status.py` | ladder transitions, composite score math, drift neutrality/degradation |
| `test_risk_extensions.py` | circuit breaker, fixed-fractional sizing, reconciliation gate, typed rejections |
| `test_safety.py` | auth (header + WS), TTL, idempotent orders, mode blocking, typed risk codes, feed health |
| `test_execution.py` | order state machine, ledger idempotency, stale→UNKNOWN, reconciliation (paper ok / unknown order closes gate), one-position-one-trade booking (TP1+SL symmetric → netPnl 0.0), breaker feed |
| `test_db.py` | migrations, WAL, transactions (rollback), CRUD, legacy archive-then-import + reports, idempotent re-import, bad-line tolerance |
| `test_security.py` | sessions (hash-only, expiry, revocation, purge), CSRF double-submit, login/API rate limits, sanitizer, security headers |
| `test_obs.py` | request ids, error envelope (typed codes, validation, retryAfter), metrics/health shapes, event log sanitization |
| `test_ai.py` | model auto-detection (no ollama / list failure / qwen pick), prompt whitelist leak-proofing, offline-safe error paths, success path |

## Isolation

`tests/conftest.py` gives every test its own SQLite DB + empty data dir
(including TestClient startup, which runs the real startup path — schema init,
legacy import, restores). Rate-limit state is cleared per test.

## CI

`.github/workflows/ci.yml` runs the suite on push; `scripts/smoke.ps1`
additionally boots the server and exercises auth, backtest, research, risk
and execution endpoints against the live process (see CI_AND_SMOKE.md).