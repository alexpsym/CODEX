import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPREAD_DIR = ROOT / "spreads-clone"
sys.path.insert(0, str(SPREAD_DIR))

from spread_core import (  # noqa: E402
    SpreadMonitorState,
    TimeframeConfig,
    broker_cell,
    classify_spread,
    format_spread_pct,
    spread_pct_from_bid_ask,
)


def _repo_cache_path(name: str) -> Path:
    path = ROOT / f".pytest_tmp_spread_core_{name}.json"
    if path.exists():
        path.unlink()
    return path


def _oanda_result(value: float = 0.0123) -> dict:
    return {
        "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": value}],
        "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": value},
    }


def test_spread_pct_formula_uses_midpoint_percentage():
    assert spread_pct_from_bid_ask(1.0, 1.1) == pytest.approx((0.1 / 1.05) * 100)


def test_spread_percent_format_switches_precision():
    assert format_spread_pct(0.009876) == "0.00988%"
    assert format_spread_pct(0.01234) == "0.0123%"


def test_broker_cell_zero_spread_is_unavailable_not_fake_zero():
    cell = broker_cell(
        {
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0},
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0}],
            "last_success": "2026-01-01T00:00:00Z",
            "error": "",
        }
    )
    assert cell["spread_pct"] is None
    assert cell["display"] == ""
    assert cell["category"] == "unavailable"


def test_percentile_classification_thresholds():
    samples = list(range(1, 101))
    assert classify_spread(50, samples) == "low"
    assert classify_spread(79, samples) == "medium"
    assert classify_spread(80, samples) == "high"
    assert classify_spread(None, samples) == "unavailable"
    assert classify_spread(1, []) == "unavailable"


def test_oanda_only_refresh_does_not_create_pepperstone_records():
    mt5_calls = []

    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return _oanda_result()

    def mt5_fetcher(_symbol, _timeframe, _context):
        mt5_calls.append((_symbol, _timeframe.label))
        return _oanda_result(0.0456)

    cache_path = _repo_cache_path("oanda_only")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
            mt5_fetcher=mt5_fetcher,
        )
        payload = state.refresh()
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    finally:
        if cache_path.exists():
            cache_path.unlink()

    assert mt5_calls == []
    cell = payload["rows"][0]["cells"]["1M"]
    assert cell["oanda"]["spread_pct"] == pytest.approx(0.0123)
    assert "pepperstone" not in cell
    assert "pepperstone_razor" not in cell
    assert not any(str(key).startswith("pepperstone|") for key in cache["records"])
    assert payload["ok"] is True


def test_background_refresh_returns_running_status_quickly():
    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        time.sleep(0.05)
        return _oanda_result()

    cache_path = _repo_cache_path("background")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
        )
        payload = state.start_refresh()
        assert payload["refresh_state"] == "running"
        assert payload["status"] == "refresh_in_progress"
        assert state._refresh_thread is not None
        state._refresh_thread.join(timeout=2)
        assert state.status()["refresh_state"] in {"succeeded", "failed"}
    finally:
        if cache_path.exists():
            cache_path.unlink()


def test_refresh_lock_returns_cached_data_instead_of_overlapping_refresh():
    def symbols():
        return ["EUR_USD"]

    def oanda_fetcher(_symbol, _timeframe, _context):
        return _oanda_result()

    cache_path = _repo_cache_path("lock")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
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
        return _oanda_result()

    cache_path = _repo_cache_path("incremental")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=oanda_fetcher,
        )
        state.refresh()
        state.refresh()
    finally:
        if cache_path.exists():
            cache_path.unlink()

    assert calls[:9] == [750] * 9
    assert calls[9:] == [250] * 9
