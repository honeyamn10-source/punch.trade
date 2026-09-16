"""Monte Carlo / Bootstrap enhancements for strategy analysis.

Provides:
- Seeded bootstrap with configurable iterations
- Fan chart generation (5th, 25th, 50th, 75th, 95th percentiles)
- Max drawdown distribution
- Losing streak distribution
- Ending equity distribution
- Time under water analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class MonteCarloConfig:
    """Configuration for Monte Carlo simulation."""

    iterations: int = 500
    seed: int = 42
    method: str = "bootstrap"  # "bootstrap" | "parametric"
    block_size: int = 1  # For block bootstrap
    confidence_levels: list[float] = field(default_factory=lambda: [0.05, 0.25, 0.50, 0.75, 0.95])


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""

    config: MonteCarloConfig
    paths: np.ndarray  # Shape: (iterations, periods)
    percentiles: dict  # percentile -> array of values over time
    ending_equity_dist: np.ndarray
    max_drawdown_dist: np.ndarray
    longest_losing_streak_dist: np.ndarray
    time_under_water_dist: np.ndarray
    expectancy_dist: np.ndarray
    prob_positive: float
    timestamp: datetime = field(default_factory=datetime.now)


class MonteCarloEngine:
    """Monte Carlo simulation engine for strategy analysis."""

    def __init__(self, config: MonteCarloConfig = None):
        self.config = config or MonteCarloConfig()
        self.rng = np.random.RandomState(self.config.seed)

    def run(
        self,
        returns: np.ndarray,
        initial_equity: float = 100000.0,
        periods: int | None = None,
    ) -> MonteCarloResult:
        """Run Monte Carlo simulation on return series.

        Args:
            returns: Historical returns (daily, hourly, etc.)
            initial_equity: Starting equity
            periods: Number of periods to simulate forward (default: len(returns))

        Returns:
            MonteCarloResult with all distributions and percentiles
        """
        if periods is None:
            periods = len(returns)

        if len(returns) < 10:
            raise ValueError("Need at least 10 returns for Monte Carlo")

        n_iter = self.config.iterations
        n_periods = periods

        # Generate paths
        if self.config.method == "bootstrap":
            paths = self._bootstrap_paths(returns, n_iter, n_periods)
        else:
            paths = self._parametric_paths(returns, n_iter, n_periods)

        # Convert returns to equity curves
        equity_paths = initial_equity * np.cumprod(1 + paths, axis=1)

        # Calculate distributions
        ending_equity = equity_paths[:, -1]
        max_dd = self._calc_max_drawdown(equity_paths)
        losing_streaks = self._calc_losing_streaks(paths)
        time_under_water = self._calc_time_under_water(equity_paths)
        expectancy = np.mean(paths, axis=1)

        # Calculate percentiles over time
        percentiles = {}
        for p in self.config.confidence_levels:
            percentiles[p] = np.percentile(equity_paths, p * 100, axis=0)

        # Probability of positive expectancy
        prob_positive = np.mean(expectancy > 0)

        return MonteCarloResult(
            config=self.config,
            paths=equity_paths,
            percentiles=percentiles,
            ending_equity_dist=ending_equity,
            max_drawdown_dist=max_dd,
            longest_losing_streak_dist=losing_streaks,
            time_under_water_dist=time_under_water,
            expectancy_dist=expectancy,
            prob_positive=prob_positive,
        )

    def _bootstrap_paths(self, returns: np.ndarray, n_iter: int, n_periods: int) -> np.ndarray:
        """Block bootstrap resampling of returns."""
        n = len(returns)
        block_size = self.config.block_size

        if block_size > 1:
            # Block bootstrap
            (n + block_size - 1) // block_size
            paths = np.zeros((n_iter, n_periods))
            for i in range(n_iter):
                idx = 0
                while idx < n_periods:
                    start = self.rng.randint(0, n - block_size + 1)
                    block = returns[start : start + block_size]
                    end = min(idx + block_size, n_periods)
                    paths[i, idx:end] = block[: end - idx]
                    idx += block_size
        else:
            # Simple bootstrap
            paths = self.rng.choice(returns, size=(n_iter, n_periods), replace=True)

        return paths

    def _parametric_paths(self, returns: np.ndarray, n_iter: int, n_periods: int) -> np.ndarray:
        """Parametric Monte Carlo assuming normal distribution."""
        mu = np.mean(returns)
        sigma = np.std(returns)
        return self.rng.normal(mu, sigma, size=(n_iter, n_periods))

    def _calc_max_drawdown(self, equity_paths: np.ndarray) -> np.ndarray:
        """Calculate maximum drawdown for each path."""
        n_iter, n_periods = equity_paths.shape
        max_dd = np.zeros(n_iter)
        for i in range(n_iter):
            peak = equity_paths[i, 0]
            max_dd_val = 0.0
            for t in range(1, n_periods):
                if equity_paths[i, t] > peak:
                    peak = equity_paths[i, t]
                dd = (peak - equity_paths[i, t]) / peak * 100
                if dd > max_dd_val:
                    max_dd_val = dd
            max_dd[i] = max_dd_val
        return max_dd

    def _calc_losing_streaks(self, paths: np.ndarray) -> np.ndarray:
        """Calculate longest losing streak for each path."""
        n_iter, n_periods = paths.shape
        streaks = np.zeros(n_iter)
        for i in range(n_iter):
            current_streak = 0
            max_streak = 0
            for t in range(n_periods):
                if paths[i, t] < 0:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 0
            streaks[i] = max_streak
        return streaks

    def _calc_time_under_water(self, equity_paths: np.ndarray) -> np.ndarray:
        """Calculate time under water (percentage of time below peak)."""
        n_iter, n_periods = equity_paths.shape
        tuw = np.zeros(n_iter)
        for i in range(n_iter):
            peak = equity_paths[i, 0]
            underwater = 0
            for t in range(1, n_periods):
                if equity_paths[i, t] > peak:
                    peak = equity_paths[i, t]
                else:
                    underwater += 1
            tuw[i] = underwater / n_periods * 100
        return tuw


def run_monte_carlo(
    returns: np.ndarray,
    initial_equity: float = 100000.0,
    iterations: int = 500,
    seed: int = 42,
    periods: int | None = None,
    method: str = "bootstrap",
) -> MonteCarloResult:
    """Convenience function to run Monte Carlo simulation."""
    config = MonteCarloConfig(iterations=iterations, seed=seed, method=method)
    engine = MonteCarloEngine(config)
    return engine.run(returns, initial_equity, periods)


def bootstrap_expectancy(
    returns: np.ndarray,
    iterations: int = 200,
    seed: int = 42,
) -> dict:
    """Bootstrap the expectancy distribution of returns."""
    rng = np.random.RandomState(seed)
    n = len(returns)
    expectancies = []
    for _ in range(iterations):
        sample = rng.choice(returns, size=n, replace=True)
        expectancies.append(np.mean(sample))
    expectancies = np.array(expectancies)
    expectancies.sort()

    def pct(p):
        return float(expectancies[min(len(expectancies) - 1, int(len(expectancies) * p))])

    positive = np.mean(expectancies > 0)
    return {
        "iterations": iterations,
        "seed": seed,
        "expectancy_p5": pct(0.05),
        "expectancy_p50": pct(0.50),
        "expectancy_p95": pct(0.95),
        "prob_positive": positive,
        "real_edge": bool(positive >= 0.60),
    }


def analyze_monte_carlo_result(result: MonteCarloResult) -> dict:
    """Analyze Monte Carlo result and return summary statistics."""
    return {
        "ending_equity": {
            "mean": float(np.mean(result.ending_equity_dist)),
            "median": float(np.median(result.ending_equity_dist)),
            "p5": float(np.percentile(result.ending_equity_dist, 5)),
            "p95": float(np.percentile(result.ending_equity_dist, 95)),
            "std": float(np.std(result.ending_equity_dist)),
        },
        "max_drawdown": {
            "mean": float(np.mean(result.max_drawdown_dist)),
            "median": float(np.median(result.max_drawdown_dist)),
            "p5": float(np.percentile(result.max_drawdown_dist, 5)),
            "p95": float(np.percentile(result.max_drawdown_dist, 95)),
            "prob_over_20pct": float(np.mean(result.max_drawdown_dist > 20)),
            "prob_over_50pct": float(np.mean(result.max_drawdown_dist > 50)),
        },
        "losing_streak": {
            "mean": float(np.mean(result.longest_losing_streak_dist)),
            "median": float(np.median(result.longest_losing_streak_dist)),
            "max": float(np.max(result.longest_losing_streak_dist)),
        },
        "time_under_water": {
            "mean": float(np.mean(result.time_under_water_dist)),
            "median": float(np.median(result.time_under_water_dist)),
            "p95": float(np.percentile(result.time_under_water_dist, 95)),
        },
        "expectancy": {
            "mean": float(np.mean(result.expectancy_dist)),
            "median": float(np.median(result.expectancy_dist)),
            "prob_positive": result.prob_positive,
        },
        "prob_positive_return": float(np.mean(result.ending_equity_dist > result.paths[0, 0])),
    }
