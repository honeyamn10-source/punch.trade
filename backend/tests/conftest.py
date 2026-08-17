"""Shared test fixtures: isolate SQLite + legacy JSONL from the real data/ dir."""

import pytest

import app.config as config
import app.db as db


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Every test gets its own empty DB + empty data dir (incl. TestClient
    startup, which now initializes the schema and archives legacy files)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    import app.api as api
    import app.execution as execution

    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "punch.db"))
    monkeypatch.setattr(api, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(api, "SIGNALS_LOG", str(data_dir / "signals.json"))
    monkeypatch.setattr(api, "ORDERS_LOG", str(data_dir / "orders.json"))
    monkeypatch.setattr(api, "POSITIONS_LOG", str(data_dir / "positions.json"))
    monkeypatch.setattr(execution, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(execution, "TRADES_LOG", str(data_dir / "trades.json"))
    db.reset()
    yield
    db.reset()