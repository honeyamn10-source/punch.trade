"""The strategy engine.

Bar-driven evaluation: indicators are computed once per completed candle
and conditions are checked at the latest bar only. Every strategy carries
a per-symbol state machine ("idle" -> "active") so it can't re-fire the
same setup on every tick — this dedup is what makes the signal stream
usable instead of spammy.

Backtesting reuses this exact class against historical bars, so the
win-rate/drawdown numbers on a strategy card come from the same code
path that runs live.

Signal identity is DETERMINISTIC: sha1(strategy_id|version|symbol|
timeframe|close_time|side) — feed reconnects, dashboard reconnects,
server restarts and double events cannot produce duplicate signals.
"""

from __future__ import annotations

import hashlib
import time

from . import config
from . import signals as signal_states
from .market import regime_of
from .strategies import (
    STRATEGIES,
    compute_indicator,
    condition_met,
    parameter_snapshot,
    strategy_metadata,
    target_levels,
)


def deterministic_signal_id(
    strategy_id: str, version: str, symbol: str, timeframe: str, close_time: float, side: str
) -> str:
    raw = f"{strategy_id}|{version}|{symbol}|{timeframe}|{close_time:.3f}|{side}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class Signal:
    __slots__ = (
        "id",
        "strategy_id",
        "strategy_version",
        "strategy_name",
        "symbol",
        "timeframe",
        "side",
        "entry",
        "targets",
        "target_price",
        "stop_loss",
        "ts",
        "candle_open",
        "candle_close",
        "close_time",
        "indicator_snapshot",
        "parameter_snapshot",
        "reason",
        "regime",
        "status",
        "expires_at",
    )

    def __init__(
        self,
        strategy: dict,
        symbol: str,
        side: str,
        entry: float,
        targets: list[float],
        stop_loss: float,
        bars: list[dict],
        series: list[float | None],
        explanation: dict,
        reason: str,
    ):
        meta = strategy_metadata(strategy)
        bar = bars[-1]
        close_time = float(bar["ts"])
        self.strategy_id = strategy["id"]
        self.strategy_version = meta["version"]
        self.strategy_name = strategy["name"]
        self.symbol = symbol
        self.timeframe = meta.get("supported_timeframes", ["5m"])[0]
        self.side = side
        self.entry = entry
        self.targets = targets
        self.target_price = targets[0]
        self.stop_loss = stop_loss
        self.ts = time.time()
        self.candle_open = float(bar["open"])
        self.candle_close = float(bar["close"])
        self.close_time = close_time
        self.indicator_snapshot = explanation
        self.parameter_snapshot = parameter_snapshot(strategy)
        self.reason = reason
        self.regime = regime_of(bars)
        self.status = signal_states.ACTIVE
        self.id = deterministic_signal_id(
            self.strategy_id,
            self.strategy_version,
            self.symbol,
            self.timeframe,
            self.close_time,
            self.side,
        )
        self.expires_at = signal_states.expired_at({"ts": self.ts}, config.SIGNAL_TTL_SECONDS)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "strategyId": self.strategy_id,
            "strategyName": self.strategy_name,
            "strategyVersion": self.strategy_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "entry": round(self.entry, 2),
            "targets": [round(t, 2) for t in self.targets],
            "targetPrice": round(self.target_price, 2),
            "stopLoss": round(self.stop_loss, 2),
            "ts": self.ts,
            "createdAt": self.ts,
            "candleOpen": round(self.candle_open, 2),
            "candleClose": round(self.candle_close, 2),
            "closeTime": self.close_time,
            "indicatorSnapshot": self.indicator_snapshot,
            "parameterSnapshot": self.parameter_snapshot,
            "reason": self.reason,
            "regime": self.regime,
            "status": self.status,
            "expiresAt": self.expires_at,
        }


class StrategyRunner:
    """Runs one strategy against a rolling bar series."""

    def __init__(self, strategy: dict):
        self.strategy = strategy
        self.state: dict[str, str] = {}  # symbol -> "idle" | "active"
        self.active_since: dict[str, float] = {}  # symbol -> ts (bars)

    def on_bar(self, bars: list[dict]) -> Signal | None:
        """Feed one completed bar (bars = full rolling series, oldest first).

        Returns a Signal the moment entry conditions fire, else None.
        """
        if len(bars) < 2:
            return None
        symbol = self.strategy["symbol"]
        state = self.state.get(symbol, "idle")
        index = len(bars) - 1

        entry = self.strategy["entry"]
        exit_cfg = self.strategy["exit"]
        try:
            series = compute_indicator(entry["indicator"], entry["period"], bars)
        except (ValueError, KeyError, TypeError):
            # corrupt/non-finite bar: no evaluation this candle
            return None
        closes = [b["close"] for b in bars]

        if state == "active":
            # exit-by-timeout: a stalled indicator must not wedge the
            # strategy in "active" forever (AUD-017)
            if bars[-1]["ts"] - self.active_since.get(
                symbol, bars[0]["ts"]
            ) >= config.EXIT_TIMEOUT_BARS * (bars[-1]["ts"] - bars[-2]["ts"]):
                self.state[symbol] = "idle"
                return None
            if condition_met(exit_cfg, series, index, closes, bars):
                self.state[symbol] = "idle"
            return None

        if not condition_met(entry, series, index, closes, bars):
            return None

        self.state[symbol] = "active"
        self.active_since[symbol] = bars[-1]["ts"]
        close = bars[index]["close"]
        sl_pct = self.strategy.get("sl_pct", 1.0)
        targets = [close * (1 + pct / 100) for pct in target_levels(self.strategy)]
        explanation = explain_entry(entry, series, index, closes, bars)
        reason = _fill_reason(self.strategy, explanation)
        return Signal(
            strategy=self.strategy,
            symbol=symbol,
            side="buy",
            entry=close,
            targets=targets,
            stop_loss=close * (1 - sl_pct / 100),
            bars=bars,
            series=series,
            explanation=explanation,
            reason=reason,
        )

    def reset(self) -> None:
        self.state.clear()
        self.active_since.clear()


def explain_entry(
    entry: dict, series: list[float | None], index: int, closes: list[float], bars: list[dict]
) -> dict:
    from .strategies import explain_condition

    return explain_condition(entry, series, index, closes, bars)


def _fill_reason(strategy: dict, explanation: dict) -> str:
    meta = strategy_metadata(strategy)
    tpl = meta["reason_template"]
    try:
        value = explanation.get("value")
        return tpl.format(
            period=strategy["entry"].get("period", ""),
            value=(f"{value}" if value is not None else "?"),
        )
    except (KeyError, IndexError):
        return meta["reason_template"]


def build_runners() -> dict[str, StrategyRunner]:
    return {s["id"]: StrategyRunner(s) for s in STRATEGIES}
