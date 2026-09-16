"""Trial Ledger — immutable append-only record of every research trial.

Each trial entry captures full provenance: strategy identity, parameter snapshot,
data fingerprint, split hashes, all research metrics, and quality gate result.
Ledger entries are NEVER mutated — they form the audit trail for DSR/PBO.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from .db import get_conn, transaction


@dataclass
class TrialRecord:
    """Single immutable trial entry."""

    trial_id: str
    strategy_id: str
    strategy_version: str
    parameter_snapshot: dict
    data_fingerprint: str  # SHA256 of bar series (ts + close)
    split_hashes: dict  # train/val/test SHA256
    research_metrics: dict  # full research_report output
    quality_gate: dict
    timestamp: float
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "parameter_snapshot": self.parameter_snapshot,
            "data_fingerprint": self.data_fingerprint,
            "split_hashes": self.split_hashes,
            "research_metrics": self.research_metrics,
            "quality_gate": self.quality_gate,
            "timestamp": self.timestamp,
            "notes": self.notes,
        }

    @staticmethod
    def from_row(row: sqlite3.Row) -> TrialRecord:
        return TrialRecord(
            trial_id=row["trial_id"],
            strategy_id=row["strategy_id"],
            strategy_version=row["strategy_version"],
            parameter_snapshot=json.loads(row["parameter_snapshot"]),
            data_fingerprint=row["data_fingerprint"],
            split_hashes=json.loads(row["split_hashes"]),
            research_metrics=json.loads(row["research_metrics"]),
            quality_gate=json.loads(row["quality_gate"]),
            timestamp=row["timestamp"],
            notes=row["notes"] if "notes" in row else "",  # noqa: SIM401 (sqlite3.Row has no .get)
        )


# ------------------------------------------------------------- schema ----
SCHEMA_VERSION = 1

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS trial_ledger (
        trial_id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        parameter_snapshot TEXT NOT NULL,
        data_fingerprint TEXT NOT NULL,
        split_hashes TEXT NOT NULL,
        research_metrics TEXT NOT NULL,
        quality_gate TEXT NOT NULL,
        timestamp REAL NOT NULL,
        notes TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_trial_strategy ON trial_ledger(strategy_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_trial_fingerprint ON trial_ledger(data_fingerprint);
    """,
]


def init_trial_ledger() -> None:
    """Create trial_ledger table if not exists."""
    conn = get_conn()
    for sql in MIGRATIONS:
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _fingerprint_bars(bars: list[dict]) -> str:
    """SHA256 of chronological bar series (timestamp + close)."""
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b['ts']}:{b['close']}".encode())
    return h.hexdigest()


def _split_hash(bars: list[dict]) -> str:
    """SHA256 of a single split."""
    return _fingerprint_bars(bars)


def append_trial(
    strategy_id: str,
    strategy_version: str,
    parameter_snapshot: dict,
    bars: list[dict],
    splits: tuple[list[dict], list[dict], list[dict]],
    research_metrics: dict,
    quality_gate: dict,
    notes: str = "",
) -> TrialRecord:
    """Append an immutable trial record. Returns the created record."""
    init_trial_ledger()
    train, val, test = splits
    record = TrialRecord(
        trial_id=uuid.uuid4().hex[:16],
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        parameter_snapshot=parameter_snapshot,
        data_fingerprint=_fingerprint_bars(bars),
        split_hashes={
            "train": _split_hash(train),
            "val": _split_hash(val),
            "test": _split_hash(test),
        },
        research_metrics=research_metrics,
        quality_gate=quality_gate,
        timestamp=time.time(),
        notes=notes,
    )
    with transaction() as c:
        c.execute(
            """
            INSERT INTO trial_ledger
            (trial_id, strategy_id, strategy_version, parameter_snapshot,
             data_fingerprint, split_hashes, research_metrics, quality_gate,
             timestamp, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.trial_id,
                record.strategy_id,
                record.strategy_version,
                json.dumps(record.parameter_snapshot, sort_keys=True),
                record.data_fingerprint,
                json.dumps(record.split_hashes, sort_keys=True),
                json.dumps(record.research_metrics, sort_keys=True),
                json.dumps(record.quality_gate, sort_keys=True),
                record.timestamp,
                record.notes,
            ),
        )
    return record


def get_trial(trial_id: str) -> TrialRecord | None:
    """Retrieve a single trial by ID."""
    init_trial_ledger()
    conn = get_conn()
    row = conn.execute("SELECT * FROM trial_ledger WHERE trial_id=?", (trial_id,)).fetchone()
    return TrialRecord.from_row(row) if row else None


def list_trials(
    strategy_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TrialRecord]:
    """List trials, newest first."""
    init_trial_ledger()
    conn = get_conn()
    if strategy_id:
        rows = conn.execute(
            "SELECT * FROM trial_ledger WHERE strategy_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (strategy_id, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trial_ledger ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [TrialRecord.from_row(r) for r in rows]


def trials_for_fingerprint(data_fingerprint: str) -> list[TrialRecord]:
    """Find all trials run on the exact same data (detects re-runs)."""
    init_trial_ledger()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trial_ledger WHERE data_fingerprint=? ORDER BY timestamp DESC",
        (data_fingerprint,),
    ).fetchall()
    return [TrialRecord.from_row(r) for r in rows]
