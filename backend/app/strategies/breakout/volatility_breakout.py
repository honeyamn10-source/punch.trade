"""Strategy Family D: Volatility Breakout / Opening Range Breakout.

Architecture:
- Volatility compression detection
- Range contraction
- Breakout with volume/liquidity confirmation
- Trend-strength confirmation
- Entry

ORB Variant:
- Opening range breakout for liquid intraday markets
- 5m/15m/30m/60m opening windows
- Requires session calendar, liquidity, relative volume, spread sanity
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from ..base import (
    AssetClass,
    ParameterSpec,
    Signal,
    SignalDirection,
    Strategy,
    StrategyFamily,
    Timeframe,
    register_strategy,
)
from ..indicators import (
    adx,
    atr,
    closes,
    donchian_high,
    donchian_low,
    percentile_rank,
)


@register_strategy
class VolatilityBreakout(Strategy):
    """Volatility breakout with compression detection and regime filtering."""

    strategy_id = "punch_volatility_breakout"
    version = "1.0.0"
    family = StrategyFamily.BREAKOUT
    name = "PUNCH Volatility Breakout"
    description = (
        "Detects volatility compression (Bollinger bandwidth, ATR percentile), "
        "waits for range contraction, then enters on breakout with volume and "
        "trend-strength confirmation."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FUTURE,
        AssetClass.FOREX,
        AssetClass.COMMODITY,
    ]
    supported_timeframes = [
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    warmup_bars = 100

    parameter_schema = [
        ParameterSpec("bb_period", int, 20, "Bollinger Bands period", 10, 50),
        ParameterSpec("bb_std", float, 2.0, "Bollinger Bands std multiplier", 1.5, 3.0),
        ParameterSpec(
            "bb_bandwidth_pct",
            float,
            10.0,
            "Max Bollinger bandwidth percentile for compression",
            5,
            30,
        ),
        ParameterSpec("atr_period", int, 14, "ATR period", 10, 30),
        ParameterSpec("atr_pct_max", float, 30.0, "Max ATR percentile for compression", 10, 50),
        ParameterSpec(
            "donchian_period", int, 20, "Donchian channel period for breakout level", 10, 50
        ),
        ParameterSpec("adx_period", int, 14, "ADX period for trend confirmation", 10, 30),
        ParameterSpec("adx_min", float, 20.0, "Minimum ADX for trend confirmation", 10, 40),
        ParameterSpec(
            "volume_pct_min", float, 50.0, "Minimum volume percentile for confirmation", 30, 80
        ),
        ParameterSpec(
            "range_contraction_lookback", int, 20, "Lookback for range contraction check", 10, 50
        ),
        ParameterSpec(
            "range_contraction_pct", float, 50.0, "Range must contract to this percentile", 10, 80
        ),
        ParameterSpec("atr_stop_mult", float, 2.0, "ATR trailing stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short breakouts", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        # Extract parameters
        bb_p = self.params["bb_period"]
        bb_std = self.params["bb_std"]
        bb_bw_pct = self.params["bb_bandwidth_pct"]
        atr_p = self.params["atr_period"]
        atr_pct_max = self.params["atr_pct_max"]
        dc_p = self.params["donchian_period"]
        adx_p = self.params["adx_period"]
        adx_min = self.params["adx_min"]
        vol_pct_min = self.params["volume_pct_min"]
        rc_lookback = self.params["range_contraction_lookback"]
        rc_pct = self.params["range_contraction_pct"]
        self.params["atr_stop_mult"]
        self.params["use_shorting"]

        bars_up_to = bars[: current_idx + 1]
        c_up_to = closes(bars_up_to)

        # Compute indicators
        bb = self._bollinger(c_up_to, bb_p, bb_std)
        atr_vals = atr(bars_up_to, atr_p)
        atr_pct = percentile_rank(atr_vals, 252)
        dc_high = donchian_high(bars_up_to, dc_p)
        dc_low = donchian_low(bars_up_to, dc_p)
        adx_vals = adx(bars_up_to, adx_p)

        # Volume
        volumes = np.array([b.get("volume", 1.0) for b in bars_up_to])
        vol_pct = percentile_rank(volumes, 252)

        # Bollinger bandwidth (measure of compression)
        bb_bandwidth = (bb["upper"] - bb["lower"]) / np.where(bb["mid"] == 0, 1, bb["mid"])
        bb_bw_pct_arr = percentile_rank(bb_bandwidth, 252)

        # Range contraction: current range vs recent max range
        highs_arr = np.array([b.get("high", np.nan) for b in bars_up_to])
        lows_arr = np.array([b.get("low", np.nan) for b in bars_up_to])
        ranges = highs_arr - lows_arr
        max_range = np.full_like(ranges, np.nan)
        for i in range(rc_lookback - 1, len(ranges)):
            window = ranges[i - rc_lookback + 1 : i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) > 0:
                max_range[i] = np.max(valid)
        range_ratio = ranges / np.where(max_range == 0, 1, max_range)
        range_contracted = range_ratio < (rc_pct / 100.0)

        idx = current_idx
        if idx >= len(c_up_to):
            return None

        # Compression check
        bb_compressed = bb_bw_pct_arr[idx] <= bb_bw_pct if idx < len(bb_bw_pct_arr) else False
        atr_compressed = atr_pct[idx] <= atr_pct_max if idx < len(atr_pct) else False
        compressed = bb_compressed and atr_compressed and range_contracted[idx]

        # Volume confirmation
        vol_ok = vol_pct[idx] >= vol_pct_min if idx < len(vol_pct) else False

        # Trend strength
        adx_val = adx_vals[idx] if idx < len(adx_vals) else np.nan
        adx_ok = not np.isnan(adx_val) and adx_val >= adx_min

        # Breakout levels
        price = c_up_to[idx]
        breakout_long = price > dc_high[idx] if idx < len(dc_high) else False
        breakout_short = price < dc_low[idx] if idx < len(dc_low) else False

        atr_val = atr_vals[idx] if idx < len(atr_vals) else np.nan

        # Signal logic
        if compressed and vol_ok and adx_ok:
            if breakout_long:
                stop_loss = price - self.params["atr_stop_mult"] * (
                    atr_val if not np.isnan(atr_val) else price * 0.02
                )
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.LONG,
                    timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                    price=price,
                    confidence=0.8,
                    stop_loss=stop_loss,
                    metadata={
                        "compression": True,
                        "bb_bandwidth_pct": float(bb_bw_pct_arr[idx])
                        if idx < len(bb_bw_pct_arr)
                        else None,
                        "atr_pct": float(atr_pct[idx]) if idx < len(atr_pct) else None,
                        "adx": float(adx_val) if idx < len(adx_vals) else None,
                        "vol_pct": float(vol_pct[idx]) if idx < len(vol_pct) else None,
                        "breakout_level": float(dc_high[idx]) if idx < len(dc_high) else None,
                    },
                )

            if self.params["use_shorting"] and breakout_short:
                stop_loss = price + self.params["atr_stop_mult"] * (
                    atr_val if not np.isnan(atr_val) else price * 0.02
                )
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.SHORT,
                    timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                    price=price,
                    confidence=0.8,
                    stop_loss=stop_loss,
                    metadata={
                        "compression": True,
                        "bb_bandwidth_pct": float(bb_bw_pct[idx]) if idx < len(bb_bw_pct) else None,
                        "atr_pct": float(atr_pct[idx]) if idx < len(atr_pct) else None,
                        "adx": float(adx_val) if idx < len(adx_vals) else None,
                        "vol_pct": float(vol_pct[idx]) if idx < len(vol_pct) else None,
                        "breakout_level": float(dc_low[idx]) if idx < len(dc_low) else None,
                    },
                )

        return None

    def _bollinger(self, values: np.ndarray, period: int, std_mult: float) -> dict[str, np.ndarray]:
        """Bollinger Bands."""
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

    def _sma(self, values: np.ndarray, period: int) -> np.ndarray:
        if period <= 1:
            return values.copy()
        out = np.full_like(values, np.nan)
        n = len(values)
        for i in range(period - 1, n):
            window = values[i - period + 1 : i + 1]
            if not np.any(np.isnan(window)):
                out[i] = np.mean(window)
        return out
