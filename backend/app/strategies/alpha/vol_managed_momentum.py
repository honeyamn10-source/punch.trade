"""Strategy Family J: Volatility-Managed Time-Series Momentum.

Literature: Barroso & Santa-Clara (2015) "Momentum has its moments",
J. Financial Economics. Volatility-managed momentum scales exposure
inversely to realized volatility, reducing crash risk and increasing
risk-adjusted returns. Time-series momentum (Moskowitz, Ooi, Pedersen
2012) is the strongest documented crypto cross-asset effect.

Signal: smoothed rate-of-change over `momentum_lookback` bars.
Sizing:  position = min(max_position, target_vol / realized_vol)
scaled further by momentum strength. Volatility is EWMA of squared
returns, annualized. Only OHLCV inputs — runs on real Binance bars.
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
from ..indicators import atr, closes, sma


@register_strategy
class VolManagedMomentum(Strategy):
    """Time-series momentum with inverse-volatility position scaling."""

    strategy_id = "punch_vol_managed_momentum"
    version = "1.0.0"
    family = StrategyFamily.TREND
    name = "PUNCH Volatility-Managed Momentum"
    description = (
        "Time-series momentum (smoothed ROC) with Barroso-Santa-Clara "
        "volatility targeting: position size scales inversely with "
        "realized volatility, capped at max_position."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
    ]
    supported_timeframes = [
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    warmup_bars = 60

    parameter_schema = [
        ParameterSpec("momentum_lookback", int, 12, "Momentum ROC lookback (bars)", 6, 96),
        ParameterSpec("signal_ma", int, 3, "Smoothing of momentum signal", 1, 10),
        ParameterSpec("vol_lookback", int, 30, "Realized volatility EWMA span", 10, 120),
        ParameterSpec("target_vol", float, 0.25, "Annualized target volatility", 0.05, 0.6),
        ParameterSpec("max_position", float, 1.0, "Maximum position size (fraction)", 0.1, 2.0),
        ParameterSpec("min_momentum", float, 0.002, "Minimum momentum for entry", 0.0, 0.05),
        ParameterSpec(
            "momentum_scale", float, 0.02, "Momentum strength scaling denominator", 0.005, 0.1
        ),
        ParameterSpec("atr_period", int, 14, "ATR period for stop", 10, 30),
        ParameterSpec("exit_atr_mult", float, 2.5, "ATR stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short signals", None, None),
        ParameterSpec("annualize_bars", int, 365, "Bars per year for vol annualization", 24, 8760),
    ]

    def __init__(self, **params):
        super().__init__(**params)

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        lookback = self.params["momentum_lookback"]
        if current_idx < lookback:
            return None

        roc = np.full_like(c, np.nan)
        for i in range(lookback, current_idx + 1):
            if not np.isnan(c[i]) and not np.isnan(c[i - lookback]) and c[i - lookback] != 0:
                roc[i] = (c[i] - c[i - lookback]) / c[i - lookback]

        ma_p = self.params["signal_ma"]
        mom = sma(roc[: current_idx + 1], ma_p) if ma_p > 1 else roc[: current_idx + 1]
        idx = current_idx
        mom_val = mom[idx] if idx < len(mom) else np.nan
        if np.isnan(mom_val):
            return None

        returns = np.diff(c[: current_idx + 1]) / c[:current_idx]
        var = np.full(len(returns), np.nan)
        span = self.params["vol_lookback"]
        alpha = 2.0 / (span + 1)
        e = 0.0
        for i in range(len(returns)):
            e = alpha * returns[i] ** 2 + (1 - alpha) * e
            var[i] = e
        ann = self.params["annualize_bars"]
        realized_vol = np.sqrt(var[idx - 1]) * np.sqrt(ann) if idx >= 1 else np.nan
        if not np.isnan(realized_vol) and realized_vol > 0:
            position = min(self.params["max_position"], self.params["target_vol"] / realized_vol)
        else:
            position = self.params["max_position"] * 0.5

        strength = min(abs(mom_val) / self.params["momentum_scale"], 1.0)
        position *= strength

        direction = None
        if mom_val > self.params["min_momentum"]:
            direction = SignalDirection.LONG
        elif mom_val < -self.params["min_momentum"] and self.params["use_shorting"]:
            direction = SignalDirection.SHORT

        if direction is None:
            return None

        current_price = c[idx]
        atr_vals = atr(bars[: idx + 1], self.params["atr_period"])
        atr_val = (
            atr_vals[idx]
            if idx < len(atr_vals) and not np.isnan(atr_vals[idx])
            else current_price * 0.02
        )

        if direction == SignalDirection.LONG:
            stop_loss = current_price - self.params["exit_atr_mult"] * atr_val
        else:
            stop_loss = current_price + self.params["exit_atr_mult"] * atr_val

        return Signal(
            strategy_id=self.strategy_id,
            symbol=bars[idx].get("symbol", "UNKNOWN"),
            direction=direction,
            timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
            price=current_price,
            confidence=min(abs(mom_val) / (2 * self.params["momentum_scale"]), 1.0),
            position_size=float(position),
            stop_loss=stop_loss,
            metadata={
                "momentum": float(mom_val),
                "realized_vol_ann": float(realized_vol) if not np.isnan(realized_vol) else None,
                "position_scale": float(position),
                "target_vol": self.params["target_vol"],
            },
        )
