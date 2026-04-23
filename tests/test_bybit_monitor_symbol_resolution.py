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


def test_coerce_alert_rejects_non_usdt_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bybit_altcoin_monitor,
        "_get_linear_perpetual_symbols",
        lambda force=False: {"BTCUSDT", "ETHUSDT"},
    )
    payload = {
        "symbol": "BTCUSDC",
        "kind": "price",
        "direction": "above",
        "target_price": 1,
    }
    with pytest.raises(ValueError, match="USDT perpetual"):
        bybit_altcoin_monitor._coerce_alert(payload)


def test_fetch_linear_perpetual_symbols_filters_to_usdt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, params=None, headers=None, timeout=None):  # noqa: ANN001
            _ = (url, headers, timeout)
            self.calls += 1
            if self.calls == 1:
                assert "cursor" not in (params or {})
                return FakeResponse(
                    {
                        "retCode": 0,
                        "result": {
                            "list": [
                                {
                                    "symbol": "RESOLVUSDT",
                                    "status": "Trading",
                                    "contractType": "LinearPerpetual",
                                    "deliveryTime": "0",
                                    "quoteCoin": "USDT",
                                    "settleCoin": "USDT",
                                },
                                {
                                    "symbol": "RESOLVPERP",
                                    "status": "Trading",
                                    "contractType": "LinearPerpetual",
                                    "deliveryTime": "0",
                                    "quoteCoin": "USD",
                                    "settleCoin": "USD",
                                },
                            ],
                            "nextPageCursor": "next",
                        },
                    }
                )
            return FakeResponse(
                {
                    "retCode": 0,
                    "result": {
                        "list": [
                            {
                                "symbol": "BTCUSDC",
                                "status": "Trading",
                                "contractType": "LinearPerpetual",
                                "deliveryTime": "0",
                                "quoteCoin": "USDC",
                                "settleCoin": "USDC",
                            }
                        ],
                        "nextPageCursor": "",
                    },
                }
            )

    monkeypatch.setattr(bybit_altcoin_monitor, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(bybit_altcoin_monitor, "_iter_api_bases", lambda: ["https://example.test"])
    monkeypatch.setattr(bybit_altcoin_monitor, "_build_headers", lambda: {})
    symbols = bybit_altcoin_monitor._fetch_linear_perpetual_symbols()
    assert symbols == {"RESOLVUSDT"}


def test_fetch_fallback_prices_filters_to_usdt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = "[]"
        content = b"[]"

        def json(self):
            return [
                {"symbol": "BTCUSDT", "price": "10"},
                {"symbol": "BTCUSDC", "price": "20"},
                {"symbol": "RESOLVPERP", "price": "30"},
            ]

    class FakeSession:
        def get(self, url, timeout=None, headers=None):  # noqa: ANN001
            _ = (url, timeout, headers)
            return FakeResponse()

    monkeypatch.setattr(bybit_altcoin_monitor, "_get_session", lambda: FakeSession())
    prices = bybit_altcoin_monitor._fetch_fallback_prices()
    assert set(prices) == {"BTCUSDT"}
