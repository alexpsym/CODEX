import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_bybit_lookup_symbol_resolves_shorthand(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_symbols(_base_url: str, category: str):
        if category == "linear":
            return ["BTCUSDT", "ETHUSDT"]
        return []

    async def fake_get(_base_url: str, path: str, params: dict):
        assert path == "/v5/market/instruments-info"
        assert params["symbol"] == "BTCUSDT"
        return {"result": {"list": [{"symbol": "BTCUSDT"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)

    result = asyncio.run(master_service._bybit_lookup_symbol("https://api.bybit.com", "BTC"))
    assert result is not None
    assert result["symbol"] == "BTCUSDT"


def test_watchlist_rejects_invalid_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"called": False}

    async def fake_resolve(symbol: str, prefer: str = "bybit", scope: str = "all"):
        if symbol == "BTC":
            return {"resolved_symbol": "BTCUSDT"}
        if symbol == "ETH":
            return {"resolved_symbol": "ETHUSDT"}
        return None

    def fake_set_watchlist(_items):
        captured["called"] = True
        return []

    class DummyRequest:
        async def json(self):
            return {"items": ["BTC", "NOPE", "EURUSD"]}

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_set_watchlist", fake_set_watchlist)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.set_watchlist(DummyRequest()))
    assert exc.value.status_code == 400
    assert captured["called"] is False
