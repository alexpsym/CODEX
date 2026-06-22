"""External Python MT5 trader mirroring Trader.mq5 where practical.

This script runs outside MT5. It can submit orders through the local terminal,
but it cannot read arbitrary chart trendline objects like a chart-attached EA.
Use manual entry-price mode for the Python equivalent of trendline execution.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import pandas as pd

from mt5_common import (
    OrderConfig,
    RiskConfig,
    build_sl_from_distance,
    build_tp_manual_from_distance,
    cancel_pending_by_magic,
    compute_auto_tp_net_rr,
    compute_volume_from_risk,
    get_attr,
    has_open_position,
    initialize,
    normalize_price,
    order_send_or_raise,
    shutdown,
    symbol_info_or_raise,
)


class StrategyMode(str, Enum):
    MANUAL_LIMIT = "manual-limit"
    STANDARD_LIMIT = "standard-limit"
    EMA_BOUNCE = "ema-bounce"


@dataclass
class TraderConfig:
    symbol: str
    strategy: StrategyMode = StrategyMode.MANUAL_LIMIT
    orders_enabled: bool = True
    side: str = "buy"
    entry_price: float = 0.0
    enforce_one_trade_at_a_time: bool = True
    pending_cancel_existing: bool = True
    use_dual_ema: bool = True
    fast_ema_period: int = 9
    slow_ema_period: int = 20
    trend_ema_period: int = 20
    timeframe: Optional[int] = None


def _is_buy_side(side: str) -> bool:
    return str(side or "").strip().lower() in {"buy", "long", "buy-limit", "buy_limit"}


def _rates_dataframe(mt5: Any, symbol: str, timeframe: int, bars: int = 120) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) < max(30, bars // 4):
        raise RuntimeError("Not enough MT5 rate data for EMA bounce signal.")
    frame = pd.DataFrame(rates)
    if "time" not in frame or "close" not in frame:
        raise RuntimeError("MT5 rates are missing required OHLC columns.")
    return frame


def ema_bounce_signal(mt5: Any, cfg: TraderConfig) -> Optional[str]:
    timeframe = cfg.timeframe or mt5.TIMEFRAME_M5
    bars = max(cfg.fast_ema_period, cfg.slow_ema_period, cfg.trend_ema_period) + 50
    frame = _rates_dataframe(mt5, cfg.symbol, timeframe, bars=bars)
    closed = frame.iloc[-2]
    close_value = float(closed["close"])
    open_value = float(closed["open"])
    candle_bear = close_value < open_value
    candle_bull = close_value > open_value

    if cfg.use_dual_ema:
        fast = frame["close"].ewm(span=cfg.fast_ema_period, adjust=False).mean().iloc[-2]
        slow = frame["close"].ewm(span=cfg.slow_ema_period, adjust=False).mean().iloc[-2]
        uptrend = close_value > slow and fast > slow
        downtrend = close_value < slow and fast < slow
    else:
        trend = frame["close"].ewm(span=cfg.trend_ema_period, adjust=False).mean().iloc[-2]
        uptrend = close_value > trend
        downtrend = close_value < trend

    if uptrend and candle_bear:
        return "buy"
    if downtrend and candle_bull:
        return "sell"
    return None


def build_order_plan(
    mt5: Any,
    cfg: TraderConfig,
    risk: RiskConfig,
    order: OrderConfig,
    *,
    is_market: bool,
) -> dict[str, Any]:
    info = symbol_info_or_raise(mt5, cfg.symbol)
    is_buy = _is_buy_side(cfg.side)
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        raise RuntimeError("Bid/ask tick is unavailable.")

    if is_market:
        entry = float(get_attr(tick, "ask") if is_buy else get_attr(tick, "bid"))
    else:
        if cfg.entry_price <= 0:
            raise ValueError("Manual/standard limit mode requires --entry-price > 0.")
        entry = cfg.entry_price
    entry = normalize_price(info, entry)

    sl = build_sl_from_distance(info, entry, is_buy, order.sl_distance_points)
    volume, risk_rounded = compute_volume_from_risk(info, entry, sl, risk)
    if order.auto_tp_net_rr_enabled:
        tp, auto_tp_points, effective_rr = compute_auto_tp_net_rr(
            mt5,
            info,
            cfg.symbol,
            entry,
            is_buy,
            volume,
            risk_rounded,
            risk,
            order,
        )
    else:
        tp = build_tp_manual_from_distance(info, entry, is_buy, order.tp_distance_points)
        auto_tp_points = 0
        effective_rr = 0.0

    return {
        "is_buy": is_buy,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "volume": volume,
        "risk_rounded": risk_rounded,
        "auto_tp_points": auto_tp_points,
        "effective_net_rr": effective_rr,
    }


def place_market_ema_bounce(mt5: Any, cfg: TraderConfig, risk: RiskConfig, order: OrderConfig) -> Any:
    side = ema_bounce_signal(mt5, cfg)
    if side is None:
        return {"status": "no_signal"}
    cfg.side = side
    plan = build_order_plan(mt5, cfg, risk, order, is_market=True)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": plan["volume"],
        "type": mt5.ORDER_TYPE_BUY if plan["is_buy"] else mt5.ORDER_TYPE_SELL,
        "price": plan["entry"],
        "sl": plan["sl"],
        "tp": plan["tp"],
        "deviation": order.slippage_points,
        "magic": order.magic_number,
        "comment": order.comment,
        "type_time": mt5.ORDER_TIME_GTC,
    }
    return order_send_or_raise(mt5, request)


def place_pending_limit(mt5: Any, cfg: TraderConfig, risk: RiskConfig, order: OrderConfig) -> Any:
    plan = build_order_plan(mt5, cfg, risk, order, is_market=False)
    if cfg.pending_cancel_existing:
        cancel_pending_by_magic(mt5, cfg.symbol, order.magic_number, order.comment)
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": cfg.symbol,
        "volume": plan["volume"],
        "type": mt5.ORDER_TYPE_BUY_LIMIT if plan["is_buy"] else mt5.ORDER_TYPE_SELL_LIMIT,
        "price": plan["entry"],
        "sl": plan["sl"],
        "tp": plan["tp"],
        "deviation": order.slippage_points,
        "magic": order.magic_number,
        "comment": order.comment,
        "type_time": mt5.ORDER_TIME_GTC,
    }
    return order_send_or_raise(mt5, request)


def run_once(mt5: Any, cfg: TraderConfig, risk: RiskConfig, order: OrderConfig) -> Any:
    if not cfg.orders_enabled:
        return {"status": "orders_disabled"}
    symbol_info_or_raise(mt5, cfg.symbol)
    if cfg.enforce_one_trade_at_a_time and has_open_position(mt5, cfg.symbol):
        return {"status": "position_exists"}
    if cfg.strategy == StrategyMode.EMA_BOUNCE:
        return place_market_ema_bounce(mt5, cfg, risk, order)
    if cfg.strategy in {StrategyMode.MANUAL_LIMIT, StrategyMode.STANDARD_LIMIT}:
        return place_pending_limit(mt5, cfg, risk, order)
    raise ValueError(f"Unsupported strategy: {cfg.strategy}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", choices=[item.value for item in StrategyMode], default=StrategyMode.MANUAL_LIMIT.value)
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--entry-price", type=float, default=0.0)
    parser.add_argument("--risk-target", type=float, default=10.0)
    parser.add_argument("--risk-min", type=float, default=9.0)
    parser.add_argument("--risk-max", type=float, default=12.0)
    parser.add_argument("--sl-points", type=int, default=200)
    parser.add_argument("--tp-points", type=int, default=400)
    parser.add_argument("--manual-tp", action="store_true")
    parser.add_argument("--net-rr", type=float, default=2.0)
    parser.add_argument("--magic", type=int, default=91001)
    parser.add_argument("--allow-existing-position", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = TraderConfig(
        symbol=args.symbol,
        strategy=StrategyMode(args.strategy),
        side=args.side,
        entry_price=args.entry_price,
        enforce_one_trade_at_a_time=not args.allow_existing_position,
    )
    risk = RiskConfig(
        risk_aud_target=args.risk_target,
        risk_aud_min=args.risk_min,
        risk_aud_max=args.risk_max,
    )
    order = OrderConfig(
        sl_distance_points=args.sl_points,
        tp_distance_points=args.tp_points,
        auto_tp_net_rr_enabled=not args.manual_tp,
        net_rr_target=args.net_rr,
        magic_number=args.magic,
    )
    mt5 = initialize()
    try:
        result = run_once(mt5, cfg, risk, order)
        print(result)
        return 0
    finally:
        shutdown(mt5)


if __name__ == "__main__":
    raise SystemExit(main())
