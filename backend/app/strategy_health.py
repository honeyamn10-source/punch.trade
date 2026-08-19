"""Strategy Health Engine — multi-dimensional strategy evaluation.

Computes:
- Health status (HEALTHY/WATCH/DEGRADED/SUSPENDED/INSUFFICIENT_DATA)
- Robustness Score (0-100) with transparent components
- Verdict (PROMISING/VALIDATED/PAPER_ELIGIBLE/DEGRADED/FAILED/INSUFFICIENT)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .research import deflated_sharpe, final_test_lock, pbo


# --------------------------------------------------------------- types ----
@dataclass
class HealthComponent:
    """Single scored component of the health assessment."""

    name: str
    weight: float
    score: float  # 0-100
    detail: str
    passed: bool


@dataclass
class HealthAssessment:
    """Complete strategy health assessment."""

    strategy_id: str
    strategy_version: str
    assessed_at: str

    status: str  # HEALTHY, WATCH, DEGRADED, SUSPENDED, INSUFFICIENT_DATA
    robustness_score: int  # 0-100
    verdict: str  # PROMISING, VALIDATED, PAPER_ELIGIBLE, DEGRADED, FAILED, INSUFFICIENT

    components: list[HealthComponent]
    metrics: dict  # raw metrics used
    warnings: list[str]

    # Research integrity
    trial_count: int
    selection_bias_warning: str | None = None
    dsr_prob: float | None = None
    pbo_prob: float | None = None
    final_test_locked: bool = False


# --------------------------------------------------------------- config ----
@dataclass
class HealthConfig:
    """Thresholds and weights for health assessment."""

    # Component weights (sum ≈ 100)
    weight_oos_expectancy: float = 25.0
    weight_walk_forward: float = 20.0
    weight_risk_adjusted: float = 15.0
    weight_drawdown: float = 15.0
    weight_param_stability: float = 10.0
    weight_cost_robustness: float = 10.0
    weight_cross_market: float = 5.0

    # Minimum sample requirements
    min_oos_trades: int = 30
    min_wf_windows: int = 4
    min_total_trades: int = 50

    # Thresholds
    oos_expectancy_positive_threshold: float = 0.0
    wf_consistency_min: float = 0.5
    sharpe_min: float = 0.5
    sortino_min: float = 0.7
    max_drawdown_pct: float = 20.0
    param_stability_spread_max: float = 1.5
    cost_stress_multiplier_max: float = 3.0
    cost_drag_max_pct: float = 50.0

    # Penalties
    trial_count_penalty_per_100: float = 2.0
    sample_size_penalty_per_trade_below_min: float = 1.0

    # Status thresholds
    healthy_min_score: int = 75
    watch_min_score: int = 60
    degraded_min_score: int = 40


DEFAULT_CONFIG = HealthConfig()


# ----------------------------------------------------------- assessment ----
def assess_strategy_health(
    strategy_id: str,
    strategy_version: str,
    research_report: dict,
    trial_history: list[dict] | None = None,
    cross_market_reports: list[dict] | None = None,
    config: HealthConfig | None = None,
) -> HealthAssessment:
    """Produce a complete health assessment for a strategy.

    Args:
        strategy_id: Strategy identifier
        strategy_version: Version string
        research_report: Output from research.research_report()
        trial_history: List of trial records from trial_ledger
        cross_market_reports: Research reports for same strategy on other markets
        config: Assessment configuration

    Returns:
        HealthAssessment with status, score, verdict, and component breakdown
    """
    cfg = config or DEFAULT_CONFIG
    warnings: list[str] = []
    now = datetime.now(UTC).isoformat()

    # Extract metrics from research report
    research_report.get("qualityGate", {})
    walk_forward = research_report.get("walkForward", {})
    param_stability = research_report.get("parameterStability", {})
    bootstrap = research_report.get("bootstrap", {})
    splits = research_report.get("splits", {})
    research_report.get("sample", {})

    test_trades = splits.get("test", {}).get("trades", 0)
    oos_trades = test_trades
    wf_windows = walk_forward.get("totalWindows", 0)
    total_trades = sum(s.get("trades", 0) for s in splits.values() if isinstance(s, dict))

    # Trial count for selection bias
    trial_count = len(trial_history) if trial_history else 1
    if trial_count > 100:
        warnings.append(f"HIGH RESEARCH SEARCH SPACE: {trial_count} trials tested")

    # DSR / PBO from trial history
    dsr_result = None
    pbo_result = None
    if trial_history and len(trial_history) >= 5:
        dsr_result = deflated_sharpe(trial_history, min_trials=5)
        if len(trial_history) >= 10:
            pbo_result = pbo(trial_history, min_trials=10)

    # Final test lock check
    final_test_locked = False
    if trial_history:
        latest_trial = trial_history[-1]
        test_sharpe = splits.get("test", {}).get("sharpe", 0.0)
        lock_result = final_test_lock(latest_trial, test_sharpe)
        final_test_locked = lock_result.get("locked", False)

    # --------------------------------------- components -------------------
    components: list[HealthComponent] = []

    # 1. OOS Expectancy Quality
    oos_score, oos_detail, oos_passed = _score_oos_expectancy(splits, cfg)
    components.append(
        HealthComponent(
            "OOS Expectancy Quality", cfg.weight_oos_expectancy, oos_score, oos_detail, oos_passed
        )
    )

    # 2. Walk-Forward Consistency
    wf_score, wf_detail, wf_passed = _score_walk_forward(walk_forward, cfg)
    components.append(
        HealthComponent(
            "Walk-Forward Consistency", cfg.weight_walk_forward, wf_score, wf_detail, wf_passed
        )
    )

    # 3. Risk-Adjusted Return (Sharpe/Sortino)
    risk_score, risk_detail, risk_passed = _score_risk_adjusted(splits, cfg)
    components.append(
        HealthComponent(
            "Risk-Adjusted Return", cfg.weight_risk_adjusted, risk_score, risk_detail, risk_passed
        )
    )

    # 4. Drawdown Quality
    dd_score, dd_detail, dd_passed = _score_drawdown(splits, cfg)
    components.append(
        HealthComponent("Drawdown Quality", cfg.weight_drawdown, dd_score, dd_detail, dd_passed)
    )

    # 5. Parameter Stability
    ps_score, ps_detail, ps_passed = _score_param_stability(param_stability, cfg)
    components.append(
        HealthComponent(
            "Parameter Stability", cfg.weight_param_stability, ps_score, ps_detail, ps_passed
        )
    )

    # 6. Cost Robustness
    cr_score, cr_detail, cr_passed = _score_cost_robustness(research_report, cfg)
    components.append(
        HealthComponent(
            "Cost Robustness", cfg.weight_cost_robustness, cr_score, cr_detail, cr_passed
        )
    )

    # 7. Cross-Market Portability
    cm_score, cm_detail, cm_passed = _score_cross_market(cross_market_reports, cfg)
    components.append(
        HealthComponent(
            "Cross-Market Portability", cfg.weight_cross_market, cm_score, cm_detail, cm_passed
        )
    )

    # --------------------------------------- weighted score ---------------
    weighted_score = sum(c.score * c.weight for c in components) / sum(c.weight for c in components)

    # Penalties
    penalty = 0.0
    if trial_count > 100:
        penalty += cfg.trial_count_penalty_per_100 * (trial_count // 100)
    if oos_trades < cfg.min_oos_trades:
        penalty += cfg.sample_size_penalty_per_trade_below_min * (cfg.min_oos_trades - oos_trades)

    final_score = max(0, min(100, round(weighted_score - penalty)))

    # --------------------------------------- status & verdict -------------
    status = _determine_status(final_score, cfg)
    verdict = _determine_verdict(
        status,
        oos_passed,
        wf_passed,
        risk_passed,
        dd_passed,
        oos_trades,
        wf_windows,
        total_trades,
        dsr_result,
        pbo_result,
        final_test_locked,
    )

    # --------------------------------------- metrics summary --------------
    metrics = {
        "oos_trades": oos_trades,
        "wf_windows": wf_windows,
        "total_trades": total_trades,
        "test_sharpe": splits.get("test", {}).get("sharpe"),
        "test_sortino": splits.get("test", {}).get("sortino"),
        "test_max_dd_pct": splits.get("test", {}).get("max_drawdown_pct"),
        "wf_consistency": walk_forward.get("consistency"),
        "param_spread": param_stability.get("spread"),
        "bootstrap_prob_positive": bootstrap.get("probPositive"),
        "trial_count": trial_count,
        "dsr_prob": dsr_result.get("dsr_prob") if dsr_result else None,
        "pbo_prob": pbo_result.get("pbo") if pbo_result else None,
        "final_test_locked": final_test_locked,
    }

    return HealthAssessment(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        assessed_at=now,
        status=status,
        robustness_score=final_score,
        verdict=verdict,
        components=components,
        metrics=metrics,
        warnings=warnings,
        trial_count=trial_count,
        selection_bias_warning=warnings[0] if warnings else None,
        dsr_prob=dsr_result.get("dsr_prob") if dsr_result else None,
        pbo_prob=pbo_result.get("pbo") if pbo_result else None,
        final_test_locked=final_test_locked,
    )


# ------------------------------------------------------- component scorers ---
def _score_oos_expectancy(splits: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    test = splits.get("test", {})
    val = splits.get("val", {})
    oos_net = test.get("net_pnl", 0)
    val_net = val.get("net_pnl", 0)
    trades = test.get("trades", 0)

    passed = oos_net > cfg.oos_expectancy_positive_threshold and val_net > 0
    if trades < cfg.min_oos_trades:
        score = 30
        detail = f"Positive but low sample ({trades} trades)"
    elif oos_net > 0 and val_net > 0:
        score = 100
        detail = f"Positive OOS ({oos_net:.2f}) and validation ({val_net:.2f})"
    elif oos_net > 0:
        score = 60
        detail = "Positive OOS but validation negative"
    else:
        score = 0
        detail = f"Negative OOS expectancy ({oos_net:.2f})"
    return score, detail, passed


def _score_walk_forward(wf: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    consistency = wf.get("consistency", 0)
    windows = wf.get("totalWindows", 0)
    profitable = wf.get("profitableWindows", 0)

    passed = consistency >= cfg.wf_consistency_min and windows >= cfg.min_wf_windows
    if windows < cfg.min_wf_windows:
        score = 20
        detail = f"Insufficient windows ({windows})"
    elif consistency >= 0.75:
        score = 100
        detail = f"{profitable}/{windows} windows profitable ({consistency:.0%})"
    elif consistency >= 0.5:
        score = 70
        detail = f"{profitable}/{windows} windows profitable ({consistency:.0%})"
    else:
        score = 20
        detail = f"Low consistency: {profitable}/{windows} ({consistency:.0%})"
    return score, detail, passed


def _score_risk_adjusted(splits: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    test = splits.get("test", {})
    sharpe = test.get("sharpe", 0)
    sortino = test.get("sortino", 0)

    passed = sharpe >= cfg.sharpe_min and sortino >= cfg.sortino_min
    if sharpe >= 1.5 and sortino >= 2.0:
        score = 100
    elif sharpe >= 1.0 and sortino >= 1.5:
        score = 85
    elif sharpe >= 0.75 and sortino >= 1.0:
        score = 65
    elif sharpe >= 0.5 and sortino >= 0.7:
        score = 45
    else:
        score = 15
    detail = f"Sharpe={sharpe:.2f}, Sortino={sortino:.2f}"
    return score, detail, passed


def _score_drawdown(splits: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    test = splits.get("test", {})
    max_dd = abs(test.get("max_drawdown_pct", 0))

    passed = max_dd <= cfg.max_drawdown_pct
    if max_dd <= 5:
        score = 100
    elif max_dd <= 10:
        score = 85
    elif max_dd <= 15:
        score = 70
    elif max_dd <= 20:
        score = 55
    elif max_dd <= 30:
        score = 30
    else:
        score = 10
    detail = f"Max DD={max_dd:.1f}%"
    return score, detail, passed


def _score_param_stability(ps: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    spread = ps.get("spread", 0)
    stable = ps.get("stable", False)

    passed = stable and spread < cfg.param_stability_spread_max
    if spread < 0.5:
        score = 100
    elif spread < 1.0:
        score = 80
    elif spread < 1.5:
        score = 60
    elif spread < 2.5:
        score = 35
    else:
        score = 15
    detail = f"Relative spread={spread:.2f}, stable={stable}"
    return score, detail, passed


def _score_cost_robustness(report: dict, cfg: HealthConfig) -> tuple[float, str, bool]:
    # Look for cost stress results in report
    cost_stress = report.get("costStress", {})
    if not cost_stress:
        return 50, "Cost stress not evaluated", False

    cost_stress.get("base_expectancy", 0)
    stress_expectancy = cost_stress.get("stress_expectancy", 0)
    cost_drag = cost_stress.get("cost_drag_pct", 0)

    if cost_drag <= 20 and stress_expectancy > 0:
        score = 100
        detail = f"Cost drag {cost_drag:.0f}%, stress expectancy positive"
        passed = True
    elif cost_drag <= 35 and stress_expectancy > 0:
        score = 75
        detail = f"Cost drag {cost_drag:.0f}%, stress expectancy positive"
        passed = True
    elif cost_drag <= 50 and stress_expectancy > 0:
        score = 50
        detail = f"Cost drag {cost_drag:.0f}%, stress expectancy positive"
        passed = True
    elif stress_expectancy > 0:
        score = 25
        detail = f"High cost drag {cost_drag:.0f}% but stress positive"
        passed = False
    else:
        score = 0
        detail = f"Edge eliminated under stress (drag={cost_drag:.0f}%)"
        passed = False
    return score, detail, passed


def _score_cross_market(
    cross_reports: list[dict] | None, cfg: HealthConfig
) -> tuple[float, str, bool]:
    if not cross_reports or len(cross_reports) < 2:
        return 50, "Single market only", False

    positive_markets = 0
    for r in cross_reports:
        test = r.get("splits", {}).get("test", {})
        if test.get("net_pnl", 0) > 0:
            positive_markets += 1

    n = len(cross_reports)
    ratio = positive_markets / n
    if ratio >= 0.75:
        score = 100
    elif ratio >= 0.5:
        score = 70
    elif ratio >= 0.33:
        score = 40
    else:
        score = 15

    detail = f"{positive_markets}/{n} markets positive"
    passed = ratio >= 0.5
    return score, detail, passed


def _determine_status(score: int, cfg: HealthConfig) -> str:
    if score >= cfg.healthy_min_score:
        return "HEALTHY"
    elif score >= cfg.watch_min_score:
        return "WATCH"
    elif score >= cfg.degraded_min_score:
        return "DEGRADED"
    else:
        return "SUSPENDED"


def _determine_verdict(
    status: str,
    oos_passed: bool,
    wf_passed: bool,
    risk_passed: bool,
    dd_passed: bool,
    oos_trades: int,
    wf_windows: int,
    total_trades: int,
    dsr_result: dict | None,
    pbo_result: dict | None,
    final_test_locked: bool,
) -> str:
    # Hard rejection conditions
    if oos_trades < 10 or wf_windows < 2 or total_trades < 20:
        return "INSUFFICIENT"
    if not oos_passed or not wf_passed:
        return "FAILED"
    if not risk_passed or not dd_passed:
        return "DEGRADED"

    # Check research integrity
    if dsr_result and dsr_result.get("dsr_prob", 0) < 0.5:
        return "DEGRADED"
    if pbo_result and pbo_result.get("pbo", 1) > 0.5:
        return "DEGRADED"
    if not final_test_locked:
        return "PROMISING"  # Not yet fully validated

    if status == "HEALTHY":
        return "PAPER_ELIGIBLE"
    elif status == "WATCH":
        return "VALIDATED"
    else:
        return "DEGRADED"


# ----------------------------------------------------------- helpers ----
def format_assessment(assessment: HealthAssessment) -> dict:
    """Convert HealthAssessment to JSON-serializable dict."""
    return {
        "strategyId": assessment.strategy_id,
        "strategyVersion": assessment.strategy_version,
        "assessedAt": assessment.assessed_at,
        "status": assessment.status,
        "robustnessScore": assessment.robustness_score,
        "verdict": assessment.verdict,
        "components": [
            {
                "name": c.name,
                "weight": c.weight,
                "score": c.score,
                "detail": c.detail,
                "passed": c.passed,
            }
            for c in assessment.components
        ],
        "metrics": assessment.metrics,
        "warnings": assessment.warnings,
        "trialCount": assessment.trial_count,
        "selectionBiasWarning": assessment.selection_bias_warning,
        "dsrProb": assessment.dsr_prob,
        "pboProb": assessment.pbo_prob,
        "finalTestLocked": assessment.final_test_locked,
    }
