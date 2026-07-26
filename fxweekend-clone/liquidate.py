from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
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

app = Flask(__name__)
_status_lock = threading.RLock()


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
        "executor_started_at": None,
        "heartbeat_at": None,
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


def _close_requested_scope(
    config: Dict[str, str],
    opened: Dict[str, Any],
    settings: Dict[str, Any],
    *,
    can_close: bool,
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

    if mode == "trades":
        trades = _scoped_items(opened.get("trades") or [], allowlist)
        for trade in trades:
            trade_id = str(trade.get("id") or "")
            instrument = str(trade.get("instrument") or "unknown")
            item = {"scope": "trade", "trade_id": trade_id, "instrument": instrument}
            if not can_close:
                item.update({"ok": False, "error": "market closing window has ended"})
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
        if not can_close:
            item.update({"ok": False, "error": "market closing window has ended"})
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


def run_liquidation(
    settings: Dict[str, Any],
    reason: str,
    *,
    can_close: bool = True,
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
    current = now or _now_brisbane()
    selected = _ordered_account_modes(settings)
    heartbeat = current.astimezone(BRISBANE_TZ).isoformat()
    if not settings.get("enabled"):
        return update_status(
            running=True,
            heartbeat_at=heartbeat,
            state="disabled",
            state_detail="FX Weekend execution is disabled by durable settings.",
            selected_accounts=selected,
            last_error=None,
        )
    window = closure_window(settings, current)
    if window["phase"] == "before cutoff":
        check = run_read_only_account_check(settings, "scheduled before-cutoff check")
        previous = status_snapshot()
        failed = check.get("state") in {"credential failure", "API failure"}
        return update_status(
            running=True,
            heartbeat_at=heartbeat,
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
        )
    if window["phase"] == "closure":
        update_status(
            running=True,
            heartbeat_at=heartbeat,
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
        return status_snapshot()
    previous = status_snapshot()
    covered_during_closure = _window_coverage_is_current(
        settings, window, previous
    )
    update_status(
        running=True,
        heartbeat_at=heartbeat,
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
    return status_snapshot()


def scheduler_loop() -> None:
    update_status(
        running=True,
        executor_started_at=_iso_now(),
        heartbeat_at=_iso_now(),
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
        if status.get("state") in FINAL_FAILURE_STATES | {"retry pending"}:
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
        time.sleep(max(5, delay))


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
  <form class="card" method="post" action="api/run_now"><p class="muted">Run now acts on every selected account. Live manual execution is permitted only during the configured tradable closure window; the scheduler owns Live execution at all other times. It is blocked while the executor is disabled.</p><button type="submit" {% if not settings.enabled %}disabled{% endif %}>Run now</button></form>
</main></body></html>
"""


@app.get("/")
def index() -> str:
    settings = load_settings()
    return render_template_string(
        PAGE_TEMPLATE,
        settings=settings,
        status=status_snapshot(),
        next_cutoff=compute_next_trigger(settings),
        ny_dst=is_us_dst(),
    )


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return load_settings()


@app.post("/api/config")
def update_config() -> Dict[str, Any]:
    settings = load_settings()
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        settings.update(payload)
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
    return {"ok": True, "settings": settings}


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
        payload = update_status(
            state=str(window.get("phase") or "before cutoff"),
            state_detail=message,
            selected_accounts=selected,
            last_error=message,
        )
        return (
            {
                "ok": False,
                "state": payload.get("state"),
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
        progress_callback=report_progress,
    )
    _apply_attempt_status(result)
    return {"ok": bool(result.get("verified_flat")), **result}


@app.get("/api/status")
def status() -> Dict[str, Any]:
    settings = load_settings()
    payload = status_snapshot()
    return {
        **payload,
        "enabled": settings.get("enabled", False),
        "selected_accounts": _ordered_account_modes(settings),
        "cutoff_time_dst": settings.get("cutoff_time_dst"),
        "cutoff_time_standard": settings.get("cutoff_time_standard"),
        "next_cutoff": compute_next_trigger(settings),
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
