
"""Simple crypto trade calculator.

This script fetches prices from the Bybit API and calculates position
sizes, stop prices and potential profit for a trade.  It also writes a
human readable summary and a small webhook payload to disk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

import requests

try:  # Optional dependency for CoinSpot support
    from coinspot import PublicAPIV2, ReadOnlyAPIV2
except ImportError:  # pragma: no cover - handled when CoinSpot features are used
    PublicAPIV2 = None  # type: ignore[assignment]
    ReadOnlyAPIV2 = None  # type: ignore[assignment]

# === BYBIT API URLs ===
BYBIT_SPOT_URL = "https://api.bybit.com/v5/market/tickers?category=spot"
BYBIT_LINEAR_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_INSTRUMENT_INFO_SPOT = (
    "https://api.bybit.com/v5/market/instruments-info?category=spot"
)
BYBIT_INSTRUMENT_INFO_LINEAR = (
    "https://api.bybit.com/v5/market/instruments-info?category=linear"
)

BYBIT_BALANCE_URL = "https://api.bybit.com/v5/account/wallet-balance"

# === DEFAULT EXCHANGE SETTINGS ===
DEFAULT_EXECUTION_EXCHANGE = "bybit"
DEFAULT_PRICE_SOURCE = "bybit_linear"

COINSPOT_MARKET_FEE_RATE = 0.001
COINSPOT_DEFAULT_TICK_SIZE = 0.01
COINSPOT_DEFAULT_MIN_QTY = 0.00000001
COINSPOT_DEFAULT_QTY_STEP = 0.00000001

PRICE_SOURCES = {
    "bybit_linear": {
        "label": "Bybit Linear Perpetual",
        "trade_mode": "linear",
    },
    "bybit_spot": {
        "label": "Bybit Spot",
        "trade_mode": "spot",
    },
    "coinspot_spot": {
        "label": "CoinSpot Spot",
        "trade_mode": "spot",
    },
}

EXECUTION_EXCHANGES = {
    "bybit": {
        "label": "Bybit",
        "supported_trade_modes": {"linear", "spot"},
    },
    "coinspot": {
        "label": "CoinSpot",
        "supported_trade_modes": {"linear", "spot"},
    },
}

TRADE_MODE_LABELS = {"linear": "Linear Perpetual", "spot": "Spot"}


def _require_coinspot_dependency(feature: str) -> None:
    """Ensure the optional CoinSpot dependency is available."""

    if PublicAPIV2 is None or ReadOnlyAPIV2 is None:
        raise RuntimeError(
            f"CoinSpot {feature} requires the 'coinspot' package. Install it with 'pip install coinspot'."
        )


def _split_symbol(symbol: str) -> Tuple[str, str]:
    """Split a combined symbol (e.g. ``BTCUSDT``) into base/quote parts."""

    upper = symbol.replace("_", "").upper()
    for quote in ("USDT", "AUD", "USD", "BTC", "ETH"):
        if upper.endswith(quote):
            base = upper[: -len(quote)]
            if not base:
                break
            return base, quote
    raise ValueError(f"Unable to determine base/quote for symbol '{symbol}'.")


def format_coinspot_symbol(symbol: str) -> str:
    """Return a CoinSpot-compatible market string (``btc_usdt``)."""

    if "_" in symbol:
        return symbol.lower()
    base, quote = _split_symbol(symbol)
    return f"{base.lower()}_{quote.lower()}"


def fetch_coinspot_price(symbol: str) -> float:
    """Return the latest CoinSpot market price for ``symbol``."""

    _require_coinspot_dependency("pricing")
    pair = format_coinspot_symbol(symbol)
    api = PublicAPIV2()
    resp = api.latest(pair)
    if resp.get("status") != "ok":
        raise ValueError(f"CoinSpot returned error for {pair}: {resp}")
    prices = resp.get("prices")
    if not prices:
        raise ValueError(f"CoinSpot price data missing for {pair}.")
    return float(prices.get("last"))


def fetch_coinspot_tick_size(_symbol: str) -> float:
    """Return the assumed tick size for CoinSpot spot markets."""

    return COINSPOT_DEFAULT_TICK_SIZE


def fetch_coinspot_lot_info(_symbol: str) -> Tuple[float, float]:
    """Return the minimum quantity and step for CoinSpot spot execution."""

    return COINSPOT_DEFAULT_MIN_QTY, COINSPOT_DEFAULT_QTY_STEP


def fetch_coinspot_account_balance(asset: str = "USDT") -> float:
    """Return the CoinSpot wallet balance for ``asset`` using API credentials."""

    _require_coinspot_dependency("balances")

    api_key = os.environ.get("COINSPOT_API_KEY")
    api_secret = os.environ.get("COINSPOT_API_SECRET")
    if not api_key or not api_secret:
        raise EnvironmentError("COINSPOT_API_KEY and COINSPOT_API_SECRET must be set")

    api = ReadOnlyAPIV2(api_key, api_secret)
    balances = api.wallet_balance().get("balances", [])
    target = asset.upper()
    for entry in balances:
        for code, data in entry.items():
            if code.upper() != target:
                continue
            balance = data.get("balance") or data.get("available") or data.get("audbalance")
            if balance is None:
                continue
            return float(balance)
    raise ValueError(f"Balance for {asset} not found on CoinSpot.")


def get_price_source_info(price_source: str, symbol: str) -> Tuple[str, float, float, float | None]:
    """Return trade mode, price, tick size, and funding rate for ``price_source``."""

    meta = PRICE_SOURCES.get(price_source)
    if meta is None:
        raise ValueError(f"Unknown price source '{price_source}'.")
    trade_mode = meta["trade_mode"]
    if price_source.startswith("bybit_"):
        market_price = fetch_current_price(symbol, trade_mode)
        tick_size = fetch_tick_size(symbol, trade_mode)
        funding_rate = fetch_funding_rate(symbol) if trade_mode == "linear" else None
        return trade_mode, market_price, tick_size, funding_rate
    if price_source == "coinspot_spot":
        market_price = fetch_coinspot_price(symbol)
        tick_size = fetch_coinspot_tick_size(symbol)
        return trade_mode, market_price, tick_size, None
    raise ValueError(f"Price source '{price_source}' is not implemented.")


def fetch_bybit_instrument(symbol: str, trade_mode: str) -> Dict[str, Any]:
    """Return instrument information for a Bybit symbol."""

    info_url = (
        BYBIT_INSTRUMENT_INFO_SPOT if trade_mode == "spot" else BYBIT_INSTRUMENT_INFO_LINEAR
    )
    resp = requests.get(info_url, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    instr_list = resp.json().get("result", {}).get("list", [])
    if not instr_list:
        raise ValueError(f"Instrument {symbol} not found.")
    return instr_list[0]


def get_execution_requirements(
    execution_exchange: str, symbol: str, trade_mode: str
) -> Tuple[float, float, float]:
    """Return ``(min_qty, qty_step, fee_rate)`` for the execution venue."""

    if execution_exchange == "bybit":
        instr = fetch_bybit_instrument(symbol, trade_mode)
        lot = instr.get("lotSizeFilter", {})
        min_qty = float(lot.get("minTrdQty", lot.get("minOrderQty", 0)))
        qty_step = float(lot.get("qtyStep", lot.get("qty_step", min_qty or 1)))
        fee_rate = (
            SPOT_TRADING_FEE_RATE if trade_mode == "spot" else LINEAR_TRADING_FEE_RATE
        )
        return min_qty, qty_step, fee_rate
    if execution_exchange == "coinspot":
        min_qty, qty_step = fetch_coinspot_lot_info(symbol)
        return min_qty, qty_step, COINSPOT_MARKET_FEE_RATE
    raise ValueError(f"Execution exchange '{execution_exchange}' is not implemented.")


def get_balance_fetcher(execution_exchange: str):
    """Return a callable that fetches balances for ``execution_exchange``."""

    if execution_exchange == "bybit":

        def _fetch(asset: str = "USDT", **kwargs: Any) -> float:
            account_type = kwargs.get("account_type", "UNIFIED")
            return fetch_account_balance(asset, account_type)

        return _fetch
    if execution_exchange == "coinspot":

        def _fetch(asset: str = "USDT", **_kwargs: Any) -> float:
            return fetch_coinspot_account_balance(asset)

        return _fetch
    raise ValueError(f"Execution exchange '{execution_exchange}' is not supported for balances.")


def resolve_price_source(
    price_source: str | None, execution_exchange: str, trade_mode: str | None
) -> str:
    """Infer the price source when one is not explicitly provided."""

    if price_source and price_source in PRICE_SOURCES:
        return price_source

    if execution_exchange == "coinspot":
        return "coinspot_spot"

    if trade_mode == "spot":
        return "bybit_spot"

    return DEFAULT_PRICE_SOURCE

# === FEES & INTEREST SETTINGS ===
SPOT_TRADING_FEE_RATE = 0.001
LINEAR_TRADING_FEE_RATE = 0.0006
SPOT_INTEREST_RATE_PER_HOUR = 0.000084

def fetch_current_price(symbol: str, mode: str) -> float:
    """Return the latest price for ``symbol`` from the Bybit API."""

    url = BYBIT_SPOT_URL if mode == "spot" else BYBIT_LINEAR_URL
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    for ticker in resp.json().get("result", {}).get("list", []):
        if ticker.get("symbol") == symbol:
            return float(ticker.get("lastPrice"))
    raise ValueError(f"Symbol {symbol} not found.")

def fetch_tick_size(symbol: str, mode: str) -> float:
    """Return the tick size for ``symbol``."""

    url = (
        BYBIT_INSTRUMENT_INFO_SPOT if mode == "spot" else BYBIT_INSTRUMENT_INFO_LINEAR
    )
    resp = requests.get(url, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    for instr in resp.json().get("result", {}).get("list", []):
        if instr.get("symbol") == symbol:
            return float(instr.get("priceFilter", {}).get("tickSize", 0))
    raise ValueError(f"Tick size for {symbol} not found.")


def fetch_funding_rate(symbol: str) -> float:
    """Return the latest funding rate for ``symbol``."""

    resp = requests.get(BYBIT_LINEAR_URL, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    for ticker in resp.json().get("result", {}).get("list", []):
        if ticker.get("symbol") == symbol:
            return float(ticker.get("fundingRate", 0.0))
    raise ValueError(f"Funding rate for {symbol} not found.")


def fetch_account_balance(coin: str = "USDT", account_type: str = "UNIFIED") -> float:
    """Return available balance for ``coin`` using API credentials."""

    api_key = os.environ.get("BYBIT_API_KEY")
    api_secret = os.environ.get("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        raise EnvironmentError("BYBIT_API_KEY and BYBIT_API_SECRET must be set")

    params = {"accountType": account_type, "coin": coin}
    query = urlencode(params)
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    to_sign = f"{timestamp}{api_key}{recv_window}{query}"
    signature = hmac.new(api_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
    }

    url = f"{BYBIT_BALANCE_URL}?{query}"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()

    for item in resp.json().get("result", {}).get("list", []):
        for bal in item.get("coin", []):
            if bal.get("coin") == coin:
                return float(bal.get("availableToTrade", bal.get("walletBalance", 0)))
    raise ValueError(f"Balance for {coin} not found.")

def load_config(filename: str) -> Dict[str, Any]:
    """Load the trade configuration from ``filename``."""

    with open(filename, "r", encoding="utf-8") as f:
        config = json.load(f)

    execution_exchange = config.get("execution_exchange", DEFAULT_EXECUTION_EXCHANGE)
    if execution_exchange not in EXECUTION_EXCHANGES:
        raise ValueError(f"Unknown execution exchange '{execution_exchange}'.")

    trade_mode = config.get("trade_mode")
    price_source = resolve_price_source(
        config.get("price_source"), execution_exchange, trade_mode
    )
    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]

    config["execution_exchange"] = execution_exchange
    config["price_source"] = price_source
    config["trade_mode"] = trade_mode

    if str(config.get("account_balance", "")).lower() == "auto":
        balance_fetcher = get_balance_fetcher(execution_exchange)
        asset = config.get("account_asset", "USDT")
        account_type = config.get("account_type", "UNIFIED")
        config["account_balance"] = balance_fetcher(asset, account_type=account_type)

    return config

def calculate_trade(config: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate trade parameters based on ``config``.

    The target price is adjusted so that the expected net profit is at least
    ``rr_ratio`` times the actual risk after rounding the position size.
    """
    # pylint: disable=too-many-locals

    execution_exchange = config.get("execution_exchange", DEFAULT_EXECUTION_EXCHANGE)
    if execution_exchange not in EXECUTION_EXCHANGES:
        raise ValueError(f"Unknown execution exchange '{execution_exchange}'.")

    price_source = resolve_price_source(
        config.get("price_source"), execution_exchange, config.get("trade_mode")
    )

    trade_mode, market_price, tick_size, funding_rate = get_price_source_info(
        price_source, config["symbol"]
    )
    config["trade_mode"] = trade_mode
    config["price_source"] = price_source

    entry_price = (
        float(config.get("entry_price", market_price))
        if config["order_type"] == "limit"
        else market_price
    )

    min_qty, qty_step, fee_rate = get_execution_requirements(
        execution_exchange, config["symbol"], trade_mode
    )
    config["execution_exchange"] = execution_exchange

    risk_amount = config["account_balance"] * (config["risk_percent"] / 100)
    stop_distance = config["stop_loss_ticks"] * tick_size

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
        "execution_exchange": execution_exchange,
        "price_source": price_source,
        "trade_mode": trade_mode,
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
    exec_label = EXECUTION_EXCHANGES.get(trade.get("execution_exchange"), {}).get(
        "label", trade.get("execution_exchange", "-")
    )
    price_label = PRICE_SOURCES.get(trade.get("price_source"), {}).get(
        "label", trade.get("price_source", "-")
    )
    trade_mode_label = TRADE_MODE_LABELS.get(trade.get("trade_mode"), trade.get("trade_mode", "-"))
    lines.append(f"Execution:        {exec_label}")
    lines.append(f"Price Source:     {price_label}")
    lines.append(f"Price Mode:       {trade_mode_label}")
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

if __name__=="__main__":
    main()
