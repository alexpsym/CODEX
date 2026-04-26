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
import time
import hmac
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from shared.env_bootstrap import load_master_env

load_master_env()

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
KLINE_LIMIT = int(os.getenv("BOUNCE_KLINE_LIMIT", "200"))

# Symbols:
# Example: "BTCUSDT,ETHUSDT,SOLUSDT"
SYMBOLS = [s.strip().upper() for s in (os.getenv("BOUNCE_SYMBOLS", "BTCUSDT").split(",")) if s.strip()]

# Strategies to arm (comma-separated):
#  - ema9_long   : enter long when price FALLS to EMA(9) from above
#  - ema9_short  : enter short when price RISES to EMA(9) from below
#  - vwap_long   : enter long when price FALLS to VWAP from above
#  - vwap_short  : enter short when price RISES to VWAP from below
STRATEGIES = [s.strip().lower() for s in (os.getenv("BOUNCE_STRATEGIES", "ema9_long,vwap_long").split(",")) if s.strip()]

EMA_LEN = int(os.getenv("EMA_LEN", "9"))
VWAP_LEN = int(os.getenv("VWAP_LEN", "20"))

# Per-symbol qty (contracts) map as JSON, fallback default qty:
# Example:
#   BOUNCE_QTY_MAP={"BTCUSDT":"0.01","ETHUSDT":"0.1"}
QTY_MAP_RAW = os.getenv("BOUNCE_QTY_MAP", "{}").strip()
try:
    QTY_MAP = {k.upper(): str(v) for k, v in json.loads(QTY_MAP_RAW).items()}
except Exception:
    QTY_MAP = {}
DEFAULT_QTY = os.getenv("BOUNCE_DEFAULT_QTY", "0.001")

# Optional TP/SL as % from entry trigger price (0 disables)
TP_PCT = float(os.getenv("BOUNCE_TP_PCT", "0"))  # e.g. 0.25 means +0.25% for longs
SL_PCT = float(os.getenv("BOUNCE_SL_PCT", "0"))  # e.g. 0.15 means -0.15% for longs

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


_instrument_cache: Dict[str, InstrumentFilters] = {}


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

    if tick <= 0 or step <= 0:
        raise RuntimeError(f"Bad filters for {symbol}: tick={tick}, step={step}")

    f = InstrumentFilters(tick_size=tick, qty_step=step)
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


def _get_klines(symbol: str) -> List[dict]:
    # /v5/market/kline
    j = _public_get(
        "/v5/market/kline",
        params={
            "category": CATEGORY,
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": str(KLINE_LIMIT),
        },
        timeout=10,
    )
    candles = ((j.get("result") or {}).get("list") or [])
    # Candle array format (Bybit): [startTime, open, high, low, close, volume, turnover]
    out = []
    for c in candles:
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

    # Ensure ASC time order for indicator calc
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


def _vwap(candles: List[dict], length: int) -> Optional[float]:
    if length <= 0 or len(candles) < length:
        return None
    window = candles[-length:]
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
    raw = f"BB_{symbol}_{strategy}_{INTERVAL}"
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
        _signed_post("/v5/order/amend", body_amend, timeout=10)
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

    _signed_post("/v5/order/create", body_create, timeout=10)
    print(f"[CREATE] {symbol} {order_link_id} side={side} trigger={trigger_price} qty={qty}")


def _compute_tp_sl(entry: float, tick: float, side: str) -> Tuple[Optional[float], Optional[float]]:
    if TP_PCT <= 0 and SL_PCT <= 0:
        return None, None

    if side == "Buy":
        tp = entry * (1.0 + TP_PCT / 100.0) if TP_PCT > 0 else None
        sl = entry * (1.0 - SL_PCT / 100.0) if SL_PCT > 0 else None
        if tp is not None:
            tp = _ceil_to_tick(tp, tick)
        if sl is not None:
            sl = _floor_to_tick(sl, tick)
        return tp, sl

    # Sell
    tp = entry * (1.0 - TP_PCT / 100.0) if TP_PCT > 0 else None
    sl = entry * (1.0 + SL_PCT / 100.0) if SL_PCT > 0 else None
    if tp is not None:
        tp = _floor_to_tick(tp, tick)
    if sl is not None:
        sl = _ceil_to_tick(sl, tick)
    return tp, sl


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
    candles = _get_klines(symbol)
    if len(candles) < max(EMA_LEN, VWAP_LEN) + 2:
        return None

    closes = [c["c"] for c in candles]

    if strategy == "ema9_long":
        ema_val = _ema(closes, EMA_LEN)
        if ema_val is None:
            return None
        return ("Buy", 2, ema_val)

    if strategy == "ema9_short":
        ema_val = _ema(closes, EMA_LEN)
        if ema_val is None:
            return None
        return ("Sell", 1, ema_val)

    if strategy == "vwap_long":
        vwap_val = _vwap(candles, VWAP_LEN)
        if vwap_val is None:
            return None
        return ("Buy", 2, vwap_val)

    if strategy == "vwap_short":
        vwap_val = _vwap(candles, VWAP_LEN)
        if vwap_val is None:
            return None
        return ("Sell", 1, vwap_val)

    return None


def main() -> None:
    print(f"MODE={MODE} BASE={BASE_URL} CATEGORY={CATEGORY} INTERVAL={INTERVAL} TRIGGER_BY={TRIGGER_BY}")
    print(f"SYMBOLS={SYMBOLS}")
    print(f"STRATEGIES={STRATEGIES}")
    print(f"POLL_SECONDS={POLL_SECONDS} EMA_LEN={EMA_LEN} VWAP_LEN={VWAP_LEN}")

    # warm cache
    for sym in SYMBOLS:
        _get_instrument_filters(sym)

    while True:
        try:
            for sym in SYMBOLS:
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

                    qty_s = QTY_MAP.get(sym, DEFAULT_QTY)
                    try:
                        qty_f = float(qty_s)
                    except Exception:
                        qty_f = float(DEFAULT_QTY)

                    qty_f = _round_qty_step(qty_f, filters.qty_step)
                    if qty_f <= 0:
                        continue

                    qty = f"{qty_f:.12f}".rstrip("0").rstrip(".")

                    tp, sl = _compute_tp_sl(trigger, tick, side)

                    order_link_id = _make_order_link_id(sym, strat)

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

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("Stopped.")
            return
        except Exception as exc:
            print(f"[ERROR] {type(exc).__name__}: {exc}")
            time.sleep(max(2.0, POLL_SECONDS))


if __name__ == "__main__":
    main()
