"""Pepperstone/MT5 spread fetcher using lazy MetaTrader5 imports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import os
import statistics
from typing import Any, Callable, Dict, Iterable, List, Optional

from spread_core import TimeframeConfig, coerce_float, make_sample, spread_pct_from_bid_ask
from symbols import resolve_mt5_symbol


DEFAULT_RAZOR_COMMISSION_PER_LOT_PER_SIDE = 3.50


def _import_mt5() -> Any:
    return importlib.import_module("MetaTrader5")


def _last_error(mt5: Any) -> str:
    try:
        return str(mt5.last_error())
    except Exception:
        return "unknown MT5 error"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tick_time_to_dt(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1_000_000_000_000:
        number = number / 1000.0
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _field(row: object, name: str) -> object:
    if isinstance(row, dict):
        return row.get(name)
    try:
        return row[name]  # numpy structured rows
    except Exception:
        return getattr(row, name, None)


def _razor_commission_per_side() -> float:
    raw = os.getenv("PEPPERSTONE_RAZOR_COMMISSION_PER_LOT_PER_SIDE", str(DEFAULT_RAZOR_COMMISSION_PER_LOT_PER_SIDE))
    value = coerce_float(raw)
    return DEFAULT_RAZOR_COMMISSION_PER_LOT_PER_SIDE if value is None else max(0.0, value)


def _pepperstone_account_type() -> str:
    return os.getenv("PEPPERSTONE_ACCOUNT_TYPE", "razor").strip().lower() or "razor"


def _point_from_info(info: object) -> float:
    return coerce_float(getattr(info, "point", None)) or coerce_float(getattr(info, "trade_tick_size", None)) or 0.0


def _contract_size_from_info(info: object) -> float:
    return coerce_float(getattr(info, "trade_contract_size", None)) or 100000.0


def _razor_commission_adjustment_factory(mt5: Any, symbol: str, info: object) -> Callable[[float], float]:
    if _pepperstone_account_type() not in {"razor", "pepperstone_razor", "raw"}:
        return lambda _midpoint: 0.0
    commission_round_turn = _razor_commission_per_side() * 2.0
    if commission_round_turn <= 0:
        return lambda _midpoint: 0.0
    point = _point_from_info(info)
    contract_size = _contract_size_from_info(info)
    order_type = getattr(mt5, "ORDER_TYPE_BUY", 0)

    def adjustment(midpoint: float) -> float:
        if midpoint <= 0:
            return 0.0
        profit_per_point = None
        if point > 0 and hasattr(mt5, "order_calc_profit"):
            try:
                profit_per_point = mt5.order_calc_profit(order_type, symbol, 1.0, midpoint, midpoint + point)
            except Exception:
                profit_per_point = None
        profit_value = coerce_float(profit_per_point)
        if point > 0 and profit_value is not None and abs(profit_value) > 0:
            commission_points = commission_round_turn / abs(profit_value)
            return ((commission_points * point) / midpoint) * 100.0
        if contract_size > 0:
            return (commission_round_turn / (midpoint * contract_size)) * 100.0
        return 0.0

    return adjustment


def _iter_tick_spreads(
    ticks: Iterable[object],
    commission_adjustment_pct: Optional[Callable[[float], float]] = None,
) -> List[Dict[str, object]]:
    samples: List[Dict[str, object]] = []
    for row in ticks:
        dt = _tick_time_to_dt(_field(row, "time"))
        if dt is None:
            dt = _tick_time_to_dt(_field(row, "time_msc"))
        if dt is None:
            continue
        bid = coerce_float(_field(row, "bid"))
        ask = coerce_float(_field(row, "ask"))
        if bid is None or ask is None:
            continue
        try:
            spread_pct = spread_pct_from_bid_ask(bid, ask)
        except ValueError:
            continue
        if commission_adjustment_pct is not None:
            midpoint = (ask + bid) / 2.0
            spread_pct += max(0.0, commission_adjustment_pct(midpoint))
        sample = make_sample(dt, spread_pct)
        if sample is not None:
            samples.append(sample)
    return sorted(samples, key=lambda item: str(item.get("time") or ""))


def _bucket_start(dt: datetime, timeframe: TimeframeConfig) -> datetime:
    dt = _ensure_utc(dt)
    if timeframe.label == "W":
        start = dt - timedelta(days=dt.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe.label == "M":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seconds = timeframe.seconds or 60
    epoch = int(dt.timestamp())
    bucket_epoch = epoch - (epoch % seconds)
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


def aggregate_tick_spreads(
    ticks: Iterable[object],
    timeframe: TimeframeConfig,
    *,
    commission_adjustment_pct: Optional[Callable[[float], float]] = None,
) -> Dict[str, object]:
    tick_samples = _iter_tick_spreads(ticks, commission_adjustment_pct)
    if not tick_samples:
        return {"samples": [], "latest": None, "error": "No MT5 tick bid/ask spread data returned."}

    grouped: Dict[str, List[float]] = {}
    for sample in tick_samples:
        dt = datetime.fromisoformat(str(sample["time"]).replace("Z", "+00:00"))
        bucket = _bucket_start(dt, timeframe).isoformat().replace("+00:00", "Z")
        grouped.setdefault(bucket, []).append(float(sample["spread_pct"]))

    baseline = [
        {"time": bucket, "spread_pct": statistics.median(values)}
        for bucket, values in sorted(grouped.items())
        if values
    ]
    latest = tick_samples[-1]
    return {"samples": baseline, "latest": latest}


def available_mt5_symbols(mt5_module: Optional[Any] = None) -> List[str]:
    mt5 = mt5_module or _import_mt5()
    initialized_here = mt5_module is None
    if initialized_here and not mt5.initialize():
        return []
    try:
        symbols = mt5.symbols_get()
        if symbols is None:
            return []
        return [str(getattr(item, "name", "") or "") for item in symbols if getattr(item, "name", "")]
    finally:
        if initialized_here:
            try:
                mt5.shutdown()
            except Exception:
                pass


def fetch_mt5_spread_samples(
    oanda_symbol: str,
    timeframe: TimeframeConfig,
    context: Optional[Dict[str, object]] = None,
    *,
    mt5_module: Optional[Any] = None,
    date_to_utc: Optional[datetime] = None,
    date_from_utc: Optional[datetime] = None,
) -> Dict[str, object]:
    mt5 = mt5_module or _import_mt5()
    initialized_here = mt5_module is None
    if initialized_here and not mt5.initialize():
        return {"error": f"MT5 terminal initialize failed: {_last_error(mt5)}"}

    try:
        account_info = mt5.account_info()
        if account_info is None:
            return {"error": f"MT5 terminal is not logged in: {_last_error(mt5)}"}

        symbol_infos = mt5.symbols_get()
        if symbol_infos is None:
            return {"error": f"MT5 symbols unavailable: {_last_error(mt5)}"}
        resolved = resolve_mt5_symbol(oanda_symbol, symbol_infos)
        if not resolved:
            return {"error": f"MT5 symbol unavailable for {oanda_symbol}."}

        if hasattr(mt5, "symbol_select") and not mt5.symbol_select(resolved, True):
            return {"error": f"MT5 symbol_select failed for {resolved}: {_last_error(mt5)}"}

        end = _ensure_utc(date_to_utc or datetime.now(timezone.utc))
        lookback_days = timeframe.mt5_lookback_days
        if isinstance(context, dict) and context.get("has_cached_baseline"):
            lookback_days = min(lookback_days, 7)
        start = _ensure_utc(date_from_utc or (end - timedelta(days=lookback_days)))
        tick_flag = getattr(mt5, "COPY_TICKS_INFO", getattr(mt5, "COPY_TICKS_ALL", 0))
        ticks = mt5.copy_ticks_range(resolved, start, end, tick_flag)
        if ticks is None:
            return {"error": f"MT5 tick history unavailable for {resolved}: {_last_error(mt5)}"}
        try:
            tick_count = len(ticks)
        except TypeError:
            tick_count = 0
        if tick_count == 0:
            return {"error": f"MT5 tick history is empty for {resolved}."}

        symbol_info = mt5.symbol_info(resolved) if hasattr(mt5, "symbol_info") else None
        commission_adjustment = _razor_commission_adjustment_factory(mt5, resolved, symbol_info)
        parsed = aggregate_tick_spreads(ticks, timeframe, commission_adjustment_pct=commission_adjustment)
        parsed["resolved_symbol"] = resolved
        parsed["cost_model"] = "pepperstone_razor_all_in"
        parsed["commission_per_lot_per_side"] = _razor_commission_per_side()
        return parsed
    finally:
        if initialized_here:
            try:
                mt5.shutdown()
            except Exception:
                pass
