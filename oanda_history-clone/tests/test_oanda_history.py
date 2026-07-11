"""Tests for the oanda_history module."""

from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import List
import types

import os
import sys
import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
import oanda_history  # noqa: E402  # pylint: disable=wrong-import-position


class FakeResponse:  # pylint: disable=too-few-public-methods
    """A simple fake response object for testing."""

    def __init__(
        self,
        status_code: int,
        json_data: dict,
        *,
        links: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = json.dumps(json_data)
        self.links = links or {}
        self.headers = headers or {}

    def json(self) -> dict:
        """Return the fake JSON data."""
        return self._json_data


def test_fetch_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure fetch_transactions parses the API response correctly."""

    captured = {}

    def fake_get(url: str, *, headers: dict, params: dict, timeout: int) -> FakeResponse:
        captured['params'] = params
        return FakeResponse(200, {"transactions": [{"id": "1", "type": "TEST"}]})

    monkeypatch.setattr(
        oanda_history,
        "requests",
        types.SimpleNamespace(get=fake_get),
    )
    data = oanda_history.fetch_transactions("acc", "key", start="2024-01-01", end="2024-01-02")
    assert data == [{"id": "1", "type": "TEST"}]
    assert captured['params']["from"] == "2024-01-01T00:00:00Z"
    assert captured['params']["to"] == "2024-01-02T00:00:00Z"


def test_save_to_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure save_to_csv writes a CSV file with formatted fields."""

    monkeypatch.setenv("OANDA_TIMEZONE", "UTC")
    records = [
        {
            "id": "1",
            "time": "2024-01-01T10:00:00Z",
            "type": "ORDER_FILL",
            "reason": "MARKET_ORDER",
            "instrument": "EUR_USD",
            "price": "1.23456",
            "units": "2",
            "halfSpreadCost": "0.1234",
            "financing": "0.00001",
            "commission": "0.1234",
            "guaranteedExecutionFee": "0.0000",
            "homeConversionFactors": {"lossQuoteHome": {"factor": "1.1111"}},
            "pl": "1.00000",
            "accountBalance": "100.12",
        },
        {
            "id": "2",
            "time": "2024-01-01T11:00:00Z",
            "type": "MARKET_ORDER",
            "reason": "CLIENT_ORDER",
            "instrument": "EUR_USD",
            "stopLossOnFill": {"price": "1.20000"},
            "takeProfitOnFill": {"price": "1.30000"},
            "trailingStopLossOnFill": {"distance": "0.00200"},
            "units": "-3",
        },
    ]
    output = tmp_path / "out.csv"
    oanda_history.save_to_csv(records, output)
    with output.open(newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        assert reader.fieldnames == oanda_history.CSV_FIELDNAMES
        rows = list(reader)

    assert rows[0]["TICKET"] == "1"
    assert rows[0]["TRANSACTION DATE"] == "2024-01-01 10:00:00 UTC"
    assert rows[0]["DETAILS"] == "MARKET_ORDER"
    assert rows[0]["PRICE"] == "1.23456"
    assert rows[0]["UNITS"] == "2.00"
    assert rows[0]["DIRECTION"] == "Buy"
    assert rows[0]["SPREAD COST"] == "0.1234"
    assert rows[0]["FINANCING"] == "0.00001"
    assert rows[0]["COMMISSION"] == "0.1234"
    assert rows[0]["GSL FEE"] == "0.0000"
    assert rows[0]["CONVERSION RATE"] == "1.1111"
    assert rows[0]["PL"] == "1.00000"
    assert rows[0]["BALANCE"] == "100.12"

    assert rows[1]["TICKET"] == "2"
    assert rows[1]["DIRECTION"] == "Sell"
    assert rows[1]["STOP LOSS"] == "1.20000"
    assert rows[1]["TAKE PROFIT"] == "1.30000"
    assert rows[1]["TRAILING STOP"] == "0.00200"
    assert rows[1]["UNITS"] == "3.00"


def test_normalize_date() -> None:
    """Ensure _normalize_date converts dates correctly."""
    assert oanda_history._normalize_date(None) is None
    assert (
        oanda_history._normalize_date("2024-07-09")
        == "2024-07-09T00:00:00Z"
    )
    # Non-date values are returned unchanged
    assert (
        oanda_history._normalize_date("2024-07-09T01:02:03Z")
        == "2024-07-09T01:02:03Z"
    )


def test_resolve_api_url_adds_v3_suffix() -> None:
    assert (
        oanda_history._resolve_api_url("https://api-fxpractice.oanda.com")
        == "https://api-fxpractice.oanda.com/v3"
    )


def test_resolve_api_url_preserves_existing_v3() -> None:
    assert (
        oanda_history._resolve_api_url("https://api-fxpractice.oanda.com/v3")
        == "https://api-fxpractice.oanda.com/v3"
    )


def test_fetch_transactions_handles_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure fetch_transactions follows pagination links."""

    calls: List[dict] = []

    def fake_get(
        url: str,
        *,
        headers: dict,
        params: dict | None = None,
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "params": params})
        if params is not None:
            assert url.endswith("/transactions")
            return FakeResponse(
                200,
                {
                    "transactions": [{"id": "base"}],
                    "pages": [
                        "https://api.example.com/v3/accounts/acc/transactions/idrange?from=1&to=2",
                        "/v3/accounts/acc/transactions/idrange?from=3&to=4",
                    ],
                },
            )

        if "from=1" in url:
            return FakeResponse(
                200, {"transactions": [{"id": "base"}, {"id": "extra"}]}
            )
        if "from=3" in url:
            return FakeResponse(
                200, {"transactions": [{"id": "extra"}, {"id": "third"}]}
            )
        raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(
        oanda_history,
        "requests",
        types.SimpleNamespace(get=fake_get),
    )
    data = oanda_history.fetch_transactions("acc", "key", base_url="https://api.example.com/v3")

    assert [item["id"] for item in data] == ["base", "extra", "third"]
    assert len(calls) == 3
    # First call should include query params, subsequent paginated calls should not
    assert calls[0]["params"] == {}
    assert calls[1]["params"] is None
    assert calls[2]["params"] is None


def test_fetch_transactions_uses_link_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure fetch_transactions follows pagination exposed via Link headers."""

    calls: List[dict] = []

    def fake_get(
        url: str,
        *,
        headers: dict,
        params: dict | None = None,
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "params": params})
        if params is not None:
            return FakeResponse(
                200,
                {"transactions": [{"id": "first"}]},
                links={
                    "next": {
                        "url": "/v3/accounts/acc/transactions/idrange?from=1&to=2"
                    }
                },
            )
        return FakeResponse(200, {"transactions": [{"id": "second"}]})

    monkeypatch.setattr(
        oanda_history,
        "requests",
        types.SimpleNamespace(get=fake_get),
    )
    data = oanda_history.fetch_transactions("acc", "key", base_url="https://api.example.com/v3")

    assert [item["id"] for item in data] == ["first", "second"]
    assert len(calls) == 2
    assert calls[0]["params"] == {}
    assert calls[1]["params"] is None


def test_fetch_transactions_handles_dict_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure fetch_transactions copes with "pages" provided as a mapping."""

    calls: List[dict] = []

    def fake_get(
        url: str,
        *,
        headers: dict,
        params: dict | None = None,
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "params": params})
        if params is not None:
            return FakeResponse(
                200,
                {
                    "transactions": [{"id": "base"}],
                    "pages": {
                        "older": "/v3/accounts/acc/transactions/idrange?from=1&to=2",
                    },
                },
            )

        if "from=1" in url:
            return FakeResponse(
                200,
                {
                    "transactions": [{"id": "extra"}],
                    "pages": {
                        "older": "/v3/accounts/acc/transactions/idrange?from=3&to=4",
                    },
                },
            )

        if "from=3" in url:
            return FakeResponse(200, {"transactions": [{"id": "third"}]})

        raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(
        oanda_history,
        "requests",
        types.SimpleNamespace(get=fake_get),
    )
    data = oanda_history.fetch_transactions("acc", "key", base_url="https://api.example.com/v3")

    assert [item["id"] for item in data] == ["base", "extra", "third"]
    assert len(calls) == 3


def test_fetch_transactions_reads_raw_link_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure fetch_transactions parses Link headers directly when needed."""

    calls: List[dict] = []

    def fake_get(
        url: str,
        *,
        headers: dict,
        params: dict | None = None,
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "params": params})
        if params is not None:
            return FakeResponse(
                200,
                {"transactions": [{"id": "first"}]},
                headers={
                    "Link": (
                        "</v3/accounts/acc/transactions/idrange?from=1&to=2>; rel=\"next\""
                    )
                },
            )

        if "from=1" in url:
            return FakeResponse(
                200,
                {"transactions": [{"id": "second"}]},
                headers={
                    "Link": (
                        "</v3/accounts/acc/transactions/idrange?from=3&to=4>; rel=\"next\""
                    )
                },
            )

        if "from=3" in url:
            return FakeResponse(200, {"transactions": [{"id": "third"}]})

        raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(
        oanda_history,
        "requests",
        types.SimpleNamespace(get=fake_get),
    )
    data = oanda_history.fetch_transactions("acc", "key", base_url="https://api.example.com/v3")

    assert [item["id"] for item in data] == ["first", "second", "third"]
    assert len(calls) == 3
