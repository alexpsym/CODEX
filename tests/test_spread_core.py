import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPREAD_DIR = ROOT / "spreads-clone"
sys.path.insert(0, str(SPREAD_DIR))

from spread_core import SpreadMonitorState, TimeframeConfig, classify_spread, format_spread_pct, spread_pct_from_bid_ask


def _repo_cache_path(name: str) -> Path:
    path = ROOT / f".pytest_tmp_spread_core_{name}.json"
    if path.exists():
        path.unlink()
    return path


def test_spread_pct_formula_uses_midpoint_percentage():
    assert spread_pct_from_bid_ask(1.0, 1.1) == pytest.approx((0.1 / 1.05) * 100)


def test_spread_percent_format_switches_precision():
    assert format_spread_pct(0.009876) == "0.00988%"
    assert format_spread_pct(0.01234) == "0.0123%"


def test_percentile_classification_thresholds():
    samples = list(range(1, 101))
    assert classify_spread(50, samples) == "low"
    assert classify_spread(79, samples) == "medium"
    assert classify_spread(80, samples) == "high"
    assert classify_spread(None, samples) == "unavailable"
    assert classify_spread(1, []) == "unavailable"


def test_unavailable_broker_data_does_not_crash_payload():
    timeframe = TimeframeConfig("1M", "M1", 60, 7)

    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
        }

    cache_path = _repo_cache_path("unavailable")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=None,
        )
        payload = state.refresh()
    finally:
        if cache_path.exists():
            cache_path.unlink()
    cell = payload["rows"][0]["cells"][timeframe.label]
    assert cell["oanda"]["spread_pct"] == pytest.approx(0.0123)
    assert cell["pepperstone"]["category"] == "unavailable"
    assert payload["ok"] is True


def test_oanda_and_pepperstone_razor_values_are_both_in_payload():
    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
        }

    def mt5_fetcher(_symbol, _timeframe, _context):
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0456}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0456},
        }

    cache_path = _repo_cache_path("pepperstone")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=mt5_fetcher,
        )
        payload = state.refresh()
    finally:
        if cache_path.exists():
            cache_path.unlink()

    cell = payload["rows"][0]["cells"]["1M"]
    assert cell["oanda"]["display"] == "0.0123%"
    assert cell["pepperstone"]["display"] == "0.0456%"
    assert cell["pepperstone_razor"]["spread_pct"] == pytest.approx(0.0456)


def test_missing_pepperstone_has_explicit_unavailable_reason():
    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
        }

    def mt5_fetcher(_symbol, _timeframe, _context):
        return {"error": "MT5 terminal is not logged in."}

    cache_path = _repo_cache_path("pepperstone_missing")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=mt5_fetcher,
        )
        payload = state.refresh()
    finally:
        if cache_path.exists():
            cache_path.unlink()

    cell = payload["rows"][0]["cells"]["1M"]
    assert cell["pepperstone_razor"]["category"] == "unavailable"
    assert cell["pepperstone_razor"]["error"] == "MT5 terminal is not logged in."
    assert any("Pepperstone EUR_USD 1M: MT5 terminal is not logged in." == warning for warning in payload["warnings"])


def test_refresh_lock_returns_cached_data_instead_of_overlapping_refresh():
    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
        }

    cache_path = _repo_cache_path("lock")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=None,
        )
        state.refresh()
        assert state.refresh_lock.acquire(blocking=False)
        try:
            payload = state.refresh()
        finally:
            state.refresh_lock.release()
    finally:
        if cache_path.exists():
            cache_path.unlink()
    assert payload["status"] == "refresh_in_progress"
    assert payload["rows"][0]["symbol"] == "EUR_USD"


def test_cached_refresh_uses_incremental_request_count():
    calls = []

    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, context):
        calls.append(context["requested_count"])
        return {
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123}],
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
        }

    cache_path = _repo_cache_path("incremental")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=None,
        )
        state.refresh()
        state.refresh()
    finally:
        if cache_path.exists():
            cache_path.unlink()

    assert calls[:9] == [750] * 9
    assert calls[9:] == [250] * 9
