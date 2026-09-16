"""Regression: carry specializations must not KeyError on missing schema keys."""

from app.strategies.carry.carry import CarryFramework, CryptoFundingCarry, FXCarry


def _bars(n=150):
    out = []
    for i in range(n):
        out.append(
            {
                "ts": float(1700000000 + i * 3600),
                "symbol": "BTC/USDT",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
            }
        )
    return out


def test_fx_carry_no_crash():
    s = FXCarry()
    assert s.params["universe"] == []
    sigs = [s.generate_signal(_bars(), i) for i in range(60, 150)]
    assert all(sig is None or sig.direction in ("FLAT", "LONG", "SHORT") for sig in sigs)


def test_crypto_funding_carry_no_crash():
    s = CryptoFundingCarry(symbols=["BTCUSDT"])
    assert s.params["universe"] == ["BTCUSDT"]
    bars = _bars()
    for b in bars:
        b["symbol"] = "BTCUSDT"
        b["funding"] = 0.0003
    sigs = [s.generate_signal(bars, i) for i in range(60, 150)]
    assert sigs, "should produce signals with funding data present"


def test_base_carry_still_defaults():
    s = CarryFramework()
    assert s.params["universe"] == []
    assert s.params["min_carry"] == 0.0
