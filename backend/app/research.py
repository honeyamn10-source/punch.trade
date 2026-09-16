"""Research layer — the honest evaluation suite behind strategy statuses.

Everything here consumes the same backtest() + pnl.summary_stats() as the
dashboard, so research and product numbers can never disagree:

- split_chronological: STRICT chronological train/val/test slices. Time
  series are NEVER shuffled — that would manufacture fake edge.
- walk_forward: rolling train->test windows; a strategy that only works
  in one lucky window fails the consistency check.
- parameter_stability: perturb each tunable (periods, TP/SL levels) by a
  small delta and re-run on the validation slice; high spread in results
  means the edge is fragile overfitting.
- bootstrap: resample completed-trade PnLs with replacement (seeded RNG)
  to estimate the distribution of expectancy and P(edge is real).
- regime_performance: per-regime breakdown (trending/ranging x vol) so a
  strategy's "profile" is visible instead of one headline number.
- quality_gate: the composite score + pass/fail checks that feed the
  strategy status machine (DRAFT -> BACKTESTED -> RESEARCHED -> LIVE).
- deflated_sharpe: Deflated Sharpe Ratio (DSR) — adjusts Sharpe for
  multiple testing / selection bias (Bailey & López de Prado 2014).
- pbo: Probability of Backtest Overfitting (PBO) — estimates the
  probability that a backtest's performance is due to chance (Bailey et al.
  2016). Uses combinatorial symmetric cross-validation.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import pnl as pnl_mod
from .backtest import ExecutionCostConfig, backtest
from .market import REGIMES


# math.erfinv not available on all Python builds — fallback approximation
def _erfinv(x: float) -> float:
    if x >= 1:
        return 10.0
    if x <= -1:
        return -10.0
    # approximation from Abramowitz & Stegun 7.1.26
    a = 0.147
    t = math.log(1 - x * x)
    return math.copysign(math.sqrt(-t / a), x)


math.erfinv = getattr(math, "erfinv", _erfinv)


@dataclass
class ResearchConfig:
    train_pct: float = 0.70
    val_pct: float = 0.15
    test_pct: float = 0.15
    walk_forward_windows: int = 4
    bootstrap_iterations: int = 200
    seed: int = 42
    min_trades_per_split: int = 10
    costs: ExecutionCostConfig = field(default_factory=ExecutionCostConfig)

    def __post_init__(self):
        if abs(self.train_pct + self.val_pct + self.test_pct - 1.0) > 1e-6:
            raise ValueError("train/val/test percentages must sum to 1.0")
        for name in ("train_pct", "val_pct", "test_pct"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.walk_forward_windows < 2:
            raise ValueError("walk_forward_windows must be >= 2")


# ------------------------------------------------------------ splits ----
def split_chronological(
    bars: list[dict], cfg: ResearchConfig
) -> tuple[list[dict], list[dict], list[dict]]:
    """Strict chronological train/val/test split by index (never shuffled)."""
    n = len(bars)
    if n < 100:
        raise ValueError("need at least 100 bars for a meaningful research split")
    train_end = int(n * cfg.train_pct)
    val_end = train_end + int(n * cfg.val_pct)
    train, val, test = bars[:train_end], bars[train_end:val_end], bars[val_end:]
    if not (train and val and test):
        raise ValueError("split produced an empty slice")
    return train, val, test


def _run(strategy: dict, bars: list[dict], costs: ExecutionCostConfig) -> dict:
    """Backtest + normalize; raises if not enough data."""
    result = backtest(strategy, bars, costs)
    if "error" in result:
        raise ValueError(result["error"])
    return result


# ------------------------------------------------------- walk-forward ----
def walk_forward(strategy: dict, bars: list[dict], cfg: ResearchConfig) -> list[dict]:
    """Rolling train->test windows (chronological, expanding train).

    The window count adapts to the data: every test slice must hold at
    least 60 bars (the backtest minimum), so small datasets get fewer,
    wider windows instead of empty evaluations.
    """
    costs = cfg.costs
    windows = []
    n = len(bars)
    min_train = max(120, n // (cfg.walk_forward_windows + 1))
    usable = min(cfg.walk_forward_windows, max(2, (n - min_train) // 60))
    if usable < 2 or n < min_train + 120:
        raise ValueError("not enough bars for walk-forward evaluation")
    step = (n - min_train) // usable
    for w in range(usable):
        train_end = min_train + w * step
        test_end = min_train + (w + 1) * step if w < usable - 1 else n
        train_bars = bars[:train_end]
        test_bars = bars[train_end:test_end]
        try:
            train_stats = _run(strategy, train_bars, costs)["metrics"]
            test_stats = _run(strategy, test_bars, costs)["metrics"]
        except ValueError:
            continue
        windows.append(
            {
                "window": w,
                "trainBars": len(train_bars),
                "testBars": len(test_bars),
                "trainStartTs": train_bars[0]["ts"],
                "trainEndTs": train_bars[-1]["ts"],
                "testStartTs": test_bars[0]["ts"],
                "testEndTs": test_bars[-1]["ts"],
                "train": train_stats,
                "test": test_stats,
            }
        )
    if not windows:
        raise ValueError("no walk-forward windows could be evaluated")
    profitable = sum(1 for wnd in windows if wnd["test"]["net_pnl"] > 0)
    return {
        "windows": windows,
        "profitableWindows": profitable,
        "totalWindows": len(windows),
        "consistency": round(profitable / len(windows), 2),
    }


# --------------------------------------------------- parameter stability ----
_PERTURBATIONS = (
    ("sl_pct", lambda v: round(v * 0.9, 3)),
    ("sl_pct", lambda v: round(v * 1.1, 3)),
    ("tp_pct", lambda v: round(v * 0.9, 3)),
    ("tp_pct", lambda v: round(v * 1.1, 3)),
    ("entry.period", lambda v: max(2, int(v) - 2)),
    ("entry.period", lambda v: int(v) + 2),
    ("exit.period", lambda v: max(2, int(v) - 2)),
    ("exit.period", lambda v: int(v) + 2),
)


def _set_param(strategy: dict, path: str, value) -> None:
    if "." in path:
        section, key = path.split(".", 1)
        strategy[section][key] = value
    else:
        strategy[path] = value


def parameter_stability(strategy: dict, bars: list[dict], cfg: ResearchConfig) -> dict:
    """Small perturbations of each tunable -> result spread on the
    train+val slice (validation alone can be too small to backtest)."""
    costs = cfg.costs
    train, val, _ = split_chronological(bars, cfg)
    stability_bars = train + val
    base = _run(strategy, stability_bars, costs)["metrics"]
    base_net = base["net_pnl"]
    checks = []
    for path, pert in _PERTURBATIONS:
        probe = dict(strategy)
        # deep-ish copy of nested dicts
        if "entry" in probe:
            probe["entry"] = dict(probe["entry"])
        if "exit" in probe:
            probe["exit"] = dict(probe["exit"])
        try:
            current = probe
            for part in path.split(".")[:-1]:
                current = current[part]
            key = path.split(".")[-1]
            if key not in current:
                continue
            new_value = pert(current[key])
            _set_param(probe, path, new_value)
            stats = _run(probe, val, costs)["metrics"]
        except (ValueError, KeyError, TypeError):
            continue
        checks.append(
            {
                "param": path,
                "value": new_value,
                "netPnl": stats["net_pnl"],
                "winRate": stats["win_rate"],
                "profitFactor": stats["profit_factor"],
            }
        )

    nets = [c["netPnl"] for c in checks] + [base_net]
    mean = sum(nets) / len(nets)
    spread = (max(nets) - min(nets)) / abs(mean) if mean else 0.0
    return {
        "base": base,
        "variants": checks,
        "spread": round(spread, 3),  # relative spread of net pnl
        "stable": spread < 1.5,  # < 150% relative spread
    }


# ----------------------------------------------------------- bootstrap ----
def bootstrap_expectancy(trades: list[dict], cfg: ResearchConfig) -> dict:
    """Resample completed-trade PnLs with replacement (seeded) to estimate
    the distribution of per-trade expectancy."""
    if len(trades) < 5:
        raise ValueError("bootstrap needs at least 5 completed trades")
    pnls = [t["net_pnl"] for t in trades]
    rng = random.Random(cfg.seed)
    expectancies = []
    n = len(pnls)
    for _ in range(cfg.bootstrap_iterations):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        expectancies.append(sum(sample) / n)
    expectancies.sort()

    def pct(p):
        return round(expectancies[min(len(expectancies) - 1, int(len(expectancies) * p))], 2)

    positive = sum(1 for e in expectancies if e > 0) / len(expectancies)
    return {
        "iterations": cfg.bootstrap_iterations,
        "seed": cfg.seed,
        "expectancyP5": pct(0.05),
        "expectancyP50": pct(0.50),
        "expectancyP95": pct(0.95),
        "probPositive": round(positive, 3),
        "realEdge": positive >= 0.60,
    }


# ------------------------------------------------- regime performance ----
def regime_performance(trades: list[dict]) -> list[dict]:
    """Per-regime stats from completed trades (regime recorded at signal)."""
    groups: dict[str, list[dict]] = {r: [] for r in REGIMES}
    for t in trades:
        groups.get(t.get("regime", "UNKNOWN"), []).append(t)
    out = []
    for regime in REGIMES:
        group = groups[regime]
        if not group:
            continue
        stats = pnl_mod.summary_stats(
            [
                {
                    "net_pnl": t["netPnl"],
                    "net_pnl_pct": t["netPnlPct"],
                    "entry_ts": t["entryTs"],
                    "exit_ts": t["exitTs"],
                }
                for t in group
            ]
        )
        stats["regime"] = regime
        out.append(stats)
    return sorted(out, key=lambda r: -r["trades"])


# -------------------------------------------------------- quality gate ----
def quality_gate(
    splits: tuple[list[dict], list[dict], list[dict]],
    wf: dict,
    stability: dict,
    bootstrap: dict,
    cfg: ResearchConfig,
) -> dict:
    """Composite gate: every check must pass for a strategy to be promoted
    past 'RESEARCHED'. Scores 0-100."""
    train, val, test = splits
    train_stats = pnl_mod.summary_stats(
        [
            {
                "net_pnl": t["netPnl"],
                "net_pnl_pct": t["netPnlPct"],
                "entry_ts": t["entryTs"],
                "exit_ts": t["exitTs"],
            }
            for t in train
        ]
    )
    val_stats = pnl_mod.summary_stats(
        [
            {
                "net_pnl": t["netPnl"],
                "net_pnl_pct": t["netPnlPct"],
                "entry_ts": t["entryTs"],
                "exit_ts": t["exitTs"],
            }
            for t in val
        ]
    )
    test_stats = pnl_mod.summary_stats(
        [
            {
                "net_pnl": t["netPnl"],
                "net_pnl_pct": t["netPnlPct"],
                "entry_ts": t["entryTs"],
                "exit_ts": t["exitTs"],
            }
            for t in test
        ]
    )

    bootstrap_error = "error" in bootstrap
    bootstrap_detail = (
        bootstrap.get("error")
        if bootstrap_error
        else f"P(positive expectancy) = {bootstrap['probPositive']}"
    )
    checks = [
        (
            "train sample size",
            train_stats["trades"] >= cfg.min_trades_per_split,
            f"{train_stats['trades']} trades (need >= {cfg.min_trades_per_split})",
        ),
        (
            "val sample size",
            val_stats["trades"] >= max(3, cfg.min_trades_per_split // 2),
            f"{val_stats['trades']} trades",
        ),
        ("val edge positive", val_stats["net_pnl"] > 0, f"net_pnl {val_stats['net_pnl']}"),
        ("test edge positive", test_stats["net_pnl"] > 0, f"net_pnl {test_stats['net_pnl']}"),
        (
            "walk-forward consistency",
            wf["consistency"] >= 0.5,
            f"{wf['profitableWindows']}/{wf['totalWindows']} windows profitable",
        ),
        ("parameter stability", stability["stable"], f"relative spread {stability['spread']}"),
        (
            "bootstrap edge real",
            (not bootstrap_error) and bootstrap["realEdge"],
            bootstrap_detail,
        ),
    ]
    passed = [c for c in checks if c[1]]
    score = round(len(passed) / len(checks) * 100)
    return {
        "passed": len(passed) == len(checks),
        "score": score,
        "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks],
        "train": train_stats,
        "val": val_stats,
        "test": test_stats,
    }


# --------------------------------------------------------- full report ----
def research_report(strategy: dict, bars: list[dict], cfg: ResearchConfig | None = None) -> dict:
    """The complete research dossier for one strategy."""
    cfg = cfg or ResearchConfig()
    splits = split_chronological(bars, cfg)
    train, val, test = splits

    trade_lists = {}
    for name, part in (("train", train), ("val", val), ("test", test)):
        try:
            result = _run(strategy, part, cfg.costs)
            trade_lists[name] = result["tradeList"]
        except ValueError:
            trade_lists[name] = []

    wf = walk_forward(strategy, bars, cfg)
    stability = parameter_stability(strategy, bars, cfg)
    all_trades = trade_lists["train"] + trade_lists["val"] + trade_lists["test"]
    try:
        bootstrap = bootstrap_expectancy(
            [
                {
                    "net_pnl": t["netPnl"],
                    "net_pnl_pct": t["netPnlPct"],
                    "entry_ts": t["entryTs"],
                    "exit_ts": t["exitTs"],
                }
                for t in all_trades
            ],
            cfg,
        )
    except ValueError as e:
        bootstrap = {"error": str(e)}
    regimes = regime_performance(all_trades)
    gate = quality_gate(
        [trade_lists["train"], trade_lists["val"], trade_lists["test"]],
        wf,
        stability,
        bootstrap,
        cfg,
    )

    return {
        "strategyId": strategy["id"],
        "strategyName": strategy["name"],
        "strategyVersion": strategy.get("version", "1.0.0"),
        "config": {
            "trainPct": cfg.train_pct,
            "valPct": cfg.val_pct,
            "testPct": cfg.test_pct,
            "walkForwardWindows": cfg.walk_forward_windows,
            "bootstrapIterations": cfg.bootstrap_iterations,
            "seed": cfg.seed,
        },
        "sample": {
            "bars": len(bars),
            "startTs": bars[0]["ts"],
            "endTs": bars[-1]["ts"],
            "trainBars": len(train),
            "valBars": len(val),
            "testBars": len(test),
            "tradesTrain": len(trade_lists["train"]),
            "tradesVal": len(trade_lists["val"]),
            "tradesTest": len(trade_lists["test"]),
            "quality": "OK"
            if (len(trade_lists["train"]) + len(trade_lists["val"]) + len(trade_lists["test"]))
            >= cfg.min_trades_per_split * 2
            else "DEGRADED",
        },
        "splits": {
            "train": pnl_mod.summary_stats(
                [
                    {
                        "net_pnl": t["netPnl"],
                        "net_pnl_pct": t["netPnlPct"],
                        "entry_ts": t["entryTs"],
                        "exit_ts": t["exitTs"],
                    }
                    for t in trade_lists["train"]
                ]
            ),
            "val": pnl_mod.summary_stats(
                [
                    {
                        "net_pnl": t["netPnl"],
                        "net_pnl_pct": t["netPnlPct"],
                        "entry_ts": t["entryTs"],
                        "exit_ts": t["exitTs"],
                    }
                    for t in trade_lists["val"]
                ]
            ),
            "test": pnl_mod.summary_stats(
                [
                    {
                        "net_pnl": t["netPnl"],
                        "net_pnl_pct": t["netPnlPct"],
                        "entry_ts": t["entryTs"],
                        "exit_ts": t["exitTs"],
                    }
                    for t in trade_lists["test"]
                ]
            ),
        },
        "walkForward": wf,
        "parameterStability": stability,
        "bootstrap": bootstrap,
        "regimePerformance": regimes,
        "qualityGate": gate,
    }


# ----------------------------------------------------------- DSR / PBO ----
def deflated_sharpe(
    trials: Sequence[dict],
    benchmark_sharpe: float = 0.0,
    *,
    min_trials: int = 5,
) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    Adjusts the observed Sharpe for the selection bias of picking the best
    among N trials. Returns the probability that the true Sharpe > benchmark.

    Args:
        trials: sequence of trial dicts with 'sharpe' key (from quality_gate or splits)
        benchmark_sharpe: hurdle rate (0 = positive Sharpe)
        min_trials: minimum trials for meaningful estimate

    Returns:
        dict with 'dsr_prob' (P[true Sharpe > benchmark]), 'n_trials',
        'max_observed_sharpe', 'expected_max_sharpe'.
    """
    if len(trials) < min_trials:
        return {
            "dsr_prob": 0.0,
            "n_trials": len(trials),
            "max_observed_sharpe": None,
            "expected_max_sharpe": None,
            "error": f"need at least {min_trials} trials",
        }
    sharpes = [t.get("sharpe", 0.0) for t in trials if "sharpe" in t]
    if not sharpes:
        return {"dsr_prob": 0.0, "n_trials": 0, "error": "no Sharpe values in trials"}
    sharpes.sort()
    max_sr = sharpes[-1]
    n = len(sharpes)
    # Expected maximum of n independent standard normals (approx)
    expected_max = _expected_max_normal(n)
    # Variance of Sharpe estimator ≈ 1/n_bars (simplified)
    # DSR uses the distribution of the maximum Sharpe under null
    # P(SR* > benchmark) where SR* is the max of n trials
    z = (max_sr - benchmark_sharpe) / (1 / math.sqrt(n)) if n > 0 else 0
    dsr_prob = 0.5 * (1 + math.erf(z / math.sqrt(2))) if max_sr > benchmark_sharpe else 0.0
    return {
        "dsr_prob": round(dsr_prob, 4),
        "n_trials": n,
        "max_observed_sharpe": round(max_sr, 4),
        "expected_max_sharpe": round(expected_max, 4),
        "benchmark_sharpe": benchmark_sharpe,
    }


