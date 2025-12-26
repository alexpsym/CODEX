import json
import math
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import cryptocalculator as calc
from exchange_adapters import COINSPOT_SPOT_FEE_RATE, InstrumentInfo


class DummyAdapter:
    """Simple stub that exposes the exchange adapter interface."""

    def __init__(self, *, price=100.0, fee_rate=0.0006, funding=0.0001, balance=100.0):
        self.price = price
        self.fee_rate = fee_rate
        self.funding = funding
        self.balance = balance
        self.instrument = InstrumentInfo(tick_size=0.5, min_qty=0.1, qty_step=0.1)

    def get_current_price(self, symbol, trade_mode, config=None):  # pragma: no cover - simple stub
        return self.price

    def get_instrument_info(self, symbol, trade_mode, config=None):  # pragma: no cover - simple stub
        return self.instrument

    def get_fee_rate(self, trade_mode):  # pragma: no cover - simple stub
        return self.fee_rate

    def get_funding_rate(self, symbol, trade_mode, config=None):  # pragma: no cover - simple stub
        return self.funding if trade_mode == "linear" else None

    def get_account_balance(self, config):  # pragma: no cover - simple stub
        return self.balance


def read_webhook_json() -> dict:
    """Helper to read the webhook JSON portion from trade_webhook.txt."""

    with open("trade_webhook.txt", "r", encoding="utf-8") as f:
        content = f.read()
    json_part = content.split("\n\nWEBHOOK FUTURES:")[0].strip()
    return json.loads(json_part)

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
    adapter = DummyAdapter()
    monkeypatch.setattr(calc, "get_exchange_adapter", lambda name: adapter)

    config = {
        "exchange": "bybit",
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
        "price_to_execution_rate": 1.5,
    }

    trade = calc.calculate_trade(config)

    assert trade["execution_exchange"] == "coinspot"
    assert trade["price_source"] == "bybit_linear"
    assert trade["price_quote_asset"] == "USDT"
    assert trade["execution_quote_asset"] == "AUD"
    assert trade["entry_price_execution"] == pytest.approx(
        trade["entry_price"] * config["price_to_execution_rate"]
    )
    expected_fees = trade["quantity"] * (
        trade["entry_price_execution"] + trade["target_price_execution"]
    ) * calc.COINSPOT_MARKET_FEE_RATE
    assert trade["fees"] == pytest.approx(expected_fees)
    assert trade["gross_reward_quote"] == pytest.approx(
        trade["gross_reward"] / config["price_to_execution_rate"]
    )
    assert trade["actual_risk_quote"] == pytest.approx(
        trade["actual_risk"] / config["price_to_execution_rate"]
    )


def test_coinspot_requires_conversion_rate(monkeypatch):
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

    with pytest.raises(ValueError):
        calc.calculate_trade(config)


def test_coinspot_requires_rate_when_quote_unknown(monkeypatch):
    monkeypatch.setattr(calc.requests, "get", mock_get)
    monkeypatch.setattr(calc, "fetch_coinspot_lot_info", lambda symbol: (0.1, 0.1))

    config = {
        "account_balance": 100,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "BTCUSDC",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "linear",
        "execution_exchange": "coinspot",
        "price_source": "bybit_linear",
    }

    with pytest.raises(ValueError):
        calc.calculate_trade(config)


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
    assert 'name="price_to_execution_rate"' in html


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
    adapter = DummyAdapter()
    monkeypatch.setattr(calc, "get_exchange_adapter", lambda name: adapter)

    config = {
        "exchange": "bybit",
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
    assert payload["tp_offset"] == round(target_dist, 6)
    assert payload["sl_offset"] == round(-trade["stop_distance"], 6)


def test_web_coinspot_passes_conversion_rate(monkeypatch):
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
            "price_source": "bybit_linear",
            "execution_exchange": "coinspot",
            "trade_mode": "linear",
        }

    monkeypatch.setattr(web_app, "calculate_trade", fake_calculate_trade)
    monkeypatch.setattr(web_app, "format_trade", lambda trade: "summary text")
    monkeypatch.setattr(web_app, "build_webhook_payload", lambda trade: {"ok": True})

    def fake_balance():
        return 999.0

    monkeypatch.setitem(web_app.BALANCE_ADAPTERS, "coinspot", fake_balance)

    resp = client.post(
        "/",
        data={
            "symbol": "BTCUSDT",
            "direction": "long",
            "order_type": "market",
            "stop_loss_ticks": "10",
            "risk_percent": "1",
            "rr_ratio": "2",
            "execution_exchange": "coinspot",
            "price_source": "bybit_linear",
            "price_to_execution_rate": "1.55",
        },
    )

    assert resp.status_code == 200
    assert captured_config["execution_exchange"] == "coinspot"
    assert captured_config["price_source"] == "bybit_linear"
    assert captured_config["account_balance"] == 999.0
    assert captured_config["account_asset"] == "AUD"
    assert captured_config["price_to_execution_rate"] == pytest.approx(1.55)


