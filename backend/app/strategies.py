"""Declarative strategy configs.

Strategies are plain dicts referencing the fixed indicator/condition
library in indicators.py. This is the "safe" marketplace representation
from the design: no arbitrary code execution, nothing a contributor can
do except pick indicators and levels.
"""

from __future__ import annotations

from typing import Dict, List, Optional

STRATEGIES: List[Dict] = [
    {
        "id": "rsi-reversal",
        "name": "RSI Reversal",
        "symbol": "RELIANCE",
        "interval": "5m",
        "description": "Buy when RSI(14) crosses below 30, exit when it crosses above 50.",
        "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
        "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 50},
        "tp_pct": 2.0,
        "sl_pct": 1.0,
    },
    {
        "id": "ema-breakout",
        "name": "EMA Breakout",
        "symbol": "TCS",
        "interval": "5m",
        "description": "Buy when close crosses above EMA(20), exit when it crosses back below.",
        "entry": {"indicator": "EMA", "period": 20, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "EMA", "period": 20, "condition": "crosses_below", "value": "self"},
        "tp_pct": 1.5,
        "sl_pct": 0.8,
    },
    {
        "id": "sma-bounce",
        "name": "SMA Bounce",
        "symbol": "HDFCBANK",
        "interval": "5m",
        "description": "Buy when close crosses above SMA(50), exit when it crosses back below. Multi-TP: 50% at +1.5%, rest at +3%.",
        "entry": {"indicator": "SMA", "period": 50, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "SMA", "period": 50, "condition": "crosses_below", "value": "self"},
        "tp_levels": [1.5, 3.0],
        "sl_pct": 1.2,
    },
    {
        "id": "btc-rsi",
        "name": "BTC RSI Dip",
        "symbol": "BTC/USDT",
        "interval": "5m",
        "description": "Buy BTC when RSI(14) crosses below 30, exit when it crosses above 55.",
        "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
        "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 55},
        "tp_pct": 1.5,
        "sl_pct": 0.8,
    },
    {
        "id": "macd-momentum",
        "name": "MACD Momentum",
        "symbol": "RELIANCE",
        "interval": "5m",
        "description": "Classic MACD(12,26,9) histogram crossing zero = momentum shift. Long on the zero-cross.",
        "entry": {"indicator": "MACD", "period": 0, "condition": "crosses_above", "value": 0},
        "exit": {"indicator": "MACD", "period": 0, "condition": "crosses_below", "value": 0},
        "tp_levels": [1.2, 2.4],
        "sl_pct": 1.0,
    },
    {
        "id": "bb-reversion",
        "name": "BB Mean Reversion",
        "symbol": "INFY",
        "interval": "5m",
        "description": "Fade the overshoot: buy when close reclaims the lower Bollinger band, exit at the middle band.",
        "entry": {"indicator": "BB_LOWER", "period": 20, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "BB_MID", "period": 20, "condition": "crosses_below", "value": "self"},
        "tp_levels": [1.0, 2.0],
        "sl_pct": 1.0,
    },
    {
        "id": "donchian-breakout",
        "name": "Donchian Breakout",
        "symbol": "TCS",
        "interval": "5m",
        "description": "The turtle system: buy a 20-bar high breakout, exit on a 10-bar low breakdown.",
        "entry": {"indicator": "DONCH_HIGH", "period": 20, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "DONCH_LOW", "period": 10, "condition": "crosses_below", "value": "self"},
        "tp_levels": [1.5, 3.0],
        "sl_pct": 1.2,
    },
    {
        "id": "vwap-reversion",
        "name": "VWAP Reversion",
        "symbol": "HDFCBANK",
        "interval": "5m",
        "description": "Buy the dip below rolling VWAP(20), exit when price reclaims it.",
        "entry": {"indicator": "VWAP", "period": 20, "condition": "crosses_below", "value": "self"},
        "exit": {"indicator": "VWAP", "period": 20, "condition": "crosses_above", "value": "self"},
        "tp_levels": [0.8, 1.6],
        "sl_pct": 0.9,
    },
    {
        "id": "golden-cross",
        "name": "Golden Cross",
        "symbol": "BTC/USDT",
        "interval": "5m",
        "description": "The classic trend filter: SMA(20) crossing above SMA(50). Exit on the death cross.",
        "entry": {"indicator": "SMA", "period": 20, "condition": "crosses_above", "value": {"indicator": "SMA", "period": 50}},
        "exit": {"indicator": "SMA", "period": 20, "condition": "crosses_below", "value": {"indicator": "SMA", "period": 50}},
        "tp_levels": [2.0, 4.0],
        "sl_pct": 1.5,
    },
]


