"""Edge-case tests for the canonical market model and every indicator.

Covers AUD phases 3-4: candle validation, timeframe normalization, and
indicator behaviour on empty/single/flat/zero-volume/NaN/Infinity inputs.
"""

from __future__ import annotations

import math

import pytest

from app import indicators as ind
from app.market import Candle, DataQualityTracker, MarketDataError, normalize_timeframe


def _bars(closes, highs=None, lows=None, opens=None, volumes=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    opens = opens or list(closes)
    volumes = volumes or [1000] * n
    return [
        {
            "ts": i,
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
        }
        for i in range(n)
    ]


# ---------------------------------------------------------- market model --
def test_timeframe_normalization():
    assert normalize_timeframe("5m") == "5m"
    assert normalize_timeframe("60") == "1h"
    assert normalize_timeframe("240m") == "4h"
    assert normalize_timeframe("1D") == "1d"
    with pytest.raises(MarketDataError):
        normalize_timeframe("fortnight")
    with pytest.raises(MarketDataError):
        normalize_timeframe("")


def test_candle_validation_ok():
    c = Candle("RELIANCE", "5m", 0, 300, 100, 110, 90, 105, 5000)
    assert c.validate() is c


@pytest.mark.parametrize(
    "bad",
    [
        dict(open=100, high=90, low=90, close=105),  # high < open
        dict(open=100, high=110, low=120, close=105),  # low > close
        dict(open=100, high=110, low=90, close=105, volume=-1),
        dict(open=float("nan"), high=110, low=90, close=105),
        dict(open=100, high=float("inf"), low=90, close=105),
    ],
)
def test_candle_validation_rejects(bad):
    c = Candle(
        "X",
        "5m",
        0,
        300,
        bad.get("open", 100),
        bad.get("high", 110),
        bad.get("low", 90),
        bad.get("close", 105),
        bad.get("volume", 100),
    )
    with pytest.raises(MarketDataError):
        c.validate()


def test_candle_close_time_must_exceed_open_time():
    with pytest.raises(MarketDataError):
        Candle("X", "5m", 300, 300, 1, 2, 0.5, 1.5, 1).validate()


def test_candle_from_legacy_bar():
    c = Candle.from_legacy_bar(
        "TCS",
        {"ts": 100, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9},
        timeframe="5",
        source="paper-synthetic",
    )
    assert c.timeframe == "5m"
    assert c.source == "paper-synthetic"
    assert c.closed is True


def test_data_quality_tracker():
    q = DataQualityTracker()
    assert q.status("RELIANCE") == "UNKNOWN"
    q.observe(Candle("RELIANCE", "5m", 0, 300, 1, 2, 0.5, 1.5, 1))
    assert q.status("RELIANCE") == "GOOD"
    q.observe(Candle("RELIANCE", "5m", 0, 300, 1, 2, 0.5, 1.5, 1))  # dup ts
    assert q.status("RELIANCE") == "WARNING"
    q.observe(Candle("RELIANCE", "5m", -10, 300, 1, 2, 0.5, 1.5, 1))  # out of order
    assert q.status("RELIANCE") == "BAD"
    q.mark_invalid("RELIANCE")
    assert q.summary("RELIANCE")["invalid"] == 1


# ------------------------------------------------------------ indicators --
@pytest.mark.parametrize(
    "fn,args",
    [
        (ind.sma, ([1, 2, 3], 2)),
        (ind.ema, ([1, 2, 3], 2)),
        (ind.rsi, ([1, 2, 3], 2)),
        (ind.macd, ([1, 2, 3],)),
        (ind.bollinger, ([1, 2, 3],)),
        (ind.donchian, ([1, 2, 3],)),
    ],
)
def test_value_indicators_empty(fn, args):
    out = fn([], *args[1:])
    if isinstance(out, dict):
        assert all(v == [] for v in out.values())
    else:
        assert out == []


@pytest.mark.parametrize(
    "fn,args",
    [
        (ind.vwap, (_bars([1.0]),)),
        (ind.atr, (_bars([1.0]),)),
        (ind.stochastic, (_bars([1.0]),)),
        (ind.adx, (_bars([1.0]),)),
    ],
)
def test_bar_indicators_empty(fn, args):
    out = fn(args[0], *args[1:])
    assert out == [None]


def test_indicators_single_value_no_crash():
    bars = _bars([100.0])
    assert ind.sma([100.0], 20) == [None]
    assert ind.ema([100.0], 20) == [100.0]
    assert ind.rsi([100.0], 14) == [None]
    for fn in (ind.vwap, ind.atr, ind.stochastic, ind.adx):
        fn(bars, 14)


def test_indicators_flat_prices():
    flat = [100.0] * 50
    bars = [
        {"ts": i, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000}
        for i in range(50)
    ]
    assert ind.sma(flat, 20)[-1] == 100.0
    assert ind.rsi(flat, 14)[-1] == 50.0  # neutral, not 100
    assert ind.stochastic(bars, 14)[-1] == 50.0
    assert ind.bollinger(flat, 20)["upper"][-1] == 100.0
    assert ind.donchian(flat, 20)["high"][-1] == 100.0
    assert ind.atr(bars, 14)[-1] == 0.0
    assert ind.vwap(bars, 20)[-1] == 100.0


def test_rsi_monotonic_up_is_100_and_down_is_0():
    up = list(range(1, 40))
    down = list(range(40, 1, -1))
    assert ind.rsi(up, 14)[-1] == 100.0
    assert ind.rsi(down, 14)[-1] == 0.0


def test_rsi_known_hand_value():
    # 14 changes: 13 up of +1.0 and 1 down of -0.9286 -> RS = 14.0
    # RSI = 100 - 100/(1+14) = 93.333...
    values = [10.0] + [10.0 + i for i in range(1, 14)] + [23.0 - 0.9286]
    assert abs(ind.rsi(values, 14)[-1] - (100 - 100 / 15)) < 0.05


def test_indicators_zero_volume():
    bars = _bars([100.0] * 30, volumes=[0] * 30)
    assert ind.vwap(bars, 20)[-1] == 100.0  # no division by zero


def test_indicators_large_values():
    big = [1e9 + i * 1e6 for i in range(40)]
    assert ind.sma(big, 5)[-1] > 0
    assert 0 <= ind.rsi(big, 14)[-1] <= 100
    assert ind.atr(_bars(big), 14)[-1] > 0


@pytest.mark.parametrize("fn", [ind.sma, ind.ema, ind.rsi])
def test_indicators_reject_nan(fn):
    with pytest.raises(ValueError):
        fn([1.0, float("nan"), 3.0], 2)


@pytest.mark.parametrize("fn", [ind.vwap, ind.atr, ind.stochastic, ind.adx])
def test_bar_indicators_reject_nan(fn):
    bars = _bars([1.0, 2.0, 3.0])
    bars[1]["high"] = float("inf")
    with pytest.raises(ValueError):
        fn(bars, 2)


def test_indicators_warmup_returns_none_not_zero():
    out = ind.sma([1.0, 2.0], 5)
    assert out == [None, None]
    assert ind.rsi([1.0, 2.0, 3.0, 4.0], 14) == [None] * 4


def test_wilder_smooth_documented():
    assert ind._wilder_smooth([1.0, 1.0, 1.0], 3) == [1.0, 1.0, 1.0]


def test_adx_and_stochastic_known_shape():
    bars = _bars([100 + math.sin(i / 3) * 5 for i in range(60)])
    st = ind.stochastic(bars, 14)
    assert all((v is None) or (0 <= v <= 100) for v in st)
    ax = ind.adx(bars, 14)
    assert any(v is not None for v in ax)
    assert all((v is None) or (0 <= v <= 100) for v in ax)
