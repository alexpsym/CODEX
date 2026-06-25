import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPREAD_DIR = ROOT / "spreads-clone"
sys.path.insert(0, str(SPREAD_DIR))

import oanda_spreads
from oanda_spreads import fetch_oanda_current_spreads, fetch_oanda_spread_samples, parse_oanda_bid_ask_candles, parse_oanda_lookback_candles, parse_oanda_pricing
from spread_core import TimeframeConfig


def test_oanda_candle_parsing_uses_bid_ask_not_mid():
    payload = {
        "candles": [
            {
                "time": "2026-01-01T00:00:00Z",
                "complete": True,
                "mid": {"c": "999.0"},
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0020"},
            }
        ]
    }
    parsed = parse_oanda_bid_ask_candles(payload)
    latest = parsed["latest"]
    assert latest["spread_pct"] == pytest.approx(((1.0020 - 1.0000) / 1.0010) * 100)


def test_oanda_incomplete_candles_are_excluded_from_baseline_but_latest_can_show():
    payload = {
        "candles": [
            {
                "time": "2026-01-01T00:00:00Z",
                "complete": True,
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0010"},
            },
            {
                "time": "2026-01-01T00:01:00Z",
                "complete": False,
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0040"},
            },
        ]
    }
    parsed = parse_oanda_bid_ask_candles(payload)
    assert len(parsed["samples"]) == 1
    assert parsed["samples"][0]["time"] == "2026-01-01T00:00:00Z"
    assert parsed["latest"]["time"] == "2026-01-01T00:01:00Z"


def test_oanda_fetch_uses_bid_ask_price_parameter_and_single_v3_endpoint():
    calls = []

    def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {
            "candles": [
                {
                    "time": "2026-01-01T00:00:00Z",
                    "complete": True,
                    "bid": {"c": "1.0000"},
                    "ask": {"c": "1.0010"},
                }
            ]
        }

    timeframe = TimeframeConfig("1M", "M1", 60, 7)
    parsed = fetch_oanda_spread_samples("EUR_USD", timeframe, request_func=fake_request)

    assert parsed["samples"]
    method, endpoint, kwargs = calls[0]
    assert method == "GET"
    assert endpoint == "/instruments/EUR_USD/candles"
    assert not endpoint.startswith("/v3/")
    assert kwargs["params"]["price"] == "BA"
    assert kwargs["params"]["granularity"] == "M1"
    assert kwargs["params"]["count"] <= 5000


def test_oanda_pricing_parsing_uses_current_bid_ask_spread_percentage():
    payload = {
        "prices": [
            {
                "instrument": "EUR_USD",
                "time": "2026-01-01T00:00:00Z",
                "bids": [{"price": "1.0000"}],
                "asks": [{"price": "1.0020"}],
            }
        ]
    }
    parsed = parse_oanda_pricing(payload)
    latest = parsed["EUR_USD"]["latest"]
    assert latest["spread_pct"] == pytest.approx(((1.0020 - 1.0000) / 1.0010) * 100)


def test_oanda_current_spread_fetch_batches_account_pricing_request(monkeypatch):
    calls = []
    monkeypatch.setattr(oanda_spreads.oanda_api, "_account_id", lambda mode: "101-001")

    def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {
            "prices": [
                {
                    "instrument": "EUR_USD",
                    "time": "2026-01-01T00:00:00Z",
                    "bids": [{"price": "1.0000"}],
                    "asks": [{"price": "1.0010"}],
                },
                {
                    "instrument": "GBP_USD",
                    "time": "2026-01-01T00:00:01Z",
                    "bids": [{"price": "1.2500"}],
                    "asks": [{"price": "1.2520"}],
                },
            ]
        }

    parsed = fetch_oanda_current_spreads(
        ["EUR_USD", "GBP_USD"],
        {"request_timeout_seconds": 7},
        mode="demo",
        request_func=fake_request,
    )

    assert set(parsed) == {"EUR_USD", "GBP_USD"}
    assert parsed["GBP_USD"]["latest"]["spread_pct"] == pytest.approx(((1.2520 - 1.2500) / 1.2510) * 100)
    assert len(calls) == 1
    method, endpoint, kwargs = calls[0]
    assert method == "GET"
    assert endpoint == "/accounts/101-001/pricing"
    assert kwargs["account_id"] == "101-001"
    assert kwargs["params"]["instruments"] == "EUR_USD,GBP_USD"
    assert kwargs["timeout"] == 7


def test_oanda_lookback_selects_nearest_complete_candle_at_or_before_target():
    payload = {
        "candles": [
            {
                "time": "2026-01-01T00:05:00Z",
                "complete": True,
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0010"},
            },
            {
                "time": "2026-01-01T00:10:00Z",
                "complete": True,
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0030"},
            },
            {
                "time": "2026-01-01T00:15:00Z",
                "complete": False,
                "bid": {"c": "1.0000"},
                "ask": {"c": "1.0090"},
            },
        ]
    }
    parsed = parse_oanda_lookback_candles(payload, datetime(2026, 1, 1, 0, 11, tzinfo=timezone.utc))
    assert parsed["latest"]["time"] == "2026-01-01T00:10:00Z"
    assert parsed["latest"]["spread_pct"] == pytest.approx(((1.0030 - 1.0000) / 1.0015) * 100)


def test_oanda_fetch_treats_columns_as_lookback_offsets_not_latest_copies():
    calls = []

    def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {
            "candles": [
                {
                    "time": "2026-01-01T00:00:00Z",
                    "complete": True,
                    "bid": {"c": "1.0000"},
                    "ask": {"c": "1.0010"},
                },
                {
                    "time": "2026-01-01T00:10:00Z",
                    "complete": True,
                    "bid": {"c": "1.0000"},
                    "ask": {"c": "1.0030"},
                },
                {
                    "time": "2026-01-01T00:14:00Z",
                    "complete": True,
                    "bid": {"c": "1.0000"},
                    "ask": {"c": "1.0050"},
                },
            ]
        }

    now = datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc)
    one_min = fetch_oanda_spread_samples("EUR_USD", TimeframeConfig("1M", "M1", 60, 7), request_func=fake_request, end=now)
    five_min = fetch_oanda_spread_samples("EUR_USD", TimeframeConfig("5M", "M5", 300, 14), request_func=fake_request, end=now)
    fifteen_min = fetch_oanda_spread_samples("EUR_USD", TimeframeConfig("15M", "M15", 900, 21), request_func=fake_request, end=now)

    assert one_min["latest"]["time"] == "2026-01-01T00:14:00Z"
    assert five_min["latest"]["time"] == "2026-01-01T00:10:00Z"
    assert fifteen_min["latest"]["time"] == "2026-01-01T00:00:00Z"
    values = {one_min["latest"]["spread_pct"], five_min["latest"]["spread_pct"], fifteen_min["latest"]["spread_pct"]}
    assert len(values) == 3
    assert all(call[2]["params"]["price"] == "BA" for call in calls)
