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
    OANDA_CURRENT_TIMEFRAME_LABEL,
    build_spread_payload,
    _cache_key,
    broker_cell,
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


def test_broker_cell_zero_spread_is_valid_zero():
    cell = broker_cell(
        {
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0, "spread_points": 0.0},
            "samples": [{"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0, "spread_points": 0.0}],
            "last_success": "2026-01-01T00:00:00Z",
            "error": "",
        }
    )
    assert cell["spread_pct"] == 0
    assert cell["spread_points"] == 0
    assert cell["display"] == "0.00000%"
    assert cell["category"] == "neutral"


def test_broker_cell_valid_spreads_are_neutral_without_history():
    sparse = broker_cell(
        {
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 0.0123},
            "samples": [],
            "last_success": "2026-01-01T00:00:00Z",
            "error": "",
        }
    )
    sampled = broker_cell(
        {
            "latest": {"time": "2026-01-01T00:00:00Z", "spread_pct": 99.0},
            "samples": [{"time": f"2026-01-01T00:{minute:02d}:00Z", "spread_pct": minute} for minute in range(60)],
            "last_success": "2026-01-01T00:00:00Z",
            "error": "",
        }
    )
    assert sparse["category"] == "neutral"
    assert sparse["display"] == "0.0123%"
    assert sampled["category"] == "neutral"
    assert sampled["display"] == "99.0000%"


def test_broker_cell_unavailable_stays_unavailable():
    cell = broker_cell(
        {
            "latest": None,
            "samples": [],
            "last_success": "",
            "error": "bid/ask unavailable",
        }
    )
    assert cell["category"] == "unavailable"
    assert cell["spread_pct"] is None
    assert cell["spread_points"] is None
    assert cell["display"] == ""
    assert cell["error"] == "bid/ask unavailable"


def test_oanda_only_refresh_does_not_create_pepperstone_records():
    mt5_calls = []
    oanda_calls = []

    def symbols():
        return ["EUR_USD"]

    def oanda_current_fetcher(batch, context):
        oanda_calls.append((list(batch), dict(context)))
        return {symbol: _oanda_result() for symbol in batch}

    def mt5_fetcher(_symbol, _timeframe, _context):
        mt5_calls.append((_symbol, _timeframe.label))
        return _oanda_result(0.0456)

    cache_path = _repo_cache_path("oanda_only")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_current_fetcher=oanda_current_fetcher,
            mt5_fetcher=mt5_fetcher,
        )
        payload = state.refresh()
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    finally:
        if cache_path.exists():
            cache_path.unlink()

    assert mt5_calls == []
    assert len(oanda_calls) == 1
    assert oanda_calls[0][0] == ["EUR_USD"]
    assert payload["timeframes"] == []
    assert payload["columns"] == [
        {"key": "symbol", "label": "Instrument"},
        {"key": "current_spread", "label": "Current Spread"},
    ]
    assert payload["rows"][0]["current_spread"]["spread_pct"] == pytest.approx(0.0123)
    assert not any(str(key).startswith("pepperstone|") for key in cache["records"])
    assert _cache_key("oanda", "EUR_USD", OANDA_CURRENT_TIMEFRAME_LABEL) in cache["records"]
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


def test_oanda_payload_sanitizes_legacy_pepperstone_cache_errors():
    cache = {
        "generated_at": "2026-01-01T00:00:00Z",
        "symbols": ["EUR_USD"],
        "warnings": ["Pepperstone unavailable: MT5 terminal initialize failed: (-6, 'Terminal: Authorization failed')"],
        "errors": ["pepperstone|EUR_USD|1M import-file cache failed"],
        "records": {
            _cache_key("oanda", "EUR_USD", "1M"): _oanda_result(),
            _cache_key("pepperstone", "EUR_USD", "1M"): {
                "latest": None,
                "samples": [],
                "error": "MT5 terminal initialize failed: Authorization failed",
            },
        },
    }
    payload = build_spread_payload(cache, brokers=("oanda",))
    text = json.dumps(payload).lower()
    assert "pepperstone" not in text
    assert "mt5" not in text
    assert "authorization failed" not in text
    assert payload["rows"][0]["cells"]["1M"]["oanda"]["spread_pct"] == pytest.approx(0.0123)


def test_oanda_payload_cells_contain_only_oanda_records():
    cache = {
        "generated_at": "2026-01-01T00:00:00Z",
        "symbols": ["EUR_USD"],
        "warnings": [],
        "errors": [],
        "records": {
            _cache_key("oanda", "EUR_USD", "1M"): _oanda_result(),
            _cache_key("pepperstone", "EUR_USD", "1M"): _oanda_result(0.0999),
        },
    }
    payload = build_spread_payload(cache, brokers=("oanda",))
    cell = payload["rows"][0]["cells"]["1M"]
    assert list(cell.keys()) == ["oanda"]
    assert cell["oanda"]["spread_pct"] == pytest.approx(0.0123)


