"""Fetch CoinSpot history and save it as CSV files."""
from __future__ import annotations

import csv
from datetime import datetime
from typing import Any, Dict, List, Tuple
import os

try:
    from coinspot import ReadOnlyAPIV2
except ImportError as exc:
    print("The 'coinspot' package is required. Install it with 'pip install coinspot'.")
    raise SystemExit(1) from exc

API_KEY = os.getenv("COINSPOT_API_KEY")
API_SECRET = os.getenv("COINSPOT_API_SECRET")

if not API_KEY or not API_SECRET:
    raise SystemExit("Missing CoinSpot credentials. Set COINSPOT_API_KEY and COINSPOT_API_SECRET environment variables. See README for details.")

def _parse_date(text: str) -> str | None:
    """Return ``text`` if it's a valid YYYY-MM-DD date, otherwise ``None``."""

    if not text:
        return None

    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        print("Invalid date format. Use YYYY-MM-DD.")
        return None
    return text


def ask_period() -> Tuple[str | None, str | None]:
    """Prompt for a full history or a start and end date."""

    choice = input("Do you want your full history? [Y/n]: ").strip().lower()
    if choice.startswith("n"):
        start = _parse_date(input("Start date (YYYY-MM-DD): ").strip())
        end = _parse_date(input("End date (YYYY-MM-DD): ").strip())
        return start, end
    return None, None


def fetch_history(start_date: str | None, end_date: str | None) -> Dict[str, List[Dict[str, Any]]]:
    """Collect deposit, withdrawal, order, and transaction history."""

    api = ReadOnlyAPIV2(API_KEY, API_SECRET)
    deposits = api.deposit_history(start_date, end_date).get("deposits", [])
    withdrawals = api.withdrawal_history(start_date, end_date).get("withdrawals", [])
    orders_resp = api.order_history(start_date=start_date, end_date=end_date, limit=500)
    orders = orders_resp.get("buyorders", []) + orders_resp.get("sellorders", [])
    market_resp = api.market_order_history(start_date=start_date, end_date=end_date, limit=500)
    market_orders = market_resp.get("buyorders", []) + market_resp.get("sellorders", [])
    sr_resp = api.send_receive_history(start_date, end_date)
    send_receive = sr_resp.get("sendtransactions", []) + sr_resp.get("receivetransactions", [])

    history: Dict[str, List[Dict[str, Any]]] = {
        "deposits": deposits,
        "withdrawals": withdrawals,
        "orders": orders,
        "market_orders": market_orders,
        "send_receive": send_receive,
    }
    return history


def save_csv(history: Dict[str, List[Dict[str, Any]]]) -> None:
    """Write each history list to a CSV file."""

    for name, items in history.items():
        if not items:
            print(f"No data for {name}.")
            continue

        fieldnames = sorted({key for item in items for key in item})
        file_name = f"{name}.csv"
        with open(file_name, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in items:
                writer.writerow({fn: row.get(fn, "") for fn in fieldnames})
        print(f"Saved {file_name} with {len(items)} records.")


def main() -> None:
    """Get history based on user input and save it as CSV."""

    start, end = ask_period()
    data = fetch_history(start, end)
    save_csv(data)


if __name__ == "__main__":
    main()