def get_strategy(strategy_id: str) -> Optional[Dict]:
    for s in STRATEGIES:
        if s["id"] == strategy_id:
            return s
    return None


def target_levels(strategy: Dict) -> List[float]:
    """Multi-level take-profit percentages. Defaults to the single tp_pct."""
    levels = strategy.get("tp_levels")
    if isinstance(levels, list) and levels:
        return [float(x) for x in levels]
    return [float(strategy.get("tp_pct", 2.0))]


def compute_indicator(indicator: str, period: int, bars: List[dict]) -> List[Optional[float]]:
    """Evaluate the indicator library against a bar series.

    Composite indicators (MACD hist, BB bands, Donchian channels, VWAP)
    return the single series the declarative conditions can test against.
    """
    from . import indicators

    values = indicators.closes(bars)
    if indicator == "SMA":
        return indicators.sma(values, period)
    if indicator == "EMA":
        return indicators.ema(values, period)
    if indicator == "RSI":
        return indicators.rsi(values, period)
    if indicator == "MACD":
        return indicators.macd(values)
    if indicator == "BB_UPPER":
        return indicators.bollinger(values, period)["upper"]
    if indicator == "BB_MID":
        return indicators.bollinger(values, period)["mid"]
    if indicator == "BB_LOWER":
        return indicators.bollinger(values, period)["lower"]
    if indicator == "DONCH_HIGH":
        return indicators.donchian(values, period)["high"]
    if indicator == "DONCH_LOW":
        return indicators.donchian(values, period)["low"]
    if indicator == "VWAP":
        return indicators.vwap(bars, period)
    raise ValueError(f"Unknown indicator: {indicator}")


def condition_met(condition: Dict, series: List[Optional[float]], index: int,
                  closes_series: Optional[List[float]] = None,
                  bars: Optional[List[dict]] = None) -> bool:
    """Check a declarative condition at `index`.

    `value == "self"` compares the indicator series to the close series
    (e.g. close crossing its EMA). A dict value computes a second
    indicator series and tests a cross between the two (e.g. SMA20
    crossing SMA50 — golden cross). Otherwise the level is a fixed number.
    """
    from . import indicators

    level = condition["value"]
    if isinstance(level, dict):
        other = compute_indicator(level["indicator"], level["period"], bars or [])
        if len(other) <= index or any(
                v is None for v in (series[index - 1], series[index],
                                    other[index - 1], other[index])):
            return False
        if condition["condition"] == "crosses_above":
            return series[index - 1] <= other[index - 1] and series[index] > other[index]
        if condition["condition"] == "crosses_below":
            return series[index - 1] >= other[index - 1] and series[index] < other[index]
        raise ValueError(f"Unknown condition: {condition['condition']}")
    if level == "self":
        if closes_series is None:
            return False
        ind_prev, ind_cur = series[index - 1], series[index]
        c_prev, c_cur = closes_series[index - 1], closes_series[index]
        if None in (ind_prev, ind_cur):
            return False
        if condition["condition"] == "crosses_above":
            return c_prev <= ind_prev and c_cur > ind_cur
        if condition["condition"] == "crosses_below":
            return c_prev >= ind_prev and c_cur < ind_cur
        raise ValueError(f"Unknown condition: {condition['condition']}")
    if condition["condition"] == "crosses_below":
        return indicators.crossed_below(series, index, level)
    if condition["condition"] == "crosses_above":
        return indicators.crossed_above(series, index, level)
    raise ValueError(f"Unknown condition: {condition['condition']}")