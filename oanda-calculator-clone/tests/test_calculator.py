import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import oanda_calculator_web as web_app


def test_form_includes_timeframe_field():
    client = web_app.app.test_client()
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'name="timeframe"' in html
    assert 'list="timeframe_suggestions"' in html


def test_post_includes_timeframe_in_alert_and_pending(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"item": {"id": "wh_oanda"}}

    captured_pending = {}

    def fake_post(_url, json=None, timeout=10):
        captured_pending.update(json or {})
        return DummyResponse()

    monkeypatch.setattr(web_app.requests, "post", fake_post)
    monkeypatch.setattr(
        web_app,
        "get_account_details",
        lambda mode: {"account": {"balance": "10000", "marginAvailable": "10000", "currency": "AUD"}},
    )
    monkeypatch.setattr(
        web_app,
        "get_instrument_details",
        lambda instrument, mode: {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.05", "type": "CURRENCY"},
    )
    monkeypatch.setattr(web_app, "get_price", lambda instrument, mode: 1.2)
    monkeypatch.setattr(
        web_app,
        "build_order",
        lambda *args, **kwargs: {"order": "ok"},
    )
    monkeypatch.setattr(web_app, "_get_available_instruments_cached", lambda mode: ["EUR_USD"])

    client = web_app.app.test_client()
    resp = client.post(
        "/",
        data={
            "account_mode": "demo",
            "instrument": "EUR_USD",
            "side": "buy",
            "order_type": "market",
            "track_pending": "yes",
            "risk_mode": "percent",
            "risk_pct": "1",
            "stop_ticks": "10",
            "rr_ratio": "2",
            "timeframe": "15-minute",
        },
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '"timeframe": "15-minute"' in html
    assert captured_pending.get("timeframe") == "15-minute"

