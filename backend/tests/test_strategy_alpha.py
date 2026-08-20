"""Tests for the Advanced Alpha strategy family (research-backed)."""

import numpy as np

from app.strategies.alpha.hurst_reversion import HurstGatedReversion, hurst_rs
from app.strategies.alpha.trend_carry import TrendCarryComposite
from app.strategies.alpha.vol_managed_momentum import VolManagedMomentum
from app.strategies.alpha.volume_flow import VolumeFlowImbalance
from app.strategies.base import SignalDirection, StrategyRegistry


def _bars(prices, volume=1000.0, sym="BTC/USDT", ts0=1700000000.0, step=3600):
    out = []
    for i, p in enumerate(prices):
        hi = p * 1.001
        lo = p * 0.999
        out.append(
            {
                "ts": ts0 + i * step,
                "symbol": sym,
                "open": p,
                "high": hi,
                "low": lo,
                "close": p,
                "volume": volume,
            }
        )
    return out


def _run(strategy, bars):
    sigs = []
    for i in range(len(bars)):
        s = strategy.generate_signal(bars, i)
        if s is not None:
            sigs.append((i, s))
    return sigs


def test_registry_contains_alpha_strategies():
    ids = {s.strategy_id for s in StrategyRegistry.all()}
    assert "punch_vol_managed_momentum" in ids
    assert "punch_hurst_reversion" in ids
    assert "punch_volume_flow" in ids
    assert "punch_trend_carry" in ids


# --------------------------------------------------------- Hurst R/S -------
def test_hurst_rs_trending_series_is_persistent():
    rng = np.random.default_rng(7)
    prices = np.cumsum(np.ones(600) * 0.1 + rng.normal(0, 0.02, 600))
    returns = np.diff(prices) / prices[:-1]
    h = hurst_rs(returns)
    assert not np.isnan(h)
    assert h > 0.55


def test_hurst_rs_mean_reverting_series_is_antipersistent():
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 0.05, 600)
    reverting = np.zeros(600)
    for i in range(1, 600):
        reverting[i] = -0.7 * reverting[i - 1] + noise[i]
    prices = 100 + reverting * 10
    returns = np.diff(prices) / prices[:-1]
    h = hurst_rs(returns)
    assert not np.isnan(h)
    assert h < 0.45


# ------------------------------------------- Vol-Managed Momentum ---------
def test_vol_managed_momentum_longs_uptrend():
    prices = [100 * (1.003**i) for i in range(300)]
    bars = _bars(prices)
    s = VolManagedMomentum()
    sigs = _run(s, bars)
    entries = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    shorts = [sig for _, sig in sigs if sig.direction == SignalDirection.SHORT]
    assert entries, "should produce long signals in an uptrend"
    assert not shorts, "no shorts in an uptrend"
    assert all(sig.position_size is not None and sig.position_size > 0 for sig in entries)
    assert all(sig.stop_loss is not None and sig.stop_loss < sig.price for sig in entries)


def test_vol_managed_momentum_scales_position_inversely_to_vol():
    s = VolManagedMomentum()
    rng = np.random.default_rng(3)
    base = [100 * (1.002**i) for i in range(400)]
    calm = _bars([p * (1 + rng.normal(0, 0.002)) for p in base])
    storm = _bars([p * (1 + rng.normal(0, 0.02)) for p in base])
    sig_calm = [s for _, s in _run(s, calm) if s.direction == SignalDirection.LONG]
    s2 = VolManagedMomentum()
    sig_storm = [s for _, s in _run(s2, storm) if s.direction == SignalDirection.LONG]
    assert sig_calm and sig_storm
    assert np.mean([x.position_size for x in sig_calm]) > np.mean(
        [x.position_size for x in sig_storm]
    )


def test_vol_managed_momentum_no_signal_on_flat_market():
    bars = _bars([100.0] * 250)
    s = VolManagedMomentum()
    sigs = _run(s, bars)
    assert not sigs


# --------------------------------------------- Hurst-Gated Reversion ------
def test_hurst_reversion_flat_when_not_antipersistent():
    prices = [100 * (1.003**i) for i in range(400)]
    bars = _bars(prices)
    s = HurstGatedReversion()
    sigs = _run(s, bars)
    assert not sigs, "trending market should gate reversion off"


def test_hurst_reversion_mean_reverting_data():
    rng = np.random.default_rng(5)
    reverting = [100.0]
    for _i in range(1, 500):
        reverting.append(reverting[-1] + (-0.85 * (reverting[-1] - 100) + rng.normal(0, 1.5)))
    bars = _bars(reverting)
    s = HurstGatedReversion(entry_z=1.5, exit_z=0.4, hurst_window=90)
    sigs = _run(s, bars)
    longs = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    assert longs, "anti-persistent series should trigger reversion entries"
    assert any(sig.direction == SignalDirection.FLAT for _, sig in sigs)


# ------------------------------------------------- Volume-Flow Imbalance ---
def test_volume_flow_uptrend_with_buying_pressure():
    bars = []
    ts0 = 1700000000.0
    for i in range(120):
        p = 100 * (1.002**i)
        bars.append(
            {
                "ts": ts0 + i * 3600,
                "symbol": "BTC/USDT",
                "open": p * 0.995,
                "high": p * 1.005,
                "low": p * 0.99,
                "close": p,
                "volume": 2000.0,
            }
        )
    s = VolumeFlowImbalance(flow_lookback=12, entry_flow=0.1)
    sigs = _run(s, bars)
    longs = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    assert longs, "sustained buying pressure should produce longs"


def test_volume_flow_flat_when_balanced():
    bars = []
    ts0 = 1700000000.0
    for i in range(150):
        p = 100.0
        up = i % 2 == 0
        bars.append(
            {
                "ts": ts0 + i * 3600,
                "symbol": "BTC/USDT",
                "open": p,
                "high": p * 1.005,
                "low": p * 0.995,
                "close": p * 1.004 if up else p * 0.996,
                "volume": 1000.0,
            }
        )
    s = VolumeFlowImbalance(flow_lookback=12, entry_flow=0.3)
    sigs = _run(s, bars)
    assert not any(
        sig.direction in (SignalDirection.LONG, SignalDirection.SHORT) for _, sig in sigs
    )


# ---------------------------------------------------- Trend-Carry Combo ----
def test_trend_carry_uses_funding_field_when_present():
    prices = [100 * (1.002**i) for i in range(200)]
    bars = _bars(prices)
    for i, b in enumerate(bars):
        b["funding"] = 0.0005 if i % 3 == 0 else 0.0
    s = TrendCarryComposite(entry_threshold=0.02, exit_threshold=0.005)
    sigs = _run(s, bars)
    entries = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    assert entries
    assert all(sig.metadata.get("carry") is not None for sig in entries)


def test_trend_carry_falls_back_to_trend_only():
    prices = [100 * (1.008**i) for i in range(300)]
    bars = _bars(prices)
    s = TrendCarryComposite(entry_threshold=0.02, exit_threshold=0.005)
    sigs = _run(s, bars)
    entries = [sig for _, sig in sigs if sig.direction == SignalDirection.LONG]
    assert entries
    assert all(sig.metadata.get("carry") is None for sig in entries)


def test_trend_carry_flats_in_dead_market():
    bars = _bars([100.0] * 250)
    s = TrendCarryComposite()
    sigs = _run(s, bars)
    flats = [sig for _, sig in sigs if sig.direction == SignalDirection.FLAT]
    assert flats
