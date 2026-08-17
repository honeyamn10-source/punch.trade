"""Central PnL accounting + trade performance metrics.

EVERY PnL number in the app (backtest, research, dashboard, AI context)
must come from here — one implementation, no duplicated math.

Trade classification is by FINAL NET PnL only. A position that hits TP1
then runs to the stop is a LOSS — never counted as a win because one
fraction of it touched a target. That rule is what keeps the win-rate
honest.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

WIN = "WIN"
LOSS = "LOSS"
BREAKEVEN = "BREAKEVEN"

BREAKEVEN_EPS = 1e-9


def classify(net_pnl: float) -> str:
    """WIN when net_pnl > 0, LOSS when < 0, BREAKEVEN when ~0."""
    if net_pnl > BREAKEVEN_EPS:
        return WIN
    if net_pnl < -BREAKEVEN_EPS:
        return LOSS
    return BREAKEVEN


def trade_pnl(side: str, entry_price: float, exit_price: float,
              qty: float, commission: float = 0.0) -> float:
    """Money PnL for a completed round trip (buy long / sell short)."""
    if side in ("buy", "long", "LONG"):
        gross = (exit_price - entry_price) * qty
    elif side in ("sell", "short", "SHORT"):
        gross = (entry_price - exit_price) * qty
    else:
        raise ValueError(f"unknown side '{side}'")
    return gross - commission


def trade_pnl_pct(entry_price: float, exit_price: float,
                  side: str = "buy", leverage: float = 1.0) -> float:
    if entry_price <= 0:
        return 0.0
    if side in ("buy", "long", "LONG"):
        return (exit_price - entry_price) / entry_price * leverage
    return (entry_price - exit_price) / entry_price * leverage


def summary_stats(trades: List[dict]) -> Dict:
    """All headline metrics from a list of completed-trade dicts.

    Every trade dict must carry: {"net_pnl": float (money),
    "net_pnl_pct": float, "entry_ts": float, "exit_ts": float}.

    Returns: trades, wins, losses, break_even, win_rate, gross_profit,
    gross_loss, net_pnl, profit_factor, avg_win, avg_loss, expectancy,
    max_drawdown_pct, max_consecutive_losses, avg_bars_held, sharpe
    (per-trade, non-annualized).
    """
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "break_even": 0,
                "win_rate": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
                "net_pnl": 0.0, "profit_factor": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "expectancy": 0.0, "max_drawdown_pct": 0.0,
                "max_consecutive_losses": 0, "avg_bars_held": 0.0,
                "sharpe": 0.0}

    results = [(classify(t["net_pnl"]), t["net_pnl"], t["net_pnl_pct"]) for t in trades]
    wins = [r for r in results if r[0] == WIN]
    losses = [r for r in results if r[0] == LOSS]
    be = [r for r in results if r[0] == BREAKEVEN]

    gross_profit = sum(r[1] for r in wins)
    gross_loss = abs(sum(r[1] for r in losses))
    net = sum(r[1] for r in results)

    # equity curve on realized pnl percentages (bootstrap-free, deterministic)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in results:
        equity += r[2]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    # consecutive losses (streak, not percentage math)
    streak = 0
    max_streak = 0
    for r in results:
        if r[0] == LOSS:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -(sum(r[1] for r in losses) / len(losses)) if losses else 0.0
    mean = net / n
    std = math.sqrt(sum((r[1] - mean) ** 2 for r in results) / n) if n else 0.0

    held = [t["exit_ts"] - t["entry_ts"] for t in trades if t.get("exit_ts") and t.get("entry_ts")]

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "break_even": len(be),
        "win_rate": round(len(wins) / n * 100, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0
        else (round(gross_profit, 4) if gross_profit > 0 else 0.0),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(net / n, 2),
        "max_drawdown_pct": round(abs(max_dd), 2),
        "max_consecutive_losses": max_streak,
        "avg_bars_held": round(sum(held) / len(held), 1) if held else 0.0,
        "sharpe": round(mean / std, 2) if std > 0 else 0.0,
    }