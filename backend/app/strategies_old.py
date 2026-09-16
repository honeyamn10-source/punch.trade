"""Declarative strategy configs.

Strategies are plain dicts referencing the fixed indicator/condition
library in indicators.py. This is the "safe" marketplace representation
from the design: no arbitrary code execution, nothing a contributor can
do except pick indicators and levels.

Identity: strategy_id (stable, primary key) + version (bumped on any
material trading-logic change). Historical signals/backtests/trades must
preserve the parameter snapshot — never reconstruct history from current
defaults.
"""

from __future__ import annotations

import copy

# per-strategy metadata: family / warmup / timeframes / status / reason
_META: dict[str, dict] = {
    "rsi-reversal": {
        "family": "mean-reversion",
        "warmup_bars": 15,
        "reason": "RSI({period}) crossed below {value} — oversold bounce setup",
    },
    "ema-breakout": {
        "family": "trend",
        "warmup_bars": 21,
        "reason": "close crossed above EMA({period}) — momentum breakout",
    },
    "sma-bounce": {
        "family": "trend",
        "warmup_bars": 51,
        "reason": "close crossed above SMA({period}) — bounce off support",
    },
    "btc-rsi": {
        "family": "mean-reversion",
        "warmup_bars": 15,
        "reason": "RSI({period}) crossed below {value} on BTC — dip buy",
    },
    "macd-momentum": {
        "family": "momentum",
        "warmup_bars": 35,
        "reason": "MACD histogram crossed above 0 — momentum shift",
    },
    "bb-reversion": {
        "family": "mean-reversion",
        "warmup_bars": 21,
        "reason": "close reclaimed the lower Bollinger band({period})",
    },
    "donchian-breakout": {
        "family": "breakout",
        "warmup_bars": 21,
        "reason": "close broke the {period}-bar Donchian high — turtle breakout",
    },
    "vwap-reversion": {
        "family": "mean-reversion",
        "warmup_bars": 21,
        "reason": "price dipped below rolling VWAP({period}) — reversion entry",
    },
    "golden-cross": {
        "family": "trend",
        "warmup_bars": 51,
        "reason": "SMA(20) crossed above SMA(50) — golden cross",
    },
    "stoch-reversal": {
        "family": "mean-reversion",
        "warmup_bars": 18,
        "reason": "Stochastic({period}) crossed below 20 — oversold snap-back",
    },
    "adx-trend": {
        "family": "trend",
        "warmup_bars": 28,
        "reason": "ADX({period}) crossed above 25 — strong trend confirmed",
    },
}

