"""Simple crypto trade calculator with exchange adapters."""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Optional

from exchange_adapters import (
    ExchangeAdapter,
    InstrumentInfo,
    get_exchange_adapter,
)


def fetch_account_balance(
    coin: str = "USDT",
    account_type: str = "UNIFIED",
    exchange: str = "bybit",
) -> float:
    """Return the spendable balance using the configured exchange adapter."""

    adapter = get_exchange_adapter(exchange)
    config = {
        "account_coin": coin,
        "account_type": account_type,
        "exchange": exchange,
    }
    return adapter.get_account_balance(config)


def load_config(filename: str) -> Dict[str, Any]:
    """Load the trade configuration from ``filename``."""

    with open(filename, "r", encoding="utf-8") as f:
        config = json.load(f)

    config.setdefault("exchange", "bybit")

    if str(config.get("account_balance", "")).lower() == "auto":
        adapter = get_exchange_adapter(config["exchange"])
        config["account_balance"] = adapter.get_account_balance(config)

    return config


def calculate_trade(
    config: Dict[str, Any], adapter: Optional[ExchangeAdapter] = None
) -> Dict[str, Any]:
    """Calculate trade parameters based on ``config``.

    The target price is adjusted so that the expected net profit is at least
    ``rr_ratio`` times the actual risk after rounding the position size.
    """
    # pylint: disable=too-many-locals

    exchange_name = config.get("exchange", "bybit")
    adapter = adapter or get_exchange_adapter(exchange_name)

    market_price = adapter.get_current_price(
        config["symbol"], config["trade_mode"], config
    )
    instrument: InstrumentInfo = adapter.get_instrument_info(
        config["symbol"], config["trade_mode"], config
    )
    tick_size = instrument.tick_size
    min_qty = instrument.min_qty
    qty_step = instrument.qty_step
    funding_rate = adapter.get_funding_rate(
        config["symbol"], config["trade_mode"], config
    )
    entry_price = (
        float(config.get("entry_price", market_price))
        if config["order_type"] == "limit"
        else market_price
    )

    risk_amount = config["account_balance"] * (config["risk_percent"] / 100)
    stop_distance = config["stop_loss_ticks"] * tick_size
    fee_rate = adapter.get_fee_rate(config["trade_mode"])

    # Determine the stop price so we can include entry and exit fees in the risk
    if config["direction"] == "long":
        stop_price = entry_price - stop_distance
    else:
        stop_price = entry_price + stop_distance

    per_unit_risk = stop_distance / entry_price
    # Approximate quantity such that price risk + fees ~= risk_amount
    net_per_unit_loss = stop_distance + fee_rate * (entry_price + stop_price)
    raw_qty = risk_amount / net_per_unit_loss
    steps = max(1, round(raw_qty / qty_step))
    quantity = steps * qty_step
    if quantity < min_qty:
        raise ValueError(f"Quantity {quantity:.6f} below minimum {min_qty}")

    actual_usdt = quantity * entry_price

    # Calculate risk including fees at the stop price
    entry_fee_stop = entry_price * quantity * fee_rate
    exit_fee_stop = stop_price * quantity * fee_rate
    actual_risk = quantity * stop_distance + entry_fee_stop + exit_fee_stop

    if config["direction"] == "long":
        target_price = entry_price + (stop_distance * config["rr_ratio"])
    else:
        target_price = entry_price - (stop_distance * config["rr_ratio"])

    interest = 0.0

    # Ensure the final target provides at least the desired risk/reward
    # based on the actual risk after rounding the position size.
    min_profit = actual_risk * config["rr_ratio"]

    diff_required = (
        min_profit + (2 * actual_usdt * fee_rate) + interest
    ) / (quantity * (1 - fee_rate))

    ticks = math.ceil(diff_required / tick_size)
    target_price = (
        entry_price + ticks * tick_size
        if config["direction"] == "long"
        else entry_price - ticks * tick_size
    )

    gross = abs(target_price - entry_price) * quantity
    entry_fee = actual_usdt * fee_rate
    exit_fee = target_price * quantity * fee_rate
    fees = entry_fee + exit_fee
    net_profit = gross - fees - interest

    return {
        "symbol": config["symbol"],
        "direction": config["direction"],
        "order_type": config["order_type"],
        "account_balance": config["account_balance"],
        "risk_percent": config["risk_percent"],
        "risk_amount": risk_amount,
        "entry_price": entry_price,
        "position_usdt": actual_usdt,
        "quantity": quantity,
        "stop_loss_ticks": config["stop_loss_ticks"],
        "stop_distance": stop_distance,
        "stop_price": stop_price,
        "target_price": target_price,
        "gross_reward": abs(target_price - entry_price) * quantity,
        "net_profit": net_profit,
        "fees": fees,
        "interest_cost": interest,
        "funding_rate": funding_rate,
        "actual_risk": actual_risk,
        "rr_ratio": config["rr_ratio"],
        "achieved_rr": net_profit / actual_risk if actual_risk else 0.0,
        "per_unit_risk": per_unit_risk,
    }


