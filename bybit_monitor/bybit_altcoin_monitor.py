"""Simple Bybit perpetual futures price monitor.

This script fetches linear perpetual futures prices (including BTC and ETH)
public API. It watches for price jumps of at least +/-5% compared to the
previous fetch and notifies the user when that happens. The script is meant to
run continuously until the user stops it manually.
"""
from __future__ import annotations

import datetime as _dt
import hmac
import json
import os
import socket
import sys
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from hashlib import sha256
from typing import Dict, Iterable, Tuple
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bybit_credentials import resolve_bybit_credentials
from shared.atomic_json import write_json_file
from shared.env_bootstrap import format_env_bootstrap_log, load_master_env
from shared.symbol_resolution import norm_symbol, resolve_bybit_symbol_from_choices

# Credential + endpoint resolution -------------------------------------------------
_ENV_BOOTSTRAP_INFO = load_master_env()


def get_bybit_creds() -> Tuple[str, str, str, str, str]:
    """Resolve Bybit credentials and base URL using existing Render env vars."""

    mode, key, secret, base_url, key_source = resolve_bybit_credentials()
    return mode, key, secret, base_url, key_source


API_FALLBACK_BASE = os.getenv("BYBIT_API_FALLBACK_BASE") or "https://api.bytick.com"
API_BASES = [
    base.strip()
    for base in os.getenv("BYBIT_API_BASES", "").split(",")
    if base.strip()
]
API_PATH = "/v5/market/tickers"
KLINE_PATH = "/v5/market/kline"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
DEFAULT_WAIT_SECONDS = int(os.getenv("BYBIT_WAIT_SECONDS", "300"))
ERROR_WAIT_SECONDS = 60
BLOCK_BACKOFFS = [60, 120, 300, 900, 1800]  # 1m, 2m, 5m, 15m, 30m
DEFAULT_PERCENT_THRESHOLD = float(os.getenv("BYBIT_PERCENT_THRESHOLD", "5.0"))
DEFAULT_ATH_ATL_ENABLED = int(os.getenv("BYBIT_ATH_ATL_ENABLED", "1"))
DEFAULT_ATH_ATL_MIN_BREAK_PCT = float(os.getenv("BYBIT_ATH_ATL_MIN_BREAK_PCT", "0.0"))
DEFAULT_ATH_ATL_COOLDOWN_SECONDS = int(os.getenv("BYBIT_ATH_ATL_COOLDOWN_SECONDS", "3600"))
DEFAULT_ATH_ATL_GRANULARITY = os.getenv("BYBIT_ATH_ATL_GRANULARITY", "D")
DEFAULT_ATH_ATL_BACKFILL_BATCH = int(os.getenv("BYBIT_ATH_ATL_BACKFILL_BATCH", "3"))
DEFAULT_ATH_ATL_BACKFILL_MAX_PAGES = int(os.getenv("BYBIT_ATH_ATL_BACKFILL_MAX_PAGES", "10"))
STABLECOIN_SUFFIXES = ("USDT", "USDC", "USDD", "USD")
SETTINGS_PATH = Path(__file__).with_name("settings.json")
STATE_PATH = Path(__file__).with_name("state.json")
CUSTOM_ALERTS_PATH = Path(__file__).with_name("custom_alerts.json")
RUNTIME_STATUS_PATH = Path(__file__).with_name("runtime_status.json")

_session: requests.Session | None = None
_target_logged = False
_logged_classifications: set[str] = set()
_auth_notice_logged = False
_settings_cache: Dict[str, float] | None = None
_settings_mtime: float | None = None
_push_warning_given = False
_push_success_logged = False
_push_failure_logged = False
_push_config_logged = False
_alerts_cache: list[dict] | None = None
_alerts_mtime: float | None = None
_perp_symbols_cache: set[str] | None = None
_perp_symbols_cache_at: float = 0.0
_PERP_SYMBOLS_TTL_SECONDS = 900
_traffic_totals = {"requests": 0, "bytes_sent": 0, "bytes_received": 0}
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Australia/Brisbane").strip() or "Australia/Brisbane"

SERVER_GUARD_ENV_VARS = {
    "RENDER",
    "RENDER_SERVICE_ID",
    "RENDER_EXTERNAL_URL",
    "RENDER_EXTERNAL_HOSTNAME",
}


def ensure_local_only_execution() -> None:
    """Refuse to run in Render/server environments."""

    present = sorted(name for name in SERVER_GUARD_ENV_VARS if os.getenv(name))
    if present:
        joined = ", ".join(present)
        raise SystemExit(
            "Scanner is local-only and cannot run on Render/server env; "
            f"detected environment variable(s): {joined}. "
            "Run run_scanner_local.bat on your PC."
        )



def _app_now() -> _dt.datetime:
    try:
        return _dt.datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return _dt.datetime.now(ZoneInfo("Australia/Brisbane"))


def _track_traffic(label: str, *, bytes_sent: int = 0, bytes_received: int = 0) -> None:
    _traffic_totals["requests"] += 1
    _traffic_totals["bytes_sent"] += max(0, int(bytes_sent))
    _traffic_totals["bytes_received"] += max(0, int(bytes_received))
    log(
        f"Outbound traffic [{label}] req={_traffic_totals['requests']} "
        f"tx={_traffic_totals['bytes_sent']}B rx={_traffic_totals['bytes_received']}B"
    )

def _get_telegram_credentials() -> tuple[str, str]:
    """Return Telegram credentials from environment variables."""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or ""
    return token, chat_id

