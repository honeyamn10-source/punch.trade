"""Canonical market data model.

One Candle representation everywhere (live feeds, backtests, chart API).
Rules:

- All timestamps are Unix epoch seconds (UTC). Presentation layers may
  convert to exchange/user timezones — internals never use naive
  datetimes.
- Timeframes use one canonical format: 1m 3m 5m 15m 30m 1h 4h 1d.
- Strategies run on CLOSED candles only (see Candle.closed / finality).
- Corrupted candles are rejected or quarantined, never silently used.
"""

from __future__ import annotations

import dataclasses
import math
import time

TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d")

# seconds per timeframe (approximation used for staleness thresholds)
TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# strategy evaluation default: closed candles only unless the strategy
# declares intrabar_capable = true
INTRABAR_CAPABLE_DEFAULT = False


class MarketDataError(ValueError):
    """Raised for malformed/corrupted candle data."""


def normalize_timeframe(tf: str) -> str:
    """Map provider-specific timeframe labels onto the canonical set."""
    t = str(tf).strip().lower()
    aliases = {
        "1": "1m",
        "1m": "1m",
        "m1": "1m",
        "minute": "1m",
        "3": "3m",
        "3m": "3m",
        "m3": "3m",
        "5": "5m",
        "5m": "5m",
        "m5": "5m",
        "5min": "5m",
        "15": "15m",
        "15m": "15m",
        "m15": "15m",
        "30": "30m",
        "30m": "30m",
        "m30": "30m",
        "1h": "1h",
        "h1": "1h",
        "60m": "1h",
        "60": "1h",
        "4h": "4h",
        "h4": "4h",
        "240m": "4h",
        "1d": "1d",
        "d": "1d",
        "1D": "1d",
        "day": "1d",
    }
    tf = aliases.get(t)
    if tf is None:
        raise MarketDataError(f"unsupported timeframe '{tf}' — use one of {TIMEFRAMES}")
    return tf


def _finite(*values: float) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    )


@dataclasses.dataclass(frozen=True)
class Candle:
    """Immutable canonical candle. timestamps are Unix epoch seconds (UTC)."""

    symbol: str
    timeframe: str
    open_time: float
    close_time: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True
    source: str = "unknown"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Candle:
        return cls(
            symbol=str(d.get("symbol", "")),
            timeframe=normalize_timeframe(d.get("timeframe", "5m")),
            open_time=float(d.get("open_time", d.get("ts", 0))),
            close_time=float(d.get("close_time", d.get("ts", d.get("open_time", 0)))),
            open=float(d.get("open")),
            high=float(d.get("high")),
            low=float(d.get("low")),
            close=float(d.get("close")),
            volume=float(d.get("volume", 0.0)),
            closed=bool(d.get("closed", True)),
            source=str(d.get("source", "unknown")),
        )

    @classmethod
    def from_legacy_bar(
        cls, symbol: str, bar: dict, timeframe: str = "5m", source: str = "legacy"
    ) -> Candle:
        """Wrap the old {ts,open,high,low,close,volume} dict format.

        ``ts`` is the candle's CLOSE timestamp (provider convention), so the
        open time is derived as close - timeframe interval. This keeps legacy
        bars valid under validate_candle (close_time > open_time).
        """
        tf = normalize_timeframe(timeframe)
        ts = float(bar.get("ts", 0))
        return cls(
            symbol=symbol,
            timeframe=tf,
            open_time=ts - TIMEFRAME_SECONDS[tf],
            close_time=ts,
            closed=True,
            source=source,
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            volume=float(bar.get("volume", 0.0)),
        )

    def validate(self) -> Candle:
        validate_candle(self)
        return self


def validate_candle(c: Candle) -> None:
    """Raise MarketDataError on any corrupted field."""
    if not c.symbol:
        raise MarketDataError("candle missing symbol")
    try:
        normalize_timeframe(c.timeframe)
    except MarketDataError:
        raise
    if not _finite(c.open, c.high, c.low, c.close, c.volume, c.open_time, c.close_time):
        raise MarketDataError(f"non-finite candle values for {c.symbol}")
    if c.volume < 0:
        raise MarketDataError(f"negative volume for {c.symbol}")
    if c.close_time <= c.open_time:
        raise MarketDataError(f"close_time <= open_time for {c.symbol}")
    if c.high < c.open or c.high < c.close or c.high < c.low:
        raise MarketDataError(
            f"high < open/close/low for {c.symbol} ({c.high} vs {c.open}/{c.close}/{c.low})"
        )
    if c.low > c.open or c.low > c.close:
        raise MarketDataError(f"low > open/close for {c.symbol}")


