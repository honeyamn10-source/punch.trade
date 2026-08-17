"""Execution layer — order state machine, reconciliation, live trades.

Order lifecycle (typed transitions, UNKNOWN is the honest timeout state):

    PENDING -> SUBMITTED -> FILLED
                      |-> REJECTED
                      |-> CANCELLED
    SUBMITTED --timeout--> UNKNOWN   (reconciliation must resolve it)

The ledger is the single source of truth for "what did we ask the broker
to do". Reconciliation compares it against the broker's own view; while
it disagrees, the risk engine's reconciliation gate refuses new LIVE
orders (paper stays usable).

Closed positions become CompletedTrade records (one position = one
trade) which feed the strategy drift checks, the dashboard and — later —
the SQLite store. Paper closes are the live-data source until a real
broker is connected.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from . import config
from . import pnl as pnl_mod
from . import risk
from .trades import CompletedTrade

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")

PENDING = "PENDING"
SUBMITTED = "SUBMITTED"
FILLED = "FILLED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
UNKNOWN = "UNKNOWN"

_TRANSITIONS = {
    PENDING: {SUBMITTED, FILLED, REJECTED, CANCELLED},
    SUBMITTED: {FILLED, REJECTED, CANCELLED, UNKNOWN},
    FILLED: set(),
    REJECTED: set(),
    CANCELLED: set(),
    UNKNOWN: {FILLED, CANCELLED},  # resolved by reconciliation
}


class OrderStateError(ValueError):
    pass


def transition(current: str, new: str) -> str:
    if current not in _TRANSITIONS or new not in _TRANSITIONS[current]:
        raise OrderStateError(f"illegal order transition {current} -> {new}")
    return new


# ------------------------------------------------------------- ledger ----
_ledger: Dict[str, dict] = {}
_trades: List[dict] = []
TRADES_LOG = os.path.join(DATA_DIR, "trades.json")


def _load_trades() -> None:
    if _trades or not os.path.exists(TRADES_LOG):
        return
    try:
        with open(TRADES_LOG, "r", encoding="utf-8") as f:
            _trades.extend(json_loads_lines(f))
    except Exception:
        pass


def json_loads_lines(f) -> List[dict]:
    import json
    return [json.loads(line) for line in f if line.strip()]


def record_order(order_id: str, *, signal_id: Optional[str],
                 strategy_id: Optional[str], symbol: str, side: str,
                 qty: int, entry: float, broker: str,
                 client_request_id: Optional[str] = None) -> dict:
    """Register a new order in PENDING state. Idempotent per order_id."""
    if order_id in _ledger:
        return _ledger[order_id]
    rec = {
        "id": order_id, "signalId": signal_id, "strategyId": strategy_id,
        "symbol": symbol, "side": side, "qty": qty, "entry": entry,
        "broker": broker, "clientRequestId": client_request_id,
        "status": PENDING,
        "ts": time.time(), "updatedTs": time.time(),
    }
    _ledger[order_id] = rec
    return rec


def mark(order_id: str, new_status: str) -> Optional[dict]:
    """Transition a ledger order; returns the updated record (or None)."""
    rec = _ledger.get(order_id)
    if rec is None:
        return None
    transition(rec["status"], new_status)
    rec["status"] = new_status
    rec["updatedTs"] = time.time()
    return rec


def get_order(order_id: str) -> Optional[dict]:
    return _ledger.get(order_id)


def ledger() -> List[dict]:
    return sorted(_ledger.values(), key=lambda r: r["ts"])


def stale_unknown_orders(now: Optional[float] = None,
                         timeout: float = 60.0) -> List[dict]:
    """SUBMITTED orders that have not filled within `timeout` -> UNKNOWN."""
    now = now if now is not None else time.time()
    out = []
    for rec in _ledger.values():
        if (rec["status"] == SUBMITTED
                and now - rec["updatedTs"] > timeout):
            rec["status"] = UNKNOWN
            rec["updatedTs"] = now
            out.append(rec)
    return out


# -------------------------------------------------- closed trades ----
_CLOSE_REASON_MAP = {"SL": "STOP", "TP1": "TP1", "TP2": "TP2", "TP3": "TP3"}


def record_closed_trade(position_id: str, events: List[dict]) -> Optional[dict]:
    """One position's exit events (TP partials + final close) -> ONE
    CompletedTrade. `events` must include a status=="closed" final event.
    PnL math happens only in trades.CompletedTrade.close (central source)."""
    final = next((e for e in events if e.get("status") == "closed"), None)
    if final is None:
        return None
    rec = _ledger.get(position_id, {})
    side = final["side"]
    entry = rec.get("entry") or final["entry"]
    trade = CompletedTrade(
        signal_id=rec.get("signalId"),
        strategy_id=rec.get("strategyId") or "manual",
        strategy_version="1.0.0",
        symbol=final["symbol"], side=side, qty=final["qty_total"],
        entry_ts=rec.get("ts") or final.get("opened_at") or final.get("ts", time.time()),
        entry_price=entry, timeframe="5m")
    for e in events:
        label = e.get("exit", "MANUAL")
        if label == "SL":
            label = "STOP"
        reason = _CLOSE_REASON_MAP.get(label, "MANUAL")
        if reason.startswith("TP") and e.get("status") == "open":
            trade.add_fill(e.get("ts", time.time()), reason, e["exit_price"], e["qty"])
    trade.close(final.get("ts", time.time()), final["exit_price"],
                _CLOSE_REASON_MAP.get(final.get("exit", "MANUAL"), "MANUAL"))
    d = trade.to_dict()
    _trades.append(d)
    with open(TRADES_LOG, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(d) + "\n")
    risk.record_trade_result(win=d["netPnl"] > 0)
    return d


def closed_trades(limit: int = 500) -> List[dict]:
    _load_trades()
    return list(_trades)[-limit:]


# ---------------------------------------------------- reconciliation ----
def reconcile(broker: str, adapter) -> Dict:
    """Compare ledger vs broker view. Sets the risk reconciliation gate.

    Mismatch rules:
    - broker positions exist that the ledger never asked for -> mismatch
    - ledger order still SUBMITTED (no broker confirmation) -> mismatch
    - broker API errors -> gate closed (can't prove state)
    """
    stale_unknown_orders()
    mismatches = []
    try:
        positions = adapter.get_positions()
    except Exception as e:
        positions = None
        mismatches.append({"type": "BROKER_UNAVAILABLE",
                           "detail": f"{type(e).__name__}: {e}"})
    open_ledger = [r for r in _ledger.values() if r["status"] in (FILLED,)]
    ledger_ids = {r["id"] for r in open_ledger}
    if positions is not None:
        for p in positions:
            if p.get("status", "open") != "open":
                continue
            if p["id"] not in ledger_ids and broker != "paper":
                mismatches.append({"type": "UNTRACKED_POSITION",
                                   "detail": f"{p['symbol']} qty {p.get('qty')} "
                                             f"id {p['id']} not in ledger"})
    unknown = [r for r in _ledger.values() if r["status"] == UNKNOWN]
    for r in unknown:
        mismatches.append({"type": "UNKNOWN_ORDER",
                           "detail": f"order {r['id']} {r['symbol']} "
                                     f"state unknown"})
    ok = not mismatches
    risk.set_reconciliation_ok(ok)
    return {
        "broker": broker,
        "ok": ok,
        "checkedAt": time.time(),
        "ledgerOrders": len(_ledger),
        "openLedger": len(open_ledger),
        "unknownOrders": len(unknown),
        "mismatches": mismatches,
    }