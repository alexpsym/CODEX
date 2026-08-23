from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pytz
import requests
from flask import Flask, render_template_string, request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.env_bootstrap import load_master_env
from shared.oanda_api import (
    FXWEEKEND_DEFAULT_ACCOUNT_MODES,
    FXWEEKEND_SETTINGS_SCHEMA_VERSION,
    OandaAPIError,
    resolve_account_config,
    upgrade_fxweekend_settings_schema,
)

load_master_env(base_dir=ROOT_DIR)

LOG_FILE = Path(__file__).with_name("trade_closure.log")
SETTINGS_PATH = Path(os.getenv("FXWEEKEND_SETTINGS_PATH") or Path(__file__).with_name("settings.json"))
STATUS_PATH = Path(os.getenv("FXWEEKEND_STATUS_PATH") or Path(__file__).with_name("status.json"))
DEFAULT_SETTINGS: Dict[str, Any] = {
    "schema_version": FXWEEKEND_SETTINGS_SCHEMA_VERSION,
    "enabled": True,
    "trigger_weekday": 5,
    "cutoff_time_dst": "05:00",
    "cutoff_time_standard": "06:00",
    "account_modes": list(FXWEEKEND_DEFAULT_ACCOUNT_MODES),
    "check_interval_seconds": 60,
    "max_retry_backoff_seconds": 300,
    "close_method": "positions",
    "dry_run": False,
    "instrument_allowlist": [],
    "news_events": [],
}

BRISBANE_TZ = pytz.timezone("Australia/Brisbane")
NY_TZ = pytz.timezone("America/New_York")
ACCOUNT_MODES = ("demo", "live")
FINAL_FAILURE_STATES = {
    "credential failure",
    "API failure",
    "partial closure failure",
    "missed cutoff/market closed",
}
HEARTBEAT_INTERVAL_SECONDS = 30.0
CLOSURE_RETRY_MAX_DELAY_FRACTION = 0.25
NEWS_LIQUIDATION_LEAD_MINUTES = 15

app = Flask(__name__)
_status_lock = threading.RLock()
_liquidation_lock = threading.Lock()
_news_schedule_lock = threading.RLock()
_scheduler_wakeup = threading.Event()


def _now_brisbane() -> datetime:
    return datetime.now(BRISBANE_TZ)


def _iso_now() -> str:
    return _now_brisbane().isoformat()


def _safe_error(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"(?i)(/accounts/)[^/\s?]+",
        r"\1[account]",
        text,
    )
    text = re.sub(
        r"\b\d{3}-\d{3}-\d{6,12}-\d{3}\b",
        "[account]",
        text,
    )
    text = re.sub(r"(?i)(bearer|token|api[_ -]?key)\s*[:=]?\s*\S+", r"\1 [redacted]", text)
    return text[:400]


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{_iso_now()} - {_safe_error(message)}\n")


def bootstrap_log() -> None:
    log("FX Weekend OANDA executor starting under Render Master Control.")


def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _normalize_hhmm(value: Any, fallback: str) -> str:
    text = str(value if value is not None else "").strip()
    match = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", text)
    if not match:
        return fallback
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return f"{hour:02d}:{minute:02d}"


def parse_news_release(release_date: Any, release_time: Any) -> datetime:
    """Parse a date/time-only news release in explicit Brisbane time."""

    date_text = str(release_date or "").strip()
    time_text = str(release_time or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        raise ValueError("News release date must use YYYY-MM-DD.")
    if not re.fullmatch(r"\d{2}:\d{2}", time_text):
        raise ValueError("News release time must use HH:MM.")
    try:
        naive = datetime.strptime(
            f"{date_text} {time_text}", "%Y-%m-%d %H:%M"
        )
    except ValueError as exc:
        raise ValueError("News release date/time is invalid.") from exc
    return BRISBANE_TZ.localize(naive)


def _news_event_id(release_at: datetime) -> str:
    canonical = release_at.astimezone(BRISBANE_TZ).isoformat()
    digest = hashlib.sha256(
        f"Australia/Brisbane|{canonical}".encode("utf-8")
    ).hexdigest()[:20]
    return f"news_{digest}"


def _new_news_event_id() -> str:
    return f"news_{uuid.uuid4().hex}"


def _normalize_news_events(value: Any) -> List[Dict[str, str]]:
    """Return canonical date/time-only events, de-duplicated by release time."""

    if not isinstance(value, list):
        return []
    normalized: List[Dict[str, str]] = []
    seen_release_times: set[str] = set()
    seen_event_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        release_date = str(raw.get("release_date") or "").strip()
        release_time = str(raw.get("release_time") or "").strip()
        if not release_date or not release_time:
            release_raw = str(raw.get("release_at") or "").strip()
            if release_raw:
                try:
                    parsed = datetime.fromisoformat(
                        release_raw.replace("Z", "+00:00")
                    )
                    if parsed.tzinfo is None:
                        parsed = BRISBANE_TZ.localize(parsed)
                    parsed = parsed.astimezone(BRISBANE_TZ)
                    release_date = parsed.strftime("%Y-%m-%d")
                    release_time = parsed.strftime("%H:%M")
                except ValueError:
                    continue
        try:
            release_at = parse_news_release(release_date, release_time)
        except ValueError:
            continue
        release_iso = release_at.isoformat()
        if release_iso in seen_release_times:
            continue
        seen_release_times.add(release_iso)
        raw_id = str(raw.get("id") or "").strip()
        event_id = (
            raw_id
            if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw_id)
            else _news_event_id(release_at)
        )
        if event_id in seen_event_ids:
            event_id = _news_event_id(release_at)
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        normalized.append(
            {
                "id": event_id,
                "release_date": release_at.strftime("%Y-%m-%d"),
                "release_time": release_at.strftime("%H:%M"),
                "release_at": release_iso,
            }
        )
    normalized.sort(key=lambda item: (item["release_at"], item["id"]))
    return normalized


def _news_event_times(event: Dict[str, Any]) -> Tuple[datetime, datetime]:
    release_at = parse_news_release(
        event.get("release_date"), event.get("release_time")
    )
    return release_at, release_at - timedelta(
        minutes=NEWS_LIQUIDATION_LEAD_MINUTES
    )


def migrate_settings(data: Any) -> Dict[str, Any]:
    source, _schema_migrated = upgrade_fxweekend_settings_schema(data)
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(source)
    if "cutoff_time_dst" not in source:
        merged["cutoff_time_dst"] = _normalize_hhmm(source.get("cutoff_hour_dst"), "05:00")
    else:
        merged["cutoff_time_dst"] = _normalize_hhmm(source.get("cutoff_time_dst"), "05:00")
    if "cutoff_time_standard" not in source:
        merged["cutoff_time_standard"] = _normalize_hhmm(
            source.get("cutoff_hour_standard"), "06:00"
        )
    else:
        merged["cutoff_time_standard"] = _normalize_hhmm(
            source.get("cutoff_time_standard"), "06:00"
        )
    merged["cutoff_hour_dst"] = int(merged["cutoff_time_dst"].split(":")[0])
    merged["cutoff_hour_standard"] = int(merged["cutoff_time_standard"].split(":")[0])
    merged["trigger_weekday"] = max(0, min(6, int(merged.get("trigger_weekday", 5))))
    merged["check_interval_seconds"] = max(
        5, int(merged.get("check_interval_seconds", 60) or 60)
    )
    merged["max_retry_backoff_seconds"] = max(
        merged["check_interval_seconds"],
        int(merged.get("max_retry_backoff_seconds", 300) or 300),
    )
    merged["close_method"] = (
        "trades" if str(merged.get("close_method")).strip().lower() == "trades" else "positions"
    )
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["dry_run"] = bool(merged.get("dry_run", False))
    merged["account_modes"] = _ordered_account_modes(merged)
    merged["instrument_allowlist"] = sorted(
        {
            str(item).strip().upper()
            for item in (merged.get("instrument_allowlist") or [])
            if str(item).strip()
        }
    )
    merged["news_events"] = _normalize_news_events(
        merged.get("news_events")
    )
    return merged


def _ordered_account_modes(settings: Dict[str, Any]) -> List[str]:
    raw_modes = settings.get("account_modes")
    if not isinstance(raw_modes, (list, tuple, set)):
        raw_modes = FXWEEKEND_DEFAULT_ACCOUNT_MODES
    selected = {str(item).strip().lower() for item in raw_modes}
    return [mode for mode in ACCOUNT_MODES if mode in selected]


def load_settings() -> Dict[str, Any]:
    data: Any = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"Invalid settings file; using safe migrated defaults: {exc}")
    settings = migrate_settings(data)
    save_settings(settings)
    return settings


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    normalized = migrate_settings(settings)
    _atomic_json_write(SETTINGS_PATH, normalized)
    return normalized


def _empty_status() -> Dict[str, Any]:
    return {
        "running": False,
        "executor_pid": None,
        "executor_instance_id": None,
        "executor_started_at": None,
        "heartbeat_at": None,
        "sleeping": False,
        "sleep_reason": None,
        "sleep_started_at": None,
        "sleep_until": None,
        "scheduled_delay_seconds": 0.0,
        "state": "checking",
        "state_detail": "Executor has not started.",
        "selected_accounts": [],
        "last_access_check_at": None,
        "last_attempt_at": None,
        "last_verified_flat_at": None,
        "last_verified_window_cutoff": None,
        "last_verified_window_scope_fingerprint": None,
        "last_verified_window_account_times": {},
        "last_verified_window_account_scope_hashes": {},
        "next_news_release": None,
        "next_news_liquidation_cutoff": None,
        "news_status": "No news releases scheduled.",
        "news_last_result": None,
        "news_audit": {},
        "last_error": None,
        "consecutive_failures": 0,
        "accounts": {},
    }


def _load_status() -> Dict[str, Any]:
    payload = _empty_status()
    if STATUS_PATH.exists():
        try:
            saved = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                payload.update(saved)
        except Exception:
            pass
    if not isinstance(payload.get("news_audit"), dict):
        payload["news_audit"] = {}
    if not isinstance(payload.get("news_last_result"), (dict, type(None))):
        payload["news_last_result"] = None
    payload["running"] = False
    return payload


STATUS: Dict[str, Any] = _load_status()


