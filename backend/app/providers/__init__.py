"""Provider registry — build all market data providers from env + vault.

Every provider exposes a sanitized ``health()`` dict; credentials only
ever come from environment variables or the Fernet vault, and are never
exposed through any endpoint.
"""

from __future__ import annotations

from .. import vault
from .alpaca import AlpacaProvider
from .alpha_vantage import AlphaVantageProvider
from .angel import AngelProvider
from .base import HealthState, MarketDataProvider
from .binance import BinanceProvider
from .coingecko import CoinGeckoProvider
from .dhan import DhanProvider
from .twelve_data import TwelveDataProvider
from .upstox import UpstoxProvider

PROVIDER_IDS = (
    "binance",
    "coingecko",
    "dhan",
    "upstox",
    "angel",
    "alpaca",
    "twelve_data",
    "alpha_vantage",
)

# provider_id -> the env var names that count as "credentials"
CREDENTIAL_ENV = {
    "dhan": ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"),
    "upstox": ("UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET", "UPSTOX_ACCESS_TOKEN"),
    "angel": ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_TOTP_SECRET"),
    "alpaca": ("ALPACA_API_KEY", "ALPACA_API_SECRET"),
    "twelve_data": ("TWELVE_DATA_API_KEY",),
    "alpha_vantage": ("ALPHA_VANTAGE_API_KEY",),
}


def build_providers() -> dict[str, MarketDataProvider]:
    """Instantiate every provider, layering env + vault credentials."""
    return {
        "binance": BinanceProvider(),
        "coingecko": CoinGeckoProvider(),
        "dhan": _with_vault_creds(DhanProvider(), "dhan"),
        "upstox": _with_vault_creds(UpstoxProvider(), "upstox"),
        "angel": _with_vault_creds(AngelProvider(), "angel"),
        "alpaca": _with_vault_creds(AlpacaProvider(), "alpaca"),
        "twelve_data": TwelveDataProvider(),
        "alpha_vantage": AlphaVantageProvider(),
    }


def _with_vault_creds(provider: MarketDataProvider, name: str) -> MarketDataProvider:
    """Vault creds override env (vault is the more explicit/secure path)."""
    saved = vault.load(f"md_{name}")
    if not saved:
        return provider
    try:
        if name == "dhan":
            return DhanProvider(
                client_id=saved.get("client_id", ""),
                access_token=saved.get("access_token", ""),
            )
        if name == "upstox":
            return UpstoxProvider(
                client_id=saved.get("client_id", ""),
                client_secret=saved.get("client_secret", ""),
                access_token=saved.get("access_token", ""),
            )
        if name == "angel":
            return AngelProvider(
                api_key=saved.get("api_key", ""),
                client_code=saved.get("client_code", ""),
                totp_secret=saved.get("totp_secret", ""),
            )
        if name == "alpaca":
            p = AlpacaProvider()
            p.api_key = saved.get("api_key", "")
            p.api_secret = saved.get("api_secret", "")
            p.configured = bool(p.api_key and p.api_secret)
            p.state = HealthState.READY if p.configured else HealthState.AUTH_REQUIRED
            return p
    except Exception:
        pass
    return provider


def provider_states() -> dict:
    """Sanitized status map — never includes credentials."""
    out = {}
    for pid in PROVIDER_IDS:
        p = build_providers()[pid]
        out[pid] = p.health()
    return out


def mask_value(value: str | None) -> str | None:
    """Show only the tail of a configured secret ('••••4F2A')."""
    if not value:
        return None
    tail = value[-4:] if len(value) > 4 else value
    return f"••••{tail}"
