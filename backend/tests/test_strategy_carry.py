"""Tests for Carry Framework strategies."""

import pytest

from app.strategies.base import SignalDirection
from app.strategies.carry.carry import CarryFramework, FXCarry, CryptoFundingCarry


def _sample_carry_bars(n: int = 200, with_carry: bool = True) -> list[dict]:
    import numpy as np
    rng = np.random.RandomState(42)
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
    bars = []
    base_rates = {"EURUSD": 0.02, "GBPUSD": 0.03, "USDJPY": -0.01, "AUDUSD": 0.04}
    for i in range(n):
        ts = float(1700000000 + i * 86400)
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]:
            carry = base_rates[sym] + np.random.normal(0, 0.001)
            bars.append({
                "ts": float(1700000000 + i * 86400),
                "symbol": sym,
                "open": 1.0,
                "high": 1.001,
                "low": 0.999,
                "close": 1.0,
                "volume": 1000000,
                "carry": carry,
                "carry_signal": 1 if carry > 0.01 else (-1 if carry < -0.01 else 0),
            })
    return bars


class TestCarryFramework:
    def test_strategy_metadata(self):
        s = CarryFramework(universe=["EURUSD", "GBPUSD"])
        assert s.strategy_id == "punch_carry"
        assert s.family == "carry"
        assert len(s.parameter_schema) > 5
        assert s.warmup_bars == 60

    def test_returns_none_before_warmup(self):
        s = CarryFramework(universe=["EURUSD"])
        bars = [{"ts": float(1700000000 + i * 86400), "symbol": "EURUSD", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000} for i in range(30)]
        for i in range(30):
            sig = CarryFramework(universe=["EURUSD"]).generate_signal(bars, i)
            assert sig is None

    def test_data_unavailable_without_carry_field(self):
        s = CarryFramework(universe=["EURUSD"], data_required=True)
        bars = [{"ts": float(1700000000 + i * 86400), "symbol": "EURUSD", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000} for i in range(100)]
        sig = s.generate_signal(bars, 99)
        assert sig is not None
        assert sig.direction == SignalDirection.FLAT
        assert sig.metadata.get("status") == "data_unavailable"

    def test_works_with_carry_data(self):
        s = CarryFramework(
            universe=["EURUSD", "GBPUSD", "USDJPY"],
            min_carry=0.01,
            data_required=False,
        )
        bars = _sample_carry_bars(100)
        sig = s.generate_signal(bars, 99)
        assert sig is not None
        assert "allocation" in sig.metadata

    def test_shorting_when_enabled(self):
        s = CarryFramework(
            universe=["USDJPY"],  # Negative carry
            use_shorting=True,
            min_carry=0.005,
            data_required=False,
        )
        bars = _sample_carry_bars(100)
        sig = s.generate_signal(bars, 99)
        if sig and sig.metadata.get("allocation"):
            allocation = sig.metadata["allocation"]
            # Should have short position for USDJPY (negative carry)
            assert any(v < 0 for v in allocation.values())

    def test_no_shorting_when_disabled(self):
        s = CarryFramework(
            universe=["USDJPY"],
            use_shorting=False,
            data_required=False,
        )
        bars = _sample_carry_bars(100)
        sig = s.generate_signal(bars, 99)
        if sig and sig.metadata.get("allocation"):
            allocation = sig.metadata["allocation"]
            # Should not have short positions
            for v in allocation.values():
                assert v >= 0

    def test_parameter_snapshot(self):
        s = CarryFramework(universe=["EURUSD"], min_carry=0.02)
        snap = s.parameter_snapshot()
        assert snap["universe"] == ["EURUSD"]
        assert snap["min_carry"] == 0.02


class TestFXCarry:
    def test_fx_carry_metadata(self):
        s = FXCarry(currency_pairs=["EURUSD", "GBPUSD"])
        assert s.strategy_id == "punch_fx_carry"
        assert s.params["domain"] == "fx"

    def test_parameter_snapshot(self):
        s = FXCarry(currency_pairs=["EURUSD"], min_rate_diff=0.02)
        snap = s.parameter_snapshot()
        assert snap["currency_pairs"] == ["EURUSD"]
        assert snap["min_rate_diff"] == 0.02


class TestCryptoFundingCarry:
    def test_crypto_funding_metadata(self):
        s = CryptoFundingCarry(symbols=["BTCUSDT", "ETHUSDT"])
        assert s.strategy_id == "punch_crypto_funding_carry"
        assert s.params["domain"] == "crypto_funding"

    def test_parameter_snapshot(self):
        s = CryptoFundingCarry(symbols=["BTCUSDT"], min_funding_apy=0.20)
        snap = s.parameter_snapshot()
        assert snap["symbols"] == ["BTCUSDT"]
        assert snap["min_funding_apy"] == 0.20