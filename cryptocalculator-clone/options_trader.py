"""Options trading and exposure helper for the Crypto Calculator."""
from __future__ import annotations

import argparse
import csv
import io
import hmac
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from tabulate import tabulate

from bybit_credentials import resolve_bybit_credentials


script_dir_path = Path(__file__).resolve().parent
script_dir = str(script_dir_path)

TRADE_LOG_DIR = os.getenv("OPTIONSTRADER_LOG_DIR") or str(
    script_dir_path / "trade_logs"
)
log_file = script_dir_path / "options_trader.log"
output_file = script_dir_path / "options_trade_output.txt"

logger = logging.getLogger("options_trader")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    fh = logging.FileHandler(log_file, mode="w")
    ch = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False

logger.info("Options trader helper initialized; logs to %s", log_file)

TRADING_ENV = "live"
BASE_URL = "https://api.bybit.com"
LOADED_ENV_FILES: list[str] = []

RECV_WINDOW = "5000"
SUB_ACCOUNT_NAME = ""
MIN_BALANCE_THRESHOLD = 10.0
DEMO_BALANCE = float(os.getenv("DEMO_BALANCE", 0.0))
MIN_ORDER_QTY = 0.01
DEFAULT_OPTION_BASES = ["BTC", "ETH", "SOL", "XRP", "MNT", "DOGE"]


def _normalize_env_choice(choice: str) -> str:
    cleaned = (choice or "").strip().lower()
    return "demo" if cleaned == "demo" else "live"


def choose_trading_environment(interactive: bool = False) -> str:
    """Return the requested trading environment without prompting."""

    env_choice = os.getenv("OPTIONSTRADER_ENV")
    if env_choice:
        return _normalize_env_choice(env_choice)

    bybit_env = os.getenv("BYBIT_ENV")
    if bybit_env:
        return "demo" if bybit_env.strip().lower() in {"demo", "testnet", "paper"} else "live"

    return "live"


def configure_trading_environment(interactive: bool = False) -> None:
    """Configure API base URL and env-based credentials for the chosen mode."""

    global BASE_URL, TRADING_ENV, LOADED_ENV_FILES

    TRADING_ENV = choose_trading_environment(interactive)
    mode, _key, _secret, base_url, key_source = resolve_bybit_credentials()
    BASE_URL = base_url
    LOADED_ENV_FILES = []
    logger.info(
        "Using %s environment; credentials from env (source=%s, mode=%s).",
        TRADING_ENV,
        key_source,
        mode,
    )


def get_base_url() -> str:
    """Return the API base URL for the active environment."""

    return BASE_URL


configure_trading_environment(interactive=False)


def print_and_write(lines: Iterable[str]) -> None:
    """Print to console and write to output file."""

    with open(output_file, "w", encoding="utf-8") as out:
        for line in lines:
            print(line)
            out.write(f"{line}\n")


