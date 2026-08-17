"""Research layer tests: chronological splits (never shuffled),
walk-forward consistency, parameter stability, seeded bootstrap,
regime grouping, sample quality and the composite quality gate."""

from __future__ import annotations

import pytest

from app import research
from app.backtest import ExecutionCostConfig
from app.market import REGIMES
from app.research import (ResearchConfig, bootstrap_expectancy, quality_gate,
                          research_report, split_chronological,
                          walk_forward)
from tests.test_core import RSI_STRAT, _bars, _rsi_cross_series


def _long_series(n_cycles=8):
    closes = []
    for _ in range(n_cycles):
        closes += _rsi_cross_series()
    return _bars(closes)


CFG = ResearchConfig(costs=ExecutionCostConfig(position_pct=0.1))


# ------------------------------------------------------------- splits ----
def test_split_chronological_strict_order():
    bars = _long_series()
    train, val, test = split_chronological(bars, CFG)
    assert len(train) + len(val) + len(test) == len(bars)
    assert train and val and test
    # chronological adjacency, no overlap, no shuffle
    assert train[-1]["ts"] < val[0]["ts"]
    assert val[-1]["ts"] < test[0]["ts"]
    assert train[0]["ts"] < train[-1]["ts"]


def test_split_too_small():
    with pytest.raises(ValueError):
        split_chronological(_bars([100.0] * 50), CFG)


def test_split_percentages_validated():
    with pytest.raises(ValueError):
        ResearchConfig(train_pct=0.5, val_pct=0.5, test_pct=0.1)


# ------------------------------------------------------- walk forward ----
def test_walk_forward_windows_are_chronological():
    bars = _long_series(12)
    wf = walk_forward(RSI_STRAT, bars, CFG)
    assert wf["totalWindows"] >= 2
    for wnd in wf["windows"]:
        assert wnd["trainEndTs"] <= wnd["testStartTs"]
        assert wnd["train"]["trades"] >= 0
        assert wnd["test"]["trades"] >= 0
    assert 0 <= wf["consistency"] <= 1


# ---------------------------------------------------- parameter stability ----
def test_parameter_stability_reports_spread():
    bars = _long_series(12)
    st = research.parameter_stability(RSI_STRAT, bars, CFG)
    assert "base" in st and st["base"]["trades"] >= 0
    assert len(st["variants"]) > 0
    params = {v["param"] for v in st["variants"]}
    assert "sl_pct" in params and "entry.period" in params
    assert isinstance(st["stable"], bool)
    assert st["spread"] >= 0


# ----------------------------------------------------------- bootstrap ----
def test_bootstrap_seeded_deterministic():
    trades = [{"net_pnl": p, "net_pnl_pct": p / 100.0, "entry_ts": 0, "exit_ts": 1}
              for p in (100, -50, 25, -25, 40, -30, 60, -20, 15, -10)]
    a = bootstrap_expectancy(trades, CFG)
    b = bootstrap_expectancy(trades, CFG)
    assert a == b  # same seed -> same result
    assert 0 <= a["probPositive"] <= 1
    assert a["expectancyP5"] <= a["expectancyP50"] <= a["expectancyP95"]


def test_bootstrap_all_wins_edge_real():
    trades = [{"net_pnl": 50.0, "net_pnl_pct": 0.5, "entry_ts": 0, "exit_ts": 1}] * 20
    b = bootstrap_expectancy(trades, CFG)
    assert b["probPositive"] == 1.0
    assert b["realEdge"] is True


def test_bootstrap_too_few_trades():
    with pytest.raises(ValueError):
        bootstrap_expectancy([{"net_pnl": 1.0, "net_pnl_pct": 0.01,
                               "entry_ts": 0, "exit_ts": 1}] * 3, CFG)


# ------------------------------------------------------------ regimes ----
def test_regime_performance_groups_trades():
    bars = _long_series()
    result = research.research_report(RSI_STRAT, bars, CFG)
    regimes = result["regimePerformance"]
    total = sum(r["trades"] for r in regimes)
    assert total == result["sample"]["tradesTrain"] + \
        result["sample"]["tradesVal"] + result["sample"]["tradesTest"]
    for r in regimes:
        assert r["regime"] in REGIMES


# -------------------------------------------------------- full report ----
def test_research_report_full_shape():
    bars = _long_series(12)
    rep = research_report(RSI_STRAT, bars, CFG)
    assert rep["strategyId"] == "t"
    assert rep["sample"]["bars"] == len(bars)
    assert rep["sample"]["quality"] in ("OK", "DEGRADED")
    for key in ("train", "val", "test"):
        assert "net_pnl" in rep["splits"][key]
    assert rep["walkForward"]["totalWindows"] >= 2
    assert "bootstrap" in rep
    assert "qualityGate" in rep


def test_quality_gate_scores():
    bars = _long_series(12)
    rep = research_report(RSI_STRAT, bars, CFG)
    gate = rep["qualityGate"]
    assert 0 <= gate["score"] <= 100
    assert len(gate["checks"]) >= 5
    assert all("passed" in c and "name" in c and "detail" in c
               for c in gate["checks"])
    # every check must be boolean
    assert all(isinstance(c["passed"], bool) for c in gate["checks"])


def test_quality_gate_fails_empty_sample():
    train = val = test = []
    wf = {"consistency": 0.0, "profitableWindows": 0, "totalWindows": 4}
    stability = {"stable": False, "spread": 9.9}
    bootstrap = {"realEdge": False, "probPositive": 0.1}
    gate = quality_gate([train, val, test], wf, stability, bootstrap, CFG)
    assert gate["passed"] is False
    assert gate["score"] == 0