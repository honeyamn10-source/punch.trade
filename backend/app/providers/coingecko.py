"""CoinGecko — crypto backup / token discovery / market metadata.

Keyless public REST API (heavily rate-limited — not an execution feed).

- ``get_candles`` returns a CLOSE-ONLY series: each candle is flattened to
  open=high=low=close (CoinGecko publishes price series, not OHLC for the
  free endpoint). Used only as an explicit fallback for charts/research,
  never for execution pricing.
"""

from __future__ import annotations

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

BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoProvider(MarketDataProvider):
    provider_id = "coingecko"
    display_name = "CoinGecko"
    asset_classes = (AssetClass.CRYPTO,)
    feed_label = "CoinGecko price series"

    def __init__(self) -> None:
        super().__init__()
        self.limiter = RateLimiter(12.0)
        self._coins: dict | None = None
        self._coins_ts: float = 0.0

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = throttled_request(
                self.provider_id,
                self.limiter,
                "GET",
                f"{BASE}{path}",
                params=params or {},
            )
        except Exception as e:
            self.state = HealthState.OFFLINE
            self.last_error = f"coingecko: {e}"
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OFFLINE, "coingecko unreachable"
            ) from None
        check_status(resp, self.provider_id)
        if resp.status_code == 429:
            self.state = HealthState.RATE_LIMITED
            self.last_error = "coingecko rate limited"
            raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, "coingecko rate limited")
        return resp.json()

    def _coin_list(self) -> dict:
        now = time.time()
        if self._coins is not None and now - self._coins_ts < 3600:
            return self._coins
        try:
            rows = self._get("/coins/list")
            self._coins = {c.get("symbol", "").upper(): c.get("id", "") for c in rows}
            self._coins_ts = now
            self.state = HealthState.READY
        except ProviderError:
            raise
        except Exception as e:
            self.last_error = f"coingecko list: {e}"
            self._coins = {}
        return self._coins

    def _coin_id(self, instrument: Instrument) -> str:
        return provider_symbol_for("coingecko", instrument)

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper()
        coins = self._coin_list()
        out: list[dict] = []
        for sym, cid in coins.items():
            if q and q not in sym:
                continue
            out.append(
                Instrument(
                    symbol=f"{sym}/USD",
                    asset_class=AssetClass.CRYPTO,
                    exchange="CRYPTO",
                    base_currency=sym,
                    quote_currency="USD",
                    currency="USD",
                    provider="coingecko",
                    provider_symbol=cid,
                    provider_instrument_id=cid,
                ).to_dict()
            )
            if len(out) >= limit:
                break
        if not out and q:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"no coingecko coins for '{query}'"
            )
        return out

    def get_instrument(self, instrument: Instrument) -> dict:
        cid = self._coin_id(instrument)
        if cid not in self._coin_list().values():
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"coingecko coin '{cid}' unknown"
            )
        return {
            "symbol": instrument.symbol,
            "provider": "coingecko",
            "provider_instrument_id": cid,
        }

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        cid = self._coin_id(instrument)
        try:
            data = self._get("/simple/price", {"ids": cid, "vs_currencies": "usd"})
        except ProviderError:
            raise
        price = (data.get(cid) or {}).get("usd")
        if price is None:
            raise ProviderError(
                ProviderErrorCode.SYMBOL_NOT_FOUND, f"coingecko has no price for {cid}"
            )
        return {
            "symbol": instrument.symbol,
            "price": float(price),
            "bid": None,
            "ask": None,
            "change": None,
            "changePct": None,
            "ts": time.time(),
            "source": "coingecko",
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
        """Close-only price series (CoinGecko free API publishes no OHLC)."""
        cid = self._coin_id(instrument)
        days = 1
        if start and end:
            days = max(1, int((end - start) // 86400) + 1)
        interval = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "1h": "hourly",
            "4h": "4h",
            "1d": "daily",
        }.get(timeframe, "hourly")
        try:
            data = self._get(
                f"/coins/{cid}/market_chart",
                {"vs_currency": "usd", "days": min(days, 90), "interval": interval},
            )
        except ProviderError:
            raise
        rows = data.get("prices") or []
        if not rows:
            raise ProviderError(
                ProviderErrorCode.DATA_UNAVAILABLE, f"no coingecko series for {cid}"
            )
        out = []
        for ms, px in rows:
            ts = ms / 1000
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            out.append(
                candle_dict(
                    instrument.symbol,
                    timeframe,
                    ts,
                    px,
                    px,
                    px,
                    px,
                    0.0,
                    "coingecko",
                    closed=True,
                )
            )
        return out[-(limit or len(out)) :]
