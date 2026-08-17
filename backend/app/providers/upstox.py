"""Upstox — India fallback data provider (NSE/BSE/NFO/BFO/MCX quotes + history).

Credentials: UPSTOX_CLIENT_ID + UPSTOX_CLIENT_SECRET + UPSTOX_ACCESS_TOKEN.
Without them: AUTH_REQUIRED.  Instrument keys come from the official public
instrument master (gzip JSON); resolved and cached locally.
"""

from __future__ import annotations

import gzip
import json
import os
import time

from ..instruments import AssetClass, Instrument
from ..rate_limit import RateLimiter, throttled_request
from .base import (
    HealthState,
    MarketDataProvider,
    ProviderError,
    ProviderErrorCode,
    candle_dict,
    check_status,
)

BASE = "https://api.upstox.com/v2"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
TIMEFRAME_UPSTOX = {
    "1m": "1minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "1hour",
    "1d": "day",
}


class UpstoxProvider(MarketDataProvider):
    provider_id = "upstox"
    display_name = "Upstox"
    asset_classes = (
        AssetClass.EQUITY,
        AssetClass.INDEX,
        AssetClass.FUTURE,
        AssetClass.OPTION,
        AssetClass.COMMODITY,
    )
    needs_credentials = True
    feed_label = "Upstox"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        access_token: str = "",
    ) -> None:
        super().__init__()
        self.client_id = client_id or os.environ.get("UPSTOX_CLIENT_ID", "").strip()
        self.client_secret = client_secret or os.environ.get("UPSTOX_CLIENT_SECRET", "").strip()
        self.access_token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
        self.configured = bool(self.access_token and self.client_id)
        self.state = HealthState.AUTH_REQUIRED if not self.configured else HealthState.READY
        self.limiter = RateLimiter(10.0)
        self._master: dict[str, dict] = {}
        self._master_ts: float = 0.0

    def _headers(self) -> dict:
        if not self.configured:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "upstox: set UPSTOX_CLIENT_ID and UPSTOX_ACCESS_TOKEN",
            )
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

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
            self.last_error = "upstox unreachable"
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, "upstox unreachable") from None
        check_status(resp, self.provider_id)
        body = resp.json()
        if resp.status_code == 429:
            self.state = HealthState.RATE_LIMITED
            raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, "upstox rate limited")
        if isinstance(body, dict) and body.get("errors"):
            msg = (
                body["errors"][0].get("message", "upstox error")
                if body["errors"]
                else "upstox error"
            )
            self.state = HealthState.AUTH_FAILED
            raise ProviderError(ProviderErrorCode.PROVIDER_AUTH_FAILED, str(msg))
        return body

    # ---------------------------------------------------------- master ----
    def _load_master(self, exchange: str) -> dict[str, dict]:
        now = time.time()
        key = f"master_{exchange}"
        if key in self._master and now - self._master_ts < 86400:
            return self._master[key]
        try:
            resp = throttled_request(
                self.provider_id,
                self.limiter,
                "GET",
                MASTER_URL.format(exchange=exchange),
                timeout=30,
            )
            rows = json.loads(gzip.decompress(resp.content))
            self._master[key] = {r.get("trading_symbol", "").upper(): r for r in rows}
            self._master_ts = now
        except Exception as e:
            self.last_error = f"upstox master: {e}"
            self._master[key] = {}
        return self._master[key]

    def _instrument_key(self, instrument: Instrument) -> str | None:
        exch = (
            "NSE_EQ"
            if instrument.exchange == "NSE"
            else (
                "BSE_EQ"
                if instrument.exchange == "BSE"
                else (
                    "NSE_FO"
                    if instrument.exchange in ("NFO", "BFO")
                    else ("MCX_COMM" if instrument.exchange == "MCX" else "")
                )
            )
        )
        if not exch:
            return None
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        row = self._load_master(exch).get(name)
        return row.get("instrument_key") if row else None

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        out: list[dict] = []
        for exch in ("NSE_EQ", "BSE_EQ"):
            for name, row in self._load_master(exch).items():
                if q and q not in name:
                    continue
                out.append(
                    Instrument(
                        symbol=f"{'NSE' if exch == 'NSE_EQ' else 'BSE'}:{name}",
                        asset_class=AssetClass.EQUITY,
                        exchange=exch.split("_")[0],
                        provider="upstox",
                        provider_instrument_id=row.get("instrument_key", ""),
                        provider_symbol=name,
                        tick_size=_f(row.get("tick_size")),
                        lot_size=_int(row.get("lot_size")),
                    ).to_dict()
                )
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if not out and q:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"no upstox instruments for '{query}'"
            )
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        key = self._instrument_key(instrument)
        if not key:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"upstox has no {instrument.symbol}"
            )
        return {"symbol": instrument.symbol, "provider": "upstox", "provider_instrument_id": key}

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        key = self._instrument_key(instrument)
        if not key:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"upstox has no {instrument.symbol}"
            )
        try:
            body = self._get("/market-quote/quotes", {"instrument_key": key})
        except ProviderError:
            raise
        d = (body.get("data") or {}).get(key) or {}
        ltp = d.get("ltp")
        if ltp is None:
            raise ProviderError(
                ProviderErrorCode.DATA_UNAVAILABLE, f"no upstox quote for {instrument.symbol}"
            )
        return {
            "symbol": instrument.symbol,
            "price": float(ltp),
            "bid": _f(d.get("depth", {}).get("buy", [{}])[0].get("price"))
            if d.get("depth")
            else None,
            "ask": _f(d.get("depth", {}).get("sell", [{}])[0].get("price"))
            if d.get("depth")
            else None,
            "change": _f(d.get("net_change")),
            "changePct": _f(d.get("net_change_perc")),
            "ts": time.time(),
            "source": "upstox",
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
        key = self._instrument_key(instrument)
        tf = TIMEFRAME_UPSTOX.get(timeframe)
        if not key:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"upstox has no {instrument.symbol}"
            )
        if tf is None:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"upstox has no {timeframe}"
            )
        end_t = end or time.time()
        start_t = start or (end_t - 7 * 86400)
        try:
            body = self._get(
                f"/historical-candle/{key}/{tf}/{time.strftime('%Y-%m-%d', time.gmtime(end_t))}/{time.strftime('%Y-%m-%d', time.gmtime(start_t))}/ccxt",
            )
        except ProviderError:
            raise
        rows = body.get("data", {}).get("candles") or []
        out = []
        for r in rows:
            try:
                ts = _parse_upstox_ts(r[0])
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
                        float(r[1]),
                        float(r[2]),
                        float(r[3]),
                        float(r[4]),
                        float(r[5]),
                        "upstox",
                    )
                )
            except (TypeError, ValueError, IndexError):
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed upstox candle"
                ) from None
        return out


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_upstox_ts(s: str) -> float:
    return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
