"""External Python research backtest for the Backtest.mq5 pullback logic.

This is not the MT5 Strategy Tester. It is a pandas-based EMA/ATR approximation
that uses Python MT5 rate history for research and CSV output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from mt5_common import RiskConfig, ensure_utc, initialize, shutdown, window_contains_time


@dataclass
class BacktestConfig:
    symbol: str
    timeframe: int
    start: datetime
    end: datetime
    use_dual_ema: bool = True
    fast_ema_period: int = 9
    slow_ema_period: int = 20
    trend_ema_period: int = 20
    atr_period: int = 14
    atr_multiple: float = 1.5
    risk_reward: float = 2.0
    auto_tp_net_rr_enabled: bool = True
    net_rr_target: float = 2.0
    close_during_blackout: bool = True
    use_aedt: bool = False
    blackout_start_hour_aest: int = 2
    blackout_end_hour_aest: int = 6
    blackout_start_hour_aedt: int = 3
    blackout_end_hour_aedt: int = 7
    use_rollover_window: bool = True
    close_before_rollover: bool = True
    rollover_start_hour: int = 23
    rollover_start_minute: int = 55
    rollover_end_hour: int = 0
    rollover_end_minute: int = 10


def fetch_rates(mt5: Any, cfg: BacktestConfig) -> pd.DataFrame:
    start = ensure_utc(cfg.start)
    end = ensure_utc(cfg.end)
    rates = mt5.copy_rates_range(cfg.symbol, cfg.timeframe, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No rates returned by copy_rates_range() for {cfg.symbol}.")
    frame = pd.DataFrame(rates)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def add_indicators(frame: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    result = frame.copy()
    result["fast_ema"] = result["close"].ewm(span=cfg.fast_ema_period, adjust=False).mean()
    result["slow_ema"] = result["close"].ewm(span=cfg.slow_ema_period, adjust=False).mean()
    result["trend_ema"] = result["close"].ewm(span=cfg.trend_ema_period, adjust=False).mean()
    previous_close = result["close"].shift(1)
    tr_components = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    result["atr"] = tr_components.max(axis=1).rolling(cfg.atr_period).mean()
    return result


def is_blackout(t: pd.Timestamp, cfg: BacktestConfig) -> bool:
    hour = int(t.hour)
    start = cfg.blackout_start_hour_aedt if cfg.use_aedt else cfg.blackout_start_hour_aest
    end = cfg.blackout_end_hour_aedt if cfg.use_aedt else cfg.blackout_end_hour_aest
    return start <= hour < end


def is_rollover(t: pd.Timestamp, cfg: BacktestConfig) -> bool:
    if not cfg.use_rollover_window:
        return False
    return window_contains_time(
        t.to_pydatetime(),
        cfg.rollover_start_hour,
        cfg.rollover_start_minute,
        cfg.rollover_end_hour,
        cfg.rollover_end_minute,
    )


def get_signal(row: pd.Series, cfg: BacktestConfig) -> Optional[str]:
    candle_bear = float(row["close"]) < float(row["open"])
    candle_bull = float(row["close"]) > float(row["open"])
    if cfg.use_dual_ema:
        uptrend = float(row["close"]) > float(row["slow_ema"]) and float(row["fast_ema"]) > float(row["slow_ema"])
        downtrend = float(row["close"]) < float(row["slow_ema"]) and float(row["fast_ema"]) < float(row["slow_ema"])
    else:
        uptrend = float(row["close"]) > float(row["trend_ema"])
        downtrend = float(row["close"]) < float(row["trend_ema"])
    if uptrend and candle_bear:
        return "buy"
    if downtrend and candle_bull:
        return "sell"
    return None


def build_order_params(row: pd.Series, signal: str, cfg: BacktestConfig, risk: RiskConfig) -> dict[str, float | str]:
    entry = float(row["close"])
    atr = float(row["atr"])
    if not pd.notna(atr) or atr <= 0:
        raise ValueError("ATR unavailable.")
    sl_distance = atr * cfg.atr_multiple
    rr = max(2.0, cfg.net_rr_target if cfg.auto_tp_net_rr_enabled else cfg.risk_reward)
    if signal == "buy":
        sl = entry - sl_distance
        tp = entry + sl_distance * rr
    else:
        sl = entry + sl_distance
        tp = entry - sl_distance * rr
    return {
        "side": signal,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_target": risk.risk_aud_target,
    }


def run_research_backtest(frame: pd.DataFrame, cfg: BacktestConfig, risk: RiskConfig) -> tuple[pd.DataFrame, dict[str, float]]:
    data = add_indicators(frame, cfg)
    trades: list[dict[str, object]] = []
    in_position: Optional[dict[str, object]] = None

    for idx in range(max(cfg.atr_period, cfg.slow_ema_period, cfg.trend_ema_period) + 1, len(data)):
        row = data.iloc[idx]
        now = row["time"]
        if in_position and cfg.close_during_blackout and is_blackout(now, cfg):
            in_position["exit_time"] = now
            in_position["exit_price"] = float(row["open"])
            in_position["exit_reason"] = "blackout"
            trades.append(in_position)
            in_position = None
            continue
        if in_position and cfg.close_before_rollover and is_rollover(now, cfg):
            in_position["exit_time"] = now
            in_position["exit_price"] = float(row["open"])
            in_position["exit_reason"] = "rollover"
            trades.append(in_position)
            in_position = None
            continue
        if in_position:
            side = in_position["side"]
            hit_tp = float(row["high"]) >= float(in_position["tp"]) if side == "buy" else float(row["low"]) <= float(in_position["tp"])
            hit_sl = float(row["low"]) <= float(in_position["sl"]) if side == "buy" else float(row["high"]) >= float(in_position["sl"])
            if hit_sl or hit_tp:
                in_position["exit_time"] = now
                in_position["exit_price"] = in_position["sl"] if hit_sl else in_position["tp"]
                in_position["exit_reason"] = "sl" if hit_sl else "tp"
                trades.append(in_position)
                in_position = None
            continue
        if is_blackout(now, cfg) or is_rollover(now, cfg):
            continue
        signal = get_signal(data.iloc[idx - 1], cfg)
        if signal is None:
            continue
        try:
            params = build_order_params(data.iloc[idx - 1], signal, cfg, risk)
        except ValueError:
            continue
        in_position = {
            "open_time": now,
            **params,
        }

    trades_frame = pd.DataFrame(trades)
    if trades_frame.empty:
        return trades_frame, {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0}
    trades_frame["gross_r"] = trades_frame.apply(
        lambda row: (
            (float(row["exit_price"]) - float(row["entry"])) / abs(float(row["entry"]) - float(row["sl"]))
            if row["side"] == "buy"
            else (float(row["entry"]) - float(row["exit_price"])) / abs(float(row["entry"]) - float(row["sl"]))
        ),
        axis=1,
    )
    wins = int((trades_frame["gross_r"] > 0).sum())
    losses = int((trades_frame["gross_r"] <= 0).sum())
    summary = {
        "trades": float(len(trades_frame)),
        "wins": float(wins),
        "losses": float(losses),
        "win_rate": wins / len(trades_frame),
        "total_gross_r": float(trades_frame["gross_r"].sum()),
    }
    return trades_frame, summary


def parse_dt(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--start", required=True, help="ISO timestamp, UTC assumed if naive")
    parser.add_argument("--end", required=True, help="ISO timestamp, UTC assumed if naive")
    parser.add_argument("--output", default="python_mt5_backtest.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mt5 = initialize()
    try:
        timeframe_value = getattr(mt5, f"TIMEFRAME_{args.timeframe.upper()}", mt5.TIMEFRAME_M5)
        cfg = BacktestConfig(
            symbol=args.symbol,
            timeframe=timeframe_value,
            start=parse_dt(args.start),
            end=parse_dt(args.end),
        )
        frame = fetch_rates(mt5, cfg)
        trades, summary = run_research_backtest(frame, cfg, RiskConfig())
        output = Path(args.output)
        trades.to_csv(output, index=False)
        print(summary)
        print(f"Wrote {output}")
        return 0
    finally:
        shutdown(mt5)


if __name__ == "__main__":
    raise SystemExit(main())
