"""Tests for fetch_history module."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import csv
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import MagicMock, patch

import fetch_history


def test_parse_date():
    """Verify date parsing."""
    assert fetch_history._parse_date("1970-01-01") == 0


def test_parse_date_pre_epoch():
    """Ensure dates before 1970 are handled on all platforms."""
    assert fetch_history._parse_date("1969-12-31") == -86400000


def test_download_history_env_missing(monkeypatch):
    """Ensure error when env vars are missing."""
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    with pytest.raises(EnvironmentError):
        fetch_history.download_history("linear")


def test_download_history_calls_api(monkeypatch):
    """Check API call parameters."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))

    with (
        patch.object(fetch_history, "_fetch_pages", return_value=[[]]) as mock_pages,
        patch.object(fetch_history, "_write_csv") as mock_write,
    ):
        name = fetch_history.download_history("linear", "2023-01-01", "2023-01-02", "BTCUSDT")

    assert mock_pages.called
    params = mock_pages.call_args.kwargs
    assert params["category"] == "linear"
    assert params["symbol"] == "BTCUSDT"
    assert "startTime" in params and "endTime" in params
    assert name is None
    mock_write.assert_not_called()


def test_download_history_reports_empty(monkeypatch, capsys):
    """A friendly message is shown when no transactions are returned."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(fetch_history, "_fetch_pages", lambda *args, **kwargs: [[]])
    mock_write = MagicMock()
    monkeypatch.setattr(fetch_history, "_write_csv", mock_write)

    name = fetch_history.download_history("linear", "2023-01-01", "2023-01-02")

    captured = capsys.readouterr()
    assert "No transactions found" in captured.out
    assert name is None
    mock_write.assert_not_called()


def test_download_history_chunking(monkeypatch):
    """Ensure large date ranges are split into multiple requests."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))

    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    with patch.object(fetch_history, "_fetch_pages", return_value=[[]]) as mock_pages:
        fetch_history.download_history("linear", "2023-01-01", "2023-01-15")

    # Expect three chunks covering the full range
    assert mock_pages.call_count == 3
    first_call = mock_pages.call_args_list[0].kwargs
    second_call = mock_pages.call_args_list[1].kwargs
    assert first_call["startTime"] < second_call["startTime"]


def test_download_history_limits_dates(monkeypatch):
    """Start date earlier than two years is clipped."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))

    fixed_now = datetime(2025, 7, 6, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    with patch.object(fetch_history, "_fetch_pages", return_value=[[]]) as mock_pages:
        fetch_history.download_history("linear", "2020-01-01", "2025-07-05")

    params = mock_pages.call_args_list[0].kwargs
    earliest = int((fixed_now - timedelta(days=730)).timestamp() * 1000) + 60000
    assert params["startTime"] == earliest


def test_exec_time_formatted(monkeypatch):
    """Ensure execTime is converted to a readable timestamp."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock())

    page = [{"execTime": 0}]

    monkeypatch.setattr(fetch_history, "_fetch_pages", lambda *args, **_: [page])

    captured: list[dict[str, Any]] = []

    def fake_write_csv(filename: str, rows: list[dict[str, Any]]) -> None:
        captured.extend(rows)

    monkeypatch.setattr(fetch_history, "_write_csv", fake_write_csv)

    name = fetch_history.download_history(
        "linear", "2023-01-01", "2023-01-02", template=False
    )

    assert captured[0]["execTime"] == "1970-01-01T10:00:00"


def test_exec_time_template_format(monkeypatch):
    """Ensure template flag formats time like the sample CSV."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock())

    page = [{"execTime": 0}]

    monkeypatch.setattr(fetch_history, "_fetch_pages", lambda *args, **_: [page])

    captured: list[dict[str, Any]] = []

    def fake_write_csv(filename: str, rows: list[dict[str, Any]]) -> None:
        captured.extend(rows)

    monkeypatch.setattr(fetch_history, "_write_csv", fake_write_csv)

    fetch_history.download_history("linear", "2023-01-01", "2023-01-02", template=True)

    assert captured[0]["Transaction Time(UTC+10)"] == "10:00 1970-01-01"


def test_side_and_order_type_format(monkeypatch):
    """Side is uppercased and UNKNOWN order type becomes '--'."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock())

    page = [{"execTime": 0, "side": "Buy", "orderType": "UNKNOWN"}]

    monkeypatch.setattr(fetch_history, "_fetch_pages", lambda *a, **k: [page])

    captured: list[dict[str, Any]] = []

    def fake_write_csv(filename: str, rows: list[dict[str, Any]]) -> None:
        captured.extend(rows)

    monkeypatch.setattr(fetch_history, "_write_csv", fake_write_csv)

    fetch_history.download_history("linear", "2023-01-01", "2023-01-02", template=True)

    assert captured[0]["Direction"] == "BUY"
    assert captured[0]["Order Type"] == "--"


