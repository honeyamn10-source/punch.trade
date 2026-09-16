"""Market data router — asset class -> provider priority -> fallback.

Responsibilities:

- pick the primary provider for an asset class (configurable priority)
- fail over to a compatible fallback on OFFLINE / RATE_LIMITED states
- keep every dataset single-provider (never silently mixes sources;
  a fallback result is explicitly labelled ``fallbackUsed``)
- expose sanitized provider status for the dashboard
"""

from __future__ import annotations

import threading
import time

from .instruments import AssetClass, Instrument, parse_instrument
from .market import TIMEFRAMES
from .providers import build_providers
from .providers.base import (
    HealthState,
    MarketDataProvider,
    ProviderError,
    ProviderErrorCode,
)

# default routing: asset class -> priority list of provider ids
DEFAULT_ROUTES: dict[AssetClass, list[str]] = {
    AssetClass.CRYPTO: ["binance", "coingecko"],
    AssetClass.EQUITY: ["dhan", "alpaca", "upstox", "angel", "twelve_data", "alpha_vantage"],
    AssetClass.ETF: ["alpaca", "twelve_data"],
    AssetClass.INDEX: ["dhan", "upstox", "angel", "alpaca"],
    AssetClass.FUTURE: ["dhan", "upstox", "angel"],
    AssetClass.OPTION: ["dhan", "upstox", "angel"],
    AssetClass.FOREX: ["twelve_data", "alpha_vantage"],
    AssetClass.COMMODITY: ["dhan", "alpha_vantage"],
}

# provider override via env: PUNCH_MARKET_ROUTE_CRYPTO="binance,coingecko"
_ENV_PREFIX = "PUNCH_MARKET_ROUTE_"


def _env_routes() -> dict[AssetClass, list[str]]:
    import os

    out: dict[AssetClass, list[str]] = {}
    for asset in AssetClass:
        raw = os.environ.get(f"{_ENV_PREFIX}{asset.value}", "").strip()
        if raw:
            out[asset] = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return out


