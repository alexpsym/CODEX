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
    monkeypatch.setattr(master_service, "_wait_for_state_restore_or_error", lambda *args, **kwargs: asyncio.sleep(0, result={"enabled": True}))

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.set_watchlist(DummyRequest()))
    assert exc.value.status_code == 400
    assert captured["called"] is False


def test_is_likely_fx_pair_avoids_six_letter_crypto_false_positive() -> None:
    assert master_service._is_likely_fx_pair("BRUSDT") is False
    assert master_service._is_likely_fx_pair("OPUSDT") is False
    assert master_service._is_likely_fx_pair("EURUSD") is True
    assert master_service._is_likely_fx_pair("XAUUSD") is True


def test_watchlist_mixed_crypto_fx_persists_canonical_values(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = {"items": None}

    async def fake_resolve(symbol: str, prefer: str = "bybit", scope: str = "all"):
        if symbol == "BRUSDT":
            return {"resolved_symbol": "BRUSDT"}
        return None

    def fake_set_watchlist(items):
        saved["items"] = list(items)
        return list(items)

    class DummyRequest:
        async def json(self):
            return {"items": ["BRUSDT", "EURUSD"]}

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_set_watchlist", fake_set_watchlist)
    monkeypatch.setattr(master_service, "_wait_for_state_restore_or_error", lambda *args, **kwargs: asyncio.sleep(0, result={"enabled": True}))
    def fake_upload_and_verify(_key, payload, verifier=None):
        if verifier:
            verifier(payload)
        return {
            "enabled": True,
            "last_verified_at": "now",
            "last_verified_watchlist": ["BRUSDT", "EUR_USD"],
        }

    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", fake_upload_and_verify)

    response = asyncio.run(master_service.set_watchlist(DummyRequest()))
    payload = response.body.decode("utf-8")
    assert "\"BRUSDT\"" in payload
    assert "\"EUR_USD\"" in payload
    assert saved["items"] == ["BRUSDT", "EUR_USD"]


def test_calculator_instrument_resolves_oanda_shorthand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "x", "account_id": "a", "token": "t"})
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_instrument_meta",
        lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1", "maximumOrderUnits": "1000", "marginRate": "0.05"}),
    )
    response = asyncio.run(master_service.calculator_instrument(asset="fx", account="demo", symbol="eurusd"))
    payload = response.body.decode("utf-8")
    assert "EUR_USD" in payload


def test_calculator_instrument_resolves_bybit_full_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_bybit_name_aliases_for_choices", lambda _base_url, _symbols: asyncio.sleep(0, result={"BITCOIN": "BTC"}))

    async def fake_get(*_args, **_kwargs):
        return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    response = asyncio.run(master_service.calculator_instrument(asset="crypto", account="live", symbol="Bitcoin USDT"))
    payload = response.body.decode("utf-8")
    assert "BTCUSDT" in payload


def test_calculator_instrument_full_name_alias_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_bybit_name_aliases_for_choices", lambda _base_url, _symbols: (_ for _ in ()).throw(RuntimeError("alias feed down")))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_instrument(asset="crypto", account="live", symbol="Bitcoin USDT"))
    assert exc.value.status_code == 503
