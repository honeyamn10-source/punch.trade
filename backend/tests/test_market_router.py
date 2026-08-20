"""Market router tests — routing priority, failover, no-silent-mixing.

All providers are fakes; no network calls happen.
"""

import pytest

from app.instruments import AssetClass
from app.market_router import MarketRouter
from app.providers.base import (
    HealthState,
    MarketDataProvider,
    ProviderError,
    ProviderErrorCode,
    candle_dict,
)


class FakeProvider(MarketDataProvider):
    def __init__(self, pid: str, state: HealthState = HealthState.READY, assets=()):
        self.provider_id = pid
        self.display_name = pid
        self.asset_classes = tuple(assets)
        self.state = state
        self.configured = True
        self.limiter = None  # type: ignore[assignment]

    def search_instruments(self, query, limit=20):
        if self.state == HealthState.READY:
            return [{"symbol": "BTC/USDT", "provider": self.provider_id}]
        raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, f"{self.provider_id} down")

    def get_instrument(self, instrument):
        return {"symbol": instrument.symbol, "provider": self.provider_id}

    def get_quote(self, instrument):
        if self.state != HealthState.READY:
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, f"{self.provider_id} down")
        return {"symbol": instrument.symbol, "price": 100.0, "ts": 0, "source": self.provider_id}

    def get_candles(self, instrument, timeframe, *, start=None, end=None, limit=None):
        if self.state != HealthState.READY:
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, f"{self.provider_id} down")
        return [
            candle_dict(
                instrument.symbol,
                timeframe,
                1786900000 + 60 * i,
                100.0,
                101.0,
                99.0,
                100.5,
                10.0,
                self.provider_id,
            )
            for i in range(3)
        ]


def _router(providers, routes=None):
    r = MarketRouter(providers)
    if routes is not None:
        r.routes = routes
    return r


class TestRouting:
    def test_primary_used_when_ready(self):
        primary = FakeProvider("p1", assets=(AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", assets=(AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        out = r.get_quote("BTC/USDT")
        assert out["source"] == "p1"
        assert out["fallbackUsed"] is False

    def test_fallback_on_offline_primary(self):
        primary = FakeProvider("p1", HealthState.OFFLINE, (AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", HealthState.READY, (AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        out = r.get_quote("BTC/USDT")
        assert out["source"] == "p2"
        assert out["fallbackUsed"] is True

    def test_fallback_on_rate_limited_primary(self):
        primary = FakeProvider("p1", HealthState.RATE_LIMITED, (AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", HealthState.READY, (AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        out = r.get_quote("BTC/USDT")
        assert out["source"] == "p2"

    def test_all_unavailable_raises(self):
        r = _router(
            {"p1": FakeProvider("p1", HealthState.OFFLINE, (AssetClass.CRYPTO,))},
            {AssetClass.CRYPTO: ["p1"]},
        )
        with pytest.raises(ProviderError) as ei:
            r.get_quote("BTC/USDT")
        assert ei.value.code in (
            ProviderErrorCode.PROVIDER_OFFLINE,
            ProviderErrorCode.DATA_UNAVAILABLE,
        )

    def test_no_provider_for_asset(self):
        r = _router({}, {AssetClass.CRYPTO: []})
        with pytest.raises(ProviderError):
            r.get_candles("BTC/USDT", "5m")

    def test_disabled_provider_skipped(self):
        primary = FakeProvider("p1", HealthState.DISABLED, (AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", HealthState.READY, (AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        assert r.get_quote("BTC/USDT")["source"] == "p2"


class TestNoSilentMixing:
    def test_candles_come_from_single_provider(self):
        primary = FakeProvider("p1", assets=(AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", assets=(AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        out = r.get_candles("BTC/USDT", "5m", limit=3)
        assert out["provider"] == "p1"
        assert all(b["source"] == "p1" for b in out["bars"])
        assert out["fallbackUsed"] is False

    def test_candles_never_merge_on_fallback(self):
        primary = FakeProvider("p1", HealthState.OFFLINE, (AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", HealthState.READY, (AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        out = r.get_candles("BTC/USDT", "5m", limit=3)
        assert out["provider"] == "p2"
        assert all(b["source"] == "p2" for b in out["bars"])  # single source, labelled

    def test_provider_switch_is_logged(self):
        primary = FakeProvider("p1", HealthState.OFFLINE, (AssetClass.CRYPTO,))
        fallback = FakeProvider("p2", HealthState.READY, (AssetClass.CRYPTO,))
        r = _router({"p1": primary, "p2": fallback}, {AssetClass.CRYPTO: ["p1", "p2"]})
        r.get_quote("BTC/USDT")
        assert any(e["event"] == "MARKET_DATA_PROVIDER_CHANGED" for e in r.switch_log.recent())


class TestTimeframes:
    def test_unsupported_timeframe_rejected(self):
        r = _router(
            {"p1": FakeProvider("p1", assets=(AssetClass.CRYPTO,))},
            {AssetClass.CRYPTO: ["p1"]},
        )
        with pytest.raises(ProviderError) as ei:
            r.get_candles("BTC/USDT", "9x")
        assert ei.value.code == ProviderErrorCode.TIMEFRAME_UNSUPPORTED

    def test_routes_table(self):
        r = _router({"p1": FakeProvider("p1", assets=(AssetClass.CRYPTO,))})
        assert r.routes_table()[AssetClass.CRYPTO.value] == ["binance", "coingecko"]
        assert r.routes_table()[AssetClass.FOREX.value] == ["twelve_data", "alpha_vantage"]
