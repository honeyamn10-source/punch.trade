"""punch.trade backend entrypoint.

    python run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from app import config
from app import risk

if __name__ == "__main__":
    # startup self-check: refuse to boot with nonsense/unsafe configuration
    config.validate_config()
    print("punch.trade backend starting on "
          f"http://{config.HOST}:{config.PORT}")
    print(f"[startup] {config.startup_report()}")
    print(f"[startup] execution gate: mode={risk.mode()} — real orders "
          "require LIVE mode + explicit arming")
    uvicorn.run("app.api:app", host=config.HOST, port=config.PORT, reload=False)