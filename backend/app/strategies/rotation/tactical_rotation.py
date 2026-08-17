"""Strategy Family B: Tactical Rotation.

Portfolio/universe strategy that ranks assets by:
- Relative momentum
- Absolute momentum
- Trend quality
- Volatility-adjusted momentum

Supports RISK_ON / NEUTRAL / RISK_OFF regimes with defensive allocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

from ..base import AssetClass, ParameterSpec, Signal, SignalDirection, Strategy, StrategyFamily, Timeframe, register_strategy
from ..indicators import (
    adx,
    atr,
    closes,
    ema,
    percentile_rank,
    slope,
)


class RotationRegime(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


@dataclass
class AssetScore:
    """Score for a single asset in the universe."""
    symbol: str
    momentum_3m: float
    momentum_6m: float
    momentum_12m: float
    abs_momentum: float
    trend_quality: float
    vol_adj_momentum: float
    composite_score: float
    regime: RotationRegime


@register_strategy
class TacticalRotation(Strategy):
    """Tactical asset rotation with regime-aware defensive switching."""

    strategy_id = "punch_tactical_rotation"
    version = "1.0.0"
    family = StrategyFamily.ROTATION
    name = "PUNCH Tactical Rotation"
    description = (
        "Multi-asset rotation strategy ranking by relative/absolute momentum "
        "with trend/volatility regime filtering and defensive switching."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FOREX,
    ]
    supported_timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]

    warmup_bars = 252

    parameter_schema = [
        ParameterSpec("universe", list, [], "List of symbols in rotation universe", None, None),
        ParameterSpec("mom_3m_period", int, 63, "3-month momentum lookback (~63 days)", 20, 126),
        ParameterSpec("mom_6m_period", int, 126, "6-month momentum lookback", 60, 252),
        ParameterSpec("mom_12m_period", int, 252, "12-month momentum lookback", 126, 504),
        ParameterSpec("skip_recent_days", int, 21, "Skip most recent period (days)", 0, 63),
        ParameterSpec("ema_trend_period", int, 200, "EMA for trend filter", 100, 300),
        ParameterSpec("adx_period", int, 14, "ADX period for trend strength", 10, 30),
        ParameterSpec("adx_threshold", float, 25.0, "ADX threshold for trend regime", 15, 40),
        ParameterSpec("vol_period", int, 20, "Volatility lookback", 10, 60),
        ParameterSpec("max_assets", int, 3, "Maximum number of assets to hold", 1, 10),
        ParameterSpec("min_score", float, 0.0, "Minimum composite score for inclusion", -2.0, 2.0),
        ParameterSpec("defensive_symbols", list, [], "Defensive assets (cash, bonds, gold)", None, None),
        ParameterSpec("risk_off_allocation", float, 1.0, "Allocation to defensive in RISK_OFF", 0.0, 1.0),
        ParameterSpec("rebalance_frequency", int, 21, "Rebalance every N bars", 5, 63),
        ParameterSpec("use_shorting", bool, False, "Allow short positions", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._last_rebalance_idx = -1
        self._current_allocation: dict[str, float] = {}

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        """Generate rotation signals for the entire universe.

        This is a portfolio strategy - returns Signal with metadata containing
        target allocations for all universe symbols.
        """
        if not self.warmup_satisfied(bars, current_idx):
            return None

        # Only rebalance at specified frequency
        if current_idx - self._last_rebalance_idx < self.params["rebalance_frequency"]:
            return None

        universe = self.params["universe"]
        if not universe:
            return None

        defensive = self.params["defensive_symbols"]
        all_symbols = universe + defensive

        # Extract close prices for all symbols up to current_idx
        # In practice, this would come from a data provider with multi-symbol data
        # For this implementation, we assume bars contain multi-symbol data or
        # the strategy receives pre-computed scores

        # Compute scores for each asset
        scores = []
        for symbol in all_symbols:
            asset_bars = self._get_asset_bars(bars, symbol, current_idx)
            if len(asset_bars) < 252:
                continue
            score = self._compute_asset_score(asset_bars, symbol, defensive)
            if score:
                scores.append(score)

        if not scores:
            return None

        # Determine regime
        regime = self._determine_regime(scores)

        # Select top assets
        selected = self._select_assets(scores, regime)

        # Build target allocation
        allocation = self._build_allocation(selected, regime, defensive)

        self._last_rebalance_idx = current_idx
        self._current_allocation = allocation

        # Return signal with allocation metadata
        return Signal(
            strategy_id=self.strategy_id,
            symbol="PORTFOLIO",
            direction=SignalDirection.LONG if regime != RotationRegime.RISK_OFF else SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=1.0,
            confidence=1.0,
            metadata={
                "regime": regime.value,
                "allocation": allocation,
                "universe_scores": [
                    {
                        "symbol": s.symbol,
                        "composite_score": s.composite_score,
                        "momentum_3m": s.momentum_3m,
                        "momentum_6m": s.momentum_6m,
                        "momentum_12m": s.momentum_12m,
                        "trend_quality": s.trend_quality,
                        "vol_adj_momentum": s.vol_adj_momentum,
                    }
                    for s in scores
                ],
                "selected_symbols": [s.symbol for s in selected],
            },
        )

    def _get_asset_bars(self, bars: list[dict], symbol: str, current_idx: int) -> list[dict]:
        """Extract bars for a specific symbol.

        In real implementation, this would query a multi-symbol data store.
        For now, assumes bars have 'symbol' field or are single-symbol.
        """
        # If bars already have symbol field, filter
        if bars and "symbol" in bars[0]:
            return [b for b in bars[:current_idx + 1] if b.get("symbol") == symbol]
        # Otherwise assume single-symbol data
        return bars[:current_idx + 1]

    def _compute_asset_score(self, bars: list[dict], symbol: str, defensive: list[str]) -> AssetScore | None:
        is_defensive = symbol in defensive
        c = closes(bars)

        # Momentum calculations
        mom_3m = self._momentum(c, self.params["mom_3m_period"])
        mom_6m = self._momentum(c, self.params["mom_6m_period"])
        mom_12m = self._momentum(c, self.params["mom_12m_period"])

        # Skip recent period if configured
        skip = self.params["skip_recent_days"]
        idx = len(c) - 1 - skip
        if idx < 0:
            idx = len(c) - 1

        m3 = mom_3m[idx] if idx < len(mom_3m) else np.nan
        m6 = mom_6m[idx] if idx < len(mom_6m) else np.nan
        m12 = mom_12m[idx] if idx < len(mom_12m) else np.nan

        # Absolute momentum (vs zero/cash)
        abs_mom = m12 if not np.isnan(m12) else 0

        # Trend quality
        ema_trend = ema(c, self.params["ema_trend_period"])
        adx_vals = adx(bars, self.params["adx_period"])
        trend_q = self._trend_quality(c, ema_trend, adx_vals, idx)

        # Volatility-adjusted momentum
        atr_vals = atr(bars, self.params["vol_period"])
        atr_pct = percentile_rank(atr_vals, 252)
        vol = atr_pct[idx] if idx < len(atr_pct) else 50
        vol_adj = m12 / (vol / 100 + 0.5) if not np.isnan(m12) else 0

        # Composite score (weighted)
        if is_defensive:
            composite = 0.5  # Neutral for defensive
        else:
            composite = (
                0.3 * self._normalize(m3)
                + 0.3 * self._normalize(m6)
                + 0.2 * self._normalize(m12)
                + 0.1 * self._normalize(trend_q)
                + 0.1 * self._normalize(vol_adj)
            )

        return AssetScore(
            symbol=symbol,
            momentum_3m=float(m3) if not np.isnan(m3) else 0.0,
            momentum_6m=float(m6) if not np.isnan(m6) else 0.0,
            momentum_12m=float(m12) if not np.isnan(m12) else 0.0,
            abs_momentum=float(abs_mom) if not np.isnan(abs_mom) else 0.0,
            trend_quality=float(trend_q) if not np.isnan(trend_q) else 0.0,
            vol_adj_momentum=float(vol_adj) if not np.isnan(vol_adj) else 0.0,
            composite_score=float(composite) if not np.isnan(composite) else 0.0,
            regime=RotationRegime.NEUTRAL,  # Will be set by _determine_regime
        )

    def _trend_quality(self, c: np.ndarray, ema_vals: np.ndarray, adx_vals: np.ndarray, idx: int) -> float:
        if idx >= len(c) or idx >= len(ema_vals):
            return 0.0
        price_vs_ema = (c[idx] - ema_vals[idx]) / ema_vals[idx] if ema_vals[idx] != 0 else 0
        adx_val = adx_vals[idx] if idx < len(adx_vals) else 0
        return float(price_vs_ema * (adx_val / 50.0))

    def _normalize(self, x: float) -> float:
        """Normalize to roughly [-1, 1] using tanh."""
        return float(np.tanh(x * 5)) if not np.isnan(x) else 0.0

    def _momentum(self, c: np.ndarray, period: int) -> np.ndarray:
        out = np.full_like(c, np.nan)
        for i in range(period, len(c)):
            if c[i - period] != 0:
                out[i] = (c[i] - c[i - period]) / c[i - period]
        return out

    def _determine_regime(self, scores: list[AssetScore]) -> RotationRegime:
        """Determine overall portfolio regime from asset scores."""
        if not scores:
            return RotationRegime.NEUTRAL

        # Count assets with positive momentum and strong trend
        pos_momentum = sum(1 for s in scores if s.momentum_12m > 0 and s.trend_quality > 0)
        total_risky = sum(1 for s in scores if s.symbol not in self.params["defensive_symbols"])

        if total_risky == 0:
            return RotationRegime.NEUTRAL

        ratio = pos_momentum / total_risky
        if ratio >= 0.6:
            return RotationRegime.RISK_ON
        elif ratio <= 0.3:
            return RotationRegime.RISK_OFF
        return RotationRegime.NEUTRAL

    def _select_assets(self, scores: list[AssetScore], regime: RotationRegime) -> list[AssetScore]:
        """Select top assets based on regime."""
        defensive = self.params["defensive_symbols"]
        risky_scores = [s for s in scores if s.symbol not in defensive]
        defensive_scores = [s for s in scores if s.symbol in defensive]

        if regime == RotationRegime.RISK_OFF:
            # Hold defensive assets
            selected = defensive_scores
        elif regime == RotationRegime.RISK_ON:
            # Select top risky assets
            risky_scores.sort(key=lambda x: x.composite_score, reverse=True)
            selected = risky_scores[:self.params["max_assets"]]
            # Add cash buffer
            if defensive_scores:
                selected.append(defensive_scores[0])
        else:
            # NEUTRAL: mix
            risky_scores.sort(key=lambda x: x.composite_score, reverse=True)
            selected = risky_scores[:max(1, self.params["max_assets"] // 2)]
            if defensive_scores:
                selected.append(defensive_scores[0])

        # Filter by minimum score
        selected = [s for s in selected if s.composite_score >= self.params["min_score"]]
        return selected

    def _build_allocation(self, selected: list[AssetScore], regime: RotationRegime, defensive: list[str]) -> dict[str, float]:
        """Build target allocation dict."""
        if not selected:
            return {}

        allocation = {}
        n = len(selected)

        if regime == RotationRegime.RISK_OFF:
            # All to defensive
            for s in selected:
                allocation[s.symbol] = 1.0 / n
        else:
            # Equal weight among selected
            for s in selected:
                allocation[s.symbol] = 1.0 / n

        # Normalize
        total = sum(allocation.values())
        if total > 0:
            allocation = {k: v / total for k, v in allocation.items()}

        return allocation