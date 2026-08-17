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

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from . import db
from . import risk
from . import security
from . import vault
from .backtest import ExecutionCostConfig, backtest
from . import execution
from .broker.base import BrokerError
from .broker.ccxt_bt import CCXTBroker
from .broker.kite import KiteAdapter, generate_session, login_url
from .broker.openalgo import OpenAlgoAdapter
from .broker.paper import PaperBroker
from .engine import build_runners
from .feed import LiveFeed
from .strategies import STRATEGIES, get_strategy, target_levels
from .proxy import router as proxy_router

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
SIGNALS_LOG = os.path.join(DATA_DIR, "signals.json")
ORDERS_LOG = os.path.join(DATA_DIR, "orders.json")
POSITIONS_LOG = os.path.join(DATA_DIR, "positions.json")
closed_positions: List[dict] = []
_placed_keys: Dict[str, dict] = {}  # idempotency keys ("sig:<id>" / "req:<id>") -> record

app = FastAPI(title="punch.trade", version="0.2.0")


# -------------------------------------------------------------- security --
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Rate-limit API traffic per client IP + security headers on all responses."""
    if request.url.path.startswith("/api") and request.url.path != "/api/health":
        try:
            security.rate_limit_api(request)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code,
                                content={"detail": e.detail},
                                headers=e.headers or {})
    response = await call_next(request)
    security.apply_security_headers(response)
    return response

# ---------------------------------------------------------------- auth --
def _check_token(token: str) -> bool:
    return token == config.API_TOKEN


def require_token(x_punch_token: str = Header(default="", alias="X-Punch-Token")):
    """REST auth: token travels in a header, never in the URL."""
    if not _check_token(x_punch_token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


def _rejection(e: risk.RiskError) -> HTTPException:
    return HTTPException(status_code=e.status,
                         detail={"code": e.code, "message": e.detail})


# ------------------------------------------------------------- ws hub ----
class Hub:
    def __init__(self):
        self.clients: List[WebSocket] = []
        self.signals: deque = deque(maxlen=50)

    async def connect(self, ws: WebSocket) -> None:
        # the endpoint accepts the socket itself (after the auth handshake)
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
    # execution-cost model (backtest.py::ExecutionCostConfig)
    startingCapital: float = 1_000_000.0
    positionPct: float = 1.0
    commissionBps: float = 0.0
    slippageBps: float = 0.0
    spreadBps: float = 0.0
    intrabarPolicy: str = "conservative"
    gapPolicy: str = "fill_at_next_open"


class ResearchReq(BacktestReq):
    trainPct: float = 0.70
    valPct: float = 0.15
    testPct: float = 0.15
    walkForwardWindows: int = 4
    bootstrapIterations: int = 200
    seed: int = 42


class BrokerReq(BaseModel):
    broker: str = "paper"


class OrderReq(BaseModel):
    broker: str = "paper"
    strategyId: Optional[str] = None
    signalId: Optional[str] = None
    clientRequestId: Optional[str] = None
    symbol: Optional[str] = None
    side: str = "buy"
    qty: int = Field(1, ge=1, le=config.MAX_QTY)
    entry: Optional[float] = Field(None, gt=0)
    targetPrice: Optional[float] = Field(None, gt=0)
    stopLoss: Optional[float] = Field(None, gt=0)


class ModeReq(BaseModel):
    mode: str


class ArmReq(BaseModel):
    broker: str


# ------------------------------------------------------------- startup ----
_loop: Optional[asyncio.AbstractEventLoop] = None


@app.on_event("startup")
async def startup() -> None:
    global feed, _loop, closed_positions
    os.makedirs(DATA_DIR, exist_ok=True)
    _loop = asyncio.get_running_loop()

    # ---- durable store: init schema, then one-time legacy import ----
    db.init_db()
    import_report = db.import_legacy_all()
    imported = {name: r["rows"] for name, r in import_report.items()}
    if any(imported.values()):
        print(f"[startup] legacy JSONL imported into SQLite: {imported}")
        for name, r in import_report.items():
            if r.get("archivedTo"):
                print(f"[startup] archived {name} -> {os.path.basename(r['archivedTo'])}")

    # seed idempotency keys from the durable order ledger so a restart
    # cannot cause a double-execution on retry
    for rec in db.read_orders():
        if rec.get("signalId"):
            _placed_keys.setdefault(f"sig:{rec['signalId']}", rec)
        if rec.get("clientRequestId"):
            _placed_keys.setdefault(f"req:{rec['clientRequestId']}", rec)

    # restore the closed-position ledger for analytics
    closed_positions = db.read_positions()

    # restore the signal ledger so /ws and /api/strategies/leaderboard see it
    if not hub.signals:
        hub.signals = deque(db.read_signals(100), maxlen=50)

    # restore the execution ledger (orders + closed trades) from SQLite
    execution.restore()

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
    print(f"[startup] risk: {config.startup_report()} | armed: {risk.armed()}")
    print("[startup] execution gate: "
          f"mode={risk.mode()} — real orders require LIVE mode + explicit arming")

    feed = LiveFeed(
        brokers.adapters["paper"],
        build_runners(),
        on_signal=on_signal,
        on_position_close=on_position_close,
    )
    feed.start()


def on_signal(signal: dict) -> None:
    # deterministic-id dedup: reconnects/restarts/double events must not
    # duplicate the same signal
    if any(s.get("id") == signal["id"] for s in hub.signals):
        return
    hub.signals.append(signal)
    _append_json(SIGNALS_LOG, signal)
    db.write_signal(signal)
    _loop.create_task(hub.broadcast({"type": "signal", "data": signal}))
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        _loop.create_task(_telegram_push(signal))


def _update_signal(signal_id: str, **fields) -> Optional[dict]:
    """Apply a status/lifecycle change to an in-memory signal (validated)."""
    from . import signals as sig_mod
    for s in hub.signals:
        if s.get("id") == signal_id:
            new = sig_mod.with_status(s, fields.pop("status"), **fields)
            hub.signals.remove(s)
            hub.signals.appendleft(new)
            return new
    return None


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
    db.write_position(position)
    _loop.create_task(hub.broadcast({"type": "position", "data": position}))


def _append_json(path: str, record: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[ledger] append failed for {path}: {e}")


# ------------------------------------------------------------- REST -----
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "brokers": list(brokers.adapters.keys()),
            "connected": [n for n in brokers.adapters if n != "paper"]}


@app.post("/api/system/login")
def system_login(request: Request, response: Response,
                 _: None = Depends(require_token),
                 _rl: None = Depends(security.rate_limit_login)) -> dict:
    """Dashboard session login. Requires the API token header; returns a
    revocable session cookie (httpOnly) + CSRF cookie for same-origin
    state changes. Raw session token is returned once in the body."""
    token = security.create_session(
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""))
    csrf = security.csrf_cookie()
    response.set_cookie(security.SESSION_COOKIE, token, httponly=True,
                        samesite="strict", max_age=security.SESSION_TTL_SECONDS,
                        path="/", secure=False)
    response.set_cookie(security.CSRF_COOKIE, csrf, httponly=False,
                        samesite="strict", max_age=security.SESSION_TTL_SECONDS,
                        path="/", secure=False)
    return {"session": token, "csrf": csrf,
            "expiresIn": security.SESSION_TTL_SECONDS}


@app.post("/api/system/logout")
def system_logout(request: Request, response: Response,
                  _session: None = Depends(security.require_session),
                  _csrf: None = Depends(security.require_csrf)) -> dict:
    """Revoke the dashboard session (CSRF-protected)."""
    security.revoke_session(request.cookies.get(security.SESSION_COOKIE))
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    response.delete_cookie(security.CSRF_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/system/storage")
def system_storage(_: None = Depends(require_token)) -> dict:
    """SQLite store info: engine, journal mode, schema, per-table counts."""
    try:
        return db.storage_status()
    except Exception as e:  # noqa: BLE001 — storage must never 500 the status page
        return {"engine": "sqlite", "error": str(e)}


@app.get("/api/strategies")
def strategies(_: None = Depends(require_token)) -> dict:
    return {"strategies": STRATEGIES}


_leaderboard_cache: Dict[str, dict] = {}
_status_cache: Dict[str, dict] = {}


@app.get("/api/strategies/leaderboard")
def strategy_leaderboard(_: None = Depends(require_token)) -> dict:
    """Ranked per-strategy backtest stats (paper source, cached 60s)."""
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
            m = stats["metrics"]
            rows.append({"id": s["id"], "name": s["name"], "symbol": s["symbol"],
                         "winRate": m["win_rate"],
                         "netReturnPct": round(
                             m["net_pnl"] / 1_000_000 * 100, 2),
                         "maxDrawdownPct": m["max_drawdown_pct"],
                         "sharpe": m["sharpe"],
                         "profitFactor": m["profit_factor"],
                         "trades": m["trades"]})
        except Exception:
            continue
    rows.sort(key=lambda r: (r["sharpe"], r["winRate"]), reverse=True)
    result = {"ts": now, "rows": rows}
    _leaderboard_cache.clear()
    _leaderboard_cache.update(result)
    return result


@app.post("/api/strategies/{strategy_id}/backtest")
def run_backtest(strategy_id: str, req: BacktestReq, _: None = Depends(require_token)) -> dict:
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
    try:
        costs = ExecutionCostConfig(
            starting_capital=req.startingCapital, position_pct=req.positionPct,
            commission_bps=req.commissionBps, slippage_bps=req.slippageBps,
            spread_bps=req.spreadBps, intrabar_policy=req.intrabarPolicy,
            gap_policy=req.gapPolicy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"source": req.broker, "bars": len(bars), **backtest(strategy, bars, costs)}


@app.post("/api/research/{strategy_id}")
def run_research(strategy_id: str, req: ResearchReq,
                 _: None = Depends(require_token)) -> dict:
    """Full research dossier: chronological splits, walk-forward,
    parameter stability, bootstrap, regime breakdown, quality gate."""
    from .research import ResearchConfig, research_report
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    if req.broker in brokers.adapters:
        adapter = brokers.adapters[req.broker]
    elif req.broker == "binance":
        adapter = CCXTBroker("", "", testnet=False)
    else:
        raise HTTPException(status_code=400,
                            detail=f"Broker '{req.broker}' not connected. Connect it first.")
    try:
        bars = adapter.get_historical_bars(strategy["symbol"], req.interval, req.days)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if len(bars) < 100:
        raise HTTPException(status_code=400,
                            detail="Need at least 100 bars for research")
    try:
        cfg = ResearchConfig(
            train_pct=req.trainPct, val_pct=req.valPct, test_pct=req.testPct,
            walk_forward_windows=req.walkForwardWindows,
            bootstrap_iterations=req.bootstrapIterations, seed=req.seed,
            costs=ExecutionCostConfig(
                starting_capital=req.startingCapital,
                position_pct=req.positionPct,
                commission_bps=req.commissionBps,
                slippage_bps=req.slippageBps,
                spread_bps=req.spreadBps,
                intrabar_policy=req.intrabarPolicy,
                gap_policy=req.gapPolicy))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        report = research_report(strategy, bars, cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.write_research_run(strategy_id, report)
    return {"source": req.broker, **report}


@app.get("/api/ai/status")
def ai_status(_: None = Depends(require_token)) -> dict:
    """Local LLM availability (auto-detected qwen2.5 model, never downloads)."""
    from .ai import status as ai_status_info
    return ai_status_info()


@app.post("/api/ai/analyze/{strategy_id}")
def ai_analyze(strategy_id: str, _: None = Depends(require_token)) -> dict:
    """Local-model assessment of one strategy's research dossier.

    Offline-safe: when no model is available the response carries an
    `error` hint and `analysis: null` — never a crash, never a secret.
    """
    from .ai import analyze as ai_analyze_info
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    try:
        from .research import ResearchConfig, research_report
        from .strategy_status import live_drift, compute_status
        adapter = brokers.adapters["paper"]
        bars = adapter.get_historical_bars(strategy["symbol"], "5m", 30)
        research = None
        if len(bars) >= 100:
            research = research_report(strategy, bars, ResearchConfig())
        st = compute_status(strategy["id"], strategy.get("status", "DRAFT"),
                            has_backtest=research is not None,
                            research=research, drift=None)
        drift = None
        try:
            drift = live_drift(strategy["id"], execution.closed_trades())
        except Exception:
            drift = None
    except Exception:
        research = None
        st = {"status": "UNKNOWN", "reason": "context build failed",
              "score": 0, "canPromoteTo": []}
        drift = None
    return ai_analyze_info(strategy, research=research,
                           status=st, drift=drift)


@app.get("/api/strategies/status")
def strategy_statuses(_: None = Depends(require_token)) -> dict:
    """Lifecycle status + composite score per strategy (research cached
    10 min; drift fed by the execution layer)."""
    from .research import ResearchConfig, research_report
    from .strategy_status import compute_status
    now = time.time()
    if _status_cache and now - _status_cache.get("ts", 0) < 600:
        return _status_cache
    adapter = brokers.adapters["paper"]
    rows = []
    for s in STRATEGIES:
        research = None
        try:
            bars = adapter.get_historical_bars(s["symbol"], "5m", 30)
            research = research_report(s, bars, ResearchConfig())
        except Exception:
            research = None
        st = compute_status(s["id"], s.get("status", "DRAFT"),
                            has_backtest=True, research=research, drift=None)
        rows.append({
            "id": s["id"], "name": s["name"], "symbol": s["symbol"],
            "status": st["status"], "reason": st["reason"],
            "score": st["score"], "canPromoteTo": st["canPromoteTo"],
            "qualityGate": (research or {}).get("qualityGate"),
        })
    result = {"ts": now, "rows": rows}
    _status_cache.clear()
    _status_cache.update(result)
    for r in rows:
        db.write_strategy_status(r["id"], {"ts": now, **r})
    return result


@app.get("/api/risk/state")
def risk_state(_: None = Depends(require_token)) -> dict:
    """Breaker, consecutive losses, reconciliation flag, sizing defaults."""
    return {
        "mode": risk.mode(),
        "armed": risk.armed(),
        "consecutiveLosses": risk.consecutive_losses(),
        "breakerOpen": risk.breaker_open(),
        "reconciliationOk": risk.reconciliation_ok(),
        "maxPositions": config.MAX_OPEN_POSITIONS,
        "maxQty": config.MAX_QTY,
        "maxDailyLossPct": config.MAX_DAILY_LOSS_PCT,
        "riskPerTradePct": config.RISK_PER_TRADE_PCT,
        "circuitBreakerLosses": config.CIRCUIT_BREAKER_LOSSES,
    }


class SizingReq(BaseModel):
    equity: float
    riskPct: Optional[float] = None
    entry: float
    stop: float
    side: str = "buy"


@app.post("/api/risk/sizing")
def risk_sizing(req: SizingReq, _: None = Depends(require_token)) -> dict:
    """Fixed-fractional position size for a trade."""
    try:
        return risk.size_position(
            equity=req.equity,
            risk_pct=req.riskPct if req.riskPct is not None
            else config.RISK_PER_TRADE_PCT,
            entry=req.entry, stop=req.stop, side=req.side)
    except risk.RiskError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code,
                                                          "message": e.detail})


@app.post("/api/risk/breaker/reset")
def risk_breaker_reset(_: None = Depends(require_token)) -> dict:
    """Manually reset the circuit breaker (also done by /api/system/stop)."""
    return risk.reset_breaker()


@app.get("/api/execution/ledger")
def execution_ledger(_: None = Depends(require_token)) -> dict:
    """Order state machine ledger (PENDING/SUBMITTED/FILLED/REJECTED/UNKNOWN)."""
    return {"orders": execution.ledger()}


@app.get("/api/execution/trades")
def execution_trades(_: None = Depends(require_token)) -> dict:
    """Closed CompletedTrades (one position = one trade), newest last."""
    return {"trades": list(reversed(execution.closed_trades()))}


@app.post("/api/execution/reconcile")
def execution_reconcile(req: BrokerReq, _: None = Depends(require_token)) -> dict:
    """Compare the ledger against the broker's view; gates live orders."""
    adapter = brokers.get(req.broker)
    return execution.reconcile(req.broker, adapter)


