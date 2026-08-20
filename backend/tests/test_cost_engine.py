"""Tests for Advanced Cost Engine."""

from app.cost_engine import (
    COST_PROFILES,
    CostEngine,
    CostProfile,
    CostProfileType,
    estimate_round_trip_cost,
    get_cost_profile,
)


class TestCostProfile:
    def test_profile_creation(self):
        profile = CostProfile(
            profile_type=CostProfileType.CRYPTO_LIQUID,
            name="Test Profile",
            commission_bps=2.0,
            spread_bps=1.0,
            slippage_bps=2.0,
        )
        assert profile.commission_bps == 2.0
        assert profile.spread_bps == 1.0
        assert profile.slippage_bps == 2.0

    def test_total_cost_calculation(self):
        profile = CostProfile(
            profile_type=CostProfileType.CRYPTO_LIQUID,
            name="Test",
            commission_bps=2.0,
            spread_bps=1.0,
            slippage_bps=2.0,
        )
        # Round trip: 2 * (commission + spread + slippage)
        expected = 2 * (2.0 + 1.0 + 2.0)  # 10 bps
        assert profile.total_cost_bps() == expected

    def test_total_cost_with_maker_fee(self):
        profile = CostProfile(
            profile_type=CostProfileType.CRYPTO_LIQUID,
            name="Test",
            commission_bps=2.0,
            spread_bps=1.0,
            slippage_bps=2.0,
            maker_fee_bps=1.0,
            taker_fee_bps=3.0,
        )
        # Taker: 2 * (3 + 1 + 2) = 12
        assert profile.total_cost_bps(is_maker=False) == 12.0
        # Maker: 2 * (1 + 1 + 2) = 8
        assert profile.total_cost_bps(is_maker=True) == 8.0


class TestCostProfiles:
    def test_all_profiles_exist(self):
        for profile_type in CostProfileType:
            assert profile_type in COST_PROFILES
            profile = COST_PROFILES[profile_type]
            assert profile.profile_type == profile_type
            assert profile.name
            assert profile.commission_bps >= 0
            assert profile.spread_bps >= 0
            assert profile.slippage_bps >= 0

    def test_crypto_liquid_profile(self):
        profile = COST_PROFILES[CostProfileType.CRYPTO_LIQUID]
        assert profile.name == "Crypto Liquid (BTC/USDT, ETH/USDT)"
        assert profile.commission_bps == 2.0
        assert profile.spread_bps == 1.0

    def test_forex_major_profile(self):
        profile = COST_PROFILES[CostProfileType.FOREX_MAJOR]
        assert profile.name == "Forex Majors (EUR/USD, GBP/USD)"
        assert profile.lot_size == 100000.0


class TestCostEngine:
    def test_engine_initialization(self):
        engine = CostEngine(CostProfileType.CRYPTO_LIQUID)
        assert engine.default_profile.profile_type == CostProfileType.CRYPTO_LIQUID

    def test_estimate_trade_cost(self):
        engine = CostEngine(CostProfileType.CRYPTO_LIQUID)
        result = engine.estimate_trade_cost(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=50000.0,
        )
        assert result["symbol"] == "BTC/USDT"
        assert result["side"] == "buy"
        assert result["quantity"] == 1.0
        assert result["price"] == 50000.0
        assert result["notional"] == 50000.0
        assert result["total_cost"] > 0
        assert result["total_cost_bps"] > 0

    def test_scenario_analysis(self):
        engine = CostEngine(CostProfileType.CRYPTO_LIQUID)
        result = engine.scenario_analysis(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=50000.0,
        )
        assert "scenarios" in result
        assert "ZERO" in result["scenarios"]
        assert "BASE" in result["scenarios"]
        assert "STRESS" in result["scenarios"]
        assert "cost_drag_analysis" in result

        # Check that costs increase with stress
        base_cost = result["scenarios"]["BASE"]["total_cost_bps"]
        stress_cost = result["scenarios"]["STRESS"]["total_cost_bps"]
        assert stress_cost > base_cost

    def test_cost_drag_analysis(self):
        engine = CostEngine(CostProfileType.CRYPTO_LIQUID)
        result = engine.scenario_analysis(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=50000.0,
        )
        drag = result["cost_drag_analysis"]
        assert "base_cost_bps" in drag
        assert "stress_cost_bps" in drag
        assert "cost_multiplier" in drag
        assert drag["cost_multiplier"] > 1.0


class TestHelperFunctions:
    def test_get_cost_profile(self):
        profile = get_cost_profile(CostProfileType.FOREX_MAJOR)
        assert profile.profile_type == CostProfileType.FOREX_MAJOR

    def test_estimate_round_trip_cost(self):
        profile = COST_PROFILES[CostProfileType.CRYPTO_LIQUID]
        cost = estimate_round_trip_cost(profile, notional=100000)
        # Uses taker_fee_bps (2.0) + spread (1.0) + slippage (3.0) = 6 per side * 2 = 12 bps
        expected = 2 * (2.0 + 1.0 + 3.0)  # 12 bps
        assert cost == expected
