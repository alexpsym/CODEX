import json
import math
import os
import sys
import types

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import cryptocalculator as calc

class MockResponse:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def raise_for_status(self):
        pass

def mock_get(url, params=None, timeout=10):
    if url == calc.BYBIT_LINEAR_URL:
        return MockResponse(
            {
                "result": {
                    "list": [
                        {
                            "symbol": "TESTUSDT",
                            "lastPrice": "100",
                            "fundingRate": "0.0001",
                        }
                    ]
                }
            }
        )
    if url == calc.BYBIT_INSTRUMENT_INFO_LINEAR:
        return MockResponse({
            "result": {
                "list": [
                    {
                        "symbol": "TESTUSDT",
                        "priceFilter": {"tickSize": "0.5"},
                        "lotSizeFilter": {"minTrdQty": "0.1", "qtyStep": "0.1"},
                    }
                ]
            }
        })
    raise ValueError("Unexpected URL: " + url)

def test_calculate_trade(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)
    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear",
        "price_source": "bybit",
        "execution_exchange": "bybit",
    }
    trade = calc.calculate_trade(config)
    assert trade["quantity"] == 0.2
    assert trade["stop_price"] == 95
    assert trade["target_price"] == 110.5
    assert trade["net_profit"] >= trade["actual_risk"] * config["rr_ratio"]
    assert trade["achieved_rr"] >= config["rr_ratio"]
    assert trade["funding_rate"] == 0.0001


def read_webhook_json() -> dict:
    """Helper to read the webhook JSON portion from trade_webhook.txt."""
    with open("trade_webhook.txt", "r", encoding="utf-8") as f:
        content = f.read()
    json_part = content.split("\n\nWEBHOOK FUTURES:")[0].strip()
    return json.loads(json_part)


def test_save_webhook_json_buy(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)

    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear",
        "price_source": "bybit",
        "execution_exchange": "bybit",
    }

    trade = calc.calculate_trade(config)
    calc.save_webhook_json(trade)
    payload = read_webhook_json()

    target_dist = abs(trade["target_price"] - trade["entry_price"])
    expected_tp = f"{{close}} + {target_dist:.6f}".replace("{close}", "{{close}}")
    expected_sl = f"{{close}} - {trade['stop_distance']:.6f}".replace("{close}", "{{close}}")

    assert payload["take_profit_price"] == expected_tp
    assert payload["stop_loss_price"] == expected_sl


def test_save_webhook_json_sell(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)

    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "short",
        "trade_mode": "linear",
        "price_source": "bybit",
        "execution_exchange": "bybit",
    }

    trade = calc.calculate_trade(config)
    calc.save_webhook_json(trade)
    payload = read_webhook_json()

    target_dist = abs(trade["target_price"] - trade["entry_price"])
    expected_tp = f"{{close}} - {target_dist:.6f}".replace("{close}", "{{close}}")
    expected_sl = f"{{close}} + {trade['stop_distance']:.6f}".replace("{close}", "{{close}}")

    assert payload["take_profit_price"] == expected_tp
    assert payload["stop_loss_price"] == expected_sl


def test_load_config_fetch_balance(monkeypatch, tmp_path):
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    monkeypatch.setattr(calc.requests, "get", lambda *a, **k: MockResponse({
        "result": {
            "list": [{"coin": [{"coin": "USDT", "availableToTrade": "150"}]}]
        }
    }))

    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "account_balance": "auto",
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear"
    }))

    cfg = calc.load_config(cfg_file)
    assert cfg["account_balance"] == 150.0
    assert cfg["price_source"] == "bybit"
    assert cfg["execution_exchange"] == "bybit"


def test_calculate_trade_cross_exchange(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)

    class DummyCoinspot(calc.ExchangeAdapter):
        name = "coinspot"

        def fetch_current_price(self, symbol, trade_mode):  # pragma: no cover - safety
            raise AssertionError("Coinspot price source should not be used in this test")

        def fetch_tick_size(self, symbol, trade_mode):  # pragma: no cover
            return 0.01

        def fetch_lot_size(self, symbol, trade_mode):
            return calc.LotSizeInfo(min_qty=0.05, qty_step=0.05)

        def fetch_fee_rate(self, trade_mode):
            return 0.0025

        def fetch_account_balance(self, coin="USDT", **_):  # pragma: no cover
            return 999.0

    dummy = DummyCoinspot()
    monkeypatch.setitem(calc.EXCHANGE_ADAPTERS, "coinspot", dummy)

    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear",
        "price_source": "bybit",
        "execution_exchange": "coinspot",
    }

    trade = calc.calculate_trade(config)

    assert trade["price_source"] == "bybit"
    assert trade["execution_exchange"] == "coinspot"
    assert trade["entry_price"] == 100
    assert trade["quantity_step"] == 0.05

    fee_rate = 0.0025
    expected_fees = (
        trade["entry_price"] * trade["quantity"] * fee_rate
        + trade["target_price"] * trade["quantity"] * fee_rate
    )
    assert math.isclose(trade["fees"], expected_fees, rel_tol=1e-9)
    assert trade["funding_rate"] == 0.0001


def test_open_in_edge_uses_registered_browser(monkeypatch):
    import cryptocalculator_web as web_app

    opened = []

    class DummyBrowser:
        def open(self, url, *_args, **_kwargs):
            opened.append(url)
            return True

    def fake_get(name=None):
        if name == "microsoft-edge":
            return DummyBrowser()
        raise web_app.webbrowser.Error()

    monkeypatch.setattr(web_app.webbrowser, "get", fake_get)

    assert web_app.open_in_edge("http://example.com") is True
    assert opened == ["http://example.com"]


def test_open_in_edge_falls_back_to_executable(monkeypatch):
    import cryptocalculator_web as web_app

    def always_fail(*_args, **_kwargs):  # pylint: disable=unused-argument
        raise web_app.webbrowser.Error()

    monkeypatch.setattr(web_app.webbrowser, "get", always_fail)

    monkeypatch.setattr(web_app.shutil, "which", lambda name: "/usr/bin/msedge" if name == "microsoft-edge" else None)

    opened = []

    class DummyBrowser:
        def __init__(self, path):
            self.path = path

        def open(self, url, *_args, **_kwargs):
            opened.append((self.path, url))
            return True

    monkeypatch.setattr(web_app.webbrowser, "BackgroundBrowser", lambda path: DummyBrowser(path))

    assert web_app.open_in_edge("http://example.com") is True
    assert opened == [("/usr/bin/msedge", "http://example.com")]


def test_open_in_edge_returns_false_when_edge_missing(monkeypatch):
    import cryptocalculator_web as web_app

    def always_fail(*_args, **_kwargs):  # pylint: disable=unused-argument
        raise web_app.webbrowser.Error()

    monkeypatch.setattr(web_app.webbrowser, "get", always_fail)
    monkeypatch.setattr(web_app.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_app, "EDGE_FALLBACK_PATHS", tuple())
    monkeypatch.setattr(web_app, "sys", types.SimpleNamespace(platform="linux"))

    assert web_app.open_in_edge("http://example.com") is False
