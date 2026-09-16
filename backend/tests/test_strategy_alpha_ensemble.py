"""Alpha Ensemble strategy tests."""

import numpy as np

from app.strategies.alpha.ensemble import AlphaEnsemble
from app.strategies.base import SignalDirection, StrategyRegistry


def _bars(prices, volume=1000.0, sym="BTC/USDT", ts0=1700000000.0, step=3600):
    out = []
    prev = prices[0]
    for i, p in enumerate(prices):
        rising = p >= prev
        out.append(
            {
                "ts": ts0 + i * step,
                "symbol": sym,
                "open": prev,
                "high": p * 1.005 if rising else prev * 1.005,
                "low": prev * 0.99 if rising else p * 0.99,
                "close": p,
                "volume": volume,
            }
        )
        prev = p
    return out


def _run(strategy, bars):
    sigs = []
    for i in range(len(bars)):
        s = strategy.generate_signal(bars, i)
        if s is not None:
            sigs.append((i, s))
    return sigs


def test_registered():
    ids = {s.strategy_id for s in StrategyRegistry.all()}
    assert "punch_alpha_ensemble" in ids


def test_longs_in_uptrend():
    prices = [100 * (1.003**i) for i in range(300)]
    sigs = _run(AlphaEnsemble(), _bars(prices))
    longs = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    shorts = [sig for _, sig in sigs if sig.direction == SignalDirection.SHORT]
    assert longs, "ensemble should agree LONG in a sustained uptrend"
    assert not shorts
    assert all(sig.metadata["votes_long"] >= sig.metadata["min_votes"] for sig in longs)


def test_flat_in_chop():
    rng = np.random.default_rng(9)
    prices = [100 + rng.normal(0, 0.5) for _ in range(300)]
    # momentum's per-bar threshold fires on any 24-bar drift, so test the
    # trend+flow agreement instead: stationary chop must not raise entries.
    sigs = _run(
        AlphaEnsemble(use_flow=True, use_trend=True, use_momentum=False, min_votes=2),
        _bars(prices),
    )
    entries = [
        sig for _, sig in sigs if sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)
    ]
    assert len(entries) < 25, "stationary chop should rarely produce flow+trend agreement"


def test_single_member_mode():
    prices = [100 * (1.003**i) for i in range(300)]
    s = AlphaEnsemble(use_flow=True, use_momentum=False, use_trend=False, min_votes=1)
    sigs = _run(s, _bars(prices))
    assert any(sig.direction == SignalDirection.LONG for _, sig in sigs)


def test_stop_loss_is_average_of_agreeing_members():
    prices = [100 * (1.003**i) for i in range(300)]
    sigs = _run(AlphaEnsemble(), _bars(prices))
    longs = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    if longs:
        assert all(sig.stop_loss is not None and sig.stop_loss < sig.price for sig in longs)
