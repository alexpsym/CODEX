import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytz
import requests
from flask import Flask, jsonify, render_template_string, request

BASE_URL = os.getenv("OANDA_URL", "https://api-fxtrade.oanda.com/v3")
LOG_FILE = Path(__file__).with_name("trade_closure.log")
SETTINGS_PATH = Path(__file__).with_name("settings.json")
DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "trigger_weekday": 5,
    "cutoff_hour_dst": 5,
    "cutoff_hour_standard": 6,
    "check_interval_seconds": 60,
    "close_method": "positions",
    "dry_run": False,
    "instrument_allowlist": [],
}

BRISBANE_TZ = pytz.timezone("Australia/Brisbane")
NY_TZ = pytz.timezone("America/New_York")

app = Flask(__name__)
_status_lock = threading.Lock()


@dataclass
class LiquidationStatus:
    last_run_at: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


STATUS = LiquidationStatus()


def log(msg: str) -> None:
    timestamp = datetime.now().isoformat()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} - {msg}\n")


def bootstrap_log() -> None:
    log("fxweekend starting up.")


def get_oanda_env() -> Tuple[Optional[str], Optional[str], str]:
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    api_key = os.getenv("OANDA_API_KEY")
    base_url = os.getenv("OANDA_URL", BASE_URL)
    if not account_id or not api_key:
        log("Missing OANDA_ACCOUNT_ID or OANDA_API_KEY; skipping liquidation.")
    return account_id, api_key, base_url


def load_settings() -> Dict[str, Any]:
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return deepcopy(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log("settings.json was invalid; restoring defaults.")
        save_settings(DEFAULT_SETTINGS)
        return deepcopy(DEFAULT_SETTINGS)
    merged = deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update(data)
    save_settings(merged)
    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    payload = deepcopy(DEFAULT_SETTINGS)
    payload.update(settings)
    temp_path = SETTINGS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(SETTINGS_PATH)


def is_us_dst(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(pytz.utc)
    if current.tzinfo is None:
        current = pytz.utc.localize(current)
    ny = current.astimezone(NY_TZ)
    return bool(ny.dst())


def cutoff_hour(settings: Dict[str, Any], now: Optional[datetime] = None) -> int:
    return settings["cutoff_hour_dst"] if is_us_dst(now) else settings["cutoff_hour_standard"]


def compute_next_trigger(settings: Dict[str, Any], now: Optional[datetime] = None) -> str:
    current = now or datetime.now(BRISBANE_TZ)
    target_weekday = int(settings["trigger_weekday"])
    for day_offset in range(0, 8):
        candidate = current + timedelta(days=day_offset)
        candidate = candidate.replace(
            hour=cutoff_hour(settings, candidate),
            minute=0,
            second=0,
            microsecond=0,
        )
        if candidate.weekday() != target_weekday:
            continue
        if candidate <= current and day_offset == 0:
            continue
        return candidate.isoformat()
    return current.isoformat()


def _request(
    method: str, url: str, headers: Dict[str, str], json_payload: Optional[Dict[str, Any]] = None
) -> requests.Response:
    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_payload,
        timeout=15,
    )
    return response


def close_positions(
    account_id: str, api_key: str, base_url: str, settings: Dict[str, Any]
) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url}/accounts/{account_id}/openPositions"
    response = _request("GET", url, headers)
    if response.status_code != 200:
        log(f"Failed to fetch positions: {response.status_code} {response.text}")
        return "failed to fetch positions"
    positions = response.json().get("positions", [])
    if not positions:
        log("No open positions.")
        return "no open positions"
    allowlist = set(settings.get("instrument_allowlist") or [])
    for pos in positions:
        instrument = pos["instrument"]
        if allowlist and instrument not in allowlist:
            log(f"Skipping {instrument} (not in allowlist).")
            continue
        payload = {}
        if float(pos["long"]["units"]) != 0:
            payload["longUnits"] = "ALL"
        if float(pos["short"]["units"]) != 0:
            payload["shortUnits"] = "ALL"
        if settings.get("dry_run"):
            log(f"Dry run: would close {instrument} with {payload}.")
            continue
        close_url = f"{base_url}/accounts/{account_id}/positions/{instrument}/close"
        close_resp = _request("PUT", close_url, headers, payload)
        if close_resp.status_code in (200, 201):
            log(f"Closed {instrument}: {close_resp.text}")
        else:
            log(f"Failed to close {instrument}: {close_resp.status_code} {close_resp.text}")
    return "positions close attempted"


