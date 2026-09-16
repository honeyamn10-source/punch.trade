"""Tests for strategy_health engine."""

from app.strategy_health import (
    assess_strategy_health,
    format_assessment,
)


def _mock_research_report(
    oos_net: float = 100.0,
    val_net: float = 50.0,
    oos_trades: int = 40,
    val_trades: int = 20,
    test_sharpe: float = 1.2,
    test_sortino: float = 1.5,
    test_max_dd: float = 8.0,
    wf_consistency: float = 0.75,
    wf_windows: int = 6,
    param_spread: float = 0.8,
    bootstrap_prob: float = 0.7,
) -> dict:
    return {
        "qualityGate": {"passed": True, "score": 85},
        "splits": {
            "train": {
                "trades": 100,
                "net_pnl": 500,
                "sharpe": 1.5,
                "sortino": 2.0,
                "max_drawdown_pct": -5.0,
            },
            "val": {
                "trades": val_trades,
                "net_pnl": val_net,
                "sharpe": 1.0,
                "sortino": 1.3,
                "max_drawdown_pct": -4.0,
            },
            "test": {
                "trades": oos_trades,
                "net_pnl": oos_net,
                "sharpe": test_sharpe,
                "sortino": test_sortino,
                "max_drawdown_pct": -test_max_dd,
            },
        },
        "walkForward": {
            "consistency": wf_consistency,
            "profitableWindows": int(wf_windows * wf_consistency),
            "totalWindows": wf_windows,
        },
        "parameterStability": {"spread": param_spread, "stable": param_spread < 1.5, "base": {}},
        "bootstrap": {"probPositive": bootstrap_prob, "realEdge": bootstrap_prob >= 0.6},
        "sample": {"tradesTrain": 100, "tradesVal": val_trades, "tradesTest": oos_trades},
    }


class TestStrategyHealth:
    def test_healthy_strategy(self):
        report = _mock_research_report()
        assessment = assess_strategy_health("adaptive_trend", "1.0.0", report)
        assert assessment.status == "HEALTHY"
        assert assessment.robustness_score >= 75
        assert assessment.verdict in ("PAPER_ELIGIBLE", "VALIDATED", "PROMISING")

    def test_watch_strategy_low_wf(self):
        report = _mock_research_report(wf_consistency=0.55, wf_windows=4)
        assessment = assess_strategy_health("test", "1.0.0", report)
        assert assessment.status in ("WATCH", "HEALTHY")

    def test_degraded_negative_oos(self):
        report = _mock_research_report(oos_net=-50.0)
        assessment = assess_strategy_health("test", "1.0.0", report)
        assert assessment.verdict == "FAILED"

    def test_failed_insufficient_trades(self):
        report = _mock_research_report(oos_trades=5, val_trades=3)
        assessment = assess_strategy_health("test", "1.0.0", report)
        assert assessment.verdict == "INSUFFICIENT"

    def test_pbo_warning(self):
        # Many trials with poor PBO
        trial_history = [
            {"sharpe": 1.0, "train_sharpe": 1.2, "test_sharpe": 0.5} for _ in range(15)
        ]
        report = _mock_research_report()
        assessment = assess_strategy_health("test", "1.0.0", report, trial_history=trial_history)
        assert assessment.pbo_prob is not None
        assert assessment.pbo_prob > 0.5
        assert assessment.verdict in ("DEGRADED", "FAILED")

    def test_dsr_bonus(self):
        # Few trials with good DSR
        trial_history = [{"sharpe": 1.5, "train_sharpe": 1.5, "test_sharpe": 1.4} for _ in range(8)]
        report = _mock_research_report()
        assessment = assess_strategy_health("test", "1.0.0", report, trial_history=trial_history)
        assert assessment.dsr_prob is not None
        assert assessment.dsr_prob > 0.5

    def test_cost_robustness_fail(self):
        report = _mock_research_report()
        report["costStress"] = {
            "base_expectancy": 100,
            "stress_expectancy": -20,
            "cost_drag_pct": 60,
        }
        assessment = assess_strategy_health("test", "1.0.0", report)
        cr = next(c for c in assessment.components if c.name == "Cost Robustness")
        assert cr.passed is False
        assert cr.score < 50

    def test_cross_market_portability(self):
        report = _mock_research_report()
        cross = [
            {"splits": {"test": {"net_pnl": 100}}},
            {"splits": {"test": {"net_pnl": 80}}},
            {"splits": {"test": {"net_pnl": -10}}},
        ]
        assessment = assess_strategy_health("test", "1.0.0", report, cross_market_reports=cross)
        cm = next(c for c in assessment.components if c.name == "Cross-Market Portability")
        assert cm.detail == "2/3 markets positive"
        assert cm.passed is True

    def test_format_assessment(self):
        report = _mock_research_report()
        assessment = assess_strategy_health("adaptive_trend", "1.0.0", report)
        formatted = format_assessment(assessment)
        assert formatted["strategyId"] == "adaptive_trend"
        assert "components" in formatted
        assert len(formatted["components"]) == 7
