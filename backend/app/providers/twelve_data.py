"""Twelve Data — forex primary, US + global backup.

Free tier: 800 credits/day with a personal key.  Twelve Data officially
publishes a public ``demo`` key (heavily rate-limited); it is used only
when no key is configured, and the dashboard labels the feed accordingly.

Symbols: EUR/USD stays ``EUR/USD``; equities pass through unchanged.
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

BASE = "https://api.twelvedata.com"
DEMO_KEY = "demo"


class TwelveDataProvider(MarketDataProvider):
    provider_id = "twelve_data"
    display_name = "Twelve Data"
    asset_classes = (AssetClass.FOREX, AssetClass.EQUITY, AssetClass.ETF, AssetClass.CRYPTO)
    feed_label = "Twelve Data free"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
        if not self.api_key:
            self.api_key = DEMO_KEY
            self.configured = False
            self.state = HealthState.DEGRADED
        self.limiter = RateLimiter(8.0)

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "apikey": self.api_key}
        try:
            resp = throttled_request(
                self.provider_id, self.limiter, "GET", f"{BASE}{path}", params=params
            )
        except Exception:
            self.state = HealthState.OFFLINE
            self.last_error = "twelve data unreachable"
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OFFLINE, "twelve data unreachable"
            ) from None
        check_status(resp, self.provider_id)
        body = resp.json()
        if resp.status_code == 429 or (
            isinstance(body, dict)
            and body.get("status") == "error"
            and "limit" in str(body.get("message", "")).lower()
        ):
            self.state = HealthState.RATE_LIMITED
            self.last_error = "twelve data rate limited"
            raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, "twelve data rate limited")
        if isinstance(body, dict) and body.get("status") == "error":
            raise ProviderError(
                ProviderErrorCode.PROVIDER_BAD_RESPONSE,
                str(body.get("message", "twelve data error")),
            )
        return body

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        try:
            body = self._get("/symbol_search", {"symbol": q})
        except ProviderError:
            raise
        out: list[dict] = []
        for row in (body.get("data") or [])[:limit]:
            sym = row.get("symbol", "")
            inst = Instrument(
                symbol=sym,
                asset_class=AssetClass.FOREX if "/" in sym else AssetClass.EQUITY,
                exchange=row.get("exchange", ""),
                provider="twelve_data",
                provider_symbol=sym,
            )
            out.append(inst.to_dict())
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        return {
            "symbol": instrument.symbol,
            "provider": "twelve_data",
            "provider_symbol": provider_symbol_for("twelve_data", instrument),
        }

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        sym = provider_symbol_for("twelve_data", instrument)
        try:
            body = self._get("/quote", {"symbol": sym})
        except ProviderError:
            raise
        if isinstance(body, dict) and body.get("status") == "error":
            raise ProviderError(
                ProviderErrorCode.PROVIDER_BAD_RESPONSE, str(body.get("message", "quote failed"))
            )
        try:
            price = float(body.get("close") or body.get("price"))
        except (TypeError, ValueError):
            raise ProviderError(ProviderErrorCode.DATA_UNAVAILABLE, f"no quote for {sym}") from None
        return {
            "symbol": instrument.symbol,
            "price": price,
            "bid": None,
            "ask": None,
            "change": _f(body.get("change")),
            "changePct": _f(body.get("percent_change")),
            "ts": time.time(),
            "source": "twelve_data",
        }

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
        sym = provider_symbol_for("twelve_data", instrument)
        tf = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1day",
        }.get(timeframe)
        if tf is None:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"twelve data has no {timeframe}"
            )
        try:
            body = self._get(
                "/time_series",
                {"symbol": sym, "interval": tf, "outputsize": min(limit or 120, 800)},
            )
        except ProviderError:
            raise
        rows = body.get("values") or []
        out = []
        for row in rows:
            try:
                ts = _parse_ts(row.get("datetime"))
            except (TypeError, ValueError):
                continue
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
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row.get("volume") or 0.0),
                        "twelve_data",
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed twelve data candle"
                ) from None
        return out


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(dt: str) -> float:
    """'2026-08-16 12:30:00' (or ISO) -> epoch seconds (UTC)."""
    if dt.endswith("Z"):
        dt = dt[:-1]
    if "T" in dt:
        dt = dt.replace("T", " ")
    if "." in dt:
        dt = dt.split(".")[0]
    return time.mktime(time.strptime(dt, "%Y-%m-%d %H:%M:%S"))
