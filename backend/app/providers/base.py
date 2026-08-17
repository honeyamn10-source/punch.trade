"""Provider-neutral market data interface.

Every provider adapter (binance, coingecko, dhan, upstox, angel, alpaca,
twelve_data, alpha_vantage) implements ``MarketDataProvider`` and feeds
canonical ``Candle`` objects into the rest of the system.  The trading
engine never sees provider-specific formats.

Errors are normalized to ``ProviderError`` with a stable code; raw
provider responses are never surfaced to callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from ..instruments import AssetClass, Instrument
from ..rate_limit import RateLimiter


class ProviderErrorCode(StrEnum):
    PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    TIMEFRAME_UNSUPPORTED = "TIMEFRAME_UNSUPPORTED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class ProviderError(Exception):
    """Normalized provider failure. ``message`` is safe to expose."""

    def __init__(self, code: ProviderErrorCode, message: str):
        super().__init__(message)
        self.code = code

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": str(self)}


class HealthState(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class MarketDataProvider(ABC):
    """Common interface for all market data providers."""

    provider_id: str = "base"
    display_name: str = "Base"
    #: asset classes this provider can serve
    asset_classes: tuple[AssetClass, ...] = ()
    #: whether market data needs credentials (public providers: False)
    needs_credentials: bool = False
    #: feed label for honest display (e.g. "IEX" for free Alpaca)
    feed_label: str = ""

    def __init__(self) -> None:
        self.limiter = RateLimiter(self.default_cpm())
        self.last_error: str | None = None
        self.state: HealthState = HealthState.READY
        self.configured = not self.needs_credentials

    # ------------------------------------------------------------- meta --
    def default_cpm(self) -> float:
        return 30.0

    def capabilities(self) -> dict:
        return {
            "provider": self.provider_id,
            "assetClasses": [a.value for a in self.asset_classes],
            "historical": True,
            "quotes": True,
            "websocket": False,
            "options": AssetClass.OPTION in self.asset_classes,
            "optionChain": False,
            "feed": self.feed_label or None,
        }

    def health(self) -> dict:
        """Sanitized status — never includes credentials."""
        return {
            "provider": self.provider_id,
            "displayName": self.display_name,
            "enabled": True,
            "configured": self.configured,
            "authenticated": self.configured and not self.public_data_only(),
            "publicData": self.public_data_only(),
            "state": self.state.value,
            "lastError": self.last_error,
        }

    def public_data_only(self) -> bool:
        return not self.needs_credentials

    # ------------------------------------------------------------ errors --
    def _fail(self, code: ProviderErrorCode, message: str, *, state: HealthState | None = None):
        self.last_error = message
        if state is not None:
            self.state = state
        raise ProviderError(code, message)

    # ---------------------------------------------------------- abstract --
    @abstractmethod
    def search_instruments(self, query: str, limit: int = 20) -> list[dict]:
        """Normalized instruments matching ``query``."""

    @abstractmethod
    def get_instrument(self, instrument: Instrument) -> dict:
        """Resolve metadata (provider symbol id, tick/lot size) for an instrument."""

    @abstractmethod
    def get_quote(self, instrument: Instrument) -> dict:
        """Latest quote: {symbol, price, bid, ask, change, changePct, ts}."""

    @abstractmethod
    def get_candles(
        self,
        instrument: Instrument,
        timeframe: str,
        *,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Historical candles as canonical dicts (see market.Candle)."""

    def supported_timeframes(self) -> list[str]:
        from ..market import TIMEFRAMES

        return list(TIMEFRAMES)

    def supported_assets(self) -> list[str]:
        return [a.value for a in self.asset_classes]


def check_status(resp, provider_id: str) -> None:
    """Map an HTTP status onto a normalized ProviderError (2xx: no-op)."""
    if resp.status_code < 400:
        return
    if resp.status_code == 429:
        raise ProviderError(ProviderErrorCode.PROVIDER_RATE_LIMITED, f"{provider_id} rate limited")
    if resp.status_code in (401, 403):
        raise ProviderError(ProviderErrorCode.PROVIDER_AUTH_FAILED, f"{provider_id} auth failed")
    if resp.status_code == 404:
        raise ProviderError(ProviderErrorCode.SYMBOL_NOT_FOUND, f"{provider_id}: not found")
    raise ProviderError(
        ProviderErrorCode.PROVIDER_OFFLINE, f"{provider_id} http {resp.status_code}"
    )


def candle_dict(
    symbol: str,
    timeframe: str,
    ts: float,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float,
    source: str,
    *,
    closed: bool = True,
) -> dict:
    """Canonical candle dict; ``ts`` is the CLOSE timestamp (provider
    convention), open time derived from the timeframe interval."""
    from ..market import TIMEFRAME_SECONDS, normalize_timeframe

    tf = normalize_timeframe(timeframe)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "open_time": float(ts) - TIMEFRAME_SECONDS[tf],
        "close_time": float(ts),
        "open": float(o),
        "high": float(h),
        "low": float(low),
        "close": float(c),
        "volume": float(v),
        "closed": closed,
        "source": source,
    }
