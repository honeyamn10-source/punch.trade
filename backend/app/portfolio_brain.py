"""Portfolio Brain — capital allocation, risk budgeting, regime awareness.

Provides:
- Correlation-aware allocation (HRP / min-variance / risk parity)
- Volatility targeting (constant portfolio vol)
- Kelly / fractional Kelly sizing
- Regime detection (HMM on returns + vol clustering)
- Factor exposure analysis
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage


# --------------------------------------------------------------- utils ----
def _cov_to_corr(cov: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.diag(cov))
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = cov / np.outer(d, d)
    np.fill_diagonal(corr, 1.0)
    return np.nan_to_num(corr, nan=0.0)


def _pseudo_inverse(mat: np.ndarray, rcond: float = 1e-10) -> np.ndarray:
    """Moore-Penrose pseudo-inverse via SVD (handles singular cov)."""
    u, s, vt = np.linalg.svd(mat, full_matrices=False)
    s_inv = np.where(s > rcond * s[0], 1.0 / s, 0.0)
    return (vt.T * s_inv) @ u.T


# ---------------------------------------------------- regime detection ----
@dataclass
class RegimeConfig:
    """Configuration for regime detection."""

    n_regimes: int = 3
    lookback: int = 252
    min_bars: int = 100
    seed: int = 42


@dataclass
class RegimeState:
    """Current regime classification."""

    regime_id: int
    regime_name: str
    confidence: float
    vol_estimate: float
    trend: float  # -1 to 1


def detect_regime_hmm(returns: np.ndarray, cfg: RegimeConfig | None = None) -> RegimeState:
    """Gaussian HMM regime detection on returns (2-3 regimes).

    Simplified implementation: K-means on (mean, vol) rolling windows
    with Viterbi-like smoothing. For production use hmmlearn.
    """
    cfg = cfg or RegimeConfig()
    if len(returns) < cfg.min_bars:
        return RegimeState(0, "UNKNOWN", 0.0, float(np.std(returns)), 0.0)

    # Rolling features: mean return, volatility
    window = min(60, len(returns) // 4)
    feats = []
    for i in range(window, len(returns)):
        w = returns[i - window : i]
        feats.append([float(np.mean(w)), float(np.std(w))])
    feats = np.array(feats)

    # K-means clustering
    rng = np.random.RandomState(cfg.seed)
    centroids = feats[rng.choice(len(feats), cfg.n_regimes, replace=False)]
    for _ in range(20):
        dists = np.linalg.norm(feats[:, None] - centroids[None, :], axis=2)
        labels = np.argmin(dists, axis=1)
        new_centroids = np.array([feats[labels == k].mean(axis=0) for k in range(cfg.n_regimes)])
        if np.allclose(centroids, new_centroids):
            break
        centroids = new_centroids

    # Smooth with last few labels
    recent = labels[-10:]
    smoothed = max(set(recent), key=list(recent).count)

    centroid = centroids[smoothed]
    if centroid[1] > np.median(centroids[:, 1]):
        name = "HIGH_VOL"
    elif abs(centroid[0]) > np.median(np.abs(centroids[:, 0])):
        name = "TRENDING"
    else:
        name = "LOW_VOL"

    confidence = float(np.mean(recent == smoothed))
    return RegimeState(
        regime_id=int(smoothed),
        regime_name=name,
        confidence=confidence,
        vol_estimate=float(centroid[1]),
        trend=float(np.clip(centroid[0] / (centroid[1] + 1e-8), -1, 1)),
    )


def detect_regime_vol_clustering(returns: np.ndarray, window: int = 20) -> dict:
    """Volatility clustering regime (GARCH-like)."""
    if len(returns) < window:
        return {"regime": "UNKNOWN", "vol": float(np.std(returns)), "persistence": 0.0}
    vol = np.array([np.std(returns[max(0, i - window) : i]) for i in range(len(returns))])
    current_vol = vol[-1]
    avg_vol = np.mean(vol)
    # Persistence: correlation of vol with lagged vol
    if len(vol) > 2 and np.std(vol[:-1]) > 1e-8 and np.std(vol[1:]) > 1e-8:
        persistence = float(np.corrcoef(vol[:-1], vol[1:])[0, 1])
    else:
        persistence = 0.0
    regime = "HIGH_VOL" if current_vol > 1.5 * avg_vol else ("LOW_VOL" if current_vol < 0.5 * avg_vol else "NORMAL")
    return {"regime": regime, "vol": float(current_vol), "avg_vol": float(avg_vol), "persistence": persistence}


# ----------------------------------------------------------- allocation ----
@dataclass
class AllocationConfig:
    """Portfolio allocation configuration."""

    method: str = "hrp"  # "hrp" | "minvar" | "riskparity" | "equal"
    target_vol: float | None = None  # annualized vol target (e.g., 0.15)
    max_weight: float = 0.3
    min_weight: float = 0.0
    kelly_fraction: float = 0.25  # fractional Kelly
    risk_free: float = 0.02


def allocate_hrp(cov: np.ndarray, corr: np.ndarray | None = None) -> np.ndarray:
    """Hierarchical Risk Parity (Lopez de Prado 2016).

    Recursive bipartitioning of correlation matrix.
    """
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    if corr is None:
        corr = _cov_to_corr(cov)

    # Distance matrix
    dist = np.sqrt(0.5 * (1 - corr))
    np.fill_diagonal(dist, 0.0)

    link = linkage(dist, method="single")
    order = leaves_list(link)

    # Recursive bisection
    weights = np.ones(n)

    def bisect(idx: list[int], w: float):
        if len(idx) == 1:
            weights[idx[0]] = w
            return
        # Split by cluster variance
        sub_cov = cov[np.ix_(idx, idx)]
        sub_corr = _cov_to_corr(sub_cov)
        sub_dist = np.sqrt(0.5 * (1 - sub_corr))
        sub_link = linkage(sub_dist, method="single")
        sub_order = leaves_list(sub_link)
        mid = len(idx) // 2
        left_idx = [idx[i] for i in sub_order[:mid]]
        right_idx = [idx[i] for i in sub_order[mid:]]
        # Variance of each cluster
        left_var = np.sum(sub_cov[np.ix_(sub_order[:mid], sub_order[:mid])])
        right_var = np.sum(sub_cov[np.ix_(sub_order[mid:], sub_order[mid:])])
        left_w = w * (1 / (left_var + 1e-8)) / (1 / (left_var + 1e-8) + 1 / (right_var + 1e-8))
        right_w = w - left_w
        bisect(left_idx, left_w)
        bisect(right_idx, right_w)

    bisect(list(range(n)), 1.0)
    # Reorder to original asset order
    return weights[np.argsort(order)]


def allocate_minvar(cov: np.ndarray, max_w: float = 1.0) -> np.ndarray:
    """Minimum variance portfolio (unconstrained, then clip)."""
    inv = _pseudo_inverse(cov)
    ones = np.ones(cov.shape[0])
    w = inv @ ones / (ones @ inv @ ones)
    w = np.clip(w, 0, max_w)
    if w.sum() > 0:
        w = w / w.sum()
    return w


def allocate_risk_parity(cov: np.ndarray, max_w: float = 1.0) -> np.ndarray:
    """Risk parity (equal risk contribution) via iterative solver."""
    n = cov.shape[0]
    w = np.ones(n) / n
    for _ in range(1000):
        rc = w * (cov @ w)  # risk contribution
        target = rc.mean()
        if np.max(np.abs(rc - target)) < 1e-6:
            break
        w = w * (target / (rc + 1e-8))
        w = np.clip(w, 0, max_w)
        if w.sum() > 0:
            w = w / w.sum()
    return w


def allocate_equal(n: int) -> np.ndarray:
    return np.ones(n) / n


def allocate_portfolio(
    returns: np.ndarray,
    cfg: AllocationConfig,
    expected_returns: np.ndarray | None = None,
) -> np.ndarray:
    """Main allocation dispatcher.

    Args:
        returns: (T, N) array of asset returns
        cfg: AllocationConfig
        expected_returns: optional expected returns for Kelly sizing

    Returns:
        (N,) weights array
    """
    cov = np.cov(returns.T)
    n = cov.shape[0]

    if cfg.method == "hrp":
        w = allocate_hrp(cov)
    elif cfg.method == "minvar":
        w = allocate_minvar(cov, cfg.max_weight)
    elif cfg.method == "riskparity":
        w = allocate_risk_parity(cov, cfg.max_weight)
    else:
        w = allocate_equal(n)

    # Apply Kelly fractional sizing if expected returns provided
    if expected_returns is not None and cfg.kelly_fraction > 0:
        kelly_w = kelly_weights(expected_returns, cov, cfg.risk_free)
        w = (1 - cfg.kelly_fraction) * w + cfg.kelly_fraction * kelly_w

    # Vol targeting
    if cfg.target_vol is not None:
        port_vol = math.sqrt(w @ cov @ w) * math.sqrt(252)
        if port_vol > 0:
            scale = min(1.0, cfg.target_vol / port_vol)
            w = w * scale

    return np.clip(w, cfg.min_weight, cfg.max_weight)


# --------------------------------------------------------------- Kelly ----
def kelly_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    risk_free: float = 0.0,
    max_leverage: float = 1.0,
) -> np.ndarray:
    """Full Kelly weights (unconstrained)."""
    excess = mu - risk_free / 252
    inv = _pseudo_inverse(cov)
    w = inv @ excess
    # Scale to max leverage
    if np.sum(np.abs(w)) > max_leverage:
        w = w * max_leverage / np.sum(np.abs(w))
    return w


def fractional_kelly(
    mu: np.ndarray,
    cov: np.ndarray,
    fraction: float = 0.25,
    risk_free: float = 0.0,
) -> np.ndarray:
    """Fractional Kelly."""
    return fraction * kelly_weights(mu, cov, risk_free)


# -------------------------------------------------------- factor models ----
def factor_exposure(
    returns: np.ndarray,
    factor_returns: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """OLS factor betas and residual variance.

    Args:
        returns: (T, N) asset returns
        factor_returns: (T, K) factor returns

    Returns:
        betas: (N, K), residual_var: (N,)
    """
    T, N = returns.shape
    X = np.column_stack([np.ones(T), factor_returns])
    # lstsq returns (K+1, N) coeffs (including intercept)
    coeffs = np.linalg.lstsq(X, returns, rcond=None)[0]
    betas = coeffs[1:]  # (K, N)
    residuals = returns - X @ coeffs
    res_var = np.var(residuals, axis=0)
    return betas.T, res_var


# -------------------------------------------------------- regime allocation ---
def regime_aware_allocation(
    returns: np.ndarray,
    regime: RegimeState,
    base_cfg: AllocationConfig,
) -> np.ndarray:
    """Adjust allocation based on detected regime."""
    cfg = AllocationConfig(
        method=base_cfg.method,
        target_vol=base_cfg.target_vol,
        max_weight=base_cfg.max_weight,
        min_weight=base_cfg.min_weight,
        kelly_fraction=base_cfg.kelly_fraction,
        risk_free=base_cfg.risk_free,
    )
    if regime.regime_name == "HIGH_VOL":
        cfg.target_vol = cfg.target_vol * 0.5 if cfg.target_vol else None
        cfg.max_weight = min(cfg.max_weight, 0.2)
        cfg.kelly_fraction *= 0.5
    elif regime.regime_name == "TRENDING":
        cfg.kelly_fraction = min(cfg.kelly_fraction * 1.5, 0.5)
    return allocate_portfolio(returns, cfg)
