"""Advanced Cost Engine - market-specific cost profiles and execution sensitivity analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np


class CostProfileType(Enum):
    """Predefined market-specific cost profiles."""

    CRYPTO_LIQUID = "crypto_liquid"      # BTC/USDT, ETH/USDT on major exchanges
    CRYPTO_ALT = "crypto_alt"            # Lower liquidity crypto
    US_LARGE_CAP = "us_large_cap"        # SPY, QQQ, AAPL, MSFT
    US_SMALL_CAP = "us_small_cap"        # Small/micro cap US equities
    INDIA_CASH = "india_cash"            # NSE/BSE cash equities
    INDIA_FUTURES = "india_futures"      # NSE F&O
    FOREX_MAJOR = "forex_major"          # EUR/USD, GBP/USD, USD/JPY
    FOREX_MINOR = "forex_minor"          # Cross pairs, EM currencies
    COMMODITY_FUTURES = "commodity_futures"  # Gold, oil futures


@dataclass
class CostProfile:
    """Market-specific cost parameters."""

    profile_type: CostProfileType
    name: str

    # Commission (per side, as fraction of notional)
    commission_bps: float = 1.0          # 1 bps = 0.01%

    # Spread (as fraction of mid price)
    spread_bps: float = 5.0              # Typical spread in bps

    # Slippage (as fraction of mid price, additional to spread)
    slippage_bps: float = 2.0            # Expected slippage in bps

    # Latency (ms) - affects slippage during fast moves
    latency_ms: int = 50

    # Minimum tick size (price increment)
    min_tick: float = 0.01

    # Lot/step size
    lot_size: float = 1.0

    # Maker/taker fee structure (if applicable)
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0

    # Funding/borrow cost (annualized) for shorts
    borrow_cost_apy: float = 0.0

    def total_cost_bps(self, is_maker: bool = False, is_short: bool = False) -> float:
        """Total round-trip cost in basis points."""
        fee = self.maker_fee_bps if is_maker else self.taker_fee_bps
        if fee == 0:
            fee = self.commission_bps
        total = 2 * (fee + self.spread_bps + self.slippage_bps)
        if is_short and self.borrow_cost_apy > 0:
            # Add daily borrow cost (approximate)
            total += self.borrow_cost_apy / 365 * 10000  # convert to bps per day
        return total


# Predefined cost profiles
COST_PROFILES = {
    CostProfileType.CRYPTO_LIQUID: CostProfile(
        profile_type=CostProfileType.CRYPTO_LIQUID,
        name="Crypto Liquid (BTC/USDT, ETH/USDT)",
        commission_bps=2.0,      # 0.02% per side (taker)
        spread_bps=1.0,          # Very tight
        slippage_bps=3.0,
        latency_ms=20,
        min_tick=0.01,
        lot_size=0.00001,
        maker_fee_bps=1.0,
        taker_fee_bps=2.0,
    ),
    CostProfileType.CRYPTO_ALT: CostProfile(
        profile_type=CostProfileType.CRYPTO_ALT,
        name="Crypto Altcoins",
        commission_bps=5.0,
        spread_bps=10.0,
        slippage_bps=15.0,
        latency_ms=50,
        min_tick=0.00001,
        lot_size=1.0,
        maker_fee_bps=2.0,
        taker_fee_bps=5.0,
    ),
    CostProfileType.US_LARGE_CAP: CostProfile(
        profile_type=CostProfileType.US_LARGE_CAP,
        name="US Large Cap (SPY, QQQ, AAPL)",
        commission_bps=0.5,      # $0.005/share typical
        spread_bps=1.0,
        slippage_bps=2.0,
        latency_ms=10,
        min_tick=0.01,
        lot_size=1.0,
    ),
    CostProfileType.US_SMALL_CAP: CostProfile(
        profile_type=CostProfileType.US_SMALL_CAP,
        name="US Small/Micro Cap",
        commission_bps=1.0,
        spread_bps=15.0,
        slippage_bps=20.0,
        latency_ms=20,
        min_tick=0.01,
        lot_size=1.0,
    ),
    CostProfileType.INDIA_CASH: CostProfile(
        profile_type=CostProfileType.INDIA_CASH,
        name="India NSE/BSE Cash",
        commission_bps=2.5,      # 0.025% + STT
        spread_bps=5.0,
        slippage_bps=5.0,
        latency_ms=50,
        min_tick=0.05,
        lot_size=1.0,
    ),
    CostProfileType.INDIA_FUTURES: CostProfile(
        profile_type=CostProfileType.INDIA_FUTURES,
        name="India NSE F&O",
        commission_bps=1.5,
        spread_bps=3.0,
        slippage_bps=5.0,
        latency_ms=30,
        min_tick=0.05,
        lot_size=1.0,
    ),
    CostProfileType.FOREX_MAJOR: CostProfile(
        profile_type=CostProfileType.FOREX_MAJOR,
        name="Forex Majors (EUR/USD, GBP/USD)",
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=1.0,
        latency_ms=10,
        min_tick=0.00001,
        lot_size=100000.0,  # Standard lot
    ),
    CostProfileType.FOREX_MINOR: CostProfile(
        profile_type=CostProfileType.FOREX_MINOR,
        name="Forex Minors/Exotics",
        commission_bps=1.0,
        spread_bps=8.0,
        slippage_bps=5.0,
        latency_ms=20,
        min_tick=0.00001,
        lot_size=100000.0,
    ),
    CostProfileType.COMMODITY_FUTURES: CostProfile(
        profile_type=CostProfileType.COMMODITY_FUTURES,
        name="Commodity Futures (Gold, Oil)",
        commission_bps=1.0,
        spread_bps=2.0,
        slippage_bps=3.0,
        latency_ms=20,
        min_tick=0.01,
        lot_size=1.0,
    ),
}


def get_cost_profile(profile_type: CostProfileType) -> CostProfile:
    """Get predefined cost profile."""
    return COST_PROFILES[profile_type]


class CostEngine:
    """Advanced cost engine with scenario analysis."""

    def __init__(self, default_profile: CostProfileType = CostProfileType.CRYPTO_LIQUID):
        self.default_profile = get_cost_profile(default_profile)
        self.profiles = COST_PROFILES.copy()

    def add_custom_profile(self, profile: CostProfile):
        """Add a custom cost profile."""
        self.profiles[profile.profile_type] = profile

    def estimate_trade_cost(
        self,
        symbol: str,
        side: str,          # "buy" or "sell"
        quantity: float,
        price: float,
        profile: Optional[CostProfile] = None,
        is_maker: bool = False,
    ) -> dict:
        """Estimate total cost for a trade."""
        profile = profile or self.default_profile

        notional = quantity * price
        commission = notional * (profile.commission_bps / 10000)
        spread_cost = notional * (profile.spread_bps / 10000)
        slippage = notional * (profile.slippage_bps / 10000)

        total_cost = commission + spread_cost + slippage
        total_bps = (total_cost / notional) * 10000 if notional > 0 else 0

        return {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "notional": notional,
            "commission": commission,
            "spread_cost": spread_cost,
            "slippage": slippage,
            "total_cost": total_cost,
            "total_cost_bps": total_bps,
            "profile": profile.name,
        }

    def scenario_analysis(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        profile: Optional[CostProfile] = None,
    ) -> dict:
        """Run cost sensitivity analysis across scenarios."""
        profile = profile or self.default_profile

        scenarios = {
            "ZERO": {"commission_mult": 0.0, "spread_mult": 0.0, "slippage_mult": 0.0},
            "LOW": {"commission_mult": 0.5, "spread_mult": 0.5, "slippage_mult": 0.5},
            "BASE": {"commission_mult": 1.0, "spread_mult": 1.0, "slippage_mult": 1.0},
            "HIGH": {"commission_mult": 2.0, "spread_mult": 2.0, "slippage_mult": 2.0},
            "STRESS": {"commission_mult": 3.0, "spread_mult": 3.0, "slippage_mult": 3.0},
        }

        results = {}
        for name, mult in scenarios.items():
            mod_profile = CostProfile(
                profile_type=profile.profile_type,
                name=profile.name,
                commission_bps=profile.commission_bps * mult["commission_mult"],
                spread_bps=profile.spread_bps * mult["spread_mult"],
                slippage_bps=profile.slippage_bps * mult["slippage_mult"],
                latency_ms=profile.latency_ms,
                min_tick=profile.min_tick,
                lot_size=profile.lot_size,
                maker_fee_bps=profile.maker_fee_bps,
                taker_fee_bps=profile.taker_fee_bps,
                borrow_cost_apy=profile.borrow_cost_apy,
            )
            result = self.estimate_trade_cost(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                profile=mod_profile,
            )
            results[name] = {
                "total_cost_bps": result["total_cost_bps"],
                "net_return_estimate": -result["total_cost_bps"] / 10000,  # cost as negative return
            }

        return {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "scenarios": results,
            "cost_drag_analysis": self._analyze_cost_drag(results),
        }

    def _analyze_cost_drag(self, results: dict) -> dict:
        """Analyze cost drag across scenarios."""
        base_bps = results.get("BASE", {}).get("total_cost_bps", 0)
        stress_bps = results.get("STRESS", {}).get("total_cost_bps", 0)

        return {
            "base_cost_bps": base_bps,
            "stress_cost_bps": stress_bps,
            "cost_multiplier": stress_bps / base_bps if base_bps > 0 else 0,
            "edge_erodes_at_mult": 2.0 if base_bps > 0 else 0,  # At what multiplier edge disappears
        }


def estimate_round_trip_cost(
    profile: CostProfile,
    notional: float,
    is_maker: bool = False,
    is_short: bool = False,
) -> float:
    """Quick round-trip cost estimation in bps."""
    return profile.total_cost_bps(is_maker=is_maker, is_short=is_short)