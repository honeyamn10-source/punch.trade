"""Broker adapter interface.

One internal interface, one adapter per broker (Kite, Binance/CCXT,
Paper). The strategy engine, feed and API never touch broker-specific
request shapes directly — swap the active broker without touching the
rest of the system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerError(Exception):
    """Raised when a broker call fails (auth, rate limit, rejection)."""


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def status(self) -> dict:
        """Connection/auth status + account summary."""

    @abstractmethod
    def get_historical_bars(self, symbol: str, interval: str, days: int) -> list[dict]:
        """Real historical OHLCV bars, oldest first. interval like "5minute" or "5m"."""

    @abstractmethod
    def place_bracket(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry: float,
        target: float,
        stop: float,
        market: bool = True,
        price: float | None = None,
        targets: list[float] | None = None,
    ) -> dict:
        """Place entry + attached take-profit + stop-loss as one unit
        (Kite BO / Binance OCO-style legs). Returns {orderId, status, legs}.
        `targets` is the multi-level TP list; adapters without multi-level
        support use `target` (the primary level)."""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Open positions / bracket legs with live PnL."""

    @abstractmethod
    def get_fills(self, since: float | None = None) -> list[dict]:
        """Recent fills/trades — audit + slippage reconciliation."""
