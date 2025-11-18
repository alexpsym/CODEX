"""Helper functions for interacting with the OANDA REST API.

These utilities were formerly part of the deleted CLI module. They are used by
``oanda_calculator_web.py`` to fetch account data and build orders without
requiring the command line script.
"""

from __future__ import annotations

import os

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

# Load environment variables from a dedicated OANDA env file so the calculator
# picks up credentials without requiring them to be exported in the shell. The
# path can be overridden with the OANDA_ENV_FILE environment variable—for
# example ``OANDA_ENV_FILE=E:\\ENV\\oanda.env`` on Windows. If the provided
# path is missing, fall back to a local .env file so users who followed generic
# dotenv conventions are still supported.
ENV_PATH = Path(os.getenv("OANDA_ENV_FILE", "oanda.env"))
ENV_FALLBACKS = [ENV_PATH]
if ENV_PATH.name != ".env":
    ENV_FALLBACKS.append(Path(".env"))


def _apply_env_file(path: Path) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` env file."""

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except OSError as exc:  # pragma: no cover - filesystem edge
        raise OandaAPIError(f"Failed to read environment file {path}: {exc}") from exc


def _load_env_file(paths: list[Path]) -> Path | None:
    """Load the first existing env file using python-dotenv if available."""

    for path in paths:
        if not path.exists():
            continue
        if load_dotenv:
            load_dotenv(dotenv_path=path)
        else:  # fallback minimal parser when python-dotenv is absent
            _apply_env_file(path)
        return path
    return None


_loaded_env_path: Path | None = _load_env_file(ENV_FALLBACKS)
from typing import Any, Dict
import requests


def _env_hint() -> str:
    """Provide a helpful hint about where to set environment variables."""

    if _loaded_env_path:
        return str(_loaded_env_path.resolve())
    paths = [path.resolve() for path in ENV_FALLBACKS]
    return " or ".join(str(path) for path in paths)


def _base_url() -> str:
    """Return the configured API base URL with a sensible default."""

    return os.getenv("OANDA_BASE_URL", "https://api-fxtrade.oanda.com/v3")


def _api_key() -> str:
    """Return the API token or raise an informative error."""

    value = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
    if not value or value.strip().upper() in {"YOUR_OANDA_API_KEY", "YOUR_OANDA_TOKEN"}:
        raise OandaAPIError(
            "OANDA_API_KEY is missing. Add it to "
            f"{_env_hint()} or export it in your shell."
        )
    return value.strip()


def _account_id() -> str:
    """Return the account ID or raise an informative error."""

    value = os.getenv("OANDA_ACCOUNT_ID")
    if not value or value.strip().upper() == "YOUR_OANDA_ACCOUNT_ID":
        raise OandaAPIError(
            "OANDA_ACCOUNT_ID is missing or still set to the placeholder. "
            "Update your oanda.env file (or the path in OANDA_ENV_FILE) with the "
            "live or practice account number shown in your OANDA dashboard. "
            f"Current search paths: {_env_hint()}."
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
