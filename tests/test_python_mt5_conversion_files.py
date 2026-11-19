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
    assert "PepperstoneSpreadExportIntervalSeconds = 30" in trader
    assert 'PepperstoneSpreadExportSymbols = ""' in trader
    assert "pepperstone_spreads_latest.json" in trader
    assert "MaybeExportPepperstoneSpreads();" in trader
    for field, constant in [("minimum_lot", "SYMBOL_VOLUME_MIN"), ("volume_step", "SYMBOL_VOLUME_STEP"),
                            ("maximum_lot", "SYMBOL_VOLUME_MAX"), ("tick_value", "SYMBOL_TRADE_TICK_VALUE")]:
        assert field in spread_export and f"SymbolInfoDouble(mt5Symbol,{constant})" in spread_export
    assert 'AccountInfoString(ACCOUNT_CURRENCY)' in spread_export
    assert 'quoteTick.time' in spread_export and 'ACCOUNT_TRADE_MODE' in spread_export
    assert '#property version   "2.50"' in trader and 'EA_VERSION = "2.50"' in trader
    assert 'MathFloor(vol / step)' in trader and 'riskTotal < riskMin' in trader
    assert 'EnforceOneTradeAtATime' in trader and 'ValidateVolumeForBroker' in trader
    assert "TERMINAL_CONNECTED)!=0 && interval>30) interval=30" in spread_export
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
    backtest = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Backtest.mq5").read_text(encoding="utf-8")
    market = trader.split("bool ExecuteStandardMarketOnce()", 1)[1].split("void CancelAllPendingByMagic()", 1)[0]
    consume = trader.split("bool ConsumeStandardMarketToken", 1)[1].split("bool ExecuteStandardMarketOnce()", 1)[0]
    desktop_market = trader.split("void ExecuteDesktopMarket", 1)[1].split("void ExecuteDesktopPendingAtEntry", 1)[0]
    desktop_dispatch = trader.split("void HandleDesktopTraderCommand", 1)[1].split("string IsoTimeUTC", 1)[0]
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
    assert "!UseDesktopTraderControls" in chart_event
    assert "StandardMarketSide == STD_MARKET_BUY" in desktop_market
    assert "SymbolInfoTick(_Symbol, liveTick)" in desktop_market
    assert "isBuy ? liveTick.ask : liveTick.bid" in desktop_market
    assert "BuildSLFromDistance(entry, isBuy" in desktop_market
    assert "ComputeVolumeFromRisk(entry, sl" in desktop_market
    assert "ComputeAutoTP_NetRR(entry, isBuy" in desktop_market
    assert "ValidateMarketStopsAtLiveQuote" in desktop_market
    assert desktop_dispatch.index("ConsumeDesktopTraderCommand(command") < desktop_dispatch.index(
        "ExecuteDesktopMarket(command.commandId, result)"
    )
    assert "StandardMarketExecutionToken" not in desktop_market
    assert "if(Strategy == STRAT_STANDARD_MARKET) return;" in trader
    assert "EventSetTimer(1);" in trader
    # Backtest retains its old policy; it is unchanged and out of scope.
    assert "double rBase = MathMax(RiskAUD_Target, finalAllInRisk);" in backtest
    assert "MathMax(riskRoundedAUD, riskBufferedAUD)" in backtest
    assert "Final normalized TP cannot satisfy the minimum Net R target." in backtest
    assert "adjustmentAttempts" in backtest
    for source in (trader, backtest):
        assert "OrderCalcProfit" in source
        assert "riskWorst > riskMax" in source or "riskBuffered > riskMax" in source
        assert "RiskAUD_Max" in source
    assert "Worst-case buffered risk exceeds RiskAUD_Max." in trader
    assert "FAIL: buffered risk" in backtest
    assert "double &outRiskBufferedAUD" in trader
    assert "outRiskBufferedAUD = riskWorst;" in trader
    assert "riskBufferedFinal" in backtest
    assert "riskRounded, riskBuffered" in trader
    assert "riskRoundedAUD, riskBufferedFinal" in backtest

    # Arithmetic/source checks only; native orders are deliberately untested.
    from fractions import Fraction as F
    from math import ceil, floor
    def select(per_lot, step, minimum=".01", maximum="100", old_min="9", target="10", cap="12"):
        per_lot,step,minimum,maximum,old_min,target,cap=map(F,(per_lot,step,minimum,maximum,old_min,target,cap))
        lower=max(old_min,target)
        first=max(1,ceil(minimum/step));last=floor(maximum/step)
        count=max(first,ceil(lower/(per_lot*step)))
        lot=count*step;loss=lot*per_lot
        if count>last or not lower<=loss<=cap:return None
        assert count==first or (count-1)*step*per_lot<lower
        return lot,loss
    for per_lot,step,expected in [("15",".01",(".67","10.05")),("28",".01",(".36","10.08")),
            ("20",".01",(".5","10")),("15",".25",(".75","11.25")),
            ("19.99999999999999999999",".01",(".51","10.1999999999999999999949"))]:
        assert select(per_lot,step)==tuple(map(F,expected))
    assert select("15",".01",minimum=".7")==tuple(map(F,(".7","10.5")))
    assert select("15",".01",maximum=".66") is None
    assert select("90",".1") is None
    assert select("15",".01",cap="9") is None  # upper cap never raised to the target
    sizing=trader.split("bool ComputeVolumeFromRisk(",1)[1].split("bool InPosition()",1)[0]
    assert 'RiskAUD_Min              = 10.0' in trader
    assert 'MathMax(RiskAUD_Min, RiskAUD_Target)' in sizing
    assert 'double riskMax = RiskAUD_Max;' in sizing
    assert 'MathCeil(riskTarget / (riskPerLotSizing * step))' in sizing
    assert 'previousRisk >= riskMin' in sizing and 'riskTotal < riskMin' in sizing
    assert 'if(riskNext < riskMin) break;' in sizing
    assert 'Final volume violates the nominal target floor or risk cap.' in sizing
    assert 'riskPerLotSizing += commissionRTPerLot' in sizing
    assert 'while(!portableAutoFit && riskWorst > riskMax' in sizing
    assert 'CalcBufferedRiskForVolume' in sizing and 'candidateRisk <= riskMax' in sizing
    assert 'MathLog10(step)' not in sizing
    assert '#property version   "2.52"' in trader and 'EA_VERSION = "2.52"' in trader

    auto=trader.split('bool ComputeAutoTP_NetRR(',1)[1].split('bool IsLimitPriceValid(',1)[0]
    assert 'double nominalAllInRisk = riskRoundedAUD;' in auto
    assert 'if(!IncludeCommissionInRisk) nominalAllInRisk += commissionRT;' in auto
    assert 'MathMax(riskRoundedAUD, riskBufferedAUD)' not in auto and 'MathMax(RiskAUD_Target' not in auto
    assert 'AutoTPAUDCents(nominalAllInRisk,rBaseCents,why)' in auto and 'rBaseCents*rrNumerator+rrDenominator/2' in auto
    assert 'requiredNetProfit + commissionRT' in auto
    assert 'if(AutoTP_SafetyPoints != 0)' in auto and 'pts += AutoTP_SafetyPoints' not in auto
    assert 'netAtTPCents!=requiredNetCents' in auto
    assert 'PriceLevelIsOnTradeGrid' in auto and 'BrokerMinimumDistancePoints' in auto
    assert 'OrderCalcProfit' in auto and 'Final normalized TP cannot satisfy the nominal Net R cash target on broker grids.' in auto
    from decimal import Decimal as D, ROUND_HALF_UP
    money=lambda x:D(x).quantize(D('.01'),rounding=ROUND_HALF_UP)
    def cash_target(nominal,buffered,commission,included,rr):
        base=money(D(nominal)+(D(0) if included else D(commission)))
        return base,money(base*D(rr)),money(base*D(rr))+D(commission)
    assert cash_target('10.20','11.78','0',True,'2.5')==(D('10.20'),D('25.50'),D('25.50'))
    assert cash_target('10.10','11.78','0',True,'2.5')[1]==D('25.25')
    assert cash_target('10.204','12','0',True,'2.5')[1]==D('25.50')
    assert cash_target('9.20','11','1',False,'2.5')==cash_target('10.20','12','1',True,'2.5')
    assert cash_target('10.20','10.20','0',True,'2.5')==cash_target('10.20','12','0',True,'2.5')
    assert D(30)*D('2.5')==75 # no88-tick cushion
    assert 'AutoTPDecimalParts(NetRR_Target,rrNumerator,rrDenominator,why)' in auto and 'maxMoneyInteger-rrDenominator/2' in auto
    def native_cent_product(cents,rr):
        numerator,denominator=D(rr).as_integer_ratio()
        return (cents*numerator+denominator//2)//denominator
    assert native_cent_product(100,'1.005')==101 # binary100*1.005 is below100.5
    assert native_cent_product(1020,'2.5')==2550
    assert native_cent_product(1010,'2.5')==2525
    money_helper=trader.split('bool AutoTPDecimalParts(',1)[1].split('bool ComputeAutoTP_NetRR(',1)[0]
    assert 'precision<=17' in money_helper and 'StringToDouble(candidate)==value' in money_helper
    assert 'numerator*100+denominator/2' in money_helper and 'MathRound' not in money_helper
    assert 'maximum/10' in money_helper and 'maximum-denominator/2' in money_helper
    # Python's shortest round-trip representation mirrors the native parser convention.
    native_money=lambda value:money(str(float(value)))
    assert native_money('10.075')==D('10.08')
    assert native_money('10.07499999999999')==D('10.07')
    assert native_money('0')==D('0') and native_money('1e-5')==D('0')
    assert native_money('1e5')==D('100000.00')


    assert 'double scale = 100000000.0;' in auto
    assert 'NormalizeDouble(tradeTick, 8) != tradeTick' in auto and 'tradeTick*scale > 2147483647.0' in auto
    from math import gcd
    point_units,tick_units=100000,150000
    assert D(point_units//gcd(point_units,tick_units)*tick_units)/D(100000000)==D('.003')



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
