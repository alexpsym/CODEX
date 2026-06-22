"""Shared helpers for external Python MT5 trading scripts.

MetaTrader5 is imported lazily so this package can be inspected and tested on
machines that do not have the Windows MT5 Python bridge installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import math
from typing import Any, Dict, Iterable, Optional


def import_mt5() -> Any:
    return importlib.import_module("MetaTrader5")


def last_error(mt5: Any) -> str:
    try:
        return str(mt5.last_error())
    except Exception:
        return "unknown MT5 error"


def initialize(mt5: Optional[Any] = None) -> Any:
    module = mt5 or import_mt5()
    if not module.initialize():
        raise RuntimeError(f"MT5 initialize failed: {last_error(module)}")
    if module.account_info() is None:
        raise RuntimeError(f"MT5 terminal is not logged in: {last_error(module)}")
    return module


def shutdown(mt5: Any) -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class RiskConfig:
    risk_aud_target: float = 10.0
    risk_aud_min: float = 9.0
    risk_aud_max: float = 12.0
    include_commission_in_risk: bool = True
    commission_per_lot_per_side: float = 3.50
    risk_slippage_buffer_points: int = 50


@dataclass
class OrderConfig:
    sl_distance_points: int = 200
    auto_tp_net_rr_enabled: bool = True
    net_rr_target: float = 2.0
    auto_tp_safety_points: int = 0
    tp_distance_points: int = 400
    slippage_points: int = 10
    magic_number: int = 91001
    comment: str = "TraderPy"


def symbol_info_or_raise(mt5: Any, symbol: str) -> Any:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Unknown MT5 symbol: {symbol}")
    if hasattr(mt5, "symbol_select") and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"MT5 symbol_select failed for {symbol}: {last_error(mt5)}")
    return info


def get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def point_for(info: Any) -> float:
    point = float(get_attr(info, "point", 0.0) or 0.0)
    if point <= 0:
        raise RuntimeError("Symbol point size is unavailable.")
    return point


def digits_for(info: Any) -> int:
    return int(get_attr(info, "digits", 5) or 5)


def normalize_price(info: Any, price: float) -> float:
    return round(float(price), digits_for(info))


def normalize_volume(info: Any, volume: float, *, round_up: bool = False) -> float:
    step = float(get_attr(info, "volume_step", 0.01) or 0.01)
    vmin = float(get_attr(info, "volume_min", step) or step)
    vmax = float(get_attr(info, "volume_max", volume) or volume)
    steps = math.ceil(volume / step) if round_up else math.floor(volume / step)
    result = steps * step
    result = max(vmin, min(vmax, result))
    digits = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(result, digits)


def value_per_point_per_lot(info: Any) -> float:
    point = point_for(info)
    tick_value = float(get_attr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(get_attr(info, "trade_tick_size", 0.0) or 0.0)
    if tick_value <= 0 or tick_size <= 0:
        raise RuntimeError("Symbol tick value/size is unavailable.")
    return tick_value * (point / tick_size)


def build_sl_from_distance(info: Any, entry: float, is_buy: bool, distance_points: int) -> float:
    if distance_points <= 0:
        raise ValueError("SL distance points must be > 0.")
    point = point_for(info)
    sl = entry - distance_points * point if is_buy else entry + distance_points * point
    return normalize_price(info, sl)


def build_tp_manual_from_distance(info: Any, entry: float, is_buy: bool, distance_points: int) -> float:
    if distance_points <= 0:
        raise ValueError("TP distance points must be > 0.")
    point = point_for(info)
    tp = entry + distance_points * point if is_buy else entry - distance_points * point
    return normalize_price(info, tp)


def compute_volume_from_risk(
    info: Any,
    entry: float,
    sl: float,
    risk: RiskConfig,
) -> tuple[float, float]:
    point = point_for(info)
    stop_points = abs(entry - sl) / point
    if stop_points <= 0:
        raise ValueError("Stop distance is zero.")
    loss_per_lot_sl = stop_points * value_per_point_per_lot(info)
    commission_round_turn = 2.0 * risk.commission_per_lot_per_side
    risk_per_lot = loss_per_lot_sl + commission_round_turn if risk.include_commission_in_risk else loss_per_lot_sl
    if risk_per_lot <= 0:
        raise ValueError("Risk per lot is invalid.")

    risk_min = risk.risk_aud_min
    risk_max = max(risk.risk_aud_max, risk_min)
    risk_target = max(risk.risk_aud_target, risk_min)
    volume = normalize_volume(info, risk_target / risk_per_lot)

    def risk_for(vol: float, stop_points_override: Optional[float] = None) -> float:
        loss = (stop_points_override or stop_points) * value_per_point_per_lot(info) * vol
        commission = commission_round_turn * vol
        return loss + commission if risk.include_commission_in_risk else loss

    current_risk = risk_for(volume)
    step = float(get_attr(info, "volume_step", 0.01) or 0.01)
    vmin = float(get_attr(info, "volume_min", step) or step)
    vmax = float(get_attr(info, "volume_max", volume) or volume)

    while current_risk > risk_max and volume - step >= vmin:
        volume = normalize_volume(info, volume - step)
        current_risk = risk_for(volume)
    while current_risk < risk_min and volume + step <= vmax:
        volume = normalize_volume(info, volume + step)
        current_risk = risk_for(volume)

    if current_risk < risk_min:
        raise ValueError("Rounded risk is below the minimum risk filter.")
    if current_risk > risk_max:
        raise ValueError("Rounded risk exceeds the maximum risk filter.")

    buffered_points = stop_points + max(0, risk.risk_slippage_buffer_points)
    while risk_for(volume, buffered_points) > risk_max and volume - step >= vmin:
        next_volume = normalize_volume(info, volume - step)
        if risk_for(next_volume) < risk_min:
            break
        volume = next_volume
        current_risk = risk_for(volume)

    return volume, current_risk


def compute_auto_tp_net_rr(
    mt5: Any,
    info: Any,
    symbol: str,
    entry: float,
    is_buy: bool,
    volume: float,
    risk_rounded_aud: float,
    risk: RiskConfig,
    order: OrderConfig,
) -> tuple[float, int, float]:
    if order.net_rr_target <= 0:
        raise ValueError("NetRR target must be > 0.")
    point = point_for(info)
    commission_rt = risk.commission_per_lot_per_side * 2.0 * volume
    r_base = risk_rounded_aud if risk.include_commission_in_risk else risk_rounded_aud + commission_rt
    required_gross = order.net_rr_target * r_base + commission_rt
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
    test_price = entry + point if is_buy else entry - point
    profit_one_point = mt5.order_calc_profit(order_type, symbol, volume, entry, test_price)
    if profit_one_point is None or abs(float(profit_one_point)) <= 0:
        raise RuntimeError(f"order_calc_profit failed while estimating TP: {last_error(mt5)}")
    points = max(1, math.ceil(required_gross / abs(float(profit_one_point))))
    points += max(0, order.auto_tp_safety_points)
    tp = entry + points * point if is_buy else entry - points * point
    tp = normalize_price(info, tp)
    gross_at_tp = mt5.order_calc_profit(order_type, symbol, volume, entry, tp)
    if gross_at_tp is None:
        raise RuntimeError(f"order_calc_profit failed while validating TP: {last_error(mt5)}")
    effective_net_rr = (float(gross_at_tp) - commission_rt) / r_base if r_base > 0 else 0.0
    return tp, points, effective_net_rr


def has_open_position(mt5: Any, symbol: str) -> bool:
    positions = mt5.positions_get(symbol=symbol)
    return bool(positions)


def cancel_pending_by_magic(mt5: Any, symbol: str, magic: int, comment: str = "TraderPy") -> int:
    orders = mt5.orders_get(symbol=symbol) or []
    cancelled = 0
    for order in orders:
        if int(get_attr(order, "magic", 0) or 0) != int(magic):
            continue
        order_type = int(get_attr(order, "type", -1) or -1)
        if order_type not in {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT}:
            continue
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(get_attr(order, "ticket")),
            "symbol": symbol,
            "magic": magic,
            "comment": comment,
        }
        result = mt5.order_send(request)
        if result is not None and int(get_attr(result, "retcode", 0) or 0) == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
    return cancelled


def order_send_or_raise(mt5: Any, request: Dict[str, Any]) -> Any:
    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"order_send returned None: {last_error(mt5)}")
    retcode = int(get_attr(result, "retcode", 0) or 0)
    accepted = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_PLACED", 10008)}
    if retcode not in accepted:
        raise RuntimeError(f"order_send rejected request retcode={retcode}: {result}")
    return result


def window_contains_time(t: datetime, start_hour: int, start_minute: int, end_hour: int, end_minute: int) -> bool:
    current = t.hour * 60 + t.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def position_summary(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "ticket": get_attr(item, "ticket"),
            "symbol": get_attr(item, "symbol"),
            "volume": get_attr(item, "volume"),
            "type": get_attr(item, "type"),
            "profit": get_attr(item, "profit"),
        }
        for item in items
    ]
