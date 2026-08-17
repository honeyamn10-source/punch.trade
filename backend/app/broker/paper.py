"""Paper broker — zero-cost smoke-test adapter.

Synthetic historical bars (seeded random walk) so backtests and the live
loop run without any account. Positions are closed by a monitor that
consumes each new live bar (TP/SL touched -> close, conservative
SL-first rule). Slippage is simulated on fills so the slippage
reconciliation path is exercised end-to-end.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from typing import Dict, List, Optional

from .. import config
from .base import BrokerAdapter, BrokerError

_SEED_SYMBOLS = {
    "RELIANCE": 2950.0,
    "TCS": 4100.0,
    "HDFCBANK": 1650.0,
    "INFY": 1550.0,
    "BTC/USDT": 97000.0,
}


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self, seed: Optional[float] = None):
        self._rng = random.Random(seed or 42)
        self._positions: List[dict] = []
        self._fills: List[dict] = []
        self._history: Dict[str, List[dict]] = {}

    # ---- synthetic data -------------------------------------------------
    def _seed_history(self, symbol: str, n: int = config.HISTORY_BARS) -> List[dict]:
        if symbol in self._history:
            return self._history[symbol]
        base = _SEED_SYMBOLS.get(symbol, 1000.0)
        bars: List[dict] = []
        price = base
        now = time.time()
        for i in range(n):
            drift = 0.0004
            shock = self._rng.gauss(0, 0.008)
            open_ = price
            close = max(1.0, price * (1 + drift + shock))
            high = max(open_, close) * (1 + abs(self._rng.gauss(0, 0.004)))
            low = min(open_, close) * (1 - abs(self._rng.gauss(0, 0.004)))
            bars.append({"ts": now - (n - i) * config.BAR_SECONDS,
                         "open": open_, "high": high, "low": low,
                         "close": close, "volume": self._rng.randint(1000, 50000)})
            price = close
        self._history[symbol] = bars
        return bars

    # ---- BrokerAdapter --------------------------------------------------
    def status(self) -> Dict:
        return {"broker": "paper", "connected": True,
                "account": "paper-simulated", "note": "No real money."}

    def get_historical_bars(self, symbol: str, interval: str, days: int) -> List[dict]:
        bars = self._seed_history(symbol)
        return [dict(b) for b in bars]

    def place_bracket(self, symbol: str, side: str, qty: int,
                      entry: float, target: float, stop: float,
                      market: bool = True, price: Optional[float] = None,
                      targets: Optional[List[float]] = None) -> Dict:
        slip = entry * (1 + (self._rng.uniform(-1, 1) * config.SLIPPAGE_PCT / 100))
        fill_price = slip if market else (price or entry)
        pos_id = uuid.uuid4().hex[:12]
        tps = targets or [target]
        position = {"id": pos_id, "symbol": symbol, "side": side, "qty": qty,
                    "entry": round(fill_price, 2), "target": round(tps[0], 2),
                    "targets": [round(t, 2) for t in tps], "stop": round(stop, 2),
                    "status": "open", "opened_at": time.time(), "pnl_pct": 0.0,
                    "exit": None, "remaining_qty": qty, "tp_count": len(tps),
                    "tp_filled": 0}
        self._positions.append(position)
        self._fills.append({"id": uuid.uuid4().hex[:12], "positionId": pos_id,
                            "symbol": symbol, "side": "buy", "qty": qty,
                            "price": round(fill_price, 2),
                            "signalEntry": round(entry, 2),
                            "slippagePct": round((fill_price - entry) / entry * 100, 3),
                            "ts": time.time()})
        return {"orderId": pos_id, "status": "FILLED", "legs": [
            {"leg": "ENTRY", "status": "FILLED", "price": round(fill_price, 2)},
            {"leg": "TAKE_PROFIT", "status": "PENDING", "price": tps},
            {"leg": "STOP_LOSS", "status": "PENDING", "price": round(stop, 2)}]}

    def on_bar(self, symbol: str, bar: dict) -> List[dict]:
        """Feed a new live bar; close position fractions at TP/SL levels."""
        closed = []
        for p in self._positions:
            if p["symbol"] != symbol or p["status"] != "open":
                continue
            if bar["low"] <= p["stop"]:
                self._exit_fraction(p, p["stop"], "SL", p["remaining_qty"], closed)
            elif bar["high"] >= p["target"] and p["targets"]:
                p["tp_filled"] += 1
                frac_qty = max(1, round(p["qty"] / p["tp_count"]))
                self._exit_fraction(p, p["targets"][0], f"TP{p['tp_filled']}", frac_qty, closed)
                p["targets"] = p["targets"][1:]
                p["target"] = p["targets"][0] if p["targets"] else p["target"]
            else:
                p["pnl_pct"] = round((bar["close"] - p["entry"]) / p["entry"] * 100, 2)
                continue
        return closed

    def _exit_fraction(self, p: dict, price: float, label: str, qty: float, closed: List[dict]) -> None:
        p["remaining_qty"] -= qty
        realized = (price - p["entry"]) / p["entry"] * 100
        event = {"id": p["id"], "symbol": p["symbol"], "side": p["side"],
                 "qty": qty, "qty_total": p["qty"], "entry": p["entry"],
                 "exit_price": round(price, 2),
                 "exit": label, "pnl_pct": round(realized, 2),
                 "opened_at": p["opened_at"], "status": "open"}
        if p["remaining_qty"] <= 0:
            p["status"] = "closed"
            p["exit"] = label
            p["exit_price"] = round(price, 2)
            p["pnl_pct"] = round(realized, 2)
            event["status"] = "closed"
        closed.append(event)

    def get_positions(self) -> List[Dict]:
        return [dict(p) for p in self._positions]

    def get_fills(self, since: Optional[float] = None) -> List[Dict]:
        if since is None:
            return [dict(f) for f in self._fills]
        return [dict(f) for f in self._fills if f["ts"] >= since]