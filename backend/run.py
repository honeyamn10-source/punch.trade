"""punch.trade backend entrypoint.

    python run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from app import config

if __name__ == "__main__":
    print("punch.trade backend starting on "
          f"http://{config.HOST}:{config.PORT} (token: {config.API_TOKEN})")
    uvicorn.run("app.api:app", host=config.HOST, port=config.PORT, reload=False)