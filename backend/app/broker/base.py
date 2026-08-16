"""Broker adapter interface.

One internal interface, one adapter per broker (Kite, Binance/CCXT,
Paper). The strategy engine, feed and API never touch broker-specific
request shapes directly — swap the active broker without touching the
rest of the system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BrokerError(Exception):
    """Raised when a broker call fails (auth, rate limit, rejection)."""


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def status(self) -> Dict:
        """Connection/auth status + account summary."""

    @abstractmethod
    def get_historical_bars(self, symbol: str, interval: str, days: int) -> List[dict]:
        """Real historical OHLCV bars, oldest first. interval like "5minute" or "5m"."""

    @abstractmethod
    def place_bracket(self, symbol: str, side: str, qty: int,
                      entry: float, target: float, stop: float,
                      market: bool = True, price: Optional[float] = None,
                      targets: Optional[List[float]] = None) -> Dict:
        """Place entry + attached take-profit + stop-loss as one unit
        (Kite BO / Binance OCO-style legs). Returns {orderId, status, legs}.
        `targets` is the multi-level TP list; adapters without multi-level
        support use `target` (the primary level)."""

    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """Open positions / bracket legs with live PnL."""

    @abstractmethod
    def get_fills(self, since: Optional[float] = None) -> List[Dict]:
        """Recent fills/trades — audit + slippage reconciliation."""