"""Pure-python indicators. Kept dependency-free so the whole pilot runs on
FastAPI + uvicorn alone.

All functions take a list of bar dicts (oldest first) with keys
open/high/low/close/volume/ts and return a list aligned to the input
(None for windows that aren't computable yet).
"""

from __future__ import annotations

from typing import List, Optional


def closes(bars: List[dict]) -> List[float]:
    return [b["close"] for b in bars]


def sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (period + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def crossed_below(series: List[Optional[float]], index: int, level: float) -> bool:
    """True when series crosses below `level` at `index`."""
    if index < 1 or series[index] is None or series[index - 1] is None:
        return False
    return series[index - 1] >= level and series[index] < level


def crossed_above(series: List[Optional[float]], index: int, level: float) -> bool:
    """True when series crosses above `level` at `index`."""
    if index < 1 or series[index] is None or series[index - 1] is None:
        return False
    return series[index - 1] <= level and series[index] > level