@app.get("/api/execution/reconciliation")
def execution_reconciliation(_: None = Depends(require_token)) -> dict:
    """Reconciliation state for every connected broker."""
    out = {}
    for name, adapter in brokers.adapters.items():
        out[name] = execution.reconcile(name, adapter)
    return {"brokers": out}


@app.post("/api/broker/kite/login-url")
def kite_login_url(req: LoginUrlReq, _: None = Depends(require_token)) -> dict:
    try:
        return {"url": login_url(req.api_key)}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/broker/kite/connect")
def kite_connect(req: KiteConnectReq, _: None = Depends(require_token)) -> dict:
    try:
        session = generate_session(req.api_key, req.api_secret, req.request_token)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    vault.save("kite", session)
    brokers.adapters["kite"] = KiteAdapter(session["api_key"], session["access_token"])
    return {"connected": True, "broker": "kite"}


@app.post("/api/broker/binance/connect")
def binance_connect(req: BinanceConnectReq, _: None = Depends(require_token)) -> dict:
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
def openalgo_connect(req: OpenAlgoConnectReq, _: None = Depends(require_token)) -> dict:
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
def broker_status(_: None = Depends(require_token)) -> dict:
    out = {}
    for name, adapter in brokers.adapters.items():
        try:
            out[name] = adapter.status()
        except BrokerError as e:
            out[name] = {"broker": name, "connected": False, "error": str(e)}
    return out


