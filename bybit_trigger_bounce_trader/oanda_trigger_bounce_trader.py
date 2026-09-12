"""OANDA bounce trader backend (EMA/VWAP pending-order flow)."""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from shared import oanda_api, oanda_risk

MODE = (os.getenv("OANDA_MODE") or "demo").strip().lower()
INSTRUMENT = (os.getenv("BOUNCE_OANDA_INSTRUMENT") or os.getenv("BOUNCE_SYMBOLS") or "EUR_USD").strip().upper().replace("/", "_")
POLL_SECONDS = float(os.getenv("BOUNCE_POLL_SECONDS", "2"))
STRATEGY = (os.getenv("BOUNCE_STRATEGIES") or "ema").strip().lower()
SIDE = (os.getenv("BOUNCE_SIDE") or "Buy").strip().title()
EMA_LEN = int(float(os.getenv("EMA_LEN", "9")))
VWAP_ANCHOR = (os.getenv("BOUNCE_VWAP_ANCHOR") or "session").strip().lower()
DEFAULT_QTY = float(os.getenv("BOUNCE_DEFAULT_QTY", "1000") or 1000)
RISK_MODE = (os.getenv("BOUNCE_RISK_MODE") or "fixed_qty").strip().lower()
RISK_AUD = Decimal(os.getenv("BOUNCE_RISK_AUD", "0") or "0")
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


def _instrument_meta(instrument: str) -> Dict[str, object]:
    account_id = _oanda_account_id()
    data = _request("GET", f"/accounts/{account_id}/instruments", params={"instruments": instrument})
    rows = data.get("instruments") or []
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError("OANDA instrument metadata unavailable for fixed-AUD sizing.")
    return rows[0]


def _price_midpoints(rows: List[Dict[str, object]]) -> Dict[str, Decimal]:
    result: Dict[str, Decimal] = {}
    for row in rows:
        symbol = str(row.get("instrument") or "").upper()
        bids, asks = row.get("bids") or [], row.get("asks") or []
        try:
            bid = Decimal(str((bids[0] if bids else {}).get("price") or row.get("closeoutBid") or "0"))
            ask = Decimal(str((asks[0] if asks else {}).get("price") or row.get("closeoutAsk") or "0"))
        except Exception:
            continue
        if symbol and bid > 0 and ask > 0:
            result[symbol] = (bid + ask) / Decimal("2")
    return result


def _format_price(value: Decimal, display_precision: int) -> str:
    return f"{value:.{max(0, int(display_precision))}f}"


def _fixed_aud_order_values(instrument: str, trigger_price: float) -> tuple[Decimal, Decimal, Decimal, int]:
    if RISK_AUD <= 0 or SL_TICKS <= 0:
        raise RuntimeError("Fixed Risk (AUD) requires positive risk and SL Ticks / Pips.")
    account_id = _oanda_account_id()
    meta = _instrument_meta(instrument)
    display_precision = int(meta.get("displayPrecision") or 0)
    units_precision = int(meta.get("tradeUnitsPrecision") or 0)
    tick = Decimal("1").scaleb(-display_precision)
    entry = Decimal(str(trigger_price))
    stop = entry - Decimal(str(SL_TICKS)) * tick if SIDE.lower() == "buy" else entry + Decimal(str(SL_TICKS)) * tick
    summary = _request("GET", f"/accounts/{account_id}/summary")
    home = str((summary.get("account") or summary).get("currency") or "").upper()
    quote = instrument.split("_", 1)[-1]
    symbols = [instrument]
    if home and home != "AUD":
        symbols.extend([f"AUD_{home}", f"{home}_AUD"])
    prices = _request("GET", f"/accounts/{account_id}/pricing", params={"instruments": ",".join(symbols), "includeHomeConversions": "true"})
    rows = [row for row in (prices.get("prices") or []) if isinstance(row, dict)]
    row = next((item for item in rows if str(item.get("instrument") or "").upper() == instrument), None)
    if row is None:
        raise RuntimeError("OANDA bid/ask unavailable for fixed-AUD sizing.")
    bids, asks = row.get("bids") or [], row.get("asks") or []
    bid = Decimal(str((bids[0] if bids else {}).get("price") or "0"))
    ask = Decimal(str((asks[0] if asks else {}).get("price") or "0"))
    if bid <= 0 or ask <= 0:
        raise RuntimeError("OANDA bid/ask unavailable for fixed-AUD sizing.")
    try:
        risk_home = oanda_risk.aud_to_home_currency(RISK_AUD, home, _price_midpoints(rows))
        _gain, loss, _value = oanda_risk.quote_home_factors(prices, row, quote, home)
        sized = oanda_risk.size_fixed_risk_units(
            risk_home=risk_home, entry=entry, stop=stop, bid=bid, ask=ask, loss_factor=loss,
            units_precision=units_precision, minimum_trade_size=Decimal(str(meta.get("minimumTradeSize") or "0")),
            maximum_order_units=Decimal(str(meta.get("maximumOrderUnits") or "0")),
            maximum_position_size=Decimal(str(meta.get("maximumPositionSize") or "0")),
        )
    except ValueError as exc:
        raise RuntimeError(f"Fixed Risk (AUD) sizing unavailable: {exc}") from exc
    units = sized["units"] if SIDE.lower() == "buy" else -sized["units"]
    return units, stop, entry, display_precision


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


def _cancel_order(order_id: str) -> Dict[str, object]:
    account_id = _oanda_account_id()
    return _request("PUT", f"/accounts/{account_id}/orders/{order_id}/cancel")


