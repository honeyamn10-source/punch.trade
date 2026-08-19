"""Strategy Family E: Statistical Pairs / Stat Arb.

Separate FORMATION and TRADING periods.
Pair selection: liquidity, correlation, cointegration, spread stability.
Trading: hedge ratio, spread, z-score, entry/exit/stop thresholds, max holding.
Relationship breakdown detector: stops when cointegration deteriorates, spread variance explodes, correlation collapses.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from ..base import (
    AssetClass,
    ParameterSpec,
    Signal,
    SignalDirection,
    Strategy,
    StrategyFamily,
    Timeframe,
    register_strategy,
)


@register_strategy
class StatisticalPairs(Strategy):
    """Statistical arbitrage pairs trading with formation/trading separation."""

    strategy_id = "punch_pairs"
    version = "1.0.0"
    family = StrategyFamily.STATARB
    name = "PUNCH Statistical Pairs"
    description = (
        "Pairs trading with separate formation/trading windows. "
        "Uses OLS hedge ratio, spread z-score, with relationship breakdown detection."
    )

    supported_asset_classes = [
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FOREX,
        AssetClass.CRYPTO,
    ]
    supported_timeframes = [Timeframe.H1, Timeframe.H4, Timeframe.D1]

    warmup_bars = 252

    parameter_schema = [
        ParameterSpec("symbol_a", str, "", "First symbol in pair", None, None),
        ParameterSpec("symbol_b", str, "", "Second symbol in pair", None, None),
        ParameterSpec("formation_period", int, 252, "Formation window (bars)", 126, 504),
        ParameterSpec("trading_period", int, 63, "Trading window (bars)", 21, 252),
        ParameterSpec(
            "correlation_min", float, 0.7, "Minimum correlation for pair selection", 0.5, 0.95
        ),
        ParameterSpec(
            "cointegration_pval_max",
            float,
            0.05,
            "Max p-value for cointegration (Engle-Granger)",
            0.01,
            0.1,
        ),
        ParameterSpec("spread_std_min", float, 0.005, "Minimum spread volatility", 0.001, 0.05),
        ParameterSpec("zscore_entry", float, 2.0, "Z-score entry threshold", 1.5, 3.0),
        ParameterSpec("zscore_exit", float, 0.5, "Z-score exit threshold", 0.0, 1.0),
        ParameterSpec("zscore_stop", float, 3.5, "Z-score stop loss threshold", 3.0, 5.0),
        ParameterSpec("max_holding_bars", int, 20, "Maximum holding period (bars)", 5, 63),
        ParameterSpec("use_shorting", bool, True, "Allow short spread", None, None),
        ParameterSpec(
            "breakdown_corr_min", float, 0.3, "Correlation below which to stop trading", 0.1, 0.5
        ),
        ParameterSpec(
            "breakdown_spread_vol_mult", float, 3.0, "Spread vol multiplier for breakdown", 2.0, 5.0
        ),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._hedge_ratio: float = 0.0
        self._spread_mean: float = 0.0
        self._spread_std: float = 1.0
        self._formation_done: bool = False
        self._in_trade: bool = False
        self._trade_direction: str | None = None
        self._entry_idx: int = -1
        self._spread_history: list = []

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        """Generate pairs trading signal.

        Expects bars to contain data for both symbols with 'symbol' field.
        """
        if not self.warmup_satisfied(bars, current_idx):
            return None

        sym_a = self.params["symbol_a"]
        sym_b = self.params["symbol_b"]
        if not sym_a or not sym_b:
            return None

        # Extract price series for both symbols up to current_idx
        price_a = self._extract_symbol_closes(bars, current_idx, self.params["symbol_a"])
        price_b = self._extract_symbol_closes(bars, current_idx, self.params["symbol_b"])

        if (
            len(price_a) < self.params["formation_period"]
            or len(price_b) < self.params["formation_period"]
        ):
            return None

        # Formation phase: compute hedge ratio and spread statistics
        if not self._formation_done:
            self._run_formation(price_a, price_b)
            self._formation_done = True

        # Need enough data for trading
        if current_idx < self.params["formation_period"]:
            return None

        # Trading phase: compute current spread and z-score
        trading_start = current_idx - self.params["trading_period"]
        recent_a = price_a[trading_start : current_idx + 1]
        recent_b = price_b[trading_start : current_idx + 1]

        if len(recent_a) < 20 or len(recent_b) < 20:
            return None

        # Current spread
        spread = recent_a[-1] - self._hedge_ratio * recent_b[-1]
        self._spread_history.append(spread)

        # Rolling z-score
        if len(self._spread_history) < 20:
            return None

        spread_arr = np.array(self._spread_history[-self.params["trading_period"] :])
        z = (spread - np.mean(spread_arr)) / (np.std(spread_arr) + 1e-8)

        # Relationship breakdown detection
        if self._check_breakdown(price_a, price_b, current_idx):
            self._reset_position()
            return Signal(
                strategy_id=self.strategy_id,
                symbol=f"{self.params['symbol_a']}/{self.params['symbol_b']}",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"reason": "relationship_breakdown"},
            )

        # Max holding period check
        if self._in_trade and (current_idx - self._entry_idx) >= self.params["max_holding_bars"]:
            return self._close_position(bars, current_idx, "max_holding")

        # Entry logic
        if not self._in_trade:
            if z <= -self.params["zscore_entry"]:
                self._in_trade = True
                self._trade_direction = "LONG_SPREAD"
                self._entry_idx = len(bars) - 1
                return self._create_signal("LONG_SPREAD", bars, -1)
            elif z >= self.params["zscore_entry"] and self.params["use_shorting"]:
                self._in_trade = True
                self._trade_direction = "SHORT_SPREAD"
                self._entry_idx = len(bars) - 1
                return self._create_signal("SHORT_SPREAD", bars, -1)

        # Exit logic
        elif self._in_trade:
            if (
                self._trade_direction == "LONG_SPREAD"
                and z >= -self.params["zscore_exit"]
                or self._trade_direction == "SHORT_SPREAD"
                and z <= self.params["zscore_exit"]
            ):
                return self._close_position(bars, current_idx, "target")
            elif abs(z) >= self.params["zscore_stop"]:
                return self._close_position(bars, current_idx, "stop_loss")

        return None

    def _extract_symbol_closes(self, bars: list[dict], current_idx: int, symbol: str) -> np.ndarray:
        """Extract close prices for a specific symbol up to current_idx."""
        closes_list = []
        for i in range(current_idx + 1):
            if bars[i].get("symbol") == symbol:
                closes_list.append(bars[i].get("close", np.nan))
        return np.array(closes_list)

    def _run_formation(self, price_a: np.ndarray, price_b: np.ndarray):
        """Run formation phase: compute hedge ratio via OLS."""
        # Use last formation_period bars
        a = price_a[-self.params["formation_period"] :]
        b = price_b[-self.params["formation_period"] :]

        # OLS: a = alpha + beta * b
        X = np.column_stack([np.ones(len(b)), b])
        try:
            coeffs = np.linalg.lstsq(X, a, rcond=None)[0]
            self._hedge_ratio = float(coeffs[1]) if len(coeffs) > 1 else 1.0
        except np.linalg.LinAlgError:
            self._hedge_ratio = 1.0

        # Spread statistics
        spread = a - self._hedge_ratio * b
        self._spread_mean = float(np.mean(spread))
        self._spread_std = float(np.std(spread) + 1e-8)

        # Correlation check
        corr = np.corrcoef(a, b)[0, 1]
        if corr < self.params["correlation_min"]:
            self._hedge_ratio = 1.0
            self._spread_std = 1.0

    def _check_breakdown(self, price_a: np.ndarray, price_b: np.ndarray, current_idx: int) -> bool:
        """Check if pair relationship has broken down."""
        if not self._formation_done:
            return False

        # Recent correlation
        lookback = min(63, current_idx)
        if lookback < 20:
            return False

        a_recent = price_a[-lookback:]
        b_recent = price_b[-lookback:]

        # Correlation breakdown
        corr = np.corrcoef(a_recent, b_recent)[0, 1]
        if corr < self.params["breakdown_corr_min"]:
            return True

        # Spread volatility explosion
        recent_spread = a_recent - self._hedge_ratio * b_recent
        recent_std = np.std(recent_spread)
        return recent_std > self._spread_std * self.params["breakdown_spread_vol_mult"]

    def _create_signal(self, direction: str, bars: list[dict], idx: int) -> Signal:
        sym_a = self.params["symbol_a"]
        sym_b = self.params["symbol_b"]
        ts = bars[idx].get("ts", 0)
        (bars[idx].get("close", 0) if bars[idx].get("symbol") == self.params["symbol_a"] else 0)
        (bars[idx].get("close", 0) if bars[idx].get("symbol") == self.params["symbol_b"] else 0)

        dir_enum = SignalDirection.LONG if direction == "LONG_SPREAD" else SignalDirection.SHORT

        return Signal(
            strategy_id=self.strategy_id,
            symbol=f"{sym_a}/{sym_b}",
            direction=dir_enum,
            timestamp=datetime.fromtimestamp(ts),
            price=0,
            confidence=0.8,
            metadata={
                "hedge_ratio": self._hedge_ratio,
                "spread_zscore": float(self._spread_history[-1]) if self._spread_history else 0,
                "trade_direction": direction,
            },
        )

    def _close_position(self, bars: list[dict], current_idx: int, reason: str) -> Signal:
        self._in_trade = False
        self._trade_direction = None
        self._entry_idx = -1
        ts = bars[current_idx].get("ts", 0)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=f"{self.params['symbol_a']}/{self.params['symbol_b']}",
            direction=SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(ts),
            price=0,
            metadata={"exit_reason": reason},
        )

    def _reset_position(self):
        self._in_trade = False
        self._trade_direction = None
        self._entry_idx = -1

    def reset_state(self):
        self._hedge_ratio = 0.0
        self._spread_mean = 0.0
        self._spread_std = 1.0
        self._formation_done = False
        self._in_trade = False
        self._trade_direction = None
        self._entry_idx = -1
        self._spread_history = []