def test_filename_format(monkeypatch):
    """Ensure CSV filename matches Bybit style."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock())

    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)
    monkeypatch.setattr(
        fetch_history,
        "_fetch_pages",
        lambda *args, **_: [[{"execTime": 0}]],
    )

    captured: dict[str, str] = {}

    def fake_write_csv(filename: str, rows: list[dict[str, Any]]) -> None:  # type: ignore
        captured["name"] = filename

    monkeypatch.setattr(fetch_history, "_write_csv", fake_write_csv)

    name = fetch_history.download_history("linear", "2023-01-01", "2023-01-02")

    expected = "Bybit-UM-USDTPerp-TradeHistory-1672531200-1672617600.csv"
    assert captured["name"] == expected
    assert name == expected


def test_export_balance_history(monkeypatch):
    """Ensure balance history is written to XLSX."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=MagicMock()))

    logs = [
        {
            "transactionTime": 0,
            "cashBalance": "100",
            "walletBalance": "80",
            "change": "10",
            "coin": "USDT",
        },
        {
            "transactionTime": 1,
            "cashBalance": "110",
            "walletBalance": "120",
            "change": "10",
            "coin": "USDT",
        },
    ]

    def fake_pages(session, **params):
        if getattr(fake_pages, "called", False):
            yield []
        else:
            fake_pages.called = True
            yield logs
    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", fake_pages)
    monkeypatch.setattr(fetch_history, "_get_balance_before", lambda *a, **k: 90)

    saved: dict[str, Any] = {}

    class FakeWs:
        def __init__(self) -> None:
            self.rows: list[list[Any]] = []

        def append(self, row: list[Any]) -> None:
            self.rows.append(row)

    class FakeWb:
        def __init__(self) -> None:
            self.active = FakeWs()

        def save(self, name: str) -> None:
            saved["name"] = name
            saved["rows"] = self.active.rows

    monkeypatch.setattr(fetch_history, "Workbook", FakeWb)

    fetch_history.export_balance_history(months=1)

    assert saved["name"] == "usdt_balance_history.xlsx"
    assert saved["rows"][1][1:] == [90.0, 110.0]


def test_export_balance_history_no_logs(monkeypatch):
    """Use previous balance when no logs exist for the month."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", lambda *a, **k: [])
    monkeypatch.setattr(fetch_history, "_get_balance_before", lambda *a, **k: 50)

    saved: dict[str, Any] = {}

    class FakeWs:
        def __init__(self) -> None:
            self.rows: list[list[Any]] = []

        def append(self, row: list[Any]) -> None:
            self.rows.append(row)

    class FakeWb:
        def __init__(self) -> None:
            self.active = FakeWs()

        def save(self, name: str) -> None:
            saved["name"] = name
            saved["rows"] = self.active.rows

    monkeypatch.setattr(fetch_history, "Workbook", FakeWb)

    fetch_history.export_balance_history(months=1)

    assert saved["rows"][1][1:] == [50, 50]


def test_export_balance_history_fallback_cash_balance(monkeypatch):
    """When no previous balance exists, cashBalance is used."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))
    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", lambda *a, **k: [])
    monkeypatch.setattr(fetch_history, "_get_balance_before", lambda *a, **k: None)
    mock_session.get_wallet_balance.return_value = {
        "result": {
            "list": [
                {
                    "coin": [
                        {
                            "coin": "USDT",
                            "walletBalance": "1",
                            "cashBalance": "2",
                        }
                    ]
                }
            ]
        }
    }

    saved: dict[str, Any] = {}

    class FakeWs:
        def __init__(self) -> None:
            self.rows: list[list[Any]] = []

        def append(self, row: list[Any]) -> None:
            self.rows.append(row)

    class FakeWb:
        def __init__(self) -> None:
            self.active = FakeWs()

        def save(self, name: str) -> None:
            saved["rows"] = self.active.rows

    monkeypatch.setattr(fetch_history, "Workbook", FakeWb)

    fetch_history.export_balance_history(months=1)

    assert saved["rows"][1][1:] == [2.0, 2.0]


