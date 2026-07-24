import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
pytestmark = pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx is not installed")

if HTTPX_AVAILABLE:
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


def test_state_manifest_migrates_legacy_camel_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "stateManifest.json"
    legacy.write_text(json.dumps({"watchlist": {"key": "watchlist"}}), encoding="utf-8")
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", tmp_path / "state_manifest.json")
    monkeypatch.setattr(master_service, "LEGACY_STATE_MANIFEST_CAMEL_PATH", legacy)
    loaded = master_service._load_state_manifest()
    assert loaded["watchlist"]["key"] == "watchlist"
    assert (tmp_path / "state_manifest.json").exists()


def test_watchlist_write_updates_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", tmp_path / "state_manifest.json")
    monkeypatch.setattr(master_service, "LEGACY_STATE_MANIFEST_CAMEL_PATH", tmp_path / "stateManifest.json")
    master_service.write_repo_state_json_and_verify("watchlist", ["BTCUSDT"])
    manifest = json.loads((tmp_path / "state_manifest.json").read_text(encoding="utf-8"))
    assert manifest["watchlist"]["key"] == "watchlist"
    assert manifest["watchlist"]["sha256"]


def test_state_file_path_mapping_uses_monkeypatched_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist_path = tmp_path / "watchlist.json"
    manifest_path = tmp_path / "state_manifest.json"
    repo_root_watchlist = master_service.BASE_DIR / "watchlist.json"
    before = repo_root_watchlist.read_text(encoding="utf-8") if repo_root_watchlist.exists() else None
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", watchlist_path)
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(master_service, "LEGACY_STATE_MANIFEST_CAMEL_PATH", tmp_path / "stateManifest.json")
    master_service.write_repo_state_json_and_verify("watchlist", ["BTCUSDT"])
    assert watchlist_path.exists()
    assert json.loads(watchlist_path.read_text(encoding="utf-8")) == ["BTCUSDT"]
    after = repo_root_watchlist.read_text(encoding="utf-8") if repo_root_watchlist.exists() else None
    assert before == after


def test_repo_state_writes_alerts_settings_and_manifest_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", tmp_path / "state_manifest.json")
    monkeypatch.setattr(master_service, "LEGACY_STATE_MANIFEST_CAMEL_PATH", tmp_path / "stateManifest.json")
    monkeypatch.setattr(master_service, "BYBIT_CUSTOM_ALERTS_PATH", tmp_path / "bybit_custom_alerts.json")
    monkeypatch.setattr(master_service, "OANDA_CUSTOM_ALERTS_PATH", tmp_path / "oanda_custom_alerts.json")
    monkeypatch.setattr(master_service, "BYBIT_SETTINGS_PATH", tmp_path / "bybit_settings.json")
    monkeypatch.setattr(master_service, "OANDA_SETTINGS_PATH", tmp_path / "oanda_settings.json")
    master_service.write_repo_state_json_and_verify("bybit_alerts", [{"id": "b1"}])
    master_service.write_repo_state_json_and_verify("oanda_alerts", [{"id": "o1"}])
    master_service.write_repo_state_json_and_verify("bybit_settings", {"wait_seconds": 30})
    master_service.write_repo_state_json_and_verify("oanda_settings", {"wait_seconds": 10})
    assert (tmp_path / "bybit_custom_alerts.json").exists()
    assert (tmp_path / "oanda_custom_alerts.json").exists()
    assert (tmp_path / "bybit_settings.json").exists()
    assert (tmp_path / "oanda_settings.json").exists()
    manifest = json.loads((tmp_path / "state_manifest.json").read_text(encoding="utf-8"))
    for key in ("bybit_alerts", "oanda_alerts", "bybit_settings", "oanda_settings"):
        assert key in manifest
        assert manifest[key]["sha256"]
        assert manifest[key]["updated_at"]
        assert manifest[key]["source_host"]
        assert manifest[key]["app_profile"]


def test_startup_repo_local_existing_state_wins_over_stale_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: json.dumps({"watchlist": ["STALEUSDT"]}).encode("utf-8"))
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: {"bybit_restored": 0, "oanda_restored": 0, "watchlist_restored": 0, "pending_webhooks_restored": 0})
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: ["LOCALUSDT"] if key == "watchlist" else [])
    captured = {"watchlist": None}
    monkeypatch.setattr(master_service, "_set_watchlist_local_mirror", lambda items: captured.__setitem__("watchlist", list(items)) or list(items))
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert captured["watchlist"] == ["LOCALUSDT"]


def test_startup_bootstraps_missing_repo_local_state_from_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: json.dumps({"watchlist": ["BOOTUSDT"]}).encode("utf-8"))
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: {"bybit_restored": 0, "oanda_restored": 0, "watchlist_restored": 0, "pending_webhooks_restored": 0})
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda _key: (_ for _ in ()).throw(FileNotFoundError()))
    captured = {"watchlist": None}
    monkeypatch.setattr(master_service, "_set_watchlist_local_mirror", lambda items: captured.__setitem__("watchlist", list(items)) or list(items))
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert captured["watchlist"] == ["BOOTUSDT"]


