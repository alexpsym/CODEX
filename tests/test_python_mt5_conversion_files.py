import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_MT5_DIR = ROOT / "mt5-clone" / "python_mt5"
sys.path.insert(0, str(PY_MT5_DIR))


def _resolve_like_pepperstone_export(raw_symbol: str, chart_symbol: str, available_symbols: list[str]) -> str:
    requested = raw_symbol.strip().upper()
    if requested in available_symbols:
        return requested
    if chart_symbol.startswith(requested):
        suffix = chart_symbol[len(requested) :]
        if suffix and not suffix[0].isalnum() and chart_symbol in available_symbols:
            return chart_symbol
    for candidate in available_symbols:
        if not candidate.startswith(requested) or len(candidate) <= len(requested):
            continue
        suffix = candidate[len(requested) :]
        if suffix and not suffix[0].isalnum():
            return candidate
    return ""


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
    assert "EnablePepperstoneSpreadExport = true" in trader
    assert "PepperstoneSpreadExportIntervalSeconds = 300" in trader
    assert "PepperstoneSpreadExportSymbols" in trader
    assert "pepperstone_spreads_latest.json" in trader
    assert "MaybeExportPepperstoneSpreads();" in trader
    assert "BuildPepperstoneSpreadJson" in trader
    assert "TimeToString" not in trader
    assert "FileOpen(requestedPath, FILE_WRITE | FILE_TXT | FILE_ANSI)" in trader


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
    assert _resolve_like_pepperstone_export("EURUSD", "GBPUSD.a", ["EURUSDmicro", "EURUSD.a"]) == "EURUSD.a"
    assert "mt5_symbol" in trader
    assert "ResolvePepperstoneSpreadSymbol" in trader
    assert "string mt5Symbol = ResolvePepperstoneSpreadSymbol(tokens[i]);" in trader
    assert "SymbolsTotal(false)" in trader
    assert "SymbolName(i, false)" in trader
    assert "Pepperstone spread export resolved " in trader
