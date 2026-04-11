"""
bybit_trigger_bounce_trader.py

Server-side triggering (Bybit conditional orders) for EMA/VWAP “touch/bounce” entries.

Core idea:
- You compute the current EMA/VWAP level server-side
- You place/keep a CONDITIONAL MARKET order on Bybit with triggerPrice = that level
- Bybit triggers the entry server-side when price reaches triggerPrice

Bybit V5 reference:
- POST /v5/order/create with triggerPrice => becomes a conditional order (server-side trigger)
- triggerDirection: 1=rises to triggerPrice, 2=falls to triggerPrice
Docs: https://bybit-exchange.github.io/docs/v5/order/create-order

Install:
  pip install requests

Run (example):
  set BYBIT_API_KEY1=...
  set BYBIT_API_SECRET1=...
  set BYBIT_ENV=live
  python bybit_trigger_bounce_trader.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from bybit_demo_tpsl_cache import cache_bybit_demo_tpsl_request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from shared.symbol_resolution import resolve_bybit_symbol_from_choices

# Reuse your repo credential resolver if present.
# If you run this inside CODEX, keep this import.
try:
    from bybit_credentials import resolve_bybit_credentials
except Exception:
    resolve_bybit_credentials = None


# -----------------------------
# Config (ENV)
# -----------------------------

BYBIT_RECV_WINDOW = os.getenv("BYBIT_RECV_WINDOW", "5000")
CATEGORY = os.getenv("BYBIT_CATEGORY", "linear").strip().lower()  # linear/inverse/spot/option
TRIGGER_BY = os.getenv("BYBIT_TRIGGER_BY", "LastPrice")  # LastPrice / MarkPrice / IndexPrice (linear/inverse)
INTERVAL = os.getenv("BYBIT_KLINE_INTERVAL", "1")  # "1","3","5","15","30","60","240","D", etc.
POLL_SECONDS = float(os.getenv("BOUNCE_POLL_SECONDS", "2.0"))
KLINE_LIMIT = int(os.getenv("BOUNCE_KLINE_LIMIT", "1000"))  # per-page limit; Bybit allows up to 1000

# Symbols:
# Example: "BTCUSDT,ETHUSDT,SOLUSDT"
SYMBOLS = [s.strip().upper() for s in (os.getenv("BOUNCE_SYMBOLS", "BTCUSDT").split(",")) if s.strip()]

# Strategies to arm (comma-separated):
#  - ema9_long   : enter long when price FALLS to EMA(9) from above
#  - ema9_short  : enter short when price RISES to EMA(9) from below
#  - vwap_long   : enter long when price FALLS to VWAP from above
#  - vwap_short  : enter short when price RISES to VWAP from below
STRATEGIES = [s.strip().lower() for s in (os.getenv("BOUNCE_STRATEGIES", "ema9_long,vwap_long").split(",")) if s.strip()]
BOUNCE_SIDE_RAW = (os.getenv("BOUNCE_SIDE", "Buy") or "Buy").strip().title()
BOUNCE_SIDE = BOUNCE_SIDE_RAW if BOUNCE_SIDE_RAW in {"Buy", "Sell"} else "Buy"
SESSION_ID = (os.getenv("BOUNCE_SESSION_ID", "") or "").strip()

EMA_LEN = int(os.getenv("EMA_LEN", "9"))
# VWAP anchor (UTC): session = UTC day, week = UTC week (Mon 00:00)
VWAP_ANCHOR = (os.getenv("BOUNCE_VWAP_ANCHOR", "session") or "session").strip().lower()

# Per-symbol qty (contracts) map as JSON, fallback default qty:
# Example:
#   BOUNCE_QTY_MAP={"BTCUSDT":"0.01","ETHUSDT":"0.1"}
QTY_MAP_RAW = os.getenv("BOUNCE_QTY_MAP", "{}").strip()
try:
    QTY_MAP = {k.upper(): str(v) for k, v in json.loads(QTY_MAP_RAW).items()}
except Exception:
    QTY_MAP = {}
DEFAULT_QTY = os.getenv("BOUNCE_DEFAULT_QTY", "0.001")

SL_TICKS = int(os.getenv("BOUNCE_SL_TICKS", "0"))  # 0 disables
_RR_RAW = (os.getenv("BOUNCE_RR_RATIO", "") or "").strip()
if _RR_RAW:
    try:
        RR_RATIO = float(_RR_RAW)
    except Exception:
        RR_RATIO = 0.0
else:
    # Legacy fallback: derive RR from fixed TP ticks if present.
    try:
        _tp_legacy = int(os.getenv("BOUNCE_TP_TICKS", "0"))
        RR_RATIO = (_tp_legacy / SL_TICKS) if (SL_TICKS > 0 and _tp_legacy > 0) else 0.0
    except Exception:
        RR_RATIO = 0.0

# Sizing
RISK_MODE = (os.getenv("BOUNCE_RISK_MODE", "fixed_qty") or "fixed_qty").strip().lower()  # fixed_qty|percent
RISK_PCT = float(os.getenv("BOUNCE_RISK_PCT", "0") or 0)
ACCOUNT_BALANCE_RAW = (os.getenv("BOUNCE_ACCOUNT_BALANCE", "auto") or "auto").strip().lower()
ACCOUNT_TYPE = (os.getenv("BOUNCE_ACCOUNT_TYPE", "UNIFIED") or "UNIFIED").strip()
ACCOUNT_ASSET = (os.getenv("BOUNCE_ACCOUNT_ASSET", "USDT") or "USDT").strip().upper()

# Position mode selector for /v5/position/trading-stop
# 0=one-way, 1=hedge buy, 2=hedge sell
POSITION_IDX = int(os.getenv("BOUNCE_POSITION_IDX", "0") or 0)

# Only amend the conditional order if trigger price changed by >= this many ticks
MIN_AMEND_TICKS = int(os.getenv("BOUNCE_MIN_AMEND_TICKS", "1"))

# Safety: do not arm if indicator too close to price (avoid immediate trigger)
MIN_GAP_TICKS = int(os.getenv("BOUNCE_MIN_GAP_TICKS", "2"))


# -----------------------------
# HTTP / Signing
# -----------------------------

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})


def _resolve_creds() -> Tuple[str, str, str, str]:
    """
    Returns (mode, api_key, api_secret, base_url)
    Uses CODEX bybit_credentials.py if available.
    """
    if resolve_bybit_credentials:
        mode, key, secret, base_url, _key_src = resolve_bybit_credentials()
        return mode, key, secret, base_url.rstrip("/")
    # Fallback envs
    mode = os.getenv("BYBIT_ENV", "live").strip().lower()
    key = os.getenv("BYBIT_API_KEY1") or os.getenv("BYBIT_API_KEY") or ""
    secret = os.getenv("BYBIT_API_SECRET1") or os.getenv("BYBIT_API_SECRET") or ""
    base_url = os.getenv("BYBIT_BASE_URL") or os.getenv("BYBIT_API_BASE") or "https://api.bybit.com"
    return mode, key, secret, base_url.rstrip("/")


MODE, API_KEY, API_SECRET, BASE_URL = _resolve_creds()

if not API_KEY or not API_SECRET:
    raise SystemExit("Missing Bybit credentials (BYBIT_API_KEY1/BYBIT_API_SECRET1 or legacy vars).")


def _sign_v5(timestamp_ms: str, api_key: str, api_secret: str, body_or_query: str) -> str:
    """
    Bybit V5 SIGN-TYPE=2:
      sign = HMAC_SHA256(secret, timestamp + api_key + recvWindow + body/query)
    """
    payload = f"{timestamp_ms}{api_key}{BYBIT_RECV_WINDOW}{body_or_query}"
    return hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _public_get(path: str, params: Optional[dict] = None, timeout: float = 10.0) -> dict:
    url = f"{BASE_URL}{path}"
    r = session.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") not in (0, "0", None):
        raise RuntimeError(f"Bybit error {j.get('retCode')}: {j.get('retMsg')}")
    return j


def _signed_post(path: str, body: dict, timeout: float = 10.0) -> dict:
    url = f"{BASE_URL}{path}"
    body_json = json.dumps(body, separators=(",", ":"))
    ts = str(int(time.time() * 1000))
    sig = _sign_v5(ts, API_KEY, API_SECRET, body_json)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json",
    }
    r = session.post(url, headers=headers, data=body_json, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") not in (0, "0", None):
        raise RuntimeError(f"Bybit error {j.get('retCode')}: {j.get('retMsg')}")
    return j


# -----------------------------
# Market data helpers
# -----------------------------

@dataclass
class InstrumentFilters:
    tick_size: float
    qty_step: float
    min_qty: float


_instrument_cache: Dict[str, InstrumentFilters] = {}
_symbol_catalog_cache: Dict[str, Dict[str, object]] = {
    "linear": {"ts": 0.0, "symbols": []},
    "spot": {"ts": 0.0, "symbols": []},
    "inverse": {"ts": 0.0, "symbols": []},
}
_symbol_catalog_ttl_seconds = float(os.getenv("BYBIT_SYMBOL_CACHE_TTL_SECONDS", "900"))


def _fetch_symbols_for_category(category: str) -> List[str]:
    symbols: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        params: Dict[str, object] = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = _public_get("/v5/market/instruments-info", params=params, timeout=10)
        rows = (payload.get("result") or {}).get("list") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if symbol:
                    symbols.append(symbol)
        cursor = (payload.get("result") or {}).get("nextPageCursor")
        if not cursor:
            break
    return sorted(set(symbols))


def _get_symbols_for_category_cached(category: str) -> List[str]:
    category_key = category if category in {"linear", "spot", "inverse"} else "linear"
    now = time.time()
    entry = _symbol_catalog_cache.get(category_key) or {"ts": 0.0, "symbols": []}
    cached = entry.get("symbols")
    ts = float(entry.get("ts") or 0.0)
    if isinstance(cached, list) and cached and (now - ts) <= _symbol_catalog_ttl_seconds:
        return list(cached)
    symbols = _fetch_symbols_for_category(category_key)
    _symbol_catalog_cache[category_key] = {"ts": now, "symbols": symbols}
    return symbols


def _normalize_runtime_symbols(raw_symbols: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    choices = _get_symbols_for_category_cached(CATEGORY)
    for raw in raw_symbols:
        resolved = resolve_bybit_symbol_from_choices(
            raw,
            choices,
            preferred_quotes=("USDT", "USDC", "USD"),
            exact_first=True,
        )
        symbol = str((resolved or {}).get("resolved_symbol") or "").strip().upper()
        if not symbol:
            raise SystemExit(f"Unable to resolve Bybit symbol '{raw}' for category '{CATEGORY}'.")
        if symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _get_instrument_filters(symbol: str) -> InstrumentFilters:
    if symbol in _instrument_cache:
        return _instrument_cache[symbol]

    # /v5/market/instruments-info
    j = _public_get(
        "/v5/market/instruments-info",
        params={"category": CATEGORY, "symbol": symbol},
        timeout=10,
    )
    lst = ((j.get("result") or {}).get("list") or [])
    if not lst:
        raise RuntimeError(f"No instruments-info for {CATEGORY}:{symbol}")

    info = lst[0]
    price_filter = info.get("priceFilter") or {}
    lot_filter = info.get("lotSizeFilter") or {}

    tick = float(price_filter.get("tickSize") or 0)
    step = float(lot_filter.get("qtyStep") or 0)
    min_qty = float(lot_filter.get("minTrdQty") or lot_filter.get("minOrderQty") or 0)

    if tick <= 0 or step <= 0:
        raise RuntimeError(f"Bad filters for {symbol}: tick={tick}, step={step}")
    if min_qty <= 0:
        # Some categories use a different field name; keep a sane fallback.
        min_qty = step

    f = InstrumentFilters(tick_size=tick, qty_step=step, min_qty=min_qty)
    _instrument_cache[symbol] = f
    return f


def _get_last_price(symbol: str) -> float:
    j = _public_get("/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol}, timeout=10)
    lst = ((j.get("result") or {}).get("list") or [])
    if not lst:
        raise RuntimeError(f"No tickers for {CATEGORY}:{symbol}")
    last = float(lst[0].get("lastPrice") or 0.0)
    if last <= 0:
        raise RuntimeError(f"Bad lastPrice for {symbol}")
    return last


def _parse_candles(raw: list) -> List[dict]:
    out: List[dict] = []
    for c in raw:
        if not isinstance(c, list) or len(c) < 6:
            continue
        try:
            out.append(
                {
                    "ts": int(c[0]),
                    "o": float(c[1]),
                    "h": float(c[2]),
                    "l": float(c[3]),
                    "c": float(c[4]),
                    "v": float(c[5]),
                }
            )
        except Exception:
            continue
    return out


def _get_klines(symbol: str, *, end_ms: Optional[int] = None, limit: int = KLINE_LIMIT) -> List[dict]:
    """Fetch up to *limit* klines ending at *end_ms* (ms). Bybit returns reverse-sorted."""
    params = {
        "category": CATEGORY,
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": str(limit),
    }
    if end_ms is not None:
        params["end"] = str(end_ms)
    j = _public_get("/v5/market/kline", params=params, timeout=10)
    candles = ((j.get("result") or {}).get("list") or [])
    out = _parse_candles(candles)
    out.sort(key=lambda x: x["ts"])
    return out


def _anchor_start_ms(anchor: str, now_ms: int) -> int:
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    if anchor == "week":
        # ISO week start (Mon 00:00 UTC)
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        delta_days = day_start.isoweekday() - 1
        week_start = day_start - timedelta(days=delta_days)
        return int(week_start.timestamp() * 1000)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(day_start.timestamp() * 1000)


def _get_klines_since_anchor(symbol: str, anchor: str) -> List[dict]:
    """Fetch klines back until we cover the anchor start (UTC)."""
    now_ms = int(time.time() * 1000)
    start_ms = _anchor_start_ms(anchor, now_ms)

    merged: Dict[int, dict] = {}
    end_ms: Optional[int] = None

    # In the worst case (1m VWAP week) we may need ~11 pages. Keep some headroom.
    for _ in range(20):
        batch = _get_klines(symbol, end_ms=end_ms, limit=KLINE_LIMIT)
        if not batch:
            break
        for c in batch:
            merged[c["ts"]] = c
        earliest = min(c["ts"] for c in batch)
        if earliest <= start_ms:
            break
        end_ms = earliest - 1
        if end_ms <= 0:
            break

    out = [c for ts, c in merged.items() if ts >= start_ms]
    out.sort(key=lambda x: x["ts"])
    return out


# -----------------------------
# Indicator math
# -----------------------------

def _ema(values: List[float], length: int) -> Optional[float]:
    if length <= 0 or len(values) < length:
        return None
    alpha = 2.0 / (length + 1.0)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e
    return float(e)


def _vwap(candles: List[dict]) -> Optional[float]:
    if not candles:
        return None
    window = candles
    pv = 0.0
    vv = 0.0
    for c in window:
        # typical price (H+L+C)/3
        tp = (c["h"] + c["l"] + c["c"]) / 3.0
        vol = max(float(c["v"]), 0.0)
        pv += tp * vol
        vv += vol
    if vv <= 0:
        return None
    return pv / vv


# -----------------------------
# Rounding helpers
# -----------------------------

def _floor_to_tick(price: float, tick: float) -> float:
    return math.floor(price / tick) * tick


def _ceil_to_tick(price: float, tick: float) -> float:
    return math.ceil(price / tick) * tick


def _round_qty_step(qty: float, step: float) -> float:
    # round down to step
    return math.floor(qty / step) * step


# -----------------------------
# Trading helpers
# -----------------------------

def _make_order_link_id(symbol: str, strategy: str) -> str:
    # Must be <= 36 chars
    session_token = "".join(ch for ch in SESSION_ID.upper() if ch.isalnum())[:6] or "GEN"
    raw = f"BB{session_token}_{symbol}_{strategy}_{INTERVAL}"
    return raw[:36]


def _place_or_amend_conditional_market(
    *,
    symbol: str,
    side: str,  # "Buy" / "Sell"
    qty: str,
    trigger_price: float,
    trigger_direction: int,  # 1 rise-to, 2 fall-to
    tp: Optional[float],
    sl: Optional[float],
    order_link_id: str,
) -> None:
    """
    Tries amend first (cheap, keeps same orderLinkId).
    If amend fails (order missing), create it.
    """
    filters = _get_instrument_filters(symbol)
    tick = filters.tick_size

    # Bybit requires triggerPrice relationship vs market expectation:
    # - rising trigger => triggerPrice should be above current market
    # - falling trigger => triggerPrice should be below current market
    # (we enforce this outside too, but keep it strict here)
    last = _get_last_price(symbol)

    if trigger_direction == 1 and trigger_price <= last:
        return
    if trigger_direction == 2 and trigger_price >= last:
        return

    body_amend = {
        "category": CATEGORY,
        "symbol": symbol,
        "orderLinkId": order_link_id,
        "triggerPrice": f"{trigger_price:.12f}".rstrip("0").rstrip("."),
    }

    try:
        amend_resp = _signed_post("/v5/order/amend", body_amend, timeout=10)
        amend_result = amend_resp.get("result") if isinstance(amend_resp, dict) else {}
        cache_bybit_demo_tpsl_request(
            order_id=str((amend_result or {}).get("orderId") or ""),
            order_link_id=order_link_id,
            parent_order_link_id=None,
            symbol=symbol,
            side=side,
            take_profit=tp,
            stop_loss=sl,
            source="bounce_conditional_amend",
        )
        print(f"[AMEND] {symbol} {order_link_id} trigger={trigger_price}")
        return
    except Exception:
        # Create new if amend failed (likely unknown orderLinkId)
        pass

    body_create = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": qty,
        "triggerPrice": f"{trigger_price:.12f}".rstrip("0").rstrip("."),
        "triggerDirection": trigger_direction,
        "triggerBy": TRIGGER_BY,
        "orderLinkId": order_link_id,
    }

    # Optional TP/SL on entry
    if tp is not None:
        body_create["takeProfit"] = f"{tp:.12f}".rstrip("0").rstrip(".")
    if sl is not None:
        body_create["stopLoss"] = f"{sl:.12f}".rstrip("0").rstrip(".")

    create_resp = _signed_post("/v5/order/create", body_create, timeout=10)
    create_result = create_resp.get("result") if isinstance(create_resp, dict) else {}
    cache_bybit_demo_tpsl_request(
        order_id=str((create_result or {}).get("orderId") or ""),
        order_link_id=order_link_id,
        parent_order_link_id=None,
        symbol=symbol,
        side=side,
        take_profit=tp,
        stop_loss=sl,
        source="bounce_conditional_create",
    )
    _last_order_link_by_symbol[symbol] = order_link_id
    print(f"[CREATE] {symbol} {order_link_id} side={side} trigger={trigger_price} qty={qty}")


def _compute_tp_sl(entry: float, tick: float, side: str) -> Tuple[Optional[float], Optional[float]]:
    """Compute SL in ticks and TP from net RR (fee-aware)."""
    tp: Optional[float] = None
    sl: Optional[float] = None

    if SL_TICKS > 0:
        if side == "Buy":
            sl = _floor_to_tick(entry - (SL_TICKS * tick), tick)
        else:
            sl = _ceil_to_tick(entry + (SL_TICKS * tick), tick)

    if RR_RATIO <= 0 or SL_TICKS <= 0:
        return tp, sl

    fee_rate = _fee_rate_for_category(CATEGORY)
    if fee_rate >= 1:
        return tp, sl

    stop_distance = SL_TICKS * tick
    stop_price = entry - stop_distance if side == "Buy" else entry + stop_distance
    # Per-unit net loss at stop includes fees on entry+exit.
    net_per_unit_loss = stop_distance + fee_rate * (entry + stop_price)
    if net_per_unit_loss <= 0:
        return tp, sl

    min_profit = net_per_unit_loss * RR_RATIO
    # Required favorable move per unit to realize min_profit after both-leg fees.
    diff_required = (min_profit + (2 * entry * fee_rate)) / (1 - fee_rate)
    ticks = math.ceil(diff_required / tick)

    if side == "Buy":
        tp = _ceil_to_tick(entry + (ticks * tick), tick)
    else:
        tp = _floor_to_tick(entry - (ticks * tick), tick)

    return tp, sl


def _fee_rate_for_category(category: str) -> float:
    return 0.001 if category == "spot" else 0.0006


def _signed_get(path: str, params: dict, timeout: float = 10.0) -> dict:
    from urllib.parse import urlencode

    query = urlencode(params)
    ts = str(int(time.time() * 1000))
    sig = _sign_v5(ts, API_KEY, API_SECRET, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{BASE_URL}{path}?{query}"
    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") not in (0, "0", None):
        raise RuntimeError(f"Bybit error {j.get('retCode')}: {j.get('retMsg')}")
    return j


_balance_cache: Tuple[float, float] = (0.0, 0.0)
_last_order_link_by_symbol: Dict[str, str] = {}
_constraint_failure: Optional[str] = None


def _get_account_balance() -> float:
    global _balance_cache
    now = time.time()
    cached_ts, cached_bal = _balance_cache
    if cached_ts and (now - cached_ts) < 15:
        return cached_bal

    if ACCOUNT_BALANCE_RAW != "auto":
        bal = float(ACCOUNT_BALANCE_RAW)
        _balance_cache = (now, bal)
        return bal

    payload = _signed_get(
        "/v5/account/wallet-balance",
        params={"accountType": ACCOUNT_TYPE, "coin": ACCOUNT_ASSET},
        timeout=10,
    )
    lst = ((payload.get("result") or {}).get("list") or [])
    for item in lst:
        for c in item.get("coin", []) or []:
            if (c.get("coin") or "").upper() == ACCOUNT_ASSET:
                raw = c.get("walletBalance") or c.get("equity") or c.get("availableToWithdraw") or "0"
                bal = float(raw)
                _balance_cache = (now, bal)
                return bal
    raise RuntimeError(f"Wallet balance missing for {ACCOUNT_ASSET} ({ACCOUNT_TYPE}).")


def _risk_qty(
    *,
    entry: float,
    side: str,
    tick: float,
    qty_step: float,
    min_qty: float,
    fee_rate: float,
) -> Optional[float]:
    if RISK_MODE != "percent" or RISK_PCT <= 0 or SL_TICKS <= 0:
        return None

    balance = _get_account_balance()
    risk_amount = balance * (RISK_PCT / 100.0)

    stop_distance = SL_TICKS * tick
    stop_price = entry - stop_distance if side == "Buy" else entry + stop_distance

    net_per_unit_loss = stop_distance + fee_rate * (entry + stop_price)
    if net_per_unit_loss <= 0:
        return None

    raw_qty = risk_amount / net_per_unit_loss
    steps = max(1, math.ceil(raw_qty / qty_step))
    qty = steps * qty_step
    if qty < min_qty:
        return None
    return qty


def _evaluate_constraints(
    *,
    balance: float,
    qty: float,
    entry: float,
    tp: Optional[float],
    sl: Optional[float],
    side: str,
    fee_rate: float,
) -> Tuple[Optional[float], Optional[float]]:
    if balance <= 0 or qty <= 0 or sl is None or tp is None:
        return None, None
    stop_move = abs(entry - sl)
    tp_move = abs(tp - entry)
    stop_net_per_unit = stop_move + fee_rate * (entry + sl)
    tp_net_per_unit = tp_move - fee_rate * (entry + tp)
    risk_pct = (qty * stop_net_per_unit / balance) * 100 if stop_net_per_unit > 0 else None
    reward_pct = (qty * tp_net_per_unit / balance) * 100 if tp_net_per_unit > 0 else None
    return risk_pct, reward_pct


def _position_entry_price(pos: dict) -> float:
    for key in ("avgPrice", "avgEntryPrice", "entryPrice", "sessionAvgPrice"):
        raw = pos.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return 0.0


def _get_open_position(symbol: str) -> Optional[dict]:
    """Return the first non-zero position payload for symbol (or None)."""
    try:
        payload = _signed_get(
            "/v5/position/list",
            params={"category": CATEGORY, "symbol": symbol},
            timeout=10,
        )
    except Exception:
        return None

    lst = ((payload.get("result") or {}).get("list") or [])
    for pos in lst:
        if (pos.get("symbol") or "").upper() != symbol.upper():
            continue
        raw_size = (
            pos.get("size")
            or pos.get("qty")
            or pos.get("positionQty")
            or pos.get("positionSize")
            or "0"
        )
        try:
            size = float(raw_size)
        except Exception:
            size = 0.0
        if abs(size) > 0:
            return pos
    return None


def _has_open_position(symbol: str) -> bool:
    return _get_open_position(symbol) is not None


def _set_trading_stop(*, symbol: str, tp: Optional[float], sl: Optional[float]) -> None:
    body: dict = {
        "category": CATEGORY,
        "symbol": symbol,
        "tpslMode": "Full",
        "positionIdx": POSITION_IDX,
        "tpTriggerBy": TRIGGER_BY,
        "slTriggerBy": TRIGGER_BY,
    }
    if tp is not None:
        body["takeProfit"] = f"{tp:.12f}".rstrip("0").rstrip(".")
    if sl is not None:
        body["stopLoss"] = f"{sl:.12f}".rstrip("0").rstrip(".")
    _signed_post("/v5/position/trading-stop", body, timeout=10)


def _apply_trading_stop_from_fill(*, symbol: str, tick: float) -> bool:
    global _constraint_failure
    if SL_TICKS <= 0 and RR_RATIO <= 0:
        return True
    if CATEGORY not in {"linear", "inverse"}:
        return True

    pos: Optional[dict] = None
    entry = 0.0
    for _ in range(10):
        pos = _get_open_position(symbol)
        if not pos:
            return False
        entry = _position_entry_price(pos)
        if entry > 0:
            break
        time.sleep(0.4)

    if not pos or entry <= 0:
        print(f"[WARN] {symbol} position detected but avg entry price unavailable; TP/SL not adjusted.")
        return False

    side = (pos.get("side") or "").strip().title()
    if side not in {"Buy", "Sell"}:
        raw_size = pos.get("size") or pos.get("qty") or "0"
        try:
            size = float(raw_size)
        except Exception:
            size = 0.0
        side = "Buy" if size >= 0 else "Sell"

    tp, sl = _compute_tp_sl(entry, tick, side)
    if tp is None and sl is None:
        return True

    qty_raw = pos.get("size") or pos.get("qty") or "0"
    try:
        qty = abs(float(qty_raw))
    except Exception:
        qty = 0.0

    fee_rate = _fee_rate_for_category(CATEGORY)
    balance = _get_account_balance() if (RISK_MODE == "percent" and RISK_PCT > 0) else 0.0
    risk_pct, reward_pct = _evaluate_constraints(
        balance=balance,
        qty=qty,
        entry=entry,
        tp=tp,
        sl=sl,
        side=side,
        fee_rate=fee_rate,
    )
    min_reward_pct = RISK_PCT * RR_RATIO
    if (
        RISK_MODE == "percent"
        and RISK_PCT > 0
        and RR_RATIO > 0
        and (risk_pct is None or reward_pct is None or risk_pct < RISK_PCT or reward_pct < min_reward_pct)
    ):
        _constraint_failure = (
            f"failed-to-meet-constraints symbol={symbol} risk_pct={risk_pct} reward_pct={reward_pct} "
            f"required_risk_pct>={RISK_PCT} required_reward_pct>={min_reward_pct}"
        )
        print(f"[CONSTRAINT_FAIL] {_constraint_failure}")
        return False

    _set_trading_stop(symbol=symbol, tp=tp, sl=sl)
    cache_bybit_demo_tpsl_request(
        order_id="",
        order_link_id=_last_order_link_by_symbol.get(symbol, ""),
        parent_order_link_id=None,
        symbol=symbol,
        side=side,
        take_profit=tp,
        stop_loss=sl,
        source="bounce_fill_recomputed",
    )
    print(f"[TP/SL] {symbol} set from fill entry={entry} tp={tp} sl={sl} (tick={tick})")
    return True


# Track last trigger price we armed per (symbol,strategy) to avoid spam amends
_last_armed_trigger: Dict[str, float] = {}


def _should_amend(key: str, new_trigger: float, tick: float) -> bool:
    prev = _last_armed_trigger.get(key)
    if prev is None:
        return True
    return abs(new_trigger - prev) >= (tick * MIN_AMEND_TICKS)


def _gap_ok(last: float, trigger: float, tick: float, direction: int) -> bool:
    # avoid immediate trigger by requiring at least MIN_GAP_TICKS distance
    gap = abs(last - trigger)
    if gap < tick * MIN_GAP_TICKS:
        return False
    # enforce correct side by direction:
    # 1: price must rise to trigger => trigger > last
    # 2: price must fall to trigger => trigger < last
    if direction == 1 and trigger <= last:
        return False
    if direction == 2 and trigger >= last:
        return False
    return True


# -----------------------------
# Main loop
# -----------------------------

def _desired_trigger_for_strategy(symbol: str, strategy: str) -> Optional[Tuple[str, int, float]]:
    """
    Returns (side, triggerDirection, indicator_price_raw)
    """
    strategy = (strategy or "").strip().lower()

    if strategy in {"ema", "ema_long", "ema9_long"}:
        candles = _get_klines(symbol)
        if len(candles) < EMA_LEN + 2:
            return None
        closes = [c["c"] for c in candles]
        ema_val = _ema(closes, EMA_LEN)
        if ema_val is None:
            return None
        if strategy in {"ema_long", "ema9_long"}:
            return ("Buy", 2, ema_val)
        return (BOUNCE_SIDE, 2 if BOUNCE_SIDE == "Buy" else 1, ema_val)

    if strategy in {"ema_short", "ema9_short"}:
        candles = _get_klines(symbol)
        if len(candles) < EMA_LEN + 2:
            return None
        closes = [c["c"] for c in candles]
        ema_val = _ema(closes, EMA_LEN)
        if ema_val is None:
            return None
        return ("Sell", 1, ema_val)

    if strategy in {"vwap", "vwap_long"}:
        candles = _get_klines_since_anchor(symbol, VWAP_ANCHOR)
        if len(candles) < 2:
            return None
        vwap_val = _vwap(candles)
        if vwap_val is None:
            return None
        if strategy == "vwap_long":
            return ("Buy", 2, vwap_val)
        return (BOUNCE_SIDE, 2 if BOUNCE_SIDE == "Buy" else 1, vwap_val)

    if strategy == "vwap_short":
        candles = _get_klines_since_anchor(symbol, VWAP_ANCHOR)
        if len(candles) < 2:
            return None
        vwap_val = _vwap(candles)
        if vwap_val is None:
            return None
        return ("Sell", 1, vwap_val)

    return None


SYMBOLS = _normalize_runtime_symbols(SYMBOLS)


def main() -> None:
    print(f"MODE={MODE} BASE={BASE_URL} CATEGORY={CATEGORY} INTERVAL={INTERVAL} TRIGGER_BY={TRIGGER_BY}")
    print(f"SYMBOLS={SYMBOLS}")
    print(f"STRATEGIES={STRATEGIES}")
    print(f"POLL_SECONDS={POLL_SECONDS} EMA_LEN={EMA_LEN} VWAP_ANCHOR={VWAP_ANCHOR} SIDE={BOUNCE_SIDE} SESSION_ID={SESSION_ID or 'n/a'}")
    print(f"RR_RATIO={RR_RATIO} SL_TICKS={SL_TICKS} RISK_MODE={RISK_MODE} RISK_PCT={RISK_PCT} BALANCE={ACCOUNT_BALANCE_RAW}")

    # warm cache
    for sym in SYMBOLS:
        _get_instrument_filters(sym)

    active_symbols = list(SYMBOLS)

    while True:
        try:
            for sym in list(active_symbols):
                pos = _get_open_position(sym)
                if pos is not None:
                    filters = _get_instrument_filters(sym)
                    tick = filters.tick_size
                    applied = _apply_trading_stop_from_fill(symbol=sym, tick=tick)
                    if not applied and _constraint_failure:
                        print(f"[SESSION_STOP] {_constraint_failure}")
                        return
                    print(f"[AUTO-STOP] {sym} position detected; stopping bounce trader for this instrument.")
                    try:
                        active_symbols.remove(sym)
                    except ValueError:
                        pass
                    continue

                filters = _get_instrument_filters(sym)
                tick = filters.tick_size

                last = _get_last_price(sym)

                for strat in STRATEGIES:
                    key = f"{sym}:{strat}"
                    desired = _desired_trigger_for_strategy(sym, strat)
                    if not desired:
                        continue

                    side, trig_dir, raw_level = desired

                    # Trigger rounding:
                    # - rise-to => ceil to tick
                    # - fall-to => floor to tick
                    if trig_dir == 1:
                        trigger = _ceil_to_tick(raw_level, tick)
                    else:
                        trigger = _floor_to_tick(raw_level, tick)

                    if not _gap_ok(last, trigger, tick, trig_dir):
                        continue

                    if not _should_amend(key, trigger, tick):
                        continue

                    fee_rate = _fee_rate_for_category(CATEGORY)
                    risk_qty = _risk_qty(
                        entry=trigger,
                        side=side,
                        tick=tick,
                        qty_step=filters.qty_step,
                        min_qty=filters.min_qty,
                        fee_rate=fee_rate,
                    )

                    if risk_qty is None:
                        qty_s = QTY_MAP.get(sym, DEFAULT_QTY)
                        try:
                            qty_f = float(qty_s)
                        except Exception:
                            qty_f = float(DEFAULT_QTY)
                        qty_f = _round_qty_step(qty_f, filters.qty_step)
                    else:
                        qty_f = max(filters.min_qty, risk_qty)

                    if qty_f < filters.min_qty:
                        continue

                    qty = f"{qty_f:.12f}".rstrip("0").rstrip(".")

                    tp, sl = _compute_tp_sl(trigger, tick, side)
                    if RISK_MODE == "percent" and RISK_PCT > 0 and RR_RATIO > 0:
                        balance = _get_account_balance()
                        risk_pct, reward_pct = _evaluate_constraints(
                            balance=balance,
                            qty=qty_f,
                            entry=trigger,
                            tp=tp,
                            sl=sl,
                            side=side,
                            fee_rate=fee_rate,
                        )
                        min_reward_pct = RISK_PCT * RR_RATIO
                        if (
                            risk_pct is None
                            or reward_pct is None
                            or risk_pct < RISK_PCT
                            or reward_pct < min_reward_pct
                        ):
                            print(
                                "[CONSTRAINT_REJECT] symbol=%s strategy=%s risk_pct=%s reward_pct=%s required_risk_pct>=%s required_reward_pct>=%s"
                                % (sym, strat, risk_pct, reward_pct, RISK_PCT, min_reward_pct)
                            )
                            continue

                    order_link_id = _make_order_link_id(sym, strat)
                    _last_order_link_by_symbol[sym] = order_link_id

                    _place_or_amend_conditional_market(
                        symbol=sym,
                        side=side,
                        qty=qty,
                        trigger_price=trigger,
                        trigger_direction=trig_dir,
                        tp=tp,
                        sl=sl,
                        order_link_id=order_link_id,
                    )

                    _last_armed_trigger[key] = trigger

            if not active_symbols:
                print("No active instruments remaining (all triggered). Exiting.")
                return

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("Stopped.")
            return
        except Exception as exc:
            print(f"[ERROR] {type(exc).__name__}: {exc}")
            time.sleep(max(2.0, POLL_SECONDS))


if __name__ == "__main__":
    main()
