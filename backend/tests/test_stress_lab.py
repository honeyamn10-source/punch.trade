"""Tests for Stress Lab."""

from app.stress_lab import (
    StressLab,
    StressType,
)


class TestStressLab:
    def test_lab_initialization(self):
        lab = StressLab()
        assert len(lab.scenarios) > 0
        # Check that SPREAD_WIDENING scenarios exist
        spread_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.SPREAD_WIDENING]
        assert len(spread_scenarios) >= 2

    def test_spread_widening_scenario(self):
        lab = StressLab()
        spread_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.SPREAD_WIDENING]
        scenario = spread_scenarios[0]
        assert scenario.stress_type == StressType.SPREAD_WIDENING
        assert "spread_multiplier" in scenario.params

    def test_scenario_execution_spread_widening(self):
        lab = StressLab()
        spread_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.SPREAD_WIDENING]
        scenario = spread_scenarios[0]

        base_metrics = {
            "cost_bps": 10.0,
            "net_return": 0.10,
            "max_drawdown_pct": 10.0,
            "sharpe": 1.5,
        }

        result = lab.run_stress_test(base_metrics, scenario)
        assert result.scenario == scenario
        assert "impact" in result.__dict__
        assert "stressed_metrics" in result.__dict__
        assert "cost_bps" in result.stressed_metrics
        # Spread widening should increase costs
        assert result.stressed_metrics["cost_bps"] > base_metrics["cost_bps"]

    def test_overnight_gap_scenario(self):
        lab = StressLab()
        gap_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.OVERNIGHT_GAP]
        scenario = gap_scenarios[0]

        base_metrics = {
            "max_drawdown_pct": 10.0,
            "net_return": 0.10,
        }

        result = lab.run_stress_test(base_metrics, scenario)
        # Overnight gap should increase max drawdown
        assert result.stressed_metrics["max_drawdown_pct"] > base_metrics["max_drawdown_pct"]
        # Net return should decrease (gap is negative)
        assert result.stressed_metrics["net_return"] < base_metrics["net_return"]

    def test_high_vol_regime(self):
        lab = StressLab()
        vol_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.HIGH_VOL_REGIME]
        scenario = vol_scenarios[0]

        base_metrics = {
            "volatility": 0.20,
            "max_drawdown_pct": 10.0,
            "sharpe": 1.5,
        }

        result = lab.run_stress_test(base_metrics, scenario)
        # High vol should increase vol and drawdown, decrease sharpe
        assert result.stressed_metrics["volatility"] > base_metrics["volatility"]
        assert result.stressed_metrics["max_drawdown_pct"] > base_metrics["max_drawdown_pct"]
        assert result.stressed_metrics["sharpe"] < base_metrics["sharpe"]

    def test_market_crash_scenario(self):
        lab = StressLab()
        crash_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.MARKET_CRASH]
        scenario = crash_scenarios[0]

        base_metrics = {
            "max_drawdown_pct": 10.0,
            "net_return": 0.15,
        }

        result = lab.run_stress_test(base_metrics, scenario)
        # Market crash should increase drawdown and reduce return
        assert result.stressed_metrics["max_drawdown_pct"] > base_metrics["max_drawdown_pct"]
        assert result.stressed_metrics["net_return"] < base_metrics["net_return"]

    def test_correlation_breakdown(self):
        lab = StressLab()
        corr_scenarios = [
            s for s in lab.scenarios if s.stress_type == StressType.CORRELATION_BREAKDOWN
        ]
        scenario = corr_scenarios[0]

        base_metrics = {"correlation_risk": 0.0}  # numeric for impact calculation
        result = lab.run_stress_test(base_metrics, scenario)
        assert result.stressed_metrics.get("correlation_risk") == "extreme"

    def test_liquidity_crisis(self):
        lab = StressLab()
        liq_scenarios = [s for s in lab.scenarios if s.stress_type == StressType.LIQUIDITY_CRISIS]
        scenario = liq_scenarios[0]

        base_metrics = {"cost_bps": 10.0, "liquidity_score": 1.0}
        result = lab.run_stress_test(base_metrics, scenario)
        assert result.stressed_metrics["cost_bps"] > base_metrics["cost_bps"]
        assert result.stressed_metrics["liquidity_score"] < base_metrics["liquidity_score"]

    def test_all_scenarios_run(self):
        lab = StressLab()
        base_metrics = {
            "cost_bps": 10.0,
            "net_return": 0.10,
            "max_drawdown_pct": 10.0,
            "sharpe": 1.5,
            "volatility": 0.20,
        }

        results = lab.run_all_scenarios(base_metrics)
        assert len(results) > 15  # Should have many scenarios

    def test_report_generation(self):
        lab = StressLab()
        base_metrics = {
            "cost_bps": 10.0,
            "net_return": 0.10,
            "max_drawdown_pct": 10.0,
            "sharpe": 1.5,
            "volatility": 0.20,
        }

        results = lab.run_all_scenarios(base_metrics)
        report = lab.generate_report(results)

        assert "total_scenarios" in report
        assert "passed" in report
        assert "failed" in report
        assert "pass_rate" in report
        assert "worst_max_drawdown_pct" in report
        assert "worst_sharpe" in report
        assert "worst_net_return" in report
        assert "scenarios" in report
        assert len(report["scenarios"]) > 15
