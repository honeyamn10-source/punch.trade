"""Strategy Family C: Regime-Conditioned Mean Reversion.

Mean reversion only when:
- Trend strength low enough (ADX < threshold)
- Volatility environment acceptable
- Market not experiencing shock
- Liquidity adequate

Candidate features:
- Return z-score
- Bollinger z-score
- RSI
- Distance from VWAP
- Relative volume
- ADX
- ATR percentile

Hysteresis:
- Separate ENTER / HOLD / EXIT thresholds
- Re-entry cooldown
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
from ..indicators import (
    adx,
    atr,
    bollinger,
    closes,
    percentile_rank,
    rsi,
    vwap,
    zscore,
)


@register_strategy
class RegimeConditionedMeanReversion(Strategy):
    """Mean reversion strategy gated by market regime filters."""

    strategy_id = "punch_regime_reversion"
    version = "1.0.0"
    family = StrategyFamily.REVERSION
    name = "PUNCH Regime-Conditioned Mean Reversion"
    description = (
        "Mean reversion with regime gating: only trades when trend is weak, "
        "volatility is normal, and no shock detected. Uses hysteresis thresholds."
    )

    supported_asset_classes = [
        AssetClass.CRYPTO,
        AssetClass.EQUITY,
        AssetClass.ETF,
        AssetClass.INDEX,
        AssetClass.FOREX,
    ]
    supported_timeframes = [
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    warmup_bars = 100

    parameter_schema = [
        # Regime gates
        ParameterSpec("adx_period", int, 14, "ADX period for trend strength", 10, 30),
        ParameterSpec(
            "adx_max", float, 25.0, "Maximum ADX for reversion (trend must be weak)", 15, 40
        ),
        ParameterSpec("atr_period", int, 14, "ATR period for volatility", 10, 30),
        ParameterSpec("vol_pct_max", float, 80.0, "Maximum volatility percentile", 60, 95),
        ParameterSpec("vol_pct_min", float, 20.0, "Minimum volatility percentile", 5, 40),
        ParameterSpec(
            "shock_threshold", float, 3.0, "Return z-score for shock detection", 2.0, 5.0
        ),
        ParameterSpec("min_volume_pct", float, 30.0, "Minimum relative volume percentile", 10, 50),
        # Mean reversion signals
        ParameterSpec("bb_period", int, 20, "Bollinger Bands period", 10, 50),
        ParameterSpec("bb_std", float, 2.0, "Bollinger Bands std multiplier", 1.5, 3.0),
        ParameterSpec("rsi_period", int, 14, "RSI period", 10, 30),
        ParameterSpec("rsi_oversold", float, 30.0, "RSI oversold threshold", 20, 40),
        ParameterSpec("rsi_overbought", float, 70.0, "RSI overbought threshold", 60, 80),
        ParameterSpec("zscore_period", int, 20, "Z-score lookback period", 10, 50),
        ParameterSpec(
            "zscore_entry", float, -2.0, "Z-score entry threshold (negative for long)", -3.0, -1.0
        ),
        ParameterSpec("zscore_exit", float, -0.5, "Z-score exit threshold", -1.0, 0.0),
        ParameterSpec("zscore_reentry_cooldown", int, 5, "Bars before re-entry after exit", 1, 20),
        # VWAP reversion
        ParameterSpec("vwap_period", int, 20, "VWAP period", 10, 50),
        ParameterSpec("vwap_entry_dev", float, 2.0, "VWAP deviation for entry", 1.0, 3.0),
        ParameterSpec("vwap_exit_dev", float, 0.5, "VWAP deviation for exit", 0.0, 1.5),
        # Risk
        ParameterSpec("atr_stop_mult", float, 2.0, "ATR stop loss multiplier", 1.0, 5.0),
        ParameterSpec("use_shorting", bool, False, "Allow short signals", None, None),
    ]

    def __init__(self, **params):
        super().__init__(**params)
        self._state = "IDLE"  # IDLE, LONG, SHORT
        self._entry_idx = -1
        self._last_exit_idx = -100

    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        if not self.warmup_satisfied(bars, current_idx):
            return None

        c = closes(bars)
        if current_idx >= len(c) or np.isnan(c[current_idx]):
            return None

        # Extract parameters
        adx_p = self.params["adx_period"]
        adx_max = self.params["adx_max"]
        atr_p = self.params["atr_period"]
        vol_max = self.params["vol_pct_max"]
        vol_min = self.params["vol_pct_min"]
        shock_thresh = self.params["shock_threshold"]
        min_vol_pct = self.params["min_volume_pct"]

        bb_p = self.params["bb_period"]
        bb_std = self.params["bb_std"]
        rsi_p = self.params["rsi_period"]
        rsi_os = self.params["rsi_oversold"]
        rsi_ob = self.params["rsi_overbought"]
        zs_p = self.params["zscore_period"]
        zs_entry = self.params["zscore_entry"]
        zs_exit = self.params["zscore_exit"]
        zs_cooldown = self.params["zscore_reentry_cooldown"]
        vwap_p = self.params["vwap_period"]
        vwap_entry = self.params["vwap_entry_dev"]
        vwap_exit = self.params["vwap_exit_dev"]
        atr_mult = self.params["atr_stop_mult"]
        use_short = self.params["use_shorting"]

        # Compute indicators up to current_idx (no lookahead)
        bars_up_to = bars[: current_idx + 1]
        c_up_to = closes(bars_up_to)

        # Regime indicators
        adx_vals = adx(bars_up_to, adx_p)
        atr_vals = atr(bars_up_to, atr_p)
        atr_pct = percentile_rank(atr_vals, 252)

        # Volume
        volumes = np.array([b.get("volume", 1.0) for b in bars_up_to])
        vol_pct = percentile_rank(volumes, 252)

        # Shock detection
        returns = np.diff(c_up_to, prepend=np.nan) / np.roll(c_up_to, 1)
        returns[0] = np.nan
        ret_zscore = zscore(returns, 20)

        # Mean reversion indicators
        bb = bollinger(c_up_to, bb_p, bb_std)
        rsi_vals = rsi(c_up_to, rsi_p)
        zs_vals = zscore(c_up_to, zs_p)
        vwap_vals = vwap(bars_up_to, vwap_p)

        idx = current_idx
        if idx >= len(c_up_to):
            return None

        # === REGIME GATES ===
        # 1. Trend must be weak
        adx_val = adx_vals[idx] if idx < len(adx_vals) else np.nan
        trend_ok = not np.isnan(adx_val) and adx_val < adx_max

        # 2. Volatility in acceptable range
        vol_val = atr_pct[idx] if idx < len(atr_pct) else np.nan
        vol_ok = not np.isnan(vol_val) and vol_min <= vol_val <= vol_max

        # 3. No shock
        shock_val = ret_zscore[idx] if idx < len(ret_zscore) else np.nan
        no_shock = np.isnan(shock_val) or abs(shock_val) < shock_thresh

        # 4. Adequate liquidity
        vol_ok2 = vol_pct[idx] >= min_vol_pct if idx < len(vol_pct) else False

        regime_ok = trend_ok and vol_ok and no_shock and vol_ok2

        # === MEAN REVERSION SIGNALS ===
        # Z-score
        z_val = zs_vals[idx] if idx < len(zs_vals) else np.nan

        # RSI
        rsi_val = rsi_vals[idx] if idx < len(rsi_vals) else np.nan

        # Bollinger Bands position
        if idx < len(bb["lower"]) and idx < len(bb["upper"]):
            rng = bb["upper"][idx] - bb["lower"][idx]
            if rng > 0:
                (c_up_to[idx] - bb["lower"][idx]) / rng

        # VWAP deviation
        vwap_dev = np.nan
        if idx < len(vwap_vals) and vwap_vals[idx] != 0:
            vwap_dev = (c_up_to[idx] - vwap_vals[idx]) / vwap_vals[idx]

        # Current price
        price = c_up_to[idx]
        atr_val = atr_vals[idx] if idx < len(atr_vals) else np.nan

        # === STATE MACHINE WITH HYSTERESIS ===
        if self._state == "IDLE":
            # Check long entry
            long_entry = (
                regime_ok
                and not np.isnan(z_val)
                and z_val <= zs_entry
                and not np.isnan(rsi_val)
                and rsi_val <= rsi_os
                and (idx - self._last_exit_idx) >= zs_cooldown
            )
            # Also check VWAP deviation for long
            if not long_entry and regime_ok:
                long_entry = (
                    not np.isnan(vwap_dev)
                    and vwap_dev <= -vwap_entry
                    and not np.isnan(rsi_val)
                    and rsi_val <= rsi_os
                    and (idx - self._last_exit_idx) >= zs_cooldown
                )

            if long_entry:
                self._state = "LONG"
                self._entry_idx = idx
                return self._create_signal(
                    bars,
                    idx,
                    SignalDirection.LONG,
                    price,
                    atr_val,
                    atr_mult,
                    adx_vals,
                    atr_pct,
                    zs_vals,
                    rsi_vals,
                )

            # Check short entry
            if use_short:
                short_entry = (
                    regime_ok
                    and not np.isnan(z_val)
                    and z_val >= -zs_entry
                    and not np.isnan(rsi_val)
                    and rsi_val >= rsi_ob
                    and (idx - self._last_exit_idx) >= zs_cooldown
                )
                if not short_entry and regime_ok:
                    short_entry = (
                        not np.isnan(vwap_dev)
                        and vwap_dev >= vwap_entry
                        and not np.isnan(rsi_val)
                        and rsi_val >= rsi_ob
                        and (idx - self._last_exit_idx) >= zs_cooldown
                    )

                if short_entry:
                    self._state = "SHORT"
                    self._entry_idx = idx
                    return self._create_signal(
                        bars,
                        idx,
                        SignalDirection.SHORT,
                        price,
                        atr_val,
                        atr_mult,
                        adx_vals,
                        atr_pct,
                        zs_vals,
                        rsi_vals,
                    )

        elif self._state == "LONG":
            # Check exit conditions
            exit_long = False
            if not np.isnan(z_val) and z_val >= zs_exit:
                exit_long = True
            if not np.isnan(vwap_dev) and vwap_dev >= -vwap_exit:
                exit_long = True
            if not regime_ok:  # Regime change forces exit
                exit_long = True

            if exit_long:
                self._state = "IDLE"
                self._last_exit_idx = idx
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.FLAT,
                    timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                    price=price,
                    metadata={
                        "exit_reason": "mean_reversion_target" if not regime_ok else "regime_change"
                    },
                )

        elif self._state == "SHORT":
            exit_short = False
            if not np.isnan(z_val) and z_val <= -zs_exit:
                exit_short = True
            if not np.isnan(vwap_dev) and vwap_dev <= vwap_exit:
                exit_short = True
            if not regime_ok:
                exit_short = True

            if exit_short:
                self._state = "IDLE"
                self._last_exit_idx = idx
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bars[idx].get("symbol", "UNKNOWN"),
                    direction=SignalDirection.FLAT,
                    timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
                    price=price,
                    metadata={
                        "exit_reason": "mean_reversion_target" if not regime_ok else "regime_change"
                    },
                )

        return None

    def _create_signal(
        self,
        bars: list[dict],
        idx: int,
        direction: SignalDirection,
        price: float,
        atr_val: float,
        atr_mult: float,
        adx_vals: np.ndarray,
        atr_pct: np.ndarray,
        zs_vals: np.ndarray,
        rsi_vals: np.ndarray,
    ) -> Signal:
        if direction == SignalDirection.LONG:
            stop_loss = price - atr_mult * (atr_val if not np.isnan(atr_val) else price * 0.02)
        else:
            stop_loss = price + atr_mult * (atr_val if not np.isnan(atr_val) else price * 0.02)

        return Signal(
            strategy_id=self.strategy_id,
            symbol=bars[idx].get("symbol", "UNKNOWN"),
            direction=direction,
            timestamp=datetime.fromtimestamp(bars[idx].get("ts", 0)),
            price=price,
            confidence=0.8,
            stop_loss=stop_loss,
            metadata={
                "regime_gate": True,
                "adx": float(adx_vals[idx]) if idx < len(adx_vals) else None,
                "vol_percentile": float(atr_pct[idx]) if idx < len(atr_pct) else None,
                "zscore": float(zs_vals[idx]) if idx < len(zs_vals) else None,
                "rsi": float(rsi_vals[idx]) if idx < len(rsi_vals) else None,
            },
        )

    def reset_state(self):
        """Reset strategy state (useful for backtesting)."""
        self._state = "IDLE"
        self._entry_idx = -1
        self._last_exit_idx = -100
