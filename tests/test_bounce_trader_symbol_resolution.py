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
