"""Fetch Oanda transaction history and export it to a CSV file."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import argparse
import re
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover - fallback for environments without requests
    requests = None  # type: ignore[assignment]
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


API_URL = os.getenv("OANDA_API_URL", "https://api-fxtrade.oanda.com/v3")

CSV_FIELDNAMES = [
    "TICKET",
    "TRANSACTION DATE",
    "TRANSACTION TYPE",
    "DETAILS",
    "INSTRUMENT",
    "PRICE",
    "UNITS",
    "DIRECTION",
    "SPREAD COST",
    "STOP LOSS",
    "GUARANTEED STOP LOSS",
    "TAKE PROFIT",
    "TRAILING STOP",
    "FINANCING",
    "COMMISSION",
    "GSL FEE",
    "GSL PREMIUM",
    "CONVERSION RATE",
    "PL",
    "AMOUNT",
    "BALANCE",
]

DEFAULT_TIMEZONE = "Australia/Sydney"


class OandaAPIError(Exception):
    """Raised when the Oanda API returns an error."""


def _get_env_var(name: str) -> str:
    """Return the value of environment variable *name*.

    Parameters
    ----------
    name:
        The environment variable to read.

    Returns
    -------
    str
        The value of the environment variable.

    Raises
    ------
    KeyError
        If the variable is not set.
    """

    value = os.getenv(name)
    if value is None:
        raise KeyError(f"Environment variable {name} not set")
    return value


def _normalize_date(value: str | None) -> str | None:
    """Convert a YYYY-MM-DD date to ISO 8601 format with a UTC suffix.

    Parameters
    ----------
    value:
        A date string like ``YYYY-MM-DD`` or ``None``.

    Returns
    -------
    str | None
        The normalized ISO 8601 timestamp or ``None`` if ``value`` is ``None``.
    """

    if value is None:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + "T00:00:00Z"

    return value


def fetch_transactions(
    account_id: str,
    api_key: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch transaction history from Oanda.

    Parameters
    ----------
    account_id:
        Oanda account ID.
    api_key:
        Oanda API key.
    start:
        Optional start time in ISO 8601 format. ``YYYY-MM-DD`` is also accepted.
    end:
        Optional end time in ISO 8601 format. ``YYYY-MM-DD`` is also accepted.

    Returns
    -------
    list[dict[str, Any]]
        A list of transaction records.

    Raises
    ------
    OandaAPIError
        If the API call fails.
    """

    if requests is None:  # pragma: no cover - defensive check
        raise RuntimeError("The 'requests' package is required to fetch transactions")

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{API_URL}/accounts/{account_id}/transactions"
    params: Dict[str, str] = {}
    start = _normalize_date(start)
    end = _normalize_date(end)
    if start is not None:
        params["from"] = start
    if end is not None:
        params["to"] = end

    response = requests.get(url, headers=headers, params=params, timeout=10)
    if response.status_code != 200:
        raise OandaAPIError(
            f"Failed to fetch transactions: {response.status_code} {response.text}"
        )

    data = response.json()

    transactions: List[Dict[str, Any]] = []
    seen_ids: set[Any] = set()

    def append_unique(new_transactions: Optional[List[Dict[str, Any]]]) -> None:
        if not new_transactions:
            return
        for item in new_transactions:
            tx_id = item.get("id") if isinstance(item, dict) else None
            if tx_id is not None and tx_id in seen_ids:
                continue
            if tx_id is not None:
                seen_ids.add(tx_id)
            transactions.append(item)

    append_unique(data.get("transactions"))

    base_api_url = API_URL if API_URL.endswith("/") else API_URL + "/"
    page_queue: list[str] = []
    seen_pages: set[str] = set()

    def enqueue_url(page_url: Any) -> None:
        if not isinstance(page_url, str):
            return
        resolved_url = (
            page_url
            if page_url.startswith("http")
            else urljoin(base_api_url, page_url)
        )
        if resolved_url in seen_pages:
            return
        seen_pages.add(resolved_url)
        page_queue.append(resolved_url)

    def enqueue_pages(pages: Any) -> None:
        """Queue pagination URLs described in *pages*."""

        if isinstance(pages, dict):
            values = pages.values()
        elif isinstance(pages, list):
            values = pages
        elif isinstance(pages, str):
            values = [pages]
        else:
            return

        for page_url in values:
            enqueue_url(page_url)

    def enqueue_link_headers(response_obj: Any) -> None:
        links = getattr(response_obj, "links", None)
        if isinstance(links, dict):
            link_values = links.values()
        elif isinstance(links, list):
            link_values = links
        else:
            link_values = []

        for link_data in link_values:
            if isinstance(link_data, dict):
                enqueue_url(
                    link_data.get("url")
                    or link_data.get("uri")
                    or link_data.get("href")
                )
            else:
                enqueue_url(link_data)

        headers = getattr(response_obj, "headers", None)
        link_header: Any = None
        if headers is not None:
            getter = getattr(headers, "get", None)
            if callable(getter):
                link_header = getter("Link") or getter("link")
            elif isinstance(headers, dict):  # pragma: no cover - defensive
                link_header = headers.get("Link") or headers.get("link")

        if isinstance(link_header, str):
            for part in link_header.split(","):
                match = re.search(r"<([^>]+)>", part)
                if match:
                    enqueue_url(match.group(1).strip())

    enqueue_pages(data.get("pages"))
    enqueue_link_headers(response)

    while page_queue:
        resolved_url = page_queue.pop(0)
        page_response = requests.get(resolved_url, headers=headers, timeout=10)
        if page_response.status_code != 200:
            raise OandaAPIError(
                "Failed to fetch transactions page: "
                f"{page_response.status_code} {page_response.text}"
            )
        page_data = page_response.json()
        append_unique(page_data.get("transactions"))
        enqueue_pages(page_data.get("pages"))
        enqueue_link_headers(page_response)

    return transactions


