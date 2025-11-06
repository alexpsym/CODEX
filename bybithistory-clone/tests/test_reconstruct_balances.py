"""Tests for reconstruct_balances utility."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

import reconstruct_balances as rb


def _write_csv(tmp_path: Path, name: str, headers: list[str], rows: list[list[str]]) -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def test_reconstruct_from_raw_format(tmp_path: Path) -> None:
    """End-of-day balances are rebuilt from raw API CSV columns."""

    headers = [
        "execTime",
        "side",
        "execType",
        "execQty",
        "execPrice",
        "execFee",
    ]
    rows = [
        ["2024-01-01T00:00:00", "Sell", "Trade", "1", "100", "-0.1"],
        ["2024-01-02T00:00:00", "Buy", "Trade", "1", "90", "-0.1"],
        ["2024-01-03T00:00:00", "Buy", "Funding", "0", "0", "-0.05"],
    ]
    csv_path = _write_csv(tmp_path, "raw.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    daily = rb.calculate_daily_cashflows(events)
    balances = rb.reconstruct_balances(
        daily,
        final_balance=100.0,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 3),
    )

    assert balances == [
        (date(2024, 1, 1), pytest.approx(90.15, rel=1e-6)),
        (date(2024, 1, 2), pytest.approx(100.05, rel=1e-6)),
        (date(2024, 1, 3), pytest.approx(100.0, rel=1e-6)),
    ]


def test_trade_headers_match_case_insensitively(tmp_path: Path) -> None:
    """Trade CSVs with upper-case headers are still understood."""

    headers = [
        "EXEC TIME",
        "SIDE",
        "EXEC TYPE",
        "EXEC QTY",
        "EXEC PRICE",
        "EXEC FEE",
    ]
    rows = [
        ["2024-08-01T00:00:00", "SELL", "TRADE", "1", "100", "-0.1"],
        ["2024-08-02T00:00:00", "BUY", "TRADE", "1", "90", "-0.1"],
    ]
    csv_path = _write_csv(tmp_path, "uppercase.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    assert len(events) == 2

    daily = rb.calculate_daily_cashflows(events)
    assert daily[date(2024, 8, 1)] == pytest.approx(-0.1)
    assert daily[date(2024, 8, 2)] == pytest.approx(9.9)


def test_trade_headers_with_suffixes_match(tmp_path: Path) -> None:
    """Headers that include currency decorations are still recognised."""

    headers = [
        "execTime(UTC)",
        "side",
        "execType",
        "execQty",
        "execPrice(USDT)",
        "execFee(USDT)",
        "Realized P&L(USDT)",
    ]
    rows = [["2024-09-01T00:00:00", "Sell", "Trade", "1", "100", "-0.1", "5"]]
    csv_path = _write_csv(tmp_path, "suffixes.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    assert len(events) == 1

    daily = rb.calculate_daily_cashflows(events)
    assert daily[date(2024, 9, 1)] == pytest.approx(4.9)


def test_load_template_format(tmp_path: Path) -> None:
    """Template CSVs with "HH:MM YYYY-MM-DD" timestamps are understood."""

    headers = [
        "contracts",
        "Direction",
        "Filled Qty",
        "Filled Price",
        "Fees Paid",
        "Filled Type",
        "Transaction Time(UTC+0)",
    ]
    rows = [
        ["ABCUSDT", "BUY", "10", "2", "-0.02", "Trade", "12:00 2024-02-01"],
        ["ABCUSDT", "SELL", "5", "2.5", "-0.01", "Trade", "13:00 2024-02-01"],
        ["ABCUSDT", "SELL", "5", "2.8", "-0.01", "Trade", "14:00 2024-02-02"],
    ]
    csv_path = _write_csv(tmp_path, "template.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    assert len(events) == 3
    # Check the realised PnL is applied on the second day when the long closes.
    daily = rb.calculate_daily_cashflows(events)
    assert daily[date(2024, 2, 1)] == pytest.approx(2.47)
    assert daily[date(2024, 2, 2)] == pytest.approx(3.99)


def test_negative_quantities_are_normalised(tmp_path: Path) -> None:
    """SELL rows with negative quantities still contribute realised PnL."""

    headers = [
        "execTime",
        "side",
        "execType",
        "execQty",
        "execPrice",
        "execFee",
    ]
    rows = [
        ["2024-06-01T00:00:00", "Sell", "Trade", "-1", "100", "-0.05"],
        ["2024-06-02T00:00:00", "Buy", "Trade", "1", "90", "-0.05"],
    ]
    csv_path = _write_csv(tmp_path, "negative_qty.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    daily = rb.calculate_daily_cashflows(events)

    assert daily[date(2024, 6, 1)] == pytest.approx(-0.05)
    assert daily[date(2024, 6, 2)] == pytest.approx(9.95)

    balances = rb.reconstruct_balances(
        daily,
        final_balance=200.0,
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 2),
    )

    assert balances == [
        (date(2024, 6, 1), pytest.approx(190.05, rel=1e-6)),
        (date(2024, 6, 2), pytest.approx(200.0, rel=1e-6)),
    ]


def test_uses_realised_column_when_available(tmp_path: Path) -> None:
    """Realised PnL columns override the manual FIFO calculation."""

    headers = [
        "execTime",
        "side",
        "execType",
        "execQty",
        "execPrice",
        "execFee",
        "Realized P&L",
    ]
    rows = [
        ["2024-03-01T00:00:00", "Sell", "Trade", "1", "120", "-0.2", "5"],
    ]
    csv_path = _write_csv(tmp_path, "realised.csv", headers, rows)

    events = rb.load_trade_events(csv_path)
    daily = rb.calculate_daily_cashflows(events)
    assert daily[date(2024, 3, 1)] == pytest.approx(4.8)


def test_funding_rows_and_fees_have_correct_signs() -> None:
    """Funding entries adjust the balance using the recorded sign."""

    event = rb.TradeEvent(
        timestamp=rb.datetime(2024, 4, 1, 0, 0),
        side="",
        event_type="Funding Fee",
        quantity=0.0,
        price=0.0,
        fee=-0.75,
        realised_pnl=None,
    )
    daily = rb.calculate_daily_cashflows([event])
    assert daily[date(2024, 4, 1)] == pytest.approx(-0.75)


def test_load_cashflow_events(tmp_path: Path) -> None:
    """Transaction log style CSVs contribute to the daily balance."""

    headers = ["Time(UTC+0)", "Type", "Change"]
    rows = [
        ["2024-05-01 00:15:00", "Deposit", "100"],
        ["2024-05-02 12:30:00", "Withdrawal", "-25.5"],
    ]
    csv_path = _write_csv(tmp_path, "ledger.csv", headers, rows)

    ledger = rb.load_cashflow_events(csv_path)
    assert len(ledger) == 2

    events = [
        rb.TradeEvent(
            timestamp=rb.datetime(2024, 5, 2, 0, 0),
            side="SELL",
            event_type="Trade",
            quantity=1,
            price=110,
            fee=-0.1,
            realised_pnl=10.0,
        )
    ]
    daily = rb.calculate_daily_cashflows(events, ledger)
    assert daily[date(2024, 5, 1)] == pytest.approx(100.0)
    # Deposit 100, realised profit 10, fee -0.1, withdrawal -25.5
    assert daily[date(2024, 5, 2)] == pytest.approx(-15.6)


def test_cashflow_headers_with_suffixes_match(tmp_path: Path) -> None:
    """Ledger exports with currency suffixes still load correctly."""

    headers = ["Created Time", "Change Amount(USDT)"]
    rows = [["2024-07-01 00:00:00", "250"]]
    csv_path = _write_csv(tmp_path, "ledger_suffix.csv", headers, rows)

    ledger = rb.load_cashflow_events(csv_path)
    assert len(ledger) == 1
    assert ledger[0].amount == pytest.approx(250)


def test_infer_date_bounds_uses_all_events() -> None:
    """The inferred date range spans both trade and cash flow records."""

    events = [
        rb.TradeEvent(
            timestamp=rb.datetime(2024, 1, 5, 10, 0),
            side="BUY",
            event_type="Trade",
            quantity=1.0,
            price=100.0,
            fee=-0.1,
            realised_pnl=None,
        ),
        rb.TradeEvent(
            timestamp=rb.datetime(2024, 1, 7, 15, 30),
            side="SELL",
            event_type="Trade",
            quantity=1.0,
            price=105.0,
            fee=-0.1,
            realised_pnl=5.0,
        ),
    ]
    cash_events = [
        rb.CashEvent(timestamp=rb.datetime(2024, 1, 8, 9, 0), amount=-20.0, source="Withdraw"),
    ]

    start, end = rb.infer_date_bounds(events, cash_events)
    assert start == date(2024, 1, 5)
    assert end == date(2024, 1, 8)


def test_main_infers_dates_from_history(tmp_path: Path) -> None:
    """CLI infers the output range when no explicit dates are provided."""

    headers = ["Time(UTC+0)", "Type", "Change"]
    rows = [
        ["2024-02-01 00:00:00", "Deposit", "100"],
        ["2024-02-02 00:00:00", "Withdrawal", "-20"],
    ]
    ledger_csv = _write_csv(tmp_path, "history.csv", headers, rows)
    output_csv = tmp_path / "result.csv"

    rb.main(
        [
            "--ledger-file",
            str(ledger_csv),
            "--current-balance",
            "80",
            "--output",
            str(output_csv),
        ]
    )

    with output_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)

    assert rows == [
        ["Period", "Balance"],
        ["2024-02-01", "100.000000000"],
        ["2024-02-02", "80.000000000"],
    ]
