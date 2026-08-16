"""Unit tests for indicators, engine dedup and backtester."""

import pytest

from app import indicators
from app.backtest import backtest
from app.engine import StrategyRunner


def _bars(prices):
    out = []
    for i, c in enumerate(prices):
        out.append({"ts": i, "open": c, "high": c * 1.002, "low": c * 0.998,
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


# ------------------------------------------------- composite indicators
def test_macd_histogram_zero_cross():
    rising = [100 + i * 0.5 for i in range(60)]
    out = indicators.macd(rising)
    assert out[0] == 0.0  # EMA seeds from the first bar
    assert any(v is not None for v in out)
    # strong rally -> histogram positive at the end
    assert out[-1] > 0
    falling = [200 - i * 0.5 for i in range(60)]
    out2 = indicators.macd(falling)
    assert out2[-1] < 0


def test_bollinger_bands_order():
    values = [100 + (i % 5) * 2 for i in range(40)]
    b = indicators.bollinger(values, 20)
    assert b["mid"][30] is not None
    assert b["lower"][30] < b["mid"][30] < b["upper"][30]


def test_donchian_and_vwap():
    values = [100 + i for i in range(30)]
    d = indicators.donchian(values, 10)
    assert d["high"][29] == max(values[20:30])
    assert d["low"][29] == min(values[20:30])
    bars = [{"open": v, "high": v + 1, "low": v - 1, "close": v, "volume": 10}
            for v in values]
    w = indicators.vwap(bars, 10)
    assert w[29] is not None
    assert w[29] == sum((b["high"] + b["low"] + b["close"]) / 3 * 10 for b in bars[20:30]) / (10 * 10)


# -------------------------------------------- professional indicators ----
def _ohlc(prices):
    return [{"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 100}
            for c in prices]


def test_atr_positive_and_small_on_flat():
    flat = _ohlc([100.0] * 20)
    a = indicators.atr(flat, 14)
    assert a[0] is None
    assert a[-1] > 0
    spiky = [100] + [100 + (3 if i % 2 else -3) for i in range(19)]
    a2 = indicators.atr(_ohlc(spiky), 14)
    assert a2[-1] > a[-1]  # spiky series has higher ATR


def test_stochastic_bounds_and_extremes():
    rising = _ohlc([100 + i for i in range(30)])
    s = indicators.stochastic(rising, 14, 3)
    assert s[0] is None
    vals = [v for v in s if v is not None]
    assert all(0 <= v <= 100 for v in vals)
    falling = _ohlc([100 - i for i in range(30)])
    s2 = indicators.stochastic(falling, 14, 3)
    vals2 = [v for v in s2 if v is not None]
    assert vals2[-1] < 20  # deep downtrend -> oversold


def test_adx_range():
    bars = _ohlc([100 + (i % 6) * 1.5 for i in range(60)])  # trend-ish
    d = indicators.adx(bars, 14)
    vals = [v for v in d if v is not None]
    assert len(vals) > 0
    assert all(0 <= v <= 100 for v in vals)
    # strong rally -> directional, high ADX
    trend = _ohlc([100 + i * 1.0 for i in range(60)])
    d2 = indicators.adx(trend, 14)
    vals2 = [v for v in d2 if v is not None]
    assert vals2[-1] > 20


# ---------------------------------------------------------------- engine
RSI_STRAT = {
    "id": "t", "name": "Test", "symbol": "X", "interval": "5m",
    "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
    "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 55},
    "tp_pct": 2.0, "sl_pct": 30.0,  # wide SL: signal fires mid-dip, dip must finish before fills
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
    assert result["exitSplit"].get("TP1", 0) + result["exitSplit"].get("SL", 0) == result["trades"]


def test_backtest_metrics_present():
    strategy = dict(RSI_STRAT)
    result = backtest(strategy, _bars(_rsi_cross_series() + _rsi_cross_series()))
    assert "sharpe" in result and "profitFactor" in result
    assert "avgWinPct" in result and "avgLossPct" in result
    assert result["profitFactor"] >= 0
    assert result["sharpe"] >= -100  # sane bound


MULTI_TP_STRAT = {
    "id": "mtp", "name": "MultiTP", "symbol": "X", "interval": "5m",
    "entry": {"indicator": "SMA", "period": 5, "condition": "crosses_above", "value": "self"},
    "exit": {"indicator": "SMA", "period": 5, "condition": "crosses_below", "value": "self"},
    "tp_levels": [1.0, 3.0], "sl_pct": 2.0,
}


def test_backtest_multi_tp_partial_fills():
    """A steady rally after entry should hit TP1 then TP2 - two exits per trade."""
    prices = [100] * 25
    for i in range(40):
        prices.append(100 + (i + 1) * 0.75)  # +30% drift, never retraces to SL
    bars = _bars(prices)
    result = backtest(MULTI_TP_STRAT, bars)
    assert result["trades"] == 2
    assert result["exitSplit"].get("TP1") == 1
    assert result["exitSplit"].get("TP2") == 1


def test_backtest_sl_closes_remaining_after_tp1():
    """TP1 hits (half closed), then SL takes the rest - two exits, one loss."""
    prices = [100] * 40
    for i in range(2):
        prices.append(100 + (i + 1) * 1.1)  # rally to ~+2.2% > TP1 (+1%), below TP2 (+3%)
    last = prices[-1]
    for i in range(20):
        prices.append(last - (i + 1) * 2.0)  # steady decline through SL (no gap)
    bars = _bars(prices)
    result = backtest(MULTI_TP_STRAT, bars)
    assert result["exitSplit"].get("TP1") == 1
    assert result["exitSplit"].get("SL") == 1
    assert result["trades"] == 2


def test_backtest_insufficient_data():
    result = backtest(RSI_STRAT, _bars([100] * 10))
    assert "error" in result


# ------------------------------------------------- two-series conditions
GOLDEN_CROSS = {
    "id": "gc", "name": "GoldenCross", "symbol": "X", "interval": "5m",
    "entry": {"indicator": "SMA", "period": 20, "condition": "crosses_above",
              "value": {"indicator": "SMA", "period": 50}},
    "exit": {"indicator": "SMA", "period": 20, "condition": "crosses_below",
             "value": {"indicator": "SMA", "period": 50}},
    "tp_pct": 2.0, "sl_pct": 1.0,
}


def test_golden_cross_two_series_condition():
    """SMA20 must cross above SMA50: flat then a strong rally."""
    prices = [100] * 70
    for i in range(30):
        prices.append(100 + (i + 1) * 2.0)
    runner = StrategyRunner(GOLDEN_CROSS)
    bars = _bars(prices)
    fired = [runner.on_bar(bars[: i + 1]) for i in range(1, len(bars))]
    signals = [s for s in fired if s is not None]
    assert len(signals) == 1
    assert signals[0].side == "buy"


# ------------------------------------------------------------ paper broker
def test_paper_broker_multi_tp_partial_closes():
    from app.broker.paper import PaperBroker

    broker = PaperBroker()
    broker.place_bracket("X", "buy", 100, 100.0, 102.0, 99.0, targets=[101.0, 103.0])
    p = broker._positions[0]
    assert p["targets"] == [101.0, 103.0]
    assert p["remaining_qty"] == 100

    closed = broker.on_bar("X", {"high": 101.5, "low": 99.5, "close": 101.0})
    assert len(closed) == 1
    assert closed[0]["exit"] == "TP1"
    assert closed[0]["status"] == "open"  # partial — still open
    p = broker._positions[0]
    assert p["remaining_qty"] == 50
    assert p["targets"] == [103.0]

    closed = broker.on_bar("X", {"high": 104.0, "low": 102.5, "close": 103.5})
    assert len(closed) == 1
    assert closed[0]["exit"] == "TP2"
    assert closed[0]["status"] == "closed"
    assert broker._positions[0]["status"] == "closed"


def test_paper_broker_sl_closes_remaining():
    from app.broker.paper import PaperBroker

    broker = PaperBroker()
    broker.place_bracket("X", "buy", 100, 100.0, 102.0, 99.0, targets=[101.0, 103.0])
    broker.on_bar("X", {"high": 101.5, "low": 99.5, "close": 101.0})  # TP1, half remains
    closed = broker.on_bar("X", {"high": 100.0, "low": 98.5, "close": 99.0})  # SL takes rest
    assert len(closed) == 1
    assert closed[0]["exit"] == "SL"
    assert closed[0]["status"] == "closed"