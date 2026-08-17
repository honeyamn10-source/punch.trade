"""Observability + /api/v1 tests: request tracing, metrics, health, envelope."""

import os

import pytest
from fastapi.testclient import TestClient

from app import api
from app import config
from app import obs
from app import security

client = TestClient(api.app)

H = {"X-Punch-Token": config.API_TOKEN}


def test_request_id_header_present():
    r = client.get("/api/health")
    assert r.headers.get("X-Request-Id")


def test_error_envelope_on_unauthorized():
    r = client.get("/api/strategies")  # no token
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["requestId"]
    assert body["error"]["message"]


def test_error_envelope_preserves_typed_codes():
    r = client.post("/api/orders", headers=H, json={
        "broker": "paper", "symbol": "RELIANCE", "side": "buy", "qty": 1,
        "signalId": "does-not-exist"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "SIGNAL_NOT_FOUND"
    assert body["error"]["requestId"]


def test_validation_error_envelope():
    r = client.post("/api/v1/orders", headers=H, json={"qty": -5})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rate_limit_error_keeps_retry_after():
    # assert the envelope shape of a typed HTTPException through the handler
    exc = api.HTTPException(429, detail={"code": "RATE_LIMITED",
                                         "message": "slow down",
                                         "retryAfter": 7})
    envelope = api._error_envelope(exc, "req-1")
    assert envelope["error"]["code"] == "RATE_LIMITED"
    assert envelope["error"]["retryAfter"] == 7


def test_metrics_endpoint_shape():
    r = client.get("/api/v1/system/metrics", headers=H)
    assert r.status_code == 200
    m = r.json()
    assert "uptimeSec" in m
    assert "requests" in m["counters"]
    assert "signals" in m and "orders" in m and "trades" in m
    assert "breakerOpen" in m["risk"] and "armed" in m["risk"]
    assert isinstance(m["errorBuckets"], dict)


def test_health_endpoint_shape():
    r = client.get("/api/v1/system/health", headers=H)
    assert r.status_code == 200
    h = r.json()
    assert h["status"] in ("ok", "degraded")
    assert "db" in h and "feed" in h and "brokers" in h
    assert h["db"]["ok"] is True


def test_event_log_written_and_sanitized(monkeypatch, tmp_path):
    log_file = tmp_path / "events.jsonl"
    monkeypatch.setattr(obs, "EVENTS_LOG", str(log_file))
    obs.log_request("rid-1", "GET", "/api/health", 200, 1.5)
    obs.log_event("custom", {"note": "fine\u0000", "requestId": "rid-2"})
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    import json as _json
    first = _json.loads(lines[0])
    assert first["kind"] == "api.request"
    assert first["requestId"] == "rid-1"
    assert first["status"] == 200
    second = _json.loads(lines[1])
    assert second["note"] == "fine"  # control char stripped
    assert "requestId" in second


def test_counters_incr():
    before = obs.counters().get("requests", 0)
    client.get("/api/health")
    assert obs.counters().get("requests", 0) >= before + 1


def test_v1_alias_endpoints_reachable():
    for path in ("/api/v1/strategies", "/api/v1/signals/last",
                 "/api/v1/risk/state", "/api/v1/execution/trades",
                 "/api/v1/system/storage", "/api/v1/system/status",
                 "/api/v1/ai/status"):
        r = client.get(path, headers=H)
        assert r.status_code == 200, path
    r = client.post("/api/v1/execution/reconcile", headers=H,
                    json={"broker": "paper"})
    assert r.status_code == 200