@app.post("/api/orders")
async def place_order(req: OrderReq, _: None = Depends(require_token)) -> dict:
    adapter = brokers.get(req.broker)

    # ---- idempotency: replay a previous request instead of re-executing
    idem_key = None
    if req.signalId:
        idem_key = f"sig:{req.signalId}"
    elif req.clientRequestId:
        idem_key = f"req:{req.clientRequestId}"
    if idem_key and idem_key in _placed_keys:
        return {**_placed_keys[idem_key], "duplicate": True}

    # ---- resolve the signal (authoritative values) or manual fallback
    signal = None
    signal_ts = None
    if req.signalId:
        signal = _find_signal(req.signalId)
        if signal is None:
            raise HTTPException(status_code=409,
                                detail={"code": "SIGNAL_NOT_FOUND",
                                        "message": f"unknown signal {req.signalId}"})
        signal_ts = signal.get("ts") or time.time()
        if not req.symbol:
            req.symbol = signal["symbol"]
        if req.symbol != signal["symbol"]:
            raise HTTPException(status_code=422,
                                detail={"code": "INVALID_INPUT",
                                        "message": "symbol does not match the signal"})
        req.entry = req.entry or signal["entry"]
        req.targetPrice = req.targetPrice or signal["targetPrice"]
        req.stopLoss = req.stopLoss or signal["stopLoss"]

    if req.entry is None or req.targetPrice is None or req.stopLoss is None:
        if req.strategyId is None or feed is None:
            raise HTTPException(status_code=400,
                                detail={"code": "INVALID_INPUT",
                                        "message": "provide entry/targetPrice/stopLoss "
                                                   "or a signalId"})
        strategy = get_strategy(req.strategyId)
        if strategy is None:
            raise HTTPException(status_code=404, detail="Unknown strategy")
        series = feed.bars.get(strategy["symbol"])
        if not series:
            raise HTTPException(status_code=409,
                                detail={"code": "NO_BARS",
                                        "message": f"No live bars yet for {strategy['symbol']}"})
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

    # ---- pre-trade risk gate (typed rejections)
    try:
        risk.check(broker=req.broker, signal=signal, signal_ts=signal_ts,
                   feed=feed, symbol=req.symbol)
        open_positions = 0
        try:
            open_positions = len([p for p in adapter.get_positions()
                                  if p.get("status") == "open"])
        except BrokerError:
            pass
        risk.enforce_limits(qty=req.qty, open_positions=open_positions,
                            daily_loss_pct=_daily_loss_pct(),
                            entry=req.entry, target=req.targetPrice,
                            stop=req.stopLoss)
    except risk.RiskError as e:
        if req.signalId:
            _update_signal(req.signalId, status="REJECTED", rejection=e.code)
            _loop.create_task(hub.broadcast(
                {"type": "signal_update",
                 "data": {"id": req.signalId, "status": "REJECTED",
                          "rejection": e.code}}))
        raise _rejection(e)

    try:
        result = await asyncio.to_thread(
            adapter.place_bracket, req.symbol, req.side, req.qty,
            req.entry, req.targetPrice, req.stopLoss, True, None, targets)
    except BrokerError as e:
        if req.clientRequestId or req.signalId:
            order_id = req.clientRequestId or req.signalId or "unknown"
            execution.mark(order_id, "REJECTED")
        raise HTTPException(status_code=502, detail=str(e))

    # execution ledger: broker order id is the key (paper closes match it)
    order_id = result.get("orderId") or req.clientRequestId or req.signalId
    execution.record_order(
        order_id, signal_id=req.signalId, strategy_id=req.strategyId,
        symbol=req.symbol, side=req.side, qty=req.qty, entry=req.entry,
        broker=req.broker, client_request_id=req.clientRequestId)
    execution.mark(order_id, "FILLED" if req.broker == "paper" else "SUBMITTED")

    # durable mirror of the execution ledger record
    led_rec = execution.get_order(order_id)
    if led_rec:
        db.write_order(led_rec)

    if req.signalId:
        _update_signal(req.signalId, status="EXECUTED")
        _loop.create_task(hub.broadcast(
            {"type": "signal_update",
             "data": {"id": req.signalId, "status": "EXECUTED"}}))

    record = {"ts": time.time(), "broker": req.broker, "strategyId": req.strategyId,
              "signalId": req.signalId, "clientRequestId": req.clientRequestId,
              "symbol": req.symbol, "side": req.side, "qty": req.qty,
              "entry": req.entry, "target": req.targetPrice, "stop": req.stopLoss,
              "mode": risk.mode(), "result": result}
    record = security.sanitize_dict(record)
    _append_json(ORDERS_LOG, record)
    if idem_key:
        _placed_keys[idem_key] = record
    return record


