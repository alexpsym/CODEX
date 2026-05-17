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


@pytest.fixture(autouse=True)
def _reset_state_sync_globals():
    old_profile = master_service.APP_PROFILE
    old_dropbox = master_service.DROPBOX_SYNC_ENABLED
    old_local_only = master_service.LOCAL_STATE_ONLY
    old_status = dict(master_service._STATE_SYNC_STATUS)
    was_done = master_service._STARTUP_STATE_RESTORE_DONE.is_set()
    master_service.APP_PROFILE = "local"
    master_service.DROPBOX_SYNC_ENABLED = True
    master_service.LOCAL_STATE_ONLY = False
    master_service._STATE_SYNC_STATUS.clear()
    master_service._STATE_SYNC_STATUS.update(
        {
            "enabled": True,
            "restore_status": "done",
            "restore_complete": True,
            "restore_error": None,
        }
    )
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    yield
    master_service.APP_PROFILE = old_profile
    master_service.DROPBOX_SYNC_ENABLED = old_dropbox
    master_service.LOCAL_STATE_ONLY = old_local_only
    master_service._STATE_SYNC_STATUS.clear()
    master_service._STATE_SYNC_STATUS.update(old_status)
    if was_done:
        master_service._STARTUP_STATE_RESTORE_DONE.set()
    else:
        master_service._STARTUP_STATE_RESTORE_DONE.clear()


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
    monkeypatch.setattr(master_service.dropbox_state_store, "download_json", lambda *_a, **_k: ["DASHUSDT", "BTCUSDT"])

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

    calls = {"scheduled": 0}
    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: calls.__setitem__("scheduled", calls["scheduled"] + 1))

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["DASHUSDT", "DOLOUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["ok"] is True
    assert "DOLOUSDT" in payload["items"]
    assert payload["state_sync"]["pending_upload"] is True
    assert calls["scheduled"] == 1
    assert "DOLOUSDT" in payload["state_sync"]["last_verified_watchlist"]


def test_watchlist_post_fails_when_remote_misses_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    called = {"local": 0}
    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(master_service, "_set_watchlist_local_mirror", lambda _items: called.__setitem__("local", called["local"] + 1) or _items)

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert called["local"] == 1


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
    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", lambda _k, payload, verifier=None: (store.__setitem__("payload", payload), verifier(payload) if verifier else None))
    monkeypatch.setattr(master_service.dropbox_state_store, "download_json", lambda key, default=None, required=False: store.get("payload") if key=="watchlist" else default)

    write_resp = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    assert json.loads(write_resp.body.decode("utf-8"))["ok"] is True

    first_watchlist.unlink(missing_ok=True)
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", second_watchlist)
    monkeypatch.setattr(master_service, "_WATCHLIST_CACHE", None)
    master_service._STARTUP_STATE_RESTORE_DONE.clear()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True, restore_error=None)
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    read_resp = asyncio.run(master_service.get_watchlist())
    assert json.loads(read_resp.body.decode("utf-8"))["items"] == []


def test_scanner_local_ui_mode_still_runs_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"restore": 0}
    monkeypatch.setattr(master_service, "_is_scanner_local_ui_mode", lambda: True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", True)
    monkeypatch.setattr(master_service, "_dropbox_restore_state_backup_on_startup", lambda: called.__setitem__("restore", called["restore"] + 1) or asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_log_outbound_traffic_summary", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_poll_pending_webhook_invalidations", lambda: asyncio.sleep(0))
    asyncio.run(master_service._autostart_scripts())
    assert called["restore"] == 1


def test_autostart_normal_path_uses_repo_local_backup_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_is_scanner_local_ui_mode", lambda: False)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "_restore_bybit_closed_pnl_last_seen_from_state", lambda: None)
    monkeypatch.setattr(master_service, "_restore_oanda_fill_state_on_startup", lambda: None)
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "_compute_autostart_scripts", lambda: [])
    monkeypatch.setattr(master_service, "_dropbox_restore_state_backup_on_startup", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_start_startup_recovery_import_after_restore", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_schedule_monthly_aud_revaluation_sync", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_poll_pending_webhook_invalidations", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_log_outbound_traffic_summary", lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, "_start_manual_save_github_sync_watcher_if_needed", lambda: None)
    monkeypatch.setattr(master_service.asyncio, "create_task", lambda coro: type("D", (), {"cancel": lambda self: None, "done": lambda self: False})())
    asyncio.run(master_service._autostart_scripts())
    status = master_service._state_sync_status_snapshot()
    assert status["backup_path"] == str(master_service.STATE_BACKUP_LOCAL_PATH)
    assert status["enabled"] is True


def test_state_sync_status_blocks_when_dropbox_disabled_and_not_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", False)
    payload = master_service._state_sync_status_snapshot()
    assert payload["restore_status"] == "failed"


def test_oanda_alert_enabled_schedules_repo_local_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    monkeypatch.setattr(master_service.oanda_monitor, "get_custom_alerts", lambda force=True: [{"id": "a1", "enabled": False}])
    captured = {}
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: captured.__setitem__("alerts", alerts))
    calls = {"n": 0}
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: calls.__setitem__("n", calls["n"] + 1))
    resp = asyncio.run(master_service.set_oanda_monitor_custom_alert_enabled("a1", DummyRequest({"enabled": True})))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["ok"] is True
    assert calls["n"] == 1
    assert captured["alerts"][0]["enabled"] is True


def test_wait_for_state_restore_local_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    master_service._STARTUP_STATE_RESTORE_DONE = asyncio.Event()
    master_service._STARTUP_STATE_RESTORE_DONE.clear()
    master_service._update_state_sync_status(enabled=True, restore_status="pending", restore_complete=False)
    with pytest.raises(master_service.HTTPException) as excinfo:
        asyncio.run(master_service._wait_for_state_restore_or_error(timeout=0.01))
    detail = excinfo.value.detail
    assert detail["error"] == "repo_local_restore_timeout"
    assert "Repo-local state restore is still pending." in detail["message"]


def test_upsert_bybit_custom_alert_source_repo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    monkeypatch.setattr(master_service.bybit_monitor, "get_custom_alerts", lambda force=True: [])
    monkeypatch.setattr(master_service.bybit_monitor, "_coerce_alert", lambda payload: {**payload, "id": payload.get("id") or "b1"})
    captured = {}
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: captured.__setitem__("alerts", alerts))
    resp = asyncio.run(master_service.upsert_bybit_monitor_custom_alert(DummyRequest({"symbol": "BTCUSDT", "direction": "above", "target": 1.0})))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["ok"] is True
    assert captured["alerts"][0]["source"] == "repo_local"


def test_upsert_oanda_custom_alert_source_repo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    monkeypatch.setattr(master_service.oanda_monitor, "get_custom_alerts", lambda force=True: [])
    monkeypatch.setattr(master_service.oanda_monitor, "_coerce_alert", lambda payload: {**payload, "id": payload.get("id") or "o1"})
    captured = {}
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: captured.__setitem__("alerts", alerts))
    resp = asyncio.run(master_service.upsert_oanda_monitor_custom_alert(DummyRequest({"instrument": "EUR_USD", "direction": "above", "target": 1.0})))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["ok"] is True
    assert captured["alerts"][0]["source"] == "repo_local"


def test_repo_local_custom_alert_get_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    monkeypatch.setattr(master_service.bybit_monitor, "get_custom_alerts", lambda force=True: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(master_service.HTTPException) as excinfo:
        asyncio.run(master_service.bybit_monitor_custom_alerts())
    assert excinfo.value.detail["error"] == "repo_local_state_unavailable"
