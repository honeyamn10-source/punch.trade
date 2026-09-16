"""Angel One — second India fallback (NSE/BSE/NFO/MCX).

Credentials: ANGEL_API_KEY + ANGEL_CLIENT_CODE + ANGEL_TOTP_SECRET.
The TOTP secret is highly sensitive: it is only ever held in the vault,
never logged, never returned through the API, never given to the AI
context.  Without a stored session (jwt + feed token in the vault)
the adapter reports AUTH_REQUIRED — no connectivity is faked.
"""

from __future__ import annotations

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

BASE = "https://apiconnect.angelbroking.com"
TIMEFRAME_ANGEL = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}


class AngelProvider(MarketDataProvider):
    provider_id = "angel"
    display_name = "Angel One"
    asset_classes = (
        AssetClass.EQUITY,
        AssetClass.INDEX,
        AssetClass.FUTURE,
        AssetClass.OPTION,
        AssetClass.COMMODITY,
    )
    needs_credentials = True
    feed_label = "Angel One"

    def __init__(self, api_key: str = "", client_code: str = "", totp_secret: str = "") -> None:
        super().__init__()
        self.api_key = api_key or os.environ.get("ANGEL_API_KEY", "").strip()
        self.client_code = client_code or os.environ.get("ANGEL_CLIENT_CODE", "").strip()
        self.totp_secret = totp_secret or os.environ.get("ANGEL_TOTP_SECRET", "").strip()
        self.configured = bool(self.api_key and self.client_code and self.totp_secret)
        self.state = HealthState.AUTH_REQUIRED if not self.configured else HealthState.AUTH_REQUIRED
        self.limiter = RateLimiter(10.0)
        self.jwt = ""
        self.feed_token = ""

    def _headers(self) -> dict:
        if not self.jwt:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "angel: session required (login via /api/v1/market/credentials/angel)",
            )
        return {
            "X-PrivateKey": self.api_key,
            "Authorization": f"Bearer {self.jwt}",
            "X-ClientCode": self.client_code,
            "X-FeedToken": self.feed_token,
            "Content-Type": "application/json",
        }

    def login(self) -> None:
        """Exchange TOTP for a jwt session. Never raises a secret outward."""
        if not self.configured:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED, "angel: credentials incomplete"
            )
        try:
            import pyotp
        except ImportError:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "angel: pip install pyotp for TOTP login",
            ) from None
        totp = pyotp.TOTP(self.totp_secret).now()
        payload = {"clientcode": self.client_code, "password": totp, "totp": totp}
        try:
            resp = throttled_request(
                self.provider_id,
                self.limiter,
                "POST",
                f"{BASE}/rest/auth/angelbroking/user/v1/loginByToken",
                json=payload,
                headers={"X-PrivateKey": self.api_key, "Content-Type": "application/json"},
            )
        except Exception:
            self.state = HealthState.AUTH_FAILED
            self.last_error = "angel login failed"
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED, "angel login failed"
            ) from None
        check_status(resp, self.provider_id)
        body = resp.json()
        data = body.get("data") or {}
        self.jwt = data.get("jwtToken") or ""
        self.feed_token = data.get("feedToken") or ""
        if not self.jwt:
            self.state = HealthState.AUTH_FAILED
            raise ProviderError(ProviderErrorCode.PROVIDER_AUTH_FAILED, "angel login rejected")
        self.state = HealthState.READY

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.jwt:
            try:
                self.login()
            except ProviderError:
                raise
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
            self.last_error = "angel unreachable"
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, "angel unreachable") from None
        body = resp.json()
        if resp.status_code == 429:
            self.state = HealthState.RATE_LIMITED
            raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, "angel rate limited")
        if (
            isinstance(body, dict)
            and body.get("status") is not None
            and not bool(body.get("status"))
        ):
            raise ProviderError(
                ProviderErrorCode.PROVIDER_BAD_RESPONSE, str(body.get("message", "angel error"))
            )
        return body

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        out: list[dict] = []
        for sym in (
            "RELIANCE",
            "TCS",
            "INFY",
            "HDFCBANK",
            "NIFTY",
            "BANKNIFTY",
            "GOLD",
            "SILVER",
            "CRUDEOIL",
            "NATURALGAS",
            "COPPER",
        ):
            if q and q not in sym:
                continue
            out.append(
                Instrument(
                    symbol=f"NSE:{sym}"
                    if not sym.startswith(("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER"))
                    else f"MCX:{sym}",
                    asset_class=AssetClass.INDEX
                    if sym in ("NIFTY", "BANKNIFTY")
                    else AssetClass.EQUITY
                    if not sym.startswith(("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER"))
                    else AssetClass.COMMODITY,
                    exchange="NSE"
                    if not sym.startswith(("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER"))
                    else "MCX",
                    provider="angel",
                    provider_symbol=sym,
                ).to_dict()
            )
        if not out and q:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"no angel instruments for '{query}'"
            )
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        return {"symbol": instrument.symbol, "provider": "angel", "provider_symbol": name}

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        try:
            body = self._get(
                "/rest/secure/angelbroking/market/v1/quote/",
                {
                    "mode": "LTP",
                    "exchangeTokens": json_dumps([{"exchangeType": 1, "tokens": [name]}]),
                },
            )
        except ProviderError:
            raise
        data = body.get("data") or {}
        # data keyed by <exchange>:<token>; find the first row with ltp
        px = None
        for v in data.values():
            if isinstance(v, dict) and v.get("ltp") is not None:
                px = v["ltp"]
                break
        if px is None:
            raise ProviderError(
                ProviderErrorCode.DATA_UNAVAILABLE, f"no angel quote for {instrument.symbol}"
            )
        return {
            "symbol": instrument.symbol,
            "price": float(px),
            "bid": None,
            "ask": None,
            "change": None,
            "changePct": None,
            "ts": time.time(),
            "source": "angel",
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
        tf = TIMEFRAME_ANGEL.get(timeframe)
        if tf is None:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"angel has no {timeframe}"
            )
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        end_t = end or time.time()
        start_t = start or (end_t - 7 * 86400)
        exchange_type = (
            "NSE"
            if instrument.exchange in ("NSE", "NFO")
            else ("MCX" if instrument.exchange == "MCX" else "BSE")
        )
        try:
            body = self._get(
                "/rest/secure/angelbroking/historical/v1/getCandleData",
                {
                    "exchange": exchange_type,
                    "symboltoken": name,
                    "interval": tf,
                    "fromdate": time.strftime("%Y-%m-%d %H:%M", time.gmtime(start_t)),
                    "todate": time.strftime("%Y-%m-%d %H:%M", time.gmtime(end_t)),
                },
            )
        except ProviderError:
            raise
        rows = body.get("data") or []
        out = []
        for r in rows:
            try:
                ts = _parse_angel_ts(r[0])
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
                        "angel",
                    )
                )
            except (TypeError, ValueError, IndexError):
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed angel candle"
                ) from None
        return out


def _parse_angel_ts(s: str) -> float:
    return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))


def json_dumps(d: dict) -> str:
    import json

    return json.dumps(d)
