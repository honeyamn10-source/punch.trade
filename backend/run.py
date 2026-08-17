"""punch.trade backend entrypoint.

    python run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from app import config
from app import risk
from app.version import VERSION


def _ollama_line() -> str:
    try:
        from app.ai import status as ai_status

        st = ai_status()
    except Exception:
        return "OLLAMA: UNKNOWN"
    if st.get("enabled"):
        return f"OLLAMA: READY ({st.get('model', '?')})"
    reason = (st.get("reason") or "not installed").split(".")[0]
    return f"OLLAMA: OFFLINE ({reason})"


def _banner() -> None:
    width = 62
    print("=" * width)
    print(f"  PUNCH.TRADE v{VERSION}")
    print(f"  MODE: {config.MODE.upper()}  (research | paper | live)")
    print(f"  HOST: http://{config.HOST}:{config.PORT}")
    print(f"  DATABASE: {config.DB_PATH}")
    print(f"  LIVE ARMED: {'YES' if risk.armed() else 'NO'}")
    print(f"  {_ollama_line()}")
    print("=" * width)


if __name__ == "__main__":
    # startup self-check: refuse to boot with nonsense/unsafe configuration
    config.validate_config()
    _banner()
    print("[startup] execution gate: real orders require LIVE mode + explicit "
          "arming (never persisted)")
    uvicorn.run("app.api:app", host=config.HOST, port=config.PORT, reload=False)