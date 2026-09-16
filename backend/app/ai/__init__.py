"""punch.trade AI analyst — local, private, offline-safe.

The analyst is a LOCAL LLM (Ollama, qwen2.5 family) that reads the
strategy research dossier and writes a natural-language assessment. Hard
rules:

- Auto-detect: only models already installed in Ollama are used
  (`ollama list`). The app NEVER downloads or pulls a model.
- Read-only & sanitized: the prompt contains ONLY whitelisted research
  fields (metrics, scores, regimes, trade counts) — never broker
  credentials, never vault contents, never order payloads.
- Offline-safe: every failure (Ollama down, no model, timeout) returns a
  structured `error` field with a hint; nothing crashes and no secret
  ever leaves the process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import httpx

from .. import security

# whitelisted research fields — the ONLY things allowed in the prompt
_ALLOWED_METRICS = (
    "win_rate",
    "loss_rate",
    "profit_factor",
    "net_pnl",
    "expectancy",
    "max_drawdown_pct",
    "max_consecutive_losses",
    "sharpe",
    "trades",
    "gross_profit",
    "gross_loss",
    "commission",
    "equity_final",
    "avg_win",
    "avg_loss",
)
_ALLOWED_STATUS = (
    "status",
    "reason",
    "score",
    "canPromoteTo",
    "id",
    "name",
    "symbol",
)


def _ollama_host() -> str:
    return os.environ.get("PUNCH_OLLAMA_HOST", "http://127.0.0.1:11434")


def _model_override() -> str | None:
    return os.environ.get("PUNCH_OLLAMA_MODEL") or None


def detect_model() -> dict:
    """Find an installed qwen2.5* model via `ollama list`.

    Never downloads. Returns {model, reason} — model None when nothing
    suitable is installed.
    """
    override = _model_override()
    if override:
        return {"model": override, "reason": None}
    binary = shutil.which("ollama")
    if not binary:
        return {
            "model": None,
            "reason": "ollama not installed (install Ollama, then "
            "`ollama pull qwen2.5:7b` to enable the AI analyst)",
        }
    try:
        out = subprocess.run([binary, "list"], capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return {"model": None, "reason": f"ollama list failed: {out.stderr[:200]}"}
        lines = [ln.split()[0] for ln in out.stdout.splitlines()[1:] if ln.strip()]
        qwen = sorted(
            [m for m in lines if m.startswith("qwen2.5")], key=lambda m: _size_rank(m), reverse=True
        )
        if not qwen:
            return {
                "model": None,
                "reason": "no qwen2.5 model installed (run: ollama pull qwen2.5:7b)",
            }
        return {"model": qwen[0], "reason": None}
    except Exception as e:  # noqa: BLE001 — detection must never crash
        return {"model": None, "reason": f"ollama detection failed: {type(e).__name__}"}


def _size_rank(name: str) -> int:
    for size in ("70b", "32b", "14b", "7b", "3b", "1.5b", "0.5b"):
        if size in name:
            return int(size.replace("b", "").replace(".", ""))
    return 0


# ------------------------------------------------------------- prompt ----
def _pick(d: dict, allowed: tuple) -> dict:
    return {k: v for k, v in d.items() if k in allowed}


def build_prompt(
    strategy: dict, research: dict | None, status: dict | None, drift: dict | None
) -> str:
    """Whitelist-only prompt: metrics + scores + counts, nothing sensitive."""
    ctx: dict = {
        "strategy": _pick(
            strategy, ("id", "name", "symbol", "timeframe", "family", "version", "status")
        ),
        "metrics": _pick((research or {}).get("metrics") or {}, _ALLOWED_METRICS),
        "qualityGate": _pick(
            (research or {}).get("qualityGate") or {}, ("passed", "score", "reasons", "sample")
        ),
        "parameterStability": _pick(
            (research or {}).get("parameterStability") or {}, ("passed", "score", "worst")
        ),
        "walkForward": _pick((research or {}).get("walkForward") or {}, ("consistency", "windows")),
        "bootstrap": _pick(
            (research or {}).get("bootstrap") or {}, ("meanExpectancy", "stdExpectancy", "error")
        ),
        "regimePerformance": (research or {}).get("regimePerformance") or [],
        "status": _pick(status or {}, _ALLOWED_STATUS),
        "liveDrift": _pick(drift or {}, ("mean", "threshold", "degraded", "trades")),
    }
    payload = json.dumps(ctx, indent=1)
    return (
        "You are a cautious algorithmic-trading analyst. Read ONLY the JSON "
        "context below (it contains backtest/research numbers for one "
        "strategy). Produce a concise assessment in 3 sections: "
        "VERDICT (one line: PASS / WATCH / FAIL with the composite score), "
        "STRENGTHS, RISKS (include overfitting/regime warnings). "
        "Never invent numbers not present; if a field is missing say so. "
        "Never mention broker or account details. "
        "Answer in plain text.\n\n" + payload
    )


def analyze(
    strategy: dict,
    research: dict | None = None,
    status: dict | None = None,
    drift: dict | None = None,
) -> dict:
    """Run the local model on the whitelisted dossier. Never raises."""
    started = time.time()
    try:
        model_info = detect_model()
        model = model_info.get("model")
        if not model:
            return {
                "model": None,
                "analysis": None,
                "error": model_info.get("reason") or "no model",
                "elapsedSec": round(time.time() - started, 2),
            }
        prompt = build_prompt(strategy, research, status, drift)
        resp = httpx.post(
            f"{_ollama_host()}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60.0,
        )
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        if not text:
            return {
                "model": model,
                "analysis": None,
                "error": "empty model response",
                "elapsedSec": 0.0,
            }
        return {
            "model": model,
            "analysis": security.sanitize(text),
            "error": None,
            "elapsedSec": round(time.time() - started, 2),
        }
    except Exception as e:  # noqa: BLE001 — offline-safe by design
        return {
            "model": None,
            "analysis": None,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "elapsedSec": round(time.time() - started, 2),
        }


def status() -> dict:
    info = detect_model()
    return {
        "enabled": bool(info["model"]),
        "model": info["model"],
        "host": _ollama_host(),
        "reason": info["reason"],
    }