STRATEGIES: list[dict] = [
    {
        "id": "rsi-reversal",
        "name": "RSI Reversal",
        "symbol": "RELIANCE",
        "interval": "5m",
        "description": "Buy when RSI(14) crosses below 30, exit when it crosses above 50.",
        "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
        "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 50},
        "tp_pct": 2.0,
        "sl_pct": 1.0,
    },
    {
        "id": "ema-breakout",
        "name": "EMA Breakout",
        "symbol": "TCS",
        "interval": "5m",
        "description": "Buy when close crosses above EMA(20), exit when it crosses back below.",
        "entry": {"indicator": "EMA", "period": 20, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "EMA", "period": 20, "condition": "crosses_below", "value": "self"},
        "tp_pct": 1.5,
        "sl_pct": 0.8,
    },
    {
        "id": "sma-bounce",
        "name": "SMA Bounce",
        "symbol": "HDFCBANK",
        "interval": "5m",
        "description": "Buy when close crosses above SMA(50), exit when it crosses back below. Multi-TP: 50% at +1.5%, rest at +3%.",
        "entry": {"indicator": "SMA", "period": 50, "condition": "crosses_above", "value": "self"},
        "exit": {"indicator": "SMA", "period": 50, "condition": "crosses_below", "value": "self"},
        "tp_levels": [1.5, 3.0],
        "sl_pct": 1.2,
    },
    {
        "id": "btc-rsi",
        "name": "BTC RSI Dip",
        "symbol": "BTC/USDT",
        "interval": "5m",
        "description": "Buy BTC when RSI(14) crosses below 30, exit when it crosses above 55.",
        "entry": {"indicator": "RSI", "period": 14, "condition": "crosses_below", "value": 30},
        "exit": {"indicator": "RSI", "period": 14, "condition": "crosses_above", "value": 55},
        "tp_pct": 1.5,
        "sl_pct": 0.8,
    },
    {
        "id": "macd-momentum",
        "name": "MACD Momentum",
        "symbol": "RELIANCE",
        "interval": "5m",
        "description": "Classic MACD(12,26,9) histogram crossing zero = momentum shift. Long on the zero-cross.",
        "entry": {"indicator": "MACD", "period": 0, "condition": "crosses_above", "value": 0},
        "exit": {"indicator": "MACD", "period": 0, "condition": "crosses_below", "value": 0},
        "tp_levels": [1.2, 2.4],
        "sl_pct": 1.0,
    },
    {
        "id": "bb-reversion",
        "name": "BB Mean Reversion",
        "symbol": "INFY",
        "interval": "5m",
        "description": "Fade the overshoot: buy when close reclaims the lower Bollinger band, exit at the middle band.",
        "entry": {
            "indicator": "BB_LOWER",
            "period": 20,
            "condition": "crosses_above",
            "value": "self",
        },
        "exit": {
            "indicator": "BB_MID",
            "period": 20,
            "condition": "crosses_below",
            "value": "self",
        },
        "tp_levels": [1.0, 2.0],
        "sl_pct": 1.0,
    },
    {
        "id": "donchian-breakout",
        "name": "Donchian Breakout",
        "symbol": "TCS",
        "interval": "5m",
        "description": "The turtle system: buy a 20-bar high breakout, exit on a 10-bar low breakdown.",
        "entry": {
            "indicator": "DONCH_HIGH",
            "period": 20,
            "condition": "crosses_above",
            "value": "self",
        },
        "exit": {
            "indicator": "DONCH_LOW",
            "period": 10,
            "condition": "crosses_below",
            "value": "self",
        },
        "tp_levels": [1.5, 3.0],
        "sl_pct": 1.2,
    },
    {
        "id": "vwap-reversion",
        "name": "VWAP Reversion",
        "symbol": "HDFCBANK",
        "interval": "5m",
        "description": "Buy the dip below rolling VWAP(20), exit when price reclaims it.",
        "entry": {"indicator": "VWAP", "period": 20, "condition": "crosses_below", "value": "self"},
        "exit": {"indicator": "VWAP", "period": 20, "condition": "crosses_above", "value": "self"},
        "tp_levels": [0.8, 1.6],
        "sl_pct": 0.9,
    },
    {
        "id": "golden-cross",
        "name": "Golden Cross",
        "symbol": "BTC/USDT",
        "interval": "5m",
        "description": "The classic trend filter: SMA(20) crossing above SMA(50). Exit on the death cross.",
        "entry": {
            "indicator": "SMA",
            "period": 20,
            "condition": "crosses_above",
            "value": {"indicator": "SMA", "period": 50},
        },
        "exit": {
            "indicator": "SMA",
            "period": 20,
            "condition": "crosses_below",
            "value": {"indicator": "SMA", "period": 50},
        },
        "tp_levels": [2.0, 4.0],
        "sl_pct": 1.5,
    },
    {
        "id": "stoch-reversal",
        "name": "Stochastic Reversal",
        "symbol": "INFY",
        "interval": "5m",
        "description": "Oversold snap-back: buy when Stochastic(14,3) crosses below 20, exit when it crosses above 80.",
        "entry": {"indicator": "STOCH_K", "period": 14, "condition": "crosses_below", "value": 20},
        "exit": {"indicator": "STOCH_K", "period": 14, "condition": "crosses_above", "value": 80},
        "tp_levels": [1.2, 2.4],
        "sl_pct": 1.0,
    },
    {
        "id": "adx-trend",
        "name": "ADX Trend Rider",
        "symbol": "RELIANCE",
        "interval": "5m",
        "description": "Trade only strong trends: enter when ADX(14) crosses above 25, exit when it decays below 20.",
        "entry": {"indicator": "ADX", "period": 14, "condition": "crosses_above", "value": 25},
        "exit": {"indicator": "ADX", "period": 14, "condition": "crosses_below", "value": 20},
        "tp_levels": [2.0, 4.0],
        "sl_pct": 1.2,
    },
]


def get_strategy(strategy_id: str) -> dict | None:
    for s in STRATEGIES:
        if s["id"] == strategy_id:
            return s
    return None


def strategy_metadata(strategy: dict) -> dict:
    """Merge identity/metadata fields into a strategy (never mutates)."""
    meta = _META.get(strategy["id"], {})
    return {
        **strategy,
        "version": strategy.get("version", "1.0.0"),
        "family": meta.get("family", "unclassified"),
        "warmup_bars": meta.get("warmup_bars", 20),
        "supported_timeframes": strategy.get("supported_timeframes", ["5m"]),
        "enabled": strategy.get("enabled", True),
        "status": strategy.get("status", "BACKTESTED"),
        "reason_template": meta.get("reason", "signal fired"),
        "intrabar_capable": strategy.get("intrabar_capable", False),
    }


def parameter_snapshot(strategy: dict) -> dict:
    """Exact tunable parameters at signal/backtest time.

    Persisted with every signal/backtest/trade — historical objects must
    never be reconstructed from current defaults.
    """
    snap = {
        "entry": copy.deepcopy(strategy.get("entry")),
        "exit": copy.deepcopy(strategy.get("exit")),
        "tp_levels": target_levels(strategy),
        "sl_pct": strategy.get("sl_pct", 1.0),
    }
    if "tp_pct" in strategy:
        snap["tp_pct"] = strategy["tp_pct"]
    return snap


