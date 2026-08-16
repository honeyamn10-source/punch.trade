"""Backtester — replays the same StrategyRunner against historical bars.

Fill model (conservative):
- Signal fires on bar close; entry fills at that close.
- Multi-TP: each TP level closes an equal fraction of the position when
  the bar's high touches it. If a bar touches a TP and the SL in the
  same bar, the SL is counted first (worst case).
- Drawdown is measured on the equity curve (cumulative PnL in %).

Metrics produced: win rate, net return, max drawdown, Sharpe
(per-trade, non-annualized), profit factor, average win/loss.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from .engine import StrategyRunner
from .strategies import target_levels


def backtest(strategy: Dict, bars: List[dict]) -> Dict:
    """bars: list of bar dicts with open/high/low/close (oldest first)."""
    if len(bars) < 60:
        return {"error": "Not enough historical bars to backtest", "trades": 0}

    runner = StrategyRunner(strategy)
    levels = target_levels(strategy)
    n_levels = len(levels)
    exits: List[dict] = []  # per-exit events
    position = None  # {"entry", "sl", "targets": [float], "remaining": float, "bar": int, "tp_filled": int}

    def realize(position, price, label, bar_idx):
        pnl = (price - position["entry"]) / position["entry"] * 100
        exits.append({"exit": label, "pnl_pct": round(pnl, 3), "bars_held": bar_idx - position["bar"]})

    for i in range(1, len(bars)):
        bar = bars[i]
        if position is not None:
            # SL first (conservative) — closes whatever remains
            if bar["low"] <= position["sl"]:
                realize(position, position["sl"], "SL", i)
                position = None
                continue
            # multi-TP: equal fractions per level, one level per bar
            # (conservative — a bar that touches several levels books the
            # first one; SL is always booked first)
            for idx, tp in enumerate(position["targets"]):
                if bar["high"] >= tp and position is not None:
                    position["tp_filled"] += 1
                    realize(position, tp, f"TP{position['tp_filled']}", i)
                    position["remaining"] -= 1.0 / n_levels
                    position["targets"] = position["targets"][idx + 1:]
                    if position["remaining"] <= 1e-9:
                        position = None
                    break

        signal = runner.on_bar(bars[: i + 1])
        if signal is not None and position is None:
            position = {"entry": signal.entry, "sl": signal.stop_loss,
                        "targets": [t for t in signal.targets],
                        "remaining": 1.0, "bar": i, "tp_filled": 0}

    wins = [t for t in exits if t["pnl_pct"] > 0]
    losses = [t for t in exits if t["pnl_pct"] <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in exits:
        equity += t["pnl_pct"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses))
    mean = equity / len(exits) if exits else 0.0
    std = math.sqrt(sum((t["pnl_pct"] - mean) ** 2 for t in exits) / len(exits)) if exits else 0.0
    sharpe = (mean / std) if std > 0 else 0.0

    tp_counts = {}
    for t in exits:
        tp_counts[t["exit"]] = tp_counts.get(t["exit"], 0) + 1

    return {
        "strategyId": strategy["id"],
        "strategyName": strategy["name"],
        "trades": len(exits),
        "winRate": round(len(wins) / len(exits) * 100, 1) if exits else 0.0,
        "netReturnPct": round(equity, 2),
        "maxDrawdownPct": round(abs(max_dd), 2),
        "sharpe": round(sharpe, 2),
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (gross_win or 0.0),
        "avgWinPct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avgLossPct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0.0,
        "avgBarsHeld": round(sum(t["bars_held"] for t in exits) / len(exits), 1) if exits else 0.0,
        "exitSplit": tp_counts,
    }