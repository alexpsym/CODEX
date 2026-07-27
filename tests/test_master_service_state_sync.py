import asyncio
import importlib.util
import json
import sys
import time
from copy import deepcopy
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

_RUNTIME_FILES_TO_PRESERVE = (
    ROOT / "watchlist.json",
    ROOT / "state_manifest.json",
    ROOT / "state_backup.json",
    ROOT / "bybit_monitor" / "custom_alerts.json",
    ROOT / "bybit_monitor" / "settings.json",
    ROOT / "oanda_monitor" / "custom_alerts.json",
    ROOT / "oanda_monitor" / "settings.json",
)


def _restore_runtime_snapshot(path: Path, original: bytes | None) -> None:
    last_error: OSError | None = None
    for _attempt in range(20):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    assert last_error is not None
    raise last_error


@pytest.fixture(autouse=True)
def _reset_state_sync_globals():
    runtime_snapshots = {
        path: path.read_bytes() if path.exists() else None
        for path in _RUNTIME_FILES_TO_PRESERVE
    }
    old_profile = master_service.APP_PROFILE
    old_dropbox = master_service.DROPBOX_SYNC_ENABLED
    old_local_only = master_service.LOCAL_STATE_ONLY
    old_watchlist_cache = master_service._WATCHLIST_CACHE
    old_watchlist_lock = master_service._WATCHLIST_MUTATION_LOCK
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
            "watchlist_indeterminate": False,
            "watchlist_mutation_blocked": False,
            "watchlist_rollback_error": None,
        }
    )
    master_service._WATCHLIST_MUTATION_LOCK = asyncio.Lock()
    master_service._STARTUP_STATE_RESTORE_DONE.set()
    yield
    master_service.APP_PROFILE = old_profile
    master_service.DROPBOX_SYNC_ENABLED = old_dropbox
    master_service.LOCAL_STATE_ONLY = old_local_only
    master_service._WATCHLIST_CACHE = old_watchlist_cache
    master_service._WATCHLIST_MUTATION_LOCK = old_watchlist_lock
    master_service._STATE_SYNC_STATUS.clear()
    master_service._STATE_SYNC_STATUS.update(old_status)
    if was_done:
        master_service._STARTUP_STATE_RESTORE_DONE.set()
    else:
        master_service._STARTUP_STATE_RESTORE_DONE.clear()
    for path, original in runtime_snapshots.items():
        _restore_runtime_snapshot(path, original)


class DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _mk_dropbox_store():
    return {"payload": b""}


