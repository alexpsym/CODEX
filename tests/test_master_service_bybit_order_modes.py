import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "render_master_service_bybit_modes", ROOT / "render" / "master_service.py"
)
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


class _DummyResponse:
    def __init__(self, payload=None):
        self._payload = payload or {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "oid-1", "orderLinkId": "link-1"},
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DummyAsyncClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *_args, **_kwargs):
        return _DummyResponse()


@pytest.fixture
def bybit_order_mocks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda mode: (mode, "k", "s", "https://api.test", "env"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: False)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)


@pytest.mark.asyncio
async def test_place_bybit_order_planned_entry_uses_absolute_levels(bybit_order_mocks, monkeypatch):
    captured = {}

    async def fake_wait_for_position_entry(**_kwargs):
        return {"avgPrice": "101.0", "positionIdx": 0, "size": "1"}

    async def fake_set_trading_stop(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_for_position_entry)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_trading_stop)

    payload = {
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": 1,
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "planned_entry_price": 100.0,
        "planned_stop_price": 95.0,
        "planned_target_price": 110.0,
        "level_anchor_mode": "planned_entry",
    }
    result = await master_service._place_bybit_order(payload, request_id="req-1")
    assert result["tpsl"] == {"ok": True}
    assert captured["take_profit"] == 110.0
    assert captured["stop_loss"] == 95.0


@pytest.mark.asyncio
async def test_place_bybit_order_actual_fill_rebases_offsets(bybit_order_mocks, monkeypatch):
    captured = {}

    async def fake_wait_for_position_entry(**_kwargs):
        return {"avgPrice": "101.0", "positionIdx": 0, "size": "1"}

    async def fake_set_trading_stop(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_for_position_entry)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_trading_stop)

    payload = {
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": 1,
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "tp_offset": 9.0,
        "sl_offset": -4.0,
        "level_anchor_mode": "actual_fill",
        "planned_stop_price": 95.0,
        "planned_target_price": 110.0,
    }
    await master_service._place_bybit_order(payload, request_id="req-2")
    assert captured["take_profit"] == pytest.approx(110.0)
    assert captured["stop_loss"] == pytest.approx(97.0)


@pytest.mark.asyncio
async def test_place_bybit_order_surfaces_tpsl_failure(bybit_order_mocks, monkeypatch):
    async def fake_wait_for_position_entry(**_kwargs):
        return {"avgPrice": "100.0", "positionIdx": 0, "size": "1"}

    async def fake_set_trading_stop(**_kwargs):
        raise ValueError("tp/sl failed")

    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_for_position_entry)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_trading_stop)

    payload = {
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": 1,
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "tp_offset": 10.0,
        "sl_offset": -5.0,
    }

    with pytest.raises(RuntimeError, match="TP/SL application failed"):
        await master_service._place_bybit_order(payload, request_id="req-3")


@pytest.mark.asyncio
async def test_place_bybit_market_order_uses_ioc_position_idx_and_tpsl(monkeypatch):
    captured = {"body": None}

    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda mode: (mode, "k", "s", "https://api.test", "env"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: False)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(
        master_service,
        "_wait_for_position_entry",
        lambda **_kwargs: asyncio.sleep(0, result={"size": "0.015", "avgPrice": "77343.8", "entryPrice": "77343.8", "positionIdx": 0}),
    )
    monkeypatch.setattr(
        master_service,
        "_fetch_bybit_positions",
        lambda **_kwargs: asyncio.sleep(0, result=[{"size": "0.015", "takeProfit": "76771", "stopLoss": "77490.9"}]),
    )
    monkeypatch.setattr(
        master_service,
        "_set_bybit_trading_stop",
        lambda **_kwargs: asyncio.sleep(0, result={"status": "ok"}),
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **kwargs):
            captured["body"] = kwargs.get("content")
            return _DummyResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", lambda *a, **k: _Client())

    await master_service._place_bybit_order(
        {
            "symbol": "BTCUSDT",
            "action": "sell",
            "quantity": "0.015",
            "account": "demo",
            "trade_mode": "linear",
            "order_type": "market",
            "stop_loss_price": "77490.9",
            "take_profit_price": "76771",
        },
        request_id="req-market",
    )
    sent = master_service.json.loads(captured["body"])
    assert sent["timeInForce"] == "IOC"
    assert sent["positionIdx"] == 0
    assert sent["tpslMode"] == "Full"
    assert sent["tpOrderType"] == "Market"
    assert sent["slOrderType"] == "Market"


@pytest.mark.asyncio
async def test_place_bybit_order_raises_structured_rejection(monkeypatch):
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda mode: (mode, "k", "s", "https://api.test", "env"),
    )
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_a, **_k: asyncio.sleep(0, result=None))

    class _RejectResponse(_DummyResponse):
        def __init__(self):
            super().__init__(
                {
                    "retCode": 10001,
                    "retMsg": "request parameter error",
                    "retExtInfo": {"hint": "bad positionIdx"},
                    "result": {},
                }
            )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _RejectResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", lambda *a, **k: _Client())

    with pytest.raises(master_service.BybitOrderRejected) as exc:
        await master_service._place_bybit_order(
            {
                "symbol": "BTCUSDT",
                "action": "sell",
                "quantity": "0.015",
                "account": "demo",
                "trade_mode": "linear",
                "order_type": "market",
            },
            request_id="req-reject",
        )
    assert exc.value.ret_code == 10001
    assert "request parameter error" in exc.value.ret_msg


