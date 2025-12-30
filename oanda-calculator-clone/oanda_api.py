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
# The project's ``oanda.env`` file now lives in ``E:\\ENV\\oanda.env``. Use
# that as the default so Windows users launching the calculator from anywhere
# still load the credentials without relying on the repo layout.
DEFAULT_ENV_PATH = Path("E:/ENV/oanda.env")

custom_env = os.getenv("OANDA_ENV_FILE")
if custom_env:
    resolved_env = Path(custom_env).expanduser()
    if not resolved_env.is_absolute():
        resolved_env = (MODULE_DIR / resolved_env).resolve()
    ENV_PATH = resolved_env
else:
    ENV_PATH = DEFAULT_ENV_PATH
# Always override any previously-exported placeholders so the values from
# the selected env file (for example, ``E:\\ENV\\oanda.env``) take
# precedence when the web app is reloaded.
load_dotenv(ENV_PATH, override=True)
from typing import Any, Dict, Optional
import requests


def _credential_suffix(mode: str) -> str:
    return "_DEMO" if mode == "demo" else ""


def _base_url(mode: str = "live") -> str:
    """Return the configured API base URL with a sensible default."""

    suffix = _credential_suffix(mode)
    return (
        os.getenv(f"OANDA_BASE_URL{suffix}")
        or os.getenv(f"OANDA_URL{suffix}")
        or os.getenv(f"OANDA_API_URL{suffix}")
        or os.getenv("OANDA_BASE_URL")
        or "https://api-fxtrade.oanda.com/v3"
    )


def _api_key(mode: str = "live") -> str:
    """Return the API token or raise an informative error."""

    suffix = _credential_suffix(mode)
    value = (
        os.getenv(f"OANDA_API_KEY{suffix}")
        or os.getenv(f"OANDA_TOKEN{suffix}")
        or os.getenv("OANDA_API_KEY")
        or os.getenv("OANDA_TOKEN")
    )
    if not value or value.strip().upper() in {"YOUR_OANDA_API_KEY", "YOUR_OANDA_TOKEN"}:
        raise OandaAPIError(
            "OANDA_API_KEY is missing. Add it to "
            f"{ENV_PATH.resolve()} or export it in your shell."
        )
    return value.strip()


def _account_id(mode: str = "live") -> str:
    """Return the account ID or raise an informative error."""

    suffix = _credential_suffix(mode)
    value = os.getenv(f"OANDA_ACCOUNT_ID{suffix}") or os.getenv("OANDA_ACCOUNT_ID")
    if not value or value.strip().upper() == "YOUR_OANDA_ACCOUNT_ID":
        raise OandaAPIError(
            "OANDA_ACCOUNT_ID is missing or still set to the placeholder. "
            "Update your oanda.env file (or the path in OANDA_ENV_FILE) with the "
            "live or practice account number shown in your OANDA dashboard."
        )
    return value.strip()


class OandaAPIError(Exception):
    """Raised when an API request fails or is misconfigured."""


def _request(method: str, endpoint: str, mode: str = "live", **kwargs: Any) -> Dict[str, Any]:
    """Send an HTTP request to the OANDA API and return the parsed JSON."""
    api_key = _api_key(mode)
    url = f"{_base_url(mode)}{endpoint}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.request(method, url, headers=headers, **kwargs)
    if not resp.ok:
        raise OandaAPIError(
            f"API request failed: {resp.status_code} {resp.text}"
        )
    return resp.json()


def get_account_details(mode: str = "live") -> Dict[str, Any]:
    """Return details for the configured account."""
    account_id = _account_id(mode)
    return _request("GET", f"/accounts/{account_id}", mode=mode)


def get_instrument_details(instrument: str, mode: str = "live") -> Dict[str, Any]:
    """Return metadata for a trading instrument."""
    account_id = _account_id(mode)
    data = _request(
        "GET",
        f"/accounts/{account_id}/instruments?instruments={instrument}",
        mode=mode,
    )
    instruments = data.get("instruments", [])
    if not instruments:
        raise OandaAPIError(f"Unknown instrument: {instrument}")
    return instruments[0]


def get_available_instruments(mode: str = "live") -> set[str]:
    """Return the set of tradable instruments for the account."""
    account_id = _account_id(mode)
    data = _request("GET", f"/accounts/{account_id}/instruments", mode=mode)
    return {inst["name"] for inst in data.get("instruments", [])}


def get_price(instrument: str, mode: str = "live") -> float:
    """Return the current midpoint price for ``instrument``."""
    account_id = _account_id(mode)
    data = _request(
        "GET", f"/accounts/{account_id}/pricing?instruments={instrument}", mode=mode
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
    order_type: str = "market",
    entry_price: Optional[float] = None,
    price_precision: int = 5,
) -> Dict[str, Any]:
    """Construct an order dictionary compatible with the OANDA API."""
    signed_units = units if side.lower() == "buy" else -units
    if units_precision > 0:
        units_str = f"{signed_units:.{units_precision}f}"
    else:
        units_str = str(int(round(signed_units)))
    order_type = order_type.lower()
    if order_type not in {"market", "limit"}:
        raise ValueError("Order type must be market or limit.")
    order: Dict[str, Any] = {
        "type": "MARKET" if order_type == "market" else "LIMIT",
        "instrument": instrument,
        "units": units_str,
        "timeInForce": "FOK" if order_type == "market" else "GTC",
        "positionFill": "DEFAULT",
        "stopLossOnFill": {"price": f"{sl_price:.{price_precision}f}"},
        "takeProfitOnFill": {"price": f"{tp_price:.{price_precision}f}"},
    }
    if order_type == "limit":
        if entry_price is None:
            raise ValueError("Limit orders require an entry price.")
        order["price"] = f"{entry_price:.{price_precision}f}"
    return {
        "order": order
    }
