"""Market data API tests — endpoint shapes, envelope, watchlist CRUD,
credential handling (never returned), secret-leakage guard."""

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.instruments import AssetClass
from app.market_router import MarketRouter
from app.providers.base import HealthState, MarketDataProvider

H = {"X-Punch-Token": "punch-demo-token"}


class StubProvider(MarketDataProvider):
    provider_id = "stub"
    display_name = "Stub"
    asset_classes = (
        AssetClass.CRYPTO,
        AssetClass.FOREX,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FUTURE,
        AssetClass.OPTION,
        AssetClass.COMMODITY,
    )
    state = HealthState.READY

    def health(self):
        return {"state": self.state.value, "provider": self.provider_id, "last_error": None}

    def capabilities(self):
        return {"search": True, "quote": True, "candles": True}

    def search_instruments(self, query, limit=20):
        return [{"symbol": "BTC/USDT", "asset_class": "CRYPTO", "provider": "stub"}]

    def get_instrument(self, instrument):
        return {"symbol": instrument.symbol, "provider": "stub"}

    def get_quote(self, instrument):
        return {
            "symbol": instrument.symbol,
            "price": 63000.0,
            "bid": 62999.0,
            "ask": 63001.0,
            "ts": 0,
            "source": "stub",
        }

    def get_candles(self, instrument, timeframe, *, start=None, end=None, limit=None):
        return [
            {
                "symbol": instrument.symbol,
                "timeframe": timeframe,
                "open_time": 0,
                "close_time": 60,
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 10,
                "closed": True,
                "source": "stub",
            }
        ]


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        api.market_router = MarketRouter({"stub": StubProvider()})
        api.market_router.routes = {k: ["stub"] for k in api.market_router.routes}
        yield c


def _stub_router():
    return MarketRouter({"stub": StubProvider()})


class TestEndpoints:
    def test_providers_shape(self, client):
        r = client.get("/api/v1/market/providers", headers=H)
        assert r.status_code == 200
        body = r.json()
        assert "providers" in body and "routes" in body
        assert body["providers"]["stub"]["state"] == "READY"

    def test_provider_health_unknown_404(self, client):
        assert client.get("/api/v1/market/providers/nope/health", headers=H).status_code == 404

    def test_search(self, client):
        r = client.get("/api/v1/market/search", params={"q": "BTC"}, headers=H)
        assert r.status_code == 200
        assert r.json()["results"][0]["symbol"] == "BTC/USDT"

    def test_instrument_parse(self, client):
        r = client.get(
            "/api/v1/market/instruments",
            params={"symbol": "NFO:NIFTY-2026-08-27-25000-CE"},
            headers=H,
        )
        assert r.status_code == 200
        assert r.json()["instrument"]["asset_class"] == "OPTION"

    def test_quote(self, client):
        r = client.get("/api/v1/market/quote", params={"symbol": "BTC/USDT"}, headers=H)
        assert r.status_code == 200
        assert r.json()["price"] == 63000.0

    def test_candles(self, client):
        r = client.get(
            "/api/v1/market/candles", params={"symbol": "BTC/USDT", "interval": "5m"}, headers=H
        )
        assert r.status_code == 200
        assert r.json()["bars"][0]["source"] == "stub"

    def test_candles_bad_timeframe(self, client):
        r = client.get(
            "/api/v1/market/candles", params={"symbol": "BTC/USDT", "interval": "9x"}, headers=H
        )
        assert r.status_code == 502
        body = r.json()
        assert body["error"]["code"] == "TIMEFRAME_UNSUPPORTED"
        assert "requestId" in body["error"]

    def test_auth_required(self, client):
        assert client.get("/api/v1/market/providers").status_code == 401


class TestWatchlist:
    def test_add_list_remove(self, client):
        r = client.post("/api/v1/market/watchlist", json={"symbol": "BTC/USDT"}, headers=H)
        assert r.status_code == 200
        assert r.json()["added"]["symbol"] == "BTC/USDT"
        lst = client.get("/api/v1/market/watchlist", headers=H)
        assert any(e["symbol"] == "BTC/USDT" for e in lst.json()["watchlist"])
        wid = lst.json()["watchlist"][0]["id"]
        d = client.delete(f"/api/v1/market/watchlist/{wid}", headers=H)
        assert d.json()["removed"] is True

    def test_add_invalid_symbol(self, client):
        r = client.post("/api/v1/market/watchlist", json={"symbol": "LSE:FOO"}, headers=H)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "SYMBOL_NOT_FOUND"


class TestCredentials:
    def test_save_returns_masked_only(self, client, monkeypatch):
        from app import vault

        saved = {}

        def fake_save(name, creds):
            saved[name] = creds

        monkeypatch.setattr(vault, "save", fake_save)
        r = client.post(
            "/api/v1/market/credentials/alpaca",
            json={"values": {"api_key": "PK-FAKEKEY1234", "api_secret": "s3cr3t-abcdef"}},
            headers=H,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["saved"] is True
        blob = str(body)
        assert "PK-FAKEKEY1234" not in blob
        assert "s3cr3t-abcdef" not in blob
        assert "••••" in blob  # masked tail only

    def test_unknown_provider_404(self, client):
        r = client.post("/api/v1/market/credentials/nope", json={"values": {}}, headers=H)
        assert r.status_code == 404

    def test_delete_credentials(self, client, monkeypatch):
        from app import vault

        monkeypatch.setattr(vault, "delete", lambda name: True)
        r = client.delete("/api/v1/market/credentials/dhan", headers=H)
        assert r.status_code == 200
        assert r.json()["deleted"] is True


class TestSecretLeakage:
    """Fake secrets must never appear in any API response."""

    FAKE_SECRETS = [
        "DHAN_FAKE_ACCESS_TOKEN_9f4a",
        "ALPACA_FAKE_API_KEY_7c21",
        "ANGEL_FAKE_TOTP_3b88",
    ]

    def test_endpoint_responses_clean(self, client, monkeypatch):
        from app import vault

        def fake_load(name):
            if name == "md_dhan":
                return {"client_id": "c", "access_token": self.FAKE_SECRETS[0]}
            if name == "md_alpaca":
                return {"api_key": self.FAKE_SECRETS[1], "api_secret": "alp-secret"}
            if name == "md_angel":
                return {"totp_secret": self.FAKE_SECRETS[2]}
            return None

        monkeypatch.setattr(vault, "load", fake_load)
        paths = [
            "/api/v1/market/providers",
            "/api/v1/market/search?q=BTC",
            "/api/v1/market/quote?symbol=BTC/USDT",
            "/api/v1/market/candles?symbol=BTC/USDT",
            "/api/v1/market/watchlist",
        ]
        for path in paths:
            r = client.get(path, headers=H)
            assert r.status_code == 200
            blob = str(r.json())
            for secret in self.FAKE_SECRETS:
                assert secret not in blob, f"secret leaked via {path}"
        r = client.get("/api/v1/market/providers/dhan/health", headers=H)
        assert self.FAKE_SECRETS[0] not in str(r.json())
