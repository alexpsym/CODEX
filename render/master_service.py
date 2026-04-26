"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import math
import atexit
import calendar
import asyncio
import threading
import base64
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import socket
import signal
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Callable
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
from shared.env_bootstrap import format_env_bootstrap_log, load_master_env
_MASTER_ENV_INFO = load_master_env(base_dir=BASE_DIR)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - optional in test envs
    matplotlib = None
    mdates = None
    plt = None
from fastapi import Body, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import httpx
import requests
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from starlette.responses import RedirectResponse

from bybit_credentials import resolve_bybit_credentials_for
from render.monthly_aud_revaluation import MonthlyAudRevalError, sync_monthly_aud_revaluation
from shared.bybit_option_resolver import resolve_option_by_target_risk
from shared.symbol_resolution import (
    is_likely_oanda_pair,
    norm_symbol,
    normalize_oanda_symbol_query,
    resolve_bybit_symbol_from_choices,
)
from shared.atomic_json import write_json_file
from render.dropbox_sync import download_bytes, list_excel_files, upload_bytes
from bybit_monitor import bybit_altcoin_monitor as bybit_monitor
from oanda_monitor import oanda_forex_monitor as oanda_monitor
from bybit_demo_tpsl_cache import (
    cache_bybit_demo_tpsl_request,
    load_bybit_demo_tpsl_cache,
    resolve_cached_bybit_demo_tpsl,
)

try:
    sys.path.insert(0, str(BASE_DIR / "oanda_history-clone"))
    import oanda_history as oanda_history_exporter
except Exception:  # pragma: no cover - optional dependency
    oanda_history_exporter = None
finally:
    sys.path.pop(0)
try:
    sys.path.insert(0, str(BASE_DIR / "bybithistory-clone"))
    import fetch_history as bybit_history_fetcher
except Exception:  # pragma: no cover - optional dependency
    bybit_history_fetcher = None
finally:
    sys.path.pop(0)
try:
    sys.path.insert(0, str(BASE_DIR / "coinspot-clone"))
    import coinspot_history as coinspot_history_exporter
except Exception:  # pragma: no cover - optional dependency
    coinspot_history_exporter = None
finally:
    sys.path.pop(0)


SKIP_DIRS = {
    "render",
    "shared",
    "mt5-clone",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "env",
    "youtube",
    "LEDGER-clone",
    "viddl-clone",
}
SKIP_DIRS_NORMALIZED = {name.casefold() for name in SKIP_DIRS}
SKIP_FILES = {"__init__.py"}
HIDDEN_SCRIPTS = {
    "render",
    "shared",
    "oanda_swap_rates",
    "oanda_swaprates",
    "oanda-swaprates",
    "oanda-swap-rates",
    "oanda-swap-rates-clone",
    "oanda_swap_rates_clone",
    "swap_rates_oanda",
    "swap-rates-oanda",
    "swap_rates",
    "swap-rates",
    "payslip_audit",
}
RETIRED_SCRIPT_NAMES = {
    "cryptocalculator-clone",
    "oanda-calculator-clone",
    "oanda_swap_rates",
    "oanda_swaprates",
    "oanda-swaprates",
    "oanda-swap-rates",
    "oanda-swap-rates-clone",
    "oanda_swap_rates_clone",
    "swap_rates_oanda",
    "swap-rates-oanda",
    "swap_rates",
    "swap-rates",
    "payslip_audit",
    "crypto-scanner-clone",
    "cryptoscanner-clone",
    "crypto_scanner_clone",
    "fxscanner-oanda-clone",
    "fxscanner_oanda_clone",
    "fx-scanner-oanda-clone",
    "scanner",
}


LOCAL_ONLY_SCRIPTS = {"bybit_monitor", "oanda_monitor"}
BYBIT_RUNTIME_STATUS_PATH = BASE_DIR / "bybit_monitor" / "runtime_status.json"
OANDA_RUNTIME_STATUS_PATH = BASE_DIR / "oanda_monitor" / "runtime_status.json"
SCANNER_HEARTBEAT_GRACE_SECONDS = 30
SCANNER_LOCAL_UI_MODE = os.getenv("SCANNER_LOCAL_UI_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_RENDER_ALLOWED_APPS = "calculator-webhook,pending-webhooks,fxweekend-clone,bybit_trigger_bounce_trader"
DEFAULT_LOCAL_ALLOWED_APPS = "bybit_monitor,oanda_monitor,bybithistory-clone,oanda_history-clone,coinspot-clone,open-orders,ivindicator-clone"


def _is_render_env() -> bool:
    return bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_SERVICE_ID")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("RENDER_EXTERNAL_HOSTNAME")
    )


def _is_scanner_local_ui_mode() -> bool:
    return SCANNER_LOCAL_UI_MODE


def _parse_allowed_apps(raw: str) -> Set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _resolve_app_profile() -> str:
    requested = str(os.getenv("APP_PROFILE") or "").strip().lower()
    journal_only = str(os.getenv("TRADING_JOURNAL_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}
    if requested == "journal" or journal_only:
        return "journal"
    if requested in {"render", "local"}:
        return requested
    return "render" if _is_render_env() else "local"


APP_PROFILE = _resolve_app_profile()
RENDER_ALLOWED_APPS = _parse_allowed_apps(os.getenv("RENDER_ALLOWED_APPS", DEFAULT_RENDER_ALLOWED_APPS))
LOCAL_ALLOWED_APPS = _parse_allowed_apps(os.getenv("LOCAL_ALLOWED_APPS", DEFAULT_LOCAL_ALLOWED_APPS))
LOCAL_ONLY_DISABLED_MESSAGE = "This app is local-only to reduce Render bandwidth. Run run_local_master_control.bat."
LOCAL_ONLY_APP_NAMES = {
    "bybit_monitor",
    "oanda_monitor",
    "bybithistory-clone",
    "oanda_history-clone",
    "coinspot-clone",
    "open-orders",
    "ivindicator-clone",
}
LOCAL_ONLY_PATH_PREFIXES = (
    "/merged/history",
    "/merged/monitor",
    "/bybit-history",
    "/oanda-history",
    "/coinspot-history",
    "/trading-journal",
    "/merged/open-orders",
    "/api/bybit-history",
    "/api/oanda-history",
    "/api/coinspot-history",
    "/api/trading-journal",
    "/api/open-orders",
)


def _profile_allows_script(script_name: str) -> bool:
    name = str(script_name or "").strip()
    if not name:
        return False
    if APP_PROFILE == "render":
        return name in RENDER_ALLOWED_APPS
    if APP_PROFILE == "journal":
        return False
    return name in LOCAL_ALLOWED_APPS


def _profile_main_buttons() -> List[Dict[str, object]]:
    buttons: List[Dict[str, object]] = [
        {"id": "calculator", "name": "calculator", "label": "Calculator", "open_url": "/merged/calculator", "dashboard_main_view": True},
    ]
    if APP_PROFILE == "render":
        if "fxweekend-clone" in RENDER_ALLOWED_APPS:
            buttons.append({"id": "fxweekend", "name": "fxweekend", "label": "FX Weekend", "open_url": "/apps/fxweekend-clone", "dashboard_main_view": True})
        if "bybit_trigger_bounce_trader" in RENDER_ALLOWED_APPS:
            buttons.append({"id": "bounce-trader", "name": "bounce-trader", "label": "Bounce Trader", "open_url": "/merged/bounce-trader", "dashboard_main_view": True})
    elif APP_PROFILE == "local":
        buttons.extend(
            [
                {"id": "open-orders", "name": "open-orders", "label": "Open Orders and Positions", "open_url": "/merged/open-orders", "dashboard_main_view": True},
                {"id": "history", "name": "history", "label": "History", "open_url": "/merged/history", "dashboard_main_view": True},
                {"id": "monitor", "name": "monitor", "label": "Scanner", "open_url": "/merged/monitor", "dashboard_main_view": True},
            ]
        )
    return buttons


def _profile_merged_source_names() -> Set[str]:
    names = {"bybit_trigger_bounce_trader", "fxweekend-clone"}
    if APP_PROFILE == "local":
        names.update(
            {
                "bybithistory-clone",
                "oanda_history-clone",
                "coinspot-clone",
                "bybit_monitor",
                "oanda_monitor",
            }
        )
    return names


def _local_only_disabled_response(path: str, *, as_json: bool = False) -> Response:
    detail = f"{LOCAL_ONLY_DISABLED_MESSAGE} (path: {path})"
    if as_json:
        return JSONResponse({"detail": detail, "status": "disabled", "path": path}, status_code=410)
    return PlainTextResponse(detail, status_code=410)


def _render_blocks_path(path: str) -> bool:
    if APP_PROFILE != "render":
        return False
    normalized = str(path or "").strip() or "/"
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in LOCAL_ONLY_PATH_PREFIXES):
        return True
    if normalized.startswith("/apps/"):
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 2 and parts[1] in LOCAL_ONLY_APP_NAMES:
            return True
    return False


def _env_source_hint() -> str:
    loaded = _MASTER_ENV_INFO.get("loaded_file") or "<none>"
    checked = _MASTER_ENV_INFO.get("checked_files") or "<none>"
    return f"env_loaded_file={loaded}; env_checked={checked}"


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pid_is_alive(pid: object) -> bool:
    try:
        pid_int = int(pid)
    except Exception:
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def _scanner_status_payload(status_path: Path) -> dict[str, object]:
    if not status_path.exists():
        return {"ui_status": "stopped", "display_status": "Stopped", "reason": "missing"}
    payload: object | None = None
    read_error: Exception | None = None
    for attempt in range(3):
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            read_error = None
            break
        except (PermissionError, json.JSONDecodeError) as exc:
            read_error = exc
            if attempt < 2:
                time.sleep(0.02)
                continue
            break
        except Exception as exc:
            read_error = exc
            break
    if read_error is not None:
        return {
            "ui_status": "unavailable",
            "display_status": "Status unavailable",
            "reason": "malformed",
            "error": str(read_error),
        }
    if not isinstance(payload, dict):
        return {
            "ui_status": "unavailable",
            "display_status": "Status unavailable",
            "reason": "malformed",
            "error": "Runtime status must be a JSON object.",
        }

    running = bool(payload.get("running"))
    wait_seconds = int(payload.get("wait_seconds") or 0)
    stale_after = int(payload.get("heartbeat_timeout_seconds") or max(60, wait_seconds * 2 + SCANNER_HEARTBEAT_GRACE_SECONDS))
    hb_dt = _parse_iso_datetime(payload.get("last_heartbeat_at"))
    now = datetime.now(timezone.utc)
    heartbeat_fresh = bool(hb_dt and (now - hb_dt).total_seconds() <= stale_after)
    pid_alive = _pid_is_alive(payload.get("pid")) if payload.get("pid") is not None else True

    if running and heartbeat_fresh and pid_alive:
        ui_status = "running"
        display = "Running"
    else:
        ui_status = "stopped"
        display = "Stopped"
    result = dict(payload)
    result.update(
        {
            "ui_status": ui_status,
            "display_status": display,
            "heartbeat_fresh": heartbeat_fresh,
            "pid_alive": pid_alive,
            "stale_after_seconds": stale_after,
        }
    )
    return result


def _scanner_runtime_is_live(script_name: str) -> bool:
    path = BYBIT_RUNTIME_STATUS_PATH if script_name == "bybit_monitor" else OANDA_RUNTIME_STATUS_PATH
    payload = _scanner_status_payload(path)
    return payload.get("ui_status") == "running"

MAX_LOG_LINES = 400
OANDA_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "oanda-history"
BYBIT_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "bybit-history"
COINSPOT_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "coinspot-history"
PENDING_WEBHOOKS_PATH = BASE_DIR / "render" / "data" / "pending_webhooks.json"
TRADE_CONTEXTS_PATH = BASE_DIR / "render" / "data" / "trade_contexts.json"
WEBHOOK_ATTEMPTS_PATH = BASE_DIR / "render" / "data" / "webhook_attempts.json"
BOUNCE_TRADERS_PATH = BASE_DIR / "render" / "data" / "bounce_traders.json"
WATCHLIST_PATH = BASE_DIR / "render" / "data" / "watchlist.json"
TRADING_JOURNAL_PATH = BASE_DIR / "render" / "data" / "trading_journal.json"
TRADING_JOURNAL_STATE_PATH = BASE_DIR / "render" / "data" / "trading_journal_state.json"
TRADING_JOURNAL_SYNC_STATE_PATH = BASE_DIR / "render" / "data" / "trading_journal_sync_state.json"
TRADING_JOURNAL_IMPORT_CACHE_PATH = BASE_DIR / "render" / "data" / "trading_journal_import_cache.json"
MONTHLY_AUD_REVALUATION_PATH = BASE_DIR / "render" / "data" / "monthly_aud_revaluation.json"
MONTHLY_AUD_REVALUATION_STATE_PATH = BASE_DIR / "render" / "data" / "monthly_aud_revaluation_state.json"
OANDA_FILL_STATE_PATH = BASE_DIR / "render" / "data" / "oanda_fill_state.json"
TRADING_JOURNAL_IMPORT_CACHE_VERSION = 2
TRADING_JOURNAL_DROPBOX_FOLDER = os.getenv(
    "TRADING_JOURNAL_DROPBOX_FOLDER", "/master_control"
).strip()
TRADING_JOURNAL_LOCAL_DIR = Path(
    os.getenv("TRADING_JOURNAL_LOCAL_DIR", str(BASE_DIR / "journal"))
).expanduser()
TRADING_JOURNAL_LOCAL_DIR_EXPLICIT = "TRADING_JOURNAL_LOCAL_DIR" in os.environ
TRADING_JOURNAL_ENABLE_LOCAL_IMPORT = os.getenv("TRADING_JOURNAL_ENABLE_LOCAL_IMPORT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRADING_JOURNAL_SOURCE = str(os.getenv("TRADING_JOURNAL_SOURCE", "both") or "both").strip().lower()
TRADING_JOURNAL_SYNC_STATE: Dict[str, object] = {
    "running": False,
    "progress": 0,
    "message": "",
    "ok": None,
    "error": None,
    "result": None,
    "started_at": None,
    "finished_at": None,
    "updated_at": None,
}
TRADING_JOURNAL_SYNC_LOCK = threading.Lock()
TRADING_JOURNAL_IMPORT_DIAGNOSTICS: Dict[str, object] = {
    "rows_total": 0,
    "rows_by_source": {},
    "rows_by_asset_class": {},
    "last_sync": {},
    "local_workbooks_seen": 0,
    "dropbox_workbooks_seen": 0,
    "errors": [],
}
BYBIT_DEMO_TEMPLATE_NAME = "Bybit-UM-USDTPerp-TradeHistory-template.csv"
BYBIT_DEMO_WORKBOOK_NAME = "Bybit Demo.xlsx"
BYBIT_DEMO_WORKBOOK_SHEET = "Trades"
BYBIT_DEMO_WORKBOOK_COLUMNS = [
    "opening_time",
    "closing_time",
    "type_buy_sell",
    "symbol",
    "size_quantity",
    "entry_price",
    "closing_price",
    "stop_loss",
    "take_profit",
    "commission",
    "net_profit",
    "balance_after_trade",
    "timeframe",
    "is_test_trade",
    "currency",
    "notes",
    "order_id",
    "fill_count",
    "source",
]
BYBIT_DEMO_WORKBOOK_TEXT_COLUMNS = {
    "opening_time",
    "closing_time",
    "type_buy_sell",
    "symbol",
    "timeframe",
    "is_test_trade",
    "currency",
    "notes",
    "order_id",
    "source",
}
BYBIT_DEMO_WORKBOOK_NUMERIC_COLUMNS = {
    "size_quantity",
    "entry_price",
    "closing_price",
    "stop_loss",
    "take_profit",
    "commission",
    "net_profit",
    "balance_after_trade",
    "fill_count",
}
ENABLE_BYBIT_DEMO_JOURNAL = os.getenv("ENABLE_BYBIT_DEMO_JOURNAL", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ENABLE_BYBIT_DEMO_CLOSED_PNL_POLL = os.getenv(
    "ENABLE_BYBIT_DEMO_CLOSED_PNL_POLL", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_TRADE_CHART_CACHE: Dict[str, Dict[str, object]] = {}
_TRADE_CHART_CACHE_TTL_SECONDS = float(os.getenv("TRADE_CHART_CACHE_TTL_SECONDS", "600") or 600)
_TRADE_CHART_CACHE_VERSION = "v2"


def _sync_state_snapshot() -> Dict[str, object]:
    data = _load_json_file(TRADING_JOURNAL_SYNC_STATE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    merged = dict(TRADING_JOURNAL_SYNC_STATE)
    merged.update(data)
    return merged


def _set_trading_journal_sync_state(**updates: object) -> None:
    with TRADING_JOURNAL_SYNC_LOCK:
        merged = _sync_state_snapshot()
        merged.update(updates)
        merged["updated_at"] = _utc_now_iso()
        TRADING_JOURNAL_SYNC_STATE.update(merged)
        _save_json_file(TRADING_JOURNAL_SYNC_STATE_PATH, merged)


def _record_bybit_demo_sync_status(**updates: object) -> None:
    state = _load_trading_journal_state()
    demo_state = state.get("bybit_demo_sync")
    merged = demo_state if isinstance(demo_state, dict) else {}
    merged.update(updates)
    merged["updated_at"] = _utc_now_iso()
    state["bybit_demo_sync"] = merged
    _save_trading_journal_state(state)


def _record_daily_trade_sync_status(**updates: object) -> None:
    state = _load_trading_journal_state()
    daily_state = state.get("daily_trade_sync")
    merged = daily_state if isinstance(daily_state, dict) else {}
    merged.update(updates)
    merged["updated_at"] = _utc_now_iso()
    state["daily_trade_sync"] = merged
    _save_trading_journal_state(state)


def _default_journal_diagnostics() -> Dict[str, object]:
    return {
        "rows_total": 0,
        "rows_by_source": {},
        "rows_by_asset_class": {},
        "last_sync": {},
        "local_workbooks_seen": 0,
        "dropbox_workbooks_seen": 0,
        "duplicate_rows_dropped": 0,
        "source_duplicate_rows_dropped": 0,
        "dedupe_groups": 0,
        "ignored_local_workbooks": [],
        "quarantined_rows": 0,
        "errors": [],
    }


def _set_trading_journal_diagnostics(payload: Optional[Dict[str, object]]) -> None:
    base = _default_journal_diagnostics()
    if isinstance(payload, dict):
        base.update(payload)
    global TRADING_JOURNAL_IMPORT_DIAGNOSTICS
    TRADING_JOURNAL_IMPORT_DIAGNOSTICS = base


TRADING_JOURNAL_DROPBOX_RECURSIVE = os.getenv(
    "TRADING_JOURNAL_DROPBOX_RECURSIVE", "0"
).strip().lower() in {"1", "true", "yes", "on"}
WATCHLIST_MAX_ITEMS = 50
DROPBOX_SYNC_ENABLED = os.getenv("DROPBOX_SYNC_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DROPBOX_BACKUP_PATH = os.getenv(
    "DROPBOX_BACKUP_PATH", "/codex/master_control_backup.json"
).strip()
DROPBOX_SYNC_DEBOUNCE_SECONDS = float(
    os.getenv("DROPBOX_SYNC_DEBOUNCE_SECONDS", "2")
)
LIMIT_CANCEL_POLL_SECONDS = float(
    os.getenv("LIMIT_CANCEL_POLL_SECONDS", "5")
)
FILL_ALERT_POLL_SECONDS = float(
    os.getenv("FILL_ALERT_POLL_SECONDS", "8")
)
BYBIT_DEMO_CLOSED_PNL_POLL_SECONDS = float(
    os.getenv("BYBIT_DEMO_CLOSED_PNL_POLL_SECONDS", "300")
)
DAILY_TRADE_SYNC_ENABLED = os.getenv("DAILY_TRADE_SYNC_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DAILY_TRADE_SYNC_HOUR = max(0, min(23, int(os.getenv("DAILY_TRADE_SYNC_HOUR", "0"))))
DAILY_TRADE_SYNC_MINUTE = max(0, min(59, int(os.getenv("DAILY_TRADE_SYNC_MINUTE", "10"))))
DAILY_TRADE_SYNC_TIMEZONE = os.getenv("DAILY_TRADE_SYNC_TIMEZONE", "Australia/Brisbane").strip() or "Australia/Brisbane"
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Australia/Brisbane").strip() or "Australia/Brisbane"
OUTBOUND_METRICS_LOG_SECONDS = float(
    os.getenv("OUTBOUND_METRICS_LOG_SECONDS", "300")
)
WEB_APPS = {
    "bybit_trigger_bounce_trader",
    "bybithistory-clone",
    "ivindicator-clone",
    "fxweekend-clone",
}
STANDALONE_SCRIPTS = {
    "bybit-alert-clone",
    "bybithistory-clone",
    "coinspot-clone",
    "ivindicator-clone",
    "fxweekend-clone",
    "oanda_history-clone",
}

ENTRY_OVERRIDES = {
    "LEDGER-clone": ["process_entries.py"],
    "bybit_monitor": ["bybit_altcoin_monitor.py"],
    "bybithistory-clone": ["app.py"],
    "coinspot-clone": ["coinspot_history.py"],
    "fxweekend-clone": ["liquidate.py"],
    "ivindicator-clone": ["ivweb.py", "ivapp.py", "ivindicator.py"],
    "oanda_monitor": ["oanda_forex_monitor.py"],
    "oanda_history-clone": ["oanda_history.py"],
}

LOG_FILE_OVERRIDES: Dict[str, Path] = {
    "fxweekend-clone": BASE_DIR / "fxweekend-clone" / "trade_closure.log",
}

BYBIT_SETTINGS_PATH = bybit_monitor.SETTINGS_PATH

OANDA_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
BYBIT_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
COINSPOT_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
PENDING_WEBHOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
TRADING_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
MONTHLY_AUD_REVALUATION_PATH.parent.mkdir(parents=True, exist_ok=True)

WEBHOOK_ATTEMPTS_MAX_ITEMS = int(os.getenv("WEBHOOK_ATTEMPTS_MAX_ITEMS", "300") or "300")

_WATCHLIST_CACHE: Optional[List[str]] = None
_TRADING_JOURNAL_CACHE: Optional[List[Dict[str, object]]] = None
_TRADING_JOURNAL_ROWS_LOCK = threading.RLock()
_STARTUP_STATE_RESTORE_DONE = asyncio.Event()
_DROPBOX_UPLOAD_TASK: Optional[asyncio.Task] = None
_DROPBOX_UPLOAD_TIMER: Optional[threading.Timer] = None
_DROPBOX_UPLOAD_TIMER_LOCK = threading.Lock()
_BYBIT_EXEC_LAST_SEEN: Dict[str, int] = {}
_BYBIT_CLOSED_PNL_LAST_SEEN: Dict[str, Optional[int]] = {"demo": None, "live": None}
_BYBIT_CLOSED_PNL_SYNC_LOCK: Dict[str, asyncio.Lock] = {"demo": asyncio.Lock(), "live": asyncio.Lock()}
_BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT: Dict[str, float] = {"demo": 0.0, "live": 0.0}
_BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS = float(
    os.getenv("BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS", "30")
)
_DAILY_TRADE_SYNC_LOCK = asyncio.Lock()
_BYBIT_DEMO_WORKBOOK_LOCK = threading.Lock()
_OANDA_TX_LAST_SEEN: Dict[str, str] = {}
_OANDA_FILL_BACKOFF_UNTIL: Dict[str, float] = {}
_OANDA_FILL_FAILURES: Dict[str, int] = {}
_OANDA_OPEN_TRADE_LEGS: Dict[str, Dict[str, Dict[str, object]]] = {"live": {}, "demo": {}}
_OANDA_FILL_DIAGNOSTICS: Dict[str, Dict[str, object]] = {}
_OANDA_ACCOUNTS_CACHE: Dict[str, Tuple[float, List[Dict[str, object]]]] = {}
_OANDA_ACCOUNTS_CACHE_TTL_SECONDS = 20.0
_OANDA_SPECS_CACHE: Dict[str, Tuple[float, Dict[str, object]]] = {}
_OANDA_SPECS_CACHE_TTL_SECONDS = 30.0
_OANDA_TRANSIENT_HTTP_STATUS_CODES = {
    408, 425, 429,
    500, 502, 503, 504,
    520, 521, 522, 523, 524,
}
_OANDA_INACTIVITY_CACHE: Dict[str, object] = {
    "expires_at": 0.0,
    "payload": None,
    "status_code": 200,
}
_OANDA_INACTIVITY_CACHE_TTL_SECONDS = 45.0
_OANDA_INACTIVITY_ERROR_CACHE_TTL_SECONDS = 10.0
_OPEN_ORDERS_CACHE_LOCK = asyncio.Lock()
_OPEN_ORDERS_CACHE_TTL_SECONDS = 60.0
_OPEN_ORDERS_CACHE: Dict[str, object] = {
    "expires_at": 0.0,
    "last_success_at": None,
    "payload": None,
    "version": 0,
}


class BybitOrderRejected(RuntimeError):
    def __init__(
        self,
        *,
        ret_code: object,
        ret_msg: object,
        ret_ext_info: object,
        result: object,
        request_body: Dict[str, object],
        http_status: Optional[int],
        response_body: Optional[Dict[str, object]] = None,
    ) -> None:
        self.ret_code = ret_code
        self.ret_msg = str(ret_msg or "").strip() or "Unknown Bybit rejection"
        self.ret_ext_info = ret_ext_info if isinstance(ret_ext_info, dict) else {}
        self.result = result if isinstance(result, dict) else {}
        self.request_body = dict(request_body) if isinstance(request_body, dict) else {}
        self.http_status = int(http_status) if http_status is not None else None
        self.response_body = (
            dict(response_body) if isinstance(response_body, dict) else {}
        )
        message = (
            f"Bybit order rejected retCode={self.ret_code} retMsg={self.ret_msg} "
            f"http_status={self.http_status}"
        )
        super().__init__(message)


class OandaUpstreamHTTPError(ValueError):
    def __init__(
        self,
        *,
        status_code: int,
        mode: str,
        account_id: str,
        endpoint: str,
        body_summary: str,
        transient: bool,
    ) -> None:
        self.status_code = status_code
        self.mode = mode
        self.account_id = account_id
        self.endpoint = endpoint
        self.body_summary = body_summary
        self.transient = transient
        super().__init__(
            f"OANDA upstream HTTP {status_code} mode={mode} "
            f"account={account_id} endpoint={endpoint}: {body_summary}"
        )


def _invalidate_open_orders_cache() -> None:
    _OPEN_ORDERS_CACHE["payload"] = None
    _OPEN_ORDERS_CACHE["expires_at"] = 0.0
    _OPEN_ORDERS_CACHE["version"] = int(_OPEN_ORDERS_CACHE.get("version") or 0) + 1
_BYBIT_SYMBOL_LIST_CACHE: Dict[str, Dict[str, object]] = {
    "linear": {"ts": 0.0, "symbols": []},
    "spot": {"ts": 0.0, "symbols": []},
    "inverse": {"ts": 0.0, "symbols": []},
}
_BYBIT_SYMBOL_LIST_CACHE_TTL_SECONDS = float(
    os.getenv("BYBIT_SYMBOL_LIST_CACHE_TTL_SECONDS", "900")
)
_BYBIT_CLOSED_PNL_RECOVERY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
_BYBIT_CLOSED_PNL_RECOVERY_SAFETY_MARGIN_MS = 60_000


def _normalize_watchlist(items: Iterable[object]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in items:
        symbol = str(item or "").strip().upper()
        if not symbol:
            continue
        if len(symbol) == 6 and symbol.isalpha() and is_likely_oanda_pair(symbol):
            symbol = f"{symbol[:3]}_{symbol[3:]}"
        if symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
        if len(normalized) >= WATCHLIST_MAX_ITEMS:
            break
    return normalized


def _norm_symbol(s: str) -> str:
    return norm_symbol(s)


def _normalize_instrument_key(value: object) -> str:
    return _norm_symbol(str(value or ""))


def _is_likely_fx_pair(value: str) -> bool:
    return is_likely_oanda_pair(value)


def _oanda_aliases(name: str, display_name: Optional[str] = None) -> set[str]:
    aliases = set()
    if name:
        aliases.add(name)
        aliases.add(name.replace("_", ""))
        aliases.add(name.replace("_", "/"))
    if display_name:
        aliases.add(display_name)
        aliases.add(display_name.replace("/", ""))
        aliases.add(display_name.replace("/", "_"))
        aliases.add(display_name.replace(" ", ""))
    return {_norm_symbol(x) for x in aliases if x}


def resolve_oanda_instrument(user_query: str, instruments: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    qn = _norm_symbol(user_query)
    if not qn:
        return None

    for inst in instruments:
        name = str(inst.get("name") or "")
        display = str(inst.get("displayName") or "")
        if qn in _oanda_aliases(name, display):
            return inst

    for inst in instruments:
        name = str(inst.get("name") or "")
        display = str(inst.get("displayName") or "")
        aliases = _oanda_aliases(name, display)
        if any(qn in a or a in qn for a in aliases):
            return inst

    return None


def _instrument_lookup_key(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _normalize_oanda_symbol_query(user_value: str, available_instruments: Optional[List[str]] = None) -> str:
    return normalize_oanda_symbol_query(user_value, available_instruments)


def _oanda_base_url() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    if env in {"practice", "demo", "test"}:
        return "https://api-fxpractice.oanda.com"
    return "https://api-fxtrade.oanda.com"


def _oanda_token() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    if env in {"practice", "demo", "test"}:
        return (
            os.getenv("OANDA_API_KEY_DEMO")
            or os.getenv("OANDA_ACCESS_TOKEN_DEMO")
            or os.getenv("OANDA_API_KEY_PRACTICE")
            or os.getenv("OANDA_ACCESS_TOKEN_PRACTICE")
            or os.getenv("OANDA_API_KEY")
            or os.getenv("OANDA_ACCESS_TOKEN")
            or ""
        ).strip()
    return (
        os.getenv("OANDA_API_KEY")
        or os.getenv("OANDA_ACCESS_TOKEN")
        or os.getenv("OANDA_API_KEY_LIVE")
        or os.getenv("OANDA_ACCESS_TOKEN_LIVE")
        or ""
    ).strip()


def _oanda_account_id_for_specs() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    if env in {"practice", "demo", "test"}:
        return (
            os.getenv("OANDA_ACCOUNT_ID_DEMO")
            or os.getenv("OANDA_ACCOUNT_ID_PRACTICE")
            or os.getenv("OANDA_ACCOUNT_ID")
            or ""
        ).strip()
    return (
        os.getenv("OANDA_ACCOUNT_ID")
        or os.getenv("OANDA_ACCOUNT_ID_LIVE")
        or ""
    ).strip()


BYBIT_BASE = "https://api.bybit.com"


def _is_likely_bybit_symbol(value: str) -> bool:
    s = str(value or "").strip().upper()
    if not s:
        return False
    return (
        s.endswith("USDT")
        or s.endswith("USDC")
        or s.endswith("USD")
        or s.endswith("PERP")
        or s.endswith("USDT.P")
    )


async def _bybit_get_async(base_url: str, path: str, params: Dict[str, object]) -> Dict[str, object]:
    timeout = httpx.Timeout(6.0, connect=2.0, read=6.0, write=6.0, pool=2.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.get(f"{base_url}{path}", params=params)
    res.raise_for_status()
    return res.json()


async def _bybit_avg_7d_turnover_usd_async(
    base_url: str, symbol: str, category: str = "linear"
) -> Optional[float]:
    try:
        end_ms = int(time.time() * 1000)
        data = await _bybit_get_async(
            base_url,
            "/v5/market/kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": "D",
                "end": end_ms,
                "limit": 10,
            },
        )
        rows = (data.get("result") or {}).get("list") or []
        turnovers: List[float] = []
        for row in rows[:7]:
            if isinstance(row, list) and len(row) >= 7:
                try:
                    turnovers.append(float(row[6]))
                except Exception:
                    pass
        return (sum(turnovers) / len(turnovers)) if turnovers else None
    except Exception:
        return None


async def _bybit_fetch_symbols_by_category(base_url: str, category: str) -> List[str]:
    symbols: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        params: Dict[str, object] = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = await _bybit_get_async(base_url, "/v5/market/instruments-info", params)
        rows = (payload.get("result") or {}).get("list") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").upper()
                if symbol:
                    symbols.append(symbol)
        cursor = (payload.get("result") or {}).get("nextPageCursor")
        if not cursor:
            break
    return sorted(set(symbols))


async def _bybit_get_symbols_by_category_cached(base_url: str, category: str) -> List[str]:
    category_key = category if category in {"linear", "spot", "inverse"} else "linear"
    entry = _BYBIT_SYMBOL_LIST_CACHE.get(category_key) or {"ts": 0.0, "symbols": []}
    now = time.time()
    cached = entry.get("symbols")
    ts = float(entry.get("ts") or 0.0)
    if isinstance(cached, list) and cached and (now - ts) <= _BYBIT_SYMBOL_LIST_CACHE_TTL_SECONDS:
        return list(cached)

    try:
        symbols = await _bybit_fetch_symbols_by_category(base_url, category_key)
    except Exception:
        if isinstance(cached, list) and cached:
            return list(cached)
        raise

    _BYBIT_SYMBOL_LIST_CACHE[category_key] = {"ts": now, "symbols": symbols}
    return list(symbols)


async def _bybit_lookup_symbol(base_url: str, symbol: str) -> Optional[Dict[str, object]]:
    normalized_symbol = _norm_symbol(symbol)
    if not normalized_symbol:
        return None

    for category in ("linear", "spot", "inverse"):
        try:
            choices = await _bybit_get_symbols_by_category_cached(base_url, category)
        except Exception:
            choices = []
        resolved = resolve_bybit_symbol_from_choices(
            normalized_symbol,
            choices,
            preferred_quotes=("USDT", "USDC", "USD"),
            exact_first=True,
        )
        if not resolved or not resolved.get("resolved_symbol"):
            name_aliases = await _bybit_name_aliases_for_choices(base_url, set(choices))
            if name_aliases:
                resolved = resolve_bybit_symbol_from_choices(
                    normalized_symbol,
                    choices,
                    preferred_quotes=("USDT", "USDC", "USD"),
                    exact_first=True,
                    extra_aliases=name_aliases,
                )
        resolved_symbol = str((resolved or {}).get("resolved_symbol") or "").upper()
        if not resolved_symbol:
            continue
        try:
            payload = await _bybit_get_async(
                base_url,
                "/v5/market/instruments-info",
                {"category": category, "symbol": resolved_symbol},
            )
        except Exception:
            continue

        items = (payload.get("result") or {}).get("list") or []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            inst = dict(items[0])
            inst["_category"] = category
            return inst
    return None


def _oanda_specs_mode() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    return "demo" if env in {"practice", "demo", "test"} else "live"


async def _oanda_resolve_and_fetch_specs(query: str) -> Optional[Dict[str, object]]:
    try:
        normalized_query = _normalize_oanda_symbol_query(query)
    except ValueError:
        return None

    cache_key = normalized_query.upper()
    now = time.time()
    cached = _OANDA_SPECS_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return dict(cached[1])

    try:
        cfg = _get_oanda_config(_oanda_specs_mode())
        payload = await _fetch_oanda_json(
            base_url=cfg["base_url"],
            account_id=cfg["account_id"],
            api_key=cfg["token"],
            endpoint=f"/accounts/{{account_id}}/instruments?instruments={normalized_query}",
            mode=cfg["mode"],
            timeout_s=4.0,
        )
    except ValueError:
        return None

    instruments = payload.get("instruments") or []
    if not isinstance(instruments, list) or not instruments:
        return None

    matched = instruments[0] if isinstance(instruments[0], dict) else None
    if not matched:
        return None

    financing = matched.get("financing") or {}
    result = {
        "source": "oanda",
        "query": query,
        "resolved_symbol": matched.get("name"),
        "type": matched.get("type"),
        "displayName": matched.get("displayName"),
        "pipLocation": matched.get("pipLocation"),
        "displayPrecision": matched.get("displayPrecision"),
        "tradeUnitsPrecision": matched.get("tradeUnitsPrecision"),
        "minimumTradeSize": matched.get("minimumTradeSize"),
        "maximumOrderUnits": matched.get("maximumOrderUnits"),
        "marginRate": matched.get("marginRate"),
        "financing.longRate": financing.get("longRate"),
        "financing.shortRate": financing.get("shortRate"),
        "financing.financingDaysOfWeek": financing.get("financingDaysOfWeek"),
    }
    _OANDA_SPECS_CACHE[cache_key] = (now + _OANDA_SPECS_CACHE_TTL_SECONDS, result)
    return dict(result)


async def _bybit_resolve_and_fetch_specs(query: str) -> Optional[Dict[str, object]]:
    want_key = _normalize_instrument_key(query)
    if not want_key:
        return None

    creds = resolve_bybit_credentials_for("default")
    base_url = creds.get("base_url") if isinstance(creds, dict) else None
    base_url = base_url or BYBIT_BASE

    resolved_inst = await _bybit_lookup_symbol(base_url, want_key)
    if not resolved_inst:
        return None

    category = str(resolved_inst.get("_category") or "")
    symbol = str(resolved_inst.get("symbol") or "")

    ticker = None
    try:
        payload = await _bybit_get_async(
            base_url,
            "/v5/market/tickers",
            {"category": category, "symbol": symbol},
        )
        items = (payload.get("result") or {}).get("list") or []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            ticker = items[0]
    except Exception:
        ticker = None

    specs: Dict[str, object] = {
        "source": "bybit",
        "query": query,
        "resolved_symbol": (ticker or {}).get("symbol") or symbol,
        "category": category,
        "lastPrice": (ticker or {}).get("lastPrice"),
        "fundingRate": (ticker or {}).get("fundingRate"),
        "nextFundingTime": (ticker or {}).get("nextFundingTime"),
        "launchTime": resolved_inst.get("launchTime"),
        "openInterest": (ticker or {}).get("openInterest"),
        "openInterestValue": (ticker or {}).get("openInterestValue"),
        "volume24h": (ticker or {}).get("volume24h"),
        "turnover24h": (ticker or {}).get("turnover24h"),
    }

    avg7d = await _bybit_avg_7d_turnover_usd_async(
        base_url,
        str((ticker or {}).get("symbol") or symbol),
        category,
    )
    if avg7d is not None:
        specs["avg7dTurnoverUsd"] = avg7d

    specs["_units"] = {
        "fundingRate": "fraction",
        "lastPrice": "price",
        "launchTime": "timestamp_ms",
        "nextFundingTime": "timestamp_ms",
        "openInterest": "contracts",
        "openInterestValue": "usd_value",
        "volume24h": "base_units_24h",
        "turnover24h": "usd_value_24h",
        "avg7dTurnoverUsd": "usd_value_per_day_avg_7d",
    }

    return {k: v for k, v in specs.items() if v is not None}


async def _fetch_instrument_specs(
    query: str,
    prefer: Optional[str] = None,
    *,
    include_scanner: bool = False,
) -> Dict[str, object]:
    q = str(query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    pref = str(prefer or "").strip().lower()
    specs: Optional[Dict[str, object]] = None
    if pref in {"bybit", "crypto", "perp", "perpetual"}:
        specs = await _bybit_resolve_and_fetch_specs(q)
    elif pref in {"oanda", "fx", "forex"}:
        specs = await _oanda_resolve_and_fetch_specs(q)
    elif _is_likely_fx_pair(q):
        specs = await _oanda_resolve_and_fetch_specs(q)
    elif _is_likely_bybit_symbol(q):
        specs = await _bybit_resolve_and_fetch_specs(q)
    else:
        specs = await _oanda_resolve_and_fetch_specs(q)
        if not specs and not _is_likely_fx_pair(q):
            specs = await _bybit_resolve_and_fetch_specs(q)

    if not specs:
        raise HTTPException(status_code=404, detail=f"Instrument not found for query: {q}")

    if include_scanner:
        try:
            await _attach_scanner_metrics(specs)
        except Exception:
            pass

    return specs


async def _resolve_symbol_payload(
    raw_symbol: str, prefer: str = "bybit", scope: str = "all"
) -> Optional[Dict[str, object]]:
    raw = str(raw_symbol or "")
    normalized = _norm_symbol(raw)
    if not normalized:
        return None

    pref = str(prefer or "bybit").strip().lower()
    selected_scope = str(scope or "all").strip().lower()

    if pref == "oanda":
        resolved = normalize_oanda_symbol_query(raw)
        return {
            "input": raw,
            "normalized": normalized,
            "resolved_symbol": resolved,
            "source": "oanda",
        }

    if pref != "bybit":
        return None

    creds = resolve_bybit_credentials_for("default")
    base_url = (creds.get("base_url") if isinstance(creds, dict) else None) or BYBIT_BASE
    categories = ("linear",) if selected_scope == "linear" else ("linear", "spot", "inverse")
    for category in categories:
        try:
            symbols = await _bybit_get_symbols_by_category_cached(base_url, category)
        except Exception:
            symbols = []
        resolved = resolve_bybit_symbol_from_choices(
            raw,
            symbols,
            preferred_quotes=("USDT", "USDC", "USD"),
            exact_first=True,
        )
        if not resolved or not resolved.get("resolved_symbol"):
            name_aliases = await _bybit_name_aliases_for_choices(base_url, set(symbols))
            if name_aliases:
                resolved = resolve_bybit_symbol_from_choices(
                    raw,
                    symbols,
                    preferred_quotes=("USDT", "USDC", "USD"),
                    exact_first=True,
                    extra_aliases=name_aliases,
                )
        if resolved and resolved.get("resolved_symbol"):
            return resolved
    return None


def _truthy_query_param(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _percentile_rank(values: List[float], current: float) -> float:
    arr = list(values) + [current]
    if not arr:
        return 0.0
    cur = float(current)
    less = 0
    equal = 0
    for v in arr:
        fv = float(v)
        if fv < cur:
            less += 1
        elif fv == cur:
            equal += 1
    if equal <= 0:
        equal = 1
    first = less + 1
    last = less + equal
    avg_rank = (first + last) / 2.0
    return float(avg_rank) / float(len(arr))


def _pearson_corr(xs: List[float], ys: List[float]) -> float:
    if not xs or not ys or len(xs) != len(ys):
        return 0.0
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return float(cov / math.sqrt(vx * vy))


async def _bybit_public_get_json(
    base_url: str, path: str, params: Dict[str, object]
) -> Dict[str, object]:
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"{base_url}{path}", params=params)
    res.raise_for_status()
    return res.json() or {}


def _bybit_parse_kline_rows(rows: object) -> List[List[str]]:
    out: List[List[str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, list) and len(row) >= 7:
            out.append([str(x) for x in row])
    out.sort(key=lambda r: int(float(r[0])))
    return out


def _ma_window_changes(values: List[float], window: int = 20) -> List[float]:
    changes: List[float] = []
    if len(values) <= window:
        return changes
    for i in range(window, len(values)):
        prev = values[i - window : i]
        avg_prev = sum(prev) / float(len(prev)) if prev else 0.0
        cur = values[i]
        if avg_prev <= 0:
            changes.append(0.0)
        else:
            changes.append((cur - avg_prev) / avg_prev)
    return changes


_BYBIT_VOLUME_INTERVALS = {
    "5M": "5",
    "15M": "15",
    "30M": "30",
    "1H": "60",
    "4H": "240",
}

_BYBIT_OI_INTERVALS = {
    "5M": ("5min", 2),
    "15M": ("15min", 2),
    "30M": ("30min", 2),
    "1H": ("1h", 2),
    "4H": ("4h", 2),
    "1D": ("1d", 2),
    "1W": ("1d", 7),
    "1M": ("1d", 30),
}

_BYBIT_FUNDING_LOOKBACK_MINUTES = {
    "5M": 5,
    "15M": 15,
    "30M": 30,
    "1H": 60,
    "4H": 240,
    "1D": 1440,
    "1W": 10080,
    "1M": 43200,
}


async def _attach_bybit_scanner_metrics(specs: Dict[str, object]) -> None:
    base_url = BYBIT_BASE
    symbol = str(specs.get("resolved_symbol") or "").strip().upper()
    category = str(specs.get("category") or "linear").strip().lower() or "linear"
    if not symbol:
        return

    units = specs.get("_units")
    if not isinstance(units, dict):
        units = {}
        specs["_units"] = units

    for tf, interval in _BYBIT_VOLUME_INTERVALS.items():
        try:
            payload = await _bybit_public_get_json(
                base_url,
                "/v5/market/kline",
                {"category": category, "symbol": symbol, "interval": interval, "limit": 220},
            )
            rows = _bybit_parse_kline_rows((payload.get("result") or {}).get("list"))
            vols = [float(r[5]) for r in rows if len(r) > 5]
            changes = _ma_window_changes(vols, window=20)
            if not changes:
                continue
            latest = float(changes[-1])
            percentile = (
                _percentile_rank([float(x) for x in changes[:-1]], latest)
                if len(changes) > 1
                else 0.0
            )
            specs[f"scan.volumeMA.{tf}"] = latest
            specs[f"scan.volumeMA_percentile.{tf}"] = percentile
            units[f"scan.volumeMA.{tf}"] = "fraction"
            units[f"scan.volumeMA_percentile.{tf}"] = "fraction"
        except Exception:
            continue

    if category in {"linear", "inverse"}:
        for tf, (interval_time, window) in _BYBIT_OI_INTERVALS.items():
            try:
                payload = await _bybit_public_get_json(
                    base_url,
                    "/v5/market/open-interest",
                    {
                        "category": category,
                        "symbol": symbol,
                        "intervalTime": interval_time,
                        "limit": 200,
                    },
                )
                rows = (payload.get("result") or {}).get("list") or []
                if not isinstance(rows, list) or len(rows) < (window + 2):
                    continue
                rows_sorted = sorted(
                    [r for r in rows if isinstance(r, dict)],
                    key=lambda r: int(float(r.get("timestamp", 0) or 0)),
                )
                vals = [float(r.get("openInterest", 0) or 0) for r in rows_sorted]
                changes: List[float] = []
                for i in range(window, len(vals)):
                    first = vals[i - window]
                    last = vals[i]
                    changes.append(0.0 if first <= 0 else (last - first) / first)
                if not changes:
                    continue
                latest = float(changes[-1])
                percentile = (
                    _percentile_rank([float(x) for x in changes[:-1]], latest)
                    if len(changes) > 1
                    else 0.0
                )
                specs[f"scan.openInterestChange.{tf}"] = latest
                specs[f"scan.openInterestChange_percentile.{tf}"] = percentile
                units[f"scan.openInterestChange.{tf}"] = "fraction"
                units[f"scan.openInterestChange_percentile.{tf}"] = "fraction"
            except Exception:
                continue

        try:
            now_ms = int(time.time() * 1000)
            hist = await _bybit_public_get_json(
                base_url,
                "/v5/market/funding/history",
                {"category": category, "symbol": symbol, "limit": 200},
            )
            rows = (hist.get("result") or {}).get("list") or []
            rows_sorted = sorted(
                [r for r in rows if isinstance(r, dict)],
                key=lambda r: int(float(r.get("fundingRateTimestamp", 0) or 0)),
            )
            cur = specs.get("fundingRate")
            if cur is not None:
                specs["scan.fundingRate.current"] = float(cur)
                units["scan.fundingRate.current"] = "fraction"
            for tf, minutes in _BYBIT_FUNDING_LOOKBACK_MINUTES.items():
                target = now_ms - int(minutes * 60 * 1000)
                val = None
                for row in reversed(rows_sorted):
                    ts = int(float(row.get("fundingRateTimestamp", 0) or 0))
                    if ts <= target:
                        val = float(row.get("fundingRate", 0) or 0)
                        break
                if val is None:
                    continue
                specs[f"scan.fundingRate.{tf}"] = val
                units[f"scan.fundingRate.{tf}"] = "fraction"
        except Exception:
            pass

        try:
            if symbol != "BTCUSDT":
                payload_s = await _bybit_public_get_json(
                    base_url,
                    "/v5/market/kline",
                    {"category": category, "symbol": symbol, "interval": "1", "limit": 600},
                )
                payload_b = await _bybit_public_get_json(
                    base_url,
                    "/v5/market/kline",
                    {
                        "category": category,
                        "symbol": "BTCUSDT",
                        "interval": "1",
                        "limit": 600,
                    },
                )
                rows_s = _bybit_parse_kline_rows((payload_s.get("result") or {}).get("list"))
                rows_b = _bybit_parse_kline_rows((payload_b.get("result") or {}).get("list"))
                closes_s = [float(r[4]) for r in rows_s if len(r) > 4]
                closes_b = [float(r[4]) for r in rows_b if len(r) > 4]
                for tf, minutes in {"5M": 5, "15M": 15, "30M": 30, "1H": 60, "4H": 240}.items():
                    if len(closes_s) < minutes + 1 or len(closes_b) < minutes + 1:
                        continue
                    s_seg = closes_s[-(minutes + 1) :]
                    b_seg = closes_b[-(minutes + 1) :]
                    s_ret = [
                        (s_seg[i + 1] - s_seg[i]) / s_seg[i]
                        for i in range(minutes)
                        if s_seg[i] != 0
                    ]
                    b_ret = [
                        (b_seg[i + 1] - b_seg[i]) / b_seg[i]
                        for i in range(minutes)
                        if b_seg[i] != 0
                    ]
                    if len(s_ret) != minutes or len(b_ret) != minutes:
                        continue
                    corr = _pearson_corr(s_ret, b_ret)
                    specs[f"scan.corrToBTC.{tf}"] = corr
                    units[f"scan.corrToBTC.{tf}"] = "ratio"
        except Exception:
            pass


_OANDA_SCAN_TIMEFRAMES = {
    "5M": "M5",
    "15M": "M15",
    "30M": "M30",
    "1H": "H1",
    "4H": "H4",
}


async def _oanda_get_json(
    path: str, *, params: Optional[Dict[str, object]] = None
) -> Optional[Dict[str, object]]:
    token = _oanda_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_oanda_base_url()}{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(url, headers=headers, params=params)
    if res.status_code != 200:
        return None
    return res.json() or {}


async def _attach_oanda_scanner_metrics(specs: Dict[str, object]) -> None:
    symbol = str(specs.get("resolved_symbol") or "").strip().upper()
    if not symbol:
        return

    units = specs.get("_units")
    if not isinstance(units, dict):
        units = {}
        specs["_units"] = units

    for label, granularity in _OANDA_SCAN_TIMEFRAMES.items():
        payload = await _oanda_get_json(
            f"/v3/instruments/{symbol}/candles",
            params={"granularity": granularity, "count": 3, "price": "M"},
        )
        if not payload:
            continue
        candles = payload.get("candles") or []
        if not isinstance(candles, list):
            continue
        complete = [c for c in candles if isinstance(c, dict) and c.get("complete")]
        if len(complete) < 2:
            continue
        last2 = complete[-2:]
        try:
            o = float(((last2[0].get("mid") or {}).get("o")))
            c = float(((last2[-1].get("mid") or {}).get("c")))
            h = max(float(((x.get("mid") or {}).get("h"))) for x in last2)
            l = min(float(((x.get("mid") or {}).get("l"))) for x in last2)
        except Exception:
            continue
        if o <= 0:
            continue
        specs[f"scan.priceChange.{label}"] = (c - o) / o
        specs[f"scan.priceRange.{label}"] = (h - l) / o
        units[f"scan.priceChange.{label}"] = "fraction"
        units[f"scan.priceRange.{label}"] = "fraction"

    account_id = _oanda_account_id_for_specs()
    if account_id:
        pricing = await _oanda_get_json(
            f"/v3/accounts/{account_id}/pricing",
            params={"instruments": symbol},
        )
        if pricing and isinstance(pricing.get("prices"), list) and pricing["prices"]:
            item = pricing["prices"][0]
            try:
                bid = float(item.get("closeoutBid", 0) or 0)
                ask = float(item.get("closeoutAsk", 0) or 0)
            except Exception:
                bid = 0.0
                ask = 0.0
            if bid > 0 and ask > 0:
                specs["scan.spread"] = (ask - bid) / ask
                specs["scan.bid"] = bid
                specs["scan.ask"] = ask
                units["scan.spread"] = "fraction"
                units["scan.bid"] = "price"
                units["scan.ask"] = "price"


async def _attach_scanner_metrics(specs: Dict[str, object]) -> None:
    src = str(specs.get("source") or "").strip().lower()
    if src == "bybit":
        await _attach_bybit_scanner_metrics(specs)
    elif src == "oanda":
        await _attach_oanda_scanner_metrics(specs)


def _specs_to_lines(specs: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    for k in sorted(specs.keys()):
        v = specs[k]
        if isinstance(v, (dict, list)):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {v}")
    return lines


def _render_specs_jpg_bytes(specs: Dict[str, object]) -> bytes:
    lines = ["Instrument Specs", ""] + _specs_to_lines(specs)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    wrapped: List[str] = []
    for line in lines:
        if len(line) <= 120:
            wrapped.append(line)
            continue
        while line:
            wrapped.append(line[:120])
            line = line[120:]

    pad = 24
    line_h = 22
    max_w = 1400
    height = pad * 2 + line_h * (len(wrapped) + 1)
    img = Image.new("RGB", (max_w, max(400, height)), (11, 18, 32))
    draw = ImageDraw.Draw(img)
    for idx, line in enumerate(wrapped):
        draw.text((pad, pad + idx * line_h), line, font=font, fill=(226, 232, 240))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _load_watchlist() -> List[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        payload = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return []
    if not isinstance(payload, list):
        return []
    return _normalize_watchlist(payload)


def _get_watchlist() -> List[str]:
    global _WATCHLIST_CACHE
    if _WATCHLIST_CACHE is None:
        _WATCHLIST_CACHE = _load_watchlist()
    return list(_WATCHLIST_CACHE)


def _save_watchlist(items: List[str]) -> None:
    WATCHLIST_PATH.write_text(json.dumps(items, indent=2, sort_keys=True), encoding="utf-8")


def _set_watchlist(items: Iterable[object]) -> List[str]:
    global _WATCHLIST_CACHE
    normalized = _normalize_watchlist(items)
    _WATCHLIST_CACHE = normalized
    _save_watchlist(normalized)
    return list(normalized)


def _load_trading_journal() -> List[Dict[str, object]]:
    if not TRADING_JOURNAL_PATH.exists():
        return []
    try:
        payload = json.loads(TRADING_JOURNAL_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return []

    items: object
    if isinstance(payload, dict):
        items = payload.get("items")
    else:
        items = payload

    if not isinstance(items, list):
        return []

    rows: List[Dict[str, object]] = []
    for entry in items:
        if isinstance(entry, dict):
            rows.append(entry)
    return rows


def _get_trading_journal() -> List[Dict[str, object]]:
    global _TRADING_JOURNAL_CACHE
    if _TRADING_JOURNAL_CACHE is None:
        _TRADING_JOURNAL_CACHE = _load_trading_journal()
    return [dict(item) for item in _TRADING_JOURNAL_CACHE]


def _save_trading_journal(rows: List[Dict[str, object]]) -> None:
    global _TRADING_JOURNAL_CACHE
    with _TRADING_JOURNAL_ROWS_LOCK:
        sorted_rows = sorted(
            rows,
            key=lambda item: str(item.get("close_time") or item.get("open_time") or ""),
            reverse=True,
        )
        _TRADING_JOURNAL_CACHE = [dict(item) for item in sorted_rows]
        _save_json_file(TRADING_JOURNAL_PATH, {"items": sorted_rows, "updated_at": _utc_now_iso()})
    _schedule_dropbox_upload_state_backup()


def _editable_trading_journal_fields() -> Set[str]:
    return {
        "open_time",
        "close_time",
        "symbol",
        "side",
        "timeframe",
        "is_test_trade",
        "setup",
        "qty",
        "entry_price",
        "exit_price",
        "stop_loss",
        "take_profit",
        "commission",
        "net_profit",
        "balance_after_trade",
        "breakeven",
        "notes",
        "account",
        "account_label",
        "currency",
        "qty_unit",
    }


def _normalize_trading_journal_edit_payload(
    payload: object,
    *,
    for_create: bool,
    existing: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Payload must be a JSON object.")
    if not payload:
        raise HTTPException(status_code=422, detail="Payload is empty.")

    allowed = _editable_trading_journal_fields()
    normalized: Dict[str, object] = {}
    protected = {"id", "row_type", "raw_refs", "source", "status", "is_manual", "created_at", "updated_at"}
    protected_identity = {
        "provider",
        "provider_account",
        "provider_trade_id",
        "provider_order_id",
        "provider_position_id",
    }
    numeric_fields = {
        "qty",
        "entry_price",
        "exit_price",
        "stop_loss",
        "take_profit",
        "commission",
        "net_profit",
        "balance_after_trade",
    }
    timestamp_fields = {"open_time", "close_time"}

    for key, raw_value in payload.items():
        field = str(key or "").strip()
        if not field:
            continue
        if field in protected or field in protected_identity:
            raise HTTPException(status_code=422, detail=f"Field '{field}' cannot be edited.")
        if field not in allowed:
            raise HTTPException(status_code=422, detail=f"Field '{field}' is not editable.")
        value = raw_value
        if field in timestamp_fields:
            if value in (None, ""):
                normalized[field] = None
            else:
                parsed = _epoch_or_iso_to_iso(value)
                if not parsed:
                    raise HTTPException(status_code=422, detail=f"Invalid timestamp for '{field}'.")
                normalized[field] = parsed
            continue
        if field in numeric_fields:
            if value in (None, ""):
                normalized[field] = None
            else:
                parsed_num = _to_float(value)
                if parsed_num is None:
                    raise HTTPException(status_code=422, detail=f"Invalid number for '{field}'.")
                normalized[field] = parsed_num
            continue
        if field == "timeframe":
            normalized[field] = _normalize_timeframe(value)
            continue
        if field == "is_test_trade":
            normalized[field] = _normalize_test_trade_flag(value)
            continue
        if field == "symbol":
            symbol = str(value or "").strip()
            if symbol:
                normalized[field] = _norm_symbol(symbol) or symbol.upper()
            else:
                normalized[field] = ""
            continue
        if field == "side":
            side_text = str(value or "").strip().lower()
            if side_text in {"buy", "long"}:
                normalized[field] = "Buy"
            elif side_text in {"sell", "short"}:
                normalized[field] = "Sell"
            elif side_text:
                normalized[field] = str(value).strip()
            else:
                normalized[field] = ""
            continue
        if field == "breakeven":
            if value in (None, ""):
                normalized[field] = ""
            elif isinstance(value, bool):
                normalized[field] = "Yes" if value else "No"
            else:
                lowered = str(value).strip().lower()
                normalized[field] = "Yes" if lowered in {"1", "true", "yes", "y"} else "No"
            continue
        normalized[field] = str(value).strip() if isinstance(value, str) else value

    # Account/currency fields are only editable on manual rows.
    if not for_create:
        target = existing if isinstance(existing, dict) else {}
        is_manual = bool(target.get("is_manual")) or str(target.get("source") or "").lower() == "manual"
        if not is_manual:
            forbidden = sorted(
                set(normalized.keys()).intersection({"account", "account_label", "currency", "qty_unit"})
            )
            if forbidden:
                raise HTTPException(
                    status_code=409,
                    detail=f"Fields {', '.join(forbidden)} can only be edited for manual rows.",
                )
    return normalized


def _apply_trading_journal_manual_overrides(
    row: Dict[str, object], overrides: Dict[str, object]
) -> Dict[str, object]:
    updated = dict(row)
    safe_overrides = {k: v for k, v in overrides.items() if k in _editable_trading_journal_fields()}
    for key, value in safe_overrides.items():
        updated[key] = value
    updated["manual_overrides"] = dict(safe_overrides)
    updated["manual_override_fields"] = sorted(safe_overrides.keys())
    updated["manual_updated_at"] = _utc_now_iso()
    return updated


def _reapply_trading_journal_manual_overrides(row: Dict[str, object]) -> Dict[str, object]:
    overrides = row.get("manual_overrides")
    if not isinstance(overrides, dict) or not overrides:
        return row
    return _apply_trading_journal_manual_overrides(row, overrides)


def _find_journal_row_index(row_id: str) -> int:
    want = str(row_id or "").strip()
    if not want:
        return -1
    rows = _get_trading_journal_rows()
    for idx, row in enumerate(rows):
        if str((row or {}).get("id") or "").strip() == want:
            return idx
    return -1


def _upsert_trading_journal_rows(rows: Iterable[Dict[str, object]]) -> int:
    with _TRADING_JOURNAL_ROWS_LOCK:
        existing = _get_trading_journal_rows()
        by_id: Dict[str, Dict[str, object]] = {}
        for row in existing:
            row_id = str(row.get("id") or "").strip()
            if row_id:
                by_id[row_id] = row
        changed = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row_id = str(row.get("id") or "").strip()
            if not row_id:
                continue
            row["id"] = row_id
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            if row_id in by_id:
                by_id[row_id] = _merge_trading_journal_row(by_id[row_id], row)
            else:
                by_id[row_id] = row
            refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
            status = str(row.get("status") or "").strip().lower()
            if status in {"closed", "filled", "complete", "completed"}:
                _mark_trade_context_closed_or_cancelled(
                    order_id=str(refs.get("orderId") or refs.get("orderID") or "").strip() or None,
                    trade_id=str(refs.get("tradeId") or refs.get("tradeID") or "").strip() or None,
                    status="CLOSED",
                )
            changed += 1
        if changed:
            _save_trading_journal(list(by_id.values()))
    return changed


def _merge_trading_journal_row(
    existing: Dict[str, object], incoming: Dict[str, object]
) -> Dict[str, object]:
    merged = dict(existing)
    preserve_when_incoming_null = {
        "stop_loss",
        "take_profit",
        "open_time",
        "entry_price",
        "balance_after_trade",
        "timeframe",
        "is_test_trade",
    }
    for key, value in incoming.items():
        if key == "metrics" and isinstance(value, dict):
            existing_metrics = merged.get("metrics") if isinstance(merged.get("metrics"), dict) else {}
            incoming_metrics = dict(value)
            if not _normalize_timeframe(incoming_metrics.get("timeframe")) and _normalize_timeframe(existing_metrics.get("timeframe")):
                incoming_metrics["timeframe"] = existing_metrics.get("timeframe")
            merged[key] = {**existing_metrics, **incoming_metrics}
            continue
        if key in preserve_when_incoming_null:
            if (value is None or (isinstance(value, str) and value.strip() == "")) and merged.get(key) is not None:
                continue
        merged[key] = value
    return _reapply_trading_journal_manual_overrides(merged)


def _load_trading_journal_state() -> Dict[str, object]:
    if not TRADING_JOURNAL_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(TRADING_JOURNAL_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_trading_journal_state(state: Dict[str, object]) -> None:
    TRADING_JOURNAL_STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _stable_registry_key(parts: Iterable[object]) -> str:
    return "|".join(str(part or "").strip() for part in parts)


def _update_unresolved_registry(
    *,
    family: str,
    key: str,
    details: Optional[Dict[str, object]] = None,
    resolved: bool,
    resolution_source: Optional[str] = None,
) -> Tuple[bool, Dict[str, object]]:
    if not family or not key:
        return False, {}
    state = _load_trading_journal_state()
    registry = state.get("unresolved_registry")
    if not isinstance(registry, dict):
        registry = {}
    family_map = registry.get(family)
    if not isinstance(family_map, dict):
        family_map = {}
    now_iso = _utc_now_iso()
    detail_payload = details if isinstance(details, dict) else {}
    detail_signature = json.dumps(detail_payload, sort_keys=True, default=str)
    existing = family_map.get(key)
    should_warn = False
    if isinstance(existing, dict):
        entry = dict(existing)
        entry["last_seen_at"] = now_iso
        entry["count"] = int(_to_float(entry.get("count")) or 0) + 1
        was_resolved = bool(entry.get("resolved"))
        previous_signature = str(entry.get("last_signature") or "")
        if resolved:
            entry["resolved"] = True
            if resolution_source:
                entry["resolution_source"] = resolution_source
            if not was_resolved:
                should_warn = False
        else:
            entry["resolved"] = False
            if was_resolved or previous_signature != detail_signature:
                should_warn = True
        entry["last_signature"] = detail_signature
        entry["details"] = detail_payload
    else:
        entry = {
            "first_seen_at": now_iso,
            "last_seen_at": now_iso,
            "count": 1,
            "resolved": bool(resolved),
            "resolution_source": resolution_source or None,
            "last_signature": detail_signature,
            "details": detail_payload,
        }
        should_warn = not resolved
    family_map[key] = entry
    registry[family] = family_map
    state["unresolved_registry"] = registry
    _save_trading_journal_state(state)
    return should_warn, entry


def _restore_bybit_closed_pnl_last_seen_from_state() -> None:
    state = _load_trading_journal_state()
    persisted = state.get("bybit_closed_pnl_last_seen") if isinstance(state, dict) else None
    if not isinstance(persisted, dict):
        return
    for mode in ("demo", "live"):
        value = persisted.get(mode)
        if value in (None, ""):
            continue
        parsed = int(_to_float(value) or 0)
        if parsed > 0:
            _BYBIT_CLOSED_PNL_LAST_SEEN[mode] = parsed


def _persist_bybit_closed_pnl_last_seen() -> None:
    state = _load_trading_journal_state()
    state["bybit_closed_pnl_last_seen"] = {
        "demo": int(_BYBIT_CLOSED_PNL_LAST_SEEN.get("demo") or 0) or None,
        "live": int(_BYBIT_CLOSED_PNL_LAST_SEEN.get("live") or 0) or None,
    }
    _save_trading_journal_state(state)


def _persist_oanda_fill_state() -> None:
    payload = {
        "last_seen_transaction_id": {
            "live": _OANDA_TX_LAST_SEEN.get("live"),
            "demo": _OANDA_TX_LAST_SEEN.get("demo"),
        },
        "open_trade_legs": _OANDA_OPEN_TRADE_LEGS,
        "diagnostics": _OANDA_FILL_DIAGNOSTICS,
        "updated_at": _utc_now_iso(),
    }
    _save_json_file(OANDA_FILL_STATE_PATH, payload)


def _restore_oanda_fill_state_on_startup() -> None:
    payload = _load_json_file(OANDA_FILL_STATE_PATH, {})
    if not isinstance(payload, dict):
        return
    last_seen = payload.get("last_seen_transaction_id")
    if isinstance(last_seen, dict):
        for account in ("live", "demo"):
            candidate = str(last_seen.get(account) or "").strip()
            if candidate:
                _OANDA_TX_LAST_SEEN[account] = candidate
    open_legs = payload.get("open_trade_legs")
    if isinstance(open_legs, dict):
        for account in ("live", "demo"):
            legs = open_legs.get(account)
            if isinstance(legs, dict):
                _OANDA_OPEN_TRADE_LEGS[account] = {
                    str(k): dict(v) for k, v in legs.items() if isinstance(v, dict)
                }
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict):
        for account in ("live", "demo"):
            node = diagnostics.get(account)
            if isinstance(node, dict):
                _OANDA_FILL_DIAGNOSTICS[account] = dict(node)


def _record_oanda_fill_diagnostic(account: str, **updates: object) -> None:
    merged = dict(_OANDA_FILL_DIAGNOSTICS.get(account, {}))
    merged.update(updates)
    merged["updated_at"] = _utc_now_iso()
    _OANDA_FILL_DIAGNOSTICS[account] = merged
    _persist_oanda_fill_state()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(
        path,
        payload,
        retries=10,
        backoff=0.05,
        sort_keys=False,
        ensure_ascii=False,
    )


def _get_trading_journal_rows() -> List[Dict[str, object]]:
    with _TRADING_JOURNAL_ROWS_LOCK:
        data = _load_json_file(TRADING_JOURNAL_PATH, {"items": []})
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        items = data.get("items") if isinstance(data, dict) else []
        return items if isinstance(items, list) else []


def _set_trading_journal_rows(rows: List[Dict[str, object]]) -> None:
    global _TRADING_JOURNAL_CACHE
    with _TRADING_JOURNAL_ROWS_LOCK:
        _TRADING_JOURNAL_CACHE = [dict(item) for item in rows if isinstance(item, dict)]
        _save_json_file(TRADING_JOURNAL_PATH, {"items": rows, "updated_at": _utc_now_iso()})
    _schedule_dropbox_upload_state_backup()


def _get_monthly_aud_revaluation_rows() -> List[Dict[str, object]]:
    data = _load_json_file(MONTHLY_AUD_REVALUATION_PATH, {"items": []})
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    items = data.get("items") if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def _set_monthly_aud_revaluation_rows(rows: List[Dict[str, object]]) -> None:
    _save_json_file(MONTHLY_AUD_REVALUATION_PATH, {"items": rows, "updated_at": _utc_now_iso()})
    _schedule_dropbox_upload_state_backup()


def _canonical_trade_epoch_second(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(round(pd.to_datetime(value, utc=True).timestamp()))
    except Exception:
        return None


def _canonical_bybit_demo_trade_signature(row: Dict[str, object]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    if not _is_bybit_demo_trade_row(row):
        return None
    if _row_type(row) != "trade":
        return None
    status = str(row.get("status") or row.get("state") or "").strip().lower()
    if status and status not in {"closed", "close", "filled", "complete", "completed"}:
        return None

    opened = row.get("open_time") or row.get("opened_at") or row.get("entry_time")
    closed = row.get("close_time") or row.get("closed_at") or row.get("exit_time") or row.get("date")
    symbol = str(row.get("symbol") or row.get("instrument") or row.get("symbol_raw") or "").strip().upper()
    qty = row.get("qty") if row.get("qty") is not None else row.get("qty_raw")

    if not symbol or not opened or not closed:
        return None

    def _rounded_num(value: object, dp: int = 8) -> str:
        num = _to_float(value)
        if num is None:
            return ""
        return f"{num:.{dp}f}"

    fees = row.get("fees") if row.get("fees") is not None else row.get("commission")
    realized_pnl = row.get("realized_pnl") if row.get("realized_pnl") is not None else row.get("net_profit")
    account_label = str(row.get("account_label") or row.get("account") or "").strip().lower()
    account_norm = re.sub(r"\s+", " ", account_label)
    return "|".join(
        [
            account_norm,
            symbol,
            str(_canonical_trade_epoch_second(opened) or ""),
            str(_canonical_trade_epoch_second(closed) or ""),
            _rounded_num(qty, 8),
            _rounded_num(row.get("entry_price")),
            _rounded_num(row.get("exit_price")),
            _rounded_num(fees, 6),
            _rounded_num(realized_pnl, 6),
        ]
    )


def _is_bybit_demo_trade_row(row: Dict[str, object]) -> bool:
    if not isinstance(row, dict):
        return False
    if _row_type(row) != "trade":
        return False
    status = str(row.get("status") or row.get("state") or "").strip().lower()
    if status and status not in {"closed", "close", "filled", "complete", "completed"}:
        return False

    source = str(row.get("source") or "").strip().lower()
    account = str(row.get("account") or "").strip().lower()
    account_label = str(row.get("account_label") or row.get("account") or "").strip()
    if source == "bybit":
        return account in {"demo", "practice"} or _is_bybit_demo_account_label(account_label)
    if source == "excel":
        if _is_bybit_demo_account_label(account_label):
            return True
        refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
        dbx_path = str(refs.get("dropbox_path") or refs.get("workbook_path") or "").strip().lower()
        return dbx_path.endswith(f"/{BYBIT_DEMO_WORKBOOK_NAME.lower()}") or dbx_path == BYBIT_DEMO_WORKBOOK_NAME.lower()
    return False


def _merge_row_notes_comments(target: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
    merged = dict(target)
    text_fields = ["notes", "pre_trade_comments", "entry_comments", "trade_management", "exit_comments"]
    for field in text_fields:
        left = str(merged.get(field) or "").strip()
        right = str(incoming.get(field) or "").strip()
        if left and right and right not in left:
            merged[field] = f"{left}\n{right}"
        elif (not left) and right:
            merged[field] = right
    return merged


def _bybit_demo_order_identity(row: Dict[str, object]) -> str:
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    return str(refs.get("orderId") or row.get("order_id") or "").strip()


def _bybit_demo_trade_score(row: Dict[str, object]) -> Tuple[int, int, int, int, int, int]:
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    has_order_id = int(bool(_bybit_demo_order_identity(row)))
    side_norm = _normalize_side_for_comparison(row.get("side") or row.get("direction"))
    side_source = str(refs.get("side_backfill_source") or refs.get("side_source") or "").strip().lower()
    has_corrected_side = int(bool(side_norm and side_source and side_source not in {"unresolved"}))
    has_tpsl = int(row.get("stop_loss") is not None or row.get("take_profit") is not None)
    has_balance = int(row.get("balance_after_trade") is not None)
    has_notes = int(any(str(row.get(f) or "").strip() for f in ["notes", "pre_trade_comments", "entry_comments", "trade_management", "exit_comments"]))
    updated = _canonical_trade_epoch_second(row.get("updated_at")) or -1
    return has_order_id, has_corrected_side, has_tpsl, has_balance, has_notes, updated


def _sanitize_bybit_demo_rows(rows: List[Dict[str, object]]) -> tuple[List[Dict[str, object]], Dict[str, int]]:
    if not rows:
        return [], {"repaired_sides": 0, "deduped_by_order_id": 0, "deduped_by_fingerprint": 0, "trade_group_merged": 0, "quarantined_invalid_time": 0, "changed": 0}

    repaired_rows, repaired_count = _repair_persisted_bybit_demo_sides(rows)
    passthrough: List[Dict[str, object]] = []
    bybit_rows: List[Dict[str, object]] = []
    quarantined_rows: List[Dict[str, object]] = []
    for row in repaired_rows:
        if isinstance(row, dict) and _is_bybit_demo_trade_row(row):
            close_ts = _canonical_trade_epoch_second(row.get("close_time"))
            open_ts = _canonical_trade_epoch_second(row.get("open_time"))
            if close_ts is not None and open_ts is not None and close_ts <= open_ts:
                q = dict(row)
                q["status"] = "invalid_time_order"
                q["row_type"] = "quarantine"
                quarantined_rows.append(q)
                continue
            bybit_rows.append(dict(row))
        else:
            passthrough.append(row)

    dedup_order: Dict[str, Dict[str, object]] = {}
    order_dropped = 0
    orderless: List[Dict[str, object]] = []
    for row in bybit_rows:
        order_id = _bybit_demo_order_identity(row)
        if not order_id:
            orderless.append(row)
            continue
        prev = dedup_order.get(order_id)
        if prev is None:
            dedup_order[order_id] = row
            continue
        winner, loser = (row, prev) if _bybit_demo_trade_score(row) > _bybit_demo_trade_score(prev) else (prev, row)
        dedup_order[order_id] = _merge_row_notes_comments(winner, loser)
        order_dropped += 1

    after_order = list(dedup_order.values()) + orderless
    dedup_fallback: Dict[str, Dict[str, object]] = {}
    fallback_dropped = 0
    for row in after_order:
        key = _canonical_bybit_demo_trade_signature(row)
        if not key:
            key = f"rowid:{str(row.get('id') or '')}"
        prev = dedup_fallback.get(key)
        if prev is None:
            dedup_fallback[key] = row
            continue
        winner, loser = (row, prev) if _bybit_demo_trade_score(row) > _bybit_demo_trade_score(prev) else (prev, row)
        dedup_fallback[key] = _merge_row_notes_comments(winner, loser)
        fallback_dropped += 1

    grouped: Dict[str, Dict[str, object]] = {}
    trade_group_merged = 0
    for row in dedup_fallback.values():
        gk = "|".join(
            [
                str(row.get("account") or row.get("account_label") or "").strip().lower(),
                str(row.get("symbol") or "").strip().upper(),
                _normalize_side_for_comparison(row.get("side")),
                str(_canonical_trade_epoch_second(row.get("open_time")) or ""),
                _num_bucket(row.get("entry_price"), 8),
                _num_bucket(row.get("qty"), 8),
            ]
        )
        prev = grouped.get(gk)
        if prev is None:
            grouped[gk] = row
            continue
        merged = _merge_row_notes_comments(prev, row)
        merged["realized_pnl"] = (_to_float(prev.get("realized_pnl")) or 0.0) + (_to_float(row.get("realized_pnl")) or 0.0)
        merged["net_profit"] = (_to_float(prev.get("net_profit")) or _to_float(prev.get("realized_pnl")) or 0.0) + (_to_float(row.get("net_profit")) or _to_float(row.get("realized_pnl")) or 0.0)
        merged["fees"] = (_to_float(prev.get("fees")) or _to_float(prev.get("commission")) or 0.0) + (_to_float(row.get("fees")) or _to_float(row.get("commission")) or 0.0)
        merged["commission"] = (_to_float(prev.get("commission")) or _to_float(prev.get("fees")) or 0.0) + (_to_float(row.get("commission")) or _to_float(row.get("fees")) or 0.0)
        merged["open_time"] = min(str(prev.get("open_time") or ""), str(row.get("open_time") or ""))
        merged["close_time"] = max(str(prev.get("close_time") or ""), str(row.get("close_time") or ""))
        grouped[gk] = merged
        trade_group_merged += 1

    sanitized_rows = sorted(list(grouped.values()) + passthrough, key=_row_sort_dt, reverse=True)
    changed_total = int(repaired_count > 0 or order_dropped > 0 or fallback_dropped > 0 or trade_group_merged > 0 or len(sanitized_rows) != len(rows))
    stats = {
        "repaired_sides": repaired_count,
        "deduped_by_order_id": order_dropped,
        "deduped_by_fingerprint": fallback_dropped,
        "trade_group_merged": trade_group_merged,
        "quarantined_invalid_time": len(quarantined_rows),
        "changed": changed_total,
    }
    return sanitized_rows, stats


def _dedupe_legacy_bybit_demo_rows() -> int:
    rows = _get_trading_journal_rows()
    if not rows:
        return 0
    sanitized, stats = _sanitize_bybit_demo_rows(rows)
    removed = int(stats.get("deduped_by_order_id", 0)) + int(stats.get("deduped_by_fingerprint", 0))
    if int(stats.get("changed", 0)):
        _set_trading_journal_rows(sanitized)
    return removed


def _repair_persisted_bybit_demo_sides(rows: List[Dict[str, object]]) -> tuple[List[Dict[str, object]], int]:
    repaired: List[Dict[str, object]] = []
    change_count = 0
    for row in rows:
        if not isinstance(row, dict):
            repaired.append(row)
            continue
        if not _is_bybit_demo_trade_row(row):
            repaired.append(row)
            continue

        inferred_side = _infer_side_from_tpsl_geometry(
            entry_price=_to_float(row.get("entry_price")),
            stop_loss=_to_float(row.get("stop_loss")),
            take_profit=_to_float(row.get("take_profit")),
        )
        backfill_source = "tpsl_geometry"
        if not inferred_side:
            inferred_side = _infer_side_from_exit_and_pnl(
                entry_price=_to_float(row.get("entry_price")),
                exit_price=_to_float(row.get("exit_price")),
                realized_pnl=_to_float(row.get("realized_pnl"))
                if row.get("realized_pnl") is not None
                else _to_float(row.get("net_profit")),
            )
            backfill_source = "price_move_vs_pnl"

        current_side_norm = _normalize_side_for_comparison(row.get("side") or row.get("direction"))
        inferred_norm = _normalize_side_for_comparison(inferred_side)
        if not inferred_norm or inferred_norm == current_side_norm:
            repaired.append(row)
            continue

        updated = dict(row)
        updated["side"] = inferred_side
        refs = updated.get("raw_refs") if isinstance(updated.get("raw_refs"), dict) else {}
        next_refs = dict(refs)
        next_refs["side_backfill_source"] = backfill_source
        updated["raw_refs"] = next_refs
        repaired.append(updated)
        change_count += 1

    return repaired, change_count


def _repair_persisted_bybit_demo_journal_sides() -> int:
    rows = _get_trading_journal_rows()
    if not rows:
        return 0
    repaired_rows, changed = _repair_persisted_bybit_demo_sides(rows)
    if changed:
        _set_trading_journal_rows(repaired_rows)
    return changed


def _repair_persisted_oanda_trade_rows() -> int:
    rows = _get_trading_journal_rows()
    if not rows:
        return 0
    contexts = _load_trade_contexts()
    changed = 0
    repaired: List[Dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("source") or "").strip().lower() != "oanda":
            repaired.append(row)
            continue
        missing = not row.get("stop_loss") or not row.get("take_profit") or not _normalize_timeframe(row.get("timeframe"))
        if not missing:
            repaired.append(row)
            continue
        refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
        candidates = [
            ctx
            for ctx in contexts
            if (
                str(refs.get("tradeId") or "").strip()
                and str(ctx.get("trade_id") or "").strip() == str(refs.get("tradeId") or "").strip()
            )
            or (
                str(refs.get("orderId") or "").strip()
                and str(ctx.get("order_id") or "").strip() == str(refs.get("orderId") or "").strip()
            )
            or (
                str(refs.get("transactionId") or "").strip()
                and str(ctx.get("transaction_id") or "").strip() == str(refs.get("transactionId") or "").strip()
            )
        ]
        if len(candidates) != 1:
            if len(candidates) > 1:
                BYBIT_LOGGER.warning("OANDA_ROW_REPAIR_AMBIGUOUS row_id=%s candidates=%s", row.get("id"), len(candidates))
            else:
                BYBIT_LOGGER.info("OANDA_ROW_REPAIR_SKIPPED row_id=%s reason=no_context_match", row.get("id"))
            repaired.append(row)
            continue
        ctx = candidates[0]
        updated = dict(row)
        if not updated.get("stop_loss") and ctx.get("stop_loss"):
            updated["stop_loss"] = ctx.get("stop_loss")
        if not updated.get("take_profit") and ctx.get("take_profit"):
            updated["take_profit"] = ctx.get("take_profit")
        tf = _normalize_timeframe(updated.get("timeframe"))
        if not tf:
            tf = _normalize_timeframe(ctx.get("timeframe"))
            if tf:
                updated["timeframe"] = tf
        if tf:
            metrics = updated.get("metrics") if isinstance(updated.get("metrics"), dict) else {}
            metrics = dict(metrics)
            metrics["timeframe"] = tf
            updated["metrics"] = metrics
        if updated != row:
            changed += 1
        repaired.append(updated)
    if changed:
        _set_trading_journal_rows(repaired)
    return changed


def _repair_persisted_bybit_trade_context_fields() -> int:
    rows = _get_trading_journal_rows()
    if not rows:
        return 0
    changed = 0
    repaired: List[Dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("source") or "").strip().lower() != "bybit":
            repaired.append(row)
            continue
        if (
            _normalize_timeframe(row.get("timeframe"))
            and row.get("stop_loss") not in (None, "")
            and row.get("take_profit") not in (None, "")
        ):
            repaired.append(row)
            continue
        patched = _backfill_trade_row_context_fields(row)
        if patched != row:
            changed += 1
        repaired.append(patched)
    if changed:
        _set_trading_journal_rows(repaired)
    return changed


def _repair_persisted_bybit_open_times(rows: List[Dict[str, object]]) -> tuple[List[Dict[str, object]], int]:
    repaired: List[Dict[str, object]] = []
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            repaired.append(row)
            continue
        if str(row.get("source") or "").strip().lower() != "bybit":
            repaired.append(row)
            continue
        open_iso = _epoch_or_iso_to_iso(row.get("open_time") or row.get("opened_at") or row.get("entry_time"))
        close_iso = _epoch_or_iso_to_iso(row.get("close_time") or row.get("closed_at") or row.get("exit_time") or row.get("date"))
        open_sec = _canonical_trade_epoch_second(open_iso)
        close_sec = _canonical_trade_epoch_second(close_iso)
        open_time_valid = bool(
            open_iso
            and close_iso
            and open_sec is not None
            and close_sec is not None
            and int(open_sec) < int(close_sec)
        )
        if open_time_valid:
            repaired.append(row)
            continue

        ctx = _lookup_trade_context_for_journal_row(row)
        if not isinstance(ctx, dict):
            ctx = _resolve_bybit_closed_pnl_trade_context(
                account_mode=str(row.get("account") or "").strip().lower() or "demo",
                symbol=str(row.get("symbol") or row.get("instrument") or "").strip().upper(),
                side=row.get("side"),
                close_time=close_iso,
            )
        if not isinstance(ctx, dict):
            repaired.append(row)
            continue
        candidate_open = _epoch_or_iso_to_iso(ctx.get("open_time")) or _epoch_or_iso_to_iso(ctx.get("created_at"))
        if not candidate_open:
            repaired.append(row)
            continue
        if close_iso and _canonical_trade_epoch_second(candidate_open) is not None and _canonical_trade_epoch_second(close_iso) is not None:
            if int(_canonical_trade_epoch_second(candidate_open) or 0) >= int(_canonical_trade_epoch_second(close_iso) or 0):
                repaired.append(row)
                continue
        updated = dict(row)
        updated["open_time"] = candidate_open
        timeframe = _normalize_timeframe(updated.get("timeframe"))
        if not timeframe:
            timeframe = _normalize_timeframe(ctx.get("timeframe"))
            if timeframe:
                updated["timeframe"] = timeframe
        if timeframe:
            metrics = updated.get("metrics") if isinstance(updated.get("metrics"), dict) else {}
            metrics = dict(metrics)
            metrics["timeframe"] = timeframe
            updated["metrics"] = metrics
        duration = _trade_duration_seconds(
            {
                "row_type": "trade",
                "open_time": updated.get("open_time"),
                "close_time": close_iso,
            }
        )
        if duration is not None:
            updated["trade_duration_seconds"] = duration
        repaired.append(updated)
        changed += 1
    return repaired, changed


def _backfill_persisted_bybit_trade_fields(rows: List[Dict[str, object]]) -> tuple[List[Dict[str, object]], int]:
    repaired: List[Dict[str, object]] = []
    changed = 0
    for row in rows:
        if not isinstance(row, dict) or str(row.get("source") or "").strip().lower() != "bybit":
            repaired.append(row)
            continue
        updated = dict(row)
        ctx = _lookup_trade_context_for_journal_row(updated)
        timeframe = _normalize_timeframe(updated.get("timeframe"))
        if not timeframe and isinstance(ctx, dict):
            timeframe = _normalize_timeframe(ctx.get("timeframe"))
            if timeframe:
                updated["timeframe"] = timeframe
        if timeframe:
            metrics = updated.get("metrics") if isinstance(updated.get("metrics"), dict) else {}
            metrics = dict(metrics)
            metrics["timeframe"] = timeframe
            updated["metrics"] = metrics
        duration = _trade_duration_seconds(
            {
                "row_type": "trade",
                "open_time": updated.get("open_time") or updated.get("opened_at") or updated.get("entry_time"),
                "close_time": updated.get("close_time") or updated.get("closed_at") or updated.get("exit_time") or updated.get("date"),
            }
        )
        if duration is not None and duration != updated.get("trade_duration_seconds"):
            updated["trade_duration_seconds"] = duration
        if updated != row:
            changed += 1
        repaired.append(updated)
    return repaired, changed


def _exclude_bybit_demo_row(row: Dict[str, object]) -> bool:
    """Return True if this journal row should be excluded (Bybit Demo)."""
    if ENABLE_BYBIT_DEMO_JOURNAL:
        return False

    src = str(row.get("source") or "").strip().lower()
    acc = str(row.get("account") or "").strip().lower()
    label = str(row.get("account_label") or row.get("account") or "").strip()
    if src == "bybit" and acc == "demo":
        return True
    return _is_bybit_demo_account_label(label)


def _purge_bybit_demo_journal_state() -> int:
    """Remove any Bybit Demo rows/balances already persisted on disk."""
    if ENABLE_BYBIT_DEMO_JOURNAL:
        return 0

    removed = 0
    try:
        rows = _get_trading_journal_rows()
        kept = [r for r in rows if isinstance(r, dict) and not _exclude_bybit_demo_row(r)]
        removed = max(0, len(rows) - len(kept))
        if removed:
            _set_trading_journal_rows(kept)
            global _TRADING_JOURNAL_CACHE
            _TRADING_JOURNAL_CACHE = [dict(r) for r in kept]
    except Exception:
        # Defensive: never break startup or API endpoints for a cleanup.
        removed = 0

    try:
        state = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
        if isinstance(state, dict):
            bals = state.get("excel_account_balances")
            if isinstance(bals, list):
                kept_bals: List[Dict[str, object]] = []
                removed_bals = 0
                for b in bals:
                    if not isinstance(b, dict):
                        continue
                    label = b.get("label") or b.get("account")
                    if _is_bybit_demo_account_label(label):
                        removed_bals += 1
                        continue
                    kept_bals.append(b)
                if removed_bals:
                    state["excel_account_balances"] = kept_bals
                    _save_trading_journal_state(state)
    except Exception:
        pass

    return removed


def _load_trading_journal_import_cache() -> Dict[str, object]:
    data = _load_json_file(TRADING_JOURNAL_IMPORT_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_trading_journal_import_cache(cache: Dict[str, object]) -> None:
    _save_json_file(TRADING_JOURNAL_IMPORT_CACHE_PATH, cache)


def _norm_col(name: object) -> str:
    value = str(name or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("/", " ").replace("?", " ")
    for char in [" ", "-", "(", ")", "[", "]", ".", ":"]:
        value = value.replace(char, "_")
    while "__" in value:
        value = value.replace("__", "_")
    return value.strip("_")


def _excel_cell_to_python(value: object) -> object:
    if value is None:
        return None
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return None
    except Exception:
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        try:
            return pd.to_datetime(value).isoformat()
        except Exception:
            return str(value)
    try:
        if hasattr(value, "item"):
            value = value.item()
    except Exception:
        pass
    return value


def _canonical_symbol(symbol: str) -> str:
    symbol_norm = (symbol or "").strip().upper().replace("/", "")
    symbol_norm = re.sub(r"\.[A-Z0-9]+$", "", symbol_norm)
    symbol_norm = symbol_norm.replace("_", "")
    return symbol_norm


def _is_fx_account_label(account_label: str) -> bool:
    text = (account_label or "").upper()
    return any(token in text for token in ("OANDA", "PEPPERSTONE", "FOREX", " FX"))


def _infer_asset_class(
    account_label: str,
    symbol: str,
    row: pd.Series,
    metrics: Dict[str, object],
) -> str:
    account_txt = (account_label or "").upper()
    symbol_txt = _canonical_symbol(symbol or "").upper()
    if any(token in account_txt for token in ("OANDA", "PEPPERSTONE", "FOREX", " FX")):
        return "fx"
    if symbol_txt and (is_likely_oanda_pair(symbol_txt) or bool(re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", str(symbol or "").upper()))):
        return "fx"
    if any(token in symbol_txt for token in ("USDT", "USDC", "BTC", "ETH", "PERP")):
        return "crypto"

    hints: List[str] = []
    for key, value in (metrics or {}).items():
        key_txt = str(key or "").lower()
        if any(k in key_txt for k in ("asset", "class", "market", "instrument", "product", "type", "account", "currency")):
            hints.append(str(value or "").lower())
    if isinstance(row, pd.Series):
        for col_name in row.index:
            col_txt = str(col_name or "").lower()
            if any(k in col_txt for k in ("asset", "class", "market", "instrument", "product", "type", "account", "currency")):
                hints.append(str(row.get(col_name) or "").lower())

    joined = " ".join(hints)
    if re.search(r"\b(forex|fx|currency)\b", joined):
        return "fx"
    if re.search(r"\b(crypto|perp|perpetual)\b", joined):
        return "crypto"
    return "fx" if _is_fx_account_label(account_label) else "crypto"


def _normalize_fx_qty_for_display(
    account_label: str, symbol: str, raw_qty: Optional[float]
) -> Optional[float]:
    if raw_qty is None:
        return None
    if "OANDA" in (account_label or "").upper():
        return raw_qty / 100000.0
    return raw_qty


def _safe_float_from_row(row: pd.Series, col: Optional[str]) -> Optional[float]:
    if not col:
        return None
    value = row.get(col)
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = re.sub(r"[^0-9+\-eE.]", "", text)
    if not text:
        return None
    try:
        num = float(text)
        return -num if negative else num
    except Exception:
        return None


def _norm_account_key(name: object) -> str:
    text = str(name or "").upper().strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^A-Z0-9 ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:\s+|^)#\s*\d+$", "", text).strip()
    text = re.sub(r"\s+DEMO\s*\d+$", " DEMO", text).strip()
    return text


def _safe_str_from_row(row: pd.Series, col: Optional[str]) -> str:
    if not col:
        return ""
    value = row.get(col)
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _boolish_text(value: object) -> str:
    return str(value or "").strip()


def _infer_account_currency(account_label: str) -> str:
    text = (account_label or "").upper()
    if any(part in text for part in ("BYBIT", "BINANCE")):
        return "USDT"
    if "COINSPOT" in text:
        return "AUD"
    if any(part in text for part in ("OANDA", "PEPPERSTONE")):
        return "AUD"
    return ""


def _is_empty_cell(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _cell_to_str(value: object) -> str:
    if _is_empty_cell(value):
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def _cell_to_float(value: object) -> Optional[float]:
    if _is_empty_cell(value):
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        neg = False
        if text.startswith("(") and text.endswith(")"):
            neg = True
            text = text[1:-1].strip()
        text = text.replace(",", "")
        text = re.sub(r"(?i)\b(AUD|USD|USDT|EUR|GBP|JPY|CHF|NZD|CAD)\b", "", text).strip()
        text = text.replace("$", "").strip()
        text = re.sub(r"[^0-9+\-\.eE]", "", text).strip()
        if not text:
            return None
        try:
            val = float(text)
            return -val if neg else val
        except Exception:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_to_iso(value: object) -> Optional[str]:
    if _is_empty_cell(value):
        return None
    try:
        return pd.to_datetime(value, utc=True).isoformat()
    except Exception:
        text = _cell_to_str(value)
        return text or None


def _first_present(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols = {_norm_col(col): col for col in df.columns}
    for candidate in candidates:
        if candidate in cols:
            return cols[candidate]
    return None


def _is_bybit_demo_account_label(label: object) -> bool:
    text = str(label or "").strip().upper()
    return bool(text) and ("BYBIT" in text) and ("DEMO" in text)


def _parse_excel_account_workbook(
    file_name: str, dbx_path: str, payload: bytes
) -> Tuple[List[Dict[str, object]], Optional[Dict[str, object]]]:
    bio = io.BytesIO(payload)
    try:
        xls = pd.ExcelFile(bio, engine="openpyxl")
    except Exception:
        bio.seek(0)
        xls = pd.ExcelFile(bio, engine="xlrd")

    all_rows: List[Dict[str, object]] = []
    account_balance: Optional[Dict[str, object]] = None
    account_label = Path(file_name).stem.strip() or file_name

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        if df is None or df.empty:
            continue
        df.columns = [str(col) for col in df.columns]
        norm_to_orig = {_norm_col(col): col for col in df.columns}

        open_time_col = _first_present(df, ["opening_time", "open_time", "entry_time", "time_open"])
        close_time_col = _first_present(df, ["closing_time", "close_time", "exit_time", "time_close", "closed_at"])
        side_col = _first_present(df, ["type_buy_sell", "side", "direction", "buy_sell", "type"])
        symbol_col = _first_present(df, ["symbol", "instrument", "pair", "market"])
        account_col = _first_present(df, ["account", "account_label", "portfolio", "book"])
        account_ccy_col = _first_present(df, ["account_currency", "currency", "ccy", "deposit_currency"])
        setup_col = _first_present(df, ["setup"])
        qty_col = _first_present(df, ["size_quantity", "qty", "quantity", "size", "units", "volume"])
        entry_col = _first_present(df, ["entry_price", "entry", "open_price", "price_open"])
        exit_col = _first_present(df, ["closing_price", "exit_price", "exit", "close_price", "price_close"])
        swap_col = _first_present(df, ["swap"])
        commission_col = _first_present(df, ["commission", "fee", "fees", "cost"])
        pnl_col = _first_present(df, ["net_profit", "realized_pnl", "pnl", "profit", "pl", "net_pnl"])
        balance_after_trade_col = _first_present(
            df,
            ["balance_after_trade", "bal_after_trade", "balance_after", "bal_after"],
        )
        sl_col = _first_present(df, ["stop_loss_optional", "stop_loss", "sl"])
        tp_col = _first_present(df, ["take_profit_optional", "take_profit", "tp"])
        high_col = _first_present(df, ["highest_price_optional", "highest_price"])
        low_col = _first_present(df, ["lowest_price_optional", "lowest_price"])
        notes_col = _first_present(df, ["notes"])
        pre_trade_col = _first_present(df, ["pre_trade_comments"])
        entry_comments_col = _first_present(df, ["entry_comments"])
        trade_mgmt_col = _first_present(df, ["trade_management"])
        exit_comments_col = _first_present(df, ["exit_comments"])
        breakeven_col = _first_present(df, ["breakeven"])

        extra_aliases = {
            "error": ["error"],
            "ath_atl": ["ath_atl"],
            "ema_bounce": ["ema_bounce"],
            "timeframe": ["timeframe"],
            "pattern": ["pattern"],
            "held_through_news": ["held_through_news"],
            "early_close": ["early_close"],
            "near_perfect_entry": ["near_perfect_entry"],
            "near_win": ["near_win"],
            "near_round_number": ["near_round_number"],
            "close_stop_out": ["close_stop_out"],
            "spiked_out": ["spiked_out"],
            "suggestions": ["suggestions"],
        }
        extra_cols = {key: _first_present(df, aliases) for key, aliases in extra_aliases.items()}

        trade_signal_count = sum(
            col is not None
            for col in [symbol_col, side_col, entry_col, exit_col, pnl_col, open_time_col, close_time_col]
        )
        if symbol_col and trade_signal_count >= 3:
            for idx, row in df.iterrows():
                symbol_raw = _safe_str_from_row(row, symbol_col)
                if not symbol_raw or symbol_raw.lower() == "nan":
                    continue

                row_account_label = _safe_str_from_row(row, account_col) or account_label
                account_currency = _safe_str_from_row(row, account_ccy_col) or _infer_account_currency(row_account_label)
                symbol_canon = _canonical_symbol(symbol_raw)

                open_time_iso = None
                close_time_iso = None
                for col_name, target in ((open_time_col, "open"), (close_time_col, "close")):
                    if not col_name:
                        continue
                    raw_t = row.get(col_name)
                    try:
                        if pd.isna(raw_t):
                            continue
                    except Exception:
                        pass
                    try:
                        iso = pd.to_datetime(raw_t).isoformat()
                    except Exception:
                        iso = str(raw_t)
                    if target == "open":
                        open_time_iso = iso
                    else:
                        close_time_iso = iso

                raw_qty = _safe_float_from_row(row, qty_col)
                qty_display = _normalize_fx_qty_for_display(row_account_label, symbol_canon, raw_qty)
                entry_price = _safe_float_from_row(row, entry_col)
                exit_price = _safe_float_from_row(row, exit_col)
                commission = _safe_float_from_row(row, commission_col)
                swap = _safe_float_from_row(row, swap_col)
                net_profit = _safe_float_from_row(row, pnl_col)
                balance_after_trade = _safe_float_from_row(row, balance_after_trade_col)
                stop_loss = _safe_float_from_row(row, sl_col)
                take_profit = _safe_float_from_row(row, tp_col)
                highest_price = _safe_float_from_row(row, high_col)
                lowest_price = _safe_float_from_row(row, low_col)

                raw_excel: Dict[str, object] = {}
                for col_name in df.columns:
                    value = _excel_cell_to_python(row.get(col_name))
                    if value is None or (isinstance(value, str) and not value.strip()):
                        continue
                    raw_excel[str(col_name)] = value

                metrics: Dict[str, object] = {}
                for key, col_name in extra_cols.items():
                    if not col_name:
                        continue
                    value = _excel_cell_to_python(row.get(col_name))
                    if value is None or (isinstance(value, str) and not str(value).strip()):
                        continue
                    metrics[key] = value
                timeframe = _normalize_timeframe(metrics.get("timeframe"))
                if timeframe:
                    metrics["timeframe"] = timeframe
                asset_class = _infer_asset_class(row_account_label, symbol_canon, row, metrics)

                used_norm = {
                    _norm_col(x)
                    for x in [
                        open_time_col, close_time_col, side_col, symbol_col, setup_col, qty_col,
                        entry_col, exit_col, swap_col, commission_col, pnl_col, balance_after_trade_col, sl_col, tp_col,
                        high_col, low_col, notes_col, pre_trade_col, entry_comments_col,
                        trade_mgmt_col, exit_comments_col, breakeven_col,
                    ] if x
                }
                used_norm.update(_norm_col(c) for c in extra_cols.values() if c)
                for norm_name, orig_name in norm_to_orig.items():
                    if norm_name in used_norm:
                        continue
                    value = _excel_cell_to_python(row.get(orig_name))
                    if value is None or (isinstance(value, str) and not str(value).strip()):
                        continue
                    metrics.setdefault(norm_name, value)

                workbook_is_bybit_demo = file_name.strip().lower() == BYBIT_DEMO_WORKBOOK_NAME.lower()
                order_id_col = _first_present(df, ["order_id", "orderid"])
                order_id_raw = _safe_str_from_row(row, order_id_col)
                row_id = f"excel:{account_label}:{sheet}:{idx}:{symbol_canon}:{close_time_iso or ''}"
                if workbook_is_bybit_demo and order_id_raw:
                    row_id = _journal_id_for_bybit_demo_row(symbol_canon, order_id_raw)
                side_txt = _safe_str_from_row(row, side_col).upper()
                if workbook_is_bybit_demo:
                    corrected = _infer_side_from_tpsl_geometry(
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    ) or _infer_side_from_exit_and_pnl(
                        entry_price=entry_price,
                        exit_price=exit_price,
                        realized_pnl=net_profit,
                    )
                    if corrected:
                        side_txt = corrected.upper()
                setup_txt = _safe_str_from_row(row, setup_col)
                breakeven_txt = _boolish_text(_safe_str_from_row(row, breakeven_col))
                status = "closed" if exit_price is not None else "unknown"
                notional = (
                    abs((qty_display or 0.0) * (entry_price or 0.0))
                    if (qty_display is not None and entry_price is not None)
                    else None
                )

                all_rows.append({
                    "id": row_id,
                    "source": "excel",
                    "account": row_account_label,
                    "account_label": row_account_label,
                    "sheet": sheet,
                    "asset_class": asset_class,
                    "currency": account_currency,
                    "symbol": symbol_canon,
                    "symbol_raw": symbol_raw,
                    "side": side_txt,
                    "setup": setup_txt,
                    "timeframe": timeframe,
                    "open_time": open_time_iso,
                    "close_time": close_time_iso,
                    "qty": qty_display,
                    "qty_raw": raw_qty,
                    "qty_unit": "lots" if asset_class == "fx" else "native",
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "swap": swap,
                    "commission": commission,
                    "commission_currency": "AUD" if asset_class == "fx" else "USDT",
                    "fees": commission,
                    "fee_currency": "AUD" if asset_class == "fx" else "USDT",
                    "realized_pnl": net_profit,
                    "realized_pnl_currency": account_currency,
                    "net_profit": net_profit,
                    "balance_after_trade": balance_after_trade,
                    "balance_after_trade_currency": account_currency,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "highest_price": highest_price,
                    "lowest_price": lowest_price,
                    "breakeven": breakeven_txt,
                    "notes": _safe_str_from_row(row, notes_col),
                    "pre_trade_comments": _safe_str_from_row(row, pre_trade_col),
                    "entry_comments": _safe_str_from_row(row, entry_comments_col),
                    "trade_management": _safe_str_from_row(row, trade_mgmt_col),
                    "exit_comments": _safe_str_from_row(row, exit_comments_col),
                    "notional_usd": notional,
                    "status": status,
                    "metrics": metrics,
                    "raw_excel": raw_excel,
                    "raw_refs": {
                        "dropbox_path": dbx_path,
                        "sheet": sheet,
                        "row_index": int(idx),
                        "orderId": order_id_raw or None,
                    },
                    "updated_at": _utc_now_iso(),
                })

        bal_col = _first_present(
            df,
            [
                "balance",
                "account_balance",
                "cash_balance",
                "balance_after_trade",
                "bal_after_trade",
                "bal_after",
                "balance_after",
            ],
        )
        nav_col = _first_present(df, ["nav", "equity", "account_equity"])
        ccy_col = _first_present(df, ["currency", "ccy", "account_currency"])
        if bal_col or nav_col:
            # Prefer the most recent non-empty balance-like row (usually bottom of sheet).
            for _, row in df.iloc[::-1].iterrows():
                bal_val = row.get(bal_col) if bal_col else None
                nav_val = row.get(nav_col) if nav_col else None
                if _is_empty_cell(bal_val) and _is_empty_cell(nav_val):
                    continue
                account_balance = {
                    "source": "excel",
                    "account": account_label,
                    "label": account_label,
                    "balance": _cell_to_float(bal_val),
                    "nav": _cell_to_float(nav_val),
                    "currency": _cell_to_str(row.get(ccy_col)) if ccy_col else _infer_account_currency(account_label),
                    "dropbox_path": dbx_path,
                }
                break

    return all_rows, account_balance
def _resolve_trading_journal_dropbox_folder() -> Tuple[str, List[Dict[str, Any]]]:
    if not TRADING_JOURNAL_DROPBOX_FOLDER:
        raise HTTPException(status_code=500, detail="TRADING_JOURNAL_DROPBOX_FOLDER is not set.")

    configured = TRADING_JOURNAL_DROPBOX_FOLDER.strip()
    candidates: List[str] = []
    if configured:
        candidates.append(configured)

    prefix = "/Apps/alexpsym_render"
    if configured.lower().startswith(prefix.lower()):
        stripped = configured[len(prefix) :]
        if not stripped:
            stripped = "/"
        if not stripped.startswith("/"):
            stripped = "/" + stripped
        candidates.append(stripped)

    if configured.startswith("/") and not configured.lower().startswith("/apps/"):
        candidates.append(f"{prefix}{configured}")

    entries: Optional[List[Dict[str, Any]]] = None
    active_folder = configured
    last_exc: Optional[Exception] = None
    for candidate in dict.fromkeys(candidates):
        try:
            entries = list_excel_files(candidate, recursive=TRADING_JOURNAL_DROPBOX_RECURSIVE)
            active_folder = candidate
            break
        except Exception as exc:
            last_exc = exc

    if entries is None:
        if last_exc is not None:
            raise last_exc
        raise FileNotFoundError(f"Dropbox folder not found: {configured}")

    return active_folder, entries


def _join_dropbox_path(folder: str, name: str) -> str:
    root = (folder or "").rstrip("/")
    if not root:
        root = "/"
    if root == "/":
        return f"/{name.lstrip('/')}"
    return f"{root}/{name.lstrip('/')}"


def _cashflow_template_bytes() -> bytes:
    buffer = io.BytesIO()
    cols = ["account", "date", "amount", "new_balance", "currency", "reason"]
    template = pd.DataFrame(columns=cols)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        template.to_excel(writer, sheet_name="Cashflows", index=False)
    return buffer.getvalue()


def _bybit_demo_workbook_bytes() -> bytes:
    buffer = io.BytesIO()
    template = pd.DataFrame(columns=BYBIT_DEMO_WORKBOOK_COLUMNS)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        template.to_excel(writer, sheet_name=BYBIT_DEMO_WORKBOOK_SHEET, index=False)
    return buffer.getvalue()


def _ensure_bybit_demo_dropbox_files(active_folder: str) -> Dict[str, bool]:
    created = {
        "trade_history_template_created": False,
        "demo_workbook_created": False,
    }

    template_path = _join_dropbox_path(active_folder, BYBIT_DEMO_TEMPLATE_NAME)
    try:
        _dropbox_download_bytes(template_path)
    except FileNotFoundError:
        if bybit_history_fetcher is None:
            raise RuntimeError("Bybit history exporter module not available.")
        buffer = io.StringIO()
        bybit_history_fetcher.write_blank_trade_history_template(buffer)
        _dropbox_upload_bytes(template_path, buffer.getvalue().encode("utf-8"))
        created["trade_history_template_created"] = True

    workbook_path = _join_dropbox_path(active_folder, BYBIT_DEMO_WORKBOOK_NAME)
    try:
        _dropbox_download_bytes(workbook_path)
    except FileNotFoundError:
        _dropbox_upload_bytes(workbook_path, _bybit_demo_workbook_bytes())
        created["demo_workbook_created"] = True

    return created


async def _ensure_trading_journal_dropbox_templates() -> None:
    try:
        active_folder, _entries = await asyncio.to_thread(_resolve_trading_journal_dropbox_folder)
        await asyncio.to_thread(_ensure_cashflow_template, active_folder)
        await asyncio.to_thread(_ensure_bybit_demo_dropbox_files, active_folder)
    except Exception as exc:  # pragma: no cover - startup safeguard
        _record_bybit_demo_sync_status(last_checked_at=_utc_now_iso(), last_error=str(exc))
        BYBIT_LOGGER.error("Dropbox journal template ensure failed: %s", exc)


def _ensure_cashflow_template(active_folder: str) -> bool:
    cashflow_path = _join_dropbox_path(active_folder, "account_cashflows.xlsx")
    try:
        _dropbox_download_bytes(cashflow_path)
        return False
    except Exception:
        pass
    _dropbox_upload_bytes(cashflow_path, _cashflow_template_bytes())
    return True


# 30s default unless CASHFLOW_CACHE_TTL_SECONDS is configured.
_CASHFLOW_CACHE_TTL_SECONDS = int(os.getenv("CASHFLOW_CACHE_TTL_SECONDS", "30"))
_CASHFLOW_CACHE: Dict[str, Tuple[float, Dict[str, List[Dict[str, object]]]]] = {}  # keyed by active Dropbox folder
_CASHFLOW_CACHE_LOCK = threading.Lock()


def _load_cashflows_from_dropbox(active_folder: str) -> Dict[str, List[Dict[str, object]]]:
    folder_key = str(active_folder or "").strip()
    if folder_key:
        now = time.time()
        with _CASHFLOW_CACHE_LOCK:
            cached = _CASHFLOW_CACHE.get(folder_key)
        if cached and (now - cached[0] < _CASHFLOW_CACHE_TTL_SECONDS):
            return cached[1]

    out: Dict[str, List[Dict[str, object]]] = defaultdict(list)

    def _cache_and_return() -> Dict[str, List[Dict[str, object]]]:
        # Cache successful, empty, and error fallbacks to avoid retry storms during Dropbox issues.
        payload: Dict[str, List[Dict[str, object]]] = dict(out)
        if folder_key:
            with _CASHFLOW_CACHE_LOCK:
                _CASHFLOW_CACHE[folder_key] = (time.time(), payload)
        return payload

    cashflow_path = _join_dropbox_path(active_folder, "account_cashflows.xlsx")
    try:
        payload = _dropbox_download_bytes(cashflow_path)
    except Exception:
        return _cache_and_return()
    bio = io.BytesIO(payload)
    try:
        df = pd.read_excel(bio, sheet_name="Cashflows")
    except Exception:
        return _cache_and_return()
    if df is None or df.empty:
        return _cache_and_return()
    df.columns = [str(c) for c in df.columns]
    acct_col = _first_present(df, ["account"])
    date_col = _first_present(df, ["date"])
    amount_col = _first_present(df, ["amount"])
    bal_col = _first_present(df, ["new_balance", "newbalance"])
    ccy_col = _first_present(df, ["currency"])
    reason_col = _first_present(df, ["reason"])
    if not acct_col or not date_col or not bal_col:
        return _cache_and_return()

    for _, row in df.iterrows():
        account = _safe_str_from_row(row, acct_col)
        account_key = _norm_account_key(account)
        if not account_key:
            continue
        raw_date = row.get(date_col)
        try:
            if pd.isna(raw_date):
                continue
        except Exception:
            pass
        try:
            dt_iso = pd.to_datetime(raw_date).isoformat()
        except Exception:
            dt_iso = str(raw_date)
        out[account_key].append(
            {
                "account": account,
                "date": dt_iso,
                "amount": _safe_float_from_row(row, amount_col),
                "new_balance": _safe_float_from_row(row, bal_col),
                "currency": _safe_str_from_row(row, ccy_col),
                "reason": _safe_str_from_row(row, reason_col),
            }
        )

    for account_key in list(out.keys()):
        out[account_key] = sorted(out[account_key], key=lambda x: str(x.get("date") or ""))
    return _cache_and_return()


def _latest_balances_from_cashflows(active_folder: str) -> List[Dict[str, object]]:
    ledger = _load_cashflows_from_dropbox(active_folder)
    items: List[Dict[str, object]] = []
    for account_key, events in ledger.items():
        if not events:
            continue
        latest = events[-1]
        label = str(latest.get("account") or account_key)
        items.append(
            {
                "account": label,
                "label": label,
                "balance": _to_float(latest.get("new_balance")),
                "nav": None,
                "currency": str(latest.get("currency") or _infer_account_currency(label)),
                "source": "cashflow_ledger",
                "as_of": latest.get("date"),
            }
        )
    return sorted(items, key=lambda x: str(x.get("label") or ""))


def _cashflow_rows_for_journal(active_folder: str) -> List[Dict[str, object]]:
    ledger = _load_cashflows_from_dropbox(active_folder)
    rows: List[Dict[str, object]] = []
    for account_key, events in ledger.items():
        for idx, ev in enumerate(events or []):
            event_dt = str(ev.get("date") or "")
            if not event_dt:
                continue
            account_label = str(ev.get("account") or account_key or "").strip()
            amount = _to_float(ev.get("amount"))
            new_balance = _to_float(ev.get("new_balance"))
            currency = str(ev.get("currency") or _infer_account_currency(account_label))
            reason = str(ev.get("reason") or "").strip()
            if amount is None and new_balance is None:
                continue
            flow_type = "cashflow"
            if amount is not None:
                if amount > 0:
                    flow_type = "deposit"
                elif amount < 0:
                    flow_type = "withdrawal"
            rows.append(
                {
                    "id": f"cashflow:{account_key}:{event_dt}:{idx}",
                    "row_type": "cashflow",
                    "cashflow_type": flow_type,
                    "cashflow_reason": reason,
                    "cashflow_amount": amount,
                    "cashflow_new_balance": new_balance,
                    "source": "cashflow_ledger",
                    "account": account_label,
                    "account_label": account_label,
                    "sheet": "Cashflows",
                    "asset_class": "fx" if _is_fx_account_label(account_label) else "crypto",
                    "currency": currency,
                    "symbol": "CASHFLOW",
                    "symbol_raw": "CASHFLOW",
                    "side": flow_type.upper(),
                    "setup": reason,
                    "open_time": event_dt,
                    "close_time": event_dt,
                    "qty": None,
                    "qty_raw": None,
                    "qty_unit": "",
                    "entry_price": None,
                    "exit_price": None,
                    "swap": None,
                    "commission": None,
                    "commission_currency": currency,
                    "fees": None,
                    "fee_currency": currency,
                    "realized_pnl": None,
                    "realized_pnl_currency": currency,
                    "net_profit": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "highest_price": None,
                    "lowest_price": None,
                    "breakeven": "",
                    "notes": reason,
                    "status": "cashflow",
                    "balance_after_trade": new_balance,
                    "balance_after_trade_currency": currency,
                    "updated_at": _utc_now_iso(),
                }
            )
    return sorted(rows, key=_row_sort_dt, reverse=True)


def _list_local_trading_journal_workbooks() -> List[Path]:
    root = TRADING_JOURNAL_LOCAL_DIR
    if not root.exists() or not root.is_dir():
        return []
    found: List[Path] = []
    for candidate in root.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".xls", ".xlsx", ".xlsm"}:
            continue
        found.append(candidate)
    return sorted(found, key=lambda p: p.name.lower())


def _local_journal_import_enabled() -> bool:
    return TRADING_JOURNAL_ENABLE_LOCAL_IMPORT or TRADING_JOURNAL_LOCAL_DIR_EXPLICIT


def _is_default_local_workbook(path: Path) -> bool:
    try:
        return path.resolve().parent == (BASE_DIR / "journal").resolve()
    except Exception:
        return False


def _num_bucket(value: object, digits: int = 6) -> str:
    num = _to_float(value)
    if num is None:
        return ""
    return f"{num:.{digits}f}"


def _workbook_row_dedupe_fingerprint(row: Dict[str, object]) -> Optional[str]:
    if not isinstance(row, dict) or _row_type(row) != "trade":
        return None
    source = str(row.get("source") or "").strip().lower()
    if source in {"manual", "cashflow_ledger"}:
        return None
    if row.get("cashflow_type") or str(row.get("symbol") or "").strip().upper() == "CASHFLOW":
        return None
    return "|".join(
        [
            str(row.get("asset_class") or "").strip().lower(),
            str(row.get("symbol") or row.get("symbol_raw") or "").strip().upper(),
            _normalize_side_for_comparison(row.get("side")),
            str(_canonical_trade_epoch_second(row.get("open_time")) or ""),
            str(_canonical_trade_epoch_second(row.get("close_time")) or ""),
            _num_bucket(row.get("qty") if row.get("qty") is not None else row.get("qty_raw"), 8),
            _num_bucket(row.get("entry_price"), 8),
            _num_bucket(row.get("exit_price"), 8),
            _num_bucket(row.get("stop_loss"), 8),
            _num_bucket(row.get("take_profit"), 8),
        ]
    )


def _row_source_rank(row: Dict[str, object]) -> int:
    source = str(row.get("source") or "").strip().lower()
    local_kind = str(row.get("_local_import_kind") or "").strip().lower()
    if source == "excel":
        return 3
    if source == "local_excel" and local_kind == "explicit":
        return 2
    if source == "local_excel":
        return 1
    return 0


def _merge_duplicate_import_rows(primary: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
    merged = dict(primary)
    for field in [
        "manual_overrides",
        "manual_override_fields",
        "notes",
        "pre_trade_comments",
        "entry_comments",
        "trade_management",
        "exit_comments",
        "flags",
    ]:
        if merged.get(field) in (None, "", [], {}) and incoming.get(field) not in (None, "", [], {}):
            merged[field] = incoming.get(field)
    for k, v in incoming.items():
        if merged.get(k) in (None, "") and v not in (None, ""):
            merged[k] = v
    return merged


def _parse_local_trading_journal_workbook(path: Path) -> Tuple[List[Dict[str, object]], Optional[Dict[str, object]]]:
    payload = path.read_bytes()
    rows, balance = _parse_excel_account_workbook(path.name, str(path), payload)
    for row in rows:
        if isinstance(row, dict):
            row["source"] = "local_excel"
    if isinstance(balance, dict):
        balance["source"] = "local_excel"
    return rows, balance


def _import_trading_journal_from_dropbox_excel(
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, object]:
    if progress_cb:
        progress_cb(2, "Resolving Dropbox folder…")
    active_folder, entries = _resolve_trading_journal_dropbox_folder()
    configured = TRADING_JOURNAL_DROPBOX_FOLDER.strip()
    cashflow_template_created = False
    bybit_demo_templates = {
        "trade_history_template_created": False,
        "demo_workbook_created": False,
    }
    if progress_cb:
        progress_cb(6, "Checking cashflow template…")
    try:
        cashflow_template_created = _ensure_cashflow_template(active_folder)
    except Exception:
        cashflow_template_created = False
    try:
        bybit_demo_templates = _ensure_bybit_demo_dropbox_files(active_folder)
    except Exception as exc:
        errors = [{"file": BYBIT_DEMO_WORKBOOK_NAME, "path": active_folder, "error": str(exc)}]
        _save_json_file(
            TRADING_JOURNAL_STATE_PATH,
            {
                "updated_at": _utc_now_iso(),
                "excel_account_balances": [],
                "source_folder": active_folder,
                "configured_folder": configured,
                "cashflow_template_created": cashflow_template_created,
                **bybit_demo_templates,
                "errors": errors,
            },
        )
        raise

    # Refresh entries after possibly creating the cashflow template.
    # Exclude the cashflow ledger file itself from workbook imports.
    used_recursive_fallback = False
    try:
        refreshed = list_excel_files(active_folder, recursive=TRADING_JOURNAL_DROPBOX_RECURSIVE)
        if (not refreshed) and (not TRADING_JOURNAL_DROPBOX_RECURSIVE):
            # If the user keeps workbooks in subfolders, do a one-shot recursive scan.
            refreshed = list_excel_files(active_folder, recursive=True)
            used_recursive_fallback = bool(refreshed)
        entries = refreshed if isinstance(refreshed, list) else entries
    except Exception:
        pass
    entries = [
        e
        for e in (entries or [])
        if str((e or {}).get("name") or "").strip().lower() != "account_cashflows.xlsx"
    ]

    existing_rows = _get_trading_journal_rows()
    existing_count = len(existing_rows)

    workbook_count = 0
    reused_count = 0
    rows: List[Dict[str, object]] = []
    balances: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []
    cache = _load_trading_journal_import_cache()
    cache_version = 0
    if isinstance(cache, dict):
        try:
            cache_version = int(cache.get("version") or 0)
        except Exception:
            cache_version = 0
    files_cache_raw = cache.get("files") if isinstance(cache, dict) else None
    files_cache: Dict[str, Dict[str, object]] = files_cache_raw if isinstance(files_cache_raw, dict) else {}
    if cache_version != TRADING_JOURNAL_IMPORT_CACHE_VERSION:
        files_cache = {}
    next_files_cache: Dict[str, Dict[str, object]] = {}

    total_files = len(entries)
    if progress_cb:
        progress_cb(10, f"Found {total_files} workbook(s)…")

    if total_files == 0:
        msg = (
            f"No Excel workbooks found in Dropbox folder {active_folder!r} "
            f"(configured {configured!r})."
        )
        # Don't wipe an existing journal just because Dropbox is empty / misconfigured.
        _save_json_file(
            TRADING_JOURNAL_STATE_PATH,
            {
                "updated_at": _utc_now_iso(),
                "excel_account_balances": balances,
                "source_folder": active_folder,
                "configured_folder": configured,
                "cashflow_template_created": cashflow_template_created,
                **bybit_demo_templates,
                "workbooks_seen": 0,
                "used_recursive_fallback": used_recursive_fallback,
                "errors": [{"file": "", "path": active_folder, "error": msg}],
            },
        )
        return {
            "ok": False,
            "message": msg,
            "source_folder": active_folder,
            "configured_folder": configured,
            "cashflow_template_created": cashflow_template_created,
            **bybit_demo_templates,
            "workbooks_seen": 0,
            "rows_imported": 0,
            "balances_found": 0,
            "used_recursive_fallback": used_recursive_fallback,
            "errors": [{"file": "", "path": active_folder, "error": msg}],
        }

    for entry_index, entry in enumerate(entries, start=1):
        name = str(entry.get("name") or "")
        dbx_path = str(entry.get("path_lower") or entry.get("path_display") or "")
        dbx_rev = str(entry.get("rev") or "")
        if not dbx_path:
            continue
        try:
            if progress_cb:
                pct = 10 + int(80 * (entry_index / max(total_files, 1)))
                progress_cb(pct, f"Importing {entry_index}/{total_files}: {name}")

            cached = files_cache.get(dbx_path) if dbx_rev else None
            cached_rev = str(cached.get("rev") or "") if isinstance(cached, dict) else ""
            if cached and dbx_rev and cached_rev == dbx_rev:
                cached_rows = cached.get("rows")
                cached_balance = cached.get("balance")
                parsed_rows = cached_rows if isinstance(cached_rows, list) else []
                parsed_balance = cached_balance if isinstance(cached_balance, dict) else None
                reused_count += 1
            else:
                payload = _dropbox_download_bytes(dbx_path)
                parsed_rows, parsed_balance = _parse_excel_account_workbook(name, dbx_path, payload)

            rows.extend(parsed_rows)
            if parsed_balance:
                balances.append(parsed_balance)
            workbook_count += 1
            next_files_cache[dbx_path] = {
                "rev": dbx_rev,
                "rows": parsed_rows,
                "balance": parsed_balance,
                "name": name,
                "updated_at": _utc_now_iso(),
            }
        except Exception as exc:
            errors.append({"file": name, "path": dbx_path, "error": str(exc)})

    dedup: Dict[str, Dict[str, object]] = {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if row_id:
            dedup[row_id] = row

    final_rows = sorted(dedup.values(), key=_row_sort_dt, reverse=True)

    if progress_cb:
        progress_cb(95, "Finalising…")

    if (not final_rows) and existing_count:
        # Keep prior data if the import produced no rows (common when folder is wrong or parsing failed).
        msg = "Imported 0 rows; keeping existing journal data."
        errors.append({"file": "", "path": active_folder, "error": msg})
    else:
        if existing_count:
            # Merge Excel-imported rows into the existing journal so webhook-fed rows are not wiped.
            combined: Dict[str, Dict[str, object]] = {}
            for r in existing_rows:
                rid = str(r.get("id") or "")
                if rid:
                    combined[rid] = r
            for r in final_rows:
                rid = str(r.get("id") or "")
                if rid:
                    combined[rid] = r
            merged_rows = sorted(combined.values(), key=_row_sort_dt, reverse=True)
            _set_trading_journal_rows(merged_rows)
        else:
            _set_trading_journal_rows(final_rows)

    ok_flag = bool(final_rows) or bool(balances)
    message = (
        "Done"
        if ok_flag
        else (
            "No trade rows imported from Excel workbooks. "
            "Check your TRADING_JOURNAL_DROPBOX_FOLDER and ensure your journal workbooks (xlsx/xlsm/xls) are inside it."
        )
    )

    _save_json_file(
        TRADING_JOURNAL_STATE_PATH,
        {
            "updated_at": _utc_now_iso(),
            "excel_account_balances": balances,
            "source_folder": active_folder,
            "configured_folder": configured,
            "cashflow_template_created": cashflow_template_created,
            **bybit_demo_templates,
            "workbooks_seen": workbook_count,
            "workbooks_reused": reused_count,
            "used_recursive_fallback": used_recursive_fallback,
            "errors": errors,
        },
    )
    _save_trading_journal_import_cache(
        {
            "version": TRADING_JOURNAL_IMPORT_CACHE_VERSION,
            "updated_at": _utc_now_iso(),
            "source_folder": active_folder,
            "files": next_files_cache,
        }
    )
    sanitized_rows, sanitize_stats = _sanitize_bybit_demo_rows(_get_trading_journal_rows())
    if int(sanitize_stats.get("changed", 0)):
        _set_trading_journal_rows(sanitized_rows)
    workbook_stats = _sanitize_bybit_demo_workbook(active_folder)
    _schedule_dropbox_upload_state_backup()

    return {
        "ok": ok_flag,
        "message": message,
        "source_folder": active_folder,
        "configured_folder": configured,
        "cashflow_template_created": cashflow_template_created,
        **bybit_demo_templates,
        "workbooks_seen": workbook_count,
        "workbooks_reused": reused_count,
        "rows_imported": len(final_rows),
        "rows_deduped": int(sanitize_stats.get("deduped_by_order_id", 0)) + int(sanitize_stats.get("deduped_by_fingerprint", 0)),
        "workbook_rows_deduped": int(workbook_stats.get("deduped_by_order_id", 0)) + int(workbook_stats.get("deduped_by_fingerprint", 0)),
        "balances_found": len(balances),
        "used_recursive_fallback": used_recursive_fallback,
        "errors": errors,
    }


def _import_trading_journal_from_sources(
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, object]:
    source_mode = TRADING_JOURNAL_SOURCE if TRADING_JOURNAL_SOURCE in {"dropbox", "local", "both", "auto"} else "both"
    include_dropbox = source_mode in {"dropbox", "both", "auto"}
    include_local = source_mode in {"local", "both", "auto"}
    local_enabled = _local_journal_import_enabled()

    existing_rows = _get_trading_journal_rows()
    all_rows: Dict[str, Dict[str, object]] = {}
    for row in existing_rows:
        if isinstance(row, dict):
            rid = str(row.get("id") or "")
            if rid:
                all_rows[rid] = dict(row)

    diagnostics = _default_journal_diagnostics()
    errors: List[Dict[str, str]] = []
    balances: List[Dict[str, object]] = []
    imported_any = False

    if include_dropbox:
        try:
            dropbox_result = _import_trading_journal_from_dropbox_excel(progress_cb=progress_cb)
            diagnostics["dropbox_workbooks_seen"] = int(dropbox_result.get("workbooks_seen") or 0)
            errors.extend(dropbox_result.get("errors") or [])
            imported_any = imported_any or bool(dropbox_result.get("rows_imported"))
            rows_now = _get_trading_journal_rows()
            for row in rows_now:
                if isinstance(row, dict):
                    rid = str(row.get("id") or "")
                    if rid:
                        all_rows[rid] = dict(row)
            balances = list(_get_excel_account_balances())
        except Exception as exc:
            errors.append({"file": "", "path": "dropbox", "error": str(exc)})

    local_files = _list_local_trading_journal_workbooks() if include_local else []
    diagnostics["local_workbooks_seen"] = len(local_files)
    ignored_local_workbooks: List[str] = []
    if include_local and (not local_enabled) and local_files:
        ignored_local_workbooks = [p.name for p in local_files]
        local_files = []
    local_rows_total = 0
    local_balances: List[Dict[str, object]] = []
    for local_file in local_files:
        try:
            local_rows, local_balance = _parse_local_trading_journal_workbook(local_file)
            local_kind = "explicit" if local_enabled else "default"
            local_rows_total += len(local_rows)
            for row in local_rows:
                rid = str(row.get("id") or "")
                if rid:
                    row["_local_import_kind"] = local_kind
                    row["_workbook_source"] = str(local_file)
                    all_rows[rid] = row
            if local_balance:
                local_balances.append(local_balance)
        except Exception as exc:
            errors.append({"file": local_file.name, "path": str(local_file), "error": str(exc)})
    imported_any = imported_any or local_rows_total > 0

    dedupe_groups = 0
    source_duplicate_rows_dropped = 0
    duplicate_rows_dropped = 0
    canonical_rows: Dict[str, Dict[str, object]] = {}
    carry_rows: List[Dict[str, object]] = []
    for row in all_rows.values():
        key = _workbook_row_dedupe_fingerprint(row)
        if not key:
            carry_rows.append(row)
            continue
        prev = canonical_rows.get(key)
        if prev is None:
            canonical_rows[key] = row
            continue
        dedupe_groups += 1
        duplicate_rows_dropped += 1
        prev_rank = _row_source_rank(prev)
        row_rank = _row_source_rank(row)
        if row_rank > prev_rank:
            canonical_rows[key] = _merge_duplicate_import_rows(row, prev)
        else:
            canonical_rows[key] = _merge_duplicate_import_rows(prev, row)
        source_duplicate_rows_dropped += 1

    final_rows = sorted([*canonical_rows.values(), *carry_rows], key=_row_sort_dt, reverse=True)
    if final_rows:
        _set_trading_journal_rows(final_rows)
        if local_balances:
            state = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
            excel_bal = state.get("excel_account_balances") if isinstance(state, dict) else []
            merged_balances = [*([b for b in excel_bal if isinstance(b, dict)] if isinstance(excel_bal, list) else []), *local_balances]
            state["excel_account_balances"] = merged_balances
            _save_json_file(TRADING_JOURNAL_STATE_PATH, state)
            balances = merged_balances
    else:
        errors.append({"file": "", "path": "sources", "error": "Imported 0 rows; keeping existing journal data."})

    rows_by_source: Dict[str, int] = defaultdict(int)
    rows_by_asset_class: Dict[str, int] = defaultdict(int)
    for row in _get_trading_journal_rows():
        if not isinstance(row, dict):
            continue
        rows_by_source[str(row.get("source") or "unknown")] += 1
        rows_by_asset_class[str(row.get("asset_class") or "unknown")] += 1
    quarantined_rows = sum(
        1
        for row in _get_trading_journal_rows()
        if isinstance(row, dict) and str(row.get("status") or "").strip().lower() == "invalid_time_order"
    )
    diagnostics.update(
        {
            "rows_total": len(_get_trading_journal_rows()),
            "rows_by_source": dict(rows_by_source),
            "rows_by_asset_class": dict(rows_by_asset_class),
            "duplicate_rows_dropped": duplicate_rows_dropped,
            "source_duplicate_rows_dropped": source_duplicate_rows_dropped,
            "dedupe_groups": dedupe_groups,
            "ignored_local_workbooks": ignored_local_workbooks,
            "quarantined_rows": quarantined_rows,
            "errors": errors,
            "last_sync": {
                "source_mode": source_mode,
                "updated_at": _utc_now_iso(),
                "ok": bool(imported_any),
                "balances_found": len(balances),
                "local_import_enabled": local_enabled,
            },
        }
    )
    _set_trading_journal_diagnostics(diagnostics)
    return {
        "ok": bool(imported_any),
        "message": "Done" if imported_any else "No rows imported from configured sources.",
        "rows_imported": len(_get_trading_journal_rows()),
        "balances_found": len(balances),
        "local_workbooks_seen": diagnostics["local_workbooks_seen"],
        "dropbox_workbooks_seen": diagnostics["dropbox_workbooks_seen"],
        "duplicate_rows_dropped": duplicate_rows_dropped,
        "source_duplicate_rows_dropped": source_duplicate_rows_dropped,
        "dedupe_groups": dedupe_groups,
        "ignored_local_workbooks": ignored_local_workbooks,
        "errors": errors,
        "diagnostics": diagnostics,
    }
def _get_excel_account_balances() -> List[Dict[str, object]]:
    state = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
    items = state.get("excel_account_balances") if isinstance(state, dict) else []
    return items if isinstance(items, list) else []


def _to_float(value: object) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms_to_iso(value: object) -> Optional[str]:
    try:
        ms = int(float(value))
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _epoch_or_iso_to_iso(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw <= 0:
            return None
        if raw >= 1_000_000_000_000:
            return datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc).isoformat()
        if raw >= 1_000_000_000:
            return datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return None
    numeric = _to_float(text)
    if numeric is not None:
        return _epoch_or_iso_to_iso(numeric)
    try:
        return pd.to_datetime(text, utc=True).isoformat()
    except Exception:
        return None


def _journal_rows_from_bybit_execution(entry: Dict[str, object]) -> List[Dict[str, object]]:
    account = str(entry.get("account") or "unknown").strip().lower()
    category = str(entry.get("category") or "").strip().lower()
    symbol = str(entry.get("symbol") or "").strip().upper()
    order_id = str(entry.get("orderId") or "")
    exec_id = str(entry.get("execId") or "")
    if not symbol or not order_id or not exec_id:
        return []
    qty = _to_float(entry.get("execQty")) or 0.0
    exec_price = _to_float(entry.get("execPrice"))
    exec_fee = _to_float(entry.get("execFee")) or 0.0
    exec_pnl = _to_float(entry.get("execPnl")) or 0.0
    exec_time_raw = _to_float(entry.get("execTime"))
    close_time = None
    if exec_time_raw:
        close_time = datetime.fromtimestamp(exec_time_raw / 1000, tz=timezone.utc).isoformat()
    side = str(entry.get("side") or "")
    timeframe = _normalize_timeframe(entry.get("timeframe"))
    is_test_trade = _normalize_test_trade_flag(
        entry.get("is_test_trade", entry.get("test_trade", entry.get("test")))
    )
    if not timeframe:
        ctx = _lookup_trade_context_for_journal_row(
            {"raw_refs": {"orderId": order_id, "orderLinkId": entry.get("orderLinkId")}}
        )
        if isinstance(ctx, dict):
            timeframe = _normalize_timeframe(ctx.get("timeframe"))
            if is_test_trade is None:
                is_test_trade = _normalize_test_trade_flag(ctx.get("is_test_trade"))
    return [
        {
            "id": f"bybit:{account}:{category}:{symbol}:{order_id}:{exec_id}",
            "source": "bybit",
            "account": account,
            "account_label": f"Bybit {account.title()}",
            "asset_class": "crypto",
            "symbol": symbol,
            "side": side.title() if side else "",
            "status": "closed" if exec_pnl != 0 else "filled",
            "open_time": close_time,
            "close_time": close_time,
            "entry_price": exec_price,
            "exit_price": exec_price,
            "qty": qty,
            "qty_unit": "native",
            "notional_usd": (exec_price or 0.0) * qty,
            "commission": exec_fee,
            "commission_currency": "USDT",
            "fees": exec_fee,
            "fee_currency": "USDT",
            "realized_pnl": exec_pnl,
            "realized_pnl_currency": str(entry.get("currency") or "USDT"),
            "strategy_tag": "",
            "notes": "",
            "timeframe": timeframe,
            "is_test_trade": is_test_trade,
            "metrics": {k: v for k, v in {"timeframe": timeframe, "is_test_trade": is_test_trade}.items() if v not in ("", None)},
            "raw_refs": {"orderId": order_id, "execIds": [exec_id]},
        }
    ]


def _journal_rows_from_oanda_order_fill(entry: Dict[str, object]) -> List[Dict[str, object]]:
    account = str(entry.get("account") or "unknown").strip().lower()
    tx_id = str(entry.get("id") or "").strip()
    symbol = str(entry.get("instrument") or "").strip().upper()
    if not tx_id or not symbol:
        return []
    legs = _OANDA_OPEN_TRADE_LEGS.setdefault(account, {})
    close_time = str(entry.get("time") or "")
    tx_order_id = str(entry.get("orderID") or "").strip()
    tx_id = str(entry.get("id") or "").strip()

    context_cache: Dict[Tuple[str, str, str, str], Optional[Dict[str, object]]] = {}
    contexts = _load_trade_contexts()
    def _context_registry_key(trade_id: Optional[str] = None) -> str:
        return _stable_registry_key([account, tx_order_id, trade_id or "", symbol])

    def _resolve_context_for_fill(trade_id: Optional[str] = None) -> Optional[Dict[str, object]]:
        warning_key = (account, tx_id, tx_order_id, str(trade_id or "").strip())
        if warning_key in context_cache:
            return context_cache[warning_key]
        refs = {"orderId": tx_order_id, "transactionId": tx_id}
        if trade_id:
            refs["tradeId"] = trade_id
        ctx = _lookup_trade_context_for_journal_row({"raw_refs": refs})
        if isinstance(ctx, dict):
            _update_unresolved_registry(
                family="oanda_context",
                key=_context_registry_key(trade_id),
                details={"status": "resolved"},
                resolved=True,
                resolution_source="direct_refs",
            )
            context_cache[warning_key] = ctx
            return ctx
        side_hint = _normalize_side_for_comparison("buy" if (_to_float(entry.get("units")) or 0.0) >= 0 else "sell")
        open_leg = legs.get(str(trade_id or "").strip()) if trade_id else None
        if isinstance(open_leg, dict):
            open_refs = {
                "orderId": open_leg.get("order_id"),
                "transactionId": open_leg.get("transaction_id"),
                "tradeId": trade_id,
            }
            ctx = _lookup_trade_context_for_journal_row({"raw_refs": open_refs})
            if isinstance(ctx, dict):
                _update_unresolved_registry(
                    family="oanda_context",
                    key=_context_registry_key(trade_id),
                    details={"status": "resolved"},
                    resolved=True,
                    resolution_source="open_leg_refs",
                )
                context_cache[warning_key] = ctx
                return ctx
        ctx = _lookup_trade_context_by_market_window(
            {
                "broker": "oanda",
                "account": account,
                "instrument": symbol,
                "side": side_hint,
                "close_time": close_time,
            },
            include_inactive=True,
            max_window_seconds=6 * 60 * 60,
        )
        if isinstance(ctx, dict):
            persisted = _upsert_trade_context(
                {
                    "broker": "oanda",
                    "account": account,
                    "instrument": symbol,
                    "side": ctx.get("side") or side_hint,
                    "status": ctx.get("status") or "ACTIVE",
                    "order_id": tx_order_id or ctx.get("order_id"),
                    "trade_id": trade_id or ctx.get("trade_id"),
                    "transaction_id": tx_id or ctx.get("transaction_id"),
                    "timeframe": _normalize_timeframe(ctx.get("timeframe")),
                    "stop_loss": ctx.get("stop_loss"),
                    "take_profit": ctx.get("take_profit"),
                }
            )
            _update_unresolved_registry(
                family="oanda_context",
                key=_context_registry_key(trade_id),
                details={"status": "resolved"},
                resolved=True,
                resolution_source="market_window",
            )
            context_cache[warning_key] = persisted
            return persisted

        candidates = []
        for item in contexts:
            if str(item.get("broker") or "").strip().lower() != "oanda":
                continue
            if str(item.get("account") or "").strip().lower() != account:
                continue
            if str(item.get("instrument") or "").strip().upper() != symbol:
                continue
            if side_hint and _normalize_side_for_comparison(item.get("side")) not in {"", side_hint}:
                continue
            refs_hit = (
                (tx_order_id and str(item.get("order_id") or "").strip() == tx_order_id)
                or (trade_id and str(item.get("trade_id") or "").strip() == str(trade_id).strip())
                or (tx_id and str(item.get("transaction_id") or "").strip() == tx_id)
            )
            has_ctx_refs = bool(
                str(item.get("order_id") or "").strip()
                or str(item.get("trade_id") or "").strip()
                or str(item.get("transaction_id") or "").strip()
            )
            if refs_hit or not has_ctx_refs or not (tx_order_id or trade_id or tx_id):
                candidates.append(item)
        if len(candidates) == 1:
            persisted = _upsert_trade_context(
                {
                    "broker": "oanda",
                    "account": account,
                    "instrument": symbol,
                    "side": candidates[0].get("side") or side_hint,
                    "status": candidates[0].get("status") or "ACTIVE",
                    "order_id": tx_order_id or candidates[0].get("order_id"),
                    "trade_id": trade_id or candidates[0].get("trade_id"),
                    "transaction_id": tx_id or candidates[0].get("transaction_id"),
                    "timeframe": _normalize_timeframe(candidates[0].get("timeframe")),
                    "stop_loss": candidates[0].get("stop_loss"),
                    "take_profit": candidates[0].get("take_profit"),
                }
            )
            _update_unresolved_registry(
                family="oanda_context",
                key=_context_registry_key(trade_id),
                details={"status": "resolved"},
                resolved=True,
                resolution_source="cross_link_inference",
            )
            context_cache[warning_key] = persisted
            return persisted
        unresolved_key = _context_registry_key(trade_id)
        if len(candidates) > 1:
            should_warn, _ = _update_unresolved_registry(
                family="oanda_context",
                key=unresolved_key,
                details={"reason": "ambiguous", "candidates": len(candidates)},
                resolved=False,
            )
            if should_warn:
                BYBIT_LOGGER.warning(
                    "OANDA_CONTEXT_AMBIGUOUS account=%s symbol=%s side=%s tx_id=%s order_id=%s trade_id=%s candidates=%s",
                    account,
                    symbol,
                    side_hint,
                    tx_id,
                    tx_order_id,
                    trade_id or "",
                    len(candidates),
                )
        else:
            should_warn, _ = _update_unresolved_registry(
                family="oanda_context",
                key=unresolved_key,
                details={"reason": "missing"},
                resolved=False,
            )
            if should_warn:
                BYBIT_LOGGER.warning(
                    "OANDA_CONTEXT_MISSING account=%s symbol=%s tx_id=%s order_id=%s trade_id=%s",
                    account,
                    symbol,
                    tx_id,
                    tx_order_id,
                    trade_id or "",
                )
        context_cache[warning_key] = None
        return None

    trade_opened = entry.get("tradeOpened") if isinstance(entry.get("tradeOpened"), dict) else None
    if isinstance(trade_opened, dict):
        trade_id = str(trade_opened.get("tradeID") or "").strip()
        opened_units = _to_float(trade_opened.get("units"))
        trade_ctx = _resolve_context_for_fill(trade_id)
        if trade_id and opened_units not in (None, 0):
            legs[trade_id] = {
                "open_time": close_time,
                "entry_price": _to_float(trade_opened.get("price")) or _to_float(entry.get("price")),
                "units": opened_units,
                "side": "Buy" if opened_units >= 0 else "Sell",
                "symbol": symbol,
                "account": account,
                "timeframe": _normalize_timeframe((trade_ctx or {}).get("timeframe")),
                "is_test_trade": _normalize_test_trade_flag((trade_ctx or {}).get("is_test_trade")),
                "stop_loss": (trade_ctx or {}).get("stop_loss"),
                "take_profit": (trade_ctx or {}).get("take_profit"),
                "order_id": tx_order_id,
                "transaction_id": tx_id,
            }

    close_legs: List[Dict[str, object]] = []
    trades_closed = entry.get("tradesClosed")
    if isinstance(trades_closed, list):
        close_legs.extend(item for item in trades_closed if isinstance(item, dict))
    trade_reduced = entry.get("tradeReduced")
    if isinstance(trade_reduced, dict):
        close_legs.append(trade_reduced)
    if not close_legs:
        _persist_oanda_fill_state()
        return []

    base_ctx = _resolve_context_for_fill()
    timeframe = _normalize_timeframe(entry.get("timeframe")) or _normalize_timeframe((base_ctx or {}).get("timeframe"))
    base_is_test_trade = _normalize_test_trade_flag(
        entry.get("is_test_trade", entry.get("test_trade", entry.get("test")))
    )
    if base_is_test_trade is None:
        base_is_test_trade = _normalize_test_trade_flag((base_ctx or {}).get("is_test_trade"))
    stop_loss = (base_ctx or {}).get("stop_loss")
    take_profit = (base_ctx or {}).get("take_profit")
    rows: List[Dict[str, object]] = []
    for idx, close_leg in enumerate(close_legs):
        trade_id = str(close_leg.get("tradeID") or "").strip()
        leg_units = abs(_to_float(close_leg.get("units")) or 0.0)
        open_leg = legs.get(trade_id) if trade_id else None
        leg_ctx = _resolve_context_for_fill(trade_id) or base_ctx
        entry_price = _to_float((open_leg or {}).get("entry_price"))
        open_time = str((open_leg or {}).get("open_time") or "") or None
        side = str((open_leg or {}).get("side") or ("Buy" if (_to_float(entry.get("units")) or 0.0) >= 0 else "Sell"))
        row_timeframe = _normalize_timeframe((open_leg or {}).get("timeframe")) or timeframe or _normalize_timeframe((leg_ctx or {}).get("timeframe"))
        row_stop_loss = (open_leg or {}).get("stop_loss") or stop_loss or (leg_ctx or {}).get("stop_loss")
        row_take_profit = (open_leg or {}).get("take_profit") or take_profit or (leg_ctx or {}).get("take_profit")
        row_is_test_trade = _normalize_test_trade_flag((open_leg or {}).get("is_test_trade"))
        if row_is_test_trade is None:
            row_is_test_trade = _normalize_test_trade_flag((leg_ctx or {}).get("is_test_trade"))
        if row_is_test_trade is None:
            row_is_test_trade = base_is_test_trade
        exit_price = _to_float(close_leg.get("price")) or _to_float(entry.get("price"))
        realized_pnl = (_to_float(close_leg.get("realizedPL")) or 0.0) + (_to_float(close_leg.get("financing")) or 0.0)
        fees = (
            abs(_to_float(entry.get("halfSpreadCost")) or 0.0)
            + abs(_to_float(entry.get("commission")) or 0.0)
            + abs(_to_float(entry.get("guaranteedExecutionFee")) or 0.0)
        )
        row_id_suffix = trade_id or f"{tx_id}:{idx}"
        row: Dict[str, object] = {
            "id": f"oanda:{account}:{symbol}:{row_id_suffix}:close",
            "source": "oanda",
            "account": account,
            "account_label": f"OANDA {account.title()}",
            "asset_class": "forex",
            "symbol": symbol,
            "side": side,
            "status": "closed",
            "open_time": open_time,
            "close_time": close_time,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": leg_units / 100000.0,
            "qty_raw": leg_units,
            "qty_unit": "lots",
            "notional_usd": None,
            "commission": fees,
            "commission_currency": "AUD",
            "fees": fees,
            "fee_currency": "AUD",
            "realized_pnl": realized_pnl,
            "realized_pnl_currency": str(entry.get("accountCurrency") or ""),
            "balance_after_trade": _to_float(entry.get("accountBalance")),
            "strategy_tag": "",
            "notes": "" if entry_price is not None and open_time else "partial_oanda_close_missing_open_leg",
            "timeframe": row_timeframe,
            "stop_loss": row_stop_loss,
            "take_profit": row_take_profit,
            "is_test_trade": row_is_test_trade,
            "metrics": {k: v for k, v in {"timeframe": row_timeframe, "is_test_trade": row_is_test_trade}.items() if v not in ("", None)},
            "raw_refs": {"transactionId": tx_id, "orderId": entry.get("orderID"), "tradeId": trade_id},
        }
        rows.append(row)
        if trade_id and open_leg and leg_units >= abs(_to_float(open_leg.get("units")) or 0.0):
            legs.pop(trade_id, None)
    _persist_oanda_fill_state()
    return rows


def _build_state_backup_payload() -> bytes:
    alerts_payload = {
        "bybit": {"alerts": bybit_monitor.get_custom_alerts(force=True)},
        "oanda": {"alerts": oanda_monitor.get_custom_alerts(force=True)},
    }
    payload = {
        "alerts": alerts_payload,
        "watchlist": _get_watchlist(),
        "pending_webhooks": _load_pending_webhooks(),
        "trade_contexts": _load_trade_contexts(),
        "trading_journal": _load_json_file(TRADING_JOURNAL_PATH, {"items": []}),
        "trading_journal_state": _load_json_file(TRADING_JOURNAL_STATE_PATH, {}),
        "trading_journal_import_cache": _load_json_file(TRADING_JOURNAL_IMPORT_CACHE_PATH, {}),
        "monthly_aud_revaluation": _load_json_file(MONTHLY_AUD_REVALUATION_PATH, {"items": []}),
        "monthly_aud_revaluation_state": _load_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, {}),
        "oanda_fill_state": _load_json_file(OANDA_FILL_STATE_PATH, {}),
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _dropbox_download_bytes(path: str) -> bytes:
    payload = download_bytes(path)
    _record_outbound_traffic(
        "dropbox",
        bytes_sent=len(path),
        bytes_received=len(payload),
        context=f"download:{path}",
    )
    return payload


def _dropbox_upload_bytes(path: str, payload: bytes) -> None:
    upload_bytes(path, payload)
    _record_outbound_traffic(
        "dropbox",
        bytes_sent=len(path) + len(payload),
        bytes_received=0,
        context=f"upload:{path}",
    )


def _schedule_dropbox_upload_state_backup() -> None:
    if not DROPBOX_SYNC_ENABLED:
        return

    global _DROPBOX_UPLOAD_TIMER
    with _DROPBOX_UPLOAD_TIMER_LOCK:
        if _DROPBOX_UPLOAD_TIMER is not None:
            try:
                _DROPBOX_UPLOAD_TIMER.cancel()
            except Exception:
                pass
            _DROPBOX_UPLOAD_TIMER = None

        def _run_upload() -> None:
            try:
                payload = _build_state_backup_payload()
                _dropbox_upload_bytes(DROPBOX_BACKUP_PATH, payload)
                BYBIT_LOGGER.info("Dropbox backup uploaded to %s", DROPBOX_BACKUP_PATH)
            except Exception as exc:  # pragma: no cover - network failure
                BYBIT_LOGGER.error("Dropbox backup failed: %s", exc)

        t = threading.Timer(DROPBOX_SYNC_DEBOUNCE_SECONDS, _run_upload)
        t.daemon = True
        _DROPBOX_UPLOAD_TIMER = t
        t.start()


def _restore_alerts_payload(data: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Backup payload must be a JSON object.")
    alerts_payload = data
    if "alerts" in data:
        alerts_payload = data.get("alerts")
        if not isinstance(alerts_payload, dict):
            raise HTTPException(status_code=400, detail="Backup missing alerts section.")
    bybit_block = alerts_payload.get("bybit") if isinstance(alerts_payload, dict) else None
    oanda_block = alerts_payload.get("oanda") if isinstance(alerts_payload, dict) else None
    if not isinstance(bybit_block, dict) or not isinstance(oanda_block, dict):
        raise HTTPException(status_code=400, detail="Backup alerts format invalid.")
    if not isinstance(bybit_block.get("alerts"), list) or not isinstance(
        oanda_block.get("alerts"), list
    ):
        raise HTTPException(status_code=400, detail="Backup alerts list missing.")

    watchlist_items: List[str] = []
    if "watchlist" in data:
        if not isinstance(data["watchlist"], list):
            raise HTTPException(status_code=400, detail="Backup watchlist must be a list.")
        watchlist_items = _normalize_watchlist(data["watchlist"])

    pending_restored: List[Dict[str, object]] = []
    if "pending_webhooks" in data:
        pending_restored = _replace_pending_webhooks(data["pending_webhooks"])
    trade_contexts_restored = 0
    if "trade_contexts" in data and isinstance(data["trade_contexts"], (list, dict)):
        trade_context_items = data["trade_contexts"]
        if isinstance(trade_context_items, dict):
            trade_context_items = trade_context_items.get("items", [])
        if isinstance(trade_context_items, list):
            cleaned = [dict(entry) for entry in trade_context_items if isinstance(entry, dict)]
            pruned = _prune_trade_contexts(cleaned)
            _save_trade_contexts(pruned)
            trade_contexts_restored = len(pruned)

    journal_restored = 0
    journal_sanitized = 0
    if "trading_journal" in data and isinstance(data["trading_journal"], (dict, list)):
        _save_json_file(TRADING_JOURNAL_PATH, data["trading_journal"])
        rows = _get_trading_journal_rows()
        journal_restored = len(rows)
        sanitized_rows, sanitize_stats = _sanitize_bybit_demo_rows(rows)
        if int(sanitize_stats.get("changed", 0)):
            _set_trading_journal_rows(sanitized_rows)
            journal_sanitized = int(sanitize_stats.get("deduped_by_order_id", 0)) + int(
                sanitize_stats.get("deduped_by_fingerprint", 0)
            )

    if "trading_journal_state" in data and isinstance(data["trading_journal_state"], dict):
        _save_json_file(TRADING_JOURNAL_STATE_PATH, data["trading_journal_state"])

    if "trading_journal_import_cache" in data and isinstance(data["trading_journal_import_cache"], dict):
        _save_json_file(TRADING_JOURNAL_IMPORT_CACHE_PATH, data["trading_journal_import_cache"])

    monthly_rows_restored = 0
    if "monthly_aud_revaluation" in data and isinstance(data["monthly_aud_revaluation"], (dict, list)):
        _save_json_file(MONTHLY_AUD_REVALUATION_PATH, data["monthly_aud_revaluation"])
        monthly_rows_restored = len(_get_monthly_aud_revaluation_rows())
    if "monthly_aud_revaluation_state" in data and isinstance(data["monthly_aud_revaluation_state"], dict):
        _save_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, data["monthly_aud_revaluation_state"])
    oanda_fill_state_restored = False
    if "oanda_fill_state" in data and isinstance(data["oanda_fill_state"], dict):
        _save_json_file(OANDA_FILL_STATE_PATH, data["oanda_fill_state"])
        _restore_oanda_fill_state_on_startup()
        oanda_fill_state_restored = True

    bybit_restored = bybit_monitor.replace_custom_alerts(bybit_block["alerts"], strict=False)
    oanda_restored = oanda_monitor.replace_custom_alerts(oanda_block["alerts"])
    invalid_bybit_restored = max(0, len(bybit_block["alerts"]) - len(bybit_restored))
    _set_watchlist(watchlist_items)
    return {
        "bybit_restored": len(bybit_restored),
        "bybit_invalid_skipped": invalid_bybit_restored,
        "oanda_restored": len(oanda_restored),
        "watchlist_restored": len(watchlist_items),
        "pending_webhooks_restored": len(pending_restored),
        "trade_contexts_restored": trade_contexts_restored,
        "oanda_fill_state_restored": oanda_fill_state_restored,
        "journal_rows_restored": journal_restored,
        "journal_rows_sanitized": journal_sanitized,
        "monthly_aud_revaluation_rows_restored": monthly_rows_restored,
    }


async def _dropbox_restore_state_backup_on_startup() -> None:
    if not DROPBOX_SYNC_ENABLED:
        _STARTUP_STATE_RESTORE_DONE.set()
        return
    try:
        payload = await asyncio.to_thread(download_bytes, DROPBOX_BACKUP_PATH)
        data = json.loads(payload.decode("utf-8"))
        restored = _restore_alerts_payload(data)
        active_folder, _ = await asyncio.to_thread(_resolve_trading_journal_dropbox_folder)
        workbook_stats = await asyncio.to_thread(_sanitize_bybit_demo_workbook, active_folder)
        oanda_repaired_rows = _repair_persisted_oanda_trade_rows()
        _schedule_dropbox_upload_state_backup()
        BYBIT_LOGGER.info(
            "Dropbox restore complete: bybit=%s skipped_invalid_bybit=%s oanda=%s watchlist=%s pending=%s trade_contexts_restored=%s oanda_fill_state_restored=%s journal_rows=%s journal_sanitized=%s workbook_deduped=%s oanda_rows_repaired=%s",
            restored["bybit_restored"],
            restored.get("bybit_invalid_skipped", 0),
            restored["oanda_restored"],
            restored["watchlist_restored"],
            restored["pending_webhooks_restored"],
            restored.get("trade_contexts_restored", 0),
            restored.get("oanda_fill_state_restored", False),
            restored.get("journal_rows_restored", 0),
            restored.get("journal_rows_sanitized", 0),
            int(workbook_stats.get("deduped_by_order_id", 0)) + int(workbook_stats.get("deduped_by_fingerprint", 0)),
            oanda_repaired_rows,
        )
    except FileNotFoundError:
        BYBIT_LOGGER.info("Dropbox restore skipped; no backup found at %s", DROPBOX_BACKUP_PATH)
    except Exception as exc:  # pragma: no cover - startup failure
        BYBIT_LOGGER.error("Dropbox restore failed: %s", exc)
    finally:
        _STARTUP_STATE_RESTORE_DONE.set()


@dataclass
class ManagedScript:
    """Represents a runnable Python script managed by the service."""

    name: str
    path: Path
    category: str = "Other"
    log_file: Optional[Path] = None
    process: Optional[asyncio.subprocess.Process] = None
    port: Optional[int] = None
    _log_lines: List[str] = field(default_factory=list)
    last_output_at: Optional[float] = None
    last_start_attempt_at: Optional[float] = None
    last_start_error: Optional[str] = None
    last_exit_code: Optional[int] = None
    last_exit_reason: Optional[str] = None
    last_spawn_command: Optional[List[str]] = None
    last_spawn_cwd: Optional[str] = None
    is_starting: bool = False
    startup_started_at: Optional[float] = None
    startup_completed_at: Optional[float] = None
    pid: Optional[int] = None
    startup_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_summary(self) -> Dict[str, object]:
        startup_pending = bool(self.startup_task is not None and not self.startup_task.done())
        return {
            "id": self.name,
            "name": self.name,
            "label": friendly_script_label(self.name),
            "path": str(self.path),
            "category": self.category,
            "running": self.is_running,
            "starting": self.is_starting or startup_pending,
            "port": self.port,
            "pid": self.pid,
            "return_code": None if self.process is None else self.process.returncode,
            "open_url": script_open_url(self),
            "logs_url": script_logs_url(self.name),
            "last_output_at": self.last_output_at,
            "last_start_attempt_at": self.last_start_attempt_at,
            "last_start_error": self.last_start_error,
            "last_exit_code": self.last_exit_code,
            "last_exit_reason": self.last_exit_reason,
            "last_spawn_command": self.last_spawn_command,
            "last_spawn_cwd": self.last_spawn_cwd,
            "startup_started_at": self.startup_started_at,
            "startup_completed_at": self.startup_completed_at,
            "standalone": self.name in STANDALONE_SCRIPTS,
        }

    def add_log(self, line: str) -> None:
        cleaned = line.rstrip("\n")
        if cleaned:
            if not re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s", cleaned):
                try:
                    now = datetime.now(ZoneInfo(APP_TIMEZONE))
                except Exception:
                    now = datetime.now(ZoneInfo("Australia/Brisbane"))
                cleaned = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {cleaned}"
            self._log_lines.append(cleaned)
            if len(self._log_lines) > MAX_LOG_LINES:
                self._log_lines = self._log_lines[-MAX_LOG_LINES :]
            self.last_output_at = time.time()

    def logs(self) -> List[str]:
        if self.log_file is not None:
            try:
                if self.log_file.exists():
                    stat = self.log_file.stat()
                    content = self.log_file.read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    if lines:
                        self.last_output_at = stat.st_mtime
                    return lines[-MAX_LOG_LINES :]
            except Exception as exc:  # pragma: no cover - defensive fallback
                return [f"Unable to read log file {self.log_file}: {exc}"]

        return list(self._log_lines)

    def log_snapshot(self, cursor: int = 0) -> Dict[str, object]:
        lines = self.logs()
        safe_cursor = max(0, min(cursor, len(lines)))
        new_lines = lines[safe_cursor:]
        return {
            "lines": new_lines,
            "cursor": safe_cursor + len(new_lines),
            "total": len(lines),
            "last_output_at": self.last_output_at,
        }

    async def start(self, *, ignore_starting: bool = False) -> None:
        if self.is_running:
            return
        if self.is_starting and not ignore_starting:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"Script not found: {self.path}")

        self.last_start_attempt_at = time.time()
        self.last_start_error = None
        self.last_exit_code = None
        self.last_exit_reason = None
        self.is_starting = True
        self.startup_started_at = self.last_start_attempt_at
        self.startup_completed_at = None
        self.pid = None
        self.add_log("Starting script...")
        self.add_log("Spawning subprocess...")
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{BASE_DIR}:{current_pythonpath}" if current_pythonpath else str(BASE_DIR)
        )
        if self.name in WEB_APPS:
            if self.port is None:
                self.port = _allocate_port()
            env["PORT"] = str(self.port)
            env["HOST"] = "127.0.0.1"
            env["APP_BASE_PATH"] = f"/apps/{quote(self.name)}"

        command = [os.getenv("PYTHON", "python"), "-u", str(self.path)]
        self.last_spawn_command = command
        self.last_spawn_cwd = str(self.path.parent)
        self.add_log(f"Command: {' '.join(command)}")
        self.add_log(f"Working directory: {self.last_spawn_cwd}")
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self.process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.last_spawn_cwd,
                    env=env,
                    creationflags=creationflags,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError as exc:
            self.last_start_error = "Timed out waiting for subprocess spawn."
            self.is_starting = False
            self.startup_completed_at = time.time()
            self.add_log(self.last_start_error)
            raise RuntimeError(self.last_start_error) from exc
        except Exception as exc:
            self.last_start_error = str(exc)
            self.is_starting = False
            self.startup_completed_at = time.time()
            self.add_log(f"Failed to start: {exc}")
            raise

        self.pid = self.process.pid
        self.add_log(f"Spawned PID {self.pid}. Waiting for monitor output...")
        asyncio.create_task(self._capture_output())

    async def _capture_output(self) -> None:
        assert self.process is not None
        if self.process.stdout is None:
            return

        saw_output = False
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                if not saw_output:
                    saw_output = True
                    self.is_starting = False
                    self.startup_completed_at = time.time()
                self.add_log(line.decode("utf-8", errors="replace"))
        finally:
            await self.process.wait()
            self.last_exit_code = self.process.returncode
            if not saw_output and self.process.returncode is not None:
                self.last_exit_reason = "Process exited before producing startup output."
                self.add_log(
                    f"Process exited before producing startup output (exit code {self.process.returncode})."
                )
            elif self.last_exit_reason is None:
                self.last_exit_reason = (
                    "Process exited unexpectedly." if self.process.returncode else None
                )
            self.is_starting = False
            self.startup_completed_at = self.startup_completed_at or time.time()
            self.pid = None
            self.port = None

    async def stop(self) -> None:
        if not self.is_running:
            return
        assert self.process is not None
        if os.name == "nt" and self.process.pid:
            try:
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except Exception:
                self.process.terminate()
        else:
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.is_starting = False
        self.startup_completed_at = time.time()
        self.pid = None


@dataclass
class OandaHistoryJob:
    job_id: str
    status: str
    created_at: float
    updated_at: float
    params: Dict[str, object]
    output_path: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class BybitHistoryJob:
    job_id: str
    status: str
    created_at: float
    updated_at: float
    params: Dict[str, object]
    output_path: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class CoinspotHistoryJob:
    job_id: str
    status: str
    created_at: float
    updated_at: float
    params: Dict[str, object]
    output_path: Optional[Path] = None
    error: Optional[str] = None


def candidate_entrypoints(app_dir: Path) -> List[Path]:
    app_name = app_dir.name
    candidates: List[str] = []

    if app_name in ENTRY_OVERRIDES:
        candidates.extend(ENTRY_OVERRIDES[app_name])

    candidates.extend(
        [
            "main.py",
            "app.py",
            "run.py",
            "server.py",
            f"{app_name}.py",
            "wsgi.py",
        ]
    )

    seen: set[str] = set()
    ordered: List[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(app_dir / candidate)
    return ordered


def categorize_script(script_path: Path) -> str:
    """Return a high-level category name for ``script_path``."""

    folder = script_path.parent.name.lower()
    filename = script_path.name.lower()
    full = f"{folder}/{filename}"

    if any(keyword in folder for keyword in ("fx", "oanda", "forex")):
        return "Forex"

    crypto_keywords = (
        "crypto",
        "bybit",
        "coinspot",
        "ivin",
    )
    if any(keyword in folder or keyword in filename for keyword in crypto_keywords):
        return "Crypto"

    return "Other"


def _encoded_script_name(script_name: str) -> str:
    """Encode a script name for safe URL usage while keeping slashes intact."""

    return quote(script_name, safe="/")


def script_open_url(script: ManagedScript) -> str:
    """Return the preferred UI URL for a script."""

    if script.name == "trading-journal":
        return "/trading-journal"
    if script.name in WEB_APPS:
        return f"/apps/{_encoded_script_name(script.name)}"
    return f"/scripts/view/{_encoded_script_name(script.name)}"


def script_logs_url(script_name: str) -> str:
    """Return the JSON logs API endpoint for a script."""

    return f"/logs/{_encoded_script_name(script_name)}"


FRIENDLY_SCRIPT_LABELS: Dict[str, str] = {
    "PUSH": "Push",
    "bybit_trigger_bounce_trader": "Bounce Trader",
    "bybithistory-clone": "History",
    "coinspot-clone": "History",
    "download_video": "Video Downloader",
    "extractor": "Extractor",
    "forextester": "Forex Tester",
    "fxweekend-clone": "FX Weekend",
    "ivindicator-clone": "IV Indicator",
    "journal": "Journal",
    "oanda_history-clone": "History",
    "pinescripts": "Pine Scripts",
    "trading-journal": "Trading Journal",
}

def get_merged_script_buttons() -> List[Dict[str, object]]:
    return [dict(btn) for btn in _profile_main_buttons()]


def get_merged_source_names() -> Set[str]:
    return _profile_merged_source_names()

_TITLE_UPPER = {"FX", "MT5", "OANDA", "BYBIT", "USDT", "IV"}


def friendly_script_label(name: str) -> str:
    """Human-friendly label for UI buttons."""

    if not name:
        return ""

    if name in FRIENDLY_SCRIPT_LABELS:
        return FRIENDLY_SCRIPT_LABELS[name]

    label = str(name).strip()

    label = re.sub(r"[-_]?clone$", "", label, flags=re.IGNORECASE)
    label = re.sub(r"[-_]?master$", "", label, flags=re.IGNORECASE)
    label = re.sub(r"[_\-]+", " ", label).strip()

    parts: List[str] = []
    for token in label.split():
        up = token.upper()
        if up in _TITLE_UPPER:
            parts.append(up)
        else:
            parts.append(token[:1].upper() + token[1:].lower())
    return " ".join(parts)



def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def discover_scripts() -> List[ManagedScript]:
    """Return one ManagedScript per app folder using a chosen entrypoint."""

    scripts: List[ManagedScript] = []

    for app_dir in sorted(BASE_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        if app_dir.name.casefold() in SKIP_DIRS_NORMALIZED or app_dir.name.startswith("."):
            continue
        if app_dir.name.casefold() in HIDDEN_SCRIPTS:
            continue
        if not _profile_allows_script(app_dir.name):
            continue

        entry_path: Optional[Path] = None
        for candidate in candidate_entrypoints(app_dir):
            if candidate.exists() and candidate.is_file():
                entry_path = candidate
                break

        if entry_path is not None:
            scripts.append(
                ManagedScript(
                    name=app_dir.name,
                    path=entry_path,
                    category=categorize_script(entry_path),
                    log_file=LOG_FILE_OVERRIDES.get(app_dir.name),
                )
            )

    return scripts


class ScriptManager:
    """Keeps track of runnable scripts and their processes."""

    def __init__(self, scripts: Iterable[ManagedScript]):
        self._scripts: Dict[str, ManagedScript] = {script.name: script for script in scripts}
        self._aliases: Dict[str, str] = {}

        for script in scripts:
            self._register_aliases(script.name)
            self._register_aliases(script.path.name, canonical=script.name)
            self._register_aliases(script.path.stem, canonical=script.name)

    def _normalize(self, name: str) -> str:
        trimmed = name.strip().strip("/")
        return trimmed.replace("-", "_").casefold()

    def _register_aliases(self, alias: str, canonical: Optional[str] = None) -> None:
        target = alias if canonical is None else canonical
        normalized = self._normalize(alias)
        self._aliases.setdefault(normalized, target)

    def _resolve_name(self, name: str) -> str:
        normalized = self._normalize(name)
        if normalized in {self._normalize(n) for n in RETIRED_SCRIPT_NAMES}:
            raise HTTPException(status_code=410, detail=f"Script is retired and unavailable: {name}")

        if name in self._scripts:
            return name

        if normalized in self._aliases:
            return self._aliases[normalized]

        raise HTTPException(status_code=404, detail=f"Script not found: {name}")

    def list_scripts(self) -> List[Dict[str, object]]:
        items = [script.to_summary() for script in self._scripts.values()]
        return sorted(items, key=lambda s: str(s["name"]).lower())

    def get(self, name: str) -> ManagedScript:
        resolved = self._resolve_name(name)
        return self._scripts[resolved]

    @property
    def names(self) -> List[str]:
        return sorted(self._scripts.keys())

    async def start(self, name: str) -> Dict[str, object]:
        script = self.get(name)
        await script.start()
        return script.to_summary()

    async def stop(self, name: str) -> Dict[str, object]:
        script = self.get(name)
        await script.stop()
        return script.to_summary()

    def logs(self, name: str) -> List[str]:
        return self.get(name).logs()

    def log_snapshot(self, name: str, cursor: int = 0) -> Dict[str, object]:
        return self.get(name).log_snapshot(cursor)


script_manager = ScriptManager(discover_scripts())
app = FastAPI(title="TradingTools", version="1.0")


@app.middleware("http")
async def profile_router_guard(request: Request, call_next: Callable) -> Response:
    path = request.url.path
    if _render_blocks_path(path):
        wants_json = path.startswith("/api/") or "application/json" in str(request.headers.get("accept", "")).lower()
        return _local_only_disabled_response(path, as_json=wants_json)
    return await call_next(request)
OANDA_HISTORY_JOBS: Dict[str, OandaHistoryJob] = {}
BYBIT_HISTORY_JOBS: Dict[str, BybitHistoryJob] = {}
COINSPOT_HISTORY_JOBS: Dict[str, CoinspotHistoryJob] = {}
AUTOSTART_LOGGER = logging.getLogger("uvicorn.error")
DEFAULT_RENDER_AUTOSTART_SCRIPTS = "fxweekend-clone"
DEFAULT_LOCAL_AUTOSTART_SCRIPTS = "bybit_monitor,oanda_monitor,fxweekend-clone"

FXWEEKEND_SETTINGS_PATH = BASE_DIR / "fxweekend-clone" / "settings.json"
FXWEEKEND_DEFAULT_SETTINGS: Dict[str, object] = {
    "enabled": True,
    "trigger_weekday": 5,
    "cutoff_hour_dst": 5,
    "cutoff_hour_standard": 6,
    "check_interval_seconds": 60,
    "close_method": "positions",
    "dry_run": False,
    "instrument_allowlist": [],
}


def _force_fxweekend_enabled_on_startup() -> None:
    if _is_scanner_local_ui_mode():
        return
    payload = dict(FXWEEKEND_DEFAULT_SETTINGS)
    try:
        existing = _load_json_file(FXWEEKEND_SETTINGS_PATH, {})
        if isinstance(existing, dict):
            payload.update(existing)
    except Exception:
        pass
    payload["enabled"] = True
    FXWEEKEND_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FXWEEKEND_SETTINGS_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _compute_autostart_scripts() -> List[str]:
    """Resolve autostart script names from env.

    AUTOSTART_SCRIPTS supports:
      - comma-separated script names
      - ALL or * to start every discovered script
    AUTOSTART_EXCLUDE may contain a comma-separated list of script names to skip.
    """

    raw_value = os.getenv("AUTOSTART_SCRIPTS")
    if _is_scanner_local_ui_mode():
        return []
    if raw_value is None:
        raw_value = (
            DEFAULT_LOCAL_AUTOSTART_SCRIPTS
            if APP_PROFILE == "local"
            else DEFAULT_RENDER_AUTOSTART_SCRIPTS
        )

    normalized = (raw_value or "").strip()
    if normalized.upper() in {"NONE", "OFF", "DISABLED"}:
        normalized = ""

    autostart_raw = [name.strip() for name in normalized.split(",") if name.strip()]
    autostart_exclude = {
        name.strip()
        for name in (os.getenv("AUTOSTART_EXCLUDE") or "").split(",")
        if name.strip()
    }

    want_all = any(token.upper() == "ALL" or token == "*" for token in autostart_raw)
    names = list(script_manager.names) if want_all else list(autostart_raw)

    if autostart_exclude:
        names = [name for name in names if name not in autostart_exclude]
    filtered: List[str] = []
    seen: Set[str] = set()
    for name in names:
        try:
            script = script_manager.get(name)
        except HTTPException:
            continue
        if script.name in seen:
            continue
        seen.add(script.name)
        filtered.append(script.name)
    return filtered


async def _run_startup_recovery_import_if_needed() -> None:
    if _is_scanner_local_ui_mode():
        return
    _set_trading_journal_sync_state(
        running=True,
        progress=10,
        message="Startup journal sync running…",
        ok=None,
        error=None,
        result=None,
    )
    oanda_recovery: Dict[str, object] = {}
    for account in ("live", "demo"):
        try:
            oanda_recovery[account] = await _recover_oanda_recent_fills(account)
        except Exception as exc:
            oanda_recovery[account] = {"ok": False, "error": str(exc)}
            _record_oanda_fill_diagnostic(
                account,
                poll_enabled=os.getenv("ENABLE_OANDA_FILL_POLL", "0") == "1",
                last_error=f"startup recovery failed: {exc}",
            )
            BYBIT_LOGGER.exception("OANDA startup recovery sync error account=%s: %s", account, exc)
    if ENABLE_BYBIT_DEMO_JOURNAL:
        try:
            await _run_bybit_closed_pnl_sync(
                account_mode="demo",
                reason="startup_recovery",
            )
        except Exception as exc:
            _record_bybit_demo_sync_status(
                last_checked_at=_utc_now_iso(),
                last_error=f"Startup recovery demo sync failed: {exc}",
            )
            BYBIT_LOGGER.exception("Bybit demo startup recovery sync error: %s", exc)
    try:
        result = await asyncio.to_thread(_import_trading_journal_from_sources)
        ok_flag = bool(result.get("ok", False)) if isinstance(result, dict) else False
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else {}
        rows_by_asset_class = diagnostics.get("rows_by_asset_class") if isinstance(diagnostics, dict) else {}
        _record_daily_trade_sync_status(
            last_attempt_at=_utc_now_iso(),
            last_success_at=_utc_now_iso() if ok_flag else None,
            last_error=None if ok_flag else str((result or {}).get("message") or "Startup import failed."),
            last_reason="startup_recovery",
            last_result={**(result or {}), "oanda_recovery": oanda_recovery},
        )
        _set_trading_journal_sync_state(
            running=False,
            progress=100,
            message="Startup journal sync complete." if ok_flag else str((result or {}).get("message") or "Startup import failed."),
            ok=ok_flag,
            error=None if ok_flag else str((result or {}).get("message") or "Startup import failed."),
            result=result,
            rows_imported=int((result or {}).get("rows_imported") or 0),
            rows_by_asset_class=rows_by_asset_class if isinstance(rows_by_asset_class, dict) else {},
            local_workbooks_seen=int((result or {}).get("local_workbooks_seen") or 0),
            dropbox_workbooks_seen=int((result or {}).get("dropbox_workbooks_seen") or 0),
            finished_at=_utc_now_iso(),
        )
    except Exception as exc:
        _record_daily_trade_sync_status(
            last_attempt_at=_utc_now_iso(),
            last_error=f"Startup import failed: {exc}",
            last_reason="startup_recovery",
            last_result={"oanda_recovery": oanda_recovery},
        )
        _set_trading_journal_sync_state(
            running=False,
            progress=100,
            message=f"Failed: {exc}",
            ok=False,
            error=str(exc),
            result={"oanda_recovery": oanda_recovery},
            rows_imported=0,
            rows_by_asset_class={},
            local_workbooks_seen=0,
            dropbox_workbooks_seen=0,
            finished_at=_utc_now_iso(),
        )


MONTHLY_AUD_REVAL_SYNC_INTERVAL_SECONDS = max(300, int(os.getenv("MONTHLY_AUD_REVAL_SYNC_INTERVAL_SECONDS", "3600") or "3600"))
STARTUP_RESTORE_WAIT_TIMEOUT_SECONDS = 120.0


async def _wait_for_startup_restore_signal(*, timeout: float, timeout_warning: str) -> bool:
    try:
        await asyncio.wait_for(_STARTUP_STATE_RESTORE_DONE.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        BYBIT_LOGGER.warning(timeout_warning)
        return False


async def _run_monthly_aud_revaluation_sync(*, reason: str) -> Dict[str, object]:
    started_at = _utc_now_iso()
    try:
        result = await sync_monthly_aud_revaluation(
            data_path=MONTHLY_AUD_REVALUATION_PATH,
            state_path=MONTHLY_AUD_REVALUATION_STATE_PATH,
            bybit_live_credentials=resolve_bybit_credentials_for("live"),
            oanda_config_provider=_get_oanda_config,
            logger=BYBIT_LOGGER,
        )
        if result.get("changed"):
            _schedule_dropbox_upload_state_backup()
        state_snapshot = _load_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, {})
        return {"ok": True, "reason": reason, "started_at": started_at, "finished_at": _utc_now_iso(), "state": state_snapshot, **result}
    except MonthlyAudRevalError as exc:
        state_snapshot = _load_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, {})
        BYBIT_LOGGER.error("%s reason=%s stage=%s detail=%s", exc.code, reason, getattr(exc, "stage", None), exc)
        return {
            "ok": False,
            "reason": reason,
            "code": exc.code,
            "stage": getattr(exc, "stage", None),
            "error": str(exc),
            "state": state_snapshot,
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
        }
    except Exception as exc:
        state_snapshot = _load_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, {})
        BYBIT_LOGGER.error("MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR reason=%s detail=%s", reason, exc)
        return {
            "ok": False,
            "reason": reason,
            "code": "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            "error": str(exc),
            "state": state_snapshot,
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
        }


async def _schedule_monthly_aud_revaluation_sync() -> None:
    await _wait_for_startup_restore_signal(
        timeout=STARTUP_RESTORE_WAIT_TIMEOUT_SECONDS,
        timeout_warning="MONTHLY_AUD_REVAL_STARTUP_WAIT_TIMEOUT proceeding without restore signal",
    )
    await _run_monthly_aud_revaluation_sync(reason="startup")
    while True:
        await asyncio.sleep(MONTHLY_AUD_REVAL_SYNC_INTERVAL_SECONDS)
        await _run_monthly_aud_revaluation_sync(reason="hourly")


async def _start_bybit_demo_closed_pnl_poll_after_restore() -> None:
    await _wait_for_startup_restore_signal(
        timeout=STARTUP_RESTORE_WAIT_TIMEOUT_SECONDS,
        timeout_warning="BYBIT_DEMO_CLOSED_PNL_STARTUP_WAIT_TIMEOUT proceeding without restore signal",
    )
    await _poll_bybit_demo_closed_pnl()


async def _start_startup_recovery_import_after_restore() -> None:
    await _wait_for_startup_restore_signal(
        timeout=STARTUP_RESTORE_WAIT_TIMEOUT_SECONDS,
        timeout_warning="STARTUP_RECOVERY_WAIT_TIMEOUT proceeding without restore signal",
    )
    await _run_startup_recovery_import_if_needed()


async def _run_daily_trade_history_sync(*, reason: str) -> Dict[str, object]:
    async with _DAILY_TRADE_SYNC_LOCK:
        started_at = _utc_now_iso()
        bybit_result: Dict[str, object] = {}
        oanda_result: Dict[str, object] = {}
        import_result: Optional[Dict[str, object]] = None
        errors: List[str] = []
        _record_daily_trade_sync_status(
            running=True,
            last_attempt_at=started_at,
            last_reason=reason,
            last_error=None,
        )

        for account in ("live", "demo"):
            try:
                oanda_result[account] = await _recover_oanda_recent_fills(account)
            except Exception as exc:
                errors.append(f"OANDA {account} recovery failed: {exc}")
                oanda_result[account] = {"ok": False, "error": str(exc)}

        if ENABLE_BYBIT_DEMO_JOURNAL:
            try:
                bybit_result["demo"] = await _run_bybit_closed_pnl_sync(account_mode="demo", reason=reason)
            except Exception as exc:
                errors.append(f"Bybit demo sync failed: {exc}")
                bybit_result["demo"] = {"ok": False, "error": str(exc)}
        try:
            bybit_result["live"] = await _run_bybit_closed_pnl_sync(account_mode="live", reason=reason)
        except Exception as exc:
            errors.append(f"Bybit live sync failed: {exc}")
            bybit_result["live"] = {"ok": False, "error": str(exc)}

        try:
            import_result = await asyncio.to_thread(_import_trading_journal_from_sources)
        except Exception as exc:
            errors.append(f"Dropbox workbook import failed: {exc}")
            import_result = {"ok": False, "error": str(exc)}

        import_ok = bool(import_result.get("ok", False)) if isinstance(import_result, dict) else False
        bybit_ok = all(bool(v.get("ok", False)) for v in bybit_result.values() if isinstance(v, dict))
        ok_flag = import_ok and bybit_ok and not errors
        monthly_result = await _run_monthly_aud_revaluation_sync(reason=f"daily:{reason}")

        finished_at = _utc_now_iso()
        _record_daily_trade_sync_status(
            running=False,
            last_reason=reason,
            last_attempt_at=started_at,
            last_success_at=finished_at if ok_flag else None,
            last_error="; ".join(errors) if errors else None,
            last_result={
                "ok": ok_flag,
                "bybit": bybit_result,
                "oanda": oanda_result,
                "import": import_result,
                "errors": errors,
                "monthly_aud_revaluation": monthly_result,
                "started_at": started_at,
                "finished_at": finished_at,
            },
        )
        return {
            "ok": ok_flag,
            "bybit": bybit_result,
            "oanda": oanda_result,
            "import": import_result,
            "errors": errors,
            "started_at": started_at,
            "finished_at": finished_at,
        }


async def _schedule_daily_trade_history_sync() -> None:
    if not DAILY_TRADE_SYNC_ENABLED:
        _record_daily_trade_sync_status(running=False, last_reason="disabled")
        return
    while True:
        try:
            now_local = datetime.now(ZoneInfo(DAILY_TRADE_SYNC_TIMEZONE))
        except Exception:
            now_local = datetime.now(ZoneInfo("Australia/Brisbane"))
        target_local = now_local.replace(
            hour=DAILY_TRADE_SYNC_HOUR,
            minute=DAILY_TRADE_SYNC_MINUTE,
            second=0,
            microsecond=0,
        )
        if target_local <= now_local:
            target_local = target_local + timedelta(days=1)
        sleep_seconds = max(5.0, (target_local - now_local).total_seconds())
        await asyncio.sleep(sleep_seconds)
        try:
            await _run_daily_trade_history_sync(reason="daily")
        except Exception as exc:
            _record_daily_trade_sync_status(
                running=False,
                last_attempt_at=_utc_now_iso(),
                last_error=f"Daily sync failed: {exc}",
                last_reason="daily",
            )


_SCANNER_SUPERVISOR_BACKOFF: Dict[str, float] = {}
_SCANNER_SUPERVISOR_BACKOFF_WINDOW: Dict[str, float] = {}
_SCANNER_SUPERVISOR_BASE_SECONDS = float(os.getenv("SCANNER_SUPERVISOR_BASE_SECONDS", "20") or 20)
_SCANNER_SUPERVISOR_MAX_BACKOFF_SECONDS = float(os.getenv("SCANNER_SUPERVISOR_MAX_BACKOFF_SECONDS", "300") or 300)


def _scanner_has_external_live_runtime(script_name: str) -> bool:
    if script_name == "bybit_monitor":
        payload = _scanner_status_payload(BYBIT_RUNTIME_STATUS_PATH)
    elif script_name == "oanda_monitor":
        payload = _scanner_status_payload(OANDA_RUNTIME_STATUS_PATH)
    else:
        return False
    if str(payload.get("ui_status")) != "running":
        return False
    runtime_pid = payload.get("pid")
    script_pid = None
    try:
        script_pid = script_manager.get(script_name).pid
    except Exception:
        pass
    return runtime_pid is not None and runtime_pid != script_pid


async def _supervise_autostart_scripts(names: List[str]) -> None:
    if APP_PROFILE != "local":
        return
    scanner_targets = [n for n in names if n in {"bybit_monitor", "oanda_monitor"}]
    if not scanner_targets:
        return
    while True:
        await asyncio.sleep(max(15.0, _SCANNER_SUPERVISOR_BASE_SECONDS))
        for name in scanner_targets:
            try:
                script = script_manager.get(name)
            except HTTPException:
                continue
            if script.is_running:
                _SCANNER_SUPERVISOR_BACKOFF.pop(name, None)
                continue
            if _scanner_has_external_live_runtime(name):
                continue
            now = time.time()
            retry_after = _SCANNER_SUPERVISOR_BACKOFF.get(name, 0.0)
            if retry_after and now < retry_after:
                continue
            AUTOSTART_LOGGER.warning("Scanner supervisor restarting %s", name)
            try:
                await script.start()
                _SCANNER_SUPERVISOR_BACKOFF.pop(name, None)
                _SCANNER_SUPERVISOR_BACKOFF_WINDOW.pop(name, None)
            except Exception as exc:
                prev_wait = _SCANNER_SUPERVISOR_BACKOFF_WINDOW.get(name, 0.0)
                current_wait = 15.0 if prev_wait <= 0 else min(_SCANNER_SUPERVISOR_MAX_BACKOFF_SECONDS, prev_wait * 2.0)
                _SCANNER_SUPERVISOR_BACKOFF_WINDOW[name] = current_wait
                _SCANNER_SUPERVISOR_BACKOFF[name] = now + current_wait
                AUTOSTART_LOGGER.error("Scanner supervisor failed to restart %s: %s", name, exc)


@app.on_event("startup")
async def _autostart_scripts() -> None:
    AUTOSTART_LOGGER.info(format_env_bootstrap_log(_MASTER_ENV_INFO))
    if _is_scanner_local_ui_mode():
        AUTOSTART_LOGGER.info(
            "SCANNER_LOCAL_UI_MODE=1: skipping non-scanner startup tasks and script autostart."
        )
        asyncio.create_task(_log_outbound_traffic_summary())
        asyncio.create_task(_poll_pending_webhook_invalidations())
        return

    _restore_bybit_closed_pnl_last_seen_from_state()
    _restore_oanda_fill_state_on_startup()
    _set_trading_journal_sync_state(
        running=True,
        progress=0,
        message="Startup journal sync queued…",
        ok=None,
        error=None,
        started_at=_utc_now_iso(),
        finished_at=None,
    )
    asyncio.create_task(_dropbox_restore_state_backup_on_startup())
    asyncio.create_task(_ensure_trading_journal_dropbox_templates())
    asyncio.create_task(_log_outbound_traffic_summary())
    asyncio.create_task(_start_startup_recovery_import_after_restore())
    asyncio.create_task(_schedule_daily_trade_history_sync())
    asyncio.create_task(_schedule_monthly_aud_revaluation_sync())
    asyncio.create_task(_poll_pending_webhook_invalidations())
    if ENABLE_BYBIT_DEMO_JOURNAL and ENABLE_BYBIT_DEMO_CLOSED_PNL_POLL:
        asyncio.create_task(_start_bybit_demo_closed_pnl_poll_after_restore())
    if not ENABLE_BYBIT_DEMO_JOURNAL:
        _purge_bybit_demo_journal_state()
    if os.getenv("ENABLE_BYBIT_FILL_POLL", "0") == "1":
        asyncio.create_task(_poll_bybit_fills())
    if os.getenv("ENABLE_OANDA_FILL_POLL", "0") == "1":
        asyncio.create_task(_start_oanda_fill_poll_after_delay())
    _force_fxweekend_enabled_on_startup()
    autostart_targets = _compute_autostart_scripts()
    AUTOSTART_LOGGER.info(
        "Resolved autostart scripts: %s",
        ", ".join(autostart_targets) if autostart_targets else "(none)",
    )
    for name in autostart_targets:
        try:
            script = script_manager.get(name)
        except HTTPException:
            continue

        if script.is_running:
            continue

        if script.name in WEB_APPS and script.port is None:
            script.port = _allocate_port()

        if script.startup_task is None or script.startup_task.done():
            script.startup_task = asyncio.create_task(_background_start(script))
    if APP_PROFILE == "local":
        asyncio.create_task(_supervise_autostart_scripts(autostart_targets))


@app.on_event("shutdown")
async def _log_local_master_shutdown() -> None:
    AUTOSTART_LOGGER.error(
        "LOCAL_MASTER_SHUTDOWN profile=%s scripts=%s",
        APP_PROFILE,
        script_manager.list_scripts(),
    )


def _log_local_master_atexit() -> None:
    AUTOSTART_LOGGER.error("LOCAL_MASTER_ATEXIT profile=%s", APP_PROFILE)


atexit.register(_log_local_master_atexit)


async def _start_oanda_fill_poll_after_delay() -> None:
    await asyncio.sleep(5)
    await _poll_oanda_fills()


async def _fetch_oanda_transactions_window(
    *, account_id: str, api_key: str, base_url: str, start: str, end: str
) -> List[Dict[str, object]]:
    if oanda_history_exporter is None:
        raise RuntimeError("OANDA history exporter module not available.")
    return await asyncio.to_thread(
        oanda_history_exporter.fetch_transactions,
        account_id,
        api_key,
        start,
        end,
        base_url,
    )


async def _collect_oanda_history_complete(
    *, account_id: str, api_key: str, base_url: str
) -> List[Dict[str, object]]:
    created_time = await _fetch_oanda_account_created_time(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
    )
    now = datetime.now(timezone.utc)
    end = now
    transactions: List[Dict[str, object]] = []
    seen_ids: set[object] = set()
    window = timedelta(days=365)

    def append_unique(items: List[Dict[str, object]]) -> None:
        for item in items:
            tx_id = item.get("id")
            if tx_id is not None:
                if tx_id in seen_ids:
                    continue
                seen_ids.add(tx_id)
            transactions.append(item)

    while end > created_time:
        start = max(created_time, end - window)
        batch = await _fetch_oanda_transactions_window(
            account_id=account_id,
            api_key=api_key,
            base_url=base_url,
            start=_format_oanda_timestamp(start),
            end=_format_oanda_timestamp(end),
        )
        append_unique(batch)
        end = start

    return transactions


async def _collect_oanda_history_range(
    *,
    account_id: str,
    api_key: str,
    base_url: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, object]]:
    """Collect OANDA transactions over an arbitrary time range by splitting into <=365-day windows."""
    transactions: List[Dict[str, object]] = []
    seen_ids: set[object] = set()
    window = timedelta(days=365)
    current_end = end

    def append_unique(items: List[Dict[str, object]]) -> None:
        for item in items:
            tx_id = item.get("id")
            if tx_id is not None:
                if tx_id in seen_ids:
                    continue
                seen_ids.add(tx_id)
            transactions.append(item)

    while current_end > start:
        current_start = max(start, current_end - window)
        batch = await _fetch_oanda_transactions_window(
            account_id=account_id,
            api_key=api_key,
            base_url=base_url,
            start=_format_oanda_timestamp(current_start),
            end=_format_oanda_timestamp(current_end),
        )
        append_unique(batch)
        current_end = current_start

    return transactions


async def _run_oanda_history_export(job: OandaHistoryJob) -> None:
    job.status = "running"
    job.updated_at = time.time()
    try:
        if oanda_history_exporter is None:
            raise RuntimeError("OANDA history exporter module not available.")
        account_mode = str(job.params.get("account") or "").strip().lower()
        if account_mode not in {"live", "demo"}:
            raise ValueError("Bybit export requires explicit account=live|demo.")
        config = _get_oanda_history_config(account_mode)

        period = _normalize_period(job.params.get("period"))
        complete = bool(job.params.get("complete")) or period == "complete"

        if complete:
            transactions = await _collect_oanda_history_complete(
                account_id=config["account_id"],
                api_key=config["api_key"],
                base_url=config["base_url"],
            )
        else:
            end_dt = datetime.now(timezone.utc)

            if period:
                delta = OANDA_PERIOD_DELTAS.get(period)
                if delta is None:
                    raise ValueError(f"Unsupported period: {period}")
                start_dt = end_dt - delta
            else:
                days_value = job.params.get("days", 0)
                try:
                    days = int(days_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Export days must be a positive integer.") from exc
                if days <= 0:
                    raise ValueError("Export days must be a positive integer.")
                start_dt = end_dt - timedelta(days=days)

            # OANDA's time-based transactions query supports a maximum window of 365 days.
            # Split larger periods into yearly windows (same approach as "complete").
            if end_dt - start_dt > timedelta(days=365):
                transactions = await _collect_oanda_history_range(
                    account_id=config["account_id"],
                    api_key=config["api_key"],
                    base_url=config["base_url"],
                    start=start_dt,
                    end=end_dt,
                )
            else:
                transactions = await _fetch_oanda_transactions_window(
                    account_id=config["account_id"],
                    api_key=config["api_key"],
                    base_url=config["base_url"],
                    start=_format_oanda_timestamp(start_dt),
                    end=_format_oanda_timestamp(end_dt),
                )

        output_path = OANDA_HISTORY_EXPORT_ROOT / f"oanda_history_{job.job_id}.csv"
        await asyncio.to_thread(oanda_history_exporter.save_to_csv, transactions, output_path)
        if not output_path.exists():
            raise RuntimeError("OANDA history export failed to write CSV output.")
        job.output_path = output_path
        job.status = "done"
    except Exception as exc:
        BYBIT_LOGGER.exception("OANDA history export job failed id=%s error=%s", job.job_id, exc)
        job.status = "error"
        job.error = _sanitize_oanda_history_error(exc)
    finally:
        job.updated_at = time.time()


def _date_range_for_days(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


OANDA_PERIOD_DELTAS: Dict[str, relativedelta] = {
    "day": relativedelta(days=1),
    "week": relativedelta(days=7),
    "month": relativedelta(months=1),
    "year": relativedelta(years=1),
    "3y": relativedelta(years=3),
}

BYBIT_PERIOD_DELTAS: Dict[str, relativedelta] = {
    "day": relativedelta(days=1),
    "week": relativedelta(days=7),
    "month": relativedelta(months=1),
    "year": relativedelta(years=1),
    "3y": relativedelta(years=3),
}


def _normalize_period(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    period = value.strip().casefold()
    if period in {"day", "week", "month", "year", "3y", "complete"}:
        return period
    return None


def _date_range_for_period(period: str, *, max_days: Optional[int] = None) -> tuple[str, str]:
    """Return YYYY-MM-DD start/end for a named period.

    - period: day | week | month | year | 3y | complete
    - max_days: optional clamp for providers with a hard retention limit (e.g. Bybit 2 years).
    """
    end = datetime.now(timezone.utc)
    if period == "complete":
        if max_days is None:
            # Caller will handle true "complete" (e.g. OANDA uses account creation time).
            start = end
        else:
            start = end - timedelta(days=max_days)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    delta = OANDA_PERIOD_DELTAS.get(period)
    if delta is None:
        raise ValueError(f"Unknown period: {period}")
    start = end - delta

    if max_days is not None:
        earliest = end - timedelta(days=max_days)
        if start < earliest:
            start = earliest

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


async def _run_bybit_history_export(job: BybitHistoryJob) -> None:
    job.status = "running"
    job.updated_at = time.time()
    try:
        if bybit_history_fetcher is None:
            raise RuntimeError("Bybit history exporter module not available.")

        account_mode = str(job.params.get("account") or "").strip().lower()
        if account_mode not in {"live", "demo"}:
            raise ValueError("Bybit export requires explicit account=live|demo.")
        period = _normalize_period(job.params.get("period"))
        complete = bool(job.params.get("complete")) or period == "complete"

        # Bybit Demo Trading only retains ~7 days of orders/executions.
        # Live/Testnet supports up to ~2 years.
        max_days = 730
        if account_mode == "demo":
            max_days = 7

        if complete:
            start_date, end_date = _date_range_for_period("complete", max_days=max_days)
        elif period:
            start_date, end_date = _date_range_for_period(period, max_days=max_days)
        else:
            days_value = job.params.get("days")
            if days_value is None:
                raise ValueError("days is required unless complete is true.")
            try:
                days = int(days_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("days must be an integer.") from exc
            if days <= 0:
                raise ValueError("days must be greater than zero.")
            # Clamp to the Bybit API retention window.
            if days > max_days:
                days = max_days
            start_date, end_date = _date_range_for_days(days)

        def _export() -> Path:
            import shutil
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                prev_cwd = os.getcwd()
                prev_env = os.environ.get("BYBIT_ENV")
                if account_mode in {"demo", "testnet", "paper"}:
                    os.environ["BYBIT_ENV"] = account_mode
                else:
                    os.environ["BYBIT_ENV"] = "live"
                os.chdir(tmp)
                try:
                    target_mode = os.environ["BYBIT_ENV"]
                    _mode, _key, _secret, _base_url, key_source = (
                        resolve_bybit_credentials_for(target_mode)
                    )
                    if target_mode in {"testnet", "demo", "paper"} and key_source == "LEGACY":
                        raise RuntimeError(
                            "DEMO/Testnet export requires BYBIT_API_KEY2 and "
                            "BYBIT_API_SECRET2 (testnet keypair). Live/legacy keys "
                            "cannot authenticate on testnet."
                        )
                    filename = bybit_history_fetcher.download_history(
                        "linear",
                        start_date,
                        end_date,
                        None,
                        True,
                    )
                finally:
                    os.chdir(prev_cwd)
                    if prev_env is None:
                        os.environ.pop("BYBIT_ENV", None)
                    else:
                        os.environ["BYBIT_ENV"] = prev_env
                if filename is None:
                    raise RuntimeError("No transactions found for the selected timeframe.")
                src = tmp_path / filename
                if not src.exists():
                    raise RuntimeError("Export was generated but could not be found on disk.")
                dest = BYBIT_HISTORY_EXPORT_ROOT / f"bybit_history_{job.job_id}.csv"
                shutil.move(str(src), dest)
                return dest

        output_path = await asyncio.to_thread(_export)
        job.output_path = output_path
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
    finally:
        job.updated_at = time.time()


async def _run_coinspot_history_export(job: CoinspotHistoryJob) -> None:
    job.status = "running"
    job.updated_at = time.time()
    try:
        if coinspot_history_exporter is None:
            raise RuntimeError("CoinSpot history exporter module not available.")

        period = _normalize_period(job.params.get("period"))
        complete = bool(job.params.get("complete")) or period == "complete"

        if complete:
            start_date, end_date = None, None
        elif period:
            start_date, end_date = _date_range_for_period(period)
        else:
            days_value = job.params.get("days")
            if days_value is None:
                raise ValueError("days is required unless complete is true.")
            try:
                days = int(days_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("days must be an integer.") from exc
            if days <= 0:
                raise ValueError("days must be greater than zero.")
            start_date, end_date = _date_range_for_days(days)

        def _export() -> Path:
            import shutil
            import tempfile
            from pathlib import Path

            dest = COINSPOT_HISTORY_EXPORT_ROOT / f"coinspot_history_{job.job_id}.zip"
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                zip_path = tmp_path / dest.name
                coinspot_history_exporter.export_zip(
                    start_date,
                    end_date,
                    output_path=zip_path,
                )
                shutil.move(str(zip_path), dest)
            return dest

        output_path = await asyncio.to_thread(_export)
        job.output_path = output_path
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
    finally:
        job.updated_at = time.time()


ASSET_VERSION = ""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>TradingTools</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h2 { margin: 0; font-size: 1.35rem; }
        .meta { color: #94a3b8; margin: 0.75rem 0 1.5rem; line-height: 1.5; }
        .home { max-width: 1400px; margin: 0 auto; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 800; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .refresh { background: #3b82f6; color: #eaf2ff; }

        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 16px; padding: 1.25rem; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .panel-header { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 0.75rem; }
        .panel-sub { color: #94a3b8; margin-top: 0.25rem; font-size: 0.95rem; line-height: 1.4; }
        .oo-toolbar { display:flex; gap:0.6rem; align-items:center; }

        .layout{
            margin-top: 1.25rem;
            display: grid;
            grid-template-columns: 260px minmax(0, 1fr);
            gap: 1rem;
            align-items: start;
        }
        .dashboard-rail{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        .sidebar{
            padding: 1rem;
        }
        #dashboard-workspace{
            min-height: 720px;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        #dashboard-workspace .panel-header{
            margin-bottom: 0.2rem;
        }
        #dashboard-workspace-title{
            font-size: 1.05rem;
            font-weight: 900;
            color: #e2e8f0;
        }
        #dashboard-workspace-status{
            font-size: 0.9rem;
            color: #94a3b8;
            margin: 0;
        }
        #dashboard-workspace-empty{
            color: #94a3b8;
            font-size: 0.95rem;
            margin: 0;
            padding: 0.65rem 0.1rem;
        }
        #dashboard-workspace-frame{
            width: 100%;
            height: calc(100vh - 14rem);
            min-height: 560px;
            border: 1px solid #1f2937;
            border-radius: 12px;
            background: #0b1220;
        }
        @media (max-width: 980px){
            .layout{ grid-template-columns: 1fr; }
            #dashboard-workspace{
                min-height: 420px;
            }
            #dashboard-workspace-frame{
                height: 70vh;
                min-height: 420px;
            }
        }
        .category-title{
            margin: 0.6rem 0 0.5rem;
            text-align: left;
            font-size: 1.05rem;
            font-weight: 900;
            letter-spacing: 0.2px;
            color: #e2e8f0;
        }
        .script-stack{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
            gap:0.65rem;
        }
        
        .script-btn {
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            text-align: left;
            padding: 0.9rem 1rem;
            border-radius: 14px;
            border: 1px solid #334155;
            background: #0a0f1b;
            color: #e5e7eb;
        }
        .script-btn:hover { background: #0f172a; }
        .script-btn.active-script { outline: 1px solid rgba(96, 165, 250, 0.8); }
        .script-btn.compact { width: auto; min-width: 190px; padding: 0.75rem 0.9rem; }
        .script-name { font-weight: 900; }
        .status-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.25rem 0.7rem;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 900;
            background: #1f2937;
            color: #cbd5e1;
            border: 1px solid #334155;
            white-space: nowrap;
        }
        .status-pill.running { background: #14532d; color: #bbf7d0; border-color: #22c55e55; }
        .status-pill.starting { background: #78350f; color: #fde68a; border-color: #f59e0b55; }
        .status-pill.stopped { background: #7f1d1d; color: #fecdd3; border-color: #ef444455; }
        .status-dot {
            width: 10px;
            height: 10px;
            min-width: 10px;
            border-radius: 999px;
            display: inline-block;
            margin-left: 10px;
            box-shadow: 0 0 0 1px rgba(255,255,255,0.12) inset;
        }
        .status-dot.running { background: #22c55e; }
        .status-dot.starting { background: #f59e0b; }
        .status-dot.stopped { background: #ef4444; }
        .empty-state { color: #94a3b8; margin-top: 0.9rem; }

        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid #1f2937; background: #0b1220; }

        .action-btn {
            display:inline-flex; align-items:center; justify-content:center;
            min-width:72px; height:30px; padding:0 10px;
            border-radius:8px; font-size:0.8rem; font-weight:900;
            background:#1f2937; color:#e2e8f0; border:1px solid #334155;
        }
        .action-btn:hover { background:#334155; }
        .action-btn:disabled { opacity:0.6; cursor:not-allowed; }

        .watchlist-sub {
            color: #94a3b8;
            margin-top: 0.25rem;
            font-size: 0.95rem;
            line-height: 1.4;
        }
        #watchlist-widget .watchlist-input {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin: 0.75rem 0 0.5rem;
            width: 100%;
            box-sizing: border-box;
        }
        #watchlist-widget .watchlist-input input {
            flex: 1;
            width: 100%;
            box-sizing: border-box;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #0b1220;
            color: #e2e8f0;
            padding: 6px 8px;
            font-size: 0.9rem;
        }
        #watchlist-widget .watchlist-input button {
            width: 100%;
            box-sizing: border-box;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #1f2937;
            color: #e2e8f0;
            font-weight: 900;
            padding: 6px 10px;
            cursor: pointer;
        }
        #watchlist-widget .watchlist-input button:hover { background: #334155; }

        #watchlist-table { width: 100%; border-collapse: collapse; }
        #watchlist-table th, #watchlist-table td {
            text-align:left;
            padding:0.55rem 0.65rem;
            border-bottom:1px solid #1f2937;
            font-size:0.9rem;
        }
        #watchlist-table th {
            background:#0f172a;
            color:#cbd5e1;
            position:sticky;
            top:0;
            z-index:1;
        }
        #watchlist-table tr:hover { background:#111827; }

        .watchlist-status {
            color: #94a3b8;
            font-size: 0.85rem;
            min-height: 1em;
        }
        #oanda-inactivity-widget .meta-grid {
            display: grid;
            grid-template-columns: max-content 1fr;
            gap: 0.35rem 0.65rem;
            font-size: 0.9rem;
            margin-top: 0.35rem;
        }
        #oanda-inactivity-widget {
            height: auto;
            margin-top: 0;
        }
        #oanda-inactivity-widget .toggle-btn {
            border-radius: 8px;
            border: 1px solid #334155;
            background: #1f2937;
            color: #e2e8f0;
            width: 32px;
            height: 32px;
            cursor: pointer;
            font-weight: 900;
            line-height: 1;
        }
        #oanda-inactivity-widget .toggle-btn:hover { background: #334155; }
        #oanda-inactivity-widget .meta-grid dt {
            color: #94a3b8;
            margin: 0;
            font-weight: 600;
        }
        #oanda-inactivity-widget .meta-grid dd {
            margin: 0;
            color: #e2e8f0;
        }
        #oanda-inactivity-widget .status-headline {
            margin-top: 0.45rem;
            font-size: 1rem;
            font-weight: 900;
            color: #93c5fd;
        }
        #oanda-inactivity-widget .status-detail {
            margin-top: 0.15rem;
            color: #cbd5e1;
            font-size: 0.85rem;
            line-height: 1.35;
        }
        #oanda-inactivity-widget .details-wrap {
            margin-top: 0.35rem;
        }
        #oanda-inactivity-widget .details-wrap[hidden] {
            display: none;
        }
    </style>
</head>
<body>
    <div class=\"home\">
        <div class="layout">
            <div class="dashboard-rail">
                <aside class="panel sidebar">
                    <div class="category-title">Scripts</div>
                    <div id="scripts-grid" class="script-stack"></div>
                </aside>

                <section class="panel" id="watchlist-widget">
                    <div class="panel-header">
                        <div>
                            <h2>Watchlist</h2>
                            <div class="watchlist-sub">Saved locally</div>
                        </div>
                        <div class="oo-toolbar">
                            <span class="status-pill" id="watchlist-count">0</span>
                            <button type="button" id="watchlist-clear-btn">Clear</button>
                        </div>
                    </div>

                    <div class="watchlist-input">
                        <input id="watchlist-input" type="text" placeholder="BTC, ETH, EURUSD" />
                        <button type="button" id="watchlist-add-btn">Add</button>
                    </div>

                    <div class="watchlist-status" id="watchlist-status"></div>

                    <div class="table-wrap">
                        <table id="watchlist-table">
                            <thead>
                                <tr>
                                    <th>Instrument</th>
                                    <th>Action</th>
                                </tr>
                            </thead>
                            <tbody id="watchlist-items"></tbody>
                        </table>
                    </div>

                    <p class="meta" id="watchlist-empty" style="display:none;">No items yet.</p>
                </section>

                <section class="panel" id="oanda-inactivity-widget">
                    <div class="panel-header">
                        <div>
                            <h2>OANDA Inactivity</h2>
                        </div>
                        <div class="oo-toolbar">
                            <button type="button" class="toggle-btn" id="oanda-inactivity-toggle" aria-expanded="false" title="Show details">▾</button>
                        </div>
                    </div>
                    <dl class="meta-grid">
                        <dt>Last trade</dt><dd id="oanda-inactivity-last-trade">—</dd>
                        <dt>Countdown</dt><dd id="oanda-inactivity-countdown">—</dd>
                    </dl>
                    <div class="status-headline" id="oanda-inactivity-headline">Loading...</div>
                    <div class="status-detail" id="oanda-inactivity-detail"></div>
                    <div class="details-wrap" id="oanda-inactivity-details" hidden>
                        <dl class="meta-grid">
                            <dt>Open OANDA trades</dt><dd id="oanda-inactivity-open-trades">—</dd>
                            <dt>12-month inactivity threshold</dt><dd id="oanda-inactivity-threshold">—</dd>
                            <dt>Earliest fee date</dt><dd id="oanda-inactivity-fee-date">—</dd>
                            <dt>Monthly fee</dt><dd id="oanda-inactivity-monthly-fee">Up to AUD 10</dd>
                        </dl>
                        <div class="status-detail" id="oanda-inactivity-error-detail"></div>
                    </div>
                </section>
            </div>
            <section class="panel" id="dashboard-workspace">
                <div class="panel-header">
                    <div>
                        <div id="dashboard-workspace-title">Workspace</div>
                        <p id="dashboard-workspace-status">Ready to load a script.</p>
                    </div>
                </div>
                <p id="dashboard-workspace-empty">Select a script from the left to load it here.</p>
                <iframe
                    id="dashboard-workspace-frame"
                    title="Dashboard script workspace"
                    src="about:blank"
                    hidden
                ></iframe>
            </section>
        </div>
    </div>

    <script src=\"/static/dashboard.js\"></script>
</body>
</html>"""

INSTRUMENT_SPECS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Instrument Specs</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        .wrap { max-width: 1200px; margin: 0 auto; }
        h1 { margin: 0 0 0.75rem; }
        .meta { color: #94a3b8; margin: 0 0 1.25rem; line-height: 1.5; }
        .bar { display:flex; gap:0.6rem; align-items:center; margin-bottom: 1rem; }
        input { flex: 1; min-width: 240px; border-radius: 10px; border: 1px solid #334155; background: #0b1220; color: #e2e8f0; padding: 8px 10px; font-size: 0.95rem; }
        button, .btn { background: #1f2937; color: #e2e8f0; border: 1px solid #334155; border-radius: 10px; padding: 8px 12px; cursor: pointer; font-weight: 900; text-decoration:none; display:inline-flex; align-items:center; }
        button:hover, .btn:hover { background: #334155; }
        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 16px; padding: 1.25rem; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid #1f2937; background: #0b1220; }
        table { width: 100%; border-collapse: collapse; min-width: 720px; }
        th, td { text-align:left; padding:0.6rem 0.75rem; border-bottom:1px solid #1f2937; font-size:0.9rem; }
        th { background:#0f172a; color:#cbd5e1; position:sticky; top:0; z-index:1; }
        #err { color:#fca5a5; white-space: pre-wrap; }
    </style>
</head>
<body>
<div class="wrap">
    <h1>Instrument Specs</h1>
    <p class="meta">Type a symbol (e.g. eurusd, BTCUSDT). The tool auto-detects OANDA/Bybit and returns available specs.</p>

    <div class="bar">
      <input id="q" type="text" placeholder="eurusd / BTC" />
      <button id="load" type="button">Load</button>
      <a class="btn" id="download" href="#">Download JPG</a>
    </div>

    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Field</th><th>Value</th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
      <div id="err"></div>
    </section>
</div>
<script src="/static/instrument_specs.js"></script>
</body>
</html>"""

CATEGORY_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Scripts - {category}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; margin-bottom: 1.5rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem; text-align: center; min-height: 96px; }
        .script-btn { width: 100%; padding: 0.8rem 1rem; border-radius: 10px; border: none; font-weight: 700; background: #1f2937; color: #e2e8f0; cursor: pointer; }
        .script-btn.running { background: #22c55e22; color: #86efac; border: 1px solid #22c55e55; }
        .script-btn.starting { background: #f59e0b22; color: #fde68a; border: 1px solid #f59e0b55; }
        .status-pill { display: inline-flex; align-items: center; justify-content: center; padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.85rem; font-weight: 700; background: #1f2937; color: #cbd5e1; }
        .status-pill.running { background: #14532d; color: #bbf7d0; }
        .status-pill.starting { background: #78350f; color: #fde68a; }
        .status-pill.stopped { background: #7f1d1d; color: #fecdd3; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .secondary { background: #1f2937; color: #cbd5e1; }
    </style>
</head>
<body data-category=\"{category}\">
    <h1>{category} scripts</h1>
    <p class=\"meta\">Select a script to view its page.</p>
    <div id=\"grid\" class=\"grid\"></div>
    <script src=\"/static/category_page.js\"></script>
</body>
</html>"""


SCRIPT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Script - {script_name}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        .actions { display: flex; gap: 0.5rem; margin: 1rem 0; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .start { background: #22c55e; color: #052e16; }
        .stop { background: #ef4444; color: #fff7ed; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; }
        .panel-header { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 0.75rem; }
        .panel-header .meta { margin: 0; }
        .badge { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.25rem 0.7rem; border-radius: 999px; font-weight: 700; background: #1f2937; color: #cbd5e1; }
        .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-top: 0.5rem; }
        .settings-grid label { display: flex; flex-direction: column; gap: 0.35rem; font-weight: 700; color: #cbd5e1; }
        .settings-grid input,
        .settings-grid select { padding: 0.55rem 0.75rem; border-radius: 10px; border: 1px solid #1f2937; background: #0a0f1b; color: #e5e7eb; }
        .settings-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }
        .ghost-link { color: #38bdf8; font-weight: 700; text-decoration: none; }
        .ghost-link:hover { text-decoration: underline; }
        .log-box { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; min-height: 220px; max-height: 360px; overflow: auto; border: 1px solid #1f2937; }
        iframe { width: 100%; height: 520px; border: 1px solid #1f2937; border-radius: 12px; background: #0a0f1b; }
    </style>
</head>
<body data-script-name=\"{script_name}\" data-has-ui=\"{has_ui}\">
    <h1>{script_name}</h1>
    <p class=\"meta\" id=\"script-status\">Loading status...</p>
    <div class=\"actions\">
        <button class=\"start\" id=\"start-btn\">Start</button>
        <button class=\"stop\" id=\"stop-btn\">Stop</button>
    </div>
    <div class=\"panel\" id=\"bybit-settings\" style=\"display:none;\">
        <div class=\"panel-header\">
            <div>
                <strong>Bybit monitor controls</strong>
                <p class=\"meta\">Adjust scan interval, alert threshold, and send a Telegram test.</p>
            </div>
            <span class=\"badge\" id=\"bybit-settings-status\">&nbsp;</span>
        </div>
        <div class=\"settings-grid\">
            <label>Wait between scans (seconds)
                <input type=\"number\" min=\"1\" step=\"1\" id=\"bybit-wait-seconds\" />
            </label>
            <label>Alert threshold (% change)
                <input type=\"number\" min=\"0.1\" step=\"0.1\" id=\"bybit-threshold\" />
            </label>
        </div>
        <div class=\"settings-actions\">
            <button id=\"bybit-save-settings\">Save settings</button>
            <button class=\"secondary\" id=\"bybit-reload-settings\">Reset</button>
            <button class=\"secondary\" id=\"bybit-test-alert\">Test Telegram alert</button>
        </div>
    </div>
    <div class=\"panel\" id=\"oanda-settings\" style=\"display:none;\">
        <div class=\"panel-header\">
            <div>
                <strong>OANDA monitor controls</strong>
                <p class=\"meta\">Tune polling and send a Telegram test.</p>
            </div>
            <span class=\"badge\" id=\"oanda-settings-status\">&nbsp;</span>
        </div>
        <div class=\"settings-grid\">
            <label>Wait between scans (seconds)
                <input type=\"number\" min=\"1\" step=\"1\" id=\"oanda-wait-seconds\" />
            </label>
            <label>Alert threshold (% change)
                <input type=\"number\" min=\"0.01\" step=\"0.01\" id=\"oanda-threshold\" />
            </label>
        </div>
        <div class=\"settings-actions\">
            <button id=\"oanda-save-settings\">Save settings</button>
            <button class=\"secondary\" id=\"oanda-reload-settings\">Reset</button>
            <button class=\"secondary\" id=\"oanda-test-alert\">Test Telegram alert</button>
        </div>
    </div>
    <div class=\"panel\">
        <div class=\"panel-header\">
            <strong>Logs</strong>
            <a class=\"ghost-link\" href=\"{log_url}\" id=\"open-logs\">Open full logs</a>
        </div>
        <div class=\"log-box\" id=\"log-box\">Waiting for output...</div>
    </div>
    <div class=\"panel\" id=\"app-panel\" style=\"display:none;\">
        <strong>App UI</strong>
        <iframe id=\"app-frame\" title=\"Script UI\"></iframe>
    </div>
    <script src=\"/static/script_page.js\"></script>
</body>
</html>"""

LAUNCHER_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Launching {script_name}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 2rem; max-width: 520px; text-align: center; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-top: 0.5rem; }
        .spinner { width: 36px; height: 36px; border: 3px solid #1f2937; border-top-color: #38bdf8; border-radius: 50%; margin: 1rem auto 0; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        a { color: #38bdf8; }
    </style>
</head>
<body data-script-name=\"{script_name}\" data-target-url=\"{target_url}\" data-has-ui=\"{has_ui}\">
    <div class=\"card\">
        <h1>Launching {script_name}</h1>
        <p class=\"meta\" id=\"status\">Starting the script...</p>
        <div class=\"spinner\"></div>
        <p class=\"meta\">If you are not redirected, <a id=\"open-link\" href=\"{target_url}\">open the script</a>.</p>
    </div>
    <script>
        const scriptName = document.body.dataset.scriptName;
        const targetUrl = document.body.dataset.targetUrl;
        const hasUi = document.body.dataset.hasUi === 'true';

        const fetchJson = async (url, options = {}) => {
            const response = await fetch(url, options);
            if (!response.ok) {
                const body = await response.text();
                const detail = body || response.statusText;
                throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}: ${detail}`);
            }
            return response.json();
        };

        const statusEl = document.getElementById('status');

        const waitForApp = async () => {
            let attempts = 0;
            while (attempts < 30) {
                attempts += 1;
                try {
                    const response = await fetch(targetUrl, { cache: 'no-store' });
                    if (response.ok) {
                        const nextUrl = hasUi ? `${targetUrl}?ts=${Date.now()}` : targetUrl;
                        window.location.replace(nextUrl);
                        return;
                    }
                } catch (err) {
                    // keep trying
                }
                await new Promise((resolve) => setTimeout(resolve, 500));
            }
            statusEl.textContent = 'Still warming up. Please use the link below to open the script.';
        };

        const launch = async () => {
            try {
                await fetchJson(`/scripts/${encodeURIComponent(scriptName)}/start`, { method: 'POST' });
                statusEl.textContent = 'Waiting for the script to respond...';
            } catch (err) {
                statusEl.textContent = 'Unable to start the script automatically.';
            }
            if (hasUi) {
                await waitForApp();
            } else {
                window.location.replace(targetUrl);
            }
        };

        launch();
    </script>
</body>
</html>"""


LOG_VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Logs - {script_name}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 1.5rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; }
        .controls { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1rem; }
        button { padding: 0.6rem 1rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        #refresh-btn { background: #3b82f6; color: #eaf2ff; }
        #save-log-btn { background: #22c55e; color: #052e16; }
        #log-box { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; min-height: 320px; border: 1px solid #1f2937; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .badge { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; }
        .settings-card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; margin: 1rem 0; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-top: 0.5rem; }
        .settings-grid label { display: flex; flex-direction: column; gap: 0.35rem; font-weight: 700; color: #cbd5e1; }
        .settings-grid input,
        .settings-grid select { padding: 0.55rem 0.75rem; border-radius: 10px; border: 1px solid #1f2937; background: #0a0f1b; color: #e5e7eb; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .settings-header { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; }
        .settings-header .meta { margin: 0; }
    </style>
</head>
<body data-script-name=\"{script_name}\">
    <h1>Logs for {script_name}</h1>
    <p class=\"meta\">Live output is streamed here so you can keep the main control panel clean.</p>
    <div class=\"controls\">
        <span class=\"badge\" id=\"line-count\">0 lines</span>
        <button id=\"refresh-btn\">Refresh</button>
        <button id=\"save-log-btn\">Save log</button>
    </div>
    <div class=\"settings-card\" id=\"bybit-settings\" style=\"display:none;\">
        <div class=\"settings-header\">
            <div>
                <strong>Bybit monitor settings</strong>
                <p class=\"meta\">Adjust scan interval and alert threshold without restarting.</p>
            </div>
            <span class=\"badge\" id=\"bybit-settings-status\">&nbsp;</span>
        </div>
        <div class=\"settings-grid\">
            <label>Wait between scans (seconds)
                <input type=\"number\" min=\"1\" step=\"1\" id=\"bybit-wait-seconds\" />
            </label>
            <label>Alert threshold (% change)
                <input type=\"number\" min=\"0.1\" step=\"0.1\" id=\"bybit-threshold\" />
            </label>
        </div>
        <div class=\"controls\">
            <button id=\"bybit-save-settings\">Save settings</button>
            <button class=\"secondary\" id=\"bybit-reload-settings\">Reset</button>
        </div>
    </div>
    <div class=\"settings-card\" id=\"oanda-settings\" style=\"display:none;\">
        <div class=\"settings-header\">
            <div>
                <strong>OANDA monitor settings</strong>
                <p class=\"meta\">Adjust OANDA monitoring.</p>
            </div>
            <span class=\"badge\" id=\"oanda-settings-status\">&nbsp;</span>
        </div>
        <div class=\"settings-grid\">
            <label>Wait between scans (seconds)
                <input type=\"number\" min=\"1\" step=\"1\" id=\"oanda-wait-seconds\" />
            </label>
            <label>Alert threshold (% change)
                <input type=\"number\" min=\"0.01\" step=\"0.01\" id=\"oanda-threshold\" />
            </label>
        </div>
        <div class=\"controls\">
            <button id=\"oanda-save-settings\">Save settings</button>
            <button class=\"secondary\" id=\"oanda-reload-settings\">Reset</button>
        </div>
    </div>
    <pre id=\"log-box\">Loading logs...</pre>

    <script>
        window.RENDER_LOG_VIEW = {
            scriptName: {script_name_json}
        };
    </script>
    <script src=\"/static/log_viewer.js?v=1\"></script>
</body>
</html>"""


PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
PROXY_STRIP_HEADERS = {"content-encoding", "content-length"}
PROXY_LOGGER = logging.getLogger("uvicorn.error")
BYBIT_RECV_WINDOW = "5000"
BYBIT_OPTIONS_TAKER_FEE_RATE = float(os.getenv("BYBIT_OPTIONS_TAKER_FEE_RATE", "0.0003"))
BYBIT_OPTIONS_MAKER_FEE_RATE = float(os.getenv("BYBIT_OPTIONS_MAKER_FEE_RATE", "0.0002"))


def _instrument_lookup_key(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _normalize_oanda_symbol_query(user_value: str, available_instruments: Optional[List[str]] = None) -> str:
    if not user_value or not user_value.strip():
        raise ValueError("Instrument is required")

    raw = user_value.strip().upper()
    if "_" in raw and len(raw) >= 7:
        return raw

    lookup = _instrument_lookup_key(raw)
    if available_instruments:
        mapping = {_instrument_lookup_key(inst): inst for inst in available_instruments if inst}
        if lookup in mapping:
            return mapping[lookup]

    if len(lookup) == 6 and lookup.isalpha():
        return f"{lookup[:3]}_{lookup[3:]}"
    return raw


def _oanda_base_url() -> str:
    raw = (
        os.getenv("OANDA_BASE_URL")
        or os.getenv("OANDA_URL")
        or os.getenv("OANDA_API_URL")
        or ""
    ).strip()
    if raw:
        return _normalize_oanda_base_url(raw)

    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    if env in {"practice", "demo", "test"}:
        return "https://api-fxpractice.oanda.com"
    return "https://api-fxtrade.oanda.com"


def _normalize_oanda_base_url(value: Optional[str]) -> str:
    base = (value or "").strip().rstrip("/")
    if base.endswith("/v3"):
        base = base[: -len("/v3")]
    return base


def _normalize_oanda_v3_base_url(value: Optional[str]) -> tuple[str, bool]:
    base = _normalize_oanda_base_url(value)
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid OANDA API base URL: {value or '(empty)'}")
    normalized_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return f"{normalized_base}/v3", base != normalized_base


def _resolve_oanda_api_base_url(mode: str) -> tuple[str, str]:
    normalized_mode = (mode or "live").strip().lower()
    if normalized_mode not in {"live", "demo", "practice"}:
        raise ValueError("OANDA mode must be live or demo.")

    if normalized_mode in {"demo", "practice"}:
        raw = (
            _clean_env("OANDA_API_URL_DEMO")
            or _clean_env("OANDA_BASE_URL_DEMO")
            or _clean_env("OANDA_BASE_URL")
            or "https://api-fxpractice.oanda.com"
        )
        resolved_mode = "demo"
    else:
        raw = (
            _clean_env("OANDA_API_URL_LIVE")
            or _clean_env("OANDA_BASE_URL_LIVE")
            or _clean_env("OANDA_API_URL")
            or _clean_env("OANDA_BASE_URL")
            or "https://api-fxtrade.oanda.com"
        )
        resolved_mode = "live"
    base_url_v3, trimmed_path = _normalize_oanda_v3_base_url(raw)
    parsed = urlparse(base_url_v3)
    BYBIT_LOGGER.info(
        "Resolved OANDA history API mode=%s host=%s normalized_to_v3=true trimmed_path=%s",
        resolved_mode,
        parsed.netloc,
        trimmed_path,
    )
    return base_url_v3, resolved_mode


def _sanitize_oanda_history_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    status_match = re.search(r"\b(\d{3})\b", message)
    status_code = status_match.group(1) if status_match else "unknown"
    lowered = message.lower()
    if "<html" in lowered or "cloudflare" in lowered or "attention required" in lowered:
        return (
            f"OANDA history export failed with HTTP {status_code} from upstream. "
            "Check OANDA history base URL and credentials."
        )
    return message if len(message) <= 240 else message[:237] + "..."


def _format_decimal_value(value: float) -> str:
    text = f"{value:.10f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_optional_float(value: object, field_name: str) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OANDA payload {field_name} must be numeric.") from exc


_OANDA_INSTRUMENT_META_CACHE: Dict[tuple[str, str], Dict[str, object]] = {}
_OANDA_INSTRUMENT_META_CACHE_TS: Dict[tuple[str, str], float] = {}
_OANDA_INSTRUMENT_META_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def _quantize_oanda_units(value: float, precision: int) -> str:
    normalized = max(0, int(precision))
    quantizer = Decimal("1").scaleb(-normalized)
    quantized = Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)
    return f"{quantized:.{normalized}f}"


def _quantize_oanda_price(value: float, precision: int) -> str:
    normalized = max(0, precision)
    quantizer = Decimal("1").scaleb(-normalized)
    quantized = Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)
    return f"{quantized:.{normalized}f}"


async def _fetch_oanda_instrument_meta(
    *,
    base_url: str,
    account_id: str,
    api_key: str,
    symbol: str,
    mode: str,
) -> Dict[str, object]:
    if not symbol:
        raise ValueError("OANDA instrument name missing; cannot determine precision.")

    cache_key = (mode.strip().lower(), symbol.upper())
    now = time.time()
    cached = _OANDA_INSTRUMENT_META_CACHE.get(cache_key)
    cached_ts = _OANDA_INSTRUMENT_META_CACHE_TS.get(cache_key, 0.0)
    if cached and (now - cached_ts) < _OANDA_INSTRUMENT_META_TTL_SECONDS:
        return cached

    try:
        payload = await _fetch_oanda_json(
            base_url=base_url,
            account_id=account_id,
            api_key=api_key,
            endpoint=f"/accounts/{{account_id}}/instruments?instruments={symbol}",
            mode=mode,
        )
    except Exception as exc:
        if cached:
            BYBIT_LOGGER.warning(
                "OANDA instrument lookup failed; using cached meta symbol=%s mode=%s error=%s",
                symbol,
                mode,
                exc,
            )
            return cached
        raise ValueError(
            f"OANDA instrument lookup failed for {symbol} (mode={mode}). "
            "Refusing to place the order to avoid precision errors. "
            "Check OANDA connectivity/credentials."
        ) from exc
    instruments = payload.get("instruments") or []
    for instrument in instruments:
        name = str(instrument.get("name", "")).upper()
        if name and name == symbol.upper():
            try:
                meta = {
                    "displayPrecision": int(instrument.get("displayPrecision")),
                    "tradeUnitsPrecision": int(instrument.get("tradeUnitsPrecision", 0)),
                    "pipLocation": int(instrument.get("pipLocation", 0)),
                    "minimumTradeSize": str(instrument.get("minimumTradeSize") or "0"),
                    "maximumOrderUnits": str(instrument.get("maximumOrderUnits") or "0"),
                    "maximumPositionSize": str(instrument.get("maximumPositionSize") or "0"),
                    "marginRate": str(instrument.get("marginRate") or "0"),
                }
                _OANDA_INSTRUMENT_META_CACHE[cache_key] = meta
                _OANDA_INSTRUMENT_META_CACHE_TS[cache_key] = now
                return meta
            except (TypeError, ValueError):
                break

    if cached:
        BYBIT_LOGGER.warning(
            "OANDA instrument meta missing/unparseable; using cached meta symbol=%s mode=%s",
            symbol,
            mode,
        )
        return cached
    raise ValueError(
        f"OANDA instrument meta missing/unparseable for {symbol} (mode={mode}). "
        "Refusing to place the order to avoid precision errors."
    )


async def _fetch_oanda_display_precision(
    *,
    base_url: str,
    account_id: str,
    api_key: str,
    symbol: str,
    mode: str,
) -> int:
    meta = await _fetch_oanda_instrument_meta(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        symbol=symbol,
        mode=mode,
    )
    return int(meta["displayPrecision"])


def _clean_env(key: str) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def _get_oanda_config(account: Optional[str]) -> Dict[str, str]:
    acct = (account or "").strip().lower()
    if acct in ("demo", "practice"):
        token = _clean_env("OANDA_API_KEY_DEMO")
        account_id = _clean_env("OANDA_ACCOUNT_ID_DEMO")
        base_url = _normalize_oanda_base_url(
            _clean_env("OANDA_API_URL_DEMO")
            or _clean_env("OANDA_BASE_URL_DEMO")
            or _clean_env("OANDA_BASE_URL")
            or "https://api-fxpractice.oanda.com"
        )
        missing = []
        if not token:
            missing.append("OANDA_API_KEY_DEMO")
        if not account_id:
            missing.append("OANDA_ACCOUNT_ID_DEMO")
        if missing:
            raise ValueError(
                f"OANDA demo credentials missing: {', '.join(missing)} ({_env_source_hint()})"
            )
        return {
            "mode": "demo",
            "token": token,
            "account_id": account_id,
            "base_url": base_url,
        }

    token = _clean_env("OANDA_API_KEY")
    account_id = _clean_env("OANDA_ACCOUNT_ID")
    base_url = _normalize_oanda_base_url(
        _clean_env("OANDA_API_URL_LIVE")
        or _clean_env("OANDA_BASE_URL_LIVE")
        or _clean_env("OANDA_BASE_URL")
        or "https://api-fxtrade.oanda.com"
    )
    missing = []
    if not token:
        missing.append("OANDA_API_KEY")
    if not account_id:
        missing.append("OANDA_ACCOUNT_ID")
    if missing:
        raise ValueError(
            f"OANDA live credentials missing: {', '.join(missing)} ({_env_source_hint()})"
        )
    return {"mode": "live", "token": token, "account_id": account_id, "base_url": base_url}


def _get_oanda_history_config(mode: str = "live") -> Dict[str, str]:
    acct = (mode or "live").strip().lower()
    base_url, resolved_mode = _resolve_oanda_api_base_url(acct)
    if resolved_mode == "demo":
        account_id = _clean_env("OANDA_ACCOUNT_ID_DEMO")
        api_key = _clean_env("OANDA_API_KEY_DEMO")
        missing = []
        if not api_key:
            missing.append("OANDA_API_KEY_DEMO")
        if not account_id:
            missing.append("OANDA_ACCOUNT_ID_DEMO")
        if missing:
            raise ValueError(f"OANDA demo export credentials missing: {', '.join(missing)}")
        return {
            "account_id": account_id,
            "api_key": api_key,
            "base_url": base_url,
            "mode": "demo",
        }

    account_id = _clean_env("OANDA_ACCOUNT_ID")
    api_key = _clean_env("OANDA_API_KEY")
    missing = []
    if not api_key:
        missing.append("OANDA_API_KEY")
    if not account_id:
        missing.append("OANDA_ACCOUNT_ID")
    if missing:
        raise ValueError(f"OANDA export credentials missing: {', '.join(missing)}")
    return {
        "account_id": account_id,
        "api_key": api_key,
        "base_url": base_url,
        "mode": "live",
    }


def _oanda_account_context(base_url: str) -> str:
    lowered = base_url.lower()
    if "fxpractice" in lowered or "practice" in lowered or "sandbox" in lowered:
        return "demo"
    return "live"


def _format_source_exception(
    exc: Exception,
    *,
    broker: str,
    account: str,
    endpoint: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    endpoint_label = endpoint or "endpoint"
    account_label = account or "unknown"
    account_id_label = account_id or "unknown"
    broker_label = broker or "source"

    if isinstance(exc, OandaUpstreamHTTPError):
        return (
            f"{broker_label} {endpoint_label} failed with HTTP {exc.status_code} "
            f"for {account_label}/{account_id_label}: {exc.body_summary}"
        )

    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Timeout contacting {broker_label} {endpoint_label} "
            f"for {account_label}/{account_id_label}"
        )

    if isinstance(exc, httpx.RequestError):
        req_url = str(exc.request.url) if getattr(exc, "request", None) is not None else None
        detail = req_url or endpoint_label
        return (
            f"{exc.__class__.__name__} contacting {broker_label} {detail} "
            f"for {account_label}/{account_id_label}"
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = _summarize_upstream_body(exc.response.text if exc.response is not None else "")
        suffix = f": {body}" if body else ""
        return (
            f"{broker_label} {endpoint_label} failed with HTTP {status} "
            f"for {account_label}/{account_id_label}{suffix}"
        )

    text = str(exc).strip()
    if not text:
        return f"{exc.__class__.__name__} with empty message"
    return text


def _summarize_upstream_body(body: str, *, limit: int = 240) -> str:
    raw = body or ""
    collapsed = re.sub(r"\s+", " ", raw).strip()
    lowered = collapsed.lower()

    if "<html" in lowered or "<!doctype" in lowered:
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )
        title = "HTML error response"
        if title_match:
            title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip() or title
        return f"{title} (HTML response, {len(raw)} bytes)"

    if not collapsed:
        return "empty response body"

    if len(collapsed) > limit:
        return f"{collapsed[:limit]}... ({len(raw)} bytes)"

    return collapsed


async def _fetch_oanda_json(
    *,
    base_url: str,
    account_id: str,
    api_key: str,
    endpoint: str,
    mode: str,
    timeout_s: float = 6.0,
) -> Dict[str, object]:
    token = (api_key or "").strip().strip('"').strip("'")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/v3{endpoint.format(account_id=account_id)}"
    timeout = httpx.Timeout(timeout_s, connect=min(3.0, timeout_s), read=timeout_s, write=timeout_s, pool=2.0)
    max_attempts = 3
    transient_statuses = _OANDA_TRANSIENT_HTTP_STATUS_CODES
    resp: Optional[httpx.Response] = None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                _record_outbound_traffic(
                    "oanda",
                    bytes_sent=len(url) + sum(len(str(v)) for v in headers.values()),
                    bytes_received=len(resp.content),
                    context=endpoint,
                )
                break
            except httpx.TimeoutException as exc:
                should_retry = attempt < max_attempts
                BYBIT_LOGGER.warning(
                    "OANDA_TIMEOUT mode=%s account=%s endpoint=%s attempt=%s/%s retry=%s err=%s",
                    mode,
                    account_id,
                    endpoint,
                    attempt,
                    max_attempts,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    raise OandaUpstreamError(
                        f"OANDA request timed out after {timeout_s:.1f}s",
                        upstream_status=None,
                        upstream_error_message=str(exc),
                        endpoint=endpoint,
                        mode=mode,
                        retry_exhausted=True,
                        maintenance_detected=False,
                    ) from exc
            except httpx.RequestError as exc:
                should_retry = attempt < max_attempts
                BYBIT_LOGGER.warning(
                    "OANDA_REQUEST_ERR mode=%s account=%s endpoint=%s attempt=%s/%s retry=%s err=%s",
                    mode,
                    account_id,
                    endpoint,
                    attempt,
                    max_attempts,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    raise OandaUpstreamError(
                        f"OANDA transport error: {exc}",
                        upstream_status=None,
                        upstream_error_message=str(exc),
                        endpoint=endpoint,
                        mode=mode,
                        retry_exhausted=True,
                        maintenance_detected=False,
                    ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_summary = _summarize_upstream_body(exc.response.text)
                should_retry = status in transient_statuses and attempt < max_attempts
                log_fn = BYBIT_LOGGER.warning if should_retry else BYBIT_LOGGER.error
                log_fn(
                    "OANDA_HTTP_ERR mode=%s account=%s endpoint=%s status=%s attempt=%s/%s retry=%s body=%s",
                    mode,
                    account_id,
                    endpoint,
                    status,
                    attempt,
                    max_attempts,
                    should_retry,
                    body_summary,
                )
                if not should_retry:
                    raise OandaUpstreamHTTPError(
                        status_code=status,
                        mode=mode,
                        account_id=account_id,
                        endpoint=endpoint,
                        body_summary=body_summary,
                        transient=status in transient_statuses,
                    ) from exc

            backoff = min(0.2 * (2 ** (attempt - 1)), 0.8) + (0.05 * attempt)
            await asyncio.sleep(backoff)
        else:
            raise OandaUpstreamError(
                "OANDA request failed after retries",
                upstream_status=resp.status_code if resp is not None else None,
                upstream_error_message=(resp.text or "").strip()[:500] if resp is not None else "",
                endpoint=endpoint,
                mode=mode,
                retry_exhausted=True,
                maintenance_detected=False,
            )

    if resp is None:
        raise ValueError("OANDA request failed with no response")
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        body = _summarize_upstream_body(resp.text)
        raise ValueError(
            f"OANDA returned non-JSON success response mode={mode} account={account_id} "
            f"endpoint={endpoint}: {body}"
        ) from exc


def _parse_oanda_timestamp(value: str) -> datetime:
    cleaned = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_oanda_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def _fetch_oanda_account_created_time(
    *, base_url: str, account_id: str, api_key: str
) -> datetime:
    payload = await _fetch_oanda_json(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        endpoint="/accounts/{account_id}",
        mode="history",
    )
    account = payload.get("account", {})
    created_time = account.get("createdTime")
    if not isinstance(created_time, str) or not created_time:
        raise ValueError("OANDA account createdTime missing from response.")
    return _parse_oanda_timestamp(created_time)


async def _oanda_preflight(
    *, base_url: str, account_id: str, api_key: str, mode: str
) -> None:
    token = (api_key or "").strip().strip('"').strip("'")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/v3/accounts"
    token_last4 = token[-4:] if token else None
    BYBIT_LOGGER.info(
        "OANDA_CFG mode=%s base=%s account_id=%s token_last4=%s",
        mode,
        base_url,
        account_id,
        token_last4,
    )
    BYBIT_LOGGER.info(
        "OANDA_CALL mode=%s base=%s account_id=%s token_last4=%s url=%s",
        mode,
        base_url,
        account_id,
        token_last4,
        url,
    )
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=headers)
            BYBIT_LOGGER.info(
                "OANDA_RESP mode=%s status=%s url=%s body=%s",
                mode,
                resp.status_code,
                url,
                resp.text[:200],
            )
            if 300 <= resp.status_code < 400:
                BYBIT_LOGGER.info(
                    "OANDA_REDIRECT mode=%s status=%s url=%s location=%s",
                    mode,
                    resp.status_code,
                    url,
                    resp.headers.get("location"),
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            BYBIT_LOGGER.error(
                "OANDA_HTTP_ERR mode=%s status=%s url=%s body=%s",
                mode,
                exc.response.status_code,
                str(exc.request.url),
                exc.response.text[:500],
            )
            raise ValueError(
                f"OANDA preflight failed ({exc.response.status_code}): {exc.response.text}"
            ) from exc
    payload = resp.json()
    accounts = [acct.get("id") for acct in payload.get("accounts", [])]
    if account_id not in accounts:
        raise ValueError(
            "OANDA account mismatch: token does not own account "
            f"{account_id}. Available accounts: {accounts}"
        )


async def _collect_oanda_open_items(
    *, base_url: str, account_id: str, api_key: str, account_context: str
) -> Dict[str, object]:
    trades_task = _fetch_oanda_json(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        endpoint="/accounts/{account_id}/openTrades",
        mode=account_context,
        timeout_s=5.0,
    )
    orders_task = _fetch_oanda_json(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        endpoint="/accounts/{account_id}/pendingOrders",
        mode=account_context,
        timeout_s=5.0,
    )

    trades_result, orders_result = await asyncio.gather(
        trades_task,
        orders_task,
        return_exceptions=True,
    )

    trades_payload: Dict[str, object] = {}
    orders_payload: Dict[str, object] = {}
    fetch_errors: List[str] = []

    if isinstance(trades_result, Exception):
        fetch_errors.append(f"openTrades: {trades_result}")
    else:
        trades_payload = trades_result

    if isinstance(orders_result, Exception):
        fetch_errors.append(f"pendingOrders: {orders_result}")
    else:
        orders_payload = orders_result

    if fetch_errors and not trades_payload and not orders_payload:
        raise ValueError("; ".join(fetch_errors))

    items: List[Dict[str, object]] = []
    for trade in trades_payload.get("trades", []):
        units = trade.get("currentUnits") or trade.get("units")
        side = "Long"
        size = None
        if units is not None:
            try:
                units_val = float(units)
                side = "Long" if units_val >= 0 else "Short"
                size = abs(units_val)
            except (TypeError, ValueError):
                size = units
        items.append(
            {
                "broker": "OANDA",
                "account": account_context,
                "category": "forex",
                "instrument": trade.get("instrument"),
                "type": "Position",
                "side": side,
                "size": size,
                "entry_price": trade.get("price"),
                "order_price": None,
                "current_price": trade.get("currentPrice"),
                "stop_loss": (trade.get("stopLossOrder") or {}).get("price"),
                "take_profit": (trade.get("takeProfitOrder") or {}).get("price"),
                "leverage": trade.get("marginUsed"),
                "opened_at": trade.get("openTime"),
                "id": trade.get("id"),
                "trade_id": trade.get("id"),
                "status": trade.get("state") or "OPEN",
            }
        )

    for order in orders_payload.get("orders", []):
        units = order.get("units")
        side = "Buy"
        size = None
        if units is not None:
            try:
                units_val = float(units)
                side = "Buy" if units_val >= 0 else "Sell"
                size = abs(units_val)
            except (TypeError, ValueError):
                size = units
        items.append(
            {
                "broker": "OANDA",
                "account": account_context,
                "category": "forex",
                "instrument": order.get("instrument"),
                "type": "Order",
                "side": side,
                "size": size,
                "entry_price": None,
                "order_price": order.get("price"),
                "current_price": order.get("triggerPrice"),
                "stop_loss": (order.get("stopLossOnFill") or {}).get("price"),
                "take_profit": (order.get("takeProfitOnFill") or {}).get("price"),
                "leverage": order.get("marginUsed"),
                "opened_at": order.get("createTime"),
                "id": order.get("id"),
                "status": order.get("state") or "PENDING",
            }
        )
    for item in items:
        ctx = _lookup_trade_context_for_open_item(item)
        timeframe = _normalize_timeframe(item.get("timeframe"))
        if not timeframe and isinstance(ctx, dict):
            timeframe = _normalize_timeframe(ctx.get("timeframe"))
        item["timeframe"] = timeframe
        item["is_test_trade"] = _display_test_trade(ctx if isinstance(ctx, dict) else item)
    return {"items": items, "errors": fetch_errors}


async def _list_oanda_accounts(*, base_url: str, api_key: str) -> List[Dict[str, object]]:
    token = (api_key or "").strip().strip('"').strip("'")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/v3/accounts"
    timeout_s = 6.0
    timeout = httpx.Timeout(timeout_s, connect=min(3.0, timeout_s), read=timeout_s, write=timeout_s, pool=2.0)
    max_attempts = 3
    transient_statuses = {429, 502, 503, 504}
    account_context = _oanda_account_context(base_url)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                break
            except httpx.TimeoutException as exc:
                should_retry = attempt < max_attempts
                BYBIT_LOGGER.warning(
                    "OANDA_ACCOUNTS_TIMEOUT account=%s endpoint=%s attempt=%s/%s retry=%s err=%s",
                    account_context,
                    "/v3/accounts",
                    attempt,
                    max_attempts,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    message = _format_source_exception(
                        exc,
                        broker="OANDA",
                        account=account_context,
                        endpoint="/v3/accounts",
                        account_id="discovery",
                    )
                    raise ValueError(message) from exc
            except httpx.RequestError as exc:
                should_retry = attempt < max_attempts
                BYBIT_LOGGER.warning(
                    "OANDA_ACCOUNTS_REQUEST_ERR account=%s endpoint=%s attempt=%s/%s retry=%s err=%s",
                    account_context,
                    "/v3/accounts",
                    attempt,
                    max_attempts,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    message = _format_source_exception(
                        exc,
                        broker="OANDA",
                        account=account_context,
                        endpoint="/v3/accounts",
                        account_id="discovery",
                    )
                    raise ValueError(message) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                should_retry = status in transient_statuses and attempt < max_attempts
                BYBIT_LOGGER.error(
                    "OANDA_ACCOUNTS_HTTP_ERR account=%s endpoint=%s status=%s attempt=%s/%s retry=%s body=%s",
                    account_context,
                    "/v3/accounts",
                    status,
                    attempt,
                    max_attempts,
                    should_retry,
                    exc.response.text[:500],
                )
                if not should_retry:
                    message = _format_source_exception(
                        exc,
                        broker="OANDA",
                        account=account_context,
                        endpoint="/v3/accounts",
                        account_id="discovery",
                    )
                    raise ValueError(message) from exc

            backoff = min(0.2 * (2 ** (attempt - 1)), 0.8) + (0.05 * attempt)
            await asyncio.sleep(backoff)
        else:
            raise ValueError("OANDA account discovery failed after retries")

    payload = resp.json()
    return payload.get("accounts", []) or []


async def _get_cached_oanda_accounts(*, base_url: str, api_key: str) -> List[Dict[str, object]]:
    token = (api_key or "").strip().strip('"').strip("'")
    cache_key = f"{base_url.rstrip('/')}:...{token[-6:]}"
    now = time.time()
    cached = _OANDA_ACCOUNTS_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return list(cached[1])

    accounts = await _list_oanda_accounts(base_url=base_url, api_key=api_key)
    _OANDA_ACCOUNTS_CACHE[cache_key] = (now + _OANDA_ACCOUNTS_CACHE_TTL_SECONDS, accounts)
    return list(accounts)


def _build_bybit_query(params: Dict[str, str]) -> str:
    if not params:
        return ""
    return "&".join(f"{key}={value}" for key, value in sorted(params.items()))


def _is_bybit_open_order(status: Optional[str]) -> bool:
    if not status:
        return True
    normalized = status.strip().lower()
    open_statuses = {
        "new",
        "untriggered",
        "partiallyfilled",
        "triggered",
        "active",
        "working",
        "created",
        "open",
    }
    closed_statuses = {
        "filled",
        "cancelled",
        "canceled",
        "rejected",
        "deactivated",
        "expired",
        "done",
    }
    if normalized in closed_statuses:
        return False
    if normalized in open_statuses:
        return True
    return normalized not in closed_statuses


async def _bybit_signed_get(
    *, base_url: str, api_key: str, api_secret: str, path: str, params: Dict[str, str]
) -> Dict[str, object]:
    query = _build_bybit_query(params)
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, query)
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers)
    _record_outbound_traffic(
        "bybit",
        bytes_sent=len(url) + sum(len(str(v)) for v in headers.values()),
        bytes_received=len(resp.content),
        context=path,
    )
    payload: Dict[str, object] = {}
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if resp.status_code >= 400:
        ret_code = payload.get("retCode")
        ret_msg = payload.get("retMsg") or resp.text
        raise ValueError(
            f"Bybit signed GET failed path={path} http_status={resp.status_code} retCode={ret_code} retMsg={ret_msg}"
        )
    ret_code = payload.get("retCode")
    if ret_code not in (0, "0"):
        ret_msg = payload.get("retMsg") or "Bybit request failed"
        raise ValueError(f"Bybit signed GET failed path={path} retCode={ret_code} retMsg={ret_msg}")
    return payload


async def _bybit_signed_post(
    *, base_url: str, api_key: str, api_secret: str, path: str, body: Dict[str, object]
) -> Dict[str, object]:
    body_json = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, body_json)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{base_url}{path}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, content=body_json)
    _record_outbound_traffic(
        "bybit",
        bytes_sent=len(url) + len(body_json) + sum(len(str(v)) for v in headers.values()),
        bytes_received=len(resp.content),
        context=path,
    )
    resp.raise_for_status()
    payload = resp.json()
    ret_code = payload.get("retCode")
    if ret_code not in (0, "0"):
        raise ValueError(payload.get("retMsg") or "Bybit request failed")
    return payload


async def _fetch_bybit_positions_for_category(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
) -> tuple[List[Dict[str, object]], List[str]]:
    if category == "linear":
        combined: List[Dict[str, object]] = []
        errors: List[str] = []
        for settle_coin in ("USDT", "USDC"):
            try:
                payload = await _bybit_signed_get(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    path="/v5/position/list",
                    params={"category": "linear", "settleCoin": settle_coin},
                )
                combined.extend(payload.get("result", {}).get("list", []))
            except Exception as exc:
                errors.append(f"linear {settle_coin}: {exc}")
        return combined, errors
    try:
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/position/list",
            params={"category": category},
        )
    except Exception as exc:
        return [], [str(exc)]
    return payload.get("result", {}).get("list", []), []


async def _confirm_bybit_position_still_open(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
) -> bool:
    symbol_norm = str(symbol or "").strip().upper()
    if not symbol_norm:
        return False
    payload = await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/position/list",
        params={"category": category, "symbol": symbol_norm},
    )
    rows = (payload.get("result") or {}).get("list") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").strip().lower()
        size = _to_float(row.get("size")) or 0.0
        if side in {"buy", "sell"} and abs(size) > 0:
            return True
    return False


async def _fetch_bybit_orders_for_category(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
) -> tuple[List[Dict[str, object]], List[str]]:
    if category == "linear":
        combined: List[Dict[str, object]] = []
        errors: List[str] = []
        for settle_coin in ("USDT", "USDC"):
            try:
                payload = await _bybit_signed_get(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    path="/v5/order/realtime",
                    params={
                        "category": "linear",
                        "settleCoin": settle_coin,
                        "openOnly": "0",
                    },
                )
                combined.extend(payload.get("result", {}).get("list", []))
            except Exception as exc:
                errors.append(f"linear {settle_coin}: {exc}")
        return combined, errors
    try:
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/order/realtime",
            params={"category": category, "openOnly": "0"},
        )
    except Exception as exc:
        return [], [str(exc)]
    return payload.get("result", {}).get("list", []), []


async def _collect_bybit_open_items(
    *, base_url: str, api_key: str, api_secret: str, account_context: str
) -> Dict[str, List[Dict[str, object]]]:
    items: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []
    confirmed_demo_linear_symbols: Set[str] = set()
    stale_demo_linear_symbols: Set[str] = set()
    position_categories = ["linear", "inverse", "option"]
    order_categories = ["linear", "inverse", "spot", "option"]

    for category in position_categories:
        positions, position_errors = await _fetch_bybit_positions_for_category(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            category=category,
        )
        for message in position_errors:
            errors.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "message": message,
                }
            )
        for position in positions:
            symbol = str(position.get("symbol") or "").strip().upper()
            size_raw = position.get("size")
            size = None
            try:
                size_val = float(size_raw)
                if size_val == 0:
                    continue
                size = abs(size_val)
            except (TypeError, ValueError):
                size = size_raw
            if account_context == "demo" and category == "linear" and symbol:
                try:
                    still_open = await _confirm_bybit_position_still_open(
                        base_url=base_url,
                        api_key=api_key,
                        api_secret=api_secret,
                        category=category,
                        symbol=symbol,
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "broker": "Bybit",
                            "account": account_context,
                            "category": category,
                            "message": f"confirm {symbol}: {exc}",
                        }
                    )
                    still_open = True
                if not still_open:
                    stale_demo_linear_symbols.add(symbol)
                    continue
                confirmed_demo_linear_symbols.add(symbol)
            items.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "instrument": symbol or position.get("symbol"),
                    "type": "Position",
                    "side": position.get("side"),
                    "size": size,
                    "entry_price": position.get("avgPrice") or position.get("entryPrice"),
                    "order_price": None,
                    "current_price": position.get("markPrice"),
                    "stop_loss": position.get("stopLoss"),
                    "take_profit": position.get("takeProfit"),
                    "leverage": position.get("leverage")
                    or position.get("positionMargin"),
                    "opened_at": position.get("updatedTime") or position.get("createdTime"),
                    "id": position.get("positionId") or position.get("positionIdx"),
                    "position_idx": position.get("positionIdx"),
                    "status": "OPEN",
                }
            )

    for category in order_categories:
        orders, order_errors = await _fetch_bybit_orders_for_category(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            category=category,
        )
        for message in order_errors:
            errors.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "message": message,
                }
            )
        for order in orders:
            status = order.get("orderStatus")
            if not _is_bybit_open_order(status):
                continue
            symbol = str(order.get("symbol") or "").strip().upper()
            stop_order_type = str(order.get("stopOrderType") or "").strip().lower()
            if (
                account_context == "demo"
                and category == "linear"
                and symbol
                and stop_order_type in {"stoploss", "takeprofit", "partialstoploss", "partialtakeprofit"}
            ):
                if symbol in stale_demo_linear_symbols:
                    continue
                if confirmed_demo_linear_symbols and symbol not in confirmed_demo_linear_symbols:
                    continue
            items.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "instrument": symbol or order.get("symbol"),
                    "type": "Order",
                    "side": order.get("side"),
                    "size": order.get("qty"),
                    "entry_price": None,
                    "order_price": order.get("price"),
                    "current_price": order.get("triggerPrice"),
                    "stop_loss": order.get("stopLoss"),
                    "take_profit": order.get("takeProfit"),
                    "leverage": order.get("leverage"),
                    "opened_at": order.get("createdTime"),
                    "id": order.get("orderId"),
                    "order_link_id": order.get("orderLinkId"),
                    "stop_order_type": order.get("stopOrderType"),
                    "status": status or "OPEN",
                }
            )
    for item in items:
        ctx = _lookup_trade_context_for_open_item(item)
        timeframe = _normalize_timeframe(item.get("timeframe"))
        if not timeframe and isinstance(ctx, dict):
            timeframe = _normalize_timeframe(ctx.get("timeframe"))
        item["timeframe"] = timeframe
        item["is_test_trade"] = _display_test_trade(ctx if isinstance(ctx, dict) else item)
    return {"items": items, "errors": errors}


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
    _invalidate_open_orders_cache()


def _to_dt_utc(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_iso_datetime(value: object) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    if parsed is None or pd.isna(parsed):
        return None
    if hasattr(parsed, "to_pydatetime"):
        dt = parsed.to_pydatetime()
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return None


def _load_pending_webhooks() -> List[Dict[str, object]]:
    if not PENDING_WEBHOOKS_PATH.exists():
        return []
    try:
        payload = json.loads(PENDING_WEBHOOKS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read pending webhooks: {exc}",
        ) from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=500, detail="Pending webhooks data must be a list.")
    cleaned: List[Dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            cleaned.append(entry)
    return cleaned


def _save_pending_webhooks(items: List[Dict[str, object]]) -> None:
    PENDING_WEBHOOKS_PATH.write_text(
        json.dumps(items, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _schedule_dropbox_upload_state_backup()


def _normalize_pending_webhooks(items: object) -> List[Dict[str, object]]:
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Pending webhooks must be a list.")
    now_ts = int(time.time())
    cleaned: List[Dict[str, object]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        payload = dict(entry)
        webhook_id = str(payload.get("id", "")).strip() or f"wh_{uuid4().hex[:12]}"
        payload["id"] = webhook_id
        payload["broker"] = "WEBHOOK"
        payload["type"] = "webhook"
        payload.setdefault("status", "WAITING")
        payload.setdefault("enabled", True)
        payload.setdefault("created_at", now_ts)
        payload["updated_at"] = now_ts
        payload["timeframe"] = _normalize_timeframe(payload.get("timeframe"))
        payload["is_test_trade"] = _normalize_test_trade_flag(
            payload.get("is_test_trade", payload.get("test_trade", payload.get("test")))
        )
        cancel_touch = _parse_pending_cancel_touch_price(payload)
        payload["cancel_if_touched_price"] = cancel_touch
        operator = str(payload.get("cancel_if_touched_operator") or "").strip().lower()
        if operator not in {"lte", "gte"}:
            payload["cancel_if_touched_operator"] = None
        else:
            payload["cancel_if_touched_operator"] = operator
        cleaned.append(payload)
    return cleaned


def _replace_pending_webhooks(items: object) -> List[Dict[str, object]]:
    normalized = _normalize_pending_webhooks(items)
    _save_pending_webhooks(normalized)
    _invalidate_open_orders_cache()
    return normalized


def _upsert_pending_webhook(payload: Dict[str, object]) -> Dict[str, object]:
    items = _load_pending_webhooks()
    if not isinstance(payload, dict):
        payload = {}

    webhook_id = str(payload.get("id", "")).strip()
    if not webhook_id:
        webhook_id = f"wh_{uuid4().hex[:12]}"

    now_ts = int(time.time())
    entry = dict(payload)
    entry["id"] = webhook_id
    entry["broker"] = "WEBHOOK"
    entry["type"] = "webhook"
    entry.setdefault("status", "WAITING")
    entry.setdefault("enabled", True)
    entry.setdefault("created_at", now_ts)
    entry["updated_at"] = now_ts
    entry["timeframe"] = _normalize_timeframe(entry.get("timeframe"))
    entry["is_test_trade"] = _normalize_test_trade_flag(
        entry.get("is_test_trade", entry.get("test_trade", entry.get("test")))
    )
    entry["cancel_if_touched_price"] = _parse_pending_cancel_touch_price(entry)
    operator = str(entry.get("cancel_if_touched_operator") or "").strip().lower()
    entry["cancel_if_touched_operator"] = operator if operator in {"lte", "gte"} else None

    replaced = False
    for idx, existing in enumerate(items):
        if str(existing.get("id", "")).strip() == webhook_id:
            items[idx] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)

    _save_pending_webhooks(items)
    _invalidate_open_orders_cache()
    opened_at_iso = _epoch_or_iso_to_iso(entry.get("opened_at"))
    _upsert_trade_context(
        {
            "pending_webhook_id": webhook_id,
            "broker": str(entry.get("category") or entry.get("broker") or "").strip().lower(),
            "account": str(entry.get("account") or "").strip().lower(),
            "category": str(entry.get("category") or "").strip().lower(),
            "instrument": str(entry.get("instrument") or "").strip().upper(),
            "side": str(entry.get("side") or "").strip().lower(),
            "order_type": str(entry.get("order_type") or "").strip().lower(),
            "entry_price": entry.get("entry_price"),
            "stop_loss": entry.get("stop_loss"),
            "take_profit": entry.get("take_profit"),
            "timeframe": entry.get("timeframe"),
            "is_test_trade": entry.get("is_test_trade"),
            "open_time": opened_at_iso,
            "status": "ACTIVE",
            "cancel_if_touched_price": entry.get("cancel_if_touched_price"),
            "cancel_if_touched_operator": entry.get("cancel_if_touched_operator"),
            "setup_reference_price": entry.get("setup_reference_price"),
            "price_source": entry.get("price_source"),
            "cancel_reason": entry.get("cancel_reason"),
            "cancelled_at": entry.get("cancelled_at"),
        }
    )
    return entry


def _update_pending_webhook(webhook_id: str, updates: Dict[str, object]) -> Optional[Dict[str, object]]:
    items = _load_pending_webhooks()
    for idx, entry in enumerate(items):
        if str(entry.get("id", "")).strip() == webhook_id:
            merged = {**entry, **updates, "updated_at": int(time.time())}
            # Prevent callers from converting a pending-webhook record into a broker order.
            merged["broker"] = "WEBHOOK"
            merged["type"] = "webhook"
            items[idx] = merged
            _save_pending_webhooks(items)
            _invalidate_open_orders_cache()
            return merged
    return None


def _set_pending_webhook_enabled(webhook_id: str, enabled: bool) -> Dict[str, object]:
    items = _load_pending_webhooks()
    for idx, entry in enumerate(items):
        if str(entry.get("id", "")).strip() == webhook_id:
            items[idx] = {**entry, "enabled": enabled, "updated_at": int(time.time())}
            _save_pending_webhooks(items)
            _invalidate_open_orders_cache()
            return items[idx]
    raise HTTPException(status_code=404, detail="Pending webhook not found.")


def _delete_pending_webhook(webhook_id: str) -> bool:
    items = _load_pending_webhooks()
    remaining = [entry for entry in items if str(entry.get("id", "")).strip() != webhook_id]
    if len(remaining) == len(items):
        return False
    _save_pending_webhooks(remaining)
    _invalidate_open_orders_cache()
    return True


def _consume_pending_webhook(
    webhook_id: str, *, request_id: str, reason: str = "webhook_received"
) -> bool:
    pending_id = str(webhook_id or "").strip()
    if not pending_id:
        raise ValueError("pending_webhook_id is required to consume pending webhook.")
    now_iso = _utc_now_iso()
    deleted = _delete_pending_webhook(pending_id)
    BYBIT_LOGGER.info(
        "PENDING_WEBHOOK_CONSUME request_id=%s pending_webhook_id=%s deleted=%s reason=%s",
        request_id,
        pending_id,
        str(deleted).lower(),
        reason,
    )
    _upsert_trade_context(
        {
            "pending_webhook_id": pending_id,
            "status": "CONSUMED" if deleted else "TRIGGERING",
            "consumed_at": now_iso,
            "triggered_at": now_iso,
            "request_id": request_id,
            "consume_reason": str(reason or "").strip() or "webhook_received",
        }
    )
    if not deleted:
        raise ValueError("Pending webhook missing or no longer active.")
    _schedule_dropbox_upload_state_backup()
    return True


def _normalize_timeframe(value: object, *, max_length: int = 64) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > max_length:
        text = text[:max_length].strip()
    return text


def _normalize_test_trade_flag(value: object) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"yes", "y", "true", "1", "test"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return None


def _is_test_trade_row(row: Dict[str, object]) -> bool:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in ("is_test_trade", "test_trade", "test"):
        normalized = _normalize_test_trade_flag(row.get(key))
        if normalized is not None:
            return normalized
        normalized = _normalize_test_trade_flag(metrics.get(key))
        if normalized is not None:
            return normalized
    return False


def _display_test_trade(row: Dict[str, object]) -> str:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    value = (
        _normalize_test_trade_flag(row.get("is_test_trade"))
        if isinstance(row, dict)
        else None
    )
    if value is None:
        value = _normalize_test_trade_flag(metrics.get("is_test_trade"))
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "—"


def _normalize_optional_price(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    parsed = _to_float(value)
    if parsed is None:
        return str(value).strip() or None
    return str(value).strip() or f"{parsed}"


def _load_trade_contexts() -> List[Dict[str, object]]:
    payload = _load_json_file(TRADE_CONTEXTS_PATH, [])
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        return []
    return [dict(entry) for entry in payload if isinstance(entry, dict)]


def _save_trade_contexts(items: List[Dict[str, object]]) -> None:
    _save_json_file(TRADE_CONTEXTS_PATH, {"items": items, "updated_at": _utc_now_iso()})
    _schedule_dropbox_upload_state_backup()


def _load_webhook_attempts() -> List[Dict[str, object]]:
    payload = _load_json_file(WEBHOOK_ATTEMPTS_PATH, [])
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        return []
    return [dict(entry) for entry in payload if isinstance(entry, dict)]


def _save_webhook_attempts(items: List[Dict[str, object]]) -> None:
    trimmed = [dict(entry) for entry in items if isinstance(entry, dict)][
        -max(1, WEBHOOK_ATTEMPTS_MAX_ITEMS) :
    ]
    _save_json_file(
        WEBHOOK_ATTEMPTS_PATH, {"items": trimmed, "updated_at": _utc_now_iso()}
    )
    _schedule_dropbox_upload_state_backup()


def _record_webhook_attempt(payload: Dict[str, object]) -> Dict[str, object]:
    attempt = dict(payload or {})
    request_id = str(attempt.get("request_id") or "").strip() or f"wh-attempt-{uuid4().hex[:12]}"
    now_iso = _utc_now_iso()
    attempt["request_id"] = request_id
    attempt.setdefault("received_at", now_iso)
    attempt["updated_at"] = now_iso
    items = _load_webhook_attempts()
    items = [entry for entry in items if str(entry.get("request_id") or "").strip() != request_id]
    items.append(attempt)
    _save_webhook_attempts(items)
    return dict(attempt)


def _update_webhook_attempt(request_id: str, updates: Dict[str, object]) -> Optional[Dict[str, object]]:
    attempt_id = str(request_id or "").strip()
    if not attempt_id:
        return None
    items = _load_webhook_attempts()
    for idx, item in enumerate(items):
        if str(item.get("request_id") or "").strip() != attempt_id:
            continue
        merged = {**item, **dict(updates or {}), "updated_at": _utc_now_iso()}
        items[idx] = merged
        _save_webhook_attempts(items)
        return dict(merged)
    return None


def _public_webhook_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_WEBHOOK_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url
    return str(request.base_url).rstrip("/")


def _prune_trade_contexts(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    kept: List[Dict[str, object]] = []
    for entry in items:
        status = str(entry.get("status") or "").strip().upper()
        updated_at = _parse_iso_datetime(entry.get("updated_at") or entry.get("closed_at"))
        if status in {"CLOSED", "CANCELLED"} and updated_at and updated_at < cutoff:
            continue
        kept.append(entry)
    return kept


def _upsert_trade_context(payload: Dict[str, object]) -> Dict[str, object]:
    items = _load_trade_contexts()
    now_iso = _utc_now_iso()
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    order_id = str(payload.get("order_id") or "").strip()
    order_link_id = str(payload.get("order_link_id") or "").strip()
    parent_order_link_id = str(payload.get("parent_order_link_id") or "").strip()
    trade_id = str(payload.get("trade_id") or "").strip()
    transaction_id = str(payload.get("transaction_id") or "").strip()

    def _is_blank(value: object) -> bool:
        return value is None or (isinstance(value, str) and value.strip() == "")

    id_fields = ("pending_webhook_id", "order_id", "order_link_id", "parent_order_link_id", "trade_id", "transaction_id")
    incoming_ids = {
        "pending_webhook_id": pending_id,
        "order_id": order_id,
        "order_link_id": order_link_id,
        "parent_order_link_id": parent_order_link_id,
        "trade_id": trade_id,
        "transaction_id": transaction_id,
    }

    merged_payload = dict(payload)
    if "is_test_trade" in merged_payload or "test_trade" in merged_payload or "test" in merged_payload:
        merged_payload["is_test_trade"] = _normalize_test_trade_flag(
            merged_payload.get("is_test_trade", merged_payload.get("test_trade", merged_payload.get("test")))
        )
    if "timeframe" in merged_payload:
        merged_payload["timeframe"] = _normalize_timeframe(merged_payload.get("timeframe"))
    for field in ("entry_price", "stop_loss", "take_profit"):
        if field in merged_payload:
            merged_payload[field] = _normalize_optional_price(merged_payload.get(field))
    for field in (
        "broker",
        "account",
        "category",
        "instrument",
        "side",
        "order_type",
        "pending_webhook_id",
        "order_id",
        "order_link_id",
        "parent_order_link_id",
        "trade_id",
        "transaction_id",
    ):
        if field in merged_payload and merged_payload.get(field) is not None:
            merged_payload[field] = str(merged_payload.get(field)).strip()
    merged_payload["updated_at"] = now_iso
    merged_payload.setdefault("created_at", now_iso)
    if not merged_payload.get("status"):
        merged_payload["status"] = "ACTIVE"

    matched_indices: List[int] = []
    for idx, entry in enumerate(items):
        for id_field in id_fields:
            incoming_id = incoming_ids.get(id_field, "")
            if not incoming_id:
                continue
            if str(entry.get(id_field) or "").strip() == incoming_id:
                matched_indices.append(idx)
                break

    if matched_indices:
        base_idx = matched_indices[0]
        merged = dict(items[base_idx])
        for idx in matched_indices[1:]:
            for k, v in items[idx].items():
                if k in {"created_at", "updated_at"}:
                    continue
                if _is_blank(merged.get(k)) and not _is_blank(v):
                    merged[k] = v

        for k, v in merged_payload.items():
            if k == "created_at":
                if _is_blank(merged.get("created_at")):
                    merged["created_at"] = v
                continue
            if _is_blank(v):
                continue
            merged[k] = v
        if merged_payload.get("is_test_trade") is None and "is_test_trade" in merged and merged.get("is_test_trade") is not None:
            pass
        elif "is_test_trade" in merged_payload:
            merged["is_test_trade"] = merged_payload.get("is_test_trade")

        merged["updated_at"] = now_iso
        merged.setdefault("created_at", now_iso)
        for field in id_fields:
            if _is_blank(merged.get(field)) and incoming_ids.get(field):
                merged[field] = incoming_ids[field]

        deduped_items = [entry for idx, entry in enumerate(items) if idx not in matched_indices]
        deduped_items.insert(base_idx, merged)
        items = deduped_items
        merged_payload = merged
    else:
        items.append(merged_payload)

    pruned = _prune_trade_contexts(items)
    _save_trade_contexts(pruned)
    return merged_payload


def _mark_trade_context_closed_or_cancelled(*, pending_webhook_id: Optional[str] = None, order_id: Optional[str] = None, trade_id: Optional[str] = None, status: str = "CLOSED") -> None:
    items = _load_trade_contexts()
    now_iso = _utc_now_iso()
    changed = False
    for idx, entry in enumerate(items):
        matches = False
        if pending_webhook_id and str(entry.get("pending_webhook_id") or "").strip() == str(pending_webhook_id).strip():
            matches = True
        if order_id and str(entry.get("order_id") or "").strip() == str(order_id).strip():
            matches = True
        if trade_id and str(entry.get("trade_id") or "").strip() == str(trade_id).strip():
            matches = True
        if matches:
            entry = dict(entry)
            entry["status"] = status
            entry["closed_at"] = now_iso
            entry["updated_at"] = now_iso
            items[idx] = entry
            changed = True
    if changed:
        _save_trade_contexts(_prune_trade_contexts(items))


def _lookup_trade_context_for_open_item(item: Dict[str, object]) -> Optional[Dict[str, object]]:
    contexts = _load_trade_contexts()
    broker = str(item.get("broker") or "").strip().lower()
    account = str(item.get("account") or "").strip().lower()
    instrument = str(item.get("instrument") or "").strip().upper()
    side = _normalize_side_for_comparison(item.get("side"))
    order_id = str(item.get("id") or item.get("order_id") or "").strip()
    order_link_id = str(item.get("order_link_id") or item.get("orderLinkId") or "").strip()
    parent_order_link_id = str(
        item.get("parent_order_link_id") or item.get("parentOrderLinkId") or ""
    ).strip()
    trade_id = str(item.get("trade_id") or "").strip()

    for ctx in contexts:
        if order_id and str(ctx.get("order_id") or "").strip() == order_id:
            return ctx
    for ctx in contexts:
        if order_link_id and str(ctx.get("order_link_id") or "").strip() == order_link_id:
            return ctx
    for ctx in contexts:
        if parent_order_link_id and str(ctx.get("parent_order_link_id") or "").strip() == parent_order_link_id:
            return ctx
    for ctx in contexts:
        if trade_id and str(ctx.get("trade_id") or "").strip() == trade_id:
            return ctx

    candidates = [
        ctx
        for ctx in contexts
        if str(ctx.get("broker") or "").strip().lower() == broker
        and str(ctx.get("account") or "").strip().lower() == account
        and str(ctx.get("instrument") or "").strip().upper() == instrument
        and _normalize_side_for_comparison(ctx.get("side")) == side
        and str(ctx.get("status") or "ACTIVE").strip().upper() == "ACTIVE"
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    opened_ts = _canonical_trade_epoch_second(item.get("opened_at") or item.get("created_at"))
    if opened_ts is None:
        return None
    scored: List[Tuple[int, int, Dict[str, object]]] = []
    for ctx in candidates:
        ctx_opened_ts = (
            _canonical_trade_epoch_second(ctx.get("open_time"))
            or _canonical_trade_epoch_second(ctx.get("created_at"))
            or _canonical_trade_epoch_second(ctx.get("opened_at"))
        )
        if ctx_opened_ts is None:
            continue
        delta = abs(opened_ts - ctx_opened_ts)
        ctx_status_boost = 0 if str(ctx.get("status") or "").strip().upper() == "ACTIVE" else 1
        scored.append((delta, ctx_status_boost, ctx))
    if scored:
        scored.sort(key=lambda item: (item[0], item[1]))
        return scored[0][2]
    return None


def _lookup_trade_context_for_journal_row(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    contexts = _load_trade_contexts()
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    order_id = str(
        refs.get("orderId")
        or refs.get("orderID")
        or row.get("orderId")
        or row.get("orderID")
        or row.get("order_id")
        or ""
    ).strip()
    trade_id = str(refs.get("tradeId") or refs.get("tradeID") or "").strip()
    transaction_id = str(refs.get("transactionId") or refs.get("transactionID") or "").strip()
    order_link_id = str(
        refs.get("orderLinkId")
        or refs.get("order_link_id")
        or refs.get("parentOrderLinkId")
        or refs.get("parent_order_link_id")
        or row.get("orderLinkId")
        or row.get("order_link_id")
        or ""
    ).strip()
    parent_order_link_id = str(
        refs.get("parentOrderLinkId")
        or refs.get("parent_order_link_id")
        or row.get("parentOrderLinkId")
        or row.get("parent_order_link_id")
        or ""
    ).strip()
    pending_webhook_id = str(refs.get("pending_webhook_id") or row.get("pending_webhook_id") or "").strip()
    for ctx in contexts:
        if order_id and str(ctx.get("order_id") or "").strip() == order_id:
            return ctx
    for ctx in contexts:
        if order_link_id and str(ctx.get("order_link_id") or "").strip() == order_link_id:
            return ctx
    for ctx in contexts:
        if parent_order_link_id and str(ctx.get("parent_order_link_id") or "").strip() == parent_order_link_id:
            return ctx
    for ctx in contexts:
        if trade_id and str(ctx.get("trade_id") or "").strip() == trade_id:
            return ctx
    for ctx in contexts:
        if transaction_id and str(ctx.get("transaction_id") or "").strip() == transaction_id:
            return ctx
    for ctx in contexts:
        if pending_webhook_id and str(ctx.get("pending_webhook_id") or "").strip() == pending_webhook_id:
            return ctx
    return None


def _lookup_trade_context_by_market_window(
    row: Dict[str, object],
    *,
    max_window_seconds: int = 90 * 60,
    include_inactive: bool = False,
) -> Optional[Dict[str, object]]:
    contexts = _load_trade_contexts()
    broker = str(row.get("broker") or row.get("source") or "").strip().lower()
    account = str(row.get("account") or "").strip().lower()
    instrument = str(row.get("instrument") or row.get("symbol") or "").strip().upper()
    side = _normalize_side_for_comparison(row.get("side"))
    target_sec = _canonical_trade_epoch_second(row.get("close_time") or row.get("open_time"))
    if not broker or not account or not instrument or not side or target_sec is None:
        return None

    candidates: List[Tuple[int, Dict[str, object]]] = []
    for ctx in contexts:
        if str(ctx.get("broker") or "").strip().lower() != broker:
            continue
        if str(ctx.get("account") or "").strip().lower() != account:
            continue
        if str(ctx.get("instrument") or "").strip().upper() != instrument:
            continue
        if _normalize_side_for_comparison(ctx.get("side")) != side:
            continue
        if not include_inactive and str(ctx.get("status") or "ACTIVE").strip().upper() != "ACTIVE":
            continue
        ctx_time_sec = (
            _canonical_trade_epoch_second(ctx.get("closed_at"))
            or _canonical_trade_epoch_second(ctx.get("updated_at"))
            or _canonical_trade_epoch_second(ctx.get("created_at"))
            or _canonical_trade_epoch_second(ctx.get("open_time"))
            or _canonical_trade_epoch_second(ctx.get("opened_at"))
        )
        if ctx_time_sec is None:
            continue
        delta = abs(ctx_time_sec - target_sec)
        if delta <= max_window_seconds:
            candidates.append((delta, ctx))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _resolve_bybit_closed_pnl_trade_context(
    *,
    account_mode: str,
    symbol: str,
    side: object,
    order_id: object = None,
    order_link_id: object = None,
    parent_order_link_id: object = None,
    trade_id: object = None,
    transaction_id: object = None,
    close_time: object = None,
    max_lookback_seconds: int = 7 * 24 * 60 * 60,
) -> Optional[Dict[str, object]]:
    refs = {
        "order_id": str(order_id or "").strip(),
        "order_link_id": str(order_link_id or "").strip(),
        "parent_order_link_id": str(parent_order_link_id or "").strip(),
        "trade_id": str(trade_id or "").strip(),
        "transaction_id": str(transaction_id or "").strip(),
    }
    contexts = _load_trade_contexts()
    close_ts = _canonical_trade_epoch_second(close_time)

    def _ctx_time_order_valid(ctx: Dict[str, object]) -> bool:
        if close_ts is None:
            return True
        ctx_open_ts = _canonical_trade_epoch_second(ctx.get("open_time")) or _canonical_trade_epoch_second(ctx.get("created_at"))
        if ctx_open_ts is None:
            return True
        return ctx_open_ts < close_ts

    for ref_field in ("order_id", "order_link_id", "parent_order_link_id", "trade_id", "transaction_id"):
        ref_value = refs[ref_field]
        if not ref_value:
            continue
        for ctx in contexts:
            if str(ctx.get(ref_field) or "").strip() == ref_value and _ctx_time_order_valid(ctx):
                return ctx
    broker = "bybit"
    account = "demo" if str(account_mode).strip().lower() == "demo" else "live"
    instrument = str(symbol or "").strip().upper()
    side_norm = _normalize_side_for_comparison(side)
    if not close_ts or not instrument or not side_norm:
        return None

    candidates: List[Tuple[int, int, Dict[str, object]]] = []
    for ctx in contexts:
        if str(ctx.get("broker") or "").strip().lower() != broker:
            continue
        if str(ctx.get("account") or "").strip().lower() != account:
            continue
        if str(ctx.get("instrument") or "").strip().upper() != instrument:
            continue
        if _normalize_side_for_comparison(ctx.get("side")) != side_norm:
            continue
        ctx_open_ts = _canonical_trade_epoch_second(ctx.get("open_time")) or _canonical_trade_epoch_second(ctx.get("created_at"))
        if ctx_open_ts is None or ctx_open_ts > close_ts:
            continue
        if close_ts - ctx_open_ts > max_lookback_seconds:
            continue
        status = str(ctx.get("status") or "").strip().upper()
        status_boost = 0 if status == "ACTIVE" else 1
        candidates.append((close_ts - ctx_open_ts, status_boost, ctx))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _oanda_credentials(mode: str) -> Dict[str, str]:
    suffix = "_DEMO" if mode == "demo" else ""
    api_key = os.getenv(f"OANDA_API_KEY{suffix}") or os.getenv(f"OANDA_TOKEN{suffix}")
    account_id = os.getenv(f"OANDA_ACCOUNT_ID{suffix}")
    base_url = (
        os.getenv(f"OANDA_BASE_URL{suffix}")
        or os.getenv(f"OANDA_URL{suffix}")
        or os.getenv(f"OANDA_API_URL{suffix}")
        or _oanda_base_url()
    )
    return {
        "api_key": api_key or "",
        "account_id": account_id or "",
        "base_url": base_url,
    }
BALANCE_LOGGER = logging.getLogger("uvicorn.error")
BYBIT_LOGGER = logging.getLogger("uvicorn.error")
_OUTBOUND_METRICS_LOCK = threading.Lock()
_OUTBOUND_METRICS: Dict[str, Dict[str, object]] = {}


def _record_outbound_traffic(
    destination: str,
    *,
    request_count: int = 1,
    bytes_sent: int = 0,
    bytes_received: int = 0,
    context: Optional[str] = None,
) -> None:
    now = _utc_now_iso()
    with _OUTBOUND_METRICS_LOCK:
        node = _OUTBOUND_METRICS.setdefault(
            destination,
            {
                "requests": 0,
                "bytes_sent": 0,
                "bytes_received": 0,
                "last_context": None,
                "last_seen": None,
            },
        )
        node["requests"] = int(node.get("requests", 0)) + max(0, int(request_count))
        node["bytes_sent"] = int(node.get("bytes_sent", 0)) + max(0, int(bytes_sent))
        node["bytes_received"] = int(node.get("bytes_received", 0)) + max(0, int(bytes_received))
        if context:
            node["last_context"] = context
        node["last_seen"] = now


def _snapshot_outbound_traffic() -> Dict[str, Dict[str, object]]:
    with _OUTBOUND_METRICS_LOCK:
        return {
            key: {
                "requests": int(value.get("requests", 0)),
                "bytes_sent": int(value.get("bytes_sent", 0)),
                "bytes_received": int(value.get("bytes_received", 0)),
                "last_context": value.get("last_context"),
                "last_seen": value.get("last_seen"),
            }
            for key, value in _OUTBOUND_METRICS.items()
        }


async def _log_outbound_traffic_summary() -> None:
    while True:
        await asyncio.sleep(max(30.0, OUTBOUND_METRICS_LOG_SECONDS))
        snapshot = _snapshot_outbound_traffic()
        if not snapshot:
            if _is_scanner_local_ui_mode():
                BYBIT_LOGGER.info(
                    "OUTBOUND_TRAFFIC_UI_SERVICE no outbound traffic recorded yet (UI service only; not scanner health)."
                )
            else:
                BYBIT_LOGGER.info("OUTBOUND_TRAFFIC no outbound traffic recorded yet.")
            continue
        top_entries = sorted(
            snapshot.items(),
            key=lambda item: int(item[1].get("bytes_sent", 0)) + int(item[1].get("bytes_received", 0)),
            reverse=True,
        )[:8]
        summary = ", ".join(
            f"{dest}:req={vals['requests']} tx={vals['bytes_sent']} rx={vals['bytes_received']}"
            for dest, vals in top_entries
        )
        BYBIT_LOGGER.info("OUTBOUND_TRAFFIC %s", summary)


def _get_telegram_credentials() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or ""
    return token, chat_id


async def _send_telegram_alert(message: str) -> None:
    token, chat_id = _get_telegram_credentials()
    if not token or not chat_id:
        BYBIT_LOGGER.info("Telegram alerts not configured; skipping alert.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    payload_raw = json.dumps(payload, separators=(",", ":"))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
        _record_outbound_traffic(
            "telegram",
            bytes_sent=len(url) + len(payload_raw),
            bytes_received=len(resp.content),
            context="/sendMessage",
        )
    except Exception as exc:  # pragma: no cover - network failure
        BYBIT_LOGGER.error("Telegram alert failed: %s", exc)


def _format_trade_alert(
    payload: Dict[str, object],
    result: Optional[Dict[str, object]] = None,
    error: Optional[str] = None,
) -> str:
    symbol = str(payload.get("symbol", ""))
    action = str(payload.get("action", ""))
    qty = payload.get("quantity")
    account = str(payload.get("account", "live"))
    trade_mode = str(payload.get("trade_mode", "linear"))
    status = "FAILED" if error else "OK"
    lines = [
        f"Trade {status}",
        f"Account: {account}",
        f"Trade mode: {trade_mode}",
        f"Symbol: {symbol}",
        f"Side: {action}",
        f"Qty: {qty}",
    ]
    if result:
        order = result.get("order", {})
        if order:
            lines.append(f"Order ID: {order.get('orderId', '')}")
        tp_order = result.get("tp_order")
        tp_error = result.get("tp_error")
        if tp_order:
            lines.append(f"TP order: {tp_order.get('orderId', '')}")
        if tp_error:
            lines.append(f"TP error: {tp_error}")
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)


def _format_bybit_fill_alert(payload: Dict[str, object]) -> str:
    symbol = payload.get("symbol")
    side = payload.get("side")
    qty = payload.get("qty")
    price = payload.get("execPrice") or payload.get("fillPrice")
    exec_type = payload.get("execType") or payload.get("type")
    category = payload.get("category")
    reduce_only = payload.get("reduceOnly")
    account = payload.get("account")
    lines = [
        "Bybit fill",
        f"Account: {account}",
        f"Category: {category}",
        f"Symbol: {symbol}",
        f"Side: {side}",
        f"Qty: {qty}",
        f"Price: {price}",
        f"Type: {exec_type}",
        f"Reduce only: {reduce_only}",
    ]
    return "\n".join(lines)


def _format_oanda_fill_alert(payload: Dict[str, object]) -> str:
    tx_type = payload.get("type")
    instrument = payload.get("instrument")
    units = payload.get("units")
    price = payload.get("price")
    account = payload.get("account")
    lines = [
        "OANDA fill",
        f"Account: {account}",
        f"Type: {tx_type}",
        f"Instrument: {instrument}",
        f"Units: {units}",
        f"Price: {price}",
        f"Reason: {payload.get('reason')}",
    ]
    return "\n".join(lines)


def _log_webhook_event(request_id: str, stage: str, details: Dict[str, object]) -> None:
    BYBIT_LOGGER.info(
        "WEBHOOK_TPSL %s %s",
        request_id,
        json.dumps({"stage": stage, **details}, sort_keys=True, default=str),
    )


def _bybit_sign_request(timestamp: str, api_key: str, api_secret: str, body: str) -> str:
    payload = f"{timestamp}{api_key}{BYBIT_RECV_WINDOW}{body}"
    return hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _parse_trigger_price(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or "{{close}}" in text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_offset_value(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_trigger_offset(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or "{{close}}" not in text:
        return None
    normalized = text.replace("{{close}}", "").strip()
    if not normalized:
        return None
    for op in ("+", "-"):
        if op in normalized:
            parts = normalized.split(op, 1)
            if len(parts) != 2:
                continue
            offset_text = parts[1].strip()
            if not offset_text:
                continue
            try:
                offset = float(offset_text)
            except ValueError:
                return None
            return offset if op == "+" else -offset
    return None


def _parse_limit_cancel_settings(payload: Dict[str, object]) -> tuple[Optional[float], Optional[float]]:
    offset = _parse_offset_value(
        payload.get("limit_cancel_offset")
        or payload.get("limit_cancel_distance")
        or payload.get("limit_cancel_value")
    )
    pct = _parse_offset_value(
        payload.get("limit_cancel_offset_pct")
        or payload.get("limit_cancel_pct")
        or payload.get("limit_cancel_percent")
    )
    if pct is not None and pct <= 0:
        pct = None
    if offset is not None and offset <= 0:
        offset = None
    return offset, pct


def _parse_pending_cancel_touch_price(payload: Dict[str, object]) -> Optional[float]:
    raw = (
        payload.get("cancel_if_touched_price")
        or payload.get("cancel_touch_price")
        or payload.get("pending_cancel_price")
    )
    value = _to_float(raw)
    if value is None or value <= 0:
        return None
    return value


def _pending_cancel_touch_triggered(
    *, current_price: float, cancel_price: float, operator: str
) -> bool:
    op = str(operator or "").strip().lower()
    if op == "lte":
        return current_price <= cancel_price
    if op == "gte":
        return current_price >= cancel_price
    return False


def _bybit_position_idx_for_order(*, side: str, configured_mode: str = "") -> int:
    mode = str(configured_mode or "").strip().lower()
    if mode == "hedge":
        return 1 if str(side or "").strip().lower() == "buy" else 2
    return 0


def _assert_pending_webhook_executable(payload: Dict[str, object]) -> None:
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    if not pending_id:
        allow_without_pending = str(os.getenv("ALLOW_EXECUTE_WITHOUT_PENDING_WEBHOOK") or "").strip().lower() in {"1", "true", "yes", "on"}
        if allow_without_pending:
            return
        raise ValueError("pending_webhook_id is required for calculator webhook execution.")
    items = _load_pending_webhooks()
    found = None
    for item in items:
        if str(item.get("id") or "").strip() == pending_id:
            found = item
            break
    if not isinstance(found, dict):
        raise ValueError("Pending webhook missing or no longer active.")
    if not bool(found.get("enabled", True)):
        raise ValueError("Pending webhook cancelled by cancel-touch rule.")
    status = str(found.get("status") or "").strip().upper()
    if status in {"CANCELLED", "CLOSED"}:
        reason = str(found.get("cancel_reason") or "").strip()
        if reason == "cancel_price_touched":
            raise ValueError("Pending webhook cancelled by cancel-touch rule.")
        raise ValueError(f"Pending webhook is not executable (status={status}).")


def _expiry_to_bybit_expdate(expiry_dmy: str) -> str:
    parts = [p.strip() for p in str(expiry_dmy).replace("-", "/").split("/") if p.strip()]
    if len(parts) != 3:
        raise ValueError("expiry must be D/M/YY")
    day, month, year = (int(part) for part in parts)
    if year < 100:
        year += 2000
    dt = datetime(year, month, day)
    return f"{dt.day}{dt.strftime('%b').upper()}{str(dt.year)[2:]}"


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    n = math.floor(value / step + 1e-12)
    return n * step


async def _bybit_public_get(base_url: str, endpoint: str, params: Dict[str, str]) -> Dict[str, object]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}{endpoint}", params=params)
    response.raise_for_status()
    data = response.json()
    if data.get("retCode") != 0:
        raise ValueError(f"Bybit public API error: {data.get('retMsg')}")
    return data


async def _resolve_trendline_option_order(
    *,
    base_url: str,
    base_coin: str,
    option_type: str,
    expiry: str,
    order_type: str,
    risk_usdt: float,
    tolerance_usdt: float,
    fee_mode: str,
    request_id: str,
) -> Dict[str, object]:
    resolved = await asyncio.to_thread(
        resolve_option_by_target_risk,
        base_url=base_url,
        account_mode="live",
        base_coin=base_coin,
        side="Buy",
        option_type=option_type,
        order_type=order_type,
        target_risk_usdt=risk_usdt,
        tolerance_usdt=tolerance_usdt,
        expiry_mode="manual",
        manual_expiry=expiry,
        strike_mode="auto",
        manual_strike="",
        quantity_mode="auto",
        manual_quantity=0.0,
        manual_limit_price=None,
        fee_mode=fee_mode,
    )
    result = {
        "symbol": resolved["resolved_symbol"],
        "qty": resolved["resolved_qty"],
        "limit_price": resolved["entry_price_used"],
        "total_est": resolved["estimated_total_cost"],
        "resolved_option": resolved,
    }
    _log_webhook_event(request_id, "trendline_option_resolved", result)
    return result


async def _fetch_bybit_positions(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    request_id: str,
) -> List[Dict[str, object]]:
    params = {"category": category, "symbol": symbol}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    path = "/v5/position/list"
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, query)
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{base_url}{path}?{query}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    _log_webhook_event(
        request_id,
        "position_list_response",
        {
            "retCode": payload.get("retCode"),
            "retMsg": payload.get("retMsg"),
            "result_count": len(payload.get("result", {}).get("list", [])),
        },
    )
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit position lookup failed: {payload.get('retMsg')}")
    return payload.get("result", {}).get("list", [])


async def _wait_for_position_entry(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    request_id: str,
    attempts: int = 6,
    delay_seconds: float = 0.6,
) -> Optional[Dict[str, object]]:
    for _ in range(attempts):
        positions = await _fetch_bybit_positions(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            category=category,
            symbol=symbol,
            request_id=request_id,
        )
        for position in positions:
            size = _parse_offset_value(position.get("size"))
            if size and size > 0:
                avg_price = _parse_offset_value(
                    position.get("avgPrice") or position.get("entryPrice")
                )
                if avg_price and avg_price > 0:
                    return position
        await asyncio.sleep(delay_seconds)
    return None


async def _set_bybit_trading_stop(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    take_profit: Optional[float],
    stop_loss: Optional[float],
    position_idx: Optional[int],
    request_id: str,
) -> Dict[str, object]:
    body: Dict[str, object] = {
        "category": category,
        "symbol": symbol,
        "tpslMode": "Full",
    }
    if position_idx is not None:
        body["positionIdx"] = position_idx
    if category != "option":
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
    _log_webhook_event(request_id, "trading_stop_request", {"payload": body})
    body_json = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, body_json)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{base_url}/v5/position/trading-stop", headers=headers, content=body_json
        )
    resp.raise_for_status()
    payload = resp.json()
    _log_webhook_event(
        request_id,
        "trading_stop_response",
        {
            "retCode": payload.get("retCode"),
            "retMsg": payload.get("retMsg"),
            "result": payload.get("result", {}),
        },
    )
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit trading-stop failed: {payload.get('retMsg')}")
    return payload.get("result", {})


def _price_levels_match(
    lhs: Optional[float],
    rhs: Optional[float],
    tolerance: float = 1e-8,
    tick_size: Optional[float] = None,
) -> bool:
    if lhs is None and rhs is None:
        return True
    if lhs is None or rhs is None:
        return False
    scale = max(1.0, abs(lhs), abs(rhs))
    dynamic_tolerance = max(tolerance, scale * 1e-8)
    tick_val = _to_float(tick_size)
    if tick_val and tick_val > 0:
        dynamic_tolerance = max(dynamic_tolerance, tick_val / 2.0)
    return abs(lhs - rhs) <= dynamic_tolerance


def _extract_position_tpsl_levels(position: Optional[Dict[str, object]]) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(position, dict):
        return None, None
    return (
        _parse_bybit_price_level(position.get("takeProfit")),
        _parse_bybit_price_level(position.get("stopLoss")),
    )


async def _place_bybit_reduce_only_limit(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    request_id: str,
) -> Dict[str, object]:
    if category == "option":
        price = await _round_option_price_to_tick(
            base_url=base_url, symbol=symbol, price=price
        )
    body: Dict[str, object] = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Limit",
        "qty": str(qty),
        "price": str(price),
        "timeInForce": "GTC",
        "orderLinkId": uuid4().hex,
        "reduceOnly": True,
    }
    _log_webhook_event(request_id, "tp_limit_request", {"payload": body})
    body_json = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, body_json)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{base_url}/v5/order/create", headers=headers, content=body_json
        )
    response.raise_for_status()
    payload = response.json()
    _log_webhook_event(
        request_id,
        "tp_limit_response",
        {
            "retCode": payload.get("retCode"),
            "retMsg": payload.get("retMsg"),
            "result": payload.get("result", {}),
        },
    )
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit TP limit order failed: {payload.get('retMsg')}")
    return payload.get("result", {})


_OPTION_TICK_CACHE: Dict[str, float] = {}


async def _fetch_option_tick_size(*, base_url: str, symbol: str) -> float:
    if symbol in _OPTION_TICK_CACHE:
        return _OPTION_TICK_CACHE[symbol]
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "symbol": symbol}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}{endpoint}", params=params)
    response.raise_for_status()
    payload = response.json()
    if payload.get("retCode") != 0:
        raise ValueError(
            f"Bybit option tick lookup failed: {payload.get('retMsg')}"
        )
    lst = payload.get("result", {}).get("list", [])
    if not lst:
        raise ValueError("Bybit option tick lookup returned no data.")
    tick = float(lst[0].get("priceFilter", {}).get("tickSize", 0) or 0)
    if tick <= 0:
        raise ValueError("Bybit option tick size is missing.")
    _OPTION_TICK_CACHE[symbol] = tick
    return tick


async def _round_option_price_to_tick(
    *, base_url: str, symbol: str, price: float
) -> float:
    tick = await _fetch_option_tick_size(base_url=base_url, symbol=symbol)
    rounded = round(price / tick) * tick
    return max(rounded, tick)




async def _place_bybit_order(
    payload: Dict[str, object], *, request_id: str
) -> Dict[str, object]:
    request_open_time_iso = _utc_now_iso()
    symbol = str(payload.get("symbol", "")).upper()
    action = str(payload.get("action", "")).lower()
    qty = payload.get("quantity")
    account = str(payload.get("account", "live")).lower()
    trade_mode = str(payload.get("trade_mode", "linear")).lower()
    options_mode = str(payload.get("options_mode", "")).lower()
    is_trendline_options = trade_mode == "options" and options_mode == "trendline"
    _log_webhook_event(
        request_id,
        "payload_parsed",
        {
            "symbol": symbol,
            "action": action,
            "quantity": qty,
            "account": account,
            "trade_mode": trade_mode,
            "take_profit_offset": payload.get("take_profit_offset")
            or payload.get("tp_offset"),
            "stop_loss_offset": payload.get("stop_loss_offset") or payload.get("sl_offset"),
            "take_profit_price": payload.get("take_profit_price"),
            "stop_loss_price": payload.get("stop_loss_price"),
        },
    )

    if action not in {"buy", "sell"}:
        raise ValueError("Webhook payload must include action=buy|sell.")
    if not symbol and not is_trendline_options:
        raise ValueError("Webhook payload must include a symbol.")
    if qty is None and not is_trendline_options:
        raise ValueError("Webhook payload must include quantity.")
    if is_trendline_options and action != "buy":
        raise ValueError("Trendline Options mode only supports action=buy.")

    qty_val: Optional[float] = None
    if not is_trendline_options:
        try:
            qty_val = float(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError("Webhook payload quantity must be numeric.") from exc

        if qty_val <= 0:
            raise ValueError("Webhook payload quantity must be greater than zero.")

    if account not in {"live", "demo"}:
        raise ValueError("Webhook payload account must be live or demo.")

    if trade_mode == "options":
        category = "option"
    else:
        category = "spot" if trade_mode == "spot" else "linear"
    side = "Buy" if action == "buy" else "Sell"
    order_type_raw = payload.get("order_type") or payload.get("orderType") or "market"
    order_type = str(order_type_raw).lower().strip()
    if order_type not in {"market", "limit"}:
        raise ValueError("Webhook payload order_type must be market or limit.")
    if is_trendline_options:
        order_type = "market"
    level_anchor_mode = str(payload.get("level_anchor_mode", "actual_fill")).strip().lower()
    if level_anchor_mode not in {"planned_entry", "actual_fill"}:
        level_anchor_mode = "actual_fill"
    limit_cancel_offset, limit_cancel_pct = _parse_limit_cancel_settings(payload)

    price_val = None
    if order_type == "limit":
        price_raw = payload.get("price") or payload.get("entry_price") or payload.get(
            "limit_price"
        )
        if price_raw is None and not is_trendline_options:
            raise ValueError("Limit orders require price.")
        if price_raw is not None:
            try:
                price_val = float(price_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Limit order price must be numeric.") from exc
            if price_val <= 0:
                raise ValueError("Limit order price must be greater than zero.")

    _mode, api_key, api_secret, base_url, key_source = resolve_bybit_credentials_for(
        "demo" if account == "demo" else "live"
    )
    if not api_key or not api_secret:
        raise ValueError("Bybit credentials are missing for the selected account.")
    _log_webhook_event(
        request_id,
        "account_context",
        {
            "account": account,
            "category": category,
            "base_url": base_url,
            "key_source": key_source,
        },
    )

    if is_trendline_options:
        resolved = await _resolve_trendline_option_order(
            base_url=base_url,
            base_coin=str(payload.get("base_coin", "")).upper(),
            option_type=str(payload.get("option_type", "Call")),
            expiry=str(payload.get("expiry", "")),
            order_type=order_type,
            risk_usdt=float(payload.get("risk_usdt", 0) or 0),
            tolerance_usdt=float(payload.get("risk_tolerance_usdt", 0.5) or 0.5),
            fee_mode=str(payload.get("fee_mode", "roundtrip") or "roundtrip"),
            request_id=request_id,
        )
        symbol = str(resolved.get("symbol", "")).upper()
        qty_val = float(resolved.get("qty", 0) or 0)
        if order_type == "limit":
            price_val = float(resolved.get("limit_price", 0) or 0)
        if not symbol:
            raise ValueError("Trendline options resolver did not return a symbol.")
        if qty_val <= 0:
            raise ValueError("Trendline options resolver returned invalid qty.")
        if order_type == "limit" and (price_val is None or price_val <= 0):
            raise ValueError("Trendline options resolver returned invalid price.")

    tick_size_dec: Optional[Decimal] = None
    if category == "linear":
        try:
            symbol_meta = await _bybit_lookup_symbol(base_url, symbol)
            if isinstance(symbol_meta, dict):
                tick_size_dec = Decimal(str((symbol_meta.get("priceFilter") or {}).get("tickSize") or "0"))
        except Exception:
            tick_size_dec = None
        if tick_size_dec is not None and tick_size_dec <= 0:
            tick_size_dec = None

    def _normalize_linear_price(value: Optional[float]) -> Optional[float]:
        if value is None or category != "linear" or tick_size_dec is None:
            return value
        snapped = _snap_to_increment(Decimal(str(value)), tick_size_dec)
        return float(snapped) if snapped is not None else value

    price_val = _normalize_linear_price(price_val)

    body: Dict[str, object] = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Limit" if order_type == "limit" else "Market",
        "qty": str(qty_val),
        "orderLinkId": uuid4().hex,
    }
    if order_type == "limit":
        body["timeInForce"] = "GTC"
        body["price"] = str(price_val)
    else:
        body["timeInForce"] = "IOC"
    if category == "linear":
        body["positionIdx"] = _bybit_position_idx_for_order(
            side=side,
            configured_mode=os.getenv("BYBIT_POSITION_MODE", "one_way"),
        )

    take_profit_offset = _parse_offset_value(
        payload.get("take_profit_offset") or payload.get("tp_offset")
    )
    if take_profit_offset is None:
        take_profit_offset = _parse_trigger_offset(payload.get("take_profit_price"))
    tp_multiplier = _parse_offset_value(payload.get("tp_multiplier"))
    stop_loss_offset = None
    if category != "option":
        stop_loss_offset = _parse_offset_value(
            payload.get("stop_loss_offset") or payload.get("sl_offset")
        )
        if stop_loss_offset is None:
            stop_loss_offset = _parse_trigger_offset(payload.get("stop_loss_price"))

    take_profit = (
        None
        if take_profit_offset is not None
        else _parse_trigger_price(payload.get("take_profit_price"))
    )
    stop_loss = None
    if category != "option":
        stop_loss = (
            None
            if stop_loss_offset is not None
            else _parse_trigger_price(payload.get("stop_loss_price"))
        )
    take_profit = _normalize_linear_price(take_profit)
    stop_loss = _normalize_linear_price(stop_loss)
    if take_profit is not None:
        body["takeProfit"] = str(take_profit)
    if stop_loss is not None:
        body["stopLoss"] = str(stop_loss)
    if category == "linear" and ("takeProfit" in body or "stopLoss" in body):
        body["tpslMode"] = "Full"
        body["tpOrderType"] = "Market"
        body["slOrderType"] = "Market"
    if order_type == "limit" and category == "linear" and price_val is not None:
        if take_profit_offset is not None:
            tp_target = _normalize_linear_price(price_val + take_profit_offset)
            body["takeProfit"] = _format_decimal_value(tp_target)
        if stop_loss_offset is not None:
            sl_target = _normalize_linear_price(price_val + stop_loss_offset)
            body["stopLoss"] = _format_decimal_value(sl_target)
        if "takeProfit" in body or "stopLoss" in body:
            body["tpslMode"] = "Full"
            body["tpOrderType"] = "Market"
            body["slOrderType"] = "Market"

    planned_entry_price = _parse_trigger_price(
        payload.get("planned_entry_price") or payload.get("entry_price")
    )
    planned_stop_price = _parse_trigger_price(
        payload.get("planned_stop_price") or payload.get("stop_loss_price")
    )
    planned_target_price = _parse_trigger_price(
        payload.get("planned_target_price") or payload.get("take_profit_price")
    )
    planned_entry_price = _normalize_linear_price(planned_entry_price)
    planned_stop_price = _normalize_linear_price(planned_stop_price)
    planned_target_price = _normalize_linear_price(planned_target_price)
    _log_webhook_event(request_id, "order_request", {"payload": body})

    body_json = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, body_json)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{base_url}/v5/order/create", headers=headers, content=body_json
        )
    data: Dict[str, object] = {}
    response_status = int(getattr(response, "status_code", 200) or 200)
    try:
        response_json = response.json()
        if isinstance(response_json, dict):
            data = response_json
    except Exception:
        data = {}
    if response_status >= 400:
        raise BybitOrderRejected(
            ret_code=data.get("retCode"),
            ret_msg=data.get("retMsg") or response.text,
            ret_ext_info=data.get("retExtInfo"),
            result=data.get("result"),
            request_body=body,
            http_status=response_status,
            response_body=data,
        )
    _log_webhook_event(
        request_id,
        "order_response",
        {
            "retCode": data.get("retCode"),
            "retMsg": data.get("retMsg"),
            "result": data.get("result", {}),
        },
    )
    if data.get("retCode") not in (0, "0"):
        raise BybitOrderRejected(
            ret_code=data.get("retCode"),
            ret_msg=data.get("retMsg"),
            ret_ext_info=data.get("retExtInfo"),
            result=data.get("result"),
            request_body=body,
            http_status=response_status,
            response_body=data,
        )
    order_result = data.get("result", {}) or {}
    order_id = order_result.get("orderId")
    _upsert_trade_context(
        {
            "pending_webhook_id": str(payload.get("pending_webhook_id") or "").strip(),
            "broker": "bybit",
            "account": account,
            "category": category,
            "instrument": symbol,
            "side": side,
            "order_type": order_type,
            "entry_price": planned_entry_price if planned_entry_price is not None else price_val,
            "timeframe": payload.get("timeframe"),
            "is_test_trade": payload.get("is_test_trade"),
            "created_at": request_open_time_iso,
            "open_time": _epoch_or_iso_to_iso(payload.get("opened_at")) or request_open_time_iso,
            "opened_at": _epoch_or_iso_to_iso(payload.get("opened_at")) or request_open_time_iso,
            "order_id": str(order_id or "").strip(),
            "order_link_id": str(order_result.get("orderLinkId") or body.get("orderLinkId") or "").strip(),
            "status": "ACTIVE",
        }
    )
    if account == "demo" and category == "linear":
        cache_bybit_demo_tpsl_request(
            order_id=str(order_id or ""),
            order_link_id=str(body.get("orderLinkId") or ""),
            parent_order_link_id=None,
            symbol=symbol,
            side=side,
            take_profit=_parse_bybit_price_level(body.get("takeProfit")),
            stop_loss=_parse_bybit_price_level(body.get("stopLoss")),
            source="order_create_request",
        )
    tpsl_result: Optional[Dict[str, object]] = None
    tpsl_error: Optional[str] = None
    tp_order: Optional[Dict[str, object]] = None
    tp_error: Optional[str] = None
    if category == "linear" and any(
        item is not None
        for item in (
            take_profit_offset,
            stop_loss_offset,
            take_profit,
            stop_loss,
            planned_stop_price,
            planned_target_price,
        )
    ):
        try:
            position = await _wait_for_position_entry(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                category=category,
                symbol=symbol,
                request_id=request_id,
            )
            if position is None:
                raise ValueError("Position entry price not available yet.")
            entry_price = _parse_offset_value(
                position.get("avgPrice") or position.get("entryPrice")
            )
            if entry_price is None:
                raise ValueError("Position entry price could not be parsed.")
            position_idx = position.get("positionIdx")
            if position_idx is not None:
                try:
                    position_idx = int(position_idx)
                except (TypeError, ValueError):
                    position_idx = None
            _log_webhook_event(
                request_id,
                "position_context",
                {
                    "positionIdx": position_idx,
                    "position": {
                        "size": position.get("size"),
                        "avgPrice": position.get("avgPrice"),
                        "entryPrice": position.get("entryPrice"),
                        "positionIdx": position.get("positionIdx"),
                        "side": position.get("side"),
                    },
                },
            )
            if (
                level_anchor_mode == "planned_entry"
                and planned_stop_price is not None
                and planned_target_price is not None
            ):
                tp_target = planned_target_price
                sl_target = planned_stop_price
            else:
                tp_target = (
                    entry_price + take_profit_offset
                    if take_profit_offset is not None
                    else take_profit
                )
                sl_target = (
                    entry_price + stop_loss_offset
                    if stop_loss_offset is not None
                    else stop_loss
                )
            tp_target = _normalize_linear_price(tp_target)
            sl_target = _normalize_linear_price(sl_target)
            _log_webhook_event(
                request_id,
                "tpsl_computed",
                {
                    "level_anchor_mode": level_anchor_mode,
                    "planned_entry_price": planned_entry_price,
                    "planned_stop_price": planned_stop_price,
                    "planned_target_price": planned_target_price,
                    "entry_price": entry_price,
                    "take_profit_offset": take_profit_offset,
                    "stop_loss_offset": stop_loss_offset,
                    "take_profit": tp_target,
                    "stop_loss": sl_target,
                },
            )
            tick_size = _to_float(tick_size_dec)

            existing_tp = _normalize_linear_price(_parse_bybit_price_level(body.get("takeProfit")))
            existing_sl = _normalize_linear_price(_parse_bybit_price_level(body.get("stopLoss")))
            if _price_levels_match(existing_tp, tp_target, tick_size=tick_size) and _price_levels_match(existing_sl, sl_target, tick_size=tick_size):
                tpsl_result = {"status": "already_applied_on_order_create"}
            else:
                try:
                    tpsl_result = await _set_bybit_trading_stop(
                        base_url=base_url,
                        api_key=api_key,
                        api_secret=api_secret,
                        category=category,
                        symbol=symbol,
                        take_profit=tp_target,
                        stop_loss=sl_target,
                        position_idx=position_idx,
                        request_id=request_id,
                    )
                except Exception as exc:
                    msg = str(exc).lower()
                    if "not modified" in msg:
                        tpsl_result = {"status": "not_modified", "message": str(exc)}
                    else:
                        tpsl_error = str(exc)
            latest_position = position
            live_state_observed = False
            try:
                latest_positions = await _fetch_bybit_positions(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category=category,
                    symbol=symbol,
                    request_id=request_id,
                )
                if isinstance(latest_positions, list):
                    for candidate in latest_positions:
                        candidate_size = _to_float(candidate.get("size")) or 0.0
                        if abs(candidate_size) <= 0:
                            continue
                        latest_position = candidate
                        live_state_observed = True
                        break
            except Exception as exc:
                BYBIT_LOGGER.warning(
                    "WEBHOOK_TPSL %s post_submit_position_lookup_failed symbol=%s account=%s error=%s",
                    request_id,
                    symbol,
                    account,
                    exc,
                )
            live_tp, live_sl = _extract_position_tpsl_levels(latest_position)
            live_tp = _normalize_linear_price(live_tp)
            live_sl = _normalize_linear_price(live_sl)
            if live_tp is not None or live_sl is not None:
                live_state_observed = True
            live_matches = _price_levels_match(live_tp, tp_target, tick_size=tick_size) and _price_levels_match(
                live_sl,
                sl_target,
                tick_size=tick_size,
            )
            BYBIT_LOGGER.info(
                "WEBHOOK_TPSL %s verify symbol=%s account=%s intended_tp=%s intended_sl=%s live_tp=%s live_sl=%s result=%s tpsl_status=%s",
                request_id,
                symbol,
                account,
                tp_target,
                sl_target,
                live_tp,
                live_sl,
                "matched" if live_matches else "mismatch",
                (tpsl_result or {}).get("status"),
            )
            if live_matches:
                if tpsl_result is None:
                    tpsl_result = {"status": "live_state_matched"}
                tpsl_error = None
            elif (tpsl_result is not None) and not live_state_observed:
                # Trading-stop call succeeded but the follow-up read path was unavailable.
                # Preserve success to avoid false negatives from transient reads.
                tpsl_error = None
            elif not tpsl_error:
                tpsl_error = (
                    f"TP/SL live state mismatch (tp={live_tp}, sl={live_sl}, expected_tp={tp_target}, expected_sl={sl_target})"
                )
            if account == "demo" and tpsl_result is not None:
                cache_bybit_demo_tpsl_request(
                    order_id=str(order_id or ""),
                    order_link_id=str(body.get("orderLinkId") or ""),
                    parent_order_link_id=None,
                    symbol=symbol,
                    side=side,
                    take_profit=_parse_bybit_price_level(tp_target),
                    stop_loss=_parse_bybit_price_level(sl_target),
                    source="trading_stop_computed",
                )
            _upsert_trade_context(
                {
                    "pending_webhook_id": str(payload.get("pending_webhook_id") or "").strip(),
                    "broker": "bybit",
                    "account": account,
                    "category": category,
                    "instrument": symbol,
                    "side": side,
                    "order_type": order_type,
                    "entry_price": entry_price,
                    "stop_loss": sl_target,
                    "take_profit": tp_target,
                    "timeframe": payload.get("timeframe"),
                    "is_test_trade": payload.get("is_test_trade"),
                    "open_time": _epoch_or_iso_to_iso(payload.get("opened_at")) or request_open_time_iso,
                    "opened_at": _epoch_or_iso_to_iso(payload.get("opened_at")) or request_open_time_iso,
                    "order_id": str(order_id or "").strip(),
                    "order_link_id": str(order_result.get("orderLinkId") or body.get("orderLinkId") or "").strip(),
                    "status": "ACTIVE",
                }
            )
        except Exception as exc:
            tpsl_error = tpsl_error or str(exc)
            BYBIT_LOGGER.exception(
                "WEBHOOK_TPSL %s tpsl_failed symbol=%s account=%s error=%s",
                request_id,
                symbol,
                account,
                exc,
            )
    if tpsl_error:
        raise RuntimeError(
            f"Bybit order {order_id or ''} created but TP/SL application failed: {tpsl_error}"
        )
    if category == "option":
        try:
            position = await _wait_for_position_entry(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                category=category,
                symbol=symbol,
                request_id=request_id,
            )
            if position is None:
                raise ValueError("Position entry price not available yet.")
            entry_price = _parse_offset_value(
                position.get("avgPrice") or position.get("entryPrice")
            )
            if entry_price is None:
                raise ValueError("Position entry price could not be parsed.")
            tp_target = None
            if take_profit_offset is not None:
                tp_target = entry_price + take_profit_offset
            elif take_profit is not None:
                tp_target = take_profit
            elif tp_multiplier is not None and tp_multiplier > 0:
                offset = entry_price * (tp_multiplier - 1)
                tp_target = entry_price + offset if side == "Buy" else entry_price - offset
            if tp_target is None:
                raise ValueError("No TP offset/price provided for options trade.")
            if tp_target < 0:
                tp_target = 0
            exit_side = "Sell" if side == "Buy" else "Buy"
            tp_order = await _place_bybit_reduce_only_limit(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                category=category,
                symbol=symbol,
                side=exit_side,
                qty=qty_val,
                price=tp_target,
                request_id=request_id,
            )
        except Exception as exc:
            tp_error = str(exc)
            BYBIT_LOGGER.exception(
                "WEBHOOK_TPSL %s tp_limit_failed symbol=%s account=%s error=%s",
                request_id,
                symbol,
                account,
                exc,
            )

    if (
        order_type == "limit"
        and (limit_cancel_offset is not None or limit_cancel_pct is not None)
        and order_id
    ):
        asyncio.create_task(
            _monitor_bybit_limit_cancel(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                category=category,
                symbol=symbol,
                order_id=order_id,
                limit_price=price_val,
                limit_cancel_offset=limit_cancel_offset,
                limit_cancel_offset_pct=limit_cancel_pct,
                pending_webhook_id=pending_id or None,
            )
        )

    _invalidate_open_orders_cache()
    return {
        "account": account,
        "category": category,
        "symbol": symbol,
        "side": side,
        "quantity": qty_val,
        "key_source": key_source,
        "order": order_result,
        "tpsl": tpsl_result,
        "tpsl_error": tpsl_error,
        "tp_order": tp_order,
        "tp_error": tp_error,
        "level_anchor_mode": level_anchor_mode,
        "planned_entry_price": planned_entry_price,
        "planned_stop_price": planned_stop_price,
        "planned_target_price": planned_target_price,
    }


async def _place_oanda_order(
    payload: Dict[str, object], *, request_id: str
) -> Dict[str, object]:
    symbol = str(payload.get("symbol", "")).upper()
    action = str(payload.get("action", "")).lower()
    qty = payload.get("quantity")
    account = str(payload.get("account", "live")).lower()
    order_type_raw = payload.get("order_type") or payload.get("orderType") or "market"
    order_type = str(order_type_raw).lower().strip()

    _log_webhook_event(
        request_id,
        "oanda_payload_parsed",
        {
            "symbol": symbol,
            "action": action,
            "quantity": qty,
            "account": account,
            "order_type": order_type,
        },
    )

    if action not in {"buy", "sell"}:
        raise ValueError("OANDA payload must include action=buy|sell.")
    if not symbol:
        raise ValueError("OANDA payload must include a symbol.")
    if qty is None:
        raise ValueError("OANDA payload must include quantity.")
    try:
        qty_val = float(qty)
    except (TypeError, ValueError) as exc:
        raise ValueError("OANDA payload quantity must be numeric.") from exc
    if qty_val <= 0:
        raise ValueError("OANDA payload quantity must be greater than zero.")
    if account not in {"live", "demo"}:
        raise ValueError("OANDA payload account must be live or demo.")
    if order_type not in {"market", "limit"}:
        raise ValueError("OANDA payload order_type must be market or limit.")

    entry_price = None
    if order_type == "limit":
        entry_price = _parse_optional_float(
            payload.get("entry_price")
            or payload.get("price")
            or payload.get("limit_price"),
            "entry_price",
        )
        if entry_price is None or entry_price <= 0:
            raise ValueError("OANDA limit orders require a positive entry price.")

    sl_price = _parse_optional_float(
        payload.get("stop_loss_price_value")
        or payload.get("stop_loss_price")
        or payload.get("sl_price"),
        "stop_loss_price",
    )
    tp_price = _parse_optional_float(
        payload.get("take_profit_price_value")
        or payload.get("take_profit_price")
        or payload.get("tp_price"),
        "take_profit_price",
    )

    cfg = _get_oanda_config(account)
    meta = await _fetch_oanda_instrument_meta(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        symbol=symbol,
        mode=account,
    )
    display_precision = int(meta["displayPrecision"])
    units_precision = int(meta.get("tradeUnitsPrecision", 0))

    if units_precision <= 0 and not math.isclose(
        qty_val, float(int(qty_val)), rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError(
            f"OANDA instrument {symbol} requires whole-number units (tradeUnitsPrecision=0). "
            f"quantity={qty_val} is not valid."
        )

    signed_units = qty_val if action == "buy" else -qty_val
    order_payload: Dict[str, object] = {
        "type": "MARKET" if order_type == "market" else "LIMIT",
        "instrument": symbol,
        "units": _quantize_oanda_units(signed_units, units_precision),
        "timeInForce": "FOK" if order_type == "market" else "GTC",
        "positionFill": "DEFAULT",
    }
    if entry_price is not None:
        order_payload["price"] = _quantize_oanda_price(entry_price, display_precision)
    if sl_price is not None:
        order_payload["stopLossOnFill"] = {
            "price": _quantize_oanda_price(sl_price, display_precision)
        }
    if tp_price is not None:
        order_payload["takeProfitOnFill"] = {
            "price": _quantize_oanda_price(tp_price, display_precision)
        }
    BYBIT_LOGGER.info(
        "OANDA_ORDER_PRECISION symbol=%s displayPrecision=%s tradeUnitsPrecision=%s "
        "units=%s price=%s sl=%s tp=%s",
        symbol,
        display_precision,
        units_precision,
        order_payload.get("units"),
        order_payload.get("price"),
        (order_payload.get("stopLossOnFill") or {}).get("price"),
        (order_payload.get("takeProfitOnFill") or {}).get("price"),
    )

    url = f"{cfg['base_url'].rstrip('/')}/v3/accounts/{cfg['account_id']}/orders"
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    token_last4 = cfg["token"][-4:] if cfg.get("token") else None
    BYBIT_LOGGER.info(
        "OANDA order cfg mode=%s base=%s account_id=%s token_last4=%s",
        account,
        cfg["base_url"],
        cfg["account_id"],
        token_last4,
    )
    _log_webhook_event(
        request_id,
        "oanda_order_request",
        {"url": url, "order": order_payload},
    )
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, headers=headers, json={"order": order_payload})
    if response.status_code >= 400:
        BYBIT_LOGGER.error(
            "OANDA order failed status=%s response=%s",
            response.status_code,
            response.text,
        )
        raise ValueError(
            f"OANDA order failed ({response.status_code}): {response.text}"
        )
    result = response.json()
    if not isinstance(result, dict):
        result = {"raw": result}
    _log_webhook_event(
        request_id,
        "oanda_order_response",
        {"result": result},
    )
    order_id = _extract_oanda_order_id(result)
    fill_tx = result.get("orderFillTransaction")
    fill_tx_id = str(fill_tx.get("id") or "").strip() if isinstance(fill_tx, dict) else ""
    trade_id = None
    if isinstance(fill_tx, dict):
        trade_opened = fill_tx.get("tradeOpened")
        if isinstance(trade_opened, dict):
            trade_id = trade_opened.get("tradeID")
    limit_cancel_offset, limit_cancel_pct = _parse_limit_cancel_settings(payload)
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    post_submit_warnings: List[str] = []
    try:
        _upsert_trade_context(
            {
                "pending_webhook_id": pending_id or None,
                "broker": "oanda",
                "account": account,
                "category": "forex",
                "instrument": symbol,
                "side": action,
                "order_type": order_type,
                "entry_price": order_payload.get("price") or entry_price,
                "stop_loss": (order_payload.get("stopLossOnFill") or {}).get("price"),
                "take_profit": (order_payload.get("takeProfitOnFill") or {}).get("price"),
                "timeframe": payload.get("timeframe"),
                "is_test_trade": payload.get("is_test_trade"),
                "order_id": str(order_id or "").strip(),
                "trade_id": str(trade_id or "").strip(),
                "transaction_id": fill_tx_id or str(result.get("lastTransactionID") or "").strip(),
                "status": "ACTIVE",
            }
        )
        _schedule_dropbox_upload_state_backup()
        if not pending_id:
            # Backwards compatibility: older TradingView alerts may not include
            # pending_webhook_id. Infer the deterministic id used by
            # legacy calculator flow when track_pending=yes.
            safe_symbol = "".join(ch for ch in symbol if ch.isalnum() or ch in "_-")
            safe_side = "".join(ch for ch in action if ch.isalnum() or ch in "_-")
            safe_ot = "".join(ch for ch in order_type if ch.isalnum() or ch in "_-")
            pending_id = f"calc_oanda_{account}_{safe_symbol}_{safe_side}_{safe_ot}"
        if pending_id:
            # Once the webhook has fired, remove it immediately so it doesn't linger
            # in the Open Orders / Positions table.
            if _delete_pending_webhook(pending_id):
                _schedule_dropbox_upload_state_backup()
    except Exception:
        warning = "Order accepted by OANDA, but post-submit bookkeeping failed."
        post_submit_warnings.append(warning)
        BYBIT_LOGGER.exception(
            "OANDA post-submit bookkeeping failed request_id=%s order_id=%s",
            request_id,
            order_id,
        )

    try:
        if (
            order_type == "limit"
            and (limit_cancel_offset is not None or limit_cancel_pct is not None)
            and order_id
            and entry_price is not None
        ):
            asyncio.create_task(
                _monitor_oanda_limit_cancel(
                    cfg=cfg,
                    instrument=symbol,
                    order_id=order_id,
                    limit_price=entry_price,
                    limit_cancel_offset=limit_cancel_offset,
                    limit_cancel_offset_pct=limit_cancel_pct,
                    pending_webhook_id=pending_id or None,
                )
            )
    except Exception:
        warning = "Order accepted by OANDA, but limit-order monitor setup failed."
        post_submit_warnings.append(warning)
        BYBIT_LOGGER.exception(
            "OANDA limit monitor setup failed request_id=%s order_id=%s",
            request_id,
            order_id,
        )

    if post_submit_warnings:
        result["warnings"] = post_submit_warnings

    return result


def _extract_oanda_order_id(result: Dict[str, object]) -> Optional[str]:
    for key in (
        "orderCreateTransaction",
        "orderFillTransaction",
        "orderCancelTransaction",
    ):
        entry = result.get(key)
        if isinstance(entry, dict):
            order_id = entry.get("id")
            if order_id:
                return str(order_id)
    order = result.get("order")
    if isinstance(order, dict):
        order_id = order.get("id")
        if order_id:
            return str(order_id)
    return None


def _limit_cancel_triggered(
    *, current_price: float, limit_price: float, offset: Optional[float], pct: Optional[float]
) -> bool:
    distance = abs(current_price - limit_price)
    if offset is not None and distance >= offset:
        return True
    if pct is not None:
        pct_distance = limit_price * (pct / 100)
        if distance >= pct_distance:
            return True
    return False


async def _fetch_bybit_market_price(
    *, base_url: str, category: str, symbol: str
) -> float:
    url = f"{base_url}/v5/market/tickers"
    params = {"category": category, "symbol": symbol}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    ticker_list = (payload.get("result") or {}).get("list") or []
    if not ticker_list:
        raise ValueError("Bybit ticker data missing.")
    last_price = ticker_list[0].get("lastPrice")
    if last_price is None:
        raise ValueError("Bybit ticker missing lastPrice.")
    return float(last_price)


async def _is_bybit_order_open(
    *, base_url: str, api_key: str, api_secret: str, category: str, symbol: str, order_id: str
) -> bool:
    payload = await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/order/realtime",
        params={"category": category, "symbol": symbol, "orderId": order_id, "openOnly": "0"},
    )
    items = (payload.get("result") or {}).get("list") or []
    if not items:
        return False
    status = items[0].get("orderStatus")
    return _is_bybit_open_order(status)


async def _cancel_bybit_order(
    *, base_url: str, api_key: str, api_secret: str, category: str, symbol: str, order_id: str
) -> None:
    await _bybit_signed_post(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/order/cancel",
        body={"category": category, "symbol": symbol, "orderId": order_id},
    )


async def _close_bybit_position_market(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    side: str,
    qty: object,
    position_idx: Optional[int],
    order_link_id: Optional[str],
) -> Dict[str, object]:
    """Close a Bybit position by submitting a reduce-only market order."""
    side_norm = str(side or "").strip().lower()
    if side_norm in {"buy", "long"}:
        close_side = "Sell"
    elif side_norm in {"sell", "short"}:
        close_side = "Buy"
    else:
        raise ValueError(f"Unknown Bybit position side: {side}")

    category_norm = str(category or "linear").strip().lower()
    body: Dict[str, object] = {
        "category": category_norm,
        "symbol": symbol,
        "side": close_side,
        "orderType": "Market",
        "qty": str(qty),
        "reduceOnly": True,
    }
    if position_idx is not None:
        body["positionIdx"] = int(position_idx)

    if order_link_id:
        body["orderLinkId"] = str(order_link_id).strip()
    elif category_norm == "option":
        body["orderLinkId"] = f"close-{uuid4().hex[:26]}"

    return await _bybit_signed_post(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/order/create",
        body=body,
    )


async def _monitor_bybit_limit_cancel(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    symbol: str,
    order_id: str,
    limit_price: Optional[float],
    limit_cancel_offset: Optional[float],
    limit_cancel_offset_pct: Optional[float],
    pending_webhook_id: Optional[str],
) -> None:
    if limit_price is None:
        return
    while True:
        await asyncio.sleep(LIMIT_CANCEL_POLL_SECONDS)
        try:
            if not await _is_bybit_order_open(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                category=category,
                symbol=symbol,
                order_id=order_id,
            ):
                if pending_webhook_id:
                    _update_pending_webhook(
                        pending_webhook_id,
                        {"status": "CLOSED", "limit_cancel_reason": "filled"},
                    )
                    _mark_trade_context_closed_or_cancelled(
                        pending_webhook_id=pending_webhook_id,
                        order_id=order_id,
                        status="CLOSED",
                    )
                break
            current_price = await _fetch_bybit_market_price(
                base_url=base_url,
                category=category,
                symbol=symbol,
            )
            if _limit_cancel_triggered(
                current_price=current_price,
                limit_price=limit_price,
                offset=limit_cancel_offset,
                pct=limit_cancel_offset_pct,
            ):
                await _cancel_bybit_order(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category=category,
                    symbol=symbol,
                    order_id=order_id,
                )
                if pending_webhook_id:
                    _update_pending_webhook(
                        pending_webhook_id,
                        {"status": "CANCELLED", "limit_cancel_reason": "price_moved"},
                    )
                    _mark_trade_context_closed_or_cancelled(
                        pending_webhook_id=pending_webhook_id,
                        order_id=order_id,
                        status="CANCELLED",
                    )
                _schedule_dropbox_upload_state_backup()
                break
        except Exception as exc:  # pragma: no cover - background task
            BYBIT_LOGGER.error("Bybit limit cancel monitor error: %s", exc)
            break


async def _fetch_oanda_mid_price(
    *, cfg: Dict[str, str], instrument: str, mode: str
) -> float:
    token = cfg["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{cfg['base_url'].rstrip('/')}/v3/accounts/{cfg['account_id']}/pricing"
    params = {"instruments": instrument}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise ValueError(f"OANDA pricing failed ({resp.status_code}): {resp.text}")
    payload = resp.json()
    prices = payload.get("prices") or []
    if not prices:
        raise ValueError("OANDA pricing data missing.")
    price = prices[0]
    bids = price.get("bids") or []
    asks = price.get("asks") or []
    bid = float(bids[0]["price"]) if bids else None
    ask = float(asks[0]["price"]) if asks else None
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    if price.get("closeoutBid") is not None and price.get("closeoutAsk") is not None:
        return (float(price["closeoutBid"]) + float(price["closeoutAsk"])) / 2
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    raise ValueError("OANDA pricing missing bid/ask data.")


async def _fetch_oanda_mid_prices_batch(
    *, cfg: Dict[str, str], instruments: List[str]
) -> Dict[str, float]:
    unique = sorted({str(item or "").strip().upper() for item in instruments if str(item or "").strip()})
    if not unique:
        return {}
    token = cfg["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{cfg['base_url'].rstrip('/')}/v3/accounts/{cfg['account_id']}/pricing"
    params = {"instruments": ",".join(unique)}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise ValueError(f"OANDA pricing failed ({resp.status_code}): {resp.text}")
    payload = resp.json() or {}
    rows = payload.get("prices") or []
    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        instrument = str(row.get("instrument") or "").strip().upper()
        bids = row.get("bids") or []
        asks = row.get("asks") or []
        bid = float(bids[0]["price"]) if bids else None
        ask = float(asks[0]["price"]) if asks else None
        if bid is not None and ask is not None:
            out[instrument] = (bid + ask) / 2
        elif row.get("closeoutBid") is not None and row.get("closeoutAsk") is not None:
            out[instrument] = (float(row["closeoutBid"]) + float(row["closeoutAsk"])) / 2
    return out




async def _convert_aud_to_home_currency(amount_aud: Decimal, account_home_ccy: str, cfg: Dict[str, str]) -> Decimal:
    home_ccy = str(account_home_ccy or "").strip().upper()
    if amount_aud <= 0:
        return amount_aud
    if not home_ccy:
        raise ValueError("OANDA account home currency unavailable for AUD conversion.")
    if home_ccy == "AUD":
        return amount_aud

    direct_symbol = f"AUD_{home_ccy}"
    inverse_symbol = f"{home_ccy}_AUD"
    prices = await _fetch_oanda_mid_prices_batch(cfg=cfg, instruments=[direct_symbol, inverse_symbol])

    direct = Decimal(str(prices.get(direct_symbol) or "0"))
    if direct > 0:
        return amount_aud * direct

    inverse = Decimal(str(prices.get(inverse_symbol) or "0"))
    if inverse > 0:
        return amount_aud / inverse

    raise ValueError(f"Unable to resolve AUD->{home_ccy} conversion from OANDA pricing.")


async def _poll_pending_webhook_invalidations() -> None:
    while True:
        await asyncio.sleep(LIMIT_CANCEL_POLL_SECONDS)
        try:
            pending_items = _load_pending_webhooks()
            watch = [
                item for item in pending_items
                if isinstance(item, dict)
                and bool(item.get("enabled", True))
                and str(item.get("status") or "").strip().upper() == "WAITING"
                and _parse_pending_cancel_touch_price(item) is not None
                and str(item.get("cancel_if_touched_operator") or "").strip().lower() in {"lte", "gte"}
            ]
            if not watch:
                continue

            oanda_groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
            for item in watch:
                category = str(item.get("category") or "").strip().lower()
                instrument = str(item.get("instrument") or "").strip().upper()
                if category == "oanda" and instrument:
                    account = str(item.get("account") or "live").strip().lower()
                    oanda_groups[account].append(item)

            oanda_prices: Dict[Tuple[str, str], float] = {}
            for account, rows in oanda_groups.items():
                cfg = _get_oanda_config(account)
                symbols = [str(r.get("instrument") or "").strip().upper() for r in rows]
                batch = await _fetch_oanda_mid_prices_batch(cfg=cfg, instruments=symbols)
                for symbol, price in batch.items():
                    oanda_prices[(account, symbol)] = price

            for item in watch:
                pending_id = str(item.get("id") or "").strip()
                category = str(item.get("category") or "").strip().lower()
                instrument = str(item.get("instrument") or "").strip().upper()
                operator = str(item.get("cancel_if_touched_operator") or "").strip().lower()
                cancel_price = _parse_pending_cancel_touch_price(item)
                if not pending_id or cancel_price is None or not instrument:
                    continue
                current_price: Optional[float] = None
                if category == "oanda":
                    account = str(item.get("account") or "live").strip().lower()
                    current_price = oanda_prices.get((account, instrument))
                else:
                    monitor_category = str(
                        item.get("monitor_category") or item.get("trade_mode") or "linear"
                    ).strip().lower()
                    if monitor_category not in {"spot", "linear"}:
                        monitor_category = "linear"
                    current_price = await _fetch_bybit_market_price(
                        base_url=BYBIT_BASE,
                        category=monitor_category,
                        symbol=instrument,
                    )
                if current_price is None:
                    continue
                if _pending_cancel_touch_triggered(
                    current_price=current_price,
                    cancel_price=cancel_price,
                    operator=operator,
                ):
                    now_iso = _utc_now_iso()
                    _update_pending_webhook(
                        pending_id,
                        {
                            "status": "CANCELLED",
                            "enabled": False,
                            "cancel_reason": "cancel_price_touched",
                            "cancelled_at": now_iso,
                        },
                    )
                    _upsert_trade_context(
                        {
                            "pending_webhook_id": pending_id,
                            "status": "CANCELLED",
                            "cancel_reason": "cancel_price_touched",
                            "cancelled_at": now_iso,
                        }
                    )
                    _invalidate_open_orders_cache()
                    _schedule_dropbox_upload_state_backup()
        except Exception as exc:  # pragma: no cover - background task
            BYBIT_LOGGER.error("Pending webhook invalidation poller error: %s", exc)


async def _is_oanda_order_open(*, cfg: Dict[str, str], order_id: str, mode: str) -> bool:
    payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint="/accounts/{account_id}/pendingOrders",
        mode=mode,
    )
    orders = payload.get("orders") or []
    for order in orders:
        if str(order.get("id", "")).strip() == order_id:
            return True
    return False


async def _cancel_oanda_order(*, cfg: Dict[str, str], order_id: str, mode: str, account_id: Optional[str] = None) -> None:
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    target_account_id = str(account_id or cfg.get("account_id") or "").strip()
    if not target_account_id:
        raise ValueError("OANDA account_id is missing.")
    endpoint = f"/v3/accounts/{target_account_id}/orders/{order_id}/cancel"
    url = f"{cfg['base_url'].rstrip('/')}{endpoint}"
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.put(url, headers=headers)
    if resp.status_code >= 400:
        raise ValueError(f"OANDA cancel failed ({resp.status_code}): {resp.text}")


async def _close_oanda_trade(*, cfg: Dict[str, str], trade_id: str, mode: str, account_id: Optional[str] = None) -> None:
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    target_account_id = str(account_id or cfg.get("account_id") or "").strip()
    if not target_account_id:
        raise ValueError("OANDA account_id is missing.")
    endpoint = f"/v3/accounts/{target_account_id}/trades/{trade_id}/close"
    url = f"{cfg['base_url'].rstrip('/')}{endpoint}"
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.put(url, headers=headers, json={"units": "ALL"})
    if resp.status_code >= 400:
        raise ValueError(f"OANDA trade close failed ({resp.status_code}): {resp.text}")


async def _monitor_oanda_limit_cancel(
    *,
    cfg: Dict[str, str],
    instrument: str,
    order_id: str,
    limit_price: float,
    limit_cancel_offset: Optional[float],
    limit_cancel_offset_pct: Optional[float],
    pending_webhook_id: Optional[str],
) -> None:
    mode = cfg.get("mode", "live")
    while True:
        await asyncio.sleep(LIMIT_CANCEL_POLL_SECONDS)
        try:
            if not await _is_oanda_order_open(cfg=cfg, order_id=order_id, mode=mode):
                if pending_webhook_id:
                    _update_pending_webhook(
                        pending_webhook_id,
                        {"status": "CLOSED", "limit_cancel_reason": "filled"},
                    )
                    _mark_trade_context_closed_or_cancelled(
                        pending_webhook_id=pending_webhook_id,
                        order_id=order_id,
                        status="CLOSED",
                    )
                break
            current_price = await _fetch_oanda_mid_price(
                cfg=cfg, instrument=instrument, mode=mode
            )
            if _limit_cancel_triggered(
                current_price=current_price,
                limit_price=limit_price,
                offset=limit_cancel_offset,
                pct=limit_cancel_offset_pct,
            ):
                await _cancel_oanda_order(cfg=cfg, order_id=order_id, mode=mode)
                if pending_webhook_id:
                    _update_pending_webhook(
                        pending_webhook_id,
                        {"status": "CANCELLED", "limit_cancel_reason": "price_moved"},
                    )
                    _mark_trade_context_closed_or_cancelled(
                        pending_webhook_id=pending_webhook_id,
                        order_id=order_id,
                        status="CANCELLED",
                    )
                _schedule_dropbox_upload_state_backup()
                break
        except Exception as exc:  # pragma: no cover - background task
            BYBIT_LOGGER.error("OANDA limit cancel monitor error: %s", exc)
            break


async def _fetch_bybit_executions(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    start_time: int,
) -> List[Dict[str, object]]:
    payload = await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/execution/list",
        params={
            "category": category,
            "startTime": str(start_time),
            "limit": "50",
        },
    )
    return (payload.get("result") or {}).get("list", []) or []


async def _fetch_bybit_closed_pnl(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_time: int,
    end_time: int,
    cursor: Optional[str] = None,
) -> Dict[str, object]:
    params = {
        "category": "linear",
        "startTime": str(start_time),
        "endTime": str(end_time),
        "limit": "50",
    }
    if cursor:
        params["cursor"] = cursor
    return await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/position/closed-pnl",
        params=params,
    )


async def _fetch_bybit_transaction_log(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_time: int,
    end_time: int,
    cursor: Optional[str] = None,
) -> Dict[str, object]:
    params = {
        "accountType": "UNIFIED",
        "startTime": str(start_time),
        "endTime": str(end_time),
        "limit": "50",
    }
    if cursor:
        params["cursor"] = cursor
    return await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/account/transaction-log",
        params=params,
    )


async def _fetch_bybit_order_history(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    start_time: int,
    end_time: int,
    cursor: Optional[str] = None,
    settle_coin: Optional[str] = None,
    order_filter: Optional[str] = None,
) -> Dict[str, object]:
    params = {
        "category": category,
        "startTime": str(start_time),
        "endTime": str(end_time),
        "limit": "50",
    }
    if cursor:
        params["cursor"] = cursor
    if settle_coin:
        params["settleCoin"] = settle_coin
    if order_filter:
        params["orderFilter"] = order_filter
    return await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/order/history",
        params=params,
    )


async def _fetch_bybit_order_realtime(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    category: str,
    settle_coin: Optional[str] = None,
    order_filter: Optional[str] = None,
    open_only: int = 1,
) -> Dict[str, object]:
    params: Dict[str, str] = {
        "category": category,
        "openOnly": str(open_only),
        "limit": "50",
    }
    if settle_coin:
        params["settleCoin"] = settle_coin
    if order_filter:
        params["orderFilter"] = order_filter
    return await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/order/realtime",
        params=params,
    )


def _parse_bybit_price_level(value: object) -> Optional[float]:
    parsed = _to_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _score_bybit_tpsl_candidate(item: Dict[str, object]) -> Tuple[int, int, int, int, int, int]:
    stop_loss = int(_parse_bybit_price_level(item.get("stopLoss")) is not None)
    take_profit = int(_parse_bybit_price_level(item.get("takeProfit")) is not None)
    stop_order_type = str(item.get("stopOrderType") or "").strip().lower()
    tpsl_hint = int(stop_order_type in {"stoploss", "takeprofit", "partialstoploss", "partialtakeprofit"})
    has_trigger = int(_parse_bybit_price_level(item.get("triggerPrice")) is not None)
    has_parent = int(bool(str(item.get("parentOrderLinkId") or "").strip()))
    status = str(item.get("orderStatus") or "").strip().lower()
    closed_status = int(status in {"filled", "triggered", "deactivated", "cancelled", "partiallyfilledcanceled"})
    updated_time = int(_to_float(item.get("updatedTime")) or _to_float(item.get("createdTime")) or 0)
    return stop_loss + take_profit, tpsl_hint, has_trigger, has_parent, closed_status, updated_time


def _resolve_bybit_tpsl(
    *,
    entry: Dict[str, object],
    order_candidates: List[Dict[str, object]],
    order_match: Dict[str, object],
    linked_orders: List[Dict[str, object]],
    orders_by_link_id: Dict[str, List[Dict[str, object]]],
    orders_by_parent_link_id: Dict[str, List[Dict[str, object]]],
    fallback_candidates: List[Dict[str, object]],
    cache_entry: Optional[Dict[str, object]],
) -> Tuple[Optional[float], Optional[float], str, Dict[str, object]]:
    best_order = max(order_candidates, key=_score_bybit_tpsl_candidate) if order_candidates else order_match
    debug: Dict[str, object] = {
        "order_match_found": bool(order_match),
        "order_candidates_count": len(order_candidates),
        "linked_orders_count": len(linked_orders),
        "fallback_candidates_count": len(fallback_candidates),
    }

    def _is_tpsl_order(item: Dict[str, object]) -> bool:
        t = str(item.get("stopOrderType") or "").strip().lower()
        return t in {"stoploss", "takeprofit", "partialstoploss", "partialtakeprofit"}

    def _extract_from_children(children: List[Dict[str, object]], source: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        sl: Optional[float] = None
        tp: Optional[float] = None
        ordered_children = sorted(
            [item for item in children if isinstance(item, dict)],
            key=_score_bybit_tpsl_candidate,
            reverse=True,
        )
        for linked in ordered_children:
            trigger_price = _parse_bybit_price_level(linked.get("triggerPrice"))
            stop_order_type = str(linked.get("stopOrderType") or "").strip().lower()
            linked_sl = _parse_bybit_price_level(linked.get("stopLoss"))
            linked_tp = _parse_bybit_price_level(linked.get("takeProfit"))
            if stop_order_type in {"stoploss", "partialstoploss"}:
                sl = linked_sl or trigger_price or sl
            elif stop_order_type in {"takeprofit", "partialtakeprofit"}:
                tp = linked_tp or trigger_price or tp
            sl = sl or linked_sl
            tp = tp or linked_tp
        if sl is not None or tp is not None:
            return sl, tp, source
        return None, None, None

    stop_loss = _parse_bybit_price_level(best_order.get("stopLoss"))
    take_profit = _parse_bybit_price_level(best_order.get("takeProfit"))
    if stop_loss is not None or take_profit is not None:
        return stop_loss, take_profit, "parent_order", debug

    stop_loss, take_profit, source = _extract_from_children(linked_orders, "linked_parent_order")
    if source:
        return stop_loss, take_profit, source, debug

    order_link_id = str(entry.get("orderLinkId") or order_match.get("orderLinkId") or "").strip()
    parent_link_id = str(entry.get("parentOrderLinkId") or order_match.get("parentOrderLinkId") or "").strip()
    cross_linked: List[Dict[str, object]] = []
    for key in {order_link_id, parent_link_id}:
        if not key:
            continue
        linked = orders_by_link_id.get(key) or []
        if isinstance(linked, list):
            cross_linked.extend([item for item in linked if isinstance(item, dict)])
        cross_linked.extend(orders_by_parent_link_id.get(key, []))
    cross_linked = [item for item in cross_linked if isinstance(item, dict) and _is_tpsl_order(item)]
    debug["cross_linked_count"] = len(cross_linked)
    stop_loss, take_profit, source = _extract_from_children(cross_linked, "cross_linked_order")
    if source:
        return stop_loss, take_profit, source, debug

    symbol = str(entry.get("symbol") or "").strip().upper()
    side = str(entry.get("side") or "").strip().lower()
    expected_child_side = "sell" if side in {"buy", "long"} else "buy"
    close_ms = int(_to_float(entry.get("updatedTime")) or 0)
    heuristic_matches: List[Dict[str, object]] = []
    for candidate in fallback_candidates:
        if str(candidate.get("symbol") or "").strip().upper() != symbol:
            continue
        candidate_side = str(candidate.get("side") or "").strip().lower()
        if expected_child_side and candidate_side and candidate_side != expected_child_side:
            continue
        if not _is_tpsl_order(candidate):
            continue
        ts = int(_to_float(candidate.get("createdTime")) or _to_float(candidate.get("updatedTime")) or 0)
        if close_ms and ts and abs(ts - close_ms) > 30 * 60 * 1000:
            continue
        heuristic_matches.append(candidate)
    debug["heuristic_match_count"] = len(heuristic_matches)
    stop_loss, take_profit, source = _extract_from_children(heuristic_matches, "heuristic_fallback")
    if source:
        return stop_loss, take_profit, source, debug

    if isinstance(cache_entry, dict):
        stop_loss = _parse_bybit_price_level(cache_entry.get("stop_loss"))
        take_profit = _parse_bybit_price_level(cache_entry.get("take_profit"))
        if stop_loss is not None or take_profit is not None:
            return stop_loss, take_profit, f"cached_request:{cache_entry.get('source') or 'unknown'}", debug

    return None, None, "unresolved", debug


def _journal_id_for_bybit_demo_row(symbol: str, order_id: str) -> str:
    symbol_norm = str(symbol or "").strip().upper()
    order_norm = str(order_id or "").strip()
    return f"bybit:demo:closedpnl:{symbol_norm}:{order_norm}"


def _normalize_bybit_closed_pnl_row(
    entry: Dict[str, object],
    *,
    account_mode: str = "demo",
    balance_after_trade: Optional[float],
    display_side: Optional[str] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    raw_refs_extra: Optional[Dict[str, object]] = None,
    resolved_trade_context: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
    symbol = str(entry.get("symbol") or "").strip().upper()
    order_id = str(entry.get("orderId") or "").strip()
    if not symbol or not order_id:
        return None
    open_fee = _to_float(entry.get("openFee")) or 0.0
    close_fee = _to_float(entry.get("closeFee")) or 0.0
    fees = open_fee + close_fee
    fill_count = int(_to_float(entry.get("fillCount")) or 0)
    notes = "" if balance_after_trade is not None else "Balance unavailable from transaction log"
    raw_side = str(entry.get("side") or "").strip()
    side_value = str(display_side or raw_side).strip()
    raw_refs = {
        "orderId": order_id,
        "orderLinkId": str(entry.get("orderLinkId") or "").strip() or None,
        "parentOrderLinkId": str(entry.get("parentOrderLinkId") or "").strip() or None,
        "tradeId": str(entry.get("tradeId") or entry.get("execId") or "").strip() or None,
        "transactionId": str(entry.get("transactionId") or "").strip() or None,
        "fillCount": fill_count,
        "source": "closed_pnl",
        "raw_closed_pnl_side": raw_side or None,
    }
    if isinstance(raw_refs_extra, dict):
        raw_refs.update(raw_refs_extra)
    mode = "demo" if str(account_mode).strip().lower() == "demo" else "live"
    ctx = resolved_trade_context if isinstance(resolved_trade_context, dict) else None
    market_window_ctx_used = False
    if not isinstance(ctx, dict):
        ctx = _lookup_trade_context_for_journal_row(
            {
                "orderId": order_id,
                "orderLinkId": str(entry.get("orderLinkId") or "").strip(),
                "raw_refs": {"orderId": order_id, "orderLinkId": str(entry.get("orderLinkId") or "").strip()},
            }
        )
    timeframe = _normalize_timeframe(ctx.get("timeframe")) if isinstance(ctx, dict) else ""
    is_test_trade = _normalize_test_trade_flag(ctx.get("is_test_trade")) if isinstance(ctx, dict) else None
    fallback_attempted = isinstance(ctx, dict)
    fallback_stop_loss = _to_float(ctx.get("stop_loss")) if isinstance(ctx, dict) else None
    fallback_take_profit = _to_float(ctx.get("take_profit")) if isinstance(ctx, dict) else None
    if stop_loss is None and fallback_stop_loss is not None:
        stop_loss = fallback_stop_loss
    if take_profit is None and fallback_take_profit is not None:
        take_profit = fallback_take_profit
    raw_refs["trade_context_tpsl_fallback_attempted"] = fallback_attempted
    raw_refs["trade_context_tpsl_fallback_via_window"] = market_window_ctx_used
    raw_refs["trade_context_tpsl_fallback_matched"] = bool(
        (stop_loss is not None and fallback_stop_loss is not None)
        or (take_profit is not None and fallback_take_profit is not None)
    )
    close_time_iso = _ms_to_iso(entry.get("updatedTime"))
    close_ts = _canonical_trade_epoch_second(close_time_iso)
    ctx_valid = isinstance(ctx, dict)
    if ctx_valid:
        candidate_open = _epoch_or_iso_to_iso(ctx.get("open_time")) or _epoch_or_iso_to_iso(ctx.get("created_at"))
        if candidate_open and close_ts is not None:
            candidate_open_ts = _canonical_trade_epoch_second(candidate_open)
            if candidate_open_ts is not None and candidate_open_ts >= close_ts:
                ctx = None
                ctx_valid = False
    if isinstance(ctx, dict):
        candidate_open = _epoch_or_iso_to_iso(ctx.get("open_time")) or _epoch_or_iso_to_iso(ctx.get("created_at"))
        if candidate_open and close_ts is not None:
            candidate_open_ts = _canonical_trade_epoch_second(candidate_open)
            if candidate_open_ts is not None and candidate_open_ts >= close_ts:
                ctx = None
        resolved_open_time = _epoch_or_iso_to_iso(ctx.get("open_time")) or _epoch_or_iso_to_iso(ctx.get("created_at")) if isinstance(ctx, dict) else None
        _upsert_trade_context(
            {
                "broker": "bybit",
                "account": mode,
                "instrument": symbol,
                "side": side_value,
                "order_id": order_id,
                "order_link_id": raw_refs.get("orderLinkId"),
                "parent_order_link_id": raw_refs.get("parentOrderLinkId"),
                "trade_id": raw_refs.get("tradeId"),
                "transaction_id": raw_refs.get("transactionId"),
                "timeframe": timeframe or ctx.get("timeframe"),
                "is_test_trade": is_test_trade if is_test_trade is not None else ctx.get("is_test_trade"),
                "open_time": resolved_open_time,
                "stop_loss": stop_loss if stop_loss is not None else ctx.get("stop_loss"),
                "take_profit": take_profit if take_profit is not None else ctx.get("take_profit"),
                "status": "CLOSED",
            }
        )
    open_time = (
        _epoch_or_iso_to_iso(ctx.get("open_time")) if isinstance(ctx, dict) else None
    ) or (
        _epoch_or_iso_to_iso(ctx.get("created_at")) if isinstance(ctx, dict) else None
    )
    created_fallback = _ms_to_iso(entry.get("createdTime"))
    if (not open_time) and created_fallback and close_ts is not None:
        created_ts = _canonical_trade_epoch_second(created_fallback)
        if created_ts is not None and created_ts < close_ts:
            open_time = created_fallback
    open_ts = _canonical_trade_epoch_second(open_time)
    if close_ts is None or open_ts is None or close_ts <= open_ts:
        return {
            "id": f"bybit:{mode}:closedpnl:{symbol}:{order_id}:invalid-time",
            "source": "bybit",
            "account": mode,
            "account_label": "Bybit Demo" if mode == "demo" else "Bybit Live",
            "asset_class": "crypto",
            "symbol": symbol,
            "side": side_value.title(),
            "status": "invalid_time_order",
            "row_type": "quarantine",
            "open_time": open_time,
            "close_time": close_time_iso,
            "notes": "Bybit row quarantined: close_time must be after open_time",
            "raw_refs": raw_refs,
        }
    return {
        "id": f"bybit:{mode}:closedpnl:{symbol}:{order_id}",
        "source": "bybit",
        "account": mode,
        "account_label": "Bybit Demo" if mode == "demo" else "Bybit Live",
        "asset_class": "crypto",
        "symbol": symbol,
        "side": side_value.title(),
        "status": "closed",
        "open_time": open_time,
        "close_time": close_time_iso,
        "entry_price": _to_float(entry.get("avgEntryPrice")),
        "exit_price": _to_float(entry.get("avgExitPrice")),
        "qty": _to_float(entry.get("closedSize")),
        "qty_unit": "native",
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "commission": fees,
        "commission_currency": "USDT",
        "fees": fees,
        "fee_currency": "USDT",
        "realized_pnl": _to_float(entry.get("closedPnl")),
        "realized_pnl_currency": "USDT",
        "balance_after_trade": balance_after_trade,
        "notes": notes,
        "timeframe": timeframe,
        "is_test_trade": is_test_trade,
        "metrics": {k: v for k, v in {"timeframe": timeframe, "is_test_trade": is_test_trade}.items() if v not in ("", None)},
        "raw_refs": raw_refs,
    }


def _backfill_trade_row_context_fields(row: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(row, dict):
        return row
    current_timeframe = _normalize_timeframe(
        row.get("timeframe")
        or ((row.get("metrics") or {}).get("timeframe") if isinstance(row.get("metrics"), dict) else "")
    )
    needs_tpsl = row.get("stop_loss") in (None, "") or row.get("take_profit") in (None, "")
    current_is_test_trade = _normalize_test_trade_flag(
        row.get("is_test_trade")
        if isinstance(row, dict)
        else None
    )
    if current_timeframe and not needs_tpsl and current_is_test_trade is not None:
        return row

    ctx = _lookup_trade_context_for_journal_row(row)
    if not isinstance(ctx, dict):
        ctx = _lookup_trade_context_by_market_window(
            {
                "broker": row.get("source"),
                "account": row.get("account"),
                "instrument": row.get("symbol") or row.get("instrument"),
                "side": row.get("side"),
                "open_time": row.get("open_time") or row.get("opened_at") or row.get("entry_time"),
                "close_time": row.get("close_time") or row.get("closed_at") or row.get("exit_time") or row.get("date"),
            },
            include_inactive=True,
        )
    if not isinstance(ctx, dict):
        return row

    patched = dict(row)
    if not current_timeframe:
        timeframe = _normalize_timeframe(ctx.get("timeframe"))
        if timeframe:
            patched["timeframe"] = timeframe
            metrics = dict(patched.get("metrics") or {}) if isinstance(patched.get("metrics"), dict) else {}
            metrics["timeframe"] = timeframe
            patched["metrics"] = metrics

    if patched.get("stop_loss") in (None, "") and ctx.get("stop_loss") not in (None, ""):
        patched["stop_loss"] = _to_float(ctx.get("stop_loss")) if _to_float(ctx.get("stop_loss")) is not None else ctx.get("stop_loss")
    if patched.get("take_profit") in (None, "") and ctx.get("take_profit") not in (None, ""):
        patched["take_profit"] = _to_float(ctx.get("take_profit")) if _to_float(ctx.get("take_profit")) is not None else ctx.get("take_profit")
    if current_is_test_trade is None:
        ctx_test = _normalize_test_trade_flag(ctx.get("is_test_trade"))
        if ctx_test is not None:
            patched["is_test_trade"] = ctx_test
            metrics = dict(patched.get("metrics") or {}) if isinstance(patched.get("metrics"), dict) else {}
            metrics["is_test_trade"] = ctx_test
            patched["metrics"] = metrics
    return patched


def _normalize_side_for_comparison(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "long"}:
        return "buy"
    if text in {"sell", "short"}:
        return "sell"
    return ""


def _infer_side_from_tpsl_geometry(
    *,
    entry_price: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Optional[str]:
    if entry_price is None or stop_loss is None or take_profit is None:
        return None
    if stop_loss < entry_price < take_profit:
        return "Buy"
    if take_profit < entry_price < stop_loss:
        return "Sell"
    return None


def _infer_side_from_exit_and_pnl(
    *,
    entry_price: Optional[float],
    exit_price: Optional[float],
    realized_pnl: Optional[float],
) -> Optional[str]:
    if entry_price is None or exit_price is None or realized_pnl is None:
        return None
    move = exit_price - entry_price
    if move == 0 or realized_pnl == 0:
        return None
    if move > 0 and realized_pnl > 0:
        return "Buy"
    if move < 0 and realized_pnl > 0:
        return "Sell"
    if move > 0 and realized_pnl < 0:
        return "Sell"
    if move < 0 and realized_pnl < 0:
        return "Buy"
    return None


def _resolve_bybit_demo_display_side(
    *,
    entry: Dict[str, object],
    order_match: Dict[str, object],
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> tuple[Optional[str], str]:
    raw_side_norm = _normalize_side_for_comparison(entry.get("side"))
    entry_order_id = str(entry.get("orderId") or "").strip()
    entry_order_link_id = str(entry.get("orderLinkId") or "").strip()
    candidate_norm = _normalize_side_for_comparison(order_match.get("side"))
    if candidate_norm:
        is_reduce_only = bool(order_match.get("reduceOnly"))
        stop_order_type = str(order_match.get("stopOrderType") or "").strip().lower()
        candidate_order_id = str(order_match.get("orderId") or "").strip()
        candidate_link_id = str(order_match.get("orderLinkId") or "").strip()
        high_confidence = (
            (candidate_order_id and candidate_order_id == entry_order_id)
            or (candidate_link_id and entry_order_link_id and candidate_link_id == entry_order_link_id)
            or (not is_reduce_only and stop_order_type in {"", "unknown"})
        )
        if high_confidence:
            return candidate_norm.title(), "opening_order_match"

    geometry_side = _infer_side_from_tpsl_geometry(
        entry_price=_to_float(entry.get("avgEntryPrice")),
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    if geometry_side:
        return geometry_side, "tpsl_geometry"

    movement_side = _infer_side_from_exit_and_pnl(
        entry_price=_to_float(entry.get("avgEntryPrice")),
        exit_price=_to_float(entry.get("avgExitPrice")),
        realized_pnl=_to_float(entry.get("closedPnl")),
    )
    if movement_side:
        return movement_side, "price_move_vs_pnl"

    if raw_side_norm:
        return raw_side_norm.title(), "closed_pnl_raw"
    return None, "unresolved"


def _bybit_demo_workbook_row(row: Dict[str, object]) -> Dict[str, object]:
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    return {
        "opening_time": row.get("open_time"),
        "closing_time": row.get("close_time"),
        "type_buy_sell": row.get("side"),
        "symbol": row.get("symbol"),
        "size_quantity": row.get("qty"),
        "entry_price": row.get("entry_price"),
        "closing_price": row.get("exit_price"),
        "stop_loss": row.get("stop_loss"),
        "take_profit": row.get("take_profit"),
        "commission": row.get("commission"),
        "net_profit": row.get("realized_pnl"),
        "balance_after_trade": row.get("balance_after_trade"),
        "timeframe": _normalize_timeframe(
            row.get("timeframe")
            or ((row.get("metrics") or {}).get("timeframe") if isinstance(row.get("metrics"), dict) else "")
        ),
        "is_test_trade": _display_test_trade(row),
        "currency": row.get("realized_pnl_currency") or "USDT",
        "notes": row.get("notes") or "",
        "order_id": refs.get("orderId"),
        "fill_count": refs.get("fillCount"),
        "source": refs.get("source") or "closed_pnl",
    }


def _coerce_bybit_demo_workbook_frame(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    frame = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    frame = frame.reindex(columns=BYBIT_DEMO_WORKBOOK_COLUMNS).copy()
    for column in BYBIT_DEMO_WORKBOOK_TEXT_COLUMNS:
        if column not in frame.columns:
            continue
        frame[column] = frame[column].astype(object).where(~pd.isna(frame[column]), "")
    for column in BYBIT_DEMO_WORKBOOK_NUMERIC_COLUMNS:
        if column not in frame.columns:
            continue
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _sanitize_bybit_demo_workbook(active_folder: str) -> Dict[str, int]:
    workbook_path = _join_dropbox_path(active_folder, BYBIT_DEMO_WORKBOOK_NAME)
    try:
        payload = _dropbox_download_bytes(workbook_path)
        bio = io.BytesIO(payload)
        try:
            existing = pd.read_excel(bio, sheet_name=BYBIT_DEMO_WORKBOOK_SHEET)
        except Exception:
            existing = pd.DataFrame(columns=BYBIT_DEMO_WORKBOOK_COLUMNS)
        existing = _coerce_bybit_demo_workbook_frame(existing)
        if existing.empty:
            return {"changed": 0, "repaired_sides": 0, "deduped_by_order_id": 0, "deduped_by_fingerprint": 0}

        workbook_rows: List[Dict[str, object]] = []
        for _, wb_row in existing.iterrows():
            row = {
                "id": "",
                "source": "excel",
                "account": "Bybit Demo",
                "account_label": "Bybit Demo",
                "symbol": _excel_cell_to_python(wb_row.get("symbol")),
                "side": _excel_cell_to_python(wb_row.get("type_buy_sell")),
                "open_time": _excel_cell_to_python(wb_row.get("opening_time")),
                "close_time": _excel_cell_to_python(wb_row.get("closing_time")),
                "qty": _excel_cell_to_python(wb_row.get("size_quantity")),
                "entry_price": _excel_cell_to_python(wb_row.get("entry_price")),
                "exit_price": _excel_cell_to_python(wb_row.get("closing_price")),
                "stop_loss": _excel_cell_to_python(wb_row.get("stop_loss")),
                "take_profit": _excel_cell_to_python(wb_row.get("take_profit")),
                "commission": _excel_cell_to_python(wb_row.get("commission")),
                "fees": _excel_cell_to_python(wb_row.get("commission")),
                "realized_pnl": _excel_cell_to_python(wb_row.get("net_profit")),
                "net_profit": _excel_cell_to_python(wb_row.get("net_profit")),
                "balance_after_trade": _excel_cell_to_python(wb_row.get("balance_after_trade")),
                "timeframe": _normalize_timeframe(_excel_cell_to_python(wb_row.get("timeframe"))),
                "is_test_trade": _normalize_test_trade_flag(_excel_cell_to_python(wb_row.get("is_test_trade"))),
                "notes": _excel_cell_to_python(wb_row.get("notes")),
                "status": "closed",
                "raw_refs": {
                    "dropbox_path": workbook_path,
                    "orderId": _excel_cell_to_python(wb_row.get("order_id")),
                },
                "updated_at": _utc_now_iso(),
            }
            workbook_rows.append(row)

        sanitized_rows, stats = _sanitize_bybit_demo_rows(workbook_rows)
        if not int(stats.get("changed", 0)):
            return stats

        output_rows = [_bybit_demo_workbook_row(row) for row in sanitized_rows if _is_bybit_demo_trade_row(row)]
        output = pd.DataFrame(output_rows, columns=BYBIT_DEMO_WORKBOOK_COLUMNS)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            output.to_excel(writer, sheet_name=BYBIT_DEMO_WORKBOOK_SHEET, index=False)
        _dropbox_upload_bytes(workbook_path, buffer.getvalue())
        return stats
    except Exception as exc:
        _record_bybit_demo_sync_status(last_error=f"Bybit Demo workbook sanitize failed: {exc}")
        BYBIT_LOGGER.error("Bybit Demo workbook sanitize failed for %s: %s", workbook_path, exc)
        raise


def _append_bybit_demo_rows_to_workbook(active_folder: str, rows: List[Dict[str, object]]) -> int:
    if not rows:
        return 0
    workbook_path = _join_dropbox_path(active_folder, BYBIT_DEMO_WORKBOOK_NAME)
    with _BYBIT_DEMO_WORKBOOK_LOCK:
        payload = _dropbox_download_bytes(workbook_path)
        bio = io.BytesIO(payload)
        try:
            existing = pd.read_excel(bio, sheet_name=BYBIT_DEMO_WORKBOOK_SHEET)
        except Exception:
            existing = pd.DataFrame(columns=BYBIT_DEMO_WORKBOOK_COLUMNS)
        existing = _coerce_bybit_demo_workbook_frame(existing)


        def _sanitize_workbook_cell(column: str, value: object) -> object:
            if column in BYBIT_DEMO_WORKBOOK_NUMERIC_COLUMNS:
                if value is None:
                    return None
                if isinstance(value, str) and not value.strip():
                    return None
                return _to_float(value)
            if column in BYBIT_DEMO_WORKBOOK_TEXT_COLUMNS:
                if value is None:
                    return ""
                if isinstance(value, str):
                    return value
                return str(value)
            return value

        changed = 0
        order_ids = existing.get("order_id", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        order_index = {order_id: idx for idx, order_id in order_ids.items() if order_id}
        for row in rows:
            workbook_row = _bybit_demo_workbook_row(row)
            workbook_row = {
                column: _sanitize_workbook_cell(column, workbook_row.get(column))
                for column in BYBIT_DEMO_WORKBOOK_COLUMNS
            }
            order_id = str(workbook_row.get("order_id") or "").strip()
            if not order_id:
                continue
            if order_id in order_index:
                idx = order_index[order_id]
                for column, value in workbook_row.items():
                    try:
                        existing.at[idx, column] = value
                    except Exception as exc:
                        BYBIT_LOGGER.error(
                            "Bybit Demo workbook update failed column=%s value_type=%s order_id=%s err=%s",
                            column,
                            type(value).__name__,
                            order_id,
                            exc,
                        )
                        raise
                changed += 1
                continue
            existing = pd.concat([existing, pd.DataFrame([workbook_row], columns=BYBIT_DEMO_WORKBOOK_COLUMNS)], ignore_index=True)
            order_index[order_id] = len(existing) - 1
            changed += 1
        if not changed:
            return 0
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            existing.to_excel(writer, sheet_name=BYBIT_DEMO_WORKBOOK_SHEET, index=False)
        _dropbox_upload_bytes(workbook_path, buffer.getvalue())
        _sanitize_bybit_demo_workbook(active_folder)
        return changed


async def _sync_bybit_closed_pnl_window(
    *,
    account_mode: str,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_time: int,
    end_time: int,
) -> int:
    mode = "demo" if str(account_mode).strip().lower() == "demo" else "live"
    active_folder: Optional[str] = None
    if mode == "demo":
        active_folder, _entries = await asyncio.to_thread(_resolve_trading_journal_dropbox_folder)
        await asyncio.to_thread(_ensure_bybit_demo_dropbox_files, active_folder)

    orders_by_id: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    orders_by_link_id: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    orders_by_parent_link_id: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    fallback_order_buckets: Dict[str, List[Dict[str, object]]] = defaultdict(list)

    def _register_order(item: Dict[str, object]) -> None:
        if not isinstance(item, dict):
            return
        order_id = str(item.get("orderId") or "").strip()
        order_link_id = str(item.get("orderLinkId") or "").strip()
        parent_link_id = str(item.get("parentOrderLinkId") or "").strip()
        symbol = str(item.get("symbol") or "").strip().upper()
        side = str(item.get("side") or "").strip().lower()
        bucket_ts = int(_to_float(item.get("createdTime")) or _to_float(item.get("updatedTime")) or 0)
        bucket = str(bucket_ts // 60000) if bucket_ts else "na"
        stop_order_type = str(item.get("stopOrderType") or "").strip().lower()
        key = f"{symbol}|{side}|{bucket}|{stop_order_type}"
        fallback_order_buckets[key].append(item)
        if order_id:
            orders_by_id[order_id].append(item)
        if order_link_id:
            orders_by_link_id[order_link_id].append(item)
        if parent_link_id:
            orders_by_parent_link_id[parent_link_id].append(item)

    def _candidate_buckets(symbol: str, opposite_side: str, close_ms: int) -> List[Dict[str, object]]:
        symbol_norm = str(symbol or "").strip().upper()
        bucket = str(close_ms // 60000) if close_ms else "na"
        results: List[Dict[str, object]] = []
        for bucket_key in {bucket, str(int(bucket) - 1) if bucket.isdigit() else bucket, str(int(bucket) + 1) if bucket.isdigit() else bucket}:
            prefix = f"{symbol_norm}|{opposite_side}|{bucket_key}|"
            for key, values in fallback_order_buckets.items():
                if key.startswith(prefix):
                    results.extend(values)
        return results

    for settle_coin in ("USDT", "USDC"):
        for order_filter in (None, "StopOrder", "BidirectionalTpslOrder"):
            order_cursor: Optional[str] = None
            while True:
                order_payload = await _fetch_bybit_order_history(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category="linear",
                    start_time=start_time,
                    end_time=end_time,
                    cursor=order_cursor,
                    settle_coin=settle_coin,
                    order_filter=order_filter,
                )
                order_result = order_payload.get("result") or {}
                for item in order_result.get("list") or []:
                    _register_order(item)
                order_cursor = str(order_result.get("nextPageCursor") or "").strip() or None
                if not order_cursor:
                    break
            try:
                realtime_payload = await _fetch_bybit_order_realtime(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category="linear",
                    settle_coin=settle_coin,
                    order_filter=order_filter,
                    open_only=1,
                )
                realtime_result = realtime_payload.get("result") or {}
                for item in realtime_result.get("list") or []:
                    _register_order(item)
            except Exception as exc:
                BYBIT_LOGGER.warning(
                    "BYBIT_DEMO_TPSL realtime_fetch_failed settle_coin=%s order_filter=%s err=%s",
                    settle_coin,
                    order_filter or "None",
                    exc,
                )

    tx_by_order: Dict[str, Dict[str, object]] = {}
    tx_cursor: Optional[str] = None
    while True:
        tx_payload = await _fetch_bybit_transaction_log(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start_time,
            end_time=end_time,
            cursor=tx_cursor,
        )
        tx_result = (tx_payload.get("result") or {})
        for item in tx_result.get("list") or []:
            if not isinstance(item, dict):
                continue
            order_id = str(item.get("orderId") or "").strip()
            if order_id and order_id not in tx_by_order:
                tx_by_order[order_id] = item
        tx_cursor = str(tx_result.get("nextPageCursor") or "").strip() or None
        if not tx_cursor:
            break

    rows: List[Dict[str, object]] = []
    tpsl_cache = load_bybit_demo_tpsl_cache() if mode == "demo" else {}
    cursor: Optional[str] = None
    max_seen = start_time
    while True:
        payload = await _fetch_bybit_closed_pnl(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start_time,
            end_time=end_time,
            cursor=cursor,
        )
        result = (payload.get("result") or {})
        for entry in result.get("list") or []:
            if not isinstance(entry, dict):
                continue
            updated_ms = int(_to_float(entry.get("updatedTime")) or 0)
            if updated_ms < start_time:
                continue
            max_seen = max(max_seen, updated_ms)
            order_id = str(entry.get("orderId") or "").strip()
            order_link_id = str(entry.get("orderLinkId") or "").strip()
            tx_match = tx_by_order.get(order_id, {})
            order_candidates = list(orders_by_id.get(order_id, []))
            if order_link_id:
                order_candidates.extend(orders_by_link_id.get(order_link_id, []))
            order_match = max(order_candidates, key=_score_bybit_tpsl_candidate) if order_candidates else {}
            parent_link_id = str(
                order_match.get("parentOrderLinkId")
                or order_match.get("orderLinkId")
                or order_link_id
            ).strip()
            linked_orders = orders_by_parent_link_id.get(parent_link_id, [])
            entry_side = str(entry.get("side") or "").strip().lower()
            opposite_side = "sell" if entry_side in {"buy", "long"} else "buy"
            fallback_candidates = _candidate_buckets(
                str(entry.get("symbol") or ""),
                opposite_side,
                updated_ms,
            )
            cache_entry: Optional[Dict[str, object]] = None
            cache_match_type = "none"
            if mode == "demo":
                cache_entry, cache_match_type = resolve_cached_bybit_demo_tpsl(
                    cache=tpsl_cache,
                    order_id=order_id,
                    order_link_id=order_link_id,
                    parent_order_link_id=parent_link_id,
                    symbol=str(entry.get("symbol") or "").strip().upper(),
                    side=str(entry.get("side") or "").strip().title(),
                    open_time_ms=int(_to_float(entry.get("createdTime")) or 0) or None,
                    close_time_ms=updated_ms or None,
                )
            stop_loss, take_profit, tpsl_source_raw, tpsl_debug = _resolve_bybit_tpsl(
                entry=entry,
                order_candidates=order_candidates,
                order_match=order_match,
                linked_orders=linked_orders,
                orders_by_link_id=orders_by_link_id,
                orders_by_parent_link_id=orders_by_parent_link_id,
                fallback_candidates=fallback_candidates,
                cache_entry=cache_entry,
            )
            tpsl_source = tpsl_source_raw
            if tpsl_source_raw.startswith("cached_request:"):
                tpsl_source = f"{tpsl_source_raw}:{cache_match_type}"
            cache_hit = cache_entry is not None
            balance_after_trade = _to_float(tx_match.get("cashBalance"))
            resolved_side, side_source = _resolve_bybit_demo_display_side(
                entry=entry,
                order_match=order_match,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            resolved_ctx = _resolve_bybit_closed_pnl_trade_context(
                account_mode=mode,
                symbol=str(entry.get("symbol") or "").strip().upper(),
                side=resolved_side,
                order_id=order_id,
                order_link_id=order_link_id,
                parent_order_link_id=parent_link_id,
                trade_id=str(entry.get("tradeId") or entry.get("execId") or "").strip(),
                transaction_id=str(entry.get("transactionId") or "").strip(),
                close_time=_ms_to_iso(entry.get("updatedTime")),
            )
            resolved_open_time = (
                _epoch_or_iso_to_iso(resolved_ctx.get("open_time")) if isinstance(resolved_ctx, dict) else None
            ) or (
                _epoch_or_iso_to_iso(resolved_ctx.get("created_at")) if isinstance(resolved_ctx, dict) else None
            )
            row = _normalize_bybit_closed_pnl_row(
                entry,
                account_mode=mode,
                balance_after_trade=balance_after_trade,
                display_side=resolved_side,
                stop_loss=stop_loss,
                take_profit=take_profit,
                resolved_trade_context=resolved_ctx,
                raw_refs_extra={
                    "orderLinkId": order_link_id or order_match.get("orderLinkId"),
                    "parentOrderLinkId": order_match.get("parentOrderLinkId"),
                    "tpsl_source": tpsl_source,
                    "side_source": side_source,
                    "tpsl_unresolved_context": {
                        "order_id": order_id,
                        "order_link_id": order_link_id,
                        "parent_order_link_id": parent_link_id,
                        **tpsl_debug,
                        "cache_hit": cache_hit,
                        "cache_match_type": cache_match_type,
                    },
                },
            )
            if row:
                refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
                unresolved_key = _stable_registry_key(
                    [
                        mode,
                        order_id,
                        order_link_id,
                        parent_link_id,
                        str(entry.get("symbol") or "").strip().upper(),
                        str(order_match.get("stopOrderType") or entry.get("stopOrderType") or "").strip(),
                    ]
                )
                if tpsl_source != "unresolved":
                    _update_unresolved_registry(
                        family="bybit_demo_tpsl",
                        key=unresolved_key,
                        details={"status": "resolved"},
                        resolved=True,
                        resolution_source=tpsl_source,
                    )
                else:
                    should_warn, _ = _update_unresolved_registry(
                        family="bybit_demo_tpsl",
                        key=unresolved_key,
                        details={
                            "cache_hit": cache_hit,
                            "cache_match_type": cache_match_type,
                            "context_fallback_attempted": bool(refs.get("trade_context_tpsl_fallback_attempted")),
                            "context_fallback_matched": bool(refs.get("trade_context_tpsl_fallback_matched")),
                        },
                        resolved=False,
                    )
                    if _STARTUP_STATE_RESTORE_DONE.is_set() and should_warn:
                        BYBIT_LOGGER.warning(
                            "BYBIT_DEMO_TPSL unresolved order_id=%s order_link_id=%s parent_order_link_id=%s stop_order_type=%s cache_hit=%s cache_match_type=%s context_fallback_attempted=%s context_fallback_matched=%s",
                            order_id,
                            order_link_id,
                            parent_link_id,
                            str(order_match.get("stopOrderType") or entry.get("stopOrderType") or ""),
                            cache_hit,
                            cache_match_type,
                            bool(refs.get("trade_context_tpsl_fallback_attempted")),
                            bool(refs.get("trade_context_tpsl_fallback_matched")),
                        )
                _upsert_trade_context(
                    {
                        "broker": "bybit",
                        "account": mode,
                        "instrument": str(entry.get("symbol") or "").strip().upper(),
                        "side": resolved_side,
                        "order_id": order_id,
                        "order_link_id": order_link_id or order_match.get("orderLinkId"),
                        "parent_order_link_id": parent_link_id,
                        "trade_id": str(entry.get("tradeId") or entry.get("execId") or "").strip() or None,
                        "transaction_id": str(entry.get("transactionId") or "").strip() or None,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "open_time": resolved_open_time,
                        "timeframe": row.get("timeframe"),
                        "status": "CLOSED",
                    }
                )
                rows.append(row)
        cursor = str(result.get("nextPageCursor") or "").strip() or None
        if not cursor:
            break

    if not rows:
        _record_bybit_demo_sync_status(last_checked_at=_utc_now_iso(), last_error=None)
        return max_seen

    changed = _upsert_trading_journal_rows(rows)
    sanitize_stats: Dict[str, int] = {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0}
    workbook_stats: Dict[str, int] = {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0}
    if mode == "demo":
        sanitized_rows, sanitize_stats = _sanitize_bybit_demo_rows(_get_trading_journal_rows())
        if int(sanitize_stats.get("changed", 0)):
            _set_trading_journal_rows(sanitized_rows)
        if active_folder:
            await asyncio.to_thread(_append_bybit_demo_rows_to_workbook, active_folder, rows)
            workbook_stats = await asyncio.to_thread(_sanitize_bybit_demo_workbook, active_folder)
    _schedule_dropbox_upload_state_backup()
    _record_bybit_demo_sync_status(
        last_checked_at=_utc_now_iso(),
        last_success_at=_utc_now_iso(),
        last_error=None,
        last_rows_seen=len(rows),
        last_rows_upserted=changed,
        last_rows_deduped=int(sanitize_stats.get("deduped_by_order_id", 0))
        + int(sanitize_stats.get("deduped_by_fingerprint", 0)),
        last_workbook_rows_deduped=int(workbook_stats.get("deduped_by_order_id", 0))
        + int(workbook_stats.get("deduped_by_fingerprint", 0)),
    )
    return max_seen


async def _poll_bybit_fills() -> None:
    lookback_seconds = int(os.getenv("BYBIT_EXEC_LOOKBACK_SECONDS", "60"))
    categories = ["linear", "spot", "option", "inverse"]
    while True:
        await asyncio.sleep(FILL_ALERT_POLL_SECONDS)
        for account in ("live", "demo"):
            try:
                _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(
                    "demo" if account == "demo" else "live"
                )
                if not api_key or not api_secret:
                    continue
                last_seen = _BYBIT_EXEC_LAST_SEEN.get(account)
                if last_seen is None:
                    last_seen = int((time.time() - lookback_seconds) * 1000)
                max_seen = last_seen
                for category in categories:
                    try:
                        executions = await _fetch_bybit_executions(
                            base_url=base_url,
                            api_key=api_key,
                            api_secret=api_secret,
                            category=category,
                            start_time=last_seen,
                        )
                    except Exception:
                        continue
                    for entry in executions:
                        exec_time = int(entry.get("execTime") or 0)
                        if exec_time <= last_seen:
                            continue
                        max_seen = max(max_seen, exec_time)
                        entry_payload = {
                            **entry,
                            "account": account,
                            "category": category,
                        }
                        await _send_telegram_alert(_format_bybit_fill_alert(entry_payload))
                _BYBIT_EXEC_LAST_SEEN[account] = max_seen
            except Exception as exc:  # pragma: no cover - background task
                BYBIT_LOGGER.error("Bybit fill poll error: %s", exc)


async def _poll_bybit_demo_closed_pnl() -> None:
    while True:
        try:
            await _run_bybit_closed_pnl_sync(account_mode="demo", reason="scheduled")
        except Exception as exc:  # pragma: no cover - background task
            _record_bybit_demo_sync_status(last_checked_at=_utc_now_iso(), last_error=str(exc))
            BYBIT_LOGGER.exception("Bybit demo closed PnL poll error: %s", exc)
        await asyncio.sleep(BYBIT_DEMO_CLOSED_PNL_POLL_SECONDS)


async def _run_bybit_closed_pnl_sync(
    *,
    account_mode: str = "demo",
    reason: str,
    enforce_manual_cooldown: bool = False,
) -> Dict[str, object]:
    mode = "demo" if str(account_mode).strip().lower() == "demo" else "live"
    lookback_seconds = int(os.getenv(f"BYBIT_{mode.upper()}_CLOSED_PNL_LOOKBACK_SECONDS", "900"))
    backfill_seconds = int(os.getenv(f"BYBIT_{mode.upper()}_CLOSED_PNL_BACKFILL_SECONDS", "3600"))
    now = time.time()
    if (
        enforce_manual_cooldown
        and _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0) > 0
        and (now - _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0)) < _BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS
    ):
        retry_after = _BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS - (
            now - _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0)
        )
        return {
            "ok": False,
            "message": (
                "Bybit demo sync is cooling down. "
                f"Retry in {max(1, int(math.ceil(retry_after)))}s."
            ),
            "cooldown_active": True,
            "retry_after_seconds": max(0, retry_after),
        }

    async with _BYBIT_CLOSED_PNL_SYNC_LOCK[mode]:
        now = time.time()
        if (
            enforce_manual_cooldown
            and _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0) > 0
            and (now - _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0)) < _BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS
        ):
            retry_after = _BYBIT_DEMO_SYNC_MANUAL_COOLDOWN_SECONDS - (
                now - _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT.get(mode, 0.0)
            )
            return {
                "ok": False,
                "message": (
                    "Bybit demo sync is cooling down. "
                    f"Retry in {max(1, int(math.ceil(retry_after)))}s."
                ),
                "cooldown_active": True,
                "retry_after_seconds": max(0, retry_after),
            }

        _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(mode)
        if not api_key or not api_secret:
            raise ValueError(
                f"Bybit {mode} API credentials are not configured. ({_env_source_hint()})"
            )

        now_ms = int(time.time() * 1000)
        last_seen = _BYBIT_CLOSED_PNL_LAST_SEEN.get(mode)
        earliest = now_ms - _BYBIT_CLOSED_PNL_RECOVERY_WINDOW_MS + _BYBIT_CLOSED_PNL_RECOVERY_SAFETY_MARGIN_MS
        force_demo_recovery = (
            mode == "demo"
            and (reason in {"manual", "startup_recovery"} or last_seen is None)
        )
        if force_demo_recovery:
            start_time = max(0, earliest)
        else:
            if last_seen is None:
                last_seen = max(0, now_ms - (lookback_seconds * 1000))
            start_time = max(last_seen - (backfill_seconds * 1000), earliest)
        end_time = now_ms
        max_seen = await _sync_bybit_closed_pnl_window(
            account_mode=mode,
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start_time,
            end_time=end_time,
        )
        previous_seen = int(last_seen or 0)
        _BYBIT_CLOSED_PNL_LAST_SEEN[mode] = max(max_seen, previous_seen)
        _persist_bybit_closed_pnl_last_seen()
        _BYBIT_CLOSED_PNL_SYNC_LAST_RUN_AT[mode] = time.time()

    return {
        "ok": True,
        "account_mode": mode,
        "reason": reason,
        "message": f"Bybit {mode} sync completed.",
        "start_time": start_time,
        "end_time": end_time,
        "max_seen": max_seen,
    }


async def _fetch_oanda_last_transaction_id(cfg: Dict[str, str]) -> str:
    payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint="/accounts/{account_id}/summary",
        mode=cfg["mode"],
        timeout_s=4.0,
    )
    last_id = str(payload.get("lastTransactionID") or "").strip()
    if not last_id:
        raise ValueError(f"OANDA summary missing lastTransactionID for {cfg['mode']}")
    return last_id


async def _fetch_oanda_transactions(
    *,
    cfg: Dict[str, str],
    since_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    tx_type: str = "ORDER_FILL",
) -> tuple[List[Dict[str, object]], Optional[str]]:
    if since_id:
        endpoint = f"/accounts/{{account_id}}/transactions/sinceid?id={since_id}"
        if tx_type:
            endpoint = f"{endpoint}&type={tx_type}"
        payload = await _fetch_oanda_json(
            base_url=cfg["base_url"],
            account_id=cfg["account_id"],
            api_key=cfg["token"],
            endpoint=endpoint,
            mode=cfg["mode"],
        )
        return (
            payload.get("transactions") or [],
            str(payload.get("lastTransactionID") or "").strip() or None,
        )

    if not start_time or not end_time:
        raise ValueError("start_time and end_time are required when since_id is not provided.")
    endpoint = (
        "/accounts/{account_id}/transactions"
        f"?from={quote(start_time, safe='')}"
        f"&to={quote(end_time, safe='')}"
        + (f"&type={quote(tx_type, safe='')}" if tx_type else "")
    )
    payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint=endpoint,
        mode=cfg["mode"],
    )
    transactions: List[Dict[str, object]] = []
    seen_ids: set[str] = set()

    def append_items(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            tx_id = str(item.get("id") or "").strip()
            if tx_id and tx_id in seen_ids:
                continue
            if tx_id:
                seen_ids.add(tx_id)
            transactions.append(item)

    append_items(payload.get("transactions"))
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page_url in pages:
            page_endpoint = _oanda_endpoint_from_page_url(str(page_url), cfg["account_id"])
            if not page_endpoint:
                continue
            page_payload = await _fetch_oanda_json(
                base_url=cfg["base_url"],
                account_id=cfg["account_id"],
                api_key=cfg["token"],
                endpoint=page_endpoint,
                mode=cfg["mode"],
            )
            append_items(page_payload.get("transactions"))
    return (transactions, str(payload.get("lastTransactionID") or "").strip() or None)


def _oanda_endpoint_from_page_url(page_url: str, account_id: str) -> Optional[str]:
    try:
        parsed = urlparse(page_url)
    except Exception:
        return None
    path = parsed.path or ""
    marker = f"/v3/accounts/{account_id}"
    idx = path.find(marker)
    if idx >= 0:
        endpoint = path[idx + len("/v3") :]
    else:
        endpoint = path
    if not endpoint.startswith("/accounts/"):
        return None
    return f"{endpoint}?{parsed.query}" if parsed.query else endpoint


def _third_last_weekday(year: int, month: int, tz_name: str = "Australia/Brisbane") -> datetime:
    zone = ZoneInfo(tz_name)
    _, days_in_month = calendar.monthrange(year, month)
    weekdays: List[int] = []
    for day in range(1, days_in_month + 1):
        dt = datetime(year, month, day)
        if dt.weekday() < 5:
            weekdays.append(day)
    if len(weekdays) < 3:
        raise ValueError(f"Unable to determine third-last weekday for {year}-{month:02d}")
    return datetime(year, month, weekdays[-3], tzinfo=zone).astimezone(timezone.utc)


def _fee_charge_date_on_or_after(
    threshold_dt: datetime, tz_name: str = "Australia/Brisbane"
) -> datetime:
    zone = ZoneInfo(tz_name)
    current = threshold_dt.astimezone(zone)
    while True:
        candidate_local_utc = _third_last_weekday(current.year, current.month, tz_name=tz_name)
        candidate_local = candidate_local_utc.astimezone(zone)
        if candidate_local >= current:
            return candidate_local.astimezone(timezone.utc)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)


async def _fetch_oanda_last_live_fill_time(cfg: Dict[str, str]) -> Optional[datetime]:
    account_created = await _fetch_oanda_account_created_time(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
    )
    window = timedelta(days=120)
    end_dt = datetime.now(timezone.utc)
    cursor_end = end_dt

    def _endpoint_from_page_url(page_url: str, account_id: str) -> Optional[str]:
        try:
            parsed = urlparse(page_url)
        except Exception:
            return None
        path = parsed.path or ""
        marker = f"/v3/accounts/{account_id}"
        idx = path.find(marker)
        if idx >= 0:
            endpoint = path[idx + len("/v3") :]
        else:
            endpoint = path
        if not endpoint.startswith("/accounts/"):
            return None
        return f"{endpoint}?{parsed.query}" if parsed.query else endpoint

    while cursor_end > account_created:
        cursor_start = max(account_created, cursor_end - window)
        endpoint = (
            "/accounts/{account_id}/transactions"
            f"?from={quote(_format_oanda_timestamp(cursor_start), safe='')}"
            f"&to={quote(_format_oanda_timestamp(cursor_end), safe='')}"
            "&type=ORDER_FILL"
        )
        payload = await _fetch_oanda_json(
            base_url=cfg["base_url"],
            account_id=cfg["account_id"],
            api_key=cfg["token"],
            endpoint=endpoint,
            mode=cfg["mode"],
            timeout_s=8.0,
        )
        page_urls = payload.get("pages") if isinstance(payload.get("pages"), list) else []
        transactions: List[Dict[str, object]] = []
        for page_url in reversed(page_urls):
            page_endpoint = _endpoint_from_page_url(str(page_url), cfg["account_id"])
            if not page_endpoint:
                continue
            page_payload = await _fetch_oanda_json(
                base_url=cfg["base_url"],
                account_id=cfg["account_id"],
                api_key=cfg["token"],
                endpoint=page_endpoint,
                mode=cfg["mode"],
                timeout_s=8.0,
            )
            page_items = page_payload.get("transactions")
            if isinstance(page_items, list):
                transactions.extend(item for item in page_items if isinstance(item, dict))
        latest_fill: Optional[datetime] = None
        for tx in transactions:
            if str(tx.get("type") or "").strip().upper() != "ORDER_FILL":
                continue
            tx_time = str(tx.get("time") or "").strip()
            if not tx_time:
                continue
            parsed = _parse_oanda_timestamp(tx_time).astimezone(timezone.utc)
            if latest_fill is None or parsed > latest_fill:
                latest_fill = parsed
        if latest_fill is not None:
            return latest_fill
        cursor_end = cursor_start
    return None


async def _build_oanda_inactivity_status() -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    cfg = _get_oanda_config("live")
    summary_payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint="/accounts/{account_id}/summary",
        mode=cfg["mode"],
    )
    account_summary = summary_payload.get("account") if isinstance(summary_payload.get("account"), dict) else {}
    open_trade_count = int(_to_float(account_summary.get("openTradeCount")) or 0)
    open_position_count = int(_to_float(account_summary.get("openPositionCount")) or 0)
    has_open_positions = open_trade_count > 0 or open_position_count > 0

    account_created = await _fetch_oanda_account_created_time(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
    )
    last_fill_at = await _fetch_oanda_last_live_fill_time(cfg)
    base_payload: Dict[str, object] = {
        "ok": True,
        "mode": "live",
        "last_live_fill_at": _format_oanda_timestamp(last_fill_at) if last_fill_at else None,
        "open_trade_count": open_trade_count,
        "open_position_count": open_position_count,
        "has_open_positions": has_open_positions,
        "policy_months_without_trade": 12,
        "monthly_fee_aud": 10,
        "updated_at": _utc_now_iso(),
    }

    if last_fill_at is None:
        return {
            **base_payload,
            "ok": False,
            "status": "unavailable",
            "error": "Could not resolve last ORDER_FILL transaction from OANDA history pages.",
            "inactivity_threshold_at": None,
            "earliest_fee_date": None,
            "seconds_until_threshold": None,
        }

    threshold_anchor = last_fill_at
    threshold_at = (threshold_anchor + relativedelta(months=12)).astimezone(timezone.utc)
    earliest_fee_date = _fee_charge_date_on_or_after(threshold_at)
    seconds_until_threshold = max(0, int((threshold_at - now).total_seconds()))
    status = "countdown"
    if has_open_positions:
        status = "paused_open_position"
    elif now >= threshold_at:
        status = "fee_eligible"

    return {
        **base_payload,
        "ok": True,
        "status": status,
        "inactivity_threshold_at": _format_oanda_timestamp(threshold_at),
        "earliest_fee_date": _format_oanda_timestamp(earliest_fee_date),
        "seconds_until_threshold": seconds_until_threshold,
    }


async def _recover_oanda_recent_fills(account: str, lookback_hours: int = 72) -> Dict[str, object]:
    cfg = _get_oanda_config(account)
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(hours=max(1, int(lookback_hours)))
    transactions, last_transaction_id = await _fetch_oanda_transactions(
        cfg=cfg,
        start_time=_format_oanda_timestamp(start_dt),
        end_time=_format_oanda_timestamp(now),
        tx_type="ORDER_FILL",
    )
    seen: set[str] = set()
    sorted_transactions = sorted(
        [tx for tx in transactions if isinstance(tx, dict)],
        key=lambda tx: (
            int(_to_float(tx.get("id")) or 0),
            str(tx.get("time") or ""),
        ),
    )
    recovered_rows = 0
    skipped_duplicates = 0
    max_seen = int(_to_float(_OANDA_TX_LAST_SEEN.get(account)) or 0)
    for entry in sorted_transactions:
        tx_id = str(entry.get("id") or "").strip()
        if tx_id and tx_id in seen:
            skipped_duplicates += 1
            continue
        if tx_id:
            seen.add(tx_id)
            max_seen = max(max_seen, int(_to_float(tx_id) or 0))
        journal_rows = _journal_rows_from_oanda_order_fill({**entry, "account": account})
        if journal_rows:
            recovered_rows += _upsert_trading_journal_rows(journal_rows)
    if last_transaction_id:
        max_seen = max(max_seen, int(_to_float(last_transaction_id) or 0))
    if max_seen > 0:
        _OANDA_TX_LAST_SEEN[account] = str(max_seen)
    _record_oanda_fill_diagnostic(
        account,
        last_success_at=_utc_now_iso(),
        last_error=None,
        last_seen_transaction_id=_OANDA_TX_LAST_SEEN.get(account),
        startup_recovered_rows=recovered_rows,
        startup_skipped_duplicates=skipped_duplicates,
    )
    BYBIT_LOGGER.info(
        "OANDA fill recovery account=%s lookback_hours=%s tx=%s recovered_rows=%s skipped_duplicates=%s last_seen=%s",
        account,
        lookback_hours,
        len(sorted_transactions),
        recovered_rows,
        skipped_duplicates,
        _OANDA_TX_LAST_SEEN.get(account),
    )
    return {
        "ok": True,
        "account": account,
        "transactions": len(sorted_transactions),
        "recovered_rows": recovered_rows,
        "skipped_duplicates": skipped_duplicates,
        "last_seen_transaction_id": _OANDA_TX_LAST_SEEN.get(account),
    }


async def _poll_oanda_fills() -> None:
    lookback_hours = int(os.getenv("OANDA_FILL_RECOVERY_LOOKBACK_HOURS", "72") or "72")
    while True:
        await asyncio.sleep(FILL_ALERT_POLL_SECONDS)
        for account in ("live", "demo"):
            try:
                cfg = _get_oanda_config(account)
            except ValueError:
                continue
            try:
                last_seen = _OANDA_TX_LAST_SEEN.get(account)
                backoff_until = _OANDA_FILL_BACKOFF_UNTIL.get(account, 0.0)
                if backoff_until > time.time():
                    continue

                if last_seen is None:
                    await _recover_oanda_recent_fills(account, lookback_hours=lookback_hours)
                    _OANDA_FILL_FAILURES.pop(account, None)
                    _OANDA_FILL_BACKOFF_UNTIL.pop(account, None)
                    continue

                transactions, last_transaction_id = await _fetch_oanda_transactions(
                    cfg=cfg,
                    since_id=last_seen,
                )

                max_seen = int(last_seen)
                for entry in transactions:
                    tx_id_raw = str(entry.get("id") or "0")
                    try:
                        tx_id = int(tx_id_raw)
                    except ValueError:
                        continue
                    if tx_id <= max_seen:
                        continue
                    max_seen = tx_id
                    entry_payload = {**entry, "account": account}
                    journal_rows = _journal_rows_from_oanda_order_fill(entry_payload)
                    if journal_rows:
                        _upsert_trading_journal_rows(journal_rows)
                    await _send_telegram_alert(_format_oanda_fill_alert(entry_payload))

                if last_transaction_id:
                    _OANDA_TX_LAST_SEEN[account] = last_transaction_id
                else:
                    _OANDA_TX_LAST_SEEN[account] = str(max_seen)
                _record_oanda_fill_diagnostic(
                    account,
                    poll_enabled=True,
                    last_success_at=_utc_now_iso(),
                    last_error=None,
                    last_seen_transaction_id=_OANDA_TX_LAST_SEEN.get(account),
                )

                _OANDA_FILL_FAILURES.pop(account, None)
                _OANDA_FILL_BACKOFF_UNTIL.pop(account, None)
            except Exception:  # pragma: no cover - background task
                failures = _OANDA_FILL_FAILURES.get(account, 0) + 1
                _OANDA_FILL_FAILURES[account] = failures
                delay_s = min(120.0, float(2 ** min(failures, 6)))
                _OANDA_FILL_BACKOFF_UNTIL[account] = time.time() + delay_s
                BYBIT_LOGGER.exception(
                    "OANDA fill poll error account=%s failures=%s next_retry_in=%.1fs",
                    account,
                    failures,
                    delay_s,
                )
                _record_oanda_fill_diagnostic(
                    account,
                    poll_enabled=True,
                    last_error=f"poll failed failures={failures}",
                    last_seen_transaction_id=_OANDA_TX_LAST_SEEN.get(account),
                )


@app.get("/api/bybit/balance")
async def fetch_bybit_balance(
    account: str = "live",
    coin: str = "USDT",
    account_type: str = "UNIFIED",
) -> JSONResponse:
    account_mode = account.strip().lower()
    if account_mode not in {"live", "demo"}:
        account_mode = "live"
    coin = coin.strip().upper()

    _mode, api_key, api_secret, base_url, key_source = resolve_bybit_credentials_for(
        "demo" if account_mode == "demo" else "live"
    )
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=500,
            detail="Missing BYBIT_API_KEY2/BYBIT_API_SECRET2 (or legacy BYBIT_API_KEY/BYBIT_API_SECRET).",
        )

    params: Dict[str, str] = {"accountType": account_type}
    if account_mode != "demo":
        params["coin"] = coin
    query = "&".join(f"{k}={v}" for k, v in params.items())
    path = "/v5/account/wallet-balance"

    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, query)
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }

    url = f"{base_url}{path}?{query}"
    BALANCE_LOGGER.info(
        "BALANCE_DIAG request mode=%s env=%s base_url=%s path=%s query=%s key_source=%s",
        account_mode,
        _mode,
        base_url,
        path,
        query,
        key_source,
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers)
    payload = resp.json()
    ret_code = payload.get("retCode")
    ret_msg = payload.get("retMsg")
    results = payload.get("result", {}).get("list", [])
    coin_entries = []
    equity_fields = []
    balance_value = None
    for item in results:
        for field in ("totalEquity", "totalWalletBalance", "totalAvailableBalance"):
            if item.get(field) is not None:
                equity_fields.append(field)
        for bal in item.get("coin", []):
            symbol = bal.get("coin")
            if symbol:
                coin_entries.append(str(symbol))
            if symbol == coin:
                balance_value = float(
                    bal.get("availableToTrade", bal.get("walletBalance", 0))
                )

    if balance_value is None and results:
        fallback = results[0].get("totalEquity")
        if fallback is not None:
            balance_value = float(fallback)

    BALANCE_LOGGER.info(
        "BALANCE_DIAG response http_status=%s retCode=%s retMsg=%s coins=%s equity_fields=%s",
        resp.status_code,
        ret_code,
        ret_msg,
        coin_entries,
        sorted(set(equity_fields)),
    )

    if ret_code not in (0, "0"):
        raise HTTPException(
            status_code=502,
            detail=f"Bybit error retCode={ret_code} retMsg={ret_msg}",
        )
    if balance_value is None:
        raise HTTPException(status_code=500, detail=f"Balance for {coin} not found.")

    return JSONResponse(
        {
            "balance": balance_value,
            "coin": coin,
            "account": account_mode,
            "retCode": ret_code,
            "retMsg": ret_msg,
        }
    )



@app.get("/", response_class=HTMLResponse, response_model=None)
async def home_page() -> Response:
    if APP_PROFILE == "journal":
        return RedirectResponse(url="/trading-journal", status_code=307)
    return HTMLResponse(HTML_TEMPLATE)


@app.get("/instrument-specs", response_class=HTMLResponse)
@app.get("/instrument-specs/", response_class=HTMLResponse)
async def instrument_specs_page() -> str:
    return INSTRUMENT_SPECS_TEMPLATE


@app.get("/api/instrument-specs")
async def api_instrument_specs(
    query: Optional[str] = None,
    q: Optional[str] = None,
    prefer: Optional[str] = None,
    include_scanner: Optional[str] = None,
) -> JSONResponse:
    lookup = str(query or q or "").strip()
    specs = await _fetch_instrument_specs(
        lookup,
        prefer=prefer,
        include_scanner=_truthy_query_param(include_scanner),
    )
    return JSONResponse(specs)


@app.get("/api/resolve-symbol")
async def api_resolve_symbol(
    symbol: str,
    prefer: Optional[str] = "bybit",
    scope: Optional[str] = "all",
) -> JSONResponse:
    resolved = await _resolve_symbol_payload(symbol, prefer or "bybit", scope or "all")
    if not resolved or not resolved.get("resolved_symbol"):
        raise HTTPException(status_code=404, detail=f"No match for '{symbol}'")
    return JSONResponse(resolved)


@app.get("/api/instrument-specs.jpg")
async def api_instrument_specs_jpg(
    query: Optional[str] = None,
    q: Optional[str] = None,
    prefer: Optional[str] = None,
    include_scanner: Optional[str] = None,
) -> Response:
    lookup = str(query or q or "").strip()
    specs = await _fetch_instrument_specs(
        lookup,
        prefer=prefer,
        include_scanner=_truthy_query_param(include_scanner),
    )
    blob = _render_specs_jpg_bytes(specs)
    safe = "".join(
        ch for ch in lookup if ch.isalnum() or ch in ("_", "-", ".")
    ) or "query"
    headers = {
        "Content-Disposition": f'inline; filename="instrument-specs-{safe}.jpg"'
    }
    return Response(content=blob, media_type="image/jpeg", headers=headers)




def _merged_shell(title: str, options: List[Tuple[str, str]]) -> str:
    opts = "".join(f'<option value="{html.escape(url)}">{html.escape(label)}</option>' for label, url in options)
    first = options[0][1] if options else "/"
    return f"""<!doctype html><html><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/><title>{html.escape(title)}</title>
<style>body{{margin:0;background:#0b1220;color:#e2e8f0;font-family:Inter,system-ui,sans-serif}}.wrap{{padding:16px;max-width:1800px;margin:0 auto}}.toolbar{{display:flex;gap:10px;align-items:center;background:#111827;border:1px solid #1f2937;border-radius:12px;padding:12px;margin-bottom:12px}}select,button{{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:8px 10px}}iframe{{width:100%;height:calc(100vh - 110px);border:1px solid #1f2937;border-radius:12px;background:#0f172a}}</style>
</head><body><div class='wrap'><div class='toolbar'><strong>{html.escape(title)}</strong><select id='sel'>{opts}</select></div><iframe id='frame' src='{html.escape(first)}'></iframe></div>
<script>const sel=document.getElementById('sel');const frame=document.getElementById('frame');sel.addEventListener('change',()=>frame.src=sel.value);</script></body></html>"""


CALCULATOR_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Position Size Calculator</title>
  <style>
    body{margin:0;background:#0b1220;color:#e2e8f0;font-family:Inter,system-ui,sans-serif}
    .wrap{width:100%;max-width:none;margin:0;padding:18px}
    .panel{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:16px}
    .calc-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;align-items:start}
    .calc-col{display:flex;flex-direction:column;gap:12px;min-width:0}
    .row{display:flex;flex-direction:column;gap:6px;align-items:stretch;margin-bottom:12px}
    .group{display:flex;gap:8px;flex-wrap:wrap}
    label{display:flex;flex-direction:column;gap:6px;font-weight:700;color:#cbd5e1}
    input,select,button{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:10px;padding:8px 10px}
    .compact{width:100%}
    .compact-symbol{max-width:180px}
    .compact-limit{max-width:160px}
    .compact-ticks{max-width:90px}
    .compact-rr{max-width:90px}
    .compact-risk{max-width:110px}
    button{cursor:pointer;font-weight:700}
    .toggle button.active{background:#2563eb;border-color:#3b82f6}
    .error{color:#fca5a5;min-height:1.2em}
    .ok{color:#86efac}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}
    .grid.compact-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
    .card{background:#0f172a;border:1px solid #1f2937;border-radius:10px;padding:10px}
    #calc-instrument-specs .card,#calc-journal-summary .card{padding:8px}
    .muted{color:#94a3b8;font-size:0.9rem}
    .right-panel-title{margin:0 0 4px;font-size:0.9rem;color:#cbd5e1}
    #calc-instrument-specs,#calc-journal-summary{width:100%;min-width:0;overflow:hidden}
    .specs-table{width:100%;border-collapse:collapse;table-layout:fixed}
    .specs-table td{border-bottom:1px solid #1f2937;padding:4px 5px;font-size:0.76rem;vertical-align:top;overflow-wrap:anywhere;word-break:break-word;line-height:1.3}
    @media (max-width:820px){.calc-grid{grid-template-columns:1fr}}
    @media (max-width:900px){.grid.compact-grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h2 style="margin-top:0">Position Size Calculator</h2>
      <div class="calc-grid">
      <div class="calc-col">
      <div class="row">
        <label>Account</label>
        <div class="group toggle" id="account-toggle"><button type="button" data-v="live" class="active">Live</button><button type="button" data-v="demo">Demo</button></div>
      </div>
      <div class="row">
        <label>Asset</label>
        <div class="group toggle" id="asset-toggle"><button type="button" data-v="crypto" class="active">Crypto</button><button type="button" data-v="fx">FX</button></div>
      </div>
      <div class="row">
        <label>Side</label>
        <div class="group toggle" id="side-toggle"><button type="button" data-v="buy" class="active">Buy</button><button type="button" data-v="sell">Sell</button></div>
      </div>
      <div class="row">
        <label>Order type</label>
        <div class="group toggle" id="order-toggle"><button type="button" data-v="market" class="active">Market</button><button type="button" data-v="limit">Limit</button></div>
      </div>
      <div class="row">
        <label>Symbol<input id="calc-symbol" class="compact compact-symbol" placeholder="BTC or EUR_USD"/></label>
        <div class="muted" id="calc-canonical-symbol"></div>
      </div>
      <div class="row">
        <h3 class="right-panel-title">Instrument specs</h3>
        <div id="calc-instrument-specs"></div>
      </div>
      <div class="row">
        <h3 class="right-panel-title">Journal stats</h3>
        <div class="grid compact-grid" id="calc-journal-summary"></div>
      </div>
      <div class="row" id="limit-wrap" style="display:none">
        <label>Limit entry price<input id="calc-limit" class="compact compact-limit" type="number" step="any"/></label>
      </div>
      <div class="row">
        <label>Stop loss ticks<input id="calc-sl-ticks" class="compact compact-ticks" type="number" min="1" step="1" value="10"/></label>
      </div>
      <div class="row" id="rr-wrap">
        <label>Risk reward<input id="calc-rr" class="compact compact-rr" type="number" min="0.1" step="0.1" value="2"/></label>
      </div>
      <div class="row" id="risk-toggle-wrap">
        <label>Risk mode</label>
        <div class="group toggle" id="risk-toggle"><button type="button" data-v="fixed_aud">Fixed AUD</button><button type="button" data-v="percent" class="active">%</button></div>
      </div>
      <div class="row">
        <label id="calc-risk-label">Risk value (%)</label>
        <input id="calc-risk" class="compact compact-risk" type="number" min="0.0001" step="any" value="1"/>
      </div>
      <div class="row">
        <label>Webhook</label>
        <div class="group toggle" id="webhook-toggle"><button type="button" data-v="no" class="active">No</button><button type="button" data-v="yes">Yes</button></div>
      </div>
      <div class="row">
        <label>Test</label>
        <div class="group toggle" id="test-toggle"><button type="button" data-v="no" class="active">No</button><button type="button" data-v="yes">Yes</button></div>
      </div>
      <div class="row">
        <label>Timeframe</label>
        <div class="group toggle" id="timeframe-toggle"></div>
      </div>
      <div class="row">
        <div class="group">
          <button id="calc-quote" type="button">Calculate</button>
          <button id="calc-submit" type="button">Submit Order</button>
        </div>
      </div>
      <div class="row" id="calc-webhook-panel" style="display:none">
        <label>TradingView Webhook URL</label>
        <pre id="calc-webhook-url" class="card" style="white-space:pre-wrap;word-break:break-word;max-height:80px;overflow:auto"></pre>
        <div class="group"><button id="calc-webhook-copy-url" type="button">Copy URL</button></div>
        <label>TradingView Message JSON</label>
        <pre id="calc-webhook-json" class="card" style="white-space:pre-wrap;word-break:break-word;max-height:220px;overflow:auto"></pre>
        <div class="group"><button id="calc-webhook-copy" type="button">Copy JSON</button></div>
      </div>
      <div id="calc-error" class="error"></div>
      <div class="grid" id="calc-error-debug"></div>
      <div id="calc-success" class="ok"></div>
      <div id="calc-request-summary" class="muted"></div>
      <p class="muted">10 ticks always means 10 × broker minimum tick size. For 5-decimal FX pairs, 35 ticks = 3.5 pips.</p>
      <div class="row">
        <label>Quote results</label>
        <div class="grid" id="calc-results"></div>
      </div>
      </div>
      </div>
    </div>
  </div>
  <script src="{{CALCULATOR_JS_URL}}"></script>
</body>
</html>"""


@app.get("/merged/calculator")
async def merged_calculator_page() -> HTMLResponse:
    calc_js_version = quote(str(os.getenv("APP_BUILD_STAMP") or os.getenv("RENDER_GIT_COMMIT") or app.version), safe="")
    page = CALCULATOR_TEMPLATE.replace("{{CALCULATOR_JS_URL}}", f"/static/calculator.js?v={calc_js_version}")
    return HTMLResponse(page)


@app.get("/merged/scanner")
async def merged_scanner_redirect() -> Response:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/merged/scanner")
    return RedirectResponse(url="/merged/monitor", status_code=307)


def _dec(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be numeric.") from exc


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _floor_to_precision(value: Decimal, precision: int) -> Decimal:
    quant = Decimal("1").scaleb(-max(0, int(precision)))
    return value.quantize(quant, rounding=ROUND_DOWN)


def _fmt_dec(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _snap_to_increment(
    value: Optional[Decimal],
    increment: Optional[Decimal],
    rounding=ROUND_DOWN,
) -> Optional[Decimal]:
    if value is None:
        return None
    if increment is None or increment <= 0:
        return value
    return (value / increment).to_integral_value(rounding=rounding) * increment


def _fmt_dec_by_increment(
    value: Optional[Decimal],
    increment: Optional[Decimal],
    rounding=ROUND_DOWN,
) -> Optional[str]:
    snapped = _snap_to_increment(value, increment, rounding=rounding)
    return _fmt_dec(snapped) if snapped is not None else None


def _fmt_dec_by_precision(
    value: Optional[Decimal],
    precision: Optional[Decimal],
    rounding=ROUND_HALF_UP,
) -> Optional[str]:
    if value is None:
        return None
    if precision and precision > 0:
        try:
            value = value.quantize(precision, rounding=rounding)
        except Exception:
            pass
    return _fmt_dec(value)


_BYBIT_NAME_ALIAS_CACHE: Dict[str, object] = {"expires_at": 0.0, "aliases": {}}


async def _bybit_name_aliases_for_choices(base_url: str, symbols: List[str] | Set[str]) -> Dict[str, str]:
    now = time.time()
    cached_aliases = _BYBIT_NAME_ALIAS_CACHE.get("aliases")
    if isinstance(cached_aliases, dict) and now < float(_BYBIT_NAME_ALIAS_CACHE.get("expires_at") or 0):
        alias_map = cached_aliases
    else:
        payload = await _bybit_get_async(base_url, "/v5/market/instruments-info", {"category": "linear", "limit": 1000})
        rows = (payload.get("result") or {}).get("list") or []
        alias_map: Dict[str, str] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            base_coin = str(row.get("baseCoin") or "").upper()
            display_name = norm_symbol(row.get("displayName"))
            if base_coin:
                alias_map[base_coin] = base_coin
            if symbol:
                alias_map[norm_symbol(symbol)] = base_coin or symbol
            if display_name and base_coin:
                alias_map[display_name] = base_coin
        _BYBIT_NAME_ALIAS_CACHE["aliases"] = alias_map
        _BYBIT_NAME_ALIAS_CACHE["expires_at"] = now + 6 * 60 * 60
    choices = {str(s or "").strip().upper() for s in symbols if str(s or "").strip()}
    out: Dict[str, str] = {}
    for name_key, ticker in alias_map.items():
        if ticker and any(f"{ticker}{quote}" in choices for quote in ("USDT", "USDC", "USD")):
            out[name_key] = ticker
    return out


async def _fetch_bybit_balance_usdt(account: str) -> Dict[str, Decimal]:
    _mode, api_key, api_secret, base_url, _src = resolve_bybit_credentials_for(account)
    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="Bybit credentials are missing for selected account.")
    path = "/v5/account/wallet-balance"
    try:
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path=path,
            params={"accountType": "UNIFIED"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bybit balance lookup failed path={path}: {exc}") from exc
    rows = (payload.get("result") or {}).get("list") or []
    for row in rows:
        total_equity = Decimal(str(row.get("totalEquity") or "0"))
        total_available_balance = Decimal(str(row.get("totalAvailableBalance") or "0"))
        for coin in row.get("coin", []) or []:
            if str(coin.get("coin") or "").upper() == "USDT":
                val = coin.get("availableToTrade") or coin.get("walletBalance")
                if val is not None:
                    return {
                        "available_usdt": Decimal(str(val)),
                        "total_equity": total_equity,
                        "total_available_balance": total_available_balance,
                    }
        if total_equity > 0 or total_available_balance > 0:
            return {
                "available_usdt": total_available_balance if total_available_balance > 0 else total_equity,
                "total_equity": total_equity,
                "total_available_balance": total_available_balance,
            }
    raise HTTPException(status_code=502, detail=f"Bybit balance unavailable path={path}.")


@app.get("/api/calculator/bootstrap")
async def calculator_bootstrap() -> JSONResponse:
    return JSONResponse(
        {
            "accounts": ["live", "demo"],
            "assets": ["crypto", "fx"],
            "sides": ["buy", "sell"],
            "order_types": ["market", "limit"],
            "risk_modes": ["fixed_aud", "percent"],
        }
    )


@app.get("/api/calculator/instrument")
async def calculator_instrument(asset: str, account: str, symbol: str) -> JSONResponse:
    asset_norm = str(asset or "").strip().lower()
    account_norm = str(account or "live").strip().lower()
    if asset_norm == "crypto":
        _mode, _key, _secret, base_url, _src = resolve_bybit_credentials_for(account_norm)
        choices = await _bybit_get_symbols_by_category_cached(base_url, "linear")
        if not choices:
            raise HTTPException(status_code=404, detail=f"Could not resolve Bybit symbol: {symbol}")
        resolved = resolve_bybit_symbol_from_choices(symbol, choices)
        if not resolved or not resolved.get("resolved_symbol"):
            try:
                name_aliases = await _bybit_name_aliases_for_choices(base_url, set(choices))
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Bybit alias metadata unavailable: {exc}") from exc
            if name_aliases:
                resolved = resolve_bybit_symbol_from_choices(symbol, choices, extra_aliases=name_aliases)
        if not resolved or not resolved.get("resolved_symbol"):
            raise HTTPException(status_code=404, detail=f"Could not resolve Bybit symbol: {symbol}")
        resolved_symbol = str(resolved["resolved_symbol"]).upper()
        payload = await _bybit_get_async(
            base_url,
            "/v5/market/instruments-info",
            {"category": "linear", "symbol": resolved_symbol},
        )
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            raise HTTPException(status_code=502, detail=f"Bybit instrument meta unavailable for {resolved_symbol}.")
        item = rows[0]
        return JSONResponse(
            {
                "broker": "bybit",
                "account": account_norm,
                "symbol": resolved_symbol,
                "tick_size": item.get("priceFilter", {}).get("tickSize"),
                "qty_step": item.get("lotSizeFilter", {}).get("qtyStep"),
                "min_qty": item.get("lotSizeFilter", {}).get("minOrderQty"),
                "max_qty": item.get("lotSizeFilter", {}).get("maxOrderQty"),
                "max_mkt_qty": item.get("lotSizeFilter", {}).get("maxMktOrderQty"),
                "min_notional": item.get("lotSizeFilter", {}).get("minNotionalValue"),
                "leverage_filter": item.get("leverageFilter") or {},
            }
        )

    if asset_norm == "fx":
        try:
            cfg = _get_oanda_config(account_norm)
            resolved_symbol = normalize_oanda_symbol_query(symbol)
            meta = await _fetch_oanda_instrument_meta(
                base_url=cfg["base_url"],
                account_id=cfg["account_id"],
                api_key=cfg["token"],
                symbol=resolved_symbol,
                mode=account_norm,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(
            {
                "broker": "oanda",
                "account": account_norm,
                "symbol": resolved_symbol,
                "displayPrecision": meta.get("displayPrecision"),
                "tradeUnitsPrecision": meta.get("tradeUnitsPrecision"),
                "pipLocation": meta.get("pipLocation"),
                "minimumTradeSize": meta.get("minimumTradeSize"),
                "maximumOrderUnits": meta.get("maximumOrderUnits"),
                "maximumPositionSize": meta.get("maximumPositionSize"),
                "marginRate": meta.get("marginRate"),
            }
        )
    raise HTTPException(status_code=400, detail="asset must be crypto or fx.")


@app.get("/api/calculator/journal-summary")
async def calculator_journal_summary(asset: str, symbol: str) -> JSONResponse:
    asset_norm = str(asset or "").strip().lower()
    symbol_in = str(symbol or "").strip()
    if not symbol_in:
        raise HTTPException(status_code=400, detail="symbol is required.")
    base_rows = [
        _backfill_trade_row_context_fields(r)
        for r in _get_trading_journal_rows()
        if isinstance(r, dict) and not _exclude_bybit_demo_row(r)
    ]
    balances = _get_excel_account_balances()
    rows = _enrich_trade_row_metrics(_apply_analysis_balances(_calc_balance_after_trade(base_rows, balances)))

    canonical = ""
    if asset_norm == "crypto":
        creds = resolve_bybit_credentials_for("live")
        base_url = creds[3] if isinstance(creds, tuple) else (creds.get("base_url") if isinstance(creds, dict) else "")
        choices = await _bybit_get_symbols_by_category_cached(base_url or BYBIT_BASE, "linear")
        if not choices:
            return JSONResponse({"status": "unresolved", "canonical_symbol": "", "stats": None}, status_code=404)
        resolved = resolve_bybit_symbol_from_choices(symbol_in, choices)
        if not resolved or not resolved.get("resolved_symbol"):
            try:
                name_aliases = await _bybit_name_aliases_for_choices(base_url or BYBIT_BASE, set(choices))
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Bybit alias metadata unavailable: {exc}") from exc
            if name_aliases:
                resolved = resolve_bybit_symbol_from_choices(symbol_in, choices, extra_aliases=name_aliases)
        canonical = str((resolved or {}).get("resolved_symbol") or "").upper()
    elif asset_norm == "fx":
        canonical = normalize_oanda_symbol_query(symbol_in)
    else:
        raise HTTPException(status_code=400, detail="asset must be crypto or fx.")

    if not canonical:
        return JSONResponse({"status": "unresolved", "canonical_symbol": "", "stats": None}, status_code=404)
    canonical_key = norm_symbol(canonical)
    filtered: List[Dict[str, object]] = []
    for r in rows:
        row_symbol = str(r.get("symbol") or "")
        row_key = norm_symbol(row_symbol)
        if not row_key:
            continue
        if asset_norm == "fx":
            try:
                row_fx = normalize_oanda_symbol_query(row_symbol)
                if norm_symbol(row_fx) == canonical_key:
                    if not _is_test_trade_row(r):
                        filtered.append(r)
            except Exception:
                if row_key == canonical_key:
                    if not _is_test_trade_row(r):
                        filtered.append(r)
            continue
        # crypto: include exact normalized key and shorthand-equivalent variants.
        if row_key == canonical_key or row_key.startswith(canonical_key) or canonical_key.startswith(row_key):
            if not _is_test_trade_row(r):
                filtered.append(r)
    filtered_sorted = sorted(filtered, key=_row_sort_dt, reverse=True)
    if not filtered_sorted:
        return JSONResponse({"status": "no_data", "canonical_symbol": canonical, "stats": None, "trades": []})
    stats = _compute_journal_stats(filtered_sorted, balances)
    totals = stats.get("totals") if isinstance(stats, dict) else {}
    last_trade_ts = None
    try:
        last_trade_ts = max(
            (str(r.get("close_time") or r.get("open_time") or "") for r in filtered_sorted if str(r.get("close_time") or r.get("open_time") or "").strip()),
            default=None,
        )
    except Exception:
        last_trade_ts = None
    summary = {
        "total_trades": totals.get("trades"),
        "wins": totals.get("wins"),
        "losses": totals.get("losses"),
        "break_even": totals.get("break_even"),
        "long_trades": totals.get("long_trades"),
        "short_trades": totals.get("short_trades"),
        "long_wins": totals.get("long_wins"),
        "long_losses": totals.get("long_losses"),
        "short_wins": totals.get("short_wins"),
        "short_losses": totals.get("short_losses"),
        "win_rate": (f"{float(totals.get('win_rate_pct')):.2f}%" if totals.get("win_rate_pct") is not None else None),
        "avg_stop_distance": totals.get("avg_stop_pct"),
        "avg_target_distance": totals.get("avg_target_pct"),
        "avg_trade_duration": totals.get("avg_duration_seconds"),
        "last_trade_timestamp": last_trade_ts,
    }
    return JSONResponse(
        {
            "status": "ok",
            "canonical_symbol": canonical,
            "stats": summary,
            "trades": filtered_sorted,
        }
    )


@app.post("/api/calculator/quote")
async def calculator_quote(request: Request, payload: Dict[str, object] = Body(default={})) -> JSONResponse:
    if isinstance(request, dict) and (not payload):
        payload = request
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/calculator/quote",
                "raw_path": b"/api/calculator/quote",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 0),
                "server": ("localhost", 80),
            }
        )
    try:
        asset = str(payload.get("asset") or "").strip().lower()
        account = str(payload.get("account") or "live").strip().lower()
        side = str(payload.get("side") or "buy").strip().lower()
        order_type = str(payload.get("order_type") or "market").strip().lower()
        risk_mode = str(payload.get("risk_mode") or "percent").strip().lower()
        symbol_in = str(payload.get("symbol") or "").strip()
        target_mode = str(payload.get("target_mode") or "").strip().lower()
        webhook_mode = str(payload.get("webhook") or payload.get("webhook_mode") or "no").strip().lower()
        is_test_trade = _normalize_test_trade_flag(payload.get("test", payload.get("test_trade", payload.get("is_test_trade"))))
        existing_pending_id = str(payload.get("pending_webhook_id") or "").strip()
        previous_pending_id = str(payload.get("previous_pending_webhook_id") or "").strip()
        if not symbol_in:
            raise HTTPException(status_code=400, detail="symbol is required.")
        if side not in {"buy", "sell"}:
            raise HTTPException(status_code=400, detail="side must be buy or sell.")
        if order_type not in {"market", "limit"}:
            raise HTTPException(status_code=400, detail="order_type must be market or limit.")
        if risk_mode not in {"fixed_aud", "percent"}:
            raise HTTPException(status_code=400, detail="risk_mode must be fixed_aud or percent.")
        if not target_mode:
            target_mode = "rr" if str(payload.get("risk_reward") or "").strip() else "ticks"
        if target_mode not in {"rr", "ticks"}:
            raise HTTPException(status_code=400, detail="target_mode must be rr or ticks.")
        if webhook_mode not in {"yes", "no", "true", "false", "1", "0"}:
            raise HTTPException(status_code=400, detail="webhook must be yes or no.")

        stop_ticks = _dec(payload.get("stop_loss_ticks"), "stop_loss_ticks")
        if stop_ticks <= 0:
            raise HTTPException(status_code=400, detail="stop_loss_ticks must be greater than zero.")

        risk_val = _dec(payload.get("risk_value"), "risk_value")
        if risk_val <= 0:
            raise HTTPException(status_code=400, detail="risk_value must be greater than zero.")
        limit_entry = payload.get("entry_price")
        if order_type == "limit" and (limit_entry is None or str(limit_entry).strip() == ""):
            raise HTTPException(status_code=400, detail="Limit orders require entry_price.")

        rr_requested: Optional[Decimal] = None
        tp_ticks: Optional[Decimal] = None
        if target_mode == "rr":
            rr_requested = _dec(payload.get("risk_reward"), "risk_reward")
            if rr_requested <= 0:
                raise HTTPException(status_code=400, detail="risk_reward must be greater than zero when target_mode=rr.")
        else:
            tp_ticks = _dec(payload.get("take_profit_ticks"), "take_profit_ticks")
            if tp_ticks <= 0:
                raise HTTPException(status_code=400, detail="take_profit_ticks must be greater than zero when target_mode=ticks.")

        webhook_enabled = webhook_mode in {"yes", "true", "1"}
        webhook_base_url = _public_webhook_base_url(request)
        webhook_endpoint_url = f"{webhook_base_url}/api/calculator/webhook"
        parsed_base = urlparse(webhook_base_url if "://" in webhook_base_url else f"https://{webhook_base_url}")
        webhook_origin_host = str(parsed_base.hostname or "").strip().lower() or None
        webhook_origin_profile = APP_PROFILE
        webhook_origin_instance_id = str(
            os.getenv("APP_INSTANCE_ID")
            or os.getenv("RENDER_INSTANCE_ID")
            or os.getenv("RENDER_SERVICE_ID")
            or os.getenv("HOSTNAME")
            or ""
        ).strip() or None
        if webhook_enabled and webhook_origin_host in {"localhost", "127.0.0.1"}:
            local_override = str(os.getenv("ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS") or "").strip().lower() in {"1", "true", "yes", "on"}
            if not local_override:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "TradingView webhook payload must be generated on the same public instance that receives it. "
                        "Use the Render calculator page or set PUBLIC_WEBHOOK_BASE_URL to a reachable same-instance URL."
                    ),
                )

        if asset == "crypto":
            if risk_mode == "fixed_aud":
                raise HTTPException(status_code=400, detail="fixed_aud risk mode is only supported for FX.")
            _mode, api_key, api_secret, base_url, _src = resolve_bybit_credentials_for(account)
            if not api_key or not api_secret:
                raise HTTPException(status_code=500, detail="Bybit credentials are missing for selected account.")
            choices = await _bybit_get_symbols_by_category_cached(base_url, "linear")
            if not choices:
                raise HTTPException(status_code=404, detail=f"Could not resolve Bybit symbol: {symbol_in}")
            resolved = resolve_bybit_symbol_from_choices(symbol_in, choices)
            if not resolved or not resolved.get("resolved_symbol"):
                try:
                    name_aliases = await _bybit_name_aliases_for_choices(base_url, set(choices))
                except Exception as exc:
                    raise HTTPException(status_code=503, detail=f"Bybit alias metadata unavailable: {exc}") from exc
                if name_aliases:
                    resolved = resolve_bybit_symbol_from_choices(symbol_in, choices, extra_aliases=name_aliases)
            resolved_symbol = str((resolved or {}).get("resolved_symbol") or "").upper()
            if not resolved_symbol:
                raise HTTPException(status_code=404, detail=f"Could not resolve Bybit symbol: {symbol_in}")
            inst_payload = await _bybit_get_async(base_url, "/v5/market/instruments-info", {"category": "linear", "symbol": resolved_symbol})
            inst_rows = (inst_payload.get("result") or {}).get("list") or []
            if not inst_rows:
                raise HTTPException(status_code=502, detail="Bybit instrument meta fetch failed.")
            inst = inst_rows[0]
            tick_size = Decimal(str(inst.get("priceFilter", {}).get("tickSize") or "0"))
            qty_step = Decimal(str(inst.get("lotSizeFilter", {}).get("qtyStep") or "0"))
            min_qty = Decimal(str(inst.get("lotSizeFilter", {}).get("minOrderQty") or "0"))
            max_qty = Decimal(str(inst.get("lotSizeFilter", {}).get("maxOrderQty") or "0"))
            max_mkt_qty = Decimal(str(inst.get("lotSizeFilter", {}).get("maxMktOrderQty") or "0"))
            min_notional = Decimal(str(inst.get("lotSizeFilter", {}).get("minNotionalValue") or "0"))
            max_leverage = Decimal(str((inst.get("leverageFilter") or {}).get("maxLeverage") or "0"))
            if tick_size <= 0 or qty_step <= 0:
                raise HTTPException(status_code=502, detail="Bybit instrument constraints are invalid.")
            tickers = await _bybit_get_async(base_url, "/v5/market/tickers", {"category": "linear", "symbol": resolved_symbol})
            ticker_rows = (tickers.get("result") or {}).get("list") or []
            if not ticker_rows:
                raise HTTPException(status_code=502, detail="Bybit ticker fetch failed.")
            row = ticker_rows[0]
            bid = Decimal(str(row.get("bid1Price") or row.get("lastPrice") or "0"))
            ask = Decimal(str(row.get("ask1Price") or row.get("lastPrice") or "0"))
            if bid <= 0 or ask <= 0:
                raise HTTPException(status_code=502, detail="Bybit pricing unavailable.")
            entry = _dec(limit_entry, "entry_price") if order_type == "limit" else (ask if side == "buy" else bid)
            if entry <= 0:
                raise HTTPException(status_code=400, detail="entry_price must be greater than zero.")
            stop_distance = stop_ticks * tick_size
            sl = (entry - stop_distance) if side == "buy" else (entry + stop_distance)
            warnings: List[str] = []
            fallback_taker = Decimal(str(os.getenv("CALCULATOR_BYBIT_TAKER_FEE_FALLBACK", "0.0006") or "0.0006"))
            fallback_maker = Decimal(str(os.getenv("CALCULATOR_BYBIT_MAKER_FEE_FALLBACK", "0.0006") or "0.0006"))
            maker = fallback_maker
            taker = fallback_taker
            if account == "demo":
                maker = fallback_maker
                taker = fallback_taker
            else:
                try:
                    fee_payload = await _bybit_signed_get(
                        base_url=base_url,
                        api_key=api_key,
                        api_secret=api_secret,
                        path="/v5/account/fee-rate",
                        params={"category": "linear", "symbol": resolved_symbol},
                    )
                    fee_row = ((fee_payload.get("result") or {}).get("list") or [{}])[0]
                    maker = Decimal(str(fee_row.get("makerFeeRate") or fallback_maker))
                    taker = Decimal(str(fee_row.get("takerFeeRate") or fallback_taker))
                except Exception as exc:
                    exc_text = str(exc)
                    ret_code_match = re.search(r"retCode=([0-9-]+)", exc_text)
                    ret_code = ret_code_match.group(1) if ret_code_match else "unknown"
                    warnings.append(
                        f"Bybit fee rate unavailable (retCode {ret_code}). Using conservative fallback fees for this quote."
                    )
            open_fee = taker if order_type == "market" else max(maker, taker)
            close_fee = taker
            try:
                aud_cfg = _get_oanda_config("live")
            except Exception:
                aud_cfg = {"base_url": "", "account_id": "", "token": ""}
            try:
                aud_usd = Decimal(str((await _fetch_oanda_mid_prices_batch(cfg=aud_cfg, instruments=["AUD_USD"])).get("AUD_USD") or 0))
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"AUD_USD conversion unavailable: {exc}") from exc
            if aud_usd <= 0:
                raise HTTPException(status_code=502, detail="AUD_USD conversion unavailable.")
            try:
                balance_snapshot = await _fetch_bybit_balance_usdt(account)
            except HTTPException as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Bybit risk sizing dependency failed path=/v5/account/wallet-balance: {exc.detail}",
                ) from exc
            available_usdt = Decimal(str(balance_snapshot.get("available_usdt") or "0"))
            total_equity = Decimal(str(balance_snapshot.get("total_equity") or "0"))
            risk_aud = (available_usdt / aud_usd) * (risk_val / Decimal("100"))
            risk_usdt = risk_aud * aud_usd
            spread_quote = max(Decimal("0"), ask - bid) if order_type == "market" else Decimal("0")
            loss_per_unit = abs(entry - sl) + spread_quote + (entry * open_fee) + (sl * close_fee)
            if loss_per_unit <= 0:
                raise HTTPException(status_code=400, detail="Invalid stop distance produced zero loss per unit.")

            requested_rr_net = None
            effective_rr_net = None
            fee_buffer_r = None
            if target_mode == "rr" and rr_requested is not None:
                desired_net_reward = loss_per_unit * rr_requested
                target_distance = stop_distance * rr_requested
                for _ in range(4):
                    tp_probe = (entry + target_distance) if side == "buy" else (entry - target_distance)
                    close_fee_cost = abs(tp_probe) * close_fee
                    fee_buffer = spread_quote + (entry * open_fee) + close_fee_cost
                    target_distance = desired_net_reward + fee_buffer
                tp = (entry + target_distance) if side == "buy" else (entry - target_distance)
                fee_buffer = spread_quote + (entry * open_fee) + (abs(tp) * close_fee)
                net_reward_per_unit = target_distance - fee_buffer
                requested_rr_net = rr_requested
                effective_rr_net = net_reward_per_unit / loss_per_unit if loss_per_unit > 0 else Decimal("0")
                fee_buffer_r = fee_buffer / loss_per_unit if loss_per_unit > 0 else Decimal("0")
            else:
                target_distance = (tp_ticks or Decimal("0")) * tick_size
                tp = (entry + target_distance) if side == "buy" else (entry - target_distance)

            qty_raw = risk_usdt / loss_per_unit
            raw_notional = qty_raw * entry
            if min_notional > 0 and raw_notional < min_notional:
                raise HTTPException(status_code=400, detail="Calculated notional is below Bybit minimum notional")
            qty = _floor_to_step(qty_raw, qty_step)
            if qty < min_qty:
                raise HTTPException(status_code=400, detail="Calculated quantity is below minimum order quantity")
            notional = qty * entry
            if order_type == "market" and max_mkt_qty > 0 and qty > max_mkt_qty:
                raise HTTPException(status_code=400, detail="Calculated quantity exceeds Bybit max market order quantity")
            if order_type == "limit" and max_qty > 0 and qty > max_qty:
                raise HTTPException(status_code=400, detail="Calculated quantity exceeds Bybit max order quantity")
            if max_leverage > 0:
                est_margin = notional / max_leverage
                available_for_margin = max(available_usdt, total_equity)
                margin_tolerance = Decimal("1.05")
                if available_for_margin > 0 and est_margin > (available_for_margin * margin_tolerance):
                    raise HTTPException(status_code=400, detail="Insufficient Bybit available margin for estimated initial margin")

            total_loss_usdt = qty * loss_per_unit
            reward_usdt = qty * max(Decimal("0"), target_distance - spread_quote - (entry * open_fee) - (abs(tp) * close_fee))
            snapped_entry = _snap_to_increment(entry, tick_size) or entry
            snapped_sl = _snap_to_increment(sl, tick_size) or sl
            snapped_tp = _snap_to_increment(tp, tick_size) or tp
            snapped_target_distance = _snap_to_increment(target_distance, tick_size) or target_distance
            response_payload: Dict[str, object] = {
                "broker": "bybit",
                "symbol": resolved_symbol,
                "tick_size": _fmt_dec(tick_size),
                "entry_price": _fmt_dec(snapped_entry),
                "stop_price": _fmt_dec(snapped_sl),
                "target_price": _fmt_dec(snapped_tp),
                "target_distance": _fmt_dec(snapped_target_distance),
                "quantity": _fmt_dec(qty),
                "notional": _fmt_dec(notional),
                "estimated_fees_or_spread_aud": _fmt_dec(((qty * entry * open_fee) + (qty * sl * close_fee)) / aud_usd),
                "estimated_total_loss_aud": _fmt_dec(total_loss_usdt / aud_usd),
                "estimated_reward_aud": _fmt_dec(max(Decimal("0"), reward_usdt / aud_usd)),
                "display_currency": "USDT",
                "estimated_fees_or_spread": _fmt_dec((qty * entry * open_fee) + (qty * sl * close_fee)),
                "estimated_total_loss": _fmt_dec(total_loss_usdt),
                "estimated_reward": _fmt_dec(max(Decimal("0"), reward_usdt)),
                "rr": _fmt_dec_by_precision((abs(tp - entry) / abs(entry - sl)) if abs(entry - sl) > 0 else Decimal("0"), Decimal("0.01")),
                "target_mode": target_mode,
                "requested_rr_net": _fmt_dec_by_precision(requested_rr_net, Decimal("0.01")) if requested_rr_net is not None else None,
                "effective_rr_net": _fmt_dec_by_precision(effective_rr_net, Decimal("0.01")) if effective_rr_net is not None else None,
                "fee_buffer_r": _fmt_dec_by_precision(fee_buffer_r, Decimal("0.01")) if fee_buffer_r is not None else None,
            }
            if warnings:
                response_payload["warnings"] = warnings

            if webhook_enabled:
                pending_id = existing_pending_id or f"calc_bybit_{uuid4().hex[:16]}"
                webhook_payload = {
                    "asset": "crypto",
                    "account": account,
                    "symbol": resolved_symbol,
                    "action": side,
                    "order_type": order_type,
                    "quantity": _fmt_dec(qty),
                    "entry_price": _fmt_dec(snapped_entry),
                    "planned_entry_price": _fmt_dec(snapped_entry),
                    "stop_loss_price": _fmt_dec(snapped_sl),
                    "planned_stop_price": _fmt_dec(snapped_sl),
                    "take_profit_price": _fmt_dec(snapped_tp),
                    "planned_target_price": _fmt_dec(snapped_tp),
                    "level_anchor_mode": "actual_fill",
                    "timeframe": _normalize_timeframe(payload.get("timeframe") or ""),
                    "is_test_trade": is_test_trade,
                    "pending_webhook_id": pending_id,
                    "webhook_endpoint_url": webhook_endpoint_url,
                    "webhook_origin_host": webhook_origin_host,
                    "webhook_origin_profile": webhook_origin_profile,
                    "webhook_origin_instance_id": webhook_origin_instance_id,
                }
                pending_item = _upsert_pending_webhook(
                    {
                        "id": pending_id,
                        "category": "bybit",
                        "account": account,
                        "instrument": resolved_symbol,
                        "side": side,
                        "order_type": order_type,
                        "entry_price": _fmt_dec(snapped_entry),
                        "stop_loss": _fmt_dec(snapped_sl),
                        "take_profit": _fmt_dec(snapped_tp),
                        "size": _fmt_dec(qty),
                        "timeframe": webhook_payload.get("timeframe") or "",
                        "is_test_trade": is_test_trade,
                        "status": "WAITING",
                        "enabled": True,
                    }
                )
                response_payload["pending_webhook_id"] = pending_item.get("id")
                response_payload["webhook_endpoint"] = "/api/calculator/webhook"
                response_payload["webhook_endpoint_url"] = webhook_endpoint_url
                response_payload["webhook_payload_json"] = json.dumps(webhook_payload, separators=(",", ":"))
                if previous_pending_id and previous_pending_id != pending_id:
                    _delete_pending_webhook(previous_pending_id)
            elif previous_pending_id:
                _delete_pending_webhook(previous_pending_id)

            return JSONResponse(response_payload)

        if asset == "fx":
            try:
                cfg = _get_oanda_config(account)
            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            symbol = normalize_oanda_symbol_query(symbol_in)
            try:
                meta = await _fetch_oanda_instrument_meta(
                    base_url=cfg["base_url"],
                    account_id=cfg["account_id"],
                    api_key=cfg["token"],
                    symbol=symbol,
                    mode=account,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            display_precision = int(meta["displayPrecision"])
            units_precision = int(meta.get("tradeUnitsPrecision", 0))
            min_trade_size = Decimal(str(meta.get("minimumTradeSize") or "0"))
            max_order_units = Decimal(str(meta.get("maximumOrderUnits") or "0"))
            max_position_size = Decimal(str(meta.get("maximumPositionSize") or "0"))
            margin_rate = Decimal(str(meta.get("marginRate") or "0"))
            tick_size = Decimal("1").scaleb(-display_precision)
            try:
                prices = await _fetch_oanda_json(
                    base_url=cfg["base_url"],
                    account_id=cfg["account_id"],
                    api_key=cfg["token"],
                    endpoint=f"/accounts/{{account_id}}/pricing?instruments={symbol}&includeHomeConversions=true",
                    mode=account,
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"OANDA pricing/meta fetch failure: {exc}") from exc
            rows = prices.get("prices") or []
            if not rows:
                raise HTTPException(status_code=502, detail="OANDA pricing/meta fetch failure.")
            row = rows[0]
            bid = Decimal(str(((row.get("bids") or [{}])[0]).get("price") or "0"))
            ask = Decimal(str(((row.get("asks") or [{}])[0]).get("price") or "0"))
            if bid <= 0 or ask <= 0:
                raise HTTPException(status_code=502, detail="OANDA bid/ask unavailable.")
            entry = _dec(limit_entry, "entry_price") if order_type == "limit" else (ask if side == "buy" else bid)
            if entry <= 0:
                raise HTTPException(status_code=400, detail="Bad limit price.")
            sl = (entry - stop_ticks * tick_size) if side == "buy" else (entry + stop_ticks * tick_size)

            summary = await _fetch_oanda_account_summary(account)
            account_home_ccy = str(summary.get("currency") or "").strip().upper()
            quote_ccy = symbol.split("_", 1)[1]
            try:
                gain_factor, loss_factor, _position_value_factor = _get_oanda_quote_home_factors(
                    prices_payload=prices,
                    row=row,
                    quote_ccy=quote_ccy,
                    account_home_ccy=account_home_ccy,
                )
            except ValueError as exc:
                top_level_conversions = prices.get("homeConversions") if isinstance(prices, dict) else []
                available_top_level = sorted(
                    {
                        str(item.get("currency") or "").strip().upper()
                        for item in (top_level_conversions or [])
                        if isinstance(item, dict) and str(item.get("currency") or "").strip()
                    }
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"OANDA pricing response missing usable home conversion for {quote_ccy}. "
                        f"includeHomeConversions=true, top_level_currencies={available_top_level}, "
                        f"row_keys={sorted(row.keys())}"
                    ),
                ) from exc

            risk_input_aud = risk_val
            if risk_mode == "percent":
                nav = Decimal(str(summary.get("nav") or "0"))
                if nav <= 0:
                    raise HTTPException(status_code=502, detail="OANDA NAV unavailable for percent risk.")
                risk_amount_home = nav * (risk_val / Decimal("100"))
            else:
                try:
                    risk_amount_home = await _convert_aud_to_home_currency(risk_input_aud, account_home_ccy, cfg)
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            spread_quote = max(Decimal("0"), ask - bid)
            loss_per_unit_home = (abs(entry - sl) + spread_quote) * loss_factor
            if loss_per_unit_home <= 0:
                raise HTTPException(status_code=400, detail="Invalid stop distance produced zero loss per unit.")

            requested_rr_net = None
            effective_rr_net = None
            fee_buffer_r = None
            if target_mode == "rr" and rr_requested is not None:
                desired_net_reward_home = loss_per_unit_home * rr_requested
                target_distance = (desired_net_reward_home / loss_factor) + spread_quote
                tp = (entry + target_distance) if side == "buy" else (entry - target_distance)
                reward_per_unit_home = max(Decimal("0"), (target_distance - spread_quote) * gain_factor)
                requested_rr_net = rr_requested
                effective_rr_net = reward_per_unit_home / loss_per_unit_home if loss_per_unit_home > 0 else Decimal("0")
                fee_buffer_r = ((spread_quote * loss_factor) / loss_per_unit_home) if loss_per_unit_home > 0 else Decimal("0")
            else:
                target_distance = (tp_ticks or Decimal("0")) * tick_size
                tp = (entry + target_distance) if side == "buy" else (entry - target_distance)

            units_raw = risk_amount_home / loss_per_unit_home
            units = _floor_to_precision(units_raw, units_precision)
            if units < min_trade_size:
                raise HTTPException(status_code=400, detail="Calculated units are below minimum trade size.")
            if max_order_units > 0 and units > max_order_units:
                raise HTTPException(status_code=400, detail="Calculated units exceed OANDA maximumOrderUnits.")
            if max_position_size > 0 and units > max_position_size:
                raise HTTPException(status_code=400, detail="Calculated units exceed OANDA maximumPositionSize.")
            margin_available = Decimal(str(summary.get("marginAvailable") or "0"))
            effective_margin_rate = margin_rate if margin_rate > 0 else Decimal(str(summary.get("marginRate") or "0"))
            estimated_position_value_home = units * entry * _position_value_factor
            estimated_initial_margin_home = estimated_position_value_home * max(Decimal("0"), effective_margin_rate)
            submitted_debug_payload = {
                "submitted_risk_mode": risk_mode,
                "submitted_risk_value": _fmt_dec(risk_val),
                "submitted_stop_loss_ticks": _fmt_dec(stop_ticks),
                "tick_size": _fmt_dec(tick_size),
                "entry_price_used": _fmt_dec_by_precision(entry, tick_size),
                "spread_quote": _fmt_dec(spread_quote),
                "loss_per_unit_home": _fmt_dec(loss_per_unit_home),
                "units_raw": _fmt_dec(units_raw),
                "units_final": _fmt_dec(units),
                "estimated_position_value_home": _fmt_dec(estimated_position_value_home),
                "estimated_initial_margin_home": _fmt_dec(estimated_initial_margin_home),
            }
            if effective_margin_rate > 0 and margin_available > 0:
                if estimated_initial_margin_home > margin_available:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "oanda_margin_insufficient",
                            "message": "Insufficient OANDA marginAvailable for estimated initial margin.",
                            "debug": {
                                **submitted_debug_payload,
                                "required_margin_home": _fmt_dec(estimated_initial_margin_home),
                                "margin_available_home": _fmt_dec(margin_available),
                                "margin_rate": _fmt_dec(effective_margin_rate),
                                "position_value_factor": _fmt_dec(_position_value_factor),
                                "account_currency": account_home_ccy,
                                "risk_input_aud": _fmt_dec(risk_input_aud),
                                "risk_amount_home": _fmt_dec(risk_amount_home),
                            },
                        },
                    )
            spread_home = spread_quote * loss_factor * units
            reward_home = max(Decimal("0"), (abs(tp - entry) - spread_quote) * gain_factor * units)
            response_payload = {
                    "broker": "oanda",
                    "symbol": symbol,
                    "tick_size": _fmt_dec(tick_size),
                    "entry_price": _fmt_dec_by_precision(entry, tick_size),
                    "stop_price": _fmt_dec_by_precision(sl, tick_size),
                    "target_price": _fmt_dec_by_precision(tp, tick_size),
                    "target_distance": _fmt_dec_by_precision(target_distance, tick_size),
                    "quantity": _fmt_dec(units),
                    "notional": _fmt_dec(units * entry),
                    "estimated_fees_or_spread_aud": _fmt_dec(max(Decimal("0"), spread_home)),
                    "estimated_total_loss_aud": _fmt_dec(loss_per_unit_home * units),
                    "estimated_reward_aud": _fmt_dec(reward_home),
                    "display_currency": account_home_ccy,
                    "estimated_fees_or_spread": _fmt_dec(max(Decimal("0"), spread_home)),
                    "estimated_total_loss": _fmt_dec(loss_per_unit_home * units),
                    "estimated_reward": _fmt_dec(reward_home),
                    "account_currency": account_home_ccy,
                    "risk_input_aud": _fmt_dec(risk_input_aud),
                    "risk_amount_home": _fmt_dec(risk_amount_home),
                    "margin_rate": _fmt_dec(effective_margin_rate),
                    "position_value_factor": _fmt_dec(_position_value_factor),
                    "estimated_position_value_home": _fmt_dec(estimated_position_value_home),
                    "estimated_initial_margin_home": _fmt_dec(estimated_initial_margin_home),
                    "margin_available_home": _fmt_dec(margin_available),
                    "submitted_risk_mode": risk_mode,
                    "submitted_risk_value": _fmt_dec(risk_val),
                    "submitted_stop_loss_ticks": _fmt_dec(stop_ticks),
                    "entry_price_used": _fmt_dec_by_precision(entry, tick_size),
                    "spread_quote": _fmt_dec(spread_quote),
                    "loss_per_unit_home": _fmt_dec(loss_per_unit_home),
                    "units_raw": _fmt_dec(units_raw),
                    "units_final": _fmt_dec(units),
                    "rr": _fmt_dec_by_precision((abs(tp - entry) / abs(entry - sl)) if abs(entry - sl) > 0 else Decimal("0"), Decimal("0.01")),
                    "target_mode": target_mode,
                    "requested_rr_net": _fmt_dec_by_precision(requested_rr_net, Decimal("0.01")) if requested_rr_net is not None else None,
                    "effective_rr_net": _fmt_dec_by_precision(effective_rr_net, Decimal("0.01")) if effective_rr_net is not None else None,
                    "fee_buffer_r": _fmt_dec_by_precision(fee_buffer_r, Decimal("0.01")) if fee_buffer_r is not None else None,
                }
            if webhook_enabled:
                pending_id = existing_pending_id or f"calc_oanda_{uuid4().hex[:16]}"
                webhook_payload = {
                    "asset": "fx",
                    "account": account,
                    "symbol": symbol,
                    "action": side,
                    "order_type": order_type,
                    "quantity": _fmt_dec(units),
                    "entry_price": _fmt_dec(entry),
                    "planned_entry_price": _fmt_dec(entry),
                    "stop_loss_price": _fmt_dec(sl),
                    "planned_stop_price": _fmt_dec(sl),
                    "take_profit_price": _fmt_dec(tp),
                    "planned_target_price": _fmt_dec(tp),
                    "timeframe": _normalize_timeframe(payload.get("timeframe") or ""),
                    "is_test_trade": is_test_trade,
                    "pending_webhook_id": pending_id,
                    "webhook_endpoint_url": webhook_endpoint_url,
                    "webhook_origin_host": webhook_origin_host,
                    "webhook_origin_profile": webhook_origin_profile,
                    "webhook_origin_instance_id": webhook_origin_instance_id,
                }
                pending_item = _upsert_pending_webhook(
                    {
                        "id": pending_id,
                        "category": "oanda",
                        "account": account,
                        "instrument": symbol,
                        "side": side,
                        "order_type": order_type,
                        "entry_price": _fmt_dec(entry),
                        "stop_loss": _fmt_dec(sl),
                        "take_profit": _fmt_dec(tp),
                        "size": _fmt_dec(units),
                        "timeframe": webhook_payload.get("timeframe") or "",
                        "is_test_trade": is_test_trade,
                        "status": "WAITING",
                        "enabled": True,
                    }
                )
                response_payload["pending_webhook_id"] = pending_item.get("id")
                response_payload["webhook_endpoint"] = "/api/calculator/webhook"
                response_payload["webhook_endpoint_url"] = webhook_endpoint_url
                response_payload["webhook_payload_json"] = json.dumps(webhook_payload, separators=(",", ":"))
                if previous_pending_id and previous_pending_id != pending_id:
                    _delete_pending_webhook(previous_pending_id)
            elif previous_pending_id:
                _delete_pending_webhook(previous_pending_id)
            return JSONResponse(response_payload)

        raise HTTPException(status_code=400, detail="asset must be crypto or fx.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _get_oanda_quote_home_factors(
    prices_payload: Dict[str, object],
    row: Dict[str, object],
    quote_ccy: str,
    account_home_ccy: str,
) -> Tuple[Decimal, Decimal, Decimal]:
    quote_code = str(quote_ccy or "").strip().upper()
    home_code = str(account_home_ccy or "").strip().upper()
    if quote_code and home_code and quote_code == home_code:
        one = Decimal("1")
        return one, one, one

    def _pick_from_home_conversions(conversions: object) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
        if not isinstance(conversions, list):
            return None
        for item in conversions:
            if not isinstance(item, dict):
                continue
            if str(item.get("currency") or "").strip().upper() != quote_code:
                continue
            gain = Decimal(str(item.get("accountGain") or "0"))
            loss = Decimal(str(item.get("accountLoss") or "0"))
            position_value = Decimal(str(item.get("positionValue") or "0"))
            if gain > 0 and loss > 0 and position_value > 0:
                return gain, loss, position_value
        return None

    top_level = _pick_from_home_conversions(prices_payload.get("homeConversions"))
    if top_level is not None:
        return top_level

    row_level = _pick_from_home_conversions(row.get("homeConversions"))
    if row_level is not None:
        return row_level

    deprecated = row.get("quoteHomeConversionFactors")
    if isinstance(deprecated, dict):
        gain = Decimal(str(deprecated.get("positiveUnits") or "0"))
        loss = Decimal(str(deprecated.get("negativeUnits") or "0"))
        if gain > 0 and loss > 0:
            return gain, loss, gain

    raise ValueError(f"missing usable conversion factors for {quote_code or quote_ccy}")




@app.post("/api/calculator/webhook")
async def calculator_webhook(request: Request, payload: Dict[str, object] = Body(default={})) -> JSONResponse:
    if isinstance(request, dict) and (not payload):
        payload = request
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/calculator/webhook",
                "raw_path": b"/api/calculator/webhook",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 0),
                "server": ("localhost", 80),
            }
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    asset = str(payload.get("asset") or "crypto").strip().lower()
    request_id = f"calc-webhook-{uuid4().hex[:12]}"
    attempt = _record_webhook_attempt(
        {
            "request_id": request_id,
            "pending_webhook_id": pending_id or None,
            "received_at": _utc_now_iso(),
            "asset": asset,
            "broker": "bybit" if asset == "crypto" else ("oanda" if asset == "fx" else None),
            "account": str(payload.get("account") or "").strip().lower() or None,
            "symbol": str(payload.get("symbol") or "").strip().upper() or None,
            "action": str(payload.get("action") or payload.get("side") or "").strip().lower() or None,
            "status": "RECEIVED",
            "request_host": request.headers.get("host"),
            "request_url": str(request.url),
            "client": request.client.host if request.client else None,
            "payload_origin_host": payload.get("webhook_origin_host"),
            "payload_endpoint_url": payload.get("webhook_endpoint_url"),
            "payload": dict(payload),
        }
    )

    try:
        _assert_pending_webhook_executable(payload)
        if pending_id:
            _update_pending_webhook(
                pending_id,
                {
                    "status": "TRIGGERING",
                    "triggered_at": _utc_now_iso(),
                    "request_id": request_id,
                },
            )
            _update_webhook_attempt(
                request_id,
                {
                    "status": "TRIGGERING",
                },
            )
        canonical = {
            "account": payload.get("account"),
            "symbol": payload.get("symbol"),
            "action": payload.get("action") or payload.get("side"),
            "order_type": payload.get("order_type"),
            "entry_price": payload.get("entry_price") or payload.get("planned_entry_price"),
            "stop_loss_price": payload.get("stop_loss_price") or payload.get("planned_stop_price"),
            "take_profit_price": payload.get("take_profit_price") or payload.get("planned_target_price"),
            "quantity": payload.get("quantity"),
            "timeframe": payload.get("timeframe"),
            "is_test_trade": _normalize_test_trade_flag(
                payload.get("is_test_trade", payload.get("test_trade", payload.get("test")))
            ),
            "pending_webhook_id": pending_id or None,
            "level_anchor_mode": payload.get("level_anchor_mode") or "actual_fill",
            "planned_entry_price": payload.get("planned_entry_price") or payload.get("entry_price"),
            "planned_stop_price": payload.get("planned_stop_price") or payload.get("stop_loss_price"),
            "planned_target_price": payload.get("planned_target_price") or payload.get("take_profit_price"),
        }

        if asset == "crypto":
            result = await _place_bybit_order(
                canonical,
                request_id=request_id,
            )
            bybit_result = result.get("order") if isinstance(result, dict) else {}
            _invalidate_open_orders_cache()
            live_state_present = False
            try:
                live_payload = await list_open_orders(force=True)
                live_body = json.loads(live_payload.body.decode("utf-8"))
                live_items = live_body.get("items") if isinstance(live_body, dict) else []
                if isinstance(live_items, list):
                    order_id = str((bybit_result or {}).get("orderId") or "").strip()
                    order_link_id = str((bybit_result or {}).get("orderLinkId") or "").strip()
                    symbol = str(canonical.get("symbol") or "").strip().upper()
                    account = str(canonical.get("account") or "").strip().lower()
                    category = "linear"
                    for row in live_items:
                        if not isinstance(row, dict):
                            continue
                        if str(row.get("broker") or "").strip().lower() != "bybit":
                            continue
                        if account and str(row.get("account") or "").strip().lower() != account:
                            continue
                        if category and str(row.get("category") or "").strip().lower() != category:
                            continue
                        if symbol and str(row.get("instrument") or "").strip().upper() != symbol:
                            continue
                        row_order_id = str(row.get("id") or row.get("order_id") or "").strip()
                        row_order_link_id = str(row.get("order_link_id") or "").strip()
                        if order_id and row_order_id == order_id:
                            live_state_present = True
                            break
                        if order_link_id and row_order_link_id == order_link_id:
                            live_state_present = True
                            break
                        row_type = str(row.get("type") or "").strip().lower()
                        if row_type == "position":
                            live_state_present = True
                            break
            except Exception:
                live_state_present = False
            _update_webhook_attempt(
                request_id,
                {
                    "status": "BYBIT_ACCEPTED" if live_state_present else "BYBIT_ACCEPTED_NO_LIVE_STATE_YET",
                    "bybit_ret_code": 0,
                    "bybit_ret_msg": "OK",
                    "bybit_result": bybit_result if isinstance(bybit_result, dict) else {},
                    "order_id": str((bybit_result or {}).get("orderId") or "").strip() or None,
                    "order_link_id": str(
                        (bybit_result or {}).get("orderLinkId") or ""
                    ).strip()
                    or None,
                },
            )
            if pending_id:
                _consume_pending_webhook(
                    pending_id,
                    request_id=request_id,
                    reason="order_accepted",
                )
            return JSONResponse({"ok": True, "broker": "bybit", "result": result})
        if asset == "fx":
            result = await _place_oanda_order(
                canonical,
                request_id=request_id,
            )
            _update_webhook_attempt(request_id, {"status": "CONSUMED"})
            return JSONResponse({"ok": True, "broker": "oanda", "result": result})

        raise HTTPException(status_code=400, detail="asset must be crypto or fx.")
    except ValueError as exc:
        if "pending_webhook_id is required" in str(exc):
            _update_webhook_attempt(
                request_id,
                {"status": "PENDING_NOT_FOUND", "error": str(exc)},
            )
            return JSONResponse(
                {
                    "ok": False,
                    "code": "PENDING_WEBHOOK_NOT_FOUND",
                    "message": "Webhook reached this server, but the pending_webhook_id does not exist on this instance.",
                    "pending_webhook_id": pending_id or None,
                    "current_host": request.headers.get("host"),
                    "payload_origin_host": payload.get("webhook_origin_host"),
                },
                status_code=409,
            )
        if "Pending webhook missing or no longer active." in str(exc):
            _update_webhook_attempt(
                request_id,
                {"status": "PENDING_NOT_FOUND", "error": str(exc)},
            )
            if pending_id:
                _update_pending_webhook(
                    pending_id,
                    {
                        "status": "PENDING_NOT_FOUND",
                        "last_error": str(exc),
                        "last_attempt_at": _utc_now_iso(),
                        "request_id": request_id,
                    },
                )
            return JSONResponse(
                {
                    "ok": False,
                    "code": "PENDING_WEBHOOK_NOT_FOUND",
                    "message": "Webhook reached this server, but the pending_webhook_id does not exist on this instance.",
                    "pending_webhook_id": pending_id or None,
                    "current_host": request.headers.get("host"),
                    "payload_origin_host": payload.get("webhook_origin_host"),
                },
                status_code=409,
            )
        raise HTTPException(status_code=400, detail=f"Calculator webhook execution failed: {exc}") from exc
    except BybitOrderRejected as exc:
        if pending_id:
            _update_pending_webhook(
                pending_id,
                {
                    "status": "BYBIT_REJECTED",
                    "last_error": str(exc),
                    "bybit_ret_code": exc.ret_code,
                    "bybit_ret_msg": exc.ret_msg,
                    "last_attempt_at": _utc_now_iso(),
                    "request_id": request_id,
                },
            )
        _update_webhook_attempt(
            request_id,
            {
                "status": "BYBIT_REJECTED",
                "bybit_request": exc.request_body,
                "bybit_http_status": exc.http_status,
                "bybit_ret_code": exc.ret_code,
                "bybit_ret_msg": exc.ret_msg,
                "bybit_ret_ext_info": exc.ret_ext_info,
                "bybit_result": exc.result,
                "error": str(exc),
                "stack": traceback.format_exc(),
            },
        )
        if pending_id:
            _upsert_trade_context(
                {
                    "pending_webhook_id": pending_id,
                    "status": "FAILED",
                    "failure_stage": "bybit_order_create",
                    "consume_reason": "webhook_received",
                    "request_id": request_id,
                    "last_error": str(exc),
                    "last_attempt_at": _utc_now_iso(),
                }
            )
        raise HTTPException(status_code=400, detail=f"Calculator webhook execution failed: {exc}") from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        message = str(exc)
        if pending_id:
            _update_pending_webhook(
                pending_id,
                {
                    "status": "ORDER_CREATED_TPSL_FAILED" if "created but TP/SL application failed" in message else "FAILED_BEFORE_SUBMIT",
                    "last_error": message,
                    "last_attempt_at": _utc_now_iso(),
                    "request_id": request_id,
                },
            )
        if "created but TP/SL application failed" in message:
            _update_webhook_attempt(
                request_id,
                {
                    "status": "ORDER_CREATED_TPSL_FAILED",
                    "error": message,
                    "stack": traceback.format_exc(),
                },
            )
        else:
            _update_webhook_attempt(
                request_id,
                {
                    "status": "FAILED_BEFORE_SUBMIT",
                    "error": message,
                    "stack": traceback.format_exc(),
                },
            )
        if pending_id:
            _upsert_trade_context(
                {
                    "pending_webhook_id": pending_id,
                    "status": "FAILED",
                    "failure_stage": "bybit_order_create",
                    "consume_reason": "webhook_received",
                    "request_id": request_id,
                    "last_error": message,
                    "last_attempt_at": _utc_now_iso(),
                },
            )
        raise HTTPException(status_code=400, detail=f"Calculator webhook execution failed: {message}") from exc
    except Exception as exc:
        if pending_id:
            _update_pending_webhook(
                pending_id,
                {
                    "status": "FAILED_BEFORE_SUBMIT",
                    "last_error": str(exc),
                    "last_attempt_at": _utc_now_iso(),
                    "request_id": request_id,
                },
            )
        _update_webhook_attempt(
            request_id,
            {
                "status": "FAILED_BEFORE_SUBMIT",
                "error": str(exc),
                "stack": traceback.format_exc(),
            },
        )
        if pending_id:
            _upsert_trade_context(
                {
                    "pending_webhook_id": pending_id,
                    "status": "FAILED",
                    "failure_stage": "bybit_order_create",
                    "consume_reason": "webhook_received",
                    "request_id": request_id,
                    "last_error": str(exc),
                    "last_attempt_at": _utc_now_iso(),
                },
            )
        raise HTTPException(status_code=400, detail=f"Calculator webhook execution failed: {exc}") from exc


@app.get("/api/calculator/webhook-attempts")
async def calculator_webhook_attempts(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
    attempts = _load_webhook_attempts()
    items = list(reversed(attempts))[: int(limit)]
    return JSONResponse({"items": items, "updated_at": _utc_now_iso()})


@app.post("/api/calculator/submit")
async def calculator_submit(payload: Dict[str, object] = Body(default={})) -> JSONResponse:
    asset = str(payload.get("asset") or "").strip().lower()
    canonical = {
        "account": payload.get("account"),
        "symbol": payload.get("symbol"),
        "action": payload.get("action") or payload.get("side"),
        "order_type": payload.get("order_type"),
        "entry_price": payload.get("entry_price"),
        "stop_loss_price": payload.get("stop_loss_price"),
        "take_profit_price": payload.get("take_profit_price"),
        "quantity": payload.get("quantity"),
        "timeframe": payload.get("timeframe"),
        "is_test_trade": _normalize_test_trade_flag(
            payload.get("is_test_trade", payload.get("test_trade", payload.get("test")))
        ),
    }
    request_id = f"calc-{uuid4().hex[:12]}"
    try:
        if asset == "crypto":
            result = await _place_bybit_order(
                canonical,
                request_id=request_id,
            )
            return JSONResponse({"ok": True, "broker": "bybit", "result": result})
        if asset == "fx":
            result = await _place_oanda_order(
                canonical,
                request_id=request_id,
            )
            return JSONResponse({"ok": True, "broker": "oanda", "result": result})
        raise HTTPException(status_code=400, detail="asset must be crypto or fx.")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Order submit failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order submit failed: {exc}") from exc


HISTORY_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Merged History Export</title>
  <style>
    body { margin:0; background:#0b1220; color:#e2e8f0; font-family:Inter,system-ui,sans-serif; }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 18px; }
    .panel { background:#111827; border:1px solid #1f2937; border-radius:14px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,0.25); }
    .row { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin-bottom:12px; }
    label { display:flex; flex-direction:column; gap:6px; font-weight:700; color:#cbd5e1; }
    select, button { background:#0f172a; color:#e2e8f0; border:1px solid #334155; border-radius:10px; padding:8px 10px; }
    button { cursor:pointer; font-weight:800; }
    .periods { display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:8px; margin:12px 0; }
    .period-btn.active { background:#2563eb; border-color:#3b82f6; }
    .status { color:#93c5fd; min-height:1.2em; }
    .muted { color:#94a3b8; font-size:0.9rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h2 style="margin-top:0">History Export</h2>
      <p class="muted">Unified quick-range history exporter for Bybit, OANDA, and CoinSpot.</p>

      <div class="row">
        <label>Broker
          <select id="history-broker">
            <option value="bybit">Bybit</option>
            <option value="oanda">OANDA</option>
            <option value="coinspot">CoinSpot</option>
          </select>
        </label>
        <label id="history-account-wrap">Account
          <select id="history-account">
            <option value="demo">Demo</option>
            <option value="live">Live</option>
          </select>
        </label>
        <button id="history-export" type="button">Export Selected Period</button>
      </div>

      <div class="periods" id="history-periods">
        <button class="period-btn" type="button" data-kind="days" data-value="7">7D</button>
        <button class="period-btn active" type="button" data-kind="days" data-value="30">30D</button>
        <button class="period-btn" type="button" data-kind="days" data-value="60">60D</button>
        <button class="period-btn" type="button" data-kind="days" data-value="90">90D</button>
        <button class="period-btn" type="button" data-kind="days" data-value="180">180D</button>
        <button class="period-btn" type="button" data-kind="days" data-value="365">365D</button>
        <button class="period-btn" type="button" data-kind="period" data-value="3y">3Y</button>
        <button class="period-btn" type="button" data-kind="complete" data-value="1">Complete</button>
      </div>

      <div class="status" id="history-status">Select broker/account/period and press Export.</div>
      <div class="muted" id="history-result"></div>
    </div>
  </div>
  <script src="/static/history_page.js"></script>
</body>
</html>"""

MERGED_MONITOR_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Merged Scanner Monitor</title>
  <style>
    body { margin:0; background:#0b1220; color:#e2e8f0; font-family:Inter,system-ui,sans-serif; }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 18px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); gap:16px; }
    .panel { background:#111827; border:1px solid #1f2937; border-radius:14px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,.25); }
    .row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:12px; }
    .badge { display:inline-flex; align-items:center; border-radius:999px; padding:4px 10px; font-size:.85rem; background:#1f2937; color:#cbd5e1; min-height:28px; }
    label { display:flex; flex-direction:column; gap:6px; color:#cbd5e1; font-weight:700; }
    input, select, button { background:#0f172a; color:#e2e8f0; border:1px solid #334155; border-radius:10px; padding:8px 10px; }
    button { font-weight:700; cursor:pointer; }
    .meta { color:#94a3b8; margin:4px 0 0; font-size:.9rem; }
    .notice { margin: 8px 0 14px; color:#bfdbfe; background:#0f172a; border:1px solid #1e3a8a; border-radius:10px; padding:10px 12px; }
    .settings-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-bottom:12px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h2 style="margin-top:0">Scanner Monitor</h2>
    <p class="meta">Local merged controls for Bybit and OANDA scanners.</p>
    <p class="notice">This page polls local scanner status every 2 seconds. Closing this tab only stops these status requests/log lines; scanner processes keep running independently.</p>
    <div class="grid">
      <section class="panel" id="bybit-panel">
        <h3 style="margin-top:0">Bybit monitor controls</h3>
        <div class="row">
          <span id="bybit-status" class="badge">Checking…</span>
        </div>
        <p class="meta" id="bybit-health">Phase: — | Heartbeat: — | Fresh: — | PID alive: —</p>
        <div class="settings-grid">
          <label>Wait between scans (seconds)
            <input id="bybit-wait-seconds" type="number" min="1" step="1"/>
          </label>
          <label>Alert threshold (%)
            <input id="bybit-threshold" type="number" min="0" step="0.01"/>
          </label>
        </div>
        <div class="row">
          <button id="bybit-save-settings" type="button">Save</button>
          <button id="bybit-reload-settings" type="button">Reset / Reload</button>
          <button id="bybit-test-alert" type="button">Telegram test</button>
          <span id="bybit-settings-status" class="badge">&nbsp;</span>
        </div>
        <div id="bybit-custom-alerts"></div>
      </section>
      <section class="panel" id="oanda-panel">
        <h3 style="margin-top:0">OANDA monitor controls</h3>
        <div class="row">
          <span id="oanda-status" class="badge">Checking…</span>
        </div>
        <p class="meta" id="oanda-health">Phase: — | Heartbeat: — | Fresh: — | PID alive: —</p>
        <div class="settings-grid">
          <label>Wait between scans (seconds)
            <input id="oanda-wait-seconds" type="number" min="1" step="1"/>
          </label>
          <label>Alert threshold (%)
            <input id="oanda-threshold" type="number" min="0" step="0.01"/>
          </label>
        </div>
        <div class="row">
          <button id="oanda-save-settings" type="button">Save</button>
          <button id="oanda-reload-settings" type="button">Reset / Reload</button>
          <button id="oanda-test-alert" type="button">Telegram test</button>
          <span id="oanda-settings-status" class="badge">&nbsp;</span>
        </div>
        <div id="oanda-custom-alerts"></div>
      </section>
    </div>
  </div>
  <script src="{{MERGED_MONITOR_JS_URL}}"></script>
</body>
</html>"""

OPEN_ORDERS_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Open Orders and Positions</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 20px; background: #0b1220; color: #e5e7eb; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    .wrap { max-width: 1500px; margin: 0 auto; }
    .panel { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; }
    .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
    .status { display: inline-flex; align-items: center; border-radius: 999px; border: 1px solid #334155; padding: 3px 10px; font-size: 12px; color: #93c5fd; background: #0f172a; min-height: 24px; }
    .btn { border: 1px solid #334155; border-radius: 8px; background: #0f172a; color: #e5e7eb; padding: 7px 12px; cursor: pointer; }
    .btn:hover { background: #1e293b; }
    .table-wrap { overflow: auto; border: 1px solid #1f2937; border-radius: 10px; background: #0b1220; }
    table { border-collapse: collapse; width: 100%; min-width: 1400px; }
    th, td { padding: 8px 10px; border-bottom: 1px solid #1f2937; white-space: nowrap; text-align: left; font-size: 13px; }
    th { position: sticky; top: 0; z-index: 2; background: #0f172a; color: #93c5fd; }
    tr:hover td { background: #0f172a; }
    .muted { color: #94a3b8; font-size: 13px; }
    .action-btn { border: 1px solid #334155; border-radius: 8px; background: #1f2937; color: #e5e7eb; padding: 5px 10px; cursor: pointer; }
    .action-btn[disabled] { opacity: .6; cursor: default; }
    .error-box { display: none; margin-bottom: 10px; border: 1px solid #7f1d1d; background: #3f0d12; color: #fecaca; border-radius: 10px; padding: 10px 12px; }
    .error-box ul { margin: 8px 0 0; padding-left: 20px; }
    #open-orders-empty { margin-top: 10px; display: none; }
    .subpanel-title { margin: 14px 0 8px; color:#93c5fd; font-size: 14px; font-weight: 700; }
    .mini-table-wrap { overflow: auto; border: 1px solid #1f2937; border-radius: 10px; background: #0b1220; margin-top: 6px; }
    table.mini { min-width: 1100px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h2 style="margin:0 0 12px 0;">Open Orders and Positions</h2>
    <div class="panel">
      <div class="toolbar">
        <button id="refresh-btn" class="btn" type="button">Refresh</button>
        <span id="open-orders-status" class="status">Idle</span>
      </div>
      <div id="open-orders-errors" class="error-box">
        <div><strong>Source errors</strong></div>
        <ul></ul>
      </div>
      <div id="open-orders-empty" class="muted">No open orders, positions, or pending webhooks.</div>
      <div class="table-wrap">
        <table id="open-orders-table">
          <thead>
            <tr>
              <th></th>
              <th>Broker</th>
              <th>Account</th>
              <th>Category</th>
              <th>Instrument</th>
              <th>Timeframe</th>
              <th>Test</th>
              <th>Type</th>
              <th>Side</th>
              <th>Size</th>
              <th>Entry / Order</th>
              <th>Current / Trigger</th>
              <th>Stop Loss</th>
              <th>Take Profit</th>
              <th>Leverage / Margin</th>
              <th>Opened</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="subpanel-title">Recent Webhook Attempts</div>
      <div class="mini-table-wrap">
        <table id="webhook-attempts-table" class="mini">
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Account</th>
              <th>Status</th>
              <th>retCode</th>
              <th>retMsg</th>
              <th>Request ID</th>
              <th>Pending ID</th>
              <th>Error</th>
              <th>Host</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
  <script src="{{OPEN_ORDERS_JS_URL}}"></script>
</body>
</html>"""


@app.get("/merged/history", response_class=HTMLResponse)
async def merged_history_page() -> str:
    return HISTORY_PAGE_TEMPLATE


@app.get("/merged/monitor")
async def merged_monitor_page() -> Response:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/merged/monitor")
    monitor_js_version = quote(str(os.getenv("APP_BUILD_STAMP") or os.getenv("RENDER_GIT_COMMIT") or app.version), safe="")
    page = MERGED_MONITOR_TEMPLATE.replace("{{MERGED_MONITOR_JS_URL}}", f"/static/merged_monitor.js?v={monitor_js_version}")
    return HTMLResponse(page)


@app.get("/merged/bounce-trader")
async def merged_bounce_page() -> Response:
    return RedirectResponse(url="/apps/bybit_trigger_bounce_trader", status_code=307)


@app.get("/merged/open-orders", response_class=HTMLResponse)
async def merged_open_orders_page() -> HTMLResponse:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/merged/open-orders")  # type: ignore[return-value]
    script_version = quote(str(os.getenv("APP_BUILD_STAMP") or os.getenv("RENDER_GIT_COMMIT") or app.version), safe="")
    page = OPEN_ORDERS_TEMPLATE.replace("{{OPEN_ORDERS_JS_URL}}", f"/static/open_orders.js?v={script_version}")
    return HTMLResponse(page)

@app.get("/scripts/view/{script_name:path}", response_class=HTMLResponse)
async def script_view_page(script_name: str) -> str:
    try:
        script = script_manager.get(script_name)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Script not found") from exc
        raise

    return (
        SCRIPT_PAGE_TEMPLATE.replace("{script_name}", html.escape(script.name))
        .replace("{has_ui}", "true" if script.name in WEB_APPS else "false")
        .replace("{log_url}", f"/logs/view/{_encoded_script_name(script.name)}")
    )



@app.get("/bybit-history")
@app.get("/bybit-history/")
async def legacy_bybit_history(request: Request) -> Response:
    query = request.url.query
    suffix = f"&{query}" if query else ""
    return RedirectResponse(url=f"/merged/history?broker=bybit{suffix}", status_code=307)


@app.get("/oanda-history")
@app.get("/oanda-history/")
async def legacy_oanda_history(request: Request) -> Response:
    query = request.url.query
    suffix = f"&{query}" if query else ""
    return RedirectResponse(url=f"/merged/history?broker=oanda{suffix}", status_code=307)


@app.get("/coinspot-history")
@app.get("/coinspot-history/")
async def legacy_coinspot_history(request: Request) -> Response:
    query = request.url.query
    suffix = f"&{query}" if query else ""
    return RedirectResponse(url=f"/merged/history?broker=coinspot{suffix}", status_code=307)


@app.get("/trading-journal", response_class=HTMLResponse)
async def trading_journal_page() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\"/>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
  <title>Trading Journal</title>
  <style>
    body { background:#0b1220; color:#e5e7eb; font-family:system-ui,sans-serif; margin:0; }
    .wrap { max-width: 1400px; margin: 0 auto; padding: 16px; }
    .toolbar, .balances, .table-wrap { background:#111827; border:1px solid #1f2937; border-radius:12px; }
    .table-shell { background:#111827; border:1px solid #1f2937; border-radius:12px; padding:8px; }
    .hscroll-top { overflow-x:auto; overflow-y:hidden; height:14px; margin-bottom:6px; }
    .hscroll-top > div { height:1px; }
    .toolbar { display:flex; gap:8px; align-items:center; padding:12px; margin-bottom:12px; }
    .toolbar input { flex:1; background:#0f172a; color:#e5e7eb; border:1px solid #334155; border-radius:8px; padding:8px 10px; }
    .toolbar button { background:#2563eb; color:white; border:0; border-radius:8px; padding:8px 12px; cursor:pointer; }
    .toolbar.compact { padding:6px 10px; gap:6px; margin-bottom:10px; }
    .toolbar.compact input { flex:0 1 520px; max-width:520px; padding:6px 8px; }
    .toolbar.compact button { padding:6px 10px; }
    .toolbar button[disabled] { opacity:0.6; cursor:not-allowed; }
    .balances { padding:8px; margin-bottom:10px; display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:8px; }
    .hidden { display:none !important; }
    .bal-card { background:#0f172a; border:1px solid #1f2937; border-radius:10px; padding:8px; }
    .table-wrap { padding:8px; overflow:auto; max-height:70vh; position:relative; }
    #tj-cal-view, #tj-equity-view { padding:8px; }
    .cal-nav { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
    .cal-title { font-weight:700; color:#cbd5e1; }
    .cal-grid { display:grid; grid-template-columns:repeat(7, minmax(0, 1fr)); gap:8px; }
    .cal-dow { color:#93c5fd; font-size:12px; padding:4px 6px; text-transform:uppercase; letter-spacing:0.04em; }
    .cal-day { min-height:128px; background:#0f172a; border:1px solid #1f2937; border-radius:10px; padding:10px; }
    .cal-day.empty { opacity:0.45; }
    .cal-day.has-trades { border-color:#2563eb; box-shadow:0 0 0 1px rgba(37,99,235,0.20) inset; }
    .cal-day.pnl-pos { background: rgba(34,197,94,0.10); border-color: rgba(34,197,94,0.55); box-shadow:0 0 0 1px rgba(34,197,94,0.22) inset; }
    .cal-day.pnl-neg { background: rgba(239,68,68,0.10); border-color: rgba(239,68,68,0.55); box-shadow:0 0 0 1px rgba(239,68,68,0.22) inset; }
    .cal-day.pnl-flat { background: rgba(148,163,184,0.06); border-color:#334155; box-shadow:0 0 0 1px rgba(148,163,184,0.16) inset; }
    .cal-day-num { font-size:14px; color:#cbd5e1; margin-bottom:8px; }
    .cal-lines { display:flex; flex-direction:column; gap:4px; font-size:14px; line-height:1.2; }
    .cal-lines .muted { font-size:13px; }
    .equity-card { background:#0f172a; border:1px solid #1f2937; border-radius:10px; padding:10px; margin-bottom:10px; }
    .equity-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
    .equity-canvas { width:100%; height:220px; display:block; background:#0b1220; border:1px solid #1f2937; border-radius:8px; }
    table { width:100%; border-collapse:collapse; min-width:1200px; }
    th, td { padding:10px 8px; border-bottom:1px solid #1f2937; white-space:nowrap; }
    th { color:#93c5fd; text-align:left; position:sticky; top:0; background:#111827; z-index:5; }
    tr:hover td { background:#0f172a; }
    .muted { color:#94a3b8; }
    .pill { border:1px solid #334155; border-radius:999px; padding:2px 8px; font-size:12px; }
    .num.pos { color:#86efac; }
    .num.neg { color:#fca5a5; }
    .btn-danger { background:#b91c1c !important; }
    .tj-modal { position:fixed; inset:0; display:none; align-items:center; justify-content:center; background:rgba(2,6,23,0.75); z-index:10001; }
    .tj-modal.open { display:flex; }
    .tj-modal-card { width:min(920px, calc(100vw - 24px)); max-height:calc(100vh - 24px); overflow:auto; background:#111827; border:1px solid #334155; border-radius:12px; padding:14px; }
    .tj-form-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
    .tj-form-field { display:flex; flex-direction:column; gap:4px; }
    .tj-form-field input, .tj-form-field select, .tj-form-field textarea { background:#0f172a; color:#e5e7eb; border:1px solid #334155; border-radius:8px; padding:8px; }
    .tj-form-field textarea { min-height:90px; resize:vertical; }
    .tj-form-actions { margin-top:10px; display:flex; gap:8px; justify-content:flex-end; }
    #tj-editor-error { color:#fca5a5; margin-top:8px; min-height:1.2em; }

    .loading-overlay { position:fixed; inset:0; background:rgba(11,18,32,0.92); display:flex; align-items:center; justify-content:center; z-index:9999; }
    .loading-panel { width:min(520px, calc(100% - 32px)); background:#111827; border:1px solid #1f2937; border-radius:14px; padding:16px; }
    .loading-bar { height:10px; background:#0f172a; border:1px solid #1f2937; border-radius:999px; overflow:hidden; }
    #tj-loading-bar { height:100%; width:0%; background:#2563eb; }
  </style>
</head>
<body>
  <div id="tj-loading" class="loading-overlay">
    <div class="loading-panel">
      <div style="font-weight:700;margin-bottom:6px;">Loading Trading Journal</div>
      <div id="tj-loading-text" class="muted" style="margin-bottom:10px;">Starting…</div>
      <div class="loading-bar"><div id="tj-loading-bar"></div></div>
      <div id="tj-loading-pct" class="muted" style="margin-top:8px; text-align:right;">0%</div>
    </div>
  </div>

  <div class="wrap">
    <div class="toolbar" style="margin-bottom:8px;">
      <button id="tj-view-trades-btn">All trades</button>
      <button id="tj-view-inst-btn">Instrument averages</button>
      <button id="tj-view-cal-btn">P/L calendar</button>
      <button id="tj-view-equity-btn">Equity curve</button>
    </div>
    <div class="toolbar compact">
      <input id="tj-filter" placeholder="Filter symbol / account / source (e.g. EURUSD, BTCUSDT, oanda, bybit demo)" />
      <button id="tj-filter-btn">Filter</button>
      <button id="tj-clear-btn">Clear</button>
      <button id="tj-add-btn">Add trade</button>
      <button id="tj-sync-btn">Sync now</button>
      <span id="tj-status" class="muted"></span>
    </div>
    <div id="tj-quick-filters" class="toolbar" style="padding:8px 12px; margin-top:-6px; margin-bottom:12px; flex-wrap:wrap;">
      <button id="btn-errors" class="tj-chip" data-flag="errors">Errors only</button>
      <button id="btn-breakeven" class="tj-chip" data-flag="breakeven">Breakeven only</button>
      <button id="btn-held-news" class="tj-chip" data-flag="held_news">Held through news</button>
      <button id="btn-spiked-out" class="tj-chip" data-flag="spiked_out">Spiked out</button>
      <button id="btn-early-close" class="tj-chip" data-flag="early_close">Early close</button>
    </div>
    <div id="tj-stats" class="balances"></div>
    <div id="tj-balances" class="balances"></div>
    <div class="table-shell">
      <div class="toolbar compact" style="margin:0 0 6px 0; padding:0;">
        <button id="tj-export-btn">Export shown trades</button>
      </div>
      <div id="tj-top-scroll" class="hscroll-top"><div></div></div>
      <div id="tj-trades-wrap" class="table-wrap">
      <table id="tj-table">
        <thead>
          <tr>
            <th data-sort="open_time">Open Time</th>
            <th data-sort="close_time">Close Time</th>
            <th data-sort="account_label">Account</th>
            <th data-sort="symbol">Symbol</th>
            <th data-sort="side">Side</th>
            <th data-sort="timeframe">Timeframe</th>
            <th data-sort="is_test_trade">Test</th>
            <th data-sort="setup">Setup</th>
            <th data-sort="qty">Qty</th>
            <th data-sort="entry_price">Entry</th>
            <th data-sort="exit_price">Exit</th>
            <th data-sort="stop_loss">Stop Loss</th>
            <th data-sort="take_profit">Target</th>
            <th data-sort="commission">Commission</th>
            <th data-sort="net_profit">Net Profit</th>
            <th data-sort="profit_pct">Profit %</th>
            <th data-sort="r_multiple">R-Multiple</th>
            <th data-sort="balance_after_trade">Balance After</th>
            <th data-sort="trade_duration_seconds">Trade Duration</th>
            <th data-sort="breakeven">Breakeven</th>
            <th>Chart</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div id="tj-empty" class="muted" style="padding:12px; display:none;">No trades found.</div>
      </div>
      <div id="tj-inst-view" class="table-wrap hidden">
        <table id="tj-inst-table">
          <thead><tr>
            <th data-sort="symbol">Symbol</th>
            <th data-sort="asset_class">Class</th>
            <th data-sort="total_trades">Trades</th>
            <th data-sort="long_trades">Longs</th>
            <th data-sort="short_trades">Shorts</th>
            <th data-sort="wins">Wins</th>
            <th data-sort="losses">Losses</th>
            <th data-sort="break_even">Break-even</th>
            <th data-sort="long_wins">Long wins</th>
            <th data-sort="long_losses">Long losses</th>
            <th data-sort="short_wins">Short wins</th>
            <th data-sort="short_losses">Short losses</th>
            <th data-sort="avg_sl_w">Avg stop dist (W)</th>
            <th data-sort="avg_sl_l">Avg stop dist (L)</th>
            <th data-sort="avg_tp_w">Avg target dist (W)</th>
            <th data-sort="avg_tp_l">Avg target dist (L)</th>
            <th data-sort="avg_duration">Avg duration</th>
            <th data-sort="min_trade_duration_seconds">Shortest</th>
            <th data-sort="max_trade_duration_seconds">Longest</th>
          </tr></thead>
          <tbody></tbody>
        </table>
        <div id="tj-inst-empty" class="muted" style="padding:12px; display:none;">No instrument data.</div>
      </div>
      <div id="tj-cal-view" class="hidden">
        <div class="cal-nav">
          <button id="tj-cal-prev">◀ Prev</button>
          <div id="tj-cal-title" class="cal-title"></div>
          <button id="tj-cal-next">Next ▶</button>
        </div>
        <div id="tj-cal-grid" class="cal-grid"></div>
      </div>
      <div id="tj-equity-view" class="hidden">
        <div id="tj-equity-wrap"></div>
      </div>
    </div>
  </div>
  <div id="tj-editor-modal" class="tj-modal" aria-hidden="true">
    <div class="tj-modal-card">
      <div id="tj-editor-title" style="font-weight:700; margin-bottom:10px;">Edit trade</div>
      <form id="tj-editor-form">
        <div class="tj-form-grid">
          <label class="tj-form-field"><span>Open time *</span><input name="open_time" type="datetime-local"/></label>
          <label class="tj-form-field"><span>Close time *</span><input name="close_time" type="datetime-local"/></label>
          <label class="tj-form-field"><span>Symbol</span><input name="symbol" type="text"/></label>
          <label class="tj-form-field"><span>Side</span><select name="side"><option value="">—</option><option>Buy</option><option>Sell</option></select></label>
          <label class="tj-form-field"><span>Timeframe</span><input name="timeframe" type="text"/></label>
          <label class="tj-form-field"><span>Test</span><select name="is_test_trade"><option value="">—</option><option value="true">Yes</option><option value="false">No</option></select></label>
          <label class="tj-form-field"><span>Setup</span><input name="setup" type="text"/></label>
          <label class="tj-form-field"><span>Qty</span><input name="qty" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Qty unit</span><input name="qty_unit" type="text"/></label>
          <label class="tj-form-field"><span>Entry price</span><input name="entry_price" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Exit price</span><input name="exit_price" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Stop loss</span><input name="stop_loss" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Take profit</span><input name="take_profit" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Commission</span><input name="commission" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Net profit</span><input name="net_profit" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Balance after trade</span><input name="balance_after_trade" type="number" step="any"/></label>
          <label class="tj-form-field"><span>Breakeven</span><select name="breakeven"><option value="">—</option><option>Yes</option><option>No</option></select></label>
          <label class="tj-form-field"><span>Account</span><input name="account" type="text"/></label>
          <label class="tj-form-field"><span>Account label</span><input name="account_label" type="text"/></label>
          <label class="tj-form-field"><span>Currency</span><input name="currency" type="text"/></label>
          <label class="tj-form-field" style="grid-column:1 / span 2;"><span>Notes</span><textarea name="notes"></textarea></label>
        </div>
        <div id="tj-editor-error"></div>
        <div class="tj-form-actions">
          <button type="button" id="tj-editor-cancel">Cancel</button>
          <button type="submit" id="tj-editor-save">Save</button>
        </div>
      </form>
    </div>
  </div>
  <script src="/static/trading_journal.js"></script>
</body>
</html>
"""


def _trading_journal_filter_rows(rows: List[Dict[str, object]], query: str) -> List[Dict[str, object]]:
    needle = query.strip().lower()
    if not needle:
        return rows
    matched: List[Dict[str, object]] = []
    for row in rows:
        haystack = " ".join(
            str(row.get(field, ""))
            for field in ("source", "account", "account_label", "symbol", "side", "status")
        ).lower()
        if needle in haystack:
            matched.append(row)
    return matched


def _trade_chart_error_page(row_id: str, message: str, status_code: int) -> HTMLResponse:
    safe_message = html.escape(message or "Unknown trade chart error.")
    safe_row = html.escape(row_id)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Trade Chart Error</title></head>
<body style="font-family:system-ui,sans-serif;background:#fff;color:#111;padding:20px;">
<h1>Trade chart unavailable</h1>
<p><strong>Row ID:</strong> {safe_row}</p>
<div style="border:1px solid #ef4444;background:#fef2f2;padding:12px;border-radius:8px;">{safe_message}</div>
</body></html>"""
    return HTMLResponse(body, status_code=status_code)


@app.get("/trade-chart/{row_id}", response_class=HTMLResponse)
async def trade_chart_page(row_id: str) -> HTMLResponse:
    row = _find_trade_row_by_id(unquote(row_id))
    if row is None:
        return _trade_chart_error_page(row_id, "Trade row not found.", 404)
    if _row_type(row) != "trade" or str(row.get("row_type") or "").lower() == "monthly_aud_reval":
        return _trade_chart_error_page(row_id, "Chart is only available for real trade rows.", 422)

    symbol_raw = str(row.get("symbol") or row.get("instrument") or row.get("symbol_raw") or "").strip()
    if not symbol_raw:
        return _trade_chart_error_page(row_id, "Trade row is missing symbol data.", 422)

    preferred_tf = _extract_trade_timeframe(row)
    chosen_tf, _upscaled = _choose_readable_interval(row, preferred_tf)
    provider = _infer_trade_chart_source(row)
    interval_value, interval_seconds = _interval_for_provider(provider, chosen_tf)
    try:
        win_start, win_end = _build_trade_chart_window(row, interval_seconds, pad_candles=5)
    except HTTPException as exc:
        return _trade_chart_error_page(row_id, str(exc.detail), exc.status_code)

    cache_key = "|".join(
        [
            _TRADE_CHART_CACHE_VERSION,
            str(row.get("id") or row_id),
            provider,
            interval_value,
            str(int(win_start.timestamp())),
            str(int(win_end.timestamp())),
        ]
    )
    cache_now = time.time()
    cached = _TRADE_CHART_CACHE.get(cache_key)
    if isinstance(cached, dict) and float(cached.get("expires_at") or 0) > cache_now:
        chart_b64 = str(cached.get("chart_b64") or "")
        meta = dict(cached.get("meta") or {})
    else:
        try:
            if provider == "oanda":
                normalized_instrument = normalize_oanda_symbol_query(symbol_raw)
                account_mode = "demo" if "demo" in str(row.get("account") or "").lower() else "live"
                candles = await _fetch_oanda_trade_candles(
                    normalized_instrument,
                    account_mode,
                    interval_value,
                    win_start.isoformat().replace("+00:00", "Z"),
                    win_end.isoformat().replace("+00:00", "Z"),
                )
            else:
                creds = resolve_bybit_credentials_for("default")
                base_url = (creds.get("base_url") if isinstance(creds, dict) else None) or BYBIT_BASE
                resolved = await _bybit_lookup_symbol(base_url, symbol_raw)
                if not resolved:
                    return _trade_chart_error_page(row_id, "Unable to resolve Bybit symbol/category for this row.", 422)
                resolved_symbol = str(resolved.get("symbol") or "").upper()
                resolved_category = str(resolved.get("_category") or "linear")
                candles = await _fetch_bybit_trade_candles(
                    resolved_symbol,
                    resolved_category,
                    interval_value,
                    int(win_start.timestamp() * 1000),
                    int(win_end.timestamp() * 1000),
                )
        except HTTPException as exc:
            return _trade_chart_error_page(row_id, str(exc.detail), exc.status_code)
        except Exception as exc:
            return _trade_chart_error_page(row_id, f"Broker data fetch failed: {exc}", 502)

        if not candles:
            return _trade_chart_error_page(row_id, "No candles available for the selected window.", 422)
        try:
            png = _render_trade_chart_png(row, candles, {"provider": provider, "interval": interval_value})
        except Exception as exc:
            return _trade_chart_error_page(row_id, f"Chart render failed: {exc}", 500)
        chart_b64 = base64.b64encode(png).decode("ascii")
        meta = {"provider": provider, "interval_value": interval_value, "candles": len(candles)}
        _TRADE_CHART_CACHE[cache_key] = {
            "expires_at": cache_now + _TRADE_CHART_CACHE_TTL_SECONDS,
            "chart_b64": chart_b64,
            "meta": meta,
        }

    body = f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Trade Chart</title></head>
<body style="margin:0;background:#fff;">
<img alt="trade chart" style="display:block;width:100vw;height:auto;max-width:none;border:0;" src="data:image/png;base64,{chart_b64}" />
</body></html>"""
    return HTMLResponse(body, status_code=200)


async def _fetch_oanda_account_summary(account: str) -> Dict[str, object]:
    cfg = _get_oanda_config(account)
    payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint="/accounts/{account_id}/summary",
        mode=cfg.get("mode") or account,
    )
    account_payload = payload.get("account") if isinstance(payload, dict) else {}
    if not isinstance(account_payload, dict):
        account_payload = {}
    return {
        "account": account,
        "label": f"OANDA {account.title()}",
        "currency": account_payload.get("currency") or "",
        "balance": _to_float(account_payload.get("balance")),
        "nav": _to_float(account_payload.get("NAV")),
        "marginAvailable": _to_float(account_payload.get("marginAvailable")),
        "marginUsed": _to_float(account_payload.get("marginUsed")),
        "marginRate": _to_float(account_payload.get("marginRate")),
    }


def _row_sort_dt(row: Dict[str, object]) -> str:
    return str(row.get("close_time") or row.get("open_time") or "")


def _row_type(row: Dict[str, object]) -> str:
    return str(row.get("row_type") or "trade").strip().lower() or "trade"


def _is_trade_row(row: Dict[str, object]) -> bool:
    return _row_type(row) == "trade"


def _trade_duration_seconds(row: Dict[str, object]) -> Optional[int]:
    if not _is_trade_row(row):
        return None
    open_time = row.get("open_time")
    close_time = row.get("close_time")
    if not open_time or not close_time:
        return None
    try:
        open_ts = pd.to_datetime(open_time)
        close_ts = pd.to_datetime(close_time)
        if pd.isna(open_ts) or pd.isna(close_ts):
            return None
        delta = (close_ts - open_ts).total_seconds()
        if delta < 0:
            return None
        # Never show 0s durations for trades. Anything under 1s (including 0) rounds up to 1s.
        return max(1, int(math.ceil(delta)))
    except Exception:
        return None


def _find_trade_row_by_id(row_id: str) -> Optional[Dict[str, object]]:
    want = str(row_id or "").strip()
    if not want:
        return None
    for row in _get_trading_journal_rows():
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip() == want:
            return row
    for row in _get_monthly_aud_revaluation_rows():
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip() == want:
            return row
    return None


def _extract_trade_timeframe(row: Dict[str, object]) -> str:
    candidates = [
        row.get("timeframe"),
        (row.get("metrics") or {}).get("timeframe") if isinstance(row.get("metrics"), dict) else None,
        (row.get("raw_excel") or {}).get("timeframe") if isinstance(row.get("raw_excel"), dict) else None,
    ]
    for value in candidates:
        text = str(value or "").strip().lower()
        if text:
            return text

    duration = _trade_duration_seconds(row)
    if duration is None:
        return "15m"
    if duration <= 2 * 3600:
        return "5m"
    if duration <= 12 * 3600:
        return "15m"
    if duration <= 48 * 3600:
        return "1h"
    if duration <= 7 * 86400:
        return "4h"
    return "1d"


def _normalize_trade_timeframe(raw: object) -> str:
    text = str(raw or "").strip().lower().replace(" ", "")
    aliases = {
        "1": "1m",
        "1m": "1m",
        "m1": "1m",
        "5": "5m",
        "5m": "5m",
        "m5": "5m",
        "15": "15m",
        "15m": "15m",
        "m15": "15m",
        "30": "30m",
        "30m": "30m",
        "m30": "30m",
        "60": "1h",
        "1h": "1h",
        "h1": "1h",
        "240": "4h",
        "4h": "4h",
        "h4": "4h",
        "d": "1d",
        "1d": "1d",
        "1day": "1d",
        "day": "1d",
        "daily": "1d",
        "w": "1w",
        "1w": "1w",
        "1week": "1w",
        "week": "1w",
        "weekly": "1w",
        "m": "1mo",
        "mo": "1mo",
        "1mo": "1mo",
        "1month": "1mo",
        "month": "1mo",
        "monthly": "1mo",
    }
    return aliases.get(text, "15m")


def _trade_interval_sequence() -> List[Tuple[str, int]]:
    return [("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600), ("4h", 14400), ("1d", 86400), ("1w", 604800), ("1mo", 2592000)]


def _build_trade_chart_window(row: Dict[str, object], interval_seconds: int, pad_candles: int = 5) -> Tuple[datetime, datetime]:
    open_time = _to_dt_utc(row.get("open_time") or row.get("opened_at") or row.get("entry_time"))
    close_time = _to_dt_utc(row.get("close_time") or row.get("closed_at") or row.get("exit_time"))
    if open_time is None or close_time is None:
        raise HTTPException(
            status_code=422,
            detail="This row does not contain enough timing data to reconstruct a full entry-to-exit candle window.",
        )
    if close_time < open_time:
        open_time, close_time = close_time, open_time
    pad = timedelta(seconds=max(1, int(interval_seconds)) * max(0, int(pad_candles)))
    return open_time - pad, close_time + pad


def _choose_readable_interval(row: Dict[str, object], preferred_timeframe: str) -> Tuple[str, bool]:
    normalized = _normalize_trade_timeframe(preferred_timeframe)
    sequence = _trade_interval_sequence()
    open_time = _to_dt_utc(row.get("open_time") or row.get("opened_at") or row.get("entry_time"))
    close_time = _to_dt_utc(row.get("close_time") or row.get("closed_at") or row.get("exit_time"))
    if open_time is None or close_time is None:
        return normalized, False
    if close_time < open_time:
        open_time, close_time = close_time, open_time
    span_seconds = max(1.0, (close_time - open_time).total_seconds())
    target_max_candles = 380
    cur_idx = next((idx for idx, pair in enumerate(sequence) if pair[0] == normalized), 2)
    chosen_idx = cur_idx
    while chosen_idx < len(sequence):
        interval_s = sequence[chosen_idx][1]
        estimate = math.ceil(span_seconds / interval_s) + 10
        if estimate <= target_max_candles:
            break
        chosen_idx += 1
    if chosen_idx >= len(sequence):
        chosen_idx = len(sequence) - 1
    return sequence[chosen_idx][0], chosen_idx != cur_idx


def _infer_trade_chart_source(row: Dict[str, object]) -> str:
    source = str(row.get("source") or "").strip().lower()
    account = str(row.get("account") or row.get("account_label") or "").strip().lower()
    symbol = str(row.get("symbol") or row.get("symbol_raw") or row.get("instrument") or "").strip()
    if source == "bybit" or "bybit" in account:
        return "bybit"
    if source == "oanda" or "oanda" in account:
        return "oanda"
    if is_likely_oanda_pair(symbol):
        return "oanda"
    return "bybit"


def _interval_for_provider(provider: str, timeframe: str) -> Tuple[str, int]:
    tf = _normalize_trade_timeframe(timeframe)
    mapping = {
        "1m": ("1", 60),
        "5m": ("5", 300),
        "15m": ("15", 900),
        "30m": ("30", 1800),
        "1h": ("60", 3600),
        "4h": ("240", 14400),
        "1d": ("D", 86400),
        "1w": ("W", 604800),
        "1mo": ("M", 2592000),
    }
    bybit_interval, seconds = mapping[tf]
    if provider == "oanda":
        oanda_map = {"1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", "D": "D", "W": "W", "M": "M"}
        return oanda_map[bybit_interval], seconds
    return bybit_interval, seconds


async def _fetch_bybit_trade_candles(symbol: str, category: str, interval: str, start_ms: int, end_ms: int) -> List[Dict[str, object]]:
    creds = resolve_bybit_credentials_for("default")
    base_url = (creds.get("base_url") if isinstance(creds, dict) else None) or BYBIT_BASE
    rows: List[List[str]] = []
    cursor_start = int(start_ms)
    while cursor_start <= end_ms:
        payload = await _bybit_public_get_json(
            base_url,
            "/v5/market/kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "start": cursor_start,
                "end": end_ms,
                "limit": 1000,
            },
        )
        parsed = _bybit_parse_kline_rows((payload.get("result") or {}).get("list"))
        if not parsed:
            break
        rows.extend(parsed)
        last_ms = int(float(parsed[-1][0]))
        if last_ms >= end_ms:
            break
        cursor_start = last_ms + 1
        if len(parsed) < 1000:
            break
    candles: List[Dict[str, object]] = []
    for item in rows:
        ts = int(float(item[0]))
        if ts < start_ms or ts > end_ms:
            continue
        candles.append(
            {
                "time": datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc),
                "open": _to_float(item[1]),
                "high": _to_float(item[2]),
                "low": _to_float(item[3]),
                "close": _to_float(item[4]),
            }
        )
    return [c for c in candles if all(c.get(k) is not None for k in ("open", "high", "low", "close"))]


async def _fetch_oanda_trade_candles(
    instrument: str,
    account_mode: str,
    granularity: str,
    from_iso: str,
    to_iso: str,
) -> List[Dict[str, object]]:
    cfg = _get_oanda_config(account_mode)
    endpoint = (
        f"/instruments/{instrument}/candles?price=M&granularity={granularity}"
        f"&from={quote(from_iso)}&to={quote(to_iso)}"
    )
    payload = await _fetch_oanda_json(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        endpoint=endpoint,
        mode=cfg["mode"],
    )
    candles: List[Dict[str, object]] = []
    for row in (payload.get("candles") or []):
        if not isinstance(row, dict) or not row.get("complete"):
            continue
        mid = row.get("mid") or {}
        if not isinstance(mid, dict):
            continue
        ts = _to_dt_utc(row.get("time"))
        if ts is None:
            continue
        candles.append(
            {
                "time": ts,
                "open": _to_float(mid.get("o")),
                "high": _to_float(mid.get("h")),
                "low": _to_float(mid.get("l")),
                "close": _to_float(mid.get("c")),
            }
        )
    return [c for c in candles if all(c.get(k) is not None for k in ("open", "high", "low", "close"))]


def _locate_trade_event_candle_index(candles: List[Dict[str, object]], event_time: Optional[datetime]) -> Optional[int]:
    if event_time is None or not candles:
        return None
    target = event_time.astimezone(timezone.utc)
    for idx, candle in enumerate(candles):
        start = _to_dt_utc(candle.get("time"))
        if start is None:
            continue
        if idx + 1 < len(candles):
            next_start = _to_dt_utc(candles[idx + 1].get("time"))
            if next_start and start <= target < next_start:
                return idx
        elif target >= start:
            return idx
    return None


def _render_trade_chart_png(row: Dict[str, object], candles: List[Dict[str, object]], meta: Dict[str, object]) -> bytes:
    if plt is None or mdates is None:
        raise ValueError("matplotlib is unavailable in this runtime.")
    if not candles:
        raise ValueError("No candles returned for selected window.")
    fig, ax = plt.subplots(figsize=(18, 9), dpi=150)
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    x = mdates.date2num([c["time"].astimezone(ZoneInfo(APP_TIMEZONE)) for c in candles])
    width = max(0.0005, (x[1] - x[0]) * 0.7) if len(x) > 1 else 0.001
    for idx, c in enumerate(candles):
        xi = x[idx]
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        color = "#2e7d32" if cl >= o else "#c62828"
        ax.vlines(xi, l, h, color=color, linewidth=0.7, alpha=0.9)
        low = min(o, cl)
        body_h = max(abs(cl - o), 1e-9)
        ax.add_patch(plt.Rectangle((xi - width / 2, low), width, body_h, facecolor=color, edgecolor=color, linewidth=0.8))

    entry_time = _to_dt_utc(row.get("open_time") or row.get("opened_at") or row.get("entry_time"))
    exit_time = _to_dt_utc(row.get("close_time") or row.get("closed_at") or row.get("exit_time"))
    line_labels: List[Tuple[str, float, str]] = []
    for label, key, color in [
        ("Entry", "entry_price", "#1565c0"),
        ("SL", "stop_loss", "#ad1457"),
        ("TP", "take_profit", "#2e7d32"),
        ("Exit", "exit_price", "#ef6c00"),
    ]:
        value = _to_float(row.get(key))
        if value is None:
            continue
        ax.axhline(value, color=color, linewidth=1.0, alpha=0.9)
        line_labels.append((label, value, color))

    y_span = max((max(float(c["high"]) for c in candles) - min(float(c["low"]) for c in candles)), 1e-9)
    min_gap = y_span * 0.018
    placed_ys: List[float] = []
    sorted_labels = sorted(line_labels, key=lambda item: item[1], reverse=True)
    for label, value, color in sorted_labels:
        label_y = value + (y_span * 0.008)
        for existing in placed_ys:
            if abs(label_y - existing) < min_gap:
                label_y = existing + min_gap
        placed_ys.append(label_y)
        ax.text(
            x[-1],
            label_y,
            f" {label}: {value:.6f}",
            color=color,
            va="bottom",
            ha="left",
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
        )

    def _mark_event(event_idx: Optional[int], event_price: Optional[float], title: str, color: str) -> None:
        if event_idx is None:
            return
        event_x = x[event_idx]
        candle_high = float(candles[event_idx]["high"])
        arrow_target_y = candle_high + (y_span * 0.006)
        text_y = candle_high + (y_span * 0.055)
        ax.annotate(
            title,
            xy=(event_x, arrow_target_y),
            xytext=(event_x, text_y),
            ha="center",
            va="bottom",
            fontsize=8,
            color=color,
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": color, "alpha": 0.95},
            arrowprops={"arrowstyle": "-|>", "color": color, "lw": 1.1, "shrinkA": 0, "shrinkB": 0},
        )

    _mark_event(
        _locate_trade_event_candle_index(candles, entry_time),
        _to_float(row.get("entry_price")),
        "Entry candle",
        "#1565c0",
    )
    _mark_event(
        _locate_trade_event_candle_index(candles, exit_time),
        _to_float(row.get("exit_price")),
        "Exit candle",
        "#ef6c00",
    )

    ax.grid(True, linewidth=0.4, alpha=0.35)
    ax.tick_params(axis="both", colors="black")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M", tz=ZoneInfo(APP_TIMEZONE)))
    ax.set_xlim(min(x), max(x) + max(0.002, (max(x) - min(x)) * 0.08))
    candle_low = min(float(c["low"]) for c in candles)
    candle_high = max(float(c["high"]) for c in candles)
    ax.set_ylim(candle_low - (y_span * 0.03), candle_high + (y_span * 0.14))
    fig.autofmt_xdate()
    buf = io.BytesIO()
    plt.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

def _is_win(row: Dict[str, object]) -> bool:
    pnl = _to_float(row.get("net_profit"))
    return pnl is not None and pnl > 0


def _is_loss(row: Dict[str, object]) -> bool:
    pnl = _to_float(row.get("net_profit"))
    return pnl is not None and pnl < 0


def _is_be(row: Dict[str, object]) -> bool:
    breakeven = str(row.get("breakeven") or "").strip().lower()
    pnl = _to_float(row.get("net_profit"))
    return breakeven in {"yes", "y", "true", "1"} or (pnl is not None and abs(pnl) < 1e-12)


def _calc_balance_after_trade(
    rows: List[Dict[str, object]], current_balances: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    state = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
    active_folder = str(state.get("source_folder") if isinstance(state, dict) else "")
    cashflows = _load_cashflows_from_dropbox(active_folder) if active_folder else {}

    out_rows = [dict(row) for row in rows]
    by_account: Dict[str, List[int]] = defaultdict(list)
    for idx, row in enumerate(out_rows):
        if not _is_trade_row(row):
            continue
        account = str(row.get("account_label") or row.get("account") or "")
        account_key = _norm_account_key(account)
        if account_key:
            by_account[account_key].append(idx)

    def _to_ts(value: object) -> float:
        if value in (None, ""):
            return float("-inf")
        try:
            return float(pd.to_datetime(value).timestamp())
        except Exception:
            return float("-inf")

    for account_key, indices in by_account.items():
        events = cashflows.get(account_key) or []
        if not events:
            continue
        events_sorted = sorted(events, key=lambda e: _to_ts(e.get("date")))
        trade_indices = sorted(
            indices,
            key=lambda i: _to_ts(
                out_rows[i].get("close_time") or out_rows[i].get("open_time")
            ),
        )

        segment_running: Dict[int, float] = {}
        for row_idx in trade_indices:
            row = out_rows[row_idx]
            trade_ts = _to_ts(row.get("close_time") or row.get("open_time"))
            anchor = -1
            for i, event in enumerate(events_sorted):
                if _to_ts(event.get("date")) <= trade_ts:
                    anchor = i
                else:
                    break
            if anchor < 0:
                continue
            if anchor not in segment_running:
                start_bal = _to_float(events_sorted[anchor].get("new_balance"))
                if start_bal is None:
                    continue
                segment_running[anchor] = start_bal

            pnl = _to_float(row.get("net_profit"))
            if pnl is not None:
                segment_running[anchor] += pnl

            if _to_float(row.get("balance_after_trade")) is None:
                row["balance_after_trade"] = segment_running[anchor]
            if not str(row.get("balance_after_trade_currency") or "").strip():
                row["balance_after_trade_currency"] = str(
                    events_sorted[anchor].get("currency") or row.get("currency") or ""
                )

    return out_rows


def _apply_analysis_balances(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    out_rows = [dict(row) for row in rows]
    by_account: Dict[str, List[int]] = defaultdict(list)

    def _to_ts(value: object) -> float:
        if value in (None, ""):
            return float("-inf")
        try:
            return float(pd.to_datetime(value).timestamp())
        except Exception:
            return float("-inf")

    for idx, row in enumerate(out_rows):
        if not _is_trade_row(row):
            continue
        account = str(row.get("account_label") or row.get("account") or "")
        key = _norm_account_key(account)
        if key:
            by_account[key].append(idx)

    for indices in by_account.values():
        running_balance: Optional[float] = None
        for row_idx in sorted(indices, key=lambda i: _to_ts(out_rows[i].get("close_time") or out_rows[i].get("open_time"))):
            row = out_rows[row_idx]
            balance_after = _to_float(row.get("balance_after_trade"))
            pnl = _to_float(row.get("net_profit"))
            if running_balance is None:
                if balance_after is None:
                    continue
                if _is_test_trade_row(row) and pnl is not None:
                    running_balance = balance_after - pnl
                else:
                    running_balance = balance_after
            before_balance = running_balance if pnl is not None else None
            if not _is_test_trade_row(row) and pnl is not None and before_balance is not None:
                running_balance = before_balance + pnl
            row["analysis_balance_before_trade"] = before_balance
            row["analysis_balance_after_trade"] = running_balance if before_balance is not None else balance_after
    return out_rows


def _avg(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _pip_size_for_symbol(symbol: str) -> float:
    sym = _canonical_symbol(symbol or "")
    if not sym:
        return 0.0001
    return 0.01 if sym.endswith("JPY") else 0.0001


def _signed_price_move(row: Dict[str, object]) -> Optional[float]:
    entry = _to_float(row.get("entry_price"))
    exitp = _to_float(row.get("exit_price"))
    if entry is None or exitp is None:
        return None
    side = str(row.get("side") or "").upper()
    move = exitp - entry
    if side.startswith("SELL") or side == "SHORT":
        move = -move
    return move


def _enrich_trade_row_metrics(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in rows:
        r = dict(row)
        metrics = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        r["timeframe"] = _normalize_timeframe(r.get("timeframe") or metrics.get("timeframe"))
        r["row_type"] = _row_type(r)

        if not _is_trade_row(r):
            r.setdefault("result_pct", None)
            r.setdefault("price_move_pct", None)
            r.setdefault("profit_pct", None)
            r.setdefault("r_multiple", None)
            r["trade_duration_seconds"] = None
            out.append(r)
            continue

        entry = _to_float(r.get("entry_price"))
        sl = _to_float(r.get("stop_loss"))
        pnl = _to_float(r.get("net_profit"))
        move = _signed_price_move(r)

        price_move_pct = None
        if move is not None and entry not in (None, 0):
            price_move_pct = (move / entry) * 100.0

        risk_amount = None
        for k in ("risk_amount", "risk", "risk_aud", "risk_usd"):
            rv = _to_float(metrics.get(k) if k in metrics else r.get(k))
            if rv and rv > 0:
                risk_amount = rv
                break

        result_pct = None
        balance_after = _to_float(r.get("analysis_balance_after_trade"))
        balance_before = _to_float(r.get("analysis_balance_before_trade"))
        if balance_after is None:
            balance_after = _to_float(r.get("balance_after_trade"))
        if pnl is not None:
            if balance_before is None and balance_after is not None:
                balance_before = balance_after - pnl
            if balance_before is not None:
                if math.isfinite(balance_before) and balance_before > 0:
                    result_pct = (pnl / balance_before) * 100.0
            if result_pct is None and risk_amount is not None:
                result_pct = (pnl / risk_amount) * 100.0

        r_multiple = None
        if pnl is not None and risk_amount is not None:
            r_multiple = pnl / risk_amount
        if r_multiple is None and move is not None and entry is not None and sl is not None:
            risk_dist = abs(entry - sl)
            if risk_dist > 0:
                r_multiple = move / risk_dist

        r["result_pct"] = result_pct
        r["price_move_pct"] = price_move_pct
        r["profit_pct"] = result_pct
        r["r_multiple"] = r_multiple
        r["trade_duration_seconds"] = _trade_duration_seconds(r)
        out.append(r)
    return out


def _is_fx_asset_class(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"fx", "forex", "foreign_exchange"}


def _is_crypto_asset_class(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"crypto", "cryptocurrency", "digital_asset", "digitalasset"}


def _compute_journal_stats(
    rows: List[Dict[str, object]], balances: List[Dict[str, object]]
) -> Dict[str, object]:
    trade_rows = [dict(r) for r in rows if _is_trade_row(r) and not _is_test_trade_row(r)]

    def _is_valid_price_level(val: Optional[float]) -> bool:
        # Some imports represent missing SL/TP/entry as 0.0, which explodes distance metrics.
        return val is not None and math.isfinite(val) and val > 0

    def _pct_distance(entry: Optional[float], level: Optional[float]) -> Optional[float]:
        if not _is_valid_price_level(entry) or not _is_valid_price_level(level):
            return None
        if entry == 0:
            return None
        return (abs(level - entry) / entry) * 100.0

    balance_by_account: List[Dict[str, object]] = []
    for bal in balances:
        balance_by_account.append(
            {
                "account": bal.get("account") or bal.get("label"),
                "label": bal.get("label") or bal.get("account"),
                "balance": _to_float(bal.get("balance")),
                "currency": bal.get("currency") or "",
            }
        )

    by_instrument: Dict[str, Dict[str, object]] = {}
    most_wins: Dict[str, object] = {"symbol": None, "wins": -1}
    most_losses: Dict[str, object] = {"symbol": None, "losses": -1}

    for row in trade_rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        if symbol not in by_instrument:
            by_instrument[symbol] = {
                "symbol": symbol,
                "total_trades": 0,
                "long_trades": 0,
                "short_trades": 0,
                "long_wins": 0,
                "long_losses": 0,
                "long_break_even": 0,
                "short_wins": 0,
                "short_losses": 0,
                "short_break_even": 0,
                "wins": 0,
                "losses": 0,
                "break_even": 0,
                "stop_losses": [],
                "take_profits": [],
                "sl_distances": [],
                "tp_distances": [],
                "durations": [],
                "quote_currency": "USDT" if not _is_fx_asset_class(row.get("asset_class")) else "",
            }
        bucket = by_instrument[symbol]
        bucket["total_trades"] += 1

        side_norm = str(row.get("side") or "").strip().upper()
        is_long_bias = side_norm.startswith("BUY") or side_norm == "LONG"
        is_short_bias = side_norm.startswith("SELL") or side_norm == "SHORT"
        if is_long_bias:
            bucket["long_trades"] += 1
        elif is_short_bias:
            bucket["short_trades"] += 1

        is_win = _is_win(row)
        is_loss = _is_loss(row)
        if is_win:
            bucket["wins"] += 1
            if is_long_bias:
                bucket["long_wins"] += 1
            elif is_short_bias:
                bucket["short_wins"] += 1
        elif is_loss:
            bucket["losses"] += 1
            if is_long_bias:
                bucket["long_losses"] += 1
            elif is_short_bias:
                bucket["short_losses"] += 1
        else:
            bucket["break_even"] += 1
            if is_long_bias:
                bucket["long_break_even"] += 1
            elif is_short_bias:
                bucket["short_break_even"] += 1

        dur = _to_float(row.get("trade_duration_seconds"))
        if dur is not None and dur >= 0:
            bucket["durations"].append(dur)

        sl = _to_float(row.get("stop_loss"))
        tp = _to_float(row.get("take_profit"))
        entry = _to_float(row.get("entry_price"))
        if sl is not None:
            bucket["stop_losses"].append(sl)
        if tp is not None:
            bucket["take_profits"].append(tp)

        def _append_metric(prefix: str, dist: float) -> None:
            if _is_fx_asset_class(row.get("asset_class")):
                pip_val = dist / _pip_size_for_symbol(symbol)
                bucket.setdefault(f"{prefix}_pips", []).append(pip_val)
                if is_win:
                    bucket.setdefault(f"{prefix}_pips_wins", []).append(pip_val)
                elif is_loss:
                    bucket.setdefault(f"{prefix}_pips_losses", []).append(pip_val)
            else:
                bucket.setdefault(f"{prefix}_quote", []).append(dist)
                if is_win:
                    bucket.setdefault(f"{prefix}_quote_wins", []).append(dist)
                elif is_loss:
                    bucket.setdefault(f"{prefix}_quote_losses", []).append(dist)

        if _is_valid_price_level(entry) and _is_valid_price_level(sl):
            dist = abs(entry - sl)
            bucket["sl_distances"].append(dist)
            _append_metric("sl_distances", dist)
        if _is_valid_price_level(entry) and _is_valid_price_level(tp):
            dist = abs(tp - entry)
            bucket["tp_distances"].append(dist)
            _append_metric("tp_distances", dist)

    out_by_instrument: List[Dict[str, object]] = []
    for _, bucket in by_instrument.items():
        item = dict(bucket)
        item["avg_stop_loss"] = _avg(item.pop("stop_losses"))
        item["avg_take_profit"] = _avg(item.pop("take_profits"))
        item["avg_sl_distance"] = _avg(item.pop("sl_distances"))
        item["avg_tp_distance"] = _avg(item.pop("tp_distances"))
        dur_vals = item.pop("durations", [])
        item["avg_trade_duration_seconds"] = _avg(dur_vals)
        item["min_trade_duration_seconds"] = min(dur_vals) if dur_vals else None
        item["max_trade_duration_seconds"] = max(dur_vals) if dur_vals else None
        item["avg_sl_distance_pips"] = _avg(item.pop("sl_distances_pips", []))
        item["avg_tp_distance_pips"] = _avg(item.pop("tp_distances_pips", []))
        item["avg_sl_distance_quote"] = _avg(item.pop("sl_distances_quote", []))
        item["avg_tp_distance_quote"] = _avg(item.pop("tp_distances_quote", []))
        item["avg_sl_distance_pips_wins"] = _avg(item.pop("sl_distances_pips_wins", []))
        item["avg_sl_distance_pips_losses"] = _avg(item.pop("sl_distances_pips_losses", []))
        item["avg_tp_distance_pips_wins"] = _avg(item.pop("tp_distances_pips_wins", []))
        item["avg_tp_distance_pips_losses"] = _avg(item.pop("tp_distances_pips_losses", []))
        item["avg_sl_distance_quote_wins"] = _avg(item.pop("sl_distances_quote_wins", []))
        item["avg_sl_distance_quote_losses"] = _avg(item.pop("sl_distances_quote_losses", []))
        item["avg_tp_distance_quote_wins"] = _avg(item.pop("tp_distances_quote_wins", []))
        item["avg_tp_distance_quote_losses"] = _avg(item.pop("tp_distances_quote_losses", []))
        item["asset_class"] = (
            "fx"
            if any(
                str(r.get("symbol") or "") == item["symbol"]
                and _is_fx_asset_class(r.get("asset_class"))
                for r in trade_rows
            )
            else "crypto"
        )
        if item["asset_class"] == "crypto":
            # Crypto distance metrics are denominated in quote currency; default to USDT.
            item["quote_currency"] = "USDT"
        else:
            item["quote_currency"] = ""
        out_by_instrument.append(item)
        if item["wins"] > most_wins["wins"]:
            most_wins = {"symbol": item["symbol"], "wins": item["wins"]}
        if item["losses"] > most_losses["losses"]:
            most_losses = {"symbol": item["symbol"], "losses": item["losses"]}
    out_by_instrument.sort(
        key=lambda x: (-(x.get("total_trades") or 0), str(x.get("symbol") or ""))
    )

    all_sl_pct: List[float] = []
    all_tp_pct: List[float] = []
    all_durations: List[float] = []
    for row in trade_rows:
        entry = _to_float(row.get("entry_price"))
        sl = _to_float(row.get("stop_loss"))
        tp = _to_float(row.get("take_profit"))
        sl_pct = _pct_distance(entry, sl)
        tp_pct = _pct_distance(entry, tp)
        if sl_pct is not None:
            all_sl_pct.append(sl_pct)
        if tp_pct is not None:
            all_tp_pct.append(tp_pct)
        dur = _to_float(row.get("trade_duration_seconds"))
        if dur is not None and dur >= 0:
            all_durations.append(dur)
    result_pct_vals = [_to_float(row.get("result_pct")) for row in trade_rows]
    result_pct_vals = [x for x in result_pct_vals if x is not None]
    r_mult_vals = [_to_float(row.get("r_multiple")) for row in trade_rows]
    r_mult_vals = [x for x in r_mult_vals if x is not None]
    winner_durations = [
        _to_float(row.get("trade_duration_seconds")) for row in trade_rows if _is_win(row)
    ]
    winner_durations = [x for x in winner_durations if x is not None and x >= 0]
    loser_durations = [
        _to_float(row.get("trade_duration_seconds")) for row in trade_rows if _is_loss(row)
    ]
    loser_durations = [x for x in loser_durations if x is not None and x >= 0]

    fx_trade_durations = [
        _to_float(row.get("trade_duration_seconds"))
        for row in trade_rows
        if _is_fx_asset_class(row.get("asset_class"))
    ]
    fx_trade_durations = [x for x in fx_trade_durations if x is not None and x >= 0]

    crypto_trade_durations = [
        _to_float(row.get("trade_duration_seconds"))
        for row in trade_rows
        if _is_crypto_asset_class(row.get("asset_class"))
    ]
    crypto_trade_durations = [x for x in crypto_trade_durations if x is not None and x >= 0]

    min_fx_trade_duration = min(fx_trade_durations) if fx_trade_durations else None
    max_fx_trade_duration = max(fx_trade_durations) if fx_trade_durations else None
    min_crypto_trade_duration = min(crypto_trade_durations) if crypto_trade_durations else None
    max_crypto_trade_duration = max(crypto_trade_durations) if crypto_trade_durations else None

    unique_symbols = sorted(
        {str(r.get("symbol") or "") for r in trade_rows if str(r.get("symbol") or "").strip()}
    )
    fx_symbols = sorted(
        {str(r.get("symbol") or "") for r in trade_rows if _is_fx_asset_class(r.get("asset_class"))}
    )
    crypto_symbols = sorted(
        {str(r.get("symbol") or "") for r in trade_rows if _is_crypto_asset_class(r.get("asset_class"))}
    )

    all_trade_durations = [_to_float(row.get("trade_duration_seconds")) for row in trade_rows]
    all_trade_durations = [x for x in all_trade_durations if x is not None and x >= 0]
    min_trade_duration = min(all_trade_durations) if all_trade_durations else None
    max_trade_duration = max(all_trade_durations) if all_trade_durations else None

    total_wins = sum(1 for row in trade_rows if _is_win(row))
    total_losses = sum(1 for row in trade_rows if _is_loss(row))
    denom = total_wins + total_losses
    win_rate_pct = (total_wins / denom * 100.0) if denom else None

    def _bias(row: Dict[str, object]) -> str:
        side_norm = str(row.get("side") or "").strip().upper()
        if side_norm.startswith("BUY") or side_norm == "LONG":
            return "long"
        if side_norm.startswith("SELL") or side_norm == "SHORT":
            return "short"
        return ""

    long_trades = sum(1 for row in trade_rows if _bias(row) == "long")
    short_trades = sum(1 for row in trade_rows if _bias(row) == "short")
    long_wins = sum(1 for row in trade_rows if _bias(row) == "long" and _is_win(row))
    long_losses = sum(1 for row in trade_rows if _bias(row) == "long" and _is_loss(row))
    short_wins = sum(1 for row in trade_rows if _bias(row) == "short" and _is_win(row))
    short_losses = sum(1 for row in trade_rows if _bias(row) == "short" and _is_loss(row))
    long_break_even = sum(1 for row in trade_rows if _bias(row) == "long" and _is_be(row))
    short_break_even = sum(1 for row in trade_rows if _bias(row) == "short" and _is_be(row))

    fx_wins = sum(
        1
        for row in trade_rows
        if _is_fx_asset_class(row.get("asset_class")) and _is_win(row)
    )
    fx_losses = sum(
        1
        for row in trade_rows
        if _is_fx_asset_class(row.get("asset_class")) and _is_loss(row)
    )
    denom_fx = fx_wins + fx_losses
    fx_win_rate_pct = (fx_wins / denom_fx * 100.0) if denom_fx else None

    crypto_wins = sum(
        1
        for row in trade_rows
        if _is_crypto_asset_class(row.get("asset_class")) and _is_win(row)
    )
    crypto_losses = sum(
        1
        for row in trade_rows
        if _is_crypto_asset_class(row.get("asset_class")) and _is_loss(row)
    )
    denom_crypto = crypto_wins + crypto_losses
    crypto_win_rate_pct = (crypto_wins / denom_crypto * 100.0) if denom_crypto else None

    def _to_ts(value: object) -> float:
        if value in (None, ""):
            return float("-inf")
        try:
            return float(pd.to_datetime(value).timestamp())
        except Exception:
            return float("-inf")

    # Drawdown stats (%), segmented by cashflow anchors so deposits/withdrawals
    # do not show up as drawdowns.
    cashflow_rows = [r for r in rows if _row_type(r) == "cashflow"]
    events_by_account: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    for r in cashflow_rows:
        account = str(r.get("account_label") or r.get("account") or "").strip()
        account_key = _norm_account_key(account)
        event_dt = r.get("close_time") or r.get("open_time")
        if not account_key or not event_dt:
            continue
        ts = _to_ts(event_dt)
        if math.isfinite(ts):
            events_by_account[account_key].append((ts, str(event_dt)))
    for account_key in list(events_by_account.keys()):
        events_by_account[account_key] = sorted(events_by_account[account_key], key=lambda x: x[0])

    segments: Dict[Tuple[str, str], List[Tuple[float, float]]] = defaultdict(list)
    for row in trade_rows:
        account = str(row.get("account_label") or row.get("account") or "").strip()
        account_key = _norm_account_key(account)
        if not account_key:
            continue
        dt = row.get("close_time") or row.get("open_time")
        ts = _to_ts(dt)
        bal = _to_float(row.get("balance_after_trade"))
        if bal is None or not math.isfinite(bal) or bal <= 0 or not math.isfinite(ts):
            continue

        anchor_id = "__no_anchor__"
        for ev_ts, ev_id in events_by_account.get(account_key, []):
            if ev_ts <= ts:
                anchor_id = ev_id
            else:
                break
        segments[(account_key, anchor_id)].append((ts, bal))

    dd_vals: List[float] = []
    for pts in segments.values():
        pts_sorted = sorted(pts, key=lambda x: x[0])
        peak: Optional[float] = None
        for _, bal in pts_sorted:
            if peak is None or bal > peak:
                peak = bal
            if peak and peak > 0:
                dd = (peak - bal) / peak * 100.0
                if dd > 0 and math.isfinite(dd):
                    dd_vals.append(dd)

    if dd_vals:
        max_drawdown_pct = max(dd_vals)
        min_drawdown_pct = min(dd_vals)
        avg_drawdown_pct = sum(dd_vals) / len(dd_vals)
    else:
        max_drawdown_pct = 0.0
        min_drawdown_pct = 0.0
        avg_drawdown_pct = 0.0

    totals = {
            "trades": len(trade_rows),
            "wins": sum(1 for row in trade_rows if _is_win(row)),
            "losses": sum(1 for row in trade_rows if _is_loss(row)),
            "break_even": sum(1 for row in trade_rows if _is_be(row)),
            "long_trades": long_trades,
            "short_trades": short_trades,
            "long_wins": long_wins,
            "long_losses": long_losses,
            "long_break_even": long_break_even,
            "short_wins": short_wins,
            "short_losses": short_losses,
            "short_break_even": short_break_even,
            # Express these as % distance from entry, not raw price levels.
            # Keep legacy keys for backward compatibility.
            "avg_stop_pct": _avg(all_sl_pct),
            "avg_target_pct": _avg(all_tp_pct),
            "avg_stop_loss": _avg(all_sl_pct),
            "avg_take_profit": _avg(all_tp_pct),
            "avg_profit_pct": _avg(result_pct_vals),
            "avg_result_pct": _avg(result_pct_vals),
            "avg_r_multiple": _avg(r_mult_vals),
            "avg_duration_seconds": _avg(all_durations),
            "avg_winner_duration_seconds": _avg(winner_durations),
            "avg_loser_duration_seconds": _avg(loser_durations),
            "avg_fx_duration_seconds": _avg(fx_trade_durations),
            "avg_crypto_duration_seconds": _avg(crypto_trade_durations),
            "min_fx_trade_duration_seconds": min_fx_trade_duration,
            "max_fx_trade_duration_seconds": max_fx_trade_duration,
            "min_crypto_trade_duration_seconds": min_crypto_trade_duration,
            "max_crypto_trade_duration_seconds": max_crypto_trade_duration,
            "min_trade_duration_seconds": min_trade_duration,
            "max_trade_duration_seconds": max_trade_duration,
            "win_rate_pct": win_rate_pct,
            "fx_win_rate_pct": fx_win_rate_pct,
            "crypto_win_rate_pct": crypto_win_rate_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "min_drawdown_pct": min_drawdown_pct,
            "avg_drawdown_pct": avg_drawdown_pct,
            "unique_instruments": len(unique_symbols),
            "crypto_instruments": len(crypto_symbols),
            "fx_instruments": len(fx_symbols),
        }

    def _market_bucket(rows_subset: List[Dict[str, object]], label: str) -> Dict[str, object]:
        durations = [
            _to_float(r.get("trade_duration_seconds"))
            for r in rows_subset
            if _to_float(r.get("trade_duration_seconds")) is not None
        ]
        durations = [d for d in durations if d is not None and d >= 0]
        wins = sum(1 for r in rows_subset if _is_win(r))
        losses = sum(1 for r in rows_subset if _is_loss(r))
        denom_local = wins + losses
        return {
            "label": label,
            "trades": len(rows_subset),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": (wins / denom_local * 100.0) if denom_local else None,
            "avg_result_pct": _avg([_to_float(r.get("result_pct")) for r in rows_subset if _to_float(r.get("result_pct")) is not None]),
            "avg_r_multiple": _avg([_to_float(r.get("r_multiple")) for r in rows_subset if _to_float(r.get("r_multiple")) is not None]),
            "avg_duration_seconds": _avg(durations),
            "longest_duration_seconds": max(durations) if durations else None,
            "shortest_duration_seconds": min(durations) if durations else None,
            "instruments": len({str(r.get("symbol") or "").strip() for r in rows_subset if str(r.get("symbol") or "").strip()}),
        }

    fx_rows = [r for r in trade_rows if _is_fx_asset_class(r.get("asset_class"))]
    crypto_rows = [r for r in trade_rows if _is_crypto_asset_class(r.get("asset_class"))]

    return {
        "totals": totals,
        "balances": balance_by_account,
        "by_instrument": out_by_instrument,
        "instrument_with_most_wins": most_wins if most_wins["symbol"] else None,
        "instrument_with_most_losses": most_losses if most_losses["symbol"] else None,
        "groups": {
            "overview": {
                "trades": totals.get("trades"),
                "wins": totals.get("wins"),
                "losses": totals.get("losses"),
                "break_even": totals.get("break_even"),
                "win_rate_pct": totals.get("win_rate_pct"),
                "avg_result_pct": totals.get("avg_result_pct"),
                "avg_r_multiple": totals.get("avg_r_multiple"),
                "max_drawdown_pct": totals.get("max_drawdown_pct"),
            },
            "direction": {
                "long_trades": totals.get("long_trades"),
                "short_trades": totals.get("short_trades"),
                "long_win_rate_pct": (long_wins / (long_wins + long_losses) * 100.0) if (long_wins + long_losses) else None,
                "short_win_rate_pct": (short_wins / (short_wins + short_losses) * 100.0) if (short_wins + short_losses) else None,
            },
            "market_breakdown": [
                _market_bucket(trade_rows, "Overall"),
                _market_bucket(fx_rows, "Forex"),
                _market_bucket(crypto_rows, "Crypto"),
            ],
            "risk_expectancy": {
                "avg_stop_pct": totals.get("avg_stop_pct"),
                "avg_target_pct": totals.get("avg_target_pct"),
                "avg_result_pct": totals.get("avg_result_pct"),
                "avg_r_multiple": totals.get("avg_r_multiple"),
                "max_drawdown_pct": totals.get("max_drawdown_pct"),
                "avg_drawdown_pct": totals.get("avg_drawdown_pct"),
                "min_drawdown_pct": totals.get("min_drawdown_pct"),
            },
            "duration": {
                "overall_avg_seconds": totals.get("avg_duration_seconds"),
                "overall_shortest_seconds": totals.get("min_trade_duration_seconds"),
                "overall_longest_seconds": totals.get("max_trade_duration_seconds"),
                "fx_avg_seconds": totals.get("avg_fx_duration_seconds"),
                "fx_shortest_seconds": totals.get("min_fx_trade_duration_seconds"),
                "fx_longest_seconds": totals.get("max_fx_trade_duration_seconds"),
                "crypto_avg_seconds": totals.get("avg_crypto_duration_seconds"),
                "crypto_shortest_seconds": totals.get("min_crypto_trade_duration_seconds"),
                "crypto_longest_seconds": totals.get("max_crypto_trade_duration_seconds"),
            },
            "leaders": {
                "most_wins_instrument": most_wins if most_wins["symbol"] else None,
                "most_losses_instrument": most_losses if most_losses["symbol"] else None,
            },
        },
        "balance_after_trade_note": "Approximate unless cashflow ledger fully captures deposits/withdrawals/transfers.",
    }

def _read_bybit_settings() -> Dict[str, float]:
    try:
        settings = bybit_monitor.get_runtime_settings(force=True)
        settings["push_ready"] = bybit_monitor.push_notifications_ready()
        return settings
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {exc}") from exc


def _update_bybit_settings(payload: Dict[str, object]) -> Dict[str, float]:
    try:
        wait_seconds = payload.get("wait_seconds") if isinstance(payload, dict) else None
        percent_threshold = payload.get("percent_threshold") if isinstance(payload, dict) else None
        return bybit_monitor.update_runtime_settings(
            wait_seconds=int(wait_seconds) if wait_seconds is not None else None,
            percent_threshold=float(percent_threshold) if percent_threshold is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}") from exc


def _read_oanda_settings() -> Dict[str, float]:
    try:
        settings = oanda_monitor.get_runtime_settings(force=True)
        settings["push_ready"] = oanda_monitor.push_notifications_ready()
        return settings
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {exc}") from exc


def _update_oanda_settings(payload: Dict[str, object]) -> Dict[str, float]:
    try:
        if not isinstance(payload, dict):
            payload = {}
        updates: Dict[str, object] = {}
        for key in (
            "wait_seconds",
            "percent_threshold",
        ):
            if key in payload:
                updates[key] = payload.get(key)
        return oanda_monitor.update_runtime_settings(**updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}") from exc



def _render_log_view(script_name: str) -> str:
    """Return the HTML log viewer for a known script."""

    safe_name = html.escape(script_name)
    return (
        LOG_VIEWER_TEMPLATE.replace("{script_name}", safe_name)
        .replace("{script_name_json}", json.dumps(script_name))
    )


@app.get("/logs/view/{script_name:path}", response_class=HTMLResponse)
async def view_logs(script_name: str) -> str:
    # Ensure the script exists so we don't render a viewer for an unknown path.
    script_manager.get(script_name)
    return _render_log_view(script_name)


@app.api_route("/apps/{script_name}", methods=PROXY_METHODS)
@app.api_route("/apps/{script_name}/{path:path}", methods=PROXY_METHODS)
async def proxy_app(script_name: str, request: Request, path: str = "") -> Response:
    if path == "" and not request.url.path.endswith("/"):
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"{request.url.path}/{suffix}", status_code=307)
    if APP_PROFILE == "render" and script_name in LOCAL_ONLY_APP_NAMES:
        accepts = str(request.headers.get("accept", "")).lower()
        wants_json = "application/json" in accepts
        return _local_only_disabled_response(f"/apps/{script_name}", as_json=wants_json)

    script = script_manager.get(script_name)
    accept = request.headers.get("accept", "")
    wants_html = (
        "text/html" in accept
        and request.query_params.get("format") != "json"
        and request.method.upper() == "GET"
    )
    if not script.is_running:
        if script.name in WEB_APPS:
            if script.port is None:
                script.port = _allocate_port()
            if not script.last_start_attempt_at or script.last_start_error:
                if script.startup_task is None or script.startup_task.done():
                    script.startup_task = asyncio.create_task(_background_start(script))
            if wants_html:
                target_url = f"/apps/{_encoded_script_name(script.name)}"
                return HTMLResponse(
                    LAUNCHER_TEMPLATE.replace(
                        "{script_name}", html.escape(script.name)
                    )
                    .replace("{target_url}", target_url)
                    .replace("{has_ui}", "true"),
                    status_code=200,
                )
            raise HTTPException(status_code=503, detail=f"{script_name} is starting.")
        if script.last_start_attempt_at:
            if script.last_start_error or script.last_exit_reason:
                detail = {
                    "error": script.last_start_error or script.last_exit_reason,
                    "exit_code": script.last_exit_code,
                    "spawn_command": script.last_spawn_command,
                    "spawn_cwd": script.last_spawn_cwd,
                    "stdout_tail": script.logs(),
                }
                raise HTTPException(status_code=500, detail=detail)
            if script.name in WEB_APPS and wants_html:
                target_url = f"/apps/{_encoded_script_name(script.name)}"
                return HTMLResponse(
                    LAUNCHER_TEMPLATE.replace(
                        "{script_name}", html.escape(script.name)
                    )
                    .replace("{target_url}", target_url)
                    .replace("{has_ui}", "true"),
                    status_code=200,
                )
            raise HTTPException(
                status_code=503, detail=f"{script_name} is starting."
            )
        raise HTTPException(status_code=404, detail=f"{script_name} is not running.")

    if script.port is None:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream port not available for script: {script.name}",
        )

    target = f"http://127.0.0.1:{script.port}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    headers["X-Forwarded-Prefix"] = f"/apps/{_encoded_script_name(script.name)}"
    body = await request.body()
    PROXY_LOGGER.info(
        "Proxying app request script=%s subpath=%s port=%s url=%s",
        script.name,
        path,
        script.port,
        target,
    )

    timeout = httpx.Timeout(30.0, connect=2.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        resp = None
        start_time = time.monotonic()
        for attempt in range(2):
            try:
                resp = await client.request(
                    request.method,
                    target,
                    content=body,
                    headers=headers,
                )
                break
            except httpx.TimeoutException as exc:
                duration = time.monotonic() - start_time
                PROXY_LOGGER.warning(
                    "Proxy timeout script=%s subpath=%s port=%s url=%s duration=%.2fs error=%s",
                    script.name,
                    path,
                    script.port,
                    target,
                    duration,
                    exc,
                )
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "Upstream timeout",
                        "script": script.name,
                        "port": script.port,
                        "url": target,
                    },
                ) from exc
            except httpx.RequestError as exc:
                duration = time.monotonic() - start_time
                PROXY_LOGGER.warning(
                    "Proxy request error script=%s subpath=%s port=%s url=%s duration=%.2fs error=%s",
                    script.name,
                    path,
                    script.port,
                    target,
                    duration,
                    exc,
                )
                if isinstance(exc, httpx.ConnectError) and attempt < 1:
                    await asyncio.sleep(0.2)
                    continue
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "Upstream request failed",
                        "script": script.name,
                        "port": script.port,
                        "url": target,
                    },
                ) from exc

    assert resp is not None
    PROXY_LOGGER.info(
        "Proxy response script=%s subpath=%s port=%s status=%s",
        script.name,
        path,
        script.port,
        resp.status_code,
    )
    filtered_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in PROXY_HOP_HEADERS | PROXY_STRIP_HEADERS
    }
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=filtered_headers,
    )


@app.get("/api/bybit-monitor/settings")
async def bybit_monitor_settings() -> JSONResponse:
    return JSONResponse(_read_bybit_settings())


@app.post("/api/bybit-monitor/settings")
async def update_bybit_monitor_settings(payload: Dict[str, object]) -> JSONResponse:
    return JSONResponse(_update_bybit_settings(payload))


@app.get("/api/oanda-monitor/settings")
async def oanda_monitor_settings() -> JSONResponse:
    return JSONResponse(_read_oanda_settings())


@app.post("/api/oanda-monitor/settings")
async def update_oanda_monitor_settings(payload: Dict[str, object]) -> JSONResponse:
    return JSONResponse(_update_oanda_settings(payload))


@app.get("/api/bybit-monitor/status")
async def bybit_monitor_runtime_status() -> JSONResponse:
    return JSONResponse(_scanner_status_payload(BYBIT_RUNTIME_STATUS_PATH))


@app.get("/api/oanda-monitor/status")
async def oanda_monitor_runtime_status() -> JSONResponse:
    return JSONResponse(_scanner_status_payload(OANDA_RUNTIME_STATUS_PATH))


@app.get("/api/bybit-monitor/custom-alerts")
async def bybit_monitor_custom_alerts() -> JSONResponse:
    return JSONResponse({"alerts": bybit_monitor.get_custom_alerts(force=True)})


@app.post("/api/bybit-monitor/custom-alerts")
async def upsert_bybit_monitor_custom_alert(request: Request) -> JSONResponse:
    payload = await request.json()
    alert = bybit_monitor.upsert_custom_alert(payload or {})
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert": alert})


@app.delete("/api/bybit-monitor/custom-alerts/{alert_id}")
async def delete_bybit_monitor_custom_alert(alert_id: str) -> JSONResponse:
    bybit_monitor.delete_custom_alert(alert_id)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert_id": alert_id})


@app.post("/api/bybit-monitor/custom-alerts/{alert_id}/enabled")
async def set_bybit_monitor_custom_alert_enabled(
    alert_id: str, request: Request
) -> JSONResponse:
    payload = await request.json()
    enabled = bool((payload or {}).get("enabled", True))
    alert = bybit_monitor.set_custom_alert_enabled(alert_id, enabled)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert": alert})


@app.get("/api/oanda-monitor/custom-alerts")
async def oanda_monitor_custom_alerts() -> JSONResponse:
    return JSONResponse({"alerts": oanda_monitor.get_custom_alerts(force=True)})


@app.post("/api/oanda-monitor/custom-alerts")
async def upsert_oanda_monitor_custom_alert(request: Request) -> JSONResponse:
    payload = await request.json()
    alert = oanda_monitor.upsert_custom_alert(payload or {})
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert": alert})


@app.delete("/api/oanda-monitor/custom-alerts/{alert_id}")
async def delete_oanda_monitor_custom_alert(alert_id: str) -> JSONResponse:
    oanda_monitor.delete_custom_alert(alert_id)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert_id": alert_id})


@app.post("/api/oanda-monitor/custom-alerts/{alert_id}/enabled")
async def set_oanda_monitor_custom_alert_enabled(
    alert_id: str, request: Request
) -> JSONResponse:
    payload = await request.json()
    enabled = bool((payload or {}).get("enabled", True))
    alert = oanda_monitor.set_custom_alert_enabled(alert_id, enabled)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "alert": alert})


@app.get("/api/admin/outbound-traffic")
async def api_admin_outbound_traffic() -> JSONResponse:
    snapshot = _snapshot_outbound_traffic()
    totals = {
        "requests": sum(int(item.get("requests", 0)) for item in snapshot.values()),
        "bytes_sent": sum(int(item.get("bytes_sent", 0)) for item in snapshot.values()),
        "bytes_received": sum(int(item.get("bytes_received", 0)) for item in snapshot.values()),
    }
    return JSONResponse(
        {
            "generated_at": _utc_now_iso(),
            "totals": totals,
            "destinations": snapshot,
        }
    )


@app.get("/api/pending-webhooks")
async def list_pending_webhooks() -> JSONResponse:
    return JSONResponse({"items": _load_pending_webhooks()})


@app.post("/api/pending-webhooks")
async def upsert_pending_webhook(request: Request) -> JSONResponse:
    payload = await request.json()
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Pending webhook payload must be an object.")
    item = _upsert_pending_webhook(payload)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "item": item})


@app.delete("/api/pending-webhooks/{webhook_id}")
async def delete_pending_webhook(webhook_id: str) -> JSONResponse:
    deleted = _delete_pending_webhook(webhook_id)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "id": webhook_id, "deleted": deleted})


@app.post("/api/pending-webhooks/{webhook_id}/enabled")
async def set_pending_webhook_enabled(
    webhook_id: str, request: Request
) -> JSONResponse:
    payload = await request.json()
    enabled = bool((payload or {}).get("enabled", True))
    item = _set_pending_webhook_enabled(webhook_id, enabled)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "item": item})


@app.get("/api/watchlist")
async def get_watchlist() -> JSONResponse:
    return JSONResponse({"items": _get_watchlist()})


@app.post("/api/watchlist")
async def set_watchlist(request: Request) -> JSONResponse:
    payload = await request.json()
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Watchlist payload must be an object.")
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Watchlist items must be a list.")
    preliminary = _normalize_watchlist(items)
    resolved_items: List[str] = []
    for item in preliminary:
        token = str(item or "").strip()
        if not token:
            continue
        if _is_likely_fx_pair(token):
            resolved_items.append(_normalize_oanda_symbol_query(token))
            continue
        resolved = await _resolve_symbol_payload(token, "bybit", "linear")
        resolved_symbol = str((resolved or {}).get("resolved_symbol") or "").upper()
        if not resolved_symbol:
            raise HTTPException(status_code=400, detail=f"Unable to resolve watchlist symbol: {token}")
        resolved_items.append(resolved_symbol)
    normalized = _set_watchlist(resolved_items)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, "items": normalized})


@app.get("/api/alerts/backup")
async def backup_all_alerts() -> Response:
    alerts_payload = {
        "bybit": {"alerts": bybit_monitor.get_custom_alerts(force=True)},
        "oanda": {"alerts": oanda_monitor.get_custom_alerts(force=True)},
    }
    payload = {
        "version": 3,
        "savedAt": datetime.now(timezone.utc).isoformat(),
        "alerts": alerts_payload,
        "watchlist": _get_watchlist(),
        "pending_webhooks": _load_pending_webhooks(),
    }
    blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    headers = {"Content-Disposition": 'attachment; filename="codex-alerts-backup.json"'}
    return Response(content=blob, media_type="application/json", headers=headers)


@app.post("/api/alerts/restore")
async def restore_all_alerts(file: UploadFile = File(...)) -> JSONResponse:
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    restored = _restore_alerts_payload(data)
    _schedule_dropbox_upload_state_backup()
    return JSONResponse({"ok": True, **restored})



@app.post("/api/bybit-monitor/push-test")
async def bybit_monitor_push_test() -> JSONResponse:
    try:
        result = bybit_monitor.send_push_test()
        configured = bool(result.get("configured"))
        status_code = 200 if (result.get("sent") or not configured) else 400
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail=f"Failed to send Telegram alert test: {exc}"
        ) from exc


@app.post("/api/oanda-monitor/push-test")
async def oanda_monitor_push_test() -> JSONResponse:
    try:
        result = oanda_monitor.send_push_test()
        configured = bool(result.get("configured"))
        status_code = 200 if (result.get("sent") or not configured) else 400
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail=f"Failed to send Telegram alert test: {exc}"
        ) from exc


async def _background_start(script: ManagedScript) -> None:
    """Start a script without tying its output or failures to the HTTP response."""

    try:
        await script.start(ignore_starting=True)
    except Exception as exc:  # pragma: no cover - runtime protection
        # Capture failures in the per-script log instead of surfacing them to the caller.
        script.add_log(f"Failed to start: {exc}")
    finally:
        if script.startup_task is asyncio.current_task():
            script.startup_task = None


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    raw = script_manager.list_scripts()
    by_name = {str(s.get("name")): s for s in raw}

    merged: List[Dict[str, object]] = []
    for btn in get_merged_script_buttons():
        row: Dict[str, object] = {
            "id": btn["id"],
            "name": btn["name"],
            "label": btn["label"],
            "category": "Merged",
            "running": False,
            "starting": False,
            "open_url": btn["open_url"],
            "standalone": False,
            "dashboard_main_view": bool(btn.get("dashboard_main_view")),
        }
        if btn["name"] == "history":
            row["starting"] = bool(
                by_name.get("bybithistory-clone", {}).get("starting")
                or by_name.get("oanda_history-clone", {}).get("starting")
                or by_name.get("coinspot-clone", {}).get("starting")
            )
            row["running"] = bool(
                by_name.get("bybithistory-clone", {}).get("running")
                or by_name.get("oanda_history-clone", {}).get("running")
                or by_name.get("coinspot-clone", {}).get("running")
            )
        elif btn["name"] == "bounce-trader":
            row["starting"] = bool(by_name.get("bybit_trigger_bounce_trader", {}).get("starting"))
            row["running"] = bool(by_name.get("bybit_trigger_bounce_trader", {}).get("running"))
        elif btn["name"] == "fxweekend":
            fx_row = by_name.get("fxweekend-clone", {})
            row["starting"] = bool(fx_row.get("starting"))
            row["running"] = bool(fx_row.get("running"))
            row["last_error"] = fx_row.get("last_error")
            row["last_start_error"] = fx_row.get("last_start_error")
            row["last_exit_reason"] = fx_row.get("last_exit_reason")
        elif btn["name"] == "monitor":
            row["starting"] = bool(
                by_name.get("bybit_monitor", {}).get("starting")
                or by_name.get("oanda_monitor", {}).get("starting")
            )
            managed_running = bool(
                by_name.get("bybit_monitor", {}).get("running")
                or by_name.get("oanda_monitor", {}).get("running")
            )
            runtime_running = _scanner_runtime_is_live("bybit_monitor") or _scanner_runtime_is_live("oanda_monitor")
            row["running"] = managed_running or runtime_running
        merged.append(row)

    merged_source_names = get_merged_source_names()
    extras = [s for s in raw if str(s.get("name")) not in merged_source_names]
    for item in extras:
        if str(item.get("name")) == "ivindicator-clone":
            item["dashboard_main_view"] = True
    extras.sort(key=lambda s: str(s.get("label") or s.get("name")).lower())

    return JSONResponse(merged + extras)


@app.get("/api/scripts/{script_name:path}")
async def script_status(script_name: str) -> JSONResponse:
    script = script_manager.get(script_name)
    return JSONResponse(script.to_summary())


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_key(value: object, *, broker: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"buy", "long"}:
        return "buy"
    if raw in {"sell", "short"}:
        return "sell"
    if broker.lower() == "oanda":
        if raw == "long":
            return "buy"
        if raw == "short":
            return "sell"
    return raw


def _qty_matches(a: object, b: object) -> bool:
    a_num = _safe_float(a)
    b_num = _safe_float(b)
    if a_num is None or b_num is None:
        return str(a or "").strip() == str(b or "").strip()
    return abs(abs(a_num) - abs(b_num)) <= max(1e-9, abs(a_num), abs(b_num)) * 1e-6


PENDING_WEBHOOK_TERMINAL_STATUSES = {
    "TRIGGERING",
    "CONSUMED",
    "TRIGGERED",
    "SUBMITTED",
    "FILLED",
    "CLOSED",
    "CANCELLED",
    "FAILED_AFTER_SUBMIT",
}


def _pending_webhook_is_terminal(status: object) -> bool:
    return str(status or "").strip().upper() in PENDING_WEBHOOK_TERMINAL_STATUSES


def _pending_webhook_is_superseded(
    pending: Dict[str, object],
    open_items: List[Dict[str, object]],
    trade_contexts: Optional[List[Dict[str, object]]] = None,
    consumed_open_indices: Optional[Set[int]] = None,
) -> bool:
    pending_id = str(pending.get("id", "")).strip()
    pending_broker = str(pending.get("broker", "")).strip().lower()
    pending_category = str(pending.get("category", "")).strip().lower()
    pending_account = str(pending.get("account", "")).strip().lower()
    pending_order_id = str(pending.get("order_id", "")).strip()
    pending_order_link_id = str(pending.get("order_link_id", "")).strip()
    pending_trade_id = str(pending.get("trade_id", "")).strip()
    pending_instrument = str(pending.get("instrument", "")).strip().upper()
    pending_side = _side_key(pending.get("side"), broker=str(pending.get("broker", "")))
    pending_size = pending.get("size")
    contexts = trade_contexts if isinstance(trade_contexts, list) else _load_trade_contexts()

    exact_order_ids: Set[str] = set()
    exact_order_link_ids: Set[str] = set()
    exact_trade_ids: Set[str] = set()
    if pending_order_id:
        exact_order_ids.add(pending_order_id)
    if pending_order_link_id:
        exact_order_link_ids.add(pending_order_link_id)
    if pending_trade_id:
        exact_trade_ids.add(pending_trade_id)
    for ctx in contexts:
        if not isinstance(ctx, dict):
            continue
        if pending_id and str(ctx.get("pending_webhook_id") or "").strip() != pending_id:
            continue
        ctx_order_id = str(ctx.get("order_id") or "").strip()
        ctx_order_link_id = str(ctx.get("order_link_id") or "").strip()
        ctx_parent_order_link_id = str(ctx.get("parent_order_link_id") or "").strip()
        ctx_trade_id = str(ctx.get("trade_id") or "").strip()
        if ctx_order_id:
            exact_order_ids.add(ctx_order_id)
        if ctx_order_link_id:
            exact_order_link_ids.add(ctx_order_link_id)
        if ctx_parent_order_link_id:
            exact_order_link_ids.add(ctx_parent_order_link_id)
        if ctx_trade_id:
            exact_trade_ids.add(ctx_trade_id)
    has_exact_linkage = bool(exact_order_ids or exact_order_link_ids or exact_trade_ids)

    for idx, item in enumerate(open_items):
        if consumed_open_indices and idx in consumed_open_indices:
            continue
        if str(item.get("broker", "")).strip().upper() == "WEBHOOK":
            continue

        open_id = str(item.get("id") or "").strip()
        open_order_link_id = str(item.get("order_link_id") or "").strip()
        open_trade_id = str(item.get("trade_id") or "").strip()
        if open_id and open_id in exact_order_ids:
            if consumed_open_indices is not None:
                consumed_open_indices.add(idx)
            return True
        if open_order_link_id and open_order_link_id in exact_order_link_ids:
            if consumed_open_indices is not None:
                consumed_open_indices.add(idx)
            return True
        if open_trade_id and open_trade_id in exact_trade_ids:
            if consumed_open_indices is not None:
                consumed_open_indices.add(idx)
            return True

    if has_exact_linkage:
        return False

    matching_indices: List[int] = []
    pending_family = "oanda" if pending_broker == "oanda" or pending_category == "forex" else "bybit"
    for idx, item in enumerate(open_items):
        if consumed_open_indices and idx in consumed_open_indices:
            continue
        item_broker = str(item.get("broker") or "").strip().lower()
        if item_broker == "webhook":
            continue
        item_category = str(item.get("category") or "").strip().lower()
        item_family = "oanda" if item_broker == "oanda" or item_category == "forex" else "bybit"
        if item_family != pending_family:
            continue
        if pending_account and str(item.get("account") or "").strip().lower() != pending_account:
            continue
        instrument = str(item.get("instrument", "")).strip().upper()
        side = _side_key(item.get("side"), broker=str(item.get("broker", "")))
        size = item.get("size")
        if (
            pending_instrument
            and instrument == pending_instrument
            and side
            and side == pending_side
            and _qty_matches(pending_size, size)
        ):
            matching_indices.append(idx)

    if len(matching_indices) == 1:
        if consumed_open_indices is not None:
            consumed_open_indices.add(matching_indices[0])
        return True
    return False


def _clean_pending_webhooks_for_open_items(
    pending_items: List[Dict[str, object]], open_items: List[Dict[str, object]]
) -> Tuple[List[Dict[str, object]], bool]:
    contexts = _load_trade_contexts()
    consumed_open_indices: Set[int] = set()
    filtered: List[Dict[str, object]] = []
    changed = False
    visible_statuses = {
        "WAITING",
        "TRIGGERING",
        "FAILED_BEFORE_SUBMIT",
        "BYBIT_REJECTED",
        "ORDER_CREATED_TPSL_FAILED",
        "PENDING_NOT_FOUND",
    }
    for pending in pending_items:
        status = str(pending.get("status") or "").strip().upper()
        if status in {"CONSUMED", "CLOSED", "CANCELLED"}:
            changed = True
            continue
        if status not in visible_statuses:
            status = "WAITING"
            pending = {**pending, "status": status}
        if not bool(pending.get("enabled", True)):
            changed = True
            continue
        if status == "WAITING" and (pending.get("consumed_at") or pending.get("triggered_at")):
            changed = True
            continue
        if _pending_webhook_is_superseded(
            pending,
            open_items,
            trade_contexts=contexts,
            consumed_open_indices=consumed_open_indices,
        ):
            changed = True
            continue
        filtered.append(pending)
    return filtered, changed


def _filter_pending_webhooks(
    pending_items: List[Dict[str, object]], open_items: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    filtered, _changed = _clean_pending_webhooks_for_open_items(pending_items, open_items)
    return filtered


@app.get("/api/open-orders/version")
async def open_orders_version() -> JSONResponse:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/api/open-orders/version", as_json=True)  # type: ignore[return-value]
    return JSONResponse(
        {
            "version": int(_OPEN_ORDERS_CACHE.get("version") or 0),
            "updated_at": _utc_now_iso(),
        }
    )


@app.get("/api/open-orders")
async def list_open_orders(force: bool = Query(False)) -> JSONResponse:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/api/open-orders", as_json=True)  # type: ignore[return-value]
    now = time.time()
    cached_payload = _OPEN_ORDERS_CACHE.get("payload")
    expires_at = float(_OPEN_ORDERS_CACHE.get("expires_at") or 0.0)
    if not force and isinstance(cached_payload, dict) and now < expires_at:
        fresh = dict(cached_payload)
        fresh["stale"] = False
        fresh.setdefault("updated_at", _utc_now_iso())
        fresh.setdefault("last_success_at", _OPEN_ORDERS_CACHE.get("last_success_at"))
        return JSONResponse(fresh)

    async with _OPEN_ORDERS_CACHE_LOCK:
        now = time.time()
        cached_payload = _OPEN_ORDERS_CACHE.get("payload")
        expires_at = float(_OPEN_ORDERS_CACHE.get("expires_at") or 0.0)
        if not force and isinstance(cached_payload, dict) and now < expires_at:
            fresh = dict(cached_payload)
            fresh["stale"] = False
            fresh.setdefault("updated_at", _utc_now_iso())
            fresh.setdefault("last_success_at", _OPEN_ORDERS_CACHE.get("last_success_at"))
            return JSONResponse(fresh)

        items: List[Dict[str, object]] = []
        errors: List[Dict[str, str]] = []

        for account in ("live", "demo"):
            try:
                cfg = _get_oanda_config(account)
                account_ids: List[str] = []
                configured_account_id = str(cfg.get("account_id") or "").strip()
                if configured_account_id:
                    account_ids.append(configured_account_id)

                owned_accounts: List[Dict[str, object]] = []
                account_tags: Dict[str, List[str]] = {}
                try:
                    owned_accounts = await _get_cached_oanda_accounts(
                        base_url=cfg["base_url"],
                        api_key=cfg["token"],
                    )
                except Exception as discovery_exc:
                    errors.append(
                        {
                            "broker": "OANDA",
                            "account": account,
                            "category": "forex",
                            "message": _format_source_exception(
                                discovery_exc,
                                broker="OANDA",
                                account=account,
                                endpoint="/v3/accounts",
                                account_id="discovery",
                            ),
                        }
                    )

                for acct in owned_accounts:
                    acct_id = str(acct.get("id") or "").strip()
                    if not acct_id:
                        continue
                    account_ids.append(acct_id)
                    account_tags[acct_id] = [str(t).upper() for t in (acct.get("tags") or [])]

                deduped_ids: List[str] = []
                seen_account_ids: Set[str] = set()
                for acct_id in account_ids:
                    if acct_id in seen_account_ids:
                        continue
                    deduped_ids.append(acct_id)
                    seen_account_ids.add(acct_id)
                account_ids = deduped_ids

                tasks = [
                    _collect_oanda_open_items(
                        base_url=cfg["base_url"],
                        account_id=acct_id,
                        api_key=cfg["token"],
                        account_context=account,
                    )
                    for acct_id in account_ids
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                oanda_items: List[Dict[str, object]] = []
                oanda_errors: List[Dict[str, str]] = []
                for acct_id, result in zip(account_ids, results):
                    if isinstance(result, Exception):
                        oanda_errors.append(
                            {
                                "broker": "OANDA",
                                "account": account,
                                "category": "forex",
                                "message": _format_source_exception(
                                    result,
                                    broker="OANDA",
                                    account=account,
                                    endpoint="/v3/accounts/{accountID}/openItems",
                                    account_id=acct_id,
                                ),
                            }
                        )
                        continue

                    result_items = result.get("items", []) if isinstance(result, dict) else []
                    result_errors = result.get("errors", []) if isinstance(result, dict) else []

                    for endpoint_error in result_errors:
                        oanda_errors.append(
                            {
                                "broker": "OANDA",
                                "account": account,
                                "category": "forex",
                                "message": f"{acct_id}: {str(endpoint_error).strip() or 'Unknown OANDA endpoint error'}",
                            }
                        )

                    tags = account_tags.get(acct_id, [])
                    for row in result_items:
                        row["account_id"] = acct_id
                        if "MT4" in tags:
                            row["account_label_suffix"] = "MT4"
                    oanda_items.extend(result_items)

                items.extend(oanda_items)
                errors.extend(oanda_errors)
                BYBIT_LOGGER.info(
                    "OPEN_ORDERS oanda account=%s owner_accounts=%s items=%s errors=%s",
                    account,
                    len(account_ids),
                    len(oanda_items),
                    len(oanda_errors),
                )
            except Exception as exc:
                errors.append(
                    {
                        "broker": "OANDA",
                        "account": account,
                        "category": "forex",
                        "message": _format_source_exception(
                            exc,
                            broker="OANDA",
                            account=account,
                            endpoint="open-orders",
                            account_id="unknown",
                        ),
                    }
                )

        for account in ("live", "demo"):
            try:
                _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(
                    account
                )
                if not api_key or not api_secret:
                    raise ValueError("Bybit API credentials are not configured.")
                result = await _collect_bybit_open_items(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    account_context=account,
                )
                items.extend(result.get("items", []))
                errors.extend(result.get("errors", []))
                BYBIT_LOGGER.info(
                    "OPEN_ORDERS bybit account=%s items=%s errors=%s",
                    account,
                    len(result.get("items", [])),
                    len(result.get("errors", [])),
                )
            except Exception as exc:
                errors.append(
                    {
                        "broker": "Bybit",
                        "account": account,
                        "category": "unknown",
                        "message": str(exc),
                    }
                )

        pending = _load_pending_webhooks()
        if pending:
            pending, pending_changed = _clean_pending_webhooks_for_open_items(pending, items)
            if pending_changed:
                _save_pending_webhooks(pending)
                _invalidate_open_orders_cache()
                _schedule_dropbox_upload_state_backup()
            items.extend(pending)

        try:
            bounce_script_running = script_manager.get("bybit_trigger_bounce_trader").is_running
        except Exception:
            bounce_script_running = False

        if bounce_script_running:
            sessions = _load_bounce_traders()
            changed = False
            now_dt = datetime.now(timezone.utc)

            bybit_orders = [
                row
                for row in items
                if str(row.get("broker", "")).strip().lower() == "bybit"
                and str(row.get("type", "")).strip().lower() == "order"
            ]
            bybit_positions = [
                row
                for row in items
                if str(row.get("broker", "")).strip().lower() == "bybit"
                and str(row.get("type", "")).strip().lower() == "position"
            ]
            oanda_orders = [
                row
                for row in items
                if str(row.get("broker", "")).strip().lower() == "oanda"
                and str(row.get("type", "")).strip().lower() == "order"
            ]
            oanda_positions = [
                row
                for row in items
                if str(row.get("broker", "")).strip().lower() == "oanda"
                and str(row.get("type", "")).strip().lower() in {"position", "trade"}
            ]

            for session in sessions:
                if not bool(session.get("running")):
                    continue

                broker = str(session.get("broker") or "").strip().lower()
                market = str(session.get("market") or "").strip().lower()
                if broker not in {"bybit", "oanda"}:
                    broker = "oanda" if market == "fx" else "bybit"

                instrument = str(session.get("instrument") or "").strip().upper()
                side = str(session.get("side") or "").strip().title()
                account = str(session.get("account") or "").strip().lower() or "demo"
                category = str(session.get("category") or "").strip().lower() or ("forex" if broker == "oanda" else "linear")
                order_link_id = str(session.get("order_link_id") or "").strip()

                seen_order = bool(session.get("seen_order"))
                if broker == "bybit":
                    if order_link_id:
                        if any(
                            str(row.get("order_link_id") or "").strip() == order_link_id
                            and str(row.get("account") or "").strip().lower() == account
                            and str(row.get("category") or "").strip().lower() == category
                            for row in bybit_orders
                        ) and not seen_order:
                            session["seen_order"] = True
                            changed = True
                            seen_order = True

                    has_position = any(
                        str(row.get("instrument") or "").strip().upper() == instrument
                        and str(row.get("side") or "").strip().title() == side
                        and str(row.get("account") or "").strip().lower() == account
                        and str(row.get("category") or "").strip().lower() == category
                        for row in bybit_positions
                    )
                else:
                    if any(
                        str(row.get("instrument") or "").strip().upper() == instrument
                        and str(row.get("account") or "").strip().lower() == account
                        for row in oanda_orders
                    ) and not seen_order:
                        session["seen_order"] = True
                        changed = True
                        seen_order = True

                    target_side = "Long" if side == "Buy" else "Short"
                    has_position = any(
                        str(row.get("instrument") or "").strip().upper() == instrument
                        and str(row.get("account") or "").strip().lower() == account
                        and str(row.get("side") or "").strip().title() == target_side
                        for row in oanda_positions
                    )

                started_at = _to_dt_utc(session.get("started_at"))
                age_seconds = (now_dt - started_at).total_seconds() if started_at else 0.0

                if has_position and (seen_order or age_seconds >= 30):
                    if bool(session.get("show_in_open_orders", True)):
                        session["show_in_open_orders"] = False
                        session["status"] = "filled"
                        session["updated_at"] = now_dt.isoformat()
                        changed = True
                    continue

                if not bool(session.get("show_in_open_orders", True)):
                    continue

                items.append(
                    {
                        "broker": "BOUNCE",
                        "account": account,
                        "category": category,
                        "instrument": instrument,
                        "type": "Bounce",
                        "side": side,
                        "size": "—",
                        "entry_price": None,
                        "order_price": None,
                        "current_price": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "leverage": None,
                        "opened_at": session.get("started_at"),
                        "id": session.get("id"),
                        "order_link_id": order_link_id,
                        "status": "WAITING",
                    }
                )

            if changed:
                _save_bounce_traders(sessions)

        def _same_price(a: object, b: object, tol: float = 1e-9) -> bool:
            try:
                return abs(float(a) - float(b)) <= tol
            except (TypeError, ValueError):
                return False

        def _is_child_exit_order(parent: Dict[str, object], child: Dict[str, object]) -> bool:
            if str(parent.get("broker", "")).strip().lower() != "bybit":
                return False
            if str(parent.get("type", "")).strip().lower() != "position":
                return False
            if str(child.get("type", "")).strip().lower() != "order":
                return False
            if str(child.get("broker", "")).strip().lower() != "bybit":
                return False
            if str(parent.get("account", "")).strip().lower() != str(child.get("account", "")).strip().lower():
                return False
            if str(parent.get("category", "")).strip().lower() != str(child.get("category", "")).strip().lower():
                return False
            if str(parent.get("instrument", "")).strip().upper() != str(child.get("instrument", "")).strip().upper():
                return False

            child_status = str(child.get("status", "")).strip().upper()
            if child_status not in {"UNTRIGGERED", "OPEN", "NEW", "CREATED"}:
                return False

            parent_side = str(parent.get("side", "")).strip().lower()
            child_side = str(child.get("side", "")).strip().lower()
            if parent_side in {"buy", "long"} and child_side not in {"sell", "short"}:
                return False
            if parent_side in {"sell", "short"} and child_side not in {"buy", "long"}:
                return False

            child_price = child.get("current_price") or child.get("order_price")
            return (
                _same_price(child_price, parent.get("stop_loss"))
                or _same_price(child_price, parent.get("take_profit"))
            )

        grouped: List[Dict[str, object]] = []
        used_child_indices: Set[int] = set()

        for _parent_idx, item in enumerate(items):
            if str(item.get("type", "")).strip().lower() != "position":
                continue
            parent = dict(item)
            parent["children"] = []
            for child_idx, child in enumerate(items):
                if child_idx in used_child_indices:
                    continue
                if _is_child_exit_order(parent, child):
                    parent["children"].append(dict(child))
                    used_child_indices.add(child_idx)
            grouped.append(parent)

        for idx, item in enumerate(items):
            if idx in used_child_indices:
                continue
            if str(item.get("type", "")).strip().lower() == "position":
                continue
            row = dict(item)
            row["children"] = []
            grouped.append(row)

        updated_at = _utc_now_iso()
        payload: Dict[str, object] = {
            "items": grouped,
            "errors": errors,
            "stale": bool(errors),
            "updated_at": updated_at,
            "last_success_at": _OPEN_ORDERS_CACHE.get("last_success_at"),
            "version": int(_OPEN_ORDERS_CACHE.get("version") or 0),
        }

        _OPEN_ORDERS_CACHE["payload"] = dict(payload)
        _OPEN_ORDERS_CACHE["expires_at"] = time.time() + _OPEN_ORDERS_CACHE_TTL_SECONDS
        if not errors:
            _OPEN_ORDERS_CACHE["last_success_at"] = updated_at
            payload["last_success_at"] = updated_at

        return JSONResponse(payload)


@app.get("/api/recent-trades")
async def recent_trades(limit: int = 25) -> JSONResponse:
    _repair_persisted_oanda_trade_rows()
    _repair_persisted_bybit_trade_context_fields()
    repaired_open_rows, repaired_open_count = _repair_persisted_bybit_open_times(_get_trading_journal_rows())
    if repaired_open_count:
        _set_trading_journal_rows(repaired_open_rows)
    backfilled_rows, backfilled_count = _backfill_persisted_bybit_trade_fields(repaired_open_rows)
    if backfilled_count:
        _set_trading_journal_rows(backfilled_rows)
    repaired_open_rows = backfilled_rows
    sanitized_rows, sanitize_stats = _sanitize_bybit_demo_rows(repaired_open_rows)
    if int(sanitize_stats.get("changed", 0)):
        _set_trading_journal_rows(sanitized_rows)
    rows = [
        r
        for r in sanitized_rows
        if isinstance(r, dict) and not _exclude_bybit_demo_row(r)
    ]
    rows = [_backfill_trade_row_context_fields(r) for r in rows]
    rows = _enrich_trade_row_metrics(
        _calc_balance_after_trade(rows, _get_excel_account_balances())
    )

    items: List[Dict[str, object]] = []
    for row in rows:
        if _row_type(row) != "trade":
            continue

        status = str(row.get("status") or row.get("state") or "").strip().lower()
        event = str(row.get("event") or row.get("type") or row.get("transaction_type") or "").strip().lower()
        if any(x in event for x in ("deposit", "withdraw")):
            continue
        if status and status not in {"closed", "close", "filled", "complete", "completed"}:
            continue

        closed_at = (
            row.get("close_time")
            or row.get("closed_at")
            or row.get("exit_time")
            or row.get("date")
        )
        if not closed_at:
            continue

        result_cash = (
            row.get("realized_pnl")
            if row.get("realized_pnl") is not None
            else row.get("net_profit")
        )
        pnl_num = _to_float(result_cash)
        balance_after = _to_float(row.get("balance_after_trade"))
        balance_before = None
        if balance_after is not None and pnl_num is not None:
            balance_before = balance_after - pnl_num

        result_pct = None
        if balance_before not in (None, 0) and pnl_num is not None:
            result_pct = (pnl_num / balance_before) * 100.0

        outcome = "Breakeven"
        if result_pct is not None:
            if result_pct > 0:
                outcome = "Win"
            elif result_pct < 0:
                outcome = "Loss"
        elif pnl_num is not None:
            if pnl_num > 0:
                outcome = "Win"
            elif pnl_num < 0:
                outcome = "Loss"

        refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
        opened_at = row.get("open_time") or row.get("opened_at") or row.get("entry_time")
        duration_seconds = _trade_duration_seconds(
            {
                "row_type": "trade",
                "open_time": opened_at,
                "close_time": closed_at,
            }
        )
        items.append(
            {
                "_row": row,
                "_row_id": row.get("id"),
                "_row_source": row.get("source"),
                "_row_order_id": refs.get("orderId"),
                "account": row.get("account_label") or row.get("account") or row.get("source"),
                "symbol": row.get("symbol") or row.get("instrument") or row.get("symbol_raw"),
                "side": row.get("side") or row.get("direction"),
                "timeframe": _normalize_timeframe(
                    row.get("timeframe")
                    or ((row.get("metrics") or {}).get("timeframe") if isinstance(row.get("metrics"), dict) else "")
                ),
                "opened_at": opened_at,
                "closed_at": closed_at,
                "stop_loss": row.get("stop_loss"),
                "take_profit": row.get("take_profit"),
                "entry_price": row.get("entry_price"),
                "exit_price": row.get("exit_price"),
                "fees": row.get("fees") if row.get("fees") is not None else row.get("commission"),
                "result_cash": pnl_num,
                "result_currency": row.get("realized_pnl_currency") or row.get("currency") or row.get("account_currency") or "USD",
                "result_pct": result_pct,
                "_row_balance_after_trade": row.get("balance_after_trade"),
                "_row_entry_price": row.get("entry_price"),
                "_row_exit_price": row.get("exit_price"),
                "_row_realized_pnl": row.get("realized_pnl")
                if row.get("realized_pnl") is not None
                else row.get("net_profit"),
                "_row_updated_at": row.get("updated_at"),
                "outcome": outcome,
                "duration_seconds": duration_seconds,
                "chart_row_id": row.get("id"),
                "chart_available": True,
            }
        )

    for row in _get_monthly_aud_revaluation_rows():
        if not isinstance(row, dict):
            continue
        closed_at = row.get("close_time") or row.get("closed_at")
        if not closed_at:
            continue
        result_cash = _to_float(row.get("result_cash"))
        items.append(
            {
                "_row": row,
                "_row_id": row.get("id"),
                "_row_source": row.get("source"),
                "_row_order_id": None,
                "row_type": row.get("row_type"),
                "account": row.get("account_label") or "Bybit Live",
                "symbol": row.get("symbol") or "MONTHLY AUD P/L",
                "side": row.get("side"),
                "opened_at": row.get("open_time") or row.get("opened_at"),
                "closed_at": closed_at,
                "stop_loss": row.get("stop_loss"),
                "take_profit": row.get("take_profit"),
                "entry_price": row.get("entry_price"),
                "exit_price": row.get("exit_price"),
                "fees": row.get("fees"),
                "result_cash": result_cash,
                "result_currency": row.get("result_currency") or "AUD",
                "result_pct": row.get("result_pct"),
                "_row_balance_after_trade": None,
                "_row_entry_price": row.get("entry_price"),
                "_row_exit_price": row.get("exit_price"),
                "_row_realized_pnl": result_cash,
                "_row_updated_at": row.get("updated_at"),
                "outcome": row.get("outcome"),
                "duration_seconds": row.get("duration_seconds"),
                "chart_row_id": row.get("id"),
                "chart_available": False,
            }
        )

    trade_items = [item for item in items if _row_type(item.get("_row") if isinstance(item.get("_row"), dict) else {}) == "trade"]
    non_trade_items = [item for item in items if item not in trade_items]

    def _norm_account(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _norm_side(value: object) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"buy", "long"}:
            return "buy"
        if raw in {"sell", "short"}:
            return "sell"
        return raw

    def _rounded_num(value: object, dp: int = 8) -> str:
        num = _to_float(value)
        if num is None:
            return ""
        return f"{num:.{dp}f}"

    def _trade_key(item: Dict[str, object]) -> str:
        order_id = str(item.get("_row_order_id") or "").strip()
        row_data = item.get("_row") if isinstance(item.get("_row"), dict) else {}
        if order_id and _is_bybit_demo_trade_row(row_data):
            return f"order:{order_id}"
        return "|".join(
            [
                _norm_account(item.get("account")),
                str(item.get("symbol") or "").strip().upper(),
                str(_canonical_trade_epoch_second(item.get("opened_at")) or ""),
                str(_canonical_trade_epoch_second(item.get("closed_at")) or ""),
                _rounded_num(row_data.get("qty"), 8),
                _rounded_num(item.get("_row_entry_price")),
                _rounded_num(item.get("_row_exit_price")),
                _rounded_num(item.get("fees"), 6),
                _rounded_num(item.get("_row_realized_pnl"), 6),
            ]
        )

    def _trade_score(item: Dict[str, object]) -> Tuple[int, int, int, int, int]:
        has_tpsl = int(item.get("stop_loss") is not None or item.get("take_profit") is not None)
        has_order_id = int(bool(str(item.get("_row_order_id") or "").strip()))
        has_balance = int(item.get("_row_balance_after_trade") is not None)
        src = str(item.get("_row_source") or "").strip().lower()
        source_rank = 1 if src == "bybit" else 0
        updated_rank = _canonical_trade_epoch_second(item.get("_row_updated_at")) or -1
        return has_tpsl, has_order_id, has_balance, source_rank, updated_rank

    deduped: Dict[str, Dict[str, object]] = {}
    for item in trade_items:
        key = _trade_key(item)
        prev = deduped.get(key)
        if prev is None or _trade_score(item) > _trade_score(prev):
            deduped[key] = item
    first_pass = list(deduped.values())

    second_pass: Dict[str, Dict[str, object]] = {}
    for item in first_pass:
        row_data = item.get("_row") if isinstance(item.get("_row"), dict) else {}
        if _is_bybit_demo_trade_row(row_data):
            key = "|".join(
                [
                    _norm_account(item.get("account")),
                    str(item.get("symbol") or "").strip().upper(),
                    str(_canonical_trade_epoch_second(item.get("opened_at")) or ""),
                    str(_canonical_trade_epoch_second(item.get("closed_at")) or ""),
                    _rounded_num(row_data.get("qty"), 8),
                    _rounded_num(item.get("_row_entry_price")),
                    _rounded_num(item.get("_row_exit_price")),
                    _rounded_num(item.get("fees"), 6),
                    _rounded_num(item.get("_row_realized_pnl"), 6),
                ]
            )
        else:
            key = _trade_key(item)
        prev = second_pass.get(key)
        if prev is None or _trade_score(item) > _trade_score(prev):
            second_pass[key] = item
    items = list(second_pass.values()) + non_trade_items

    def _sort_ts(value: object) -> float:
        try:
            return float(pd.to_datetime(value).timestamp())
        except Exception:
            return float("-inf")

    items.sort(key=lambda r: _sort_ts(r.get("closed_at")), reverse=True)
    public_items = []
    for item in items[: max(1, min(limit, 200))]:
        copy = dict(item)
        copy["chart_row_id"] = item.get("_row_id")
        if item.get("chart_available") is None:
            copy["chart_available"] = _row_type(item.get("_row") if isinstance(item.get("_row"), dict) else {}) == "trade"
        copy.pop("_row_id", None)
        copy.pop("_row_source", None)
        copy.pop("_row_order_id", None)
        copy.pop("_row_balance_after_trade", None)
        copy.pop("_row_realized_pnl", None)
        copy.pop("_row_updated_at", None)
        copy.pop("_row", None)
        public_items.append(copy)
    return JSONResponse({"items": public_items})


@app.get("/api/diagnostics/monthly-aud-reval")
async def diagnostics_monthly_aud_reval() -> JSONResponse:
    rows = _get_monthly_aud_revaluation_rows()
    state = _load_json_file(MONTHLY_AUD_REVALUATION_STATE_PATH, {})
    latest = rows[0] if rows else None
    march_id = "monthly_aud_reval:bybit_live:2026-03"
    march_row = next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == march_id),
        None,
    )
    last_boundary = state.get("last_boundary_resolution") if isinstance(state, dict) else {}
    last_oanda_window = state.get("last_oanda_window") if isinstance(state, dict) else {}
    start_boundary = last_boundary.get("start") if isinstance(last_boundary, dict) else {}
    end_boundary = last_boundary.get("end") if isinstance(last_boundary, dict) else {}
    start_window = (last_oanda_window.get("start_window") if isinstance(last_oanda_window, dict) else {}) or {}
    end_window = (last_oanda_window.get("end_window") if isinstance(last_oanda_window, dict) else {}) or {}
    return JSONResponse(
        {
            "ok": True,
            "rows_count": len(rows),
            "row_count": len(rows),
            "march_2026_exists": isinstance(march_row, dict),
            "march_2026_row_id": march_row.get("id") if isinstance(march_row, dict) else None,
            "latest_row_id": latest.get("id") if isinstance(latest, dict) else None,
            "latest_period_month": ((latest or {}).get("raw_refs") or {}).get("period_month") if isinstance(latest, dict) else None,
            "last_sync_result": (state or {}).get("last_result") if isinstance(state, dict) else None,
            "last_error_code": (state or {}).get("last_error_code") if isinstance(state, dict) else None,
            "last_error": (state or {}).get("last_error") if isinstance(state, dict) else None,
            "last_stage": (state or {}).get("stage") if isinstance(state, dict) else None,
            "month_key": (state or {}).get("month_key") if isinstance(state, dict) else None,
            "traceback": (state or {}).get("traceback") if isinstance(state, dict) else None,
            "last_resolved_start_balance": start_boundary.get("resolved_balance") if isinstance(start_boundary, dict) else None,
            "last_resolved_end_balance": end_boundary.get("resolved_balance") if isinstance(end_boundary, dict) else None,
            "last_start_balance_source": start_boundary.get("balance_source") if isinstance(start_boundary, dict) else None,
            "last_end_balance_source": end_boundary.get("balance_source") if isinstance(end_boundary, dict) else None,
            "last_boundary_resolution": last_boundary if isinstance(last_boundary, dict) else None,
            "last_oanda_request_window": last_oanda_window if isinstance(last_oanda_window, dict) else None,
            "request_start_utc": end_window.get("request_start_utc") or start_window.get("request_start_utc"),
            "request_end_utc": end_window.get("request_end_utc") or start_window.get("request_end_utc"),
            "clamped_end_utc": end_window.get("clamped_end_utc") or start_window.get("clamped_end_utc"),
            "now_utc": end_window.get("now_utc") or start_window.get("now_utc"),
            "rows": rows[:12],
            "updated_at": _utc_now_iso(),
        }
    )


@app.get("/api/oanda-inactivity-status")
async def oanda_inactivity_status() -> JSONResponse:
    now = time.time()
    cached = _OANDA_INACTIVITY_CACHE.get("payload")
    cached_status = int(_OANDA_INACTIVITY_CACHE.get("status_code") or 200)
    if (
        isinstance(cached, dict)
        and float(_OANDA_INACTIVITY_CACHE.get("expires_at") or 0.0) > now
    ):
        return JSONResponse(cached, status_code=cached_status)

    status_code = 200
    ttl_seconds = _OANDA_INACTIVITY_CACHE_TTL_SECONDS
    try:
        payload = await _build_oanda_inactivity_status()
    except OandaUpstreamHTTPError as exc:
        status_code = 503 if exc.transient else 502
        ttl_seconds = _OANDA_INACTIVITY_ERROR_CACHE_TTL_SECONDS
        payload = {
            "ok": False,
            "mode": "live",
            "status": "unavailable",
            "detail": str(exc),
            "upstream_status": exc.status_code,
            "transient": exc.transient,
            "body_summary": exc.body_summary,
            "error": str(exc),
            "last_live_fill_at": None,
            "open_trade_count": None,
            "open_position_count": None,
            "has_open_positions": None,
            "inactivity_threshold_at": None,
            "earliest_fee_date": None,
            "policy_months_without_trade": 12,
            "monthly_fee_aud": 10,
            "seconds_until_threshold": None,
            "updated_at": _utc_now_iso(),
        }
    except Exception as exc:
        status_code = 500
        ttl_seconds = _OANDA_INACTIVITY_ERROR_CACHE_TTL_SECONDS
        payload = {
            "ok": False,
            "mode": "live",
            "status": "unavailable",
            "detail": str(exc),
            "upstream_status": None,
            "transient": False,
            "body_summary": None,
            "error": str(exc),
            "upstream_status": None,
            "upstream_error_message": None,
            "endpoint": None,
            "retry_exhausted": None,
            "maintenance_detected": False,
            "last_live_fill_at": None,
            "open_trade_count": None,
            "open_position_count": None,
            "has_open_positions": None,
            "inactivity_threshold_at": None,
            "earliest_fee_date": None,
            "policy_months_without_trade": 12,
            "monthly_fee_aud": 10,
            "seconds_until_threshold": None,
            "updated_at": _utc_now_iso(),
        }
    _OANDA_INACTIVITY_CACHE["payload"] = payload
    _OANDA_INACTIVITY_CACHE["status_code"] = status_code
    _OANDA_INACTIVITY_CACHE["expires_at"] = now + ttl_seconds
    return JSONResponse(payload, status_code=status_code)


@app.post("/api/open-orders/close")
async def close_open_order(item: Dict[str, Any] = Body(...)) -> JSONResponse:
    if APP_PROFILE == "render":
        return _local_only_disabled_response("/api/open-orders/close", as_json=True)  # type: ignore[return-value]
    broker = str(item.get("broker", "")).strip().lower()
    account = str(item.get("account", "live")).strip().lower()
    category = str(item.get("category", "")).strip().lower()
    instrument = str(item.get("instrument", "")).strip().upper()
    item_type = str(item.get("type", "")).strip().lower()
    item_id = str(item.get("id", "")).strip()

    if not broker or not item_type or not item_id:
        raise HTTPException(status_code=400, detail="Missing broker/type/id in request.")

    action = "cancel" if item_type == "order" else "close"
    action_requested = False

    try:
        if broker == "webhook" or item_type == "webhook":
            _mark_trade_context_closed_or_cancelled(
                pending_webhook_id=item_id,
                status="CANCELLED",
            )
            _delete_pending_webhook(item_id)
            action = "cancel"
            action_requested = True
        elif broker == "bybit":
            _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(
                "demo" if account in {"demo", "practice"} else "live"
            )
            if not api_key or not api_secret:
                raise ValueError("Bybit API credentials are not configured.")

            if action == "cancel":
                if not instrument:
                    raise ValueError("Bybit instrument is missing.")
                await _cancel_bybit_order(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category=category or "linear",
                    symbol=instrument,
                    order_id=item_id,
                )
            else:
                position_idx_raw = item.get("position_idx")
                position_idx = None
                if position_idx_raw is not None and str(position_idx_raw).strip() != "":
                    try:
                        position_idx = int(position_idx_raw)
                    except (TypeError, ValueError):
                        position_idx = None

                if not instrument:
                    raise ValueError("Bybit instrument is missing.")
                qty = item.get("size")
                if qty is None or str(qty).strip() == "" or str(qty).strip() in {"0", "0.0"}:
                    raise ValueError("Bybit position size is missing.")
                try:
                    qty_num = float(qty)
                    if qty_num <= 0:
                        raise ValueError("Bybit position size must be > 0.")
                    qty = _format_decimal_value(qty_num)
                except (TypeError, ValueError):
                    pass

                await _close_bybit_position_market(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    category=category or "linear",
                    symbol=instrument,
                    side=str(item.get("side", "")),
                    qty=qty,
                    position_idx=position_idx,
                    order_link_id=str(item.get("order_link_id", "")).strip() or None,
                )
            action_requested = True

        elif broker == "oanda":
            cfg = _get_oanda_config(account)
            mode = account if account in {"demo", "practice"} else "live"
            action_account_id = str(item.get("account_id") or cfg.get("account_id") or "").strip()
            if action == "cancel":
                await _cancel_oanda_order(cfg=cfg, order_id=item_id, mode=mode, account_id=action_account_id)
            else:
                await _close_oanda_trade(cfg=cfg, trade_id=item_id, mode=mode, account_id=action_account_id)
            action_requested = True
        else:
            raise ValueError(f"Unsupported broker: {broker}")

        if action_requested:
            _invalidate_open_orders_cache()
            _schedule_dropbox_upload_state_backup()

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(
        {
            "ok": True,
            "broker": broker,
            "action": action,
            "id": item_id,
            "action_requested": action_requested,
        }
    )


@app.post("/scripts/{script_name:path}/start")
async def start_script(script_name: str) -> JSONResponse:
    script = script_manager.get(script_name)

    if script.is_running:
        return JSONResponse({"status": "already_running", **script.to_summary()})
    if script.is_starting or (script.startup_task is not None and not script.startup_task.done()):
        return JSONResponse({"status": "already_starting", **script.to_summary()}, status_code=202)

    if script.name in WEB_APPS and script.port is None:
        script.port = _allocate_port()

    script.startup_task = asyncio.create_task(_background_start(script))

    # Respond immediately so no script output can leak into the HTTP response cycle.
    return JSONResponse({"status": "starting", **script.to_summary()}, status_code=202)


@app.post("/scripts/{script_name:path}/stop")
async def stop_script(script_name: str) -> JSONResponse:
    try:
        summary = await script_manager.stop(script_name)
        return JSONResponse(summary)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to stop {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/logs/", include_in_schema=False)
async def logs_index() -> RedirectResponse:
    return RedirectResponse("/")


@app.get("/api/logs/", include_in_schema=False)
@app.get("/api/logs", include_in_schema=False)
async def api_logs_root(
    request: Request,
    cursor: int = 0,
    script: Optional[str] = None,
    name: Optional[str] = None,
) -> JSONResponse:
    """Compatibility endpoint for fetching logs without embedding the script in the path.

    The log viewer historically called `/api/logs/` with only a `cursor` query param. Try to
    resolve the script name from explicit `script`/`name` params or, as a last resort, from
    the referer header that points back to `/logs/view/<script>`.
    """

    script_name = script or name

    if not script_name:
        referer = request.headers.get("referer") or request.headers.get("referrer")
        if referer:
            parsed = urlparse(referer)
            path = parsed.path.rstrip("/")
            if path.startswith("/logs/view/"):
                script_name = unquote(path.split("/logs/view/", 1)[1])

    if not script_name:
        # Keep the shape consistent with the standard log endpoint while remaining a 200
        # response so the UI can render gracefully.
        return JSONResponse({"lines": [], "cursor": 0, "detail": "No script specified"})

    try:
        snapshot = script_manager.log_snapshot(script_name, cursor)
        return JSONResponse(snapshot)
    except HTTPException as exc:
        if exc.status_code == 404:
            return JSONResponse(
                {"lines": [], "cursor": 0, "detail": exc.detail}, status_code=200
            )
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/api/logs/{script_name:path}")
async def api_logs(script_name: str, cursor: int = 0) -> JSONResponse:
    try:
        snapshot = script_manager.log_snapshot(script_name, cursor)
        return JSONResponse(snapshot)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/logs/{script_name:path}")
async def read_logs(script_name: str) -> JSONResponse:
    try:
        return JSONResponse(script_manager.logs(script_name))
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/results/{script_name:path}/{result_path:path}")
async def read_script_results(script_name: str, result_path: str) -> FileResponse:
    safe_script_name = script_name.replace("..", "").strip("/")
    safe_result_path = result_path.replace("..", "").lstrip("/")

    script = None
    try:
        script = script_manager.get(safe_script_name)
    except HTTPException as exc:
        if exc.status_code == 404 and "/" in safe_script_name:
            parts = [part for part in safe_script_name.split("/") if part]
            resolved = False
            for cut in range(len(parts) - 1, 0, -1):
                candidate = "/".join(parts[:cut])
                extra = "/".join(parts[cut:])
                try:
                    script = script_manager.get(candidate)
                    safe_result_path = f"{extra}/{safe_result_path}" if extra else safe_result_path
                    resolved = True
                    break
                except HTTPException:
                    continue
            if not resolved:
                raise
        else:
            raise

    base_dir = script.last_spawn_cwd or str(script.path.parent) if script else None
    if not base_dir:
        raise HTTPException(status_code=404, detail="Script has not been started yet.")

    file_path = Path(base_dir) / safe_result_path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Result file not found.")
    try:
        resolved = file_path.resolve()
        base_resolved = Path(base_dir).resolve()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not str(resolved).startswith(str(base_resolved)):
        raise HTTPException(status_code=403, detail="Access denied.")
    return FileResponse(resolved)


@app.post("/api/oanda-history/export")
async def start_oanda_history_export(request: Request) -> JSONResponse:
    if oanda_history_exporter is None:
        raise HTTPException(status_code=500, detail="OANDA history exporter not available.")
    payload = await request.json()

    account = str(payload.get("account") or "").strip().lower()
    if account not in {"live", "demo"}:
        raise HTTPException(status_code=400, detail="account must be live or demo.")
    period = _normalize_period(payload.get("period"))
    days = payload.get("days")
    complete = payload.get("complete")

    if period is not None:
        if days is not None or complete:
            raise HTTPException(status_code=400, detail="Specify only one of period, days, or complete.")
        if period == "complete":
            complete = True
            period = None

    params: Dict[str, object] = {"account": account}
    if complete:
        params.update({"complete": True})
    elif period is not None:
        params.update({"period": period, "complete": False})
    else:
        if days is None:
            raise HTTPException(status_code=400, detail="days is required unless complete is true.")
        try:
            days_int = int(days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="days must be an integer.") from exc
        if days_int <= 0:
            raise HTTPException(status_code=400, detail="days must be greater than zero.")
        params.update({"days": days_int, "complete": False})

    job_id = uuid4().hex
    job = OandaHistoryJob(
        job_id=job_id,
        status="queued",
        created_at=time.time(),
        updated_at=time.time(),
        params=params,
    )
    OANDA_HISTORY_JOBS[job_id] = job
    asyncio.create_task(_run_oanda_history_export(job))
    return JSONResponse({"job_id": job_id})

@app.get("/api/oanda-history/export/{job_id}")
async def oanda_history_export_status(job_id: str) -> JSONResponse:
    job = OANDA_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    payload: Dict[str, object] = {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.error,
    }
    if (
        job.status == "done"
        and job.output_path is not None
        and job.output_path.exists()
    ):
        payload["download_url"] = f"/api/oanda-history/export/{job.job_id}/download"
    return JSONResponse(payload)


@app.get("/api/oanda-history/export/{job_id}/download")
async def download_oanda_history_export(job_id: str) -> FileResponse:
    job = OANDA_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    if job.status != "done" or job.output_path is None:
        raise HTTPException(status_code=400, detail="Export not ready.")
    if not job.output_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found.")
    return FileResponse(
        job.output_path,
        filename=job.output_path.name,
        media_type="text/csv",
    )


@app.post("/api/coinspot-history/export")
async def start_coinspot_history_export(request: Request) -> JSONResponse:
    payload = await request.json()
    period = _normalize_period(payload.get("period"))
    days = payload.get("days")
    complete = payload.get("complete")

    if period is not None:
        if days is not None or complete:
            raise HTTPException(status_code=400, detail="Specify only one of period, days, or complete.")
        if period == "complete":
            complete = True
            period = None

    params: Dict[str, object] = {}
    if complete:
        params.update({"complete": True})
    elif period is not None:
        params.update({"period": period, "complete": False})
    else:
        if days is None:
            raise HTTPException(status_code=400, detail="days is required unless complete is true.")
        try:
            days_int = int(days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="days must be an integer.") from exc
        if days_int <= 0:
            raise HTTPException(status_code=400, detail="days must be greater than zero.")
        params.update({"days": days_int, "complete": False})

    job_id = uuid4().hex
    job = CoinspotHistoryJob(
        job_id=job_id,
        status="queued",
        created_at=time.time(),
        updated_at=time.time(),
        params=params,
    )
    COINSPOT_HISTORY_JOBS[job_id] = job
    asyncio.create_task(_run_coinspot_history_export(job))
    return JSONResponse({"job_id": job_id})


@app.get("/api/coinspot-history/export/{job_id}")
async def coinspot_history_export_status(job_id: str) -> JSONResponse:
    job = COINSPOT_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    payload: Dict[str, object] = {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.error,
    }
    if job.status == "done" and job.output_path is not None:
        payload["download_url"] = f"/api/coinspot-history/export/{job.job_id}/download"
    return JSONResponse(payload)


@app.get("/api/coinspot-history/export/{job_id}/download")
async def download_coinspot_history_export(job_id: str) -> FileResponse:
    job = COINSPOT_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    if job.status != "done" or job.output_path is None:
        raise HTTPException(status_code=404, detail="Export not ready.")
    if not job.output_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found.")
    return FileResponse(
        job.output_path,
        filename=f"coinspot_history_{job_id}.zip",
        media_type="application/zip",
    )


@app.post("/api/bybit-history/export")
async def start_bybit_history_export(request: Request) -> JSONResponse:
    if bybit_history_fetcher is None:
        raise HTTPException(status_code=500, detail="Bybit history exporter not available.")
    payload = await request.json()

    account = str(payload.get("account") or "").strip().lower()
    if account not in {"live", "demo"}:
        raise HTTPException(status_code=400, detail="account must be live or demo.")
    period = _normalize_period(payload.get("period"))
    days = payload.get("days")
    complete = payload.get("complete")

    if period is not None:
        if days is not None or complete:
            raise HTTPException(status_code=400, detail="Specify only one of period, days, or complete.")
        if period == "complete":
            complete = True
            period = None

    params: Dict[str, object] = {"account": account}
    if complete:
        params.update({"complete": True})
    elif period is not None:
        params.update({"period": period, "complete": False})
    else:
        if days is None:
            raise HTTPException(status_code=400, detail="days is required unless complete is true.")
        try:
            days_int = int(days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="days must be an integer.") from exc
        if days_int <= 0:
            raise HTTPException(status_code=400, detail="days must be greater than zero.")
        params.update({"days": days_int, "complete": False})

    job_id = uuid4().hex
    job = BybitHistoryJob(
        job_id=job_id,
        status="queued",
        created_at=time.time(),
        updated_at=time.time(),
        params=params,
    )
    BYBIT_HISTORY_JOBS[job_id] = job
    asyncio.create_task(_run_bybit_history_export(job))
    return JSONResponse({"job_id": job_id})

@app.get("/api/bybit-history/export/{job_id}")
async def bybit_history_export_status(job_id: str) -> JSONResponse:
    job = BYBIT_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    payload: Dict[str, object] = {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.error,
    }
    if job.status == "done" and job.output_path is not None:
        payload["download_url"] = f"/api/bybit-history/export/{job.job_id}/download"
    return JSONResponse(payload)


@app.get("/api/bybit-history/export/{job_id}/download")
async def download_bybit_history_export(job_id: str) -> FileResponse:
    job = BYBIT_HISTORY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    if job.status != "done" or job.output_path is None:
        raise HTTPException(status_code=400, detail="Export not ready.")
    if not job.output_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found.")
    return FileResponse(
        job.output_path,
        filename=job.output_path.name,
        media_type="text/csv",
    )


@app.post("/webhook/{script_name:path}")
async def webhook(script_name: str, request: Request) -> JSONResponse:
    await request.body()
    normalized = script_name.strip().strip("/").replace("-", "_").casefold()
    retired = {name.strip().strip("/").replace("-", "_").casefold() for name in RETIRED_SCRIPT_NAMES}
    if normalized in retired:
        raise HTTPException(status_code=410, detail="Position size calculator has been removed.")

    _ = request
    raise HTTPException(status_code=404, detail=f"Unsupported webhook target: {script_name}")


@app.post("/execute_now")
async def execute_now(request: Request) -> JSONResponse:
    _ = request
    raise HTTPException(status_code=410, detail="Position size calculator has been removed.")


@app.post("/webhook")
async def default_webhook(request: Request) -> JSONResponse:
    _ = request
    raise HTTPException(status_code=410, detail="Position size calculator has been removed.")


@app.get("/health")
async def healthcheck() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.head("/")
async def root_head_health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/favicon.ico")
async def favicon() -> Response:
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6XvZl8AAAAASUVORK5CYII="
    )
    return Response(content=png_bytes, media_type="image/png")


app.mount("/static", StaticFiles(directory=BASE_DIR / "render" / "static"), name="static")

@app.get("/api/trading-journal")
async def trading_journal_items(filter: str = "") -> JSONResponse:
    # Enforce "no Bybit Demo" across all journal outputs.
    items = [
        _backfill_trade_row_context_fields(r)
        for r in _get_trading_journal_rows()
        if isinstance(r, dict)
        and not _exclude_bybit_demo_row(r)
        and str(r.get("status") or "").strip().lower() != "invalid_time_order"
    ]

    def _norm_search_text(value: object) -> str:
        text = str(value or "").lower()
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    query = _norm_search_text((filter or "").strip())
    tokens = [t for t in query.split(" ") if t]
    if tokens:

        def match(row: Dict[str, object]) -> bool:
            searchable = [
                row.get("symbol"),
                row.get("symbol_raw"),
                row.get("account_label"),
                row.get("account"),
                row.get("source"),
                row.get("sheet"),
                row.get("timeframe"),
                _display_test_trade(row),
                row.get("setup"),
                row.get("breakeven"),
                row.get("notes"),
                row.get("pre_trade_comments"),
                row.get("entry_comments"),
                row.get("trade_management"),
                row.get("exit_comments"),
            ]
            metrics = row.get("metrics")
            if isinstance(metrics, dict):
                searchable.extend(metrics.values())
            hay = _norm_search_text(" ".join(str(x or "") for x in searchable))
            return all(tok in hay for tok in tokens)

        items = [r for r in items if match(r)]

    balances = [b for b in _get_excel_account_balances() if not _is_bybit_demo_account_label(b.get("label") or b.get("account"))]
    state_meta = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
    source_folder = str(state_meta.get("source_folder") if isinstance(state_meta, dict) else "")

    # Pull cashflow rows from the active source folder tracked in journal state.
    trade_items = _enrich_trade_row_metrics(_apply_analysis_balances(_calc_balance_after_trade(items, balances)))
    cashflow_rows = _cashflow_rows_for_journal(source_folder) if source_folder else []
    cashflow_rows = [r for r in cashflow_rows if isinstance(r, dict) and not _exclude_bybit_demo_row(r)]
    if tokens:
        # Apply the same filter tokens to cashflow rows so symbol filters (e.g. BTCUSDT)
        # do not include deposits/withdrawals.
        cashflow_rows = [r for r in cashflow_rows if match(r)]

    combined_items = sorted([*trade_items, *cashflow_rows], key=_row_sort_dt, reverse=True)
    stats = _compute_journal_stats(combined_items, balances)
    return JSONResponse({"items": combined_items, "count": len(combined_items), "stats": stats})


@app.get("/api/trading-journal/diagnostics")
async def trading_journal_diagnostics() -> JSONResponse:
    payload = dict(TRADING_JOURNAL_IMPORT_DIAGNOSTICS or _default_journal_diagnostics())
    payload["rows_total"] = int(payload.get("rows_total") or 0)
    payload["rows_by_source"] = payload.get("rows_by_source") if isinstance(payload.get("rows_by_source"), dict) else {}
    payload["rows_by_asset_class"] = payload.get("rows_by_asset_class") if isinstance(payload.get("rows_by_asset_class"), dict) else {}
    payload["last_sync"] = payload.get("last_sync") if isinstance(payload.get("last_sync"), dict) else {}
    payload["local_workbooks_seen"] = int(payload.get("local_workbooks_seen") or 0)
    payload["dropbox_workbooks_seen"] = int(payload.get("dropbox_workbooks_seen") or 0)
    payload["duplicate_rows_dropped"] = int(payload.get("duplicate_rows_dropped") or 0)
    payload["source_duplicate_rows_dropped"] = int(payload.get("source_duplicate_rows_dropped") or 0)
    payload["dedupe_groups"] = int(payload.get("dedupe_groups") or 0)
    payload["quarantined_rows"] = int(payload.get("quarantined_rows") or 0)
    payload["ignored_local_workbooks"] = payload.get("ignored_local_workbooks") if isinstance(payload.get("ignored_local_workbooks"), list) else []
    payload["errors"] = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    return JSONResponse(payload)
@app.get("/api/trading-journal/balances")
async def trading_journal_balances() -> JSONResponse:
    rows = [r for r in _get_trading_journal_rows() if isinstance(r, dict) and not _exclude_bybit_demo_row(r)]
    rows = _enrich_trade_row_metrics(_calc_balance_after_trade(rows, _get_excel_account_balances()))
    state = _load_json_file(TRADING_JOURNAL_STATE_PATH, {})
    source_folder = str(state.get("source_folder") if isinstance(state, dict) else "")

    cashflow_items = _latest_balances_from_cashflows(source_folder) if source_folder else []
    cashflow_items = [b for b in cashflow_items if not _is_bybit_demo_account_label(b.get("label") or b.get("account"))]
    excel = [b for b in _get_excel_account_balances() if not _is_bybit_demo_account_label(b.get("label") or b.get("account"))]

    by_acc: Dict[str, Dict[str, object]] = {}

    # Priority for displayed balances:
    #   - Prefer explicit balances (Excel/cashflow) *only if they are at least as recent* as
    #     the latest trade-derived balance.
    #   - Otherwise, use latest trade-derived balance_after_trade for the displayed balance.
    for bal in excel:
        label = str((bal.get("account") or bal.get("label") or "")).strip()
        key = _norm_account_key(label)
        if key:
            by_acc[key] = dict(bal)

    for bal in cashflow_items:
        label = str((bal.get("account") or bal.get("label") or "")).strip()
        key = _norm_account_key(label)
        if not key:
            continue

        existing = dict(by_acc.get(key) or {})
        existing_balance = _to_float(existing.get("balance"))
        cash_balance = _to_float(bal.get("balance"))

        if not existing:
            by_acc[key] = dict(bal)
            continue

        if existing_balance is None and cash_balance is not None:
            merged = dict(bal)
            for k, v in existing.items():
                if merged.get(k) in (None, "") and v not in (None, ""):
                    merged[k] = v
            by_acc[key] = merged
            continue

        merged = dict(existing)
        for k, v in dict(bal).items():
            if merged.get(k) in (None, "") and v not in (None, ""):
                merged[k] = v
        by_acc[key] = merged

    for row in rows:
        account = str(row.get("account_label") or row.get("account") or "").strip()
        if not account:
            continue
        key = _norm_account_key(account)
        if key not in by_acc:
            by_acc[key] = {
                "account": account,
                "label": account,
                "balance": None,
                "nav": None,
                "currency": _infer_account_currency(account),
                "missing_balance": True,
            }


    latest_by_acc: Dict[str, Dict[str, object]] = {}
    for row in sorted(rows, key=lambda r: _row_sort_dt(r), reverse=True):
        account = str(row.get("account_label") or row.get("account") or "").strip()
        key = _norm_account_key(account)
        bal = _to_float(row.get("balance_after_trade"))
        if key and bal is not None and key not in latest_by_acc:
            latest_by_acc[key] = {
                "account": account,
                "label": account,
                "balance": bal,
                "nav": None,
                "currency": str(row.get("balance_after_trade_currency") or row.get("currency") or _infer_account_currency(account)),
                "source": "latest_trade_row",
                "as_of": row.get("close_time") or row.get("open_time"),
            }
    def _ts(value: object) -> float:
        if value in (None, ""):
            return float("-inf")
        try:
            return float(pd.to_datetime(value).timestamp())
        except Exception:
            return float("-inf")

    for key, bal in latest_by_acc.items():
        existing = dict(by_acc.get(key) or {})
        existing_balance = _to_float(existing.get("balance")) if existing else None

        # If we already have a numeric balance (Excel/cashflow), only keep it when it is
        # at least as recent as the latest trade-derived balance.
        if existing and existing_balance is not None:
            existing_ts = _ts(existing.get("as_of"))
            trade_ts = _ts(bal.get("as_of"))
            if trade_ts > existing_ts:
                merged = dict(existing)
                merged["balance"] = bal.get("balance")
                merged["currency"] = bal.get("currency") or merged.get("currency")
                merged["nav"] = bal.get("nav") if bal.get("nav") is not None else merged.get("nav")
                merged["source"] = bal.get("source") or merged.get("source")
                merged["as_of"] = bal.get("as_of") or merged.get("as_of")
                # Fill any remaining missing fields from the trade-derived payload.
                for k, v in bal.items():
                    if merged.get(k) in (None, "") and v is not None:
                        merged[k] = v
                by_acc[key] = merged
            else:
                merged = dict(existing)
                for k, v in bal.items():
                    if merged.get(k) in (None, "") and v is not None:
                        merged[k] = v
                by_acc[key] = merged
            continue

        if existing:
            merged = dict(existing)
            merged.update({k: v for k, v in bal.items() if v is not None})
            by_acc[key] = merged
        else:
            by_acc[key] = bal

    items = sorted(by_acc.values(), key=lambda x: str(x.get("label") or ""))
    return JSONResponse({"items": items})


@app.post("/api/trading-journal/rows")
async def trading_journal_create_row(payload: Dict[str, object] = Body(...)) -> JSONResponse:
    normalized = _normalize_trading_journal_edit_payload(payload, for_create=True)
    open_time = normalized.get("open_time")
    close_time = normalized.get("close_time")
    if not open_time or not close_time:
        raise HTTPException(status_code=422, detail="open_time and close_time are required.")
    row = {
        "id": f"manual:{uuid4().hex}",
        "row_type": "trade",
        "source": "manual",
        "status": "closed",
        "is_manual": True,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        **normalized,
    }
    row = _apply_trading_journal_manual_overrides(row, normalized)
    rows = _get_trading_journal_rows()
    rows.append(row)
    _set_trading_journal_rows(rows)
    return JSONResponse({"ok": True, "row": row})


@app.patch("/api/trading-journal/rows/{row_id}")
async def trading_journal_patch_row(row_id: str, payload: Dict[str, object] = Body(...)) -> JSONResponse:
    idx = _find_journal_row_index(row_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Journal row not found.")
    rows = _get_trading_journal_rows()
    existing = dict(rows[idx])
    row_type = _row_type(existing)
    if row_type == "cashflow":
        raise HTTPException(status_code=409, detail="Cashflow rows are read-only in trading journal.")
    if row_type != "trade":
        raise HTTPException(status_code=422, detail="Only trade rows can be edited.")
    normalized = _normalize_trading_journal_edit_payload(payload, for_create=False, existing=existing)
    if not normalized:
        raise HTTPException(status_code=422, detail="No editable fields supplied.")

    is_manual = bool(existing.get("is_manual")) or str(existing.get("source") or "").lower() == "manual"
    updated = dict(existing)
    updated.update(normalized)
    updated["updated_at"] = _utc_now_iso()
    if is_manual:
        # Manual rows are source-of-truth and can be updated directly.
        merged_overrides = dict(updated.get("manual_overrides") or {})
        merged_overrides.update(normalized)
        updated = _apply_trading_journal_manual_overrides(updated, merged_overrides)
    else:
        # Imported rows keep a manual override layer reapplied after future sync merges.
        merged_overrides = dict(existing.get("manual_overrides") or {})
        merged_overrides.update(normalized)
        updated = _apply_trading_journal_manual_overrides(updated, merged_overrides)
    rows[idx] = updated
    _set_trading_journal_rows(rows)
    return JSONResponse({"ok": True, "row": updated})


@app.delete("/api/trading-journal/rows/{row_id}")
async def trading_journal_delete_row(row_id: str) -> JSONResponse:
    idx = _find_journal_row_index(row_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Journal row not found.")
    rows = _get_trading_journal_rows()
    row = dict(rows[idx])
    row_type = _row_type(row)
    if row_type == "cashflow":
        raise HTTPException(status_code=409, detail="Cashflow rows are read-only in trading journal.")
    if row_type != "trade":
        raise HTTPException(status_code=422, detail="Only trade rows can be deleted.")
    is_manual = bool(row.get("is_manual")) or str(row.get("source") or "").lower() == "manual"
    if not is_manual:
        raise HTTPException(status_code=409, detail="Only manual trade rows can be deleted.")
    removed = rows.pop(idx)
    _set_trading_journal_rows(rows)
    return JSONResponse({"ok": True, "row": removed})


@app.get("/api/trading-journal/sync/status")
async def trading_journal_sync_status() -> JSONResponse:
    with TRADING_JOURNAL_SYNC_LOCK:
        snapshot = _sync_state_snapshot()
        TRADING_JOURNAL_SYNC_STATE.update(snapshot)
    state = _load_trading_journal_state()
    bybit_demo_sync = state.get("bybit_demo_sync")
    snapshot["bybit_demo_sync"] = bybit_demo_sync if isinstance(bybit_demo_sync, dict) else {}
    snapshot["oanda_fill_poll"] = {
        "enabled": os.getenv("ENABLE_OANDA_FILL_POLL", "0") == "1",
        "accounts": {
            "live": {
                **_OANDA_FILL_DIAGNOSTICS.get("live", {}),
                "last_seen_transaction_id": _OANDA_TX_LAST_SEEN.get("live"),
            },
            "demo": {
                **_OANDA_FILL_DIAGNOSTICS.get("demo", {}),
                "last_seen_transaction_id": _OANDA_TX_LAST_SEEN.get("demo"),
            },
        },
    }
    return JSONResponse(snapshot)


async def _run_trading_journal_sync_job() -> None:
    def _cb(progress: int, message: str) -> None:
        _set_trading_journal_sync_state(
            running=True,
            progress=int(progress),
            message=str(message or ""),
            ok=None,
            error=None,
            result=None,
        )

    _set_trading_journal_sync_state(
        running=True,
        progress=0,
        message="Starting…",
        ok=None,
        error=None,
        result=None,
        started_at=_utc_now_iso(),
        finished_at=None,
    )

    try:
        bybit_demo = None
        bybit_live = None
        try:
            bybit_demo = await _run_bybit_closed_pnl_sync(
                account_mode="demo",
                reason="manual",
                enforce_manual_cooldown=True,
            )
        except Exception as exc:
            bybit_demo = {"ok": False, "error": str(exc)}
        try:
            bybit_live = await _run_bybit_closed_pnl_sync(
                account_mode="live",
                reason="manual",
                enforce_manual_cooldown=True,
            )
        except Exception as exc:
            bybit_live = {"ok": False, "error": str(exc)}

        result = await asyncio.to_thread(_import_trading_journal_from_sources, progress_cb=_cb)
        if isinstance(result, dict):
            result["bybit"] = {"demo": bybit_demo, "live": bybit_live}
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else {}
        rows_by_asset_class = diagnostics.get("rows_by_asset_class") if isinstance(diagnostics, dict) else {}
        ok_flag = bool(result.get("ok", True)) if isinstance(result, dict) else True
        msg = "Done" if ok_flag else str((result or {}).get("message") or (result or {}).get("error") or "Failed")
        _set_trading_journal_sync_state(
            running=False,
            progress=100,
            message=msg,
            ok=ok_flag,
            error=None if ok_flag else msg,
            result=result,
            rows_imported=int((result or {}).get("rows_imported") or 0),
            rows_by_asset_class=rows_by_asset_class if isinstance(rows_by_asset_class, dict) else {},
            local_workbooks_seen=int((result or {}).get("local_workbooks_seen") or 0),
            dropbox_workbooks_seen=int((result or {}).get("dropbox_workbooks_seen") or 0),
            finished_at=_utc_now_iso(),
        )
    except Exception as exc:
        _set_trading_journal_sync_state(
            running=False,
            progress=100,
            message=f"Failed: {exc}",
            ok=False,
            error=str(exc),
            result=None,
            rows_imported=0,
            rows_by_asset_class={},
            local_workbooks_seen=0,
            dropbox_workbooks_seen=0,
            finished_at=_utc_now_iso(),
        )


@app.post("/api/trading-journal/sync")
async def trading_journal_sync() -> JSONResponse:
    with TRADING_JOURNAL_SYNC_LOCK:
        running = bool(_sync_state_snapshot().get("running"))
    if running:
        return await trading_journal_sync_status()

    _set_trading_journal_sync_state(
        running=True,
        progress=0,
        message="Queued…",
        ok=None,
        error=None,
        result=None,
        started_at=_utc_now_iso(),
        finished_at=None,
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_trading_journal_sync_job())
    except RuntimeError:
        threading.Thread(
            target=lambda: asyncio.run(_run_trading_journal_sync_job()),
            daemon=True,
        ).start()
    return await trading_journal_sync_status()
