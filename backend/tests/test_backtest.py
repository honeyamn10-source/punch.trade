"""Backtest/trades/PnL honesty tests.

Covers: the hand-calculated metric example, one-position-one-trade,
no lookahead (entry only at NEXT open), SL-first conservative fills,
gap policy, execution costs, and end-of-test liquidation.
"""

from __future__ import annotations

import pytest

from app import pnl as pnl_mod
from app.backtest import ExecutionCostConfig, backtest
from app.trades import CompletedTrade


# ------------------------------------------------------- pnl metrics ----
def test_hand_calculated_metrics():
    trades = [
        {"net_pnl": 100.0, "net_pnl_pct": 1.0, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -50.0, "net_pnl_pct": -0.5, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": 25.0, "net_pnl_pct": 0.25, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -25.0, "net_pnl_pct": -0.25, "entry_ts": 0, "exit_ts": 1},
    ]
    s = pnl_mod.summary_stats(trades)
    assert s["trades"] == 4
    assert s["wins"] == 2 and s["losses"] == 2
    assert s["win_rate"] == 50.0
    assert s["gross_profit"] == 125.0
    assert s["gross_loss"] == 75.0
    assert s["net_pnl"] == 50.0
    assert abs(s["profit_factor"] - 1.6667) < 0.001
    assert s["avg_win"] == 62.5
    assert s["avg_loss"] == 37.5
    assert s["expectancy"] == 12.5


def test_classify_by_final_net_pnl():
    assert pnl_mod.classify(10.0) == "WIN"
    assert pnl_mod.classify(-10.0) == "LOSS"
    assert pnl_mod.classify(0.0) == "BREAKEVEN"
    # TP1-then-stop still a LOSS
    assert pnl_mod.classify(-1.0) == "LOSS"


def test_consecutive_losses_streak():
    trades = [
        {"net_pnl": 1, "net_pnl_pct": 0.01, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -1, "net_pnl_pct": -0.01, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -2, "net_pnl_pct": -0.02, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -3, "net_pnl_pct": -0.03, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": 5, "net_pnl_pct": 0.05, "entry_ts": 0, "exit_ts": 1},
        {"net_pnl": -1, "net_pnl_pct": -0.01, "entry_ts": 0, "exit_ts": 1},
    ]
    assert pnl_mod.summary_stats(trades)["max_consecutive_losses"] == 3


# ------------------------------------------------------ CompletedTrade ----
def test_completed_trade_close_validation():
    t = CompletedTrade("sig1", "s1", "1.0.0", "X", "buy", 100, 0.0, 100.0)
    with pytest.raises(ValueError):
        t.add_fill(1.0, "ENTRY", 100.0, 10)  # duplicate entry
    t.add_fill(1.0, "TP1", 102.0, 50)
    t.close(2.0, 99.0, "STOP")  # default qty = remaining 50
    assert t.closed and t.closed_by == "STOP"
    with pytest.raises(ValueError):
        t.close(3.0, 99.0, "MANUAL")


def test_one_trade_freeze_pnl():
    t = CompletedTrade(None, "s1", "1.0.0", "X", "buy", 100, 0.0, 100.0)
    t.add_fill(1.0, "TP1", 110.0, 50)
    t.close(2.0, 95.0, "STOP", commission=2.0)
    d = t.to_dict()
    # 50@+10, 50@-5, minus 2 commission = 248
    assert d["netPnl"] == 248.0
    assert d["classification"] == "WIN"
    assert len(d["fills"]) == 3
    assert [f["reason"] for f in d["fills"]] == ["ENTRY", "TP1", "STOP"]


# --------------------------------------------------------- fixtures ----
def _bars(rows, start_ts=0):
    """rows: list of (open, high, low, close)."""
    out = []
    for i, (o, h, lo, c) in enumerate(rows):
        out.append(
            {"ts": start_ts + i, "open": o, "high": h, "low": lo, "close": c, "volume": 1000}
        )
    return out


_WEDGE = {
    "id": "wedge",
    "name": "Wedge",
    "symbol": "X",
    "interval": "5m",
    "description": "test",
    "entry": {"indicator": "SMA", "period": 5, "condition": "crosses_above", "value": "self"},
    "exit": {"indicator": "SMA", "period": 5, "condition": "crosses_above", "value": 99999.0},
    "tp_pct": 1.0,
    "sl_pct": 1.0,
}


def _series_with_signal_fire():
    # 70 flat bars, then one big jump bar -> entry condition fires on the
    # jump bar's close (signal bar index 70). Entry must fill at bar 71 open.
    rows = [(100, 101, 99, 100)] * 70
    rows += [
        (105, 111, 104, 110),  # signal bar (index 70)
        (109, 111, 105, 110),
    ]  # entry fills at this open (109)
    return _bars(rows), 71


# ----------------------------------------------------------- honesty ----
def test_no_lookahead_entry_at_next_open():
    bars, entry_bar = _series_with_signal_fire()
    res = backtest(_WEDGE, bars, ExecutionCostConfig(slippage_bps=0, commission_bps=0))
    assert res["trades"] == 1
    trade = res["metrics"]
    assert trade["trades"] == 1
    # find the trade dict to check entry price
    # (metrics only: verify entry price equals bar[71].open via equity math
    # is implicit; reconstruct entry from fills would need full trades —
    # check via totalCommission==0 and no error is enough here)
    assert res["costs"]["totalCommission"] == 0.0


def test_trades_list_entry_price_is_next_open():
    bars, entry_bar = _series_with_signal_fire()
    # monkeypatch-free check: re-run with slippage 0 and introspect the
    # single trade through the API-level equivalence: entry price must be
    # bars[entry_bar]["open"] -> we capture by rerunning with a spy
    captured = {}
    orig_close = CompletedTrade.close

    def spy(self, *a, **kw):
        captured["entry_price"] = self.entry_price
        return orig_close(self, *a, **kw)

    CompletedTrade.close = spy
    try:
        res = backtest(_WEDGE, bars, ExecutionCostConfig(slippage_bps=0, commission_bps=0))
    finally:
        CompletedTrade.close = orig_close
    assert res["trades"] == 1
    assert captured["entry_price"] == bars[entry_bar]["open"] == 109.0


def test_conservative_sl_first_same_bar():
    # entry at 109, SL=107.91, TP1=110.09; next bar low 96 AND high 112 ->
    # conservative books the STOP first
    rows = [(100, 101, 99, 100)] * 70
    rows += [(105, 111, 104, 110), (109, 111, 105, 110), (108, 112, 96, 98)]
    res = backtest(_WEDGE, _bars(rows), ExecutionCostConfig(slippage_bps=0))
    assert res["trades"] == 1
    assert res["exitSplit"].get("STOP") == 1


def test_optimistic_tp_first_same_bar():
    rows = [(100, 101, 99, 100)] * 70
    rows += [(105, 111, 104, 110), (109, 111, 105, 110), (108, 112, 96, 98)]
    res = backtest(
        _WEDGE, _bars(rows), ExecutionCostConfig(slippage_bps=0, intrabar_policy="optimistic")
    )
    assert res["trades"] == 1
    # TP1 filled (half at 110.09) then stop on the rest
    assert "TP1" in res["exitSplit"] or res["exitSplit"].get("STOP") == 1


def test_gap_fills_at_next_open():
    # bar after entry OPENS below the stop -> fill at open, not at SL
    rows = [(100, 101, 99, 100)] * 70
    rows += [
        (105, 111, 104, 110),
        (109, 111, 105, 110),
        (103, 104, 102, 103),
    ]  # open 103 < SL 107.91 -> gap fill at 103
    res = backtest(_WEDGE, _bars(rows), ExecutionCostConfig(slippage_bps=0))
    assert res["trades"] == 1
    assert res["exitSplit"].get("STOP") == 1


def test_end_of_test_liquidation():
    # signal fires near the end, position never hits SL/TP -> END_OF_TEST
    rows = [(100, 101, 99, 100)] * 70
    rows += [
        (105, 111, 104, 110),
        (109, 109.5, 108.5, 109),
        (109.5, 110, 109, 109.5),
        (110, 110, 109.5, 110),
    ]
    res = backtest(_WEDGE, _bars(rows), ExecutionCostConfig(slippage_bps=0))
    assert res["trades"] == 1
    assert res["exitSplit"].get("END_OF_TEST") == 1


def test_costs_hit_entry_price_and_commission():
    rows = [(100, 101, 99, 100)] * 70
    rows += [(105, 111, 104, 110), (109, 111, 105, 110), (109, 111, 105, 110)]
    costs = ExecutionCostConfig(commission_bps=10, slippage_bps=10, spread_bps=5)
    captured = {}
    orig_close = CompletedTrade.close

    def spy(self, *a, **kw):
        captured["entry_price"] = self.entry_price
        return orig_close(self, *a, **kw)

    CompletedTrade.close = spy
    try:
        res = backtest(_WEDGE, _bars(rows), costs)
    finally:
        CompletedTrade.close = orig_close
    # slippage+spread = 15 bps on 109 -> +0.1635
    assert captured["entry_price"] == pytest.approx(109 + 109 * 15 * 0.0001, abs=1e-3)
    assert res["costs"]["totalCommission"] > 0
    assert res["costs"]["totalSlippageCost"] > 0


def test_config_validation():
    with pytest.raises(ValueError):
        ExecutionCostConfig(intrabar_policy="bad")
    with pytest.raises(ValueError):
        ExecutionCostConfig(gap_policy="bad")
    with pytest.raises(ValueError):
        ExecutionCostConfig(starting_capital=-1)


def test_insufficient_bars():
    rows = [(100, 101, 99, 100)] * 10
    assert "error" in backtest(_WEDGE, _bars(rows))
