import os
import sys

import requests

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import oanda_calculator_web as web_app


class _DummyResponse:
    def __init__(self, status_code, json_payload=None, text=""):
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        if self._json_payload is None:
            raise ValueError("not json")
        return self._json_payload


def test_execute_now_preserves_upstream_http_error_json(monkeypatch):
    def fake_post(_url, json=None, timeout=15):
        return _DummyResponse(500, {"errorMessage": "upstream failed", "code": 99})

    monkeypatch.setattr(web_app.requests, "post", fake_post)

    client = web_app.app.test_client()
    resp = client.post("/execute_now", json={"symbol": "NZD_USD", "action": "buy", "quantity": 1})

    payload = resp.get_json()
    assert resp.status_code == 500
    assert payload["status"] == "error"
    assert payload["upstream_status"] == 500
    assert payload["detail"] == {"errorMessage": "upstream failed", "code": 99}


def test_execute_now_preserves_upstream_http_error_text(monkeypatch):
    def fake_post(_url, json=None, timeout=15):
        return _DummyResponse(400, None, "Bad request from upstream")

    monkeypatch.setattr(web_app.requests, "post", fake_post)

    client = web_app.app.test_client()
    resp = client.post("/execute_now", json={"symbol": "NZD_USD", "action": "buy", "quantity": 1})

    payload = resp.get_json()
    assert resp.status_code == 400
    assert payload["status"] == "error"
    assert payload["upstream_status"] == 400
    assert payload["detail"] == "Bad request from upstream"