def test_master_journal_startup_skips_dropbox_workbook_but_restores_repo_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(
        master_service,
        "_load_local_state_backup",
        lambda: json.dumps({"watchlist": ["BACKUPUSDT"]}).encode("utf-8"),
    )
    monkeypatch.setattr(
        master_service,
        "_resolve_trading_journal_dropbox_folder",
        lambda: (_ for _ in ()).throw(AssertionError("Dropbox journal folder must not be resolved")),
    )
    monkeypatch.setattr(
        master_service,
        "_sanitize_bybit_demo_workbook",
        lambda _folder: (_ for _ in ()).throw(AssertionError("Dropbox workbook must not be sanitized")),
    )
    monkeypatch.setattr(
        master_service,
        "read_repo_state_json",
        lambda key: ["LOCALUSDT"] if key == "watchlist" else ([] if key in {"bybit_alerts", "oanda_alerts"} else {}),
    )
    restored = {"watchlist": None, "oanda_repair": 0, "backup": 0}
    monkeypatch.setattr(
        master_service,
        "_set_watchlist_local_mirror",
        lambda items: restored.__setitem__("watchlist", list(items)) or list(items),
    )
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(
        master_service,
        "_repair_persisted_oanda_trade_rows",
        lambda: restored.__setitem__("oanda_repair", restored["oanda_repair"] + 1) or 1,
    )
    monkeypatch.setattr(
        master_service,
        "_schedule_dropbox_upload_state_backup",
        lambda: restored.__setitem__("backup", restored["backup"] + 1),
    )
    master_service._STARTUP_STATE_RESTORE_DONE.clear()

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    status = master_service._state_sync_status_snapshot()
    assert restored == {"watchlist": ["LOCALUSDT"], "oanda_repair": 1, "backup": 1}
    assert status["restore_status"] == "done"
    assert status["restore_complete"] is True
    assert status["per_file_state_ready"] is True
    assert master_service._STARTUP_STATE_RESTORE_DONE.is_set()


def test_empty_repo_local_states_are_authoritative_over_stale_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(
        master_service,
        "_load_local_state_backup",
        lambda: json.dumps({"watchlist": ["BTCUSDT"], "alerts": {"bybit": {"alerts": [{"id": "b1"}]}, "oanda": {"alerts": [{"id": "o1"}]}}}).encode("utf-8"),
    )
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: {"bybit_restored": 0, "oanda_restored": 0, "watchlist_restored": 0, "pending_webhooks_restored": 0})
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: (_ for _ in ()).throw(AssertionError("must not replay state_backup in repo-local mode")))
    seen = {"watchlist": None, "bybit": None, "oanda": None}
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: [] if key in {"watchlist", "bybit_alerts", "oanda_alerts"} else {})
    monkeypatch.setattr(master_service, "_set_watchlist_local_mirror", lambda items: seen.__setitem__("watchlist", list(items)) or list(items))
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: seen.__setitem__("bybit", list(alerts)))
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: seen.__setitem__("oanda", list(alerts)))
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert seen["watchlist"] == []
    assert seen["bybit"] == []
    assert seen["oanda"] == []


def test_invalid_repo_local_json_surfaces_restore_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: json.dumps({"watchlist": []}).encode("utf-8"))
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: {"bybit_restored": 0, "oanda_restored": 0, "watchlist_restored": 0, "pending_webhooks_restored": 0})
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: (_ for _ in ()).throw(ValueError("bad json")) if key == "watchlist" else [])
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "failed"
    assert "Invalid repo-local state for keys" in str(status.get("restore_error") or "")


def test_invalid_repo_local_file_not_overwritten_in_same_startup_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: json.dumps({"watchlist": ["BTCUSDT"]}).encode("utf-8"))
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: (_ for _ in ()).throw(ValueError("bad json")) if key == "watchlist" else [])
    writes = []
    monkeypatch.setattr(master_service, "write_repo_state_json_and_verify", lambda key, payload: writes.append((key, payload)) or {"ok": True})
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert all(key != "watchlist" for key, _ in writes)


def test_repo_local_ready_without_state_backup_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: [] if key in {"watchlist", "bybit_alerts", "oanda_alerts"} else {"wait_seconds": 30})
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "done"
    assert status["restore_error"] is None
    assert status["per_file_state_ready"] is True
    assert status["remote_backup_hash"] is None


def test_invalid_repo_local_without_state_backup_still_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda key: (_ for _ in ()).throw(ValueError("bad")) if key == "watchlist" else [])
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "failed"
    assert "watchlist" in str(status["restore_error"])
    assert status["per_file_state_ready"] is False


def test_missing_repo_local_files_bootstrap_writes_relocated_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    backup = {
        "watchlist": ["WIFUSDT"],
        "alerts": {"bybit": {"alerts": [{"id": "b1"}]}, "oanda": {"alerts": [{"id": "o1"}]}},
        "bybit_settings": {"wait_seconds": 30},
        "oanda_settings": {"wait_seconds": 20},
    }
    monkeypatch.setattr(master_service, "_load_local_state_backup", lambda: json.dumps(backup).encode("utf-8"))
    monkeypatch.setattr(master_service, "_restore_alerts_payload", lambda _data: {"bybit_restored": 0, "oanda_restored": 0, "watchlist_restored": 0, "pending_webhooks_restored": 0})
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: alerts)
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: alerts)
    monkeypatch.setattr(master_service, "read_repo_state_json", lambda _key: (_ for _ in ()).throw(FileNotFoundError()))
    written = []
    monkeypatch.setattr(master_service, "write_repo_state_json_and_verify", lambda key, payload: written.append((key, payload)) or {"ok": True})
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    keys = [k for k, _ in written]
    assert "watchlist" in keys
    assert "bybit_alerts" in keys
    assert "oanda_alerts" in keys
