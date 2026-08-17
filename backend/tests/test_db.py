"""SQLite persistence layer tests (DB isolated per-test via conftest)."""

import json
import os

import pytest

from app import config, db, execution


# ----------------------------------------------------------- schema -----
def test_init_idempotent_and_wal():
    db.init_db()
    db.init_db()
    assert db.storage_status()["engine"] == "sqlite"
    assert db.storage_status()["journalMode"] == "wal"
    assert db.storage_status()["schemaVersion"] == db.SCHEMA_VERSION


def test_writes_and_reads_roundtrip():
    db.write_signal({"id": "s1", "symbol": "X", "ts": 1.0})
    db.write_signal({"id": "s2", "symbol": "Y", "ts": 2.0})
    sigs = db.read_signals()
    assert [s["id"] for s in sigs] == ["s1", "s2"]

    rec = {"id": "o1", "status": "PENDING", "symbol": "X", "ts": 3.0, "updatedTs": 3.0}
    db.write_order(rec)
    db.mark_order("o1", "FILLED")
    orders = db.read_orders()
    assert orders[0]["status"] == "FILLED"

    db.write_trade({"id": "t1", "netPnl": 12.5, "exitTs": 4.0})
    db.write_position({"id": "p1", "status": "closed", "ts": 5.0})
    db.write_research_run("rsi-reversal", {"score": 10})
    db.write_strategy_status("s", {"status": "DRAFT"})

    assert db.row_count("trades") == 1
    assert db.row_count("positions") == 1
    assert db.row_count("research_runs") == 1
    assert db.row_count("strategy_status") == 1
    assert len(db.read_trades()) == 1
    assert len(db.read_positions()) == 1


def test_transaction_rollback():
    with pytest.raises(RuntimeError), db.transaction() as c:
        c.execute("INSERT INTO signals (id, payload, ts) VALUES ('x', '{}', 1)")
        raise RuntimeError("boom")
    assert db.row_count("signals") == 0


def test_execution_write_through_and_restore():
    execution.record_order(
        "o1",
        signal_id="s1",
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=1,
        entry=10,
        broker="paper",
    )
    execution.mark("o1", "FILLED")
    assert db.row_count("orders") == 1

    execution._ledger.clear()
    execution._trades.clear()
    execution.restore()
    assert execution.get_order("o1")["status"] == "FILLED"


# ------------------------------------------------- legacy archive+import --
def _write_legacy(name, lines):
    path = os.path.join(config.DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    return path


def test_import_legacy_files_with_report_and_archive():
    _write_legacy("signals.json", [{"id": "s1", "ts": 1}, {"id": "s2", "ts": 2}])
    _write_legacy(
        "orders.json",
        [{"id": "o1", "status": "FILLED", "ts": 1}, {"result": {"orderId": "o2"}, "ts": 2}],
    )
    _write_legacy("trades.json", [{"id": "t1", "netPnl": 1}, {"bad": "no id"}])
    _write_legacy("positions.json", [])

    report = db.import_legacy_all()

    assert report["signals.json"]["rows"] == 2
    assert report["orders.json"]["rows"] == 2  # o1 by id, o2 by result.orderId
    assert report["trades.json"]["rows"] == 1
    assert report["trades.json"]["skipped"] == 1  # row without id
    assert report["positions.json"]["rows"] == 0  # empty file, not archived

    assert not os.path.exists(_legacy("signals.json"))
    assert not os.path.exists(_legacy("orders.json"))
    assert not os.path.exists(_legacy("trades.json"))
    assert os.path.exists(_legacy("positions.json"))

    assert db.row_count("signals") == 2
    assert db.row_count("orders") == 2
    assert db.row_count("trades") == 1


def _legacy(name):
    return os.path.join(config.DATA_DIR, name)


def test_import_is_idempotent_across_restarts():
    _write_legacy("signals.json", [{"id": "s1", "ts": 1}])
    db.import_legacy_all()
    # second boot: file is gone, nothing to import, DB keeps its rows
    report = db.import_legacy_all()
    assert report["signals.json"]["rows"] == 0
    assert db.row_count("signals") == 1


def test_bad_line_does_not_kill_import():
    _write_legacy("signals.json", ["not json", {"id": "s1", "ts": 1}])
    report = db.import_legacy_all()
    assert report["signals.json"]["rows"] == 1
    assert report["signals.json"]["errors"]


def test_storage_status_counts():
    db.write_signal({"id": "a", "ts": 1})
    db.write_order({"id": "b", "status": "PENDING", "ts": 1, "updatedTs": 1})
    st = db.storage_status()
    assert st["counts"]["signals"] == 1
    assert st["counts"]["orders"] == 1
    assert st["counts"]["trades"] == 0
