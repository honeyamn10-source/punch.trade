"""Vault tests: encrypted at-rest credential store + key rotation."""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import api, vault


@pytest.fixture(autouse=True)
def _isolate_vault(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(vault, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(vault, "VAULT_PATH", str(data_dir / "vault.json"))
    monkeypatch.setattr(vault, "SECRET_PATH", str(data_dir / ".secret"))
    monkeypatch.setattr(vault, "_fernet", Fernet(vault._load_key()))
    yield


def test_save_load_delete_roundtrip():
    assert vault.brokers() == []
    vault.save("binance", {"api_key": "k", "api_secret": "s", "testnet": True})
    assert vault.brokers() == ["binance"]
    assert vault.load("binance")["api_key"] == "k"
    vault.delete("binance")
    assert vault.brokers() == []
    assert vault.load("binance") is None


def test_vault_file_is_encrypted_at_rest():
    vault.save("kite", {"api_key": "sekret"})
    with open(vault.VAULT_PATH, "rb") as f:
        raw = f.read()
    assert b"sekret" not in raw
    assert vault.load("kite")["api_key"] == "sekret"


def test_rotate_key_preserves_credentials():
    vault.save("openalgo", {"host": "h", "apikey": "a", "broker": "b"})
    with open(vault.SECRET_PATH, "rb") as f:
        old_key = f.read()
    rep = vault.rotate_key()
    assert rep["brokers"] == ["openalgo"]
    assert rep["encrypted"] == 1
    with open(vault.SECRET_PATH, "rb") as f:
        new_key = f.read()
    assert new_key != old_key
    assert vault.load("openalgo")["host"] == "h"


def test_rotate_key_via_api():
    vault.save("binance", {"api_key": "k", "api_secret": "s"})
    with TestClient(api.app) as client:
        r = client.post("/api/vault/rotate-key", headers={"X-Punch-Token": "punch-demo-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["rotated"] is True
    assert body["brokers"] == ["binance"]
    assert vault.load("binance")["api_secret"] == "s"
