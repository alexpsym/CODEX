import os
import sys
import csv
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _setup_flask(monkeypatch):
    fake_flask = types.ModuleType("flask")
    class DummyFlask:
        def __init__(self, *args, **kwargs):
            pass
        def route(self, *a, **k):
            def dec(f):
                return f
            return dec
        post = route
    fake_flask.Flask = DummyFlask
    fake_flask.render_template_string = lambda *a, **k: ""
    fake_flask.request = None
    fake_flask.send_file = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "flask", fake_flask)


def test_bnb_loss_reduces_usdt_balance(monkeypatch):
    _setup_flask(monkeypatch)
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    import importlib
    app = importlib.import_module("app")
    fetch_history = importlib.import_module("fetch_history")

    monkeypatch.setattr(fetch_history, "_get_balance_before", lambda s, t: 100.0)

    log = {"coin": "BNB", "transactionTime": 0, "change": "-0.01"}
    def fake_pages(session, **params):
        yield [log]
    monkeypatch.setattr(fetch_history, "_fetch_transaction_pages", fake_pages)
    monkeypatch.setattr(fetch_history, "_get_price", lambda s, c, t: 300.0)

    class DummySession:
        def get_wallet_balance(self, **kwargs):
            return {"result": {"list": [{"coin": []}]}}
    monkeypatch.setattr(fetch_history, "HTTP", lambda api_key, api_secret: DummySession())

    fname = app.export_balance_csv("1970-01-01", "1970-01-01", "daily")
    with open(fname, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "97.0"
