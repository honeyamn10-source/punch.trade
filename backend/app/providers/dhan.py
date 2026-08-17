"""DhanHQ — primary India market data (NSE / BSE / NFO / BFO / MCX).

Credentials: DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN (env or vault).
Without credentials the adapter is honest: AUTH_REQUIRED, no fake data.

Instrument mapping: Dhan uses security ids + exchange segments; the
adapter caches the official instrument master (public CSV) and resolves
canonical symbols (NSE:RELIANCE, NFO:NIFTY-..., MCX:GOLD-...) to it.
"""

from __future__ import annotations

import csv
import io
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

BASE = "https://api.dhan.co/v2"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SEGMENT_MAP = {
    "NSE": ("NSE_EQ", "NSE_EQ"),
    "BSE": ("BSE_EQ", "BSE_EQ"),
    "NFO": ("NSE_FNO", "NFO_FUT"),
    "BFO": ("BSE_FNO", "BFO_FUT"),
    "MCX": ("MCX_COMM", "MCX_FUT"),
}
TIMEFRAME_DHAN = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "D"}


class DhanProvider(MarketDataProvider):
    provider_id = "dhan"
    display_name = "Dhan"
    asset_classes = (
        AssetClass.EQUITY,
        AssetClass.INDEX,
        AssetClass.FUTURE,
        AssetClass.OPTION,
        AssetClass.COMMODITY,
    )
    needs_credentials = True
    feed_label = "Dhan"

    def __init__(self, client_id: str = "", access_token: str = "") -> None:
        super().__init__()
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID", "").strip()
        self.access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
        self.configured = bool(self.client_id and self.access_token)
        self.state = HealthState.AUTH_REQUIRED if not self.configured else HealthState.READY
        self.limiter = RateLimiter(10.0)
        self._master: list[dict] | None = None
        self._master_ts: float = 0.0

    def _headers(self) -> dict:
        if not self.configured:
            self.state = HealthState.AUTH_REQUIRED
            raise ProviderError(
                ProviderErrorCode.PROVIDER_AUTH_FAILED,
                "dhan: set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN",
            )
        return {"access_token": self.access_token, "client-id": self.client_id}

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
            self.last_error = "dhan unreachable"
            raise ProviderError(ProviderErrorCode.PROVIDER_OFFLINE, "dhan unreachable") from None
        check_status(resp, self.provider_id)
        body = resp.json()
        if resp.status_code == 429 or (isinstance(body, dict) and body.get("status") == "429"):
            self.state = HealthState.RATE_LIMITED
            self.last_error = "dhan rate limited"
            raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, "dhan rate limited")
        return body

    # ---------------------------------------------------------- master ----
    def _load_master(self) -> list[dict]:
        now = time.time()
        if self._master is not None and now - self._master_ts < 86400:
            return self._master
        try:
            resp = throttled_request(self.provider_id, self.limiter, "GET", MASTER_URL, timeout=30)
            rows = []
            for row in csv.DictReader(io.StringIO(resp.text)):
                rows.append(
                    {
                        "sem_exch": (row.get("SEM_EXM_EXCH_ID") or "").upper(),
                        "sem_seg": (row.get("SEM_SEGMENT") or "").upper(),
                        "name": (row.get("SEM_TRADING_SYMBOL") or "").upper(),
                        "security_id": row.get("SEM_SMST_SECURITY_ID", ""),
                        "instrument_token": row.get("SEM_INSTRUMENT_TOKEN", ""),
                        "lot": row.get("SEM_LOT_SIZE", ""),
                        "expiry": row.get("SEM_EXPIRY_DATE", ""),
                        "strike": row.get("SEM_STRIKE_PRICE", ""),
                    }
                )
            self._master = rows
            self._master_ts = now
        except Exception as e:
            self.last_error = f"dhan master: {e}"
            self._master = []
        return self._master

    def _resolve(self, instrument: Instrument) -> dict | None:
        """Map a canonical instrument onto the Dhan master row (best effort)."""
        base = instrument.underlying or instrument.symbol.split(":")[-1]
        for row in self._load_master():
            if row["name"] == base and row["sem_exch"] == instrument.exchange:
                if instrument.asset_class == AssetClass.FUTURE and row["sem_seg"] != "FUT":
                    continue
                if instrument.asset_class == AssetClass.OPTION and row["sem_seg"] != "OPT":
                    continue
                return row
        return None

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        out: list[dict] = []
        for row in self._load_master():
            if q and q not in row["name"]:
                continue
            out.append(
                Instrument(
                    symbol=f"{row['sem_exch']}:{row['name']}",
                    asset_class=AssetClass.FUTURE if row["sem_seg"] == "FUT" else AssetClass.EQUITY,
                    exchange=row["sem_exch"],
                    provider="dhan",
                    provider_instrument_id=row["security_id"],
                    provider_symbol=row["name"],
                    lot_size=_int(row["lot"]),
                ).to_dict()
            )
            if len(out) >= limit:
                break
        if not out and q:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"no dhan instruments for '{query}'"
            )
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        row = self._resolve(instrument)
        if row is None:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"dhan has no {instrument.symbol}"
            )
        return {
            "symbol": instrument.symbol,
            "provider": "dhan",
            "provider_instrument_id": row["security_id"],
            "provider_symbol": row["name"],
            "lot_size": _int(row["lot"]),
            "expiry": row["expiry"] or None,
            "strike": _f(row["strike"]),
        }

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        seg = SEGMENT_MAP.get(instrument.exchange)
        if seg is None:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"dhan: no segment for {instrument.symbol}"
            )
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        try:
            body = self._get("/marketfeed/quote", {"symbol": name, "exchange_segment": seg[0]})
        except ProviderError:
            raise
        data = body.get("data") or {}
        px = data.get("LTP") or data.get("last_price")
        if px is None:
            raise ProviderError(
                ProviderErrorCode.DATA_UNAVAILABLE, f"no dhan quote for {instrument.symbol}"
            )
        return {
            "symbol": instrument.symbol,
            "price": float(px),
            "bid": _f(data.get("BID") or data.get("bid")),
            "ask": _f(data.get("ASK") or data.get("ask")),
            "change": None,
            "changePct": None,
            "ts": time.time(),
            "source": "dhan",
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
        seg = SEGMENT_MAP.get(instrument.exchange)
        tf = TIMEFRAME_DHAN.get(timeframe)
        if seg is None:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"dhan: no segment for {instrument.symbol}"
            )
        if tf is None:
            raise ProviderError(ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"dhan has no {timeframe}")
        name = instrument.underlying or instrument.symbol.split(":")[-1]
        end_t = end or time.time()
        start_t = start or (end_t - 7 * 86400)
        try:
            body = self._get(
                "/charts/historical",
                {
                    "symbol": name,
                    "exchange_segment": seg[0],
                    "instrument_token": instrument.provider_instrument_id or "",
                    "interval": tf,
                    "start_date": time.strftime("%Y-%m-%d", time.gmtime(start_t)),
                    "end_date": time.strftime("%Y-%m-%d", time.gmtime(end_t)),
                },
            )
        except ProviderError:
            raise
        rows = body.get("data") or []
        if not rows and body.get("security_id"):
            rows = body.get("security_id") if isinstance(body.get("security_id"), list) else []
        if body.get("error") or body.get("message"):
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND,
                f"dhan: {body.get('error') or body.get('message')} for {instrument.symbol}",
            )
        out = []
        for r in rows:
            try:
                ts = _parse_dhan_ts(r.get("start_time") or r.get("time"))
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
                        float(r["open"]),
                        float(r["high"]),
                        float(r["low"]),
                        float(r["close"]),
                        float(r.get("volume") or 0.0),
                        "dhan",
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_BAD_RESPONSE, "malformed dhan candle"
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


def _parse_dhan_ts(s: str) -> float:
    return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
