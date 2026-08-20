"""Binance provider funding enrichment tests."""

import types

import pytest

from app.instruments import parse_instrument
from app.providers.binance import BinanceProvider


class FakeEx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.markets = {"BTC/USDT": {}}
        self.fail_funding = False

    def load_markets(self):
        return self.markets

    def fetch_ticker(self, symbol):
        return {"last": 63500.0, "bid": 63499.0, "ask": 63501.0, "timestamp": 1700000000000}

    def fetch_ohlcv(self, symbol, tf, limit=300):
        # bars at open ts 17:00, 18:00 and 01:00 next day (1h + 28h gap)
        return [
            [1700000000000, 63000.0, 63010.0, 62990.0, 63005.0, 10.0],
            [1700003600000, 63005.0, 63015.0, 62995.0, 63010.0, 12.0],
            [1700028800000, 63010.0, 63020.0, 63000.0, 63015.0, 9.0],
        ]

    def fetch_funding_rate_history(self, symbol, limit=500):
        if self.fail_funding:
            raise RuntimeError("fapi down")
        return [
            {"timestamp": 1700000000000, "fundingRate": 0.0001},
            {"timestamp": 1700028800000, "fundingRate": -0.00005},
            {"timestamp": 1700057600000, "fundingRate": 0.0002},
        ]


@pytest.fixture
def fake_ccxt(monkeypatch):
    shared = FakeEx(None)

    class Factory:
        def __new__(cls, cfg):
            return shared

    monkeypatch.setattr("app.providers.binance.ccxt", types.SimpleNamespace(binance=Factory))
    return shared


def test_funding_annotated_on_candles(fake_ccxt):
    p = BinanceProvider()
    bars = p.get_candles(parse_instrument("BTC/USDT"), "1h")
    assert len(bars) == 3
    assert bars[0]["funding"] == 0.0001  # settled 17:00 <= open 17:00
    assert bars[1]["funding"] == 0.0001  # settled 17:00 <= open 18:00
    assert bars[2]["funding"] == -0.00005  # settled 01:00 <= open 01:00


def test_funding_missing_when_fetch_fails(fake_ccxt):
    fake_ccxt.fail_funding = True
    p = BinanceProvider()
    bars = p.get_candles(parse_instrument("BTC/USDT"), "1h")
    assert len(bars) == 3
    assert all("funding" not in b for b in bars)
    assert p.last_error and "funding" in p.last_error


def test_funding_helper_chooses_most_recent_settlement():
    funding = [
        {"ts": 100.0, "rate": 0.001},
        {"ts": 200.0, "rate": 0.002},
        {"ts": 300.0, "rate": 0.003},
    ]
    assert BinanceProvider._funding_at(100.0, funding) == 0.001
    assert BinanceProvider._funding_at(250.0, funding) == 0.002
    assert BinanceProvider._funding_at(99.0, funding) is None