def status_snapshot() -> Dict[str, Any]:
    with _status_lock:
        return deepcopy(STATUS)


def update_status(*, persist: bool = True, **updates: Any) -> Dict[str, Any]:
    with _status_lock:
        STATUS.update(updates)
        payload = deepcopy(STATUS)
        if persist:
            _atomic_json_write(STATUS_PATH, payload)
        return payload


def is_us_dst(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(pytz.utc)
    if current.tzinfo is None:
        current = pytz.utc.localize(current)
    return bool(current.astimezone(NY_TZ).dst())


def cutoff_time(settings: Dict[str, Any], at: Optional[datetime] = None) -> str:
    return (
        str(settings["cutoff_time_dst"])
        if is_us_dst(at)
        else str(settings["cutoff_time_standard"])
    )


def _localize_brisbane(day: datetime, hhmm: str) -> datetime:
    hour, minute = [int(part) for part in hhmm.split(":")]
    naive = datetime(day.year, day.month, day.day, hour, minute)
    return BRISBANE_TZ.localize(naive)


def _cutoff_for_day(settings: Dict[str, Any], candidate_day: datetime) -> datetime:
    provisional = _localize_brisbane(candidate_day, "12:00")
    return _localize_brisbane(candidate_day, cutoff_time(settings, provisional))


def compute_next_cutoff(
    settings: Dict[str, Any], now: Optional[datetime] = None
) -> datetime:
    current = now or _now_brisbane()
    if current.tzinfo is None:
        current = BRISBANE_TZ.localize(current)
    current = current.astimezone(BRISBANE_TZ)
    target_weekday = int(settings.get("trigger_weekday", 5))
    for offset in range(0, 8):
        day = current + timedelta(days=offset)
        if day.weekday() != target_weekday:
            continue
        candidate = _cutoff_for_day(settings, day)
        if candidate > current:
            return candidate
    raise RuntimeError("Unable to compute the next FX Weekend cutoff.")


def compute_next_trigger(settings: Dict[str, Any], now: Optional[datetime] = None) -> str:
    return compute_next_cutoff(settings, now).isoformat()


def _latest_cutoff(settings: Dict[str, Any], now: datetime) -> datetime:
    current = now.astimezone(BRISBANE_TZ)
    target_weekday = int(settings.get("trigger_weekday", 5))
    days_back = (current.weekday() - target_weekday) % 7
    day = current - timedelta(days=days_back)
    candidate = _cutoff_for_day(settings, day)
    if candidate > current:
        day -= timedelta(days=7)
        candidate = _cutoff_for_day(settings, day)
    return candidate


def closure_window(
    settings: Dict[str, Any], now: Optional[datetime] = None
) -> Dict[str, Any]:
    current = (now or _now_brisbane()).astimezone(BRISBANE_TZ)
    cutoff = _latest_cutoff(settings, current)
    cutoff_ny = cutoff.astimezone(NY_TZ)
    close_day = cutoff_ny.date()
    if cutoff_ny.weekday() != 4:
        close_day -= timedelta(days=(cutoff_ny.weekday() - 4) % 7)
    market_close = NY_TZ.localize(
        datetime(close_day.year, close_day.month, close_day.day, 17, 0)
    ).astimezone(BRISBANE_TZ)
    reopen_day = close_day + timedelta(days=2)
    market_reopen = NY_TZ.localize(
        datetime(reopen_day.year, reopen_day.month, reopen_day.day, 17, 0)
    ).astimezone(BRISBANE_TZ)
    if current < cutoff:
        phase = "before cutoff"
    elif current <= market_close:
        phase = "closure"
    elif current < market_reopen:
        phase = "missed"
    else:
        phase = "before cutoff"
    return {
        "phase": phase,
        "cutoff": cutoff,
        "market_close": market_close,
        "market_reopen": market_reopen,
        "now": current,
    }


def _headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _request(
    method: str,
    url: str,
    headers: Dict[str, str],
    json_payload: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    return requests.request(
        method, url, headers=headers, json=json_payload, timeout=15
    )


def _get_open_items(config: Dict[str, str]) -> Dict[str, Any]:
    base_url = config["base_url"]
    account_id = config["account_id"]
    headers = _headers(config["api_key"])
    result: Dict[str, Any] = {
        "positions": [],
        "trades": [],
        "requests": [],
        "errors": [],
    }
    for kind, endpoint, key in (
        ("positions", "openPositions", "positions"),
        ("trades", "openTrades", "trades"),
    ):
        request_result: Dict[str, Any] = {
            "scope": kind,
            "method": "GET",
            "http_status": None,
            "ok": False,
        }
        try:
            response = _request(
                "GET", f"{base_url}/accounts/{account_id}/{endpoint}", headers
            )
            request_result["http_status"] = response.status_code
            if response.status_code != 200:
                error = f"{kind} GET failed with HTTP {response.status_code}"
                request_result["error"] = error
                result["errors"].append(error)
                result["requests"].append(request_result)
                continue
            try:
                payload = response.json()
            except Exception as exc:
                error = f"{kind} GET returned invalid JSON: {_safe_error(exc)}"
                request_result["error"] = error
                result["errors"].append(error)
                result["requests"].append(request_result)
                continue
            values = payload.get(key, []) if isinstance(payload, dict) else None
            if not isinstance(values, list):
                error = f"{kind} GET returned an invalid payload"
                request_result["error"] = error
                result["errors"].append(error)
                result["requests"].append(request_result)
                continue
            request_result["ok"] = True
            result[kind] = values
            result["requests"].append(request_result)
        except requests.RequestException as exc:
            error = f"{kind} GET request failed: {_safe_error(exc)}"
            request_result["error"] = error
            result["errors"].append(error)
            result["requests"].append(request_result)
        except Exception as exc:
            error = f"{kind} GET failed: {_safe_error(exc)}"
            request_result["error"] = error
            result["errors"].append(error)
            result["requests"].append(request_result)
    return result


def _position_open(position: Dict[str, Any]) -> bool:
    try:
        return float((position.get("long") or {}).get("units") or 0) != 0 or float(
            (position.get("short") or {}).get("units") or 0
        ) != 0
    except (TypeError, ValueError):
        return True


def _scoped_items(
    items: Iterable[Dict[str, Any]], allowlist: Iterable[str]
) -> List[Dict[str, Any]]:
    allowed = {str(item).upper() for item in allowlist}
    return [
        item
        for item in items
        if isinstance(item, dict)
        and (not allowed or str(item.get("instrument") or "").upper() in allowed)
    ]


def _close_deadline_is_open(close_deadline: Optional[datetime]) -> bool:
    if close_deadline is None:
        return True
    current = _now_brisbane()
    if current.tzinfo is None:
        current = BRISBANE_TZ.localize(current)
    deadline = close_deadline
    if deadline.tzinfo is None:
        deadline = BRISBANE_TZ.localize(deadline)
    return current.astimezone(BRISBANE_TZ) < deadline.astimezone(
        BRISBANE_TZ
    )


def _close_requested_scope(
    config: Dict[str, str],
    opened: Dict[str, Any],
    settings: Dict[str, Any],
    *,
    can_close: bool,
    close_deadline: Optional[datetime] = None,
    on_progress: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> List[Dict[str, Any]]:
    mode = settings.get("close_method", "positions")
    allowlist = settings.get("instrument_allowlist") or []
    base_url = config["base_url"]
    account_id = config["account_id"]
    headers = _headers(config["api_key"])
    results: List[Dict[str, Any]] = []

    def record(item: Dict[str, Any]) -> None:
        results.append(item)
        if on_progress is not None:
            on_progress(deepcopy(results))

    def closure_request_allowed() -> bool:
        return bool(
            can_close
            and _close_deadline_is_open(close_deadline)
        )

    if mode == "trades":
        trades = _scoped_items(opened.get("trades") or [], allowlist)
        for trade in trades:
            trade_id = str(trade.get("id") or "")
            instrument = str(trade.get("instrument") or "unknown")
            item = {"scope": "trade", "trade_id": trade_id, "instrument": instrument}
            if not closure_request_allowed():
                item.update(
                    {
                        "ok": False,
                        "window_closed": True,
                        "error": "market closing window has ended",
                    }
                )
            elif settings.get("dry_run"):
                item.update({"ok": False, "error": "dry run; no close sent"})
            else:
                try:
                    response = _request(
                        "PUT",
                        f"{base_url}/accounts/{account_id}/trades/{trade_id}/close",
                        headers,
                        {"units": "ALL"},
                    )
                    item.update(
                        {
                            "http_status": response.status_code,
                            "ok": response.status_code in {200, 201},
                        }
                    )
                    if not item["ok"]:
                        item["error"] = (
                            f"trade close failed with HTTP {response.status_code}"
                        )
                except requests.RequestException as exc:
                    item.update(
                        {
                            "ok": False,
                            "error": f"trade close request failed: {_safe_error(exc)}",
                        }
                    )
            record(item)
        return results

    positions = [
        item
        for item in _scoped_items(opened.get("positions") or [], allowlist)
        if _position_open(item)
    ]
    for position in positions:
        instrument = str(position.get("instrument") or "unknown")
        payload: Dict[str, str] = {}
        try:
            if float((position.get("long") or {}).get("units") or 0) != 0:
                payload["longUnits"] = "ALL"
            if float((position.get("short") or {}).get("units") or 0) != 0:
                payload["shortUnits"] = "ALL"
        except (TypeError, ValueError):
            payload = {"longUnits": "ALL", "shortUnits": "ALL"}
        item = {"scope": "position", "instrument": instrument}
        if not closure_request_allowed():
            item.update(
                {
                    "ok": False,
                    "window_closed": True,
                    "error": "market closing window has ended",
                }
            )
        elif settings.get("dry_run"):
            item.update({"ok": False, "error": "dry run; no close sent"})
        else:
            try:
                response = _request(
                    "PUT",
                    f"{base_url}/accounts/{account_id}/positions/{instrument}/close",
                    headers,
                    payload,
                )
                item.update(
                    {
                        "http_status": response.status_code,
                        "ok": response.status_code in {200, 201},
                    }
                )
                if not item["ok"]:
                    item["error"] = (
                        f"position close failed with HTTP {response.status_code}"
                    )
            except requests.RequestException as exc:
                item.update(
                    {
                        "ok": False,
                        "error": f"position close request failed: {_safe_error(exc)}",
                    }
                )
        record(item)
    return results


def _account_open_counts(opened: Dict[str, Any]) -> Tuple[int, int]:
    position_count = sum(
        1
        for item in (opened.get("positions") or [])
        if isinstance(item, dict) and _position_open(item)
    )
    trade_count = sum(1 for item in (opened.get("trades") or []) if isinstance(item, dict))
    return position_count, trade_count


def _account_scope_hash(mode: str, config: Dict[str, str]) -> str:
    scope = {
        "mode": str(mode or "").strip().lower(),
        "base_url": str(config.get("base_url") or "").strip().rstrip("/"),
        "account_id": str(config.get("account_id") or "").strip(),
    }
    return hashlib.sha256(
        json.dumps(
            scope, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def process_account(
    mode: str,
    settings: Dict[str, Any],
    *,
    can_close: bool = True,
    close_deadline: Optional[datetime] = None,
    check_only: bool = False,
    allow_post_window_flat_verification: bool = True,
    on_state_change: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    attempted_at = _iso_now()
    result: Dict[str, Any] = {
        "mode": mode,
        "state": "checking",
        "last_attempt_at": attempted_at,
        "last_verified_flat_at": None,
        "position_count": None,
        "trade_count": None,
        "open_count": None,
        "last_error": None,
        "requests": [],
        "closures": [],
    }
    try:
        config = resolve_account_config(mode)
        result["account_scope_hash"] = _account_scope_hash(mode, config)
    except OandaAPIError as exc:
        result.update(
            {"state": "credential failure", "last_error": _safe_error(exc)}
        )
        return result
    try:
        opened = _get_open_items(config)
        result["requests"].extend(opened.get("requests") or [])
        position_count, trade_count = _account_open_counts(opened)
        failed_scopes = {
            str(item.get("scope") or "")
            for item in (opened.get("requests") or [])
            if item.get("ok") is False
        }
        result.update(
            {
                "position_count": (
                    None if "positions" in failed_scopes else position_count
                ),
                "trade_count": (
                    None if "trades" in failed_scopes else trade_count
                ),
                "open_count": (
                    None
                    if failed_scopes
                    else max(position_count, trade_count)
                ),
            }
        )
        if opened.get("errors"):
            result.update(
                {
                    "state": "API failure",
                    "last_error": "; ".join(
                        str(item) for item in opened.get("errors") or []
                    ),
                }
            )
            return result
        if check_only:
            result.update({"state": "checking", "last_error": None})
            return result
        if position_count == 0 and trade_count == 0:
            if not can_close and not allow_post_window_flat_verification:
                result.update(
                    {
                        "state": "missed cutoff/market closed",
                        "open_count": 0,
                        "last_error": (
                            "No open items are visible now, but this executor did "
                            "not verify the current cutoff during the tradable "
                            "closing window; the weekend guarantee was missed."
                        ),
                    }
                )
                return result
            verified_at = _iso_now()
            result.update(
                {
                    "state": "verified flat",
                    "last_verified_flat_at": verified_at,
                    "open_count": 0,
                }
            )
            return result
        if not can_close:
            result.update(
                {
                    "state": "missed cutoff/market closed",
                    "last_error": (
                        "Tradable closing window ended with "
                        f"{result['open_count']} open item(s); the weekend "
                        "guarantee was missed."
                    ),
                }
            )
            return result

        result["state"] = "closing"
        if on_state_change is not None:
            on_state_change(deepcopy(result))

        def report_closure_progress(
            closures: List[Dict[str, Any]],
        ) -> None:
            result["closures"] = closures
            if on_state_change is not None:
                on_state_change(deepcopy(result))

        result["closures"] = _close_requested_scope(
            config,
            opened,
            settings,
            can_close=can_close,
            close_deadline=close_deadline,
            on_progress=report_closure_progress,
        )
        # The post-close re-fetch is authoritative even if one close response was
        # ambiguous. Success is never inferred from a close response alone.
        after = _get_open_items(config)
        result["requests"].extend(after.get("requests") or [])
        if after.get("errors"):
            result.update(
                {
                    "state": "API failure",
                    "position_count": None,
                    "trade_count": None,
                    "open_count": None,
                    "last_error": (
                        "Post-close verification failed: "
                        + "; ".join(
                            str(item) for item in after.get("errors") or []
                        )
                    ),
                }
            )
            return result
        position_count, trade_count = _account_open_counts(after)
        result.update(
            {
                "position_count": position_count,
                "trade_count": trade_count,
                "open_count": max(position_count, trade_count),
            }
        )
        if position_count == 0 and trade_count == 0:
            verified_at = _iso_now()
            result.update(
                {
                    "state": "verified flat",
                    "last_verified_flat_at": verified_at,
                    "last_error": None,
                }
            )
            return result
        if any(
            item.get("window_closed")
            for item in result["closures"]
            if isinstance(item, dict)
        ):
            result.update(
                {
                    "state": "missed cutoff/market closed",
                    "last_error": (
                        "Tradable closing window ended with "
                        f"{result['open_count']} open item(s); no close "
                        "request was sent after market close."
                    ),
                }
            )
            return result
        failed = [item for item in result["closures"] if not item.get("ok")]
        result["state"] = "partial closure failure" if failed else "retry pending"
        result["last_error"] = (
            f"{len(failed)} closure request(s) failed; "
            f"{result['open_count']} open item(s) remain."
            if failed
            else f"{result['open_count']} open item(s) remain after the closure attempt."
        )
        return result
    except requests.RequestException as exc:
        result.update({"state": "API failure", "last_error": _safe_error(exc)})
        return result
    except Exception as exc:
        result.update({"state": "API failure", "last_error": _safe_error(exc)})
        return result


def _run_liquidation_impl(
    settings: Dict[str, Any],
    reason: str,
    *,
    can_close: bool = True,
    close_deadline: Optional[datetime] = None,
    allow_post_window_flat_verification: bool = True,
    progress_callback: Optional[
        Callable[[str, Dict[str, Dict[str, Any]]], None]
    ] = None,
) -> Dict[str, Any]:
    modes = _ordered_account_modes(settings)
    attempted_at = _iso_now()
    account_results: Dict[str, Dict[str, Any]] = {}
    if not modes:
        return {
            "state": "credential failure",
            "result": "No OANDA account mode is selected.",
            "error": "Select at least one OANDA account mode.",
            "last_attempt_at": attempted_at,
            "accounts": {},
            "verified_flat": False,
        }
    log(f"Starting OANDA account check ({reason}) for: {', '.join(modes)}.")
    for mode in modes:
        def report_account_state(
            account_state: Dict[str, Any], current_mode: str = mode
        ) -> None:
            account_results[current_mode] = account_state
            if progress_callback is not None:
                progress_callback(current_mode, deepcopy(account_results))

        try:
            account_results[mode] = process_account(
                mode,
                settings,
                can_close=can_close,
                close_deadline=close_deadline,
                allow_post_window_flat_verification=(
                    allow_post_window_flat_verification
                ),
                on_state_change=report_account_state,
            )
        except Exception as exc:
            account_results[mode] = {
                "mode": mode,
                "state": "API failure",
                "last_attempt_at": _iso_now(),
                "last_verified_flat_at": None,
                "position_count": None,
                "trade_count": None,
                "open_count": None,
                "last_error": _safe_error(exc) or "Unexpected account check failure.",
                "requests": [],
                "closures": [],
            }
    all_flat = (
        bool(modes)
        and set(account_results) == set(modes)
        and all(
            item.get("state") == "verified flat"
            for item in account_results.values()
        )
    )
    errors = [
        f"{mode}: {item.get('last_error')}"
        for mode, item in account_results.items()
        if item.get("last_error")
    ]
    if all_flat:
        state = "verified flat"
        error = None
    elif any(item.get("state") == "missed cutoff/market closed" for item in account_results.values()):
        state = "missed cutoff/market closed"
        error = "; ".join(errors) or "Weekend guarantee was missed."
    elif any(item.get("state") == "partial closure failure" for item in account_results.values()):
        state = "partial closure failure"
        error = "; ".join(errors)
    elif any(item.get("state") == "credential failure" for item in account_results.values()):
        state = "credential failure"
        error = "; ".join(errors)
    elif any(item.get("state") == "API failure" for item in account_results.values()):
        state = "API failure"
        error = "; ".join(errors)
    else:
        state = "retry pending"
        error = "; ".join(errors)
    verified_times = [
        str(item.get("last_verified_flat_at"))
        for item in account_results.values()
        if item.get("last_verified_flat_at")
    ]
    return {
        "state": state,
        "result": state,
        "error": error or None,
        "last_attempt_at": attempted_at,
        "last_verified_flat_at": max(verified_times) if all_flat and verified_times else None,
        "accounts": account_results,
        "verified_flat": all_flat,
    }


def run_liquidation(
    settings: Dict[str, Any],
    reason: str,
    *,
    can_close: bool = True,
    close_deadline: Optional[datetime] = None,
    lock_timeout: Optional[float] = None,
    allow_post_window_flat_verification: bool = True,
    progress_callback: Optional[
        Callable[[str, Dict[str, Dict[str, Any]]], None]
    ] = None,
) -> Dict[str, Any]:
    """Run one close/verify pass without overlapping another liquidation."""

    if lock_timeout is None:
        acquired = _liquidation_lock.acquire()
    else:
        acquired = _liquidation_lock.acquire(
            timeout=max(0.0, float(lock_timeout))
        )
    if not acquired:
        attempted_at = _iso_now()
        return {
            "state": "liquidation already in progress",
            "result": "liquidation already in progress",
            "error": (
                "Another scheduled or manual liquidation is already in "
                "progress. No duplicate close request was submitted."
            ),
            "last_attempt_at": attempted_at,
            "accounts": {},
            "verified_flat": False,
        }
    try:
        return _run_liquidation_impl(
            settings,
            reason,
            can_close=can_close,
            close_deadline=close_deadline,
            allow_post_window_flat_verification=(
                allow_post_window_flat_verification
            ),
            progress_callback=progress_callback,
        )
    finally:
        _liquidation_lock.release()


def run_read_only_account_check(
    settings: Dict[str, Any], reason: str = "read-only access check"
) -> Dict[str, Any]:
    modes = _ordered_account_modes(settings)
    checked_at = _iso_now()
    accounts: Dict[str, Dict[str, Any]] = {}
    if not modes:
        return {
            "state": "credential failure",
            "result": "No OANDA account mode is selected.",
            "error": "Select at least one OANDA account mode.",
            "checked_at": checked_at,
            "accounts": {},
        }
    log(f"Starting OANDA read-only check ({reason}) for: {', '.join(modes)}.")
    for mode in modes:
        try:
            accounts[mode] = process_account(
                mode,
                settings,
                can_close=False,
                check_only=True,
            )
        except Exception as exc:
            accounts[mode] = {
                "mode": mode,
                "state": "API failure",
                "last_attempt_at": _iso_now(),
                "last_verified_flat_at": None,
                "position_count": None,
                "trade_count": None,
                "open_count": None,
                "last_error": _safe_error(exc) or "Unexpected account check failure.",
                "requests": [],
                "closures": [],
            }
        if accounts[mode].get("state") == "checking":
            accounts[mode]["state"] = "before cutoff"
    errors = [
        f"{mode}: {item.get('last_error')}"
        for mode, item in accounts.items()
        if item.get("last_error")
    ]
    if any(item.get("state") == "credential failure" for item in accounts.values()):
        state = "credential failure"
    elif any(item.get("state") == "API failure" for item in accounts.values()):
        state = "API failure"
    else:
        state = "before cutoff"
    return {
        "state": state,
        "result": state,
        "error": "; ".join(errors) or None,
        "checked_at": checked_at,
        "accounts": accounts,
    }


def _apply_attempt_status(result: Dict[str, Any]) -> None:
    previous = status_snapshot()
    failures = 0 if result.get("verified_flat") else int(previous.get("consecutive_failures") or 0) + 1
    update_status(
        state=result.get("state"),
        state_detail=result.get("result"),
        last_attempt_at=result.get("last_attempt_at"),
        last_verified_flat_at=result.get("last_verified_flat_at")
        or previous.get("last_verified_flat_at"),
        last_error=result.get("error"),
        accounts=result.get("accounts") or {},
        consecutive_failures=failures,
    )


def _coverage_scope_fingerprint(settings: Dict[str, Any]) -> str:
    scope = {
        "enabled": bool(settings.get("enabled")),
        "account_modes": _ordered_account_modes(settings),
        "close_method": str(settings.get("close_method") or "positions"),
        "instrument_allowlist": list(settings.get("instrument_allowlist") or []),
        "dry_run": bool(settings.get("dry_run")),
        "check_interval_seconds": int(
            settings.get("check_interval_seconds") or 60
        ),
        "max_retry_backoff_seconds": int(
            settings.get("max_retry_backoff_seconds") or 300
        ),
    }
    encoded = json.dumps(
        scope, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = BRISBANE_TZ.localize(parsed)
    return parsed.astimezone(BRISBANE_TZ)


def _news_verification_datetime(value: Any) -> Optional[datetime]:
    """Parse only explicit timezone-aware evidence for a news cutoff."""

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(BRISBANE_TZ)


def _news_selected_scope_verified_at(
    result: Dict[str, Any], selected: List[str]
) -> Optional[datetime]:
    """Return the latest trustworthy flat time for the full selected scope."""

    if not result.get("verified_flat") or not selected:
        return None
    accounts = result.get("accounts")
    if not isinstance(accounts, dict):
        return None
    verified_times: List[datetime] = []
    for mode in selected:
        account = accounts.get(mode)
        if (
            not isinstance(account, dict)
            or account.get("state") != "verified flat"
        ):
            return None
        verified_at = _news_verification_datetime(
            account.get("last_verified_flat_at")
        )
        if verified_at is None:
            return None
        verified_times.append(verified_at)
    return max(verified_times) if verified_times else None


def _as_brisbane(value: datetime) -> datetime:
    if value.tzinfo is None:
        return BRISBANE_TZ.localize(value)
    return value.astimezone(BRISBANE_TZ)


def _news_scope_fingerprint(settings: Dict[str, Any]) -> str:
    """Fingerprint the selected OANDA scope without retaining account IDs."""

    account_scopes: Dict[str, Optional[str]] = {}
    for mode in _ordered_account_modes(settings):
        try:
            account_scopes[mode] = _account_scope_hash(
                mode, resolve_account_config(mode)
            )
        except OandaAPIError:
            account_scopes[mode] = None
    scope = {
        "account_modes": _ordered_account_modes(settings),
        "account_scopes": account_scopes,
        "close_method": str(settings.get("close_method") or "positions"),
        "dry_run": bool(settings.get("dry_run")),
        # News always covers the full selected OANDA scope. The weekend
        # instrument allowlist is intentionally not part of this fingerprint.
        "news_instrument_filter": None,
    }
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _news_audit(status: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = status.get("news_audit")
    if not isinstance(raw, dict):
        return {}
    return {
        str(event_id): deepcopy(item)
        for event_id, item in raw.items()
        if isinstance(item, dict)
    }


def _news_event_is_complete(
    event: Dict[str, Any],
    audit: Dict[str, Dict[str, Any]],
    scope_fingerprint: str,
    current: datetime,
) -> bool:
    entry = audit.get(str(event.get("id") or "")) or {}
    if entry.get("deleted_at"):
        return True
    if str(entry.get("scope_fingerprint") or "") != scope_fingerprint:
        return False
    if entry.get("verified_flat_at"):
        return True
    release_at, _cutoff = _news_event_times(event)
    return bool(
        current >= release_at and entry.get("post_release_attempted_at")
    )


def _due_news_events(
    settings: Dict[str, Any],
    status: Dict[str, Any],
    now: datetime,
) -> List[Dict[str, str]]:
    current = _as_brisbane(now)
    audit = _news_audit(status)
    scope_fingerprint = _news_scope_fingerprint(settings)
    due: List[Dict[str, str]] = []
    for event in settings.get("news_events") or []:
        if not isinstance(event, dict):
            continue
        _release_at, cutoff = _news_event_times(event)
        if current < cutoff:
            continue
        if _news_event_is_complete(
            event, audit, scope_fingerprint, current
        ):
            continue
        due.append(event)
    return sorted(due, key=lambda item: _news_event_times(item)[1])


def _next_news_execution_deadline(
    settings: Dict[str, Any],
    status: Dict[str, Any],
    now: datetime,
) -> Optional[datetime]:
    current = _as_brisbane(now)
    audit = _news_audit(status)
    scope_fingerprint = _news_scope_fingerprint(settings)
    deadlines: List[datetime] = []
    for event in settings.get("news_events") or []:
        if not isinstance(event, dict):
            continue
        release_at, cutoff = _news_event_times(event)
        if _news_event_is_complete(
            event, audit, scope_fingerprint, current
        ):
            continue
        if current < cutoff:
            deadlines.append(cutoff)
        elif current < release_at:
            deadlines.append(release_at)
        else:
            deadlines.append(current)
    return min(deadlines) if deadlines else None


def _news_schedule_fields(
    settings: Dict[str, Any],
    status: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = _as_brisbane(now or _now_brisbane())
    events = [
        item
        for item in (settings.get("news_events") or [])
        if isinstance(item, dict)
    ]
    future_events = [
        item for item in events if _news_event_times(item)[0] >= current
    ]
    next_event = (
        min(future_events, key=lambda item: _news_event_times(item)[0])
        if future_events
        else None
    )
    next_release: Optional[str] = None
    next_cutoff: Optional[str] = None
    if next_event is not None:
        release_at, cutoff = _news_event_times(next_event)
        next_release = release_at.isoformat()
        next_cutoff = cutoff.isoformat()

    due = _due_news_events(settings, status, current) if events else []
    if not events:
        news_status = "No news releases scheduled."
    elif not settings.get("enabled"):
        news_status = "News liquidation is disabled with FX Weekend."
    elif due:
        release_at, _cutoff = _news_event_times(due[0])
        news_status = (
            "News cutoff missed; an immediate safe-close verification is due."
            if current >= release_at
            else "News liquidation is due; close/flat verification is in progress or awaiting retry."
        )
    elif next_event is not None:
        event_audit = _news_audit(status).get(str(next_event.get("id"))) or {}
        news_status = (
            "Selected OANDA accounts are verified flat for the next release."
            if event_audit.get("verified_flat_at")
            else "Next news liquidation is scheduled for release minus 15 minutes."
        )
    else:
        last_result = status.get("news_last_result")
        news_status = (
            str((last_result or {}).get("state") or "News schedule has no future releases.")
            if isinstance(last_result, dict)
            else "News schedule has no future releases."
        )
    return {
        "next_news_release": next_release,
        "next_news_liquidation_cutoff": next_cutoff,
        "news_status": news_status,
    }


def _news_account_outcomes(
    result: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    outcomes: Dict[str, Dict[str, Any]] = {}
    accounts = result.get("accounts")
    if not isinstance(accounts, dict):
        return outcomes
    for mode, raw in accounts.items():
        if not isinstance(raw, dict):
            continue
        outcomes[str(mode)] = {
            "state": raw.get("state"),
            "attempt_at": raw.get("last_attempt_at"),
            "verified_flat_at": raw.get("last_verified_flat_at"),
            "position_count": raw.get("position_count"),
            "trade_count": raw.get("trade_count"),
            "open_count": raw.get("open_count"),
            "account_scope_hash": raw.get("account_scope_hash"),
            "error": raw.get("last_error"),
        }
    return outcomes


def _run_due_news_events(
    settings: Dict[str, Any],
    due_events: List[Dict[str, str]],
    current: datetime,
    weekend_window: Dict[str, Any],
) -> Dict[str, Any]:
    """Coalesce all currently due news releases into one verified close pass."""

    current = _as_brisbane(current)
    attempt_at = current.isoformat()
    selected = _ordered_account_modes(settings)
    scope_fingerprint = _news_scope_fingerprint(settings)
    previous_status = status_snapshot()
    audit = _news_audit(previous_status)
    for event in due_events:
        event_id = str(event["id"])
        release_at, cutoff = _news_event_times(event)
        prior = audit.get(event_id) or {}
        if (
            prior
            and str(prior.get("scope_fingerprint") or "")
            and str(prior.get("scope_fingerprint")) != scope_fingerprint
        ):
            history = list(prior.get("scope_history") or [])
            history.append(
                {
                    key: deepcopy(value)
                    for key, value in prior.items()
                    if key != "scope_history"
                }
            )
            prior = {"scope_history": history[-20:]}
        prior_cutoff_missed = (
            prior.get("cutoff_met") is False
            or "cutoff missed" in str(prior.get("state") or "").lower()
        )
        cutoff_met = prior.get("cutoff_met")
        if prior_cutoff_missed or current > cutoff:
            cutoff_met = False
        audit[event_id] = {
            **prior,
            "event_id": event_id,
            "release_at": release_at.isoformat(),
            "liquidation_cutoff": cutoff.isoformat(),
            "attempt_at": attempt_at,
            "attempt_count": int(prior.get("attempt_count") or 0) + 1,
            "scope_fingerprint": scope_fingerprint,
            "account_outcomes": {},
            "verified_flat_at": prior.get("verified_flat_at"),
            "cutoff_met": cutoff_met,
            "state": "liquidation in progress",
            "last_error": None,
        }

    update_status(
        running=True,
        heartbeat_at=attempt_at,
        state="checking",
        state_detail=(
            "Checking the full selected OANDA scope for due news releases."
        ),
        selected_accounts=selected,
        news_status="News liquidation is in progress.",
        news_audit=audit,
    )

    close_deadlines = [
        release_at
        for release_at, _cutoff in (
            _news_event_times(event) for event in due_events
        )
        if release_at > current
    ]
    can_close = weekend_window.get("phase") != "missed"
    market_close = weekend_window.get("market_close")
    if (
        weekend_window.get("phase") == "closure"
        and isinstance(market_close, datetime)
        and _as_brisbane(market_close) > current
    ):
        close_deadlines.append(_as_brisbane(market_close))
    close_deadline = min(close_deadlines) if close_deadlines else None

    news_settings = deepcopy(settings)
    # News liquidation is deliberately account-wide. The weekend allowlist
    # remains untouched in durable settings and still applies to weekend runs.
    news_settings["instrument_allowlist"] = []

    def report_progress(
        mode: str, accounts: Dict[str, Dict[str, Any]]
    ) -> None:
        update_status(
            running=True,
            heartbeat_at=_iso_now(),
            state="closing",
            state_detail=(
                f"Closing the full OANDA scope for {mode.upper()} before news."
            ),
            selected_accounts=selected,
            accounts=accounts,
            news_status="News liquidation is in progress.",
        )

    result = run_liquidation(
        news_settings,
        "scheduled news release",
        can_close=can_close,
        close_deadline=close_deadline,
        allow_post_window_flat_verification=True,
        progress_callback=report_progress,
    )
    _apply_attempt_status(result)

    verified = bool(result.get("verified_flat"))
    latest_scope_verification = _news_selected_scope_verified_at(
        result, selected
    )
    verified_at = (
        latest_scope_verification.isoformat()
        if latest_scope_verification is not None
        else None
    )
    verification_evidence_error = (
        None
        if not verified or latest_scope_verification is not None
        else (
            "Verified-flat result lacked a valid timezone-aware verification "
            "timestamp for every selected account."
        )
    )
    outcomes = _news_account_outcomes(result)
    cutoff_results: List[bool] = []
    for event in due_events:
        event_id = str(event["id"])
        release_at, cutoff = _news_event_times(event)
        entry = audit[event_id]
        if verified:
            # A scheduler wake at the boundary is insufficient evidence. Every
            # selected account must have actually been verified flat by it.
            cutoff_met = bool(
                entry.get("cutoff_met") is not False
                and current <= cutoff
                and latest_scope_verification is not None
                and latest_scope_verification <= cutoff
            )
            state = (
                "verified flat at news cutoff"
                if cutoff_met
                else "verified flat; news cutoff missed"
            )
            entry.update(
                {
                    "verified_flat_at": verified_at,
                    "cutoff_met": cutoff_met,
                    "state": state,
                    "last_error": verification_evidence_error,
                }
            )
            if current >= release_at and latest_scope_verification is None:
                entry["post_release_attempted_at"] = attempt_at
        else:
            cutoff_met = False
            state = (
                "news cutoff missed; final safe-close was not verified"
                if current >= release_at
                else "news cutoff missed; retry pending before release"
            )
            entry.update(
                {
                    "verified_flat_at": None,
                    "cutoff_met": False,
                    "state": state,
                    "last_error": result.get("error") or result.get("state"),
                }
            )
            if current >= release_at:
                entry["post_release_attempted_at"] = attempt_at
        entry["account_outcomes"] = deepcopy(outcomes)
        cutoff_results.append(cutoff_met)

    overall_state = (
        "verified flat at news cutoff"
        if verified and all(cutoff_results)
        else "verified flat; one or more news cutoffs were missed"
        if verified
        else "news liquidation retry pending"
        if any(current < _news_event_times(event)[0] for event in due_events)
        else "news cutoff missed; safe-close was not verified"
    )
    last_result = {
        "event_ids": [str(event["id"]) for event in due_events],
        "state": overall_state,
        "verified_flat": verified,
        "attempt_at": attempt_at,
        "scope_fingerprint": scope_fingerprint,
        "account_outcomes": deepcopy(outcomes),
        "verified_flat_at": verified_at,
        "cutoff_met": bool(verified and all(cutoff_results)),
        "error": result.get("error") or verification_evidence_error,
    }
    status_with_audit = {
        **status_snapshot(),
        "news_audit": audit,
        "news_last_result": last_result,
    }
    schedule_fields = _news_schedule_fields(
        settings, status_with_audit, current
    )
    update_status(
        news_audit=audit,
        news_last_result=last_result,
        state_detail=overall_state,
        **schedule_fields,
    )
    return result


def _window_coverage_is_current(
    settings: Dict[str, Any],
    window: Dict[str, Any],
    status: Dict[str, Any],
) -> bool:
    cutoff = window.get("cutoff")
    market_close = window.get("market_close")
    if not isinstance(cutoff, datetime) or not isinstance(
        market_close, datetime
    ):
        return False
    if cutoff.tzinfo is None:
        cutoff = BRISBANE_TZ.localize(cutoff)
    if market_close.tzinfo is None:
        market_close = BRISBANE_TZ.localize(market_close)
    if str(status.get("last_verified_window_cutoff") or "") != cutoff.isoformat():
        return False
    if (
        str(status.get("last_verified_window_scope_fingerprint") or "")
        != _coverage_scope_fingerprint(settings)
    ):
        return False
    account_times = status.get("last_verified_window_account_times")
    if not isinstance(account_times, dict):
        return False
    account_scope_hashes = status.get(
        "last_verified_window_account_scope_hashes"
    )
    if not isinstance(account_scope_hashes, dict):
        return False
    selected = _ordered_account_modes(settings)
    if not selected:
        return False
    grace_seconds = max(
        30,
        min(
            600,
            int(settings.get("check_interval_seconds") or 60) * 2,
        ),
    )
    market_close = market_close.astimezone(BRISBANE_TZ)
    for mode in selected:
        try:
            current_config = resolve_account_config(mode)
        except OandaAPIError:
            return False
        if (
            str(account_scope_hashes.get(mode) or "")
            != _account_scope_hash(mode, current_config)
        ):
            return False
        verified_at = _status_datetime(account_times.get(mode))
        if verified_at is None:
            return False
        seconds_before_close = (
            market_close - verified_at
        ).total_seconds()
        if seconds_before_close < 0 or seconds_before_close > grace_seconds:
            return False
    return True


def scheduler_iteration(
    settings: Dict[str, Any], now: Optional[datetime] = None
) -> Dict[str, Any]:
    current = _as_brisbane(now or _now_brisbane())
    selected = _ordered_account_modes(settings)
    heartbeat = current.isoformat()
    if not settings.get("enabled"):
        schedule_fields = _news_schedule_fields(
            settings, status_snapshot(), current
        )
        return update_status(
            running=True,
            heartbeat_at=heartbeat,
            sleeping=False,
            sleep_reason=None,
            sleep_started_at=None,
            sleep_until=None,
            scheduled_delay_seconds=0.0,
            state="disabled",
            state_detail="FX Weekend execution is disabled by durable settings.",
            selected_accounts=selected,
            last_error=None,
            **schedule_fields,
        )
    window = closure_window(settings, current)
    with _news_schedule_lock:
        due_news = _due_news_events(settings, status_snapshot(), current)
        if due_news:
            _run_due_news_events(settings, due_news, current, window)
            return status_snapshot()
    if window["phase"] == "before cutoff":
        check = run_read_only_account_check(settings, "scheduled before-cutoff check")
        previous = status_snapshot()
        failed = check.get("state") in {"credential failure", "API failure"}
        pending_status = {
            **previous,
            "accounts": check.get("accounts") or {},
        }
        schedule_fields = _news_schedule_fields(
            settings, pending_status, current
        )
        return update_status(
            running=True,
            heartbeat_at=heartbeat,
            sleeping=False,
            sleep_reason=None,
            sleep_started_at=None,
            sleep_until=None,
            scheduled_delay_seconds=0.0,
            state=check.get("state"),
            state_detail=(
                str(check.get("error"))
                if failed
                else f"Next cutoff: {compute_next_trigger(settings, current)}"
            ),
            selected_accounts=selected,
            last_access_check_at=check.get("checked_at"),
            last_error=check.get("error"),
            accounts=check.get("accounts") or {},
            consecutive_failures=(
                int(previous.get("consecutive_failures") or 0) + 1
                if failed
                else 0
            ),
            **schedule_fields,
        )
    if window["phase"] == "closure":
        update_status(
            running=True,
            heartbeat_at=heartbeat,
            sleeping=False,
            sleep_reason=None,
            sleep_started_at=None,
            sleep_until=None,
            scheduled_delay_seconds=0.0,
            state="checking",
            state_detail="Checking all selected OANDA accounts.",
            selected_accounts=selected,
        )
        def report_progress(
            mode: str, accounts: Dict[str, Dict[str, Any]]
        ) -> None:
            update_status(
                running=True,
                heartbeat_at=_iso_now(),
                state="closing",
                state_detail=f"Closing open OANDA scope for {mode.upper()}.",
                selected_accounts=selected,
                accounts=accounts,
            )

        result = run_liquidation(
            settings,
            "scheduled",
            can_close=True,
            close_deadline=window.get("market_close"),
            progress_callback=report_progress,
        )
        _apply_attempt_status(result)
        verified = bool(result.get("verified_flat"))
        account_times = {
            mode: account.get("last_verified_flat_at")
            for mode, account in (result.get("accounts") or {}).items()
            if isinstance(account, dict)
            and account.get("last_verified_flat_at")
        }
        account_scope_hashes = {
            mode: account.get("account_scope_hash")
            for mode, account in (result.get("accounts") or {}).items()
            if isinstance(account, dict)
            and account.get("account_scope_hash")
        }
        update_status(
            last_verified_window_cutoff=(
                window["cutoff"].isoformat() if verified else None
            ),
            last_verified_window_scope_fingerprint=(
                _coverage_scope_fingerprint(settings)
                if verified
                else None
            ),
            last_verified_window_account_times=(
                account_times if verified else {}
            ),
            last_verified_window_account_scope_hashes=(
                account_scope_hashes if verified else {}
            ),
        )
        schedule_fields = _news_schedule_fields(
            settings, status_snapshot(), current
        )
        update_status(**schedule_fields)
        return status_snapshot()
    previous = status_snapshot()
    covered_during_closure = _window_coverage_is_current(
        settings, window, previous
    )
    update_status(
        running=True,
        heartbeat_at=heartbeat,
        sleeping=False,
        sleep_reason=None,
        sleep_started_at=None,
        sleep_until=None,
        scheduled_delay_seconds=0.0,
        state="checking",
        state_detail=(
            "Closing window ended; performing a fresh read-only account check. "
            + (
                "This cutoff was verified during the tradable window."
                if covered_during_closure
                else "This process has no durable verification for the current cutoff."
            )
        ),
        selected_accounts=selected,
    )
    result = run_liquidation(
        settings,
        "missed-window verification",
        can_close=False,
        allow_post_window_flat_verification=covered_during_closure,
    )
    _apply_attempt_status(result)
    if not result.get("verified_flat"):
        update_status(
            last_verified_window_cutoff=None,
            last_verified_window_scope_fingerprint=None,
            last_verified_window_account_times={},
            last_verified_window_account_scope_hashes={},
        )
    schedule_fields = _news_schedule_fields(
        settings, status_snapshot(), current
    )
    update_status(**schedule_fields)
    return status_snapshot()


def _scheduler_delay_seconds(
    settings: Dict[str, Any],
    status: Dict[str, Any],
    now: Optional[datetime] = None,
) -> float:
    try:
        base_interval = max(
            5,
            int(settings.get("check_interval_seconds", 60)),
        )
    except (TypeError, ValueError):
        base_interval = 60
    try:
        failures = int(status.get("consecutive_failures") or 0)
    except (AttributeError, TypeError, ValueError):
        failures = 1
    retrying_failure = (
        status.get("state") in FINAL_FAILURE_STATES | {"retry pending"}
    )
    if retrying_failure:
        try:
            max_backoff = max(
                base_interval,
                int(
                    settings.get(
                        "max_retry_backoff_seconds",
                        300,
                    )
                ),
            )
        except (TypeError, ValueError):
            max_backoff = 300
        delay = min(
            max_backoff,
            base_interval * (2 ** min(max(failures - 1, 0), 4)),
        )
    else:
        delay = base_interval

    # Never let an intentional failure backoff consume the entire remaining
    # closure window. Retain bounded headroom for another close/verify attempt;
    # after market close, the missed-window branch prevents live liquidation.
    current = now or _now_brisbane()
    try:
        window = closure_window(settings, current)
        market_close = window.get("market_close")
        if (
            retrying_failure
            and window.get("phase") == "closure"
            and isinstance(
                market_close, datetime
            )
        ):
            if current.tzinfo is None:
                current = BRISBANE_TZ.localize(current)
            remaining = (
                market_close.astimezone(BRISBANE_TZ)
                - current.astimezone(BRISBANE_TZ)
            ).total_seconds()
            if remaining > 0:
                closure_retry_delay = min(
                    remaining,
                    max(
                        1.0,
                        remaining
                        * CLOSURE_RETRY_MAX_DELAY_FRACTION,
                    ),
                )
                delay = min(
                    float(delay),
                    closure_retry_delay,
                )
    except Exception as exc:
        log(
            "Could not cap the scheduler delay to market close; "
            f"using the normal delay: {_safe_error(exc)}"
        )

    # Wake on the earliest exact weekend/news deadline even when the normal
    # polling interval is longer. Once a news cutoff is active, failure
    # backoff retains headroom for another attempt before the release.
    try:
        current = _as_brisbane(current)
        upcoming_deadlines = [compute_next_cutoff(settings, current)]
        news_deadline = _next_news_execution_deadline(
            settings, status, current
        )
        if news_deadline is not None:
            upcoming_deadlines.append(news_deadline)
        earliest = min(upcoming_deadlines)
        seconds_until = max(0.0, (earliest - current).total_seconds())
        delay = min(float(delay), seconds_until)

        if retrying_failure:
            active_releases = [
                release_at
                for release_at, cutoff in (
                    _news_event_times(event)
                    for event in (settings.get("news_events") or [])
                    if isinstance(event, dict)
                )
                if cutoff <= current < release_at
            ]
            if active_releases:
                remaining = (
                    min(active_releases) - current
                ).total_seconds()
                retry_headroom = min(
                    remaining,
                    max(
                        1.0,
                        remaining * CLOSURE_RETRY_MAX_DELAY_FRACTION,
                    ),
                )
                delay = min(float(delay), retry_headroom)
    except Exception as exc:
        log(
            "Could not cap the scheduler delay to the next FX/news deadline; "
            f"using the prior delay: {_safe_error(exc)}"
        )
    return max(0.0, float(delay))


def wait_with_heartbeat(delay_seconds: float, reason: str) -> None:
    """Wait without allowing an intentional scheduler delay to look stale."""
    delay = max(0.0, float(delay_seconds))
    started = _now_brisbane()
    sleep_until = started + timedelta(seconds=delay)
    update_status(
        running=True,
        heartbeat_at=started.isoformat(),
        sleeping=delay > 0,
        sleep_reason=str(reason) if delay > 0 else None,
        sleep_started_at=started.isoformat() if delay > 0 else None,
        sleep_until=sleep_until.isoformat() if delay > 0 else None,
        scheduled_delay_seconds=delay,
    )
    remaining = delay
    while remaining > 0:
        interval = min(HEARTBEAT_INTERVAL_SECONDS, remaining)
        settings_changed = _scheduler_wakeup.wait(interval)
        if settings_changed:
            _scheduler_wakeup.clear()
            remaining = 0.0
        else:
            remaining = max(0.0, remaining - interval)
        update_status(
            running=True,
            heartbeat_at=_iso_now(),
            sleeping=remaining > 0,
            sleep_reason=str(reason) if remaining > 0 else None,
            sleep_started_at=(
                started.isoformat() if remaining > 0 else None
            ),
            sleep_until=(
                sleep_until.isoformat() if remaining > 0 else None
            ),
            scheduled_delay_seconds=remaining,
        )


def scheduler_loop() -> None:
    update_status(
        running=True,
        executor_pid=os.getpid(),
        executor_instance_id=(
            str(os.getenv("FXWEEKEND_EXECUTOR_INSTANCE_ID") or "").strip()
            or None
        ),
        executor_started_at=_iso_now(),
        heartbeat_at=_iso_now(),
        sleeping=False,
        sleep_reason=None,
        sleep_started_at=None,
        sleep_until=None,
        scheduled_delay_seconds=0.0,
        state="checking",
        state_detail="Executor scheduler started.",
        selected_accounts=[],
        last_access_check_at=None,
        accounts={},
    )
    while True:
        settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        try:
            settings = load_settings()
            status = scheduler_iteration(settings)
            if not isinstance(status, dict):
                raise RuntimeError(
                    "scheduler iteration returned an invalid status payload"
                )
        except Exception as exc:
            error = _safe_error(exc) or "unexpected scheduler failure"
            log(f"Scheduler iteration failed; retry pending: {error}")
            previous = status_snapshot()
            fallback_status = {
                **previous,
                "state": "retry pending",
                "consecutive_failures": (
                    int(previous.get("consecutive_failures") or 0) + 1
                ),
            }
            try:
                status = update_status(
                    running=True,
                    heartbeat_at=_iso_now(),
                    sleeping=False,
                    sleep_reason=None,
                    sleep_started_at=None,
                    sleep_until=None,
                    scheduled_delay_seconds=0.0,
                    state="retry pending",
                    state_detail=(
                        "Unexpected scheduler failure; the executor will retry."
                    ),
                    last_error=error,
                    consecutive_failures=fallback_status[
                        "consecutive_failures"
                    ],
                )
            except Exception as status_exc:
                log(
                    "Scheduler failure status could not be persisted: "
                    f"{_safe_error(status_exc)}"
                )
                status = fallback_status
        delay = _scheduler_delay_seconds(settings, status)
        wait_with_heartbeat(
            delay,
            (
                f"failure backoff after {status.get('state')}"
                if status.get("state")
                in FINAL_FAILURE_STATES | {"retry pending"}
                else "scheduled check interval"
            ),
        )


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FX Weekend — OANDA</title>
  <style>
    body{font-family:Inter,system-ui,sans-serif;margin:0;background:#0b1220;color:#e2e8f0}
    main{max-width:980px;margin:0 auto;padding:24px}.card{background:#111c30;border:1px solid #334155;border-radius:12px;padding:18px;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.muted{color:#94a3b8}
    label{display:block;margin:10px 0}input,select{background:#0f172a;color:#e2e8f0;border:1px solid #475569;border-radius:6px;padding:7px}
    button{background:#2563eb;color:white;border:0;border-radius:8px;padding:10px 14px;font-weight:700;cursor:pointer}
    button:disabled{cursor:not-allowed;opacity:.55}.action-result{margin-top:12px;padding:10px;border-radius:8px;background:#0f172a}
    .action-result[data-ok="true"]{border:1px solid #16a34a}.action-result[data-ok="false"]{border:1px solid #dc2626}
    table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #334155;padding:8px}
  </style>
</head>
<body><main>
  <h1>FX Weekend <span class="muted">— OANDA only</span></h1>
  <div class="card grid">
    <div><strong>Enabled</strong><br>{{ "Yes" if settings.enabled else "No" }}</div>
    <div><strong>Executor</strong><br>{{ "Running" if status.running else "Stopped" }}</div>
    <div><strong>State</strong><br>{{ status.state }}</div>
    <div><strong>Selected accounts</strong><br>{{ settings.account_modes | join(", ") or "None" }}</div>
    <div><strong>Brisbane cutoff</strong><br>DST {{ settings.cutoff_time_dst }} / Standard {{ settings.cutoff_time_standard }}</div>
    <div><strong>New York clock</strong><br>{{ "DST" if ny_dst else "Standard time" }}</div>
    <div><strong>Next cutoff</strong><br>{{ next_cutoff }}</div>
    <div><strong>Next news release</strong><br>{{ status.next_news_release or "None" }}</div>
    <div><strong>Next news liquidation cutoff</strong><br>{{ status.next_news_liquidation_cutoff or "None" }}</div>
    <div><strong>News status</strong><br>{{ status.news_status or "No news releases scheduled." }}</div>
    <div><strong>Last news result</strong><br>{{ status.news_last_result.get("state") if status.news_last_result else "None" }}</div>
    <div><strong>Heartbeat</strong><br>{{ status.heartbeat_at or "Never" }}</div>
    <div><strong>Last access check</strong><br>{{ status.last_access_check_at or "Never" }}</div>
    <div><strong>Last attempt</strong><br>{{ status.last_attempt_at or "Never" }}</div>
    <div><strong>Last verified flat</strong><br>{{ status.last_verified_flat_at or "Never" }}</div>
    <div><strong>Last error</strong><br>{{ status.last_error or "None" }}</div>
  </div>
  <div class="card">
    <h2>Account status</h2>
    <table><thead><tr><th>Mode</th><th>State</th><th>Open</th><th>Positions</th><th>Trades</th><th>Error</th></tr></thead>
    <tbody>
    {% for mode in settings.account_modes %}
      {% set item = status.accounts.get(mode, {}) %}
      <tr><td>{{ mode | upper }}</td><td>{{ item.get("state", "checking") }}</td><td>{{ item.get("open_count", "—") }}</td><td>{{ item.get("position_count", "—") }}</td><td>{{ item.get("trade_count", "—") }}</td><td>{{ item.get("last_error") or "" }}</td></tr>
    {% endfor %}
    </tbody></table>
  </div>
  <div class="card">
    <h2>News</h2>
    <p class="muted">Release dates and times are entered and interpreted only in <strong>Australia/Brisbane</strong> time. FX Weekend closes and verifies the full selected OANDA account scope 15 minutes before each release. It does not close Pepperstone/MT5 positions.</p>
    <p class="muted">Trading-plan reminder: highest-risk releases require a 60-minute no-new-trade window and other high-impact releases require 30 minutes. This date/time-only liquidation service does not guess a category or enforce either entry blackout. After release, wait at least 15 minutes and require the initial move, near-normal spread, stable liquidity, new impulse/pullback and structure, at least 2R, and no imminent related event.</p>
    <form id="fxweekend-news-form" method="post" action="api/news">
      <div class="grid">
        <label>Release date (Brisbane) <input type="date" name="release_date" required></label>
        <label>Release time (Brisbane) <input type="time" name="release_time" required></label>
      </div>
      <button type="submit">Add news release</button>
      <div id="fxweekend-news-result" class="action-result" role="status" aria-live="polite" hidden></div>
    </form>
    <table><thead><tr><th>Release date</th><th>Release time</th><th>Liquidation cutoff</th><th>Status</th><th>Action</th></tr></thead>
    <tbody>
    {% for event in settings.news_events %}
      {% set event_status = status.news_audit.get(event.id, {}) %}
      <tr>
        <td>{{ event.release_date }}</td>
        <td>{{ event.release_time }} Brisbane</td>
        <td>{{ event.liquidation_cutoff }}</td>
        <td>{{ event_status.get("state", event.schedule_state) }}</td>
        <td><button type="button" data-news-delete-url="api/news/{{ event.id }}">Delete</button></td>
      </tr>
    {% else %}
      <tr><td colspan="5">No news releases scheduled.</td></tr>
    {% endfor %}
    </tbody></table>
    <p class="muted">Deleting a release prevents future execution; its durable audit result remains in status history.</p>
    {% if status.news_audit %}
    <h3>News audit history</h3>
    <table><thead><tr><th>Event ID</th><th>Release</th><th>Cutoff</th><th>Attempt</th><th>Verified flat</th><th>Cutoff met</th><th>State</th></tr></thead>
    <tbody>
    {% for event_id, audit in status.news_audit | dictsort %}
      <tr>
        <td>{{ event_id }}</td>
        <td>{{ audit.get("release_at") or "—" }}</td>
        <td>{{ audit.get("liquidation_cutoff") or "—" }}</td>
        <td>{{ audit.get("attempt_at") or "Never" }}</td>
        <td>{{ audit.get("verified_flat_at") or "No" }}</td>
        <td>{{ "Yes" if audit.get("cutoff_met") is sameas true else "No" if audit.get("cutoff_met") is sameas false else "Pending" }}</td>
        <td>{{ audit.get("state") or "unknown" }}{% if audit.get("deleted_at") %} (deleted {{ audit.get("deleted_at") }}){% endif %}</td>
      </tr>
    {% endfor %}
    </tbody></table>
    {% endif %}
  </div>
  <form class="card" method="post" action="api/config">
    <h2>Durable schedule settings</h2>
    <label><input type="checkbox" name="enabled" {% if settings.enabled %}checked{% endif %}> Enabled</label>
    <label>OANDA accounts:
      <input type="checkbox" name="account_modes" value="demo" {% if "demo" in settings.account_modes %}checked{% endif %}> Demo
      <input type="checkbox" name="account_modes" value="live" {% if "live" in settings.account_modes %}checked{% endif %}> Live
    </label>
    <div class="grid">
      <label>Brisbane cutoff while New York is on DST <input type="time" name="cutoff_time_dst" value="{{ settings.cutoff_time_dst }}"></label>
      <label>Brisbane cutoff during New York standard time <input type="time" name="cutoff_time_standard" value="{{ settings.cutoff_time_standard }}"></label>
      <label>Check interval (seconds) <input type="number" min="5" name="check_interval_seconds" value="{{ settings.check_interval_seconds }}"></label>
      <label>Maximum retry backoff (seconds) <input type="number" min="5" name="max_retry_backoff_seconds" value="{{ settings.max_retry_backoff_seconds }}"></label>
      <label>Close scope <select name="close_method"><option value="positions" {% if settings.close_method == "positions" %}selected{% endif %}>Positions</option><option value="trades" {% if settings.close_method == "trades" %}selected{% endif %}>Trades</option></select></label>
    </div>
    <label>Instrument allowlist (blank means all Forex instruments) <input type="text" name="instrument_allowlist" value="{{ settings.instrument_allowlist | join(', ') }}"></label>
    <label><input type="checkbox" name="dry_run" {% if settings.dry_run %}checked{% endif %}> Dry run</label>
    <button type="submit">Save settings</button>
  </form>
  <form id="fxweekend-run-liquidation-form" class="card" method="post" action="api/run_now">
    <h2>Manual liquidation</h2>
    <p class="muted"><strong>Run liquidation now is not a start button.</strong> It immediately checks and liquidates every selected account. It does not start, enable, or activate the continuously owned scheduler. Live manual liquidation remains blocked outside the configured tradable closure window, and manual liquidation is blocked while the executor is disabled.</p>
    <button type="submit" {% if not settings.enabled %}disabled{% endif %}>Run liquidation now</button>
    <div id="fxweekend-liquidation-result" class="action-result" role="status" aria-live="polite" hidden></div>
  </form>
</main>
<script>
(() => {
  const form = document.getElementById("fxweekend-news-form");
  const output = document.getElementById("fxweekend-news-result");
  if (form && output) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      output.hidden = false;
      output.dataset.ok = "";
      output.textContent = "Saving news release…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {"Accept": "application/json"},
          body: new FormData(form),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `Save failed with HTTP ${response.status}.`);
        }
        output.dataset.ok = "true";
        output.textContent = payload.message || "News release saved.";
        window.location.reload();
      } catch (error) {
        output.dataset.ok = "false";
        output.textContent = error && error.message ? error.message : "News release could not be saved.";
      }
    });
  }
  document.querySelectorAll("[data-news-delete-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.newsDeleteUrl, {
          method: "DELETE",
          headers: {"Accept": "application/json"},
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `Delete failed with HTTP ${response.status}.`);
        }
        window.location.reload();
      } catch (error) {
        if (output) {
          output.hidden = false;
          output.dataset.ok = "false";
          output.textContent = error && error.message ? error.message : "News release could not be deleted.";
        }
        button.disabled = false;
      }
    });
  });
})();
(() => {
  const form = document.getElementById("fxweekend-run-liquidation-form");
  const output = document.getElementById("fxweekend-liquidation-result");
  if (!form || !output) return;
  const button = form.querySelector('button[type="submit"]');
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button) button.disabled = true;
    output.hidden = false;
    output.dataset.ok = "";
    output.textContent = "Liquidation request in progress…";
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: {"Accept": "application/json"},
      });
      const payload = await response.json().catch(() => ({}));
      const accountStates = Object.entries(payload.accounts || {})
        .map(([mode, item]) => `${mode.toUpperCase()}: ${item.state || "unknown"}`)
        .join("; ");
      const message = payload.error || payload.result || payload.state
        || `Request finished with HTTP ${response.status}.`;
      output.dataset.ok = String(Boolean(response.ok && payload.ok));
      output.textContent = accountStates ? `${message} ${accountStates}` : message;
    } catch (_error) {
      output.dataset.ok = "false";
      output.textContent = "The liquidation request could not be completed. The scheduler remains independently managed by Render.";
    } finally {
      if (button) button.disabled = false;
    }
  });
})();
</script>
</body></html>
"""


def _news_page_settings(
    settings: Dict[str, Any],
    status: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = _as_brisbane(now or _now_brisbane())
    payload = deepcopy(settings)
    audit = _news_audit(status)
    page_events: List[Dict[str, Any]] = []
    for raw in payload.get("news_events") or []:
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        release_at, cutoff = _news_event_times(event)
        if current < cutoff:
            schedule_state = "scheduled"
        elif current < release_at:
            schedule_state = "liquidation due"
        else:
            schedule_state = "release passed; cutoff missed"
        event["liquidation_cutoff"] = cutoff.isoformat()
        event["schedule_state"] = str(
            (audit.get(str(event.get("id"))) or {}).get("state")
            or schedule_state
        )
        page_events.append(event)
    payload["news_events"] = page_events
    return payload


def _news_due_state(event: Dict[str, Any], now: datetime) -> str:
    release_at, cutoff = _news_event_times(event)
    current = _as_brisbane(now)
    if current < cutoff:
        return "scheduled"
    if current < release_at:
        return "cutoff is already due; the scheduler will liquidate safely"
    return (
        "release and cutoff have passed; the scheduler will record the missed "
        "cutoff and make one immediate safe-close verification"
    )


@app.get("/")
def index() -> str:
    settings = load_settings()
    current = _now_brisbane()
    status_payload = status_snapshot()
    status_payload.update(
        _news_schedule_fields(settings, status_payload, current)
    )
    return render_template_string(
        PAGE_TEMPLATE,
        settings=_news_page_settings(settings, status_payload, current),
        status=status_payload,
        next_cutoff=compute_next_trigger(settings, current),
        ny_dst=is_us_dst(),
    )


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return load_settings()


@app.post("/api/config")
def update_config() -> Any:
    settings = load_settings()
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        if "news_events" in payload:
            return {
                "ok": False,
                "error": "Use /api/news to add or delete date/time-only releases.",
            }, 400
        mutable_fields = {
            "enabled",
            "trigger_weekday",
            "cutoff_time_dst",
            "cutoff_time_standard",
            "account_modes",
            "check_interval_seconds",
            "max_retry_backoff_seconds",
            "close_method",
            "dry_run",
            "instrument_allowlist",
        }
        settings.update(
            {
                key: value
                for key, value in payload.items()
                if key in mutable_fields
            }
        )
    else:
        form = request.form
        settings["enabled"] = "enabled" in form
        settings["account_modes"] = form.getlist("account_modes")
        settings["cutoff_time_dst"] = form.get(
            "cutoff_time_dst", settings["cutoff_time_dst"]
        )
        settings["cutoff_time_standard"] = form.get(
            "cutoff_time_standard", settings["cutoff_time_standard"]
        )
        settings["check_interval_seconds"] = form.get(
            "check_interval_seconds", settings["check_interval_seconds"]
        )
        settings["max_retry_backoff_seconds"] = form.get(
            "max_retry_backoff_seconds", settings["max_retry_backoff_seconds"]
        )
        settings["close_method"] = form.get("close_method", settings["close_method"])
        settings["dry_run"] = "dry_run" in form
        settings["instrument_allowlist"] = [
            item.strip()
            for item in form.get("instrument_allowlist", "").split(",")
            if item.strip()
        ]
    settings = save_settings(settings)
    update_status(
        selected_accounts=settings["account_modes"],
        last_verified_window_cutoff=None,
        last_verified_window_scope_fingerprint=None,
        last_verified_window_account_times={},
        last_verified_window_account_scope_hashes={},
    )
    log("Durable settings updated through the Render-owned page.")
    _scheduler_wakeup.set()
    return {"ok": True, "settings": settings}


@app.post("/api/news")
def add_news_event() -> Any:
    with _news_schedule_lock:
        return _add_news_event_locked()


def _add_news_event_locked() -> Any:
    settings = load_settings()
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload if isinstance(payload, dict) or hasattr(payload, "get") else {}
    try:
        release_at = parse_news_release(
            payload.get("release_date"), payload.get("release_time")
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    event = {
        "id": _new_news_event_id(),
        "release_date": release_at.strftime("%Y-%m-%d"),
        "release_time": release_at.strftime("%H:%M"),
        "release_at": release_at.isoformat(),
    }
    existing = next(
        (
            item
            for item in (settings.get("news_events") or [])
            if isinstance(item, dict)
            and str(item.get("release_at") or "") == event["release_at"]
        ),
        None,
    )
    duplicate = existing is not None
    if not duplicate:
        settings["news_events"] = [
            *(settings.get("news_events") or []),
            event,
        ]
        settings = save_settings(settings)
    else:
        event = dict(existing)
    current = _now_brisbane()
    status_payload = status_snapshot()
    schedule_fields = _news_schedule_fields(
        settings, status_payload, current
    )
    update_status(**schedule_fields)
    state = _news_due_state(event, current)
    message = (
        f"That Brisbane release is already scheduled ({state})."
        if duplicate
        else f"News release saved ({state}). Saving did not itself liquidate an account."
    )
    log(
        "Durable Brisbane news release retained without immediate route-side "
        "execution; the scheduler owns any due action."
    )
    _scheduler_wakeup.set()
    return {
        "ok": True,
        "duplicate": duplicate,
        "event": event,
        "state": state,
        "message": message,
    }


@app.delete("/api/news/<event_id>")
def delete_news_event(event_id: str) -> Any:
    with _news_schedule_lock:
        return _delete_news_event_locked(event_id)


def _delete_news_event_locked(event_id: str) -> Any:
    settings = load_settings()
    existing = next(
        (
            item
            for item in (settings.get("news_events") or [])
            if isinstance(item, dict)
            and str(item.get("id") or "") == str(event_id)
        ),
        None,
    )
    if existing is None:
        return {"ok": False, "error": "News release was not found."}, 404

    # Persist the audit tombstone first. If a process stops between the two
    # writes, the scheduler still treats the event as deleted and cannot run it.
    status_payload = status_snapshot()
    audit = _news_audit(status_payload)
    prior = audit.get(str(event_id)) or {}
    audit[str(event_id)] = {
        **prior,
        "event_id": str(event_id),
        "release_at": existing.get("release_at"),
        "liquidation_cutoff": _news_event_times(existing)[1].isoformat(),
        "state": prior.get("state") or "deleted before execution",
        "deleted_at": _iso_now(),
    }
    update_status(news_audit=audit)
    settings["news_events"] = [
        item
        for item in (settings.get("news_events") or [])
        if not isinstance(item, dict)
        or str(item.get("id") or "") != str(event_id)
    ]
    settings = save_settings(settings)
    schedule_fields = _news_schedule_fields(
        settings, status_snapshot(), _now_brisbane()
    )
    update_status(**schedule_fields)
    log("A durable news release was deleted; its audit tombstone was retained.")
    _scheduler_wakeup.set()
    return {
        "ok": True,
        "deleted_event_id": str(event_id),
        "audit_retained": True,
    }


@app.post("/api/run_now")
def run_now() -> Any:
    settings = load_settings()
    if not settings.get("enabled"):
        payload = update_status(
            state="disabled",
            state_detail="Manual execution is blocked while FX Weekend is disabled.",
            selected_accounts=_ordered_account_modes(settings),
            last_error=None,
        )
        return {
            "ok": False,
            "state": "disabled",
            "result": "disabled",
            "error": None,
            "accounts": payload.get("accounts") or {},
            "verified_flat": False,
        }
    selected = _ordered_account_modes(settings)
    window = closure_window(settings)
    if "live" in selected and window.get("phase") != "closure":
        message = (
            "Manual Live closure is blocked outside the configured tradable "
            "closure window."
        )
        # A rejected manual request is not an executor failure and must not
        # overwrite scheduler state, heartbeat, or retry counters.
        payload = status_snapshot()
        log(
            "Manual Live liquidation request blocked outside the configured "
            "tradable closure window; scheduler state was left unchanged."
        )
        return (
            {
                "ok": False,
                "state": str(
                    payload.get("state")
                    or window.get("phase")
                    or "before cutoff"
                ),
                "result": "live manual closure blocked",
                "error": message,
                "accounts": payload.get("accounts") or {},
                "verified_flat": False,
            },
            409,
        )

    def report_progress(
        mode: str, accounts: Dict[str, Dict[str, Any]]
    ) -> None:
        update_status(
            running=True,
            heartbeat_at=_iso_now(),
            state="closing",
            state_detail=f"Closing open OANDA scope for {mode.upper()}.",
            selected_accounts=selected,
            accounts=accounts,
        )

    result = run_liquidation(
        settings,
        "manual",
        can_close=True,
        close_deadline=(
            window.get("market_close")
            if "live" in selected
            else None
        ),
        lock_timeout=0.0,
        progress_callback=report_progress,
    )
    if result.get("state") == "liquidation already in progress":
        return {"ok": False, **result}, 409
    _apply_attempt_status(result)
    return {"ok": bool(result.get("verified_flat")), **result}


@app.get("/api/status")
def status() -> Dict[str, Any]:
    settings = load_settings()
    payload = status_snapshot()
    current = _now_brisbane()
    payload.update(_news_schedule_fields(settings, payload, current))
    return {
        **payload,
        "enabled": settings.get("enabled", False),
        "selected_accounts": _ordered_account_modes(settings),
        "cutoff_time_dst": settings.get("cutoff_time_dst"),
        "cutoff_time_standard": settings.get("cutoff_time_standard"),
        "next_cutoff": compute_next_trigger(settings, current),
        "new_york_time_mode": "DST" if is_us_dst() else "standard",
    }


@app.get("/api/self_test")
def self_test() -> Dict[str, Any]:
    settings = load_settings()
    results = run_read_only_account_check(
        settings, "self-test read-only account check"
    ).get("accounts") or {}
    return {
        "ok": bool(results)
        and all(
            item.get("state") not in {"credential failure", "API failure"}
            for item in results.values()
        ),
        "accounts": results,
    }


def run_web() -> None:
    bootstrap_log()
    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, use_reloader=False)


def run_cli() -> None:
    bootstrap_log()
    scheduler_loop()


if __name__ == "__main__":
    if os.getenv("PORT"):
        run_web()
    else:
        run_cli()
