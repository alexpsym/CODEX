import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("bybit_altcoin_monitor", ROOT / "bybit_monitor" / "bybit_altcoin_monitor.py")
bybit_altcoin_monitor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = bybit_altcoin_monitor
SPEC.loader.exec_module(bybit_altcoin_monitor)


def test_coerce_alert_resolves_shorthand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bybit_altcoin_monitor,
        "_get_linear_perpetual_symbols",
        lambda force=False: {"BTCUSDT", "ETHUSDT"},
    )
    payload = {
        "symbol": "BTC",
        "kind": "price",
        "direction": "above",
        "target_price": 100000,
    }
    alert = bybit_altcoin_monitor._coerce_alert(payload)
    assert alert["symbol"] == "BTCUSDT"


def test_coerce_alert_unknown_symbol_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bybit_altcoin_monitor,
        "_get_linear_perpetual_symbols",
        lambda force=False: {"BTCUSDT", "ETHUSDT"},
    )
    payload = {
        "symbol": "NOPE",
        "kind": "price",
        "direction": "above",
        "target_price": 1,
    }
    with pytest.raises(ValueError, match="Unable to resolve"):
        bybit_altcoin_monitor._coerce_alert(payload)
