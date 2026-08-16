"""Central configuration for the punch.trade backend."""

HOST = "127.0.0.1"
PORT = 8000

# Trivial dev token. Swap for real auth (OAuth per broker) before anyone
# other than your trusted circle connects.
API_TOKEN = "punch-demo-token"

SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]

# Simulated market: a new candle is emitted every BAR_SECONDS seconds.
BAR_SECONDS = 4.0
HISTORY_BARS = 300
MAX_BARS_KEPT = 200

# Paper broker behaviour
SLIPPAGE_PCT = 0.05