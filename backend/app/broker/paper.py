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
                      market: bool = True, price: Optional[float] = None) -> Dict:
        slip = entry * (1 + (self._rng.uniform(-1, 1) * config.SLIPPAGE_PCT / 100))
        fill_price = slip if market else (price or entry)
        pos_id = uuid.uuid4().hex[:12]
        position = {"id": pos_id, "symbol": symbol, "side": side, "qty": qty,
                    "entry": round(fill_price, 2), "target": round(target, 2),
                    "stop": round(stop, 2), "status": "open",
                    "opened_at": time.time(), "pnl_pct": 0.0, "exit": None}
        self._positions.append(position)
        self._fills.append({"id": uuid.uuid4().hex[:12], "positionId": pos_id,
                            "symbol": symbol, "side": "buy", "qty": qty,
                            "price": round(fill_price, 2),
                            "signalEntry": round(entry, 2),
                            "slippagePct": round((fill_price - entry) / entry * 100, 3),
                            "ts": time.time()})
        return {"orderId": pos_id, "status": "FILLED", "legs": [
            {"leg": "ENTRY", "status": "FILLED", "price": round(fill_price, 2)},
            {"leg": "TAKE_PROFIT", "status": "PENDING", "price": round(target, 2)},
            {"leg": "STOP_LOSS", "status": "PENDING", "price": round(stop, 2)}]}

    def on_bar(self, symbol: str, bar: dict) -> List[dict]:
        """Feed a new live bar; close any positions that hit TP/SL."""
        closed = []
        for p in self._positions:
            if p["symbol"] != symbol or p["status"] != "open":
                continue
            if bar["low"] <= p["stop"]:
                p["status"] = "closed"
                p["exit"] = "SL"
                p["exit_price"] = p["stop"]
                p["pnl_pct"] = round((p["stop"] - p["entry"]) / p["entry"] * 100, 2)
            elif bar["high"] >= p["target"]:
                p["status"] = "closed"
                p["exit"] = "TP"
                p["exit_price"] = p["target"]
                p["pnl_pct"] = round((p["target"] - p["entry"]) / p["entry"] * 100, 2)
            else:
                p["pnl_pct"] = round((bar["close"] - p["entry"]) / p["entry"] * 100, 2)
                continue
            closed.append(dict(p))
        return closed

    def get_positions(self) -> List[Dict]:
        return [dict(p) for p in self._positions]

    def get_fills(self, since: Optional[float] = None) -> List[Dict]:
        if since is None:
            return [dict(f) for f in self._fills]
        return [dict(f) for f in self._fills if f["ts"] >= since]