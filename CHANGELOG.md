# Changelog

All notable changes, newest first. Versioned from `backend/app/version.py`
(single source; API health exposes `version` + `gitCommit`).

## [0.3.0] — 2026-08-16

### Added
- GitHub Actions CI (`.github/workflows/ci.yml`): ruff check/format + full
  pytest suite with coverage artifact on push/PR.
- `POST /api/vault/rotate-key`: re-encrypt the broker credential vault under
  a fresh Fernet key (key file rotated after the new vault file is written,
  so a mid-crash leaves the old key valid). Button in the extension popup.
- Pagination (capped `limit` + `offset`, with `total`/`limit`/`offset` meta)
  on `/api/signals/history`, `/api/orders/history`, `/api/execution/ledger`.
- uvicorn access + error logs written to `data/logs/access.log` and
  `data/logs/error.log` (rotating files) in addition to the console.
- API compatibility suite (`backend/tests/test_api_compat.py`): schema locks
  for health/metrics/orders/positions/fills/analytics/research/risk/ai/v1
  aliases, error envelope (404 + 405), and requestId correlation
  (`X-Request-Id` echo + body `requestId` match).
- Vault tests: at-rest encryption, save/load/delete round-trip, key rotation
  preserves credentials.

### Changed
- FastAPI `on_event("startup")` migrated to an `asynccontextmanager` lifespan
  (startup + shutdown: feed loop stop, SQLite connections closed). The
  `on_event` deprecation warning is gone.
- 405 (method not allowed) responses now use the uniform
  `{error:{code,message,requestId}}` envelope instead of the plain Starlette
  response.
- `execution.restore()` synthesizes a stable `id` for legacy audit rows
  (mirroring `db._row_id` precedence: `id` → `result.orderId` → `signalId`),
  fixing a `KeyError: 'id'` in `/api/execution/reconcile` whenever the
  database contained pre-migration audit-shaped rows.
- Test isolation hardened: module-scoped `TestClient` fixtures are
  function-scoped so app startup always runs under per-test SQLite/legacy
  paths (previously tests could read/write the live `data/` directory).
- `test_reconcile_paper_ok_via_api` enters the app context and seeds feed
  `last_ts`; it previously passed only because the app never actually started
  in that test.

### Fixed
- `execution.reconcile` crash on legacy audit rows without an `id` key.
- Race where an order placed right after startup could be rejected
  `FEED_STALE` before the feed seeded its first synthetic bars.

## [0.2.0] — earlier

- Storage: SQLite durable store (WAL, versioned migrations, thread-local
  connections), legacy JSONL archive-then-import, write-through persistence
  for signals/orders/positions/trades/research/strategy-status, startup
  restores all in-memory ledgers from SQLite.
- Security: dashboard sessions (httpOnly cookie, SHA-256 hash at rest, 12 h
  TTL, revocable), double-submit CSRF, per-IP sliding-window rate limits
  (login 5/min, API 240/min), sanitizer (control chars stripped, 512-char
  caps), security headers on every response.
- Execution: order-state ledger (PENDING/SUBMITTED/FILLED/REJECTED/
  CANCELLED/UNKNOWN), stale-unknown detection, broker reconciliation with
  typed mismatches gating live orders, one-position-one-trade closed-trade
  booking feeding the circuit breaker.
- Risk: research/paper/live modes, explicit ephemeral arming, emergency
  stop, signal TTL, idempotent orders, fixed-fractional sizing, circuit
  breaker on consecutive losses, reconciliation gate.
- Research + backtesting: honest cost-model backtester, chronological
  splits, walk-forward, bootstrap, regime analysis, composite strategy
  quality gate, lifecycle ladder (DRAFT → BACKTESTED → RESEARCHED →
  LIVE_ACTIVE/DEGRADED → DISABLED).
- Observability: structured event log (`data/logs/events.jsonl`), request
  tracing with `X-Request-Id` on every response, uniform error envelope,
  deep health + metrics under `/api/v1`.
- AI: local Qwen analyst via Ollama (offline-safe, whitelisted research
  fields only, graceful failure paths).
- Dashboard redesign (tabs: Signals/Positions/Execution/Research+AI/Risk/
  Strategies/System), live WS signals, session login.
- Single version source (`app/version.py`), structured startup banner,
  OpenAPI tag groups, live-mode tripwire refusing the default token.
- Repo standardization: `.editorconfig`, `.gitattributes`, `.env.example`,
  `pyproject.toml` (ruff + pytest + coverage), pre-commit hooks, ruff lint
  fixes (556 auto + real latent bugs: undefined `Dict`/`Response` imports,
  16× `B904`, `SIM105`/`SIM102`/`SIM117`, `E741`), ruff format pass over all
  files.

[0.3.0]: #0.3.0
[0.2.0]: #0.2.0
