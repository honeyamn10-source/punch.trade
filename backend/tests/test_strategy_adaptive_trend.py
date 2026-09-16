"""Tests for Adaptive Multi-Horizon Trend strategy."""

from app.strategies.base import SignalDirection
from app.strategies.trend.adaptive_trend import AdaptiveMultiHorizonTrend


def _sample_bars(n: int = 300, trend: str = "up", seed: int = 42) -> list[dict]:
    import numpy as np

    rng = np.random.RandomState(seed)
    base = 100.0
    bars = []
    for i in range(n):
        if trend == "up":
            drift = 0.0005
        elif trend == "down":
            drift = -0.0005
        else:
            drift = 0.0
        ret = rng.normal(drift, 0.01)
        base *= 1 + ret
        bars.append(
            {
                "ts": float(1700000000 + i * 300),
                "symbol": "BTC/USDT",
                "open": base * (1 + rng.normal(0, 0.001)),
                "high": base * (1 + abs(rng.normal(0, 0.002))),
                "low": base * (1 - abs(rng.normal(0, 0.002))),
                "close": base,
                "volume": float(rng.lognormal(10, 0.5)),
            }
        )
    return bars


class TestAdaptiveMultiHorizonTrend:
    def test_strategy_metadata(self):
        s = AdaptiveMultiHorizonTrend()
        assert s.strategy_id == "punch_adaptive_trend"
        assert s.family.name == "TREND"
        assert len(s.parameter_schema) > 10
        assert s.warmup_bars == 100

    def test_returns_none_before_warmup(self):
        s = AdaptiveMultiHorizonTrend()
        bars = _sample_bars(50)
        for i in range(50):
            sig = s.generate_signal(bars, i)
            assert sig is None

    def test_generates_long_in_uptrend(self):
        s = AdaptiveMultiHorizonTrend(
            adx_threshold=15.0,
            vol_percentile_high=95,
            vol_percentile_low=5,
            trend_breadth_threshold=0.1,
        )
        bars = _sample_bars(400, trend="up")
        signals = []
        for i in range(150, 400):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        assert len(signals) > 0
        longs = [sig for sig in signals if sig.direction.name == "LONG"]
        assert len(longs) > 0

    def test_no_short_by_default(self):
        s = AdaptiveMultiHorizonTrend(
            adx_threshold=15.0,
            vol_percentile_high=95,
            vol_percentile_low=5,
            trend_breadth_threshold=0.1,
        )
        bars = _sample_bars(400, trend="down")
        signals = []
        for i in range(150, 400):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        shorts = [sig for sig in signals if sig.direction.name == "SHORT"]
        assert len(shorts) == 0

    def test_allows_short_when_enabled(self):
        s = AdaptiveMultiHorizonTrend(
            use_shorting=True,
            adx_threshold=15.0,
            vol_percentile_high=95,
            vol_percentile_low=5,
            trend_breadth_threshold=0.1,
        )
        bars = _sample_bars(400, trend="down")
        signals = []
        for i in range(150, 400):
            sig = s.generate_signal(bars, i)
            if sig:
                signals.append(sig)
        shorts = [sig for sig in signals if sig.direction.name == "SHORT"]
        assert len(shorts) > 0

    def test_signal_has_required_fields(self):
        s = AdaptiveMultiHorizonTrend(
            adx_threshold=15.0,
            vol_percentile_high=95,
            vol_percentile_low=5,
            trend_breadth_threshold=0.1,
        )
        bars = _sample_bars(400, trend="up")
        sig = s.generate_signal(bars, 200)
        assert sig is not None
        assert sig.strategy_id == "punch_adaptive_trend"
        assert sig.symbol == "BTC/USDT"
        assert sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)
        assert sig.price > 0
        assert sig.stop_loss is not None
        assert "trend_breadth" in sig.metadata
        assert "adx" in sig.metadata

    def test_parameter_snapshot(self):
        s = AdaptiveMultiHorizonTrend(short_period=15, adx_threshold=30)
        snap = s.parameter_snapshot()
        assert snap["short_period"] == 15
        assert snap["adx_threshold"] == 30

    def test_weights_normalized(self):
        s = AdaptiveMultiHorizonTrend(weight_short=1.0, weight_medium=1.0, weight_long=1.0)
        total = s.params["weight_short"] + s.params["weight_medium"] + s.params["weight_long"]
        assert abs(total - 1.0) < 1e-6
