from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, List, Optional

import requests

BYBIT_OPTIONS_TAKER_FEE_RATE = 0.0003
BYBIT_OPTIONS_MAKER_FEE_RATE = 0.0002


@dataclass
class OptionCandidate:
    symbol: str
    expiry_token: str
    strike: float
    price_estimate: float
    bid: float
    ask: float
    open_interest: float
    volume_24h: float
    min_qty: float
    qty_step: float
    max_qty: float
    tick_size: float
    expiry_ts: int


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_manual_expiry_to_token(expiry: str) -> str:
    parts = [p.strip() for p in str(expiry or "").replace("-", "/").split("/") if p.strip()]
    if len(parts) != 3:
        raise ValueError("Manual expiry must be in D/M/YY format.")
    day, month, year = (int(x) for x in parts)
    if year < 100:
        year += 2000
    dt = datetime(year, month, day)
    return f"{dt.day}{dt.strftime('%b').upper()}{str(dt.year)[2:]}"


def _round_down_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    val = Decimal(str(value))
    st = Decimal(str(step))
    return float((val / st).to_integral_value(rounding=ROUND_FLOOR) * st)


def _round_to_step_nearest(value: float, step: float) -> float:
    if step <= 0:
        return value
    val = Decimal(str(value))
    st = Decimal(str(step))
    rounded = (val / st).quantize(Decimal("1")) * st
    return float(rounded)


