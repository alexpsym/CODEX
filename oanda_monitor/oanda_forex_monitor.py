"""
Simple OANDA forex price monitor (Bybit-monitor style).
- Polls OANDA account pricing for a list of instruments.
- Alerts on % move vs previous poll.
- Alerts on new "all-time high" / "all-time low" observed by this script (persisted state.json).
Env vars:
- OANDA_API_KEY (or OANDA_ACCESS_TOKEN)
- OANDA_ACCOUNT_ID
- OANDA_ENV = practice|live (optional; default: live)
- OANDA_INSTRUMENTS (optional CSV, e.g. "EUR_USD,USD_JPY,AUD_USD")
- TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN)
- TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_PATH_PRICING = "/v3/accounts/{accountID}/pricing"
API_PATH_INSTRUMENTS = "/v3/accounts/{accountID}/instruments"
API_PATH_CANDLES = "/v3/instruments/{instrument}/candles"
DEFAULT_WAIT_SECONDS = int(os.getenv("OANDA_WAIT_SECONDS", "30"))
DEFAULT_PERCENT_THRESHOLD = float(os.getenv("OANDA_PERCENT_THRESHOLD", "0.10"))  # percent
DEFAULT_ATH_ATL_ENABLED = int(os.getenv("OANDA_ATH_ATL_ENABLED", "1"))
DEFAULT_ATH_ATL_MIN_BREAK_PCT = float(os.getenv("OANDA_ATH_ATL_MIN_BREAK_PCT", "0.0"))  # percent
DEFAULT_ATH_ATL_COOLDOWN_SECONDS = int(os.getenv("OANDA_ATH_ATL_COOLDOWN_SECONDS", "3600"))
DEFAULT_ATH_ATL_GRANULARITY = os.getenv("OANDA_ATH_ATL_GRANULARITY", "D")
DEFAULT_ATH_ATL_PRICE = os.getenv("OANDA_ATH_ATL_PRICE", "M")
DEFAULT_ATH_ATL_BACKFILL_BATCH = int(os.getenv("OANDA_ATH_ATL_BACKFILL_BATCH", "3"))
DEFAULT_ATH_ATL_BACKFILL_MAX_PAGES = int(os.getenv("OANDA_ATH_ATL_BACKFILL_MAX_PAGES", "20"))
SETTINGS_PATH = Path(__file__).with_name("settings.json")
STATE_PATH = Path(__file__).with_name("state.json")
CUSTOM_ALERTS_PATH = Path(__file__).with_name("custom_alerts.json")

_session: requests.Session | None = None
_settings_cache: Dict[str, float] | None = None
_settings_mtime: float | None = None
_alerts_cache: list[dict] | None = None
_alerts_mtime: float | None = None

_ALLOWED_ALERT_KINDS = {"price", "move"}
_ALLOWED_PRICE_DIRECTIONS = {"above", "below"}
_ALLOWED_MOVE_DIRECTIONS = {"up", "down", "either"}
_ALLOWED_MOVE_UNITS = {"pips", "pct"}


def _normalize_oanda_symbol(raw: str) -> str:
    s = str(raw or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("/", "_").replace("-", "_")
    if "_" not in s and re.fullmatch(r"[A-Z]{6}", s):
        s = f"{s[:3]}_{s[3:]}"
    return s


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
    symbol = _normalize_oanda_symbol(payload.get("symbol"))
    if not symbol:
        raise ValueError("symbol is required")

    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _ALLOWED_ALERT_KINDS:
        raise ValueError("kind must be one of: price, move")

    enabled = bool(payload.get("enabled", True))
    cooldown_seconds = int(float(payload.get("cooldown_seconds", 0) or 0))
    if cooldown_seconds < 0:
        cooldown_seconds = 0

    alert: dict = {
        "id": alert_id,
        "symbol": symbol,
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
        return alert

    direction = str(payload.get("direction") or "").strip().lower()
    if direction not in _ALLOWED_MOVE_DIRECTIONS:
        raise ValueError("direction must be up, down, or either for move alerts")
    unit = str(payload.get("unit") or "pips").strip().lower()
    if unit not in _ALLOWED_MOVE_UNITS:
        raise ValueError("unit must be pips or pct")
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


def replace_custom_alerts(alerts_payload: object) -> list[dict]:
    """Replace ALL custom alerts with the provided list (used for backup/restore)."""
    if not isinstance(alerts_payload, list):
        raise ValueError("alerts must be a list")
    replaced: list[dict] = []
    for item in alerts_payload:
        if not isinstance(item, dict):
            continue
        replaced.append(_coerce_alert(item))
    _save_custom_alerts(replaced)
    get_custom_alerts(force=True)
    return list(replaced)


def _pip_size_from_location(pip_location: int) -> float:
    try:
        return float(10 ** int(pip_location))
    except Exception:
        return 0.0001


def fetch_pip_locations(base_url: str, token: str, account_id: str) -> Dict[str, float]:
    session = _get_session()
    url = f"{base_url}{API_PATH_INSTRUMENTS.format(accountID=account_id)}"
    response = session.get(url, headers=_oanda_headers(token), timeout=15)
    response.raise_for_status()
    data = response.json() or {}
    instruments = data.get("instruments") or []
    out: Dict[str, float] = {}
    for inst in instruments:
        try:
            name = str(inst.get("name"))
            pip_loc = int(inst.get("pipLocation"))
            out[name] = _pip_size_from_location(pip_loc)
        except Exception:
            continue
    return out


def _evaluate_move_condition(
    unit: str,
    direction: str,
    threshold: float,
    current: float,
    window_min: float,
    window_max: float,
    pip_size: float,
) -> tuple[bool, str, float, float]:
    if direction in ("up", "either"):
        ref = window_min
        if ref > 0:
            measured = (
                ((current - ref) / ref) * 100.0 if unit == "pct" else (current - ref) / pip_size
            )
            if measured >= threshold:
                return True, "up", measured, ref
    if direction in ("down", "either"):
        ref = window_max
        if ref > 0:
            measured = (
                ((ref - current) / ref) * 100.0 if unit == "pct" else (ref - current) / pip_size
            )
            if measured >= threshold:
                return True, "down", measured, ref
    return False, direction, 0.0, current


def evaluate_custom_alerts(
    alerts: list[dict],
    prices: Dict[str, float],
    state: Dict[str, object],
    price_history: Dict[str, deque],
    pip_sizes: Dict[str, float],
) -> bool:
    now = time.time()
    grace_seconds = 2
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
        cutoff = now - max_window - grace_seconds
        for dq in price_history.values():
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    changed = False
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
                changed = True
                msg = f"{symbol} PRICE {direction.upper()} {target} | now {current}"
                log(msg)
                send_push_notification("OANDA Custom Price Alert", msg)
            elif not condition_met and not armed:
                st["armed"] = True
                changed = True
            continue

        window_s = int(alert.get("window_seconds"))
        dq = price_history.get(symbol)
        if not dq:
            continue
        cutoff = now - window_s - grace_seconds
        window_prices = [price for (ts, price) in dq if ts >= cutoff]
        if len(window_prices) < 2:
            continue
        window_min = min(window_prices)
        window_max = max(window_prices)
        unit = str(alert.get("unit") or "pips")
        direction = str(alert.get("direction") or "either")
        threshold = float(alert.get("threshold"))
        pip_size = float(pip_sizes.get(symbol) or 0.0001)

        triggered, resolved_dir, measured, ref = _evaluate_move_condition(
            unit, direction, threshold, current, window_min, window_max, pip_size
        )

        if triggered and armed and can_trigger:
            st["armed"] = False
            st["last_trigger_at"] = now
            changed = True
            unit_label = "%" if unit == "pct" else " pips"
            msg = (
                f"{symbol} MOVE {resolved_dir.upper()} {measured:.2f}{unit_label} in {window_s}s "
                f"| ref {ref:.6f} -> now {current:.6f}"
            )
            log(msg)
            send_push_notification("OANDA Custom Move Alert", msg)
        elif not triggered and not armed:
            st["armed"] = True
            changed = True

    return changed


def log(message: str) -> None:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _get_telegram_credentials() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or ""
    return token, chat_id


def _push_configured() -> bool:
    token, chat_id = _get_telegram_credentials()
    return bool(token and chat_id)


def log_push_state() -> None:
    token, chat_id = _get_telegram_credentials()
    if token and chat_id:
        log(f"Telegram alerts ready. chat_id={chat_id}")
    else:
        log(
            "Telegram alerts disabled: set TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) "
            "and TELEGRAM_CHAT_ID env vars to enable them."
        )


def send_push_notification(title: str, message: str) -> bool:
    token, chat_id = _get_telegram_credentials()
    if not (token and chat_id):
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": f"{title}\n{message}"}
        response = _get_session().post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        log(f"Telegram notification attempt failed: {exc}")
        return False


def push_notifications_ready() -> bool:
    return _push_configured()


def send_push_test() -> Dict[str, object]:
    configured = _push_configured()
    success = False
    if configured:
        success = send_push_notification(
            "OANDA monitor Telegram test",
            "If you received this, Telegram alerts are working for oanda_monitor.",
        )
    detail = (
        "Telegram alerts are not configured (set TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)."
        if not configured
        else "Test Telegram alert sent successfully." if success else "Telegram alert send attempt failed."
    )
    return {"sent": success, "detail": detail, "configured": configured}


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


def _coerce_settings(data: Dict[str, object]) -> Dict[str, float]:
    def as_int(key: str, default: int) -> int:
        try:
            return int(float(data.get(key, default)))
        except Exception:
            return default

    def as_float(key: str, default: float) -> float:
        try:
            return float(data.get(key, default))
        except Exception:
            return default

    wait_seconds = as_int("wait_seconds", DEFAULT_WAIT_SECONDS)
    pct_threshold = as_float("percent_threshold", DEFAULT_PERCENT_THRESHOLD)

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be greater than zero")
    if pct_threshold <= 0:
        raise ValueError("percent_threshold must be greater than zero")

    return {
        "wait_seconds": float(wait_seconds),
        "percent_threshold": float(pct_threshold),
    }


def get_runtime_settings(force: bool = False) -> Dict[str, float]:
    global _settings_cache, _settings_mtime
    try:
        mtime = SETTINGS_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = None

    if force or _settings_cache is None or mtime != _settings_mtime:
        settings: Dict[str, object] = {
            "wait_seconds": DEFAULT_WAIT_SECONDS,
            "percent_threshold": DEFAULT_PERCENT_THRESHOLD,
        }
        if mtime is not None:
            try:
                loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except Exception:
                pass
        _settings_cache = _coerce_settings(settings)
        _settings_mtime = mtime
    return dict(_settings_cache)


def update_runtime_settings(
    *, wait_seconds: int | None = None, percent_threshold: float | None = None
) -> Dict[str, float]:
    current = get_runtime_settings(force=True)
    merged: Dict[str, object] = dict(current)
    if wait_seconds is not None:
        merged["wait_seconds"] = wait_seconds
    if percent_threshold is not None:
        merged["percent_threshold"] = percent_threshold
    sanitized = _coerce_settings(merged)
    SETTINGS_PATH.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
    return get_runtime_settings(force=True)


def _oanda_token() -> str:
    return (os.getenv("OANDA_API_KEY") or os.getenv("OANDA_ACCESS_TOKEN") or "").strip()


def _oanda_account_id() -> str:
    return (os.getenv("OANDA_ACCOUNT_ID") or "").strip()


def _oanda_base_url() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    override = (os.getenv("OANDA_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if env in ("practice", "fxpractice", "demo"):
        return "https://api-fxpractice.oanda.com"
    return "https://api-fxtrade.oanda.com"


def _oanda_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


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


def _mid_from_price_obj(p: Dict[str, object]) -> float | None:
    try:
        bid = float(p.get("closeoutBid") or 0.0)
        ask = float(p.get("closeoutAsk") or 0.0)
        if bid and ask:
            return (bid + ask) / 2.0
    except Exception:
        pass

    try:
        bids = p.get("bids") or []
        asks = p.get("asks") or []
        bid2 = float(bids[0]["price"]) if bids else 0.0
        ask2 = float(asks[0]["price"]) if asks else 0.0
        if bid2 and ask2:
            return (bid2 + ask2) / 2.0
    except Exception:
        pass

    return None


def fetch_prices(
    base_url: str,
    token: str,
    account_id: str,
    instruments: List[str],
    since: str | None,
) -> Tuple[Dict[str, float], str | None]:
    session = _get_session()
    url = f"{base_url}{API_PATH_PRICING.format(accountID=account_id)}"
    params = {"instruments": ",".join(instruments)}
    if since:
        params["since"] = since
    response = session.get(url, headers=_oanda_headers(token), params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    out: Dict[str, float] = {}
    for price in data.get("prices", []) or []:
        inst = price.get("instrument")
        if not inst:
            continue
        mid = _mid_from_price_obj(price)
        if mid is None:
            continue
        out[str(inst)] = float(mid)
    next_since = data.get("time")
    return out, (str(next_since) if next_since else None)


def fetch_account_instruments(base_url: str, token: str, account_id: str) -> List[str]:
    session = _get_session()
    url = f"{base_url}{API_PATH_INSTRUMENTS.format(accountID=account_id)}"
    response = session.get(url, headers=_oanda_headers(token), timeout=15)
    response.raise_for_status()
    data = response.json()
    instruments = data.get("instruments", []) or []
    names: List[str] = []
    for inst in instruments:
        try:
            if inst.get("type") != "CURRENCY":
                continue
            name = inst.get("name")
            if name:
                names.append(str(name))
        except Exception:
            continue
    return sorted(set(names))


def _pct_change(new: float, old: float) -> float:
    if not old:
        return 0.0
    return ((new - old) / old) * 100.0


def _price_bucket_key(price: str) -> str:
    if price == "B":
        return "bid"
    if price == "A":
        return "ask"
    return "mid"


def _parse_oanda_time(value: str) -> _dt.datetime:
    cleaned = value.replace("Z", "+00:00")
    return _dt.datetime.fromisoformat(cleaned)


def _extract_candle_high_low(candle: Dict[str, object], price_key: str) -> Tuple[float, float] | None:
    price_blob = candle.get(price_key) or {}
    if not isinstance(price_blob, dict):
        return None
    high = price_blob.get("h")
    low = price_blob.get("l")
    try:
        return float(high), float(low)
    except (TypeError, ValueError):
        return None


def fetch_historical_baseline(
    *,
    base_url: str,
    token: str,
    instrument: str,
    granularity: str,
    price: str,
    max_pages: int,
) -> Tuple[float, float] | None:
    session = _get_session()
    url = f"{base_url}{API_PATH_CANDLES.format(instrument=instrument)}"
    price_key = _price_bucket_key(price)
    ath = None
    atl = None
    to_param = None
    last_to = None
    for _ in range(max_pages):
        params = {
            "count": 5000,
            "granularity": granularity,
            "price": price,
        }
        if to_param:
            params["to"] = to_param
        response = session.get(url, headers=_oanda_headers(token), params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        candles = data.get("candles") or []
        if not candles:
            break
        for candle in candles:
            if not isinstance(candle, dict):
                continue
            parsed = _extract_candle_high_low(candle, price_key)
            if parsed is None:
                continue
            high, low = parsed
            ath = high if ath is None else max(ath, high)
            atl = low if atl is None else min(atl, low)
        oldest_time = None
        for candle in reversed(candles):
            if not isinstance(candle, dict):
                continue
            candle_time = candle.get("time")
            if isinstance(candle_time, str):
                oldest_time = candle_time
                break
        if not oldest_time:
            break
        oldest_dt = _parse_oanda_time(oldest_time) - _dt.timedelta(seconds=1)
        to_param = oldest_dt.isoformat()
        if to_param == last_to:
            break
        last_to = to_param
    if ath is None or atl is None:
        return None
    return ath, atl


def _get_symbol_state(symbols_state: Dict[str, Dict[str, object]], symbol: str) -> Dict[str, object]:
    entry = symbols_state.get(symbol)
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("baseline_ready", False)
    entry.setdefault("last_ath_alert_at", 0.0)
    entry.setdefault("last_atl_alert_at", 0.0)
    return entry


def backfill_baselines(
    *,
    base_url: str,
    token: str,
    instruments: List[str],
    settings: Dict[str, float],
    symbols_state: Dict[str, Dict[str, object]],
) -> bool:
    batch_size = int(settings["ath_atl_backfill_batch"])
    max_pages = int(settings["ath_atl_backfill_max_pages"])
    granularity = str(settings["ath_atl_granularity"])
    price = str(settings["ath_atl_price"])
    pending = [
        instrument
        for instrument in instruments
        if not _get_symbol_state(symbols_state, instrument).get("baseline_ready")
    ]
    if not pending:
        return False
    changed = False
    for instrument in pending[:batch_size]:
        try:
            baseline = fetch_historical_baseline(
                base_url=base_url,
                token=token,
                instrument=instrument,
                granularity=granularity,
                price=price,
                max_pages=max_pages,
            )
        except Exception as exc:
            log(f"Failed to backfill {instrument} candles: {exc}")
            continue
        if baseline is None:
            log(f"No historical candles returned for {instrument}; skipping baseline.")
            continue
        ath, atl = baseline
        entry = _get_symbol_state(symbols_state, instrument)
        entry.update(
            {
                "ath": ath,
                "atl": atl,
                "baseline_ready": True,
            }
        )
        symbols_state[instrument] = entry
        changed = True
        log(f"Baseline ready for {instrument}: ATH={ath:.6f} ATL={atl:.6f}.")
    return changed


def run_monitor() -> None:
    token = _oanda_token()
    account_id = _oanda_account_id()
    if not token or not account_id:
        raise SystemExit("Missing OANDA_API_KEY (or OANDA_ACCESS_TOKEN) and/or OANDA_ACCOUNT_ID")
    base_url = _oanda_base_url()
    settings = get_runtime_settings(force=True)
    env_instruments = (os.getenv("OANDA_INSTRUMENTS") or "").strip()
    if env_instruments:
        instruments = [
            entry.strip() for entry in env_instruments.split(",") if entry.strip()
        ]
    else:
        instruments = fetch_account_instruments(base_url, token, account_id)
    if not instruments:
        raise SystemExit(
            "No instruments to monitor (set OANDA_INSTRUMENTS or ensure /instruments works)."
        )

    log(
        f"Using OANDA pricing endpoint: {base_url}{API_PATH_PRICING.format(accountID=account_id)}"
    )
    log(f"Monitoring {len(instruments)} instruments.")
    log_push_state()

    try:
        pip_sizes = fetch_pip_locations(base_url, token, account_id)
    except Exception as exc:
        pip_sizes = {}
        log(
            "Failed to fetch pipLocation map; defaulting pips conversion for missing instruments: "
            f"{exc}"
        )

    previous_prices: Dict[str, float] = {}
    price_history: Dict[str, deque] = {}
    since: str | None = None
    state = _load_state()
    last_logged_settings = None
    iteration = 0
    history_keep_s = int(os.getenv("OANDA_PRICE_HISTORY_SECONDS", "3600"))

    while True:
        iteration += 1
        settings = get_runtime_settings()
        if settings != last_logged_settings:
            log(
                "Monitor settings: "
                f"wait_seconds={int(settings['wait_seconds'])}s, "
                f"percent_threshold={settings['percent_threshold']:.2f}%"
            )
            last_logged_settings = dict(settings)
        log(f"Starting price check #{iteration}...")

        try:
            prices, next_since = fetch_prices(base_url, token, account_id, instruments, since)
            if next_since:
                since = next_since
        except Exception:
            log("Could not retrieve data from OANDA during this attempt.")
            print("-" * 80)
            traceback.print_exc()
            print("-" * 80)
            log("Waiting 30 seconds before trying again...")
            time.sleep(30)
            continue

        log(f"Received {len(prices)} prices from OANDA.")
        if not prices:
            log("Empty pricing response; waiting and retrying.")
        else:
            now = time.time()
            cutoff = now - history_keep_s
            for sym, px in prices.items():
                dq = price_history.get(sym)
                if dq is None:
                    dq = deque()
                    price_history[sym] = dq
                dq.append((now, float(px)))
                while dq and dq[0][0] < cutoff:
                    dq.popleft()
            if previous_prices:
                triggered_any = False
                current_symbols = set(prices)
                previous_symbols = set(previous_prices)
                for symbol in sorted(current_symbols - previous_symbols):
                    log(f"New symbol detected: {symbol}. It will be tracked from now on.")
                for symbol in sorted(previous_symbols - current_symbols):
                    log(f"Instrument missing this round: {symbol}.")

                for symbol in sorted(current_symbols & previous_symbols):
                    current_price = prices[symbol]
                    previous_price = previous_prices.get(symbol)
                    if not previous_price:
                        continue
                    change_pct = _pct_change(current_price, previous_price)
                    if abs(change_pct) >= float(settings["percent_threshold"]):
                        direction = "up" if change_pct > 0 else "down"
                        msg = (
                            f"{symbol} moved {direction} {change_pct:+.2f}% "
                            f"| {previous_price:.6f} -> {current_price:.6f}"
                        )
                        log(msg)
                        send_push_notification("OANDA Move Alert", msg)
                        triggered_any = True
                if not triggered_any:
                    log(
                        "No moves reached the "
                        f"{settings['percent_threshold']:.2f}% threshold during this cycle."
                    )
            else:
                log("Baseline prices recorded. Alerts will begin after the next update.")

            # Custom alerts (per-symbol price + move alerts)
            try:
                custom_alerts_definitions = get_custom_alerts()
                if custom_alerts_definitions:
                    if evaluate_custom_alerts(
                        custom_alerts_definitions, prices, state, price_history, pip_sizes
                    ):
                        _save_state(state)
            except Exception:
                log("Custom alert evaluation failed for this cycle.")
                traceback.print_exc()
            previous_prices = prices

        wait_s = int(settings["wait_seconds"])
        log(f"Waiting {wait_s} seconds before the next price check.")
        time.sleep(wait_s)


def main() -> None:
    log("OANDA forex monitor started.")
    if not SETTINGS_PATH.exists():
        update_runtime_settings()
    run_monitor()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user request. Goodbye!")
        sys.exit(0)
