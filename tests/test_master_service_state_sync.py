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


def _mk_dropbox_store():
    return {"payload": b""}


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


def test_watchlist_post_verifies_remote_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _mk_dropbox_store()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    def fake_upload(_path: str, payload: bytes):
        store["payload"] = payload

    def fake_download(_path: str):
        return store["payload"]

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", fake_upload)
    monkeypatch.setattr(master_service, "_dropbox_download_bytes", fake_download)

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert "BTCUSDT" in payload["state_sync"]["last_verified_watchlist"]


def test_watchlist_post_fails_when_remote_misses_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    remote = {"payload": json.dumps({"watchlist": ["DASHUSDT"]}).encode("utf-8")}

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda _path, _payload: None)
    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: remote["payload"])

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    assert exc.value.status_code == 502
    assert "verification mismatch" in str(exc.value.detail).lower()


def test_lifecycle_repo_replace_restores_watchlist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _mk_dropbox_store()
    first_watchlist = tmp_path / "inst_a_watchlist.json"
    second_watchlist = tmp_path / "inst_b_watchlist.json"
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", first_watchlist)
    monkeypatch.setattr(master_service, "_WATCHLIST_CACHE", None)
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda _path, payload: store.__setitem__("payload", payload))
    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: store["payload"])

    write_resp = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    assert json.loads(write_resp.body.decode("utf-8"))["ok"] is True

    first_watchlist.unlink(missing_ok=True)
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", second_watchlist)
    monkeypatch.setattr(master_service, "_WATCHLIST_CACHE", None)
    master_service._STARTUP_STATE_RESTORE_DONE.clear()
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    read_resp = asyncio.run(master_service.get_watchlist())
    assert "BTCUSDT" in json.loads(read_resp.body.decode("utf-8"))["items"]


def test_scanner_local_ui_mode_still_runs_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"restore": 0}
    monkeypatch.setattr(master_service, "_is_scanner_local_ui_mode", lambda: True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", True)
    monkeypatch.setattr(master_service, "_dropbox_restore_state_backup_on_startup", lambda: called.__setitem__("restore", called["restore"] + 1) or asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_log_outbound_traffic_summary", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_poll_pending_webhook_invalidations", lambda: asyncio.sleep(0))
    asyncio.run(master_service._autostart_scripts())
    assert called["restore"] == 1


def test_state_sync_status_blocks_when_dropbox_disabled_and_not_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", False)
    payload = master_service._state_sync_status_snapshot()
    assert payload["restore_status"] == "failed"