def format_trade(trade: Dict[str, Any]) -> str:
    """Return a formatted multi-line summary for ``trade``."""

    lines: list[str] = []
    lines.append("=" * 50)
    lines.append("              CRYPTO TRADE CALCULATOR")
    lines.append("=" * 50)
    lines.append("")
    lines.append("                 TRADE PARAMETERS")
    lines.append("-" * 50)
    lines.append(f"Symbol:           {trade['symbol']}")
    lines.append(f"Direction:        {trade['direction'].capitalize()}")
    lines.append(f"Order Type:       {trade['order_type'].capitalize()}")
    lines.append(f"Account Balance:  {trade['account_balance']:.2f} USDT")
    lines.append(f"Risk Percent:     {trade['risk_percent']}%")
    lines.append(f"Risk Amount:      {trade['risk_amount']:.6f} USDT")
    lines.append(f"Entry Price:      {trade['entry_price']:.6f} USDT")
    lines.append("")
    lines.append("                 POSITION DETAILS")
    lines.append("-" * 50)
    lines.append(f"Position Size:     {trade['position_usdt']:.2f} USDT")
    lines.append(
        f"Quantity:          {trade['quantity']:.3f} {trade['symbol'].replace('USDT', '')}"
    )
    lines.append(f"Stop Loss Ticks:   {trade['stop_loss_ticks']}")
    target_distance = trade["target_price"] - trade["entry_price"]
    lines.append(f"Stop Distance:     {trade['stop_distance']:.6f} USDT")
    lines.append(f"Target Distance:   {target_distance:.6f} USDT")
    lines.append(f"Stop Price:        {trade['stop_price']:.6f} USDT")
    lines.append(f"Target Price:      {trade['target_price']:.6f} USDT")
    lines.append("")
    lines.append("                 RISK/REWARD")
    lines.append("-" * 50)
    lines.append(f"Gross Reward:     {trade['gross_reward']:.6f} USDT")
    lines.append(f"Net Profit:       {trade['net_profit']:.6f} USDT")
    lines.append(f"Actual Risk:      {trade['actual_risk']:.6f} USDT")
    lines.append(f"Achieved RR:     {trade['achieved_rr']:.2f}x")
    lines.append("")
    lines.append("                 COST BREAKDOWN")
    lines.append("-" * 50)
    lines.append(f"Fees:            {trade['fees']:.6f} USDT")
    lines.append(f"Interest Cost:   {trade['interest_cost']:.6f} USDT")
    if trade.get("funding_rate") is not None:
        lines.append(f"Funding Rate:    {trade['funding_rate'] * 100:.4f}%")
    return "\n".join(lines)


def display_trade(trade: Dict[str, Any]) -> str:
    """Print ``trade`` summary to the console and return it."""

    text = format_trade(trade)
    print(text)
    return text


def build_webhook_payload(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Return the SignalStack webhook payload for ``trade``."""

    action = "buy" if trade["direction"] == "long" else "sell"
    stop_dist = trade["stop_distance"]
    target_dist = abs(trade["target_price"] - trade["entry_price"])

    tp_op = "+" if action == "buy" else "-"
    sl_op = "-" if action == "buy" else "+"

    return {
        "symbol": trade["symbol"],
        "action": action,
        "quantity": round(trade["quantity"], 3),
        "take_profit_price": f"{{{{close}}}} {tp_op} {target_dist:.6f}",
        "stop_loss_price": f"{{{{close}}}} {sl_op} {stop_dist:.6f}",
    }


def save_summary(text: str) -> None:
    """Write ``text`` to ``trade_summary.txt``."""

    path = os.path.join(os.getcwd(), "trade_summary.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n✅ Summary saved as '{path}'")


def save_webhook_json(trade: Dict[str, Any]) -> None:
    """Write a small webhook payload for ``trade``."""

    payload = build_webhook_payload(trade)
    with open("trade_webhook.txt", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write(
            "\n\nWEBHOOK FUTURES:\n"
            "https://app.signalstack.com/hook/6vSSkN1tYQLj3C1H3YQqpz\n"
        )
    print("\n✅ Webhook JSON saved as 'trade_webhook.txt'")


def main() -> None:
    """Entry point for the script."""

    config = load_config("config.json")
    trade = calculate_trade(config)
    summary = display_trade(trade)
    save_summary(summary)
    save_webhook_json(trade)


if __name__ == "__main__":
    main()