@pytest.mark.asyncio
async def test_place_bybit_limit_order_with_attached_tpsl_does_not_wait_for_position_entry(bybit_order_mocks, monkeypatch):
    async def boom(**_kwargs):
        raise AssertionError("should not wait for position")
    monkeypatch.setattr(master_service, "_wait_for_position_entry", boom)
    payload={"symbol":"BTCUSDT","action":"buy","quantity":1,"account":"demo","trade_mode":"linear","order_type":"limit","entry_price":100,"stop_loss_price":95,"take_profit_price":110}
    result=await master_service._place_bybit_order(payload, request_id="req-limit")
    assert result["tpsl"]["status"]=="attached_to_order_create"
    assert (result.get("order") or {}).get("orderId")

@pytest.mark.asyncio
async def test_place_bybit_limit_order_requested_tpsl_without_absolute_levels_rejects_before_unprotected_create(bybit_order_mocks, monkeypatch):
    called={"post":False}
    async def fake_post(**_kwargs):
        called["post"]=True
        return {"retCode":0,"result":{"orderId":"1"}}
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_post)
    payload={"symbol":"BTCUSDT","action":"buy","quantity":1,"account":"demo","trade_mode":"linear","order_type":"limit","entry_price":100,"tp_offset":"x"}
    with pytest.raises(ValueError):
        await master_service._place_bybit_order(payload, request_id="req-limit-2")
    assert called["post"] is False

@pytest.mark.asyncio
async def test_place_bybit_order_blocks_invalid_linear_levels_before_create(monkeypatch):
    called = {'post': 0}
    monkeypatch.setattr(master_service, '_log_webhook_event', lambda *args, **kwargs: None)
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda mode: (mode,'k','s','https://api.test','env'))
    monkeypatch.setattr(master_service, '_bybit_lookup_symbol', lambda *_a, **_k: asyncio.sleep(0, result={'priceFilter': {'tickSize': '0.0001'}}))
    async def fake_get(base, path, params):
        if path.endswith('tickers'):
            return {'result': {'list': [{'lastPrice': '0.4939'}]}}
        raise AssertionError(path)
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    async def fake_post(**kwargs):
        called['post'] += 1
        return {'retCode': 0, 'result': {'orderId': 'x'}}
    monkeypatch.setattr(master_service, '_bybit_signed_post', fake_post)

    with pytest.raises(master_service.BybitPreSubmitValidationError):
        await master_service._place_bybit_order({'symbol':'PARTIUSDT','action':'buy','quantity':'1','account':'demo','trade_mode':'linear','order_type':'limit','price':'0.5313','stop_loss_price':'0.5276','take_profit_price':'0.5400'}, request_id='rid')
    assert called['post'] == 0


@pytest.mark.asyncio
async def test_market_submit_uses_planned_entry_validation_anchor_and_no_price(monkeypatch):
    captured = {"body": None}
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *a, **k: None)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda mode: (mode, "k", "s", "https://api.test", "env"))
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: False)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.5"}}))
    monkeypatch.setattr(master_service, "_bybit_get_async", lambda *_a, **_k: asyncio.sleep(0, result={"result": {"list": [{"lastPrice": "79200"}]}}))
    monkeypatch.setattr(master_service, "_wait_for_position_entry", lambda **_k: asyncio.sleep(0, result={"size": "0.012", "avgPrice": "79300", "entryPrice": "79300", "positionIdx": 0}))
    monkeypatch.setattr(master_service, "_fetch_bybit_positions", lambda **_k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", lambda **_k: asyncio.sleep(0, result={"ok": True}))
    async def fake_post(**kwargs):
        captured["body"] = kwargs["body"]
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "1", "orderLinkId": "l1"}}
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_post)
    payload = {"symbol":"BTCUSDT","action":"buy","quantity":"0.012","account":"demo","trade_mode":"linear","order_type":"market","entry_price":"79300","planned_entry_price":"79300","stop_loss_price":"78784.5","take_profit_price":"79669","level_anchor_mode":"actual_fill"}
    result = await master_service._place_bybit_order(payload, request_id="r1")
    assert captured["body"]["orderType"] == "Market"
    assert captured["body"]["timeInForce"] == "IOC"
    assert "price" not in captured["body"]
    assert captured["body"]["tpslMode"] == "Full"
    assert captured["body"]["tpOrderType"] == "Market"
    assert captured["body"]["slOrderType"] == "Market"
    assert result["submit_level_adjustments"]["entry_validation_source"] == "planned_entry_price"


@pytest.mark.asyncio
async def test_bybit_tpsl_rejects_when_tick_size_unavailable(monkeypatch):
    called = {"post": 0}
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *a, **k: None)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda mode: (mode, "k", "s", "https://api.test", "env"))
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {}}))
    async def fake_post(**_kwargs):
        called["post"] += 1
        return {"retCode": 0, "result": {"orderId": "x"}}
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_post)
    with pytest.raises(master_service.BybitPreSubmitValidationError) as exc:
        await master_service._place_bybit_order({"symbol":"BTCUSDT","action":"buy","quantity":"0.01","account":"demo","trade_mode":"linear","order_type":"market","stop_loss_price":"78000","take_profit_price":"80000"}, request_id="rid-ts")
    assert exc.value.code == "BYBIT_TICK_SIZE_UNAVAILABLE"
    assert called["post"] == 0