class DataQualityTracker:
    """Tracks per-symbol data quality while bars stream in."""

    def __init__(self):
        self.bars: dict[str, int] = {}
        self.missing: dict[str, int] = {}
        self.duplicates: dict[str, int] = {}
        self.out_of_order: dict[str, int] = {}
        self.invalid: dict[str, int] = {}
        self._last_ts: dict[str, float] = {}

    def observe(self, c: Candle) -> None:
        sym = c.symbol
        self.bars[sym] = self.bars.get(sym, 0) + 1
        last = self._last_ts.get(sym)
        if last is not None:
            if c.open_time < last:
                self.out_of_order[sym] = self.out_of_order.get(sym, 0) + 1
            elif c.open_time == last:
                self.duplicates[sym] = self.duplicates.get(sym, 0) + 1
        self._last_ts[sym] = c.open_time

    def mark_invalid(self, symbol: str) -> None:
        self.invalid[symbol] = self.invalid.get(symbol, 0) + 1

    def status(self, symbol: str) -> str:
        """GOOD / WARNING / BAD / UNKNOWN."""
        if symbol not in self.bars:
            return "UNKNOWN"
        bad = self.invalid.get(symbol, 0) > 0 or self.out_of_order.get(symbol, 0) > 0
        warn = self.duplicates.get(symbol, 0) > 0 or self.missing.get(symbol, 0) > 0
        if bad:
            return "BAD"
        if warn:
            return "WARNING"
        return "GOOD"

    def summary(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "status": self.status(symbol),
            "bars": self.bars.get(symbol, 0),
            "missing": self.missing.get(symbol, 0),
            "duplicates": self.duplicates.get(symbol, 0),
            "outOfOrder": self.out_of_order.get(symbol, 0),
            "invalid": self.invalid.get(symbol, 0),
        }


def is_fresh(last_ts: float, now: float | None = None, stale_after: float = 60.0) -> bool:
    """True when a feed timestamp is within the staleness window."""
    now = now if now is not None else time.time()
    return bool(last_ts) and (now - last_ts) <= stale_after


# ------------------------------------------------------------ regime ----
REGIMES = (
    "TRENDING_HIGH_VOL",
    "TRENDING_LOW_VOL",
    "RANGING_HIGH_VOL",
    "RANGING_LOW_VOL",
    "UNKNOWN",
)

# documented thresholds (5m-bar scale; tune per timeframe if ever needed)
TREND_MIN_SLOPE = 0.0005  # |SMA20 slope per bar| above this = trending
VOL_MIN_ATR_PCT = 0.004  # ATR14/close above this = high vol


def regime_of(
    bars: list[dict], sma_period: int = 20, atr_period: int = 14, lookback: int = 10
) -> str:
    """Deterministic regime classifier.

    Formulas:
        slope = (SMA20[i] - SMA20[i-lookback]) / SMA20[i-lookback] / lookback
        atr_pct = ATR14[i] / close[i]
    trend  = |slope| >= TREND_MIN_SLOPE
    vol    = atr_pct >= VOL_MIN_ATR_PCT
    < 60 bars of data -> UNKNOWN
    """
    from . import indicators as ind

    n = len(bars)
    if n < 60 or n < lookback + sma_period + atr_period + 1:
        return "UNKNOWN"
    closes = ind.closes(bars)
    sma = ind.sma(closes, sma_period)
    atr = ind.atr(bars, atr_period)
    if sma[-1] is None or sma[-1 - lookback] is None or atr[-1] is None:
        return "UNKNOWN"
    base = sma[-1 - lookback]
    slope = ((sma[-1] - base) / base) / lookback if base else 0.0
    atr_pct = atr[-1] / closes[-1] if closes[-1] else 0.0
    trend = abs(slope) >= TREND_MIN_SLOPE
    vol = atr_pct >= VOL_MIN_ATR_PCT
    if trend and vol:
        return "TRENDING_HIGH_VOL"
    if trend:
        return "TRENDING_LOW_VOL"
    if vol:
        return "RANGING_HIGH_VOL"
    return "RANGING_LOW_VOL"
