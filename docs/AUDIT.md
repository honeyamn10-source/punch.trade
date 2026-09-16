# Security Review History

The repository was audited at commit `94ecc4d` (2026-08-16). Five areas were
flagged; all have since been addressed. Current controls are documented in
[SECURITY.md](SECURITY.md) and [RISK.md](RISK.md).

| Finding (original severity) | Status | Addressed by |
|---|---|---|
| **Execution safety** — `/api/orders` placed orders with no pre-trade checks | **Fixed** | Risk engine: `research`/`paper`/`live` modes, explicit ephemeral arming, emergency stop, signal TTL, idempotency keys, position/qty/daily-loss limits, stale-feed detection, circuit breaker, reconciliation gate for live brokers |
| **Authentication** — token travelled in query strings of URLs | **Fixed** | Header-only auth (`X-Punch-Token`), WebSocket auth message with 5 s handshake, session cookies httpOnly + double-submit CSRF |
| **Data honesty** — multi-TP PnL summed per-fraction without qty weighting; live positions never reconciled | **Fixed** | Qty-weighted realized PnL (`/api/analytics`); order ledger + typed broker reconciliation (`/api/execution/*`) gating live orders |
| **Resilience** — feed errors swallowed silently; strategies could fire on frozen data | **Fixed** | Per-symbol feed health + staleness limits, candle validation/quarantining, sanitized structured event log with request tracing |
| **Operation** — no health endpoint, no startup self-check, no config validation, no order idempotency | **Fixed** | `/api/system/*` + `/api/v1/system/health`, `config.validate_config()` at boot, SQLite durable store with idempotent replay |
| **OpenAI-compatible proxy** — `/v1/*` unauthenticated (loopback only) | **Resolved as designed** | Bind remains `127.0.0.1`; threat model in [SECURITY.md](SECURITY.md) assumes a trusted local user |

No fabricated or manipulated results were found in either review pass:
backtest/paper/live share the same `StrategyRunner`, and backtest fills are
conservative (SL-first, no lookahead entry).
