"""Trading-safety gates: credential isolation, kill-switch precedence, arming.

These tests pin the execution invariants that matter in an AI-assisted trading
workflow: paper/research never loads live credentials into adapters, duplicate
requests still respect kill switches, and arming requires a real connection.
"""

from __future__ import annotations

import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import api, config, risk, vault


class FakeAdapter:
    """Minimal adapter exposing only a connection status."""

    def __init__(self, name: str, connected: bool) -> None:
        self.name = name
        self.connected = connected

    def status(self) -> dict:
        return {"broker": self.name, "connected": self.connected}


@pytest.fixture(autouse=True)
def _reset_trading_state():
    """Risk mode, shield and broker adapters are module-global — isolate them."""
    risk.set_shield(False)
    risk.set_mode("paper")
    api.brokers.adapters = {"paper": api.PaperBroker()}
    api._vault_backed_adapters.clear()
    yield
    risk.set_shield(False)
    risk.set_mode("paper")
    api.brokers.adapters = {"paper": api.PaperBroker()}
    api._vault_backed_adapters.clear()


@pytest.fixture
def client():
    with TestClient(api.app) as test_client:
        test_client.headers["X-Punch-Token"] = config.API_TOKEN
        yield test_client


def _place(client: TestClient, **kwargs: Any):
    body = {
        "broker": "paper",
        "symbol": "RELIANCE",
        "side": "buy",
        "qty": 1,
        "entry": 100.0,
        "targetPrice": 105.0,
        "stopLoss": 99.0,
    }
    body.update(kwargs)
    api.feed.last_ts["RELIANCE"] = time.time()
    return client.post("/api/orders", json=body)


def _fake_kite_factory(created: list):
    class FakeKite:
        def __init__(self, api_key: str, access_token: str) -> None:
            created.append(("kite", api_key, access_token))

        def status(self) -> dict:
            return {"broker": "kite", "connected": True}

    return FakeKite


def _fake_binance_factory(created: list):
    class FakeBinance:
        def __init__(self, api_key: str, api_secret: str, testnet: bool) -> None:
            created.append(("binance", api_key, api_secret, testnet))

        def status(self) -> dict:
            return {"broker": "binance", "connected": True, "testnet": self.testnet}

    return FakeBinance


def _fake_openalgo_factory(created: list):
    class FakeOpenAlgo:
        def __init__(self, host: str, apikey: str, broker: str) -> None:
            created.append(("openalgo", host, apikey, broker))

        def status(self) -> dict:
            return {"broker": "openalgo", "connected": True}

    return FakeOpenAlgo


def test_paper_and_research_never_load_vault_credentials(monkeypatch) -> None:
    created: list = []
    monkeypatch.setattr(vault, "brokers", lambda: ["kite", "binance", "openalgo"])
    monkeypatch.setattr(
        vault,
        "load",
        lambda name: {
            "kite": {"api_key": "KITE_FAKE_KEY", "access_token": "KITE_FAKE_TOKEN"},
            "binance": {"api_key": "BIN_FAKE_KEY", "api_secret": "BIN_FAKE_SECRET"},
            "openalgo": {"host": "http://local", "apikey": "OPENALGO_FAKE_KEY"},
        }[name],
    )
    monkeypatch.setattr(api, "KiteAdapter", _fake_kite_factory(created))
    monkeypatch.setattr(api, "CCXTBroker", _fake_binance_factory(created))
    monkeypatch.setattr(api, "OpenAlgoAdapter", _fake_openalgo_factory(created))

    risk.set_mode("research")
    api._restore_live_adapters()
    assert created == []
    assert set(api.brokers.adapters) == {"paper"}

    risk.set_mode("paper")
    api._restore_live_adapters()
    assert created == []
    assert set(api.brokers.adapters) == {"paper"}

    monkeypatch.setattr(config, "API_TOKEN", "strong-token-for-live-test")
    risk.set_mode("live")
    api._restore_live_adapters()
    assert {name for name, *_ in created} == {"kite", "binance", "openalgo"}
    assert api._vault_backed_adapters == {"kite", "binance", "openalgo"}
    assert set(api.brokers.adapters) >= {"paper", "kite", "binance", "openalgo"}

    api._release_live_adapters()
    assert set(api.brokers.adapters) == {"paper"}
    assert api._vault_backed_adapters == set()


