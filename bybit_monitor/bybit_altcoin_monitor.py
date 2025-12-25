"""Simple Bybit altcoin perpetual futures price monitor.

This script fetches linear perpetual futures prices for altcoins from Bybit's
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
from pathlib import Path
from hashlib import sha256
from typing import Dict, Iterable, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bybit_credentials import resolve_bybit_credentials

# Credential + endpoint resolution -------------------------------------------------


def get_bybit_creds() -> Tuple[str, str, str, str, str]:
    """Resolve Bybit credentials and base URL using existing Render env vars."""

    mode, key, secret, base_url, key_source = resolve_bybit_credentials()
    return mode, key, secret, base_url, key_source


BYBIT_MODE, BYBIT_API_KEY, BYBIT_API_SECRET, PRIMARY_API_BASE, BYBIT_KEY_SOURCE = get_bybit_creds()
API_FALLBACK_BASE = os.getenv("BYBIT_API_FALLBACK_BASE") or "https://api.bytick.com"
API_BASES = [
    base.strip()
    for base in os.getenv("BYBIT_API_BASES", "").split(",")
    if base.strip()
]
API_PATH = "/v5/market/tickers"
DEFAULT_WAIT_SECONDS = int(os.getenv("BYBIT_WAIT_SECONDS", "300"))
ERROR_WAIT_SECONDS = 60
BLOCK_BACKOFFS = [60, 120, 300, 900, 1800]  # 1m, 2m, 5m, 15m, 30m
DEFAULT_PERCENT_THRESHOLD = float(os.getenv("BYBIT_PERCENT_THRESHOLD", "5.0"))
STABLECOIN_SUFFIXES = ("USDT", "USDC", "USDD", "USD")
PRIMARY_COINS = {"BTC", "ETH"}
SETTINGS_PATH = Path(__file__).with_name("settings.json")

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

def _get_telegram_credentials() -> tuple[str, str]:
    """Return Telegram credentials from environment variables."""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or ""
    return token, chat_id

try:  # Optional helper for desktop notifications
    from plyer import notification as _plyer_notification
except Exception:  # pragma: no cover - very environment specific
    _plyer_notification = None

_notification_warning_given = False


def _coerce_settings(data: Dict[str, float]) -> Dict[str, float]:
    wait_seconds = int(float(data.get("wait_seconds", DEFAULT_WAIT_SECONDS)))
    pct_threshold = float(data.get("percent_threshold", DEFAULT_PERCENT_THRESHOLD))

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be greater than zero")
    if pct_threshold <= 0:
        raise ValueError("percent_threshold must be greater than zero")

    return {
        "wait_seconds": wait_seconds,
        "percent_threshold": pct_threshold,
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
    *, wait_seconds: int | None = None, percent_threshold: float | None = None
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
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    primary = PRIMARY_API_BASE.rstrip("/")
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
    api_key = BYBIT_API_KEY
    api_secret = BYBIT_API_SECRET
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


def _coalesce_prices(tickers: Iterable[Dict[str, object]]) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    for entry in tickers:
        symbol = entry.get("symbol")
        last_price = entry.get("lastPrice")
        if not symbol or last_price in (None, "", "0"):
            continue

        base_symbol = extract_base_symbol(str(symbol))
        if base_symbol in PRIMARY_COINS:
            continue  # skip BTC and ETH because the focus is on altcoins

        try:
            price = float(last_price)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue

        prices[str(symbol).upper()] = price
    return prices


def _fetch_fallback_prices() -> Dict[str, float]:
    """Retrieve altcoin prices from a fallback market-data source (Binance futures)."""

    session = _get_session()
    url = os.getenv("FALLBACK_MARKET_URL", "https://fapi.binance.com/fapi/v1/ticker/price")
    timeout = float(os.getenv("FALLBACK_MARKET_TIMEOUT", "15"))
    headers = {
        "User-Agent": "BybitAltcoinMonitor/1.1 (fallback-binance)",
        "Accept": "application/json",
    }

    response = session.get(url, timeout=timeout, headers=headers)
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
        if base_symbol in PRIMARY_COINS:
            continue

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
    """Ask Bybit for all linear perpetual futures prices and keep altcoins only."""

    params = {"category": "linear"}
    headers = _build_headers()
    session = _get_session()
    errors: list[str] = []
    blocked_errors: list[str] = []
    timeout = float(os.getenv("BYBIT_API_TIMEOUT", "20"))
    global _auth_notice_logged
    have_auth = bool(BYBIT_API_KEY and BYBIT_API_SECRET)
    if not have_auth and not _auth_notice_logged:
        _auth_notice_logged = True
        log(f"Bybit auth disabled: missing KEY/SECRET for selected mode={BYBIT_MODE}.")

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
    """Send Telegram + desktop notifications, falling back to console beeps."""

    global _notification_warning_given
    notification_sent = False

    # Push notification (Telegram)
    push_sent = send_push_notification(title, message)
    notification_sent = notification_sent or push_sent

    # Desktop notification where supported
    if _plyer_notification is not None:
        try:
            _plyer_notification.notify(
                title=title,
                message=message,
                app_name="Bybit Altcoin Monitor",
                timeout=15,
            )
            notification_sent = True
            log("Desktop notification sent successfully.")
        except Exception as exc:  # pragma: no cover - depends on OS desktop support
            log(f"Desktop notification attempt failed: {exc}")

    if not notification_sent:
        if not _notification_warning_given:
            _notification_warning_given = True
            log(
                "Desktop/Telegram notifications unavailable. Install 'plyer' for desktop and set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID env vars for Telegram alerts, then restart this script."
            )
        # Audible fallback if possible
        try:  # pragma: no cover - OS specific
            if sys.platform.startswith("win"):
                import winsound

                winsound.MessageBeep()  # type: ignore[attr-defined]
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            pass
        log("ALERT: " + message)


def wait_with_log(total_seconds: int, label: str) -> None:
    """Wait for the given duration with a single log line."""
    total_seconds = max(0, int(total_seconds))
    if total_seconds == 0:
        return
    log(f"{label}: sleeping for {total_seconds} seconds.")
    time.sleep(total_seconds)


def run_monitor() -> None:
    """Continuous monitoring loop."""
    previous_prices: Dict[str, float] = {}
    iteration = 0
    blocked_streak = 0
    settings = get_runtime_settings(force=True)
    last_logged_settings = None

    api_targets = ", ".join(f"{base}{API_PATH}" for base in _iter_api_bases())
    log(
        "Using Bybit endpoint sequence "
        f"[{api_targets}]?category=linear (primary from {PRIMARY_API_BASE}; override with BYBIT_BASE_URL/BYBIT_API_BASE/BYBIT_API_BASES)"
    )

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

        log(f"Received {len(prices)} altcoin perpetual prices from {source_label}.")

        if not prices:
            log(
                "Price source returned an empty list of altcoins. This is unusual, so we will simply wait "
                "and try again."
            )
        elif previous_prices:
            triggered_any = False

            # Notify about new or missing symbols
            current_symbols = set(prices)
            previous_symbols = set(previous_prices)
            new_symbols = sorted(current_symbols - previous_symbols)
            missing_symbols = sorted(previous_symbols - current_symbols)

            for symbol in new_symbols:
                log(f"New altcoin detected: {symbol}. It will be tracked from now on.")
            for symbol in missing_symbols:
                log(f"Altcoin missing this round: {symbol}. It may have been delisted or is temporarily unavailable.")

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

        previous_prices = prices
        log(
            "Waiting "
            f"{settings['wait_seconds'] // 60} minute(s) ({settings['wait_seconds']} seconds) "
            "before the next price check."
        )
        wait_with_log(settings["wait_seconds"], "Waiting for the next check")


def main() -> None:
    """Entry point for the monitor."""
    log("Bybit altcoin monitor started.")
    settings = get_runtime_settings(force=True)
    log(
        "The script asks Bybit for every linear perpetual altcoin price and raises alerts when the "
        f"price moves +/-{settings['percent_threshold']:.1f}% compared to the previous reading."
    )
    log("Press Ctrl+C at any time to stop the script safely.")
    auth_enabled = bool(BYBIT_API_KEY and BYBIT_API_SECRET)
    log(
        "BYBIT mode="
        f"{BYBIT_MODE} base_url={PRIMARY_API_BASE} auth={'yes' if auth_enabled else 'no'} "
        f"key_source={BYBIT_KEY_SOURCE}"
    )
    log_push_state()
    if not auth_enabled:
        global _auth_notice_logged
        _auth_notice_logged = True
        log(f"Bybit auth disabled: missing KEY/SECRET for selected mode={BYBIT_MODE}.")
    if _plyer_notification is None:
        log("Desktop alerts need the 'plyer' package. Install it with: pip install plyer")
    run_monitor()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user request. Goodbye!")
        sys.exit(0)
