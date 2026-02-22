"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import math
import asyncio
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
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import httpx
import requests
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from starlette.responses import RedirectResponse

from bybit_credentials import resolve_bybit_credentials_for
from render.dropbox_sync import download_bytes, list_excel_files, upload_bytes
from payslip_audit.tesseract import (
    TESSERACT_MISSING_MESSAGE,
    _resolve_tesseract_binary,
    is_tesseract_available,
)
from bybit_monitor import bybit_altcoin_monitor as bybit_monitor
from oanda_monitor import oanda_forex_monitor as oanda_monitor

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
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
    "oanda_swap_rates",
    "oanda-swap-rates",
    "oanda-swap-rates-clone",
    "oanda_swap_rates_clone",
    "swap_rates_oanda",
    "swap-rates-oanda",
    "swap_rates",
    "swap-rates",
}
MAX_LOG_LINES = 400
PAYSLIP_REPORT_NAME = "audit_report.pdf"
PAYSLIP_UPLOAD_ROOT = BASE_DIR / "render" / "uploads" / "payslip"
PAYSLIP_ALLOWED_IMAGES = {".jpg", ".jpeg", ".png"}
OANDA_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "oanda-history"
BYBIT_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "bybit-history"
COINSPOT_HISTORY_EXPORT_ROOT = BASE_DIR / "render" / "uploads" / "coinspot-history"
PENDING_WEBHOOKS_PATH = BASE_DIR / "render" / "data" / "pending_webhooks.json"
WATCHLIST_PATH = BASE_DIR / "render" / "data" / "watchlist.json"
TRADING_JOURNAL_PATH = BASE_DIR / "render" / "data" / "trading_journal.json"
TRADING_JOURNAL_STATE_PATH = BASE_DIR / "render" / "data" / "trading_journal_state.json"
TRADING_JOURNAL_DROPBOX_FOLDER = os.getenv(
    "TRADING_JOURNAL_DROPBOX_FOLDER", "/master_control"
).strip()
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
WEB_APPS = {
    "bybit_trigger_bounce_trader",
    "bybithistory-clone",
    "cryptocalculator-clone",
    "oanda-calculator-clone",
    "ivindicator-clone",
    "fxweekend-clone",
}
STANDALONE_SCRIPTS = {
    "Crypto-Scanner-clone",
    "bybit-alert-clone",
    "bybit_monitor",
    "oanda_monitor",
    "bybithistory-clone",
    "coinspot-clone",
    "cryptocalculator-clone",
    "ivindicator-clone",
    "fxscanner-oanda-clone",
    "fxweekend-clone",
    "oanda-calculator-clone",
    "oanda_history-clone",
}

ENTRY_OVERRIDES = {
    "Crypto-Scanner-clone": ["continuous_scan.py", "scan.py"],
    "LEDGER-clone": ["process_entries.py"],
    "bybit_monitor": ["bybit_altcoin_monitor.py"],
    "bybithistory-clone": ["app.py"],
    "coinspot-clone": ["coinspot_history.py"],
    "cryptocalculator-clone": ["cryptocalculator_web.py", "cryptocalculator.py"],
    "fxscanner-oanda-clone": ["forex_scanner.py"],
    "fxweekend-clone": ["liquidate.py"],
    "ivindicator-clone": ["ivweb.py", "ivapp.py", "ivindicator.py"],
    "oanda-calculator-clone": ["oanda_calculator_web.py", "oanda_api.py"],
    "oanda_monitor": ["oanda_forex_monitor.py"],
    "oanda_history-clone": ["oanda_history.py"],
    "payslip_audit": ["payslip_timesheet_audit.py"],
}

LOG_FILE_OVERRIDES: Dict[str, Path] = {
    "fxweekend-clone": BASE_DIR / "fxweekend-clone" / "trade_closure.log",
}

BYBIT_SETTINGS_PATH = bybit_monitor.SETTINGS_PATH

PAYSLIP_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
OANDA_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
BYBIT_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
COINSPOT_HISTORY_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
PENDING_WEBHOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
TRADING_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)

_WATCHLIST_CACHE: Optional[List[str]] = None
_TRADING_JOURNAL_CACHE: Optional[List[Dict[str, object]]] = None
_DROPBOX_UPLOAD_TASK: Optional[asyncio.Task] = None
_BYBIT_EXEC_LAST_SEEN: Dict[str, int] = {}
_OANDA_TX_LAST_SEEN: Dict[str, str] = {}


