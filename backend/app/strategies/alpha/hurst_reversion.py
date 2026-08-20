"""Strategy Family K: Hurst-Exponent-Gated Mean Reversion.

Literature: anti-persistent series (Hurst exponent H < 0.5) revert
faster than random-walk; gating mean reversion on H < threshold
avoids fading genuine trends (H > 0.5). Rolling R/S analysis over
`hurst_window` returns estimates H per bar.

Signal: z-score of price vs SMA. Trades only when the Hurst regime
is anti-persistent; returns FLAT when the regime turns persistent or
the z-score mean-reverts.
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
from ..indicators import closes, sma


def hurst_rs(series: np.ndarray, min_chunk: int = 8) -> float:
    """Rescaled-range (R/S) Hurst exponent estimate.

    Uses log-spaced sub-window sizes (8, 16, 32, ... up to n//2),
    regresses log(R/S) on log(window). Returns nan when data is
    degenerate.
    """
    n = len(series)
    if n < 2 * min_chunk:
        return float("nan")
    log_rs: list[float] = []
    log_n: list[float] = []
    m = min_chunk
    while m <= n // 2:
        chunks = n // m
        rs_vals = []
        for k in range(chunks):
            seg = series[k * m : (k + 1) * m]
            mean = seg.mean()
            dev = seg - mean
            cum = np.cumsum(dev)
            r = cum.max() - cum.min()
            s = seg.std()
            if s > 0 and r > 0:
                rs_vals.append(r / s)
        if rs_vals:
            log_rs.append(np.log(np.mean(rs_vals)))
            log_n.append(np.log(m))
        m *= 2
    if len(log_rs) < 3:
        return float("nan")
    coeffs = np.polyfit(log_n, log_rs, 1)
    return float(coeffs[0])


@register_strategy
class HurstGatedReversion(Strategy):
    """Mean reversion gated by anti-persistence of the return series."""

    strategy_id = "punch_hurst_reversion"
    version = "1.0.0"
    family = StrategyFamily.REVERSION
    name = "PUNCH Hurst-Gated Mean Reversion"
    description = (
        "Z-score mean reversion traded only when the rolling Hurst "
        "exponent indicates anti-persistence (H < threshold); avoids "
        "fading persistent trends. Returns FLAT to exit."
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

    warmup_bars = 120

    parameter_schema = [
        ParameterSpec("hurst_window", int, 120, "Rolling window for R/S Hurst", 60, 400),
        ParameterSpec("hurst_threshold", float, 0.48, "Max Hurst for reversion regime", 0.3, 0.5),
        ParameterSpec("zscore_period", int, 30, "SMA period for z-score", 10, 80),
        ParameterSpec("entry_z", float, 2.0, "Z-score entry threshold", 1.5, 4.0),
        ParameterSpec("exit_z", float, 0.5, "Z-score exit threshold", 0.1, 1.5),
        ParameterSpec("atr_period", int, 14, "ATR period for stop", 10, 30),
        ParameterSpec("exit_atr_mult", float, 2.0, "ATR stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short signals", None, None),
    ]

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        window = self.params["hurst_window"]
        if current_idx < window + 2:
            return None

        seg = c[current_idx - window : current_idx + 1]
        if len(seg) < window + 1 or np.any(np.isnan(seg)):
            return None

        returns = np.diff(seg) / seg[:-1]
        h = hurst_rs(returns)
        if np.isnan(h):
            return None

        anti_persistent = h < self.params["hurst_threshold"]

        zper = self.params["zscore_period"]
        ma = sma(c[: current_idx + 1], zper)
        idx = current_idx
        ma_val = ma[idx] if idx < len(ma) else np.nan
        if np.isnan(ma_val) or ma_val <= 0:
            return None

        hist = c[current_idx - zper + 1 : current_idx + 1]
        std = hist.std()
        if std <= 0:
            return None
        z = (c[idx] - ma_val) / std

        entry_z = self.params["entry_z"]
        exit_z = self.params["exit_z"]

        direction = None
        if anti_persistent and z <= -entry_z:
            direction = SignalDirection.LONG
        elif anti_persistent and z >= entry_z and self.params["use_shorting"]:
            direction = SignalDirection.SHORT
        elif not anti_persistent or abs(z) <= exit_z:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bars[idx].get("symbol", "UNKNOWN"),
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                price=c[idx],
                confidence=0.5,
                position_size=0.0,
                metadata={"hurst": h, "zscore": float(z), "regime": "flat"},
            )

        if direction is None:
            return None

        from ..indicators import atr

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
            confidence=min(abs(z) / (2 * entry_z), 1.0),
            stop_loss=stop_loss,
            metadata={
                "hurst": h,
                "zscore": float(z),
                "regime": "anti_persistent" if anti_persistent else "persistent",
            },
        )
