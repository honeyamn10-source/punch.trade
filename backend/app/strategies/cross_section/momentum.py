"""Strategy Family F: Cross-Sectional Momentum.

Operates over a universe. Ranks instruments by:
- Medium-term momentum (3m)
- Long-term momentum (6m, 12m)
- Volatility-adjusted momentum
- Trend quality

Supports skip-most-recent-period variants.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from ..base import AssetClass, ParameterSpec, Signal, SignalDirection, Strategy, StrategyFamily, Timeframe, register_strategy
from ..indicators import closes, percentile_rank


@register_strategy
class CrossSectionalMomentum(Strategy):
    """Cross-sectional momentum strategy operating on a universe."""

    strategy_id = "punch_cross_section_momentum"
    version = "1.0.0"
    family = "cross_section"
    name = "PUNCH Cross-Sectional Momentum"
    description = (
        "Ranks universe by momentum (3m/6m/12m), volatility-adjusted momentum, "
        "and trend quality. Supports skip-recent-period variants."
    )

    supported_asset_classes = [
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.CRYPTO,
    ]
    supported_timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]

    warmup_bars = 252

    parameter_schema = [
        ParameterSpec("universe", list, [], "List of symbols in universe", None, None),
        ParameterSpec("mom_3m_period", int, 63, "3-month momentum lookback", 20, 126),
        ParameterSpec("mom_6m_period", int, 126, "6-month momentum lookback", 60, 252),
        ParameterSpec("mom_12m_period", int, 252, "12-month momentum lookback", 126, 504),
        ParameterSpec("skip_recent_days", int, 21, "Skip most recent period (days)", 0, 63),
        ParameterSpec("vol_adjust", bool, True, "Volatility-adjust momentum", None, None),
        ParameterSpec("trend_quality_weight", float, 0.2, "Weight for trend quality", 0.0, 1.0),
        ParameterSpec("vol_momentum_weight", float, 0.3, "Weight for vol-adjusted momentum", 0.0, 1.0),
        ParameterSpec("mom_3m_weight", float, 0.2, "Weight for 3m momentum", 0.0, 1.0),
        ParameterSpec("mom_6m_weight", float, 0.3, "Weight for 6m momentum", 0.0, 1.0),
        ParameterSpec("mom_12m_weight", float, 0.3, "Weight for 12m momentum", 0.0, 1.0),
        ParameterSpec("top_n", int, 3, "Number of top assets to long", 1, 10),
        ParameterSpec("bottom_n", int, 0, "Number of bottom assets to short (0=long-only)", 0, 10),
        ParameterSpec("use_shorting", bool, False, "Allow short positions", None, None),
        ParameterSpec("min_momentum", float, 0.0, "Minimum momentum score for long", -1.0, 1.0),
        ParameterSpec("rebalance_frequency", int, 21, "Rebalance every N bars", 5, 63),
        ParameterSpec("skip_recent", bool, True, "Skip most recent period in momentum calc", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._last_rebalance_idx: int = -1
        self._current_allocation: dict = {}

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        # Only rebalance at specified frequency
        if current_idx - self._last_rebalance_idx < self.params["rebalance_frequency"]:
            return None

        universe = self.params["universe"]
        if not universe or len(universe) < 2:
            return None

        # Extract price series for all symbols
        price_data = {}
        for sym in self.params["universe"]:
            price_data[sym] = self._extract_closes(bars, current_idx, sym)

        # Filter symbols with enough data
        min_bars = max(self.params["mom_12m_period"], self.params["mom_6m_period"], self.params["mom_3m_period"]) + 20
        valid_symbols = {s: p for s, p in price_data.items() if len(p) >= len(p) * 0.8}

        if len(valid_symbols) < 2:
            return None

        # Compute momentum scores for each symbol
        scores = {}
        for sym, prices in valid_symbols.items():
            score = self._compute_momentum_score(prices)
            if score is not None:
                scores[sym] = score

        if not scores:
            return None

        # Rank by composite score
        ranked = sorted(scores.items(), key=lambda x: x[1]["composite"], reverse=True)

        # Select top/bottom
        top_n = self.params["top_n"]
        bottom_n = self.params["bottom_n"]

        longs = [s for s, sc in ranked[:top_n] if sc["composite"] >= self.params["min_momentum"]]
        shorts = [s for s, sc in ranked[-bottom_n:] if sc["composite"] <= -self.params["min_momentum"] and self.params["use_shorting"] and bottom_n > 0]

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

        # Normalize to sum to 1 (gross)
        total_gross = sum(abs(v) for v in allocation.values())
        if total_gross > 0:
            allocation = {k: v / total_gross for k, v in allocation.items()}

        self._last_rebalance_idx = current_idx
        self._current_allocation = allocation

        # Return portfolio signal
        return Signal(
            strategy_id=self.strategy_id,
            symbol="PORTFOLIO",
            direction=SignalDirection.LONG if allocation else SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=1.0,
            confidence=0.8,
            metadata={
                "allocation": allocation,
                "ranking": [
                    {"symbol": s, "composite": sc["composite"], "mom_3m": sc["mom_3m"], "mom_6m": sc["mom_6m"], "mom_12m": sc["mom_12m"], "vol_adj": sc["vol_adj"], "trend_q": sc["trend_q"]}
                    for s, sc in ranked
                ],
                "longs": longs,
                "shorts": shorts,
            },
        )

    def _extract_closes(self, bars: list[dict], current_idx: int, symbol: str) -> np.ndarray:
        closes_list = []
        for i in range(current_idx + 1):
            if bars[i].get("symbol") == symbol:
                closes_list.append(bars[i].get("close", np.nan))
        return np.array(closes_list)

    def _compute_momentum_score(self, prices: np.ndarray) -> Optional[dict]:
        if len(prices) < 252:
            return None

        skip = self.params["skip_recent_days"] if self.params["skip_recent"] else 0
        idx = len(prices) - 1 - skip
        if idx < 0:
            idx = len(prices) - 1

        # Momentum calculations
        mom_3m = self._momentum(prices, self.params["mom_3m_period"], idx)
        mom_6m = self._momentum(prices, self.params["mom_6m_period"], idx)
        mom_12m = self._momentum(prices, self.params["mom_12m_period"], idx)

        if any(np.isnan(x) for x in [mom_3m, mom_6m, mom_12m]):
            return None

        # Volatility-adjusted momentum
        vol_adj = mom_12m
        if self.params["vol_adjust"]:
            vol = np.std(np.diff(prices[-252:]) / prices[-252:-1])
            if vol > 0:
                vol_adj = mom_12m / (vol * np.sqrt(252) + 1e-8)

        # Trend quality (using 200-day EMA slope)
        trend_q = 0.0
        if len(prices) >= 200:
            ema_200 = self._ema(prices, 200)
            if len(ema_200) > 20:
                slope = (ema_200[-1] - ema_200[-20]) / 20
                trend_q = np.tanh(slope * 1000)  # Normalize

        # Composite score (weights sum to 1)
        w = {
            "m3": self.params["mom_3m_weight"],
            "m6": self.params["mom_6m_weight"],
            "m12": self.params["mom_12m_weight"],
            "va": self.params["vol_momentum_weight"],
            "tq": self.params["trend_quality_weight"],
        }
        total_w = sum(w.values())
        if total_w > 0:
            w = {k: v / total_w for k, v in w.items()}

        composite = (
            w["m3"] * np.tanh(mom_3m * 10) +
            w["m6"] * np.tanh(mom_6m * 10) +
            w["m12"] * np.tanh(mom_12m * 10) +
            w["va"] * np.tanh(vol_adj * 5) +
            w["tq"] * trend_q
        )

        return {
            "composite": float(composite),
            "mom_3m": float(mom_3m),
            "mom_6m": float(mom_6m),
            "mom_12m": float(mom_12m),
            "vol_adj": float(vol_adj),
            "trend_q": float(trend_q),
        }

    def _momentum(self, prices: np.ndarray, period: int, idx: int) -> float:
        if idx < period or idx >= len(prices):
            return np.nan
        if prices[idx - period] == 0:
            return np.nan
        return (prices[idx] - prices[idx - period]) / prices[idx - period]

    def _ema(self, values: np.ndarray, period: int) -> np.ndarray:
        if period <= 1:
            return values.copy()
        alpha = 2.0 / (period + 1)
        out = np.full_like(values, np.nan)
        first_valid = np.where(~np.isnan(values))[0]
        if len(first_valid) == 0:
            return out
        start = first_valid[0]
        out[start] = values[start]
        for i in range(start + 1, len(values)):
            if np.isnan(values[i]):
                out[i] = out[i - 1]
            else:
                out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
        return out