def test_oanda_current_payload_uses_current_spread_column_without_timeframes():
    cache = {
        "generated_at": "2026-01-01T00:00:00Z",
        "last_refresh_finished_at": "2026-01-01T00:00:00Z",
        "symbols": ["EUR_USD", "GBP_USD"],
        "warnings": [],
        "errors": [],
        "records": {
            _cache_key("oanda", "EUR_USD", OANDA_CURRENT_TIMEFRAME_LABEL): _oanda_result(),
            _cache_key("oanda", "EUR_USD", "1M"): _oanda_result(0.0999),
        },
    }
    payload = build_spread_payload(cache, brokers=("oanda",), current_only=True)
    assert payload["timeframes"] == []
    assert payload["current_only"] is True
    assert payload["columns"][0]["label"] == "Instrument"
    assert payload["columns"][1]["label"] == "Current Spread"
    assert payload["symbols"] == ["EUR_USD", "GBP_USD"]
    by_symbol = {row["symbol"]: row for row in payload["rows"]}
    assert by_symbol["EUR_USD"]["current_spread"]["spread_pct"] == pytest.approx(0.0123)
    assert "1M" not in by_symbol["EUR_USD"]["cells"]
    gbp_cell = by_symbol["GBP_USD"]["current_spread"]
    assert gbp_cell["spread_pct"] is None
    assert gbp_cell["category"] == "unavailable"
    assert gbp_cell["display"] == ""
    assert "No data cached" in gbp_cell["error"]


def test_pepperstone_current_payload_uses_snapshot_column_without_timeframes():
    cache = {
        "generated_at": "2026-01-01T00:00:00Z",
        "last_imported_at": "2026-01-01T00:00:00Z",
        "symbols": ["EUR_USD", "GBP_USD"],
        "warnings": [],
        "errors": [],
        "records": {
            _cache_key("pepperstone", "EUR_USD", OANDA_CURRENT_TIMEFRAME_LABEL): _oanda_result(),
            _cache_key("pepperstone", "GBP_USD", "1M"): _oanda_result(0.0456),
        },
    }
    payload = build_spread_payload(cache, brokers=("pepperstone",), current_only=True)
    assert payload["timeframes"] == []
    assert payload["current_only"] is True
    assert payload["columns"] == [
        {"key": "symbol", "label": "Instrument"},
        {"key": "current_spread", "label": "Current Spread"},
    ]
    by_symbol = {row["symbol"]: row for row in payload["rows"]}
    assert by_symbol["EUR_USD"]["current_spread"]["spread_pct"] == pytest.approx(0.0123)
    assert by_symbol["EUR_USD"]["cells"]["CURRENT"]["pepperstone_razor"] == by_symbol["EUR_USD"]["current_spread"]
    assert by_symbol["GBP_USD"]["current_spread"]["spread_pct"] == pytest.approx(0.0456)
    assert "1M" not in by_symbol["GBP_USD"]["cells"]


def test_refresh_timeout_returns_diagnostics_and_allows_second_attempt():
    calls = []

    def symbols():
        return ["EUR_USD"]

    def slow_fetcher(_symbol, _timeframe, _context):
        calls.append("slow")
        time.sleep(0.2)
        return _oanda_result()

    cache_path = _repo_cache_path("timeout")
    try:
        state = SpreadMonitorState(
            cache_path,
            symbol_provider=symbols,
            oanda_fetcher=slow_fetcher,
            oanda_concurrency=1,
        )
        state.global_refresh_timeout_seconds = 0.05
        timed_out = state.refresh()
        assert timed_out["refresh_state"] == "timed_out"
        assert "timed out" in timed_out["refresh"]["error"].lower()
        diagnostics = timed_out["refresh"]["diagnostics"]
        assert diagnostics["timed_out"] is True
        assert diagnostics["total_requests_planned"] == 9
        assert diagnostics["skipped_request_count"] >= 1

        def fast_fetcher(_symbol, _timeframe, _context):
            return _oanda_result(0.0456)

        state.oanda_fetcher = fast_fetcher
        state.global_refresh_timeout_seconds = 5
        second = state.refresh()
        assert second["refresh_state"] == "succeeded"
        assert second["rows"][0]["cells"]["1M"]["oanda"]["spread_pct"] == pytest.approx(0.0456)
    finally:
        if cache_path.exists():
            cache_path.unlink()
