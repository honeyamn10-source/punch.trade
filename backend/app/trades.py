"""Completed-trade model.

ONE position = ONE CompletedTrade. Every fill carries a reason
(ENTRY / TP1 / TP2 / TP3 / STOP / MANUAL / END_OF_TEST / LIQUIDATION).
PnL is computed centrally via pnl.trade_pnl on final book-keeping numbers;
multi-target partial exits accumulate into the single trade's fills.

Used by backtester, research, dashboard and (later) live execution.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import pnl as pnl_mod

FILL_REASONS = ("ENTRY", "TP1", "TP2", "TP3", "STOP", "MANUAL", "END_OF_TEST",
                "LIQUIDATION")


class Fill:
    __slots__ = ("ts", "reason", "price", "qty", "commission")

    def __init__(self, ts: float, reason: str, price: float, qty: float,
                 commission: float = 0.0):
        if reason not in FILL_REASONS:
            raise ValueError(f"unknown fill reason '{reason}'")
        self.ts = ts
        self.reason = reason
        self.price = price
        self.qty = qty
        self.commission = commission

    def to_dict(self) -> dict:
        return {"ts": self.ts, "reason": self.reason,
                "price": round(self.price, 4), "qty": self.qty,
                "commission": round(self.commission, 4)}


class CompletedTrade:
    """A finished round trip. PnL fields are computed at close() time and
    frozen — later edits to the trade must go through close()."""

    def __init__(self, signal_id: Optional[str], strategy_id: str,
                 strategy_version: str, symbol: str, side: str,
                 qty: float, entry_ts: float, entry_price: float,
                 entry_commission: float = 0.0,
                 timeframe: str = "5m", regime: str = "UNKNOWN"):
        import uuid
        self.id = f"t-{uuid.uuid4().hex[:12]}"
        self.signal_id = signal_id
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.entry_ts = entry_ts
        self.entry_price = entry_price
        self.entry_commission = entry_commission
        self.timeframe = timeframe
        self.regime = regime
        self.fills: List[Fill] = [
            Fill(entry_ts, "ENTRY", entry_price, qty, entry_commission)]
        self.exit_ts: Optional[float] = None
        self.exit_price: Optional[float] = None
        self.closed_by: Optional[str] = None
        self.commission = entry_commission
        self.net_pnl: float = 0.0
        self.net_pnl_pct: float = 0.0
        self.closed = False

    # --------------------------------------------------------- fills ----
    def add_fill(self, ts: float, reason: str, price: float, qty: float,
                 commission: float = 0.0) -> None:
        """Record a partial (TP) or closing fill. Money math happens only
        in close(), but TP fills accumulate cost + realized fraction."""
        if self.closed:
            raise ValueError("trade already closed")
        if reason == "ENTRY":
            raise ValueError("ENTRY fill already recorded at open")
        self.fills.append(Fill(ts, reason, price, qty, commission))
        self.commission += commission

    # ----------------------------------------------------------- close ----
    def close(self, ts: float, price: float, closed_by: str,
              commission: float = 0.0, qty: Optional[float] = None) -> dict:
        """Close the trade and freeze PnL. `closed_by` is a closing fill
        reason (STOP / MANUAL / END_OF_TEST / LIQUIDATION / TP1..TP3 when
        the final target completes the position). Default qty = remaining
        open quantity. Returns the trade dict.
        """
        if self.closed:
            raise ValueError("trade already closed")
        if closed_by not in FILL_REASONS or closed_by == "ENTRY":
            raise ValueError(f"'{closed_by}' is not a closing reason")
        remaining = self.qty - sum(f.qty for f in self.fills if f.reason != "ENTRY")
        qty = qty if qty is not None else remaining
        if qty <= 0:
            raise ValueError("nothing left to close")
        if qty > remaining + 1e-9:
            raise ValueError(f"only {remaining:.6f} qty remains open")

        self.fills.append(Fill(ts, closed_by, price, qty, commission))
        self.commission += commission
        self.exit_ts = ts
        self.exit_price = price
        self.closed_by = closed_by
        self.closed = True

        # weighted-average exit price across all closing fills (TPs + close)
        close_fills = [f for f in self.fills if f.reason != "ENTRY"]
        total_qty = sum(f.qty for f in close_fills)
        avg_exit = sum(f.price * f.qty for f in close_fills) / total_qty if total_qty else price
        self.net_pnl = pnl_mod.trade_pnl(self.side, self.entry_price, avg_exit,
                                         self.qty, self.commission)
        self.net_pnl_pct = pnl_mod.trade_pnl_pct(
            self.entry_price, avg_exit, self.side)
        return self.to_dict()

    # ------------------------------------------------------------- dto ----
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "signalId": self.signal_id,
            "strategyId": self.strategy_id,
            "strategyVersion": self.strategy_version,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "entryTs": self.entry_ts,
            "entryPrice": round(self.entry_price, 4),
            "exitTs": self.exit_ts,
            "exitPrice": round(self.exit_price, 4) if self.exit_price is not None else None,
            "closedBy": self.closed_by,
            "netPnl": round(self.net_pnl, 2),
            "netPnlPct": round(self.net_pnl_pct * 100, 3),
            "classification": pnl_mod.classify(self.net_pnl),
            "commission": round(self.commission, 4),
            "fills": [f.to_dict() for f in self.fills],
            "closed": self.closed,
        }