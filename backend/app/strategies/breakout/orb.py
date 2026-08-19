"""Opening Range Breakout variant for liquid intraday markets."""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dt_time

import numpy as np

from ..base import ParameterSpec, Signal, SignalDirection, Timeframe, register_strategy
from ..indicators import atr, closes, percentile_rank
from .volatility_breakout import VolatilityBreakout


@register_strategy
class OpeningRangeBreakout(VolatilityBreakout):
    """Opening Range Breakout for liquid intraday markets.

    Research opening windows: 5m, 15m, 30m, 60m
    Requires: session calendar, liquidity, relative volume, spread sanity
    """

    strategy_id = "punch_opening_range_breakout"
    version = "1.0.0"
    name = "PUNCH Opening Range Breakout"
    description = (
        "Breakout of the opening range (first N minutes of session) "
        "with volume and volatility confirmation."
    )

    supported_timeframes = [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1]

    warmup_bars = 50

    parameter_schema = [
        ParameterSpec("opening_minutes", int, 30, "Opening range duration in minutes", 5, 120),
        ParameterSpec("session_start", str, "09:30", "Session start time (HH:MM)", None, None),
        ParameterSpec("session_end", str, "16:00", "Session end time (HH:MM)", None, None),
        ParameterSpec(
            "volume_surge_pct", float, 75.0, "Volume surge percentile for confirmation", 50, 95
        ),
        ParameterSpec("atr_period", int, 14, "ATR period for stop", 10, 30),
        ParameterSpec("atr_stop_mult", float, 1.5, "ATR trailing stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short ORB", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._opening_high: float | None = None
        self._opening_low: float | None = None
        self._opening_volume: float | None = None
        self._current_session_date = None

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        # Extract parameters
        opening_min = self.params["opening_minutes"]
        session_start = self._parse_time(self.params["session_start"])
        session_end = self._parse_time(self.params["session_end"])
        vol_surge_pct = self.params["volume_surge_pct"]
        atr_p = self.params["atr_period"]
        self.params["atr_stop_mult"]
        use_short = self.params["use_shorting"]

        # Get current bar timestamp
        ts = bars[current_idx].get("ts", 0)
        dt = datetime.fromtimestamp(ts)
        current_time = dt.time()
        current_date = dt.date()

        # Reset opening range at new session
        if self._current_session_date != current_date:
            self._opening_high = None
            self._opening_low = None
            self._opening_volume = None
            self._current_session_date = current_date

        # Check if within opening range window
        session_start_dt = datetime.combine(current_date, session_start)
        opening_end_dt = session_start_dt + timedelta(minutes=opening_min)

        in_opening_range = session_start <= current_time <= opening_end_dt.time()
        after_opening = current_time > opening_end_dt.time()
        in_session = session_start <= current_time <= session_end

        bars_up_to = bars[: current_idx + 1]
        c_up_to = closes(bars_up_to)

        if in_opening_range:
            # Build opening range
            high = bars[current_idx].get("high", np.nan)
            low = bars[current_idx].get("low", np.nan)
            vol = bars[current_idx].get("volume", 0)

            if self._opening_high is None or high > self._opening_high:
                self._opening_high = high
            if self._opening_low is None or low < self._opening_low:
                self._opening_low = low
            if self._opening_volume is None:
                self._opening_volume = vol
            else:
                self._opening_volume += vol

            return None

        # After opening range, wait for breakout
        if after_opening and in_session:
            if self._opening_high is None or self._opening_low is None:
                return None

            # Compute volume surge
            volumes = np.array([b.get("volume", 1.0) for b in bars[: current_idx + 1]])
            vol_pct = percentile_rank(volumes, 252)

            # ATR for stop
            atr_vals = atr(bars[: current_idx + 1], atr_p)
            atr_val = atr_vals[current_idx] if current_idx < len(atr_vals) else np.nan

            price = c_up_to[current_idx]
            vol_ok = vol_pct[current_idx] >= vol_surge_pct if current_idx < len(vol_pct) else False

            # Breakout signals
            if price > self._opening_high and vol_ok:
                stop_loss = price - self.params["atr_stop_mult"] * (
                    atr_val if not np.isnan(atr_val) else price * 0.02
                )
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[current_idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.LONG,
                    timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                    price=price,
                    confidence=0.8,
                    stop_loss=stop_loss,
                    metadata={
                        "opening_high": self._opening_high,
                        "opening_low": self._opening_low,
                        "opening_volume": self._opening_volume,
                        "vol_pct": float(vol_pct[current_idx])
                        if current_idx < len(vol_pct)
                        else None,
                    },
                )

            if use_short and price < self._opening_low and vol_ok:
                stop_loss = price + self.params["atr_stop_mult"] * (
                    atr_val if not np.isnan(atr_val) else price * 0.02
                )
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[current_idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.SHORT,
                    timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                    price=price,
                    confidence=0.8,
                    stop_loss=stop_loss,
                    metadata={
                        "opening_high": self._opening_high,
                        "opening_low": self._opening_low,
                        "opening_volume": self._opening_volume,
                        "vol_pct": float(vol_pct[current_idx])
                        if current_idx < len(vol_pct)
                        else None,
                    },
                )

        return None

    def _parse_time(self, time_str: str) -> dt_time:
        parts = time_str.split(":")
        return dt_time(int(parts[0]), int(parts[1]))

    def _sma(self, values: np.ndarray, period: int) -> np.ndarray:
        # Inherited from parent but needed for compilation
        if period <= 1:
            return values.copy()
        out = np.full_like(values, np.nan)
        n = len(values)
        for i in range(period - 1, n):
            window = values[i - period + 1 : i + 1]
            if not np.any(np.isnan(window)):
                out[i] = np.mean(window)
        return out

    def _bollinger(self, values: np.ndarray, period: int, std_mult: float) -> dict[str, np.ndarray]:
        # Not used in ORB but required by parent
        mid = self._sma(values, period)
        std = np.full_like(values, np.nan)
        for i in range(period - 1, len(values)):
            window = values[i - period + 1 : i + 1]
            if not np.any(np.isnan(window)):
                std[i] = np.std(window)
        upper = mid + std_mult * std
        lower = mid - std_mult * std
        return {
            "upper": upper,
            "mid": mid,
            "lower": lower,
            "std": std,
            "bandwidth": (upper - lower) / np.where(mid == 0, 1, mid),
        }