def load_trade_config(path: str) -> dict:
    """Load and validate trade configuration from a JSON file."""

    path = os.path.expanduser(os.path.expandvars(path))
    candidate = path
    if not os.path.isabs(candidate) and not os.path.exists(candidate):
        candidate = os.path.join(script_dir, candidate)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"Trade config file not found: {path}")
    with open(candidate, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("auto_trade", False)
    cfg.setdefault("risk_usd", 0)
    for field in ("symbol", "side", "quantity"):
        if field not in cfg or cfg[field] in (None, ""):
            raise ValueError(f"Missing required field in config: {field}")
    return cfg


def get_api_credentials(cfg: dict) -> tuple[str, str]:
    """Return API credentials from environment variables or config."""

    _mode, key, secret, _base_url, _key_source = resolve_bybit_credentials()
    key = cfg.get("api_key", "") or key
    secret = cfg.get("api_secret", "") or secret
    return key, secret


def get_telegram_credentials(cfg: dict) -> tuple[str, str]:
    """Return Telegram bot token and chat id from env or config."""

    token = os.getenv("TELEGRAM_TOKEN") or cfg.get("telegram_token", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id", "")
    return token, chat_id


def send_telegram_document(path: str, token: str, chat_id: str, caption: str | None = None) -> None:
    """Send a file to a Telegram chat using the Bot API."""

    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    try:
        with open(path, "rb") as doc:
            requests.post(url, data=data, files={"document": doc}, timeout=10)
        logger.info("Sent %s to Telegram chat %s", path, chat_id)
    except Exception as exc:  # pragma: no cover - network guard
        logger.error("Failed to send Telegram document: %s", exc)


# === Greek fetching via public market endpoint ===

def fetch_option_ticker(symbol: str, base_url: str | None = None) -> dict:
    """Return ticker data for a given option symbol."""

    base_url = base_url or get_base_url()
    endpoint = "/v5/market/tickers"
    params = {"category": "option", "symbol": symbol}
    qs = urlencode(params)
    url = f"{base_url}{endpoint}?{qs}"
    logger.debug("Fetching ticker: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Ticker response: %s", data)
    if data.get("retCode") != 0:
        raise RuntimeError(f"API Error {data['retCode']}: {data.get('retMsg')}")
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"No ticker data for symbol: {symbol}")
    return lst[0]


def fetch_option_instruments(
    base_coin: str = "BTC",
    expiry: str | None = None,
    option_type: str | None = None,
    base_url: str | None = None,
) -> list[dict]:
    """Return a list of option symbols for the given filters."""

    base_url = base_url or get_base_url()
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "baseCoin": base_coin}
    if expiry:
        params["expDate"] = expiry
    if option_type:
        opt = option_type
        if opt.upper() in ("P", "PUT"):
            opt = "Put"
        elif opt.upper() in ("C", "CALL"):
            opt = "Call"
        params["optionType"] = opt

    instruments = []
    cursor = None
    while True:
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        if cursor:
            qs += f"&cursor={cursor}"
        url = f"{base_url}{endpoint}?{qs}"
        logger.debug("Fetching instruments: %s", url)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Instruments response: %s", data)
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"API Error {data['retCode']}: {data.get('retMsg')}"
            )
        instruments.extend(data.get("result", {}).get("list", []))
        cursor = data.get("result", {}).get("nextPageCursor")
        if not cursor:
            break
    return instruments


_tick_size_cache: dict[str, float] = {}
_min_qty_cache: dict[str, float] = {}


def get_tick_size(symbol: str, base_url: str | None = None) -> float:
    """Return the minimum price increment for ``symbol``."""

    base_url = base_url or get_base_url()
    if symbol in _tick_size_cache:
        return _tick_size_cache[symbol]
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "symbol": symbol}
    qs = urlencode(params)
    url = f"{base_url}{endpoint}?{qs}"
    logger.debug("Fetching tick size: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Tick size response: %s", data)
    if data.get("retCode") != 0:
        raise RuntimeError(
            f"API Error {data['retCode']}: {data.get('retMsg')}"
        )
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"No instrument data for symbol: {symbol}")
    tick = float(lst[0].get("priceFilter", {}).get("tickSize", 0))
    _tick_size_cache[symbol] = tick
    return tick


def get_min_order_qty(symbol: str, base_url: str | None = None) -> float:
    """Return the minimum order quantity for ``symbol``."""

    base_url = base_url or get_base_url()
    if symbol in _min_qty_cache:
        return _min_qty_cache[symbol]
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "symbol": symbol}
    qs = urlencode(params)
    url = f"{base_url}{endpoint}?{qs}"
    logger.debug("Fetching min order qty: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Min order qty response: %s", data)
    if data.get("retCode") != 0:
        raise RuntimeError(
            f"API Error {data['retCode']}: {data.get('retMsg')}"
        )
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"No instrument data for symbol: {symbol}")
    min_qty = float(
        lst[0].get("lotSizeFilter", {}).get("minOrderQty", MIN_ORDER_QTY)
    )
    _min_qty_cache[symbol] = min_qty
    return min_qty


def get_supported_option_bases(base_url: str | None = None) -> list[str]:
    """Return available base coins for options."""

    base_url = base_url or get_base_url()
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option"}
    instruments = []
    cursor = None
    try:
        while True:
            qs = urlencode({k: v for k, v in params.items() if v is not None})
            if cursor:
                qs += f"&cursor={cursor}"
            url = f"{base_url}{endpoint}?{qs}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                break
            instruments.extend(data.get("result", {}).get("list", []))
            cursor = data.get("result", {}).get("nextPageCursor")
            if not cursor:
                break
    except requests.RequestException:
        return DEFAULT_OPTION_BASES
    bases = sorted(
        {inst.get("baseCoin", "").upper() for inst in instruments if inst.get("baseCoin")}
    )
    if not bases:
        return DEFAULT_OPTION_BASES
    return sorted(set(bases) | set(DEFAULT_OPTION_BASES))