def test_export_balance_history_month_limit(monkeypatch):
    """Ensure an error is raised when requesting more than 24 months."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    monkeypatch.setattr(fetch_history, "HTTP", MagicMock())
    monkeypatch.setattr(fetch_history, "Workbook", MagicMock())

    with pytest.raises(ValueError):
        fetch_history.export_balance_history(months=25)


def test_convert_csv_file(tmp_path):
    """Raw CSV is reformatted to template layout."""
    content = (
        "symbol,orderId,side,orderType,execQty,execPrice,orderPrice,execType,feeRate,execFee,execId,execTime\n"
        "BTCUSDT,abc,Buy,Market,1,10000,10000,Trade,0.01,0.1,eid,0\n"
    )
    raw = tmp_path / "raw.csv"
    raw.write_text(content)
    out = fetch_history.convert_csv_file(str(raw))
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["contracts"] == "BTCUSDT"
    assert rows[0]["Trading Fee Rate"] == "0.01"
    assert rows[0]["Transaction Time(UTC+10)"] == "10:00 1970-01-01"


def test_convert_csv_file_unknown(tmp_path):
    """Unknown order types become '--' and side uppercased."""
    content = (
        "symbol,orderId,side,orderType,execQty,execPrice,orderPrice,execType,feeRate,execFee,execId,execTime\n"
        "BTCUSDT,abc,Buy,UNKNOWN,1,10000,10000,Trade,0.01,0.1,eid,0\n"
    )
    raw = tmp_path / "raw2.csv"
    raw.write_text(content)
    out = fetch_history.convert_csv_file(str(raw))
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Direction"] == "BUY"
    assert rows[0]["Order Type"] == "--"


def test_get_balance_before_clips(monkeypatch):
    """Query does not go earlier than two years."""
    fixed_now = datetime(2025, 7, 6, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    mock_session = MagicMock()
    mock_session.get_wallet_balance.return_value = {
        "result": {
            "list": [
                {
                    "coin": [
                        {
                            "coin": "USDT",
                            "walletBalance": "42",
                            "cashBalance": "50",
                        }
                    ]
                }
            ]
        }
    }

    def fail_fetch(*args, **kwargs):  # type: ignore[unused-argument]
        raise AssertionError("fetch called")

    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", fail_fetch)

    timestamp = (
        int(FixedDateTime.now(timezone.utc).timestamp() * 1000)
        - fetch_history.TWO_YEARS_MS
        + fetch_history.LIMIT_CUSHION_MS
    )

    bal = fetch_history._get_balance_before(mock_session, timestamp)
    assert bal == 50.0


def test_get_balance_before_prefers_cash_balance(monkeypatch):
    """cashBalance is used when both balances are present."""
    fixed_now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    mock_session = MagicMock()
    mock_session.get_wallet_balance.side_effect = AssertionError("wallet called")
    logs = [
        {
            "transactionTime": 0,
            "walletBalance": "5",
            "cashBalance": "7",
            "coin": "USDT",
        }
    ]
    monkeypatch.setattr(
        fetch_history, "_fetch_transaction_pages", lambda *a, **k: [logs]
    )
    bal = fetch_history._get_balance_before(
        mock_session, int(fixed_now.timestamp() * 1000)
    )
    assert bal == 7.0


def test_get_balance_before_uses_equity(monkeypatch):
    """equity field is treated as a balance value."""
    fixed_now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    mock_session = MagicMock()
    mock_session.get_wallet_balance.side_effect = AssertionError("wallet called")

    logs = [{"coin": "USDT", "transactionTime": 0, "equity": "75"}]
    monkeypatch.setattr(
        fetch_history, "_fetch_transaction_pages", lambda *a, **k: [logs]
    )

    bal = fetch_history._get_balance_before(
        mock_session, int(fixed_now.timestamp() * 1000)
    )
    assert bal == 75.0


def test_get_balance_before_uses_usd_value(monkeypatch):
    """usdValue is treated as already converted to USDT."""
    fixed_now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    mock_session = MagicMock()
    mock_session.get_wallet_balance.side_effect = AssertionError("wallet called")

    logs = [{"coin": "BTC", "transactionTime": 0, "usdValue": "200"}]

    def fail_price(*args, **kwargs):  # type: ignore[unused-argument]
        raise AssertionError("price requested")

    monkeypatch.setattr(fetch_history, "_get_price", fail_price)
    monkeypatch.setattr(
        fetch_history, "_fetch_transaction_pages", lambda *a, **k: [logs]
    )

    bal = fetch_history._get_balance_before(
        mock_session, int(fixed_now.timestamp() * 1000)
    )
    assert bal == 200.0


def test_export_balance_csv_clips(monkeypatch):
    """export_balance_csv should not query before Bybit's two-year window."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    fixed_now = datetime(2025, 7, 6, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):  # type: ignore[override]
            return fixed_now

    import types
    import importlib
    fake_flask = types.ModuleType("flask")

    class DummyFlask:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def route(self, *args, **kwargs):  # type: ignore
            def decorator(func):
                return func

            return decorator

        post = route

    fake_flask.Flask = DummyFlask
    fake_flask.render_template_string = lambda *args, **kwargs: ""
    fake_flask.request = None
    fake_flask.send_file = lambda *args, **kwargs: None
    sys.modules.setdefault("flask", fake_flask)

    app = importlib.import_module("app")

    monkeypatch.setattr(app, "datetime", FixedDateTime)
    monkeypatch.setattr(fetch_history, "datetime", FixedDateTime)

    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))

    captured: list[dict[str, int]] = []

    def fake_pages(*args, **kwargs):
        captured.append(kwargs)
        return [[]]

    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", fake_pages)

    app.export_balance_csv("2023-07-05", "2023-07-05", "daily")

    earliest = (
        int(fixed_now.timestamp() * 1000)
        - fetch_history.TWO_YEARS_MS
        + fetch_history.LIMIT_CUSHION_MS
    )
    assert captured[0]["startTime"] == earliest