def save_to_csv(transactions: List[Dict[str, Any]], filename: Path) -> None:
    """Save transaction records to *filename* as CSV."""

    rows = [_transaction_to_row(item) for item in transactions]

    with filename.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _transaction_to_row(transaction: Dict[str, Any]) -> Dict[str, str]:
    """Convert a raw transaction dictionary to the Excel-friendly format."""

    units_raw = _safe_str(transaction.get("units"))
    if not units_raw:
        units_raw = _find_first_value(
            transaction,
            ("tradeOpened", "units"),
            ("tradeReduced", "units"),
        )

    direction = _direction_from_units(units_raw)
    units = _format_units(units_raw)

    stop_loss, guaranteed_stop = _split_stop_loss(transaction)

    trailing_stop = _find_first_value(
        transaction,
        ("trailingStopLossOnFill", "distance"),
        ("trailingStopLossOnFill", "price"),
    )

    gsl_fee = _find_first_value(transaction, ("guaranteedExecutionFee",))
    gsl_premium = _find_first_value(
        transaction,
        ("guaranteedExecutionPremium",),
        ("tradeOpened", "guaranteedExecutionFee"),
        ("tradeReduced", "guaranteedExecutionFee"),
    )

    conversion_rate = _find_first_value(
        transaction,
        ("homeConversionFactors", "gain"),
        ("homeConversionFactors", "loss"),
        ("plHomeConversionFactors", "gain"),
        ("plHomeConversionFactors", "loss"),
    )

    price = _find_first_value(
        transaction,
        ("price",),
        ("fullPrice", "closeoutBid"),
        ("fullPrice", "closeoutAsk"),
    )

    details = _find_first_value(
        transaction,
        ("reason",),
        ("orderType",),
        ("clientOrderID",),
    )

    row = {
        "TICKET": _safe_str(transaction.get("id")),
        "TRANSACTION DATE": _format_timestamp(transaction.get("time")),
        "TRANSACTION TYPE": _safe_str(transaction.get("type")),
        "DETAILS": _safe_str(details),
        "INSTRUMENT": _safe_str(transaction.get("instrument")),
        "PRICE": _format_decimal(price, places=5),
        "UNITS": units,
        "DIRECTION": direction,
        "SPREAD COST": _format_decimal(
            _find_first_value(transaction, ("halfSpreadCost",)), places=4
        ),
        "STOP LOSS": _format_decimal(stop_loss, places=5),
        "GUARANTEED STOP LOSS": _format_decimal(guaranteed_stop, places=5),
        "TAKE PROFIT": _format_decimal(
            _find_first_value(
                transaction,
                ("takeProfitOnFill", "price"),
                ("tradeOpened", "takeProfitOrder", "price"),
            ),
            places=5,
        ),
        "TRAILING STOP": _format_decimal(trailing_stop, places=5),
        "FINANCING": _format_decimal(
            _find_first_value(transaction, ("financing",)), places=5
        ),
        "COMMISSION": _format_decimal(
            _find_first_value(transaction, ("commission",)), places=4
        ),
        "GSL FEE": _format_decimal(gsl_fee, places=4),
        "GSL PREMIUM": _format_decimal(gsl_premium, places=4),
        "CONVERSION RATE": _format_decimal(conversion_rate, places=4),
        "PL": _format_decimal(_find_first_value(transaction, ("pl",)), places=5),
        "AMOUNT": _format_decimal(
            _find_first_value(transaction, ("amount",)), places=2
        ),
        "BALANCE": _format_decimal(
            _find_first_value(transaction, ("accountBalance",)), places=2
        ),
    }

    return {key: value or "" for key, value in row.items()}