def _client_order_id(instrument: str) -> str:
    return f"{SESSION_ID}-{instrument}"[:64]


def _tracked_order_state(order_id: str) -> tuple[str, Optional[Dict[str, object]]]:
    """Read only the session-owned order; never infer state from pendingOrders."""
    account_id = _oanda_account_id()
    data = _request("GET", f"/accounts/{account_id}/orders/{order_id}")
    order = data.get("order") if isinstance(data, dict) else None
    if not isinstance(order, dict):
        return "uncertain", None
    state = str(order.get("state") or "").upper()
    if state in {"FILLED", "TRIGGERED"} or order.get("orderFillTransaction") or order.get("fillingTransactionID") or order.get("tradeID"):
        return "triggered", order
    if state in {"CANCELLED", "REJECTED", "DEACTIVATED"}:
        return "cancelled", order
    if state == "PENDING":
        return "armed", order
    return "uncertain", order


def _terminal_log(order_id: str, state: str) -> None:
    print(
        f"[{_iso_now()}] [ONE_SHOT_TERMINAL] broker=oanda instrument={INSTRUMENT} "
        f"session={SESSION_ID or 'n/a'} order={order_id or _client_order_id(INSTRUMENT)} state={state}"
    )


def _place_pending_order(instrument: str, trigger_price: float) -> str:
    account_id = _oanda_account_id()
    current = float(oanda_api.get_price(instrument, mode=MODE))
    side = SIDE.lower()
    if RISK_MODE == "fixed_aud":
        units, sl_price, entry, display_precision = _fixed_aud_order_values(instrument, trigger_price)
        trigger_price = float(entry)
        tick = float(Decimal("1").scaleb(-display_precision))
    else:
        tick = _tick_size(instrument)
        units = Decimal(str(DEFAULT_QTY if side == "buy" else -DEFAULT_QTY))
        sl_price = None
        display_precision = int(oanda_api.get_instrument_details(instrument, mode=MODE).get("displayPrecision", 5) or 5)

    order_type = "LIMIT"
    if side == "buy" and trigger_price >= current:
        order_type = "STOP"
    if side == "sell" and trigger_price <= current:
        order_type = "STOP"

    tp_price = None
    if SL_TICKS > 0 and RISK_MODE != "fixed_aud":
        if side == "buy":
            sl_price = trigger_price - (SL_TICKS * tick)
            if RR_RATIO > 0:
                tp_price = trigger_price + (SL_TICKS * RR_RATIO * tick)
        else:
            sl_price = trigger_price + (SL_TICKS * tick)
            if RR_RATIO > 0:
                tp_price = trigger_price - (SL_TICKS * RR_RATIO * tick)
    elif RISK_MODE == "fixed_aud" and RR_RATIO > 0 and sl_price is not None:
        distance = abs(trigger_price - float(sl_price)) * RR_RATIO
        tp_price = trigger_price + distance if side == "buy" else trigger_price - distance

    order: Dict[str, object] = {
        "type": order_type,
        "instrument": instrument,
        "units": str(units),
        "timeInForce": "GTC",
        "positionFill": "DEFAULT",
        "price": _format_price(Decimal(str(trigger_price)), display_precision),
        "clientExtensions": {"id": _client_order_id(instrument), "comment": "bounce-trader"},
    }
    if sl_price is not None:
        order["stopLossOnFill"] = {"price": _format_price(Decimal(str(sl_price)), display_precision)}
    if tp_price is not None:
        order["takeProfitOnFill"] = {"price": _format_price(Decimal(str(tp_price)), display_precision)}

    payload = {"order": order}
    data = _request("POST", f"/accounts/{account_id}/orders", json_body=payload)
    created = data.get("orderCreateTransaction") or {}
    order_id = str(created.get("id") or "")
    if not order_id:
        raise RuntimeError("ambiguous OANDA create response: missing order id")
    return order_id


def _same(a: float, b: float, tick: float) -> bool:
    return abs(float(a) - float(b)) <= max(tick * max(1.0, MIN_AMEND_TICKS), 1e-9)


def main() -> None:
    print(f"[{_iso_now()}] OANDA bounce trader started mode={MODE} instrument={INSTRUMENT} side={SIDE}")
    tracked_order_id = ""
    while True:
        try:
            if tracked_order_id:
                state, existing = _tracked_order_state(tracked_order_id)
                if state != "armed":
                    _terminal_log(tracked_order_id, state)
                    return
            else:
                existing = None

            trigger = _trigger_price(INSTRUMENT)
            tick = _tick_size(INSTRUMENT)
            if existing:
                ex_price = float(existing.get("price") or existing.get("triggerCondition") or 0)
                if not _same(ex_price, trigger, tick):
                    _cancel_order(tracked_order_id)
                    state, _resolved = _tracked_order_state(tracked_order_id)
                    if state == "triggered":
                        _terminal_log(tracked_order_id, "triggered")
                        return
                    if state != "cancelled":
                        _terminal_log(tracked_order_id, "uncertain")
                        return
                    tracked_order_id = _place_pending_order(INSTRUMENT, trigger)
                    print(f"[{_iso_now()}] amended pending order -> {tracked_order_id} trigger={trigger:.5f}")
            else:
                tracked_order_id = _place_pending_order(INSTRUMENT, trigger)
                print(f"[{_iso_now()}] placed pending order -> {tracked_order_id} trigger={trigger:.5f}")
        except Exception as exc:
            _terminal_log(tracked_order_id, f"uncertain-{type(exc).__name__}")
            return
        time.sleep(max(1.0, POLL_SECONDS))


if __name__ == "__main__":
    main()
