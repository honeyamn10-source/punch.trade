"""Tests for portfolio_brain."""

import numpy as np
import pytest

from app.portfolio_brain import (
    allocate_hrp,
    allocate_minvar,
    allocate_risk_parity,
    allocate_equal,
    allocate_portfolio,
    detect_regime_hmm,
    detect_regime_vol_clustering,
    kelly_weights,
    fractional_kelly,
    factor_exposure,
    regime_aware_allocation,
    RegimeConfig,
    AllocationConfig,
    RegimeState,
)


def _sample_returns(n_assets: int = 5, n_bars: int = 500, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    # Correlated returns
    true_corr = rng.randn(n_assets, n_assets)
    true_corr = true_corr @ true_corr.T
    true_corr = true_corr / np.sqrt(np.outer(np.diag(true_corr), np.diag(true_corr)))
    true_cov = true_corr * np.outer(rng.uniform(0.01, 0.05, n_assets), rng.uniform(0.01, 0.05, n_assets))
    returns = rng.multivariate_normal(np.zeros(n_assets), true_cov, size=n_bars)
    return returns


class TestAllocation:
    def test_equal_weights(self):
        w = allocate_equal(5)
        assert len(w) == 5
        assert np.isclose(w.sum(), 1.0)
        assert np.allclose(w, 0.2)

    def test_minvar_weights(self):
        returns = _sample_returns(5, 500)
        cov = np.cov(returns.T)
        w = allocate_minvar(cov)
        assert len(w) == 5
        assert np.isclose(w.sum(), 1.0)
        assert np.all(w >= 0)

    def test_risk_parity_weights(self):
        returns = _sample_returns(5, 500)
        cov = np.cov(returns.T)
        w = allocate_risk_parity(cov)
        assert len(w) == 5
        assert np.isclose(w.sum(), 1.0)
        assert np.all(w >= 0)

    def test_hrp_weights(self):
        returns = _sample_returns(5, 500)
        cov = np.cov(returns.T)
        corr = np.corrcoef(returns.T)
        w = allocate_hrp(cov, corr)
        assert len(w) == 5
        assert np.isclose(w.sum(), 1.0)
        assert np.all(w >= 0)

    def test_allocate_portfolio_all_methods(self):
        returns = _sample_returns(5, 500)
        for method in ["hrp", "minvar", "riskparity", "equal"]:
            cfg = AllocationConfig(method=method, target_vol=0.15)
            w = allocate_portfolio(returns, cfg)
            assert len(w) == 5
            # With vol targeting, weights sum to <= 1.0 (cash position)
            assert w.sum() <= 1.0 + 1e-6
            assert w.sum() > 0

    def test_vol_targeting(self):
        returns = _sample_returns(5, 500) * 2  # high vol
        cfg = AllocationConfig(method="equal", target_vol=0.10)
        w = allocate_portfolio(returns, cfg)
        port_vol = math.sqrt(w @ np.cov(returns.T) @ w) * math.sqrt(252)
        assert port_vol <= 0.10 + 1e-3

    def test_kelly_fraction_blending(self):
        returns = _sample_returns(5, 500)
        mu = np.mean(returns, axis=0) * 252
        cov = np.cov(returns.T)
        cfg = AllocationConfig(method="minvar", kelly_fraction=0.5)
        w = allocate_portfolio(returns, cfg, expected_returns=mu)
        assert len(w) == 5
        # Kelly blending can produce weights that don't sum to 1.0 (leveraged/short)
        assert w.sum() > 0
        assert np.all(np.isfinite(w))


class TestKelly:
    def test_kelly_weights(self):
        mu = np.array([0.10, 0.05, 0.15]) / 252
        cov = np.diag([0.02, 0.03, 0.01]) ** 2
        w = kelly_weights(mu, cov, risk_free=0.02)
        assert len(w) == 3
        assert np.all(np.isfinite(w))

    def test_fractional_kelly(self):
        mu = np.array([0.10, 0.05]) / 252
        cov = np.diag([0.02, 0.03]) ** 2
        w_full = kelly_weights(mu, cov)
        w_frac = fractional_kelly(mu, cov, fraction=0.5)
        assert np.allclose(w_frac, 0.5 * w_full)


class TestFactorExposure:
    def test_factor_exposure(self):
        returns = _sample_returns(5, 500)
        factor_ret = returns[:, :2]  # first 2 assets as factors
        betas, res_var = factor_exposure(returns, factor_ret)
        assert betas.shape == (5, 2)
        assert len(res_var) == 5
        assert np.all(res_var >= 0)


class TestRegimeDetection:
    def test_hmm_regime(self):
        # Trending returns
        returns = np.cumsum(np.random.randn(200) * 0.01 + 0.001)
        returns = np.diff(returns)
        cfg = RegimeConfig(n_regimes=3, seed=42)
        state = detect_regime_hmm(returns, cfg)
        assert isinstance(state, RegimeState)
        assert state.regime_name in ("LOW_VOL", "HIGH_VOL", "TRENDING")
        assert 0 <= state.confidence <= 1

    def test_vol_clustering(self):
        # GARCH-like vol clustering
        vol = 0.01
        returns = []
        for _ in range(300):
            vol = 0.9 * vol + 0.1 * abs(np.random.randn() * 0.02)
            returns.append(np.random.randn() * vol)
        returns = np.array(returns)
        result = detect_regime_vol_clustering(returns)
        assert result["regime"] in ("LOW_VOL", "NORMAL", "HIGH_VOL")
        assert result["vol"] > 0
        assert 0 <= result["persistence"] <= 1


class TestRegimeAwareAllocation:
    def test_high_vol_reduces_risk(self):
        returns = _sample_returns(5, 500)
        high_vol_regime = RegimeState(1, "HIGH_VOL", 0.9, 0.05, 0.0)
        cfg = AllocationConfig(method="minvar", target_vol=0.15, kelly_fraction=0.25)
        w_normal = allocate_portfolio(returns, cfg)
        w_high = regime_aware_allocation(returns, high_vol_regime, cfg)
        # High vol regime should reduce weights (lower target vol, lower max weight)
        assert np.sum(np.abs(w_high)) <= np.sum(np.abs(w_normal)) + 1e-6

    def test_trending_increases_kelly(self):
        returns = _sample_returns(5, 500)
        trend_regime = RegimeState(2, "TRENDING", 0.8, 0.02, 0.5)
        cfg = AllocationConfig(method="minvar", kelly_fraction=0.2)
        w_normal = allocate_portfolio(returns, cfg)
        w_trend = regime_aware_allocation(returns, trend_regime, cfg)
        # Trending regime increases kelly fraction
        assert np.sum(np.abs(w_trend)) >= np.sum(np.abs(w_normal)) - 1e-6


import math