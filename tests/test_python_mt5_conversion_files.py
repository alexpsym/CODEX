import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_MT5_DIR = ROOT / "mt5-clone" / "python_mt5"
sys.path.insert(0, str(PY_MT5_DIR))


def _chart_suffix_like_pepperstone_export(chart_symbol: str) -> str:
    chart = chart_symbol.strip()
    suffix = chart[6:] if len(chart) > 6 else ""
    if suffix and not suffix[0].isalnum():
        return suffix
    return ""


def _chart_suffix_attempts_like_pepperstone_export(raw_symbol: str, chart_symbol: str) -> list[str]:
    exact = raw_symbol.strip()
    requested = exact.upper()
    attempts = [exact]
    if exact != requested:
        attempts.append(requested)
    suffix = _chart_suffix_like_pepperstone_export(chart_symbol)
    if suffix:
        attempts.append(requested + suffix)
    return attempts


def _resolve_like_pepperstone_export(raw_symbol: str, chart_symbol: str, available_symbols: list[str]) -> str:
    for candidate in _chart_suffix_attempts_like_pepperstone_export(raw_symbol, chart_symbol):
        if candidate in available_symbols:
            return candidate
    requested = raw_symbol.strip().upper()
    for candidate in available_symbols:
        if not candidate.startswith(requested) or len(candidate) <= len(requested):
            continue
        suffix = candidate[len(requested) :]
        if suffix and not suffix[0].isalnum():
            return candidate
    return ""


def _quote_export_like_pepperstone_bid_ask(bid: float, ask: float, point: float = 0.00001) -> dict:
    if bid <= 0 or ask <= 0:
        return {"available": False, "error": "bid/ask unavailable"}
    spread_points = (ask - bid) / point if point > 0 else 0.0
    midpoint = (ask + bid) / 2.0
    payload = {
        "available": True,
        "spread_pct": ((ask - bid) / midpoint) * 100.0,
        "spread_points": spread_points,
    }
    return payload


