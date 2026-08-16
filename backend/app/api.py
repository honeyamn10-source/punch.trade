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
from .strategies import STRATEGIES, get_strategy, target_levels
from .proxy import router as proxy_router
from . import vault

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
SIGNALS_LOG = os.path.join(DATA_DIR, "signals.json")
ORDERS_LOG = os.path.join(DATA_DIR, "orders.json")
POSITIONS_LOG = os.path.join(DATA_DIR, "positions.json")
closed_positions: List[dict] = []

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
    global feed, _loop, closed_positions
    os.makedirs(DATA_DIR, exist_ok=True)
    _loop = asyncio.get_running_loop()

    # restore the closed-position ledger for analytics
    if os.path.exists(POSITIONS_LOG):
        try:
            with open(POSITIONS_LOG, "r", encoding="utf-8") as f:
                closed_positions = [json.loads(line) for line in f if line.strip()]
        except Exception:
            closed_positions = []

    # restore the signal ledger so /ws and /api/strategies/leaderboard see it
    if os.path.exists(SIGNALS_LOG) and not hub.signals:
        try:
            with open(SIGNALS_LOG, "r", encoding="utf-8") as f:
                hub.signals = [json.loads(line) for line in f if line.strip()][-100:]
        except Exception:
            pass

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

    print(f"[startup] telegram alerts: {'ON' if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID else 'OFF'}")

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
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        _loop.create_task(_telegram_push(signal))


async def _telegram_push(signal: dict) -> None:
    """Optional Telegram alert (set PUNCH_TELEGRAM_BOT_TOKEN + CHAT_ID)."""
    import httpx

    levels = " → ".join(str(t) for t in signal.get("targets", [signal.get("targetPrice")]))
    text = (f"PUNCH.TRADE signal\n{signal['symbol']} {signal['side'].upper()}\n"
            f"entry {signal['entry']} | TP {levels} | SL {signal['stopLoss']}\n"
            f"strategy: {signal['strategyName']}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text})
    except Exception as e:
        print(f"[telegram] push failed: {e}")


def on_position_close(position: dict) -> None:
    closed_positions.append(position)
    _append_json(POSITIONS_LOG, position)
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


_leaderboard_cache: Dict[str, dict] = {}


@app.get("/api/strategies/leaderboard")
def strategy_leaderboard(token: Optional[str] = None) -> dict:
    """Ranked per-strategy backtest stats (paper source, cached 60s)."""
    require_token(token)
    now = time.time()
    if _leaderboard_cache and now - _leaderboard_cache.get("ts", 0) < 60:
        return _leaderboard_cache

    adapter = brokers.adapters["paper"]
    rows = []
    for s in STRATEGIES:
        try:
            bars = adapter.get_historical_bars(s["symbol"], "5m", 30)
            stats = backtest(s, bars)
            if "error" in stats:
                continue
            rows.append({"id": s["id"], "name": s["name"], "symbol": s["symbol"],
                         **{k: stats[k] for k in ("winRate", "netReturnPct", "maxDrawdownPct",
                                                   "sharpe", "profitFactor", "trades")}})
        except Exception:
            continue
    rows.sort(key=lambda r: (r["sharpe"], r["winRate"]), reverse=True)
    result = {"ts": now, "rows": rows}
    _leaderboard_cache.clear()
    _leaderboard_cache.update(result)
    return result


@app.post("/api/strategies/{strategy_id}/backtest")
def run_backtest(strategy_id: str, req: BacktestReq, token: Optional[str] = None) -> dict:
    require_token(token)
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    if req.broker in brokers.adapters:
        adapter = brokers.adapters[req.broker]
    elif req.broker == "binance":
        # Public OHLCV needs no API keys — backtests work without connecting.
        adapter = CCXTBroker("", "", testnet=False)
    else:
        raise HTTPException(status_code=400,
                            detail=f"Broker '{req.broker}' not connected. Connect it first.")
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
        req.targetPrice = close * (1 + target_levels(strategy)[0] / 100)
        req.stopLoss = close * (1 - strategy["sl_pct"] / 100)
        req.symbol = strategy["symbol"]

    targets = None
    if req.strategyId:
        strategy = get_strategy(req.strategyId)
        if strategy:
            targets = [round(req.entry * (1 + pct / 100), 2) for pct in target_levels(strategy)]

    try:
        result = await asyncio.to_thread(
            adapter.place_bracket, req.symbol, req.side, req.qty,
            req.entry, req.targetPrice, req.stopLoss, True, None, targets)
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


@app.get("/api/signals/history")
def signal_history(token: Optional[str] = None) -> dict:
    require_token(token)
    # Prefer the live in-memory hub: records there carry live AI scores.
    # Fall back to the audit file for a cold start with no hub yet.
    rows = list(hub.signals)
    if not rows and os.path.exists(SIGNALS_LOG):
        with open(SIGNALS_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return {"signals": rows[-100:]}


@app.get("/api/orders/history")
def order_history(token: Optional[str] = None) -> dict:
    require_token(token)
    rows = []
    if os.path.exists(ORDERS_LOG):
        with open(ORDERS_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return {"orders": rows}


@app.get("/api/analytics")
def analytics(token: Optional[str] = None) -> dict:
    """Dashboard aggregate: performance from the closed-position ledger."""
    require_token(token)
    wins = [p for p in closed_positions if (p.get("pnl_pct") or 0) > 0]
    losses = [p for p in closed_positions if (p.get("pnl_pct") or 0) <= 0]
    equity = 0.0
    curve = []
    for p in sorted(closed_positions, key=lambda x: x.get("opened_at", 0)):
        equity += p.get("pnl_pct", 0.0)
        curve.append({"ts": p.get("opened_at", 0), "equity": round(equity, 2)})
    open_positions = []
    try:
        open_positions = brokers.adapters["paper"].get_positions()
    except Exception:
        pass
    return {
        "closed": len(closed_positions),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(len(wins) / len(closed_positions) * 100, 1) if closed_positions else 0.0,
        "netPnlPct": round(sum(p.get("pnl_pct", 0.0) for p in closed_positions), 2),
        "openPositions": len([p for p in open_positions if p.get("status") == "open"]),
        "equityCurve": curve,
        "recentCloses": list(reversed(closed_positions[-10:])),
    }


@app.get("/api/candles")
def candles(symbol: str, token: Optional[str] = None, limit: int = 120) -> dict:
    """Live candle series for the chart panel (falls back to paper history)."""
    require_token(token)
    bars: List[dict] = []
    if feed is not None:
        bars = feed.bars.get(symbol, [])
    if not bars:
        try:
            bars = brokers.adapters["paper"].get_historical_bars(symbol, "5m", 1)
        except Exception:
            bars = []
    return {"symbol": symbol, "bars": bars[-limit:]}


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


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "dashboard.html"))


# OpenAI-compatible -> Ollama proxy (qwen thinking fix), mounted last so
# it never shadows the /api routes.
app.include_router(proxy_router)