def build_journal_csv(trader: BybitOptionsTrader, days: int = 30) -> str:
    """Return a CSV report for recent option trades and deliveries."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades = trader.list_trade_history(start, end)
    deliveries = trader.list_delivery_history(start, end)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["record_type", "symbol", "side", "price", "qty", "timestamp", "order_id"]
    )
    for trade in sorted(trades, key=lambda x: int(x.get("execTime", 0))):
        writer.writerow(
            [
                "trade",
                trade.get("symbol", ""),
                trade.get("side", ""),
                trade.get("execPrice", ""),
                trade.get("execQty", ""),
                trade.get("execTime", ""),
                trade.get("orderId", ""),
            ]
        )
    for delivery in sorted(
        deliveries, key=lambda x: int(x.get("deliveryTime", 0))
    ):
        writer.writerow(
            [
                "delivery",
                delivery.get("symbol", ""),
                delivery.get("side", ""),
                delivery.get("deliveryPrice", ""),
                delivery.get("deliveryQty", ""),
                delivery.get("deliveryTime", ""),
                "",
            ]
        )
    return output.getvalue()


def format_open_orders(orders: list[dict]) -> str:
    """Return a readable table for open orders."""

    if not orders:
        return "No open orders found."
    headers = ["symbol", "side", "orderStatus", "price", "qty", "orderId", "createdTime"]
    rows = [
        [o.get(h, "") for h in headers]
        for o in sorted(orders, key=lambda x: x.get("createdTime", ""))
    ]
    return "\n".join(tabulate(rows, headers=headers, tablefmt="plain").splitlines())


def format_open_positions(positions: list[dict]) -> str:
    """Return a readable table for open positions."""

    if not positions:
        return "No open positions found."
    headers = [
        "symbol",
        "side",
        "size",
        "avgPrice",
        "markPrice",
        "unrealisedPnl",
        "positionValue",
    ]
    rows = [
        [p.get(h, "") for h in headers]
        for p in sorted(positions, key=lambda x: x.get("symbol", ""))
    ]
    return "\n".join(tabulate(rows, headers=headers, tablefmt="plain").splitlines())


def round_to_tick(price: float, symbol: str) -> float:
    """Round ``price`` to the nearest tick for ``symbol``."""

    tick = get_tick_size(symbol)
    if not tick:
        raise ValueError(f"Tick size for {symbol} is zero or missing")
    price_dec = Decimal(str(price))
    tick_dec = Decimal(str(tick))
    rounded = (price_dec / tick_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_dec
    return float(rounded)


def _parse_expiry(token: str) -> datetime | None:
    """Return datetime for an expiry token like '7JUN25' or '07JUN25'."""

    tok = token.upper()
    if len(tok) == 6:
        tok = "0" + tok
    try:
        return datetime.strptime(tok, "%d%b%y")
    except ValueError:
        return None


def build_option_symbol(base: str, strike: str, option_type: str, expiry: str, quote: str) -> str:
    """Return a Bybit option symbol built from individual parts."""

    base = str(base or "").upper()
    quote = str(quote or "").upper()
    opt = str(option_type or "").upper()
    if opt:
        opt = opt[0]
    try:
        strike_val = int(float(strike))
    except (ValueError, TypeError):
        strike_val = 0
    strike_str = str(strike_val)

    exp = str(expiry or "").replace("-", "/").strip()
    try:
        d, m, y = exp.split("/")
        day = int(d)
        month = int(m)
        year = int(y)
    except ValueError:
        day = month = 1
        year = 1970
    if year < 100:
        year += 2000
    dt = datetime(year, month, day)
    month_token = dt.strftime("%b").upper()
    expiry_token = f"{day}{month_token}{str(year)[2:]}"

    return f"{base}-{expiry_token}-{strike_str}-{opt}-{quote}"


def validate_expiry(expiry: str) -> None:
    """Validate an expiry date string in D/M/YY or D/M/YYYY format."""

    exp = str(expiry or "").replace("-", "/").strip()
    if not exp:
        raise ValueError("Expiry is required for options.")
    try:
        d, m, y = exp.split("/")
        day = int(d)
        month = int(m)
        year = int(y)
    except ValueError as exc:
        raise ValueError("Expiry must be in D/M/YY format.") from exc
    if year < 100:
        year += 2000
    try:
        datetime(year, month, day)
    except ValueError as exc:
        raise ValueError("Expiry date is invalid.") from exc


def compute_order_qty(risk_usd: float, price: float, min_qty: float = MIN_ORDER_QTY) -> float:
    """Return the order quantity rounded to the exchange increment."""

    if not risk_usd or not price:
        return 0.0
    qty = risk_usd / price
    if qty < min_qty:
        qty = min_qty
    steps = round(qty / min_qty)
    qty = steps * min_qty
    return round(qty, 2)


def choose_symbol_by_risk(base_symbol: str, risk_usd: float, qty: float, base_url: str | None = None) -> tuple[str, float]:
    """Return the option symbol from the earliest expiry whose mark price is closest to risk/qty."""

    base_url = base_url or get_base_url()
    if not risk_usd or not qty:
        return base_symbol, 0.0
    parts = base_symbol.split("-")
    if len(parts) < 5:
        return base_symbol, 0.0
    base_coin, expiry_token, _strike, opt_type, _quote = parts
    instruments = fetch_option_instruments(base_coin, option_type=opt_type, base_url=base_url)
    if not instruments:
        return base_symbol, 0.0

    instruments = [
        inst for inst in instruments if inst.get("symbol", "").split("-")[3].upper() == opt_type.upper()
    ]
    if not instruments:
        return base_symbol, 0.0

    def expiry_from_symbol(sym: str) -> datetime:
        p = sym.split("-")
        if len(p) > 1:
            dt = _parse_expiry(p[1])
            if dt:
                return dt
        return datetime.max

    desired_expiry = _parse_expiry(expiry_token)
    if desired_expiry:
        same_expiry = [
            inst
            for inst in instruments
            if expiry_from_symbol(inst.get("symbol", "")) == desired_expiry
        ]
        if same_expiry:
            instruments = same_expiry

    instruments.sort(key=lambda inst: expiry_from_symbol(inst.get("symbol", "")))
    first_expiry = expiry_from_symbol(instruments[0].get("symbol", ""))
    filtered = [
        inst
        for inst in instruments
        if expiry_from_symbol(inst.get("symbol", "")) == first_expiry
    ]
    target = risk_usd / qty
    best_sym = base_symbol
    best_price = 0.0
    best_diff = float("inf")
    for inst in filtered:
        sym = inst.get("symbol")
        if not sym:
            continue
        tick = fetch_option_ticker(sym, base_url)
        price = float(tick.get("markPrice", 0))
        diff = abs(price - target)
        if diff < best_diff:
            best_diff = diff
            best_sym = sym
            best_price = price
    return best_sym, best_price


class ApiException(Exception):
    """Custom exception for Bybit API errors."""


class BybitOptionsTrader:
    """Simple wrapper around Bybit's options REST API."""

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def _generate_signature(self, timestamp: str, body_or_query: str) -> str:
        payload = f"{timestamp}{self.api_key}{RECV_WINDOW}{body_or_query}"
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def _send_request(self, method: str, path: str, body: dict | None = None, query: str = "") -> dict:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + query
        ts = str(int(time.time() * 1000))
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        to_sign = query if method == "GET" else body_str
        sig = self._generate_signature(ts, to_sign)
        headers = {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN-TYPE": "2",
        }
        if SUB_ACCOUNT_NAME:
            headers["X-BAPI-SUB-ACCOUNT-NAME"] = SUB_ACCOUNT_NAME
        resp = requests.request(method, url, headers=headers, data=body_str, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            raise ApiException(f"API Error {data['retCode']}: {data.get('retMsg')}")
        return data

    def get_wallet_balance(self, coin: str = "USDT") -> float:
        try:
            data = self._send_request(
                "GET", "/v5/account/wallet-balance", "", "accountType=UNIFIED"
            )
            for entry in data.get("result", {}).get("list", []):
                for c in entry.get("coin", []):
                    if c.get("coin") == coin:
                        return float(c.get("walletBalance", 0))
        except Exception as exc:  # pragma: no cover - network error path
            logger.error("Failed to retrieve wallet balance: %s", exc)
        return 0.0

    def place_order(self, symbol: str, side: str, qty: float, price: float | None = None, tif: str = "GTC", is_exit: bool = False) -> dict:
        if price is not None:
            price = round_to_tick(price, symbol)
        body = {
            "category": "option",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit" if price is not None else "Market",
            "qty": str(qty),
            "timeInForce": tif,
            "orderLinkId": uuid.uuid4().hex,
        }
        if price is not None:
            body["price"] = str(price)
        if is_exit:
            body["reduceOnly"] = True
        resp = self._send_request("POST", "/v5/order/create", body)
        order_type = "Exit" if is_exit else "Entry"
        logger.info("%s order placed: %s", order_type, resp.get("result", {}))
        return resp.get("result", {})

    def get_trade_history(self, symbol: str, order_id: str, limit: int = 20) -> list[dict]:
        q = f"category=option&symbol={symbol}&limit={limit}"
        data = self._send_request("GET", "/v5/execution/list", "", q)
        trades = data.get("result", {}).get("list", [])
        return [t for t in trades if t.get("orderId") == order_id]

    def get_order_detail(self, symbol: str, order_id: str) -> list[dict]:
        q = f"category=option&symbol={symbol}&orderId={order_id}"
        data = self._send_request("GET", "/v5/order/realtime", "", q)
        return data.get("result", {}).get("list", [])

    def wait_for_order_fill(self, symbol: str, order_id: str, timeout: int = 60, poll_interval: int = 2) -> list[dict]:
        start = time.time()
        while time.time() - start < timeout:
            trades = self.get_trade_history(symbol, order_id)
            if trades:
                return trades
            details = self.get_order_detail(symbol, order_id)
            status = details[0].get("orderStatus") if details else ""
            if status in {"Filled", "PartiallyFilled"}:
                trades = self.get_trade_history(symbol, order_id)
                if trades:
                    return trades
            time.sleep(poll_interval)
        return []

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        q = "category=option"
        if symbol:
            q += f"&symbol={symbol}"
        data = self._send_request("GET", "/v5/order/realtime", "", q)
        orders = data.get("result", {}).get("list", [])
        return [o for o in orders if o.get("orderStatus") not in {"Filled", "Cancelled"}]

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        q = "category=option"
        if symbol:
            q += f"&symbol={symbol}"
        data = self._send_request("GET", "/v5/position/list", "", q)
        return data.get("result", {}).get("list", [])

    def cancel_all_orders(self) -> None:
        body = {"category": "option"}
        try:
            self._send_request("POST", "/v5/order/cancel-all", body)
        except ApiException as exc:
            if "110008" in str(exc):
                logger.info("No open orders to cancel")
            else:
                raise

    def close_position(self, symbol: str, side: str, qty: float) -> None:
        self.place_order(symbol, side, qty, None, "GTC", True)

    def amend_order(self, order_id: str, price: float | None = None, qty: float | None = None) -> None:
        body = {"category": "option", "orderId": order_id}
        if price is not None:
            body["price"] = str(price)
        if qty is not None:
            body["qty"] = str(qty)
        self._send_request("POST", "/v5/order/amend", body)

    def list_trade_history(self, start_time: int, end_time: int | None = None, limit: int = 50) -> list[dict]:
        if end_time is None:
            end_time = int(time.time() * 1000)

        max_range = 7 * 24 * 60 * 60 * 1000
        trades = []
        current_end = end_time
        empty_runs = 0

        while current_end > start_time and empty_runs < 3:
            current_start = max(start_time, current_end - max_range)
            q = f"category=option&startTime={current_start}&endTime={current_end}"
            if limit:
                q += f"&limit={limit}"

            cursor = None
            chunk = []
            while True:
                query = q
                if cursor:
                    query += f"&cursor={cursor}"
                data = self._send_request("GET", "/v5/execution/list", "", query)
                chunk.extend(data.get("result", {}).get("list", []))
                cursor = data.get("result", {}).get("nextPageCursor")
                if not cursor:
                    break

            if chunk:
                trades.extend(chunk)
                empty_runs = 0
            else:
                empty_runs += 1

            current_end = current_start - 1

        return trades

    def list_delivery_history(self, start_time: int, end_time: int | None = None, limit: int = 50) -> list[dict]:
        if end_time is None:
            end_time = int(time.time() * 1000)

        max_range = 7 * 24 * 60 * 60 * 1000
        deliveries = []
        current_end = end_time
        empty_runs = 0

        while current_end > start_time and empty_runs < 3:
            current_start = max(start_time, current_end - max_range)
            q = f"category=option&startTime={current_start}&endTime={current_end}"
            if limit:
                q += f"&limit={limit}"

            cursor = None
            chunk = []
            while True:
                query = q
                if cursor:
                    query += f"&cursor={cursor}"
                data = self._send_request("GET", "/v5/asset/delivery-record", "", query)
                chunk.extend(data.get("result", {}).get("list", []))
                cursor = data.get("result", {}).get("nextPageCursor")
                if not cursor:
                    break

            if chunk:
                deliveries.extend(chunk)
                empty_runs = 0
            else:
                empty_runs += 1

            current_end = current_start - 1

        return deliveries

    def place_and_log(self, symbol: str, side: str, qty: float, entry_price: float | None, tif: str) -> tuple[list[dict], str]:
        result = self.place_order(symbol, side, qty, entry_price, tif, False)
        oid = result.get("orderId")
        trades = []
        for _ in range(5):
            time.sleep(2)
            trades = self.get_trade_history(symbol, oid)
            if trades:
                break
        if not trades:
            trades = self.wait_for_order_fill(symbol, oid)

        order_info = self.get_order_detail(symbol, oid)
        order = order_info[0] if order_info else {}
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        os.makedirs(TRADE_LOG_DIR, exist_ok=True)
        trade_log = os.path.join(TRADE_LOG_DIR, f"option_trade_log_{ts}.log")
        with open(trade_log, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t, indent=2) + "\n")
            if order:
                f.write(json.dumps({"order": order}, indent=2) + "\n")
        logger.info("Trade log saved to %s", trade_log)
        if not trades:
            logger.info("Order not filled; skipping exit order")
            return trades, trade_log

        if not entry_price:
            entry = next((t for t in trades if t.get("side", "").lower() == side.lower()), None)
            if entry and entry.get("execPrice"):
                entry_price = float(entry.get("execPrice"))
            elif order and order.get("avgPrice"):
                entry_price = float(order.get("avgPrice"))
            elif order and order.get("price"):
                entry_price = float(order.get("price"))
            else:
                logger.warning("No entry trade to infer price; skipping exit order")
                return trades, trade_log

        target = round_to_tick(entry_price * 3, symbol)
        exit_side = "Sell" if side.lower() == "buy" else "Buy"
        self.place_order(symbol, exit_side, qty, target, tif, True)
        return trades, trade_log


