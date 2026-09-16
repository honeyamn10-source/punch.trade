"""Provider-specific throttling + bounded retry with backoff.

Every provider adapter funnels its HTTP traffic through
``throttled_request`` so free-tier quotas are respected:

- sliding-window per-provider rate limits (calls/minute, calls/day)
- ``429 Too Many Requests`` honours ``Retry-After``
- transient failures (429/500/502/503/504/timeouts) retry with bounded
  exponential backoff + jitter; auth/validation errors never retry
- never loops forever: bounded attempts, capped waits
"""

from __future__ import annotations

import random
import threading
import time

import httpx

TRANSIENT = (429, 500, 502, 503, 504)
MAX_BACKOFF_SECONDS = 30.0


class RateLimiter:
    """Per-provider sliding-window limiter (thread-safe)."""

    def __init__(self, calls_per_minute: float = 30.0, calls_per_day: int | None = None):
        self.cpm = calls_per_minute
        self.cpd = calls_per_day
        self._lock = threading.Lock()
        self._window: list[float] = []
        self._day: list[float] = []

    def wait_if_needed(self, now: float | None = None) -> float:
        """Return seconds to sleep before the next call (0 if free)."""
        now = now if now is not None else time.time()
        with self._lock:
            cutoff = now - 60.0
            self._window = [t for t in self._window if t > cutoff]
            day_cutoff = now - 86400.0
            self._day = [t for t in self._day if t > day_cutoff]
            if self.cpd and len(self._day) >= self.cpd:
                return min(86400.0, self._day[0] + 86400.0 - now)
            if len(self._window) >= int(self.cpm):
                return min(60.0, self._window[0] + 60.0 - now)
            return 0.0

    def record(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._window.append(now)
            self._day.append(now)

    def reset(self) -> None:
        with self._lock:
            self._window.clear()
            self._day.clear()


def throttled_request(
    provider_id: str,
    limiter: RateLimiter,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json: dict | None = None,
    timeout: float = 15.0,
    attempts: int = 3,
) -> httpx.Response:
    """Rate-limited HTTP call with bounded retry on transient failures.

    HTTP error statuses are RETURNED to the caller (providers map them with
    ``check_status``); only exhausted transport retries raise
    ``httpx.TransportError``. Never retries 401/403/404/422.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        wait = limiter.wait_if_needed()
        if wait > 0:
            time.sleep(min(wait, 5.0))
        try:
            resp = httpx.request(
                method,
                url,
                params=params,
                headers=headers,
                json=json,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.TransportError as e:
            last_exc = e
            if i == attempts - 1:
                break
            time.sleep(_backoff(i))
            continue
        limiter.record()
        if resp.status_code in TRANSIENT and i < attempts - 1:
            retry_after = _retry_after_seconds(resp)
            time.sleep(min(max(retry_after, _backoff(i)), MAX_BACKOFF_SECONDS))
            continue
        return resp
    raise httpx.TransportError(f"{provider_id}: request failed after retries ({last_exc})")


def _backoff(attempt: int) -> float:
    return min(MAX_BACKOFF_SECONDS, (2.0**attempt) + random.uniform(0, 0.5))


def _retry_after_seconds(resp: httpx.Response) -> float:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return 0.0
    try:
        return min(float(raw), 60.0)
    except ValueError:
        return 0.0