def test_save_webhook_json_sell(monkeypatch):
    adapter = DummyAdapter()
    monkeypatch.setattr(calc, "get_exchange_adapter", lambda name: adapter)

    config = {
        "exchange": "bybit",
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
    assert payload["tp_offset"] == round(-target_dist, 6)
    assert payload["sl_offset"] == round(trade["stop_distance"], 6)


def test_load_config_fetch_balance(monkeypatch, tmp_path):
    adapter = DummyAdapter(balance=150.0)
    monkeypatch.setattr(calc, "get_exchange_adapter", lambda name: adapter)

    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(
        json.dumps(
            {
                "exchange": "bybit",
                "account_balance": "auto",
                "risk_percent": 1,
                "rr_ratio": 2,
                "order_type": "market",
                "symbol": "TESTUSDT",
                "stop_loss_ticks": 10,
                "direction": "long",
                "trade_mode": "linear",
            }
        )
    )

    cfg = calc.load_config(cfg_file)
    assert cfg["account_balance"] == 150.0


def test_coinspot_fee_rate(monkeypatch):
    adapter = DummyAdapter(fee_rate=COINSPOT_SPOT_FEE_RATE, funding=None)
    monkeypatch.setattr(calc, "get_exchange_adapter", lambda name: adapter)

    config = {
        "exchange": "coinspot",
        "account_balance": 200,
        "risk_percent": 1,
        "rr_ratio": 2,
        "order_type": "market",
        "symbol": "BTCAUD",
        "stop_loss_ticks": 10,
        "direction": "long",
        "trade_mode": "spot",
        "base_asset": "BTC",
        "quote_asset": "AUD",
        "tick_size": 0.5,
        "qty_step": 0.1,
        "min_qty": 0.1,
    }

    trade = calc.calculate_trade(config)
    expected_fee_rate = COINSPOT_SPOT_FEE_RATE
    entry_fee = trade["position_usdt"] * expected_fee_rate
    exit_fee = trade["target_price"] * trade["quantity"] * expected_fee_rate
    assert abs(trade["fees"] - (entry_fee + exit_fee)) < 1e-9
    assert trade["funding_rate"] is None
    cfg = calc.load_config(cfg_file)
    assert cfg["account_balance"] == 150.0
    assert cfg["execution_exchange"] == "bybit"
    assert cfg["price_source"] == "bybit_linear"
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
        "price_to_execution_rate": 1.4,
    }

    trade = calc.calculate_trade(config)

    assert trade["price_source"] == "bybit"
    assert trade["execution_exchange"] == "coinspot"
    assert trade["entry_price"] == 100
    assert trade["quantity_step"] == 0.05

    fee_rate = 0.0025
    expected_fees = (
        trade["entry_price_execution"] * trade["quantity"] * fee_rate
        + trade["target_price_execution"] * trade["quantity"] * fee_rate
    )
    assert math.isclose(trade["fees"], expected_fees, rel_tol=1e-9)
    assert trade["funding_rate"] == 0.0001
    assert math.isclose(
        trade["net_profit"],
        trade["net_profit_quote"] * config["price_to_execution_rate"],
        rel_tol=1e-9,
    )


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

    monkeypatch.setattr(
        web_app.shutil,
        "which",
        lambda name: "/usr/bin/msedge" if name == "microsoft-edge" else None,
    )

    opened = []

    class DummyBrowser:
        def __init__(self, path):
            self.path = path

        def open(self, url, *_args, **_kwargs):
            opened.append((self.path, url))
            return True

    monkeypatch.setattr(
        web_app.webbrowser, "BackgroundBrowser", lambda path: DummyBrowser(path)
    )

    assert web_app.open_in_edge("http://example.com") is True
    assert opened == [("/usr/bin/msedge", "http://example.com")]


def test_open_in_edge_returns_false_when_edge_missing(monkeypatch):
    import cryptocalculator_web as web_app

    def always_fail(*_args, **_kwargs):  # pylint: disable=unused-argument
        raise web_app.webbrowser.Error()

    monkeypatch.setattr(web_app.webbrowser, "get", always_fail)
    monkeypatch.setattr(web_app.shutil, "which", lambda _name: None)

    assert web_app.open_in_edge("http://example.com") is False
