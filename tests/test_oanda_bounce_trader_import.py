import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_oanda_bounce_trader_imports_shared_oanda_api() -> None:
    module_path = ROOT / "bybit_trigger_bounce_trader" / "oanda_trigger_bounce_trader.py"
    spec = importlib.util.spec_from_file_location("oanda_trigger_bounce_trader_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.oanda_api.__name__ == "shared.oanda_api"
