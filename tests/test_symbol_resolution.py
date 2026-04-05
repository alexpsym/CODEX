import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("shared_symbol_resolution", ROOT / "shared" / "symbol_resolution.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

resolve_bybit_symbol_from_choices = mod.resolve_bybit_symbol_from_choices


def test_resolve_bybit_shorthand_usdt_preferred() -> None:
    symbols = ["BTCUSDT", "BTCUSDC", "ETHUSDT"]
    resolved = resolve_bybit_symbol_from_choices("BTC", symbols, preferred_quotes=("USDT", "USDC", "USD"))
    assert resolved is not None
    assert resolved["resolved_symbol"] == "BTCUSDT"


def test_resolve_bybit_full_symbol_pass_through() -> None:
    symbols = ["BTCUSDT", "BTCUSDC"]
    resolved = resolve_bybit_symbol_from_choices("BTCUSDC", symbols, preferred_quotes=("USDT", "USDC", "USD"))
    assert resolved is not None
    assert resolved["resolved_symbol"] == "BTCUSDC"


def test_resolve_bybit_unknown_returns_none() -> None:
    symbols = ["BTCUSDT", "ETHUSDT"]
    assert resolve_bybit_symbol_from_choices("UNKNOWN", symbols) is None