def _append_greeks(lines: list[str], tick: dict, qty: float, side: str) -> None:
    greeks = {k: float(tick[k]) for k in ("delta", "gamma", "vega", "theta") if k in tick}
    mult = 1 if side.lower() == "buy" else -1
    headers = ["Greek", "Per-Contract", "Qty", "Exposure"]
    rows = []
    for name, per in greeks.items():
        exp = per * qty * mult
        rows.append([name.capitalize(), f"{per:.8f}", str(qty), f"{exp:.8f}"])
    lines.append("\nGreek Exposures:")
    table = tabulate(rows, headers=headers, tablefmt="plain")
    lines.extend(table.splitlines())


def execute_trade_from_cfg(cfg: dict) -> None:
    """Execute trade using a configuration dictionary ``cfg``."""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    symbol, side, qty = cfg["symbol"], cfg["side"], cfg["quantity"]
    entry_price = cfg.get("limit_price")
    lines = [f"Timestamp: {ts}"]
    key, secret = get_api_credentials(cfg)
    if not key or not secret:
        raise RuntimeError(
            "API credentials not provided. Set BYBIT_API_KEY and BYBIT_API_SECRET "
            "environment variables or include api_key/api_secret in the config."
        )
    trader = BybitOptionsTrader(key, secret, get_base_url())
    balance = trader.get_wallet_balance()
    lines.append(f"Balance: {balance:.4f} USDT")
    if balance < MIN_BALANCE_THRESHOLD:
        lines.append("⚠️ Insufficient balance => abort")
        print_and_write(lines)
        return
    order_desc = "Market" if not entry_price else entry_price
    lines.append(f"Placing {side} {qty} {symbol} @ {order_desc}")
    _trades, trade_log = trader.place_and_log(symbol, side, qty, entry_price, "GTC")
    lines.append(f"Trade log: {trade_log}")

    tick = fetch_option_ticker(symbol)
    lines.append("\nTicker Data:")
    for k, v in sorted(tick.items()):
        lines.append(f"  {k}: {v}")
    _append_greeks(lines, tick, qty, side)

    print_and_write(lines)
    token, chat_id = get_telegram_credentials(cfg)
    send_telegram_document(str(output_file), token, chat_id, caption=f"{side} {qty} {symbol}")


