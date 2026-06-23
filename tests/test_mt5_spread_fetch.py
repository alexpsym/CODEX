import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPREAD_DIR = ROOT / "spreads-clone"
sys.path.insert(0, str(SPREAD_DIR))

import mt5_spreads
from mt5_spreads import MT5_AUTHORIZATION_FAILED_MESSAGE, aggregate_tick_spreads
from spread_core import TimeframeConfig
from symbols import build_symbol_universe, oanda_to_mt5_symbol, resolve_mt5_symbol


def test_mt5_module_import_is_lazy_without_metatrader5_package():
    original = sys.modules.pop("MetaTrader5", None)
    sys.modules.pop("mt5_spreads", None)
    try:
        importlib.import_module("mt5_spreads")
        assert "MetaTrader5" not in sys.modules
    finally:
        if original is not None:
            sys.modules["MetaTrader5"] = original


def test_mt5_symbol_mapping_oanda_to_plain_and_suffix():
    assert oanda_to_mt5_symbol("EUR_USD") == "EURUSD"
    available = [SimpleNamespace(name="EURUSD.r"), SimpleNamespace(name="GBPUSD")]
    assert resolve_mt5_symbol("EUR_USD", available) == "EURUSD.r"


def test_mt5_tick_aggregation_uses_median_baseline_and_latest_tick():
    timeframe = TimeframeConfig("1M", "M1", 60, 7)
    ticks = [
        {"time": 1_767_225_600, "bid": 1.0000, "ask": 1.0010},
        {"time": 1_767_225_620, "bid": 1.0000, "ask": 1.0030},
        {"time": 1_767_225_660, "bid": 1.0000, "ask": 1.0020},
    ]
    parsed = aggregate_tick_spreads(ticks, timeframe)
    assert len(parsed["samples"]) == 2
    first_bucket_values = [
        ((1.0010 - 1.0000) / 1.0005) * 100,
        ((1.0030 - 1.0000) / 1.0015) * 100,
    ]
    assert parsed["samples"][0]["spread_pct"] == pytest.approx(sum(first_bucket_values) / 2)
    assert parsed["latest"]["spread_pct"] == pytest.approx(((1.0020 - 1.0000) / 1.0010) * 100)


def test_mt5_tick_aggregation_can_apply_pepperstone_razor_commission_adjustment():
    timeframe = TimeframeConfig("1M", "M1", 60, 7)
    ticks = [{"time": 1_767_225_600, "bid": 1.0000, "ask": 1.0010}]
    parsed = aggregate_tick_spreads(
        ticks,
        timeframe,
        commission_adjustment_pct=lambda _midpoint: 0.007,
    )
    raw = ((1.0010 - 1.0000) / 1.0005) * 100
    assert parsed["latest"]["spread_pct"] == pytest.approx(raw + 0.007)


def test_mt5_tick_aggregation_selects_tick_at_or_before_lookback_target():
    timeframe = TimeframeConfig("5M", "M5", 300, 14)
    ticks = [
        {"time": 1_767_225_000, "bid": 1.0000, "ask": 1.0010},
        {"time": 1_767_225_300, "bid": 1.0000, "ask": 1.0040},
        {"time": 1_767_225_600, "bid": 1.0000, "ask": 1.0080},
    ]
    parsed = aggregate_tick_spreads(
        ticks,
        timeframe,
        target_at=datetime.fromtimestamp(1_767_225_350, tz=timezone.utc),
    )
    assert parsed["latest"]["time"] == datetime.fromtimestamp(1_767_225_300, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert parsed["latest"]["spread_pct"] == pytest.approx(((1.0040 - 1.0000) / 1.0020) * 100)


def test_mt5_zero_spread_ticks_are_unavailable_not_fake_zero():
    timeframe = TimeframeConfig("1M", "M1", 60, 7)
    ticks = [{"time": 1_767_225_600, "bid": 1.0000, "ask": 1.0000}]
    parsed = aggregate_tick_spreads(ticks, timeframe)
    assert parsed["latest"] is None
    assert parsed["error"] == "No MT5 tick bid/ask spread data returned."


def test_mt5_initialize_timeout_is_used_for_preflight_symbols_and_fetch(monkeypatch):
    class FakeMT5:
        def __init__(self, *, initialize_ok):
            self.initialize_ok = initialize_ok
            self.init_kwargs = []

        def initialize(self, **kwargs):
            self.init_kwargs.append(kwargs)
            return self.initialize_ok

        def shutdown(self):
            return None

        def last_error(self):
            return (-6, "Terminal: Authorization failed")

        def account_info(self):
            return None

        def symbols_get(self):
            return []

    preflight_mt5 = FakeMT5(initialize_ok=True)
    monkeypatch.setattr(mt5_spreads, "_import_mt5", lambda: preflight_mt5)
    preflight = mt5_spreads.preflight_mt5_environment()
    assert preflight["error"] == MT5_AUTHORIZATION_FAILED_MESSAGE
    assert preflight_mt5.init_kwargs[0]["timeout"] == 9000

    symbols_mt5 = FakeMT5(initialize_ok=False)
    monkeypatch.setattr(mt5_spreads, "_import_mt5", lambda: symbols_mt5)
    assert mt5_spreads.available_mt5_symbols() == []
    assert symbols_mt5.init_kwargs[0]["timeout"] == 9000

    fetch_mt5 = FakeMT5(initialize_ok=True)
    monkeypatch.setattr(mt5_spreads, "_import_mt5", lambda: fetch_mt5)
    result = mt5_spreads.fetch_mt5_spread_samples("EUR_USD", TimeframeConfig("1M", "M1", 60, 7))
    assert result["error"] == MT5_AUTHORIZATION_FAILED_MESSAGE
    assert fetch_mt5.init_kwargs[0]["timeout"] == 9000


def test_symbol_universe_includes_available_symbols_by_default():
    missing_journal = ROOT / ".pytest_tmp_missing_spread_journal.xlsx"
    symbols = build_symbol_universe(
        journal_path=missing_journal,
        oanda_symbols=["EUR_USD", "GBP_USD", "AUD_JPY"],
        mt5_symbols=[SimpleNamespace(name="NZDUSD.r")],
    )
    assert symbols == ["AUD_JPY", "EUR_USD", "GBP_USD", "NZD_USD"]
