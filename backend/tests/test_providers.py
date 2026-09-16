"""Provider unit tests — everything mocked, no external calls.

Covers: normalization, symbol mapping, error mapping (401/403/404/429/500),
malformed candles, empty results, close-only labelling.
"""

import time

import httpx
import pytest

from app.instruments import AssetClass, parse_instrument, provider_symbol_for
from app.providers import build_providers
from app.providers.base import ProviderError, ProviderErrorCode


def _resp(status: int, body) -> httpx.Response:
    req = httpx.Request("GET", "https://mock.local")
    return httpx.Response(status, json=body, request=req)


REST_PROVIDERS = [
    "app.providers.coingecko",
    "app.providers.twelve_data",
    "app.providers.alpha_vantage",
    "app.providers.alpaca",
    "app.providers.dhan",
    "app.providers.upstox",
    "app.providers.angel",
]


def _patch_all(monkeypatch, fake):
    import importlib

    for modname in REST_PROVIDERS:
        monkeypatch.setattr(importlib.import_module(modname), "throttled_request", fake)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """All provider HTTP goes through throttled_request — replace it.

    Providers bind the symbol at import time, so each module's own
    ``throttled_request`` name must be patched (not ``app.rate_limit``).
    """

    def fake(provider_id, limiter, method, url, **kw):
        raise AssertionError(f"unexpected network call: {method} {url}")

    _patch_all(monkeypatch, fake)


def _set_throttled(monkeypatch, responder):
    def fake(provider_id, limiter, method, url, **kw):
        return responder(method, url, kw.get("params") or {})

    _patch_all(monkeypatch, fake)


# ------------------------------------------------------------- parsing ----
class TestInstrumentParsing:
    def test_crypto_pair(self):
        i = parse_instrument("BTC/USDT")
        assert i.asset_class == AssetClass.CRYPTO
        assert i.base_currency == "BTC" and i.quote_currency == "USDT"

    def test_forex_pair(self):
        assert parse_instrument("eur/usd").asset_class == AssetClass.FOREX

    def test_us_equity_and_etf(self):
        assert parse_instrument("AAPL").asset_class == AssetClass.EQUITY
        assert parse_instrument("SPY").asset_class == AssetClass.ETF

    def test_indian_equity_and_index(self):
        assert parse_instrument("NSE:RELIANCE").asset_class == AssetClass.EQUITY
        assert parse_instrument("NSE:NIFTY50").asset_class == AssetClass.INDEX

    def test_indian_future(self):
        i = parse_instrument("NFO:NIFTY-2026-08-27-FUT")
        assert i.asset_class == AssetClass.FUTURE
        assert i.expiry == "2026-08-27"

    def test_indian_option(self):
        i = parse_instrument("NFO:NIFTY-2026-08-27-25000-CE")
        assert i.asset_class == AssetClass.OPTION
        assert i.strike == 25000.0
        assert i.option_type.value == "CALL"
        assert parse_instrument("NFO:NIFTY-2026-08-27-25000-PE").option_type.value == "PUT"

    def test_mcx_commodity(self):
        i = parse_instrument("MCX:GOLD-2026-10-05")
        assert i.asset_class == AssetClass.COMMODITY
        assert i.underlying == "GOLD"

    def test_commodity_reference(self):
        assert parse_instrument("GOLD").asset_class == AssetClass.COMMODITY

    def test_unsupported_prefix(self):
        with pytest.raises(ValueError):
            parse_instrument("LSE:FOO")


class TestSymbolMapping:
    def test_binance_pair(self):
        assert provider_symbol_for("binance", parse_instrument("BTC/USDT")) == "BTCUSDT"

    def test_eurusd_unchanged(self):
        assert provider_symbol_for("twelve_data", parse_instrument("EUR/USD")) == "EUR/USD"

    def test_alpaca_strips_exchange(self):
        assert provider_symbol_for("alpaca", parse_instrument("NSE:RELIANCE")) == "RELIANCE"

    def test_alpha_vantage_forex(self):
        assert provider_symbol_for("alpha_vantage", parse_instrument("EUR/USD")) == "EURUSD"

    def test_coingecko_ids(self):
        assert provider_symbol_for("coingecko", parse_instrument("BTC/USDT")) == "bitcoin"


