"""Risk extensions: fixed-fractional sizing, circuit breaker,
reconciliation gate."""

from __future__ import annotations

import pytest

from app import config
from app import risk


@pytest.fixture(autouse=True)
def _reset():
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)
    yield
    risk.reset_breaker()
    risk.set_reconciliation_ok(True)


# -------------------------------------------------------------- sizing ----
def test_size_position_fixed_fractional():
    out = risk.size_position(equity=10_000, risk_pct=0.01,
                             entry=100.0, stop=99.0)
    assert out["qty"] == 100          # 100 risk / 1.0 distance
    assert out["riskAmount"] == 100.0
    assert out["riskPerShare"] == 1.0


def test_size_position_caps_at_max_qty(monkeypatch):
    monkeypatch.setattr(config, "MAX_QTY", 50)
    out = risk.size_position(equity=10_000, risk_pct=0.10,
                             entry=100.0, stop=99.0)
    assert out["qty"] == 50


def test_size_position_validates_inputs():
    with pytest.raises(risk.RiskError) as e:
        risk.size_position(equity=-1, risk_pct=0.01, entry=100, stop=99)
    assert e.value.code == "INVALID_EQUITY"
    with pytest.raises(risk.RiskError):
        risk.size_position(equity=1000, risk_pct=0, entry=100, stop=99)
    with pytest.raises(risk.RiskError):
        risk.size_position(equity=1000, risk_pct=0.01, entry=100, stop=100)


# --------------------------------------------------------- breaker --------
def test_breaker_opens_after_consecutive_losses(monkeypatch):
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_LOSSES", 3)
    for _ in range(2):
        risk.record_trade_result(win=False)
        assert not risk.breaker_open()
    risk.record_trade_result(win=False)
    assert risk.breaker_open()
    assert risk.consecutive_losses() == 3
    with pytest.raises(risk.RiskError) as e:
        risk.check_circuit_breaker()
    assert e.value.code == "CIRCUIT_BREAKER"


def test_breaker_resets_on_win(monkeypatch):
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_LOSSES", 2)
    risk.record_trade_result(win=False)
    risk.record_trade_result(win=False)
    assert risk.breaker_open()
    risk.record_trade_result(win=True)
    assert not risk.breaker_open()
    assert risk.consecutive_losses() == 0


def test_breaker_reset_endpoint_logic():
    risk.record_trade_result(win=False)
    risk.record_trade_result(win=False)
    out = risk.reset_breaker()
    assert out["breakerOpen"] is False and out["consecutiveLosses"] == 0


def test_breaker_gates_paper_orders():
    risk.set_mode("paper")
    risk.record_trade_result(win=False)
    risk.record_trade_result(win=False)
    risk.record_trade_result(win=False)
    with pytest.raises(risk.RiskError) as e:
        risk.check(broker="paper", signal=None, signal_ts=None)
    assert e.value.code == "CIRCUIT_BREAKER"


# ------------------------------------------------------- reconciliation ----
def test_reconciliation_gate_blocks_live_orders(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "a-real-token-12345")
    risk.set_mode("paper")
    risk.set_reconciliation_ok(False)
    risk.check(broker="paper", signal=None, signal_ts=None)  # paper unaffected
    risk.set_mode("live")
    risk.arm("kite", connected=True)
    with pytest.raises(risk.RiskError) as e:
        risk.check(broker="kite", signal=None, signal_ts=None)
    assert e.value.code == "RECONCILIATION_FAILED"


def test_reconciliation_gate_passes_when_ok():
    risk.set_mode("paper")
    risk.set_reconciliation_ok(True)
    risk.check(broker="paper", signal=None, signal_ts=None)


def test_status_includes_risk_state():
    s = risk.status()
    assert "consecutiveLosses" in s and "breakerOpen" in s
    assert "reconciliationOk" in s