def execute_trade(order_file: str) -> None:
    """Execute trade specified by ``order_file`` and print greek exposures."""

    cfg = load_trade_config(order_file)
    execute_trade_from_cfg(cfg)


def show_open(trader: BybitOptionsTrader) -> None:
    """Display open option orders and positions."""

    orders = trader.get_open_orders()
    positions = trader.get_positions()
    print("\nOpen Orders:")
    if not orders:
        print("  None")
    for o in orders:
        print(json.dumps(o, indent=2))
    print("\nOpen Positions:")
    if not positions:
        print("  None")
    for p in positions:
        print(json.dumps(p, indent=2))


def cancel_all(trader: BybitOptionsTrader) -> None:
    """Cancel all open orders and close all positions."""

    trader.cancel_all_orders()
    for pos in trader.get_positions():
        qty = abs(float(pos.get("size", 0)))
        if qty:
            side = "Sell" if pos.get("side", "Buy").lower() == "buy" else "Buy"
            trader.close_position(pos.get("symbol"), side, qty)
    print("All orders cancelled and positions closed.")


def edit_open_order(trader: BybitOptionsTrader) -> None:
    """Prompt for an order id and new values then amend the order."""

    oid = input("Enter order ID to amend: ").strip()
    price = input("New price (blank to keep): ").strip()
    qty = input("New qty (blank to keep): ").strip()
    price_val = float(price) if price else None
    qty_val = float(qty) if qty else None
    trader.amend_order(oid, price_val, qty_val)
    print("Order amended.")


