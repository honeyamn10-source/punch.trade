"""Strategy Family G: Carry Framework.

Framework for carry strategies where correct data exists.
Possible domains:
- FX interest-rate differential
- Commodity futures curve
- Futures roll yield
- Crypto funding
- Crypto futures basis

If required data does not exist: framework only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from ..base import AssetClass, ParameterSpec, Signal, SignalDirection, Strategy, StrategyFamily, Timeframe, register_strategy
from ..indicators import closes


class CarryDomain:
    FX = "fx"
    COMMODITY = "commodity"
    CRYPTO_FUNDING = "crypto_funding"
    CRYPTO_BASIS = "crypto_basis"


@register_strategy
class CarryFramework(Strategy):
    """Carry strategy framework - requires valid carry data to function."""

    strategy_id = "punch_carry"
    version = "1.0.0"
    family = "carry"
    name = "PUNCH Carry Framework"
    description = (
        "Carry strategy framework supporting FX, commodity, and crypto carry. "
        "Requires valid carry data (interest rate differentials, futures curves, funding rates). "
        "If data unavailable, runs in framework-only mode."
    )

    supported_asset_classes = [
        AssetClass.FOREX,
        AssetClass.COMMODITY,
        AssetClass.CRYPTO,
    ]
    supported_timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]

    warmup_bars = 60

    parameter_schema = [
        ParameterSpec("domain", str, "fx", "Carry domain: fx, commodity, crypto_funding, crypto_basis", None, None),
        ParameterSpec("universe", list, [], "Symbols in universe", None, None),
        ParameterSpec("carry_field", str, "carry", "Field name for carry data in bars", None, None),
        ParameterSpec("signal_field", str, "carry_signal", "Field name for pre-computed signal in bars", None, None),
        ParameterSpec("min_carry", float, 0.0, "Minimum annualized carry for entry", 0.0, 0.1),
        ParameterSpec("max_positions", int, 5, "Maximum concurrent positions", 1, 20),
        ParameterSpec("rebalance_frequency", int, 1, "Rebalance every N bars", 1, 20),
        ParameterSpec("use_shorting", bool, True, "Allow short negative carry", None, None),
        ParameterSpec("stop_loss_atr_mult", float, 2.0, "ATR stop loss multiplier", 1.0, 5.0),
        ParameterSpec("data_required", bool, True, "Require actual carry data (not framework-only)", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._last_rebalance_idx: int = -1
        self._current_allocation: dict = {}
        self._data_available: bool = False

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        # Only rebalance at specified frequency
        if current_idx - self._last_rebalance_idx < self.params["rebalance_frequency"]:
            return None

        universe = self.params["universe"]
        if not universe:
            return None

        domain = self.params["domain"]
        carry_field = self.params["carry_field"]
        signal_field = self.params["signal_field"]
        min_carry = self.params["min_carry"]
        use_short = self.params["use_shorting"]

        # Check if carry data is available
        self._data_available = self._check_carry_data(bars, current_idx, carry_field, signal_field)

        if not self._data_available and self.params["data_required"]:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "data_unavailable", "domain": domain},
            )

        # Extract carry for each symbol
        carry_scores = {}
        for sym in self.params["universe"]:
            carry = self._get_carry(bars, current_idx, sym, carry_field, signal_field)
            if carry is not None:
                carry_scores[sym] = carry

        if not carry_scores:
            return Signal(
                strategy_id=self.strategy_id,
                symbol="PORTFOLIO",
                direction=SignalDirection.FLAT,
                timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
                price=0,
                metadata={"status": "no_valid_carry", "domain": domain},
            )

        # Rank by carry
        ranked = sorted(carry_scores.items(), key=lambda x: x[1], reverse=True)

        # Select top longs
        longs = [s for s, c in ranked if c >= self.params["min_carry"]][:self.params["max_positions"]]

        # Select shorts (negative carry)
        shorts = [s for s, c in ranked if c <= -self.params["min_carry"]] if self.params["use_shorting"] else []

        # Build allocation
        allocation = {}
        n_long = len(longs)
        n_short = len(shorts)

        if n_long > 0:
            for s in longs:
                allocation[s] = 1.0 / n_long

        if n_short > 0:
            for s in shorts:
                allocation[s] = -1.0 / n_short

        # Normalize
        total_gross = sum(abs(v) for v in allocation.values())
        if total_gross > 0:
            allocation = {k: v / total_gross for k, v in allocation.items()}

        self._last_rebalance_idx = current_idx
        self._current_allocation = allocation

        return Signal(
            strategy_id=self.strategy_id,
            symbol="PORTFOLIO",
            direction=SignalDirection.LONG if allocation else SignalDirection.FLAT,
            timestamp=datetime.fromtimestamp(bars[current_idx].get("ts", 0)),
            price=1.0,
            confidence=0.8 if self._data_available else 0.3,
            metadata={
                "allocation": allocation,
                "carry_scores": carry_scores,
                "domain": domain,
                "data_available": self._data_available,
                "longs": [s for s in longs],
                "shorts": [s for s in shorts],
            },
        )

    def _check_carry_data(self, bars: list[dict], current_idx: int, carry_field: str, signal_field: str) -> bool:
        """Check if carry data is available in recent bars."""
        for i in range(max(0, current_idx - 10), current_idx + 1):
            bar = bars[i]
            if carry_field in bar or signal_field in bar:
                return True
        return False

    def _get_carry(self, bars: list[dict], current_idx: int, symbol: str, carry_field: str, signal_field: str) -> Optional[float]:
        """Extract carry value for a symbol at current_idx."""
        for i in range(current_idx, max(0, current_idx - 5), -1):
            bar = bars[i]
            if bar.get("symbol") == symbol:
                if signal_field in bar:
                    return float(bar[signal_field])
                if carry_field in bar:
                    return float(bar[carry_field])
        return None


# FX Carry specialization
@register_strategy
class FXCarry(CarryFramework):
    """FX Carry trade using interest rate differentials."""

    strategy_id = "punch_fx_carry"
    version = "1.0.0"
    name = "PUNCH FX Carry"
    description = "FX carry trade using interest rate differentials between currencies."

    supported_asset_classes = [AssetClass.FOREX]

    parameter_schema = [
        ParameterSpec("domain", str, "fx", "Carry domain", None, None),
        ParameterSpec("currency_pairs", list, [], "List of FX pairs (e.g., ['EURUSD', 'GBPUSD'])", None, None),
        ParameterSpec("rate_source", str, "central_bank", "Rate source: central_bank, bloomberg, fred", None, None),
        ParameterSpec("min_rate_diff", float, 0.01, "Minimum rate differential (1%)", 0.005, 0.05),
        ParameterSpec("max_positions", int, 5, "Maximum concurrent positions", 1, 10),
        ParameterSpec("rebalance_frequency", int, 5, "Rebalance every N days", 1, 20),
        ParameterSpec("use_shorting", bool, True, "Allow short negative carry", None, None),
    ]

    def __init__(self, **params):
        params["domain"] = "fx"
        params["universe"] = params.get("currency_pairs", [])
        super().__init__(**params)


# Crypto Funding Carry specialization
@register_strategy
class CryptoFundingCarry(CarryFramework):
    """Crypto funding rate carry."""

    strategy_id = "punch_crypto_funding_carry"
    version = "1.0.0"
    name = "PUNCH Crypto Funding Carry"
    description = "Crypto carry using perpetual funding rates."

    supported_asset_classes = [AssetClass.CRYPTO]

    parameter_schema = [
        ParameterSpec("domain", str, "crypto_funding", "Carry domain", None, None),
        ParameterSpec("symbols", list, [], "List of crypto symbols (e.g., ['BTCUSDT', 'ETHUSDT'])", None, None),
        ParameterSpec("funding_source", str, "binance", "Funding rate source: binance, bybit, okx", None, None),
        ParameterSpec("min_funding_apy", float, 0.10, "Minimum funding APY (10%)", 0.05, 0.50),
        ParameterSpec("max_positions", int, 5, "Maximum concurrent positions", 1, 10),
        ParameterSpec("rebalance_frequency", int, 8, "Rebalance every N hours", 1, 24),
        ParameterSpec("use_shorting", bool, True, "Allow short negative funding", None, None),
    ]

    def __init__(self, **params):
        params["domain"] = "crypto_funding"
        params["universe"] = params.get("symbols", [])
        super().__init__(**params)