import json
import os
import sys
import types

import pytest

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
    if url == calc.BYBIT_SPOT_URL:
        return MockResponse(
            {
                "result": {
                    "list": [
                        {
                            "symbol": "TESTUSDT",
                            "lastPrice": "100",
                        }
                    ]
                }
            }
        )
    if url == calc.BYBIT_INSTRUMENT_INFO_SPOT:
        return MockResponse(
            {
                "result": {
                    "list": [
                        {
                            "symbol": "TESTUSDT",
                            "priceFilter": {"tickSize": "0.25"},
                            "lotSizeFilter": {
                                "minTrdQty": "0.01",
                                "qtyStep": "0.01",
                            },
                        }
                    ]
                }
            }
        )
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
    }
    trade = calc.calculate_trade(config)
    assert trade["quantity"] == 0.2
    assert trade["stop_price"] == 95
    assert trade["target_price"] == 110.5
    assert trade["net_profit"] >= trade["actual_risk"] * config["rr_ratio"]
    assert trade["achieved_rr"] >= config["rr_ratio"]
    assert trade["funding_rate"] == 0.0001


def test_calculate_trade_coinspot_execution(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)
    monkeypatch.setattr(calc, "fetch_coinspot_lot_info", lambda symbol: (0.1, 0.1))

    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "TESTUSDT",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear",
        "execution_exchange": "coinspot",
        "price_source": "bybit_linear",
    }

    trade = calc.calculate_trade(config)

    assert trade["execution_exchange"] == "coinspot"
    assert trade["price_source"] == "bybit_linear"
    expected_fees = trade["quantity"] * (
        trade["entry_price"] + trade["target_price"]
    ) * calc.COINSPOT_MARKET_FEE_RATE
    assert trade["fees"] == pytest.approx(expected_fees)


def test_web_form_includes_exchange_fields():
    import cryptocalculator_web as web_app

    client = web_app.app.test_client()
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert 'name="execution_exchange"' in html
    assert 'value="bybit"' in html
    assert 'value="coinspot"' in html
    assert 'name="price_source"' in html
    assert 'value="bybit_linear"' in html
    assert 'value="bybit_spot"' in html
    assert 'value="coinspot_spot"' in html
    assert 'id="price_mode_note"' in html


def test_web_post_uses_exchange_and_price_source(monkeypatch):
    import cryptocalculator_web as web_app

    client = web_app.app.test_client()
    captured_config = {}

    def fake_calculate_trade(config):
        captured_config.clear()
        captured_config.update(config)
        return {
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 110.0,
            "actual_risk": 5.0,
            "stop_distance": 5.0,
        }

    monkeypatch.setattr(web_app, "calculate_trade", fake_calculate_trade)
    monkeypatch.setattr(web_app, "format_trade", lambda trade: "summary text")
    monkeypatch.setattr(web_app, "build_webhook_payload", lambda trade: {"ok": True})

    balance_calls = []

    def fake_balance():
        balance_calls.append(True)
        return 555.0

    monkeypatch.setitem(web_app.BALANCE_ADAPTERS, "bybit", fake_balance)

    resp = client.post(
        "/",
        data={
            "symbol": "TESTUSDT",
            "direction": "long",
            "order_type": "market",
            "stop_loss_ticks": "10",
            "risk_percent": "1",
            "rr_ratio": "2",
            "execution_exchange": "bybit",
            "price_source": "bybit_spot",
        },
    )

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Execution Settings" in html
    assert "Bybit Spot" in html
    assert "Spot" in html
    assert balance_calls == [True]
    assert captured_config["execution_exchange"] == "bybit"
    assert captured_config["price_source"] == "bybit_spot"
    assert captured_config["trade_mode"] == "spot"
    assert captured_config["account_balance"] == 555.0


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
    assert cfg["execution_exchange"] == "bybit"
    assert cfg["price_source"] == "bybit_linear"


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