def close_trades(
    account_id: str, api_key: str, base_url: str, settings: Dict[str, Any]
) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url}/accounts/{account_id}/openTrades"
    response = _request("GET", url, headers)
    if response.status_code != 200:
        log(f"Failed to fetch trades: {response.status_code} {response.text}")
        return "failed to fetch trades"
    trades = response.json().get("trades", [])
    if not trades:
        log("No open trades.")
        return "no open trades"
    allowlist = set(settings.get("instrument_allowlist") or [])
    for trade in trades:
        instrument = trade["instrument"]
        if allowlist and instrument not in allowlist:
            log(f"Skipping {instrument} (not in allowlist).")
            continue
        trade_id = trade["id"]
        if settings.get("dry_run"):
            log(f"Dry run: would close trade {trade_id} ({instrument}).")
            continue
        close_url = f"{base_url}/accounts/{account_id}/trades/{trade_id}/close"
        close_resp = _request("PUT", close_url, headers, {"units": "ALL"})
        if close_resp.status_code in (200, 201):
            log(f"Closed trade {trade_id} ({instrument}): {close_resp.text}")
        else:
            log(
                f"Failed to close trade {trade_id} ({instrument}): "
                f"{close_resp.status_code} {close_resp.text}"
            )
    return "trades close attempted"


