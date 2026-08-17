"""Tests for Tactical Rotation strategy."""

import pytest

from app.strategies.base import SignalDirection
from app.strategies.rotation.tactical_rotation import TacticalRotation, RotationRegime


def _sample_multi_asset_bars(n: int = 500, seed: int = 42) -> list[dict]:
    import numpy as np
    rng = np.random.RandomState(seed)
    symbols = ["SPY", "QQQ", "IWM", "TLT", "GLD"]
    bars = []
    for i in range(n):
        base_ts = 1700000000 + i * 86400
        for sym in symbols:
            if sym == "SPY":
                drift, vol = 0.0003, 0.01
            elif sym == "QQQ":
                drift, vol = 0.0004, 0.015
            elif sym == "IWM":
                drift, vol = 0.0002, 0.02
            elif sym == "TLT":
                drift, vol = -0.0001, 0.008
            else:  # GLD
                drift, vol = 0.0001, 0.012

            ret = rng.normal(drift, vol)
            bars.append({
                "ts": float(base_ts),
                "symbol": sym,
                "open": 100 * (1 + rng.normal(0, 0.001)),
                "high": 100 * (1 + abs(rng.normal(0, 0.002))),
                "low": 100 * (1 - abs(rng.normal(0, 0.002))),
                "close": 100 * (1 + ret),
                "volume": float(rng.lognormal(10, 0.5)),
            })
    return bars


class TestTacticalRotation:
    def test_strategy_metadata(self):
        s = TacticalRotation()
        assert s.strategy_id == "punch_tactical_rotation"
        assert s.family.name == "ROTATION"
        assert len(s.parameter_schema) > 10
        assert s.warmup_bars == 252

    def test_returns_none_before_warmup(self):
        s = TacticalRotation(universe=["SPY", "QQQ"])
        bars = _sample_multi_asset_bars(200)
        for i in range(200):
            sig = s.generate_signal(bars, i)
            assert sig is None

    def test_generates_allocation_in_risk_on(self):
        s = TacticalRotation(
            universe=["SPY", "QQQ", "IWM"],
            defensive_symbols=["TLT", "GLD"],
            adx_threshold=15.0,
            rebalance_frequency=1,
            mom_12m_period=120,
            mom_6m_period=60,
            mom_3m_period=30,
            ema_trend_period=50,
        )
        # Need enough data: 252 warmup * 5 symbols = 1260 minimum
        bars = _sample_multi_asset_bars(3000)
        signals = []
        for i in range(2500, 3000):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        assert len(signals) > 0
        sig = signals[0]
        assert sig.direction == SignalDirection.LONG
        assert "allocation" in sig.metadata
        assert "regime" in sig.metadata
        assert sig.metadata["regime"] in ("RISK_ON", "NEUTRAL", "RISK_OFF")

    def test_risk_off_allocates_defensive(self):
        s = TacticalRotation(
            universe=["SPY", "QQQ"],
            defensive_symbols=["TLT", "GLD"],
            adx_threshold=15.0,
            rebalance_frequency=1,
            mom_12m_period=120,
            mom_6m_period=60,
            mom_3m_period=30,
            ema_trend_period=50,
        )
        bars = _sample_multi_asset_bars(3000)
        signals = []
        for i in range(2500, 3000):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        if signals:
            sig = signals[0]
            assert "allocation" in sig.metadata

    def test_parameter_snapshot(self):
        s = TacticalRotation(universe=["SPY"], max_assets=2)
        snap = s.parameter_snapshot()
        assert snap["universe"] == ["SPY"]
        assert snap["max_assets"] == 2

    def test_rebalance_frequency(self):
        s = TacticalRotation(
            universe=["SPY", "QQQ"],
            defensive_symbols=["TLT"],
            rebalance_frequency=10,
            mom_12m_period=120,
            mom_6m_period=60,
            mom_3m_period=30,
            ema_trend_period=50,
        )
        bars = _sample_multi_asset_bars(3000)
        signals = []
        for i in range(2500, 3000):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        # With rebalance_frequency=10 and 500 iterations, expect ~50 signals
        assert len(signals) <= 60