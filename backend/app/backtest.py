"""Backtester — replays the same StrategyRunner against historical bars.

Fill model (conservative):
- Signal fires on bar close; entry fills at that close.
- Scan subsequent bars; if a bar touches both TP and SL, count the SL
  (worst case). First touch decides win/loss.
- Drawdown is measured on the equity curve (cumulative PnL in %).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .engine import StrategyRunner


def backtest(strategy: Dict, bars: List[dict]) -> Dict:
    """bars: list of bar dicts with open/high/low/close (oldest first)."""
    if len(bars) < 60:
        return {"error": "Not enough historical bars to backtest", "trades": 0}

    runner = StrategyRunner(strategy)
    trades: List[dict] = []
    position = None  # {"entry": float, "tp": float, "sl": float}

    for i in range(1, len(bars)):
        bar = bars[i]
        if position is not None:
            if bar["low"] <= position["sl"]:
                if bar["high"] >= position["tp"]:
                    pnl = (position["sl"] - position["entry"]) / position["entry"] * 100
                else:
                    pnl = (position["sl"] - position["entry"]) / position["entry"] * 100
                trades.append({"exit": "SL", "pnl_pct": round(pnl, 3), "bars_held": i - position["bar"]})
                position = None
            elif bar["high"] >= position["tp"]:
                pnl = (position["tp"] - position["entry"]) / position["entry"] * 100
                trades.append({"exit": "TP", "pnl_pct": round(pnl, 3), "bars_held": i - position["bar"]})
                position = None

        signal = runner.on_bar(bars[: i + 1])
        if signal is not None:
            position = {"entry": signal.entry, "tp": signal.target_price,
                        "sl": signal.stop_loss, "bar": i}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t["pnl_pct"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    return {
        "strategyId": strategy["id"],
        "strategyName": strategy["name"],
        "trades": len(trades),
        "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "netReturnPct": round(equity, 2),
        "maxDrawdownPct": round(abs(max_dd), 2),
        "avgBarsHeld": round(sum(t["bars_held"] for t in trades) / len(trades), 1) if trades else 0.0,
        "exitSplit": {"TP": sum(1 for t in trades if t["exit"] == "TP"),
                      "SL": sum(1 for t in trades if t["exit"] == "SL")},
    }