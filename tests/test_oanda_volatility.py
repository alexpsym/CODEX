from __future__ import annotations

import pytest

from render.oanda_volatility import (
    TIMEFRAME_GRANULARITIES,
    filter_currency_instruments,
    sort_rows,
    wilder_atr_percent,
)


def test_granularity_mapping() -> None:
    assert TIMEFRAME_GRANULARITIES == {
        "1m": "M1",
        "5m": "M5",
        "1h": "H1",
        "1D": "D",
        "1W": "W",
        "1Mo": "M",
    }


def test_atr_percent_uses_completed_candles() -> None:
    completed = [
        {
            "time": f"2026-08-31T00:{minute:02d}:00Z",
            "complete": True,
            "mid": {"h": "101", "l": "99", "c": "100"},
        }
        for minute in range(15)
    ]
    incomplete = {
        "time": "2026-08-31T00:15:00Z",
        "complete": False,
        "mid": {"h": "10000", "l": "1", "c": "2"},
    }

    assert wilder_atr_percent([*completed, incomplete]) == pytest.approx(2.0)
    assert wilder_atr_percent([*completed, incomplete]) == wilder_atr_percent(completed)


def test_currency_filter_and_deterministic_sort() -> None:
    instruments = filter_currency_instruments(
        {
            "instruments": [
                {"name": "XAU_USD", "type": "METAL"},
                {"name": "GBP_USD", "type": "CURRENCY"},
                {"name": "EUR_USD", "type": "CURRENCY"},
                {"name": "AUD_USD", "type": "CURRENCY", "tradeable": False},
                {"name": "EUR_USD", "type": "CURRENCY"},
                {"name": "US30_USD", "type": "CFD"},
            ]
        }
    )
    assert instruments == ["EUR_USD", "GBP_USD"]

    rows = sort_rows(
        [
            {"instrument": "GBP_USD", "atr_pct": {"1m": 0.25}},
            {"instrument": "EUR_USD", "atr_pct": {"1m": 0.25}},
            {
                "instrument": "USD_JPY",
                "atr_pct": {"1m": None},
                "atr_status": {"1m": "error"},
                "diagnostics": {"1m": "bounded request failed"},
            },
        ],
        "1m",
    )

    assert [row["instrument"] for row in rows] == ["EUR_USD", "GBP_USD", "USD_JPY"]
    assert rows[-1]["atr_pct"]["1m"] is None
    assert rows[-1]["atr_status"]["1m"] == "error"
    assert rows[-1]["diagnostics"]["1m"] == "bounded request failed"
