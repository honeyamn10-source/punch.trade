"""punch.trade API — REST + WebSocket signal feed.

The extension connects to /ws/signals (real-time signals + position
events) and fires /api/orders for one-tap execution through the user's
own broker account. Every signal and order is appended to data/ for the
audit trail.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .backtest import backtest
from .broker.base import BrokerError
from .broker.ccxt_bt import CCXTBroker
from .broker.kite import KiteAdapter, generate_session, login_url
from .broker.openalgo import OpenAlgoAdapter
from .broker.paper import PaperBroker
from .engine import build_runners
from .feed import LiveFeed
from .strategies import STRATEGIES, get_strategy
from . import vault

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
SIGNALS_LOG = os.path.join(DATA_DIR, "signals.json")
ORDERS_LOG = os.path.join(DATA_DIR, "orders.json")

app = FastAPI(title="punch.trade", version="0.1.0")

# ---------------------------------------------------------------- auth --
def _check_token(token: str) -> bool:
    return token == config.API_TOKEN


def require_token(token: Optional[str]) -> None:
    if not token or not _check_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


# ------------------------------------------------------------- ws hub ----
class Hub:
    def __init__(self):
        self.clients: List[WebSocket] = []
        self.signals: deque = deque(maxlen=50)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)


hub = Hub()

# -------------------------------------------------------- broker mgr ----
class BrokerManager:
    def __init__(self):
        self.adapters: Dict[str, object] = {"paper": PaperBroker()}

    def get(self, name: str):
        if name not in self.adapters:
            raise HTTPException(status_code=400,
                                detail=f"Broker '{name}' not connected. Connect it first.")
        return self.adapters[name]


brokers = BrokerManager()
feed: Optional[LiveFeed] = None


# ------------------------------------------------------------ models ----
class LoginUrlReq(BaseModel):
    api_key: str


class KiteConnectReq(BaseModel):
    api_key: str
    api_secret: str
    request_token: str


class BinanceConnectReq(BaseModel):
    api_key: str
    api_secret: str
    testnet: bool = True


class OpenAlgoConnectReq(BaseModel):
    host: str
    apikey: str
    broker: str = "zerodha"


class BacktestReq(BaseModel):
    broker: str = "paper"
    interval: str = "5m"
    days: int = 30


class OrderReq(BaseModel):
    broker: str = "paper"
    strategyId: Optional[str] = None
    symbol: Optional[str] = None
    side: str = "buy"
    qty: int = 1
    entry: Optional[float] = None
    targetPrice: Optional[float] = None
    stopLoss: Optional[float] = None


# ------------------------------------------------------------- startup ----
_loop: Optional[asyncio.AbstractEventLoop] = None


@app.on_event("startup")
async def startup() -> None:
    global feed, _loop
    os.makedirs(DATA_DIR, exist_ok=True)
    _loop = asyncio.get_running_loop()

    # restore broker sessions from the encrypted vault
    for broker_name in vault.brokers():
        try:
            if broker_name == "kite":
                creds = vault.load("kite")
                brokers.adapters["kite"] = KiteAdapter(creds["api_key"], creds["access_token"])
            elif broker_name == "binance":
                creds = vault.load("binance")
                brokers.adapters["binance"] = CCXTBroker(
                    creds["api_key"], creds["api_secret"], creds.get("testnet", True))
            elif broker_name == "openalgo":
                creds = vault.load("openalgo")
                brokers.adapters["openalgo"] = OpenAlgoAdapter(
                    creds["host"], creds["apikey"], creds.get("broker", "zerodha"))
        except Exception as e:
            print(f"[startup] failed to restore {broker_name}: {e}")

    feed = LiveFeed(
        brokers.adapters["paper"],
        build_runners(),
        on_signal=on_signal,
        on_position_close=on_position_close,
    )
    feed.start()


def on_signal(signal: dict) -> None:
    hub.signals.append(signal)
    _append_json(SIGNALS_LOG, signal)
    _loop.create_task(hub.broadcast({"type": "signal", "data": signal}))


def on_position_close(position: dict) -> None:
    _loop.create_task(hub.broadcast({"type": "position", "data": position}))


def _append_json(path: str, record: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ------------------------------------------------------------- REST -----
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "brokers": list(brokers.adapters.keys()),
            "connected": [n for n in brokers.adapters if n != "paper"]}


@app.get("/api/strategies")
def strategies(token: Optional[str] = None) -> dict:
    require_token(token)
    return {"strategies": STRATEGIES}


@app.post("/api/strategies/{strategy_id}/backtest")
def run_backtest(strategy_id: str, req: BacktestReq, token: Optional[str] = None) -> dict:
    require_token(token)
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    adapter = brokers.get(req.broker)
    try:
        bars = adapter.get_historical_bars(strategy["symbol"], req.interval, req.days)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"source": req.broker, "bars": len(bars), **backtest(strategy, bars)}


@app.post("/api/broker/kite/login-url")
def kite_login_url(req: LoginUrlReq, token: Optional[str] = None) -> dict:
    require_token(token)
    try:
        return {"url": login_url(req.api_key)}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/broker/kite/connect")
def kite_connect(req: KiteConnectReq, token: Optional[str] = None) -> dict:
    require_token(token)
    try:
        session = generate_session(req.api_key, req.api_secret, req.request_token)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    vault.save("kite", session)
    brokers.adapters["kite"] = KiteAdapter(session["api_key"], session["access_token"])
    return {"connected": True, "broker": "kite"}


@app.post("/api/broker/binance/connect")
def binance_connect(req: BinanceConnectReq, token: Optional[str] = None) -> dict:
    require_token(token)
    try:
        adapter = CCXTBroker(req.api_key, req.api_secret, req.testnet)
        status = adapter.status()
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    vault.save("binance", {"api_key": req.api_key, "api_secret": req.api_secret,
                           "testnet": req.testnet})
    brokers.adapters["binance"] = adapter
    return {"connected": True, "broker": "binance", **status}


@app.post("/api/broker/openalgo/connect")
def openalgo_connect(req: OpenAlgoConnectReq, token: Optional[str] = None) -> dict:
    require_token(token)
    adapter = OpenAlgoAdapter(req.host, req.apikey, req.broker)
    try:
        status = adapter.status()
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not status.get("connected"):
        raise HTTPException(status_code=502, detail=f"OpenAlgo at {req.host} unreachable")
    vault.save("openalgo", {"host": req.host, "apikey": req.apikey, "broker": req.broker})
    brokers.adapters["openalgo"] = adapter
    return {"connected": True, "broker": "openalgo", **status}


@app.get("/api/broker/status")
def broker_status(token: Optional[str] = None) -> dict:
    require_token(token)
    out = {}
    for name, adapter in brokers.adapters.items():
        try:
            out[name] = adapter.status()
        except BrokerError as e:
            out[name] = {"broker": name, "connected": False, "error": str(e)}
    return out


@app.post("/api/orders")
async def place_order(req: OrderReq, token: Optional[str] = None) -> dict:
    require_token(token)
    adapter = brokers.get(req.broker)

    if req.entry is None or req.targetPrice is None or req.stopLoss is None:
        if req.strategyId is None or feed is None:
            raise HTTPException(status_code=400,
                                detail="Provide entry/targetPrice/stopLoss or strategyId")
        strategy = get_strategy(req.strategyId)
        if strategy is None:
            raise HTTPException(status_code=404, detail="Unknown strategy")
        series = feed.bars.get(strategy["symbol"])
        if not series:
            raise HTTPException(status_code=409,
                                detail=f"No live bars yet for {strategy['symbol']}")
        close = series[-1]["close"]
        req.entry = close
        req.targetPrice = close * (1 + strategy["tp_pct"] / 100)
        req.stopLoss = close * (1 - strategy["sl_pct"] / 100)
        req.symbol = strategy["symbol"]

    try:
        result = await asyncio.to_thread(
            adapter.place_bracket, req.symbol, req.side, req.qty,
            req.entry, req.targetPrice, req.stopLoss)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    record = {"ts": time.time(), "broker": req.broker, "strategyId": req.strategyId,
              "symbol": req.symbol, "side": req.side, "qty": req.qty,
              "entry": req.entry, "target": req.targetPrice, "stop": req.stopLoss,
              "result": result}
    _append_json(ORDERS_LOG, record)
    return record


@app.get("/api/positions")
def positions(broker: str = "paper", token: Optional[str] = None) -> dict:
    require_token(token)
    adapter = brokers.get(broker)
    try:
        return {"broker": broker, "positions": adapter.get_positions()}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/fills")
def fills(broker: str = "paper", token: Optional[str] = None) -> dict:
    require_token(token)
    adapter = brokers.get(broker)
    try:
        return {"broker": broker, "fills": adapter.get_fills()}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/signals/last")
def last_signals(token: Optional[str] = None) -> dict:
    require_token(token)
    return {"signals": list(hub.signals)}


# ------------------------------------------------------------- WS -------
@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    if not _check_token(token or ""):
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    # snapshot: recent signals + open positions + broker status
    positions = []
    try:
        positions = brokers.adapters["paper"].get_positions()
    except Exception:
        pass
    try:
        await ws.send_json({"type": "snapshot",
                            "data": {"signals": list(hub.signals),
                                     "positions": positions,
                                     "brokers": {n: a.name for n, a in brokers.adapters.items()}}})
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)


# ------------------------------------------------------------- demo -----
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")),
          name="static")


@app.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "demo.html"))