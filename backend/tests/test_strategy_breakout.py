"""Tests for Volatility Breakout and ORB strategies - simplified."""

import pytest

from app.strategies.base import SignalDirection
from app.strategies.breakout.volatility_breakout import VolatilityBreakout
from app.strategies.breakout.orb import OpeningRangeBreakout


class TestVolatilityBreakout:
    def test_strategy_metadata(self):
        s = VolatilityBreakout()
        assert s.strategy_id == "punch_volatility_breakout"
        assert s.family.name == "BREAKOUT"
        assert len(s.parameter_schema) > 10
        assert s.warmup_bars == 100

    def test_returns_none_before_warmup(self):
        s = VolatilityBreakout()
        # Create minimal bars
        bars = [{"ts": float(1700000000 + i * 300), "symbol": "BTC/USDT", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000} for i in range(50)]
        for i in range(50):
            sig = VolatilityBreakout().generate_signal(bars, i)
            assert sig is None

    def test_parameter_snapshot(self):
        s = VolatilityBreakout(bb_bandwidth_pct=15.0, atr_pct_max=25.0)
        snap = s.parameter_snapshot()
        assert snap["bb_bandwidth_pct"] == 15.0
        assert snap["atr_pct_max"] == 25.0


class TestOpeningRangeBreakout:
    def test_strategy_metadata(self):
        s = OpeningRangeBreakout()
        assert s.strategy_id == "punch_opening_range_breakout"
        assert s.family.name == "BREAKOUT"
        assert len(s.parameter_schema) > 5

    def test_returns_none_before_warmup(self):
        s = OpeningRangeBreakout()
        bars = [{"ts": float(1700000000 + i * 300), "symbol": "SPY", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000} for i in range(30)]
        for i in range(30):
            sig = s.generate_signal(bars, i)
            assert sig is None

    def test_parameter_snapshot(self):
        s = OpeningRangeBreakout(opening_minutes=15, volume_surge_pct=80.0)
        snap = s.parameter_snapshot()
        assert snap["opening_minutes"] == 15
        assert snap["volume_surge_pct"] == 80.0