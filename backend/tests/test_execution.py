"""Execution layer tests: order state machine, ledger, reconciliation
gate, one-position-one-trade booking from paper close events."""

from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import api, config, db, execution, risk
from app.execution import (
    OrderStateError,
    closed_trades,
    mark,
    reconcile,
    record_closed_trade,
    record_order,
    stale_unknown_orders,
    transition,
)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    execution._ledger.clear()
    execution._trades.clear()
    # isolate from the running server's trades.json
    monkeypatch.setattr(execution, "TRADES_LOG", str(tmp_path / "trades.json"))
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)
    yield
    execution._ledger.clear()
    execution._trades.clear()
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)


# ------------------------------------------------------- state machine ----
def test_order_lifecycle():
    assert transition("PENDING", "SUBMITTED") == "SUBMITTED"
    assert transition("PENDING", "FILLED") == "FILLED"
    assert transition("SUBMITTED", "UNKNOWN") == "UNKNOWN"
    with pytest.raises(OrderStateError):
        transition("FILLED", "CANCELLED")
    with pytest.raises(OrderStateError):
        transition("PENDING", "UNKNOWN")


def test_ledger_record_and_mark():
    rec = record_order(
        "o1",
        signal_id="s1",
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=10,
        entry=100.0,
        broker="paper",
    )
    assert rec["status"] == "PENDING"
    assert (
        record_order(
            "o1",
            signal_id="s1",
            strategy_id="t",
            symbol="X",
            side="buy",
            qty=10,
            entry=100.0,
            broker="paper",
        )["id"]
        == "o1"
    )  # idempotent
    assert mark("o1", "FILLED")["status"] == "FILLED"
    assert mark("missing", "FILLED") is None
    with pytest.raises(OrderStateError):
        mark("o1", "CANCELLED")  # FILLED is terminal


def test_stale_submitted_becomes_unknown():
    record_order(
        "o2",
        signal_id=None,
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=1,
        entry=1.0,
        broker="kite",
    )
    mark("o2", "SUBMITTED")
    unknown = stale_unknown_orders(now=time.time() + 1000, timeout=60)
    assert any(r["id"] == "o2" for r in unknown)
    assert execution.get_order("o2")["status"] == "UNKNOWN"


# ----------------------------------------------------- reconciliation ----
def test_reconcile_paper_ok_via_api():
    h = {"X-Punch-Token": "punch-demo-token"}
    with TestClient(api.app) as client:
        api.feed.last_ts["X"] = time.time()  # X has no strategy, so no bars arrive
        r = client.post(
            "/api/orders",
            headers=h,
            json={
                "broker": "paper",
                "symbol": "X",
                "side": "buy",
                "qty": 1,
                "entry": 100.0,
                "targetPrice": 101.0,
                "stopLoss": 99.0,
            },
        )
        assert r.status_code == 200
        order_id = r.json()["result"]["orderId"]
        assert execution.get_order(order_id)["status"] == "FILLED"

        rep = client.post("/api/execution/reconcile", headers=h, json={"broker": "paper"}).json()
        assert rep["ok"] is True
        assert risk.reconciliation_ok() is True


def test_reconcile_flags_unknown_and_closes_gate():
    record_order(
        "o3",
        signal_id=None,
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=1,
        entry=1.0,
        broker="paper",
    )
    mark("o3", "FILLED")
    mark("o3", "SUBMITTED") if False else None
    # force an unknown order (stale submitted)
    record_order(
        "o4",
        signal_id=None,
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=1,
        entry=1.0,
        broker="paper",
    )
    mark("o4", "SUBMITTED")
    stale_unknown_orders(now=time.time() + 1000, timeout=1)
    rep = reconcile("paper", api.brokers.adapters["paper"])
    assert rep["ok"] is False
    assert any(m["type"] == "UNKNOWN_ORDER" for m in rep["mismatches"])
    assert risk.reconciliation_ok() is False
    with pytest.raises(risk.RiskError):
        risk.check_reconciliation()