def _isolate_render_watchlist_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: list[str],
) -> dict[str, object]:
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", False)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", True)
    monkeypatch.setattr(
        master_service, "_state_backup_uses_local_repo_file", lambda: False
    )
    monkeypatch.setattr(
        master_service.dropbox_state_store, "dropbox_state_enabled", lambda: True
    )
    monkeypatch.setattr(
        master_service, "WATCHLIST_PATH", tmp_path / "watchlist.json"
    )
    monkeypatch.setattr(
        master_service,
        "FXWEEKEND_SETTINGS_PATH",
        tmp_path / "fxweekend_settings.json",
    )
    monkeypatch.setattr(
        master_service,
        "FXWEEKEND_STATUS_PATH",
        tmp_path / "fxweekend_status.json",
    )
    monkeypatch.setattr(master_service, "_WATCHLIST_CACHE", list(initial))
    monkeypatch.setattr(master_service, "_WATCHLIST_MUTATION_LOCK", asyncio.Lock())
    master_service._save_watchlist(list(initial))
    master_service._update_state_sync_status(
        enabled=True,
        restore_status="done",
        restore_complete=True,
        restore_error=None,
        watchlist_indeterminate=False,
        watchlist_mutation_blocked=False,
        watchlist_rollback_error=None,
    )
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    primary_store = {
        "watchlist": list(initial),
        "bybit_alerts": [],
        "oanda_alerts": [],
        "bybit_settings": {},
        "oanda_settings": {},
        "fxweekend_settings": {
            "schema_version": master_service.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
            "enabled": False,
            "account_modes": [],
        },
        "fxweekend_status": {},
    }
    verifier_calls: list[str] = []

    def fake_upload_json_and_verify(key, payload, verifier=None):
        roundtrip = deepcopy(payload)
        if key == "watchlist":
            verifier_calls.append(key if verifier is not None else "missing")
        primary_store[key] = roundtrip
        if verifier is not None and not verifier(deepcopy(roundtrip)):
            raise ValueError(f"Dropbox verification failed for {key}")
        return {"ok": True, "verified": True}

    def fake_download_json(key, default=None, required=False):
        if key in primary_store:
            return deepcopy(primary_store[key])
        if required:
            raise FileNotFoundError(key)
        return deepcopy(default)

    def build_backup_payload() -> bytes:
        return json.dumps(
            {
                "version": 4,
                "alerts": {
                    "bybit": {"alerts": []},
                    "oanda": {"alerts": []},
                },
                "watchlist": master_service._get_watchlist(),
                "fxweekend_settings": master_service._load_json_file(
                    master_service.FXWEEKEND_SETTINGS_PATH,
                    master_service.FXWEEKEND_DEFAULT_SETTINGS,
                ),
                "fxweekend_status": master_service._load_json_file(
                    master_service.FXWEEKEND_STATUS_PATH, {}
                ),
            },
            sort_keys=True,
        ).encode("utf-8")

    aggregate_store = {"payload": build_backup_payload()}

    def fake_upload_bytes(_path: str, payload: bytes) -> None:
        aggregate_store["payload"] = bytes(payload)

    def fake_download_bytes(_path: str) -> bytes:
        return bytes(aggregate_store["payload"])

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)
    monkeypatch.setattr(
        master_service.dropbox_state_store,
        "upload_json_and_verify",
        fake_upload_json_and_verify,
    )
    monkeypatch.setattr(
        master_service.dropbox_state_store, "download_json", fake_download_json
    )
    monkeypatch.setattr(
        master_service, "_build_state_backup_payload", build_backup_payload
    )
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", fake_upload_bytes)
    monkeypatch.setattr(
        master_service, "_dropbox_download_bytes", fake_download_bytes
    )
    monkeypatch.setattr(master_service, "download_bytes", fake_download_bytes)
    monkeypatch.setattr(
        master_service.bybit_monitor,
        "replace_custom_alerts",
        lambda alerts, strict=False: list(alerts),
    )
    monkeypatch.setattr(
        master_service.oanda_monitor,
        "replace_custom_alerts",
        lambda alerts: list(alerts),
    )
    return {
        "primary": primary_store,
        "aggregate": aggregate_store,
        "verifier_calls": verifier_calls,
    }


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


