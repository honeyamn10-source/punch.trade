# AGENTS.md

Self-hosted trading workstation: FastAPI backend + Chrome MV3 extension.
Docs in `docs/` (ARCHITECTURE, API, RISK, SECURITY, TESTING, OPERATIONS — read
the relevant one before touching a subsystem).

## Layout

- `backend/app/` — FastAPI app (`api.py` is the router/entrypoint, `run.py` boots it)
- `backend/tests/` — pytest suite; `tests/conftest.py` isolates SQLite + data dir per test (autouse)
- `extension/` — Chrome MV3 extension (popup + chart overlay)
- `docs/` — 19 guides; `CHANGELOG.md`, `README.md` at root
- `pyproject.toml` — at repo **root** (tooling config); runtime deps in `backend/requirements.txt` (canonical; pyproject `dependencies` mirrors it — keep in sync)

## Commands

- Tests: `cd backend; python -m pytest tests -q` — **must run from `backend/`** (`app` is not importable from root; CI works around it with `PYTHONPATH=backend`)
- Single test: `cd backend; python -m pytest tests/test_x.py::test_y`
- Lint: `ruff check backend` from root (or `ruff check .` from `backend/`)
- Format: `ruff format backend`; format check: `ruff format --check backend`
- Boot: `cd backend; python run.py` (binds `127.0.0.1:8000`)
- Smoke: `backend/scripts/smoke.ps1`
- CI: `.github/workflows/ci.yml` (ruff + pytest with coverage)

## Versioning

`backend/app/version.py` is the single version source (currently 0.3.0).
On a release bump it too: `pyproject.toml [project] version` and
`extension/manifest.json` version.

## Gotchas (verified, easy to miss)

- **Never commit `data/`** — holds `punch.db` (WAL), `vault.json` + `.secret`
  (Fernet key), `data/logs/`. Fully gitignored; `.secret` loss = vault unrecoverable.
- **`with TestClient(api.app) as client:` is required** — startup (lifespan)
  only runs in the context. A bare `TestClient(...)` silently skips startup.
- **Feed staleness**: symbols without a strategy never get bars. Tests placing
  orders for bare symbols (e.g. `X`) must seed `api.feed.last_ts[symbol]`
  after entering the context, or the order returns 409 `FEED_STALE`.
- **Startup imports + archives legacy JSONL** from the data dir into SQLite
  (`db.import_legacy_all`) — it's a one-time migration; a server run touches
  the real `data/` dir.
- Only one backend instance may run (SQLite lock).
- `PUNCH_MODE=live` refuses the default demo token; `PUNCH_ALLOW_LIVE_TESTS`
  is a tripwire for real-broker integration tests and must stay off in CI.
- All errors use `{error:{code,message,requestId}}`; responses carry
  `X-Request-Id`. New endpoints should keep the envelope + header behavior.
- List endpoints paginate with `limit`/`offset` (cap 500) + `total` meta —
  keep that shape for new list endpoints.
- The API compat suite (`tests/test_api_compat.py`) schema-locks wire
  formats; breaking a shape there is a deliberate change requiring the test
  to be updated.

## Security posture

- API auth = `X-Punch-Token` header only (never cookies/URLs). Dashboard
  sessions use httpOnly cookie + double-submit CSRF. Rate limits per IP.
- Broker creds live only in the Fernet vault; endpoints must never return
  stored secrets (status endpoints return connection state only).
- The git user is `punch.trade bot <bot@punch.trade>` — commits land under
  that identity; no GitHub username to reference in CODEOWNERS.