class ProviderSwitchLog:
    """Append-only in-memory log of provider switches (never silent)."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def record(self, symbol: str, old: str, new: str, reason: str) -> None:
        with self._lock:
            self._rows.append(
                {
                    "event": "MARKET_DATA_PROVIDER_CHANGED",
                    "symbol": symbol,
                    "oldProvider": old,
                    "newProvider": new,
                    "reason": reason,
                    "ts": time.time(),
                }
            )
            self._rows = self._rows[-50:]

    def recent(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._rows))


class MarketRouter:
    """Routes instrument requests onto providers with failover."""

    def __init__(self, providers: dict[str, MarketDataProvider] | None = None):
        self.providers = providers if providers is not None else build_providers()
        self.routes = DEFAULT_ROUTES
        self.routes.update(_env_routes())
        self.switch_log = ProviderSwitchLog()
        self._last_tried: dict[str, str] = {}

    # ------------------------------------------------------------- pick --
    def _candidates(self, asset_class: AssetClass) -> list[MarketDataProvider]:
        """Priority list of enabled providers for an asset class."""
        out = []
        for pid in self.routes.get(asset_class, []):
            p = self.providers.get(pid)
            if p is not None and p.state != HealthState.DISABLED:
                out.append(p)
        return out

    def _pick(
        self, asset_class: AssetClass, allow_fallback: bool = True
    ) -> tuple[MarketDataProvider | None, bool]:
        """(provider, usedFallback)."""
        cands = self._candidates(asset_class)
        if not cands:
            return None, False
        primary = cands[0]
        if primary.state in (HealthState.READY, HealthState.DEGRADED):
            return primary, False
        if allow_fallback:
            for p in cands[1:]:
                if p.state not in (HealthState.DISABLED, HealthState.OFFLINE):
                    return p, True
        return primary, False

    # --------------------------------------------------------- resolve --
    def resolve_instrument(self, symbol: str) -> Instrument:
        return parse_instrument(symbol)

    def search_instruments(self, query: str, limit: int = 20) -> dict:
        """Search across providers, normalized + deduped.

        Unconfigured / auth-required providers are skipped (their searches
        would need credentials or heavy instrument-master downloads).
        """
        skip = (
            HealthState.AUTH_REQUIRED,
            HealthState.AUTH_FAILED,
            HealthState.NOT_CONFIGURED,
            HealthState.DISABLED,
        )
        seen: dict[str, dict] = {}
        errors: list[dict] = []
        for asset in AssetClass:
            for p in self._candidates(asset):
                if p.state in skip:
                    continue
                try:
                    for inst in p.search_instruments(query, limit):
                        seen.setdefault(inst.get("symbol", inst.get("provider_symbol", "")), inst)
                except ProviderError as e:
                    errors.append({"provider": p.provider_id, **e.to_dict()})
        rows = list(seen.values())[:limit]
        return {"results": rows, "count": len(rows), "errors": errors}

    # ----------------------------------------------------------- quote --
    def get_quote(self, symbol: str) -> dict:
        instrument = self.resolve_instrument(symbol)
        provider, used_fallback = self._pick(instrument.asset_class)
        if provider is None:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OFFLINE,
                f"no provider available for {instrument.asset_class.value}",
            )
        prev = self._last_tried.get(symbol)
        primary = (
            self._candidates(instrument.asset_class)[0]
            if self._candidates(instrument.asset_class)
            else None
        )
        if used_fallback and primary and primary.provider_id != provider.provider_id:
            self.switch_log.record(symbol, primary.provider_id, provider.provider_id, "failover")
        elif prev and prev != provider.provider_id:
            self.switch_log.record(symbol, prev, provider.provider_id, "failover")
        self._last_tried[symbol] = provider.provider_id
        quote = provider.get_quote(instrument)
        quote["provider"] = provider.provider_id
        quote["fallbackUsed"] = used_fallback
        quote["feed"] = provider.feed_label or None
        return quote

    # --------------------------------------------------------- candles --
    def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        *,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = None,
    ) -> dict:
        """Candles from a single provider (never mixed sources)."""
        if timeframe not in TIMEFRAMES:
            raise ProviderError(
                ProviderErrorCode.TIMEFRAME_UNSUPPORTED, f"unsupported timeframe {timeframe}"
            )
        instrument = self.resolve_instrument(symbol)
        provider, used_fallback = self._pick(instrument.asset_class)
        if provider is None:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OFFLINE,
                f"no provider available for {instrument.asset_class.value}",
            )
        last_err: ProviderError | None = None
        for p in [provider] + [
            x for x in self._candidates(instrument.asset_class) if x is not provider
        ]:
            try:
                bars = p.get_candles(instrument, timeframe, start=start, end=end, limit=limit)
                if not bars:
                    raise ProviderError(
                        ProviderErrorCode.DATA_UNAVAILABLE, f"{p.provider_id}: empty result"
                    )
                self._last_tried[symbol] = p.provider_id
                return {
                    "symbol": symbol,
                    "assetClass": instrument.asset_class.value,
                    "provider": p.provider_id,
                    "feed": p.feed_label or None,
                    "fallbackUsed": p is not provider,
                    "timeframe": timeframe,
                    "bars": bars,
                }
            except ProviderError as e:
                last_err = e
                self.switch_log.record(symbol, p.provider_id, "?", f"{e.code.value}")
                continue
        raise ProviderError(
            last_err.code if last_err else ProviderErrorCode.DATA_UNAVAILABLE,
            f"all providers failed for {symbol}: {last_err}",
        )

    # ----------------------------------------------------------- meta --
    def provider_status(self) -> dict:
        return {pid: p.health() for pid, p in self.providers.items()}

    def capabilities(self) -> dict:
        return {pid: p.capabilities() for pid, p in self.providers.items()}

    def routes_table(self) -> dict:
        return {a.value: self.routes[a] for a in AssetClass}