def run_liquidation(settings: Dict[str, Any], reason: str) -> Dict[str, Optional[str]]:
    account_id, api_key, base_url = get_oanda_env()
    if not account_id or not api_key:
        return {"result": "missing credentials", "error": "missing credentials"}

    try:
        log(f"Starting liquidation ({reason}).")
        close_method = settings.get("close_method", "positions")
        if close_method == "trades":
            result = close_trades(account_id, api_key, base_url, settings)
        else:
            result = close_positions(account_id, api_key, base_url, settings)
        return {"result": result, "error": None}
    except requests.RequestException as exc:
        log(f"Request error during liquidation: {exc}")
        return {"result": "request error", "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive logging
        log(f"Unexpected error during liquidation: {exc}")
        return {"result": "unexpected error", "error": str(exc)}


def update_status(result: Dict[str, Optional[str]]) -> None:
    with _status_lock:
        STATUS.last_run_at = datetime.now(BRISBANE_TZ).isoformat()
        STATUS.last_result = result.get("result")
        STATUS.last_error = result.get("error")


def scheduler_loop() -> None:
    last_week = None
    while True:
        settings = load_settings()
        interval = int(settings.get("check_interval_seconds", 60))
        if settings.get("enabled"):
            brisbane = datetime.now(BRISBANE_TZ)
            cutoff = cutoff_hour(settings, brisbane)
            if brisbane.weekday() == int(settings["trigger_weekday"]) and brisbane.hour >= cutoff:
                current_week = brisbane.isocalendar()[1]
                if last_week != current_week:
                    result = run_liquidation(settings, "scheduled")
                    update_status(result)
                    last_week = current_week
        time.sleep(max(5, interval))


@app.get("/")
def index() -> str:
    settings = load_settings()
    status = STATUS.to_dict()
    creds_present = bool(os.getenv("OANDA_ACCOUNT_ID") and os.getenv("OANDA_API_KEY"))
    next_trigger = compute_next_trigger(settings)
    template = """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>fxweekend settings</title>
        <style>
          body { font-family: sans-serif; margin: 2rem; }
          label { display: block; margin: 0.5rem 0; }
          input[type="number"], input[type="text"] { width: 10rem; }
          .section { margin-bottom: 1.5rem; }
          .status { background: #f4f4f4; padding: 1rem; border-radius: 6px; }
        </style>
      </head>
      <body>
        <h1>fxweekend settings</h1>
        <div class="section status">
          <strong>Status</strong>
          <div>Last run: {{ status.last_run_at or "never" }}</div>
          <div>Last result: {{ status.last_result or "n/a" }}</div>
          <div>Last error: {{ status.last_error or "none" }}</div>
          <div>Next trigger: {{ next_trigger }}</div>
          <div>Credentials present: {{ "yes" if creds_present else "no" }}</div>
        </div>
        <form method="post" action="api/config">
          <div class="section">
            <label>
              <input type="checkbox" name="enabled" {% if settings.enabled %}checked{% endif %}>
              Enabled
            </label>
            <label>
              Trigger weekday (0=Mon ... 6=Sun)
              <input type="number" name="trigger_weekday" value="{{ settings.trigger_weekday }}">
            </label>
            <label>
              Cutoff hour (DST)
              <input type="number" name="cutoff_hour_dst" value="{{ settings.cutoff_hour_dst }}">
            </label>
            <label>
              Cutoff hour (standard)
              <input type="number" name="cutoff_hour_standard" value="{{ settings.cutoff_hour_standard }}">
            </label>
            <label>
              Check interval seconds
              <input type="number" name="check_interval_seconds" value="{{ settings.check_interval_seconds }}">
            </label>
            <label>
              Close method
              <select name="close_method">
                <option value="positions" {% if settings.close_method == "positions" %}selected{% endif %}>
                  positions
                </option>
                <option value="trades" {% if settings.close_method == "trades" %}selected{% endif %}>
                  trades
                </option>
              </select>
            </label>
            <label>
              <input type="checkbox" name="dry_run" {% if settings.dry_run %}checked{% endif %}>
              Dry run
            </label>
            <label>
              Instrument allowlist (comma-separated)
              <input type="text" name="instrument_allowlist" value="{{ settings.instrument_allowlist | join(', ') }}">
            </label>
          </div>
          <button type="submit">Save settings</button>
        </form>
        <form method="post" action="api/run_now" style="margin-top: 1rem;">
          <button type="submit">Run now</button>
        </form>
      </body>
    </html>
    """
    return render_template_string(
        template,
        settings=settings,
        status=status,
        creds_present=creds_present,
        next_trigger=next_trigger,
    )


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return load_settings()


@app.post("/api/config")
def update_config() -> Dict[str, Any]:
    settings = load_settings()
    form = request.form
    settings["enabled"] = "enabled" in form
    settings["trigger_weekday"] = int(form.get("trigger_weekday", settings["trigger_weekday"]))
    settings["cutoff_hour_dst"] = int(form.get("cutoff_hour_dst", settings["cutoff_hour_dst"]))
    settings["cutoff_hour_standard"] = int(
        form.get("cutoff_hour_standard", settings["cutoff_hour_standard"])
    )
    settings["check_interval_seconds"] = int(
        form.get("check_interval_seconds", settings["check_interval_seconds"])
    )
    settings["close_method"] = form.get("close_method", settings["close_method"])
    settings["dry_run"] = "dry_run" in form
    allowlist_raw = form.get("instrument_allowlist", "")
    settings["instrument_allowlist"] = [
        item.strip() for item in allowlist_raw.split(",") if item.strip()
    ]
    save_settings(settings)
    log("Settings updated via web UI.")
    return settings


@app.post("/api/run_now")
def run_now() -> Dict[str, Any]:
    settings = load_settings()
    result = run_liquidation(settings, "manual")
    update_status(result)
    return {
        "ok": result["error"] is None,
        "result": result["result"],
        "error": result["error"],
    }


@app.get("/api/status")
def status() -> Dict[str, Any]:
    settings = load_settings()
    with _status_lock:
        status_payload = STATUS.to_dict()
    return {
        **status_payload,
        "next_trigger": compute_next_trigger(settings),
        "creds_present": bool(os.getenv("OANDA_ACCOUNT_ID") and os.getenv("OANDA_API_KEY")),
        "enabled": settings.get("enabled", False),
    }


@app.get("/api/self_test")
def self_test() -> Dict[str, Any]:
    account_id, api_key, base_url = get_oanda_env()
    if not account_id or not api_key:
        return {"ok": False, "error": "missing credentials"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url}/accounts/{account_id}"
    try:
        resp = _request("GET", url, headers)
        if resp.status_code == 200:
            return {"ok": True, "result": "connected"}
        return {"ok": False, "error": f"{resp.status_code} {resp.text}"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


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