def _public_get(base_url: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(f"{base_url}{endpoint}", params=params, timeout=12)
    r.raise_for_status()
    payload = r.json() or {}
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit API error: {payload.get('retMsg')}")
    return payload


def resolve_option_by_target_risk(
    *,
    base_url: str,
    account_mode: str,
    base_coin: str,
    side: str,
    option_type: str,
    order_type: str,
    target_risk_usdt: float,
    tolerance_usdt: float,
    expiry_mode: str,
    manual_expiry: str,
    strike_mode: str,
    manual_strike: str,
    quantity_mode: str,
    manual_quantity: float,
    manual_limit_price: Optional[float] = None,
    fee_mode: str = "roundtrip",
) -> Dict[str, Any]:
    side_norm = str(side or "").strip().capitalize()
    if side_norm not in {"Buy", "Sell"}:
        raise ValueError("Side must be Buy or Sell.")

    if side_norm == "Sell" and (
        str(expiry_mode).lower() == "auto"
        or str(strike_mode).lower() == "auto"
        or str(quantity_mode).lower() == "auto"
    ):
        raise ValueError(
            "Auto risk-based contract selection is only supported for Buy in single-leg options."
        )

    if target_risk_usdt <= 0:
        raise ValueError("Target risk must be greater than zero.")
    tolerance = max(0.0, float(tolerance_usdt or 0.0))
    risk_min = target_risk_usdt - tolerance
    risk_max = target_risk_usdt + tolerance

    opt = str(option_type or "").strip().capitalize()
    if opt not in {"Call", "Put"}:
        raise ValueError("Option type must be Call or Put.")
    opt_char = "C" if opt == "Call" else "P"

    manual_expiry_token = _parse_manual_expiry_to_token(manual_expiry) if str(expiry_mode).lower() == "manual" else ""
    manual_strike_value = _to_float(manual_strike, 0.0) if str(strike_mode).lower() == "manual" else 0.0

    inst_payload = _public_get(
        base_url,
        "/v5/market/instruments-info",
        {"category": "option", "baseCoin": str(base_coin or "").upper()},
    )
    tk_payload = _public_get(
        base_url,
        "/v5/market/tickers",
        {"category": "option", "baseCoin": str(base_coin or "").upper()},
    )

    inst_list = inst_payload.get("result", {}).get("list", []) or []
    ticker_list = tk_payload.get("result", {}).get("list", []) or []
    ticker_by_symbol = {str(t.get("symbol") or ""): t for t in ticker_list if t.get("symbol")}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    candidates: List[OptionCandidate] = []

    for inst in inst_list:
        symbol = str(inst.get("symbol") or "")
        if not symbol:
            continue
        parts = symbol.split("-")
        if len(parts) < 4 or parts[3].upper() != opt_char:
            continue
        if str(inst.get("status") or "Trading") not in {"Trading", "Settling"}:
            continue
        delivery_ts = int(_to_float(inst.get("deliveryTime"), 0.0))
        if delivery_ts and delivery_ts <= now_ms:
            continue
        expiry_token = parts[1]
        strike_val = _to_float(parts[2], 0.0)
        if manual_expiry_token and expiry_token != manual_expiry_token:
            continue
        if manual_strike_value > 0 and abs(strike_val - manual_strike_value) > 1e-9:
            continue

        tk = ticker_by_symbol.get(symbol) or {}
        bid = _to_float(tk.get("bid1Price"), 0.0)
        ask = _to_float(tk.get("ask1Price"), 0.0)
        last = _to_float(tk.get("lastPrice"), 0.0)
        mark = _to_float(tk.get("markPrice"), 0.0)
        price_market = ask if ask > 0 else (last if last > 0 else mark)
        if price_market <= 0:
            continue
        spread = (ask - bid) if ask > 0 and bid > 0 else 999999.0
        if spread < 0:
            spread = 999999.0

        lot = inst.get("lotSizeFilter") or {}
        min_qty = _to_float(lot.get("minOrderQty"), 0.0)
        qty_step = _to_float(lot.get("qtyStep"), min_qty)
        max_qty = _to_float(lot.get("maxOrderQty"), 0.0)
        tick_size = _to_float((inst.get("priceFilter") or {}).get("tickSize"), 0.0)
        if min_qty <= 0 or qty_step <= 0:
            continue

        price_for_calc = (
            float(manual_limit_price)
            if str(order_type).lower() == "limit" and manual_limit_price and manual_limit_price > 0
            else price_market
        )
        if price_for_calc <= 0:
            continue

        candidates.append(
            OptionCandidate(
                symbol=symbol,
                expiry_token=expiry_token,
                strike=strike_val,
                price_estimate=price_for_calc,
                bid=bid,
                ask=ask,
                open_interest=_to_float(tk.get("openInterest"), 0.0),
                volume_24h=_to_float(tk.get("volume24h"), 0.0),
                min_qty=min_qty,
                qty_step=qty_step,
                max_qty=max_qty,
                tick_size=tick_size,
                expiry_ts=delivery_ts,
            )
        )

    if not candidates:
        raise ValueError("No tradable option contracts found for the selected filters.")

    sides = 2 if str(fee_mode).lower() == "roundtrip" else 1
    fee_rate = BYBIT_OPTIONS_MAKER_FEE_RATE if str(order_type).lower() == "limit" else BYBIT_OPTIONS_TAKER_FEE_RATE

    scored: List[Dict[str, Any]] = []
    for c in candidates:
        per_unit_cost = c.price_estimate * (1.0 + fee_rate * sides)
        if per_unit_cost <= 0:
            continue

        if str(quantity_mode).lower() == "manual":
            qty = _round_to_step_nearest(float(manual_quantity or 0.0), c.qty_step)
        else:
            qty = _round_to_step_nearest(target_risk_usdt / per_unit_cost, c.qty_step)
        if qty < c.min_qty:
            qty = c.min_qty
        if c.max_qty > 0 and qty > c.max_qty:
            if str(quantity_mode).lower() == "manual":
                continue
            qty = _round_down_step(c.max_qty, c.qty_step)
        if qty <= 0:
            continue

        est_cost = per_unit_cost * qty
        dist = abs(est_cost - target_risk_usdt)
        inside = risk_min <= est_cost <= risk_max
        spread = (c.ask - c.bid) if c.ask > 0 and c.bid > 0 else 999999.0
        expiry_rank = c.expiry_ts if c.expiry_ts > 0 else 9999999999999
        scored.append(
            {
                "candidate": c,
                "qty": qty,
                "est_cost": est_cost,
                "distance": dist,
                "inside": inside,
                "spread": spread,
                "liq": -(c.open_interest + c.volume_24h),
                "expiry_rank": expiry_rank,
            }
        )

    if not scored:
        raise ValueError("No candidates survived quantity/risk validation.")

    scored.sort(
        key=lambda x: (
            0 if x["inside"] else 1,
            x["distance"],
            x["spread"],
            x["liq"],
            x["expiry_rank"],
        )
    )
    best = scored[0]
    if not best["inside"]:
        raise ValueError(
            f"No option candidate found within tolerance band [{risk_min:.4f}, {risk_max:.4f}] USDT."
        )

    c = best["candidate"]
    reason = (
        f"inside tolerance, distance={best['distance']:.4f}, spread={best['spread']:.6f}, "
        f"liq={c.open_interest + c.volume_24h:.2f}, expiry={c.expiry_token}"
    )
    return {
        "account_mode": account_mode,
        "resolved_symbol": c.symbol,
        "resolved_expiry": c.expiry_token,
        "resolved_strike": c.strike,
        "resolved_qty": round(best["qty"], 8),
        "entry_price_used": round(c.price_estimate, 8),
        "estimated_total_cost": round(best["est_cost"], 8),
        "fee_estimate": round(c.price_estimate * fee_rate * sides * best["qty"], 8),
        "distance_from_target": round(best["distance"], 8),
        "target_risk_usdt": round(target_risk_usdt, 8),
        "tolerance_usdt": round(tolerance, 8),
        "risk_band_min": round(risk_min, 8),
        "risk_band_max": round(risk_max, 8),
        "why_selected": reason,
        "lot_size": {
            "minOrderQty": c.min_qty,
            "qtyStep": c.qty_step,
            "maxOrderQty": c.max_qty,
        },
    }
