"""Web UI wrapper for the Bybit trigger bounce trader."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, render_template_string, request


APP = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "bounce_config.json"
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "")

DEFAULT_CONFIG: Dict[str, str] = {
    "account_mode": "demo",
    "symbols": "BTCUSDT",
    # UI only exposes a single choice: EMA or VWAP.
    # The trader still accepts legacy comma-separated strategies.
    "strategy": "EMA",
    "category": "linear",
    "trigger_by": "LastPrice",
    # Bybit V5 interval enum: 1/3/5/15/30/60/120/240/360/720/D/W/M
    "interval": "1",
    "poll_seconds": "2",
    "ema_len": "9",
    "vwap_anchor": "session",  # session|week (UTC)
    # Risk / sizing
    "risk_mode": "fixed_qty",  # fixed_qty|percent
    "risk_pct": "1",
    "account_balance": "auto",  # auto|number
    "account_type": "UNIFIED",
    "account_asset": "USDT",
    "default_qty": "0.001",
    "qty_map": "{}",
    "tp_ticks": "0",
    "sl_ticks": "0",
    "min_amend_ticks": "1",
    "min_gap_ticks": "2",
}

_process_lock = threading.Lock()
_trader_process: Optional[subprocess.Popen[str]] = None


def _load_config() -> Dict[str, str]:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(DEFAULT_CONFIG)
    config = dict(DEFAULT_CONFIG)
    if isinstance(payload, dict):
        config.update({k: str(v) for k, v in payload.items()})

    # Backwards compatibility for older persisted keys.
    if not config.get("strategy"):
        strategies = str(config.get("strategies") or "").strip().lower()
        config["strategy"] = "VWAP" if "vwap" in strategies and "ema" not in strategies else "EMA"
    if not config.get("vwap_anchor"):
        config["vwap_anchor"] = "session"
    if not config.get("risk_mode"):
        config["risk_mode"] = "fixed_qty"
    if not config.get("tp_ticks"):
        config["tp_ticks"] = str(config.get("tp_pct") or "0")
    if not config.get("sl_ticks"):
        config["sl_ticks"] = str(config.get("sl_pct") or "0")
    return config


def _save_config(config: Dict[str, str]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _is_running() -> bool:
    global _trader_process
    if _trader_process is None:
        return False
    if _trader_process.poll() is None:
        return True
    _trader_process = None
    return False


def _build_env(config: Dict[str, str]) -> Dict[str, str]:
    env = os.environ.copy()
    env["BYBIT_ENV"] = "demo" if config["account_mode"] == "demo" else "live"
    env["BYBIT_CATEGORY"] = config["category"]
    env["BYBIT_TRIGGER_BY"] = config["trigger_by"]
    env["BYBIT_KLINE_INTERVAL"] = config["interval"]
    env["BOUNCE_POLL_SECONDS"] = config["poll_seconds"]
    env["BOUNCE_SYMBOLS"] = config["symbols"]
    # Map the UI dropdown to the strategy tokens understood by the trader.
    strategy_ui = (config.get("strategy") or "EMA").strip().upper()
    env["BOUNCE_STRATEGIES"] = "ema" if strategy_ui == "EMA" else "vwap"
    env["EMA_LEN"] = config["ema_len"]
    env["BOUNCE_VWAP_ANCHOR"] = config.get("vwap_anchor", "session")

    env["BOUNCE_RISK_MODE"] = config.get("risk_mode", "fixed_qty")
    env["BOUNCE_RISK_PCT"] = config.get("risk_pct", "0")
    env["BOUNCE_ACCOUNT_BALANCE"] = config.get("account_balance", "auto")
    env["BOUNCE_ACCOUNT_TYPE"] = config.get("account_type", "UNIFIED")
    env["BOUNCE_ACCOUNT_ASSET"] = config.get("account_asset", "USDT")

    env["BOUNCE_DEFAULT_QTY"] = config["default_qty"]
    env["BOUNCE_QTY_MAP"] = config["qty_map"]
    env["BOUNCE_TP_TICKS"] = config.get("tp_ticks", "0")
    env["BOUNCE_SL_TICKS"] = config.get("sl_ticks", "0")
    env["BOUNCE_MIN_AMEND_TICKS"] = config["min_amend_ticks"]
    env["BOUNCE_MIN_GAP_TICKS"] = config["min_gap_ticks"]
    return env


def _start_trader(config: Dict[str, str]) -> None:
    global _trader_process
    if _is_running():
        return
    env = _build_env(config)
    cmd = [os.getenv("PYTHON", "python"), "-u", "bybit_trigger_bounce_trader.py"]
    _trader_process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_trader() -> None:
    global _trader_process
    if _trader_process is None:
        return
    if _trader_process.poll() is None:
        _trader_process.terminate()
    _trader_process = None


@APP.route("/", methods=["GET", "POST"])
def index() -> str:
    config = _load_config()
    error = ""
    message = ""
    action = None
    if request.method == "POST":
        action = request.form.get("action")
        config = {
            "account_mode": request.form.get("account_mode", "demo"),
            "symbols": request.form.get("symbols", DEFAULT_CONFIG["symbols"]).strip(),
            "strategy": request.form.get("strategy", DEFAULT_CONFIG["strategy"]).strip(),
            "category": request.form.get("category", DEFAULT_CONFIG["category"]).strip(),
            "trigger_by": request.form.get("trigger_by", DEFAULT_CONFIG["trigger_by"]).strip(),
            "interval": request.form.get("interval", DEFAULT_CONFIG["interval"]).strip(),
            "poll_seconds": request.form.get("poll_seconds", DEFAULT_CONFIG["poll_seconds"]).strip(),
            "ema_len": request.form.get("ema_len", DEFAULT_CONFIG["ema_len"]).strip(),
            "vwap_anchor": request.form.get("vwap_anchor", DEFAULT_CONFIG["vwap_anchor"]).strip(),
            "risk_mode": request.form.get("risk_mode", DEFAULT_CONFIG["risk_mode"]).strip(),
            "risk_pct": request.form.get("risk_pct", DEFAULT_CONFIG["risk_pct"]).strip(),
            "account_balance": request.form.get("account_balance", DEFAULT_CONFIG["account_balance"]).strip(),
            "account_type": request.form.get("account_type", DEFAULT_CONFIG["account_type"]).strip(),
            "account_asset": request.form.get("account_asset", DEFAULT_CONFIG["account_asset"]).strip(),
            "default_qty": request.form.get("default_qty", DEFAULT_CONFIG["default_qty"]).strip(),
            "qty_map": request.form.get("qty_map", DEFAULT_CONFIG["qty_map"]).strip(),
            "tp_ticks": request.form.get("tp_ticks", DEFAULT_CONFIG["tp_ticks"]).strip(),
            "sl_ticks": request.form.get("sl_ticks", DEFAULT_CONFIG["sl_ticks"]).strip(),
            "min_amend_ticks": request.form.get("min_amend_ticks", DEFAULT_CONFIG["min_amend_ticks"]).strip(),
            "min_gap_ticks": request.form.get("min_gap_ticks", DEFAULT_CONFIG["min_gap_ticks"]).strip(),
        }
        _save_config(config)
        if action == "arm":
            confirm_arm = request.form.get("confirm_arm") == "on"
            confirm_live = request.form.get("confirm_live") == "on"
            if not confirm_arm:
                error = "Please confirm ARM before starting."
            elif config["account_mode"] == "live" and not confirm_live:
                error = "Live mode requires the additional confirmation checkbox."
            else:
                with _process_lock:
                    _start_trader(config)
                message = "Trader armed and running."
        elif action == "stop":
            with _process_lock:
                _stop_trader()
            message = "Trader stopped."
        else:
            message = "Configuration saved."

    running = _is_running()
    return render_template_string(
        FORM_HTML,
        config=config,
        running=running,
        error=error,
        message=message,
        app_root=APP_BASE_PATH,
    )


@APP.route("/status")
def status() -> Dict[str, object]:
    config = _load_config()
    return {
        "running": _is_running(),
        "account_mode": config.get("account_mode", "demo"),
        "symbols": config.get("symbols", ""),
        "strategy": config.get("strategy", ""),
    }


@atexit.register
def _cleanup_process() -> None:
    _stop_trader()


FORM_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Bybit Trigger Bounce Trader</title>
    <style>
      :root { color-scheme: light dark; }
      body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
      h1 { margin-top: 0; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 1100px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-top: 1rem; }
      label { display: flex; flex-direction: column; gap: 0.35rem; font-weight: 600; }
      input, select, textarea { padding: 0.55rem 0.65rem; border-radius: 10px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; }
      textarea { min-height: 80px; }
      .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1.5rem; }
      button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
      .primary { background: #22c55e; color: #052e16; }
      .secondary { background: #334155; color: #e2e8f0; }
      .danger { background: #dc2626; color: #fff; }
      .status { margin-top: 1rem; font-weight: 700; }
      .warning { color: #fca5a5; font-size: 0.95rem; }
      .success { color: #86efac; }
      .notice { color: #cbd5f5; }
      .checkbox-row { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Bybit Trigger Bounce Trader</h1>
      <p class="notice">Configure the bounce triggers and explicitly ARM the trader. Live mode requires an extra confirmation step. The app defaults to demo mode.</p>
      {% if error %}
        <div class="warning">{{ error }}</div>
      {% endif %}
      {% if message %}
        <div class="success">{{ message }}</div>
      {% endif %}
      <div class="status">Status: {{ "RUNNING" if running else "STOPPED" }}</div>
      <form method="post">
        <div class="grid">
          <label>
            Account Mode
            <select name="account_mode">
              <option value="demo" {% if config.account_mode == "demo" %}selected{% endif %}>Demo</option>
              <option value="live" {% if config.account_mode == "live" %}selected{% endif %}>Live</option>
            </select>
          </label>
          <label>
            Symbols (comma-separated)
            <input name="symbols" value="{{ config.symbols }}" />
          </label>
          <label>
            Strategy
            <select name="strategy" id="strategy">
              <option value="EMA" {% if config.strategy == "EMA" %}selected{% endif %}>EMA</option>
              <option value="VWAP" {% if config.strategy == "VWAP" %}selected{% endif %}>VWAP</option>
            </select>
          </label>
          <label>
            Category
            <select name="category">
              <option value="linear" {% if config.category == "linear" %}selected{% endif %}>Linear</option>
              <option value="spot" {% if config.category == "spot" %}selected{% endif %}>Spot</option>
              <option value="inverse" {% if config.category == "inverse" %}selected{% endif %}>Inverse</option>
              <option value="option" {% if config.category == "option" %}selected{% endif %}>Option</option>
            </select>
          </label>
          <label>
            Trigger By
            <select name="trigger_by">
              <option value="LastPrice" {% if config.trigger_by == "LastPrice" %}selected{% endif %}>LastPrice</option>
              <option value="MarkPrice" {% if config.trigger_by == "MarkPrice" %}selected{% endif %}>MarkPrice</option>
              <option value="IndexPrice" {% if config.trigger_by == "IndexPrice" %}selected{% endif %}>IndexPrice</option>
            </select>
          </label>
          <label>
            Interval
            <select name="interval" id="interval">
              <option value="1" {% if config.interval == "1" %}selected{% endif %}>1min</option>
              <option value="5" {% if config.interval == "5" %}selected{% endif %}>5min</option>
              <option value="15" {% if config.interval == "15" %}selected{% endif %}>15min</option>
              <option value="30" {% if config.interval == "30" %}selected{% endif %}>30min</option>
              <option value="60" {% if config.interval == "60" %}selected{% endif %}>1h</option>
              <option value="240" {% if config.interval == "240" %}selected{% endif %}>4h</option>
              <option value="D" {% if config.interval == "D" %}selected{% endif %}>Daily</option>
              <option value="W" {% if config.interval == "W" %}selected{% endif %}>Weekly</option>
              <option value="M" {% if config.interval == "M" %}selected{% endif %}>Monthly</option>
            </select>
          </label>
          <label>
            Poll Seconds
            <input name="poll_seconds" value="{{ config.poll_seconds }}" />
          </label>
          <label>
            EMA Length
            <input name="ema_len" id="ema_len" value="{{ config.ema_len }}" />
          </label>
          <label>
            VWAP Anchor
            <select name="vwap_anchor" id="vwap_anchor">
              <option value="session" {% if config.vwap_anchor == "session" %}selected{% endif %}>Session (UTC day)</option>
              <option value="week" {% if config.vwap_anchor == "week" %}selected{% endif %}>Week (UTC Mon)</option>
            </select>
          </label>
          <label>
            Risk Mode
            <select name="risk_mode" id="risk_mode">
              <option value="fixed_qty" {% if config.risk_mode == "fixed_qty" %}selected{% endif %}>Fixed Qty</option>
              <option value="percent" {% if config.risk_mode == "percent" %}selected{% endif %}>Risk %</option>
            </select>
          </label>
          <label id="risk_pct_label">
            Risk %
            <input name="risk_pct" id="risk_pct" value="{{ config.risk_pct }}" />
          </label>
          <label id="account_balance_label">
            Account Balance (auto or number)
            <input name="account_balance" id="account_balance" value="{{ config.account_balance }}" />
          </label>
          <label id="account_type_label">
            Account Type
            <input name="account_type" id="account_type" value="{{ config.account_type }}" />
          </label>
          <label id="account_asset_label">
            Account Asset
            <input name="account_asset" id="account_asset" value="{{ config.account_asset }}" />
          </label>
          <label>
            Default Qty
            <input name="default_qty" id="default_qty" value="{{ config.default_qty }}" />
          </label>
          <label>
            TP Ticks (0 to disable)
            <input name="tp_ticks" value="{{ config.tp_ticks }}" />
          </label>
          <label>
            SL Ticks (0 to disable)
            <input name="sl_ticks" value="{{ config.sl_ticks }}" />
          </label>
        </div>

        <details style="margin-top: 1rem;">
          <summary style="cursor:pointer; font-weight: 700;">Advanced</summary>
          <div class="grid" style="margin-top: 1rem;">
            <label>
              Qty Map (JSON) - optional per-symbol override (only used in Fixed Qty mode)
              <textarea name="qty_map">{{ config.qty_map }}</textarea>
            </label>
            <label>
              Min Amend Ticks
              <input name="min_amend_ticks" value="{{ config.min_amend_ticks }}" />
            </label>
            <label>
              Min Gap Ticks
              <input name="min_gap_ticks" value="{{ config.min_gap_ticks }}" />
            </label>
          </div>
        </details>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="save">Save</button>
          <button class="primary" type="submit" name="action" value="arm">ARM / START</button>
          <button class="danger" type="submit" name="action" value="stop">Stop</button>
        </div>
        <div class="checkbox-row">
          <input type="checkbox" name="confirm_arm" id="confirm_arm" />
          <label for="confirm_arm">I understand this will start placing orders once armed.</label>
        </div>
        <div class="checkbox-row">
          <input type="checkbox" name="confirm_live" id="confirm_live" />
          <label for="confirm_live">I confirm LIVE trading and accept the risk.</label>
        </div>
        <p class="warning">Tip: Keep account mode on demo until you intentionally switch to live.</p>
      </form>

      <script>
        function syncVisibility() {
          const strat = (document.getElementById('strategy') || {}).value || 'EMA';
          const riskMode = (document.getElementById('risk_mode') || {}).value || 'fixed_qty';

          // Strategy-specific controls
          const emaLen = document.getElementById('ema_len');
          const vwapAnchor = document.getElementById('vwap_anchor');
          if (emaLen) emaLen.closest('label').style.display = (strat === 'EMA') ? '' : 'none';
          if (vwapAnchor) vwapAnchor.closest('label').style.display = (strat === 'VWAP') ? '' : 'none';

          // Risk controls
          const showRisk = riskMode === 'percent';
          for (const id of ['risk_pct_label','account_balance_label','account_type_label','account_asset_label']) {
            const el = document.getElementById(id);
            if (el) el.style.display = showRisk ? '' : 'none';
          }
          const defaultQty = document.getElementById('default_qty');
          if (defaultQty) defaultQty.closest('label').style.display = showRisk ? 'none' : '';
        }
        document.getElementById('strategy')?.addEventListener('change', syncVisibility);
        document.getElementById('risk_mode')?.addEventListener('change', syncVisibility);
        syncVisibility();
      </script>
    </div>
  </body>
</html>
"""


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    try:
        from waitress import serve

        serve(APP, host=host, port=port)
    except Exception:
        APP.run(host=host, port=port)


if __name__ == "__main__":
    main()