def test_python_mt5_conversion_files_exist_and_keep_mql5_files():
    assert (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").exists()
    assert (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Backtest.mq5").exists()
    for name in ["mt5_common.py", "trader_py.py", "backtest_py.py", "README.md"]:
        assert (PY_MT5_DIR / name).exists()


def test_python_mt5_modules_import_without_top_level_metatrader5():
    original = sys.modules.pop("MetaTrader5", None)
    for module_name in ["mt5_common", "trader_py", "backtest_py"]:
        sys.modules.pop(module_name, None)
    try:
        importlib.import_module("mt5_common")
        importlib.import_module("trader_py")
        importlib.import_module("backtest_py")
        assert "MetaTrader5" not in sys.modules
    finally:
        if original is not None:
            sys.modules["MetaTrader5"] = original


def test_python_mt5_trader_documents_and_uses_expected_mt5_actions():
    trader = (PY_MT5_DIR / "trader_py.py").read_text(encoding="utf-8")
    readme = (PY_MT5_DIR / "README.md").read_text(encoding="utf-8")
    assert "class StrategyMode" in trader
    assert "order_send" in (PY_MT5_DIR / "mt5_common.py").read_text(encoding="utf-8")
    assert "manual-limit" in trader
    assert "cannot read arbitrary chart trendline objects" in readme
    assert "Python MT5 requires a local Windows MT5 terminal open" in readme


def test_python_mt5_backtest_fetches_rates_and_warns_not_strategy_tester():
    backtest = (PY_MT5_DIR / "backtest_py.py").read_text(encoding="utf-8")
    readme = (PY_MT5_DIR / "README.md").read_text(encoding="utf-8")
    assert "copy_rates_range" in backtest
    assert "EMA" in backtest
    assert "ATR" in backtest.lower() or "atr" in backtest
    assert "not the MT5 Strategy Tester" in readme


def test_mql5_trader_exports_one_pepperstone_spread_json_file():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    spread_export = trader.split("// ---------- Pepperstone spread export helpers ----------", 1)[1]
    assert "EnablePepperstoneSpreadExport = true" in trader
    assert "PepperstoneSpreadExportIntervalSeconds = 300" in trader
    assert 'PepperstoneSpreadExportSymbols = ""' in trader
    assert "pepperstone_spreads_latest.json" in trader
    assert "MaybeExportPepperstoneSpreads();" in trader
    assert "BuildPepperstoneSpreadJson" in trader
    assert "TimeToString" not in spread_export
    assert "FileOpen(requestedPath, FILE_WRITE | FILE_TXT | FILE_ANSI)" in trader


def test_mql5_trader_exports_selected_market_watch_symbols_by_default():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    assert "int marketWatchCount = SymbolsTotal(true);" in trader
    assert "SymbolName(i, true)" in trader
    assert "Pepperstone Market Watch symbols found: " in trader
    assert "Pepperstone spread export wrote " in trader
    assert " Market Watch symbols" in trader
    assert "SymbolInfoTick(symbol, tick)" in trader
    assert r'\"available\":true' in trader
    assert r'\"available\":false' in trader
    assert "spread_points" in trader
    assert trader.find("SymbolsTotal(true)") < trader.find("StringSplit(PepperstoneSpreadExportSymbols")


def test_mql5_trader_treats_equal_bid_ask_as_valid_zero_spread_without_note():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    rounded = _quote_export_like_pepperstone_bid_ask(1.1000, 1.1000)
    unavailable = _quote_export_like_pepperstone_bid_ask(0.0, 1.1000)

    assert rounded["available"] is True
    assert rounded["spread_points"] == 0
    assert rounded["spread_pct"] == 0
    assert "spread_note" not in rounded
    assert unavailable["available"] is False

    bid_ask_body = trader.split("bool TryGetPepperstoneBidAsk", 1)[1].split("void AppendPepperstoneSpreadJsonEntry", 1)[0]
    assert "tick.bid > 0.0 && tick.ask > 0.0" in bid_ask_body
    assert "tick.ask > tick.bid" not in bid_ask_body
    assert "return (bid > 0.0 && ask > 0.0);" in bid_ask_body
    assert "ask > bid" not in bid_ask_body
    assert "SYMBOL_SPREAD" in trader
    assert "symbol_spread" in trader
    assert "spread_note" not in trader


def test_mql5_trader_trimtext_uses_in_place_string_trim_calls():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    trim_body = trader.split("string TrimText(string value)", 1)[1].split("string NormalizePepperstoneSpreadSymbol", 1)[0]
    assert "value = StringTrimLeft" not in trim_body
    assert "value = StringTrimRight" not in trim_body
    assert "StringTrimLeft(value);" in trim_body
    assert "StringTrimRight(value);" in trim_body


def test_mql5_trader_resolves_pepperstone_dot_suffix_symbols():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    assert _resolve_like_pepperstone_export(" eurusd ", "EURUSD.a", ["GBPUSD.a", "EURUSD.a"]) == "EURUSD.a"
    assert _resolve_like_pepperstone_export("GBPUSD", "EURUSD.a", ["GBPUSD.a"]) == "GBPUSD.a"
    assert _resolve_like_pepperstone_export("USDJPY", "EURUSD.a", ["USDJPY.a"]) == "USDJPY.a"
    assert "GBPUSD.a" in _chart_suffix_attempts_like_pepperstone_export("GBPUSD", "EURUSD.a")
    assert "USDJPY.a" in _chart_suffix_attempts_like_pepperstone_export("USDJPY", "EURUSD.a")
    assert _resolve_like_pepperstone_export("EURUSD", "GBPUSD.a", ["EURUSDmicro", "EURUSD.a"]) == "EURUSD.a"
    assert "mt5_symbol" in trader
    assert "ResolvePepperstoneSpreadSymbol" in trader
    assert "ExtractPepperstoneChartSuffix" in trader
    assert "requestedSymbol + chartSuffix" in trader
    assert "string mt5Symbol = ResolvePepperstoneSpreadSymbol(tokens[i]);" in trader
    assert "SymbolsTotal(false)" in trader
    assert "SymbolName(i, false)" in trader
    assert "Pepperstone spread export resolved " in trader


def test_mql5_trader_standard_market_is_live_quote_anchored_and_terminal_token_gated():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    market = trader.split("bool ExecuteStandardMarketOnce()", 1)[1].split("void CancelAllPendingByMagic()", 1)[0]
    consume = trader.split("bool ConsumeStandardMarketToken", 1)[1].split("bool ExecuteStandardMarketOnce()", 1)[0]
    on_init = trader.split("int OnInit()", 1)[1].split("void OnDeinit", 1)[0]
    chart_event = trader.split("void OnChartEvent", 1)[1].split("void OnTick", 1)[0]

    assert "STRAT_STANDARD_MARKET = 3" in trader
    assert "input StandardMarketDirection StandardMarketSide" in trader
    assert "input string StandardMarketExecutionToken" in trader
    assert "SymbolInfoTick(_Symbol, liveTick)" in market
    assert "isBuy ? liveTick.ask : liveTick.bid" in market
    assert "BuildSLFromDistance(entry, isBuy" in market
    assert "ComputeVolumeFromRisk(entry, sl" in market
    assert "ComputeAutoTP_NetRR(entry, isBuy" in market
    assert "ValidateMarketStopsAtLiveQuote" in market
    assert "SYMBOL_TRADE_STOPS_LEVEL" in trader
    assert "SYMBOL_TRADE_FREEZE_LEVEL" in trader
    assert "SYMBOL_VOLUME_MIN" in trader and "SYMBOL_VOLUME_STEP" in trader and "SYMBOL_VOLUME_MAX" in trader
    assert "GlobalVariableCheck(key)" in consume
    assert "GlobalVariableSetOnCondition(key, marker, 0.0)" in consume
    assert "GlobalVariableSet(key, 0.0)" not in consume
    assert "AcquireStandardMarketGateLock(lockHandle" in consume
    assert "FILE_COMMON" in trader
    assert consume.index("AcquireStandardMarketGateLock(lockHandle") < consume.index("GlobalVariableCheck(key)")
    assert "GlobalVariablesFlush()" in consume
    assert "StringLen(key) > 63" in consume
    assert market.index("ConsumeStandardMarketToken") < market.index("trade.Buy")
    assert "already_consumed_token" in market
    assert "blocked_one_trade_rule" in market
    assert "HasBlockingPendingOrderForMarket" in market
    assert "invalid_stops" in market
    assert 'LogStandardMarketOutcome("accepted"' in market
    assert 'LogStandardMarketOutcome("rejected"' in market
    assert "token_fp=" in trader
    assert trader.count("ExecuteStandardMarketOnce();") == 1
    assert "ExecuteStandardMarketOnce();" not in on_init
    assert "ExecuteStandardMarketOnce();" in chart_event
    assert "STANDARD_MARKET_EXECUTE_BUTTON" in chart_event
    assert "if(Strategy == STRAT_STANDARD_MARKET) return;" in trader
    assert "EventSetTimer(1);" in trader


def test_trader_has_no_embedded_market_watch_spread_feed():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    window = (ROOT / "mt5-clone" / "spread_percent_window.py").read_text(encoding="utf-8")

    for removed in [
        "MarketWatchUnifiedFeed.json", "UnifiedMarketWatch", "g_unified",
        ".heartbeat", "ShellExecuteW", "EventSetMillisecondTimer",
    ]:
        assert removed not in trader
    assert "EnablePepperstoneSpreadExport" in trader
    assert "MaybeExportPepperstoneSpreads();" in trader
    assert "MarketWatchSpreadPercentFeed.json" not in trader
    assert "MarketWatchUnifiedFeed.json" not in window
    assert "heartbeat" not in window.lower()
    assert "MarketWatchSpreadPercentFeed.json" in window
    assert "attach MarketWatchSpreadPercentFeed.mq5" in window
    assert "_feed_age_seconds" in window


def test_mql5_trader_standard_limit_retries_transient_failures_without_duplicate_send():
    trader = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    maintain = trader.split("void MaintainStandardLimit", 1)[1].split("void RefreshTrendlineNameFromInputs", 1)[0]
    placement = trader.split("bool PlaceOrReplacePendingLimitAtEntry", 2)[2].split("bool PlacePendingStandardLimit", 1)[0]

    assert 'MaintainStandardLimit("OnInit")' in trader
    assert 'MaintainStandardLimit("OnTick")' in trader
    assert 'MaintainStandardLimit("OnTimer")' in trader
    assert "STANDARD_LIMIT_MAX_ATTEMPTS" in maintain
    assert "g_standardLimitNextAttemptAt" in maintain
    assert "StandardLimitRetryDelaySeconds" in maintain
    assert maintain.index("FindMatchingPendingLimit") < maintain.index("PlacePendingStandardLimit")
    assert "FindAnyPendingLimitForEA" in maintain
    assert "blocked_nonmatching_pending" in maintain
    assert "g_standardLimitStructuralBlock" in maintain
    assert "Wrong-side/too-close limit price" in placement
    assert "mode=standard_limit preflight" in placement
    assert "mode=standard_limit broker_result" in placement
    assert "accepted_not_observable" in placement
    assert "IsPendingLimitTicketMatching(orderTicket" in placement
    cancel = trader.split("void CancelAllPendingByMagic()", 1)[1].split("datetime ComputeExpireAt", 1)[0]
    assert "ORDER_SYMBOL" in cancel
    assert "ORDER_MAGIC" in cancel


def test_market_watch_feed_validates_configured_desktop_paths_without_false_success():
    feed_path = ROOT / "mt5-clone" / "MQL5" / "Experts" / "MarketWatchSpreadPercentFeed.mq5"
    feed = feed_path.read_text(encoding="utf-8")
    docs = (ROOT / "mt5-clone" / "MARKET_WATCH_SPREAD_FEED.md").read_text(encoding="utf-8")

    assert 'input string PythonExecutable' in feed
    assert 'input string DesktopWindowScriptPath' in feed
    assert 'DesktopWindowScriptPath  = "C:\\\\GPT\\\\CODEX-master\\\\mt5-clone\\\\spread_percent_window.py"' in feed
    assert "MQLInfoInteger(MQL_DLLS_ALLOWED)" in feed
    assert "Allow DLL imports" in feed
    assert 'GetFileAttributesW(string file_name)' in feed
    assert "ConfiguredLaunchFileExists(python)" in feed
    assert "ConfiguredLaunchFileExists(script)" in feed
    assert "configured PythonExecutable does not exist or is not a file" in feed
    assert "configured DesktopWindowScriptPath does not exist or is not a file" in feed
    assert "ShellExecuteW accepted" in feed
    assert "Process startup is not yet confirmed" in feed
    assert "launched desktop spread window" not in feed
    assert r"C:\GPT\CODEX-master\mt5-clone\spread_percent_window.py" in docs
    assert "repository is moved" in docs
    assert "Allow DLL imports" in docs
