"""Strategy Family H: Multi-Factor Equity Framework.

Framework for point-in-time multi-factor equity strategies.
Candidate factors: momentum, value, quality, low-risk.

ONLY implement a factor when point-in-time fundamental data is genuinely available.
Never use current fundamentals for historical backtests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from ..base import AssetClass, ParameterSpec, Signal, SignalDirection, Strategy, Timeframe, register_strategy
from ..indicators import closes


@register_strategy
class MultiFactorEquity(Strategy):
    """Multi-factor equity framework - requires point-in-time fundamental data."""

    strategy_id = "punch_equity_multifactor"
    version = "1.0.0"
    family = "multifactor"
    name = "PUNCH Multi-Factor Equity"
    description = (
        "Multi-factor equity framework combining momentum, value, quality, and low-risk factors. "
        "Requires point-in-time fundamental data. If unavailable, runs in framework-only mode. "
        "Never uses current fundamentals for historical backtests."
    )

    supported_asset_classes = [AssetClass.EQUITY, AssetClass.ETF]
    supported_timeframes = [Timeframe.D1]

    warmup_bars = 252

    parameter_schema = [
        ParameterSpec("universe", list, [], "List of equity symbols", None, None),
        ParameterSpec("factor_weights", dict, {}, "Factor weights: momentum, value, quality, low_risk", None, None),
        ParameterSpec("min_factor_score", float, 0.0, "Minimum composite factor score for long", -1.0, 1.0),
        ParameterSpec("max_positions", int, 10, "Maximum concurrent positions", 1, 50),
        ParameterSpec("rebalance_frequency", int, 21, "Rebalance every N days", 5, 63),
        ParameterSpec("use_shorting", bool, False, "Allow short positions", None, None),
        ParameterSpec("require_point_in_time", bool, True, "Require point-in-time fundamental data", None, None),
        ParameterSpec("data_available", bool, False, "Whether point-in-time data is available", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._last_rebalance_idx: int = -1
        self._current_allocation: dict = {}
        self._data_available: bool = params.get("data_available", False)

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        if current_idx - self._last_rebalance_idx < self.params["rebalance_frequency"]:
            return None

        universe = self.params["universe"]
        if not universe:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "empty_universe"},
            )

        if not self._data_available and self.params["require_point_in_time"]:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "point_in_time_data_unavailable"},
            )

        # Extract factor scores for each symbol
        factor_scores = {}
        for sym in self.params["universe"]:
            scores = self._extract_factor_scores(bars, current_idx, sym)
            if scores:
                factor_scores[sym] = scores

        if not factor_scores:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "no_factor_data"},
            )

        # Compute composite scores
        weights = self.params.get("factor_weights", {
            "momentum": 0.3,
            "value": 0.25,
            "quality": 0.25,
            "low_risk": 0.2,
        })

        composite_scores = {}
        for sym, scores in factor_scores.items():
            composite = sum(scores.get(f, 0) * weights.get(f, 0) for f in weights)
            if not np.isnan(composite):
                composite_scores[sym] = composite

        if not composite_scores:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "no_valid_scores"},
            )

        # Rank by composite score
        ranked = sorted(composite_scores.items(), key=lambda x: x[1], reverse=True)

        # Select top longs
        longs = [s for s, c in ranked if c >= self.params["min_factor_score"]][:self.params["max_positions"]]

        # Select shorts
        shorts = [s for s, c in ranked if c <= -self.params["min_factor_score"]] if self.params["use_shorting"] else []

        # Build allocation
        allocation = {}
        n_long = len(longs)
        n_short = len(shorts)

        if n_long > 0:
            for s in longs:
                allocation[s] = 1.0 / n_long

        if n_short > 0 and self.params["use_shorting"]:
            for s in shorts:
                allocation[s] = -1.0 / n_short

        # Normalize
        total_gross = sum(abs(v) for v in allocation.values())
        if total_gross > 0:
            allocation = {k: v / total_gross for k, v in allocation.items()}

        self._last_rebalance_idx = current_idx
        self._current_allocation = allocation

        return Signal(
            strategy_id=self.strategy_id,
            symbol="PORTFOLIO",
            direction=SignalDirection.LONG if allocation else SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=1.0,
            confidence=0.8 if self._data_available else 0.3,
            metadata={
                "allocation": allocation,
                "factor_scores": {s: {k: float(v) for k, v in sc.items()} for s, sc in factor_scores.items() if s in allocation},
                "longs": longs,
                "shorts": shorts,
            },
        )

    def _extract_factor_scores(self, bars: list[dict], current_idx: int, symbol: str) -> dict:
        """Extract factor scores for a symbol from bar data.

        Expects bars to have factor fields: factor_momentum, factor_value, factor_quality, factor_low_risk
        """
        scores = {}
        # Look back up to 100 bars to find the most recent bar for this symbol
        for i in range(current_idx, max(-1, current_idx - 100), -1):
            if i < 0:
                break
            bar = bars[i]
            if bar.get("symbol") == symbol:
                for factor in ["momentum", "value", "quality", "low_risk"]:
                    field = f"factor_{factor}"
                    if field in bar:
                        scores[factor] = float(bar[field])
                if scores:
                    break
        return scores