"""Helper functions for interacting with the OANDA REST API.

These utilities are shared by OANDA-related services in this repository.
"""

from __future__ import annotations

import logging
import os

from pathlib import Path
from shared.env_bootstrap import load_master_env

ENV_INFO = load_master_env()
ENV_PATH = Path(ENV_INFO.get("loaded_file") or ".env")
from typing import Any, Dict, Optional
import requests

LOGGER = logging.getLogger(__name__)


def _credential_suffix(mode: str) -> str:
    return "_DEMO" if mode == "demo" else ""


def _normalize_base(base: str) -> str:
    """Normalize the OANDA base URL so it always includes ``/v3``."""

    cleaned = (base or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""
    cleaned = cleaned.rstrip("/")
    if cleaned.endswith("/v3"):
        return cleaned
    return f"{cleaned}/v3"


def _base_url(mode: str = "live") -> str:
    """Return the configured API base URL with a sensible default."""

    if mode == "demo":
        base = (
            os.getenv("OANDA_API_URL_DEMO")
            or os.getenv("OANDA_BASE_URL_DEMO")
            or os.getenv("OANDA_URL_DEMO")
            or os.getenv("OANDA_API_URL_PRACTICE")
            or os.getenv("OANDA_BASE_URL_PRACTICE")
            or os.getenv("OANDA_URL_PRACTICE")
            or "https://api-fxpractice.oanda.com"
        )
    else:
        base = (
            os.getenv("OANDA_API_URL_LIVE")
            or os.getenv("OANDA_BASE_URL_LIVE")
            or os.getenv("OANDA_URL_LIVE")
            or os.getenv("OANDA_BASE_URL")
            or os.getenv("OANDA_URL")
            or os.getenv("OANDA_API_URL")
            or "https://api-fxtrade.oanda.com"
        )
    return _normalize_base(base)


def _api_key(mode: str = "live") -> str:
    """Return the API token or raise an informative error."""

    if mode == "demo":
        value = (
            os.getenv("OANDA_API_KEY_DEMO")
            or os.getenv("OANDA_TOKEN_DEMO")
            or os.getenv("OANDA_API_KEY_PRACTICE")
            or os.getenv("OANDA_TOKEN_PRACTICE")
        )
        missing_message = (
            "OANDA_API_KEY_DEMO is missing. Set OANDA_API_KEY_DEMO "
            "or OANDA_TOKEN_DEMO for the practice account."
        )
    else:
        value = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
        missing_message = (
            "OANDA_API_KEY is missing. Add it to "
            f"{ENV_PATH.resolve()} or export it in your shell."
        )
    if not value or value.strip().upper() in {"YOUR_OANDA_API_KEY", "YOUR_OANDA_TOKEN"}:
        raise OandaAPIError(missing_message)
    return value.strip()


def _account_id(mode: str = "live") -> str:
    """Return the account ID or raise an informative error."""

    if mode == "demo":
        value = os.getenv("OANDA_ACCOUNT_ID_DEMO") or os.getenv("OANDA_ACCOUNT_ID_PRACTICE")
        missing_message = (
            "OANDA_ACCOUNT_ID_DEMO is missing or still set to the placeholder. "
            "Update your oanda.env file (or the path in OANDA_ENV_FILE) with the "
            "practice account number shown in your OANDA dashboard."
        )
    else:
        value = os.getenv("OANDA_ACCOUNT_ID")
        missing_message = (
            "OANDA_ACCOUNT_ID is missing or still set to the placeholder. "
            "Update your oanda.env file (or the path in OANDA_ENV_FILE) with the "
            "live account number shown in your OANDA dashboard."
        )
    if not value or value.strip().upper() == "YOUR_OANDA_ACCOUNT_ID":
        raise OandaAPIError(missing_message)
    return value.strip()


class OandaAPIError(Exception):
    """Raised when an API request fails or is misconfigured."""


def _token_last4(value: str) -> str:
    return value[-4:] if value else ""


def _ensure_endpoint(endpoint: str) -> str:
    cleaned = endpoint.strip()
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    if cleaned.startswith("/v3/"):
        LOGGER.warning("OANDA endpoint already includes /v3 prefix: %s", cleaned)
        cleaned = cleaned[len("/v3") :]
    return cleaned


def _preflight_account_check(
    *, base_url: str, api_key: str, account_id: str, mode: str
) -> Optional[list[str]]:
    url = f"{base_url}/accounts"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:  # pragma: no cover - network dependent
        LOGGER.warning(
            "OANDA_PREFLIGHT_ERROR mode=%s base=%s account_id=%s token_last4=%s error=%s",
            mode,
            base_url,
            account_id,
            _token_last4(api_key),
            exc,
        )
        return None
    if not resp.ok:
        LOGGER.warning(
            "OANDA_PREFLIGHT_FAIL mode=%s base=%s account_id=%s token_last4=%s status=%s body=%s",
            mode,
            base_url,
            account_id,
            _token_last4(api_key),
            resp.status_code,
            resp.text[:200],
        )
        return None
    payload = resp.json()
    accounts = [
        acct.get("id")
        for acct in payload.get("accounts", [])
        if isinstance(acct, dict) and acct.get("id")
    ]
    return accounts or None


def _request(
    method: str,
    endpoint: str,
    mode: str = "live",
    account_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Send an HTTP request to the OANDA API and return the parsed JSON."""
    api_key = _api_key(mode)
    base_url = _base_url(mode)
    endpoint = _ensure_endpoint(endpoint)
    url = f"{base_url}{endpoint}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.request(method, url, headers=headers, **kwargs)
    if not resp.ok:
        if resp.status_code in {401, 403}:
            account_id = account_id or ""
            LOGGER.warning(
                "OANDA_AUTH_FAIL mode=%s base=%s account_id=%s token_last4=%s endpoint=%s url=%s status=%s body=%s",
                mode,
                base_url,
                account_id,
                _token_last4(api_key),
                endpoint,
                url,
                resp.status_code,
                resp.text[:200],
            )
            available_accounts = None
            if account_id:
                available_accounts = _preflight_account_check(
                    base_url=base_url,
                    api_key=api_key,
                    account_id=account_id,
                    mode=mode,
                )
            if available_accounts and account_id not in available_accounts:
                raise OandaAPIError(
                    "OANDA credentials do not match the configured account. "
                    f"Mode: {mode}, base: {base_url}, account_id: {account_id}. "
                    "Token/account/environment mismatch; check "
                    "OANDA_API_KEY_DEMO + OANDA_ACCOUNT_ID_DEMO vs practice base URL. "
                    f"Available accounts: {', '.join(available_accounts)}."
                )
        raise OandaAPIError(
            "API request failed. "
            f"Mode: {mode}, base: {base_url}, account_id: {account_id or 'unknown'}. "
            f"Status: {resp.status_code}. "
            "Token/account/environment mismatch; check "
            "OANDA_API_KEY_DEMO + OANDA_ACCOUNT_ID_DEMO vs practice base URL. "
            f"Details: {resp.text}"
        )
    return resp.json()


def get_account_details(mode: str = "live") -> Dict[str, Any]:
    """Return details for the configured account."""
    account_id = _account_id(mode)
    return _request("GET", f"/accounts/{account_id}", mode=mode, account_id=account_id)


def get_instrument_details(instrument: str, mode: str = "live") -> Dict[str, Any]:
    """Return metadata for a trading instrument."""
    account_id = _account_id(mode)
    data = _request(
        "GET",
        f"/accounts/{account_id}/instruments?instruments={instrument}",
        mode=mode,
        account_id=account_id,
    )
    instruments = data.get("instruments", [])
    if not instruments:
        raise OandaAPIError(f"Unknown instrument: {instrument}")
    return instruments[0]


def get_available_instruments(mode: str = "live") -> set[str]:
    """Return the set of tradable instruments for the account."""
    account_id = _account_id(mode)
    data = _request(
        "GET", f"/accounts/{account_id}/instruments", mode=mode, account_id=account_id
    )
    return {inst["name"] for inst in data.get("instruments", [])}


def get_price(instrument: str, mode: str = "live") -> float:
    """Return the current midpoint price for ``instrument``."""
    account_id = _account_id(mode)
    data = _request(
        "GET",
        f"/accounts/{account_id}/pricing?instruments={instrument}",
        mode=mode,
        account_id=account_id,
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
