"""OpenAlgo adapter — one API for 34+ Indian brokers.

OpenAlgo (free, self-hosted Flask app) wraps Angel One, Fyers, Dhan,
Upstox, Alice Blue, etc. behind a single REST API and handles per-broker
strategy tagging. We treat it as an optional execution proxy: run your
own OpenAlgo instance, connect it here, and punch.trade can route
orders through any supported Indian broker without writing N adapters.

Requires your own OpenAlgo server (pip install openalgo) on the same
network with broker keys configured inside OpenAlgo. This adapter is
optional — the direct Kite adapter doesn't depend on it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import BrokerAdapter, BrokerError

ORDER_TYPES = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL-M"}
PRODUCTS = {"MIS": "MIS", "CNC": "CNC", "INTRADAY": "INTRADAY"}


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            raise BrokerError(f"OpenAlgo HTTP {e.code}") from None


class OpenAlgoAdapter(BrokerAdapter):
    name = "openalgo"

    def __init__(self, host: str, apikey: str, broker: str = "zerodha"):
        self.host = host.rstrip("/")
        self.apikey = apikey
        self.broker = broker
        self._base = f"{self.host}/api/v1"

    def _auth(self, payload: dict) -> dict:
        payload.update({"apikey": self.apikey, "strategy": "punch.trade"})
        return payload

    # ---- BrokerAdapter --------------------------------------------------
    def status(self) -> dict:
        try:
            data = _post(f"{self._base}/getorderbook", self._auth({}))
            connected = data.get("status") == "success"
            return {
                "broker": "openalgo",
                "connected": connected,
                "account": self.broker,
                "host": self.host,
                "note": "backed by your OpenAlgo instance",
            }
        except BrokerError as e:
            raise BrokerError(f"OpenAlgo unreachable: {e}") from None

    def get_historical_bars(self, symbol: str, interval: str, days: int) -> list[dict]:
        raise BrokerError("OpenAlgo provides no historical data. Backtest via kite or binance.")

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
        """Entry order via OpenAlgo; TP/SL legs attempted as GTT pairs
        (brokers that support GTT), otherwise only the entry is placed —
        the extension still shows the levels for manual exits."""
        action = "BUY" if side.lower() == "buy" else "SELL"
        payload = self._auth(
            {
                "symbol": symbol,
                "exchange": "NSE",
                "action": action,
                "pricetype": "MARKET" if market else "LIMIT",
                "product": "MIS",
                "quantity": qty,
                "price": "0" if market else str(price or entry),
                "trigger_price": "0",
                "disclosed_quantity": "0",
                "validity": "DAY",
            }
        )
        try:
            data = _post(f"{self._base}/placeorder", payload)
        except BrokerError as e:
            raise BrokerError(f"OpenAlgo order failed: {e}") from None
        legs = [
            {
                "leg": "ENTRY",
                "status": data.get("status", "?"),
                "orderId": data.get("orderid"),
                "message": data.get("message"),
            }
        ]
        gtt_ok = False
        if data.get("status") == "success":
            try:
                tp = _post(
                    f"{self._base}/placeGTT",
                    self._auth(
                        {
                            "symbol": symbol,
                            "exchange": "NSE",
                            "action": "SELL" if action == "BUY" else "BUY",
                            "product": "MIS",
                            "quantity": qty,
                            "pricetype": "LIMIT",
                            "price": str(target),
                            "trigger_price": str(target),
                            "disclosed_quantity": "0",
                            "validity": "DAY",
                        }
                    ),
                )
                sl = _post(
                    f"{self._base}/placeGTT",
                    self._auth(
                        {
                            "symbol": symbol,
                            "exchange": "NSE",
                            "action": "SELL" if action == "BUY" else "BUY",
                            "product": "MIS",
                            "quantity": qty,
                            "pricetype": "SL-M",
                            "price": "0",
                            "trigger_price": str(stop),
                            "disclosed_quantity": "0",
                            "validity": "DAY",
                        }
                    ),
                )
                legs.append(
                    {"leg": "TAKE_PROFIT (GTT)", "status": tp.get("status"), "id": tp.get("gtt_id")}
                )
                legs.append(
                    {"leg": "STOP_LOSS (GTT)", "status": sl.get("status"), "id": sl.get("gtt_id")}
                )
                gtt_ok = tp.get("status") == "success" and sl.get("status") == "success"
            except BrokerError:
                pass
        return {
            "orderId": data.get("orderid", "?"),
            "status": data.get("status", "?"),
            "broker": "openalgo",
            "gttAttached": gtt_ok,
            "legs": legs,
        }

    def get_positions(self) -> list[dict]:
        try:
            data = _post(f"{self._base}/getpositions", self._auth({}))
        except BrokerError as e:
            raise BrokerError(f"OpenAlgo positions failed: {e}") from None
        if data.get("status") != "success":
            return []
        rows = data.get("data", [])
        if isinstance(rows, dict):
            rows = rows.get("positions", [])
        out = []
        for r in rows or []:
            qty = int(r.get("quantity", 0) or 0)
            if qty == 0:
                continue
            avg = float(r.get("average_price", 0) or 0)
            ltp = float(r.get("last_price", 0) or 0)
            out.append(
                {
                    "id": r.get("tradingsymbol"),
                    "symbol": r.get("tradingsymbol"),
                    "side": "buy" if qty > 0 else "sell",
                    "qty": abs(qty),
                    "entry": avg,
                    "current": ltp,
                    "pnl_pct": round((ltp - avg) / avg * 100, 2) if avg else 0.0,
                    "status": "open",
                }
            )
        return out

    def get_fills(self, since: float | None = None) -> list[dict]:
        try:
            data = _post(f"{self._base}/getorderbook", self._auth({}))
        except BrokerError as e:
            raise BrokerError(f"OpenAlgo orderbook failed: {e}") from None
        rows = data.get("data", [])
        if isinstance(rows, dict):
            rows = rows.get("orders", [])
        out = []
        for r in rows or []:
            out.append(
                {
                    "id": r.get("orderid"),
                    "symbol": r.get("tradingsymbol"),
                    "side": r.get("transaction_type", "").lower(),
                    "qty": r.get("quantity"),
                    "price": r.get("price"),
                    "status": r.get("status"),
                    "ts": r.get("order_timestamp"),
                }
            )
        return out
