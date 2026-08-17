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
from typing import Dict, List, Optional

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
_armed: List[str] = []  # live brokers explicitly armed this session
_emergency_stop = False
_started_at = time.time()


def mode() -> str:
    return _mode


def armed() -> List[str]:
    return list(_armed)


def emergency_stopped() -> bool:
    return _emergency_stop


def started_at() -> float:
    return _started_at


def set_mode(new_mode: str) -> dict:
    """Switch execution mode. Entering live never auto-arms anything."""
    global _mode, _emergency_stop
    if new_mode not in config.VALID_MODES:
        raise RiskError("INVALID_MODE", f"mode must be one of {config.VALID_MODES}",
                        status=400)
    if new_mode == "live" and config.API_TOKEN == config.DEFAULT_TOKEN:
        raise RiskError("DEMO_TOKEN_BLOCKS_LIVE",
                        "LIVE mode refused while PUNCH_TOKEN is the default demo token",
                        status=403)
    _mode = new_mode
    if new_mode != "live":
        _armed.clear()
    _emergency_stop = False
    return status()


def arm(broker: str, connected: bool) -> dict:
    """Arm a real broker for live execution. Never persists."""
    if _mode != "live":
        raise RiskError("NOT_LIVE_MODE",
                        f"arming requires LIVE mode (current: {_mode})", status=409)
    if broker == "paper":
        raise RiskError("PAPER_NEVER_ARMS", "the paper broker never needs arming",
                        status=400)
    if not connected:
        raise RiskError("BROKER_NOT_CONNECTED",
                        f"broker '{broker}' is not connected", status=409)
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
    }


# ------------------------------------------------------------ the gate --
def check(*, broker: str, signal: Optional[dict] = None,
          feed: Optional[object] = None, symbol: Optional[str] = None,
          open_positions: int = 0, signal_ts: Optional[float] = None,
          stale_after: Optional[float] = None) -> None:
    """Pre-trade checklist. Raises RiskError on the first failed rule."""
    if _mode == "research":
        raise RiskError("MODE_BLOCKED",
                        "research mode: order execution is disabled", status=409)
    if broker == "paper":
        pass  # allowed in paper and live modes
    elif _mode != "live":
        raise RiskError("BROKER_NOT_ALLOWED",
                        f"'{broker}' is a real broker — switch to LIVE mode first",
                        status=409)
    elif broker not in _armed:
        raise RiskError("NOT_ARMED",
                        f"broker '{broker}' is not armed for LIVE execution "
                        "(POST /api/system/arm)", status=409)

    if feed is not None and symbol is not None:
        last_ts = getattr(feed, "last_ts", {}).get(symbol, 0.0)
        limit = stale_after or (config.LIVE_FEED_STALE_AFTER
                                if broker != "paper" else config.FEED_STALE_AFTER)
        if not last_ts or (time.time() - last_ts) > limit:
            raise RiskError("FEED_STALE",
                            f"market feed for {symbol} is stale — no bar in "
                            f"{time.time() - last_ts:.0f}s (limit {limit:.0f}s)",
                            status=409)

    if signal is not None and signal_ts is not None:
        age = time.time() - signal_ts
        if age > config.SIGNAL_TTL_SECONDS:
            raise RiskError("SIGNAL_EXPIRED",
                            f"signal is {age:.0f}s old — expired after "
                            f"{config.SIGNAL_TTL_SECONDS:.0f}s", status=409)


def enforce_limits(*, qty: int, open_positions: int,
                   daily_loss_pct: float, entry: float,
                   target: float, stop: float) -> None:
    if qty < 1:
        raise RiskError("INVALID_QTY", "qty must be >= 1", status=422)
    if qty > config.MAX_QTY:
        raise RiskError("MAX_QTY", f"qty {qty} exceeds limit {config.MAX_QTY}",
                        status=409)
    if open_positions >= config.MAX_OPEN_POSITIONS:
        raise RiskError("MAX_POSITIONS",
                        f"{open_positions} positions open — limit is "
                        f"{config.MAX_OPEN_POSITIONS}", status=409)
    if daily_loss_pct <= -config.MAX_DAILY_LOSS_PCT:
        raise RiskError("DAILY_LOSS_LIMIT",
                        f"daily realized loss {daily_loss_pct:.2f}% reached the "
                        f"limit of -{config.MAX_DAILY_LOSS_PCT:.1f}%", status=409)
    if entry <= 0 or target <= 0 or stop <= 0:
        raise RiskError("INVALID_PRICE", "entry/target/stop must be positive",
                        status=422)