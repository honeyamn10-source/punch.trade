"""Security layer tests: sessions, CSRF, rate limits, sanitizer, headers."""

import time

import pytest
from fastapi.testclient import TestClient

from app import api
from app import config
from app import security

client = TestClient(api.app)


def _login():
    r = client.post("/api/system/login",
                    headers={"X-Punch-Token": config.API_TOKEN})
    assert r.status_code == 200, r.text
    return r


# -------------------------------------------------------------- sessions --
def test_login_sets_session_and_csrf_cookies():
    r = _login()
    body = r.json()
    assert body["session"] and body["csrf"]
    assert r.cookies.get(security.SESSION_COOKIE)
    assert r.cookies.get(security.CSRF_COOKIE)
    # only the hash is stored
    assert security.validate_session(r.cookies.get(security.SESSION_COOKIE))


def test_login_requires_token():
    r = client.post("/api/system/login")
    assert r.status_code == 401


def test_logout_revokes_session_with_csrf():
    r = _login()
    token = r.cookies.get(security.SESSION_COOKIE)
    csrf = r.cookies.get(security.CSRF_COOKIE)
    out = client.post("/api/system/logout",
                      cookies={security.SESSION_COOKIE: token},
                      headers={"X-Punch-CSRF": csrf})
    assert out.status_code == 200
    assert not security.validate_session(token)


def test_logout_requires_csrf():
    r = _login()
    out = client.post("/api/system/logout",
                      cookies={security.SESSION_COOKIE: r.cookies.get(security.SESSION_COOKIE)})
    assert out.status_code == 403


def test_session_revoked_then_rejected():
    token = security.create_session()
    assert security.validate_session(token)
    security.revoke_session(token)
    assert not security.validate_session(token)


def test_expired_session_rejected_and_purged():
    token = security.create_session()
    with api.db.transaction() as c:
        c.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?",
                  (time.time() - 1, security._hash(token)))
    assert security.purge_expired() >= 1
    assert not security.validate_session(token)


# -------------------------------------------------------------- csrf ----
def test_csrf_mismatch_rejected():
    token = security.create_session()
    out = client.post("/api/system/logout",
                      cookies={security.SESSION_COOKIE: token},
                      headers={"X-Punch-CSRF": "wrong"})
    assert out.status_code == 403


# --------------------------------------------------------- rate limits ----
def test_login_rate_limited_after_five_attempts():
    security.clear_limits()
    for _ in range(5):
        r = client.post("/api/system/login",
                        headers={"X-Punch-Token": config.API_TOKEN})
        assert r.status_code == 200
    r = client.post("/api/system/login",
                    headers={"X-Punch-Token": config.API_TOKEN})
    assert r.status_code == 429
    assert "retryAfter" in r.json()["detail"]


def test_api_rate_limit_with_small_window(monkeypatch):
    security.clear_limits()
    monkeypatch.setattr(security, "API_LIMIT", (60, 3))
    for _ in range(3):
        r = client.get("/api/strategies",
                       headers={"X-Punch-Token": config.API_TOKEN})
        assert r.status_code == 200
    r = client.get("/api/strategies",
                   headers={"X-Punch-Token": config.API_TOKEN})
    assert r.status_code == 429


# ------------------------------------------------------------ sanitizer --
def test_sanitize_strips_control_and_caps_length():
    assert security.sanitize("ok\u0000\u0007text") == "oktext"
    long = "x" * 2000
    assert len(security.sanitize(long)) == security.MAX_FIELD_LEN
    assert security.sanitize(None) is None


def test_sanitize_dict_recursive():
    d = {"a": "ok\u0000", "b": {"c": "x" * 900, "n": 1},
         "list": ["\u0001y", {"z": "z\u0000"}]}
    out = security.sanitize_dict(d)
    assert out["a"] == "ok"
    assert len(out["b"]["c"]) == security.MAX_FIELD_LEN
    assert out["list"][0] == "y"
    assert out["list"][1]["z"] == "z"
    assert out["b"]["n"] == 1


# -------------------------------------------------------------- headers --
def test_security_headers_present():
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]