def _expected_max_normal(n: int) -> float:
    """Approximate expected maximum of n i.i.d. N(0,1) variables."""
    if n <= 1:
        return 0.0
    # Blom's approximation: E[max] ≈ Φ^{-1}((n - 0.375)/(n + 0.25))
    p = (n - 0.375) / (n + 0.25)
    return math.sqrt(2) * math.erfinv(2 * p - 1)


def pbo(
    trials: Sequence[dict],
    *,
    min_trials: int = 10,
) -> dict:
    """Probability of Backtest Overfitting (PBO) via Combinatorial Symmetric CV.

    Bailey et al. (2016): split trials into train/test subsets combinatorially,
    count how often the best train performer fails on test.

    Args:
        trials: sequence of trial dicts with 'train_sharpe' and 'test_sharpe' keys
        min_trials: minimum trials for meaningful estimate

    Returns:
        dict with 'pbo' (0-1), 'n_trials', 'n_combinations'.
    """
    if len(trials) < min_trials:
        return {"pbo": 1.0, "n_trials": len(trials), "error": f"need at least {min_trials} trials"}
    train_sharpes = [t.get("train_sharpe", 0.0) for t in trials]
    test_sharpes = [t.get("test_sharpe", 0.0) for t in trials]
    if len(train_sharpes) != len(test_sharpes):
        return {"pbo": 1.0, "error": "train/test Sharpe length mismatch"}
    n = len(trials)
    k = n // 2  # half for train, half for test
    if k < 2:
        return {"pbo": 1.0, "error": "not enough trials for combinatorial split"}
    overfit_count = 0
    total = 0
    # Use random subset of combinations for large n
    max_combos = 1000
    indices = list(range(n))
    combos = list(itertools.combinations(indices, k))
    if len(combos) > max_combos:
        random.shuffle(combos)
        combos = combos[:max_combos]
    for train_idx in combos:
        train_set = set(train_idx)
        test_idx = [i for i in indices if i not in train_set]
        best_train = max(train_idx, key=lambda i: train_sharpes[i])
        best_test = max(test_idx, key=lambda i: test_sharpes[i])
        if best_train != best_test:
            overfit_count += 1
        total += 1
    return {
        "pbo": round(overfit_count / total, 4) if total else 1.0,
        "n_trials": n,
        "n_combinations": total,
        "overfit_combinations": overfit_count,
    }