def _split_stop_loss(transaction: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the stop loss and guaranteed stop loss values."""

    details = _find_first_dict(
        transaction,
        ("stopLossOnFill",),
        ("stopLoss",),
        ("stopLossOrder",),
    )
    if not isinstance(details, dict):
        return None, None

    price = details.get("price") or details.get("priceBound")
    if details.get("guaranteed"):
        return None, price
    return price, None


def _find_first_dict(data: Dict[str, Any], *paths: tuple[str, ...]) -> Any:
    """Return the first nested dictionary located using *paths*."""

    for path in paths:
        value = data
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break
        if isinstance(value, dict):
            return value
    return None


def _find_first_value(data: Dict[str, Any], *paths: tuple[str, ...]) -> Any:
    """Return the first non-empty value found by following *paths*."""

    for path in paths:
        value: Any = data
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and isinstance(key, int):
                if 0 <= key < len(value):
                    value = value[key]
                else:
                    value = None
                    break
            else:
                value = None
                break
        if value not in (None, ""):
            return value
    return None


def _direction_from_units(units: str | None) -> str:
    """Return "Buy" or "Sell" based on the sign of *units*."""

    if units is None or units == "":
        return ""
    try:
        value = Decimal(str(units))
    except InvalidOperation:
        return ""
    if value > 0:
        return "Buy"
    if value < 0:
        return "Sell"
    return ""


def _format_units(units: str | None) -> str:
    """Format the units field to two decimal places."""

    if units is None or units == "":
        return ""
    try:
        value = Decimal(str(units))
    except InvalidOperation:
        return _safe_str(units)
    return f"{abs(value):.2f}"


def _format_decimal(value: Any, *, places: int) -> str:
    """Format *value* with the specified number of decimal places."""

    if value in (None, ""):
        return ""
    if isinstance(value, str) and value.strip() == "":
        return ""
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation:
        return _safe_str(value)

    exponent = Decimal("1").scaleb(-places)
    quantized = decimal_value.quantize(exponent, rounding=ROUND_HALF_UP)
    return f"{quantized:f}"


def _safe_str(value: Any) -> str:
    """Convert *value* to a string, returning an empty string for falsy values."""

    if value in (None, ""):
        return ""
    return str(value)


def _format_timestamp(value: str | None) -> str:
    """Convert an ISO 8601 timestamp to the configured timezone."""

    if value in (None, ""):
        return ""
    try:
        source = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _safe_str(value)

    tz_name = os.getenv("OANDA_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        target_zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        target_zone = ZoneInfo("UTC")

    localized = source.astimezone(target_zone)
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def main(argv: List[str] | None = None) -> int:
    """Entry point for the command line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        help="Start time (YYYY-MM-DD, default: all history)",
    )
    parser.add_argument(
        "--end",
        help="End time (YYYY-MM-DD, default: all history)",
    )
    parser.add_argument(
        "--output",
        help="Output CSV file",
        default="oanda_history.csv",
    )

    args = parser.parse_args(argv)

    if (
        args.start is None
        and args.end is None
        and sys.stdin.isatty()
    ):
        print("No time range specified.")
        choice = input("Fetch full transaction history? (y/N): ").strip().lower()
        if not choice.startswith("y"):
            start = input("Enter start time (YYYY-MM-DD, blank for none): ").strip()
            end = input("Enter end time (YYYY-MM-DD, blank for none): ").strip()
            args.start = start or None
            args.end = end or None

    account_id = _get_env_var("OANDA_ACCOUNT_ID")
    api_key = _get_env_var("OANDA_API_KEY")

    try:
        transactions = fetch_transactions(
            account_id=account_id,
            api_key=api_key,
            start=args.start,
            end=args.end,
        )
    except OandaAPIError as exc:
        print(exc, file=sys.stderr)
        return 1

    save_to_csv(transactions, Path(args.output))
    print(f"Saved {len(transactions)} transactions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
