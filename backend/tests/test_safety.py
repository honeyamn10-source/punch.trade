"""Safety tests: risk engine, execution modes, order hardening, auth.

Covers AUD-001/002/003/004/005/007/008 from docs/AUDIT.md.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import api, config, risk


# ------------------------------------------------------------- risk ----
class FakeFeed:
    def __init__(self, last_ts):
        self.last_ts = last_ts


@pytest.fixture(autouse=True)
def _reset_risk_state():
    """Circuit-breaker/reconciliation state is module-global — isolate it."""
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)
    yield
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)


def test_mode_blocks_all_orders():
    risk.set_mode("research")
    with pytest.raises(risk.RiskError) as ei:
        risk.check(broker="paper", signal=None, signal_ts=None)
    assert ei.value.code == "MODE_BLOCKED"


def test_paper_allowed_in_paper_mode():
    risk.set_mode("paper")
    risk.check(broker="paper", signal=None, signal_ts=None)  # must not raise


def test_real_broker_rejected_in_paper_mode():
    risk.set_mode("paper")
    with pytest.raises(risk.RiskError) as ei:
        risk.check(broker="binance", signal=None, signal_ts=None)
    assert ei.value.code == "BROKER_NOT_ALLOWED"


def test_live_requires_arm(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "super-secret-test-token")
    risk.set_mode("live")
    assert risk.armed() == []
    with pytest.raises(risk.RiskError) as ei:
        risk.check(broker="binance", signal=None, signal_ts=None)
    assert ei.value.code == "NOT_ARMED"


def test_arm_rules(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "super-secret-test-token")
    risk.set_mode("live")
    with pytest.raises(risk.RiskError) as ei:
        risk.arm("paper", connected=True)
    assert ei.value.code == "PAPER_NEVER_ARMS"
    with pytest.raises(risk.RiskError) as ei:
        risk.arm("binance", connected=False)
    assert ei.value.code == "BROKER_NOT_CONNECTED"
    risk.arm("binance", connected=True)
    assert risk.armed() == ["binance"]
    risk.check(broker="binance", signal=None, signal_ts=None)  # passes now


def test_live_mode_refused_with_demo_token(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", config.DEFAULT_TOKEN)
    with pytest.raises(risk.RiskError) as ei:
        risk.set_mode("live")
    assert ei.value.code == "DEMO_TOKEN_BLOCKS_LIVE"


def test_emergency_stop_disarms_everything(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "super-secret-test-token")
    risk.set_mode("live")
    risk.arm("binance", connected=True)
    st = risk.stop()
    assert st["mode"] == "research"
    assert st["armed"] == []
    with pytest.raises(risk.RiskError):
        risk.check(broker="binance", signal=None, signal_ts=None)


def test_feed_stale_rejects():
    risk.set_mode("paper")
    now = time.time()
    with pytest.raises(risk.RiskError) as ei:
        risk.check(
            broker="paper", signal=None, signal_ts=None, feed=FakeFeed({}), symbol="RELIANCE"
        )
    assert ei.value.code == "FEED_STALE"
    with pytest.raises(risk.RiskError) as ei:
        risk.check(
            broker="paper",
            signal=None,
            signal_ts=None,
            feed=FakeFeed({"RELIANCE": now - 3600}),
            symbol="RELIANCE",
        )
    assert ei.value.code == "FEED_STALE"
    risk.check(
        broker="paper",
        signal=None,
        signal_ts=None,
        feed=FakeFeed({"RELIANCE": now}),
        symbol="RELIANCE",
    )


def test_signal_expiry():
    risk.set_mode("paper")
    with pytest.raises(risk.RiskError) as ei:
        risk.check(broker="paper", signal={}, signal_ts=time.time() - 1e6)
    assert ei.value.code == "SIGNAL_EXPIRED"
    risk.check(broker="paper", signal={}, signal_ts=time.time())


def test_limits():
    with pytest.raises(risk.RiskError) as ei:
        risk.enforce_limits(
            qty=0, open_positions=0, daily_loss_pct=0.0, entry=100, target=105, stop=99
        )
    assert ei.value.code == "INVALID_QTY"
    with pytest.raises(risk.RiskError) as ei:
        risk.enforce_limits(
            qty=config.MAX_QTY + 1,
            open_positions=0,
            daily_loss_pct=0.0,
            entry=100,
            target=105,
            stop=99,
        )
    assert ei.value.code == "MAX_QTY"
    with pytest.raises(risk.RiskError) as ei:
        risk.enforce_limits(
            qty=1,
            open_positions=config.MAX_OPEN_POSITIONS,
            daily_loss_pct=0.0,
            entry=100,
            target=105,
            stop=99,
        )
    assert ei.value.code == "MAX_POSITIONS"
    with pytest.raises(risk.RiskError) as ei:
        risk.enforce_limits(
            qty=1,
            open_positions=0,
            daily_loss_pct=-config.MAX_DAILY_LOSS_PCT,
            entry=100,
            target=105,
            stop=99,
        )
    assert ei.value.code == "DAILY_LOSS_LIMIT"
    with pytest.raises(risk.RiskError) as ei:
        risk.enforce_limits(
            qty=1, open_positions=0, daily_loss_pct=0.0, entry=-5, target=105, stop=99
        )
    assert ei.value.code == "INVALID_PRICE"


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        c.headers["X-Punch-Token"] = config.API_TOKEN
        yield c


def _place(client, **kw):
    body = {
        "broker": "paper",
        "symbol": "RELIANCE",
        "side": "buy",
        "qty": 1,
        "entry": 100.0,
        "targetPrice": 105.0,
        "stopLoss": 99.0,
    }
    body.update(kw)
    api.feed.last_ts["RELIANCE"] = time.time()
    return client.post("/api/orders", json=body)


def test_http_requires_token(client):
    r = client.get("/api/analytics", headers={"X-Punch-Token": ""})
    assert r.status_code == 401
    r = client.post("/api/orders", json={"broker": "paper"}, headers={"X-Punch-Token": ""})
    assert r.status_code == 401


def test_order_idempotency_by_client_request_id(client):
    cid = "req-" + uuid.uuid4().hex[:12]
    r1 = _place(client, clientRequestId=cid)
    assert r1.status_code == 200, r1.text
    r2 = _place(client, clientRequestId=cid)
    assert r2.status_code == 200
    body = r2.json()
    assert body["duplicate"] is True
    assert body["result"]["orderId"] == r1.json()["result"]["orderId"]


def test_order_idempotency_by_signal_id(client):
    now = time.time()
    sig = {
        "id": "sig-" + uuid.uuid4().hex[:8],
        "symbol": "RELIANCE",
        "ts": now,
        "entry": 100.0,
        "targetPrice": 105.0,
        "stopLoss": 99.0,
    }
    api.hub.signals.append(sig)
    r1 = _place(client, signalId=sig["id"])
    assert r1.status_code == 200, r1.text
    r2 = _place(client, signalId=sig["id"])
    assert r2.json()["duplicate"] is True


def test_unknown_signal_rejected(client):
    r = _place(client, signalId="does-not-exist")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SIGNAL_NOT_FOUND"


def test_expired_signal_rejected(client):
    sig = {
        "id": "sig-old-" + uuid.uuid4().hex[:6],
        "symbol": "RELIANCE",
        "ts": time.time() - 1e6,
        "entry": 100.0,
        "targetPrice": 105.0,
        "stopLoss": 99.0,
    }
    api.hub.signals.append(sig)
    r = _place(client, signalId=sig["id"])
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SIGNAL_EXPIRED"


def test_invalid_qty_rejected(client):
    r = _place(client, qty=0)
    assert r.status_code == 422


def test_research_mode_blocks_orders(client):
    client.post("/api/system/mode", json={"mode": "research"})
    r = _place(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "MODE_BLOCKED"
    client.post("/api/system/mode", json={"mode": "paper"})


def test_emergency_stop_endpoint(client):
    client.post("/api/system/stop")
    r = _place(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "MODE_BLOCKED"
    client.post("/api/system/mode", json={"mode": "paper"})


def test_analytics_weights_partial_fills(client):
    saved = api.closed_positions
    now = time.time()
    api.closed_positions = [
        {"qty": 1, "qty_total": 3, "pnl_pct": 1.0, "opened_at": now},
        {"qty": 1, "qty_total": 3, "pnl_pct": 2.0, "opened_at": now + 1},
        {"qty": 1, "qty_total": 3, "pnl_pct": 3.0, "opened_at": now + 2},
    ]
    try:
        a = client.get("/api/analytics").json()
        assert a["netPnlPct"] == 2.0  # (1+2+3)/3, not 6.0
        assert a["equityCurve"][-1]["equity"] == 2.0
        assert a["closed"] == 3
    finally:
        api.closed_positions = saved


def test_ws_requires_auth_message(client):
    with pytest.raises(WebSocketDisconnect) as ei, client.websocket_connect("/ws/signals") as ws:
        ws.receive_json()
    assert ei.value.code == 4401


def test_ws_auth_ok_and_snapshot(client):
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "auth", "token": config.API_TOKEN})
        assert ws.receive_json()["type"] == "auth_ok"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert "signals" in snap["data"]


def test_ws_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect) as ei, client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "auth", "token": "wrong"})
        ws.receive_json()
    assert ei.value.code == 4401


def test_system_status_shape(client):
    st = client.get("/api/system/status").json()
    assert st["mode"] in ("research", "paper", "live")
    assert "feeds" in st and "armed" in st
    assert "version" in st
    assert st["version"].count(".") == 2  # semantic MAJOR.MINOR.PATCH
    assert "gitCommit" in st


def test_live_test_tripwire_defaults_off():
    """CI/test mode can never accidentally reach a live broker: the
    PUNCH_ALLOW_LIVE_TESTS tripwire must be false in the test environment,
    and no CI workflow may ever set it (see docs/TESTING.md)."""
    assert config.ALLOW_LIVE_TESTS is False


def test_health_exposes_version_and_commit(client):
    h = client.get("/api/v1/system/health").json()
    assert h["version"].count(".") == 2
    assert "gitCommit" in h


def test_feed_health_reports_age(client):
    api.feed.last_ts["RELIANCE"] = time.time()
    feeds = client.get("/api/system/status").json()["feeds"]
    row = next((f for f in feeds if f["symbol"] == "RELIANCE"), None)
    assert row is not None and row["lastBarAgeSec"] is not None
    assert row["stale"] is False
