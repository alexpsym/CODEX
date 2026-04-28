import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_state_sync", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


class DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def test_watchlist_get_waits_for_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    master_service._STARTUP_STATE_RESTORE_DONE.clear()
    master_service._update_state_sync_status(enabled=True, restore_status="pending", restore_complete=False)
    monkeypatch.setattr(master_service, "_get_watchlist", lambda: ["DASHUSDT", "BTCUSDT"])

    async def runner():
        async def release_later():
            await asyncio.sleep(0.05)
            master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
            master_service._STARTUP_STATE_RESTORE_DONE.set()

        task = asyncio.create_task(release_later())
        started = time.monotonic()
        response = await master_service.get_watchlist()
        waited = time.monotonic() - started
        await task
        return response, waited

    response, waited = asyncio.run(runner())
    payload = json.loads(response.body.decode("utf-8"))
    assert waited >= 0.045
    assert payload["items"] == ["DASHUSDT", "BTCUSDT"]


def test_watchlist_post_upload_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    async def fake_wait(*_args, **_kwargs):
        return {"enabled": True, "restore_status": "done"}

    async def fake_upload(*_args, **_kwargs):
        raise master_service.HTTPException(
            status_code=502,
            detail={"error": "dropbox_upload_failed", "message": "upload failed"},
        )

    monkeypatch.setattr(master_service, "_wait_for_state_restore_or_error", fake_wait)
    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_upload_state_backup_now", fake_upload)
    monkeypatch.setattr(master_service, "_set_watchlist", lambda items: list(items))

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    assert exc.value.status_code == 502
    assert "dropbox_upload_failed" in str(exc.value.detail)


def test_watchlist_post_triggers_upload_and_returns_sync_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"upload_called": 0}

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    async def fake_wait(*_args, **_kwargs):
        return {"enabled": True, "restore_status": "done"}

    async def fake_upload(*_args, **_kwargs):
        captured["upload_called"] += 1
        return {"enabled": True, "restore_status": "done", "last_upload_error": None}

    monkeypatch.setattr(master_service, "_wait_for_state_restore_or_error", fake_wait)
    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_upload_state_backup_now", fake_upload)
    monkeypatch.setattr(master_service, "_set_watchlist", lambda items: list(items))

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["items"] == ["BTCUSDT"]
    assert captured["upload_called"] == 1
    assert payload["state_sync"]["enabled"] is True


def test_custom_alert_mutations_trigger_immediate_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"uploads": 0}
    async def fake_wait(*_args, **_kwargs):
        return {"enabled": True, "restore_status": "done"}
    async def fake_upload(*_args, **_kwargs):
        called["uploads"] += 1
        return {"enabled": True, "restore_status": "done"}
    monkeypatch.setattr(master_service, "_wait_for_state_restore_or_error", fake_wait)
    monkeypatch.setattr(master_service, "_upload_state_backup_now", fake_upload)
    monkeypatch.setattr(master_service.bybit_monitor, "upsert_custom_alert", lambda payload: {"id": "b1", **payload})
    monkeypatch.setattr(master_service.bybit_monitor, "delete_custom_alert", lambda _alert_id: None)
    monkeypatch.setattr(master_service.bybit_monitor, "set_custom_alert_enabled", lambda alert_id, enabled: {"id": alert_id, "enabled": enabled})

    asyncio.run(master_service.upsert_bybit_monitor_custom_alert(DummyRequest({"symbol": "BTCUSDT"})))
    asyncio.run(master_service.delete_bybit_monitor_custom_alert("b1"))
    asyncio.run(master_service.set_bybit_monitor_custom_alert_enabled("b1", DummyRequest({"enabled": True})))
    assert called["uploads"] == 3


def test_state_sync_status_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {"enabled": False, "restore_complete": True, "restore_status": "skipped", "backup_path": "/x"},
    )
    payload = json.loads(asyncio.run(master_service.state_sync_status()).body.decode("utf-8"))
    assert payload["enabled"] is False
    assert payload["restore_status"] == "skipped"
