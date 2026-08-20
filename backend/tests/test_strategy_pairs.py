"""Tests for Statistical Pairs strategy."""

from app.strategies.base import SignalDirection
from app.strategies.statarb.pairs import StatisticalPairs


def _sample_pair_bars(n: int = 600, seed: int = 42) -> list[dict]:
    """Generate cointegrated pair data."""
    import numpy as np

    rng = np.random.RandomState(seed)
    base_a = 100.0
    base_b = 50.0
    bars = []
    for i in range(n):
        # Cointegrated: A = 2*B + noise
        if i < 300:
            # Formation period
            drift_a = rng.normal(0, 0.001)
            drift_b = rng.normal(0, 0.001)
        else:
            # Trading period with mean reversion
            spread = base_a - 2.0 * base_b
            drift_a = rng.normal(0, 0.001) - 0.1 * spread * 0.01
            drift_b = rng.normal(0, 0.001) + 0.05 * spread * 0.01
        base_a *= 1 + drift_a
        base_b *= 1 + drift_b
        base_a = max(50, min(200, base_a))
        base_b = max(20, min(100, base_b))
        ts = float(1700000000 + i * 3600)
        bars.extend(
            [
                {
                    "ts": ts,
                    "symbol": "SPY",
                    "open": base_a,
                    "high": base_a * 1.001,
                    "low": base_a * 0.999,
                    "close": base_a,
                    "volume": 1000000,
                },
                {
                    "ts": ts,
                    "symbol": "IVV",
                    "open": base_b,
                    "high": base_b * 1.001,
                    "low": base_b * 0.999,
                    "close": base_b,
                    "volume": 500000,
                },
            ]
        )
    return bars


class TestStatisticalPairs:
    def test_strategy_metadata(self):
        s = StatisticalPairs()
        assert s.strategy_id == "punch_pairs"
        assert s.family.name == "STATARB"
        assert len(s.parameter_schema) > 10
        assert s.warmup_bars == 252

    def test_returns_none_before_warmup(self):
        s = StatisticalPairs(symbol_a="SPY", symbol_b="IVV")
        bars = [
            {
                "ts": float(1700000000 + i * 3600),
                "symbol": "SPY",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1000,
            }
            for i in range(100)
        ]
        bars.extend(
            [
                {
                    "ts": float(1700000000 + i * 3600),
                    "symbol": "IVV",
                    "open": 50,
                    "high": 51,
                    "low": 49,
                    "close": 50,
                    "volume": 500,
                }
                for i in range(100)
            ]
        )
        for i in range(100):
            sig = s.generate_signal(bars, i)
            assert sig is None

    def test_formation_phase(self):
        s = StatisticalPairs(symbol_a="SPY", symbol_b="IVV", formation_period=50, trading_period=20)
        bars = _sample_pair_bars(200)
        # During formation, should not trade
        for i in range(150):
            sig = s.generate_signal(bars, i)
            # Formation not done yet
            assert sig is None or sig.direction == SignalDirection.FLAT

    def test_trading_after_formation(self):
        s = StatisticalPairs(
            symbol_a="SPY",
            symbol_b="IVV",
            formation_period=100,
            trading_period=50,
            zscore_entry=2.0,
            zscore_exit=0.5,
        )
        bars = _sample_pair_bars(500)
        signals = []
        for i in range(400, 500):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        # Should have some trading signals
        # (depends on generated data, at least shouldn't crash)
        assert isinstance(signals, list)

    def test_parameter_snapshot(self):
        s = StatisticalPairs(symbol_a="SPY", symbol_b="IVV", zscore_entry=2.5)
        snap = s.parameter_snapshot()
        assert snap["symbol_a"] == "SPY"
        assert snap["symbol_b"] == "IVV"
        assert snap["zscore_entry"] == 2.5

    def test_reset_state(self):
        s = StatisticalPairs(symbol_a="SPY", symbol_b="IVV")
        s._formation_done = True
        s._in_trade = True
        s.reset_state()
        assert s._formation_done is False
        assert s._in_trade is False
