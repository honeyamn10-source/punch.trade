"""Alpaca — US equities + ETFs (free plan = IEX feed).

Free plan market data comes from the IEX feed — the dashboard labels
charts ``ALPACA / IEX`` and never claims a consolidated US SIP.

Credentials: ALPACA_API_KEY / ALPACA_API_SECRET (read-only recommended).
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

BASE = "https://data.alpaca.markets/v2"
TIMEFRAME_ALPACA = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "4h": "4Hour",
    "1d": "1Day",
}


class AlpacaProvider(MarketDataProvider):
    provider_id = "alpaca"
    display_name = "Alpaca"
    asset_classes = (AssetClass.EQUITY, AssetClass.ETF, AssetClass.INDEX)
    needs_credentials = True
    feed_label = "IEX (free plan)"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = os.environ.get("ALPACA_API_KEY", "").strip()
        self.api_secret = os.environ.get("ALPACA_API_SECRET", "").strip()
        self.configured = bool(self.api_key and self.api_secret)
        self.state = HealthState.AUTH_REQUIRED if not self.configured else HealthState.READY
        self.limiter = RateLimiter(15.0)

    def _headers(self) -> dict:
        if not self.configured:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "alpaca: set ALPACA_API_KEY and ALPACA_API_SECRET",
            )
        return {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret}

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = throttled_request(
                self.provider_id,
                self.limiter,
                "GET",
                f"{BASE}{path}",
                params=params,
                headers=self._headers(),
            )
        except ProviderError:
            raise
        except Exception:
            self.state = HealthState.OFFLINE
            self.last_error = "alpaca unreachable"
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, "alpaca unreachable") from None
        check_status(resp, self.provider_id)
        body = resp.json()
        if isinstance(body, dict) and body.get("code"):
            self.state = HealthState.AUTH_FAILED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                str(body.get("message", "alpaca auth failed")),
            )
        return body

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        if not self.configured:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "alpaca: set ALPACA_API_KEY and ALPACA_API_SECRET",
            )
        try:
            body = self._get("/stocks", {"search": q, "limit": limit, "status": "active"})
        except ProviderError:
            raise
        out: list[dict] = []
        for a in (body.get("stocks") or [])[:limit]:
            sym = a.get("symbol", "")
            out.append(
                Instrument(
                    symbol=sym,
                    asset_class=AssetClass.ETF if a.get("exchange") == "ETF" else AssetClass.EQUITY,
                    exchange="US",
                    currency="USD",
                    provider="alpaca",
                    provider_symbol=sym,
                ).to_dict()
            )
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        sym = provider_symbol_for("alpaca", instrument)
        if not self.configured:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED, "alpaca: credentials required"
            )
        return {"symbol": instrument.symbol, "provider": "alpaca", "provider_symbol": sym}

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        sym = provider_symbol_for("alpaca", instrument)
        try:
            body = self._get(f"/stocks/{sym}/quotes/latest", {"feed": "iex"})
        except ProviderError:
            raise
        q = body.get("quote") or body or {}
        px = q.get("ap") or q.get("bp")
        if px is None:
            raise ProviderError(ProviderErrorCode.DATA_UNAVAILABLE, f"no alpaca quote for {sym}")
        return {
            "symbol": instrument.symbol,
            "price": float(px),
            "bid": _f(q.get("bp")),
            "ask": _f(q.get("ap")),
            "change": None,
            "changePct": None,
            "ts": (q.get("t") or time.time() * 1000) / 1000,
            "source": "alpaca",
            "feed": "iex",
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
        sym = provider_symbol_for("alpaca", instrument)
        tf = TIMEFRAME_ALPACA.get(timeframe)
        if tf is None:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"alpaca has no {timeframe}"
            )
        params = {"timeframe": tf, "feed": "iex", "limit": min(limit or 300, 1000)}
        if start:
            params["start"] = _iso(start)
        if end:
            params["end"] = _iso(end)
        try:
            body = self._get(f"/stocks/{sym}/bars", params)
        except ProviderError:
            raise
        out = []
        for b in body.get("bars") or []:
            try:
                ts = _parse_iso(b.get("t", ""))
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
                        float(b["o"]),
                        float(b["h"]),
                        float(b["l"]),
                        float(b["c"]),
                        float(b.get("v") or 0.0),
                        "alpaca",
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed alpaca candle"
                ) from None
        return out


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _parse_iso(s: str) -> float:
    return time.mktime(time.strptime(s.split(".")[0], "%Y-%m-%dT%H:%M:%SZ"))
