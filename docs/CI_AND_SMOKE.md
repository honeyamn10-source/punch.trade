# CI & Smoke Testing

## GitHub Actions (`ci.yml`)

`.github/workflows/ci.yml` runs on push to `main` and on pull requests
(ubuntu-latest, Python 3.12):

1. Install deps + pytest.
2. **Unit tests**: `python -m pytest tests -q` — the full 181-test suite,
   isolated per test (own SQLite DB + data dir via `conftest.py`).
3. **Boot + API smoke test**: starts `run.py`, waits 8 s, then curls every
   subsystem with header auth (`X-Punch-Token`, never query params):
   health → auth reject/accept → system status → deep health → metrics →
   storage (WAL) → signals → status ladder → honest backtest → research
   dossier → risk sizing → paper order (FILLED) → ledger → reconcile →
   AI status → dashboard. Any failure prints the server log and exits 1.

The CI smoke mirrors the local script below (curl vs Invoke-RestMethod).

## Local smoke (`backend/scripts/smoke.ps1`)

Windows PowerShell end-to-end smoke against a real server process:

```powershell
cd backend
powershell -ExecutionPolicy Bypass -File scripts/smoke.ps1
```

- Boots `run.py` with `PUNCH_TOKEN=smoke-test-token-123` and a throwaway
  SQLite DB in `%TEMP%` (deleted afterwards — never touches real data).
- Runs the 18 checks: health, auth, envelope, status, health, metrics,
  storage, signals, ladder, backtest, research, risk sizing, paper order →
  ledger → reconcile, trades, AI status, dashboard, session login + logout
  (with CSRF cookie).
- Prints `PASS`/`FAIL` per check and exits non-zero on any failure; also
  prints the last server log lines on hard errors.

## When to run

- Before every commit: `python -m pytest tests -q` (backend).
- Before a release: smoke.ps1, then restart the server and re-check
  `/api/v1/system/health`.
- The scripts never arm live mode, never touch broker credentials, and only
  exercise the paper broker.