def _write_trade_history_csv(trader: BybitOptionsTrader, trades: list[dict], filename: str) -> None:
    """Write ``trades`` to ``filename`` adding fees, PnL and balance."""

    final_balance = trader.get_wallet_balance("USDT")
    path = os.path.join(script_dir, filename)
    base_fields = sorted(trades[0].keys())
    extra = ["netFee", "netPnl", "localTime", "balance"]

    with open(path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=base_fields + extra)
        writer.writeheader()

        trades_sorted = sorted(trades, key=lambda x: int(x.get("execTime", 0)))

        processed = []
        for t in trades_sorted:
            row = dict(t)
            try:
                row["netFee"] = float(t.get("execFee", 0))
            except (TypeError, ValueError):
                row["netFee"] = 0.0
            pnl = None
            for pf in ("closedPnl", "realisedPnl", "execPnl"):
                if pf in t and t[pf] not in (None, ""):
                    try:
                        pnl = float(t[pf])
                        break
                    except (TypeError, ValueError):
                        pass
            if pnl is None:
                try:
                    value = float(t.get("execValue", 0) or 0)
                    side = str(t.get("side", "")).lower()
                    sign = 1 if side == "sell" else -1
                    fee = float(t.get("execFee", 0) or 0)
                    pnl = sign * value - fee
                except Exception:
                    pnl = 0.0
            row["netPnl"] = pnl
            processed.append((row, pnl))

        starting_balance = final_balance - sum(p for _, p in processed)
        running_balance = starting_balance

        for row, pnl in processed:
            ts = None
            for tf in ("execTime", "createdTime", "updatedTime", "tradeTime"):
                if tf in row and row[tf] not in (None, ""):
                    ts = row[tf]
                    break
            if ts is not None:
                try:
                    ts_int = int(ts)
                    dt = datetime.fromtimestamp(ts_int / 1000, timezone.utc)
                    dt = dt.astimezone(ZoneInfo("Australia/Brisbane"))
                    row["localTime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    row["localTime"] = ""
            else:
                row["localTime"] = ""

            running_balance += pnl
            row["balance"] = running_balance
            writer.writerow(row)
    print(f"Saved {len(trades)} trades to {path}")


def export_recent_trade_history(trader: BybitOptionsTrader, days: int = 7) -> None:
    """Save trades from the last ``days`` days to a CSV file with extra info."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades = trader.list_trade_history(start, end)
    if not trades:
        print("No recent trades found.")
        return
    _write_trade_history_csv(trader, trades, "recent_trades.csv")


def export_all_trade_history(trader: BybitOptionsTrader) -> None:
    """Save all available trades up to now to a CSV file."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = 0
    try:
        trades = trader.list_trade_history(start, end)
    except ApiException as exc:
        print(f"Failed to retrieve trade history: {exc}")
        return
    if not trades:
        print("No trades found.")
        return
    _write_trade_history_csv(trader, trades, "all_trades.csv")


def export_recent_delivery_history(trader: BybitOptionsTrader, days: int = 7) -> None:
    """Save delivery records from the last ``days`` days to a CSV file."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    deliveries = trader.list_delivery_history(start, end)
    if not deliveries:
        print("No recent deliveries found.")
        return
    _write_trade_history_csv(trader, deliveries, "recent_deliveries.csv")


def export_all_delivery_history(trader: BybitOptionsTrader) -> None:
    """Save all available delivery records up to now to a CSV file."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = 0
    try:
        deliveries = trader.list_delivery_history(start, end)
    except ApiException as exc:
        print(f"Failed to retrieve delivery history: {exc}")
        return
    if not deliveries:
        print("No deliveries found.")
        return
    _write_trade_history_csv(trader, deliveries, "all_deliveries.csv")


def set_profit_targets(trader: BybitOptionsTrader, multiplier: int = 2) -> None:
    """Place reduce-only limit orders for open long positions."""

    positions = trader.get_positions()
    if not positions:
        print("No open positions found.")
        return
    for pos in positions:
        qty = abs(float(pos.get("size", 0)))
        if qty <= 0:
            continue
        side = str(pos.get("side", "")).lower()
        if side != "buy":
            continue
        symbol = pos.get("symbol")
        avg_price = float(pos.get("avgPrice", 0))
        if not symbol or not avg_price:
            continue
        target = avg_price * (multiplier + 1)
        try:
            trader.place_order(symbol, "Sell", qty, target, "GTC", True)
            print(f"Placed reduce-only Sell {qty} {symbol} @ {target}")
        except ApiException as exc:
            print(
                f"Warning: failed to place reduce-only Sell {qty} {symbol} @ {target}: {exc}"
            )
            continue


def build_journal_report(trader: BybitOptionsTrader, days: int = 30) -> str:
    """Return a text report for recent option trades and deliveries."""

    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades = trader.list_trade_history(start, end)
    deliveries = trader.list_delivery_history(start, end)

    lines = [f"Options activity journal (last {days} days)"]
    lines.append("")
    if trades:
        lines.append("Trade History:")
        headers = ["symbol", "side", "execPrice", "execQty", "execTime", "orderId"]
        rows = [
            [t.get(h, "") for h in headers]
            for t in sorted(trades, key=lambda x: int(x.get("execTime", 0)))
        ]
        lines.extend(tabulate(rows, headers=headers, tablefmt="plain").splitlines())
    else:
        lines.append("No trades found.")

    lines.append("")
    if deliveries:
        lines.append("Delivery History:")
        headers = ["symbol", "side", "deliveryPrice", "deliveryQty", "deliveryTime"]
        rows = [
            [d.get(h, "") for h in headers]
            for d in sorted(deliveries, key=lambda x: int(x.get("deliveryTime", 0)))
        ]
        lines.extend(tabulate(rows, headers=headers, tablefmt="plain").splitlines())
    else:
        lines.append("No deliveries found.")

    return "\n".join(lines)


def main() -> None:
    """Entry point for CLI execution."""

    parser = argparse.ArgumentParser(description="Bybit options helper")
    parser.add_argument(
        "order_file",
        nargs="?",
        default="",
        help="Path to JSON config (used only with --no-menu)",
    )
    parser.add_argument(
        "--no-menu",
        action="store_true",
        help="Execute trade immediately without showing the menu",
    )
    args = parser.parse_args()
    configure_trading_environment(interactive=True)
    if args.no_menu:
        if not args.order_file:
            raise SystemExit("order_file required with --no-menu")
        execute_trade(args.order_file)
    else:
        raise SystemExit("Interactive menu is available via cryptocalculator_web.py")


if __name__ == "__main__":
    main()
