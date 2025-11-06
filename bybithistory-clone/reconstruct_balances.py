"""Rebuild daily USDT balances from Bybit trade history exports.

The balance export that ships with the sample data shows the same value for
every day.  This utility works backwards from the current balance and applies
the profit, loss, fees, funding entries and direct cash movements that appear
in the trade history or transaction log to recreate a realistic equity curve.

Example
-------
```
python reconstruct_balances.py \
    --trade-file Bybit-UM-USDTPerp-TradeHistory-1688688000-1751760000.csv \
    --ledger-file usdt_transaction_log.csv \
    --balance-file 'usdt_balance_history (2).csv' \
    --current-balance 238.821415 \
    --output corrected_balance_history.csv
```

The script understands both the raw API CSV layout and the template layout
exported from the Bybit website.  It also accepts multiple ``--trade-file`` and
``--ledger-file`` arguments in case the history spans more than one file.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


# ---------------------------------------------------------------------------
# Data structures and helpers


@dataclass
class TradeEvent:
    """Normalized representation of a trade, funding or fee event."""

    timestamp: datetime
    side: str
    event_type: str
    quantity: float
    price: float
    fee: float
    realised_pnl: float | None = None


@dataclass
class CashEvent:
    """Direct cash flow that affects the wallet balance."""

    timestamp: datetime
    amount: float
    source: str = ""


CSV_TIMESTAMP_KEYS = (
    "execTime",
    "Transaction Time(UTC+0)",
    "Transaction Time(UTC+10)",
)

CSV_SIDE_KEYS = ("side", "Direction")
CSV_TYPE_KEYS = ("execType", "Filled Type")
CSV_QTY_KEYS = ("execQty", "Filled Qty")
CSV_PRICE_KEYS = ("execPrice", "Filled Price")
CSV_FEE_KEYS = ("execFee", "Fees Paid")
CSV_REALIZED_KEYS = (
    "closedPnl",
    "realisedPnl",
    "Realized P&L",
    "Realised P&L",
    "Closed P&L",
)

LEDGER_TIMESTAMP_KEYS = (
    "Transaction Time(UTC+0)",
    "Transaction Time(UTC+8)",
    "Time(UTC+0)",
    "Created Time",
    "Created Time(UTC+0)",
    "Time",
)

LEDGER_AMOUNT_KEYS = (
    "Change",
    "Change Amount",
    "Amount",
    "Quantity",
    "Wallet Balance Change",
)


def _coerce_float(value: str | float | int | None) -> float:
    """Convert Bybit CSV values to a float, treating blanks as zero."""

    if value is None:
        return 0.0
    if isinstance(value, (float, int)):
        return float(value)
    value = str(value).strip()
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Cannot convert {value!r} to float") from exc


def _coerce_optional_float(value: str | float | int | None) -> float | None:
    """Return ``None`` when ``value`` is blank, otherwise a float."""

    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Cannot convert {value!r} to float") from exc


def _normalise_header_key(name: str) -> str:
    """Return a simplified key for case-insensitive CSV header matching."""

    return "".join(ch for ch in name.lower() if ch.isalnum())


def _pick_value(row: Dict[str, str], keys: Sequence[str]) -> str:
    """Return the first non-empty value from the candidate keys."""

    for key in keys:
        if key in row and str(row[key]).strip():
            return str(row[key]).strip()

    # Fall back to case-insensitive matching when the CSV headers differ in
    # capitalisation or contain extra punctuation/spaces.
    normalised_items = None
    for key in keys:
        if normalised_items is None:
            normalised_items = [
                (existing, _normalise_header_key(existing)) for existing in row.keys()
            ]

        key_norm = _normalise_header_key(key)
        for existing, existing_norm in normalised_items:
            if not existing_norm:
                continue
            matches_exact = existing_norm == key_norm
            matches_prefix = (
                existing_norm.startswith(key_norm)
                or key_norm.startswith(existing_norm)
            )
            if matches_exact or matches_prefix:
                value = str(row[existing]).strip()
                if value:
                    return value

    return ""


def _parse_timestamp(row: Dict[str, str], keys: Sequence[str]) -> datetime | None:
    """Parse the timestamp column from a CSV row."""

    formats = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%H:%M %Y-%m-%d",
    )
    for key in keys:
        raw = _pick_value(row, (key,))
        if not raw:
            continue
        text = raw.strip()
        # Handle trailing timezone 'Z'.
        if text.endswith("Z"):
            text = text[:-1]
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            for fmt in formats:
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return None


def _normalise_side(value: str) -> str:
    """Return BUY or SELL for the given column value."""

    value = value.strip().upper()
    if value not in {"BUY", "SELL"}:
        return ""
    return value


def load_trade_events(csv_path: Path) -> List[TradeEvent]:
    """Read a Bybit trade CSV file into a list of :class:`TradeEvent`."""

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        records: List[Tuple[datetime, TradeEvent]] = []
        for row in reader:
            timestamp = _parse_timestamp(row, CSV_TIMESTAMP_KEYS)
            if timestamp is None:
                continue
            side = _normalise_side(_pick_value(row, CSV_SIDE_KEYS))
            event_type = _pick_value(row, CSV_TYPE_KEYS).strip().title()
            quantity = _coerce_float(_pick_value(row, CSV_QTY_KEYS))
            price = _coerce_float(_pick_value(row, CSV_PRICE_KEYS))
            fee = _coerce_float(_pick_value(row, CSV_FEE_KEYS))
            realised = _coerce_optional_float(_pick_value(row, CSV_REALIZED_KEYS))
            records.append(
                (
                    timestamp,
                    TradeEvent(
                        timestamp=timestamp,
                        side=side,
                        event_type=event_type,
                        quantity=quantity,
                        price=price,
                        fee=fee,
                        realised_pnl=realised,
                    ),
                )
            )
    records.sort(key=lambda item: item[0])
    return [record for _, record in records]


def load_cashflow_events(csv_path: Path) -> List[CashEvent]:
    """Read transaction log style CSV files into :class:`CashEvent` rows."""

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        entries: List[Tuple[datetime, CashEvent]] = []
        for row in reader:
            timestamp = _parse_timestamp(row, LEDGER_TIMESTAMP_KEYS)
            if timestamp is None:
                continue
            amount_value = _coerce_optional_float(_pick_value(row, LEDGER_AMOUNT_KEYS))
            if amount_value is None:
                continue
            source = row.get("Type", "") or row.get("Category", "") or ""
            entries.append((timestamp, CashEvent(timestamp, amount_value, source.strip())))
    entries.sort(key=lambda item: item[0])
    return [entry for _, entry in entries]


# ---------------------------------------------------------------------------
# Cash-flow reconstruction


def _close_position(
    lots: deque[Tuple[float, float]],
    quantity: float,
    exit_price: float,
    pnl_calc,
) -> Tuple[float, float]:
    """Consume lots up to ``quantity`` and return realised PnL and leftover."""

    realised = 0.0
    remaining = quantity
    while remaining > 0 and lots:
        lot_qty, lot_price = lots[0]
        close_qty = min(remaining, lot_qty)
        realised += pnl_calc(exit_price, lot_price, close_qty)
        lot_qty -= close_qty
        remaining -= close_qty
        if lot_qty == 0:
            lots.popleft()
        else:
            lots[0] = (lot_qty, lot_price)
    return realised, remaining


def calculate_daily_cashflows(
    events: Sequence[TradeEvent], cash_events: Iterable[CashEvent] | None = None
) -> Dict[date, float]:
    """Return a mapping of ``date`` -> ``net USDT change``."""

    long_lots: deque[Tuple[float, float]] = deque()
    short_lots: deque[Tuple[float, float]] = deque()
    daily_changes: Dict[date, float] = defaultdict(float)

    for event in events:
        realised = 0.0
        funding = 0.0
        fees = 0.0

        event_type_lower = event.event_type.lower()
        if "fund" in event_type_lower:
            funding = event.fee
        else:
            qty = abs(event.quantity)
            realised_manual = 0.0

            if event.side == "BUY":
                realised_manual, leftover = _close_position(
                    short_lots,
                    qty,
                    event.price,
                    lambda exit_price, entry_price, amount: (
                        (entry_price - exit_price) * amount
                    ),
                )
                if leftover > 0:
                    long_lots.append((leftover, event.price))
            elif event.side == "SELL":
                realised_manual, leftover = _close_position(
                    long_lots,
                    qty,
                    event.price,
                    lambda exit_price, entry_price, amount: (
                        (exit_price - entry_price) * amount
                    ),
                )
                if leftover > 0:
                    short_lots.append((leftover, event.price))

            if event.realised_pnl is not None:
                realised = event.realised_pnl
            else:
                realised = realised_manual

            fees = event.fee

        net_change = realised + funding + fees
        daily_changes[event.timestamp.date()] += net_change

    if cash_events:
        for cash in cash_events:
            daily_changes[cash.timestamp.date()] += cash.amount

    return daily_changes


def infer_date_bounds(
    events: Sequence[TradeEvent], cash_events: Iterable[CashEvent] | None = None
) -> Tuple[date, date]:
    """Return the earliest and latest dates present in the event history."""

    dates = [event.timestamp.date() for event in events]
    if cash_events:
        dates.extend(cash.timestamp.date() for cash in cash_events)
    if not dates:
        raise ValueError("Cannot infer date range without dated trade or cash events")
    return min(dates), max(dates)


def reconstruct_balances(
    daily_changes: Dict[date, float],
    final_balance: float,
    start_date: date,
    end_date: date,
) -> List[Tuple[date, float]]:
    """Build end-of-day balances for the inclusive date range."""

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)

    balances: Dict[date, float] = {days[-1]: final_balance}
    for later, earlier in zip(reversed(days[1:]), reversed(days[:-1])):
        change = daily_changes.get(later, 0.0)
        balances[earlier] = balances[later] - change

    return [(day, balances.get(day, final_balance)) for day in days]


# ---------------------------------------------------------------------------
# CSV helpers and command line interface


def _read_balance_dates(balance_file: Path) -> List[date]:
    """Extract the ordered list of dates from an existing CSV."""

    with balance_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        periods: List[date] = []
        for row in reader:
            raw = row.get("Period", "").strip()
            if not raw:
                continue
            periods.append(datetime.strptime(raw, "%Y-%m-%d").date())
    if not periods:
        raise ValueError("Balance file does not contain any Period values")
    return periods


def _read_last_balance(balance_file: Path) -> float:
    """Return the final balance from an existing CSV."""

    last_value: float | None = None
    with balance_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = row.get("Balance", "").strip()
            if raw:
                last_value = float(raw)
    if last_value is None:
        raise ValueError("Could not determine current balance from file")
    return last_value


def _write_balances_csv(rows: Sequence[Tuple[date, float]], output: Path) -> None:
    """Write ``Period``/``Balance`` rows to ``output``."""

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Period", "Balance"])
        for day, value in rows:
            writer.writerow([day.strftime("%Y-%m-%d"), f"{value:.9f}"])


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Create and parse the command line options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trade-file",
        action="append",
        dest="trade_files",
        help="Path to a Bybit trade history CSV file.  Can be provided multiple times",
    )
    parser.add_argument(
        "--ledger-file",
        action="append",
        dest="ledger_files",
        help="Optional transaction log CSV containing deposits, withdrawals, etc.",
    )
    parser.add_argument(
        "--balance-file",
        type=Path,
        help="Existing balance CSV containing the date range to rebuild",
    )
    parser.add_argument(
        "--start-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        help="Start date (inclusive) when not using --balance-file",
    )
    parser.add_argument(
        "--end-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        help="End date (inclusive) when not using --balance-file",
    )
    parser.add_argument(
        "--current-balance",
        type=float,
        help="Wallet balance on the end date.  Defaults to the last value in --balance-file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reconstructed_balance_history.csv"),
        help="Where to save the rebuilt balance history",
    )
    options = parser.parse_args(argv)
    if not options.trade_files and not options.ledger_files:
        parser.error("Provide at least one --trade-file or --ledger-file")
    return options


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""

    options = parse_arguments(argv)
    trade_paths = [Path(path) for path in options.trade_files or []]
    events: List[TradeEvent] = []
    for path in trade_paths:
        events.extend(load_trade_events(path))

    ledger_paths = [Path(path) for path in options.ledger_files or []]
    cash_events: List[CashEvent] = []
    for path in ledger_paths:
        cash_events.extend(load_cashflow_events(path))

    if not events and not cash_events:
        raise ValueError("No trade or ledger events were loaded")

    daily_changes = calculate_daily_cashflows(events, cash_events)

    if options.balance_file:
        periods = _read_balance_dates(options.balance_file)
        start_date = periods[0]
        end_date = periods[-1]
        final_balance = (
            options.current_balance
            if options.current_balance is not None
            else _read_last_balance(options.balance_file)
        )
    else:
        start_date = options.start_date
        end_date = options.end_date
        if start_date is None or end_date is None:
            inferred_start, inferred_end = infer_date_bounds(events, cash_events)
            if start_date is None:
                start_date = inferred_start
            if end_date is None:
                end_date = inferred_end
        if start_date is None or end_date is None:
            raise ValueError(
                "start-date and end-date are required when balance-file is omitted"
            )
        if options.current_balance is None:
            raise ValueError("current-balance is required when balance-file is omitted")
        final_balance = options.current_balance

    if end_date < start_date:
        raise ValueError("end-date must not be earlier than start-date")

    rows = reconstruct_balances(daily_changes, final_balance, start_date, end_date)
    _write_balances_csv(rows, options.output)


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    main()
