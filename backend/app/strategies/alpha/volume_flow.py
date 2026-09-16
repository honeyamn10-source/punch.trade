"""Strategy Family L: Volume-Flow Imbalance (OHLCV microstructure proxy).

Literature: order-flow imbalance is the best documented microstructure
signal; from OHLCV alone the closest proxy is volume-weighted Close
Location Value (CLV). Sustained imbalance with price above/below VWAP
is a flow-confirmed directional signal. Intended for 1h+ horizons —
5m turnover does not survive realistic exchange fees (Frontiers 2026).

Signal: cumulative CLV*volume over `flow_lookback`, normalized by
average volume; VWAP deviation gate. Returns FLAT to exit.
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
from ..indicators import atr, closes, vwap


@register_strategy
class VolumeFlowImbalance(Strategy):
    """Flow-imbalance direction trading from OHLCV-only proxies."""

    strategy_id = "punch_volume_flow"
    version = "1.0.0"
    family = StrategyFamily.BREAKOUT
    name = "PUNCH Volume-Flow Imbalance"
    description = (
        "Trades sustained volume-weighted Close Location Value imbalance "
        "gated by VWAP deviation — a flow proxy built from OHLCV only. "
        "Designed for 1h+ horizons to survive fees."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
    ]
    supported_timeframes = [Timeframe.H1, Timeframe.H4, Timeframe.D1]

    warmup_bars = 60

    parameter_schema = [
        ParameterSpec("flow_lookback", int, 24, "Cumulative flow window (bars)", 6, 120),
        ParameterSpec("entry_flow", float, 0.15, "Normalized flow entry threshold", 0.05, 0.6),
        ParameterSpec("exit_flow", float, 0.05, "Normalized flow exit threshold", 0.01, 0.3),
        ParameterSpec("vwap_period", int, 20, "VWAP lookback period", 10, 60),
        ParameterSpec(
            "require_vwap_confirm", bool, True, "Require price on correct VWAP side", None, None
        ),
        ParameterSpec("atr_period", int, 14, "ATR period for stop", 10, 30),
        ParameterSpec("exit_atr_mult", float, 2.5, "ATR stop multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, True, "Allow short signals", None, None),
    ]

    def _clv(self, bar: dict) -> float:
        h = bar.get("high", 0.0)
        low = bar.get("low", 0.0)
        c = bar.get("close", 0.0)
        if h <= low:
            return 0.0
        return ((c - low) - (h - c)) / (h - low)

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        idx = current_idx
        lookback = self.params["flow_lookback"]
        if idx < lookback:
            return None

        flow = np.array(
            [self._clv(b) * b.get("volume", 0.0) for b in bars[idx - lookback + 1 : idx + 1]]
        )
        vols = np.array([b.get("volume", 0.0) for b in bars[idx - lookback + 1 : idx + 1]])
        avg_vol = vols.mean()
        if avg_vol <= 0:
            return None
        flow_score = flow.sum() / (avg_vol * lookback)

        c = closes(bars)
        current_price = c[idx]
        vw_vals = vwap(bars[: idx + 1], self.params["vwap_period"])
        vw = vw_vals[idx] if idx < len(vw_vals) and not np.isnan(vw_vals[idx]) else np.nan
        dev = (current_price / vw - 1.0) if not np.isnan(vw) and vw > 0 else 0.0

        entry_flow = self.params["entry_flow"]
        exit_flow = self.params["exit_flow"]
        require_vwap = self.params["require_vwap_confirm"]

        direction = None
        if flow_score > entry_flow and (not require_vwap or dev > 0):
            direction = SignalDirection.LONG
        elif (
            flow_score < -entry_flow
            and (not require_vwap or dev < 0)
            and self.params["use_shorting"]
        ):
            direction = SignalDirection.SHORT
        elif abs(flow_score) < exit_flow:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bars[idx].get("symbol", "UNKNOWN"),
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                price=current_price,
                confidence=0.5,
                position_size=0.0,
                metadata={"flow_score": float(flow_score), "vwap_dev": float(dev)},
            )

        if direction is None:
            return None

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
            confidence=min(abs(flow_score) / (2 * entry_flow), 1.0),
            stop_loss=stop_loss,
            metadata={"flow_score": float(flow_score), "vwap_dev": float(dev)},
        )