def _normalize_watchlist(items: Iterable[object]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in items:
        symbol = str(item or "").strip().upper()
        if not symbol:
            continue
        if len(symbol) == 6 and symbol.isalpha():
            symbol = f"{symbol[:3]}_{symbol[3:]}"
        if symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
        if len(normalized) >= WATCHLIST_MAX_ITEMS:
            break
    return normalized


def _norm_symbol(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()


def _normalize_instrument_key(value: object) -> str:
    return _norm_symbol(str(value or ""))


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


def _bybit_get(path: str, params: Dict[str, object]) -> Dict[str, object]:
    response = requests.get(f"{BYBIT_BASE}{path}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def _bybit_avg_7d_turnover_usd(symbol: str, category: str = "linear") -> Optional[float]:
    try:
        end_ms = int(time.time() * 1000)
        data = _bybit_get(
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
        if not rows:
            return None

        turnovers: List[float] = []
        for row in rows[:7]:
            if isinstance(row, list) and len(row) >= 7:
                try:
                    turnovers.append(float(row[6]))
                except Exception:
                    continue
        if not turnovers:
            return None
        return sum(turnovers) / len(turnovers)
    except Exception:
        return None


async def _oanda_resolve_and_fetch_specs(query: str) -> Optional[Dict[str, object]]:
    token = _oanda_token()
    account_id = _oanda_account_id_for_specs()
    if not token or not account_id:
        print(f"[instrument-specs] OANDA creds missing for env={os.getenv('OANDA_ENV', 'live')!r}")
        return None

    want_key = _normalize_instrument_key(query)
    if not want_key:
        return None

    url = f"{_oanda_base_url()}/v3/accounts/{account_id}/instruments"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(url, headers=headers)
    if res.status_code != 200:
        return None
    data = res.json() or {}
    instruments = data.get("instruments") or []
    if not isinstance(instruments, list):
        return None

    inst_rows = [inst for inst in instruments if isinstance(inst, dict)]
    available_names = [str(inst.get("name") or "") for inst in inst_rows]
    try:
        normalized_query = _normalize_oanda_symbol_query(query, available_names)
    except ValueError:
        return None

    matched = resolve_oanda_instrument(normalized_query, inst_rows)
    if not matched:
        return None

    financing = matched.get("financing") or {}
    return {
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


async def _bybit_resolve_and_fetch_specs(query: str) -> Optional[Dict[str, object]]:
    want_key = _normalize_instrument_key(query)
    if not want_key:
        return None

    creds = resolve_bybit_credentials_for("default")
    base_url = creds.get("base_url") if isinstance(creds, dict) else None
    base_url = base_url or BYBIT_BASE

    async def _get(path: str, params: Dict[str, object]) -> Dict[str, object]:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(f"{base_url}{path}", params=params)
        res.raise_for_status()
        return res.json()

    async def _find_symbol_in_instruments(category: str) -> Optional[Dict[str, object]]:
        cursor: Optional[str] = None
        for _ in range(4):
            params: Dict[str, object] = {"category": category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await _get("/v5/market/instruments-info", params)
            result = payload.get("result") or {}
            items = result.get("list") or []
            if isinstance(items, list):
                for inst in items:
                    if not isinstance(inst, dict):
                        continue
                    if _normalize_instrument_key(inst.get("symbol")) == want_key:
                        inst = dict(inst)
                        inst["_category"] = category
                        return inst
            cursor = result.get("nextPageCursor")
            if not cursor:
                break
        return None

    resolved_inst = None
    for cat in ("linear", "inverse", "spot"):
        try:
            resolved_inst = await _find_symbol_in_instruments(cat)
        except Exception:
            continue
        if resolved_inst:
            break
    if not resolved_inst:
        return None

    category = str(resolved_inst.get("_category") or "")
    symbol = str(resolved_inst.get("symbol") or "")

    ticker = None
    try:
        payload = await _get("/v5/market/tickers", {"category": category, "symbol": symbol})
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

    avg7d = _bybit_avg_7d_turnover_usd(str((ticker or {}).get("symbol") or symbol), category)
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


async def _fetch_instrument_specs(query: str) -> Dict[str, object]:
    q = str(query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    oanda = await _oanda_resolve_and_fetch_specs(q)
    if oanda:
        return oanda

    bybit = await _bybit_resolve_and_fetch_specs(q)
    if bybit:
        return bybit

    raise HTTPException(status_code=404, detail=f"Instrument not found for query: {q}")


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
    if not isinstance(payload, list):
        return []
    rows: List[Dict[str, object]] = []
    for entry in payload:
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
    sorted_rows = sorted(
        rows,
        key=lambda item: str(item.get("close_time") or item.get("open_time") or ""),
        reverse=True,
    )
    _TRADING_JOURNAL_CACHE = [dict(item) for item in sorted_rows]
    TRADING_JOURNAL_PATH.write_text(
        json.dumps(sorted_rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _upsert_trading_journal_rows(rows: Iterable[Dict[str, object]]) -> int:
    existing = _get_trading_journal()
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
            by_id[row_id].update(row)
        else:
            by_id[row_id] = row
        changed += 1
    if changed:
        _save_trading_journal(list(by_id.values()))
    return changed


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
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_trading_journal_rows() -> List[Dict[str, object]]:
    data = _load_json_file(TRADING_JOURNAL_PATH, {"items": []})
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    items = data.get("items") if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def _set_trading_journal_rows(rows: List[Dict[str, object]]) -> None:
    _save_json_file(TRADING_JOURNAL_PATH, {"items": rows, "updated_at": _utc_now_iso()})


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
    return ("OANDA" in text) or ("PEPPERSTONE" in text)


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
        return None


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
        setup_col = _first_present(df, ["setup"])
        qty_col = _first_present(df, ["size_quantity", "qty", "quantity", "size", "units", "volume"])
        entry_col = _first_present(df, ["entry_price", "entry", "open_price", "price_open"])
        exit_col = _first_present(df, ["closing_price", "exit_price", "exit", "close_price", "price_close"])
        swap_col = _first_present(df, ["swap"])
        commission_col = _first_present(df, ["commission", "fee", "fees", "cost"])
        pnl_col = _first_present(df, ["net_profit", "realized_pnl", "pnl", "profit", "pl", "net_pnl"])
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

        trade_signal_count = sum(col is not None for col in [symbol_col, side_col, entry_col, exit_col, pnl_col])
        if trade_signal_count >= 3:
            for idx, row in df.iterrows():
                symbol_raw = _safe_str_from_row(row, symbol_col)
                if not symbol_raw or symbol_raw.lower() == "nan":
                    continue

                account_currency = _infer_account_currency(account_label)
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
                qty_display = _normalize_fx_qty_for_display(account_label, symbol_canon, raw_qty)
                entry_price = _safe_float_from_row(row, entry_col)
                exit_price = _safe_float_from_row(row, exit_col)
                commission = _safe_float_from_row(row, commission_col)
                swap = _safe_float_from_row(row, swap_col)
                net_profit = _safe_float_from_row(row, pnl_col)
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

                used_norm = {
                    _norm_col(x)
                    for x in [
                        open_time_col,
                        close_time_col,
                        side_col,
                        symbol_col,
                        setup_col,
                        qty_col,
                        entry_col,
                        exit_col,
                        swap_col,
                        commission_col,
                        pnl_col,
                        sl_col,
                        tp_col,
                        high_col,
                        low_col,
                        notes_col,
                        pre_trade_col,
                        entry_comments_col,
                        trade_mgmt_col,
                        exit_comments_col,
                        breakeven_col,
                    ]
                    if x
                }
                used_norm.update(_norm_col(c) for c in extra_cols.values() if c)
                for norm_name, orig_name in norm_to_orig.items():
                    if norm_name in used_norm:
                        continue
                    value = _excel_cell_to_python(row.get(orig_name))
                    if value is None or (isinstance(value, str) and not str(value).strip()):
                        continue
                    metrics.setdefault(norm_name, value)

                row_id = f"excel:{account_label}:{sheet}:{idx}:{symbol_canon}:{close_time_iso or ''}"
                side_txt = _safe_str_from_row(row, side_col).upper()
                setup_txt = _safe_str_from_row(row, setup_col)
                breakeven_txt = _boolish_text(_safe_str_from_row(row, breakeven_col))
                status = "closed" if exit_price is not None else "unknown"

                notional = (
                    abs((qty_display or 0.0) * (entry_price or 0.0))
                    if (qty_display is not None and entry_price is not None)
                    else None
                )

                all_rows.append(
                    {
                        "id": row_id,
                        "source": "excel",
                        "account": account_label,
                        "account_label": account_label,
                        "sheet": sheet,
                        "asset_class": "fx" if _is_fx_account_label(account_label) else "crypto",
                        "currency": account_currency,
                        "symbol": symbol_canon,
                        "symbol_raw": symbol_raw,
                        "side": side_txt,
                        "setup": setup_txt,
                        "open_time": open_time_iso,
                        "close_time": close_time_iso,
                        "qty": qty_display,
                        "qty_raw": raw_qty,
                        "qty_unit": "lots" if _is_fx_account_label(account_label) else "native",
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "swap": swap,
                        "commission": commission,
                        "fees": commission,
                        "fee_currency": account_currency,
                        "realized_pnl": net_profit,
                        "realized_pnl_currency": account_currency,
                        "net_profit": net_profit,
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
                        "raw_refs": {"dropbox_path": dbx_path, "sheet": sheet, "row_index": int(idx)},
                        "updated_at": _utc_now_iso(),
                    }
                )

        bal_col = _first_present(df, ["balance", "account_balance", "cash_balance"])
        nav_col = _first_present(df, ["nav", "equity", "account_equity"])
        ccy_col = _first_present(df, ["currency", "ccy", "account_currency"])
        if bal_col or nav_col:
            for _, row in df.iterrows():
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


def _import_trading_journal_from_dropbox_excel() -> Dict[str, object]:
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
            entries = list_excel_files(
                candidate, recursive=TRADING_JOURNAL_DROPBOX_RECURSIVE
            )
            active_folder = candidate
            break
        except Exception as exc:
            last_exc = exc

    if entries is None:
        if last_exc is not None:
            raise last_exc
        raise FileNotFoundError(f"Dropbox folder not found: {configured}")

    workbook_count = 0
    rows: List[Dict[str, object]] = []
    balances: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []

    for entry in entries:
        name = str(entry.get("name") or "")
        dbx_path = str(entry.get("path_lower") or entry.get("path_display") or "")
        if not dbx_path:
            continue
        try:
            payload = download_bytes(dbx_path)
            parsed_rows, parsed_balance = _parse_excel_account_workbook(name, dbx_path, payload)
            rows.extend(parsed_rows)
            if parsed_balance:
                balances.append(parsed_balance)
            workbook_count += 1
        except Exception as exc:
            errors.append({"file": name, "path": dbx_path, "error": str(exc)})

    dedup: Dict[str, Dict[str, object]] = {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if row_id:
            dedup[row_id] = row

    final_rows = sorted(
        dedup.values(),
        key=lambda row: str(row.get("close_time") or ""),
        reverse=True,
    )

    _set_trading_journal_rows(final_rows)
    _save_json_file(
        TRADING_JOURNAL_STATE_PATH,
        {
            "updated_at": _utc_now_iso(),
            "excel_account_balances": balances,
            "source_folder": active_folder,
            "configured_folder": configured,
            "workbooks_seen": workbook_count,
            "errors": errors,
        },
    )

    return {
        "ok": True,
        "source_folder": active_folder,
        "configured_folder": configured,
        "workbooks_seen": workbook_count,
        "rows_imported": len(final_rows),
        "balances_found": len(balances),
        "errors": errors,
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
            "notional_usd": (exec_price or 0.0) * qty,
            "fees": exec_fee,
            "fee_currency": str(entry.get("feeCurrency") or "USDT"),
            "realized_pnl": exec_pnl,
            "realized_pnl_currency": str(entry.get("currency") or "USDT"),
            "strategy_tag": "",
            "notes": "",
            "raw_refs": {"orderId": order_id, "execIds": [exec_id]},
        }
    ]


def _journal_rows_from_oanda_order_fill(entry: Dict[str, object]) -> List[Dict[str, object]]:
    account = str(entry.get("account") or "unknown").strip().lower()
    tx_id = str(entry.get("id") or "").strip()
    symbol = str(entry.get("instrument") or "").strip().upper()
    if not tx_id or not symbol:
        return []
    units = _to_float(entry.get("units")) or 0.0
    qty = abs(units)
    side = "Buy" if units >= 0 else "Sell"
    price = _to_float(entry.get("price"))
    realized_pnl = (_to_float(entry.get("pl")) or 0.0) + (_to_float(entry.get("financing")) or 0.0)
    fees = abs(_to_float(entry.get("halfSpreadCost")) or 0.0)
    close_time = str(entry.get("time") or "")
    return [
        {
            "id": f"oanda:{account}:{symbol}:{tx_id}",
            "source": "oanda",
            "account": account,
            "account_label": f"OANDA {account.title()}",
            "asset_class": "forex",
            "symbol": symbol,
            "side": side,
            "status": "closed",
            "open_time": close_time,
            "close_time": close_time,
            "entry_price": price,
            "exit_price": price,
            "qty": qty,
            "notional_usd": None,
            "fees": fees,
            "fee_currency": str(entry.get("accountCurrency") or ""),
            "realized_pnl": realized_pnl,
            "realized_pnl_currency": str(entry.get("accountCurrency") or ""),
            "strategy_tag": "",
            "notes": "",
            "raw_refs": {"transactionId": tx_id, "orderId": entry.get("orderID")},
        }
    ]


def _build_state_backup_payload() -> bytes:
    alerts_payload = {
        "bybit": {"alerts": bybit_monitor.get_custom_alerts(force=True)},
        "oanda": {"alerts": oanda_monitor.get_custom_alerts(force=True)},
    }
    payload = {
        "alerts": alerts_payload,
        "watchlist": _get_watchlist(),
        "pending_webhooks": _load_pending_webhooks(),
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _schedule_dropbox_upload_state_backup() -> None:
    if not DROPBOX_SYNC_ENABLED:
        return

    global _DROPBOX_UPLOAD_TASK
    if _DROPBOX_UPLOAD_TASK is not None and not _DROPBOX_UPLOAD_TASK.done():
        return

    async def _delayed_upload() -> None:
        await asyncio.sleep(DROPBOX_SYNC_DEBOUNCE_SECONDS)
        try:
            payload = _build_state_backup_payload()
            await asyncio.to_thread(upload_bytes, DROPBOX_BACKUP_PATH, payload)
            BYBIT_LOGGER.info("Dropbox backup uploaded to %s", DROPBOX_BACKUP_PATH)
        except Exception as exc:  # pragma: no cover - network failure
            BYBIT_LOGGER.error("Dropbox backup failed: %s", exc)

    _DROPBOX_UPLOAD_TASK = asyncio.create_task(_delayed_upload())


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

    bybit_restored = bybit_monitor.replace_custom_alerts(bybit_block["alerts"])
    oanda_restored = oanda_monitor.replace_custom_alerts(oanda_block["alerts"])
    _set_watchlist(watchlist_items)
    return {
        "bybit_restored": len(bybit_restored),
        "oanda_restored": len(oanda_restored),
        "watchlist_restored": len(watchlist_items),
        "pending_webhooks_restored": len(pending_restored),
    }


async def _dropbox_restore_state_backup_on_startup() -> None:
    if not DROPBOX_SYNC_ENABLED:
        return
    try:
        payload = await asyncio.to_thread(download_bytes, DROPBOX_BACKUP_PATH)
        data = json.loads(payload.decode("utf-8"))
        restored = _restore_alerts_payload(data)
        BYBIT_LOGGER.info(
            "Dropbox restore complete: bybit=%s oanda=%s watchlist=%s pending=%s",
            restored["bybit_restored"],
            restored["oanda_restored"],
            restored["watchlist_restored"],
            restored["pending_webhooks_restored"],
        )
    except FileNotFoundError:
        BYBIT_LOGGER.info("Dropbox restore skipped; no backup found at %s", DROPBOX_BACKUP_PATH)
    except Exception as exc:  # pragma: no cover - startup failure
        BYBIT_LOGGER.error("Dropbox restore failed: %s", exc)


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

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_summary(self) -> Dict[str, object]:
        return {
            "id": self.name,
            "name": self.name,
            "path": str(self.path),
            "category": self.category,
            "running": self.is_running,
            "port": self.port,
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
            "standalone": self.name in STANDALONE_SCRIPTS,
        }

    def add_log(self, line: str) -> None:
        cleaned = line.rstrip("\n")
        if cleaned:
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

    async def start(self) -> None:
        if self.is_running:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"Script not found: {self.path}")

        self.last_start_attempt_at = time.time()
        self.last_start_error = None
        self.last_exit_code = None
        self.last_exit_reason = None
        self.add_log("Starting script...")
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
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.last_spawn_cwd,
                env=env,
            )
        except Exception as exc:
            self.last_start_error = str(exc)
            self.add_log(f"Failed to start: {exc}")
            raise

        asyncio.create_task(self._capture_output())

    async def _capture_output(self) -> None:
        assert self.process is not None
        if self.process.stdout is None:
            return

        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                self.add_log(line.decode("utf-8", errors="replace"))
        finally:
            await self.process.wait()
            self.last_exit_code = self.process.returncode
            if self.last_exit_reason is None:
                self.last_exit_reason = (
                    "Process exited unexpectedly." if self.process.returncode else None
                )
            self.port = None

    async def stop(self) -> None:
        if not self.is_running:
            return
        assert self.process is not None
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()


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

    other_explicit = {"payslip_audit"}

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

    if folder in other_explicit:
        return "Other"

    return "Other"


def _encoded_script_name(script_name: str) -> str:
    """Encode a script name for safe URL usage while keeping slashes intact."""

    return quote(script_name, safe="/")


def script_open_url(script: ManagedScript) -> str:
    """Return the preferred UI URL for a script."""

    if script.name == "oanda_history-clone":
        return "/oanda-history"
    if script.name == "bybithistory-clone":
        return "/bybit-history"
    if script.name == "coinspot-clone":
        return "/coinspot-history"
    if script.name == "trading-journal":
        return "/trading-journal"
    if script.name in WEB_APPS:
        return f"/apps/{_encoded_script_name(script.name)}"
    return f"/scripts/view/{_encoded_script_name(script.name)}"


def script_logs_url(script_name: str) -> str:
    """Return the JSON logs API endpoint for a script."""

    return f"/logs/{_encoded_script_name(script_name)}"


def _payslip_session_dir(session_id: str) -> Path:
    return PAYSLIP_UPLOAD_ROOT / session_id


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


TESSERACT_MISSING_DETAIL = TESSERACT_MISSING_MESSAGE


def ensure_tesseract_available() -> None:
    """Raise an HTTP 500 with clear guidance when Tesseract is absent."""

    if not is_tesseract_available():
        raise HTTPException(status_code=500, detail=TESSERACT_MISSING_DETAIL)


async def _execute_payslip_audit(payslip: Path, timesheets: List[Path], output_path: Path) -> str:
    script_path = BASE_DIR / "payslip_audit" / "payslip_timesheet_audit.py"

    ensure_tesseract_available()

    command = [
        os.getenv("PYTHON", "python"),
        str(script_path),
        "--payslip",
        str(payslip),
        "--timesheet",
    ] + [str(path) for path in timesheets] + ["--output", str(output_path)]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(script_path.parent),
    )
    stdout, _ = await process.communicate()
    log_output = stdout.decode("utf-8", errors="replace") if stdout else ""

    if process.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Audit failed with exit code {process.returncode}.\n{log_output}",
        )

    if not output_path.exists():
        raise HTTPException(status_code=500, detail="Audit completed but no report was produced.")

    return log_output


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

        entry_path: Optional[Path] = None
        for candidate in candidate_entrypoints(app_dir):
            if candidate.exists() and candidate.is_file():
                entry_path = candidate
                break

        if entry_path is None:
            py_files = sorted(
                p
                for p in app_dir.glob("*.py")
                if p.name not in SKIP_FILES and not p.name.startswith("test_")
            )
            if py_files:
                entry_path = py_files[0]

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
        if name in self._scripts:
            return name

        normalized = self._normalize(name)
        if normalized in self._aliases:
            return self._aliases[normalized]

        raise HTTPException(status_code=404, detail=f"Script not found: {name}")

    def list_scripts(self) -> List[Dict[str, object]]:
        items = [script.to_summary() for script in self._scripts.values()]
        items.append(
            {
                "id": "trading-journal",
                "name": "trading-journal",
                "path": str(BASE_DIR / "render" / "master_service.py"),
                "category": "Other",
                "running": True,
                "port": None,
                "return_code": None,
                "open_url": "/trading-journal",
                "logs_url": None,
                "last_output_at": None,
                "last_start_attempt_at": None,
                "last_start_error": None,
                "last_exit_code": None,
                "last_exit_reason": None,
                "last_spawn_command": None,
                "last_spawn_cwd": None,
                "standalone": False,
            }
        )
        return sorted(items, key=lambda s: str(s["name"]).lower())

    def get(self, name: str) -> ManagedScript:
        resolved = self._resolve_name(name)
        return self._scripts[resolved]

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
app = FastAPI(title="Render Master Script", version="1.0")
OANDA_HISTORY_JOBS: Dict[str, OandaHistoryJob] = {}
BYBIT_HISTORY_JOBS: Dict[str, BybitHistoryJob] = {}
COINSPOT_HISTORY_JOBS: Dict[str, CoinspotHistoryJob] = {}

_AUTOSTART_ENV = os.getenv("AUTOSTART_SCRIPTS")
if _AUTOSTART_ENV is None:
    # Default autostart set for Render deploys (override by setting AUTOSTART_SCRIPTS).
    _AUTOSTART_ENV = "bybit_monitor,oanda_monitor,fxweekend-clone"

# AUTOSTART_SCRIPTS supports:
#   - comma-separated script names
#   - ALL or * to start every discovered script
_AUTOSTART_EXCLUDE_ENV = os.getenv("AUTOSTART_EXCLUDE") or ""
AUTOSTART_EXCLUDE = {
    name.strip()
    for name in _AUTOSTART_EXCLUDE_ENV.split(",")
    if name.strip()
}

AUTOSTART_SCRIPTS_RAW = [
    name.strip() for name in _AUTOSTART_ENV.split(",") if name.strip()
]


def _compute_autostart_scripts() -> List[str]:
    """Resolve autostart script names from env.

    AUTOSTART_SCRIPTS supports:
      - comma-separated script names
      - ALL or * to start every discovered script
    AUTOSTART_EXCLUDE may contain a comma-separated list of script names to skip.
    """

    want_all = any(token.upper() == "ALL" or token == "*" for token in AUTOSTART_SCRIPTS_RAW)
    if want_all:
        names = list(script_manager.names)
    else:
        names = list(AUTOSTART_SCRIPTS_RAW)

    if AUTOSTART_EXCLUDE:
        names = [name for name in names if name not in AUTOSTART_EXCLUDE]
    return names


@app.on_event("startup")
async def _autostart_scripts() -> None:
    await _dropbox_restore_state_backup_on_startup()
    asyncio.create_task(_poll_bybit_fills())
    asyncio.create_task(_poll_oanda_fills())
    for name in _compute_autostart_scripts():
        try:
            script = script_manager.get(name)
        except HTTPException:
            continue

        if script.is_running:
            continue

        if script.name in WEB_APPS and script.port is None:
            script.port = _allocate_port()

        asyncio.create_task(_background_start(script))


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
        account_mode = str(job.params.get("account") or "live").strip().lower()
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
        job.output_path = output_path
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
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

        account_mode = str(job.params.get("account") or "live").strip().lower()
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
    <title>Render Master Control</title>
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
            display: grid;
            grid-template-columns: 260px 1fr;
            gap: 1.25rem;
            margin-top: 1.25rem;
            align-items: start;
        }
        @media (max-width: 980px){
            .layout{ grid-template-columns: 1fr; }
        }
        .sidebar{
            position: sticky;
            top: 1rem;
            max-height: calc(100vh - 2rem);
            overflow: auto;
            padding: 1rem;
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
            display: flex;
            flex-direction: column;
            gap: 0.65rem;
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
        .script-btn.compact { width: auto; min-width: 190px; padding: 0.75rem 0.9rem; }
        #instrument-specs-widget .instrument-specs-row{
            display: flex;
            gap: 0.65rem;
            align-items: center;
            flex-wrap: wrap;
        }
        #instrument-specs-widget input{
            flex: 1;
            min-width: 200px;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #0b1220;
            color: #e2e8f0;
            padding: 8px 10px;
            font-size: 0.95rem;
        }
        #instrument-specs-widget button{
            border-radius: 10px;
            border: 1px solid #334155;
            background: #1f2937;
            color: #e2e8f0;
            font-weight: 900;
            padding: 8px 12px;
            cursor: pointer;
        }
        #instrument-specs-widget button:hover{ background: #334155; }
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
        .status-dot.stopped { background: #ef4444; }
        .empty-state { color: #94a3b8; margin-top: 0.9rem; }

        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid #1f2937; background: #0b1220; }
        #open-orders-table { width: 100%; border-collapse: collapse; min-width: 920px; }
        #open-orders-table th, #open-orders-table td { text-align:left; padding:0.6rem 0.75rem; border-bottom:1px solid #1f2937; font-size:0.9rem; }
        #open-orders-table th { background:#0f172a; color:#cbd5e1; position:sticky; top:0; z-index:1; }
        #open-orders-table tr:hover { background:#111827; }

        .action-cell { white-space: nowrap; }
        .action-btn {
            display:inline-flex; align-items:center; justify-content:center;
            min-width:72px; height:30px; padding:0 10px;
            border-radius:8px; font-size:0.8rem; font-weight:900;
            background:#1f2937; color:#e2e8f0; border:1px solid #334155;
        }
        .action-btn:hover { background:#334155; }
        .action-btn:disabled { opacity:0.6; cursor:not-allowed; }

        .error-list { margin-top:0.75rem; color:#fca5a5; font-size:0.9rem; }
        .error-list ul { margin:0.4rem 0 0; padding-left:1.25rem; }

        .top-stack {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1rem;
            align-items: start;
            margin-bottom: 1rem;
        }

        .top-panel {
            height: 420px;
            display: flex;
            flex-direction: column;
        }

        .top-panel .table-wrap {
            flex: 1;
            overflow: auto;
        }
        @media (max-width: 1100px) {
            .top-stack { grid-template-columns: 1fr; }
        }

        .watchlist-sub {
            color: #94a3b8;
            margin-top: 0.25rem;
            font-size: 0.95rem;
            line-height: 1.4;
        }
        #watchlist-widget .watchlist-input {
            display: flex;
            gap: 6px;
            margin: 0.75rem 0 0.5rem;
        }
        #watchlist-widget .watchlist-input input {
            flex: 1;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #0b1220;
            color: #e2e8f0;
            padding: 6px 8px;
            font-size: 0.9rem;
        }
        #watchlist-widget .watchlist-input button {
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
    </style>
</head>
<body>
    <div class=\"home\">
        <div class="layout">
            <aside class="panel sidebar">
                <div class="category-title">Forex</div>
                <div id="forex-scripts" class="script-stack"></div>

                <div class="category-title" style="margin-top:1rem">Crypto</div>
                <div id="crypto-scripts" class="script-stack"></div>

                <div class="category-title" style="margin-top:1rem">Other</div>
                <div id="other-scripts" class="script-stack"></div>
            </aside>

            <main>
            <div class="top-stack">

            <section class="panel" id="instrument-specs-widget">
                <div class="panel-header">
                    <div>
                        <h2>Instrument Specs</h2>
                        <div class="panel-sub">Type a symbol (e.g. eurusd, BTCUSDT) and load full specs in a new tab.</div>
                    </div>
                </div>
                <form id="instrument-specs-form" class="instrument-specs-row" action="/instrument-specs" method="get" target="_blank">
                    <input id="instrument-specs-input" name="q" type="text" placeholder="eurusd / BTCUSDT" />
                    <button id="instrument-specs-go" type="submit">Confirm</button>
                </form>
            </section>

            <section class=\"panel top-panel\" id=\"watchlist-widget\">
                <div class=\"panel-header\">
                    <div>
                        <h2>Watchlist</h2>
                        <div class=\"watchlist-sub\">Saved locally</div>
                    </div>
                    <div class=\"oo-toolbar\">
                        <span class=\"status-pill\" id=\"watchlist-count\">0</span>
                    </div>
                </div>

                <div class=\"watchlist-input\">
                    <input id=\"watchlist-input\" type=\"text\" placeholder=\"BTCUSDT, EURUSD\" />
                    <button type=\"button\" id=\"watchlist-add-btn\">Add</button>
                </div>

                <div class=\"watchlist-status\" id=\"watchlist-status\"></div>

                <div class=\"table-wrap\">
                    <table id=\"watchlist-table\">
                        <thead>
                            <tr>
                                <th>Instrument</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id=\"watchlist-items\"></tbody>
                    </table>
                </div>

                <p class=\"meta\" id=\"watchlist-empty\" style=\"display:none;\">No items yet.</p>
            </section>
            <section class=\"panel top-panel\" id=\"open-orders-panel\">
                <div class=\"panel-header\">
                    <div>
                        <h2>Open Orders / Positions</h2>
                        <div class=\"panel-sub\">Unchanged view, just moved to the top.</div>
                    </div>
                    <div class=\"oo-toolbar\">
                        <button class=\"secondary\" id=\"oo-refresh-btn\">Refresh</button>
                        <span class=\"status-pill\" id=\"oo-status\">Loading...</span>
                    </div>
                </div>

                <div class=\"table-wrap\">
                    <table id=\"open-orders-table\">
                        <thead>
                            <tr>
                                <th>Broker</th>
                                <th>Account</th>
                                <th>Category</th>
                                <th>Instrument</th>
                                <th>Type</th>
                                <th>Side</th>
                                <th>Size</th>
                                <th>Entry / Order</th>
                                <th>Current / Trigger</th>
                                <th>Stop Loss</th>
                                <th>Take Profit</th>
                                <th>Leverage / Margin</th>
                                <th>Opened</th>
                                <th>ID</th>
                                <th>Status</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>

                <p class=\"meta\" id=\"open-orders-empty\" style=\"display:none;\">No open orders or trades.</p>

                <div class=\"error-list\" id=\"open-orders-errors\" style=\"display:none;\">
                    <strong>Source issues</strong>
                    <ul></ul>
                </div>
            </section></div>
            </main>
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
      <input id="q" type="text" placeholder="eurusd" />
      <button id="load" type="button">Load</button>
      <a class="btn" id="download" href="#">Download JPG</a>
      <a class="btn" href="/">Back</a>
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
        .status-pill { display: inline-flex; align-items: center; justify-content: center; padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.85rem; font-weight: 700; background: #1f2937; color: #cbd5e1; }
        .status-pill.running { background: #14532d; color: #bbf7d0; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .secondary { background: #1f2937; color: #cbd5e1; }
    </style>
</head>
<body data-category=\"{category}\">
    <div class=\"nav-bar\">
        <button class=\"secondary\" id=\"nav-back\">Back</button>
        <button class=\"secondary\" id=\"nav-forward\">Forward</button>
    </div>
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
    <div class=\"nav-bar\">
        <button class=\"secondary\" id=\"nav-back\">Back</button>
        <button class=\"secondary\" id=\"nav-forward\">Forward</button>
    </div>
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


_OANDA_INSTRUMENT_META_CACHE: Dict[tuple[str, str], Dict[str, int]] = {}
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
) -> Dict[str, int]:
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
            _clean_env("OANDA_API_URL_DEMO") or "https://api-fxpractice.oanda.com"
        )
        missing = []
        if not token:
            missing.append("OANDA_API_KEY_DEMO")
        if not account_id:
            missing.append("OANDA_ACCOUNT_ID_DEMO")
        if missing:
            raise ValueError(f"OANDA demo credentials missing: {', '.join(missing)}")
        return {
            "mode": "demo",
            "token": token,
            "account_id": account_id,
            "base_url": base_url,
        }

    token = _clean_env("OANDA_API_KEY")
    account_id = _clean_env("OANDA_ACCOUNT_ID")
    base_url = _normalize_oanda_base_url(
        _clean_env("OANDA_API_URL_LIVE") or "https://api-fxtrade.oanda.com"
    )
    missing = []
    if not token:
        missing.append("OANDA_API_KEY")
    if not account_id:
        missing.append("OANDA_ACCOUNT_ID")
    if missing:
        raise ValueError(f"OANDA live credentials missing: {', '.join(missing)}")
    return {"mode": "live", "token": token, "account_id": account_id, "base_url": base_url}


def _get_oanda_history_config(mode: str = "live") -> Dict[str, str]:
    acct = (mode or "live").strip().lower()
    if acct in ("demo", "practice"):
        account_id = _clean_env("OANDA_ACCOUNT_ID_DEMO")
        api_key = _clean_env("OANDA_API_KEY_DEMO")
        base_url = _normalize_oanda_base_url(
            _clean_env("OANDA_API_URL_DEMO") or "https://api-fxpractice.oanda.com"
        )
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
    base_url = _normalize_oanda_base_url(
        _clean_env("OANDA_API_URL") or "https://api-fxtrade.oanda.com"
    )
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


async def _fetch_oanda_json(
    *, base_url: str, account_id: str, api_key: str, endpoint: str, mode: str
) -> Dict[str, object]:
    token = (api_key or "").strip().strip('"').strip("'")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/v3{endpoint.format(account_id=account_id)}"
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
                f"OANDA request failed ({exc.response.status_code}): {exc.response.text}"
            ) from exc
    return resp.json()


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
) -> List[Dict[str, object]]:
    await _oanda_preflight(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        mode=account_context,
    )
    trades_payload = await _fetch_oanda_json(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        endpoint="/accounts/{account_id}/openTrades",
        mode=account_context,
    )
    orders_payload = await _fetch_oanda_json(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        endpoint="/accounts/{account_id}/pendingOrders",
        mode=account_context,
    )

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
    return items


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
    BYBIT_LOGGER.info(
        "OANDA_CFG mode=%s base=%s account_id=%s token_last4=%s",
        cfg["mode"],
        cfg["base_url"],
        cfg["account_id"],
        cfg["token"][-4:],
    )
    await _oanda_preflight(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        mode=cfg["mode"],
    )
    meta = await _fetch_oanda_instrument_meta(
        base_url=cfg["base_url"],
        account_id=cfg["account_id"],
        api_key=cfg["token"],
        symbol=symbol,
        mode=cfg["mode"],
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

    url = (
        f"{cfg['base_url'].rstrip('/')}/v3/accounts/{cfg['account_id']}/orders"
    )
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    BYBIT_LOGGER.info(
        "OANDA_CALL mode=%s base=%s account_id=%s token_last4=%s url=%s",
        cfg["mode"],
        cfg["base_url"],
        cfg["account_id"],
        cfg["token"][-4:],
        url,
    )
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json={"order": order_payload})
    BYBIT_LOGGER.info(
        "OANDA_RESP mode=%s status=%s url=%s body=%s",
        cfg["mode"],
        resp.status_code,
        url,
        resp.text[:200],
    )
    if resp.status_code >= 400:
        raise ValueError(f"OANDA order failed ({resp.status_code}): {resp.text}")
    return resp.json()
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
    resp.raise_for_status()
    payload = resp.json()
    ret_code = payload.get("retCode")
    if ret_code not in (0, "0"):
        raise ValueError(payload.get("retMsg") or "Bybit request failed")
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
            size_raw = position.get("size")
            size = None
            try:
                size_val = float(size_raw)
                if size_val == 0:
                    continue
                size = abs(size_val)
            except (TypeError, ValueError):
                size = size_raw
            items.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "instrument": position.get("symbol"),
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
            items.append(
                {
                    "broker": "Bybit",
                    "account": account_context,
                    "category": category,
                    "instrument": order.get("symbol"),
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
                    "status": status or "OPEN",
                }
            )
    return {"items": items, "errors": errors}


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
        cleaned.append(payload)
    return cleaned


def _replace_pending_webhooks(items: object) -> List[Dict[str, object]]:
    normalized = _normalize_pending_webhooks(items)
    _save_pending_webhooks(normalized)
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

    replaced = False
    for idx, existing in enumerate(items):
        if str(existing.get("id", "")).strip() == webhook_id:
            items[idx] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)

    _save_pending_webhooks(items)
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
            return merged
    return None


def _set_pending_webhook_enabled(webhook_id: str, enabled: bool) -> Dict[str, object]:
    items = _load_pending_webhooks()
    for idx, entry in enumerate(items):
        if str(entry.get("id", "")).strip() == webhook_id:
            items[idx] = {**entry, "enabled": enabled, "updated_at": int(time.time())}
            _save_pending_webhooks(items)
            return items[idx]
    raise HTTPException(status_code=404, detail="Pending webhook not found.")


def _delete_pending_webhook(webhook_id: str) -> bool:
    items = _load_pending_webhooks()
    remaining = [entry for entry in items if str(entry.get("id", "")).strip() != webhook_id]
    if len(remaining) == len(items):
        return False
    _save_pending_webhooks(remaining)
    return True


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
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
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
    if not base_coin:
        raise ValueError("base_coin is required for trendline options.")
    if risk_usdt <= 0:
        raise ValueError("risk_usdt must be > 0.")

    exp_date = _expiry_to_bybit_expdate(expiry)
    opt = str(option_type).strip().capitalize()
    if opt not in {"Call", "Put"}:
        raise ValueError("option_type must be Call or Put.")

    inst = await _bybit_public_get(
        base_url,
        "/v5/market/instruments-info",
        {"category": "option", "baseCoin": base_coin, "expDate": exp_date, "optionType": opt},
    )
    inst_list = inst.get("result", {}).get("list", []) or []
    inst_map = {item.get("symbol"): item for item in inst_list if item.get("symbol")}

    tks = await _bybit_public_get(
        base_url,
        "/v5/market/tickers",
        {"category": "option", "baseCoin": base_coin, "expDate": exp_date, "symbol": ""},
    )
    tk_list = tks.get("result", {}).get("list", []) or []

    sides = 2 if fee_mode == "roundtrip" else 1
    fee_rate = (
        BYBIT_OPTIONS_TAKER_FEE_RATE
        if order_type == "market"
        else BYBIT_OPTIONS_MAKER_FEE_RATE
    )

    target_min = risk_usdt
    target_max = risk_usdt + max(0.0, tolerance_usdt)

    best: Optional[Dict[str, object]] = None
    best_score = float("inf")

    for tk in tk_list:
        sym = tk.get("symbol")
        if not sym or sym not in inst_map:
            continue

        parts = str(sym).split("-")
        if len(parts) < 4:
            continue
        sym_opt = parts[3].upper()
        if (opt == "Call" and sym_opt != "C") or (opt == "Put" and sym_opt != "P"):
            continue

        ask = float(tk.get("ask1Price") or 0)
        bid = float(tk.get("bid1Price") or 0)
        lastp = float(tk.get("lastPrice") or 0)
        mark = float(tk.get("markPrice") or 0)

        price = ask or lastp or mark or bid
        if price <= 0:
            continue

        inst_info = inst_map[sym]
        lot = inst_info.get("lotSizeFilter", {}) or {}
        min_qty = float(lot.get("minOrderQty") or 0)
        step = float(lot.get("qtyStep") or min_qty or 0)
        max_qty = float(lot.get("maxOrderQty") or 0)

        if min_qty <= 0 or step <= 0:
            continue

        per_unit = price * (1.0 + fee_rate * sides)
        if per_unit <= 0:
            continue

        qty_floor = _round_step(target_max / per_unit, step)
        if qty_floor < min_qty:
            qty_floor = min_qty
        if max_qty > 0 and qty_floor > max_qty:
            continue

        total = per_unit * qty_floor
        if total > target_max:
            continue

        penalty = 0.0 if total >= target_min else (target_min - total) * 10.0
        score = abs(total - target_min) + penalty

        if score < best_score:
            best_score = score
            best = {
                "symbol": sym,
                "qty": round(qty_floor, 8),
                "limit_price": round(price, 8),
                "total_est": total,
            }

    if not best:
        raise ValueError("No option found that fits the requested risk/tolerance.")

    _log_webhook_event(request_id, "trendline_option_resolved", best)
    return best


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


def _get_oanda_config(account: Optional[str]) -> Dict[str, str]:
    acct = (account or "").strip().lower()
    if acct in ("demo", "practice"):
        token = os.getenv("OANDA_API_KEY_DEMO") or os.getenv("OANDA_API_KEY")
        account_id = os.getenv("OANDA_ACCOUNT_ID_DEMO")
        base_url = os.getenv("OANDA_API_URL_DEMO") or "https://api-fxpractice.oanda.com"
        missing = []
        if not token:
            missing.append("OANDA_API_KEY_DEMO (or OANDA_API_KEY fallback)")
        if not account_id:
            missing.append("OANDA_ACCOUNT_ID_DEMO")
        if missing:
            raise ValueError(f"OANDA demo credentials missing: {', '.join(missing)}")
        return {"token": token, "account_id": account_id, "base_url": base_url}

    token = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    base_url = os.getenv("OANDA_API_URL_LIVE") or "https://api-fxtrade.oanda.com"
    missing = []
    if not token:
        missing.append("OANDA_API_KEY")
    if not account_id:
        missing.append("OANDA_ACCOUNT_ID")
    if missing:
        raise ValueError(f"OANDA live credentials missing: {', '.join(missing)}")
    return {"token": token, "account_id": account_id, "base_url": base_url}


async def _place_bybit_order(
    payload: Dict[str, object], *, request_id: str
) -> Dict[str, object]:
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

    body: Dict[str, object] = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Limit" if order_type == "limit" else "Market",
        "qty": str(qty_val),
        "timeInForce": "GTC",
        "orderLinkId": uuid4().hex,
    }
    if order_type == "limit":
        body["price"] = str(price_val)

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
    if take_profit is not None:
        body["takeProfit"] = str(take_profit)
    if stop_loss is not None:
        body["stopLoss"] = str(stop_loss)
    if order_type == "limit" and category == "linear" and price_val is not None:
        if take_profit_offset is not None:
            tp_target = price_val + take_profit_offset
            body["takeProfit"] = _format_decimal_value(tp_target)
        if stop_loss_offset is not None:
            sl_target = price_val + stop_loss_offset
            body["stopLoss"] = _format_decimal_value(sl_target)
        if "takeProfit" in body or "stopLoss" in body:
            body["tpslMode"] = "Full"
            body["tpOrderType"] = "Market"
            body["slOrderType"] = "Market"
            body.setdefault("positionIdx", 0)
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
    response.raise_for_status()
    data = response.json()
    _log_webhook_event(
        request_id,
        "order_response",
        {
            "retCode": data.get("retCode"),
            "retMsg": data.get("retMsg"),
            "result": data.get("result", {}),
        },
    )
    if data.get("retCode") != 0:
        raise ValueError(f"Bybit order failed: {data.get('retMsg')}")
    order_result = data.get("result", {}) or {}
    order_id = order_result.get("orderId")
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    if pending_id:
        # Mark the local pending webhook as triggered/consumed. Do not convert it into a broker order.
        _update_pending_webhook(
            pending_id,
            {
                "status": "TRIGGERED",
                "enabled": False,
                "triggered_at": int(time.time()),
                "exchange": "Bybit",
                "account": account,
                "category": category,
                "instrument": symbol,
                "order_id": order_id,
                "limit_cancel_offset": limit_cancel_offset,
                "limit_cancel_offset_pct": limit_cancel_pct,
            },
        )
        _schedule_dropbox_upload_state_backup()

    tpsl_result: Optional[Dict[str, object]] = None
    tpsl_error: Optional[str] = None
    tp_order: Optional[Dict[str, object]] = None
    tp_error: Optional[str] = None
    if category == "linear" and any(
        item is not None
        for item in (take_profit_offset, stop_loss_offset, take_profit, stop_loss)
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
            _log_webhook_event(
                request_id,
                "tpsl_computed",
                {
                    "entry_price": entry_price,
                    "take_profit_offset": take_profit_offset,
                    "stop_loss_offset": stop_loss_offset,
                    "take_profit": tp_target,
                    "stop_loss": sl_target,
                },
            )
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
            tpsl_error = str(exc)
            BYBIT_LOGGER.exception(
                "WEBHOOK_TPSL %s tpsl_failed symbol=%s account=%s error=%s",
                request_id,
                symbol,
                account,
                exc,
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
    _log_webhook_event(
        request_id,
        "oanda_order_response",
        {"result": result},
    )
    order_id = _extract_oanda_order_id(result)
    limit_cancel_offset, limit_cancel_pct = _parse_limit_cancel_settings(payload)
    pending_id = str(payload.get("pending_webhook_id") or "").strip()
    if not pending_id:
        # Backwards compatibility: older TradingView alerts may not include
        # pending_webhook_id. Infer the deterministic id used by
        # oanda-calculator-clone when track_pending=yes.
        safe_symbol = "".join(ch for ch in instrument if ch.isalnum() or ch in "_-")
        safe_side = "".join(ch for ch in action if ch.isalnum() or ch in "_-")
        safe_ot = "".join(ch for ch in order_type if ch.isalnum() or ch in "_-")
        pending_id = f"calc_oanda_{account}_{safe_symbol}_{safe_side}_{safe_ot}"
    if pending_id:
        # Mark the local pending webhook as triggered/consumed. Do not convert it into a broker order.
        updated = _update_pending_webhook(
            pending_id,
            {
                "status": "TRIGGERED",
                "enabled": False,
                "triggered_at": int(time.time()),
                "exchange": "OANDA",
                "account": account,
                "category": "forex",
                "instrument": symbol,
                "order_id": order_id,
                "limit_cancel_offset": limit_cancel_offset,
                "limit_cancel_offset_pct": limit_cancel_pct,
            },
        )
        if updated:
            _schedule_dropbox_upload_state_backup()

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


async def _cancel_oanda_order(*, cfg: Dict[str, str], order_id: str, mode: str) -> None:
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    endpoint = f"/v3/accounts/{cfg['account_id']}/orders/{order_id}/cancel"
    url = f"{cfg['base_url'].rstrip('/')}{endpoint}"
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.put(url, headers=headers)
    if resp.status_code >= 400:
        raise ValueError(f"OANDA cancel failed ({resp.status_code}): {resp.text}")


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
                        journal_rows = _journal_rows_from_bybit_execution(entry_payload)
                        if journal_rows:
                            _upsert_trading_journal_rows(journal_rows)
                        await _send_telegram_alert(_format_bybit_fill_alert(entry_payload))
                _BYBIT_EXEC_LAST_SEEN[account] = max_seen
            except Exception as exc:  # pragma: no cover - background task
                BYBIT_LOGGER.error("Bybit fill poll error: %s", exc)


async def _fetch_oanda_transactions(
    *,
    cfg: Dict[str, str],
    since_id: Optional[str],
) -> List[Dict[str, object]]:
    token = cfg["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{cfg['base_url'].rstrip('/')}/v3/accounts/{cfg['account_id']}/transactions"
    params: Dict[str, str] = {}
    if since_id:
        params["sinceID"] = since_id
    else:
        params["pageSize"] = "1"
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise ValueError(f"OANDA transactions failed ({resp.status_code}): {resp.text}")
    payload = resp.json()
    return payload.get("transactions", []) or []


async def _poll_oanda_fills() -> None:
    while True:
        await asyncio.sleep(FILL_ALERT_POLL_SECONDS)
        for account in ("live", "demo"):
            try:
                cfg = _get_oanda_config(account)
            except ValueError:
                continue
            try:
                last_seen = _OANDA_TX_LAST_SEEN.get(account)
                transactions = await _fetch_oanda_transactions(cfg=cfg, since_id=last_seen)
                if not transactions:
                    continue
                if last_seen is None:
                    _OANDA_TX_LAST_SEEN[account] = str(transactions[-1].get("id", ""))
                    continue
                max_seen = int(last_seen or 0)
                for entry in transactions:
                    tx_id_raw = str(entry.get("id", "")).strip()
                    if not tx_id_raw:
                        continue
                    try:
                        tx_id = int(tx_id_raw)
                    except ValueError:
                        continue
                    if tx_id <= max_seen:
                        continue
                    if tx_id > max_seen:
                        max_seen = tx_id
                    tx_type = str(entry.get("type") or "")
                    if "ORDER_FILL" not in tx_type:
                        continue
                    entry_payload = {**entry, "account": account}
                    journal_rows = _journal_rows_from_oanda_order_fill(entry_payload)
                    if journal_rows:
                        _upsert_trading_journal_rows(journal_rows)
                    await _send_telegram_alert(_format_oanda_fill_alert(entry_payload))
                _OANDA_TX_LAST_SEEN[account] = str(max_seen)
            except Exception as exc:  # pragma: no cover - background task
                BYBIT_LOGGER.error("OANDA fill poll error: %s", exc)


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
    .toolbar { display:flex; gap:8px; align-items:center; padding:12px; margin-bottom:12px; }
    .toolbar input { flex:1; background:#0f172a; color:#e5e7eb; border:1px solid #334155; border-radius:8px; padding:8px 10px; }
    .toolbar button { background:#2563eb; color:white; border:0; border-radius:8px; padding:8px 12px; cursor:pointer; }
    .balances { padding:12px; margin-bottom:12px; display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; }
    .bal-card { background:#0f172a; border:1px solid #1f2937; border-radius:10px; padding:10px; }
    .table-wrap { padding:8px; overflow:auto; }
    table { width:100%; border-collapse:collapse; min-width:1200px; }
    th, td { padding:10px 8px; border-bottom:1px solid #1f2937; white-space:nowrap; }
    th { color:#93c5fd; text-align:left; position:sticky; top:0; background:#111827; }
    tr:hover td { background:#0f172a; }
    .muted { color:#94a3b8; }
    .pill { border:1px solid #334155; border-radius:999px; padding:2px 8px; font-size:12px; }
    .num.pos { color:#86efac; }
    .num.neg { color:#fca5a5; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"toolbar\">
      <input id=\"tj-filter\" placeholder=\"Filter symbol / account / source (e.g. EURUSD, BTCUSDT, oanda, bybit demo)\" />
      <button id=\"tj-filter-btn\">Filter</button>
      <button id=\"tj-clear-btn\">Clear</button>
      <button id=\"tj-sync-btn\">Sync now</button>
      <span id=\"tj-status\" class=\"muted\"></span>
    </div>
    <div id="tj-quick-filters" class="toolbar" style="padding:8px 12px; margin-top:-6px; margin-bottom:12px; flex-wrap:wrap;">
      <button class="tj-chip" data-q="error">Errors only</button>
      <button class="tj-chip" data-q="breakeven">Breakeven only</button>
      <button class="tj-chip" data-q="held through news">Held through news</button>
      <button class="tj-chip" data-q="spiked out">Spiked out</button>
      <button class="tj-chip" data-q="early close">Early close</button>
    </div>
    <div id=\"tj-stats\" class=\"balances\"></div>
    <div id=\"tj-balances\" class=\"balances\"></div>
    <div class=\"table-wrap\">
      <table id=\"tj-table\">
        <thead>
          <tr>
            <th data-sort="close_time">Close Time</th>
            <th data-sort="account_label">Account</th>
            <th data-sort="symbol">Symbol</th>
            <th data-sort="side">Side</th>
            <th data-sort="setup">Setup</th>
            <th data-sort="qty">Qty</th>
            <th data-sort="entry_price">Entry</th>
            <th data-sort="exit_price">Exit</th>
            <th data-sort="stop_loss">Stop Loss</th>
            <th data-sort="take_profit">Target</th>
            <th data-sort="commission">Commission</th>
            <th data-sort="net_profit">Net Profit</th>
            <th data-sort="balance_after_trade">Bal After Trade</th>
            <th data-sort="breakeven">Breakeven</th>
            <th data-sort="status">Status</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div id=\"tj-empty\" class=\"muted\" style=\"padding:12px; display:none;\">No trades found.</div>
    </div>
  </div>
  <script src=\"/static/trading_journal.js\"></script>
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


async def _fetch_oanda_account_summary(account: str) -> Dict[str, object]:
    cfg = _get_oanda_config(account)
    payload = await _fetch_oanda_json(
        cfg["base_url"],
        f"/v3/accounts/{cfg['account_id']}/summary",
        cfg["token"],
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
    }


def _row_sort_dt(row: Dict[str, object]) -> str:
    return str(row.get("close_time") or row.get("open_time") or "")


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
    bal_map: Dict[str, float] = {}
    ccy_map: Dict[str, str] = {}
    for bal in current_balances:
        account = str(bal.get("account") or bal.get("label") or "")
        balance = _to_float(bal.get("balance"))
        if account and balance is not None:
            bal_map[account] = balance
            ccy_map[account] = str(bal.get("currency") or "")

    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("account_label") or row.get("account") or "")].append(row)

    out_rows = [dict(row) for row in rows]
    idx_by_id = {str(row.get("id")): idx for idx, row in enumerate(out_rows)}

    for account, account_rows in grouped.items():
        if account not in bal_map:
            continue
        running = bal_map[account]
        ordered = sorted(account_rows, key=_row_sort_dt, reverse=True)
        for row in ordered:
            row_id = str(row.get("id"))
            out_idx = idx_by_id.get(row_id)
            if out_idx is None:
                continue
            out_rows[out_idx]["balance_after_trade"] = running
            out_rows[out_idx]["balance_after_trade_currency"] = ccy_map.get(account) or str(
                row.get("currency") or ""
            )
            pnl = _to_float(row.get("net_profit"))
            if pnl is not None:
                running -= pnl
    return out_rows


def _avg(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _compute_journal_stats(
    rows: List[Dict[str, object]], balances: List[Dict[str, object]]
) -> Dict[str, object]:
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
    by_journal_instrument: Dict[str, Dict[str, object]] = {}

    most_wins: Dict[str, object] = {"symbol": None, "wins": -1}
    most_losses: Dict[str, object] = {"symbol": None, "losses": -1}

    for row in rows:
        symbol = str(row.get("symbol") or "")
        account = str(row.get("account_label") or row.get("account") or "")
        key_ji = f"{account}::{symbol}"

        for bucket_key, bucket_map in ((symbol, by_instrument), (key_ji, by_journal_instrument)):
            if bucket_key not in bucket_map:
                bucket_map[bucket_key] = {
                    "symbol": symbol,
                    "account": account if bucket_map is by_journal_instrument else None,
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "break_even": 0,
                    "stop_losses": [],
                    "take_profits": [],
                }
            bucket = bucket_map[bucket_key]
            bucket["total_trades"] += 1
            if _is_win(row):
                bucket["wins"] += 1
            elif _is_loss(row):
                bucket["losses"] += 1
            else:
                bucket["break_even"] += 1

            sl = _to_float(row.get("stop_loss"))
            tp = _to_float(row.get("take_profit"))
            if sl is not None:
                bucket["stop_losses"].append(sl)
            if tp is not None:
                bucket["take_profits"].append(tp)

    def finalize_bucket_map(
        source: Dict[str, Dict[str, object]]
    ) -> List[Dict[str, object]]:
        nonlocal most_wins, most_losses
        out: List[Dict[str, object]] = []
        for _, bucket in source.items():
            item = dict(bucket)
            item["avg_stop_loss"] = _avg(item.pop("stop_losses"))
            item["avg_take_profit"] = _avg(item.pop("take_profits"))
            out.append(item)
            if item["account"] is None:
                if item["wins"] > most_wins["wins"]:
                    most_wins = {"symbol": item["symbol"], "wins": item["wins"]}
                if item["losses"] > most_losses["losses"]:
                    most_losses = {"symbol": item["symbol"], "losses": item["losses"]}
        out.sort(key=lambda x: (-(x.get("total_trades") or 0), str(x.get("symbol") or "")))
        return out

    all_sl = [_to_float(row.get("stop_loss")) for row in rows]
    all_tp = [_to_float(row.get("take_profit")) for row in rows]
    all_sl = [x for x in all_sl if x is not None]
    all_tp = [x for x in all_tp if x is not None]

    return {
        "totals": {
            "trades": len(rows),
            "wins": sum(1 for row in rows if _is_win(row)),
            "losses": sum(1 for row in rows if _is_loss(row)),
            "break_even": sum(1 for row in rows if _is_be(row)),
            "avg_stop_loss": _avg(all_sl),
            "avg_take_profit": _avg(all_tp),
        },
        "balances": balance_by_account,
        "by_instrument": finalize_bucket_map(by_instrument),
        "by_journal_instrument": finalize_bucket_map(by_journal_instrument),
        "instrument_with_most_wins": most_wins if most_wins["symbol"] else None,
        "instrument_with_most_losses": most_losses if most_losses["symbol"] else None,
        "balance_after_trade_note": "Approximate if deposits/withdrawals/transfers are not captured in journal rows.",
    }


@app.get("/api/trading-journal")
async def trading_journal_items(filter: str = "") -> JSONResponse:
    items = _get_trading_journal_rows()
    query = (filter or "").strip().lower()
    if query:

        def match(row: Dict[str, object]) -> bool:
            searchable = [
                row.get("symbol"),
                row.get("symbol_raw"),
                row.get("account_label"),
                row.get("account"),
                row.get("source"),
                row.get("sheet"),
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
            hay = " ".join(str(x or "") for x in searchable).lower()
            return query in hay

        items = [r for r in items if match(r)]

    balances = _get_excel_account_balances()
    items = _calc_balance_after_trade(items, balances)
    stats = _compute_journal_stats(items, balances)
    return JSONResponse({"items": items, "count": len(items), "stats": stats})


@app.get("/api/trading-journal/balances")
async def trading_journal_balances() -> JSONResponse:
    rows = _get_trading_journal_rows()
    excel = _get_excel_account_balances()
    by_acc = {
        str((bal.get("account") or bal.get("label") or "")).upper(): dict(bal)
        for bal in excel
    }

    for row in rows:
        account = str(row.get("account_label") or row.get("account") or "").strip()
        if not account:
            continue
        key = account.upper()
        if key not in by_acc:
            by_acc[key] = {
                "account": account,
                "label": account,
                "balance": None,
                "nav": None,
                "currency": _infer_account_currency(account),
                "missing_balance": True,
            }

    items = sorted(by_acc.values(), key=lambda x: str(x.get("label") or ""))
    return JSONResponse({"items": items})


@app.post("/api/trading-journal/sync")
async def trading_journal_sync() -> JSONResponse:
    try:
        result = await asyncio.to_thread(_import_trading_journal_from_dropbox_excel)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc), "type": exc.__class__.__name__},
            status_code=500,
        )


@app.get("/api/open-orders")
async def fetch_open_orders() -> JSONResponse:
    items: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []

    oanda_has_credentials = False
    for account_mode in ("live", "demo"):
        try:
            cfg = _get_oanda_config(account_mode)
        except ValueError:
            continue
        oanda_has_credentials = True
        try:
            items.extend(
                await _collect_oanda_open_items(
                    base_url=cfg["base_url"],
                    account_id=cfg["account_id"],
                    api_key=cfg["token"],
                    account_context=account_mode,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "broker": "OANDA",
                    "account": account_mode,
                    "message": str(exc),
                }
            )

    if not oanda_has_credentials:
        errors.append(
            {
                "broker": "OANDA",
                "account": "unknown",
                "message": "Missing OANDA credentials.",
            }
        )

    bybit_has_credentials = False
    for account_mode in ("live", "demo"):
        _mode, api_key, api_secret, base_url, key_source = resolve_bybit_credentials_for(
            account_mode
        )
        if not api_key or not api_secret:
            continue
        bybit_has_credentials = True
        try:
            bybit_result = await _collect_bybit_open_items(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                account_context=account_mode,
            )
            items.extend(bybit_result["items"])
            errors.extend(bybit_result["errors"])
        except Exception as exc:
            errors.append(
                {
                    "broker": "Bybit",
                    "account": account_mode,
                    "message": str(exc),
                }
            )
        if key_source:
            BYBIT_LOGGER.debug(
                "OPEN_ORDERS account=%s base_url=%s key_source=%s",
                account_mode,
                base_url,
                key_source,
            )

    if not bybit_has_credentials:
        errors.append(
            {
                "broker": "Bybit",
                "account": "unknown",
                "message": "Missing Bybit credentials.",
            }
        )

    try:
        pending = _load_pending_webhooks()
        items.extend(
            [
                entry
                for entry in pending
                if bool(entry.get("enabled", True))
                and str(entry.get("status", "WAITING")).upper() == "WAITING"
            ]
        )
    except HTTPException as exc:
        errors.append(
            {
                "broker": "WEBHOOK",
                "account": "local",
                "message": str(exc.detail),
            }
        )

    return JSONResponse({"updated_at": time.time(), "items": items, "errors": errors})


@app.post("/api/open-orders/close")
async def close_open_order(payload: Dict[str, object]) -> JSONResponse:
    broker = str(payload.get("broker", "")).strip().lower()
    item_type = str(payload.get("type", "")).strip().lower()
    account = str(payload.get("account", "live")).strip().lower()

    if broker == "bybit":
        _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(
            "demo" if account == "demo" else "live"
        )
        if not api_key or not api_secret:
            raise HTTPException(status_code=500, detail="Missing Bybit credentials.")
        category = str(payload.get("category", "")).strip()
        symbol = str(payload.get("instrument", "")).strip()
        if not category or not symbol:
            raise HTTPException(status_code=400, detail="Bybit item missing category or symbol.")

        if item_type == "order":
            order_ref = str(payload.get("id", "")).strip()
            if not order_ref:
                raise HTTPException(status_code=400, detail="Bybit order ID missing.")
            cancel_body: Dict[str, object] = {"category": category, "symbol": symbol}
            if order_ref.startswith("calc_"):
                cancel_body["orderLinkId"] = order_ref
            else:
                cancel_body["orderId"] = order_ref
            try:
                response = await _bybit_signed_post(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    path="/v5/order/cancel",
                    body=cancel_body,
                )
            except Exception as exc:
                if _delete_pending_webhook(order_ref):
                    _schedule_dropbox_upload_state_backup()
                    return JSONResponse({"status": "ok", "removed_local": True})
                raise HTTPException(
                    status_code=502, detail=f"Bybit cancel failed: {exc}"
                ) from exc
            return JSONResponse({"status": "ok", "result": response.get("result", {})})

        if item_type == "position":
            side_raw = str(payload.get("side", "")).strip().lower()
            if side_raw in {"buy", "long"}:
                close_side = "Sell"
            elif side_raw in {"sell", "short"}:
                close_side = "Buy"
            else:
                raise HTTPException(status_code=400, detail="Bybit position side missing.")
            qty_raw = payload.get("size")
            try:
                qty_val = float(qty_raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Bybit position size must be numeric."
                ) from exc
            if qty_val <= 0:
                raise HTTPException(
                    status_code=400, detail="Bybit position size must be greater than zero."
                )
            body: Dict[str, object] = {
                "category": category,
                "symbol": symbol,
                "side": close_side,
                "orderType": "Market",
                "qty": str(qty_val),
                "reduceOnly": True,
            }
            position_idx = payload.get("position_idx")
            if position_idx is not None:
                try:
                    body["positionIdx"] = int(position_idx)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400, detail="Bybit positionIdx must be numeric."
                    ) from exc
            response = await _bybit_signed_post(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                path="/v5/order/create",
                body=body,
            )
            return JSONResponse({"status": "ok", "result": response.get("result", {})})

        raise HTTPException(status_code=400, detail="Unsupported Bybit item type.")

    if broker == "oanda":
        mode = account if account in {"live", "demo"} else "live"
        try:
            cfg = _get_oanda_config(mode)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        order_id = str(payload.get("id", "")).strip()
        if not order_id:
            raise HTTPException(status_code=400, detail="OANDA item ID missing.")
        await _oanda_preflight(
            base_url=cfg["base_url"],
            account_id=cfg["account_id"],
            api_key=cfg["token"],
            mode=mode,
        )
        headers = {
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
        }
        if item_type == "order":
            endpoint = f"/v3/accounts/{cfg['account_id']}/orders/{order_id}/cancel"
            url = f"{cfg['base_url'].rstrip('/')}{endpoint}"
            token_last4 = cfg["token"][-4:] if cfg["token"] else None
            BYBIT_LOGGER.info(
                "OANDA_CALL mode=%s base=%s account_id=%s token_last4=%s url=%s",
                mode,
                cfg["base_url"],
                cfg["account_id"],
                token_last4,
                url,
            )
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.put(url, headers=headers)
            if 300 <= resp.status_code < 400:
                BYBIT_LOGGER.info(
                    "OANDA_REDIRECT mode=%s status=%s url=%s location=%s",
                    mode,
                    resp.status_code,
                    url,
                    resp.headers.get("location"),
                )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"OANDA cancel failed ({resp.status_code}): {resp.text}",
                )
            return JSONResponse({"status": "ok", "result": resp.json()})
        if item_type == "position":
            endpoint = f"/v3/accounts/{cfg['account_id']}/trades/{order_id}/close"
            url = f"{cfg['base_url'].rstrip('/')}{endpoint}"
            token_last4 = cfg["token"][-4:] if cfg["token"] else None
            BYBIT_LOGGER.info(
                "OANDA_CALL mode=%s base=%s account_id=%s token_last4=%s url=%s",
                mode,
                cfg["base_url"],
                cfg["account_id"],
                token_last4,
                url,
            )
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.put(
                    url,
                    headers=headers,
                    json={"units": "ALL"},
                )
            if 300 <= resp.status_code < 400:
                BYBIT_LOGGER.info(
                    "OANDA_REDIRECT mode=%s status=%s url=%s location=%s",
                    mode,
                    resp.status_code,
                    url,
                    resp.headers.get("location"),
                )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"OANDA close failed ({resp.status_code}): {resp.text}",
                )
            return JSONResponse({"status": "ok", "result": resp.json()})
        raise HTTPException(status_code=400, detail="Unsupported OANDA item type.")

    raise HTTPException(status_code=400, detail="Unsupported broker.")

OANDA_HISTORY_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>OANDA Transaction History Export</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }
        .toggle-group { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.75rem; }
        .toggle-group button.active { background: #38bdf8; color: #0f172a; }
        .toggle-group { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.75rem; }
        .toggle-group button.active { background: #38bdf8; color: #0f172a; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        .error { margin-top: 0.75rem; color: #fca5a5; }
        .badge { display: inline-block; padding: 0.35rem 0.65rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>OANDA Transaction History Export</h1>
        <p class=\"meta\">Generate transaction history CSV exports for the selected timeframe. Jobs run in the background and will download automatically when ready.</p>
        <div class=\"badge\">Account</div>
        <div class=\"toggle-group\" data-group=\"account\">
            <button class=\"secondary\" data-account=\"live\">LIVE</button>
            <button class=\"secondary\" data-account=\"demo\">DEMO</button>
        </div>
        <div class=\"badge\" style=\"margin-top: 1rem;\">Select range</div>
        <div class=\"actions\">
            <button class=\"primary\" data-period=\"day\">DAY</button>
            <button class=\"primary\" data-period=\"week\">WEEK</button>
            <button class=\"primary\" data-period=\"month\">MONTH</button>
            <button class=\"primary\" data-period=\"year\">YEAR</button>
            <button class=\"primary\" data-period=\"3y\">3 YEARS</button>
            <button class=\"primary\" data-period=\"complete\">COMPLETE</button>
        </div>
        <div id=\"status\" class=\"status\">Choose a timeframe to start.</div>
        <div id=\"error\" class=\"error\"></div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const errorEl = document.getElementById('error');
        const buttons = Array.from(document.querySelectorAll('button[data-period]'));
        const accountButtons = Array.from(document.querySelectorAll('button[data-account]'));
        let selectedAccount = 'demo';

        const setButtonsDisabled = (disabled) => {
            buttons.forEach((btn) => {
                btn.disabled = disabled;
            });
        };

        const startExport = async (payload) => {
            errorEl.textContent = '';
            statusEl.textContent = 'Creating export job...';
            setButtonsDisabled(true);
            try {
                const response = await fetch('/api/oanda-history/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...payload, account: selectedAccount }),
                });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                await pollJob(data.job_id);
            } catch (err) {
                errorEl.textContent = err.message || 'Unable to start export.';
                statusEl.textContent = 'Export failed to start.';
                setButtonsDisabled(false);
            }
        };

        const pollJob = async (jobId) => {
            statusEl.textContent = 'Job queued...';
            while (true) {
                await new Promise((resolve) => setTimeout(resolve, 1500));
                const response = await fetch(`/api/oanda-history/export/${jobId}`, { cache: 'no-store' });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                statusEl.textContent = `Status: ${data.status}`;
                if (data.status === 'done') {
                    window.location.href = data.download_url;
                    setButtonsDisabled(false);
                    return;
                }
                if (data.status === 'error') {
                    errorEl.textContent = data.error || 'Export failed.';
                    setButtonsDisabled(false);
                    return;
                }
            }
        };

        accountButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const account = button.dataset.account || 'live';
                selectedAccount = account;
                accountButtons.forEach((btn) => btn.classList.toggle('active', btn === button));
            });
        });
        const defaultAccountButton = accountButtons.find((btn) => btn.dataset.account === selectedAccount);
        if (defaultAccountButton) {
            defaultAccountButton.classList.add('active');
        }

        buttons.forEach((button) => {
            button.addEventListener('click', () => {
                const period = button.dataset.period;
                if (period) {
                    startExport({ period });
                }
            });
        });
    </script>
</body>
</html>"""

BYBIT_HISTORY_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Bybit Trade History Export</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }
        .toggle-group { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; }
        .toggle-group { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.75rem; }
        .toggle-group button.active { background: #2563eb; color: #e2e8f0; }
        .hidden { display: none !important; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        .error { margin-top: 0.75rem; color: #fca5a5; }
        .badge { display: inline-block; padding: 0.35rem 0.65rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>Bybit Trade History Export</h1>
        <p class=\"meta\">Generate a CSV export of Bybit execution history for the selected timeframe. Jobs run in the background and will download automatically when ready. Note: Bybit trade history is limited to the last 2 years.</p>
        <div class=\"badge\">Account</div>
        <div class=\"toggle-group\" data-group=\"account\">
            <button class=\"secondary\" data-account=\"live\">LIVE</button>
            <button class=\"secondary\" data-account=\"demo\">DEMO</button>
        </div>
        <div class=\"badge\" style=\"margin-top: 1rem;\">Select range</div>
        <div class=\"actions\" id=\"range-buttons\">
            <button class=\"primary\" data-period=\"day\">DAY</button>
            <button class=\"primary\" data-period=\"week\">WEEK</button>
            <button class=\"primary\" data-period=\"month\">MONTH</button>
            <button class=\"primary\" data-period=\"year\">YEAR</button>
            <button class=\"primary\" data-period=\"3y\">3 YEARS</button>
            <button class=\"primary\" data-period=\"complete\">COMPLETE</button>
            <button class=\"primary hidden\" data-days=\"7\" id=\"demo-7d\">7 DAYS</button>
        </div>
        <div id=\"status\" class=\"status\">Choose a timeframe to start.</div>
        <div id=\"error\" class=\"error\"></div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const errorEl = document.getElementById('error');
        const rangeButtonsEl = document.getElementById('range-buttons');
        const periodButtons = Array.from(document.querySelectorAll('button[data-period]'));
        const demo7dButton = document.getElementById('demo-7d');
        const accountButtons = Array.from(document.querySelectorAll('button[data-account]'));
        let selectedAccount = 'demo';

        const setButtonsDisabled = (disabled) => {
            Array.from(rangeButtonsEl.querySelectorAll('button')).forEach((btn) => { btn.disabled = disabled; });
        };

        const syncRangeButtons = () => {
            const isDemo = selectedAccount === 'demo';
            periodButtons.forEach((btn) => btn.classList.toggle('hidden', isDemo));
            if (demo7dButton) demo7dButton.classList.toggle('hidden', !isDemo);
        };

        const startExport = async (payload) => {
            errorEl.textContent = '';
            statusEl.textContent = 'Creating export job...';
            setButtonsDisabled(true);
            try {
                const response = await fetch('/api/bybit-history/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...payload, account: selectedAccount }),
                });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                await pollJob(data.job_id);
            } catch (err) {
                errorEl.textContent = err.message || 'Unable to start export.';
                statusEl.textContent = 'Export failed to start.';
                setButtonsDisabled(false);
            }
        };

        const pollJob = async (jobId) => {
            statusEl.textContent = 'Job queued...';
            while (true) {
                await new Promise((resolve) => setTimeout(resolve, 1500));
                const response = await fetch(`/api/bybit-history/export/${jobId}`, { cache: 'no-store' });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                statusEl.textContent = `Status: ${data.status}`;
                if (data.status === 'done') {
                    window.location.href = data.download_url;
                    setButtonsDisabled(false);
                    return;
                }
                if (data.status === 'error') {
                    errorEl.textContent = data.error || 'Export failed.';
                    setButtonsDisabled(false);
                    return;
                }
            }
        };

        accountButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const account = button.dataset.account || 'live';
                selectedAccount = account;
                accountButtons.forEach((btn) => btn.classList.toggle('active', btn === button));
                syncRangeButtons();
            });
        });
        const defaultAccountButton = accountButtons.find((btn) => btn.dataset.account === selectedAccount);
        if (defaultAccountButton) {
            defaultAccountButton.classList.add('active');
        }

        periodButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const period = button.dataset.period;
                if (period) startExport({ period });
            });
        });

        if (demo7dButton) {
            demo7dButton.addEventListener('click', () => startExport({ days: 7 }));
        }

        syncRangeButtons();
    </script>
</body>
</html>"""

COINSPOT_HISTORY_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>CoinSpot History Export</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        .error { margin-top: 0.75rem; color: #fca5a5; }
        .badge { display: inline-block; padding: 0.35rem 0.65rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>CoinSpot History Export</h1>
        <p class=\"meta\">Generate a ZIP file containing CoinSpot deposits, withdrawals, orders, and transfer history. Jobs run in the background and will download automatically when ready.</p>
        <div class=\"badge\">Select range</div>
        <div class=\"actions\">
            <button class=\"primary\" data-period=\"day\">DAY</button>
            <button class=\"primary\" data-period=\"week\">WEEK</button>
            <button class=\"primary\" data-period=\"month\">MONTH</button>
            <button class=\"primary\" data-period=\"year\">YEAR</button>
            <button class=\"primary\" data-period=\"3y\">3 YEARS</button>
            <button class=\"primary\" data-period=\"complete\">COMPLETE</button>
        </div>
        <div id=\"status\" class=\"status\">Choose a timeframe to start.</div>
        <div id=\"error\" class=\"error\"></div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const errorEl = document.getElementById('error');
        const buttons = Array.from(document.querySelectorAll('button[data-period]'));

        const setButtonsDisabled = (disabled) => {
            buttons.forEach((btn) => { btn.disabled = disabled; });
        };

        const startExport = async (payload) => {
            errorEl.textContent = '';
            statusEl.textContent = 'Creating export job...';
            setButtonsDisabled(true);
            try {
                const response = await fetch('/api/coinspot-history/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                await pollJob(data.job_id);
            } catch (err) {
                errorEl.textContent = err.message || 'Unable to start export.';
                statusEl.textContent = 'Export failed to start.';
                setButtonsDisabled(false);
            }
        };

        const pollJob = async (jobId) => {
            statusEl.textContent = 'Job queued...';
            while (true) {
                await new Promise((resolve) => setTimeout(resolve, 1500));
                const response = await fetch(`/api/coinspot-history/export/${jobId}`, { cache: 'no-store' });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || response.statusText);
                }
                const data = await response.json();
                statusEl.textContent = `Status: ${data.status}`;
                if (data.status === 'done') {
                    window.location.href = data.download_url;
                    setButtonsDisabled(false);
                    return;
                }
                if (data.status === 'error') {
                    errorEl.textContent = data.error || 'Export failed.';
                    setButtonsDisabled(false);
                    return;
                }
            }
        };

        buttons.forEach((button) => {
            button.addEventListener('click', () => {
                const period = button.dataset.period;
                if (period) {
                    startExport({ period });
                }
            });
        });
    </script>
</body>
</html>"""

PAYSLIP_AUDIT_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Payslip Audit Upload</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .drop-zone { border: 2px dashed #334155; border-radius: 14px; padding: 2rem; text-align: center; background: #0a0f1b; transition: border-color 0.2s ease, background 0.2s ease; }
        .drop-zone.dragover { border-color: #38bdf8; background: #0b1930; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        ul { margin: 0.5rem 0 0; padding-left: 1.25rem; color: #cbd5e1; }
        .badge { display: inline-block; padding: 0.35rem 0.65rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.9rem; }
        .log { background: #0a0f1b; border: 1px solid #1f2937; border-radius: 10px; padding: 0.75rem; margin-top: 1rem; white-space: pre-wrap; color: #e5e7eb; min-height: 120px; }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>Payslip Audit Upload</h1>
        <p class=\"meta\">Upload the payslip PDF plus all related timesheet screenshots. Drag and drop the files into the window below or use the file picker. The audit will begin automatically once the uploads are validated.</p>
        <div class=\"badge\">Step 1</div>
        <h3>Upload payslip and timesheets</h3>
        <div id=\"drop-zone\" class=\"drop-zone\">
            <p><strong>Drag &amp; drop your payslip PDF and timesheet images here</strong></p>
            <p class=\"meta\">Accepted formats: PDF, JPG, JPEG, PNG. The payslip file is required along with at least one timesheet image.</p>
            <input id=\"file-input\" type=\"file\" multiple accept=\".pdf,.jpg,.jpeg,.png\" style=\"display:none\" />
            <div class=\"actions\">
                <button id=\"pick-btn\" class=\"secondary\">Choose files</button>
                <button id=\"clear-btn\" class=\"secondary\">Clear selection</button>
            </div>
            <ul id=\"file-list\"></ul>
        </div>

        <div class=\"badge\" style=\"margin-top:1.5rem\">Step 2</div>
        <h3>Run audit</h3>
        <p class=\"meta\">When you are ready, start the upload. The report will download automatically after the audit finishes.</p>
        <div class=\"actions\">
            <button id=\"upload-btn\" class=\"primary\">Upload &amp; Start Audit</button>
            <a href=\"/\" class=\"secondary\" style=\"text-decoration:none; display:inline-flex; align-items:center;\">Back to dashboard</a>
        </div>
        <div id=\"status\" class=\"status\">Select your payslip PDF and timesheet screenshots to begin.</div>
        <div id=\"log\" class=\"log\">Awaiting upload...</div>
    </div>

    <script>
        window.PAYSLIP_AUDIT_CONFIG = {
            uploadEndpoint: '/api/payslip-audit/run',
            reportBase: '/api/payslip-audit/report/',
        };
    </script>
    <script src=\"/static/payslip_audit.js\"></script>
</body>
</html>"""




@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE.replace("{asset_version}", ASSET_VERSION)


@app.get("/instrument-specs", response_class=HTMLResponse)
async def instrument_specs_page() -> str:
    return INSTRUMENT_SPECS_TEMPLATE


@app.get("/api/instrument-specs")
async def api_instrument_specs(query: str) -> JSONResponse:
    specs = await _fetch_instrument_specs(query)
    return JSONResponse(specs)


@app.get("/api/instrument-specs.jpg")
async def api_instrument_specs_jpg(query: str) -> Response:
    specs = await _fetch_instrument_specs(query)
    jpg = _render_specs_jpg_bytes(specs)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(specs.get("resolved_symbol") or query or "instrument").strip("_"))
    filename = f"instrument_specs_{safe or 'instrument'}.jpg"
    return Response(
        content=jpg,
        media_type="image/jpeg",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


@app.get("/open-orders", response_class=HTMLResponse)
async def open_orders_page() -> str:
    return OPEN_ORDERS_TEMPLATE


@app.get("/category/{category}", response_class=HTMLResponse)
async def category_page(category: str) -> str:
    safe_category = html.escape(category)
    return CATEGORY_TEMPLATE.replace("{category}", safe_category)


@app.get("/scripts/view/{script_name:path}", response_class=HTMLResponse)
async def script_page(script_name: str) -> str:
    script = script_manager.get(script_name)
    if script.name in STANDALONE_SCRIPTS and script.name not in {"bybit_monitor", "oanda_monitor"}:
        target_url = script_open_url(script)
        fallback_logs = f"/logs/view/{_encoded_script_name(script.name)}"
        if target_url == f"/scripts/view/{_encoded_script_name(script.name)}":
            target_url = fallback_logs
        has_ui = target_url != fallback_logs
        return (
            LAUNCHER_TEMPLATE.replace("{script_name}", html.escape(script.name))
            .replace("{target_url}", target_url)
            .replace("{has_ui}", "true" if has_ui else "false")
        )
    safe_name = html.escape(script.name)
    has_ui = "true" if script.name in WEB_APPS else "false"
    log_url = f"/logs/view/{_encoded_script_name(script.name)}"
    return (
        SCRIPT_PAGE_TEMPLATE.replace("{script_name}", safe_name)
        .replace("{has_ui}", has_ui)
        .replace("{log_url}", log_url)
    )




@app.get("/payslip-audit", response_class=HTMLResponse)
async def payslip_audit_page() -> str:
    return PAYSLIP_AUDIT_TEMPLATE


@app.get("/oanda-history", response_class=HTMLResponse, include_in_schema=False)
async def oanda_history_page() -> HTMLResponse:
    return HTMLResponse(OANDA_HISTORY_TEMPLATE)


@app.get("/bybit-history", response_class=HTMLResponse, include_in_schema=False)
async def bybit_history_page() -> HTMLResponse:
    return HTMLResponse(BYBIT_HISTORY_TEMPLATE)


@app.get("/coinspot-history", response_class=HTMLResponse, include_in_schema=False)
async def coinspot_history_page() -> HTMLResponse:
    return HTMLResponse(COINSPOT_HISTORY_TEMPLATE)


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


@app.get("/scripts/{script_name:path}/status")
async def script_status(script_name: str) -> JSONResponse:
    script = script_manager.get(script_name)
    return JSONResponse(
        {
            "name": script.name,
            "running": script.is_running,
            "port": script.port,
            "last_start_attempt_at": script.last_start_attempt_at,
            "last_start_error": script.last_start_error,
            "last_exit_code": script.last_exit_code,
            "last_exit_reason": script.last_exit_reason,
            "last_spawn_command": script.last_spawn_command,
            "last_spawn_cwd": script.last_spawn_cwd,
            "stdout_tail": script.logs(),
        }
    )


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
                asyncio.create_task(_background_start(script))
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
    if script.name == "cryptocalculator-clone" and resp.status_code >= 500:
        PROXY_LOGGER.warning(
            "Proxy upstream error script=%s subpath=%s port=%s status=%s",
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
    normalized = _set_watchlist(items)
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
        await script.start()
    except Exception as exc:  # pragma: no cover - runtime protection
        # Capture failures in the per-script log instead of surfacing them to the caller.
        script.add_log(f"Failed to start: {exc}")


@app.post("/scripts/{script_name:path}/start")
async def start_script(script_name: str) -> JSONResponse:
    # Never launch the payslip audit script directly; force users to the upload flow
    # so the required files can be provided first.
    if script_name == "payslip_audit":
        return JSONResponse(
            {
                "redirect": "/payslip-audit",
                "detail": "Upload your payslip PDF and timesheets to begin the audit.",
            }
        )

    script = script_manager.get(script_name)

    if script.is_running:
        return JSONResponse({"status": "already_running", **script.to_summary()})

    if script.name in WEB_APPS and script.port is None:
        script.port = _allocate_port()

    asyncio.create_task(_background_start(script))

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


@app.post("/api/payslip-audit/run")
async def upload_and_run_payslip_audit(files: List[UploadFile] = File(...)) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Please upload a payslip PDF and at least one timesheet image.")

    ensure_tesseract_available()

    session_id = uuid4().hex
    session_dir = _payslip_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    saved_files: List[Path] = []
    for upload in files:
        filename = Path(upload.filename or "upload").name
        destination = session_dir / filename
        destination.write_bytes(await upload.read())
        saved_files.append(destination)

    payslips = [path for path in saved_files if path.suffix.lower() == ".pdf"]
    timesheets = [path for path in saved_files if path.suffix.lower() in PAYSLIP_ALLOWED_IMAGES]

    if not payslips:
        raise HTTPException(status_code=400, detail="A payslip PDF is required.")
    if not timesheets:
        raise HTTPException(status_code=400, detail="At least one timesheet image (JPG/PNG) is required.")

    output_path = session_dir / PAYSLIP_REPORT_NAME
    log_output = await _execute_payslip_audit(payslips[0], sorted(timesheets), output_path)

    return JSONResponse(
        {
            "session_id": session_id,
            "download_url": f"/api/payslip-audit/report/{session_id}",
            "log": log_output,
        }
    )


@app.get("/api/payslip-audit/report/{session_id}")
async def download_payslip_report(session_id: str) -> FileResponse:
    report_path = _payslip_session_dir(session_id) / PAYSLIP_REPORT_NAME
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found. Please rerun the audit.")
    return FileResponse(report_path, filename=PAYSLIP_REPORT_NAME, media_type="application/pdf")


@app.post("/api/oanda-history/export")
async def start_oanda_history_export(request: Request) -> JSONResponse:
    if oanda_history_exporter is None:
        raise HTTPException(status_code=500, detail="OANDA history exporter not available.")
    payload = await request.json()

    account = str(payload.get("account") or "demo").strip().lower()
    if account not in {"live", "demo"}:
        account = "live"
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
    if job.status == "done" and job.output_path is not None:
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

    account = str(payload.get("account") or "demo").strip().lower()
    if account not in {"live", "demo"}:
        account = "live"
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
    payload_bytes = await request.body()
    payload_text = payload_bytes.decode("utf-8", errors="replace")
    script = script_manager.get(script_name)
    script.add_log(f"Webhook received: {payload_text}")
    request_id = uuid4().hex
    _log_webhook_event(
        request_id,
        "webhook_received",
        {"script_name": script_name, "path": "/webhook/{script_name}"},
    )

    if script.name == "payslip_audit":
        script.add_log("Webhook ignored: upload flow required via /payslip-audit")
        return JSONResponse(
            {
                "status": "payslip_audit requires upload flow",
                "redirect": "/payslip-audit",
            }
        )

    if script.name not in {"cryptocalculator-clone", "oanda-calculator-clone"}:
        return JSONResponse({"status": "ok", "script": script_name})

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        script.add_log(f"Webhook payload invalid JSON: {exc}")
        raise HTTPException(status_code=400, detail="Webhook payload must be JSON.") from exc

    try:
        if script.name == "cryptocalculator-clone":
            result = await _place_bybit_order(payload, request_id=request_id)
        else:
            result = await _place_oanda_order(payload, request_id=request_id)
        script.add_log(f"Order request sent: {result}")
        await _send_telegram_alert(_format_trade_alert(payload, result=result))
        return JSONResponse(
            {"status": "ok", "script": script_name, "request_id": request_id, "order": result}
        )
    except Exception as exc:
        script.add_log(f"Order placement failed: {exc}")
        await _send_telegram_alert(
            _format_trade_alert(payload, error=str(exc))
        )
        BYBIT_LOGGER.exception(
            "WEBHOOK_TPSL %s webhook_failed script=%s error=%s",
            request_id,
            script_name,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/execute_now")
async def execute_now(request: Request) -> JSONResponse:
    script_name = "cryptocalculator-clone"
    script = script_manager.get(script_name)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object.")
    request_id = uuid4().hex
    _log_webhook_event(
        request_id,
        "execute_now_received",
        {"script_name": script_name, "path": "/execute_now"},
    )
    try:
        result = await _place_bybit_order(payload, request_id=request_id)
        script.add_log(f"Execute-now order sent: {result}")
        await _send_telegram_alert(_format_trade_alert(payload, result=result))
        return JSONResponse({"status": "ok", "request_id": request_id, "order": result})
    except Exception as exc:
        script.add_log(f"Execute-now order failed: {exc}")
        await _send_telegram_alert(
            _format_trade_alert(payload, error=str(exc))
        )
        BYBIT_LOGGER.exception(
            "WEBHOOK_TPSL %s execute_now_failed script=%s error=%s",
            request_id,
            script_name,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook")
async def default_webhook(request: Request) -> JSONResponse:
    payload_bytes = await request.body()
    payload_text = payload_bytes.decode("utf-8", errors="replace")
    request_id = uuid4().hex
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        script_name = "cryptocalculator-clone"
        script = script_manager.get(script_name)
        script.add_log(f"Webhook received: {payload_text}")
        script.add_log(f"Webhook payload invalid JSON: {exc}")
        _log_webhook_event(
            request_id,
            "webhook_received",
            {"script_name": script_name, "path": "/webhook"},
        )
        raise HTTPException(status_code=400, detail="Webhook payload must be JSON.") from exc

    script_name = str(
        payload.get("script_name") or payload.get("target_app") or "cryptocalculator-clone"
    )
    script = script_manager.get(script_name)
    script.add_log(f"Webhook received: {payload_text}")
    _log_webhook_event(
        request_id,
        "webhook_received",
        {"script_name": script_name, "path": "/webhook"},
    )

    try:
        if script.name == "cryptocalculator-clone":
            result = await _place_bybit_order(payload, request_id=request_id)
        elif script.name == "oanda-calculator-clone":
            result = await _place_oanda_order(payload, request_id=request_id)
        else:
            return JSONResponse({"status": "ok", "script": script_name})
        script.add_log(f"Order request sent: {result}")
        await _send_telegram_alert(_format_trade_alert(payload, result=result))
        return JSONResponse(
            {"status": "ok", "script": script_name, "request_id": request_id, "order": result}
        )
    except Exception as exc:
        script.add_log(f"Order placement failed: {exc}")
        await _send_telegram_alert(
            _format_trade_alert(payload, error=str(exc))
        )
        BYBIT_LOGGER.exception(
            "WEBHOOK_TPSL %s webhook_failed script=%s error=%s",
            request_id,
            script_name,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
async def healthcheck() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/debug/tesseract")
def debug_tesseract() -> JSONResponse:
    import shutil

    return JSONResponse(
        {
            "PATH": os.environ.get("PATH"),
            "which_tesseract": shutil.which("tesseract"),
            "resolved": _resolve_tesseract_binary(),
            "available": is_tesseract_available(),
        }
    )


@app.get("/favicon.ico")
async def favicon() -> Response:
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6XvZl8AAAAASUVORK5CYII="
    )
    return Response(content=png_bytes, media_type="image/png")


app.mount("/static", StaticFiles(directory=BASE_DIR / "render" / "static"), name="static")
