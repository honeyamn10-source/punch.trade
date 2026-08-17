"""SQLite persistence layer — the durable store.

Design:
- One database at config.DB_PATH (default data/punch.db), WAL mode,
  synchronous=NORMAL, busy timeout — safe for the mixed thread/async
  backend without sacrificing durability.
- Schema migrations are explicit (SCHEMA_VERSION + ordered migration
  list); init_db() applies them transactionally.
- The JSONL ledgers in data/ remain the append-only audit trail; on
  startup their contents are imported into SQLite once and the files
  are archived into data/archive/ (legacy archive-then-import with a
  per-file report). SQLite is the source of truth for restores.
- The database file itself is never committed to git (data/ is
  gitignored); tests isolate via conftest patching config.DB_PATH.

Row model: every table stores the full canonical record as JSON
(payload) next to a few indexed columns (id, status, ts) so future
phases can query without schema churn.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import suppress

from . import config

SCHEMA_VERSION = 2

# ordered migrations: (version, sql) — applied in a transaction, in order
MIGRATIONS: list[tuple] = [
    (
        1,
        """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_status (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);
""",
    ),
    (
        2,
        """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
""",
    ),
]

# JSONL audit files (relative to DATA_DIR) and the table they import into
LEGACY_FILES = (
    ("signals.json", "signals"),
    ("orders.json", "orders"),
    ("positions.json", "positions"),
    ("trades.json", "trades"),
)

_local = threading.local()
_initialized = False
_conns: set = set()
_write_lock = threading.Lock()


# ------------------------------------------------------------- conns ----
def _db_path() -> str:
    return config.DB_PATH


def get_conn() -> sqlite3.Connection:
    """Thread-local connection; lazily initializes the schema."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        path = _db_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
        with _write_lock:
            _conns.add(conn)
    if not _initialized and not _initializing:
        init_db()
    return conn


_initializing = False


