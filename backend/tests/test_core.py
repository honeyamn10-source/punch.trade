"""Unit tests for indicators, engine dedup and backtester."""

import pytest

from app import indicators
from app.backtest import backtest
from app.engine import StrategyRunner


def _bars(prices):
    out = []
    for i, c in enumerate(prices):
        out.append({"ts": i, "open": c, "high": c * 1.01, "low": c * 0.99,
                    "close": c, "volume": 1000})
    return out


# ------------------------------------------------------------ indicators
def test_sma():
    out = indicators.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[4] == pytest.approx(4.0)


def test_rsi_extremes():
    rising = list(range(1, 31))
    out = indicators.rsi(rising, 14)
    assert out[-1] is not None and out[-1] > 90
    falling = list(range(30, 0, -1))
    out2 = indicators.rsi(falling, 14)
    assert out2[-1] < 10


def test_crosses():
    series = [10, 30, 5, 20]
    assert indicators.crossed_below(series, 2, 10)
    assert not indicators.crossed_below(series, 3, 10)
    up = [20, 5, 10, 40]
    assert indicators.crossed_above(up, 3, 10)


# ---------------------------------------------------------------- engine
RSI_STRAT = {
    "id": "t", "name": "Test", "symbol": "X", "interval": "5m",
    "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
    "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 55},
    "tp_pct": 2.0, "sl_pct": 1.0,
}


def _rsi_cross_series():
    """One cycle: oscillation above RSI 30, a dip that crosses below 30,
    then a recovery that crosses back above 55."""
    prices = [100 + (i % 8) * 1.0 for i in range(30)]
    p = prices[-1]
    for _ in range(30):
        p -= 1.2
        prices.append(p)
    for _ in range(35):
        p += 1.0
        prices.append(p)
    return prices


def test_engine_fires_once_and_dedups():
    runner = StrategyRunner(RSI_STRAT)
    bars = _bars(_rsi_cross_series())
    fired = [runner.on_bar(bars[: i + 1]) for i in range(1, len(bars))]
    signals = [s for s in fired if s is not None]
    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].target_price > signals[0].entry > signals[0].stop_loss


def test_engine_reactivates_after_exit():
    runner = StrategyRunner(RSI_STRAT)
    bars = _bars(_rsi_cross_series() + _rsi_cross_series())
    fired = [runner.on_bar(bars[: i + 1]) for i in range(1, len(bars))]
    signals = [s for s in fired if s is not None]
    assert len(signals) == 2  # fires again on the second dip


def test_backtest_reuses_engine():
    strategy = dict(RSI_STRAT)
    bars = _bars(_rsi_cross_series() + _rsi_cross_series())
    result = backtest(strategy, bars)
    assert result["trades"] == 2
    assert 0 <= result["winRate"] <= 100
    assert result["maxDrawdownPct"] >= 0
    assert result["exitSplit"]["TP"] + result["exitSplit"]["SL"] == result["trades"]


def test_backtest_insufficient_data():
    result = backtest(RSI_STRAT, _bars([100] * 10))
    assert "error" in result