"""Central configuration for the punch.trade backend."""

import os

HOST = "127.0.0.1"
PORT = 8000

# Local API token. Sent via the X-Punch-Token header (REST) or the WS
# "auth" message — never in URLs. The default is a demo token: refuse to
# run LIVE mode with it (see validate_config).
API_TOKEN = os.environ.get("PUNCH_TOKEN", "punch-demo-token")
DEFAULT_TOKEN = "punch-demo-token"

SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]

# Simulated market: a new candle is emitted every BAR_SECONDS seconds.
BAR_SECONDS = 4.0
HISTORY_BARS = 300
MAX_BARS_KEPT = 200

# Paper broker behaviour
SLIPPAGE_PCT = 0.05

# Execution mode at startup: "research" (orders blocked), "paper" (paper
# broker only) or "live" (paper + explicitly armed real brokers).
MODE = os.environ.get("PUNCH_MODE", "paper").strip().lower()
VALID_MODES = ("research", "paper", "live")

# ---- risk limits (all enforced by app/risk.py at order time) ----
# A signal older than this can no longer be executed.
SIGNAL_TTL_SECONDS = float(os.environ.get("PUNCH_SIGNAL_TTL", 300))
# An "active" strategy state auto-resets after this many bars (anti-wedge).
EXIT_TIMEOUT_BARS = int(os.environ.get("PUNCH_EXIT_TIMEOUT_BARS", 120))
# Max open positions across all symbols (paper broker tracks its own).
MAX_OPEN_POSITIONS = int(os.environ.get("PUNCH_MAX_POSITIONS", 5))
# Max shares/lots per order.
MAX_QTY = int(os.environ.get("PUNCH_MAX_QTY", 10000))
# Daily realized-loss circuit: paper ledger, % of sum of closed pnl.
MAX_DAILY_LOSS_PCT = float(os.environ.get("PUNCH_DAILY_LOSS_PCT", 5.0))
# Feed is "stale" (orders rejected) when no bar arrived for this long.
FEED_STALE_AFTER = float(os.environ.get("PUNCH_FEED_STALE_AFTER",
                                        max(30.0, 5 * BAR_SECONDS)))
# Real-broker feeds (binance/kite) poll less often: 3 minutes is fine.
LIVE_FEED_STALE_AFTER = 180.0

# Optional Telegram alerts on every live signal. Create a bot with
# @BotFather, put the token + your chat id in these env vars.
TELEGRAM_BOT_TOKEN = os.environ.get("PUNCH_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("PUNCH_TELEGRAM_CHAT_ID", "")


class ConfigError(ValueError):
    """Raised by validate_config() — the server refuses to start."""


def validate_config() -> None:
    """Startup self-check: fail fast on nonsense configuration."""
    if MODE not in VALID_MODES:
        raise ConfigError(f"PUNCH_MODE must be one of {VALID_MODES}, got '{MODE}'")
    if not API_TOKEN or len(API_TOKEN) < 8:
        raise ConfigError("PUNCH_TOKEN must be at least 8 characters")
    if BAR_SECONDS <= 0:
        raise ConfigError("BAR_SECONDS must be positive")
    if SIGNAL_TTL_SECONDS <= 0:
        raise ConfigError("PUNCH_SIGNAL_TTL must be positive")
    if MAX_OPEN_POSITIONS < 1 or MAX_QTY < 1:
        raise ConfigError("PUNCH_MAX_POSITIONS / PUNCH_MAX_QTY must be >= 1")
    if MAX_DAILY_LOSS_PCT <= 0:
        raise ConfigError("PUNCH_DAILY_LOSS_PCT must be positive")
    if MODE == "live" and API_TOKEN == DEFAULT_TOKEN:
        raise ConfigError(
            "LIVE mode refused with the default demo token — set PUNCH_TOKEN "
            "to a strong value first (risk rule: no real orders with the "
            "default token).")


def startup_report() -> str:
    lines = [
        f"mode: {MODE}",
        f"token: {'<set>' if API_TOKEN != DEFAULT_TOKEN else DEFAULT_TOKEN}",
        f"signal TTL: {SIGNAL_TTL_SECONDS:.0f}s",
        f"max positions: {MAX_OPEN_POSITIONS}, max qty: {MAX_QTY}",
        f"daily loss limit: {MAX_DAILY_LOSS_PCT:.1f}%",
        f"feed stale after: {FEED_STALE_AFTER:.0f}s",
    ]
    return " | ".join(lines)