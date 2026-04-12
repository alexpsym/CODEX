"""OANDA bounce trader backend (EMA/VWAP pending-order flow)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from shared import oanda_api

MODE = (os.getenv("OANDA_MODE") or "demo").strip().lower()
INSTRUMENT = (os.getenv("BOUNCE_OANDA_INSTRUMENT") or os.getenv("BOUNCE_SYMBOLS") or "EUR_USD").strip().upper().replace("/", "_")
POLL_SECONDS = float(os.getenv("BOUNCE_POLL_SECONDS", "2"))
STRATEGY = (os.getenv("BOUNCE_STRATEGIES") or "ema").strip().lower()
SIDE = (os.getenv("BOUNCE_SIDE") or "Buy").strip().title()
EMA_LEN = int(float(os.getenv("EMA_LEN", "9")))
VWAP_ANCHOR = (os.getenv("BOUNCE_VWAP_ANCHOR") or "session").strip().lower()
DEFAULT_QTY = float(os.getenv("BOUNCE_DEFAULT_QTY", "1000") or 1000)
RR_RATIO = float(os.getenv("BOUNCE_RR_RATIO", "0") or 0)
SL_TICKS = float(os.getenv("BOUNCE_SL_TICKS", "0") or 0)
MIN_AMEND_TICKS = float(os.getenv("BOUNCE_MIN_AMEND_TICKS", "1") or 1)
SESSION_ID = (os.getenv("BOUNCE_SESSION_ID") or "").strip()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oanda_base_url() -> str:
    return oanda_api._base_url(MODE)  # type: ignore[attr-defined]


def _oanda_token() -> str:
    return oanda_api._api_key(MODE)  # type: ignore[attr-defined]


def _oanda_account_id() -> str:
    return oanda_api._account_id(MODE)  # type: ignore[attr-defined]


def _request(method: str, endpoint: str, *, params: Optional[Dict[str, object]] = None, json_body: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    url = f"{_oanda_base_url()}{endpoint}"
    headers = {"Authorization": f"Bearer {_oanda_token()}", "Content-Type": "application/json"}
    resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"OANDA {method} {endpoint} failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


def _tick_size(instrument: str) -> float:
    details = oanda_api.get_instrument_details(instrument, mode=MODE)
    pip_location = int(details.get("pipLocation", -4) or -4)
    return float(10 ** pip_location)


def _candles(instrument: str, granularity: str = "M1", count: int = 200) -> List[Dict[str, float]]:
    account_id = _oanda_account_id()
    data = _request(
        "GET",
        f"/accounts/{account_id}/instruments/{instrument}/candles",
        params={"price": "M", "granularity": granularity, "count": count},
    )
    rows = []
    for c in data.get("candles", []):
        if not c.get("complete"):
            continue
        mid = c.get("mid") or {}
        try:
            close = float(mid.get("c"))
            vol = float(c.get("volume") or 0)
        except Exception:
            continue
        rows.append({"close": close, "volume": vol})
    return rows


def _ema(values: List[float], length: int) -> Optional[float]:
    if len(values) < max(2, length):
        return None
    alpha = 2.0 / (length + 1.0)
    acc = values[0]
    for v in values[1:]:
        acc = alpha * v + (1 - alpha) * acc
    return acc


def _vwap(rows: List[Dict[str, float]]) -> Optional[float]:
    if not rows:
        return None
    pv = 0.0
    vv = 0.0
    for r in rows:
        pv += float(r["close"]) * max(1.0, float(r["volume"]))
        vv += max(1.0, float(r["volume"]))
    if vv <= 0:
        return None
    return pv / vv


def _trigger_price(instrument: str) -> float:
    rows = _candles(instrument, granularity="M1", count=300)
    closes = [r["close"] for r in rows]
    if "vwap" in STRATEGY:
        anchor_rows = rows[-500:] if VWAP_ANCHOR == "week" else rows[-200:]
        val = _vwap(anchor_rows)
    else:
        val = _ema(closes, EMA_LEN)
    if val is None:
        raise RuntimeError("Not enough OANDA candle data for trigger calculation")
    return float(val)


def _pending_orders(instrument: str) -> List[Dict[str, object]]:
    account_id = _oanda_account_id()
    data = _request("GET", f"/accounts/{account_id}/pendingOrders")
    out = []
    for o in data.get("orders", []):
        if str(o.get("instrument") or "").upper() != instrument.upper():
            continue
        if str(o.get("state") or "").upper() not in {"PENDING"}:
            continue
        out.append(o)
    return out


def _cancel_order(order_id: str) -> None:
    account_id = _oanda_account_id()
    _request("PUT", f"/accounts/{account_id}/orders/{order_id}/cancel")


def _place_pending_order(instrument: str, trigger_price: float) -> str:
    account_id = _oanda_account_id()
    current = float(oanda_api.get_price(instrument, mode=MODE))
    tick = _tick_size(instrument)
    side = SIDE.lower()
    units = int(round(DEFAULT_QTY if side == "buy" else -DEFAULT_QTY))

    order_type = "LIMIT"
    if side == "buy" and trigger_price >= current:
        order_type = "STOP"
    if side == "sell" and trigger_price <= current:
        order_type = "STOP"

    sl_price = None
    tp_price = None
    if SL_TICKS > 0:
        if side == "buy":
            sl_price = trigger_price - (SL_TICKS * tick)
            if RR_RATIO > 0:
                tp_price = trigger_price + (SL_TICKS * RR_RATIO * tick)
        else:
            sl_price = trigger_price + (SL_TICKS * tick)
            if RR_RATIO > 0:
                tp_price = trigger_price - (SL_TICKS * RR_RATIO * tick)

    order: Dict[str, object] = {
        "type": order_type,
        "instrument": instrument,
        "units": str(units),
        "timeInForce": "GTC",
        "positionFill": "DEFAULT",
        "price": f"{trigger_price:.5f}",
        "clientExtensions": {"id": f"{SESSION_ID}-{instrument}"[:64], "comment": "bounce-trader"},
    }
    if sl_price is not None:
        order["stopLossOnFill"] = {"price": f"{sl_price:.5f}"}
    if tp_price is not None:
        order["takeProfitOnFill"] = {"price": f"{tp_price:.5f}"}

    payload = {"order": order}
    data = _request("POST", f"/accounts/{account_id}/orders", json_body=payload)
    created = data.get("orderCreateTransaction") or {}
    return str(created.get("id") or "")


def _same(a: float, b: float, tick: float) -> bool:
    return abs(float(a) - float(b)) <= max(tick * max(1.0, MIN_AMEND_TICKS), 1e-9)


def main() -> None:
    print(f"[{_iso_now()}] OANDA bounce trader started mode={MODE} instrument={INSTRUMENT} side={SIDE}")
    while True:
        try:
            trigger = _trigger_price(INSTRUMENT)
            tick = _tick_size(INSTRUMENT)
            pending = _pending_orders(INSTRUMENT)
            existing = pending[0] if pending else None
            if existing:
                ex_price = float(existing.get("price") or existing.get("triggerCondition") or 0)
                if not _same(ex_price, trigger, tick):
                    _cancel_order(str(existing.get("id")))
                    oid = _place_pending_order(INSTRUMENT, trigger)
                    print(f"[{_iso_now()}] amended pending order -> {oid} trigger={trigger:.5f}")
            else:
                oid = _place_pending_order(INSTRUMENT, trigger)
                print(f"[{_iso_now()}] placed pending order -> {oid} trigger={trigger:.5f}")
        except Exception as exc:
            print(f"[{_iso_now()}] loop error: {exc}")
        time.sleep(max(1.0, POLL_SECONDS))


if __name__ == "__main__":
    main()
