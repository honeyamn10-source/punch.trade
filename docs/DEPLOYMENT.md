# Deployment

punch.trade is a self-hosted Windows/Linux single-node app: FastAPI backend,
SQLite store, optional local Ollama for the AI analyst.

## Requirements

- Python 3.12 (Windows PowerShell or bash)
- Ollama (optional) with a `qwen2.5*` model pre-installed — the app never downloads models
- A browser for the dashboard; the Chrome MV3 extension is optional

## Install & run

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on Linux)
pip install -r requirements.txt
python run.py                   # http://127.0.0.1:8000
```

The first boot migrates any legacy JSONL ledgers into SQLite and archives the
files (see STORAGE.md) — the startup log prints the import report.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `PUNCH_TOKEN` | `punch-demo-token` | API token (≥ 8 chars; LIVE mode refuses the default) |
| `PUNCH_MODE` | `paper` | `research` / `paper` / `live` |
| `PUNCH_DB_PATH` | `data/punch.db` | SQLite location |
| `PUNCH_BAR_SECONDS` | 4.0 | simulated candle cadence |
| `PUNCH_SIGNAL_TTL` | 300 | signal expiry (s) |
| `PUNCH_MAX_POSITIONS` | 5 | open-position limit |
| `PUNCH_MAX_QTY` | 10000 | per-order size cap |
| `PUNCH_DAILY_LOSS_PCT` | 5.0 | realized daily loss stop |
| `PUNCH_CIRCUIT_BREAKER_LOSSES` | 3 | consecutive-loss breaker |
| `PUNCH_RISK_PER_TRADE_PCT` | 0.01 | sizing default |
| `PUNCH_EXIT_TIMEOUT_BARS` | 120 | anti-wedge state reset |
| `PUNCH_TELEGRAM_BOT_TOKEN` / `PUNCH_TELEGRAM_CHAT_ID` | "" | optional alerts |
| `PUNCH_OLLAMA_HOST` / `PUNCH_OLLAMA_MODEL` | `http://127.0.0.1:11434` / auto | AI analyst |

`config.validate_config()` runs at startup and refuses nonsense (bad mode,
short token, LIVE with the default token, non-positive limits).

## Going live

1. `PUNCH_MODE=live` + a strong `PUNCH_TOKEN`.
2. Store broker credentials via the vault endpoints (Fernet-encrypted,
   `data/.secret` key file).
3. `POST /api/risk/arm {broker}` after every start — arming is never persisted.
4. Reconcile before trusting the ledger (`/api/execution/reconcile`).
5. Watch `/api/v1/system/health` + `/api/v1/system/metrics`.

The server binds `127.0.0.1` only; for remote access put a reverse proxy
(nginx/caddy) in front and enforce HTTPS. Never expose it raw on the internet.

## Backups

Stop the server, copy `data/punch.db` (+ `-wal`/`-shm`), `data/.secret` and
`data/archive/`. The JSONL audit trail (data/*.json) is not needed for restores —
SQLite is authoritative — but keep it for the audit trail.