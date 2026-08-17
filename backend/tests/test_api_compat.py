"""API compatibility suite.

Locks the wire schema of critical endpoints so a future change either
updates this file deliberately or fails CI. Schema drift here is a
breaking-change signal, not a stylistic preference.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import api, config, risk

TOKEN = {"X-Punch-Token": config.API_TOKEN}


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        for symbol in ("RELIANCE", "X"):
            api.feed.last_ts[symbol] = time.time()  # avoid the async bar-seed race
        c.headers.update(TOKEN)
        yield c


@pytest.fixture(autouse=True)
def _isolate_risk_state():
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)
    yield
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)


# ------------------------------------------------------------ health ----
def test_health_schema(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_v1_health_schema(client):
    body = client.get("/api/v1/system/health").json()
    for key in ("status", "version", "gitCommit", "uptimeSec", "db", "feed", "brokers"):
        assert key in body, key
    assert body["db"]["ok"] is True
    assert body["version"].count(".") == 2


def test_v1_metrics_schema(client):
    body = client.get("/api/v1/system/metrics").json()
    for key in ("counters", "errorBuckets", "signals", "orders", "trades", "risk"):
        assert key in body, key
    for key in ("ledger", "filled", "rejected", "open"):
        assert key in body["orders"], key


# ------------------------------------------------------------ signals ----
def test_signals_last_schema(client):
    body = client.get("/api/signals/last").json()
    assert isinstance(body["signals"], list)


def test_signals_history_schema(client):
    body = client.get("/api/signals/history").json()
    assert isinstance(body["signals"], list)


# ------------------------------------------------------------- orders ----
def test_place_order_schema_and_ledger(client):
    r = client.post(
        "/api/orders",
        json={
            "broker": "paper",
            "symbol": "RELIANCE",
            "side": "buy",
            "qty": 1,
            "entry": 100.0,
            "targetPrice": 101.0,
            "stopLoss": 99.0,
            "clientRequestId": "compat-" + uuid.uuid4().hex[:8],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["status"] == "FILLED"
    assert body["result"]["orderId"]

    ledger = client.get("/api/execution/ledger").json()
    assert ledger["orders"][-1]["id"] == body["result"]["orderId"]


def test_error_envelope_schema(client):
    r = client.get("/api/definitely-not-a-route", headers=TOKEN)
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "NOT_FOUND"
    assert err["requestId"]


def test_reconciliation_schema(client):
    r = client.post("/api/execution/reconcile", json={"broker": "paper"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ok" in body and "mismatches" in body


# -------------------------------------------------------- market data ----
def test_positions_and_fills_schema(client):
    pos = client.get("/api/positions", params={"broker": "paper"}).json()
    assert pos["broker"] == "paper" and isinstance(pos["positions"], list)
    fills = client.get("/api/fills", params={"broker": "paper"}).json()
    assert fills["broker"] == "paper" and isinstance(fills["fills"], list)


def test_analytics_schema(client):
    body = client.get("/api/analytics").json()
    for key in ("closed", "wins", "losses", "winRate", "netPnlPct", "equityCurve"):
        assert key in body, key


# ------------------------------------------------------------ research ----
def test_research_schema(client):
    r = client.post("/api/research/rsi-reversal", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "sample",
        "splits",
        "walkForward",
        "parameterStability",
        "bootstrap",
        "regimePerformance",
        "qualityGate",
    ):
        assert key in body, key
    assert "passed" in body["qualityGate"] and "score" in body["qualityGate"]
    assert "checks" in body["qualityGate"]


# ---------------------------------------------------------------- risk ----
def test_risk_state_and_sizing_schema(client):
    st = client.get("/api/risk/state").json()
    for key in ("mode", "armed", "breakerOpen", "reconciliationOk"):
        assert key in st, key
    sz = client.post(
        "/api/risk/sizing", json={"equity": 1_000_000, "riskPct": 0.01, "entry": 100, "stop": 99}
    ).json()
    assert sz["qty"] == 10000 and sz["riskAmount"] == 10000.0


# ------------------------------------------------------------------ AI ----
def test_ai_status_schema(client):
    body = client.get("/api/ai/status").json()
    for key in ("enabled", "model", "host", "reason"):
        assert key in body, key


# ------------------------------------------------------------ v1 sync ----
def test_v1_aliases_match_v0(client):
    """The versioned surface must not silently drift from /api."""
    v0 = client.get("/api/strategies").json()
    v1 = client.get("/api/v1/strategies").json()
    assert v0 == v1
    assert set(client.get("/api/v1/system/status").json()) == set(
        client.get("/api/system/status").json()
    )