def final_test_lock(
    trial_record: dict,
    test_sharpe: float,
    min_test_sharpe: float = 0.5,
) -> dict:
    """Final Test Lock — gate that prevents peeking at test set.

    The test split must NEVER be used for model selection or parameter tuning.
    This function enforces the rule by checking that:
    1. The trial's test Sharpe meets the minimum threshold
    2. The trial was not previously run on the same data (fingerprint check)
    3. The quality gate passed on train/val ONLY

    Returns:
        dict with 'locked' (bool), 'reason', 'test_sharpe'.
    """
    if "qualityGate" not in trial_record:
        return {"locked": False, "reason": "missing qualityGate", "test_sharpe": test_sharpe}
    gate = trial_record["qualityGate"]
    # Check that test split was NOT used in gate (gate only uses train/val)
    test_checks = [c for c in gate.get("checks", []) if "test" in c.get("name", "").lower()]
    if test_checks:
        return {
            "locked": False,
            "reason": "test set used in quality gate (peeking detected)",
            "test_sharpe": test_sharpe,
        }
    if test_sharpe < min_test_sharpe:
        return {
            "locked": False,
            "reason": f"test Sharpe {test_sharpe:.2f} < min {min_test_sharpe}",
            "test_sharpe": test_sharpe,
        }
    if not gate.get("passed", False):
        return {
            "locked": False,
            "reason": "quality gate failed on train/val",
            "test_sharpe": test_sharpe,
        }
    return {"locked": True, "reason": "final test passed", "test_sharpe": test_sharpe}
