"""Web UI wrapper for the Bybit trigger bounce trader."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from flask import Flask, redirect, render_template_string, request


APP = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "bounce_config.json"
BOUNCE_TRADERS_PATH = BASE_DIR.parent / "render" / "data" / "bounce_traders.json"
SESSION_LOG_DIR = BASE_DIR / "session_logs"
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "")

DEFAULT_CONFIG: Dict[str, str] = {
    "account_mode": "demo",
    "symbols": "BTCUSDT",
    "strategy": "EMA",
    "side": "Buy",
    "category": "linear",
    "trigger_by": "LastPrice",
    "interval": "1",
    "poll_seconds": "2",
    "ema_len": "9",
    "vwap_anchor": "session",  # session|week (UTC)
    "risk_mode": "fixed_qty",  # fixed_qty|percent
    "risk_pct": "1",
    "rr_ratio": "2",
    "default_qty": "0.001",
    "qty_map": "{}",
    "sl_ticks": "0",
    "min_amend_ticks": "1",
    "min_gap_ticks": "2",
}

_process_lock = threading.Lock()
_session_processes: Dict[str, subprocess.Popen[str]] = {}
_session_logs: Dict[str, object] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_symbols(raw_symbols: str) -> List[str]:
    seen = set()
    symbols: List[str] = []
    for token in str(raw_symbols or "").split(","):
        symbol = token.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _session_token(session_id: str) -> str:
    cleaned = "".join(ch for ch in str(session_id).upper() if ch.isalnum())
    return (cleaned or "GEN")[:6]


def _session_order_link_id(*, session_id: str, symbol: str, strategy: str, interval: str) -> str:
    strategy_token = (strategy or "ema").strip().lower()
    raw = f"BB{_session_token(session_id)}_{symbol}_{strategy_token}_{interval}"
    return raw[:36]


def _load_bounce_traders() -> List[Dict[str, object]]:
    if not BOUNCE_TRADERS_PATH.exists():
        return []
    try:
        payload = json.loads(BOUNCE_TRADERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    rows: List[Dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            rows.append(dict(entry))
    return rows


def _save_bounce_traders(items: List[Dict[str, object]]) -> None:
    BOUNCE_TRADERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOUNCE_TRADERS_PATH.write_text(json.dumps(items, indent=2, sort_keys=True), encoding="utf-8")


def _upsert_bounce_session(session: Dict[str, object]) -> None:
    with _process_lock:
        rows = _load_bounce_traders()
        sid = str(session.get("id") or "").strip()
        if not sid:
            return
        updated = False
        for idx, row in enumerate(rows):
            if str(row.get("id") or "").strip() == sid:
                rows[idx] = {**row, **session, "updated_at": _utc_now_iso()}
                updated = True
                break
        if not updated:
            session = dict(session)
            session.setdefault("created_at", _utc_now_iso())
            session["updated_at"] = _utc_now_iso()
            rows.append(session)
        _save_bounce_traders(rows)


def _set_session_stopped(session_id: str) -> None:
    with _process_lock:
        rows = _load_bounce_traders()
        changed = False
        for row in rows:
            if str(row.get("id") or "").strip() != session_id:
                continue
            row["status"] = "stopped"
            row["running"] = False
            row["show_in_open_orders"] = False
            row["stopped_at"] = _utc_now_iso()
            row["updated_at"] = _utc_now_iso()
            changed = True
            break
        if changed:
            _save_bounce_traders(rows)


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

    if not config.get("strategy"):
        strategies = str(config.get("strategies") or "").strip().lower()
        config["strategy"] = "VWAP" if "vwap" in strategies and "ema" not in strategies else "EMA"
    if not config.get("vwap_anchor"):
        config["vwap_anchor"] = "session"
    if not config.get("risk_mode"):
        config["risk_mode"] = "fixed_qty"
    if not config.get("sl_ticks"):
        config["sl_ticks"] = str(config.get("sl_pct") or "0")
    # Migrate legacy tp_ticks -> rr_ratio (best-effort)
    rr_raw = str(config.get("rr_ratio") or "").strip()
    if not rr_raw:
        rr_val = None
        try:
            sl = float(str(config.get("sl_ticks") or "0"))
            tp = float(str(config.get("tp_ticks") or "0"))
            if sl > 0 and tp > 0:
                rr_val = tp / sl
        except Exception:
            rr_val = None
        config["rr_ratio"] = str(rr_val if rr_val is not None else DEFAULT_CONFIG["rr_ratio"])
    else:
        try:
            rr = float(rr_raw)
            if rr <= 0:
                config["rr_ratio"] = "0"
        except Exception:
            config["rr_ratio"] = DEFAULT_CONFIG["rr_ratio"]
    side = (config.get("side") or "Buy").strip().title()
    config["side"] = side if side in {"Buy", "Sell"} else "Buy"
    return config


def _save_config(config: Dict[str, str]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _build_env(config: Dict[str, str], *, symbol: str, session_id: str) -> Dict[str, str]:
    env = os.environ.copy()
    env["BYBIT_ENV"] = "demo" if config["account_mode"] == "demo" else "live"
    env["BYBIT_CATEGORY"] = config["category"]
    env["BYBIT_TRIGGER_BY"] = config["trigger_by"]
    env["BYBIT_KLINE_INTERVAL"] = config["interval"]
    env["BOUNCE_POLL_SECONDS"] = config["poll_seconds"]
    env["BOUNCE_SYMBOLS"] = symbol
    env["BOUNCE_SESSION_ID"] = session_id

    strategy_ui = (config.get("strategy") or "EMA").strip().upper()
    env["BOUNCE_STRATEGIES"] = "ema" if strategy_ui == "EMA" else "vwap"
    env["BOUNCE_SIDE"] = (config.get("side") or "Buy").strip().title()
    env["EMA_LEN"] = config["ema_len"]
    env["BOUNCE_VWAP_ANCHOR"] = config.get("vwap_anchor", "session")

    env["BOUNCE_RISK_MODE"] = config.get("risk_mode", "fixed_qty")
    env["BOUNCE_RISK_PCT"] = config.get("risk_pct", "0")
    env["BOUNCE_ACCOUNT_BALANCE"] = "auto"
    env["BOUNCE_ACCOUNT_TYPE"] = "UNIFIED"
    env["BOUNCE_ACCOUNT_ASSET"] = "USDT"

    env["BOUNCE_DEFAULT_QTY"] = config["default_qty"]
    env["BOUNCE_QTY_MAP"] = config["qty_map"]
    env["BOUNCE_RR_RATIO"] = config.get("rr_ratio", "0")
    env["BOUNCE_SL_TICKS"] = config.get("sl_ticks", "0")
    env["BOUNCE_MIN_AMEND_TICKS"] = config["min_amend_ticks"]
    env["BOUNCE_MIN_GAP_TICKS"] = config["min_gap_ticks"]
    return env


def _refresh_sessions() -> None:
    ended: List[str] = []
    with _process_lock:
        for session_id, proc in list(_session_processes.items()):
            if proc.poll() is None:
                continue
            ended.append(session_id)
            _session_processes.pop(session_id, None)
            fh = _session_logs.pop(session_id, None)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
    for session_id in ended:
        _set_session_stopped(session_id)


def _running_sessions() -> List[Dict[str, object]]:
    _refresh_sessions()
    rows = _load_bounce_traders()
    active = [r for r in rows if bool(r.get("running"))]
    return sorted(active, key=lambda r: str(r.get("started_at") or ""), reverse=True)


def _start_session(config: Dict[str, str], symbol: str) -> str:
    session_id = f"bb-{uuid4().hex[:12]}"
    env = _build_env(config, symbol=symbol, session_id=session_id)
    cmd = [os.getenv("PYTHON", "python"), "-u", "bybit_trigger_bounce_trader.py"]

    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SESSION_LOG_DIR / f"{session_id}.log"
    log_file = log_path.open("a", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with _process_lock:
        _session_processes[session_id] = proc
        _session_logs[session_id] = log_file

    strategy_ui = (config.get("strategy") or "EMA").strip().upper()
    strategy_token = "ema" if strategy_ui == "EMA" else "vwap"
    side = (config.get("side") or "Buy").strip().title()
    account = "demo" if config.get("account_mode") == "demo" else "live"
    session_row: Dict[str, object] = {
        "id": session_id,
        "instrument": symbol,
        "side": side,
        "account": account,
        "category": config.get("category", "linear"),
        "strategy": strategy_token,
        "interval": config.get("interval", "1"),
        "order_link_id": _session_order_link_id(
            session_id=session_id,
            symbol=symbol,
            strategy=strategy_token,
            interval=config.get("interval", "1"),
        ),
        "show_in_open_orders": True,
        "seen_order": False,
        "status": "running",
        "running": True,
        "log_path": str(log_path),
        "started_at": _utc_now_iso(),
    }
    _upsert_bounce_session(session_row)
    return session_id


def _stop_session(session_id: str) -> bool:
    stopped = False
    with _process_lock:
        proc = _session_processes.pop(session_id, None)
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            stopped = True
        fh = _session_logs.pop(session_id, None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
    _set_session_stopped(session_id)
    return stopped


def _stop_all_sessions() -> int:
    _refresh_sessions()
    with _process_lock:
        session_ids = list(_session_processes.keys())
    for sid in session_ids:
        _stop_session(sid)
    return len(session_ids)


@APP.route("/", methods=["GET", "POST"])
def index() -> str:
    config = _load_config()
    error = ""
    message = ""
    if request.method == "POST":
        action = request.form.get("action")
        config = {
            "account_mode": request.form.get("account_mode", "demo"),
            "symbols": request.form.get("symbols", DEFAULT_CONFIG["symbols"]).strip(),
            "strategy": request.form.get("strategy", DEFAULT_CONFIG["strategy"]).strip(),
            "side": request.form.get("side", DEFAULT_CONFIG["side"]).strip(),
            "category": request.form.get("category", DEFAULT_CONFIG["category"]).strip(),
            "trigger_by": request.form.get("trigger_by", DEFAULT_CONFIG["trigger_by"]).strip(),
            "interval": request.form.get("interval", DEFAULT_CONFIG["interval"]).strip(),
            "poll_seconds": request.form.get("poll_seconds", DEFAULT_CONFIG["poll_seconds"]).strip(),
            "ema_len": request.form.get("ema_len", DEFAULT_CONFIG["ema_len"]).strip(),
            "vwap_anchor": request.form.get("vwap_anchor", DEFAULT_CONFIG["vwap_anchor"]).strip(),
            "risk_mode": request.form.get("risk_mode", DEFAULT_CONFIG["risk_mode"]).strip(),
            "risk_pct": request.form.get("risk_pct", DEFAULT_CONFIG["risk_pct"]).strip(),
            "rr_ratio": request.form.get("rr_ratio", DEFAULT_CONFIG["rr_ratio"]).strip(),
            "default_qty": request.form.get("default_qty", DEFAULT_CONFIG["default_qty"]).strip(),
            "qty_map": request.form.get("qty_map", DEFAULT_CONFIG["qty_map"]).strip(),
            "sl_ticks": request.form.get("sl_ticks", DEFAULT_CONFIG["sl_ticks"]).strip(),
            "min_amend_ticks": request.form.get("min_amend_ticks", DEFAULT_CONFIG["min_amend_ticks"]).strip(),
            "min_gap_ticks": request.form.get("min_gap_ticks", DEFAULT_CONFIG["min_gap_ticks"]).strip(),
        }
        side = (config.get("side") or "Buy").strip().title()
        config["side"] = side if side in {"Buy", "Sell"} else "Buy"
        _save_config(config)

        if action == "arm":
            confirm_arm = request.form.get("confirm_arm") == "on"
            confirm_live = request.form.get("confirm_live") == "on"
            if not confirm_arm:
                error = "Please confirm ARM before starting."
            elif config["account_mode"] == "live" and not confirm_live:
                error = "Live mode requires the additional confirmation checkbox."
            else:
                symbols = _normalize_symbols(config.get("symbols", ""))
                if not symbols:
                    error = "Please provide at least one valid symbol."
                else:
                    started: List[str] = []
                    for symbol in symbols:
                        started.append(_start_session(config, symbol))
                    message = f"Started {len(started)} bounce trader session(s)."
        elif action == "stop":
            stopped = _stop_all_sessions()
            message = f"Stopped {stopped} running session(s)."
        else:
            message = "Configuration saved."

    running_sessions = _running_sessions()
    running = len(running_sessions) > 0
    return render_template_string(
        FORM_HTML,
        config=config,
        running=running,
        running_sessions=running_sessions,
        error=error,
        message=message,
        app_root=APP_BASE_PATH,
    )


@APP.post("/sessions/<session_id>/stop")
def stop_session(session_id: str):
    _stop_session(session_id)
    return redirect(f"{APP_BASE_PATH}/")


@APP.route("/status")
def status() -> Dict[str, object]:
    config = _load_config()
    sessions = _running_sessions()
    return {
        "running": bool(sessions),
        "running_count": len(sessions),
        "sessions": sessions,
        "account_mode": config.get("account_mode", "demo"),
        "symbols": config.get("symbols", ""),
        "strategy": config.get("strategy", ""),
        "side": config.get("side", "Buy"),
    }


@atexit.register
def _cleanup_process() -> None:
    _stop_all_sessions()


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
      .danger { background: #ef4444; color: #fff; }
      .status-pill { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.35rem 0.75rem; border-radius: 999px; font-size: 0.85rem; font-weight: 700; }
      .status-running { background: rgba(34, 197, 94, 0.2); color: #86efac; }
      .status-stopped { background: rgba(239, 68, 68, 0.2); color: #fca5a5; }
      .notice { margin-top: 1rem; padding: 0.75rem 0.9rem; border-radius: 10px; font-weight: 600; }
      .notice.error { background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); color: #fecaca; }
      .notice.success { background: rgba(34, 197, 94, 0.2); border: 1px solid rgba(34, 197, 94, 0.4); color: #bbf7d0; }
      .checkbox-row { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.8rem; }
      .checkbox-row input { width: auto; }
      .warning { margin-top: 1rem; color: #fbbf24; }
      .session-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
      .session-table th, .session-table td { border-bottom: 1px solid #253046; padding: 0.55rem 0.4rem; text-align: left; font-size: 0.92rem; }
      .session-table th { color: #93c5fd; }
      .inline-form { margin: 0; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Bybit Trigger Bounce Trader</h1>
      <p>
        Status:
        <span class="status-pill {{ 'status-running' if running else 'status-stopped' }}">
          {{ 'RUNNING' if running else 'STOPPED' }}
        </span>
      </p>

      {% if error %}<div class="notice error">{{ error }}</div>{% endif %}
      {% if message %}<div class="notice success">{{ message }}</div>{% endif %}

      <form method="post" action="{{ app_root }}/">
        <div class="grid">
          <label>
            Account Mode
            <select name="account_mode" id="account_mode">
              <option value="demo" {% if config.account_mode == "demo" %}selected{% endif %}>Demo</option>
              <option value="live" {% if config.account_mode == "live" %}selected{% endif %}>Live</option>
            </select>
          </label>
          <label>
            Symbols (comma separated)
            <input name="symbols" value="{{ config.symbols }}" placeholder="BTCUSDT,ETHUSDT" />
          </label>
          <label>
            Strategy
            <select name="strategy" id="strategy">
              <option value="EMA" {% if config.strategy|upper == "EMA" %}selected{% endif %}>EMA</option>
              <option value="VWAP" {% if config.strategy|upper == "VWAP" %}selected{% endif %}>VWAP</option>
            </select>
          </label>
          <label>
            Side
            <select name="side" id="side">
              <option value="Buy" {% if config.side == "Buy" %}selected{% endif %}>Buy</option>
              <option value="Sell" {% if config.side == "Sell" %}selected{% endif %}>Sell</option>
            </select>
          </label>
          <label>
            Category
            <select name="category">
              <option value="linear" {% if config.category == "linear" %}selected{% endif %}>Linear</option>
              <option value="inverse" {% if config.category == "inverse" %}selected{% endif %}>Inverse</option>
              <option value="spot" {% if config.category == "spot" %}selected{% endif %}>Spot</option>
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
            Kline Interval
            <select name="interval">
              <option value="1" {% if config.interval == "1" %}selected{% endif %}>1m</option>
              <option value="3" {% if config.interval == "3" %}selected{% endif %}>3m</option>
              <option value="5" {% if config.interval == "5" %}selected{% endif %}>5m</option>
              <option value="15" {% if config.interval == "15" %}selected{% endif %}>15m</option>
              <option value="30" {% if config.interval == "30" %}selected{% endif %}>30m</option>
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
          <label>
            Default Qty
            <input name="default_qty" id="default_qty" value="{{ config.default_qty }}" />
          </label>
          <label>
            Risk Reward (RR) (0 to disable TP)
            <input name="rr_ratio" value="{{ config.rr_ratio }}" />
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
          <button class="danger" type="submit" name="action" value="stop">Stop All</button>
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

      <h2 style="margin-top:1.75rem;">Running bounce traders</h2>
      {% if running_sessions %}
      <table class="session-table">
        <thead>
          <tr>
            <th>Session</th>
            <th>Instrument</th>
            <th>Side</th>
            <th>Strategy</th>
            <th>Account</th>
            <th>Category</th>
            <th>Started</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {% for s in running_sessions %}
          <tr>
            <td>{{ s.id }}</td>
            <td>{{ s.instrument }}</td>
            <td>{{ s.side }}</td>
            <td>{{ s.strategy }}</td>
            <td>{{ s.account }}</td>
            <td>{{ s.category }}</td>
            <td>{{ s.started_at }}</td>
            <td>
              <form class="inline-form" method="post" action="{{ app_root }}/sessions/{{ s.id }}/stop">
                <button class="danger" type="submit">Stop</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p style="color:#94a3b8;">No active bounce trader sessions.</p>
      {% endif %}

      <script>
        function syncVisibility() {
          const strat = (document.getElementById('strategy') || {}).value || 'EMA';
          const riskMode = (document.getElementById('risk_mode') || {}).value || 'fixed_qty';

          const emaLen = document.getElementById('ema_len');
          const vwapAnchor = document.getElementById('vwap_anchor');
          if (emaLen) emaLen.closest('label').style.display = (strat === 'EMA') ? '' : 'none';
          if (vwapAnchor) vwapAnchor.closest('label').style.display = (strat === 'VWAP') ? '' : 'none';

          const showRisk = riskMode === 'percent';
          for (const id of ['risk_pct_label']) {
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
