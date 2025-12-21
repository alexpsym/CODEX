"""Simple Bybit altcoin perpetual futures price monitor.

This script fetches linear perpetual futures prices for altcoins from Bybit's
public API. It watches for price jumps of at least +/-5% compared to the
previous fetch and notifies the user when that happens. The script is meant to
run continuously until the user stops it manually.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import sys
import time
import traceback
from typing import Dict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE = os.getenv("BYBIT_API_BASE", "https://api.bybit.com")
API_FALLBACK_BASE = os.getenv("BYBIT_API_FALLBACK_BASE") or "https://api.bytick.com"
API_PATH = "/v5/market/tickers"
WAIT_SECONDS = 300  # 5 minutes
ERROR_WAIT_SECONDS = 60
PERCENT_THRESHOLD = 5.0
STABLECOIN_SUFFIXES = ("USDT", "USDC", "USDD", "USD")
PRIMARY_COINS = {"BTC", "ETH"}

_session: requests.Session | None = None
_target_logged = False

try:  # Optional helper for desktop notifications
    from plyer import notification as _plyer_notification
except Exception:  # pragma: no cover - very environment specific
    _plyer_notification = None

_notification_warning_given = False


def log(message: str) -> None:
    """Print a time-stamped log message."""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


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
    bases = [API_BASE.rstrip("/")]

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
    }


def fetch_altcoin_prices() -> Dict[str, float]:
    """Ask Bybit for all linear perpetual futures prices and keep altcoins only."""

    params = {"category": "linear"}
    headers = _build_headers()
    session = _get_session()
    errors: list[str] = []

    for api_base in _iter_api_bases():
        url = f"{api_base}{API_PATH}"
        prepared = session.prepare_request(
            requests.Request("GET", url, headers=headers, params=params)
        )
        _log_request_target(prepared.url or url, headers)

        try:
            response = session.send(prepared, timeout=20)
        except requests.RequestException as exc:  # pragma: no cover - network dependent
            errors.append(f"{api_base} connection error: {exc}")
            continue

        body_snippet = response.text[:200]
        content_type = response.headers.get("Content-Type", "")

        if response.status_code != 200:
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
        prices: Dict[str, float] = {}

        for entry in tickers:
            symbol = entry.get("symbol")
            last_price = entry.get("lastPrice")
            if not symbol or last_price in (None, "", "0"):
                continue

            base_symbol = extract_base_symbol(symbol)
            if base_symbol in PRIMARY_COINS:
                continue  # skip BTC and ETH because the focus is on altcoins

            try:
                price = float(last_price)
            except (TypeError, ValueError):
                continue

            prices[symbol.upper()] = price

        return prices

    # All endpoints failed; raise a detailed summary to surface the block reason.
    detail = "; ".join(errors) if errors else "All Bybit endpoints failed with unknown errors."
    raise RuntimeError(detail)


def send_notification(title: str, message: str) -> None:
    """Try to show a desktop notification. Fall back to a console alert."""
    global _notification_warning_given
    notification_sent = False

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
                "Desktop notifications are unavailable. Install them by running 'pip install plyer' "
                "and then restart this script."
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


def progress_bar(total_seconds: int, label: str) -> None:
    """Show a simple progress bar that updates every second."""
    total_seconds = max(0, int(total_seconds))
    bar_length = 40
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= total_seconds:
            percent = 1.0 if total_seconds else 0.0
            filled_length = bar_length if total_seconds else 0
            remaining_seconds = 0
        else:
            percent = elapsed / total_seconds if total_seconds else 0.0
            filled_length = int(bar_length * percent)
            remaining_seconds = int(round(total_seconds - elapsed))

        bar = "#" * filled_length + "-" * (bar_length - filled_length)
        minutes, seconds = divmod(max(0, remaining_seconds), 60)
        sys.stdout.write(
            f"\r{label}: [{bar}] {percent * 100:6.2f}% | Time left: {minutes:02d}:{seconds:02d}"
        )
        sys.stdout.flush()

        if elapsed >= total_seconds:
            break

        try:
            time.sleep(1)
        except KeyboardInterrupt:  # allow the main loop to catch this
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise

    sys.stdout.write("\n")
    sys.stdout.flush()


def run_monitor() -> None:
    """Continuous monitoring loop."""
    previous_prices: Dict[str, float] = {}
    iteration = 0

    fallback_note = ""
    if API_FALLBACK_BASE and API_FALLBACK_BASE.rstrip("/") != API_BASE.rstrip("/"):
        fallback_note = f"; fallback {API_FALLBACK_BASE.rstrip('/')}{API_PATH}"

    log(
        "Using Bybit endpoint "
        f"{API_BASE.rstrip('/')}{API_PATH}?category=linear (override with BYBIT_API_BASE){fallback_note}"
    )

    while True:
        iteration += 1
        log(f"Starting price check #{iteration}...")

        try:
            prices = fetch_altcoin_prices()
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
            progress_bar(ERROR_WAIT_SECONDS, "Retry delay")
            continue

        log(f"Received {len(prices)} altcoin perpetual prices from Bybit.")

        if not prices:
            log(
                "Bybit returned an empty list of altcoins. This is unusual, so we will simply wait "
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
                if abs(change_pct) >= PERCENT_THRESHOLD:
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
                    f"No price jumps reached the {PERCENT_THRESHOLD:.1f}% threshold during this cycle."
                )
        else:
            log("Baseline prices recorded. Alerts will begin after the next update.")

        previous_prices = prices
        log(
            f"Waiting {WAIT_SECONDS // 60} minute(s) ({WAIT_SECONDS} seconds) before the next price check."
        )
        progress_bar(WAIT_SECONDS, "Waiting for the next check")


def main() -> None:
    """Entry point for the monitor."""
    log("Bybit altcoin monitor started.")
    log(
        "The script asks Bybit for every linear perpetual altcoin price and raises alerts when the "
        f"price moves +/-{PERCENT_THRESHOLD:.1f}% compared to the previous reading."
    )
    log("Press Ctrl+C at any time to stop the script safely.")
    if _plyer_notification is None:
        log("Desktop alerts need the 'plyer' package. Install it with: pip install plyer")
    run_monitor()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user request. Goodbye!")
        sys.exit(0)
