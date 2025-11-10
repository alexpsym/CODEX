import json
import os
import sys

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
    }
    trade = calc.calculate_trade(config)
    assert trade["quantity"] == 0.2
    assert trade["stop_price"] == 95
    assert trade["target_price"] == 110.5
    assert trade["net_profit"] >= trade["actual_risk"] * config["rr_ratio"]
    assert trade["achieved_rr"] >= config["rr_ratio"]
    assert trade["funding_rate"] == 0.0001


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
