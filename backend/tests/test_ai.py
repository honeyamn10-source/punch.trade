"""AI analyst tests: model detection, prompt whitelisting, offline-safety."""

import json

import pytest
from fastapi.testclient import TestClient

from app import api
from app import config
from app.ai import analyze, build_prompt, detect_model, status

client = TestClient(api.app)

H = {"X-Punch-Token": config.API_TOKEN}


# ------------------------------------------------------------ detection --
def test_detect_model_no_ollama(monkeypatch):
    monkeypatch.setattr("app.ai.shutil.which", lambda name: None)
    info = detect_model()
    assert info["model"] is None
    assert "ollama" in info["reason"].lower()
    assert "pull qwen2.5:7b" in info["reason"]  # hint, not an action


def test_detect_model_ollama_list_fails(monkeypatch):
    class Proc:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr("app.ai.shutil.which", lambda name: "/bin/ollama")
    monkeypatch.setattr("app.ai.subprocess.run",
                        lambda *a, **k: Proc())
    info = detect_model()
    assert info["model"] is None
    assert "failed" in info["reason"]


def test_detect_model_picks_biggest_qwen(monkeypatch):
    class Proc:
        returncode = 0
        stderr = ""
        stdout = ("NAME            SIZE\n"
                  "llama3.2       1.2GB\n"
                  "qwen2.5:3b     2GB\n"
                  "qwen2.5:7b     4.7GB\n")

    monkeypatch.setattr("app.ai.shutil.which", lambda name: "/bin/ollama")
    monkeypatch.setattr("app.ai.subprocess.run",
                        lambda *a, **k: Proc())
    assert detect_model()["model"] == "qwen2.5:7b"


def test_detect_model_override(monkeypatch):
    monkeypatch.setenv("PUNCH_OLLAMA_MODEL", "qwen2.5:14b")
    assert detect_model()["model"] == "qwen2.5:14b"
    monkeypatch.delenv("PUNCH_OLLAMA_MODEL")


# --------------------------------------------------------------- prompt --
def test_prompt_contains_only_whitelisted_fields():
    strategy = {"id": "rsi-reversal", "name": "RSI Reversal", "symbol": "X",
                "apiKey": "SECRET-123", "secret": "TOP-SECRET"}
    research = {"metrics": {"win_rate": 0.5, "net_pnl": 100,
                            "apiKey": "LEAK"},
                "qualityGate": {"passed": True, "score": 60},
                "secretField": "nope"}
    prompt = build_prompt(strategy, research, None, None)
    assert "SECRET-123" not in prompt
    assert "TOP-SECRET" not in prompt
    assert "LEAK" not in prompt
    assert "nope" not in prompt
    assert "win_rate" in prompt and "qualityGate" in prompt
    assert "apiKey" not in prompt and "secret" not in prompt


def test_prompt_missing_fields_tolerated():
    prompt = build_prompt({"id": "x", "name": "X"}, None, None, None)
    assert "never invent numbers" in prompt.lower()


# ----------------------------------------------------------- offline-safe -
def test_analyze_without_model_returns_hint(monkeypatch):
    monkeypatch.setattr("app.ai.shutil.which", lambda name: None)
    r = analyze({"id": "x"}, research=None)
    assert r["analysis"] is None
    assert r["error"] and "ollama" in r["error"].lower()


def _fake_httpx(fake_post):
    import types
    import app.ai as ai_mod
    fake = types.SimpleNamespace(post=fake_post)
    ai_mod.httpx = fake
    return fake


def test_analyze_ollama_down_returns_error(monkeypatch):
    def fake_detect():
        return {"model": "qwen2.5:7b", "reason": None}

    def fake_post(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr("app.ai.detect_model", fake_detect)
    _fake_httpx(fake_post)
    r = analyze({"id": "x"}, research={})
    assert r["analysis"] is None
    assert "refused" in r["error"]


def test_analyze_success_path(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "VERDICT: PASS\nSTRENGTHS: good PF"}

    monkeypatch.setattr("app.ai.detect_model",
                        lambda: {"model": "qwen2.5:7b", "reason": None})
    _fake_httpx(lambda *a, **k: Resp())
    r = analyze({"id": "x"}, research={})
    assert r["analysis"].startswith("VERDICT")
    assert r["error"] is None
    assert r["elapsedSec"] >= 0


# --------------------------------------------------------------- api ----
def test_ai_status_endpoint_shape():
    r = client.get("/api/ai/status", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert "enabled" in body and "model" in body and "reason" in body


def test_ai_analyze_offline_is_graceful(monkeypatch):
    monkeypatch.setattr("app.ai.shutil.which", lambda name: None)
    r = client.post("/api/ai/analyze/rsi-reversal", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["analysis"] is None
    assert body["error"]


def test_ai_analyze_unknown_strategy_404():
    r = client.post("/api/ai/analyze/does-not-exist", headers=H)
    assert r.status_code == 404