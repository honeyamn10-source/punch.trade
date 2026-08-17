"""Alpha Vantage — commodity reference, forex fallback, historical backup.

Free tier: 25 requests/day with a personal key; use conservatively.
Commodity endpoints (GOLD, SILVER, WTI, BRENT, NATURAL_GAS, COPPER)
return macro/reference price series — NOT tradable futures contracts.
"""

from __future__ import annotations

import os
import time

from ..instruments import AssetClass, Instrument, provider_symbol_for
from ..rate_limit import RateLimiter, throttled_request
from .base import (
    HealthState,
    MarketDataProvider,
    ProviderError,
    ProviderErrorCode,
    candle_dict,
    check_status,
)

BASE = "https://www.alphavantage.co/query"

# Alpha Vantage commodity functions (reference series, daily)
COMMODITY_FUNCTIONS = {
    "WTI": "WTI",
    "BRENT": "BRENT",
    "NATURAL_GAS": "NATURAL_GAS",
    "COPPER": "COPPER",
    "CRUDE_OIL": "WTI",
    # GOLD / SILVER are NOT published by Alpha Vantage's API — the router
    # falls back to Dhan/MCX for them or reports DATA_UNAVAILABLE.
}

TIMEFRAME_AV = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "daily"}


class AlphaVantageProvider(MarketDataProvider):
    provider_id = "alpha_vantage"
    display_name = "Alpha Vantage"
    asset_classes = (AssetClass.COMMODITY, AssetClass.FOREX, AssetClass.EQUITY)
    needs_credentials = True
    feed_label = "Alpha Vantage free"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
        self.configured = bool(self.api_key)
        self.state = HealthState.AUTH_REQUIRED if not self.api_key else HealthState.READY
        self.limiter = RateLimiter(2.0)

    def _get(self, params: dict) -> dict:
        if not self.api_key:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "alpha vantage: set ALPHA_VANTAGE_API_KEY",
            )
        params = {**params, "apikey": self.api_key}
        try:
            resp = throttled_request(self.provider_id, self.limiter, "GET", BASE, params=params)
        except Exception:
            self.state = HealthState.OFFLINE
            self.last_error = "alpha vantage unreachable"
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OFFLINE, "alpha vantage unreachable"
            ) from None
        check_status(resp, self.provider_id)
        body = resp.json()
        msg = body.get("Note") or body.get("Information") or ""
        if "rate limit" in str(msg).lower():
            self.state = HealthState.RATE_LIMITED
            self.last_error = "alpha vantage rate limited"
            raise ProviderError(
                ProviderErrorCode.PROVIDER_RATE_LIMITED, "alpha vantage rate limited"
            )
        if isinstance(body, dict) and "Error Message" in body:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_BAD_RESPONSE,
                str(body["Error Message"]),
            )
        return body

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        out: list[dict] = []
        for name in COMMODITY_FUNCTIONS:
            if q in name:
                out.append(
                    Instrument(
                        symbol=name,
                        asset_class=AssetClass.COMMODITY,
                        exchange="REFERENCE",
                        base_currency=name,
                        currency="USD",
                        provider="alpha_vantage",
                    ).to_dict()
                )
        if q in ("EUR", "GBP", "USD", "JPY", "CAD", "AUD", "NZD", "CHF"):
            for pair in (
                "EUR/USD",
                "GBP/USD",
                "USD/JPY",
                "USD/CAD",
                "AUD/USD",
                "NZD/USD",
                "USD/CHF",
                "EUR/GBP",
            ):
                if q in pair:
                    out.append(
                        Instrument(
                            symbol=pair,
                            asset_class=AssetClass.FOREX,
                            exchange="FX",
                            provider="alpha_vantage",
                        ).to_dict()
                    )
        if not out and q:
            out.append(
                Instrument(
                    symbol=q,
                    asset_class=AssetClass.EQUITY,
                    exchange="US",
                    currency="USD",
                    provider="alpha_vantage",
                ).to_dict()
            )
        return out[:limit]

    def get_instrument(self, instrument: Instrument) -> dict:
        return {
            "symbol": instrument.symbol,
            "provider": "alpha_vantage",
            "provider_symbol": provider_symbol_for("alpha_vantage", instrument),
        }

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        if instrument.asset_class == AssetClass.COMMODITY:
            fn = COMMODITY_FUNCTIONS.get(instrument.underlying or instrument.symbol)
            if not fn:
                raise ProviderError(
                    ProviderErrorCode.SYMBOL_NOT_FOUND, f"no commodity {instrument.symbol}"
                )
            try:
                body = self._get({"function": fn, "interval": "monthly"})
            except ProviderError:
                raise
            series = body.get("data") or body.get(f"{fn} (Monthly)") or {}
            ts = max(series.keys()) if series else None
            if not ts:
                raise ProviderError(
                    ProviderErrorCode.DATA_UNAVAILABLE,
                    f"no commodity series for {instrument.symbol}",
                )
            return {
                "symbol": instrument.symbol,
                "price": float(series[ts].get("value") or series[ts].get("price") or 0),
                "bid": None,
                "ask": None,
                "change": None,
                "changePct": None,
                "ts": _parse_av_ts(ts),
                "source": "alpha_vantage",
            }
        sym = provider_symbol_for("alpha_vantage", instrument)
        if instrument.asset_class == AssetClass.FOREX:
            base, quote = (
                instrument.base_currency or sym.split("/")[0],
                instrument.quote_currency or sym.split("/")[1],
            )
            try:
                body = self._get(
                    {
                        "function": "CURRENCY_EXCHANGE_RATE",
                        "from_currency": base,
                        "to_currency": quote,
                    }
                )
            except ProviderError:
                raise
            info = body.get("Realtime Currency Exchange Rate") or {}
            try:
                price = float(info["5. Exchange Rate"])
            except (KeyError, TypeError, ValueError):
                raise ProviderError(
                    ProviderErrorCode.DATA_UNAVAILABLE, f"no fx rate for {instrument.symbol}"
                ) from None
            return {
                "symbol": instrument.symbol,
                "price": price,
                "bid": None,
                "ask": None,
                "change": None,
                "changePct": None,
                "ts": time.time(),
                "source": "alpha_vantage",
            }
        raise ProviderError(
            ProviderErrorCode.DATA_UNAVAILABLE, "alpha vantage: equity quotes not supported"
        )

    # ----------------------------------------------------------- candles --
    def get_candles(
        self,
        instrument: Instrument,
        timeframe: str,
        *,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        tf = TIMEFRAME_AV.get(timeframe)
        if instrument.asset_class == AssetClass.FOREX and tf is None:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"alpha vantage has no {timeframe} for fx"
            )
        if instrument.asset_class == AssetClass.COMMODITY:
            fn = COMMODITY_FUNCTIONS.get(instrument.underlying or instrument.symbol)
            if not fn:
                raise ProviderError(
                    ProviderErrorCode.SYMBOL_NOT_FOUND, f"no commodity {instrument.symbol}"
                )
            try:
                body = self._get({"function": fn})
            except ProviderError:
                raise
            series = body.get("data") or {}
            out = []
            for ts_key in sorted(series.keys()):
                row = series[ts_key]
                ts = _parse_av_ts(ts_key)
                try:
                    price = float(row.get("value") or row.get("price"))
                except (TypeError, ValueError):
                    continue
                out.append(
                    candle_dict(
                        instrument.symbol,
                        "1d",
                        ts,
                        price,
                        price,
                        price,
                        price,
                        0.0,
                        "alpha_vantage",
                    )
                )
            if not out:
                raise ProviderError(
                    ProviderErrorCode.DATA_UNAVAILABLE,
                    f"no commodity candles for {instrument.symbol}",
                )
            return out
        if instrument.asset_class == AssetClass.FOREX:
            base, quote = instrument.symbol.split("/")
            try:
                body = self._get(
                    {
                        "function": "FX_INTRADAY",
                        "from_symbol": base,
                        "to_symbol": quote,
                        "interval": tf,
                        "outputsize": "compact",
                    }
                )
            except ProviderError:
                raise
            series = (
                body.get("Time Series FX (5min)")
                or body.get(f"Time Series FX ({tf.upper()})")
                or {}
            )
            out = []
            for ts_key in sorted(series.keys()):
                row = series[ts_key]
                ts = _parse_av_ts(ts_key)
                if start and ts < start:
                    continue
                if end and ts > end:
                    continue
                try:
                    out.append(
                        candle_dict(
                            instrument.symbol,
                            timeframe,
                            ts,
                            float(row["1. open"]),
                            float(row["2. high"]),
                            float(row["3. low"]),
                            float(row["4. close"]),
                            float(row.get("5. volume") or 0.0),
                            "alpha_vantage",
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    raise ProviderError(
                        ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed alpha vantage candle"
                    ) from None
            if not out:
                raise ProviderError(
                    ProviderErrorCode.DATA_UNAVAILABLE, f"no fx candles for {instrument.symbol}"
                )
            return out
        raise ProviderError(
            ProviderErrorCode.DATA_UNAVAILABLE, "alpha vantage: no candles for this asset"
        )


def _parse_av_ts(ts: str) -> float:
    """'2026-08-16 12:30:00' or '2026-08-16' -> epoch seconds (UTC)."""
    s = ts.strip()
    if len(s) == 10:
        return time.mktime(time.strptime(s, "%Y-%m-%d"))
    if "." in s:
        s = s.split(".")[0]
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
