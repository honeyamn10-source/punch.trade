"""Canonical signal model + state machine.

Lifecycle:

    CANDIDATE --activate--> ACTIVE --execute--> EXECUTED --partial--> PARTIAL
                                       \--reject--> REJECTED --close--> CLOSED
    ACTIVE --expire--> EXPIRED · CANDIDATE/ACTIVE --invalidate--> INVALIDATED

Terminal states: EXECUTED's children CLOSED, PARTIAL, INVALIDATED, EXPIRED,
REJECTED.

Signal identity is deterministic (strategy_id + version + symbol + timeframe
+ close_time + side) so reconnects/restarts/double events cannot duplicate.
"""

from __future__ import annotations

from typing import Dict, Optional

# ------------------------------------------------------------- states ----
CANDIDATE = "CANDIDATE"
ACTIVE = "ACTIVE"
EXECUTED = "EXECUTED"
PARTIAL = "PARTIAL"
CLOSED = "CLOSED"
INVALIDATED = "INVALIDATED"
EXPIRED = "EXPIRED"
REJECTED = "REJECTED"

TERMINAL = {CLOSED, INVALIDATED, EXPIRED, REJECTED}

_TRANSITIONS: Dict[str, set] = {
    CANDIDATE: {ACTIVE, EXPIRED, INVALIDATED},
    ACTIVE: {EXECUTED, PARTIAL, CLOSED, EXPIRED, INVALIDATED, REJECTED},
    EXECUTED: {PARTIAL, CLOSED, INVALIDATED},
    PARTIAL: {CLOSED, INVALIDATED},
    CLOSED: set(),
    INVALIDATED: set(),
    EXPIRED: set(),
    REJECTED: set(),
}


class SignalStateError(ValueError):
    pass


def transition(current: str, new: str) -> str:
    """Validate a state transition; raises SignalStateError when illegal."""
    if current not in _TRANSITIONS:
        raise SignalStateError(f"unknown signal state '{current}'")
    if new not in _TRANSITIONS:
        raise SignalStateError(f"unknown signal state '{new}'")
    if new not in _TRANSITIONS[current]:
        raise SignalStateError(f"illegal signal transition {current} -> {new}")
    return new


def is_terminal(state: str) -> bool:
    return state in TERMINAL


# ------------------------------------------------------------- helpers ----
def with_status(signal: Dict, new_status: str, **extra) -> Dict:
    """Return a copy of the signal dict with a validated status change."""
    current = signal.get("status", ACTIVE)
    transition(current, new_status)
    out = dict(signal)
    out["status"] = new_status
    out.update(extra)
    return out


def expired_at(signal: Dict, ttl_seconds: float) -> Optional[float]:
    """expires_at for a signal (None if TTL is disabled/<= 0)."""
    if ttl_seconds <= 0:
        return None
    return (signal.get("ts") or 0) + ttl_seconds


def is_expired(signal: Dict, now: Optional[float] = None) -> bool:
    import time
    now = now if now is not None else time.time()
    expires = signal.get("expiresAt")
    return expires is not None and now > expires