_ALLOWED_ALERT_KINDS = {"price", "move"}
_ALLOWED_PRICE_DIRECTIONS = {"above", "below"}
_ALLOWED_MOVE_DIRECTIONS = {"up", "down", "either"}
_ALLOWED_MOVE_UNITS = {"pct", "abs"}
_runtime_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _write_runtime_status(**extra: object) -> None:
    payload = {
        "running": False,
        "pid": os.getpid(),
        "started_at": _runtime_started_at,
        "last_heartbeat_at": _utc_now_iso(),
        "phase": "stopped",
        "wait_seconds": 0,
        "last_error": "",
        "last_exit_reason": "",
        "heartbeat_timeout_seconds": 120,
    }
    payload.update(extra)
    write_json_file(
        RUNTIME_STATUS_PATH,
        payload,
        best_effort=True,
        retries=20,
        backoff=0.05,
        direct_fallback=True,
    )


def _heartbeat(*, phase: str, wait_seconds: int = 0, last_error: str = "") -> None:
    _write_runtime_status(
        running=True,
        phase=phase,
        wait_seconds=max(0, int(wait_seconds)),
        last_error=(last_error or "")[:500],
        heartbeat_timeout_seconds=max(60, int(wait_seconds) * 2 + 30),
    )


def _load_custom_alerts() -> list[dict]:
    try:
        raw = json.loads(CUSTOM_ALERTS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [alert for alert in raw if isinstance(alert, dict)]
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return []


def _save_custom_alerts(alerts: list[dict]) -> None:
    tmp = CUSTOM_ALERTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(alerts, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CUSTOM_ALERTS_PATH)


def get_custom_alerts(force: bool = False) -> list[dict]:
    global _alerts_cache, _alerts_mtime
    try:
        mtime = CUSTOM_ALERTS_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    if force or _alerts_cache is None or mtime != _alerts_mtime:
        _alerts_cache = _load_custom_alerts()
        _alerts_mtime = mtime
    return list(_alerts_cache or [])


def _coerce_alert(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Alert payload must be an object.")
    alert_id = str(payload.get("id") or "").strip() or uuid.uuid4().hex
    raw_symbol = str(payload.get("symbol") or "").strip()
    symbol = norm_symbol(raw_symbol)
    if not symbol:
        raise ValueError("symbol is required")

    allowed = _get_linear_perpetual_symbols()
    resolved = resolve_bybit_symbol_from_choices(
        symbol,
        allowed,
        preferred_quotes=("USDT", "USDC", "USD"),
        exact_first=True,
    )
    resolved_symbol = str((resolved or {}).get("resolved_symbol") or "").upper()
    if not resolved_symbol or resolved_symbol not in allowed:
        raise ValueError(f"Unable to resolve '{raw_symbol}' to a Bybit linear perpetual symbol.")

    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _ALLOWED_ALERT_KINDS:
        raise ValueError("kind must be one of: price, move")
    message = str(payload.get("message") or "").strip()

    enabled = bool(payload.get("enabled", True))
    cooldown_seconds = int(float(payload.get("cooldown_seconds", 0) or 0))
    if cooldown_seconds < 0:
        cooldown_seconds = 0

    alert: dict = {
        "id": alert_id,
        "symbol": resolved_symbol,
        "kind": kind,
        "enabled": enabled,
        "cooldown_seconds": cooldown_seconds,
    }

    if kind == "price":
        direction = str(payload.get("direction") or "").strip().lower()
        if direction not in _ALLOWED_PRICE_DIRECTIONS:
            raise ValueError("direction must be above or below for price alerts")
        try:
            target_price = float(payload.get("target_price"))
        except Exception as exc:
            raise ValueError("target_price must be a number") from exc
        if target_price <= 0:
            raise ValueError("target_price must be > 0")
        alert.update({"direction": direction, "target_price": target_price})
        if message:
            alert["message"] = message[:500]
        return alert

    direction = str(payload.get("direction") or "").strip().lower()
    if direction not in _ALLOWED_MOVE_DIRECTIONS:
        raise ValueError("direction must be up, down, or either for move alerts")
    unit = str(payload.get("unit") or "pct").strip().lower()
    if unit not in _ALLOWED_MOVE_UNITS:
        raise ValueError("unit must be pct or abs")
    try:
        threshold = float(payload.get("threshold"))
    except Exception as exc:
        raise ValueError("threshold must be a number") from exc
    if threshold <= 0:
        raise ValueError("threshold must be > 0")
    window_seconds = int(float(payload.get("window_seconds", 0) or 0))
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    alert.update(
        {
            "direction": direction,
            "unit": unit,
            "threshold": threshold,
            "window_seconds": window_seconds,
        }
    )
    return alert


def upsert_custom_alert(payload: dict) -> dict:
    alerts = get_custom_alerts(force=True)
    alert = _coerce_alert(payload)
    for idx, existing in enumerate(alerts):
        if str(existing.get("id")) == alert["id"]:
            alerts[idx] = alert
            _save_custom_alerts(alerts)
            get_custom_alerts(force=True)
            return alert
    alerts.append(alert)
    _save_custom_alerts(alerts)
    get_custom_alerts(force=True)
    return alert


def delete_custom_alert(alert_id: str) -> None:
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        raise ValueError("alert_id is required")
    alerts = get_custom_alerts(force=True)
    alerts = [alert for alert in alerts if str(alert.get("id")) != alert_id]
    _save_custom_alerts(alerts)
    get_custom_alerts(force=True)


def set_custom_alert_enabled(alert_id: str, enabled: bool) -> dict:
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        raise ValueError("alert_id is required")
    alerts = get_custom_alerts(force=True)
    for alert in alerts:
        if str(alert.get("id")) == alert_id:
            alert["enabled"] = bool(enabled)
            _save_custom_alerts(alerts)
            get_custom_alerts(force=True)
            return dict(alert)
    raise ValueError("Unknown alert id")


def replace_custom_alerts(alerts_payload: object, *, strict: bool = True) -> list[dict]:
    """Replace ALL custom alerts with the provided list (used for backup/restore)."""
    if not isinstance(alerts_payload, list):
        raise ValueError("alerts must be a list")
    replaced: list[dict] = []
    skipped: list[dict] = []
    for item in alerts_payload:
        if not isinstance(item, dict):
            continue
        try:
            replaced.append(_coerce_alert(item))
        except ValueError:
            if strict:
                raise
            skipped.append(item)
    _save_custom_alerts(replaced)
    get_custom_alerts(force=True)
    if skipped:
        log(
            "Skipped invalid restored Bybit alerts: "
            f"removed={len(skipped)} kept={len(replaced)}"
        )
    return list(replaced)


def _evaluate_move_condition(
    unit: str,
    direction: str,
    threshold: float,
    current: float,
    window_min: float,
    window_max: float,
) -> tuple[bool, str, float, float]:
    if direction in ("up", "either"):
        ref = window_min
        if ref > 0:
            measured = ((current - ref) / ref) * 100.0 if unit == "pct" else (current - ref)
            if measured >= threshold:
                return True, "up", measured, ref
    if direction in ("down", "either"):
        ref = window_max
        if ref > 0:
            measured = ((ref - current) / ref) * 100.0 if unit == "pct" else (ref - current)
            if measured >= threshold:
                return True, "down", measured, ref
    return False, direction, 0.0, current


def evaluate_custom_alerts(
    alerts: list[dict],
    prices: Dict[str, float],
    state: Dict[str, object],
    price_history: Dict[str, deque],
) -> bool:
    now = time.time()
    alert_state = state.get("custom_alerts")
    if not isinstance(alert_state, dict):
        alert_state = {}
        state["custom_alerts"] = alert_state

    enabled_alerts = [alert for alert in alerts if alert.get("enabled", True)]
    max_window = 0
    for alert in enabled_alerts:
        if alert.get("kind") == "move":
            max_window = max(max_window, int(alert.get("window_seconds", 0) or 0))

    if max_window > 0:
        cutoff = now - max_window
        for symbol, price in prices.items():
            dq = price_history.get(symbol)
            if dq is None:
                dq = deque()
                price_history[symbol] = dq
            dq.append((now, float(price)))
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    changed = False
    fired_price_alert_ids: set[str] = set()
    for alert in enabled_alerts:
        symbol = alert.get("symbol")
        if not symbol or symbol not in prices:
            continue
        current = float(prices[symbol])
        alert_id = str(alert.get("id"))
        st = alert_state.get(alert_id)
        if not isinstance(st, dict):
            st = {}
            alert_state[alert_id] = st
            changed = True

        armed = bool(st.get("armed", True))
        last_trigger_at = float(st.get("last_trigger_at", 0) or 0)
        cooldown = int(alert.get("cooldown_seconds", 0) or 0)
        can_trigger = cooldown <= 0 or (now - last_trigger_at) >= cooldown

        if alert.get("kind") == "price":
            direction = str(alert.get("direction"))
            target = float(alert.get("target_price"))
            condition_met = current >= target if direction == "above" else current <= target
            if condition_met and armed and can_trigger:
                st["armed"] = False
                st["last_trigger_at"] = now
                fired_price_alert_ids.add(alert_id)
                changed = True
                msg = f"{symbol} PRICE {direction.upper()} {target} | now {current}"
                custom_msg = str(alert.get("message") or "").strip()
                notify_msg = f"{msg}\nNote: {custom_msg}" if custom_msg else msg
                log(msg if not custom_msg else f"{msg} | note={custom_msg}")
                send_notification("BYBIT Custom Price Alert", notify_msg)
            elif not condition_met and not armed:
                st["armed"] = True
                changed = True
            continue

        window_s = int(alert.get("window_seconds"))
        dq = price_history.get(symbol)
        if not dq:
            continue
        cutoff = now - window_s
        window_prices = [price for (ts, price) in dq if ts >= cutoff]
        if len(window_prices) < 2:
            continue
        window_min = min(window_prices)
        window_max = max(window_prices)
        unit = str(alert.get("unit") or "pct")
        direction = str(alert.get("direction") or "either")
        threshold = float(alert.get("threshold"))

        triggered, resolved_dir, measured, ref = _evaluate_move_condition(
            unit, direction, threshold, current, window_min, window_max
        )

        if triggered and armed and can_trigger:
            st["armed"] = False
            st["last_trigger_at"] = now
            changed = True
            unit_label = "%" if unit == "pct" else ""
            msg = (
                f"{symbol} MOVE {resolved_dir.upper()} {measured:.4f}{unit_label} in {window_s}s "
                f"| ref {ref:.8f} -> now {current:.8f}"
            )
            log(msg)
            send_notification("BYBIT Custom Move Alert", msg)
        elif not triggered and not armed:
            st["armed"] = True
            changed = True

    if fired_price_alert_ids:
        original_count = len(alerts)
        alerts[:] = [
            alert
            for alert in alerts
            if str(alert.get("id")) not in fired_price_alert_ids or alert.get("kind") != "price"
        ]
        if len(alerts) != original_count:
            for fired_id in fired_price_alert_ids:
                alert_state.pop(fired_id, None)
            changed = True
            log(
                f"Auto-removed {original_count - len(alerts)} fired fixed-price alert(s): "
                f"{', '.join(sorted(fired_price_alert_ids))}"
            )

    return changed


def _coerce_settings(data: Dict[str, object]) -> Dict[str, float]:
    wait_seconds = int(float(data.get("wait_seconds", DEFAULT_WAIT_SECONDS)))
    pct_threshold = float(data.get("percent_threshold", DEFAULT_PERCENT_THRESHOLD))

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be greater than zero")
    if pct_threshold <= 0:
        raise ValueError("percent_threshold must be greater than zero")

    return {
        "wait_seconds": float(wait_seconds),
        "percent_threshold": float(pct_threshold),
    }


def get_runtime_settings(force: bool = False) -> Dict[str, float]:
    """Return the active settings, reloading from disk when changed."""

    global _settings_cache, _settings_mtime

    try:
        mtime = SETTINGS_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = None

    if force or _settings_cache is None or mtime != _settings_mtime:
        settings = {
            "wait_seconds": DEFAULT_WAIT_SECONDS,
            "percent_threshold": DEFAULT_PERCENT_THRESHOLD,
        }

        if mtime is not None:
            try:
                loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                settings.update(_coerce_settings(loaded))
            except Exception:
                # Keep defaults if the settings file is malformed.
                settings.update(_coerce_settings(settings))

        _settings_cache = settings
        _settings_mtime = mtime

    return dict(_settings_cache)


def update_runtime_settings(
    *,
    wait_seconds: int | None = None,
    percent_threshold: float | None = None,
) -> Dict[str, float]:
    """Update the persisted settings file and return the sanitized values."""

    current = get_runtime_settings(force=True)

    if wait_seconds is not None:
        current["wait_seconds"] = wait_seconds
    if percent_threshold is not None:
        current["percent_threshold"] = percent_threshold

    sanitized = _coerce_settings(current)
    SETTINGS_PATH.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")

    # Refresh the cache immediately so the running loop picks up changes on the next check.
    return get_runtime_settings(force=True)


def log(message: str) -> None:
    """Print a time-stamped log message."""
    now = _app_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def log_push_state() -> None:
    """Log the Telegram notification configuration state (without secrets)."""

    token, chat_id = _get_telegram_credentials()
    if token and chat_id:
        chat_note = f" chat_id={chat_id}" if chat_id else ""
        log(f"Telegram alerts ready.{chat_note}")
    else:
        log(
            "Telegram alerts disabled: set TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) "
            "and TELEGRAM_CHAT_ID env vars to enable them."
        )


def _log_classification_once(kind: str, detail: str, hint: str | None = None) -> None:
    """Log classification-specific details once per attempt window."""

    if kind not in _logged_classifications:
        _logged_classifications.add(kind)
        log(detail)
        if hint:
            log(hint)


def _push_configured() -> bool:
    token, chat_id = _get_telegram_credentials()
    return bool(token and chat_id)


def push_notifications_ready() -> bool:
    """Public helper for consumers that need to check configuration state."""

    return _push_configured()


def send_push_notification(title: str, message: str) -> bool:
    """Send a push notification via Telegram when configured."""

    global _push_warning_given, _push_success_logged, _push_failure_logged, _push_config_logged

    token, chat_id = _get_telegram_credentials()
    if not (token and chat_id):
        if not _push_warning_given:
            _push_warning_given = True
            log(
                "Telegram alerts are disabled: provide TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) and "
                "TELEGRAM_CHAT_ID env vars to enable them."
            )
        return False

    if not _push_config_logged:
        _push_config_logged = True
        log("Telegram alerts enabled via Telegram bot chat.")

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": f"{title}\n{message}"}
        response = _get_session().post(url, json=payload, timeout=10)
        _track_traffic("telegram", bytes_sent=len(url) + len(json.dumps(payload)), bytes_received=len(response.content))
        response.raise_for_status()
        if not _push_success_logged:
            _push_success_logged = True
            _push_failure_logged = False
            log("Telegram alert sent successfully.")
        return True
    except Exception as exc:
        if not _push_failure_logged:
            _push_failure_logged = True
            log(f"Telegram notification attempt failed: {exc}")
        return False


def send_push_test() -> Dict[str, object]:
    """Trigger a Telegram alert test and report the outcome."""

    configured = _push_configured()
    success = False

    if configured:
        success = send_push_notification(
            "Bybit monitor Telegram test",
            "If you received this, Telegram alerts are working for bybit_monitor.",
        )
    detail = (
        "Telegram alerts are not configured (set TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)."
        if not configured
        else "Test Telegram alert sent successfully." if success else "Telegram alert send attempt failed."
    )
    return {"sent": success, "detail": detail, "configured": configured}


class BybitBlockedError(RuntimeError):
    """Raised when Bybit returns a blocked response (e.g., 403 HTML)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        wait_hint: int | None = None,
        classification: str | None = None,
        hint: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.wait_hint = wait_hint
        self.classification = classification or "BLOCKED_WAF"
        self.hint = hint


class AccessIssueError(RuntimeError):
    """Raised when a fallback market source is restricted or unavailable."""

    def __init__(self, classification: str, detail: str, hint: str | None = None):
        super().__init__(detail)
        self.classification = classification
        self.hint = hint


def _get_session() -> requests.Session:
    global _session

    if _session is None:
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _session = session

    return _session


def _log_request_target(url: str, headers: Dict[str, str]) -> None:
    global _target_logged

    host_suffix = ""

    try:
        hostname = requests.utils.urlparse(url).hostname or "<unknown>"
        ip_address = socket.gethostbyname(hostname)
        host_suffix = f"; resolved host: {hostname} ({ip_address})"
    except Exception:
        host_suffix = "; resolved host: <unavailable>"

    if not _target_logged:
        _target_logged = True
    log(f"Preparing Bybit request -> URL: {url}; headers: {headers}{host_suffix}")


def extract_base_symbol(symbol: str) -> str:
    """Return the base asset name by stripping common quote currency suffixes."""
    uppercase_symbol = symbol.upper()
    for suffix in STABLECOIN_SUFFIXES:
        if uppercase_symbol.endswith(suffix):
            return uppercase_symbol[: -len(suffix)]
    return uppercase_symbol


def _iter_api_bases() -> list[str]:
    bases: list[str] = []

    for base in API_BASES:
        normalized = base.rstrip("/")
        if normalized and normalized not in bases:
            bases.append(normalized)

    primary = get_bybit_creds()[3].rstrip("/")
    if primary and primary not in bases:
        bases.append(primary)

    # Use a documented fallback host so we can switch regions when the primary is blocked.
    if API_FALLBACK_BASE:
        fallback = API_FALLBACK_BASE.rstrip("/")
        if fallback not in bases:
            bases.append(fallback)

    return bases


def _build_headers() -> Dict[str, str]:
    return {
        "User-Agent": os.getenv(
            "BYBIT_API_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
        "Accept": os.getenv("BYBIT_API_ACCEPT", "application/json"),
        "Accept-Language": os.getenv("BYBIT_API_ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
        "Connection": "keep-alive",
    }


def _auth_headers(params: Dict[str, str]) -> Dict[str, str]:
    _, api_key, api_secret, _primary, _source = get_bybit_creds()
    
    if not api_key or not api_secret:
        return {}

    timestamp_ms = str(int(time.time() * 1000))
    recv_window = os.getenv("BYBIT_RECV_WINDOW", "5000")
    # For public GET, sign timestamp + api_key + recv_window + query_string (sorted)
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{timestamp_ms}{api_key}{recv_window}{sorted_params}"
    signature = hmac.new(api_secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()

    return {
        "X-BAPI-SIGN": signature,
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp_ms,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN-TYPE": "2",
    }


def _fetch_linear_perpetual_symbols() -> set[str]:
    session = _get_session()
    headers = _build_headers()
    timeout = float(os.getenv("BYBIT_API_TIMEOUT", "20"))
    symbols: set[str] = set()

    for api_base in _iter_api_bases():
        cursor: str | None = None
        while True:
            params: Dict[str, object] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            url = f"{api_base}{INSTRUMENTS_PATH}"
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            _track_traffic("bybit", bytes_sent=len(url), bytes_received=len(resp.content))
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(f"instruments-info failed: {payload.get('retMsg')}")
            result = payload.get("result") or {}
            rows = result.get("list") or []
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                contract_type = str(row.get("contractType") or "")
                status = str(row.get("status") or "")
                delivery_time = str(row.get("deliveryTime") or "")
                if (
                    symbol
                    and status == "Trading"
                    and contract_type == "LinearPerpetual"
                    and delivery_time in {"", "0"}
                ):
                    symbols.add(symbol)
            cursor = result.get("nextPageCursor")
            if not cursor:
                break
        if symbols:
            return symbols
    return symbols


def _get_linear_perpetual_symbols(force: bool = False) -> set[str]:
    global _perp_symbols_cache, _perp_symbols_cache_at
    now = time.time()
    if (
        force
        or _perp_symbols_cache is None
        or (now - _perp_symbols_cache_at) > _PERP_SYMBOLS_TTL_SECONDS
    ):
        try:
            _perp_symbols_cache = _fetch_linear_perpetual_symbols()
            _perp_symbols_cache_at = now
        except Exception as exc:
            log(f"Failed to refresh linear perpetual symbol cache: {exc}")
            if _perp_symbols_cache is None:
                _perp_symbols_cache = set()
    return set(_perp_symbols_cache or set())


def _coalesce_prices(tickers: Iterable[Dict[str, object]]) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    allowed = _get_linear_perpetual_symbols()
    if not allowed:
        return prices

    for entry in tickers:
        symbol = str(entry.get("symbol") or "").upper()
        last_price = entry.get("lastPrice")
        if not symbol or last_price in (None, "", "0"):
            continue
        if symbol not in allowed:
            continue

        try:
            prices[symbol] = float(last_price)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return prices


def _fetch_fallback_prices() -> Dict[str, float]:
    """Retrieve perpetual futures prices from a fallback market-data source (Binance futures)."""

    session = _get_session()
    url = os.getenv("FALLBACK_MARKET_URL", "https://fapi.binance.com/fapi/v1/ticker/price")
    timeout = float(os.getenv("FALLBACK_MARKET_TIMEOUT", "15"))
    headers = {
        "User-Agent": "BybitAltcoinMonitor/1.1 (fallback-binance)",
        "Accept": "application/json",
    }

    response = session.get(url, timeout=timeout, headers=headers)
    _track_traffic("fallback_market", bytes_sent=len(url), bytes_received=len(response.content))
    content_type = response.headers.get("Content-Type", "")
    body_preview = response.text[:200]

    if response.status_code == 451 or "restricted location" in body_preview.lower():
        detail = (
            "ACCESS RESTRICTED (Binance 451) — restricted location / eligibility. "
            f"Status={response.status_code}; content-type={content_type}; body: {body_preview}"
        )
        hint = "Binance access restricted from this location; fallback source must be non-restricted."
        _log_classification_once("GEO_RESTRICTED", detail, hint)
        raise AccessIssueError("GEO_RESTRICTED", detail, hint)

    if response.status_code == 403 and "html" in content_type.lower():
        detail = (
            "ACCESS BLOCKED (Binance 403 HTML) — likely egress or WAF restriction. "
            f"Status={response.status_code}; content-type={content_type}; body: {body_preview}"
        )
        hint = (
            "This host is blocked when reaching Binance; try a different egress or non-restricted source."
        )
        _log_classification_once("BLOCKED_WAF", detail, hint)
        raise AccessIssueError("BLOCKED_WAF", detail, hint)

    if response.status_code != 200:
        detail = (
            f"Fallback source failed; status={response.status_code}; "
            f"content-type={content_type}; body={body_preview}"
        )
        _log_classification_once("DOWN", detail, None)
        raise AccessIssueError("DOWN", detail)

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Fallback JSON parse error: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("Fallback source returned unexpected payload shape.")

    prices: Dict[str, float] = {}
    for entry in payload:
        symbol = entry.get("symbol")
        price_val = entry.get("price") or entry.get("lastPrice")
        if not symbol or price_val in (None, "", "0"):
            continue

        # Keep only USDT/USDC perps to mirror linear contracts.
        symbol_str = str(symbol).upper()
        if not symbol_str.endswith(("USDT", "USDC")):
            continue

        base_symbol = extract_base_symbol(symbol_str)

        try:
            price = float(price_val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue

        prices[symbol_str] = price

    if not prices:
        raise RuntimeError("Fallback source returned no usable prices.")

    log(f"Using fallback futures market data (Binance) with {len(prices)} symbols.")
    return prices


def fetch_altcoin_prices() -> Dict[str, float]:
    """Ask Bybit for all linear perpetual futures prices and keep non-stablecoins only."""

    params = {"category": "linear"}
    headers = _build_headers()
    session = _get_session()
    errors: list[str] = []
    blocked_errors: list[str] = []
    timeout = float(os.getenv("BYBIT_API_TIMEOUT", "20"))
    global _auth_notice_logged
    mode, api_key, api_secret, _base_url, _key_source = get_bybit_creds()
    have_auth = bool(api_key and api_secret)
    if not have_auth and not _auth_notice_logged:
        _auth_notice_logged = True
        log(f"Bybit auth disabled: missing KEY/SECRET for selected mode={mode}.")

    for api_base in _iter_api_bases():
        url = f"{api_base}{API_PATH}"
        blocked_for_base = False
        for with_auth in (False, True):
            if with_auth and not have_auth:
                continue

            req_headers = headers.copy()
            if with_auth:
                req_headers.update(_auth_headers(params))

            prepared = session.prepare_request(
                requests.Request("GET", url, headers=req_headers, params=params)
            )
            _log_request_target(prepared.url or url, req_headers)

            try:
                response = session.send(prepared, timeout=timeout)
                _track_traffic("bybit", bytes_sent=len(prepared.url or url), bytes_received=len(response.content))
            except requests.RequestException as exc:  # pragma: no cover - network dependent
                errors.append(f"{api_base} connection error: {exc}")
                continue

            body_snippet = response.text[:200]
            content_type = response.headers.get("Content-Type", "")

            if response.status_code == 403 and "html" in content_type.lower():
                blocked_detail = (
                    f"endpoint={api_base}, auth={'yes' if with_auth else 'no'}, "
                    f"status={response.status_code}, content-type={content_type}, "
                    f"body preview: {body_snippet}"
                )
                blocked_errors.append(blocked_detail)
                _log_classification_once(
                    "BLOCKED_WAF",
                    (
                        "ACCESS BLOCKED (Bybit 403 HTML) — likely WAF/egress restriction. "
                        f"Details: {blocked_detail}"
                    ),
                    hint=(
                        "This host is being blocked from Render egress; try a different region/provider, "
                        "or proxy the request through allowed egress, or use authenticated + official SDK endpoints."
                    ),
                )
                if not with_auth and have_auth:
                    log("Unauthenticated request blocked; retrying once with API credentials...")
                    continue
                blocked_for_base = True
                break

            if response.status_code != 200:
                log(
                    "Bybit request failed; "
                    f"endpoint={api_base}, status={response.status_code}, "
                    f"content-type={content_type}, body preview: {body_snippet}"
                )
                errors.append(
                    f"{api_base} status {response.status_code}; content-type: {content_type}; body: {body_snippet}"
                )
                continue

            if "json" not in content_type:
                log(f"Warning: unexpected content type from Bybit ({api_base}): {content_type}")

            try:
                payload = response.json()
            except json.JSONDecodeError as decode_error:
                errors.append(f"{api_base} JSON decode error: {decode_error}")
                continue

            if payload.get("retCode") != 0:
                errors.append(
                    f"{api_base} retCode {payload.get('retCode')}: {payload.get('retMsg')} (trace {payload.get('traceId')})"
                )
                continue

            tickers = payload.get("result", {}).get("list", [])
            prices = _coalesce_prices(tickers)
            if not prices:
                errors.append(f"{api_base} returned no usable prices.")
                continue
            return prices

        if blocked_for_base:
            continue

    if blocked_errors:
        raise BybitBlockedError(
            "All Bybit endpoints appear blocked.",
            status=403,
            classification="BLOCKED_WAF",
            hint=(
                "This host is being blocked from Render egress; consider alternate egress, "
                "region, or authenticated official SDK usage."
            ),
        )

    # All endpoints failed; raise a detailed summary to surface the block reason.
    detail = "; ".join(errors) if errors else "All Bybit endpoints failed with unknown errors."
    log(f"All configured Bybit endpoints failed. Details: {detail}")
    raise RuntimeError(detail)


def send_notification(title: str, message: str) -> None:
    """Send Telegram notifications, falling back to console logging only."""

    if not send_push_notification(title, message):
        if _push_configured():
            log("Telegram alert delivery failed; using console logging fallback only.")
        else:
            log("Telegram alert not sent because Telegram is not configured.")
        log("ALERT: " + message)


def wait_with_log(total_seconds: int, label: str) -> None:
    """Wait while continuously updating heartbeat status."""
    total_seconds = max(0, int(total_seconds))
    if total_seconds == 0:
        return
    log(f"{label}: sleeping for {total_seconds} seconds.")
    remaining = total_seconds
    while remaining > 0:
        _heartbeat(phase="waiting", wait_seconds=total_seconds)
        chunk = min(5, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _load_state() -> Dict[str, object]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("symbols", {})
            raw.setdefault("custom_alerts", {})
            if isinstance(raw["symbols"], dict):
                return raw
    except Exception:
        pass
    return {"symbols": {}, "custom_alerts": {}}


def _save_state(state: Dict[str, object]) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _get_symbol_state(symbols_state: Dict[str, Dict[str, object]], symbol: str) -> Dict[str, object]:
    entry = symbols_state.get(symbol)
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("baseline_ready", False)
    entry.setdefault("last_ath_alert_at", 0.0)
    entry.setdefault("last_atl_alert_at", 0.0)
    return entry


def _fetch_klines(symbol: str, interval: str, end_ms: int | None) -> list[list[str]]:
    session = _get_session()
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": "1000",
    }
    if end_ms is not None:
        params["end"] = str(end_ms)
    url = f"{get_bybit_creds()[3].rstrip('/')}{KLINE_PATH}"
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("retCode") not in (0, "0", None):
        raise RuntimeError(f"Bybit kline error: {payload.get('retMsg') or payload}")
    result = payload.get("result") or {}
    candles = result.get("list") or []
    if not isinstance(candles, list):
        return []
    return candles


def fetch_historical_baseline(
    symbol: str,
    interval: str,
    max_pages: int,
) -> Tuple[float, float] | None:
    ath = None
    atl = None
    end_ms = None
    last_end = None
    for _ in range(max_pages):
        candles = _fetch_klines(symbol, interval, end_ms)
        if not candles:
            break
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 4:
                continue
            try:
                high = float(candle[2])
                low = float(candle[3])
            except (TypeError, ValueError):
                continue
            ath = high if ath is None else max(ath, high)
            atl = low if atl is None else min(atl, low)
        oldest = candles[-1][0] if candles else None
        try:
            oldest_ms = int(oldest) if oldest is not None else None
        except (TypeError, ValueError):
            oldest_ms = None
        if oldest_ms is None:
            break
        end_ms = oldest_ms - 1
        if end_ms == last_end:
            break
        last_end = end_ms
    if ath is None or atl is None:
        return None
    return ath, atl


def backfill_baselines(
    symbols: list[str],
    settings: Dict[str, float],
    symbols_state: Dict[str, Dict[str, object]],
) -> bool:
    batch_size = int(settings["ath_atl_backfill_batch"])
    max_pages = int(settings["ath_atl_backfill_max_pages"])
    interval = str(settings["ath_atl_granularity"])
    pending = [
        symbol
        for symbol in symbols
        if not _get_symbol_state(symbols_state, symbol).get("baseline_ready")
    ]
    if not pending:
        return False
    changed = False
    for symbol in pending[:batch_size]:
        try:
            baseline = fetch_historical_baseline(
                symbol=symbol,
                interval=interval,
                max_pages=max_pages,
            )
        except Exception as exc:
            log(f"Failed to backfill {symbol} klines: {exc}")
            continue
        if baseline is None:
            log(f"No historical klines returned for {symbol}; skipping baseline.")
            continue
        ath, atl = baseline
        entry = _get_symbol_state(symbols_state, symbol)
        entry.update(
            {
                "ath": ath,
                "atl": atl,
                "baseline_ready": True,
            }
        )
        symbols_state[symbol] = entry
        changed = True
        log(f"Baseline ready for {symbol}: ATH={ath:.6f} ATL={atl:.6f}.")
    return changed


def prune_non_perpetual_custom_alerts() -> None:
    allowed = _get_linear_perpetual_symbols(force=True)
    alerts = get_custom_alerts(force=True)
    kept = [a for a in alerts if str(a.get("symbol") or "").upper() in allowed]
    if len(kept) != len(alerts):
        _save_custom_alerts(kept)
        get_custom_alerts(force=True)
        log(
            "Pruned non-perpetual custom alerts: "
            f"removed={len(alerts) - len(kept)} kept={len(kept)}"
        )


def run_monitor() -> None:
    """Continuous monitoring loop."""
    previous_prices: Dict[str, float] = {}
    price_history: Dict[str, deque] = {}
    state = _load_state()
    iteration = 0
    blocked_streak = 0
    settings = get_runtime_settings(force=True)
    last_logged_settings = None

    api_targets = ", ".join(f"{base}{API_PATH}" for base in _iter_api_bases())
    log(
        "Using Bybit endpoint sequence "
        f"[{api_targets}]?category=linear (primary from {get_bybit_creds()[3]}; override with BYBIT_BASE_URL/BYBIT_API_BASE/BYBIT_API_BASES)"
    )

    _heartbeat(phase="starting", wait_seconds=int(settings["wait_seconds"]))
    while True:
        global _logged_classifications
        _logged_classifications = set()
        iteration += 1
        settings = get_runtime_settings()
        if settings != last_logged_settings:
            log(
                "Monitor settings: "
                f"wait_seconds={settings['wait_seconds']}s, "
                f"percent_threshold={settings['percent_threshold']:.2f}%"
            )
            last_logged_settings = dict(settings)
        log(f"Starting price check #{iteration}...")
        _heartbeat(phase="scanning", wait_seconds=int(settings["wait_seconds"]))

        try:
            prices = fetch_altcoin_prices()
            blocked_streak = 0
            source_label = "Bybit"
        except BybitBlockedError as exc:
            blocked_streak += 1
            wait_seconds = BLOCK_BACKOFFS[min(blocked_streak - 1, len(BLOCK_BACKOFFS) - 1)]
            _log_classification_once(
                exc.classification,
                (
                    f"ACCESS BLOCKED (status {exc.status or 403}) — likely WAF/egress restriction. "
                    "Trying fallback market data before backing off."
                ),
                hint=exc.hint,
            )
            try:
                prices = _fetch_fallback_prices()
                source_label = "Fallback futures"
                blocked_streak = 0  # success via fallback should reset aggressive backoff
            except AccessIssueError as fallback_exc:
                detail = (
                    f"Fallback market data unavailable ({fallback_exc.classification}): {fallback_exc}"
                )
                _log_classification_once(fallback_exc.classification, detail, fallback_exc.hint)
                log(
                    "Unable to reach Bybit or fallback due to access restrictions. "
                    f"Waiting {wait_seconds} seconds before retrying."
                )
                wait_with_log(wait_seconds, "Block backoff")
                continue
            except Exception as fallback_exc:
                log(f"Fallback market data unavailable: {fallback_exc}")
                log(
                    "Access to configured data sources is blocked or restricted. "
                    f"Waiting {wait_seconds} seconds before retrying."
                )
                wait_with_log(wait_seconds, "Block backoff")
                continue
        except Exception as exc:
            log("⚠️ Could not retrieve data from Bybit during this attempt.")
            print("-" * 80)
            print("Full error details to help with troubleshooting:")
            traceback.print_exc()
            print("-" * 80)
            log(
                "Quick tips: confirm your internet connection, make sure https://api.bybit.com is "
                "reachable from your location, and retry after checking firewall or VPN settings."
            )
            log(f"Waiting {ERROR_WAIT_SECONDS} seconds before trying again...")
            wait_with_log(ERROR_WAIT_SECONDS, "Retry delay")
            continue

        log(f"Received {len(prices)} perpetual prices from {source_label}.")

        if not prices:
            log(
                "Price source returned an empty list of symbols. This is unusual, so we will simply wait "
                "and try again."
            )
        else:
            if previous_prices:
                triggered_any = False

                # Notify about new or missing symbols
                current_symbols = set(prices)
                previous_symbols = set(previous_prices)
                new_symbols = sorted(current_symbols - previous_symbols)
                missing_symbols = sorted(previous_symbols - current_symbols)

                for symbol in new_symbols:
                    log(f"New symbol detected: {symbol}. It will be tracked from now on.")
                for symbol in missing_symbols:
                    log(
                        f"Altcoin missing this round: {symbol}. It may have been delisted or is temporarily unavailable."
                    )

                for symbol in sorted(current_symbols & previous_symbols):
                    current_price = prices[symbol]
                    previous_price = previous_prices.get(symbol)
                    if previous_price in (None, 0):
                        continue

                    change_pct = ((current_price - previous_price) / previous_price) * 100
                    if abs(change_pct) >= settings["percent_threshold"]:
                        direction = "up" if change_pct > 0 else "down"
                        message = (
                            f"{symbol} moved {direction} by {change_pct:+.2f}% "
                            f"(from {previous_price:.6f} to {current_price:.6f})."
                        )
                        log(message)
                        send_notification("Bybit Altcoin Alert", message)
                        triggered_any = True

                if not triggered_any:
                    log(
                        "No price jumps reached the "
                        f"{settings['percent_threshold']:.1f}% threshold during this cycle."
                    )
            else:
                log("Baseline prices recorded. Alerts will begin after the next update.")

            # Custom alerts (per-symbol price + move alerts)
            try:
                custom_alerts_definitions = get_custom_alerts()
                if custom_alerts_definitions:
                    if evaluate_custom_alerts(
                        custom_alerts_definitions, prices, state, price_history
                    ):
                        _save_state(state)
            except Exception:
                log("Custom alert evaluation failed for this cycle.")
                traceback.print_exc()

        previous_prices = prices
        log(
            "Waiting "
            f"{settings['wait_seconds'] // 60} minute(s) ({settings['wait_seconds']} seconds) "
            "before the next price check."
        )
        wait_with_log(settings["wait_seconds"], "Waiting for the next check")


def main() -> None:
    """Entry point for the monitor."""
    ensure_local_only_execution()
    log(format_env_bootstrap_log(_ENV_BOOTSTRAP_INFO))
    log("Bybit perpetual futures monitor started.")
    settings = get_runtime_settings(force=True)
    log(
        "The script asks Bybit for every linear perpetual price and raises alerts when the "
        f"price moves +/-{settings['percent_threshold']:.1f}% compared to the previous reading."
    )
    log("Press Ctrl+C at any time to stop the script safely.")
    mode, api_key, api_secret, base_url, key_source = get_bybit_creds()
    auth_enabled = bool(api_key and api_secret)
    log(
        "BYBIT mode="
        f"{mode} base_url={base_url} auth={'yes' if auth_enabled else 'no'} "
        f"key_source={key_source}"
    )
    log_push_state()
    if not auth_enabled:
        global _auth_notice_logged
        _auth_notice_logged = True
        log(f"Bybit auth disabled: missing KEY/SECRET for selected mode={mode}.")
    prune_non_perpetual_custom_alerts()
    try:
        run_monitor()
        _write_runtime_status(
            running=False,
            phase="stopped",
            wait_seconds=0,
            last_exit_reason="clean_exit",
        )
    except Exception as exc:
        _write_runtime_status(
            running=False,
            phase="error",
            wait_seconds=0,
            last_error=str(exc)[:500],
            last_exit_reason="uncaught_exception",
        )
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _write_runtime_status(
            running=False,
            phase="stopped",
            wait_seconds=0,
            last_exit_reason="keyboard_interrupt",
        )
        log("Stopped by user request. Goodbye!")
        sys.exit(0)
