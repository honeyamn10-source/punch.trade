"""Strategy lifecycle status tests: legal promotions, composite score
components (never win-rate-only), and drift detection."""

from __future__ import annotations

import pytest

from app.strategy_status import (
    BACKTESTED,
    DISABLED,
    DRAFT,
    LIVE_ACTIVE,
    LIVE_DEGRADED,
    RESEARCHED,
    StatusError,
    can_promote,
    composite_score,
    compute_status,
    live_drift,
    transition,
)


def test_legal_promotions():
    assert can_promote(DRAFT, BACKTESTED)
    assert can_promote(BACKTESTED, RESEARCHED)
    assert can_promote(RESEARCHED, LIVE_ACTIVE)
    assert can_promote(LIVE_ACTIVE, LIVE_DEGRADED)
    assert can_promote(LIVE_DEGRADED, DISABLED)
    assert not can_promote(DRAFT, LIVE_ACTIVE)
    assert not can_promote(DISABLED, RESEARCHED)
    with pytest.raises(StatusError):
        transition(DRAFT, LIVE_ACTIVE)


def test_disable_is_terminal():
    assert transition(LIVE_ACTIVE, LIVE_DEGRADED) == LIVE_DEGRADED
    assert transition(LIVE_DEGRADED, DISABLED) == DISABLED
    with pytest.raises(StatusError):
        transition(DISABLED, DRAFT)


def test_composite_score_components_visible():
    research = {
        "qualityGate": {"score": 80, "passed": True},
        "parameterStability": {"stable": True},
    }
    c = composite_score(research, None)
    assert c["score"] == 78  # 0.6*80 + 0.2*100 + 0.2*50
    assert set(c["components"]) == {"qualityGate", "parameterStability", "liveDrift"}
    # win rate alone can't push a failing gate up
    bad = composite_score(None, {"trades": 5, "health": 100.0})
    assert bad["score"] == 20  # only drift component at 100*0.2


def test_live_drift_detection():
    baseline = {"expectancy": 100.0}
    trades = [{"netPnl": 10.0, "netPnlPct": 0.1, "entryTs": 0, "exitTs": 1}] * 5
    d = live_drift(baseline, trades)
    assert d["degraded"] is True  # 10 vs 100 -> ratio 0.1 < 0.5
    assert d["health"] == 10.0
    ok = [{"netPnl": 90.0, "netPnlPct": 0.9, "entryTs": 0, "exitTs": 1}] * 5
    assert live_drift(baseline, ok)["degraded"] is False


def test_live_drift_insufficient_data_is_neutral():
    d = live_drift(
        {"expectancy": 50.0}, [{"netPnl": 5.0, "netPnlPct": 0.05, "entryTs": 0, "exitTs": 1}] * 2
    )
    assert d["degraded"] is False
    assert d["health"] == 50.0


def test_compute_status_research_gate():
    research = {
        "qualityGate": {"passed": True, "score": 90},
        "parameterStability": {"stable": True},
    }
    st = compute_status("s1", DRAFT, has_backtest=True, research=research, drift=None)
    assert st["status"] == RESEARCHED
    assert st["score"]["score"] > 0
    assert LIVE_ACTIVE in st["canPromoteTo"]


def test_compute_status_disabled_override():
    st = compute_status("s1", DISABLED, has_backtest=True, research=None, drift=None)
    assert st["status"] == DISABLED


def test_compute_status_drift_degrades():
    research = {
        "qualityGate": {"passed": True, "score": 90},
        "parameterStability": {"stable": True},
    }
    drift = {"degraded": True, "reason": "drift below threshold", "trades": 10, "health": 10.0}
    st = compute_status("s1", LIVE_ACTIVE, has_backtest=True, research=research, drift=drift)
    assert st["status"] == LIVE_DEGRADED