def _find_signal(signal_id: str) -> Optional[dict]:
    for s in hub.signals:
        if s.get("id") == signal_id:
            return s
    return None


def _daily_loss_pct() -> float:
    """Realized PnL % of today's closed positions (paper ledger)."""
    start_of_day = int(time.time() // 86400) * 86400
    return sum((p.get("pnl_pct") or 0.0)
               for p in closed_positions if (p.get("opened_at") or 0) >= start_of_day)


@app.get("/api/positions")
def positions(broker: str = "paper", _: None = Depends(require_token)) -> dict:
    adapter = brokers.get(broker)
    try:
        return {"broker": broker, "positions": adapter.get_positions()}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/fills")
def fills(broker: str = "paper", _: None = Depends(require_token)) -> dict:
    adapter = brokers.get(broker)
    try:
        return {"broker": broker, "fills": adapter.get_fills()}
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/signals/last")
def last_signals(_: None = Depends(require_token)) -> dict:
    return {"signals": list(hub.signals)}


@app.get("/api/signals/history")
def signal_history(_: None = Depends(require_token)) -> dict:
    # Prefer the live in-memory hub: records there carry live AI scores.
    # Fall back to the audit file for a cold start with no hub yet.
    rows = list(hub.signals)
    if not rows and os.path.exists(SIGNALS_LOG):
        with open(SIGNALS_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return {"signals": rows[-100:]}


@app.get("/api/orders/history")
def order_history(_: None = Depends(require_token)) -> dict:
    rows = []
    if os.path.exists(ORDERS_LOG):
        with open(ORDERS_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return {"orders": rows}


@app.get("/api/analytics")
def analytics(_: None = Depends(require_token)) -> dict:
    """Dashboard aggregate: performance from the closed-position ledger.

    Multi-TP positions close in fractions; each event's pnl_pct is per-unit,
    so events are weighted by qty/qty_total to get the position-level return.
    """
    def weighted(p: dict) -> float:
        qty = p.get("qty") or 1.0
        total = p.get("qty_total") or qty
        return (p.get("pnl_pct") or 0.0) * qty / total

    wins = [p for p in closed_positions if weighted(p) > 0]
    losses = [p for p in closed_positions if weighted(p) <= 0]
    equity = 0.0
    curve = []
    for p in sorted(closed_positions, key=lambda x: x.get("opened_at", 0)):
        equity += weighted(p)
        curve.append({"ts": p.get("opened_at", 0), "equity": round(equity, 2)})
    open_positions = []
    try:
        open_positions = brokers.adapters["paper"].get_positions()
    except Exception as e:
        print(f"[analytics] paper positions unavailable: {e}")
    return {
        "closed": len(closed_positions),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(len(wins) / len(closed_positions) * 100, 1) if closed_positions else 0.0,
        "netPnlPct": round(sum(weighted(p) for p in closed_positions), 2),
        "openPositions": len([p for p in open_positions if p.get("status") == "open"]),
        "equityCurve": curve,
        "recentCloses": list(reversed(closed_positions[-10:])),
    }


@app.get("/api/candles")
def candles(symbol: str, _: None = Depends(require_token), limit: int = 120) -> dict:
    """Live candle series for the chart panel (falls back to paper history)."""
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
    """Auth handshake: the client sends {"type":"auth","token":...} within 5s.

    The token never appears in the URL — the WS endpoint accepts the
    connection, waits for the auth message, and closes 4401 otherwise.
    """
    await ws.accept()
    authed = False
    try:
        msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        data = json.loads(msg)
        authed = (data.get("type") == "auth"
                  and _check_token(str(data.get("token", ""))))
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError,
            AttributeError, KeyError):
        pass
    if not authed:
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "auth_ok"})
    except Exception:
        pass
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


# ------------------------------------------------------- system -------
@app.get("/api/system/status")
def system_status(_: None = Depends(require_token)) -> dict:
    return {
        **risk.status(),
        "feeds": feed.health() if feed is not None else [],
        "signals": len(hub.signals),
        "ledger": {"signals": os.path.exists(SIGNALS_LOG),
                   "orders": os.path.exists(ORDERS_LOG),
                   "positions": os.path.exists(POSITIONS_LOG)},
        "version": app.version,
    }


@app.post("/api/system/mode")
def system_mode(req: ModeReq, _: None = Depends(require_token)) -> dict:
    try:
        return risk.set_mode(req.mode)
    except risk.RiskError as e:
        raise _rejection(e)


@app.post("/api/system/arm")
def system_arm(req: ArmReq, _: None = Depends(require_token)) -> dict:
    try:
        return risk.arm(req.broker, connected=req.broker in brokers.adapters)
    except risk.RiskError as e:
        raise _rejection(e)


@app.post("/api/system/stop")
def system_stop(_: None = Depends(require_token)) -> dict:
    """Emergency stop: research mode + everything disarmed."""
    return risk.stop()


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