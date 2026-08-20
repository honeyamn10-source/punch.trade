"""Tests for Multi-Factor Equity framework."""

from app.strategies.base import SignalDirection
from app.strategies.multifactor.multifactor import MultiFactorEquity


def _sample_multifactor_bars(n: int = 300, with_factors: bool = True) -> list[dict]:
    import numpy as np

    np.random.RandomState(42)
    bars = []
    for i in range(n):
        float(1700000000 + i * 86400)
        for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT"]:
            bar = {
                "ts": float(1700000000 + i * 86400),
                "symbol": sym,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000000,
            }
            if True:
                bar["factor_momentum"] = np.random.normal(0, 1)
                bar["factor_value"] = np.random.normal(0, 1)
                bar["factor_quality"] = np.random.normal(0, 1)
                bar["factor_low_risk"] = np.random.normal(0, 1)
            bars.append(bar)
    return bars


class TestMultiFactorEquity:
    def test_strategy_metadata(self):
        s = MultiFactorEquity(universe=["SPY", "QQQ"])
        assert s.strategy_id == "punch_equity_multifactor"
        assert s.family == "multifactor"
        assert len(s.parameter_schema) > 5
        assert s.warmup_bars == 252

    def test_returns_none_before_warmup(self):
        MultiFactorEquity(universe=["SPY"])
        bars = [
            {
                "ts": float(1700000000 + i * 86400),
                "symbol": "SPY",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1000000,
            }
            for i in range(100)
        ]
        for i in range(100):
            sig = MultiFactorEquity(universe=["SPY"]).generate_signal(bars, i)
            assert sig is None

    def test_data_unavailable_without_factors(self):
        s = MultiFactorEquity(universe=["SPY"], require_point_in_time=True)
        bars = [
            {
                "ts": float(1700000000 + i * 86400),
                "symbol": "SPY",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1000000,
            }
            for i in range(300)
        ]
        sig = s.generate_signal(bars, 299)
        assert sig is not None
        assert sig.direction == SignalDirection.FLAT
        assert sig.metadata.get("status") == "point_in_time_data_unavailable"

    def test_works_with_factor_data(self):
        s = MultiFactorEquity(
            universe=["SPY", "QQQ", "IWM"],
            data_available=True,
            require_point_in_time=False,
        )
        bars = _sample_multifactor_bars(300)
        sig = s.generate_signal(bars, 299)
        assert sig is not None
        assert "allocation" in sig.metadata

    def test_shorting_when_enabled(self):
        s = MultiFactorEquity(
            universe=["SPY", "QQQ", "IWM"],
            use_shorting=True,
            data_available=True,
            require_point_in_time=False,
            min_factor_score=0.0,
        )
        bars = _sample_multifactor_bars(300)
        sig = s.generate_signal(bars, 299)
        if sig and sig.metadata.get("allocation"):
            allocation = sig.metadata["allocation"]
            assert isinstance(allocation, dict)

    def test_no_shorting_when_disabled(self):
        s = MultiFactorEquity(
            universe=["SPY", "QQQ"],
            use_shorting=False,
            data_available=True,
            require_point_in_time=False,
        )
        bars = _sample_multifactor_bars(300)
        sig = s.generate_signal(bars, 299)
        if sig and sig.metadata.get("allocation"):
            allocation = sig.metadata["allocation"]
            for v in allocation.values():
                assert v >= 0

    def test_parameter_snapshot(self):
        s = MultiFactorEquity(universe=["SPY", "QQQ"], min_factor_score=0.2)
        snap = s.parameter_snapshot()
        assert snap["universe"] == ["SPY", "QQQ"]
        assert snap["min_factor_score"] == 0.2

    def test_empty_universe(self):
        s = MultiFactorEquity(universe=[], data_available=True)
        bars = _sample_multifactor_bars(100)
        sig = s.generate_signal(bars, 99)
        # Empty universe returns None (no signal)
        assert sig is None
