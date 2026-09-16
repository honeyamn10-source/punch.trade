"""punch.trade backend entrypoint.

python run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from app import config, risk
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
    print(f"  LOGS: {LOG_DIR}")
    print(f"  LIVE ARMED: {'YES' if risk.armed() else 'NO'}")
    print(f"  {_ollama_line()}")
    print("=" * width)


def _log_config() -> dict:
    """uvicorn logging: console + rotating files under data/logs/."""
    os.makedirs(LOG_DIR, exist_ok=True)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "access": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "default"},
            "access_console": {"class": "logging.StreamHandler", "formatter": "access"},
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": os.path.join(LOG_DIR, "error.log"),
                "formatter": "default",
                "maxBytes": 5_000_000,
                "backupCount": 3,
            },
            "access_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": os.path.join(LOG_DIR, "access.log"),
                "formatter": "access",
                "maxBytes": 5_000_000,
                "backupCount": 3,
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["console", "error_file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {
                "handlers": ["console", "error_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access_console", "access_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


LOG_DIR = os.path.join(config.DATA_DIR, "logs")


if __name__ == "__main__":
    # startup self-check: refuse to boot with nonsense/unsafe configuration
    config.validate_config()
    _banner()
    print(
        "[startup] execution gate: real orders require LIVE mode + explicit "
        "arming (never persisted)"
    )
    uvicorn.run(
        "app.api:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_config=_log_config(),
    )
