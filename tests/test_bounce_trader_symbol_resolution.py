import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("flask")
SPEC = importlib.util.spec_from_file_location(
    "bounce_trader_app",
    ROOT / "bybit_trigger_bounce_trader" / "app.py",
)
app = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)


def test_shorthand_symbol_resolves_before_spawn(monkeypatch: pytest.MonkeyPatch):
    started: list[str] = []

    monkeypatch.setattr(app, "_save_config", lambda _cfg: None)
    monkeypatch.setattr(app, "_running_sessions", lambda: [])
    monkeypatch.setattr(app, "_resolve_bybit_symbol", lambda raw, category: "RAVEUSDT" if raw == "RAVE" else raw)
    monkeypatch.setattr(app, "_start_session", lambda _cfg, symbol: started.append(symbol) or "sid-1")

    client = app.APP.test_client()
    resp = client.post(
        "/",
        data={
            "action": "arm",
            "confirm_arm": "on",
            "market": "crypto",
            "account_mode": "demo",
            "symbols": "RAVE",
            "strategy": "EMA",
            "side": "Buy",
            "category": "linear",
            "trigger_by": "LastPrice",
            "interval": "1",
            "poll_seconds": "2",
            "ema_len": "9",
            "vwap_anchor": "session",
            "risk_mode": "fixed_qty",
            "risk_pct": "1",
            "rr_ratio": "2",
            "default_qty": "0.001",
            "qty_map": "{}",
            "sl_ticks": "0",
            "min_amend_ticks": "1",
            "min_gap_ticks": "2",
        },
    )
    assert resp.status_code == 200
    assert started == ["RAVEUSDT"]


def test_get_page_hides_save_and_confirmations() -> None:
    client = app.APP.test_client()
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    assert "value=\"save\"" not in body
    assert "confirm_arm" not in body
    assert "confirm_live" not in body


def test_get_page_has_specs_host_below_heading() -> None:
    client = app.APP.test_client()
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    heading_idx = body.find("<h1>Bounce Trader</h1>")
    canonical_idx = body.find("id=\"preview-canonical-symbol\"")
    specs_idx = body.find("id=\"preview-instrument-specs\"")
    notice_idx = body.find("notice error")
    market_idx = body.find("name=\"market\"")
    assert heading_idx >= 0
    assert heading_idx < canonical_idx < specs_idx
    assert specs_idx < notice_idx or notice_idx == -1
    assert specs_idx < market_idx


def test_full_symbol_passes_through_before_spawn(monkeypatch: pytest.MonkeyPatch):
    started: list[str] = []

    monkeypatch.setattr(app, "_save_config", lambda _cfg: None)
    monkeypatch.setattr(app, "_running_sessions", lambda: [])
    monkeypatch.setattr(app, "_resolve_bybit_symbol", lambda raw, category: raw)
    monkeypatch.setattr(app, "_start_session", lambda _cfg, symbol: started.append(symbol) or "sid-1")

    client = app.APP.test_client()
    client.post(
        "/",
        data={
            "action": "arm",
            "confirm_arm": "on",
            "market": "crypto",
            "account_mode": "demo",
            "symbols": "RAVEUSDT",
            "strategy": "EMA",
            "side": "Buy",
            "category": "linear",
        },
    )
    assert started == ["RAVEUSDT"]


def test_unknown_symbol_fails_without_spawning_session(monkeypatch: pytest.MonkeyPatch):
    started = {"called": False}

    def fail_resolve(raw: str, category: str) -> str:
        raise ValueError(f"Unable to resolve Bybit symbol '{raw}' in category '{category}'.")

    monkeypatch.setattr(app, "_save_config", lambda _cfg: None)
    monkeypatch.setattr(app, "_running_sessions", lambda: [])
    monkeypatch.setattr(app, "_resolve_bybit_symbol", fail_resolve)
    monkeypatch.setattr(app, "_start_session", lambda _cfg, _symbol: started.update({"called": True}) or "sid-1")

    client = app.APP.test_client()
    resp = client.post(
        "/",
        data={
            "action": "arm",
            "confirm_arm": "on",
            "market": "crypto",
            "account_mode": "demo",
            "symbols": "GARBAGE",
            "strategy": "EMA",
            "side": "Buy",
            "category": "linear",
        },
    )
    body = resp.get_data(as_text=True)
    assert "Unable to resolve Bybit symbol" in body
    assert started["called"] is False


def test_preview_endpoint_resolves_crypto_linear(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app, "_resolve_bybit_symbol", lambda raw, category: "BTCUSDT" if (raw, category) == ("BTC", "linear") else "")
    client = app.APP.test_client()
    resp = client.get("/api/preview-symbol?market=crypto&category=linear&symbols=BTC")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "resolved"
    assert payload["canonical"] == "BTCUSDT"
    assert payload["prefer"] == "bybit"


def test_preview_endpoint_passes_category(monkeypatch: pytest.MonkeyPatch):
    called = {}

    def fake_resolve(raw: str, category: str) -> str:
        called["args"] = (raw, category)
        return "BTCUSD"

    monkeypatch.setattr(app, "_resolve_bybit_symbol", fake_resolve)
    client = app.APP.test_client()
    resp = client.get("/api/preview-symbol?market=crypto&category=inverse&symbols=BTC")
    assert resp.status_code == 200
    assert called["args"] == ("BTC", "inverse")


def test_preview_endpoint_resolves_fx() -> None:
    client = app.APP.test_client()
    resp = client.get("/api/preview-symbol?market=fx&category=linear&symbols=EURUSD")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "resolved"
    assert payload["canonical"] == "EUR_USD"
    assert payload["prefer"] == "oanda"


def test_preview_endpoint_returns_multi_state() -> None:
    client = app.APP.test_client()
    resp = client.get("/api/preview-symbol?market=crypto&category=linear&symbols=BTC,ETH")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "multi"


def test_arm_submit_no_longer_requires_confirmations(monkeypatch: pytest.MonkeyPatch):
    started: list[str] = []
    monkeypatch.setattr(app, "_save_config", lambda _cfg: None)
    monkeypatch.setattr(app, "_running_sessions", lambda: [])
    monkeypatch.setattr(app, "_resolve_bybit_symbol", lambda raw, category: "BTCUSDT")
    monkeypatch.setattr(app, "_start_session", lambda _cfg, symbol: started.append(symbol) or "sid-1")
    client = app.APP.test_client()
    resp = client.post(
        "/",
        data={"action": "arm", "market": "crypto", "symbols": "BTC", "category": "linear"},
    )
    assert resp.status_code == 200
    assert started == ["BTCUSDT"]
