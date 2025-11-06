import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _load_app(monkeypatch):
    """Import app with a stubbed Flask module."""
    import types
    import importlib

    fake_flask = types.ModuleType("flask")

    class DummyFlask:  # minimal stand-in for flask.Flask
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - trivial
            pass

        def route(self, *args, **kwargs):  # type: ignore
            def decorator(func):
                return func

            return decorator

        post = route

    fake_flask.Flask = DummyFlask
    fake_flask.render_template_string = lambda *args, **kwargs: ""
    fake_flask.request = None
    fake_flask.send_file = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "flask", fake_flask)
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_range_all_time(monkeypatch):
    """Ensure "all" period only spans two years."""
    fake_now = datetime(2024, 1, 10, tzinfo=ZoneInfo("Australia/Brisbane"))

    class FakeDate:
        @classmethod
        def now(cls, tz=None):
            return fake_now

    app = _load_app(monkeypatch)

    monkeypatch.setattr(app, "datetime", FakeDate)
    start, end = app._range_from_period("all")
    assert start == (fake_now - timedelta(days=730)).strftime("%Y-%m-%d")
    assert end == fake_now.strftime("%Y-%m-%d")


def test_trade_prefers_manual_dates(monkeypatch):
    """Manual dates override quick range for trade downloads."""
    app = _load_app(monkeypatch)
    captured = {}

    class FakeForm(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    app.request = type("R", (), {"form": FakeForm({
        "start_date": "2024-01-01",
        "end_date": "2024-01-10",
        "period": "week",
    })})

    def fake_download(*args):
        captured["args"] = args
        return "file.csv"

    monkeypatch.setattr(app.fetch_history, "download_history", fake_download)
    app.send_file = lambda filename, as_attachment=True: filename

    result = app.trade()
    assert result == "file.csv"
    assert captured["args"][1:3] == ("2024-01-01", "2024-01-10")


def test_balance_prefers_manual_dates(monkeypatch):
    """Manual dates override quick range for balance downloads."""
    app = _load_app(monkeypatch)

    class FakeForm(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    app.request = type("R", (), {"form": FakeForm({
        "start_date": "2024-02-01",
        "end_date": "2024-02-15",
        "freq": "weekly",
    })})

    captured = {}

    def fake_export(start, end, freq):
        captured["params"] = (start, end, freq)
        return "balance.csv"

    monkeypatch.setattr(app, "export_balance_csv", fake_export)
    app.send_file = lambda filename, as_attachment=True: filename

    result = app.balance()
    assert result == "balance.csv"
    assert captured["params"] == ("2024-02-01", "2024-02-15", "weekly")
