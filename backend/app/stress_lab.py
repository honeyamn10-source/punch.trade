"""Stress Scenario Lab - comprehensive stress testing for strategies.

Stress scenarios:
- 2x normal spread
- 3x slippage
- latency delay
- overnight gaps
- high-vol regime
- market crash segment
- trading halt-like gaps
- missing bars
- feed outage
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import numpy as np


class StressType(Enum):
    """Types of stress scenarios."""
    SPREAD_WIDENING = "spread_widening"
    SLIPPAGE_INCREASE = "slippage_increase"
    LATENCY_DELAY = "latency_delay"
    OVERNIGHT_GAP = "overnight_gap"
    HIGH_VOL_REGIME = "high_vol_regime"
    MARKET_CRASH = "market_crash"
    TRADING_HALT = "trading_halt"
    MISSING_BARS = "missing_bars"
    FEED_OUTAGE = "feed_outage"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    LIQUIDITY_CRISIS = "liquidity_crisis"


@dataclass
class StressScenario:
    """Defines a single stress scenario."""
    stress_type: StressType
    name: str
    description: str
    params: dict = field(default_factory=dict)
    severity: float = 1.0  # 1.0 = base, >1.0 = stress
    probability: float = 0.01  # Annual probability


# Predefined stress scenarios
STRESS_SCENARIOS = [
    StressScenario(
        stress_type=StressType.SPREAD_WIDENING,
        name="2x Spread Widening",
        description="Bid-ask spread doubles due to reduced liquidity",
        params={"spread_multiplier": 2.0},
        severity=2.0,
        probability=0.1,
    ),
    StressScenario(
        stress_type=StressType.SPREAD_WIDENING,
        name="3x Spread Widening",
        description="Severe spread widening during crisis",
        params={"spread_multiplier": 3.0},
        severity=3.0,
        probability=0.02,
    ),
    StressScenario(
        stress_type=StressType.SLIPPAGE_INCREASE,
        name="2x Slippage",
        description="Execution slippage doubles due to thin order books",
        params={"slippage_multiplier": 2.0},
        severity=2.0,
        probability=0.05,
    ),
    StressScenario(
        stress_type=StressType.SLIPPAGE_INCREASE,
        name="3x Slippage",
        description="Extreme slippage during flash crashes",
        params={"slippage_multiplier": 3.0},
        severity=3.0,
        probability=0.01,
    ),
    StressScenario(
        stress_type=StressType.LATENCY_DELAY,
        name="100ms Latency",
        description="100ms execution delay causing slippage",
        params={"latency_ms": 100},
        severity=1.5,
        probability=0.1,
    ),
    StressScenario(
        stress_type=StressType.LATENCY_DELAY,
        name="500ms Latency",
        description="Severe latency during exchange outages",
        params={"latency_ms": 500},
        severity=2.5,
        probability=0.02,
    ),
    StressScenario(
        stress_type=StressType.OVERNIGHT_GAP,
        name="BTC -8% Gap",
        description="Crypto overnight gap down 8%",
        params={"gap_pct": -0.08, "asset_class": "crypto"},
        severity=3.0,
        probability=0.005,
    ),
    StressScenario(
        stress_type=StressType.OVERNIGHT_GAP,
        name="NIFTY -3% Gap",
        description="India equity index overnight gap down 3%",
        params={"gap_pct": -0.03, "asset_class": "india_equity"},
        severity=2.0,
        probability=0.01,
    ),
    StressScenario(
        stress_type=StressType.HIGH_VOL_REGIME,
        name="Volatility Spike (2x)",
        description="Realized volatility doubles for 20 days",
        params={"vol_multiplier": 2.0, "duration_days": 20},
        severity=2.0,
        probability=0.05,
    ),
    StressScenario(
        stress_type=StressType.HIGH_VOL_REGIME,
        name="Volatility Spike (3x)",
        description="Extreme volatility regime (VIX > 50 equivalent)",
        params={"vol_multiplier": 3.0, "duration_days": 30},
        severity=3.0,
        probability=0.01,
    ),
    StressScenario(
        stress_type=StressType.MARKET_CRASH,
        name="Flash Crash (-10% in 1h)",
        description="Rapid 10% drop within 1 hour",
        params={"crash_pct": -0.10, "duration_hours": 1, "recovery_pct": 0.5},
        severity=4.0,
        probability=0.001,
    ),
    StressScenario(
        stress_type=StressType.MARKET_CRASH,
        name="Bear Market (-30% over 3m)",
        description="Prolonged 30% drawdown over 3 months",
        params={"crash_pct": -0.30, "duration_days": 90, "recovery_pct": 0.0},
        severity=4.0,
        probability=0.02,
    ),
    StressScenario(
        stress_type=StressType.TRADING_HALT,
        name="Circuit Breaker Halt",
        description="Exchange-wide trading halt for 15 minutes",
        params={"halt_duration_minutes": 15, "gap_on_reopen_pct": 0.02},
        severity=2.5,
        probability=0.005,
    ),
    StressScenario(
        stress_type=StressType.MISSING_BARS,
        name="10% Missing Bars",
        description="Random 10% of price bars missing from feed",
        params={"missing_pct": 0.10},
        severity=1.5,
        probability=0.05,
    ),
    StressScenario(
        stress_type=StressType.FEED_OUTAGE,
        name="30min Feed Outage",
        description="Complete market data feed outage for 30 minutes",
        params={"outage_minutes": 30},
        severity=2.0,
        probability=0.02,
    ),
    StressScenario(
        stress_type=StressType.CORRELATION_BREAKDOWN,
        name="Correlation -> 1.0",
        description="All assets become perfectly correlated (crisis)",
        params={"correlation_target": 0.95},
        severity=3.0,
        probability=0.01,
    ),
    StressScenario(
        stress_type=StressType.LIQUIDITY_CRISIS,
        name="Liquidity Drought",
        description="Order book depth drops 90%, spreads 10x",
        params={"depth_multiplier": 0.1, "spread_multiplier": 10.0},
        severity=4.0,
        probability=0.002,
    ),
]


@dataclass
class StressResult:
    """Result of a stress test."""
    scenario: StressScenario
    base_metrics: dict
    stressed_metrics: dict
    impact: dict  # metric -> pct change
    passed: bool
    notes: str = ""


class StressLab:
    """Stress testing laboratory for strategies."""

    def __init__(self):
        self.scenarios = STRESS_SCENARIOS

    def run_stress_test(
        self,
        strategy_metrics: dict,
        scenario: StressScenario,
        portfolio_state: Optional[dict] = None,
    ) -> StressResult:
        """Run a single stress scenario against strategy metrics."""
        base = strategy_metrics.copy()
        stressed = base.copy()

        # Apply scenario-specific transformations
        if scenario.stress_type == StressType.SPREAD_WIDENING:
            mult = scenario.params.get("spread_multiplier", 2.0)
            stressed["cost_bps"] = base.get("cost_bps", 0) * scenario.params.get("spread_multiplier", 1.0)
            stressed["net_return"] = base.get("net_return", 0) - (base.get("cost_bps", 0) * (scenario.params.get("spread_multiplier", 1.0) - 1.0) / 10000)

        elif scenario.stress_type == StressType.SLIPPAGE_INCREASE:
            mult = scenario.params.get("slippage_multiplier", 2.0)
            stressed["cost_bps"] = base.get("cost_bps", 0) * scenario.params.get("slippage_multiplier", 1.0)
            stressed["net_return"] = base.get("net_return", 0) - (base.get("cost_bps", 0) * (scenario.params.get("slippage_multiplier", 1.0) - 1.0) / 10000)

        elif scenario.stress_type == StressType.OVERNIGHT_GAP:
            gap = scenario.params.get("gap_pct", 0)
            stressed["max_drawdown_pct"] = base.get("max_drawdown_pct", 0) + abs(gap) * 100
            stressed["net_return"] = base.get("net_return", 0) + gap

        elif scenario.stress_type == StressType.HIGH_VOL_REGIME:
            mult = scenario.params.get("vol_multiplier", 2.0)
            stressed["volatility"] = base.get("volatility", 1) * mult
            stressed["max_drawdown_pct"] = base.get("max_drawdown_pct", 0) * mult
            stressed["sharpe"] = base.get("sharpe", 1) / mult

        elif scenario.stress_type == StressType.MARKET_CRASH:
            crash = scenario.params.get("crash_pct", -0.10)
            stressed["max_drawdown_pct"] = base.get("max_drawdown_pct", 0) + abs(crash) * 100
            stressed["net_return"] = base.get("net_return", 0) + crash
            recovery = scenario.params.get("recovery_pct", 0)
            if recovery < 1.0:
                stressed["net_return"] *= recovery

        elif scenario.stress_type == StressType.TRADING_HALT:
            gap = scenario.params.get("gap_on_reopen_pct", 0.02)
            stressed["max_drawdown_pct"] = base.get("max_drawdown_pct", 0) + gap * 100
            stressed["net_return"] = base.get("net_return", 0) - gap

        elif scenario.stress_type == StressType.MISSING_BARS:
            missing = scenario.params.get("missing_pct", 0.1)
            stressed["sharpe"] = base.get("sharpe", 1) * (1 - missing)
            stressed["data_quality"] = "degraded"

        elif scenario.stress_type == StressType.FEED_OUTAGE:
            stressed["data_quality"] = "outage"
            stressed["signal_latency_ms"] = base.get("signal_latency_ms", 0) + scenario.params.get("outage_minutes", 30) * 60 * 1000

        elif scenario.stress_type == StressType.CORRELATION_BREAKDOWN:
            corr = scenario.params.get("correlation_target", 0.95)
            stressed["correlation_risk"] = "extreme"
            stressed["diversification_ratio"] = 1.0  # No diversification benefit

        elif scenario.stress_type == StressType.LIQUIDITY_CRISIS:
            depth_mult = scenario.params.get("depth_multiplier", 0.1)
            spread_mult = scenario.params.get("spread_multiplier", 10.0)
            stressed["cost_bps"] = base.get("cost_bps", 0) * spread_mult
            stressed["liquidity_score"] = base.get("liquidity_score", 1) * depth_mult

        # Calculate impact
        impact = {}
        for key in set(base.keys()) | set(stressed.keys()):
            b = base.get(key, 0)
            s = stressed.get(key, 0)
            if b != 0:
                impact[key] = (s - b) / abs(b) * 100
            else:
                impact[key] = float('inf') if s != 0 else 0

        # Determine pass/fail
        passed = True
        notes = []
        if stressed.get("max_drawdown_pct", 0) > 50:
            passed = False
            notes.append(f"Max DD exceeds 50%: {stressed['max_drawdown_pct']:.1f}%")
        if stressed.get("sharpe", 1) < 0:
            passed = False
            notes.append(f"Sharpe negative: {stressed['sharpe']:.2f}")
        if stressed.get("net_return", 0) < -0.2:
            passed = False
            notes.append(f"Net return below -20%: {stressed['net_return']:.2%}")

        return StressResult(
            scenario=scenario,
            base_metrics=base,
            stressed_metrics=stressed,
            impact=impact,
            passed=passed,
            notes="; ".join(notes) if notes else "Passed",
        )

    def run_all_scenarios(
        self,
        strategy_metrics: dict,
        portfolio_state: Optional[dict] = None,
        scenario_filter: Optional[list[StressType]] = None,
    ) -> list[StressResult]:
        """Run all stress scenarios (or filtered subset)."""
        if scenario_filter:
            scenarios = [s for s in self.scenarios if s.stress_type in scenario_filter]
        else:
            scenarios = self.scenarios
        results = []
        for scenario in scenarios:
            result = self.run_stress_test(strategy_metrics, scenario, portfolio_state)
            results.append(result)
        return results

    def generate_report(self, results: list[StressResult]) -> dict:
        """Generate summary report from stress test results."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed

        # Worst impacts
        worst_dd = max((r.stressed_metrics.get("max_drawdown_pct", 0) for r in results), default=0)
        worst_sharpe = min((r.stressed_metrics.get("sharpe", 1) for r in results), default=1)
        worst_return = min((r.stressed_metrics.get("net_return", 0) for r in results), default=0)

        return {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / total if total > 0 else 0,
            "worst_max_drawdown_pct": worst_dd,
            "worst_sharpe": worst_sharpe,
            "worst_net_return": worst_return,
            "scenarios": [
                {
                    "name": r.scenario.name,
                    "type": r.scenario.stress_type.value,
                    "passed": r.passed,
                    "notes": r.notes,
                    "impact": r.impact,
                }
                for r in results
            ],
        }