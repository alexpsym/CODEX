import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_oanda", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


class _DummyResponse:
    def __init__(self, status_code=201, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"orderCreateTransaction": {"id": "12345"}}
        self.text = text

    def json(self):
        return self._payload


class _DummyAsyncClient:
    def __init__(self, *_args, **_kwargs):
        self.response = _DummyResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *_args, **_kwargs):
        return self.response


def _base_payload():
    return {
        "symbol": "NZD_USD",
        "action": "buy",
        "quantity": 1000,
        "account": "demo",
        "order_type": "market",
        "script_name": "oanda-calculator-clone",
    }


@pytest.fixture
def oanda_order_mocks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        master_service,
        "_get_oanda_config",
        lambda account: {"base_url": "https://example.test", "account_id": "acct", "token": "tok1234"},
    )
    async def fake_meta(**_kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0}

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: False)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)


def test_place_oanda_order_without_pending_webhook_id_does_not_raise(oanda_order_mocks):
    result = asyncio.run(master_service._place_oanda_order(_base_payload(), request_id="req-1"))
    assert result["orderCreateTransaction"]["id"] == "12345"


def test_bookkeeping_failure_does_not_convert_success_to_error(oanda_order_mocks, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = asyncio.run(master_service._place_oanda_order(_base_payload(), request_id="req-2"))

    assert result["orderCreateTransaction"]["id"] == "12345"
    assert "warnings" in result
    assert any("bookkeeping failed" in warning.lower() for warning in result["warnings"])


def test_oanda_context_upsert_always_schedules_backup(oanda_order_mocks, monkeypatch: pytest.MonkeyPatch):
    calls = {"backup": 0}
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: False)
    monkeypatch.setattr(
        master_service,
        "_schedule_dropbox_upload_state_backup",
        lambda: calls.__setitem__("backup", calls["backup"] + 1),
    )

    result = asyncio.run(master_service._place_oanda_order(_base_payload(), request_id="req-3"))

    assert result["orderCreateTransaction"]["id"] == "12345"
    assert calls["backup"] == 1
