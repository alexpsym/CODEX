import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_calculator_api", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_scripts_page_contains_calculator_row() -> None:
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    calc = next((row for row in payload if row.get("name") == "calculator"), None)
    assert calc is not None
    assert calc["open_url"] == "/merged/calculator"


def test_merged_calculator_page_returns_200() -> None:
    response = asyncio.run(master_service.merged_calculator_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Position Size Calculator" in html


def test_bybit_quote_uses_tick_step_fee_and_no_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 0.5}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)

    payload = {
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    qty = float(body["quantity"])
    assert qty > 0
    assert float(body["estimated_total_loss_aud"]) <= 100.0001


def test_bybit_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 1}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "101"
    assert sell["entry_price"] == "100"


def test_oanda_quote_uses_display_precision_and_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {
            "prices": [{
                "bids": [{"price": "0.65000"}],
                "asks": [{"price": "0.65010"}],
                "homeConversions": [{"currency": "USD", "accountLoss": "1.5"}],
            }]
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    payload = {
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    assert body["tick_size"] == "0.00001"
    assert float(body["quantity"]) >= 1
    assert float(body["quantity"]) < 500000  # spread is included in risk sizing; otherwise this would be larger


def test_oanda_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}], "homeConversions": [{"currency": "USD", "accountLoss": "1"}]}]}

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "1.1002"
    assert sell["entry_price"] == "1.1"


def test_limit_requires_entry_price() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "EUR_USD", "side": "buy", "order_type": "limit",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400


def test_missing_env_and_bad_symbols_return_real_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "", "", "https://bybit.test", "KEY1"))
    with pytest.raises(master_service.HTTPException) as bybit_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bybit_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: (_ for _ in ()).throw(ValueError("missing env")))
    with pytest.raises(master_service.HTTPException) as oanda_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "live", "symbol": "EUR_USD", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert oanda_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    async def no_symbols(*_args, **_kwargs):
        return []
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", no_symbols)
    with pytest.raises(master_service.HTTPException) as bad_symbol_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "NOTREAL", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bad_symbol_exc.value.status_code == 404


def test_submit_routes_to_existing_order_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"bybit": 0, "oanda": 0}

    async def fake_bybit(payload, request_id):
        calls["bybit"] += 1
        assert payload["timeframe"] == "15m"
        assert payload["stop_loss_price"] == "1"
        assert payload["take_profit_price"] == "2"
        return {"ok": True}

    async def fake_oanda(payload, request_id):
        calls["oanda"] += 1
        assert payload["timeframe"] == "1h"
        return {"ok": True}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_place_oanda_order", fake_oanda)

    asyncio.run(master_service.calculator_submit({
        "asset": "crypto", "account": "live", "symbol": "BTCUSDT", "action": "buy", "order_type": "market",
        "entry_price": "100", "stop_loss_price": "1", "take_profit_price": "2", "quantity": "0.01", "timeframe": "15m",
    }))
    asyncio.run(master_service.calculator_submit({
        "asset": "fx", "account": "demo", "symbol": "EUR_USD", "action": "sell", "order_type": "limit",
        "entry_price": "1.2", "stop_loss_price": "1.3", "take_profit_price": "1.1", "quantity": "1000", "timeframe": "1h",
    }))
    assert calls == {"bybit": 1, "oanda": 1}
