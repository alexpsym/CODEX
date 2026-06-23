import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_MT5_DIR = ROOT / "mt5-clone" / "python_mt5"
sys.path.insert(0, str(PY_MT5_DIR))


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
