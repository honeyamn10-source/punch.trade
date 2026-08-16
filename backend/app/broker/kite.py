"""Zerodha Kite Connect adapter — real NSE/BSE data and real bracket orders.

Runs on the user's OWN Zerodha account (Kite Connect API keys are free;
the API itself is free). Money never touches the punch.trade server —
the access token lives encrypted in the vault and only the user's
account is used for execution. This is the non-custodial design.

Bracket orders: product="BO" places the entry + take-profit +
stop-loss as ONE unit — the "attachment handling" from the design.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .. import config
from ..vault import load
from .base import BrokerAdapter, BrokerError

try:
    from kiteconnect import KiteConnect
    from kiteconnect.exceptions import KiteException
except ImportError:
    KiteConnect = None
    KiteException = Exception

IST = timezone(timedelta(hours=5, minutes=30))

INTERVALS = {"1m": "minute", "5m": "5minute", "15m": "15minute", "1d": "day"}


def login_url(api_key: str) -> str:
    if KiteConnect is None:
        raise BrokerError("kiteconnect not installed. pip install kiteconnect[ws]")
    return KiteConnect(api_key=api_key).login_url()


def generate_session(api_key: str, api_secret: str, request_token: str) -> Dict:
    if KiteConnect is None:
        raise BrokerError("kiteconnect not installed. pip install kiteconnect[ws]")
    try:
        kc = KiteConnect(api_key=api_key)
        data = kc.generate_session(request_token, api_secret=api_secret)
        return {"api_key": api_key, "access_token": data["access_token"]}
    except KiteException as e:
        raise BrokerError(f"Kite session failed: {e}")


class KiteAdapter(BrokerAdapter):
    name = "kite"

    def __init__(self, api_key: str, access_token: str):
        if KiteConnect is None:
            raise BrokerError("kiteconnect not installed. pip install kiteconnect[ws]")
        self.api_key = api_key
        self.access_token = access_token
        self._kite = KiteConnect(api_key=api_key, access_token=access_token)
        self._instruments: List[dict] = []
        self._tokens: Dict[str, int] = {}

    # ---- helpers --------------------------------------------------------
    def _resolve_token(self, symbol: str) -> int:
        if symbol in self._tokens:
            return self._tokens[symbol]
        if not self._instruments:
            self._instruments = self._kite.instruments("NSE")
        for ins in self._instruments:
            if ins.get("tradingsymbol") == symbol and ins.get("segment") == "NSE":
                self._tokens[symbol] = ins["instrument_token"]
                return ins["instrument_token"]
        raise BrokerError(f"Symbol {symbol} not found on NSE")

    @staticmethod
    def _bar_from_row(row: dict) -> dict:
        dt = row["date"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return {"ts": dt.timestamp(), "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]), "volume": float(row["volume"])}

    # ---- BrokerAdapter --------------------------------------------------
    def status(self) -> Dict:
        try:
            profile = self._kite.profile()
            return {"broker": "kite", "connected": True,
                    "account": profile.get("user_id", "?"),
                    "name": profile.get("user_name", "?")}
        except Exception as e:
            raise BrokerError(f"Kite session invalid or expired: {e}")

    def get_historical_bars(self, symbol: str, interval: str, days: int) -> List[dict]:
        token = self._resolve_token(symbol)
        iv = INTERVALS.get(interval, "5minute")
        to = datetime.now(IST)
        fr = to - timedelta(days=days)
        try:
            rows = self._kite.historical_data(token, iv, fr, to, continuous=False)
        except KiteException as e:
            raise BrokerError(f"Kite historical data failed: {e}")
        return [self._bar_from_row(r) for r in rows]

    def place_bracket(self, symbol: str, side: str, qty: int,
                      entry: float, target: float, stop: float,
                      market: bool = True, price: Optional[float] = None,
                      targets: Optional[List[float]] = None) -> Dict:
        token = self._resolve_token(symbol)
        txn = "BUY" if side.lower() == "buy" else "SELL"
        # For a buy BO: the SL leg activates when price drops to the
        # trigger; set it just above the stop level so the market SL
        # order fires ~at the stop instead of gapping through it.
        trigger = round(stop + (entry - stop) * 0.25, 2)
        try:
            order_id = self._kite.place_order(
                variety="regular", exchange="NSE", tradingsymbol=symbol,
                transaction_type=txn, quantity=qty, product="BO",
                order_type="MARKET" if market else "LIMIT",
                price=0 if market else (price or entry),
                squareoff=round(target - entry, 2),
                stoploss=round(entry - stop, 2),
                trigger_price=trigger, validity="DAY",
            )
        except KiteException as e:
            raise BrokerError(f"Kite order rejected: {e}")
        return {"orderId": order_id, "status": "ACCEPTED", "broker": "kite",
                "legs": [{"leg": "ENTRY+TP+SL (BO bracket)", "status": "ACCEPTED",
                          "note": f"trigger {trigger}"}]}

    def get_positions(self) -> List[Dict]:
        try:
            data = self._kite.positions()
        except KiteException as e:
            raise BrokerError(f"Kite positions failed: {e}")
        out = []
        for row in data.get("net", []):
            if row.get("quantity", 0) == 0:
                continue
            out.append({"id": row.get("tradingsymbol"),
                        "symbol": row.get("tradingsymbol"),
                        "side": "buy" if row.get("quantity", 0) > 0 else "sell",
                        "qty": abs(row.get("quantity", 0)),
                        "entry": row.get("average_price"),
                        "current": row.get("last_price"),
                        "pnl_pct": round((row.get("last_price", 0) - row.get("average_price", 0))
                                         / row.get("average_price", 1) * 100, 2)
                        if row.get("average_price") else 0.0,
                        "status": "open"})
        return out

    def get_fills(self, since: Optional[float] = None) -> List[Dict]:
        try:
            trades = self._kite.trades()
        except KiteException as e:
            raise BrokerError(f"Kite trades failed: {e}")
        out = []
        for t in trades:
            ts = t.get("trade_time") or t.get("order_timestamp")
            if ts is not None and since is not None and ts.timestamp() < since:
                continue
            out.append({"id": t.get("trade_id"), "symbol": t.get("tradingsymbol"),
                        "side": t.get("transaction_type", "").lower(),
                        "qty": t.get("quantity"), "price": t.get("price"),
                        "ts": ts.timestamp() if ts is not None else None})
        return out