"""Shared Bybit credential resolution helper.

This helper keeps legacy BYBIT_API_KEY/BYBIT_API_SECRET support while
preferencing the Render-provided KEY1/SECRET1 (live) and KEY2/SECRET2
(testnet) pairs. It also returns the base URL to keep callers consistent.
"""
from __future__ import annotations

import os
from typing import Dict, Tuple


def resolve_bybit_credentials() -> Tuple[str, str, str, str]:
    """Return (mode, key, secret, base_url, key_source).

    The default mode is ``live``. Setting ``BYBIT_ENV`` to ``demo``, ``testnet``
    or ``paper`` switches to KEY2/SECRET2 + the testnet base URL. Legacy
    ``BYBIT_API_KEY``/``BYBIT_API_SECRET`` values are still honored as a
    fallback for both modes so existing env files keep working.
    """

    mode = os.getenv("BYBIT_ENV", "live").strip().lower() or "live"
    if mode in {"demo", "testnet", "paper"}:
        key = os.getenv("BYBIT_API_KEY2") or os.getenv("BYBIT_API_KEY") or ""
        secret = os.getenv("BYBIT_API_SECRET2") or os.getenv("BYBIT_API_SECRET") or ""
        base_url = (
            os.getenv("BYBIT_BASE_URL_TESTNET")
            or os.getenv("BYBIT_API_BASE_TESTNET")
            or "https://api-testnet.bybit.com"
        )
        key_source = "KEY2" if os.getenv("BYBIT_API_KEY2") or os.getenv("BYBIT_API_SECRET2") else "LEGACY"
    else:
        key = os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_KEY") or ""
        secret = os.getenv("BYBIT_API_SECRET1") or os.getenv("BYBIT_API_SECRET") or ""
        base_url = os.getenv("BYBIT_BASE_URL") or os.getenv("BYBIT_API_BASE") or "https://api.bybit.com"
        key_source = "KEY1" if os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_SECRET1") else "LEGACY"

    return mode, key, secret, base_url.rstrip("/"), key_source


def summarize_bybit_auth() -> Dict[str, str]:
    """Return a summary payload without exposing secrets."""

    mode, key, secret, base_url, key_source = resolve_bybit_credentials()
    return {
        "mode": mode,
        "base_url": base_url,
        "auth": "yes" if key and secret else "no",
        "key_source": key_source,
    }
