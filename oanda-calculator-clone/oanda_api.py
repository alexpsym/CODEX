"""Helper functions for interacting with the OANDA REST API.

These utilities were formerly part of the deleted CLI module. They are used by
``oanda_calculator_web.py`` to fetch account data and build orders without
requiring the command line script.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from a dedicated OANDA env file so the calculator
# picks up credentials without requiring them to be exported in the shell. The
# path is anchored to this module's directory and can be overridden with the
# OANDA_ENV_FILE environment variable—for example ``OANDA_ENV_FILE=E:\\ENV\\oanda.env``
# on Windows. Relative or ``~``-prefixed paths supplied via OANDA_ENV_FILE are
# resolved against the module directory to match the old working directory
# behaviour when launching the web app from elsewhere in the repository.
MODULE_PATH = Path(__file__).resolve()
MODULE_DIR = MODULE_PATH.parent
# The project's ``oanda.env`` file now lives in ``E:\\ENV\\oanda.env`` for the
# Windows deployment, but macOS/Linux environments (including automated tests)
# still expect to fall back to the repo's bundled ``oanda.env``. Prefer the
# Windows path when running on Windows and the file exists; otherwise use the
# module-relative file so local development and CI work out of the box.
WINDOWS_ENV_PATH = Path("E:/ENV/oanda.env")
MODULE_ENV_PATH = MODULE_DIR / "oanda.env"
if os.name == "nt" and WINDOWS_ENV_PATH.exists():
    DEFAULT_ENV_PATH = WINDOWS_ENV_PATH
else:
    DEFAULT_ENV_PATH = MODULE_ENV_PATH

custom_env = os.getenv("OANDA_ENV_FILE")
if custom_env:
    resolved_env = Path(custom_env).expanduser()
    if not resolved_env.is_absolute():
        resolved_env = (MODULE_DIR / resolved_env).resolve()
    ENV_PATH = resolved_env
else:
    ENV_PATH = DEFAULT_ENV_PATH


def _is_placeholder(value: str | None, placeholders: set[str]) -> bool:
    """Return True when ``value`` matches a known placeholder token."""

    return bool(value) and value.strip().upper() in {p.upper() for p in placeholders}


# Only override pre-existing environment variables when they are placeholders
# (e.g., left over from a previous load) or when the caller explicitly points to
# a custom env file. This prevents the default ``oanda.env`` with placeholder
# credentials from clobbering real values already exported in the shell.
should_override = bool(custom_env)
existing_api_token = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
existing_account_id = os.getenv("OANDA_ACCOUNT_ID")

if _is_placeholder(existing_api_token, {"YOUR_OANDA_API_KEY", "YOUR_OANDA_TOKEN"}):
    should_override = True
if _is_placeholder(existing_account_id, {"YOUR_OANDA_ACCOUNT_ID"}):
    should_override = True

load_dotenv(ENV_PATH, override=should_override)
from typing import Any, Dict
import requests


def _base_url() -> str:
    """Return the configured API base URL with a sensible default."""

    return os.getenv("OANDA_BASE_URL", "https://api-fxtrade.oanda.com/v3")


def _api_key() -> str:
    """Return the API token or raise an informative error."""

    value = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
    if not value or value.strip().upper() in {"YOUR_OANDA_API_KEY", "YOUR_OANDA_TOKEN"}:
        raise OandaAPIError(
            "OANDA_API_KEY is missing. Add it to "
            f"{ENV_PATH.resolve()} or export it in your shell."
        )
    return value.strip()


def _account_id() -> str:
    """Return the account ID or raise an informative error."""

    value = os.getenv("OANDA_ACCOUNT_ID")
    if not value or value.strip().upper() == "YOUR_OANDA_ACCOUNT_ID":
        raise OandaAPIError(
            "OANDA_ACCOUNT_ID is missing or still set to the placeholder. "
            "Update your oanda.env file (or the path in OANDA_ENV_FILE) with the "
            "live or practice account number shown in your OANDA dashboard."
        )
    return value.strip()


class OandaAPIError(Exception):
    """Raised when an API request fails or is misconfigured."""


def _request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
    """Send an HTTP request to the OANDA API and return the parsed JSON."""
    api_key = _api_key()
    url = f"{_base_url()}{endpoint}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.request(method, url, headers=headers, **kwargs)
    if not resp.ok:
        raise OandaAPIError(
            f"API request failed: {resp.status_code} {resp.text}"
        )
    return resp.json()


def get_account_details() -> Dict[str, Any]:
    """Return details for the configured account."""
    account_id = _account_id()
    return _request("GET", f"/accounts/{account_id}")


def get_instrument_details(instrument: str) -> Dict[str, Any]:
    """Return metadata for a trading instrument."""
    account_id = _account_id()
    data = _request(
        "GET",
        f"/accounts/{account_id}/instruments?instruments={instrument}",
    )
    instruments = data.get("instruments", [])
    if not instruments:
        raise OandaAPIError(f"Unknown instrument: {instrument}")
    return instruments[0]


def get_available_instruments() -> set[str]:
    """Return the set of tradable instruments for the account."""
    account_id = _account_id()
    data = _request("GET", f"/accounts/{account_id}/instruments")
    return {inst["name"] for inst in data.get("instruments", [])}


def get_price(instrument: str) -> float:
    """Return the current midpoint price for ``instrument``."""
    account_id = _account_id()
    data = _request(
        "GET", f"/accounts/{account_id}/pricing?instruments={instrument}"
    )
    prices = data.get("prices", [])
    if not prices:
        raise OandaAPIError("Price data unavailable")
    bid = float(prices[0]["bids"][0]["price"])
    ask = float(prices[0]["asks"][0]["price"])
    return (bid + ask) / 2


def build_order(
    instrument: str,
    side: str,
    units: float,
    sl_price: float,
    tp_price: float,
    units_precision: int = 0,
) -> Dict[str, Any]:
    """Construct an order dictionary compatible with the OANDA API."""
    signed_units = units if side.lower() == "buy" else -units
    if units_precision > 0:
        units_str = f"{signed_units:.{units_precision}f}"
    else:
        units_str = str(int(round(signed_units)))
    return {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": units_str,
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{sl_price:.5f}"},
            "takeProfitOnFill": {"price": f"{tp_price:.5f}"},
        }
    }