def test_broker_connect_stores_credentials_without_loading_adapters(
    client: TestClient, monkeypatch
) -> None:
    saved: dict[str, dict] = {}
    monkeypatch.setattr(vault, "save", lambda name, creds: saved.update({name: creds}))
    monkeypatch.setattr(
        api,
        "generate_session",
        lambda *_: {"api_key": "KITE_FAKE_KEY", "access_token": "KITE_FAKE_TOKEN"},
    )
    monkeypatch.setattr(api, "CCXTBroker", _fake_binance_factory([]))

    kite = client.post(
        "/api/broker/kite/connect",
        json={"api_key": "REQ_KEY", "api_secret": "REQ_SECRET", "request_token": "REQ_TOKEN"},
    )
    assert kite.status_code == 200
    assert kite.json()["connected"] is False
    assert kite.json()["stored"] is True
    assert "kite" not in api.brokers.adapters

    binance = client.post(
        "/api/broker/binance/connect",
        json={"api_key": "BIN_FAKE_KEY", "api_secret": "BIN_FAKE_SECRET"},
    )
    assert binance.status_code == 200
    body = binance.json()
    assert body["connected"] is False and body["stored"] is True
    assert body["testnet"] is True and body["simulated"] is True
    assert "binance" not in api.brokers.adapters

    for canary in ("KITE_FAKE_KEY", "KITE_FAKE_TOKEN", "BIN_FAKE_KEY", "BIN_FAKE_SECRET"):
        assert canary not in kite.text
        assert canary not in binance.text
    assert saved["kite"]["api_key"] == "KITE_FAKE_KEY"
    assert saved["binance"]["testnet"] is True


def test_arming_requires_adapter_status_not_dict_membership(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(config, "API_TOKEN", "strong-token-for-arm-test")
    client.headers["X-Punch-Token"] = "strong-token-for-arm-test"
    risk.set_mode("live")
    api.brokers.adapters["stub"] = FakeAdapter("stub", connected=False)

    refused = client.post("/api/system/arm", json={"broker": "stub"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "BROKER_NOT_CONNECTED"
    assert "stub" not in risk.armed()

    api.brokers.adapters["stub"] = FakeAdapter("stub", connected=True)
    armed = client.post("/api/system/arm", json={"broker": "stub"})
    assert armed.status_code == 200
    assert "stub" in risk.armed()


def test_duplicate_replay_respects_shield_and_emergency_stop(client: TestClient) -> None:
    request_id = "req-" + uuid.uuid4().hex[:12]
    first = _place(client, clientRequestId=request_id)
    assert first.status_code == 200, first.text

    shielded = client.post("/api/risk/shield", json={"on": True})
    assert shielded.status_code == 200
    replay_while_shielded = _place(client, clientRequestId=request_id)
    assert replay_while_shielded.status_code == 409
    assert replay_while_shielded.json()["error"]["code"] == "SHIELD_ACTIVE"

    client.post("/api/risk/shield", json={"on": False})
    replay_after_lift = _place(client, clientRequestId=request_id)
    assert replay_after_lift.status_code == 200
    assert replay_after_lift.json()["duplicate"] is True

    client.post("/api/system/stop")
    replay_after_stop = _place(client, clientRequestId=request_id)
    assert replay_after_stop.status_code == 409
    assert replay_after_stop.json()["error"]["code"] == "MODE_BLOCKED"


def test_default_paper_orders_are_explicitly_simulated(client: TestClient) -> None:
    assert risk.mode() == "paper"
    assert api.brokers.adapters["paper"].status()["account"] == "paper-simulated"
    placed = _place(client)
    assert placed.status_code == 200, placed.text
    assert placed.json()["mode"] == "paper"
    assert placed.json()["broker"] == "paper"


def test_packaging_scopes_editable_install_to_backend_app() -> None:
    root = Path(__file__).resolve().parents[2]
    config_data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert config_data["build-system"]["build-backend"] == "setuptools.build_meta"
    find = config_data["tool"]["setuptools"]["packages"]["find"]
    assert find["where"] == ["backend"]
    assert find["include"] == ["app*"]
    assert find["namespaces"] is False
    assert "backend" in config_data["tool"]["pytest"]["ini_options"]["pythonpath"]
