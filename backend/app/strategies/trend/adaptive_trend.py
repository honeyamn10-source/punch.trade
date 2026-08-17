"""Strategy Family A: Adaptive Multi-Horizon Trend.

Purpose: robust directional trend capture across multiple horizons.

Features:
- Short/medium/long momentum
- EMA slope
- Price vs moving averages
- Donchian breakout position
- ADX confirmation
- Normalized ATR for volatility regime

Architecture:
  short trend
  medium trend
  long trend
       │
       └──> trend breadth (agreement across horizons)
                │
           ADX confirmation
                │
           volatility regime filter
                │
           signal candidate
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from ..base import AssetClass, ParameterSpec, Signal, SignalDirection, Strategy, StrategyFamily, Timeframe, register_strategy
from ..indicators import (
    adx,
    atr,
    closes,
    crossed_above,
    crossed_below,
    donchian_high,
    donchian_low,
    ema,
    normalize,
    percentile_rank,
    slope,
)


@register_strategy
class AdaptiveMultiHorizonTrend(Strategy):
    """Multi-horizon adaptive trend following with regime awareness."""

    strategy_id = "punch_adaptive_trend"
    version = "1.0.0"
    family = StrategyFamily.TREND
    name = "PUNCH Adaptive Multi-Horizon Trend"
    description = (
        "Multi-horizon trend score combining short/medium/long momentum, "
        "EMA slopes, Donchian position, with ADX and volatility regime filters."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FOREX,
        AssetClass.COMMODITY,
    ]
    supported_timeframes = [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1]

    warmup_bars = 100

    parameter_schema = [
        ParameterSpec("short_period", int, 10, "Short momentum lookback", 5, 30),
        ParameterSpec("medium_period", int, 30, "Medium momentum lookback", 20, 60),
        ParameterSpec("long_period", int, 60, "Long momentum lookback", 40, 120),
        ParameterSpec("ema_short", int, 20, "Short EMA period", 10, 50),
        ParameterSpec("ema_medium", int, 50, "Medium EMA period", 30, 100),
        ParameterSpec("ema_long", int, 200, "Long EMA period", 100, 300),
        ParameterSpec("donchian_period", int, 20, "Donchian channel period", 10, 50),
        ParameterSpec("adx_period", int, 14, "ADX period", 10, 30),
        ParameterSpec("adx_threshold", float, 25.0, "ADX trend strength threshold", 15, 40),
        ParameterSpec("atr_period", int, 14, "ATR period for volatility", 10, 30),
        ParameterSpec("vol_percentile_high", float, 80.0, "High volatility percentile", 60, 95),
        ParameterSpec("vol_percentile_low", float, 20.0, "Low volatility percentile", 5, 40),
        ParameterSpec("trend_breadth_threshold", float, 0.5, "Minimum trend breadth for signal", 0.0, 1.0),
        ParameterSpec("weight_short", float, 0.2, "Weight for short horizon", 0.0, 1.0),
        ParameterSpec("weight_medium", float, 0.3, "Weight for medium horizon", 0.0, 1.0),
        ParameterSpec("weight_long", float, 0.5, "Weight for long horizon", 0.0, 1.0),
        ParameterSpec("use_shorting", bool, False, "Allow short signals", None, None),
        ParameterSpec("exit_atr_mult", float, 2.5, "ATR trailing stop multiplier", 1.0, 5.0),
        ParameterSpec("exit_donchian_period", int, 10, "Donchian exit period", 5, 30),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        # Normalize weights
        total = self.params["weight_short"] + self.params["weight_medium"] + self.params["weight_long"]
        if total > 0:
            self.params["weight_short"] /= total
            self.params["weight_medium"] /= total
            self.params["weight_long"] /= total

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        # Extract parameters
        sp = self.params["short_period"]
        mp = self.params["medium_period"]
        lp = self.params["long_period"]
        adx_p = self.params["adx_period"]
        adx_thresh = self.params["adx_threshold"]
        atr_p = self.params["atr_period"]
        vol_high = self.params["vol_percentile_high"]
        vol_low = self.params["vol_percentile_low"]
        dc_p = self.params["donchian_period"]
        breadth_thresh = self.params["trend_breadth_threshold"]
        w_s = self.params["weight_short"]
        w_m = self.params["weight_medium"]
        w_l = self.params["weight_long"]
        use_short = self.params["use_shorting"]

        # Compute all indicators up to current_idx (no lookahead)
        # We need indicator values for the full series up to current_idx
        bars_up_to = bars[:current_idx + 1]
        c_up_to = closes(bars_up_to)

        # Momentum (rate of change)
        mom_short = self._momentum(c_up_to, sp)
        mom_medium = self._momentum(c_up_to, mp)
        mom_long = self._momentum(c_up_to, lp)

        # EMA slopes
        ema_s = ema(c_up_to, self.params["ema_short"])
        ema_m = ema(c_up_to, self.params["ema_medium"])
        ema_l = ema(c_up_to, self.params["ema_long"])
        slope_s = slope(ema_s, 5)
        slope_m = slope(ema_m, 10)
        slope_l = slope(ema_l, 20)

        # Price vs EMA position
        pos_s = self._price_vs_ema(c_up_to, ema_s)
        pos_m = self._price_vs_ema(c_up_to, ema_m)
        pos_l = self._price_vs_ema(c_up_to, ema_l)

        # Donchian position
        dc_high = donchian_high(bars_up_to, dc_p)
        dc_low = donchian_low(bars_up_to, dc_p)
        dc_pos = self._donchian_position(c_up_to, dc_high, dc_low)

        # ADX
        adx_vals = adx(bars_up_to, adx_p)

        # Volatility regime
        atr_vals = atr(bars_up_to, atr_p)
        atr_pct = percentile_rank(atr_vals, 252)

        # Get current values
        idx = len(c_up_to) - 1
        if idx < 0 or idx >= len(c_up_to):
            return None

        # Normalize momentum components to [-1, 1] using recent history
        norm_short = self._normalize_component(mom_short, idx, lookback=100)
        norm_medium = self._normalize_component(mom_medium, idx, lookback=100)
        norm_long = self._normalize_component(mom_long, idx, lookback=100)

        norm_slope_s = self._normalize_component(slope_s, idx, lookback=50)
        norm_slope_m = self._normalize_component(slope_m, idx, lookback=50)
        norm_slope_l = self._normalize_component(slope_l, idx, lookback=50)

        norm_pos_s = self._normalize_component(pos_s, idx, lookback=50)
        norm_pos_m = self._normalize_component(pos_m, idx, lookback=50)
        norm_pos_l = self._normalize_component(pos_l, idx, lookback=50)

        norm_dc = self._normalize_component(dc_pos, idx, lookback=50)

        # Build trend score for each horizon
        trend_short = (
            0.4 * norm_short
            + 0.3 * norm_slope_s
            + 0.2 * norm_pos_s
            + 0.1 * norm_dc
        )
        trend_medium = (
            0.4 * norm_medium
            + 0.3 * norm_slope_m
            + 0.2 * norm_pos_m
            + 0.1 * norm_dc
        )
        trend_long = (
            0.4 * norm_long
            + 0.3 * norm_slope_l
            + 0.2 * norm_pos_l
            + 0.1 * norm_dc
        )

        # Weighted trend breadth (agreement across horizons)
        trend_breadth = (
            w_s * np.sign(trend_short)
            + w_m * np.sign(trend_medium)
            + w_l * np.sign(trend_long)
        )

        # Regime filters
        adx_val = adx_vals[idx] if idx < len(adx_vals) else np.nan
        vol_val = atr_pct[idx] if idx < len(atr_pct) else np.nan

        adx_ok = not np.isnan(adx_val) and adx_val >= adx_thresh
        vol_ok = not np.isnan(vol_val) and vol_low <= vol_val <= vol_high

        # Current trend direction
        trend_dir = np.sign(trend_breadth)

        # Previous breadth for crossover detection
        prev_breadth = trend_breadth if idx == 0 else (
            w_s * np.sign(self._normalize_component(mom_short, idx - 1, 100))
            + w_m * np.sign(self._normalize_component(mom_medium, idx - 1, 100))
            + w_l * np.sign(self._normalize_component(mom_long, idx - 1, 100))
        )
        prev_dir = np.sign(prev_breadth)

        # Signal logic
        direction = None
        if trend_dir > breadth_thresh and adx_ok and vol_ok:
            direction = SignalDirection.LONG
        elif trend_dir < -breadth_thresh and adx_ok and vol_ok and use_short:
            direction = SignalDirection.SHORT
        elif trend_dir * prev_dir < 0:  # Trend reversal
            direction = SignalDirection.FLAT

        if direction is None or direction == SignalDirection.FLAT:
            return None

        # Risk parameters
        current_price = c_up_to[idx]
        atr_val = atr_vals[idx] if idx < len(atr_vals) else np.nan

        if direction == SignalDirection.LONG:
            stop_loss = current_price - self.params["exit_atr_mult"] * (atr_val if not np.isnan(atr_val) else current_price * 0.02)
            # Donchian exit
            if idx < len(dc_low):
                stop_loss = max(stop_loss, dc_low[idx])
        else:
            stop_loss = current_price + self.params["exit_atr_mult"] * (atr_val if not np.isnan(atr_val) else current_price * 0.02)
            if idx < len(dc_high):
                stop_loss = min(stop_loss, dc_high[idx])

        return Signal(
            strategy_id=self.strategy_id,
            symbol=bars_up_to[idx].get("symbol", "UNKNOWN"),
            direction=direction,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=current_price,
            confidence=min(abs(trend_breadth), 1.0),
            stop_loss=stop_loss,
            metadata={
                "trend_breadth": float(trend_breadth),
                "trend_short": float(trend_short),
                "trend_medium": float(trend_medium),
                "trend_long": float(trend_long),
                "adx": float(adx_val) if not np.isnan(adx_val) else None,
                "vol_percentile": float(vol_val) if not np.isnan(vol_val) else None,
                "breadth_threshold": breadth_thresh,
            },
        )

    def _momentum(self, c: np.ndarray, period: int) -> np.ndarray:
        """Rate of change over period."""
        out = np.full_like(c, np.nan)
        for i in range(period, len(c)):
            if not np.isnan(c[i]) and not np.isnan(c[i - period]) and c[i - period] != 0:
                out[i] = (c[i] - c[i - period]) / c[i - period]
        return out

    def _price_vs_ema(self, c: np.ndarray, ema_vals: np.ndarray) -> np.ndarray:
        """Normalized distance from EMA."""
        out = np.full_like(c, np.nan)
        for i in range(len(c)):
            if not np.isnan(c[i]) and not np.isnan(ema_vals[i]) and ema_vals[i] != 0:
                out[i] = (c[i] - ema_vals[i]) / ema_vals[i]
        return out

    def _donchian_position(self, c: np.ndarray, dc_high: np.ndarray, dc_low: np.ndarray) -> np.ndarray:
        """Position within Donchian channel: -1 at bottom, +1 at top, 0 at middle."""
        out = np.full_like(c, np.nan)
        for i in range(len(c)):
            if not np.isnan(c[i]) and not np.isnan(dc_high[i]) and not np.isnan(dc_low[i]):
                rng = dc_high[i] - dc_low[i]
                if rng > 0:
                    out[i] = 2 * (c[i] - dc_low[i]) / rng - 1
        return out

    def _normalize_component(self, comp: np.ndarray, idx: int, lookback: int = 100) -> float:
        """Normalize component to [-1, 1] using recent history percentile."""
        if idx < lookback:
            lookback = idx
        if lookback < 10:
            return 0.0
        window = comp[idx - lookback + 1:idx + 1]
        valid = window[~np.isnan(window)]
        if len(valid) < 5:
            return 0.0
        current = comp[idx]
        if np.isnan(current):
            return 0.0
        pct = np.sum(valid <= current) / len(valid)
        return 2 * pct - 1  # map [0,1] to [-1,1]