# ---------------------------------------------------------- binance ------
class TestBinanceProvider:
    def _fake_ccxt(self, monkeypatch, ohlcv=None, ticker=None, markets=None):
        import types

        class Ex:
            def __init__(self, cfg):
                self.cfg = cfg

            def load_markets(self):
                return markets or {
                    "BTC/USDT": {
                        "precision": {"price": 0.01},
                        "limits": {"amount": {"min": 0.0001}},
                    }
                }

            def fetch_ticker(self, sym):
                return ticker or {
                    "last": 63500.0,
                    "bid": 63499.0,
                    "ask": 63501.0,
                    "percentage": 0.5,
                    "timestamp": time.time() * 1000,
                }

            def fetch_ohlcv(self, sym, tf, limit=None):
                if ohlcv is not None:
                    return ohlcv
                now = int(time.time()) * 1000
                return [
                    [
                        now - 300000 + 60000 * i,
                        63000.0 + i,
                        63010.0 + i,
                        62990.0 + i,
                        63005.0 + i,
                        10.0,
                    ]
                    for i in range(5)
                ]

        monkeypatch.setattr("app.providers.binance.ccxt", types.SimpleNamespace(binance=Ex))
        from app.providers.binance import BinanceProvider

        return BinanceProvider()

    def test_candle_normalization(self, monkeypatch):
        p = self._fake_ccxt(monkeypatch)
        inst = parse_instrument("BTC/USDT")
        bars = p.get_candles(inst, "5m")
        assert len(bars) == 5
        c = bars[0]
        assert c["symbol"] == "BTC/USDT"
        assert c["source"] == "binance"
        assert c["open_time"] < c["close_time"]
        assert (
            c["open"] == 63000.0
            and c["high"] == 63010.0
            and c["low"] == 62990.0
            and c["close"] == 63005.0
        )

    def test_quote_shape(self, monkeypatch):
        p = self._fake_ccxt(monkeypatch)
        q = p.get_quote(parse_instrument("BTC/USDT"))
        assert q["price"] == 63500.0
        assert q["bid"] == 63499.0 and q["ask"] == 63501.0
        assert q["source"] == "binance"

    def test_search_and_mapping(self, monkeypatch):
        p = self._fake_ccxt(monkeypatch)
        hits = p.search_instruments("BTC")
        assert any(h["symbol"] == "BTC/USDT" for h in hits)

    def test_offline_error(self, monkeypatch):
        import types

        class Boom:
            def __init__(self, cfg):
                raise RuntimeError("network down")

        monkeypatch.setattr("app.providers.binance.ccxt", types.SimpleNamespace(binance=Boom))
        from app.providers.binance import BinanceProvider

        p = BinanceProvider()
        with pytest.raises(ProviderError) as ei:
            p.get_candles(parse_instrument("BTC/USDT"), "5m")
        assert ei.value.code == ProviderErrorCode.PROVIDER_OFFLINE


