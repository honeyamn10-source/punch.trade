"""Canonical instrument model + symbol normalization.

One Instrument representation across every provider:

- Asset classes are explicit (never inferred from string formatting alone).
- Provider-specific symbol forms live in provider adapters, never in
  strategies.  This module only knows the canonical forms:

    BTC/USDT              -> CRYPTO
    EUR/USD, USD/JPY, ..  -> FOREX
    AAPL, MSFT, SPY       -> US EQUITY / ETF / INDEX
    NSE:RELIANCE          -> INDIA EQUITY
    NSE:NIFTY50           -> INDIA INDEX
    NFO:NIFTY-2026-08-27-FUT        -> INDIA FUTURE
    NFO:NIFTY-2026-08-27-25000-CE   -> INDIA OPTION (CALL)
    MCX:GOLD-2026-10-05   -> MCX COMMODITY FUTURE
    GOLD, SILVER, WTI     -> COMMODITY REFERENCE (macro series)
"""

from __future__ import annotations

import dataclasses
import re
from enum import StrEnum

FOREX_MAJORS = {
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CAD",
    "AUD/USD",
    "NZD/USD",
    "USD/CHF",
    "EUR/GBP",
}


class AssetClass(StrEnum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    ETF = "ETF"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FOREX = "FOREX"
    COMMODITY = "COMMODITY"


# macro/reference commodity series — NOT tradable futures contracts
COMMODITY_REFERENCES = {
    "GOLD",
    "SILVER",
    "WTI",
    "BRENT",
    "NATURAL_GAS",
    "COPPER",
    "CRUDE_OIL",
}

US_INDICES = {"SPX", "NDX", "DJI", "VIX", "RUT"}

# Indian index symbols (exchange token names are prefixed in provider adapters)
INDIA_INDICES = {"NIFTY50", "NIFTY", "BANKNIFTY", "INDIAVIX", "FINNIFTY", "SENSEX"}


class OptionType(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


def normalize_symbol(symbol: str) -> str:
    """Canonical spelling: uppercase, trimmed, single slash for pairs."""
    s = str(symbol or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    if "/" in s and ":" not in s:
        left, right = s.split("/", 1)
        s = f"{left}/{right}"
    return s


_FUT_RE = re.compile(r"^(.+?)-(\d{4}-\d{2}-\d{2})-FUT$")
_OPT_RE = re.compile(r"^(.+?)-(\d{4}-\d{2}-\d{2})-(\d+(?:\.\d+)?)-(CE|PE)$")


@dataclasses.dataclass
class Instrument:
    """Canonical instrument. Optional fields stay None when unknown."""

    symbol: str
    asset_class: AssetClass
    exchange: str = ""
    segment: str = ""
    base_currency: str = ""
    quote_currency: str = ""
    currency: str = ""
    provider: str = ""
    provider_symbol: str = ""
    provider_instrument_id: str = ""
    tick_size: float | None = None
    lot_size: int | None = None
    min_quantity: float | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    underlying: str = ""
    timezone: str = "UTC"

    def display_symbol(self) -> str:
        return self.symbol

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["asset_class"] = self.asset_class.value
        d["option_type"] = self.option_type.value if self.option_type else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Instrument:
        return cls(
            symbol=str(d.get("symbol", "")),
            asset_class=AssetClass(d.get("asset_class", "EQUITY")),
            exchange=str(d.get("exchange", "")),
            segment=str(d.get("segment", "")),
            base_currency=str(d.get("base_currency", "")),
            quote_currency=str(d.get("quote_currency", "")),
            currency=str(d.get("currency", "")),
            provider=str(d.get("provider", "")),
            provider_symbol=str(d.get("provider_symbol", "")),
            provider_instrument_id=str(d.get("provider_instrument_id", "")),
            tick_size=d.get("tick_size"),
            lot_size=d.get("lot_size"),
            min_quantity=d.get("min_quantity"),
            expiry=d.get("expiry"),
            strike=d.get("strike"),
            option_type=OptionType(d["option_type"]) if d.get("option_type") else None,
            underlying=str(d.get("underlying", "")),
            timezone=str(d.get("timezone", "UTC")),
        )


def parse_instrument(symbol: str) -> Instrument:
    """Parse a canonical symbol into an Instrument with best-effort metadata."""
    s = normalize_symbol(symbol)
    if not s:
        raise ValueError("empty symbol")

    # ---- pairs ----------------------------------------------------------
    if "/" in s:
        base, quote = s.split("/", 1)
        if s in FOREX_MAJORS:
            return Instrument(
                symbol=s,
                asset_class=AssetClass.FOREX,
                exchange="FX",
                base_currency=base,
                quote_currency=quote,
                currency=quote,
                tick_size=0.0001,
            )
        return Instrument(
            symbol=s,
            asset_class=AssetClass.CRYPTO,
            exchange="CRYPTO",
            base_currency=base,
            quote_currency=quote,
            currency=quote,
            timezone="UTC",
        )

    # ---- Indian exchange prefixes --------------------------------------
    if ":" in s:
        exchange, rest = s.split(":", 1)
        ex = exchange.upper()
        if ex in ("NSE", "BSE", "NFO", "BFO", "MCX", "INDICES"):
            return _parse_indian(ex, rest, s)
        raise ValueError(f"unsupported exchange prefix in '{s}'")

    # ---- commodity reference series ------------------------------------
    if s in COMMODITY_REFERENCES:
        return Instrument(
            symbol=s,
            asset_class=AssetClass.COMMODITY,
            exchange="REFERENCE",
            base_currency=s,
            currency="USD",
        )

    # ---- US symbols -----------------------------------------------------
    if s in US_INDICES:
        return Instrument(symbol=s, asset_class=AssetClass.INDEX, exchange="US", currency="USD")
    if s in ("SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "VTI", "VOO"):
        return Instrument(symbol=s, asset_class=AssetClass.ETF, exchange="US", currency="USD")
    return Instrument(symbol=s, asset_class=AssetClass.EQUITY, exchange="US", currency="USD")


def _parse_indian(exchange: str, rest: str, full: str) -> Instrument:
    if exchange in ("NFO", "BFO"):
        m = _FUT_RE.match(rest)
        if m:
            underlying, expiry = m.group(1), m.group(2)
            return Instrument(
                symbol=full,
                asset_class=AssetClass.FUTURE,
                exchange=exchange,
                segment="FUT",
                underlying=underlying,
                expiry=expiry,
                currency="INR",
                timezone="Asia/Kolkata",
            )
        m = _OPT_RE.match(rest)
        if m:
            underlying, expiry, strike, opt = m.groups()
            return Instrument(
                symbol=full,
                asset_class=AssetClass.OPTION,
                exchange=exchange,
                segment="OPT",
                underlying=underlying,
                expiry=expiry,
                strike=float(strike),
                option_type=OptionType.CALL if opt == "CE" else OptionType.PUT,
                currency="INR",
                timezone="Asia/Kolkata",
            )
        raise ValueError(f"cannot parse {exchange} instrument '{rest}'")
    if exchange == "MCX":
        m = re.match(r"^(.+?)(?:-(\d{4}-\d{2}-\d{2}))?$", rest)
        name = m.group(1)
        expiry = m.group(2)
        return Instrument(
            symbol=full,
            asset_class=AssetClass.COMMODITY,
            exchange="MCX",
            segment="COMMODITY_FUT",
            underlying=name,
            expiry=expiry,
            currency="INR",
            timezone="Asia/Kolkata",
        )
    if exchange == "INDICES":
        return Instrument(
            symbol=full,
            asset_class=AssetClass.INDEX,
            exchange="NSE",
            segment="INDEX",
            currency="INR",
            timezone="Asia/Kolkata",
        )
    is_index = rest in INDIA_INDICES or rest.startswith("NIFTY") or rest.startswith("BANKNIFTY")
    return Instrument(
        symbol=full,
        asset_class=AssetClass.INDEX if is_index else AssetClass.EQUITY,
        exchange=exchange,
        segment="INDEX" if is_index else "EQUITY",
        currency="INR",
        timezone="Asia/Kolkata",
    )


def provider_symbol_for(provider_id: str, instrument: Instrument) -> str:
    """Map a canonical instrument to a provider's symbol form.

    Keeps conversion logic inside the provider layer (one place), never in
    strategies.
    """
    s = instrument.symbol
    if provider_id == "binance":
        return s.replace("/", "")
    if provider_id == "coingecko":
        return _coingecko_id(instrument)
    if provider_id in ("dhan", "upstox", "angel"):
        return _indian_provider_symbol(instrument)
    if provider_id == "twelve_data":
        return s
    if provider_id == "alpha_vantage":
        if instrument.asset_class == AssetClass.FOREX:
            return s.replace("/", "")
        if instrument.asset_class == AssetClass.COMMODITY:
            return instrument.underlying or s
        return s
    if provider_id == "alpaca":
        return s.split(":")[-1]
    return s


def _coingecko_id(instrument: Instrument) -> str:
    """Best-effort CoinGecko coin id for a crypto pair (base coin)."""
    base = (instrument.base_currency or instrument.symbol.split("/")[0]).lower()
    return {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "bnb": "binancecoin",
        "xrp": "ripple",
        "doge": "dogecoin",
        "ada": "cardano",
        "avax": "avalanche-2",
        "link": "chainlink",
        "usdt": "tether",
        "usdc": "usd-coin",
    }.get(base, base)


def _indian_provider_symbol(instrument: Instrument) -> str:
    ex = instrument.exchange
    if ex == "MCX":
        return instrument.underlying or instrument.symbol
    if ex in ("NFO", "BFO"):
        return instrument.underlying or instrument.symbol
    return (instrument.underlying or instrument.symbol).split(":")[-1]