def strategy_id(strategy: dict) -> str:
    """Canonical identity: id@version."""
    return f"{strategy['id']}@{strategy.get('version', '1.0.0')}"


def target_levels(strategy: dict) -> list[float]:
    """Multi-level take-profit percentages. Defaults to the single tp_pct."""
    levels = strategy.get("tp_levels")
    if isinstance(levels, list) and levels:
        return [float(x) for x in levels]
    return [float(strategy.get("tp_pct", 2.0))]


def compute_indicator(indicator: str, period: int, bars: list[dict]) -> list[float | None]:
    """Evaluate the indicator library against a bar series.

    Composite indicators (MACD hist, BB bands, Donchian channels, VWAP)
    return the single series the declarative conditions can test against.
    """
    from . import indicators

    values = indicators.closes(bars)
    if indicator == "SMA":
        return indicators.sma(values, period)
    if indicator == "EMA":
        return indicators.ema(values, period)
    if indicator == "RSI":
        return indicators.rsi(values, period)
    if indicator == "MACD":
        return indicators.macd(values)
    if indicator == "BB_UPPER":
        return indicators.bollinger(values, period)["upper"]
    if indicator == "BB_MID":
        return indicators.bollinger(values, period)["mid"]
    if indicator == "BB_LOWER":
        return indicators.bollinger(values, period)["lower"]
    if indicator == "DONCH_HIGH":
        return indicators.donchian(values, period)["high"]
    if indicator == "DONCH_LOW":
        return indicators.donchian(values, period)["low"]
    if indicator == "VWAP":
        return indicators.vwap(bars, period)
    if indicator == "ATR":
        return indicators.atr(bars, period)
    if indicator == "STOCH_K":
        return indicators.stochastic(bars, period)
    if indicator == "ADX":
        return indicators.adx(bars, period)
    raise ValueError(f"Unknown indicator: {indicator}")


def condition_met(
    condition: dict,
    series: list[float | None],
    index: int,
    closes_series: list[float] | None = None,
    bars: list[dict] | None = None,
) -> bool:
    """Check a declarative condition at `index`.

    `value == "self"` compares the indicator series to the close series
    (e.g. close crossing its EMA). A dict value computes a second
    indicator series and tests a cross between the two (e.g. SMA20
    crossing SMA50 — golden cross). Otherwise the level is a fixed number.
    """
    return explain_condition(condition, series, index, closes_series, bars)["passed"]


def explain_condition(
    condition: dict,
    series: list[float | None],
    index: int,
    closes_series: list[float] | None = None,
    bars: list[dict] | None = None,
) -> dict:
    """Like condition_met but returns a structured explanation:
    {name, value, operator, threshold, passed} — the "why did this fire"
    payload for signals and the dashboard.
    """
    from . import indicators

    level = condition["value"]
    indicator = condition.get("indicator", "?")
    period = condition.get("period", 0)
    op = "crosses_above" if condition.get("condition") == "crosses_above" else "crosses_below"

    def result(value, threshold, passed):
        return {
            "name": f"{indicator}({period})",
            "value": value,
            "operator": op,
            "threshold": threshold,
            "passed": passed,
        }

    if isinstance(level, dict):
        other = compute_indicator(level["indicator"], level["period"], bars or [])
        if len(other) <= index or any(
            v is None for v in (series[index - 1], series[index], other[index - 1], other[index])
        ):
            return result(None, f"{level['indicator']}({level['period']})", False)
        if op == "crosses_above":
            return result(
                round(series[index], 4),
                f"{level['indicator']}({level['period']})={round(other[index], 4)}",
                series[index - 1] <= other[index - 1] and series[index] > other[index],
            )
        return result(
            round(series[index], 4),
            f"{level['indicator']}({level['period']})={round(other[index], 4)}",
            series[index - 1] >= other[index - 1] and series[index] < other[index],
        )
    if level == "self":
        if closes_series is None:
            return result(None, "close", False)
        ind_prev, ind_cur = series[index - 1], series[index]
        c_prev, c_cur = closes_series[index - 1], closes_series[index]
        if None in (ind_prev, ind_cur):
            return result(None, f"close={round(c_cur, 4) if c_cur is not None else '?'}", False)
        if op == "crosses_above":
            return result(
                round(c_cur, 4),
                f"{indicator}({period})={round(ind_cur, 4)}",
                c_prev <= ind_prev and c_cur > ind_cur,
            )
        return result(
            round(c_cur, 4),
            f"{indicator}({period})={round(ind_cur, 4)}",
            c_prev >= ind_prev and c_cur < ind_cur,
        )
    if op == "crosses_below":
        return result(
            round(series[index], 4) if series[index] is not None else None,
            level,
            indicators.crossed_below(series, index, level),
        )
    return result(
        round(series[index], 4) if series[index] is not None else None,
        level,
        indicators.crossed_above(series, index, level),
    )