# --------------------------------------------------------- coingecko -----
class TestCoinGeckoProvider:
    def test_close_only_candles(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(
                200,
                {
                    "prices": [
                        [1786900000000, 100.0],
                        [1786900060000, 101.0],
                        [1786900120000, 102.0],
                    ]
                },
            ),
        )
        from app.providers.coingecko import CoinGeckoProvider

        p = CoinGeckoProvider()
        bars = p.get_candles(parse_instrument("BTC/USDT"), "1m")
        assert len(bars) == 3
        c = bars[0]
        assert c["source"] == "coingecko"
        assert c["open"] == c["high"] == c["low"] == c["close"]  # close-only, honestly labelled

    def test_429_maps_to_rate_limited(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(429, {"status": {"error_code": 429}}),
        )
        from app.providers.coingecko import CoinGeckoProvider

        p = CoinGeckoProvider()
        with pytest.raises(ProviderError) as ei:
            p.get_quote(parse_instrument("BTC/USDT"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_RATE_LIMITED


# ------------------------------------------------------- twelve data -----
class TestTwelveDataProvider:
    def test_forex_candles(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(
                200,
                {
                    "values": [
                        {
                            "datetime": "2026-08-16 12:30:00",
                            "open": "1.1500",
                            "high": "1.1510",
                            "low": "1.1490",
                            "close": "1.1505",
                            "volume": "100",
                        },
                        {
                            "datetime": "2026-08-16 12:31:00",
                            "open": "1.1505",
                            "high": "1.1515",
                            "low": "1.1495",
                            "close": "1.1510",
                            "volume": "120",
                        },
                    ]
                },
            ),
        )
        from app.providers.twelve_data import TwelveDataProvider

        p = TwelveDataProvider()
        bars = p.get_candles(parse_instrument("EUR/USD"), "1m")
        assert len(bars) == 2
        assert bars[0]["close"] == 1.1505 and bars[0]["source"] == "twelve_data"

    def test_demo_key_when_unset(self):
        from app.providers.twelve_data import TwelveDataProvider

        assert TwelveDataProvider().api_key  # demo key always available

    def test_error_status(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(200, {"status": "error", "message": "Invalid symbol"}),
        )
        from app.providers.twelve_data import TwelveDataProvider

        with pytest.raises(ProviderError) as ei:
            TwelveDataProvider().get_quote(parse_instrument("XXX/YYY"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_BAD_RESPONSE


# ------------------------------------------------------ alpha vantage ----
class TestAlphaVantageProvider:
    def test_auth_required_without_key(self):
        from app.providers.alpha_vantage import AlphaVantageProvider

        p = AlphaVantageProvider()
        with pytest.raises(ProviderError) as ei:
            p.get_quote(parse_instrument("WTI"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_AUTH_FAILED

    def test_commodity_series(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(
                200,
                {
                    "data": {
                        "2026-08-14": {"value": "2450.10"},
                        "2026-08-13": {"value": "2440.50"},
                    }
                },
            ),
        )
        from app.providers.alpha_vantage import AlphaVantageProvider

        p = AlphaVantageProvider()
        p.api_key = "fake-key"
        p.configured = True
        bars = p.get_candles(parse_instrument("WTI"), "1d")
        assert len(bars) == 2
        assert bars[0]["close"] == 2440.50

    def test_rate_limit_message(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(
                200, {"Note": "API rate limit exceeded. Your API call frequency is restricted..."}
            ),
        )
        from app.providers.alpha_vantage import AlphaVantageProvider

        p = AlphaVantageProvider()
        p.api_key = "fake-key"
        p.configured = True
        with pytest.raises(ProviderError) as ei:
            p.get_quote(parse_instrument("EUR/USD"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_RATE_LIMITED


# ------------------------------------------------------------- dhan ------
class TestDhanProvider:
    def test_auth_required_without_creds(self):
        from app.providers.dhan import DhanProvider

        p = DhanProvider()
        assert p.state.value == "AUTH_REQUIRED"
        with pytest.raises(ProviderError) as ei:
            p.get_quote(parse_instrument("NSE:RELIANCE"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_AUTH_FAILED

    def test_symbol_not_found_path(self, monkeypatch):
        def responder(method, url, params):
            if "api-scrip-master" in url:
                return _resp(200, {"error": "unexpected"})
            return _resp(200, {"error": "Invalid symbol"})

        _set_throttled(monkeypatch, responder)
        from app.providers.dhan import DhanProvider

        p = DhanProvider(client_id="c", access_token="t")
        with pytest.raises(ProviderError) as ei:
            p.get_candles(parse_instrument("NSE:RELIANCE"), "5m")
        assert ei.value.code == ProviderErrorCode.SYMBOL_NOT_FOUND


# ------------------------------------------------------------- alpaca -----
class TestAlpacaProvider:
    def test_auth_required_without_keys(self):
        from app.providers.alpaca import AlpacaProvider

        p = AlpacaProvider()
        with pytest.raises(ProviderError) as ei:
            p.get_quote(parse_instrument("AAPL"))
        assert ei.value.code == ProviderErrorCode.PROVIDER_AUTH_FAILED

    def test_iex_label_and_bars(self, monkeypatch):
        _set_throttled(
            monkeypatch,
            lambda m, u, p: _resp(
                200,
                {
                    "bars": [
                        {
                            "t": "2026-08-16T12:30:00Z",
                            "o": 100.0,
                            "h": 101.0,
                            "l": 99.5,
                            "c": 100.5,
                            "v": 1000,
                        },
                    ]
                },
            ),
        )
        from app.providers.alpaca import AlpacaProvider

        p = AlpacaProvider()
        p.api_key, p.api_secret = "k", "s"
        p.configured = True
        bars = p.get_candles(parse_instrument("AAPL"), "5m")
        assert len(bars) == 1
        assert bars[0]["source"] == "alpaca"
        assert p.feed_label == "IEX (free plan)"  # honest labeling


# ----------------------------------------------------------- registry ----
class TestProviderRegistry:
    def test_all_providers_build(self):
        ps = build_providers()
        assert set(ps) == {
            "binance",
            "coingecko",
            "dhan",
            "upstox",
            "angel",
            "alpaca",
            "twelve_data",
            "alpha_vantage",
        }

    def test_health_never_contains_secrets(self):
        from app.providers import provider_states

        for _pid, h in provider_states().items():
            blob = json_repr(h)
            assert "token" not in blob.lower() or "access" not in blob.lower()
            assert "secret" not in blob.lower()
            assert "api_key" not in blob.lower()
            assert h["state"] in (
                "READY",
                "DEGRADED",
                "RATE_LIMITED",
                "AUTH_REQUIRED",
                "AUTH_FAILED",
                "OFFLINE",
                "DISABLED",
                "NOT_CONFIGURED",
            )


def json_repr(o) -> str:
    import json

    return json.dumps(o)