def test_watchlist_post_verifies_remote_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = _isolate_render_watchlist_state(tmp_path, monkeypatch, [])
    calls = {"scheduled": 0}
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: calls.__setitem__("scheduled", calls["scheduled"] + 1))

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["DASHUSDT", "DOLOUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert "DOLOUSDT" in payload["items"]
    assert payload["durable_verified"] is True
    assert payload["pending"] is False
    assert payload["verified_items"] == ["DASHUSDT", "DOLOUSDT"]
    assert payload["verified_at"]
    assert payload["state_sync"]["pending_upload"] is False
    assert calls["scheduled"] == 0
    assert stores["primary"]["watchlist"] == ["DASHUSDT", "DOLOUSDT"]
    assert "missing" not in stores["verifier_calls"]


def test_watchlist_post_fails_when_primary_verification_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_render_watchlist_state(tmp_path, monkeypatch, [])
    master_service._update_state_sync_status(enabled=True, restore_status="done", restore_complete=True)
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    called = {"local": 0}
    monkeypatch.setattr(master_service.dropbox_state_store, "upload_json_and_verify", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(master_service, "_set_watchlist_local_mirror", lambda _items: called.__setitem__("local", called["local"] + 1) or _items)

    response = asyncio.run(master_service.set_watchlist(DummyRequest({"items": ["BTCUSDT"]})))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["durable_verified"] is False
    assert payload["error"]
    assert payload["items"] is None
    assert payload["rollback_verified"] is False
    assert payload["indeterminate"] is True
    assert payload["state_sync"]["watchlist_mutation_blocked"] is True
    assert called["local"] >= 1


def _isolate_repo_watchlist_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: list[str]
) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", tmp_path / "state_manifest.json")
    monkeypatch.setattr(master_service, "LEGACY_STATE_MANIFEST_CAMEL_PATH", tmp_path / "stateManifest.json")
    monkeypatch.setattr(master_service, "STATE_BACKUP_LOCAL_PATH", tmp_path / "state_backup.json")
    monkeypatch.setattr(master_service, "_WATCHLIST_CACHE", list(initial))
    monkeypatch.setattr(master_service, "_WATCHLIST_MUTATION_LOCK", asyncio.Lock())
    master_service._save_watchlist(list(initial))
    master_service._update_state_sync_status(
        enabled=True, restore_status="done", restore_complete=True
    )
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    async def fake_resolve(symbol: str, *_args, **_kwargs):
        return {"resolved_symbol": symbol.upper()}

    monkeypatch.setattr(master_service, "_resolve_symbol_payload", fake_resolve)


@pytest.mark.parametrize(
    ("initial", "requested"),
    [
        ([], ["BTCUSDT"]),
        (["BTCUSDT", "ETHUSDT"], ["ETHUSDT"]),
        (["BTCUSDT"], []),
        (["BTCUSDT", "ETHUSDT"], []),
    ],
)
def test_render_watchlist_add_delete_and_clear_verify_exact_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: list[str],
    requested: list[str],
) -> None:
    stores = _isolate_render_watchlist_state(tmp_path, monkeypatch, initial)
    response = asyncio.run(
        master_service.set_watchlist(DummyRequest({"items": requested}))
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["durable_verified"] is True
    assert payload["verified_items"] == requested
    assert payload["items"] == requested
    assert json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8")) == requested
    assert stores["primary"]["watchlist"] == requested
    assert stores["verifier_calls"][-1] == "watchlist"
    backup = json.loads(stores["aggregate"]["payload"].decode("utf-8"))
    assert backup["watchlist"] == requested


def test_stale_remote_watchlist_containing_deleted_item_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_service._update_state_sync_status(
        enabled=True, restore_status="done", restore_complete=True
    )
    monkeypatch.setattr(master_service, "_build_state_backup_payload", lambda: b"{}")
    monkeypatch.setattr(master_service, "_write_local_state_backup_bytes_or_payload", lambda _payload: None)

    async def stale_summary(timeout: float = 10.0):
        return {"watchlist": ["BTCUSDT"], "hash": "stale"}

    monkeypatch.setattr(master_service, "_download_remote_backup_summary", stale_summary)
    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            master_service._upload_and_verify_state_backup_now(
                expected_watchlist=[], timeout=1
            )
        )
    assert "expected exact sequence" in str(exc_info.value)


@pytest.mark.parametrize(
    ("remote_payload", "message_fragment"),
    [
        ({}, "watchlist key is missing"),
        ({"watchlist": {}}, "watchlist must be a list"),
    ],
)
def test_exact_empty_verification_rejects_missing_or_wrong_type_proof(
    monkeypatch: pytest.MonkeyPatch,
    remote_payload: dict[str, object],
    message_fragment: str,
) -> None:
    master_service._update_state_sync_status(
        enabled=True, restore_status="done", restore_complete=True
    )
    summary = master_service._extract_remote_backup_summary(
        json.dumps(remote_payload).encode("utf-8")
    )
    monkeypatch.setattr(
        master_service, "_state_backup_uses_local_repo_file", lambda: True
    )
    monkeypatch.setattr(
        master_service, "_build_state_backup_payload", lambda: b"{}"
    )
    monkeypatch.setattr(
        master_service,
        "_write_local_state_backup_bytes_or_payload",
        lambda _payload: None,
    )

    async def fake_remote_summary(timeout: float = 10.0):
        return summary

    monkeypatch.setattr(
        master_service,
        "_download_remote_backup_summary",
        fake_remote_summary,
    )
    with pytest.raises(master_service.HTTPException) as exc_info:
        asyncio.run(
            master_service._upload_and_verify_state_backup_now(
                expected_watchlist=[], timeout=1
            )
        )
    assert message_fragment in exc_info.value.detail["message"]


