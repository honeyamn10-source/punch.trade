# Storage

## SQLite durable store (`app/db.py`)

The single source of truth for restores. Default `data/punch.db` (override with
`PUNCH_DB_PATH`; the data/ directory — and therefore the DB — is gitignored).

- **WAL mode** + `synchronous=NORMAL` + 5 s busy timeout: safe for the
  mixed thread/async backend without sacrificing durability.
- **Versioned migrations**: `SCHEMA_VERSION` + an ordered `MIGRATIONS` list;
  `init_db()` applies missing migrations transactionally.
- **Thread-local connections**, `transaction()` context manager (commit /
  rollback).

### Tables

| Table | Contents |
|---|---|
| `signals` | every signal (id, JSON payload, ts) |
| `orders` | execution ledger rows (id, status, payload, ts, updated_ts) |
| `positions` | closed position events |
| `trades` | closed trades — one row per CompletedTrade |
| `research_runs` | every research dossier (strategy_id, payload, ts) |
| `strategy_status` | lifecycle snapshots (id, payload, ts) |
| `sessions` | dashboard session token **hashes** (never raw tokens), expiry, ip, user agent |
| `meta` | schema version |

Rows store the canonical record as JSON next to indexed columns — future
phases can query without schema churn.

## Legacy JSONL archive-then-import

The JSONL audit files (`data/signals.json`, `orders.json`, `positions.json`,
`trades.json`) remain the append-only audit trail. On startup they are
**imported once** into SQLite and **archived** into `data/archive/` with a
per-file report (rows imported, skipped, errors). Id fallbacks exist for
legacy shapes (orders via `result.orderId` / `signalId` / `clientRequestId`).
A bad line never kills the import.

`GET /api/system/storage` (and `/api/v1/system/storage`) reports engine, WAL
mode, schema version and per-table counts. Tests isolate the DB via
`tests/conftest.py` (each test gets its own DB + data dir).