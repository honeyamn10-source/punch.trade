"""Base strategy interfaces and data structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AssetClass(StrEnum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    ETF = "ETF"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FOREX = "FOREX"
    COMMODITY = "COMMODITY"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class StrategyStatus(StrEnum):
    DRAFT = "DRAFT"
    BACKTESTED = "BACKTESTED"
    VALIDATED = "VALIDATED"
    OOS_VALIDATED = "OOS_VALIDATED"
    WF_VALIDATED = "WF_VALIDATED"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    LIVE_ACTIVE = "LIVE_ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class StrategyFamily(StrEnum):
    TREND = "trend"
    ROTATION = "rotation"
    REVERSION = "reversion"
    BREAKOUT = "breakout"
    STATARB = "statarb"
    CARRY = "carry"
    CROSS_SECTION = "cross_section"
    ENSEMBLE = "ensemble"


@dataclass
class ParameterSpec:
    """Schema for a single strategy parameter."""

    name: str
    type: type
    default: Any
    description: str
    min_value: float | None = None
    max_value: float | None = None
    choices: list[Any] | None = None


@dataclass
class Signal:
    """Trading signal output."""

    strategy_id: str
    symbol: str
    direction: SignalDirection
    timestamp: datetime
    price: float
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

    # Risk parameters (set by strategy or risk engine)
    stop_loss: float | None = None
    take_profit: list[float] | None = None
    position_size: float | None = None


@dataclass
class StrategyMetrics:
    """Performance metrics for a strategy."""

    strategy_id: str
    total_trades: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    avg_holding_bars: float = 0.0
    turnover: float = 0.0


class Strategy(ABC):
    """Abstract base class for all strategies."""

    # Class-level metadata (override in subclasses)
    strategy_id: str = ""
    version: str = "1.0.0"
    family: StrategyFamily = StrategyFamily.TREND
    name: str = ""
    description: str = ""

    supported_asset_classes: list[AssetClass] = []
    supported_timeframes: list[Timeframe] = []

    # Parameter schema for research/optimization
    parameter_schema: list[ParameterSpec] = []

    # Warmup bars needed before first signal
    warmup_bars: int = 50

    # Required feature columns in bar data
    required_features: list[str] = field(default_factory=list)

    def __init__(self, **params):
        """Initialize strategy with parameter overrides."""
        self.params = {}
        for spec in self.parameter_schema:
            self.params[spec.name] = params.get(spec.name, spec.default)

    @abstractmethod
    def generate_signal(self, bars: list[dict], current_idx: int) -> Signal | None:
        """Generate signal at current_idx using historical bars up to that point.

        Args:
            bars: List of bar dicts with OHLCV + features
            current_idx: Index of current bar (0 = oldest)

        Returns:
            Signal or None
        """
        pass

    def get_param(self, name: str, default=None):
        return self.params.get(name, default)

    def set_param(self, name: str, value):
        self.params[name] = value

    def parameter_snapshot(self) -> dict:
        """Return current parameters for trial recording."""
        return dict(self.params)

    def warmup_satisfied(self, bars: list[dict], current_idx: int) -> bool:
        return current_idx >= self.warmup_bars


# --------------------------------------------------------------- registry ----
class StrategyRegistry:
    """Central registry for all strategy classes."""

    _strategies: dict[str, type[Strategy]] = {}

    @classmethod
    def register(cls, strategy_class: type[Strategy]) -> type[Strategy]:
        if not strategy_class.strategy_id:
            raise ValueError("Strategy must have strategy_id")
        cls._strategies[strategy_class.strategy_id] = strategy_class
        return strategy_class

    @classmethod
    def get(cls, strategy_id: str) -> type[Strategy] | None:
        return cls._strategies.get(strategy_id)

    @classmethod
    def all(cls) -> list[type[Strategy]]:
        return list(cls._strategies.values())

    @classmethod
    def by_family(cls, family: StrategyFamily) -> list[type[Strategy]]:
        return [s for s in cls._strategies.values() if s.family == family]

    @classmethod
    def create(cls, strategy_id: str, **params) -> Strategy | None:
        klass = cls.get(strategy_id)
        if klass:
            return klass(**params)
        return None


def register_strategy(klass: type[Strategy]) -> type[Strategy]:
    """Decorator for registering strategy classes."""
    return StrategyRegistry.register(klass)
