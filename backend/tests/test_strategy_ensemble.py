"""Tests for Adaptive Ensemble strategy."""

import pytest

from app.strategies.base import SignalDirection
from app.strategies.ensemble.adaptive import AdaptiveEnsemble


def _sample_ensemble_bars(n: int = 300, seed: int = 42) -> list[dict]:
    import numpy as np
    rng = np.random.RandomState(seed)
    symbols = ["SPY", "QQQ", "IWM", "EFA", "GLD"]
    bars = []
    for i in range(n):
        ts = float(1700000000 + i * 86400)
        for sym in symbols:
            drift = rng.normal(0.0003, 0.01)
            base = 100.0
            bars.append({
                "ts": float(1700000000 + i * 86400),
                "symbol": sym,
                "open": base * (1 + np.random.normal(0, 0.001)),
                "high": base * (1 + abs(np.random.normal(0, 0.002))),
                "low": base * (1 - abs(np.random.normal(0, 0.002))),
                "close": base,
                "volume": 1000000,
            })
    return bars


class TestAdaptiveEnsemble:
    def test_strategy_metadata(self):
        s = AdaptiveEnsemble()
        assert s.strategy_id == "punch_adaptive_ensemble"
        assert s.family == "ensemble"
        assert len(s.parameter_schema) > 5
        assert s.warmup_bars == 252

    def test_returns_none_before_warmup(self):
        s = AdaptiveEnsemble()
        bars = [{"ts": float(1700000000 + i * 86400), "symbol": "SPY", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000000} for i in range(100)]
        for i in range(100):
            sig = AdaptiveEnsemble().generate_signal(bars, i)
            assert sig is None

    def test_regime_detection(self):
        s = AdaptiveEnsemble()
        bars = _sample_ensemble_bars(300)
        regime = s._detect_regime(bars, 250)
        assert regime in ["TRENDING", "RANGING", "HIGH_VOL", "LOW_VOL", "NEUTRAL", "UNKNOWN"]

    def test_eligible_families_by_regime(self):
        s = AdaptiveEnsemble()
        # Test default mapping
        assert "trend" in s._get_eligible_families("TRENDING")
        assert "reversion" in s._get_eligible_families("RANGING")
        assert "carry" in s._get_eligible_families("HIGH_VOL")

    def test_generates_allocation(self):
        s = AdaptiveEnsemble(
            eligible_families=["trend", "reversion", "carry"],
            regime_filters={"TRENDING": ["trend", "breakout"], "RANGING": ["reversion", "carry"]},
            rebalance_frequency=1,
        )
        bars = _sample_ensemble_bars(500)
        signals = []
        for i in range(300, 500):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        assert len(signals) > 0
        sig = signals[0]
        assert "allocation" in sig.metadata
        assert "regime" in sig.metadata

    def test_parameter_snapshot(self):
        s = AdaptiveEnsemble(eligible_families=["trend", "reversion"])
        snap = s.parameter_snapshot()
        assert snap["eligible_families"] == ["trend", "reversion"]

    def test_rebalance_frequency(self):
        s = AdaptiveEnsemble(rebalance_frequency=10)
        bars = _sample_ensemble_bars(300)
        signals = []
        for i in range(252, 300):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        assert len(signals) <= 6  # ~50 bars / 10 = 5 rebalances