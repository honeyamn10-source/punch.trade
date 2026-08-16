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
        "description": "Buy when close crosses above SMA(50), exit when it crosses back below.",
        "entry": {"indicator": "SMA", "period": 50, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "SMA", "period": 50, "condition": "crosses_below", "value": "self"},
        "tp_pct": 2.5,
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
]


def get_strategy(strategy_id: str) -> Optional[Dict]:
    for s in STRATEGIES:
        if s["id"] == strategy_id:
            return s
    return None


def compute_indicator(indicator: str, period: int, bars: List[dict]) -> List[Optional[float]]:
    """Evaluate the indicator library against a bar series."""
    from . import indicators

    values = indicators.closes(bars)
    if indicator == "SMA":
        return indicators.sma(values, period)
    if indicator == "EMA":
        return indicators.ema(values, period)
    if indicator == "RSI":
        return indicators.rsi(values, period)
    raise ValueError(f"Unknown indicator: {indicator}")


def condition_met(condition: Dict, series: List[Optional[float]], index: int,
                  closes_series: Optional[List[float]] = None) -> bool:
    """Check a declarative condition at `index`.

    `value == "self"` compares the indicator series to the close series
    (e.g. close crossing its EMA). Otherwise the level is a fixed number.
    """
    from . import indicators

    level = condition["value"]
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