"""Pure-python indicators. Kept dependency-free so the whole pilot runs on
FastAPI + uvicorn alone.

All functions take a list of bar dicts (oldest first) with keys
open/high/low/close/volume/ts and return a list aligned to the input
(None for windows that aren't computable yet).
"""

from __future__ import annotations

import math


def _require_finite(values: list[float], name: str) -> None:
    for i, v in enumerate(values):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"{name}: non-finite input at index {i}")


def _require_bar_finite(bars: list[dict], name: str) -> None:
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close", "volume"):
            v = b.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                raise ValueError(f"{name}: non-finite {key} at bar {i}")


def closes(bars: list[dict]) -> list[float]:
    return [b["close"] for b in bars]


def sma(values: list[float], period: int) -> list[float | None]:
    _require_finite(values, "sma")
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    _require_finite(values, "ema")
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (period + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    _require_finite(values, "rsi")
    out: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi_from_avgs(avg_gain, avg_loss)
    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    """Wilder RSI from average gain/loss. Flat series (both zero) is
    neutral 50.0; zero average loss alone is 100.0 (no downside)."""
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def crossed_below(series: list[float | None], index: int, level: float) -> bool:
    """True when series crosses below `level` at `index`."""
    if index < 1 or series[index] is None or series[index - 1] is None:
        return False
    return series[index - 1] >= level and series[index] < level


def crossed_above(series: list[float | None], index: int, level: float) -> bool:
    """True when series crosses above `level` at `index`."""
    if index < 1 or series[index] is None or series[index - 1] is None:
        return False
    return series[index - 1] <= level and series[index] > level


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> list[float | None]:
    """MACD histogram: macd_line - signal_line (crosses of the histogram
    through 0 are the classic buy/sell triggers)."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]
    valid = [v for v in macd_line if v is not None]
    if not valid:
        return [None] * len(values)
    signal_line = ema(valid, signal)
    out: list[float | None] = [None] * len(values)
    offset = len(values) - len(signal_line)
    for j, v in enumerate(signal_line):
        if v is not None:
            out[offset + j] = macd_line[offset + j] - v
    return out


def bollinger(
    values: list[float], period: int = 20, mult: float = 2.0
) -> dict[str, list[float | None]]:
    """Bollinger bands. Returns {"upper": [...], "mid": [...], "lower": [...]}."""
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = mid[i]
        var = sum((v - mean) ** 2 for v in window) / period
        sd = var**0.5
        upper[i] = mean + mult * sd
        lower[i] = mean - mult * sd
    return {"upper": upper, "mid": mid, "lower": lower}


def donchian(values: list[float], period: int = 20) -> dict[str, list[float | None]]:
    """Donchian channels: rolling high/low over `period` bars (turtle system)."""
    hi: list[float | None] = [None] * len(values)
    lo: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        hi[i] = max(window)
        lo[i] = min(window)
    return {"high": hi, "low": lo}


def vwap(bars: list[dict], period: int = 20) -> list[float | None]:
    _require_bar_finite(bars, "vwap")
    """Rolling volume-weighted average price (typical price weighted)."""
    out: list[float | None] = [None] * len(bars)
    cum_pv = 0.0
    cum_v = 0.0
    for i, b in enumerate(bars):
        tp = (b["high"] + b["low"] + b["close"]) / 3.0
        v = b.get("volume", 1.0) or 1.0
        cum_pv += tp * v
        cum_v += v
        if i >= period:
            prev = bars[i - period]
            tp0 = (prev["high"] + prev["low"] + prev["close"]) / 3.0
            v0 = prev.get("volume", 1.0) or 1.0
            cum_pv -= tp0 * v0
            cum_v -= v0
        if i >= period - 1:
            out[i] = cum_pv / cum_v
    return out


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    out: list[float] = []
    if not values:
        return out
    prev = values[0]
    out.append(prev)
    for v in values[1:]:
        prev = (prev * (period - 1) + v) / period
        out.append(prev)
    return out


def atr(bars: list[dict], period: int = 14) -> list[float | None]:
    _require_bar_finite(bars, "atr")
    """Average True Range (Wilder). Classic volatility measure."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    trs: list[float] = []
    for i in range(1, n):
        h, lo, c_prev = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - lo, abs(h - c_prev), abs(lo - c_prev)))
    smoothed = _wilder_smooth(trs, period)
    for j, s in enumerate(smoothed):
        out[j + 1] = s
    return out


def stochastic(bars: list[dict], period: int = 14, smooth_k: int = 3) -> list[float | None]:
    _require_bar_finite(bars, "stochastic")
    """Stochastic %K (fast, smoothed). Values 0-100; <20 oversold, >80 overbought."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period + smooth_k:
        return out
    raw: list[float] = []
    for i in range(period - 1, n):
        window = bars[i - period + 1 : i + 1]
        hi = max(b["high"] for b in window)
        lo = min(b["low"] for b in window)
        raw.append((bars[i]["close"] - lo) / (hi - lo) * 100 if hi > lo else 50.0)
    for j in range(len(raw) - smooth_k + 1):
        out[period - 1 + j + smooth_k - 1] = sum(raw[j : j + smooth_k]) / smooth_k
    return out


def adx(bars: list[dict], period: int = 14) -> list[float | None]:
    _require_bar_finite(bars, "adx")
    """Average Directional Index (Wilder). >25 = trend, <20 = range."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period * 2:
        return out
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, n):
        h, lo, pc, ph, pl = (
            bars[i]["high"],
            bars[i]["low"],
            bars[i - 1]["close"],
            bars[i - 1]["high"],
            bars[i - 1]["low"],
        )
        up = h - ph
        down = pl - lo
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr_s = _wilder_smooth(trs, period)
    pdi_s = _wilder_smooth(plus_dm, period)
    mdi_s = _wilder_smooth(minus_dm, period)
    for i in range(period, n):
        atr_v = atr_s[i - 1]
        if atr_v <= 0:
            continue
        pdi = pdi_s[i - 1] / atr_v * 100
        mdi = mdi_s[i - 1] / atr_v * 100
        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0.0
        out[i] = dx
    # smooth DX into ADX (Wilder rolling)
    adx_vals = [v for v in out if v is not None]
    if len(adx_vals) >= period:
        smoothed = _wilder_smooth(adx_vals, period)
        idx = 0
        for i in range(n):
            if out[i] is not None:
                out[i] = smoothed[idx]
                idx += 1
    return out
