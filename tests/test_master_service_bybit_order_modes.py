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
