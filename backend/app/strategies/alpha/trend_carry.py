"""Strategy Family M: Trend + Carry Composite.

Literature: crypto perpetual funding rates positively predict returns
(carry ~43% annualized cross-sectionally); momentum + carry composites
historically outperform either alone. Carry uses the optional `funding`
(or `carry`) field in bars when present (Binance funding snapshots);
when unavailable the strategy degrades to vol-gated trend with a
higher confirmation bar — never fabricates carry data.

Signal: composite = w_trend * trend_score + w_carry * carry_score.
LONG when composite > entry_threshold, SHORT below -entry_threshold
(if shorting enabled). Returns FLAT when composite exits the band.
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
class TrendCarryComposite(Strategy):
    """Trend + carry composite with honest carry-field fallback."""

    strategy_id = "punch_trend_carry"
    version = "1.0.0"
    family = StrategyFamily.CARRY
    name = "PUNCH Trend-Carry Composite"
    description = (
        "Blends time-series trend with crypto funding carry when a "
        "`funding` field is present in bars; otherwise runs trend-only "
        "with a stricter confirmation threshold. Never invents carry data."
    )

    supported_asset_classes = [AssetClass.CRYPTO]
    supported_timeframes = [
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    warmup_bars = 60

    parameter_schema = [
        ParameterSpec("trend_lookback", int, 24, "Trend ROC lookback (bars)", 6, 120),
        ParameterSpec("trend_ma", int, 3, "Trend signal smoothing", 1, 10),
        ParameterSpec("weight_trend", float, 0.6, "Trend weight in composite", 0.0, 1.0),
        ParameterSpec("weight_carry", float, 0.4, "Carry weight in composite", 0.0, 1.0),
        ParameterSpec("carry_scale", float, 0.02, "Carry normalization (fraction)", 0.005, 0.1),
        ParameterSpec("entry_threshold", float, 0.08, "Composite entry threshold", 0.02, 0.4),
        ParameterSpec("exit_threshold", float, 0.03, "Composite exit threshold", 0.0, 0.2),
        ParameterSpec(
            "trend_only_min", float, 0.15, "Min |trend| when carry unavailable", 0.05, 0.5
        ),
        ParameterSpec(
            "funding_field", str, "funding", "Bar field carrying funding rate", None, None
        ),
        ParameterSpec("atr_period", int, 14, "ATR period for stop", 10, 30),
        ParameterSpec("exit_atr_mult", float, 2.5, "ATR stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short signals", None, None),
    ]

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        idx = current_idx
        if idx >= len(c) or np.isnan(c[idx]):
            return None

        lookback = self.params["trend_lookback"]
        if idx < lookback:
            return None

        roc = np.full(len(c[: idx + 1]), np.nan)
        for i in range(lookback, idx + 1):
            if not np.isnan(c[i]) and not np.isnan(c[i - lookback]) and c[i - lookback] != 0:
                roc[i] = (c[i] - c[i - lookback]) / c[i - lookback]

        ma_p = self.params["trend_ma"]
        trend = sma(roc, ma_p) if ma_p > 1 else roc
        trend_val = trend[idx] if idx < len(trend) else np.nan
        if np.isnan(trend_val):
            return None

        funding_field = self.params["funding_field"]
        has_carry = funding_field and funding_field in bars[idx]
        carry_val = 0.0
        if has_carry:
            raw = bars[idx].get(funding_field, 0.0)
            try:
                carry_val = float(raw) / self.params["carry_scale"]
            except (TypeError, ValueError):
                carry_val = 0.0
            carry_val = max(-1.0, min(1.0, carry_val))

        w_t = self.params["weight_trend"]
        w_c = self.params["weight_carry"]
        total_w = w_t + w_c
        if total_w > 0:
            w_t /= total_w
            w_c /= total_w

        composite = w_t * trend_val + w_c * carry_val if has_carry else trend_val

        entry_thresh = self.params["entry_threshold"]
        if not has_carry:
            entry_thresh = self.params["trend_only_min"]

        exit_thresh = self.params["exit_threshold"]

        direction = None
        if composite > entry_thresh:
            direction = SignalDirection.LONG
        elif composite < -entry_thresh and self.params["use_shorting"]:
            direction = SignalDirection.SHORT
        elif abs(composite) < exit_thresh:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bars[idx].get("symbol", "UNKNOWN"),
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                price=c[idx],
                confidence=0.5,
                position_size=0.0,
                metadata={
                    "composite": float(composite),
                    "trend": float(trend_val),
                    "carry": float(carry_val) if has_carry else None,
                },
            )

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
            confidence=min(abs(composite) / (2 * entry_thresh), 1.0),
            stop_loss=stop_loss,
            metadata={
                "composite": float(composite),
                "trend": float(trend_val),
                "carry": float(carry_val) if has_carry else None,
            },
        )
