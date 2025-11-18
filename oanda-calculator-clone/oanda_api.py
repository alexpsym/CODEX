"""Helper functions for interacting with the OANDA REST API.

These utilities were formerly part of the deleted CLI module. They are used by
``oanda_calculator_web.py`` to fetch account data and build orders without
requiring the command line script.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

ENV_PATH = Path(os.getenv("OANDA_ENV_FILE", "oanda.env"))

# Load environment variables from a dedicated OANDA env file so the calculator
# picks up credentials without requiring them to be exported in the shell. The
# path can be overridden with the OANDA_ENV_FILE environment variable—for
# example ``OANDA_ENV_FILE=E:\\ENV\\oanda.env`` on Windows. If python-dotenv
# is unavailable, skip loading so the package remains usable when env vars are
# already set in the environment.
if load_dotenv:
    load_dotenv(ENV_PATH)
import requests

# Allow overriding the OANDA API base URL via an environment variable.
BASE_URL = os.getenv("OANDA_BASE_URL", "https://api-fxtrade.oanda.com/v3")
# Support a legacy variable name for backwards compatibility.
API_KEY = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")


class OandaAPIError(Exception):
    """Raised when an API request fails or is misconfigured."""


def _request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
    """Send an HTTP request to the OANDA API and return the parsed JSON."""
    if not API_KEY:
        raise OandaAPIError("OANDA_API_KEY environment variable not set")
    url = f"{BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    resp = requests.request(method, url, headers=headers, **kwargs)
    if not resp.ok:
        raise OandaAPIError(
            f"API request failed: {resp.status_code} {resp.text}"
        )
    return resp.json()


def get_account_details() -> Dict[str, Any]:
    """Return details for the configured account."""
    return _request("GET", f"/accounts/{ACCOUNT_ID}")


def get_instrument_details(instrument: str) -> Dict[str, Any]:
    """Return metadata for a trading instrument."""
    data = _request(
        "GET",
        f"/accounts/{ACCOUNT_ID}/instruments?instruments={instrument}",
    )
    instruments = data.get("instruments", [])
    if not instruments:
        raise OandaAPIError(f"Unknown instrument: {instrument}")
    return instruments[0]


def get_available_instruments() -> set[str]:
    """Return the set of tradable instruments for the account."""
    data = _request("GET", f"/accounts/{ACCOUNT_ID}/instruments")
    return {inst["name"] for inst in data.get("instruments", [])}


def get_price(instrument: str) -> float:
    """Return the current midpoint price for ``instrument``."""
    data = _request(
        "GET", f"/accounts/{ACCOUNT_ID}/pricing?instruments={instrument}"
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
