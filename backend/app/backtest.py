"""Honest backtester.

Replays the exact same StrategyRunner the live engine uses, against
historical bars, with an explicit execution-cost model and NO lookahead:

- Signal fires on completed bar i (close evaluation). Entry executes at
  bar i+1's OPEN, adjusted by slippage/spread, plus commission. The
  decision is never used against the candle that produced it.
- Intrabar fills (conservative default): stop-loss is checked BEFORE
  targets within the same bar — the worst-case assumption.
- Gap policy: if a bar opens beyond the stop, the stop fills at that
  OPEN (adjusted by slippage) — stops never get a better price than the
  market's opening gap.
- ONE position = ONE CompletedTrade (trades.py). Multi-TP partial exits
  accumulate into that trade's fills; classification is by FINAL NET PnL.
- All metrics come from pnl.summary_stats (central implementation).

Optimistic mode (intrabar_policy="optimistic") checks TP levels before
the stop — only use it to sanity-check the conservative ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from . import pnl as pnl_mod
from .engine import StrategyRunner
from .trades import CompletedTrade
from .strategies import target_levels


@dataclass
class ExecutionCostConfig:
    starting_capital: float = 1_000_000.0
    position_pct: float = 1.0            # fraction of cash risked per trade
    commission_bps: float = 0.0          # per side, in basis points
    slippage_bps: float = 0.0            # per side, in basis points
    spread_bps: float = 0.0              # half-spread per side, bps
    intrabar_policy: str = "conservative"   # conservative | optimistic
    gap_policy: str = "fill_at_next_open"   # fill_at_next_open | skip

    def __post_init__(self):
        if self.intrabar_policy not in ("conservative", "optimistic"):
            raise ValueError("intrabar_policy must be conservative|optimistic")
        if self.gap_policy not in ("fill_at_next_open", "skip"):
            raise ValueError("gap_policy must be fill_at_next_open|skip")
        for name in ("starting_capital", "position_pct", "commission_bps",
                     "slippage_bps", "spread_bps"):
            v = getattr(self, name)
            if v < 0:
                raise ValueError(f"{name} must be >= 0")


def _bps(v: float, p: float) -> float:
    return round(v * p * 0.0001, 6)


def backtest(strategy: Dict, bars: List[dict],
             costs: Optional[ExecutionCostConfig] = None) -> Dict:
    """bars: OHLCV bar dicts, oldest first, with 'ts' (float, seconds)."""
    costs = costs or ExecutionCostConfig()
    if len(bars) < 60:
        return {"error": "Not enough historical bars to backtest", "trades": 0}

    runner = StrategyRunner(strategy)
    n_levels = len(target_levels(strategy))
    cash = costs.starting_capital
    position: Optional[CompletedTrade] = None
    trades: List[dict] = []
    equity_curve: List[dict] = []
    total_commission = 0.0
    total_slippage_cost = 0.0
    entry_pending: Optional[dict] = None  # {"signal":..} waits for next open
    tp_done: Dict[int, int] = {}  # id(position) -> count of filled TP levels

    def commission(qty: float, price: float) -> float:
        return _bps(qty * price, costs.commission_bps)

    def slippage_cost(qty: float, price: float) -> float:
        slip = _bps(price, costs.slippage_bps) + _bps(price, costs.spread_bps)
        return round(slip * qty, 6)

    def open_position(signal, price: float, ts: float) -> Optional[CompletedTrade]:
        nonlocal cash, total_commission, total_slippage_cost
        qty = int(cash * costs.position_pct / price) if price > 0 else 0
        if qty <= 0:
            entry_pending.clear()
            return None
        slip = slippage_cost(qty, price)
        price_adj = price + slip / qty if signal.side == "buy" else price - slip / qty
        comm = commission(qty, price_adj)
        cash -= qty * price_adj + comm
        total_commission += comm
        total_slippage_cost += slip
        pos = CompletedTrade(
            signal_id=signal.id, strategy_id=strategy["id"],
            strategy_version=signal.strategy_version, symbol=strategy["symbol"],
            side=signal.side, qty=qty, entry_ts=ts, entry_price=price_adj,
            entry_commission=comm, timeframe=signal.timeframe,
            regime=signal.regime)
        tp_done[id(pos)] = 0
        entry_pending.clear()
        return pos

    for i in range(1, len(bars)):
        bar = bars[i]
        ts = bar["ts"]

        if entry_pending and position is None:
            position = open_position(entry_pending["signal"], bar["open"], ts)

        if position is not None and not position.closed:
            sl = position.entry_price * (1 - strategy.get("sl_pct", 1.0) / 100)
            tp_prices = [position.entry_price * (1 + p / 100)
                         for p in target_levels(strategy)]

            # gap: market opens beyond the stop -> fill at open (worse)
            gap_hit = bar["open"] <= sl if position.side == "buy" else bar["open"] >= sl
            if costs.gap_policy == "fill_at_next_open" and gap_hit:
                price = bar["open"] - _bps(bar["open"], costs.slippage_bps) \
                    if position.side == "buy" else bar["open"] + _bps(bar["open"], costs.slippage_bps)
                slip = slippage_cost(position.qty, price)
                total_slippage_cost += slip
                position.close(ts, price, "STOP",
                               commission(position.qty, price))
                total_commission += commission(position.qty, price)
                trades.append(position.to_dict())
                position = None
                continue

            if costs.intrabar_policy == "conservative":
                if bar["low"] <= sl if position.side == "buy" else bar["high"] >= sl:
                    price = sl - _bps(sl, costs.slippage_bps) if position.side == "buy" \
                        else sl + _bps(sl, costs.slippage_bps)
                    slip = slippage_cost(position.qty, price)
                    total_slippage_cost += slip
                    position.close(ts, price, "STOP",
                                   commission(position.qty, price))
                    total_commission += commission(position.qty, price)
                    trades.append(position.to_dict())
                    position = None
                    continue
                done = tp_done.get(id(position), 0)
                for idx, tp in enumerate(tp_prices):
                    if idx < done:
                        continue
                    hit = bar["high"] >= tp if position.side == "buy" else bar["low"] <= tp
                    if hit:
                        if idx == n_levels - 1:
                            # final TP fill IS the close (no double record)
                            total_commission += commission(position.qty / n_levels, tp)
                            position.close(ts, tp, f"TP{n_levels}",
                                           commission(position.qty / n_levels, tp))
                            trades.append(position.to_dict())
                            position = None
                            tp_done.clear()
                            break
                        qty = position.qty / n_levels
                        position.add_fill(ts, f"TP{idx + 1}", tp, qty,
                                          commission(qty, tp))
                        total_commission += commission(qty, tp)
                        tp_done[id(position)] = idx + 1
                        break
            else:  # optimistic: TP before SL
                done = tp_done.get(id(position), 0)
                for idx, tp in enumerate(tp_prices):
                    if idx < done:
                        continue
                    hit = bar["high"] >= tp if position.side == "buy" else bar["low"] <= tp
                    if hit:
                        if idx == n_levels - 1:
                            total_commission += commission(position.qty / n_levels, tp)
                            position.close(ts, tp, f"TP{n_levels}",
                                           commission(position.qty / n_levels, tp))
                            trades.append(position.to_dict())
                            position = None
                            tp_done.clear()
                            break
                        qty = position.qty / n_levels
                        position.add_fill(ts, f"TP{idx + 1}", tp, qty,
                                          commission(qty, tp))
                        total_commission += commission(qty, tp)
                        tp_done[id(position)] = idx + 1
                        break
                if position is not None and not position.closed:
                    if bar["low"] <= sl if position.side == "buy" else bar["high"] >= sl:
                        price = sl - _bps(sl, costs.slippage_bps) if position.side == "buy" \
                            else sl + _bps(sl, costs.slippage_bps)
                        slip = slippage_cost(position.qty, price)
                        total_slippage_cost += slip
                        position.close(ts, price, "STOP",
                                       commission(position.qty, price))
                        total_commission += commission(position.qty, price)
                        trades.append(position.to_dict())
                        position = None
                        tp_done.clear()

        signal = runner.on_bar(bars[: i + 1])
        if signal is not None and position is None and not entry_pending:
            entry_pending = {"signal": signal}

        # equity mark (cash + open position at close)
        mark = cash
        if position is not None and not position.closed:
            mark += position.qty * bar["close"]
        if i % max(1, len(bars) // 2000) == 0:
            equity_curve.append({"ts": ts, "equity": round(mark, 2)})

    # end of test: close anything still open at the last close
    if position is not None and not position.closed:
        last = bars[-1]
        price = last["close"] - _bps(last["close"], costs.slippage_bps) \
            if position.side == "buy" else last["close"] + _bps(last["close"], costs.slippage_bps)
        slip = slippage_cost(position.qty, price)
        total_slippage_cost += slip
        position.close(last["ts"], price, "END_OF_TEST",
                       commission(position.qty, price))
        total_commission += commission(position.qty, price)
        trades.append(position.to_dict())
        tp_done.clear()

    metrics = pnl_mod.summary_stats([
        {"net_pnl": t["netPnl"], "net_pnl_pct": t["netPnlPct"],
         "entry_ts": t["entryTs"], "exit_ts": t["exitTs"]}
        for t in trades])
    exit_counts = {}
    for t in trades:
        exit_counts[t["closedBy"]] = exit_counts.get(t["closedBy"], 0) + 1

    return {
        "strategyId": strategy["id"],
        "strategyName": strategy["name"],
        "strategyVersion": strategy.get("version", "1.0.0"),
        "trades": len(trades),
        "tradeList": trades,
        "metrics": metrics,
        "equityCurve": equity_curve[-2000:],
        "exitSplit": exit_counts,
        "costs": {
            "startingCapital": costs.starting_capital,
            "commissionBps": costs.commission_bps,
            "slippageBps": costs.slippage_bps,
            "spreadBps": costs.spread_bps,
            "intrabarPolicy": costs.intrabar_policy,
            "gapPolicy": costs.gap_policy,
            "totalCommission": round(total_commission, 2),
            "totalSlippageCost": round(total_slippage_cost, 2),
        },
    }