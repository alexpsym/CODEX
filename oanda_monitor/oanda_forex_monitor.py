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
import sys
import time
import traceback
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

_session: requests.Session | None = None
_settings_cache: Dict[str, float] | None = None
_settings_mtime: float | None = None


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
    ath_atl_enabled = as_int("ath_atl_enabled", DEFAULT_ATH_ATL_ENABLED)
    ath_atl_min_break_pct = as_float("ath_atl_min_break_pct", DEFAULT_ATH_ATL_MIN_BREAK_PCT)
    ath_atl_cooldown_seconds = as_int(
        "ath_atl_cooldown_seconds", DEFAULT_ATH_ATL_COOLDOWN_SECONDS
    )
    ath_atl_granularity = str(
        data.get("ath_atl_granularity", DEFAULT_ATH_ATL_GRANULARITY)
    ).strip()
    ath_atl_price = str(data.get("ath_atl_price", DEFAULT_ATH_ATL_PRICE)).strip().upper()
    ath_atl_backfill_batch = as_int(
        "ath_atl_backfill_batch", DEFAULT_ATH_ATL_BACKFILL_BATCH
    )
    ath_atl_backfill_max_pages = as_int(
        "ath_atl_backfill_max_pages", DEFAULT_ATH_ATL_BACKFILL_MAX_PAGES
    )

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be greater than zero")
    if pct_threshold <= 0:
        raise ValueError("percent_threshold must be greater than zero")
    if ath_atl_enabled not in (0, 1):
        ath_atl_enabled = 1 if ath_atl_enabled else 0
    if ath_atl_min_break_pct < 0:
        ath_atl_min_break_pct = 0.0
    if ath_atl_cooldown_seconds < 0:
        ath_atl_cooldown_seconds = 0
    if not ath_atl_granularity:
        ath_atl_granularity = DEFAULT_ATH_ATL_GRANULARITY
    if not ath_atl_price:
        ath_atl_price = DEFAULT_ATH_ATL_PRICE
    if ath_atl_backfill_batch < 1:
        ath_atl_backfill_batch = 1
    if ath_atl_backfill_max_pages < 1:
        ath_atl_backfill_max_pages = 1

    return {
        "wait_seconds": float(wait_seconds),
        "percent_threshold": float(pct_threshold),
        "ath_atl_enabled": float(ath_atl_enabled),
        "ath_atl_min_break_pct": float(ath_atl_min_break_pct),
        "ath_atl_cooldown_seconds": float(ath_atl_cooldown_seconds),
        "ath_atl_granularity": ath_atl_granularity,
        "ath_atl_price": ath_atl_price,
        "ath_atl_backfill_batch": float(ath_atl_backfill_batch),
        "ath_atl_backfill_max_pages": float(ath_atl_backfill_max_pages),
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
            "ath_atl_enabled": DEFAULT_ATH_ATL_ENABLED,
            "ath_atl_min_break_pct": DEFAULT_ATH_ATL_MIN_BREAK_PCT,
            "ath_atl_cooldown_seconds": DEFAULT_ATH_ATL_COOLDOWN_SECONDS,
            "ath_atl_granularity": DEFAULT_ATH_ATL_GRANULARITY,
            "ath_atl_price": DEFAULT_ATH_ATL_PRICE,
            "ath_atl_backfill_batch": DEFAULT_ATH_ATL_BACKFILL_BATCH,
            "ath_atl_backfill_max_pages": DEFAULT_ATH_ATL_BACKFILL_MAX_PAGES,
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


def update_runtime_settings(**updates: object) -> Dict[str, float]:
    current = get_runtime_settings(force=True)
    merged: Dict[str, object] = dict(current)
    merged.update(updates)
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
            if isinstance(raw["symbols"], dict):
                return raw
    except Exception:
        pass
    return {"symbols": {}}


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

    previous_prices: Dict[str, float] = {}
    since: str | None = None
    state = _load_state()
    symbols_state: Dict[str, Dict[str, float]] = state.get("symbols", {})  # type: ignore[assignment]
    last_logged_settings = None
    iteration = 0

    while True:
        iteration += 1
        settings = get_runtime_settings()
        if settings != last_logged_settings:
            log(
                "Monitor settings: "
                f"wait_seconds={int(settings['wait_seconds'])}s, "
                f"percent_threshold={settings['percent_threshold']:.2f}%, "
                f"ath_atl_enabled={int(settings['ath_atl_enabled'])}, "
                f"ath_atl_min_break_pct={settings['ath_atl_min_break_pct']:.4f}%, "
                f"ath_atl_cooldown_seconds={int(settings['ath_atl_cooldown_seconds'])}, "
                f"ath_atl_granularity={settings['ath_atl_granularity']}, "
                f"ath_atl_price={settings['ath_atl_price']}, "
                f"ath_atl_backfill_batch={int(settings['ath_atl_backfill_batch'])}, "
                f"ath_atl_backfill_max_pages={int(settings['ath_atl_backfill_max_pages'])}"
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
            if int(settings["ath_atl_enabled"]) == 1:
                baseline_changed = backfill_baselines(
                    base_url=base_url,
                    token=token,
                    instruments=list(prices.keys()),
                    settings=settings,
                    symbols_state=symbols_state,
                )
                changed_state = baseline_changed
                min_break_pct = float(settings["ath_atl_min_break_pct"])
                cooldown_seconds = int(settings["ath_atl_cooldown_seconds"])
                now_ts = time.time()
                for symbol, price in prices.items():
                    entry = _get_symbol_state(symbols_state, symbol)
                    if not entry.get("baseline_ready"):
                        continue
                    ath = float(entry.get("ath", price))
                    atl = float(entry.get("atl", price))
                    ath_trigger = price > ath * (1.0 + (min_break_pct / 100.0))
                    atl_trigger = price < atl * (1.0 - (min_break_pct / 100.0))
                    last_ath_alert = float(entry.get("last_ath_alert_at", 0.0))
                    last_atl_alert = float(entry.get("last_atl_alert_at", 0.0))
                    if ath_trigger and (cooldown_seconds <= 0 or now_ts - last_ath_alert >= cooldown_seconds):
                        entry["ath"] = price
                        entry["last_ath_alert_at"] = now_ts
                        symbols_state[symbol] = entry
                        changed_state = True
                        msg = f"{symbol} NEW ATH | {ath:.6f} -> {price:.6f}"
                        log(msg)
                        send_push_notification("OANDA ATH Alert", msg)
                    if atl_trigger and (cooldown_seconds <= 0 or now_ts - last_atl_alert >= cooldown_seconds):
                        entry["atl"] = price
                        entry["last_atl_alert_at"] = now_ts
                        symbols_state[symbol] = entry
                        changed_state = True
                        msg = f"{symbol} NEW ATL | {atl:.6f} -> {price:.6f}"
                        log(msg)
                        send_push_notification("OANDA ATL Alert", msg)
                if changed_state:
                    state["symbols"] = symbols_state
                    _save_state(state)

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
