"""Strategy lifecycle status + composite score + live drift detection.

Status ladder (one-way promotion, DISABLED is a manual override):

    DRAFT -> BACKTESTED -> RESEARCHED -> LIVE_ACTIVE
                              |-> LIVE_DEGRADED (drift) -> DISABLED (manual)

Promotion rules (enforced here, so the dashboard and API can't disagree):
- BACKTESTED: the strategy runs on the engine and has a backtest result.
- RESEARCHED: research quality gate passed (research.quality_gate).
- LIVE_ACTIVE: RESEARCHED + user action (paper/live enablement).
- LIVE_DEGRADED: live/paper performance drifted below threshold.
- DISABLED: manual disable (stops new signals).

The composite score is deliberately NOT win-rate-only:
- quality gate score (research)    -> 60%
- parameter stability spread       -> 20%
- live drift health                -> 20% (neutral when no live trades)
"""

from __future__ import annotations

DRAFT = "DRAFT"
BACKTESTED = "BACKTESTED"
RESEARCHED = "RESEARCHED"
LIVE_ACTIVE = "LIVE_ACTIVE"
LIVE_DEGRADED = "LIVE_DEGRADED"
DISABLED = "DISABLED"

STATUS_LADDER = (DRAFT, BACKTESTED, RESEARCHED, LIVE_ACTIVE, LIVE_DEGRADED, DISABLED)

_PROMOTABLE = {
    DRAFT: {BACKTESTED},
    BACKTESTED: {RESEARCHED},
    RESEARCHED: {LIVE_ACTIVE},
    LIVE_ACTIVE: {LIVE_DEGRADED},
    LIVE_DEGRADED: {DISABLED},
    DISABLED: set(),
}


class StatusError(ValueError):
    pass


def transition(current: str, new: str) -> str:
    if new not in _PROMOTABLE.get(current, set()):
        raise StatusError(f"illegal strategy status transition {current} -> {new}")
    return new


def can_promote(current: str, to: str) -> bool:
    return to in _PROMOTABLE.get(current, set())


# --------------------------------------------------------- composite ----
def composite_score(research: dict | None, drift: dict | None) -> dict:
    """0-100 score with visible components. research/qualityGate shape is
    research.py::quality_gate output; drift is live_drift() output."""
    quality = (research or {}).get("qualityGate", {})
    stability = (research or {}).get("parameterStability", {})
    q_score = quality.get("score", 0) if quality else 0
    stability_ok = stability.get("stable", False) if stability else False
    stability_pct = 100 if stability_ok else 0

    drift_pct = 50.0  # neutral when no live data
    if drift is not None and drift.get("trades", 0) >= 3:
        drift_pct = drift.get("health", 50.0)

    score = round(0.60 * q_score + 0.20 * stability_pct + 0.20 * drift_pct)
    return {
        "score": min(100, max(0, score)),
        "components": {
            "qualityGate": q_score,
            "parameterStability": stability_pct,
            "liveDrift": round(drift_pct, 1),
        },
    }


# -------------------------------------------------------------- drift ----
def live_drift(
    backtest_baseline: dict,
    live_trades: list[dict],
    min_trades: int = 3,
    degrade_ratio: float = 0.5,
) -> dict:
    """Compare live expectancy vs backtest expectancy (per-trade).

    degrade_ratio: live expectancy below this fraction of baseline marks
    the strategy LIVE_DEGRADED. Health 0-100, 50 = baseline parity.
    """
    from . import pnl as pnl_mod

    live_stats = pnl_mod.summary_stats(
        [
            {
                "net_pnl": t.get("netPnl", 0.0),
                "net_pnl_pct": t.get("netPnlPct", 0.0),
                "entry_ts": t.get("entryTs", 0.0),
                "exit_ts": t.get("exitTs", 0.0),
            }
            for t in live_trades
        ]
    )
    base_exp = backtest_baseline.get("expectancy", 0.0)
    live_exp = live_stats["expectancy"]

    if live_stats["trades"] < min_trades or base_exp <= 0:
        return {
            "trades": live_stats["trades"],
            "degraded": False,
            "health": 50.0,
            "reason": "insufficient data",
        }

    ratio = live_exp / base_exp if base_exp else 0.0
    health = round(max(0.0, min(100.0, ratio * 100)), 1)
    degraded = ratio < degrade_ratio
    return {
        "trades": live_stats["trades"],
        "baselineExpectancy": round(base_exp, 2),
        "liveExpectancy": round(live_exp, 2),
        "ratio": round(ratio, 3),
        "health": health,
        "degraded": degraded,
        "reason": ("drift below threshold" if degraded else "within threshold"),
    }


# ------------------------------------------------------------ compute ----
def compute_status(
    strategy_id: str,
    current_status: str,
    has_backtest: bool,
    research: dict | None,
    drift: dict | None,
) -> dict:
    """Derive the strategy's status from evidence, applying legal
    transitions. Returns {status, score, reason, canPromoteTo}."""
    new_status = current_status
    if current_status == DISABLED:
        reason = "manually disabled"
    else:
        if has_backtest and new_status == DRAFT:
            new_status, reason = BACKTESTED, "backtest completed"
        else:
            reason = "no backtest yet"
        if research and research.get("qualityGate", {}).get("passed"):
            if new_status == BACKTESTED:
                new_status, reason = RESEARCHED, "research quality gate passed"
            elif new_status == RESEARCHED:
                reason = "research gate still passing"
            else:
                reason = "research gate passed"
        else:
            reason = "research gate not passed" if research else "no research report yet"
    if drift and drift.get("degraded") and new_status in (LIVE_ACTIVE, RESEARCHED, BACKTESTED):
        new_status, reason = LIVE_DEGRADED, f"drift: {drift.get('reason')}"

    score = composite_score(research, drift)
    ladder_pos = STATUS_LADDER.index(new_status)
    promotable = [s for s in STATUS_LADDER if can_promote(new_status, s)]
    return {
        "strategyId": strategy_id,
        "status": new_status,
        "reason": reason,
        "score": score,
        "canPromoteTo": promotable,
        "ladderPosition": ladder_pos,
    }
