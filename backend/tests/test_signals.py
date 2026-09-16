"""Signal engine tests: deterministic identity, lifecycle states,
explanations, regime tagging, and the anti-wedge fix (AUD-017)."""

from __future__ import annotations

import pytest

from app import config
from app import signals as sig_mod
from app.engine import StrategyRunner, deterministic_signal_id
from app.market import regime_of
from app.strategies import explain_condition, get_strategy


def _bars(closes, start_ts=0):
    return [
        {
            "ts": start_ts + i,
            "open": closes[i],
            "high": closes[i] * 1.01,
            "low": closes[i] * 0.99,
            "close": closes[i],
            "volume": 1000,
        }
        for i in range(len(closes))
    ]


# ------------------------------------------------------ deterministic id --
def test_deterministic_signal_id_stable():
    a = deterministic_signal_id("rsi-reversal", "1.0.0", "RELIANCE", "5m", 100.0, "buy")
    b = deterministic_signal_id("rsi-reversal", "1.0.0", "RELIANCE", "5m", 100.0, "buy")
    assert a == b
    assert len(a) == 16
    assert a != deterministic_signal_id("rsi-reversal", "1.0.1", "RELIANCE", "5m", 100.0, "buy")
    assert a != deterministic_signal_id("rsi-reversal", "1.0.0", "RELIANCE", "5m", 101.0, "buy")
    assert a != deterministic_signal_id("rsi-reversal", "1.0.0", "RELIANCE", "5m", 100.0, "sell")


def _signal_for(strategy_id, closes, start_ts=0):
    strategy = get_strategy(strategy_id)
    runner = StrategyRunner(strategy)
    sig = None
    for i in range(1, len(closes)):
        sig = runner.on_bar(_bars(closes[: i + 1], start_ts))
        if sig:
            break
    return sig, runner


def _rsi_oversold_series():
    # long flat warmup, rally, then a drop deep enough that RSI(14) crosses
    # below 30 only after 60+ bars exist (regime classifier needs 60)
    return [100.0] * 40 + [108.0 + i for i in range(15)] + [123.0 - i * 1.0 for i in range(45)]


# ----------------------------------------------------------- fields ----
def test_signal_carries_identity_and_snapshots():
    closes = _rsi_oversold_series()
    sig, _ = _signal_for("rsi-reversal", closes)
    assert sig is not None
    d = sig.to_dict()
    assert d["strategyId"] == "rsi-reversal"
    assert d["strategyVersion"] == "1.0.0"
    assert d["timeframe"] == "5m"
    assert d["status"] == "ACTIVE"
    assert d["expiresAt"] > d["ts"]
    assert "parameterSnapshot" in d and d["parameterSnapshot"]["sl_pct"] == 1.0
    assert d["reason"] and "RSI" in d["reason"]
    assert d["regime"] in (
        "TRENDING_HIGH_VOL",
        "TRENDING_LOW_VOL",
        "RANGING_HIGH_VOL",
        "RANGING_LOW_VOL",
    )
    assert d["candleClose"] > 0 and d["closeTime"] > 0
    assert d["indicatorSnapshot"]["passed"] is True
    assert d["indicatorSnapshot"]["operator"] == "crosses_below"


def test_signal_explanation_condition():
    strat = get_strategy("rsi-reversal")
    closes = _rsi_oversold_series()
    bars = _bars(closes)
    from app.strategies import compute_indicator

    series = compute_indicator("RSI", 14, bars)
    # at the final bar RSI is deeply oversold — the *cross* fired earlier,
    # so a fixed-level comparison here should be simply "below"
    assert series[-1] is not None and series[-1] < 30
    # explanation at the firing bar (found via the runner)
    sig, _ = _signal_for("rsi-reversal", closes)
    exp = sig.to_dict()["indicatorSnapshot"]
    assert exp["passed"] is True
    assert exp["name"] == "RSI(14)"
    assert exp["operator"] == "crosses_below"
    assert isinstance(exp["value"], (int, float))
    # and a non-firing condition explains why
    exp2 = explain_condition(strat["entry"], series, 25, [b["close"] for b in bars], bars)
    assert exp2["passed"] is False


def test_signal_does_not_fire_before_warmup():
    closes = [100.0] * 10 + [108.0 + i for i in range(10)] + [100.0 - i for i in range(10)]
    sig, _ = _signal_for("sma-bounce", closes)  # warmup 50 bars
    assert sig is None  # 30 bars << 50 warmup


# ----------------------------------------------------------- states ----
def test_state_machine_legal_and_illegal():
    assert sig_mod.transition("ACTIVE", "EXECUTED") == "EXECUTED"
    assert sig_mod.transition("EXECUTED", "CLOSED") == "CLOSED"
    with pytest.raises(sig_mod.SignalStateError):
        sig_mod.transition("CLOSED", "EXECUTED")
    with pytest.raises(sig_mod.SignalStateError):
        sig_mod.transition("CANDIDATE", "CLOSED")
    assert sig_mod.is_terminal("EXPIRED")


def test_with_status_validation():
    s = {"status": "ACTIVE", "id": "x"}
    assert (
        sig_mod.with_status(s, "REJECTED", rejection="MAX_POSITIONS")["rejection"]
        == "MAX_POSITIONS"
    )
    with pytest.raises(sig_mod.SignalStateError):
        sig_mod.with_status(s, "CANDIDATE")


# ----------------------------------------------------------- regime ----
def test_regime_trending_and_ranging():
    bars = _bars([100 + i for i in range(120)])
    assert regime_of(bars).startswith("TRENDING")
    flat = _bars([100.0] * 120)
    assert regime_of(flat) in ("RANGING_LOW_VOL", "RANGING_HIGH_VOL")
    assert regime_of(_bars([100.0] * 10)) == "UNKNOWN"


# ------------------------------------------------------- anti-wedge ----
def test_active_state_resets_after_timeout(monkeypatch):
    monkeypatch.setattr(config, "EXIT_TIMEOUT_BARS", 5)
    strategy = {
        "id": "wedge-test",
        "name": "Wedge Test",
        "symbol": "X",
        "interval": "5m",
        "description": "test",
        "entry": {"indicator": "SMA", "period": 5, "condition": "crosses_above", "value": "self"},
        "exit": {
            "indicator": "SMA",
            "period": 5,
            "condition": "crosses_above",
            "value": 99999.0,
        },  # never fires
        "tp_pct": 1.0,
        "sl_pct": 1.0,
    }
    runner = StrategyRunner(strategy)
    closes = [100.0] * 12 + [110.0] * 6  # entry fires at the jump
    first = None
    for i in range(1, len(closes)):
        s = runner.on_bar(_bars(closes[: i + 1]))
        first = s if s else first
    assert first is not None
    # no exit ever fires; after EXIT_TIMEOUT_BARS the state must reset
    closes2 = closes + [115.0] * 10
    fired_again = None
    for i in range(len(closes), len(closes2)):
        s = runner.on_bar(_bars(closes2[: i + 1]))
        fired_again = s if s else fired_again
    assert fired_again is not None
    # and a *second* wedge signal is not the same deterministic signal
    assert fired_again.id != first.id
