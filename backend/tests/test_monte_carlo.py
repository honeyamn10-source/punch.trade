"""Tests for Monte Carlo engine."""

import numpy as np

from app.monte_carlo import (
    MonteCarloConfig,
    MonteCarloEngine,
    MonteCarloResult,
    analyze_monte_carlo_result,
    bootstrap_expectancy,
    run_monte_carlo,
)


def _sample_returns(n: int = 1000, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    # Small positive drift
    return rng.normal(0.0005, 0.015, n)


class TestMonteCarloEngine:
    def test_engine_initialization(self):
        config = MonteCarloConfig(iterations=100, seed=42)
        engine = MonteCarloEngine(config)
        assert engine.config.iterations == 100
        assert engine.config.seed == 42

    def test_bootstrap_paths(self):
        config = MonteCarloConfig(iterations=100, seed=42, method="bootstrap")
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        paths = engine._bootstrap_paths(returns, 100, 100)
        assert paths.shape == (100, 100)

    def test_parametric_paths(self):
        config = MonteCarloConfig(iterations=100, seed=42, method="parametric")
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        paths = engine._parametric_paths(returns, 100, 100)
        assert paths.shape == (100, 100)

    def test_run_bootstrap(self):
        config = MonteCarloConfig(iterations=50, seed=42, method="bootstrap")
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, initial_equity=100000.0, periods=100)
        assert isinstance(result, MonteCarloResult)
        assert result.paths.shape == (50, 100)
        assert len(result.ending_equity_dist) == 50

    def test_run_parametric(self):
        config = MonteCarloConfig(iterations=50, seed=42, method="parametric")
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, initial_equity=100000.0, periods=100)
        assert isinstance(result, MonteCarloResult)
        assert result.paths.shape == (50, 100)

    def test_percentiles(self):
        config = MonteCarloConfig(iterations=100, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=100)
        for p in [0.05, 0.25, 0.50, 0.75, 0.95]:
            assert p in result.percentiles
            assert len(result.percentiles[p]) == 100

    def test_ending_equity_distribution(self):
        config = MonteCarloConfig(iterations=200, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, initial_equity=100000.0, periods=252)
        assert len(result.ending_equity_dist) == 200
        assert np.mean(result.ending_equity_dist) > 0

    def test_max_drawdown_distribution(self):
        config = MonteCarloConfig(iterations=100, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=252)
        assert len(result.max_drawdown_dist) == 100
        assert np.all(result.max_drawdown_dist >= 0)

    def test_losing_streak_distribution(self):
        config = MonteCarloConfig(iterations=100, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=252)
        assert len(result.longest_losing_streak_dist) == 100
        assert np.all(result.longest_losing_streak_dist >= 0)

    def test_time_under_water(self):
        config = MonteCarloConfig(iterations=100, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=252)
        assert len(result.time_under_water_dist) == 100
        assert np.all(result.time_under_water_dist >= 0)
        assert np.all(result.time_under_water_dist <= 100)

    def test_prob_positive(self):
        config = MonteCarloConfig(iterations=200, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=252)
        assert 0 <= result.prob_positive <= 1


class TestBootstrapExpectancy:
    def test_bootstrap_expectancy(self):
        returns = _sample_returns(500, seed=42)
        result = bootstrap_expectancy(returns, iterations=100, seed=42)
        assert "iterations" in result
        assert "expectancy_p5" in result
        assert "expectancy_p50" in result
        assert "expectancy_p95" in result
        assert "prob_positive" in result
        assert "real_edge" in result
        assert 0 <= result["prob_positive"] <= 1

    def test_bootstrap_edge_case(self):
        # Negative returns
        returns = np.array([-0.01, -0.02, -0.005, -0.015, -0.01])
        result = bootstrap_expectancy(returns, iterations=100, seed=42)
        assert result["prob_positive"] == 0.0
        assert bool(result["real_edge"]) is False


class TestAnalyzeResult:
    def test_analyze_result(self):
        config = MonteCarloConfig(iterations=200, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, initial_equity=100000.0, periods=252)
        analysis = analyze_monte_carlo_result(result)
        assert "ending_equity" in analysis
        assert "max_drawdown" in analysis
        assert "losing_streak" in analysis
        assert "time_under_water" in analysis
        assert "expectancy" in analysis
        assert "prob_positive_return" in analysis


class TestConvenienceFunction:
    def test_run_monte_carlo(self):
        returns = _sample_returns(500)
        result = run_monte_carlo(
            returns,
            initial_equity=100000.0,
            iterations=100,
            seed=42,
            periods=100,
            method="bootstrap",
        )
        assert isinstance(result, MonteCarloResult)
        assert result.paths.shape == (100, 100)


class TestMonteCarloResult:
    def test_result_attributes(self):
        config = MonteCarloConfig(iterations=50, seed=42)
        engine = MonteCarloEngine(config)
        returns = _sample_returns(500)
        result = engine.run(returns, periods=100)
        assert isinstance(result.paths, np.ndarray)
        assert isinstance(result.percentiles, dict)
        assert isinstance(result.ending_equity_dist, np.ndarray)
        assert isinstance(result.max_drawdown_dist, np.ndarray)
        assert isinstance(result.longest_losing_streak_dist, np.ndarray)
        assert isinstance(result.time_under_water_dist, np.ndarray)
        assert isinstance(result.expectancy_dist, np.ndarray)
        assert isinstance(result.prob_positive, float)