# -------------------------------------------------- closed-trade booking ----
def test_record_closed_trade_one_position_one_trade():
    record_order(
        "p1",
        signal_id="s1",
        strategy_id="rsi-reversal",
        symbol="X",
        side="buy",
        qty=100,
        entry=100.0,
        broker="paper",
    )
    events = [
        {
            "id": "p1",
            "symbol": "X",
            "side": "buy",
            "qty": 50,
            "qty_total": 100,
            "entry": 100.0,
            "exit_price": 102.0,
            "exit": "TP1",
            "status": "open",
            "ts": 1.0,
        },
        {
            "id": "p1",
            "symbol": "X",
            "side": "buy",
            "qty": 50,
            "qty_total": 100,
            "entry": 100.0,
            "exit_price": 98.0,
            "exit": "SL",
            "status": "closed",
            "ts": 2.0,
        },
    ]
    d = record_closed_trade("p1", events)
    assert d is not None
    assert d["strategyId"] == "rsi-reversal"
    assert d["signalId"] == "s1"
    assert [f["reason"] for f in d["fills"]] == ["ENTRY", "TP1", "STOP"]
    assert d["netPnl"] == 0.0  # 50@+2, 50@-2 -> 0
    assert closed_trades()[-1]["id"] == d["id"]
    # no second trade from the same position
    assert len(closed_trades()) == 1


def test_record_closed_trade_without_final_ignored():
    events = [
        {
            "id": "p2",
            "symbol": "X",
            "side": "buy",
            "qty": 50,
            "qty_total": 100,
            "entry": 100.0,
            "exit_price": 102.0,
            "exit": "TP1",
            "status": "open",
            "ts": 1.0,
        }
    ]
    assert record_closed_trade("p2", events) is None
    assert len(closed_trades()) == 0


def test_closed_trade_feeds_breaker():
    record_order(
        "p3",
        signal_id=None,
        strategy_id="t",
        symbol="X",
        side="buy",
        qty=10,
        entry=100.0,
        broker="paper",
    )
    events = [
        {
            "id": "p3",
            "symbol": "X",
            "side": "buy",
            "qty": 10,
            "qty_total": 10,
            "entry": 100.0,
            "exit_price": 95.0,
            "exit": "SL",
            "status": "closed",
            "ts": 2.0,
        }
    ]
    record_closed_trade("p3", events)
    assert risk.consecutive_losses() == 1


# ------------------------------------------- legacy audit-row restore ----
class _NoPositionsAdapter:
    def get_positions(self):
        return []


def test_restore_and_reconcile_with_legacy_audit_rows(tmp_path, monkeypatch):
    """Legacy JSONL audit rows (no id/status keys) must survive the DB
    round-trip and reconcile without a KeyError."""
    orders_path = os.path.join(config.DATA_DIR, "orders.json")
    with open(orders_path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": 1000.0,
                    "broker": "paper",
                    "strategyId": "legacy",
                    "signalId": "sig-legacy",
                    "clientRequestId": "req-legacy",
                    "symbol": "X",
                    "side": "buy",
                    "qty": 5,
                    "entry": 100.0,
                    "target": 101.0,
                    "stop": 99.0,
                    "mode": "paper",
                    "result": {"orderId": "legacy-order-1", "status": "FILLED"},
                }
            )
            + "\n"
        )
    db.import_legacy_all()
    execution.restore()
    rec = execution.get_order("legacy-order-1")
    assert rec is not None
    assert rec["status"] == "UNKNOWN"  # audit rows have no ledger state
    rep = reconcile("paper", _NoPositionsAdapter())
    assert rep["ok"] is False
    assert any(
        m["type"] == "UNKNOWN_ORDER" and "legacy-order-1" in m["detail"] for m in rep["mismatches"]
    )
