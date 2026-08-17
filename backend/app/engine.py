"""The strategy engine.

Bar-driven evaluation: indicators are computed once per completed candle
and conditions are checked at the latest bar only. Every strategy carries
a per-symbol state machine ("idle" -> "active") so it can't re-fire the
same setup on every tick — this dedup is what makes the signal stream
usable instead of spammy.

Backtesting reuses this exact class against historical bars, so the
win-rate/drawdown numbers on a strategy card come from the same code
path that runs live.
"""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional

from .strategies import STRATEGIES, compute_indicator, condition_met, get_strategy, target_levels


class Signal:
    __slots__ = ("id", "strategy_id", "strategy_name", "symbol", "side",
                 "entry", "targets", "target_price", "stop_loss", "ts")

    def __init__(self, strategy_id: str, strategy_name: str, symbol: str,
                 side: str, entry: float, targets: List[float], stop_loss: float):
        self.id = uuid.uuid4().hex[:12]
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.side = side
        self.entry = entry
        self.targets = targets
        self.target_price = targets[0]  # primary target, for brokers with single-bracket support
        self.stop_loss = stop_loss
        self.ts = time.time()

    def to_dict(self) -> dict:
        from . import config
        return {
            "id": self.id,
            "strategyId": self.strategy_id,
            "strategyName": self.strategy_name,
            "symbol": self.symbol,
            "side": self.side,
            "entry": round(self.entry, 2),
            "targets": [round(t, 2) for t in self.targets],
            "targetPrice": round(self.target_price, 2),
            "stopLoss": round(self.stop_loss, 2),
            "ts": self.ts,
            "expiresAt": self.ts + config.SIGNAL_TTL_SECONDS,
        }


class StrategyRunner:
    """Runs one strategy against a rolling bar series."""

    def __init__(self, strategy: Dict):
        self.strategy = strategy
        self.state: Dict[str, str] = {}  # symbol -> "idle" | "active"

    def on_bar(self, bars: List[dict]) -> Optional[Signal]:
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
            if condition_met(exit_cfg, series, index, closes, bars):
                self.state[symbol] = "idle"
            return None

        if not condition_met(entry, series, index, closes, bars):
            return None

        self.state[symbol] = "active"
        close = bars[index]["close"]
        sl_pct = self.strategy.get("sl_pct", 1.0)
        targets = [close * (1 + pct / 100) for pct in target_levels(self.strategy)]
        return Signal(
            strategy_id=self.strategy["id"],
            strategy_name=self.strategy["name"],
            symbol=symbol,
            side="buy",
            entry=close,
            targets=targets,
            stop_loss=close * (1 - sl_pct / 100),
        )

    def reset(self) -> None:
        self.state.clear()


def build_runners() -> Dict[str, StrategyRunner]:
    return {s["id"]: StrategyRunner(s) for s in STRATEGIES}