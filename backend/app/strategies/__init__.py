"""PUNCH NEXUS Strategy Package.

This module maintains backward compatibility with the legacy strategies.py
while providing the new class-based strategy architecture.
"""

import contextlib

from .base import (
    AssetClass,
    ParameterSpec,
    Signal,
    SignalDirection,
    Strategy,
    StrategyFamily,
    StrategyRegistry,
    StrategyStatus,
    Timeframe,
    register_strategy,
)

# Import strategy modules to register them
with contextlib.suppress(ImportError):
    from .trend import adaptive_trend  # noqa: F401

# ---------------------------------------------------------------
# Backward compatibility with legacy strategies.py
# ---------------------------------------------------------------
from app.strategies_old import (
    STRATEGIES,
    compute_indicator,
    condition_met,
    explain_condition,
    get_strategy,
    parameter_snapshot,
    strategy_id,
    strategy_metadata,
    target_levels,
)

__all__ = [
    # New architecture
    "AssetClass",
    "ParameterSpec",
    "Signal",
    "SignalDirection",
    "Strategy",
    "StrategyFamily",
    "StrategyStatus",
    "Timeframe",
    "StrategyRegistry",
    "register_strategy",
    # Legacy compatibility
    "STRATEGIES",
    "compute_indicator",
    "condition_met",
    "parameter_snapshot",
    "strategy_metadata",
    "target_levels",
    "explain_condition",
    "get_strategy",
    "strategy_id",
]
