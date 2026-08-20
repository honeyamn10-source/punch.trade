"""Binance — primary crypto market data (public APIs, no account needed).

- OHLCV + ticker via CCXT public endpoints (free, keyless).
- Symbol form: internal BTC/USDT <-> provider BTCUSDT.
- Timeframes: the canonical set maps 1:1 onto Binance intervals.
"""

from __future__ import annotations

import time

from ..instruments import AssetClass, Instrument, provider_symbol_for
from ..market import TIMEFRAME_SECONDS
from .base import HealthState, MarketDataProvider, ProviderErrorCode, candle_dict

try:
    import ccxt
except ImportError:  # pragma: no cover
    ccxt = None


class BinanceProvider(MarketDataProvider):
    provider_id = "binance"
    display_name = "Binance"
    asset_classes = (AssetClass.CRYPTO,)
    feed_label = "Binance spot"

    def __init__(self) -> None:
        super().__init__()
        self._markets: dict | None = None
        self._markets_ts: float = 0.0
        self._funding_cache: dict[str, tuple[float, list[dict]]] = {}

    def _exchange(self):
        if ccxt is None:
            self._fail(ProviderErrorCode.PROVIDER_OFFLINE, "ccxt not installed (pip install ccxt)")
        try:
            return ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        except Exception as e:  # pragma: no cover
            self._fail(ProviderErrorCode.PROVIDER_OFFLINE, f"binance init failed: {e}")

    def _load_markets(self) -> dict:
        now = time.time()
        if self._markets is not None and now - self._markets_ts < 3600:
            return self._markets
        try:
            self._markets = self._exchange().load_markets()
            self._markets_ts = now
            self.state = HealthState.READY
        except Exception as e:
            self.last_error = f"binance markets: {e}"
            self.state = HealthState.OFFLINE
            return {}
        return self._markets

    # ------------------------------------------------------------ search --
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().upper().replace(" ", "")
        out: list[dict] = []
        for sym in self._load_markets():
            if sym.endswith(":USDT") or sym.endswith(":BUSD"):
                continue
            if q and q not in sym:
                continue
            if sym.endswith(("/USDT", "/BTC", "/ETH", "/USD")):
                out.append(self._instrument(sym).to_dict())
                if len(out) >= limit:
                    break
        if not out and q:
            self._fail(ProviderErrorCode.SYMBOL_NOT_FOUND, f"no binance symbols for '{query}'")
        return out

    def _instrument(self, provider_sym: str) -> Instrument:
        canon = provider_sym if "/" in provider_sym else provider_sym.replace("_", "/")
        inst = Instrument(
            symbol=canon,
            asset_class=AssetClass.CRYPTO,
            exchange="CRYPTO",
            base_currency=canon.split("/")[0],
            quote_currency=canon.split("/")[1],
            currency=canon.split("/")[1],
            provider="binance",
            provider_symbol=provider_sym,
        )
        m = self._markets.get(provider_sym) or {}
        if m:
            inst.tick_size = float(m.get("precision", {}).get("price") or 0) or None
            inst.min_quantity = m.get("limits", {}).get("amount", {}).get("min")
        return inst

    def get_instrument(self, instrument: Instrument) -> dict:
        p = provider_symbol_for("binance", instrument)
        if p not in self._load_markets():
            self._fail(ProviderErrorCode.SYMBOL_NOT_FOUND, f"binance has no symbol {p}")
        return self._instrument(p).to_dict()

    # ------------------------------------------------------------ quotes --
    def get_quote(self, instrument: Instrument) -> dict:
        p = provider_symbol_for("binance", instrument)
        try:
            t = self._exchange().fetch_ticker(p)
        except Exception as e:
            self._fail(ProviderErrorCode.PROVIDER_OFFLINE, f"binance ticker failed: {e}")
        return {
            "symbol": instrument.symbol,
            "price": t.get("last"),
            "bid": t.get("bid"),
            "ask": t.get("ask"),
            "change": t.get("percentage"),
            "changePct": t.get("percentage"),
            "ts": (t.get("timestamp") or time.time() * 1000) / 1000,
            "source": "binance",
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
        p = provider_symbol_for("binance", instrument)
        tf = timeframe if timeframe != "1d" else "1d"
        try:
            rows = self._exchange().fetch_ohlcv(p, tf, limit=limit or 300)
        except Exception as e:
            self._fail(ProviderErrorCode.PROVIDER_OFFLINE, f"binance OHLCV failed: {e}")
        funding = self._funding_for(p)
        out = []
        for r in rows:
            if r[0] / 1000 < (start or 0) or (end is not None and r[0] / 1000 > end):
                continue
            bar = candle_dict(
                instrument.symbol,
                timeframe,
                r[0] / 1000 + TIMEFRAME_SECONDS.get(timeframe, 60),
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                "binance",
            )
            f = self._funding_at(bar["open_time"], funding)
            if f is not None:
                bar["funding"] = f
            out.append(bar)
        return out

    # ------------------------------------------------------------ funding --
    def get_funding_history(self, instrument: Instrument, limit: int = 500) -> list[dict]:
        """Perpetual funding history from Binance FAPI (public, keyless).

        Returns [{ts, rate, symbol}] or [] when the swap feed is
        unavailable — funding is optional enrichment, never fatal.
        """
        if ccxt is None:
            return []
        p = provider_symbol_for("binance", instrument)
        try:
            ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            rows = ex.fetch_funding_rate_history(p, limit=limit)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"binance funding: {e}"
            return []
        return [
            {"ts": r.get("timestamp", 0) / 1000, "rate": r.get("fundingRate", 0.0), "symbol": p}
            for r in rows
        ]

    def _funding_for(self, provider_symbol: str) -> list[dict]:
        cached = self._funding_cache.get(provider_symbol)
        if cached and time.time() - cached[0] < 120:
            return cached[1]
        try:
            ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            rows = ex.fetch_funding_rate_history(provider_symbol, limit=500)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"binance funding: {e}"
            self._funding_cache[provider_symbol] = (time.time(), [])
            return []
        history = [
            {"ts": r.get("timestamp", 0) / 1000, "rate": r.get("fundingRate", 0.0)}
            for r in rows
            if r.get("fundingRate") is not None
        ]
        history.sort(key=lambda h: h["ts"])
        self._funding_cache[provider_symbol] = (time.time(), history)
        return history

    @staticmethod
    def _funding_at(open_time: float, funding: list[dict]) -> float | None:
        """Most recent funding rate settled at or before bar open."""
        best = None
        for f in funding:
            if f["ts"] <= open_time:
                best = f["rate"]
            else:
                break
        return best