def test_export_balance_csv_uses_cash_balance(monkeypatch):
    """Fallback wallet balance prefers cashBalance."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    import types
    import importlib

    fake_flask = types.ModuleType("flask")

    class DummyFlask:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def route(self, *args, **kwargs):  # type: ignore
            def decorator(func):
                return func

            return decorator

        post = route

    fake_flask.Flask = DummyFlask
    fake_flask.render_template_string = lambda *args, **kwargs: ""
    fake_flask.request = None
    fake_flask.send_file = lambda *args, **kwargs: None
    sys.modules.setdefault("flask", fake_flask)

    app = importlib.import_module("app")

    mock_session = MagicMock()
    monkeypatch.setattr(fetch_history, "HTTP", MagicMock(return_value=mock_session))
    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", lambda *a, **k: [])
    monkeypatch.setattr(fetch_history, "_get_balance_before", lambda *a, **k: None)
    mock_session.get_wallet_balance.return_value = {
        "result": {
            "list": [
                {
                    "coin": [
                        {
                            "coin": "USDT",
                            "walletBalance": "3",
                            "cashBalance": "4",
                        }
                    ]
                }
            ]
        }
    }

    fname = app.export_balance_csv("2024-01-01", "2024-01-01", "daily")
    with open(fname, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Balance"] == "4.0"

def test_export_balance_csv_uses_cashflow(monkeypatch):
    """Transaction logs using cashFlow still update balances."""
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")

    import types
    import importlib

    fake_flask = types.ModuleType("flask")

    class DummyFlask:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def route(self, *args, **kwargs):  # type: ignore
            def decorator(func):
                return func

            return decorator

        post = route

    fake_flask.Flask = DummyFlask
    fake_flask.render_template_string = lambda *args, **kwargs: ""
    fake_flask.request = None
    fake_flask.send_file = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    app = importlib.import_module("app")
    fetch_history_mod = importlib.import_module("fetch_history")

    class DummySession:
        def get_wallet_balance(self, **kwargs):  # type: ignore[unused-argument]
            return {"result": {"list": [{"coin": []}]}}

    monkeypatch.setattr(
        fetch_history_mod, "HTTP", lambda api_key, api_secret: DummySession()
    )
    monkeypatch.setattr(fetch_history_mod, "_get_balance_before", lambda *a, **k: 10.0)

    log = {"coin": "USDT", "transactionTime": 0, "cashFlow": "5"}

    def fake_pages(*args, **kwargs):  # type: ignore[unused-argument]
        yield [log]

    monkeypatch.setattr(fetch_history_mod, "_fetch_transaction_pages", fake_pages)

    fname = app.export_balance_csv("1970-01-01", "1970-01-01", "daily")
    with open(fname, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "15.0"

