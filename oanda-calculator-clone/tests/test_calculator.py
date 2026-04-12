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
    assert 'data-input="timeframe"' in html
    assert 'value="1-week"' in html
    assert 'value="1-month"' in html
    assert 'value="3-minute"' not in html
    assert 'Show in Dashboard Open Orders' not in html
    assert 'Webhook' in html


def test_embedded_form_action_and_heading():
    client = web_app.app.test_client()
    resp = client.get(
        "/?embedded=1&shell=merged&title=Position+Size+Calculator",
        headers={"X-Forwarded-Prefix": "/apps/oanda-calculator-clone"},
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'action="/apps/oanda-calculator-clone/?embedded=1&amp;shell=merged&amp;title=Position+Size+Calculator"' in html
    assert "OANDA Position Size Calculator" not in html
    assert "<h1>Position Size Calculator</h1>" not in html
    assert 'data-merged-switch-asset="crypto"' in html
    assert 'data-merged-switch-asset="fx"' in html
    assert html.index("<label>Asset:</label>") < html.index("<label>Account:</label>")


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


def test_embedded_template_reports_fx_height_channel():
    assert 'source: "fx"' in web_app.HTML_TEMPLATE
    assert 'source: "oanda"' not in web_app.HTML_TEMPLATE
