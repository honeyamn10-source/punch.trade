"""Tests for Cross-Sectional Momentum strategy."""

from app.strategies.base import SignalDirection
from app.strategies.cross_section.momentum import CrossSectionalMomentum


def _sample_cross_section_bars(n: int = 500, seed: int = 42) -> list[dict]:
    """Generate multi-symbol bars with different momentum profiles."""
    import numpy as np

    rng = np.random.RandomState(seed)
    symbols = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT"]
    bars = []
    base_prices = {s: 100.0 for s in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT"]}

    for i in range(n):
        float(1700000000 + i * 86400)
        for sym in symbols:
            # Different momentum profiles
            if sym == "SPY":
                drift, vol = 0.0003, 0.01
            elif sym == "QQQ":
                drift, vol = 0.0004, 0.015
            elif sym == "IWM":
                drift, vol = 0.0002, 0.02
            elif sym == "EFA":
                drift, vol = 0.0001, 0.012
            elif sym == "EEM":
                drift, vol = 0.00005, 0.018
            elif sym == "GLD":
                drift, vol = 0.0001, 0.012
            else:  # TLT
                drift, vol = -0.0001, 0.008

            ret = rng.normal(drift, vol)
            base_prices[sym] *= 1 + ret
            base_prices[sym] = max(20, min(500, base_prices[sym]))

            bars.append(
                {
                    "ts": float(1700000000 + i * 86400),
                    "symbol": sym,
                    "open": base_prices[sym],
                    "high": base_prices[sym] * 1.001,
                    "low": base_prices[sym] * 0.999,
                    "close": base_prices[sym],
                    "volume": float(1000000),
                }
            )
    return bars


class TestCrossSectionalMomentum:
    def test_strategy_metadata(self):
        s = CrossSectionalMomentum()
        assert s.strategy_id == "punch_cross_section_momentum"
        assert s.family == "cross_section"
        assert len(s.parameter_schema) > 10
        assert s.warmup_bars == 252

    def test_returns_none_before_warmup(self):
        CrossSectionalMomentum(universe=["SPY", "QQQ"])
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
        bars.extend(
            [
                {
                    "ts": float(1700000000 + i * 86400),
                    "symbol": "QQQ",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 500000,
                }
                for i in range(100)
            ]
        )
        for i in range(100):
            sig = CrossSectionalMomentum(universe=["SPY", "QQQ"]).generate_signal(bars, i)
            assert sig is None

    def test_generates_allocation(self):
        s = CrossSectionalMomentum(
            universe=["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT"],
            mom_3m_period=63,
            mom_6m_period=126,
            mom_12m_period=120,  # Shorter for test data
            rebalance_frequency=1,
        )
        # Need enough data: 252 warmup per symbol * 7 symbols = 1764 minimum
        bars = _sample_cross_section_bars(2000)
        signals = []
        for i in range(1800, 2000):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        assert len(signals) > 0
        sig = signals[0]
        assert sig.direction == SignalDirection.LONG
        assert "allocation" in sig.metadata
        assert "ranking" in sig.metadata

    def test_rebalance_frequency(self):
        s = CrossSectionalMomentum(
            universe=["SPY", "QQQ", "IWM"],
            rebalance_frequency=10,
        )
        bars = _sample_cross_section_bars(300)
        signals = []
        for i in range(252, 300):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        # With rebalance_frequency=10, ~5 signals in 48 bars
        assert len(signals) <= 6

    def test_shorting_disabled_by_default(self):
        s = CrossSectionalMomentum(
            universe=["SPY", "QQQ", "IWM"],
            bottom_n=2,
        )
        bars = _sample_cross_section_bars(300)
        sig = s.generate_signal(bars, 252)
        if sig:
            allocation = sig.metadata.get("allocation", {})
            # Should not have short positions
            for v in allocation.values():
                assert v >= 0

    def test_shorting_when_enabled(self):
        s = CrossSectionalMomentum(
            universe=["SPY", "QQQ", "IWM", "EFA"],
            bottom_n=1,
            use_shorting=True,
        )
        bars = _sample_cross_section_bars(300)
        sig = s.generate_signal(bars, 252)
        if sig:
            allocation = sig.metadata.get("allocation", {})
            # Should have at least one short position
            shorts = [v for v in allocation.values() if v < 0]
            assert len(shorts) > 0

    def test_parameter_snapshot(self):
        s = CrossSectionalMomentum(universe=["SPY", "QQQ"], mom_3m_weight=0.3)
        snap = s.parameter_snapshot()
        assert snap["universe"] == ["SPY", "QQQ"]
        assert snap["mom_3m_weight"] == 0.3

    def test_skip_recent_period(self):
        s = CrossSectionalMomentum(
            universe=["SPY", "QQQ"],
            skip_recent_days=10,
            skip_recent=True,
        )
        bars = _sample_cross_section_bars(300)
        s.generate_signal(bars, 252)
        # Should not crash
        assert True
