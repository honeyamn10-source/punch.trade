"""Observability — structured event log, counters, request tracing.

- Every API request is logged as one JSON line (data/logs/events.jsonl)
  with a request_id, method, path, status and duration.
- Counters (signals, orders, fills, rejections, errors, ...) are kept
  in-process and exposed via /api/v1/system/metrics.
- Nothing sensitive is ever written: events are sanitized with the same
  sanitizer the rest of the app uses.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Dict, Optional

from . import security

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
EVENTS_LOG = os.path.join(LOG_DIR, "events.jsonl")

started_at = time.time()

_counters: Dict[str, int] = {}
_counters_lock = threading.Lock()

# error counters by status class (4xx/5xx)
_errors: Dict[str, int] = {}
_errors_lock = threading.Lock()


def incr(name: str, n: int = 1) -> None:
    with _counters_lock:
        _counters[name] = _counters.get(name, 0) + n


def error_incr(status_code: int) -> None:
    bucket = f"{status_code // 100}xx"
    with _errors_lock:
        _errors[bucket] = _errors.get(bucket, 0) + 1
    incr("errors")


def counters() -> Dict[str, int]:
    with _counters_lock:
        return dict(_counters)


def errors() -> Dict[str, int]:
    with _errors_lock:
        return dict(_errors)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_event(kind: str, payload: Optional[Dict] = None) -> None:
    """Append one sanitized JSON line to the event log (best-effort)."""
    rec = {"ts": time.time(), "kind": kind, **({} if payload is None else payload)}
    try:
        rec = security.sanitize_dict(rec)
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 — logging must never break the app
        pass


def log_request(request_id: str, method: str, path: str,
                status_code: int, ms: float) -> None:
    log_event("api.request", {
        "requestId": request_id, "method": method, "path": path,
        "status": status_code, "ms": round(ms, 1)})


def uptime() -> float:
    return time.time() - started_at