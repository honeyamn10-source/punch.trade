"""Risk engine — execution modes, arming, and the pre-trade gate.

Every order goes through `check()` before it reaches a broker. Rejections
are typed (machine-readable `code` + human `detail`) so the UI and the
extension can render the right reason instead of a generic 4xx.

Execution modes:
- research : all order execution blocked (signals still stream)
- paper    : the paper broker may execute (default)
- live     : paper + real brokers, but every real broker must be armed
             explicitly with POST /api/system/arm first

Safety properties:
- Arming state lives in memory only — a server restart drops it. LIVE
  mode therefore always starts disarmed.
- Emergency stop (POST /api/system/stop) drops to research mode and
  disarms everything; no restart required.
- Orders for live brokers are refused while PUNCH_TOKEN is the default
  demo token (see config.validate_config for the startup-side guard).
"""

from __future__ import annotations

import time

from . import config


class RiskError(Exception):
    """Typed pre-trade rejection. `code` is stable for clients."""

    def __init__(self, code: str, detail: str, status: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


# ---------------------------------------------------------------- state --
_mode = config.MODE
_armed: list[str] = []  # live brokers explicitly armed this session
_emergency_stop = False
_started_at = time.time()


def mode() -> str:
    return _mode


def armed() -> list[str]:
    return list(_armed)


def emergency_stopped() -> bool:
    return _emergency_stop


def started_at() -> float:
    return _started_at


def set_mode(new_mode: str) -> dict:
    """Switch execution mode. Entering live never auto-arms anything."""
    global _mode, _emergency_stop
    if new_mode not in config.VALID_MODES:
        raise RiskError("INVALID_MODE", f"mode must be one of {config.VALID_MODES}", status=400)
    if new_mode == "live" and config.API_TOKEN == config.DEFAULT_TOKEN:
        raise RiskError(
            "DEMO_TOKEN_BLOCKS_LIVE",
            "LIVE mode refused while PUNCH_TOKEN is the default demo token",
            status=403,
        )
    _mode = new_mode
    if new_mode != "live":
        _armed.clear()
    _emergency_stop = False
    return status()


def arm(broker: str, connected: bool) -> dict:
    """Arm a real broker for live execution. Never persists."""
    if _mode != "live":
        raise RiskError(
            "NOT_LIVE_MODE", f"arming requires LIVE mode (current: {_mode})", status=409
        )
    if broker == "paper":
        raise RiskError("PAPER_NEVER_ARMS", "the paper broker never needs arming", status=400)
    if not connected:
        raise RiskError("BROKER_NOT_CONNECTED", f"broker '{broker}' is not connected", status=409)
    if broker not in _armed:
        _armed.append(broker)
    return status()


def stop() -> dict:
    """Emergency stop: research mode, everything disarmed."""
    global _mode, _emergency_stop
    _mode = "research"
    _armed.clear()
    _emergency_stop = True
    return status()


def status() -> dict:
    return {
        "mode": _mode,
        "armed": list(_armed),
        "emergencyStop": _emergency_stop,
        "startedAt": _started_at,
        "uptimeSec": round(time.time() - _started_at, 1),
        "signalsTtlSec": config.SIGNAL_TTL_SECONDS,
        "consecutiveLosses": _consecutive_losses,
        "breakerOpen": _breaker_open,
        "reconciliationOk": _recon_ok,
    }


# ------------------------------------------------------------ the gate --
def check(
    *,
    broker: str,
    signal: dict | None = None,
    feed: object | None = None,
    symbol: str | None = None,
    open_positions: int = 0,
    signal_ts: float | None = None,
    stale_after: float | None = None,
) -> None:
    """Pre-trade checklist. Raises RiskError on the first failed rule."""
    if _mode == "research":
        raise RiskError("MODE_BLOCKED", "research mode: order execution is disabled", status=409)
    if broker == "paper":
        pass  # allowed in paper and live modes
    elif _mode != "live":
        raise RiskError(
            "BROKER_NOT_ALLOWED",
            f"'{broker}' is a real broker — switch to LIVE mode first",
            status=409,
        )
    elif broker not in _armed:
        raise RiskError(
            "NOT_ARMED",
            f"broker '{broker}' is not armed for LIVE execution (POST /api/system/arm)",
            status=409,
        )

    check_circuit_breaker()
    if broker != "paper":
        check_reconciliation()

    if feed is not None and symbol is not None:
        last_ts = getattr(feed, "last_ts", {}).get(symbol, 0.0)
        limit = stale_after or (
            config.LIVE_FEED_STALE_AFTER if broker != "paper" else config.FEED_STALE_AFTER
        )
        if not last_ts or (time.time() - last_ts) > limit:
            raise RiskError(
                "FEED_STALE",
                f"market feed for {symbol} is stale — no bar in "
                f"{time.time() - last_ts:.0f}s (limit {limit:.0f}s)",
                status=409,
            )

    if signal is not None and signal_ts is not None:
        age = time.time() - signal_ts
        if age > config.SIGNAL_TTL_SECONDS:
            raise RiskError(
                "SIGNAL_EXPIRED",
                f"signal is {age:.0f}s old — expired after {config.SIGNAL_TTL_SECONDS:.0f}s",
                status=409,
            )


def enforce_limits(
    *,
    qty: int,
    open_positions: int,
    daily_loss_pct: float,
    entry: float,
    target: float,
    stop: float,
) -> None:
    if qty < 1:
        raise RiskError("INVALID_QTY", "qty must be >= 1", status=422)
    if qty > config.MAX_QTY:
        raise RiskError("MAX_QTY", f"qty {qty} exceeds limit {config.MAX_QTY}", status=409)
    if open_positions >= config.MAX_OPEN_POSITIONS:
        raise RiskError(
            "MAX_POSITIONS",
            f"{open_positions} positions open — limit is {config.MAX_OPEN_POSITIONS}",
            status=409,
        )
    if daily_loss_pct <= -config.MAX_DAILY_LOSS_PCT:
        raise RiskError(
            "DAILY_LOSS_LIMIT",
            f"daily realized loss {daily_loss_pct:.2f}% reached the "
            f"limit of -{config.MAX_DAILY_LOSS_PCT:.1f}%",
            status=409,
        )
    if entry <= 0 or target <= 0 or stop <= 0:
        raise RiskError("INVALID_PRICE", "entry/target/stop must be positive", status=422)


# ------------------------------------------------------ circuit breaker --
_consecutive_losses = 0
_breaker_open = False
_breaker_opened_at: float | None = None


def consecutive_losses() -> int:
    return _consecutive_losses


def breaker_open() -> bool:
    return _breaker_open


def record_trade_result(win: bool, realized_loss: float = 0.0) -> dict:
    """Feed realized outcomes into the breaker + daily-loss accounting.
    Returns the breaker state."""
    global _consecutive_losses, _breaker_open, _breaker_opened_at
    if not win:
        _consecutive_losses += 1
        if _consecutive_losses >= config.CIRCUIT_BREAKER_LOSSES:
            _breaker_open = True
            _breaker_opened_at = time.time()
    else:
        _consecutive_losses = 0
        if _breaker_open:
            # a win after the breaker opened resets it
            _breaker_open = False
            _breaker_opened_at = None
    return {"consecutiveLosses": _consecutive_losses, "breakerOpen": _breaker_open}


def check_circuit_breaker() -> None:
    if _breaker_open:
        raise RiskError(
            "CIRCUIT_BREAKER",
            f"circuit breaker open after {config.CIRCUIT_BREAKER_LOSSES} "
            f"consecutive losses (reset on next win or /api/system/stop)",
            status=409,
        )


def reset_breaker() -> dict:
    global _consecutive_losses, _breaker_open, _breaker_opened_at
    _consecutive_losses = 0
    _breaker_open = False
    _breaker_opened_at = None
    return {"consecutiveLosses": 0, "breakerOpen": False}


# -------------------------------------------------------------- sizing ----
def size_position(
    *, equity: float, risk_pct: float, entry: float, stop: float, side: str = "buy"
) -> dict:
    """Fixed-fractional position sizing.

    risk per trade = equity * risk_pct (default config.RISK_PER_TRADE_PCT).
    qty = risk_amount / |entry - stop|, floored, capped at MAX_QTY.
    Returns {qty, riskAmount, riskPerShare, equity}.
    """
    if equity <= 0:
        raise RiskError("INVALID_EQUITY", "equity must be positive", status=422)
    if not 0 < risk_pct <= 1:
        raise RiskError("INVALID_RISK_PCT", "risk_pct must be in (0, 1]", status=422)
    if entry <= 0 or stop <= 0 or entry == stop:
        raise RiskError("INVALID_PRICE", "entry/stop must be positive and differ", status=422)
    risk_amount = equity * risk_pct
    distance = abs(entry - stop)
    qty = int(risk_amount / distance)
    qty = min(qty, config.MAX_QTY)
    return {
        "qty": max(qty, 0),
        "riskAmount": round(risk_amount, 2),
        "riskPerShare": round(distance, 4),
        "equity": round(equity, 2),
    }


# ----------------------------------------------------- reconciliation ----
_recon_ok = True


def reconciliation_ok() -> bool:
    return _recon_ok


def set_reconciliation_ok(ok: bool) -> None:
    """Called by the execution reconciliation pass. LIVE orders are gated
    on this — if broker/local state can't be matched, new orders wait."""
    global _recon_ok
    _recon_ok = bool(ok)


def check_reconciliation() -> None:
    if not _recon_ok:
        raise RiskError(
            "RECONCILIATION_FAILED",
            "broker/local state could not be reconciled — refusing "
            "new orders until reconciliation passes",
            status=409,
        )
