from __future__ import annotations
import hashlib, json, os, socket
from datetime import datetime, timezone
from typing import Any, Callable
from render.dropbox_sync import download_bytes, upload_bytes

DROPBOX_STATE_ROOT = os.getenv("DROPBOX_STATE_ROOT", "/codex/tradingtools_state").strip() or "/codex/tradingtools_state"

STATE_FILES = {
    "watchlist": "watchlist.json",
    "bybit_alerts": "bybit_monitor/custom_alerts.json",
    "oanda_alerts": "oanda_monitor/custom_alerts.json",
    "bybit_settings": "bybit_monitor/settings.json",
    "oanda_settings": "oanda_monitor/settings.json",
    "fxweekend_settings": "fxweekend-clone/settings.json",
    "fxweekend_status": "fxweekend-clone/status.json",
    "pending_webhooks": "pending_webhooks.json",
    "trade_contexts": "trade_contexts.json",
    "state_manifest": "state_manifest.json",
}

_last: dict[str, Any] = {"last_fetch_at": None, "last_save_at": None, "last_verify_at": None, "last_error": None}

def _now(): return datetime.now(timezone.utc).isoformat()

def dropbox_state_enabled() -> bool:
    return os.getenv("DROPBOX_SYNC_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

def remote_json_path(key: str) -> str:
    if key not in STATE_FILES:
        raise KeyError(f"Unknown dropbox state key: {key}")
    return f"{DROPBOX_STATE_ROOT.rstrip('/')}/{STATE_FILES[key]}"

def download_json(key: str, default: object | None = None, required: bool = False) -> object:
    path = remote_json_path(key)
    try:
        payload = download_bytes(path)
    except FileNotFoundError as exc:
        if required:
            raise FileNotFoundError(f"Missing Dropbox state file for key '{key}': {path}") from exc
        return default
    if payload is None:
        if required:
            raise FileNotFoundError(f"Missing Dropbox state file: {path}")
        return default
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid Dropbox JSON for {path}: {exc}") from exc
    _last["last_fetch_at"] = _now()
    return decoded

def _manifest_update(key: str, payload: object) -> None:
    try:
        manifest = download_json("state_manifest", default={}, required=False)
        if not isinstance(manifest, dict): manifest = {}
    except Exception:
        manifest = {}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest[key] = {
        "key": key, "updated_at": _now(), "sha256": hashlib.sha256(blob).hexdigest(),
        "source_host": socket.gethostname(), "app_profile": os.getenv("APP_PROFILE", "")
    }
    upload_bytes(remote_json_path("state_manifest"), json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))

def upload_json(key: str, payload: object) -> dict:
    path = remote_json_path(key)
    blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    upload_bytes(path, blob)
    _last["last_save_at"] = _now()
    _manifest_update(key, payload)
    return {"ok": True, "path": path}

def upload_json_and_verify(key: str, payload: object, verifier: Callable[[object], bool] | None = None) -> dict:
    uploaded = upload_json(key, payload)
    roundtrip = download_json(key, required=True)
    if verifier and not verifier(roundtrip):
        raise ValueError(f"Dropbox verification failed for {key}")
    _last["last_verify_at"] = _now()
    return {**uploaded, "verified": True}

def snapshot_remote_json(key: str, reason: str) -> str | None:
    existing = download_json(key, default=None, required=False)
    if existing is None: return None
    snap_key = f"_snapshots/{key}_{reason}_{int(datetime.now(timezone.utc).timestamp())}"
    upload_bytes(f"{DROPBOX_STATE_ROOT.rstrip('/')}/{snap_key}.json", json.dumps(existing, indent=2, sort_keys=True).encode("utf-8"))
    return snap_key

def ensure_remote_json_exists(key: str, default: object) -> object:
    current = download_json(key, default=None, required=False)
    if current is not None: return current
    upload_json(key, default)
    return default

def state_store_summary() -> dict:
    return {"dropbox_state_enabled": dropbox_state_enabled(), "dropbox_state_root": DROPBOX_STATE_ROOT, **_last}
