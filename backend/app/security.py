"""Security layer — sessions, CSRF, rate limits, sanitizer, headers.

Threat model for a self-hosted single-operator tool:

- The API token (X-Punch-Token header) is the primary credential — it is
  NEVER sent in URLs or cookies.
- Dashboard sessions are a secondary, revocable credential: an httpOnly
  cookie holding a random session token (only its SHA-256 hash is
  persisted). Sessions expire and can be revoked (logout).
- CSRF: the dashboard is same-origin; state-changing requests that rely
  on the session cookie must echo the CSRF token (double-submit cookie).
  Header-token requests are not cookie-authenticated, so CSRF does not
  apply to them.
- Rate limits: sliding window per client IP; login is throttled harder
  (brute-force), general API throttled (abuse / runaway clients).
- Sanitizer: user-supplied strings are stripped of control characters
  and length-capped before logging / persistence.
- Security headers are added on every response.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional

from fastapi import HTTPException, Request, Response

from . import db

# ------------------------------------------------------------- config ----
SESSION_TTL_SECONDS = 12 * 3600          # dashboard session lifetime
CSRF_COOKIE = "punch_csrf"
SESSION_COOKIE = "punch_session"

# rate limits: (window_seconds, max_events)
LOGIN_LIMIT = (60, 5)                    # 5 login attempts / min / IP
API_LIMIT = (60, 240)                    # 240 API calls / min / IP


# ------------------------------------------------------------- sessions ----
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(ip: str = "", user_agent: str = "") -> str:
    """Issue a session token (raw token returned ONCE, only hash stored)."""
    token = secrets.token_urlsafe(32)
    with db.transaction() as c:
        c.execute(
            "INSERT INTO sessions (token_hash, expires_at, created_at, ip, user_agent) "
            "VALUES (?,?,?,?,?)",
            (_hash(token), time.time() + SESSION_TTL_SECONDS, time.time(),
             ip[:64], user_agent[:128]))
    return token


def validate_session(token: Optional[str]) -> bool:
    if not token:
        return False
    with db.transaction() as c:
        row = c.execute(
            "SELECT expires_at FROM sessions WHERE token_hash=?", (_hash(token),)
        ).fetchone()
    if row is None:
        return False
    if row["expires_at"] < time.time():
        with db.transaction() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
        return False
    return True


def revoke_session(token: Optional[str]) -> None:
    if not token:
        return
    with db.transaction() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))


def purge_expired() -> int:
    """Remove expired sessions; returns the number removed."""
    with db.transaction() as c:
        cur = c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        return cur.rowcount


def require_session(request: Request) -> None:
    """FastAPI dependency: valid dashboard session cookie required."""
    token = request.cookies.get(SESSION_COOKIE)
    if not validate_session(token):
        raise HTTPException(status_code=401, detail="Invalid or expired session")


# ----------------------------------------------------------------- CSRF ----
def require_csrf(request: Request) -> None:
    """Double-submit: cookie value must match the X-Punch-CSRF header."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-Punch-CSRF", "")
    if not cookie or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF check failed")


# ---------------------------------------------------------- rate limits ----
_limits: Dict[str, list] = {}
_limits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sweep(now: float, window: float) -> None:
    for key in [k for k, v in _limits.items() if v and now - v[-1] > window]:
        _limits.pop(key, None)


def rate_limit(request: Request, *, kind: str, window: float,
               max_events: int) -> None:
    """Sliding-window rate limit per client IP; raises 429 when exceeded."""
    now = time.time()
    key = f"{_client_ip(request)}:{kind}"
    with _limits_lock:
        events = _limits.setdefault(key, [])
        while events and now - events[0] > window:
            events.pop(0)
        _sweep(now, window * 4)
        if len(events) >= max_events:
            retry = max(1, int(window - (now - events[0])))
            raise HTTPException(status_code=429,
                                detail={"code": "RATE_LIMITED",
                                        "message": f"too many requests — retry in {retry}s",
                                        "retryAfter": retry},
                                headers={"Retry-After": str(retry)})
        events.append(now)


def rate_limit_login(request: Request) -> None:
    rate_limit(request, kind="login", window=LOGIN_LIMIT[0],
               max_events=LOGIN_LIMIT[1])


def rate_limit_api(request: Request) -> None:
    rate_limit(request, kind="api", window=API_LIMIT[0],
               max_events=API_LIMIT[1])


def clear_limits() -> None:
    with _limits_lock:
        _limits.clear()


# ------------------------------------------------------------ sanitizer ----
MAX_FIELD_LEN = 512


def sanitize(value: Optional[str]) -> Optional[str]:
    """Strip control characters and cap length. None stays None."""
    if value is None:
        return None
    cleaned = "".join(ch for ch in str(value)
                      if ch.isprintable() or ch in "\t\n\r")
    return cleaned[:MAX_FIELD_LEN]


def sanitize_dict(d: Dict) -> Dict:
    """Deep-clean string fields of a record (applied before persistence)."""
    out: Dict = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = sanitize(v)
        elif isinstance(v, dict):
            out[k] = sanitize_dict(v)
        elif isinstance(v, list):
            out[k] = [sanitize_dict(i) if isinstance(i, dict) else
                      (sanitize(i) if isinstance(i, str) else i) for i in v]
        else:
            out[k] = v
    return out


# ---------------------------------------------------------- middleware ----
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; frame-ancestors 'none'"),
}


def apply_security_headers(response: Response) -> None:
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v


def csrf_cookie() -> str:
    """Random per-session CSRF value for the double-submit cookie."""
    return secrets.token_urlsafe(24)