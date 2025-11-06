"""Simple Bybit perpetual futures price monitor.

This script fetches linear perpetual futures prices for every pair from Bybit's
public API. It watches for price jumps of at least +/-5% compared to the
previous fetch and notifies the user when that happens. The script is meant to
run continuously until the user stops it manually.
"""
from __future__ import annotations

import datetime as _dt
import http.server
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Optional

API_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
DEFAULT_WAIT_SECONDS = 60  # 1 minute
ERROR_WAIT_SECONDS = 60
PERCENT_THRESHOLD = 5.0
STABLECOIN_SUFFIXES = ("USDT", "USDC", "USDD", "USD")
MIN_CHECK_INTERVAL_SECONDS = 30
_PROGRESS_BAR_LENGTH = 40


def _format_minutes_value(seconds: int) -> str:
    """Return a human-friendly minute value string for the given seconds."""

    minutes = seconds / 60
    if minutes.is_integer():
        return str(int(minutes))
    return f"{minutes:.2f}".rstrip("0").rstrip(".")

try:  # Optional helper for desktop notifications
    from plyer import notification as _plyer_notification
except Exception:  # pragma: no cover - very environment specific
    _plyer_notification = None

_notification_warning_given = False


CONTROL_PANEL_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Bybit Futures Monitor Control Panel</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: \"Segoe UI\", Tahoma, sans-serif; margin: 0; padding: 2rem; background: #f5f5f5; color: #1f1f1f; }
    .container { max-width: 640px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 2rem; box-shadow: 0 6px 18px rgba(31, 31, 31, 0.12); }
    h1 { margin-top: 0; font-size: 1.8rem; }
    label { display: block; font-weight: 600; margin-bottom: 0.5rem; }
    input[type=\"number\"] { width: 100%; padding: 0.6rem; font-size: 1.1rem; border-radius: 8px; border: 1px solid #c6c6c6; box-sizing: border-box; }
    button { margin-top: 1rem; padding: 0.6rem 1rem; font-size: 1.1rem; background-color: #2563eb; color: white; border: none; border-radius: 8px; cursor: pointer; }
    button:hover { background-color: #1d4ed8; }
    .status { margin-top: 1rem; min-height: 1.4rem; font-weight: 600; }
    .status.error { color: #b91c1c; }
    .status.success { color: #047857; }
    .hint { margin-top: 1rem; font-size: 0.95rem; color: #4b5563; }
    @media (prefers-color-scheme: dark) {
        body { background: #0f172a; color: #e2e8f0; }
        .container { background: #1e293b; box-shadow: 0 6px 18px rgba(15, 23, 42, 0.5); }
        input[type=\"number\"] { background: #0f172a; color: #e2e8f0; border: 1px solid #334155; }
        button { background-color: #38bdf8; color: #0f172a; }
        button:hover { background-color: #0ea5e9; }
        .hint { color: #94a3b8; }
    }
  </style>
</head>
<body>
  <div class=\"container\">
    <h1>Bybit Futures Monitor Control Panel</h1>
    <p>Use this page to decide how often the script checks Bybit for price changes. When you save a new value it is applied immediately without restarting the monitor.</p>
    <label for=\"intervalMinutes\">Price check interval (minutes)</label>
    <input id=\"intervalMinutes\" type=\"number\" min=\"__MIN__\" step=\"0.1\" value=\"__DEFAULT__\" />
    <button id=\"saveButton\">Save interval</button>
    <div class=\"hint\" id=\"minutesHint\"></div>
    <div class=\"status\" id=\"status\"></div>
    <p class=\"hint\">Keep the interval above __MIN__ minute(s) to avoid overloading the public API. The monitor keeps running in the background while you adjust these settings.</p>
  </div>
  <script>
    const input = document.getElementById('intervalMinutes');
    const statusBox = document.getElementById('status');
    const minutesHint = document.getElementById('minutesHint');
    function formatIntervalDescription(minutes) {
        if (!Number.isFinite(minutes) || minutes <= 0) {
            return '';
        }
        const seconds = Math.round(minutes * 60);
        if (minutes < 1) {
            return `That is about ${seconds} second${seconds === 1 ? '' : 's'} between price checks.`;
        }
        if (Number.isInteger(minutes)) {
            return `That is ${minutes} minute${minutes === 1 ? '' : 's'} between price checks.`;
        }
        return `That is roughly ${minutes.toFixed(1)} minutes between price checks.`;
    }
    function updateHint() {
        const value = Number(input.value);
        if (!Number.isFinite(value) || value <= 0) {
            minutesHint.textContent = '';
            return;
        }
        minutesHint.textContent = formatIntervalDescription(value);
    }
    async function loadSettings() {
        try {
            const response = await fetch('/settings', { cache: 'no-store' });
            if (!response.ok) {
                throw new Error('Network response was not OK');
            }
            const data = await response.json();
            if (typeof data.checkIntervalMinutes === 'number') {
                input.value = data.checkIntervalMinutes;
            }
            updateHint();
        } catch (error) {
            statusBox.textContent = 'Unable to load the current settings. You can still enter a new value.';
            statusBox.className = 'status error';
        }
    }
    async function saveSettings() {
        const value = Number(input.value);
        if (!Number.isFinite(value) || value < __MIN__) {
            statusBox.textContent = `Please choose a value of at least __MIN__ minute(s).`;
            statusBox.className = 'status error';
            return;
        }
        statusBox.textContent = 'Saving...';
        statusBox.className = 'status';
        try {
            const response = await fetch('/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ checkIntervalMinutes: value })
            });
            if (!response.ok) {
                throw new Error('Server rejected the value');
            }
            statusBox.textContent = 'New interval saved. The monitor is already using it.';
            statusBox.className = 'status success';
        } catch (error) {
            statusBox.textContent = 'Could not save the value. Please try again.';
            statusBox.className = 'status error';
        }
    }
    document.getElementById('saveButton').addEventListener('click', saveSettings);
    input.addEventListener('input', updateHint);
    loadSettings();
    updateHint();
  </script>
</body>
</html>
"""

CONTROL_PANEL_HTML = (
    CONTROL_PANEL_HTML.replace("__MIN__", _format_minutes_value(MIN_CHECK_INTERVAL_SECONDS))
    .replace("__DEFAULT__", _format_minutes_value(DEFAULT_WAIT_SECONDS))
)


def log(message: str) -> None:
    """Print a time-stamped log message."""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


class MonitorSettings:
    """Thread-safe container for live monitor configuration."""

    def __init__(self, initial_interval: int) -> None:
        self._lock = threading.Lock()
        self._check_interval = max(MIN_CHECK_INTERVAL_SECONDS, int(initial_interval))
        self._update_event = threading.Event()

    def get_check_interval(self) -> int:
        with self._lock:
            return self._check_interval

    def get_check_interval_minutes(self) -> float:
        return self.get_check_interval() / 60

    def set_check_interval_minutes(self, minutes: float) -> None:
        try:
            numeric_minutes = float(minutes)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive programming
            raise ValueError("Interval must be a numeric value in minutes.") from exc

        if not math.isfinite(numeric_minutes):
            raise ValueError("Interval must be a numeric value in minutes.")

        new_value = int(round(numeric_minutes * 60))
        new_value = max(MIN_CHECK_INTERVAL_SECONDS, new_value)

        with self._lock:
            if new_value == self._check_interval:
                return
            self._check_interval = new_value

        minutes_text = _format_minutes_value(new_value)
        log(
            f"Check interval updated to {minutes_text} minute(s) ({new_value} seconds)."
        )
        self._update_event.set()

    def wait_for_update(self, timeout: float) -> bool:
        return self._update_event.wait(timeout)

    def clear_update_event(self) -> None:
        self._update_event.clear()

    def as_dict(self) -> Dict[str, float]:
        return {"checkIntervalMinutes": self.get_check_interval_minutes()}


class ControlPanelRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve the HTML interface and handle setting updates."""

    settings: Optional[MonitorSettings] = None

    def do_GET(self) -> None:  # pragma: no cover - integration behaviour
        path = self.path.split("?", 1)[0]

        if path in {"/", "/index.html"}:
            payload = CONTROL_PANEL_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/settings":
            settings = self.settings
            if settings is None:
                self.send_error(500, "Settings are not available")
                return

            payload = json.dumps(settings.as_dict()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # pragma: no cover - integration behaviour
        path = self.path.split("?", 1)[0]
        if path != "/settings":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length else b""

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON payload")
            log("Control panel received invalid JSON data.")
            return

        new_value = payload.get("checkIntervalMinutes")
        if not isinstance(new_value, (int, float)):
            self.send_error(400, "'checkIntervalMinutes' must be a number")
            log(
                "Control panel received a request without a numeric 'checkIntervalMinutes'."
            )
            return

        settings = self.settings
        if settings is None:
            self.send_error(500, "Settings are not available")
            return

        try:
            settings.set_check_interval_minutes(float(new_value))
        except ValueError as exc:
            self.send_error(400, str(exc))
            return

        payload = json.dumps(settings.as_dict()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # pragma: no cover - quiet server
        # Silence the default HTTP server logging to keep the console readable.
        return


def start_control_panel(settings: MonitorSettings) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Start the local HTTP server that powers the control panel."""

    ControlPanelRequestHandler.settings = settings
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ControlPanelRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="ControlPanelServer", daemon=True)
    thread.start()
    server.control_panel_thread = thread  # type: ignore[attr-defined]
    port = server.server_address[1]
    log(f"Control panel is running at http://127.0.0.1:{port}/")
    return server, port


def _launch_in_edge(url: str, context: str) -> bool:
    """Open the given URL in Microsoft Edge if possible."""

    if sys.platform.startswith("win"):
        edge_path: Optional[str] = shutil.which("msedge")
        candidate_paths = [
            edge_path,
            r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
            r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        ]

        for candidate in candidate_paths:
            if candidate and os.path.exists(candidate):
                try:
                    subprocess.Popen(
                        [candidate, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
                except Exception as exc:  # pragma: no cover - environment specific
                    log(
                        f"Attempted to launch Microsoft Edge via '{candidate}' for {context} but it failed: {exc}"
                    )

        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "msedge", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:  # pragma: no cover - environment specific
            log(f"Fallback Edge launch command failed for {context}: {exc}")
            return False

    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", "-a", "Microsoft Edge", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:  # pragma: no cover - environment specific
            log(f"macOS Edge launch command failed for {context}: {exc}")
            return False

    linux_candidates = [
        shutil.which("microsoft-edge"),
        shutil.which("microsoft-edge-stable"),
        shutil.which("msedge"),
        shutil.which("microsoft-edge-dev"),
        shutil.which("microsoft-edge-beta"),
    ]
    for candidate in linux_candidates:
        if not candidate:
            continue
        try:
            subprocess.Popen(
                [candidate, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:  # pragma: no cover - environment specific
            log(
                f"Attempted to launch Microsoft Edge via '{candidate}' for {context} but it failed: {exc}"
            )

    return False


def open_control_panel_in_edge(url: str) -> None:
    """Try to open the control panel page in Microsoft Edge."""

    log("Opening the control panel interface...")

    if _launch_in_edge(url, "the control panel"):
        log("Control panel opened in Microsoft Edge.")
        return

    try:
        webbrowser.open(url, new=2)
        log(
            "Opened the control panel in the default browser. If Microsoft Edge is available, "
            "you can copy the link into it manually."
        )
    except Exception as exc:  # pragma: no cover - environment specific
        log(f"Please open this address manually in Microsoft Edge: {url} (automatic open failed: {exc})")

def extract_base_symbol(symbol: str) -> str:
    """Return the base asset name by stripping common quote currency suffixes."""
    uppercase_symbol = symbol.upper()
    for suffix in STABLECOIN_SUFFIXES:
        if uppercase_symbol.endswith(suffix):
            return uppercase_symbol[: -len(suffix)]
    return uppercase_symbol


def fetch_perpetual_prices() -> Dict[str, float]:
    """Ask Bybit for all linear perpetual futures prices and return them all."""
    request = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BybitFuturesMonitor/1.0)",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status_code = response.getcode()
            raw_payload = response.read()
    except urllib.error.HTTPError as http_error:  # pragma: no cover - depends on network
        detail = ""
        try:
            detail = http_error.read().decode("utf-8", "ignore")
        except Exception:  # pragma: no cover - very specific
            detail = "<no additional error body provided>"
        raise RuntimeError(
            f"HTTP error {http_error.code} - {http_error.reason}. Response snippet: {detail[:200]}"
        ) from http_error
    except urllib.error.URLError as url_error:  # pragma: no cover - depends on network
        raise RuntimeError(f"Connection error: {url_error.reason}") from url_error
    except Exception as exc:  # pragma: no cover - unexpected edge cases
        raise RuntimeError(f"Unexpected problem while contacting Bybit: {exc}") from exc

    if status_code != 200:
        raise RuntimeError(f"Bybit replied with unexpected status code {status_code}.")

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except json.JSONDecodeError as decode_error:
        raise RuntimeError("Could not decode Bybit's response as JSON.") from decode_error

    if payload.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit returned retCode {payload.get('retCode')} with message: {payload.get('retMsg')}"
        )

    tickers = payload.get("result", {}).get("list", [])
    prices: Dict[str, float] = {}

    for entry in tickers:
        symbol = entry.get("symbol")
        last_price = entry.get("lastPrice")
        if not symbol or last_price in (None, "", "0"):
            continue

        try:
            price = float(last_price)
        except (TypeError, ValueError):
            continue

        prices[symbol.upper()] = price

    return prices


def send_notification(title: str, message: str) -> None:
    """Try to show a desktop notification. Fall back to a console alert."""
    global _notification_warning_given
    notification_sent = False

    if _plyer_notification is not None:
        try:
            _plyer_notification.notify(
                title=title,
                message=message,
                app_name="Bybit Futures Monitor",
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


def create_alert_summary_page(alerts: list[dict[str, object]]) -> Path:
    """Generate an HTML summary for the triggered alerts and return its path."""

    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[str] = []
    for alert in alerts:
        direction_label = (
            "Up" if str(alert.get("direction", "")).lower() == "up" else "Down"
        )
        symbol = str(alert.get("symbol", ""))
        change_pct = float(alert.get("change_pct", 0.0))
        previous_price = float(alert.get("previous_price", 0.0))
        current_price = float(alert.get("current_price", 0.0))
        row_html = (
            f"        <tr><td>{symbol}</td><td>{direction_label}</td>"
            f"<td>{change_pct:+.2f}%</td><td>{previous_price:.6f}</td>"
            f"<td>{current_price:.6f}</td></tr>"
        )
        rows.append(row_html)

    rows_html = "\n".join(rows)
    table_body = rows_html or (
        "        <tr><td colspan=\"5\">No alert details available.</td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Bybit Futures Alert Summary</title>
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; padding: 2rem; background: #f5f5f5; color: #1f1f1f; }}
    h1 {{ margin-top: 0; }}
    .card {{ max-width: 900px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 2rem; box-shadow: 0 6px 18px rgba(31, 31, 31, 0.12); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1.5rem; }}
    th, td {{ padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #d1d5db; }}
    th {{ background: #e5e7eb; font-weight: 600; }}
    .timestamp {{ color: #4b5563; margin-top: 0.75rem; }}
    @media (prefers-color-scheme: dark) {{
        body {{ background: #0f172a; color: #e2e8f0; }}
        .card {{ background: #1e293b; box-shadow: 0 6px 18px rgba(15, 23, 42, 0.5); }}
        th {{ background: #334155; }}
        th, td {{ border-bottom: 1px solid #475569; }}
        .timestamp {{ color: #94a3b8; }}
    }}
  </style>
</head>
<body>
  <div class=\"card\">
    <h1>Price alert summary</h1>
    <p class=\"timestamp\">Generated at {timestamp}.</p>
    <table>
      <thead>
        <tr>
          <th>Pair</th>
          <th>Direction</th>
          <th>Change</th>
          <th>Previous price</th>
          <th>Current price</th>
        </tr>
      </thead>
      <tbody>
{table_body}
      </tbody>
    </table>
  </div>
</body>
</html>
"""

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".html", encoding="utf-8") as file:
        file.write(html)
        return Path(file.name)


def open_alert_summary_page(alerts: list[dict[str, object]]) -> None:
    """Create and open an HTML page summarising the triggered alerts."""

    if not alerts:
        return

    summary_path = create_alert_summary_page(alerts)
    summary_url = summary_path.as_uri()
    log(
        f"Opening alert summary for {len(alerts)} pair(s): {summary_url}"
    )
    if _launch_in_edge(summary_url, "the alert summary"):
        log("Alert summary opened in Microsoft Edge.")
    else:
        log(
            "Could not launch Microsoft Edge automatically. "
            f"Please open this summary in Microsoft Edge manually: {summary_path}"
        )


def progress_bar(total_seconds: int, label: str) -> None:
    """Show a simple progress bar that updates every second."""
    total_seconds = max(0, int(total_seconds))
    bar_length = _PROGRESS_BAR_LENGTH
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


def wait_for_next_check(settings: MonitorSettings) -> None:
    """Pause between price checks while respecting live setting updates."""

    interval = settings.get_check_interval()
    minutes_text = _format_minutes_value(interval)
    log(
        f"Waiting {minutes_text} minute(s) ({interval} seconds) before the next price check."
    )

    label = "Waiting for the next check"
    start_time = time.time()
    last_interval = max(interval, 1)
    target_end_time = start_time + last_interval

    while True:
        now = time.time()
        current_interval = settings.get_check_interval()

        if current_interval != last_interval:
            last_interval = max(current_interval, 1)
            target_end_time = start_time + last_interval

        remaining = target_end_time - now
        elapsed = now - start_time
        percent = 1.0 if last_interval <= 0 else min(1.0, max(0.0, elapsed / last_interval))
        filled_length = int(_PROGRESS_BAR_LENGTH * percent)
        bar = "#" * filled_length + "-" * (_PROGRESS_BAR_LENGTH - filled_length)
        minutes, seconds = divmod(int(max(0, round(remaining))), 60)

        sys.stdout.write(
            f"\r{label}: [{bar}] {percent * 100:6.2f}% | Time left: {minutes:02d}:{seconds:02d}"
        )
        sys.stdout.flush()

        if remaining <= 0:
            break

        wait_time = min(1.0, max(0.0, remaining))
        if settings.wait_for_update(wait_time):
            settings.clear_update_event()
            continue

    sys.stdout.write("\n")
    sys.stdout.flush()


def run_monitor(settings: MonitorSettings) -> None:
    """Continuous monitoring loop."""
    previous_prices: Dict[str, float] = {}
    iteration = 0

    while True:
        iteration += 1
        log(f"Starting price check #{iteration}...")

        try:
            prices = fetch_perpetual_prices()
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

        log(f"Received {len(prices)} perpetual futures prices from Bybit.")

        if not prices:
            log(
                "Bybit returned an empty list of perpetual futures pairs. This is unusual, so we will simply wait "
                "and try again."
            )
        elif previous_prices:
            triggered_alerts: list[dict[str, object]] = []

            # Notify about new or missing symbols
            current_symbols = set(prices)
            previous_symbols = set(previous_prices)
            new_symbols = sorted(current_symbols - previous_symbols)
            missing_symbols = sorted(previous_symbols - current_symbols)

            for symbol in new_symbols:
                base_symbol = extract_base_symbol(symbol)
                if base_symbol != symbol.upper():
                    log(
                        f"New perpetual pair detected: {symbol} (base asset {base_symbol}). It will be tracked from now on."
                    )
                else:
                    log(f"New perpetual pair detected: {symbol}. It will be tracked from now on.")
            for symbol in missing_symbols:
                log(
                    f"Perpetual pair missing this round: {symbol}. It may have been delisted or is temporarily unavailable."
                )

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
                    triggered_alerts.append(
                        {
                            "symbol": symbol,
                            "direction": direction,
                            "change_pct": change_pct,
                            "previous_price": previous_price,
                            "current_price": current_price,
                            "message": message,
                        }
                    )

            if triggered_alerts:
                if len(triggered_alerts) == 1:
                    notification_message = str(triggered_alerts[0]["message"])
                else:
                    pair_list = ", ".join(
                        str(alert["symbol"]) for alert in triggered_alerts
                    )
                    notification_message = (
                        f"{len(triggered_alerts)} pairs moved {PERCENT_THRESHOLD:.1f}% or more: {pair_list}. "
                        "Open the summary page for details."
                    )

                send_notification("Bybit Futures Alert", notification_message)
                open_alert_summary_page(triggered_alerts)
            else:
                log(
                    f"No price jumps reached the {PERCENT_THRESHOLD:.1f}% threshold during this cycle."
                )
        else:
            log("Baseline prices recorded. Alerts will begin after the next update.")

        previous_prices = prices
        wait_for_next_check(settings)


def main() -> None:
    """Entry point for the monitor."""
    log("Bybit perpetual futures monitor started.")
    log(
        "The script asks Bybit for every linear perpetual futures price and raises alerts when the "
        f"price moves +/-{PERCENT_THRESHOLD:.1f}% compared to the previous reading."
    )
    log("Press Ctrl+C at any time to stop the script safely.")
    if _plyer_notification is None:
        log("Desktop alerts need the 'plyer' package. Install it with: pip install plyer")
    settings = MonitorSettings(DEFAULT_WAIT_SECONDS)
    server, port = start_control_panel(settings)
    control_panel_url = f"http://127.0.0.1:{port}/"
    open_control_panel_in_edge(control_panel_url)

    try:
        run_monitor(settings)
    finally:
        log("Stopping the control panel server...")
        try:
            server.shutdown()
            server.server_close()
            control_thread = getattr(server, "control_panel_thread", None)
            if isinstance(control_thread, threading.Thread) and control_thread.is_alive():
                try:
                    control_thread.join(timeout=2)
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover - depends on interpreter shutdown
            log(f"Encountered an issue while stopping the control panel server: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user request. Goodbye!")
        sys.exit(0)
