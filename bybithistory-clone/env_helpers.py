"""Utilities for loading Bybit API credentials from a standard location."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


BYBIT_LIVE_ENV_PATH = Path(r"E:/ENV/bybit-live.env")


def load_bybit_live_env(logger: Optional[object] = None) -> bool:
    """Load the Bybit live credential file if it exists.

    The helper prefers ``python-dotenv`` when available so it can correctly
    parse ``KEY=VALUE`` lines and override any pre-existing environment
    variables. If the dependency is missing, it falls back to a minimal parser
    that applies the same override behaviour. Returns ``True`` when the file
    exists and was processed successfully.
    """

    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        load_dotenv = None  # type: ignore

    if not BYBIT_LIVE_ENV_PATH.exists():
        if logger:
            logger.info("Bybit live env file not found at %s", BYBIT_LIVE_ENV_PATH)
        return False

    if load_dotenv is not None:
        loaded = load_dotenv(BYBIT_LIVE_ENV_PATH, override=True)
    else:  # pragma: no cover - simple fallback parser
        loaded = False
        for line in BYBIT_LIVE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()
            loaded = True

    if loaded and logger:
        logger.info("Loaded Bybit live credentials from %s", BYBIT_LIVE_ENV_PATH)
    return bool(loaded)

