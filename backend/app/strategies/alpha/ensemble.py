"""Strategy Family N: Alpha Ensemble (majority-vote composite).

Votes among the live-validated directional families — Volume-Flow
Imbalance, Volatility-Managed Momentum and Adaptive Multi-Horizon
Trend — and trades only when at least `min_votes` members agree on
direction. Combines the three strongest live performers on real
Binance data; disagreement keeps you flat (no forced trades).

Stop-loss is the average of the agreeing members' stops.
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
from ..trend.adaptive_trend import AdaptiveMultiHorizonTrend
from .vol_managed_momentum import VolManagedMomentum
from .volume_flow import VolumeFlowImbalance


@register_strategy
class AlphaEnsemble(Strategy):
    """Majority-vote ensemble of live-validated directional strategies."""

    strategy_id = "punch_alpha_ensemble"
    version = "1.0.0"
    family = StrategyFamily.ENSEMBLE
    name = "PUNCH Alpha Ensemble"
    description = (
        "Majority-vote composite of Volume-Flow Imbalance, "
        "Volatility-Managed Momentum and Adaptive Trend; trades only "
        "when at least min_votes members agree on direction."
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
        ParameterSpec("min_votes", int, 2, "Minimum agreeing members for a trade", 1, 3),
        ParameterSpec("use_flow", bool, True, "Include Volume-Flow member", None, None),
        ParameterSpec(
            "use_momentum", bool, True, "Include Vol-Managed Momentum member", None, None
        ),
        ParameterSpec("use_trend", bool, True, "Include Adaptive Trend member", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._members: list[Strategy] = []
        if self.params["use_flow"]:
            self._members.append(VolumeFlowImbalance())
        if self.params["use_momentum"]:
            self._members.append(VolManagedMomentum())
        if self.params["use_trend"]:
            self._members.append(AdaptiveMultiHorizonTrend())

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        longs = 0
        shorts = 0
        stops_long: list[float] = []
        stops_short: list[float] = []
        conf = 0.0

        for member in self._members:
            sig = member.generate_signal(bars, current_idx)
            if sig is None:
                continue
            conf += sig.confidence
            if sig.direction == SignalDirection.LONG:
                longs += 1
                if sig.stop_loss is not None:
                    stops_long.append(sig.stop_loss)
            elif sig.direction == SignalDirection.SHORT:
                shorts += 1
                if sig.stop_loss is not None:
                    stops_short.append(sig.stop_loss)

        min_votes = self.params["min_votes"]
        direction = None
        stop_loss = None
        if longs >= min_votes and longs > shorts:
            direction = SignalDirection.LONG
            stop_loss = float(np.mean(stops_long)) if stops_long else None
        elif shorts >= min_votes and shorts > longs:
            direction = SignalDirection.SHORT
            stop_loss = float(np.mean(stops_short)) if stops_short else None

        if direction is None:
            return None

        idx = current_idx
        price = bars[idx].get("close", 0.0)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bars[idx].get("symbol", "UNKNOWN"),
            direction=direction,
            timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
            price=price,
            confidence=min(conf / len(self._members), 1.0),
            stop_loss=stop_loss,
            metadata={
                "votes_long": longs,
                "votes_short": shorts,
                "members": len(self._members),
                "min_votes": min_votes,
            },
        )
