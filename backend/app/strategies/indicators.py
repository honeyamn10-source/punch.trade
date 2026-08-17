"""Technical indicators for strategy computations.

All functions operate on bar lists and return arrays aligned with input.
NaN values used for warmup periods.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def closes(bars: list[dict]) -> np.ndarray:
    """Extract close prices as float array."""
    return np.array([b.get("close", np.nan) for b in bars], dtype=float)


def highs(bars: list[dict]) -> np.ndarray:
    return np.array([b.get("high", np.nan) for b in bars], dtype=float)


def lows(bars: list[dict]) -> np.ndarray:
    return np.array([b.get("low", np.nan) for b in bars], dtype=float)


def volumes(bars: list[dict]) -> np.ndarray:
    return np.array([b.get("volume", np.nan) for b in bars], dtype=float)


def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    if period <= 1:
        return values.copy()
    out = np.full_like(values, np.nan)
    n = len(values)
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        if not np.any(np.isnan(window)):
            out[i] = np.mean(window)
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    if period <= 1:
        return values.copy()
    alpha = 2.0 / (period + 1)
    out = np.full_like(values, np.nan)
    # Find first valid value
    first_valid = np.where(~np.isnan(values))[0]
    if len(first_valid) == 0:
        return out
    start = first_valid[0]
    out[start] = values[start]
    for i in range(start + 1, len(values)):
        if np.isnan(values[i]):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    if period <= 1:
        return np.full_like(values, 50.0)
    deltas = np.diff(values, prepend=np.nan)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.full_like(values, np.nan)
    avg_loss = np.full_like(values, np.nan)

    # Initial SMA
    if len(values) > period:
        avg_gain[period] = np.nanmean(gains[1:period + 1])
        avg_loss[period] = np.nanmean(losses[1:period + 1])
        for i in range(period + 1, len(values)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    return 100 - (100 / (1 + rs))


def atr(bars: list[dict], period: int = 14) -> np.ndarray:
    """Average True Range."""
    h = highs(bars)
    l = lows(bars)
    c = closes(bars)

    prev_c = np.roll(c, 1)
    prev_c[0] = np.nan

    tr1 = h - l
    tr2 = np.abs(h - prev_c)
    tr3 = np.abs(l - prev_c)

    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    return sma(tr, period)


def adx(bars: list[dict], period: int = 14) -> np.ndarray:
    """Average Directional Index."""
    h = highs(bars)
    l = lows(bars)
    c = closes(bars)

    up_move = h - np.roll(h, 1)
    down_move = np.roll(l, 1) - l

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = atr(bars, period=1)  # true range for 1 period

    plus_di = 100 * sma(plus_dm, period) / sma(tr, period)
    minus_di = 100 * sma(minus_dm, period) / sma(tr, period)

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    return sma(dx, period)


def donchian(bars: list[dict], period: int = 20) -> dict[str, np.ndarray]:
    """Donchian Channels."""
    h = highs(bars)
    l = lows(bars)
    return {
        "high": np.full_like(h, np.nan),
        "low": np.full_like(l, np.nan),
        "mid": np.full_like(h, np.nan),
    }


def donchian_high(bars: list[dict], period: int) -> np.ndarray:
    h = highs(bars)
    out = np.full_like(h, np.nan)
    for i in range(period - 1, len(h)):
        out[i] = np.nanmax(h[i - period + 1:i + 1])
    return out


def donchian_low(bars: list[dict], period: int) -> np.ndarray:
    l = lows(bars)
    out = np.full_like(l, np.nan)
    for i in range(period - 1, len(l)):
        out[i] = np.nanmin(l[i - period + 1:i + 1])
    return out


def bollinger(values: np.ndarray, period: int = 20, std_mult: float = 2.0) -> dict[str, np.ndarray]:
    """Bollinger Bands."""
    mid = sma(values, period)
    std = np.full_like(values, np.nan)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if not np.any(np.isnan(window)):
            std[i] = np.std(window)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return {"upper": upper, "mid": mid, "lower": lower, "std": std, "bandwidth": (upper - lower) / np.where(mid == 0, 1, mid)}


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, np.ndarray]:
    """MACD."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def vwap(bars: list[dict], period: int = 20) -> np.ndarray:
    """Volume Weighted Average Price."""
    c = closes(bars)
    v = volumes(bars)
    pv = c * v
    out = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        window_pv = pv[i - period + 1:i + 1]
        window_v = v[i - period + 1:i + 1]
        if np.sum(window_v) > 0 and not np.any(np.isnan(window_pv)):
            out[i] = np.sum(window_pv) / np.sum(window_v)
    return out


def stochastic(bars: list[dict], period: int = 14) -> np.ndarray:
    """Stochastic %K."""
    h = highs(bars)
    l = lows(bars)
    c = closes(bars)

    out = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        hh = np.nanmax(h[i - period + 1:i + 1])
        ll = np.nanmin(l[i - period + 1:i + 1])
        if hh != ll:
            out[i] = 100 * (c[i] - ll) / (hh - ll)
    return out


def crossed_above(series: np.ndarray, level: float | np.ndarray, idx: int) -> bool:
    """Check if series crossed above level at idx."""
    if idx <= 0:
        return False
    if isinstance(level, np.ndarray):
        return series[idx - 1] <= level[idx - 1] and series[idx] > level[idx]
    return series[idx - 1] <= level and series[idx] > level


def crossed_below(series: np.ndarray, level: float | np.ndarray, idx: int) -> bool:
    """Check if series crossed below level at idx."""
    if idx <= 0:
        return False
    if isinstance(level, np.ndarray):
        return series[idx - 1] >= level[idx - 1] and series[idx] < level[idx]
    return series[idx - 1] >= level and series[idx] < level


def slope(values: np.ndarray, period: int = 5) -> np.ndarray:
    """Linear regression slope over period."""
    out = np.full_like(values, np.nan)
    x = np.arange(period)
    for i in range(period - 1, len(values)):
        y = values[i - period + 1:i + 1]
        if not np.any(np.isnan(y)):
            out[i] = np.polyfit(x, y, 1)[0]
    return out


def percentile_rank(values: np.ndarray, lookback: int = 252) -> np.ndarray:
    """Percentile rank of current value vs lookback window."""
    out = np.full_like(values, np.nan)
    start = min(lookback - 1, len(values) - 1)
    for i in range(start, len(values)):
        window_start = max(0, i - lookback + 1)
        window = values[window_start:i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            out[i] = 100 * np.sum(valid <= values[i]) / len(valid)
    return out


def zscore(values: np.ndarray, period: int = 20) -> np.ndarray:
    """Rolling z-score."""
    mean = sma(values, period)
    std = np.full_like(values, np.nan)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if not np.any(np.isnan(window)):
            std[i] = np.std(window)
    return (values - mean) / np.where(std == 0, 1, std)


def normalize(values: np.ndarray, method: str = "zscore", period: int = 20) -> np.ndarray:
    """Normalize values."""
    if method == "zscore":
        return zscore(values, period)
    elif method == "percentile":
        return percentile_rank(values, period) / 100
    elif method == "minmax":
        out = np.full_like(values, np.nan)
        for i in range(period - 1, len(values)):
            window = values[i - period + 1:i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) > 1:
                mn, mx = np.min(valid), np.max(valid)
                if mx != mn:
                    out[i] = (values[i] - mn) / (mx - mn)
        return out
    return values