def test_delayed_backup_failure_rolls_back_displayed_watchlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_repo_watchlist_state(tmp_path, monkeypatch, ["BTCUSDT"])
    original_upload = master_service._upload_and_verify_state_backup_now
    attempts = {"count": 0}

    async def fail_first(*, expected_watchlist=None, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("delayed upload failed")
        return await original_upload(
            expected_watchlist=expected_watchlist, **kwargs
        )

    monkeypatch.setattr(master_service, "_upload_and_verify_state_backup_now", fail_first)
    response = asyncio.run(
        master_service.set_watchlist(DummyRequest({"items": []}))
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["items"] == ["BTCUSDT"]
    assert payload["durable_verified"] is False
    assert payload["rollback_verified"] is True
    assert payload["indeterminate"] is False
    assert payload["state_sync"]["watchlist_mutation_blocked"] is False
    assert json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8")) == [
        "BTCUSDT"
    ]


def test_failed_rollback_verification_blocks_watchlist_as_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = _isolate_render_watchlist_state(
        tmp_path, monkeypatch, ["BTCUSDT"]
    )

    async def fail_backup(*_args, **_kwargs):
        raise RuntimeError("aggregate round-trip unavailable")

    monkeypatch.setattr(
        master_service, "_upload_and_verify_state_backup_now", fail_backup
    )
    response = asyncio.run(
        master_service.set_watchlist(DummyRequest({"items": []}))
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["items"] is None
    assert payload["verified_items"] is None
    assert payload["rollback_primary_verified"] is True
    assert payload["rollback_backup_verified"] is False
    assert payload["rollback_verified"] is False
    assert payload["indeterminate"] is True
    assert payload["state_sync"]["watchlist_indeterminate"] is True
    assert payload["state_sync"]["watchlist_mutation_blocked"] is True
    assert stores["primary"]["watchlist"] == ["BTCUSDT"]

    with pytest.raises(master_service.HTTPException) as read_exc:
        asyncio.run(master_service.get_watchlist())
    assert read_exc.value.status_code == 503
    assert read_exc.value.detail["error"] == "watchlist_state_indeterminate"

    with pytest.raises(master_service.HTTPException) as write_exc:
        asyncio.run(
            master_service.set_watchlist(
                DummyRequest({"items": ["ETHUSDT"]})
            )
        )
    assert write_exc.value.status_code == 503
    assert write_exc.value.detail["error"] == "watchlist_state_indeterminate"


def test_concurrent_render_watchlist_transactions_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_render_watchlist_state(tmp_path, monkeypatch, [])
    original_upload = master_service._upload_and_verify_state_backup_now
    activity = {"active": 0, "maximum": 0}

    async def delayed_upload(*args, **kwargs):
        activity["active"] += 1
        activity["maximum"] = max(activity["maximum"], activity["active"])
        try:
            await asyncio.sleep(0.03)
            return await original_upload(*args, **kwargs)
        finally:
            activity["active"] -= 1

    monkeypatch.setattr(
        master_service,
        "_upload_and_verify_state_backup_now",
        delayed_upload,
    )

    async def run_both():
        return await asyncio.gather(
            master_service.set_watchlist(
                DummyRequest({"items": ["BTCUSDT"]})
            ),
            master_service.set_watchlist(
                DummyRequest({"items": ["ETHUSDT"]})
            ),
        )

    responses = asyncio.run(run_both())
    payloads = [
        json.loads(response.body.decode("utf-8")) for response in responses
    ]
    assert all(payload["ok"] is True for payload in payloads)
    assert activity["maximum"] == 1


def test_render_restart_keeps_authoritative_final_empty_over_stale_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stores = _isolate_render_watchlist_state(
        tmp_path, monkeypatch, ["BTCUSDT"]
    )
    write_resp = asyncio.run(
        master_service.set_watchlist(DummyRequest({"items": []}))
    )
    assert json.loads(write_resp.body.decode("utf-8"))["ok"] is True
    assert stores["primary"]["watchlist"] == []

    stores["aggregate"]["payload"] = json.dumps(
        {
            "version": 4,
            "alerts": {
                "bybit": {"alerts": []},
                "oanda": {"alerts": []},
            },
            "watchlist": ["STALEUSDT"],
        }
    ).encode("utf-8")
    master_service._WATCHLIST_CACHE = ["STALEUSDT"]
    master_service._save_watchlist(["STALEUSDT"])
    restored_payload: dict[str, object] = {}

    def fake_restore(data):
        restored_payload.update(deepcopy(data))
        master_service._set_watchlist_local_mirror(data["watchlist"])
        return {
            "bybit_restored": 0,
            "bybit_invalid_skipped": 0,
            "oanda_restored": 0,
            "watchlist_restored": len(data["watchlist"]),
            "pending_webhooks_restored": 0,
            "trade_contexts_restored": 0,
            "oanda_fill_state_restored": False,
            "journal_rows_restored": 0,
            "journal_rows_sanitized": 0,
        }

    monkeypatch.setattr(master_service, "_restore_alerts_payload", fake_restore)
    monkeypatch.setattr(
        master_service,
        "_resolve_trading_journal_dropbox_folder",
        lambda: ("/tmp", []),
    )
    monkeypatch.setattr(
        master_service,
        "_sanitize_bybit_demo_workbook",
        lambda _folder: {
            "deduped_by_order_id": 0,
            "deduped_by_fingerprint": 0,
        },
    )
    monkeypatch.setattr(
        master_service, "_repair_persisted_oanda_trade_rows", lambda: 0
    )
    monkeypatch.setattr(
        master_service, "_schedule_dropbox_upload_state_backup", lambda: None
    )
    master_service._STARTUP_STATE_RESTORE_DONE.clear()

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    status = master_service._state_sync_status_snapshot()
    assert restored_payload["watchlist"] == []
    assert master_service._get_watchlist() == []
    assert json.loads(
        (tmp_path / "watchlist.json").read_text(encoding="utf-8")
    ) == []
    assert status["restore_status"] == "done"
    assert status["watchlist_mutation_blocked"] is False
    assert master_service._STARTUP_STATE_RESTORE_DONE.is_set()


def test_render_restart_restores_authoritative_fxweekend_settings_and_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stores = _isolate_render_watchlist_state(tmp_path, monkeypatch, [])
    expected_settings = {
        "schema_version": master_service.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "enabled": True,
        "account_modes": ["demo", "live"],
        "cutoff_time_dst": "05:15",
        "cutoff_time_standard": "06:30",
    }
    expected_status = {
        "state": "verified flat",
        "last_verified_flat_at": "2026-07-25T05:16:00+10:00",
        "last_verified_window_cutoff": "2026-07-25T05:15:00+10:00",
        "accounts": {
            "demo": {"state": "verified flat", "open_count": 0},
            "live": {"state": "verified flat", "open_count": 0},
        },
    }
    stores["primary"]["fxweekend_settings"] = deepcopy(expected_settings)
    stores["primary"]["fxweekend_status"] = deepcopy(expected_status)
    stores["aggregate"]["payload"] = json.dumps(
        {
            "version": 4,
            "alerts": {
                "bybit": {"alerts": []},
                "oanda": {"alerts": []},
            },
            "watchlist": [],
            "fxweekend_settings": {
                "enabled": False,
                "account_modes": [],
            },
            "fxweekend_status": {"state": "disabled"},
        }
    ).encode("utf-8")
    master_service._save_json_file(
        master_service.FXWEEKEND_SETTINGS_PATH,
        {
            "schema_version": master_service.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
            "enabled": False,
            "account_modes": [],
        },
    )
    master_service._save_json_file(
        master_service.FXWEEKEND_STATUS_PATH,
        {"state": "disabled"},
    )

    monkeypatch.setattr(
        master_service,
        "_restore_alerts_payload",
        lambda _data: {
            "bybit_restored": 0,
            "bybit_invalid_skipped": 0,
            "oanda_restored": 0,
            "watchlist_restored": 0,
            "pending_webhooks_restored": 0,
            "trade_contexts_restored": 0,
            "oanda_fill_state_restored": False,
            "journal_rows_restored": 0,
            "journal_rows_sanitized": 0,
        },
    )
    monkeypatch.setattr(
        master_service, "_master_journal_single_file_mode", lambda: True
    )
    monkeypatch.setattr(
        master_service, "_repair_persisted_oanda_trade_rows", lambda: 0
    )
    monkeypatch.setattr(
        master_service, "_schedule_dropbox_upload_state_backup", lambda: None
    )
    master_service._STARTUP_STATE_RESTORE_DONE.clear()

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    assert master_service._load_json_file(
        master_service.FXWEEKEND_SETTINGS_PATH, {}
    ) == expected_settings
    assert master_service._load_json_file(
        master_service.FXWEEKEND_STATUS_PATH, {}
    ) == expected_status
    assert stores["primary"]["fxweekend_settings"] == expected_settings
    assert json.loads(stores["aggregate"]["payload"].decode("utf-8"))[
        "fxweekend_settings"
    ] == expected_settings
    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "done"
    assert status["fxweekend_durable_verified"] is True
    assert master_service._STARTUP_STATE_RESTORE_DONE.is_set()


def test_render_restart_durably_migrates_legacy_live_only_fxweekend_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stores = _isolate_render_watchlist_state(tmp_path, monkeypatch, [])
    legacy_settings = {
        "enabled": False,
        "account_modes": ["live"],
        "cutoff_time_dst": "05:17",
        "cutoff_time_standard": "06:23",
        "check_interval_seconds": 19,
        "max_retry_backoff_seconds": 97,
        "dry_run": True,
        "instrument_allowlist": ["EUR_USD"],
    }
    expected_settings = {
        **legacy_settings,
        "schema_version": master_service.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "account_modes": ["demo", "live"],
    }
    expected_status = {"state": "disabled", "accounts": {}}
    stores["primary"]["fxweekend_settings"] = deepcopy(legacy_settings)
    stores["primary"]["fxweekend_status"] = deepcopy(expected_status)
    stores["aggregate"]["payload"] = json.dumps(
        {
            "version": 4,
            "alerts": {
                "bybit": {"alerts": []},
                "oanda": {"alerts": []},
            },
            "watchlist": [],
            "fxweekend_settings": legacy_settings,
            "fxweekend_status": expected_status,
        }
    ).encode("utf-8")
    master_service._save_json_file(
        master_service.FXWEEKEND_SETTINGS_PATH, legacy_settings
    )
    master_service._save_json_file(
        master_service.FXWEEKEND_STATUS_PATH, expected_status
    )
    monkeypatch.setattr(
        master_service,
        "_restore_alerts_payload",
        lambda _data: {
            "bybit_restored": 0,
            "bybit_invalid_skipped": 0,
            "oanda_restored": 0,
            "watchlist_restored": 0,
            "pending_webhooks_restored": 0,
            "trade_contexts_restored": 0,
            "oanda_fill_state_restored": False,
            "journal_rows_restored": 0,
            "journal_rows_sanitized": 0,
        },
    )
    monkeypatch.setattr(
        master_service, "_master_journal_single_file_mode", lambda: True
    )
    monkeypatch.setattr(
        master_service, "_repair_persisted_oanda_trade_rows", lambda: 0
    )
    monkeypatch.setattr(
        master_service, "_schedule_dropbox_upload_state_backup", lambda: None
    )
    master_service._STARTUP_STATE_RESTORE_DONE.clear()

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    assert master_service._load_json_file(
        master_service.FXWEEKEND_SETTINGS_PATH, {}
    ) == expected_settings
    assert stores["primary"]["fxweekend_settings"] == expected_settings
    aggregate = json.loads(stores["aggregate"]["payload"].decode("utf-8"))
    assert aggregate["fxweekend_settings"] == expected_settings
    assert aggregate["fxweekend_status"] == expected_status
    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "done"
    assert status["fxweekend_settings_schema_migrated"] is True
    assert status["fxweekend_durable_verified"] is True
    assert master_service._STARTUP_STATE_RESTORE_DONE.is_set()


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


def _configure_local_restart_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    aggregate_watchlist: list[str],
) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "LOCAL_STATE_ONLY", True)
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", False)
    monkeypatch.setattr(master_service, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(master_service, "STATE_MANIFEST_PATH", tmp_path / "state_manifest.json")
    monkeypatch.setattr(
        master_service,
        "LEGACY_STATE_MANIFEST_CAMEL_PATH",
        tmp_path / "stateManifest.json",
    )
    monkeypatch.setattr(
        master_service, "STATE_BACKUP_LOCAL_PATH", tmp_path / "state_backup.json"
    )
    monkeypatch.setattr(
        master_service,
        "BYBIT_CUSTOM_ALERTS_PATH",
        tmp_path / "bybit_custom_alerts.json",
    )
    monkeypatch.setattr(
        master_service,
        "OANDA_CUSTOM_ALERTS_PATH",
        tmp_path / "oanda_custom_alerts.json",
    )
    monkeypatch.setattr(
        master_service, "BYBIT_SETTINGS_PATH", tmp_path / "bybit_settings.json"
    )
    monkeypatch.setattr(
        master_service, "OANDA_SETTINGS_PATH", tmp_path / "oanda_settings.json"
    )
    for path, payload in (
        (master_service.BYBIT_CUSTOM_ALERTS_PATH, []),
        (master_service.OANDA_CUSTOM_ALERTS_PATH, []),
        (master_service.BYBIT_SETTINGS_PATH, {}),
        (master_service.OANDA_SETTINGS_PATH, {}),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    master_service.STATE_BACKUP_LOCAL_PATH.write_text(
        json.dumps(
            {
                "watchlist": aggregate_watchlist,
                "alerts": {
                    "bybit": {"alerts": []},
                    "oanda": {"alerts": []},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "_repair_persisted_oanda_trade_rows", lambda: 0)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(
        master_service.bybit_monitor,
        "replace_custom_alerts",
        lambda alerts, strict=False: list(alerts),
    )
    monkeypatch.setattr(
        master_service.oanda_monitor,
        "replace_custom_alerts",
        lambda alerts: list(alerts),
    )
    master_service._WATCHLIST_CACHE = None
    master_service._STARTUP_STATE_RESTORE_DONE.clear()


def test_missing_watchlist_mirror_honours_verified_empty_manifest_over_stale_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_local_restart_state(
        tmp_path, monkeypatch, aggregate_watchlist=["STALEUSDT"]
    )
    empty_hash = master_service.hashlib.sha256(
        json.dumps([], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    master_service.STATE_MANIFEST_PATH.write_text(
        json.dumps(
            {
                "watchlist": {
                    "key": "watchlist",
                    "sha256": empty_hash,
                    "updated_at": "2026-07-27T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    assert master_service._get_watchlist() == []
    assert json.loads(master_service.WATCHLIST_PATH.read_text(encoding="utf-8")) == []


def test_invalid_repo_local_watchlist_does_not_overwrite_healthy_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_local_restart_state(
        tmp_path, monkeypatch, aggregate_watchlist=["HEALTHYUSDT"]
    )
    master_service.WATCHLIST_PATH.write_text("{invalid json", encoding="utf-8")
    aggregate_before = master_service.STATE_BACKUP_LOCAL_PATH.read_bytes()
    calls = {"scheduled": 0}

    def destructive_schedule() -> None:
        calls["scheduled"] += 1
        payload = json.loads(
            master_service.STATE_BACKUP_LOCAL_PATH.read_text(encoding="utf-8")
        )
        payload["watchlist"] = master_service._get_watchlist()
        master_service.STATE_BACKUP_LOCAL_PATH.write_text(
            json.dumps(payload), encoding="utf-8"
        )

    monkeypatch.setattr(
        master_service,
        "_schedule_dropbox_upload_state_backup",
        destructive_schedule,
    )

    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())

    status = master_service._state_sync_status_snapshot()
    assert status["restore_status"] == "failed"
    assert "watchlist" in str(status["restore_error"])
    assert calls["scheduled"] == 0
    assert master_service.STATE_BACKUP_LOCAL_PATH.read_bytes() == aggregate_before
    assert json.loads(aggregate_before.decode("utf-8"))["watchlist"] == [
        "HEALTHYUSDT"
    ]


def test_empty_watchlist_and_legitimate_edits_survive_repeated_local_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_local_restart_state(
        tmp_path, monkeypatch, aggregate_watchlist=["STALEUSDT"]
    )
    master_service.write_repo_state_json_and_verify("watchlist", [])

    for _ in range(2):
        master_service._WATCHLIST_CACHE = None
        asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
        assert master_service._get_watchlist() == []

    master_service.write_repo_state_json_and_verify("watchlist", ["BTCUSDT"])
    master_service._WATCHLIST_CACHE = None
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert master_service._get_watchlist() == ["BTCUSDT"]

    master_service.write_repo_state_json_and_verify("watchlist", [])
    master_service._WATCHLIST_CACHE = None
    asyncio.run(master_service._dropbox_restore_state_backup_on_startup())
    assert master_service._get_watchlist() == []


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
