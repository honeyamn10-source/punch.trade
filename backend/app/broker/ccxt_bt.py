"""Binance adapter via CCXT — real global market data, real/testnet orders.

- OHLCV via public endpoints: free, no account needed (live + history).
- Orders: entry market order, then TP and SL legs. On spot, Binance
  doesn't accept a native 3-leg bracket in one request, so we place the
  take-profit-limit and stop-loss-limit legs immediately after the entry
  — functionally an attached bracket.
- Testnet (free, fake money): flip `testnet` in the connect call; all
  orders then hit Binance's paper environment.
"""

from __future__ import annotations

from contextlib import suppress

from .base import BrokerAdapter, BrokerError

try:
    import ccxt
except ImportError:
    ccxt = None


class CCXTBroker(BrokerAdapter):
    name = "binance"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        if ccxt is None:
            raise BrokerError("ccxt not installed. pip install ccxt") from None
        self.testnet = testnet
        self.public = not (api_key or api_secret)
        params: dict = {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if api_key:
            params["apiKey"] = api_key
        if api_secret:
            params["secret"] = api_secret
        if testnet:
            params["sandbox"] = True
        self._ex = ccxt.binance(params)
        if testnet:
            with suppress(Exception):
                self._ex.set_sandbox_mode(True)

    # ---- BrokerAdapter --------------------------------------------------
    def status(self) -> dict:
        if self.public:
            return {
                "broker": "binance",
                "connected": True,
                "testnet": False,
                "account": "public market data (read-only)",
                "usdt": None,
            }
        try:
            balance = self._ex.fetch_balance()
            return {
                "broker": "binance",
                "connected": True,
                "testnet": self.testnet,
                "account": "spot",
                "usdt": round(float(balance.get("USDT", {}).get("free", 0) or 0), 2),
            }
        except Exception as e:
            raise BrokerError(f"Binance auth failed: {e}") from None

    def get_historical_bars(self, symbol: str, interval: str, days: int) -> list[dict]:
        tf = interval if interval != "1d" else "1d"
        try:
            rows = self._ex.fetch_ohlcv(symbol, tf, limit=min(1000, days * 12 * 5))
        except Exception as e:
            raise BrokerError(f"Binance OHLCV failed: {e}") from None
        return [
            {
                "ts": r[0] / 1000,
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
            }
            for r in rows
        ]

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
        side_s = "buy" if side.lower() == "buy" else "sell"
        exit_side = "sell" if side_s == "buy" else "buy"
        try:
            entry_order = self._ex.create_order(
                symbol,
                "market" if market else "limit",
                side_s,
                qty,
                None if market else (price or entry),
            )
            entry_id = entry_order.get("id", "?")
            tp = self._ex.create_order(
                symbol, "take_profit_limit", exit_side, qty, target, {"stopPrice": target}
            )
            sl = self._ex.create_order(
                symbol, "stop_loss_limit", exit_side, qty, stop, {"stopPrice": stop}
            )
        except Exception as e:
            raise BrokerError(f"Binance bracket failed: {e}") from None
        return {
            "orderId": entry_id,
            "status": "ACCEPTED",
            "broker": "binance",
            "legs": [
                {
                    "leg": "ENTRY",
                    "orderId": entry_id,
                    "status": entry_order.get("status", "?"),
                    "price": entry_order.get("price"),
                },
                {
                    "leg": "TAKE_PROFIT",
                    "orderId": tp.get("id", "?"),
                    "status": tp.get("status", "?"),
                    "price": target,
                },
                {
                    "leg": "STOP_LOSS",
                    "orderId": sl.get("id", "?"),
                    "status": sl.get("status", "?"),
                    "price": stop,
                },
            ],
        }

    def get_positions(self) -> list[dict]:
        """Spot has no positions — report open bracket legs + available quote."""
        try:
            orders = self._ex.fetch_open_orders()
            balance = self._ex.fetch_balance()
        except Exception as e:
            raise BrokerError(f"Binance orders failed: {e}") from None
        out = []
        for o in orders:
            out.append(
                {
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "qty": o.get("amount"),
                    "price": o.get("price") or o.get("stopPrice"),
                    "type": o.get("type"),
                    "status": "open",
                }
            )
        usdt = balance.get("USDT", {}).get("free", 0) or 0
        return out + [
            {
                "id": "balance",
                "symbol": "USDT",
                "qty": round(float(usdt), 2),
                "side": "available",
                "status": "balance",
            }
        ]

    def get_fills(self, since: float | None = None) -> list[dict]:
        try:
            trades = self._ex.fetch_my_trades()
        except Exception as e:
            raise BrokerError(f"Binance trades failed: {e}") from None
        out = []
        for t in trades:
            ts = t.get("timestamp")
            if ts is not None and since is not None and ts / 1000 < since:
                continue
            out.append(
                {
                    "id": t.get("id"),
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "qty": t.get("amount"),
                    "price": t.get("price"),
                    "ts": ts / 1000 if ts is not None else None,
                }
            )
        return out
