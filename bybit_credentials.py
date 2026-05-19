"""Shared Bybit credential resolution helper.

This helper keeps legacy BYBIT_API_KEY/BYBIT_API_SECRET support while
preferencing the Render-provided KEY1/SECRET1 (live) and KEY2/SECRET2
(demo/testnet) pairs.

Important:
- Bybit "Demo Trading" on mainnet uses a dedicated domain: https://api-demo.bybit.com
- Bybit Testnet uses: https://api-testnet.bybit.com

These are *not* interchangeable. Demo API keys must be used against the
demo domain.
"""
from __future__ import annotations

import os
from typing import Dict, Tuple, List


def _coerce_base_url(env_mode: str, candidate: str) -> str:
    """Force environment-correct Bybit hosts even if env vars are misconfigured."""

    c = (candidate or "").strip()
    lower = c.lower()

    if env_mode == "demo":
        if "api-demo.bybit.com" not in lower:
            return "https://api-demo.bybit.com"
        return c

    if env_mode in {"testnet", "paper"}:
        if "api-testnet.bybit.com" not in lower:
            return "https://api-testnet.bybit.com"
        return c

    if "api.bybit.com" not in lower:
        return "https://api.bybit.com"
    return c


def resolve_bybit_credentials() -> Tuple[str, str, str, str]:
    """Return (mode, key, secret, base_url, key_source).

    The default mode is ``live``.

    - ``BYBIT_ENV=live`` -> KEY1/SECRET1 + https://api.bybit.com
    - ``BYBIT_ENV=demo`` -> KEY2/SECRET2 + https://api-demo.bybit.com
    - ``BYBIT_ENV=testnet`` (or paper) -> KEY2/SECRET2 + https://api-testnet.bybit.com

    Legacy ``BYBIT_API_KEY``/``BYBIT_API_SECRET`` values are still honored as a
    fallback so existing env files keep working.
    """

    mode = os.getenv("BYBIT_ENV", "live").strip().lower() or "live"
    if mode in {"demo", "testnet", "paper"}:
        key = os.getenv("BYBIT_API_KEY2") or os.getenv("BYBIT_API_KEY") or ""
        secret = os.getenv("BYBIT_API_SECRET2") or os.getenv("BYBIT_API_SECRET") or ""
        if mode == "demo":
            base_url = _coerce_base_url(
                "demo",
                os.getenv("BYBIT_BASE_URL_DEMO")
                or os.getenv("BYBIT_API_BASE_DEMO")
                or "https://api-demo.bybit.com",
            )
        else:
            base_url = _coerce_base_url(
                "testnet",
                os.getenv("BYBIT_BASE_URL_TESTNET")
                or os.getenv("BYBIT_API_BASE_TESTNET")
                or "https://api-testnet.bybit.com",
            )
        key_source = "KEY2" if os.getenv("BYBIT_API_KEY2") or os.getenv("BYBIT_API_SECRET2") else "LEGACY"
    else:
        key = os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_KEY") or ""
        secret = os.getenv("BYBIT_API_SECRET1") or os.getenv("BYBIT_API_SECRET") or ""
        base_url = _coerce_base_url(
            "live",
            os.getenv("BYBIT_BASE_URL")
            or os.getenv("BYBIT_API_BASE")
            or "https://api.bybit.com",
        )
        key_source = "KEY1" if os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_SECRET1") else "LEGACY"

    return mode, key, secret, base_url.rstrip("/"), key_source


def resolve_bybit_credentials_for(mode: str) -> Tuple[str, str, str, str, str]:
    """Return credentials for an explicit mode without relying on BYBIT_ENV."""

    normalized = (mode or "live").strip().lower()
    if normalized in {"demo", "testnet", "paper"}:
        key = (
            os.getenv("BYBIT_DEMO_API_KEY")
            or os.getenv("BYBIT_API_KEY2")
            or os.getenv("BYBIT_API_KEY")
            or ""
        )
        secret = (
            os.getenv("BYBIT_DEMO_API_SECRET")
            or os.getenv("BYBIT_API_SECRET2")
            or os.getenv("BYBIT_API_SECRET")
            or ""
        )
        if normalized == "testnet":
            base_url = _coerce_base_url(
                "testnet",
                os.getenv("BYBIT_BASE_URL_TESTNET")
                or os.getenv("BYBIT_API_BASE_TESTNET")
                or "https://api-testnet.bybit.com",
            )
            env_label = "testnet"
        else:
            base_url = _coerce_base_url(
                "demo",
                os.getenv("BYBIT_BASE_URL_DEMO")
                or os.getenv("BYBIT_API_BASE_DEMO")
                or "https://api-demo.bybit.com",
            )
            env_label = "demo"
        if os.getenv("BYBIT_DEMO_API_KEY") or os.getenv("BYBIT_DEMO_API_SECRET"):
            key_source = "DEMO_EXPLICIT"
        elif os.getenv("BYBIT_API_KEY2") or os.getenv("BYBIT_API_SECRET2"):
            key_source = "KEY2"
        else:
            key_source = "LEGACY"
        return env_label, key, secret, base_url.rstrip("/"), key_source

    key = os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_KEY") or ""
    secret = os.getenv("BYBIT_API_SECRET1") or os.getenv("BYBIT_API_SECRET") or ""
    base_url = _coerce_base_url(
        "live",
        os.getenv("BYBIT_BASE_URL_LIVE")
        or os.getenv("BYBIT_BASE_URL")
        or os.getenv("BYBIT_API_BASE")
        or "https://api.bybit.com",
    )
    key_source = (
        "KEY1"
        if os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_SECRET1")
        else "LEGACY"
    )
    return "live", key, secret, base_url.rstrip("/"), key_source


def summarize_bybit_auth() -> Dict[str, str]:
    """Return a summary payload without exposing secrets."""

    mode, key, secret, base_url, key_source = resolve_bybit_credentials()
    return {
        "mode": mode,
        "base_url": base_url,
        "auth": "yes" if key and secret else "no",
        "key_source": key_source,
    }


def describe_bybit_credentials_for(mode: str) -> Dict[str, object]:
    env_label, key, secret, base_url, key_source = resolve_bybit_credentials_for(mode)
    missing: List[str] = []
    if env_label == "demo":
        if not key:
            missing.append("BYBIT_DEMO_API_KEY|BYBIT_API_KEY2|BYBIT_API_KEY")
        if not secret:
            missing.append("BYBIT_DEMO_API_SECRET|BYBIT_API_SECRET2|BYBIT_API_SECRET")
    elif env_label == "live":
        if not key:
            missing.append("BYBIT_API_KEY1|BYBIT_API_KEY")
        if not secret:
            missing.append("BYBIT_API_SECRET1|BYBIT_API_SECRET")
    return {
        "mode": env_label,
        "base_url": base_url,
        "key_source": key_source,
        "credentials_available": bool(key and secret),
        "missing_env_vars": missing,
    }
