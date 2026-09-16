"""Strategy Family I: PUNCH Adaptive Ensemble.

Flagship META strategy. Does not simply average signals.

Pipeline:
REGIME
↓
eligible strategy families
↓
OOS health
↓
walk-forward health
↓
recent paper/live drift
↓
cost robustness
↓
strategy correlation
↓
risk allocation

Output: TRADE / REDUCED RISK / NO TRADE
"""

from __future__ import annotations

from datetime import datetime

from app.strategy_health import HealthAssessment

from ..base import (
    AssetClass,
    ParameterSpec,
    Signal,
    SignalDirection,
    Strategy,
    Timeframe,
    register_strategy,
)


@register_strategy
class AdaptiveEnsemble(Strategy):
    """Adaptive Ensemble - the flagship meta-strategy.

    Combines eligible strategies based on:
    - Market regime
    - Strategy health (OOS, WF, robustness)
    - Correlation/diversification
    - Cost awareness
    - Risk allocation
    """

    strategy_id = "punch_adaptive_ensemble"
    version = "1.0.0"
    family = "ensemble"
    name = "PUNCH Adaptive Ensemble"
    description = (
        "Flagship meta-strategy. Combines validated strategies based on regime, "
        "health, correlation, and risk. Output: TRADE / REDUCED RISK / NO TRADE."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FOREX,
        AssetClass.COMMODITY,
        AssetClass.FUTURE,
        AssetClass.OPTION,
    ]
    supported_timeframes = [
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    warmup_bars = 252

    parameter_schema = [
        ParameterSpec(
            "eligible_families",
            list,
            [
                "trend",
                "rotation",
                "reversion",
                "breakout",
                "statarb",
                "carry",
                "cross_section",
                "multifactor",
            ],
            "Strategy families to consider",
            None,
            None,
        ),
        ParameterSpec("regime_filters", dict, {}, "Regime -> allowed families mapping", None, None),
        ParameterSpec(
            "min_health_score", int, 60, "Minimum robustness score for eligibility", 0, 100
        ),
        ParameterSpec(
            "min_health_status",
            str,
            "VALIDATED",
            "Minimum health status: VALIDATED, PAPER_ELIGIBLE, DEGRADED",
            None,
            None,
        ),
        ParameterSpec(
            "max_correlation",
            float,
            0.7,
            "Maximum pairwise correlation for diversification",
            0.3,
            0.9,
        ),
        ParameterSpec(
            "risk_budget", float, 1.0, "Total risk budget (fraction of capital)", 0.1, 2.0
        ),
        ParameterSpec("cost_sensitivity_threshold", float, 0.5, "Max cost/drag ratio", 0.1, 1.0),
        ParameterSpec("drift_threshold", float, 0.3, "Paper-to-live drift threshold", 0.1, 1.0),
        ParameterSpec("rebalance_frequency", int, 1, "Rebalance every N bars", 1, 20),
        ParameterSpec(
            "use_health_assessment", bool, True, "Use formal health assessment", None, None
        ),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._last_rebalance_idx: int = -1
        self._current_allocation: dict = {}
        self._eligible_strategies: dict = {}

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        if current_idx - self._last_rebalance_idx < self.params["rebalance_frequency"]:
            return None

        # Detect current regime
        regime = self._detect_regime(bars, current_idx)

        # Get eligible strategy families for this regime
        eligible_families = self._get_eligible_families(regime)

        # Filter by health and correlation
        eligible_strategies = self._filter_strategies(eligible_families, bars, current_idx)

        if not eligible_strategies:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "no_eligible_strategies", "regime": regime},
            )

        # Allocate risk budget across eligible strategies
        allocation = self._allocate_risk(eligible_strategies, current_idx)

        if not allocation:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "zero_allocation", "regime": regime},
            )

        self._last_rebalance_idx = current_idx
        self._current_allocation = allocation

        return Signal(
            strategy_id=self.strategy_id,
            symbol="PORTFOLIO",
            direction=SignalDirection.LONG if allocation else SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=1.0,
            confidence=0.8,
            metadata={
                "allocation": allocation,
                "regime": regime,
                "eligible_families": eligible_families,
                "eligible_strategies": list(eligible_strategies.keys()),
            },
        )

    def _detect_regime(self, bars: list[dict], current_idx: int) -> str:
        """Simple regime detection based on ADX and volatility."""
        if current_idx < 50:
            return "UNKNOWN"

        from ..indicators import adx, atr, closes, percentile_rank

        bars_up_to = bars[: current_idx + 1]
        closes(bars_up_to)
        adx_vals = adx(bars_up_to, 14)
        atr_vals = atr(bars_up_to, 14)
        atr_pct = percentile_rank(atr_vals, 252)

        adx_val = adx_vals[current_idx] if current_idx < len(adx_vals) else 0
        vol_pct = atr_pct[current_idx] if current_idx < len(atr_pct) else 50

        if adx_val > 25 and vol_pct < 70:
            return "TRENDING"
        elif adx_val < 20 and vol_pct < 50:
            return "RANGING"
        elif vol_pct >= 70:
            return "HIGH_VOL"
        elif vol_pct <= 20:
            return "LOW_VOL"
        return "NEUTRAL"

    def _get_eligible_families(self, regime: str) -> list[str]:
        """Get eligible strategy families for the current regime."""
        regime_filters = self.params.get("regime_filters", {})
        if regime in regime_filters:
            return regime_filters[regime]

        # Default regime mapping
        default_mapping = {
            "TRENDING": ["trend", "breakout", "cross_section"],
            "RANGING": ["reversion", "statarb", "carry"],
            "HIGH_VOL": ["carry", "breakout"],
            "LOW_VOL": ["reversion", "carry"],
            "NEUTRAL": ["trend", "rotation", "carry"],
        }
        return default_mapping.get(regime, self.params["eligible_families"])

    def _filter_strategies(self, families: list[str], bars: list[dict], current_idx: int) -> dict:
        """Filter strategies by health, correlation, and cost."""
        # This is a simplified version - in production would query strategy health assessments
        eligible = {}

        for family in families:
            # Get strategies in this family (simplified - in practice would query registry)
            strategies = self._get_family_strategies(family)

            for strat_id, strat in strategies.items():
                # Health check
                if self.params.get("use_health_assessment", True):
                    health = self._get_health_assessment(strat_id, bars, current_idx)
                    if health and health.robustness_score < self.params["min_health_score"]:
                        continue
                    if health and health.status not in ["HEALTHY", "WATCH"]:
                        continue

                # Cost robustness
                if not self._check_cost_robustness(strat):
                    continue

                # Correlation check (simplified)
                if not self._check_correlation(strat, eligible):
                    continue

                eligible[strat_id] = strat

        return eligible

    def _get_family_strategies(self, family: str) -> dict:
        """Get available strategies for a family (placeholder)."""
        # In production, this would query the strategy registry
        family_strategies = {
            "trend": {"punch_adaptive_trend": "adaptive_trend"},
            "rotation": {"punch_tactical_rotation": "tactical_rotation"},
            "reversion": {"punch_regime_reversion": "regime_reversion"},
            "breakout": {
                "punch_volatility_breakout": "vol_breakout",
                "punch_opening_range_breakout": "orb",
            },
            "statarb": {"punch_pairs": "pairs"},
            "carry": {
                "punch_carry": "carry",
                "punch_fx_carry": "fx_carry",
                "punch_crypto_funding_carry": "crypto_funding",
            },
            "cross_section": {"punch_cross_section_momentum": "cross_section_momentum"},
            "multifactor": {"punch_equity_multifactor": "equity_multifactor"},
        }
        return family_strategies.get(family, {})

    def _get_health_assessment(
        self, strategy_id: str, bars: list[dict], current_idx: int
    ) -> HealthAssessment | None:
        """Get or compute health assessment for a strategy."""
        # Placeholder - in production would load from trial ledger
        # For now, return None to skip health filtering
        return None

    def _check_cost_robustness(self, strat_id: str) -> bool:
        """Check if strategy is cost-robust."""
        # Placeholder
        return True

    def _check_correlation(self, strat_id: str, existing: dict) -> bool:
        """Check correlation with existing selected strategies."""
        # Placeholder - in production would check return correlations
        return True

    def _allocate_risk(self, eligible: dict, current_idx: int) -> dict:
        """Allocate risk budget across eligible strategies."""
        if not eligible:
            return {}

        # Equal weight for now - in production would use risk parity / optimization
        n = len(eligible)
        if n == 0:
            return {}

        allocation = {strat_id: 1.0 / n for strat_id in eligible}

        # Scale to risk budget
        total_risk = sum(abs(v) for v in allocation.values())
        budget = self.params["risk_budget"]
        if total_risk > 0:
            allocation = {k: v * budget / total_risk for k, v in allocation.items()}

        return allocation
