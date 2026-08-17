"""Canonical punch.trade application version — the single source of truth.

The API (FastAPI metadata + health/system responses), the dashboard and
release tooling all read from here. Bump VERSION for a release; never
hardcode the version anywhere else.
"""

from __future__ import annotations

import functools
import subprocess

VERSION = "0.3.0"


@functools.lru_cache(maxsize=1)
def git_commit() -> str | None:
    """Best-effort short git commit hash (cached).

    The application must never *require* git at runtime; this returns None
    when git is unavailable so callers can degrade gracefully.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    commit = proc.stdout.strip()
    return commit or None
