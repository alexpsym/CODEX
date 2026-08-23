import importlib.util
import math
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EA_PATH = ROOT / "mt5-clone" / "MQL5" / "Experts" / "MarketWatchATRPercentFeed.mq5"
WINDOW_PATH = ROOT / "mt5-clone" / "atr_percent_window.py"
SPREAD_EA_PATH = ROOT / "mt5-clone" / "MQL5" / "Experts" / "MarketWatchSpreadPercentFeed.mq5"
SPREAD_WINDOW_PATH = ROOT / "mt5-clone" / "spread_percent_window.py"


def _load_window_module():
    spec = importlib.util.spec_from_file_location("atr_percent_window_under_test", WINDOW_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def test_separate_ea_enumerates_selected_market_watch_forex_only():
    source = _source()
    assert "SymbolsTotal(true)" in source
    assert "SymbolName(index, true)" in source
    assert "SYMBOL_TRADE_CALC_MODE" in source
    assert "SYMBOL_CALC_MODE_FOREX" in source
    assert "SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE" in source
    assert "Selected Market Watch instrument is not Forex" in source
    assert "SYMBOL_VOLUME" not in source
    assert "tick_volume" not in source.lower()
    assert "real_volume" not in source.lower()
    assert "OrderSend(" not in source
    assert "CTrade" not in source
    assert ".Buy(" not in source and ".Sell(" not in source


def test_ea_uses_six_unambiguous_timeframes_and_closed_candle_atr_percent():
    source = _source()
    for period in ["PERIOD_M1", "PERIOD_M5", "PERIOD_H1", "PERIOD_D1", "PERIOD_W1", "PERIOD_MN1"]:
        assert period in source
    assert '"m1", "m5", "h1", "d1", "w1", "mn1"' in source
    assert "iATR(g_symbols[index], FRAME_PERIODS[frame], SafeATRLength())" in source
    assert "CopyBuffer(handle, 0, 1, 1, atr_buffer)" in source
    assert "iClose(g_symbols[index], FRAME_PERIODS[frame], 1)" in source
    assert "double percent = (atr_buffer[0] / closed_price) * 100.0;" in source
    assert "last closed candle" in source.lower() or "last-closed-candle" in source.lower()


def test_ea_batches_history_loading_reuses_handles_and_rejects_empty_values():
    source = _source()
    rebuild = source.split("void RebuildUniverse()", 1)[1].split("void MarkUnavailableFrame", 1)[0]
    refresh = source.split("void RefreshForexSymbol", 1)[1].split("void ProcessNextBatch", 1)[0]
    assert "iATR(" not in rebuild
    assert "iATR(" in refresh
    assert "BarsCalculated(handle) < SafeATRLength() + 2" in refresh
    assert "atr_buffer[0] == EMPTY_VALUE" in refresh
    assert "closed_price == EMPTY_VALUE" in refresh
    assert 'MarkUnavailableFrame(slot, "Loading")' in refresh
    assert 'MarkUnavailableFrame(slot, "Error")' in refresh
    assert 'g_frame_status[slot] = "Stale"' in source
    assert "g_last_closed_bars[slot] == closed_bar" in refresh
    assert "SymbolsPerTimer" in source
    assert "ProcessNextBatch();" in source
    assert "last_successful_refresh_epoch_ms" in source
    assert "OnTick()" in source
    assert "timer-batched" in source


def test_ea_timer_and_indicator_lifecycle_are_bounded_and_complete():
    source = _source()
    release = source.split("void ReleaseHandles()", 1)[1].split("bool IsForexSymbol", 1)[0]
    deinit = source.split("void OnDeinit", 1)[1].split("void OnTimer", 1)[0]
    init = source.split("int OnInit()", 1)[1].split("void OnDeinit", 1)[0]
    assert "IndicatorRelease(g_handles[i])" in release
    assert "ReleaseHandles();" in deinit
    assert "EventKillTimer();" in deinit
    assert "if(!EventSetMillisecondTimer(SafeUpdateInterval()))" in init
    assert "return INIT_FAILED;" in init


def test_atr_feed_and_window_names_cannot_collide_with_spread_tool():
    atr_source = _source()
    spread_source = SPREAD_EA_PATH.read_text(encoding="utf-8")
    atr_window = WINDOW_PATH.read_text(encoding="utf-8")
    spread_window = SPREAD_WINDOW_PATH.read_text(encoding="utf-8")
    assert 'ExportFileName     = "MarketWatchATRPercentFeed.json"' in atr_source
    assert 'DesktopWindowScriptPath  = "C:\\\\GPT\\\\CODEX-master\\\\mt5-clone\\\\atr_percent_window.py"' in atr_source
    assert "MarketWatchATRPercentFeed.json" not in spread_source
    assert "MarketWatchSpreadPercentFeed.json" not in atr_source
    assert 'self.root.title("Market Watch Forex ATR %")' in atr_window
    assert 'self.root.title("Market Watch Spread %")' in spread_window
    assert '"ATR.Treeview"' in atr_window
    assert '"Spread.Treeview"' in spread_window


def test_window_parses_ranks_raw_values_and_keeps_unavailable_diagnostics():
    window = _load_window_module()
    feed = {
        "symbols": [
            {
                "symbol": "EURUSD.a",
                "is_forex": True,
                "status": "Ready",
                "reason": "",
                "atr_percent_m1": 0.100004,
                "atr_percent_m5": 0.2,
                "state_m1": "Ready",
                "state_m5": "Ready",
            },
            {
                "symbol": "AUDUSD.a",
                "is_forex": True,
                "status": "Ready",
                "reason": "",
                "atr_percent_m1": 0.1000049,
                "atr_percent_m5": 0.1,
                "state_m1": "Ready",
                "state_m5": "Ready",
            },
            {
                "symbol": "GBPUSD.a",
                "is_forex": True,
                "status": "Loading",
                "reason": "history loading",
                "atr_percent_m1": None,
                "state_m1": "Loading",
            },
            {
                "symbol": "XAUUSD.a",
                "is_forex": False,
                "status": "Excluded",
                "reason": "not Forex",
                "atr_percent_m1": 8.0,
                "state_m1": "N/A",
            },
        ]
    }
    rows = window.parse_feed_rows(feed)
    ranked = window.rank_rows(rows, "1m", 10)
    assert [row.symbol for row in ranked] == ["AUDUSD.a", "EURUSD.a"]
    diagnostics = window.diagnostic_rows(rows, "1m")
    assert [row.symbol for row in diagnostics] == ["GBPUSD.a", "XAUUSD.a"]
    assert ranked[0].atr_percent["1h"] is None
    assert window.optional_positive_float(float("nan")) is None
    assert window.optional_positive_float(float("inf")) is None
    assert window.optional_positive_float(0) is None


def test_window_deterministic_ties_top_n_and_each_timeframe():
    window = _load_window_module()
    rows = [
        window.ATRRow(
            symbol=symbol,
            is_forex=True,
            status="Ready",
            reason="",
            atr_percent={label: value for label in window.TIMEFRAME_LABELS},
            frame_states={label: "Ready" for label in window.TIMEFRAME_LABELS},
        )
        for symbol, value in [("ZZZUSD", 0.2), ("AAAUSD", 0.2), ("MIDUSD", 0.1)]
    ]
    for timeframe in window.TIMEFRAME_LABELS:
        assert [row.symbol for row in window.rank_rows(rows, timeframe, 2)] == ["AAAUSD", "ZZZUSD"]
        assert window.diagnostic_rows(rows, timeframe) == []
    with pytest.raises(ValueError, match="Unsupported rank timeframe"):
        window.rank_rows(rows, "1M", 10)


def test_window_module_help_is_a_non_gui_startup_smoke():
    result = subprocess.run(
        [sys.executable, str(WINDOW_PATH), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--rank-timeframe" in result.stdout
    assert "--top-n" in result.stdout


def test_window_rejects_non_finite_values_instead_of_formatting_or_ranking_them():
    window = _load_window_module()
    for value in [math.nan, math.inf, -math.inf, "NaN", "Infinity", -1, 0, True]:
        assert window.optional_positive_float(value) is None