class transaction:
    """Context manager: commit on success, rollback on error."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = get_conn()
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            pass


def init_db() -> None:
    """Apply all migrations transactionally (idempotent)."""
    global _initialized, _initializing
    with _write_lock:
        if _initialized:
            return
        _initializing = True
        try:
            conn = getattr(_local, "conn", None)
            if conn is None:
                # create directly (get_conn would deadlock on _write_lock)
                path = _db_path()
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                conn = sqlite3.connect(path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                _local.conn = conn
                _conns.add(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = int(row["value"]) if row else 0
            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                with transaction():
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                        (str(version),),
                    )
            _initialized = True
        finally:
            _initializing = False


def close_all() -> None:
    """Close every open connection (tests / shutdown)."""
    global _initialized
    with _write_lock:
        for conn in list(_conns):
            with suppress(Exception):
                conn.close()
        _conns.clear()
        _local.conn = None
    _initialized = False


def reset() -> None:
    """Full reset of cached state; next get_conn() re-inits (tests)."""
    close_all()


def row_count(table: str) -> int:
    conn = get_conn()
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


_TABLES = (
    "signals",
    "orders",
    "positions",
    "trades",
    "research_runs",
    "strategy_status",
    "sessions",
)


def backup(dest: str) -> dict:
    """Consistent online backup via the SQLite backup API.

    Safe to call while the server is running (WAL). The destination file
    must not already be open elsewhere. Returns a report so callers/tests
    can verify: schema version, integrity check and per-table counts.
    """
    import sqlite3 as _sqlite3

    conn = get_conn()  # outside the lock: get_conn may need _write_lock itself
    with _write_lock:
        dest_conn = _sqlite3.connect(dest)
        try:
            conn.backup(dest_conn)
            integrity = dest_conn.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {
                t: int(dest_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for t in _TABLES
            }
        finally:
            dest_conn.close()
    return {
        "path": dest,
        "schemaVersion": SCHEMA_VERSION,
        "integrity": integrity,
        "counts": counts,
    }


# ------------------------------------------------------------- writes ----
def write_signal(sig: dict) -> None:
    sid = sig.get("id") or sig.get("signalId")
    if not sid:
        return
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO signals (id, payload, ts) VALUES (?,?,?)",
            (str(sid), json.dumps(sig), sig.get("ts") or time.time()),
        )


def write_order(rec: dict) -> None:
    oid = rec.get("id") or rec.get("orderId")
    if not oid:
        return
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO orders (id, status, payload, ts, updated_ts) "
            "VALUES (?,?,?,?,?)",
            (
                str(oid),
                rec.get("status", "PENDING"),
                json.dumps(rec),
                rec.get("ts") or time.time(),
                rec.get("updatedTs") or time.time(),
            ),
        )


def mark_order(order_id: str, new_status: str) -> None:
    with transaction() as c:
        c.execute(
            "UPDATE orders SET status=?, payload=json_set(payload,'$.status',?), "
            "updated_ts=? WHERE id=?",
            (new_status, new_status, time.time(), str(order_id)),
        )


def write_position(pos: dict) -> None:
    pid = pos.get("id") or pos.get("positionId")
    if not pid:
        return
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO positions (id, status, payload, ts) VALUES (?,?,?,?)",
            (str(pid), pos.get("status", "open"), json.dumps(pos), pos.get("ts") or time.time()),
        )


def write_trade(t: dict) -> None:
    tid = t.get("id")
    if not tid:
        return
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO trades (id, payload, ts) VALUES (?,?,?)",
            (str(tid), json.dumps(t), t.get("exitTs") or t.get("entryTs") or time.time()),
        )


def write_research_run(strategy_id: str, payload: dict) -> None:
    with transaction() as c:
        c.execute(
            "INSERT INTO research_runs (strategy_id, payload, ts) VALUES (?,?,?)",
            (str(strategy_id), json.dumps(payload), time.time()),
        )


def write_strategy_status(sid: str, payload: dict) -> None:
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategy_status (id, payload, ts) VALUES (?,?,?)",
            (str(sid), json.dumps(payload), time.time()),
        )


# -------------------------------------------------------------- reads ----
def read_signals(limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT payload FROM signals ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["payload"]) for r in reversed(rows)]


def read_orders(limit: int = 500) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT payload FROM orders ORDER BY ts LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def read_trades(limit: int = 500) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT payload FROM trades ORDER BY ts LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def read_positions(limit: int = 500) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT payload FROM positions ORDER BY ts LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


# ------------------------------------------- legacy archive + import ----
def _legacy_path(name: str) -> str:
    return os.path.join(config.DATA_DIR, name)


def _archive_dir() -> str:
    d = os.path.join(config.DATA_DIR, "archive")
    os.makedirs(d, exist_ok=True)
    return d


def _row_id(table: str, row: dict) -> str | None:
    if table == "signals":
        return row.get("id") or row.get("signalId")
    if table == "orders":
        # audit rows carry the broker orderId inside result; fall back to
        # the request idempotency keys so nothing is dropped at import
        result = row.get("result") or {}
        return (
            row.get("id")
            or result.get("orderId")
            or row.get("signalId")
            or row.get("clientRequestId")
        )
    if table == "positions":
        return row.get("id") or row.get("positionId") or row.get("orderId")
    if table == "trades":
        return row.get("id")
    return None


def import_legacy_file(name: str, table: str) -> dict:
    """Import one JSONL file into its table. Report: rows, skipped, errors."""
    path = _legacy_path(name)
    report = {"file": name, "rows": 0, "skipped": 0, "errors": []}
    if not os.path.exists(path):
        return report
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    for i, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
            rid = _row_id(table, row)
            if rid is None:
                report["skipped"] += 1
                continue
            payload = json.dumps(row)
            ts = row.get("ts") or row.get("entryTs") or time.time()
            conn = get_conn()
            if table == "signals":
                conn.execute(
                    "INSERT OR REPLACE INTO signals (id, payload, ts) VALUES (?,?,?)",
                    (str(rid), payload, ts),
                )
            elif table == "orders":
                conn.execute(
                    "INSERT OR REPLACE INTO orders (id, status, payload, ts, updated_ts) "
                    "VALUES (?,?,?,?,?)",
                    (
                        str(rid),
                        row.get("status", "UNKNOWN"),
                        payload,
                        row.get("ts") or time.time(),
                        row.get("updatedTs") or time.time(),
                    ),
                )
            elif table == "positions":
                conn.execute(
                    "INSERT OR REPLACE INTO positions (id, status, payload, ts) VALUES (?,?,?,?)",
                    (str(rid), row.get("status", "closed"), payload, ts),
                )
            elif table == "trades":
                conn.execute(
                    "INSERT OR REPLACE INTO trades (id, payload, ts) VALUES (?,?,?)",
                    (str(rid), payload, ts),
                )
            report["rows"] += 1
        except Exception as e:  # noqa: BLE001 — one bad line must not kill the import
            report["errors"].append(f"line {i}: {e}")
            if len(report["errors"]) > 20:
                report["errors"].append("...truncated")
                break
    if report["rows"]:
        get_conn().commit()
    return report


def archive_legacy(name: str) -> str | None:
    """Move a JSONL audit file to data/archive/ (keeps the audit trail)."""
    path = _legacy_path(name)
    if not os.path.exists(path):
        return None
    dest = os.path.join(_archive_dir(), f"{name}.{int(time.time())}")
    os.replace(path, dest)
    return dest


def import_legacy_all() -> dict[str, dict]:
    """Import every legacy JSONL audit file, then archive them.

    Returns {file: report}. Called once at startup; afterwards SQLite is
    the restore source and the JSONL trail starts fresh.
    """
    report: dict[str, dict] = {}
    for name, table in LEGACY_FILES:
        r = import_legacy_file(name, table)
        if r["rows"]:
            dest = archive_legacy(name)
            r["archivedTo"] = dest
        report[name] = r
    return report


def storage_status() -> dict:
    """Machine-readable storage info for /api/system/storage."""
    conn = get_conn()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return {
        "engine": "sqlite",
        "path": _db_path(),
        "journalMode": mode,
        "schemaVersion": int(row["value"]) if row else 0,
        "counts": {
            t: row_count(t)
            for t in (
                "signals",
                "orders",
                "positions",
                "trades",
                "research_runs",
                "strategy_status",
                "sessions",
            )
        },
    }
