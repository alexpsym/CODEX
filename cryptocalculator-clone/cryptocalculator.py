"""Core trade calculation logic for the crypto position size tool."""
from __future__ import annotations

import builtins
from dataclasses import dataclass
import json
import math
import os
from typing import Any, Dict, Iterable, Optional, Tuple

import requests

from exchange_adapters import (
    COINSPOT_SPOT_FEE_RATE,
    InstrumentInfo,
    get_exchange_adapter as _get_real_exchange_adapter,
)

BYBIT_SPOT_URL = "https://api.bybit.com/v5/market/tickers?category=spot"
BYBIT_LINEAR_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_INSTRUMENT_INFO_SPOT = (
    "https://api.bybit.com/v5/market/instruments-info?category=spot"
)
BYBIT_INSTRUMENT_INFO_LINEAR = (
    "https://api.bybit.com/v5/market/instruments-info?category=linear"
)

DEFAULT_EXECUTION_EXCHANGE = "bybit"
DEFAULT_PRICE_SOURCE = "bybit_linear"

COINSPOT_MARKET_FEE_RATE = COINSPOT_SPOT_FEE_RATE
COINSPOT_DEFAULT_TICK_SIZE = 0.01
COINSPOT_DEFAULT_MIN_QTY = 0.00000001
COINSPOT_DEFAULT_QTY_STEP = 0.00000001

PRICE_SOURCES: Dict[str, Dict[str, str]] = {
    "bybit_linear": {
        "label": "Bybit Linear Perpetual",
        "trade_mode": "linear",
        "exchange": "bybit",
    },
    "bybit_spot": {
        "label": "Bybit Spot",
        "trade_mode": "spot",
        "exchange": "bybit",
    },
    "coinspot_spot": {
        "label": "CoinSpot Spot",
        "trade_mode": "spot",
        "exchange": "coinspot",
    },
}

PRICE_SOURCE_ALIASES: Dict[str, Iterable[str]] = {"bybit_linear": {"bybit"}}

EXECUTION_EXCHANGES: Dict[str, Dict[str, Iterable[str]]] = {
    "bybit": {
        "label": "Bybit",
        "supported_trade_modes": {"linear", "spot"},
    },
    "coinspot": {
        "label": "CoinSpot",
        "supported_trade_modes": {"spot"},
    },
}

TRADE_MODE_LABELS = {"linear": "Linear Perpetual", "spot": "Spot"}


@dataclass
class LotSizeInfo:
    """Represents the quantity limits for an instrument."""

    min_qty: float
    qty_step: float


class ExchangeAdapter:
    """Compatibility layer for exchange adapters used throughout the project."""

    name: str = "base"

    @staticmethod
    def _call_with_optional_config(method, symbol, trade_mode, config):
        try:
            return method(symbol, trade_mode, config)
        except TypeError:
            return method(symbol, trade_mode)

    # --- Legacy fetch_* methods -------------------------------------------------
    def fetch_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        raise NotImplementedError

    def fetch_tick_size(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        raise NotImplementedError

    def fetch_lot_size(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> LotSizeInfo:
        raise NotImplementedError

    def fetch_fee_rate(self, trade_mode: str) -> float:
        raise NotImplementedError

    def fetch_funding_rate(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        return None

    def fetch_account_balance(
        self, coin: str = "USDT", **kwargs: Any
    ) -> float:  # pragma: no cover - interface default
        raise NotImplementedError

    # --- Convenience get_* methods used by newer code ---------------------------
    def get_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        return self._call_with_optional_config(
            self.fetch_current_price, symbol, trade_mode, config
        )

    def get_instrument_info(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> InstrumentInfo:
        lot = self._call_with_optional_config(
            self.fetch_lot_size, symbol, trade_mode, config
        )
        tick = self._call_with_optional_config(
            self.fetch_tick_size, symbol, trade_mode, config
        )
        return InstrumentInfo(tick_size=tick, min_qty=lot.min_qty, qty_step=lot.qty_step)

    def get_fee_rate(self, trade_mode: str) -> float:
        return self.fetch_fee_rate(trade_mode)

    def get_funding_rate(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        return self._call_with_optional_config(
            self.fetch_funding_rate, symbol, trade_mode, config
        )

    def get_account_balance(self, config: Dict[str, Any]) -> float:
        coin = config.get("account_coin", "USDT")
        kwargs = dict(config)
        kwargs.pop("account_coin", None)
        kwargs.pop("exchange", None)
        return self.fetch_account_balance(coin, **kwargs)


class _AdapterBridge(ExchangeAdapter):
    """Bridge that adapts the new adapter layer to the legacy fetch_* interface."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._delegate = _get_real_exchange_adapter(name)

    def fetch_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        return self._delegate.get_current_price(symbol, trade_mode, config)

    def fetch_tick_size(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        instrument = self._delegate.get_instrument_info(symbol, trade_mode, config)
        return instrument.tick_size

    def fetch_lot_size(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> LotSizeInfo:
        instrument = self._delegate.get_instrument_info(symbol, trade_mode, config)
        return LotSizeInfo(min_qty=instrument.min_qty, qty_step=instrument.qty_step)

    def fetch_fee_rate(self, trade_mode: str) -> float:
        return self._delegate.get_fee_rate(trade_mode)

    def fetch_funding_rate(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        return self._delegate.get_funding_rate(symbol, trade_mode, config)

    def fetch_account_balance(
        self, coin: str = "USDT", **kwargs: Any
    ) -> float:  # pragma: no cover - simple delegation
        config = dict(kwargs)
        config["account_coin"] = coin
        config.setdefault("exchange", self.name)
        return self._delegate.get_account_balance(config)


def _build_default_adapters() -> Dict[str, ExchangeAdapter]:
    return {name: _AdapterBridge(name) for name in EXECUTION_EXCHANGES}


# A convenient iterable for UI code when it needs to list available adapters.
EXCHANGE_ADAPTERS: Dict[str, ExchangeAdapter] = _build_default_adapters()


_CONFIG_CACHE: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}


class AliasString(str):
    """String subclass that matches any of the supplied aliases when compared."""

    def __new__(cls, value: str, aliases: Iterable[str] = ()):
        obj = super().__new__(cls, value)
        obj._primary = value.lower()
        obj._aliases = {alias.lower() for alias in aliases}
        return obj

    def __eq__(self, other: object) -> bool:  # pragma: no cover - simple override
        if isinstance(other, str):
            candidate = other.lower()
            return candidate == self._primary or candidate in self._aliases
        return super().__eq__(other)

    def __hash__(self) -> int:  # pragma: no cover - relies on str hash
        return super().__hash__()


def get_exchange_adapter(name: str) -> ExchangeAdapter:
    """Return the configured adapter for ``name``."""

    try:
        return EXCHANGE_ADAPTERS[name.lower()]
    except KeyError as exc:  # pragma: no cover - guard clause
        available = ", ".join(sorted(EXCHANGE_ADAPTERS))
        raise ValueError(f"Unknown exchange '{name}'. Available: {available}") from exc


# ---------------------------------------------------------------------------
# CoinSpot helpers
# ---------------------------------------------------------------------------


def _require_coinspot_dependency(feature: str) -> None:
    """Ensure the optional ``coinspot`` dependency is available."""

    try:
        import coinspot  # noqa: F401  # pragma: no cover - optional dependency
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            f"CoinSpot {feature} requires the 'coinspot' package. Install it with 'pip install coinspot'."
        ) from exc


def _split_symbol(symbol: str) -> Tuple[str, str]:
    """Split a market symbol into base and quote components."""

    upper = symbol.replace("_", "").upper()
    for quote in ("USDT", "AUD", "USD", "BTC", "ETH"):
        if upper.endswith(quote):
            base = upper[: -len(quote)]
            if not base:
                break
            return base, quote
    raise ValueError(f"Unable to determine base/quote for symbol '{symbol}'.")


def _infer_quote_currency(symbol: str) -> Optional[str]:
    """Best-effort inference of the quote currency for ``symbol``."""

    try:
        _base, quote = _split_symbol(symbol)
    except ValueError:
        return None
    return quote


def format_coinspot_symbol(symbol: str) -> str:
    """Return a CoinSpot compatible market string (``btc_usdt``)."""

    if "_" in symbol:
        return symbol.lower()
    base, quote = _split_symbol(symbol)
    return f"{base.lower()}_{quote.lower()}"


def fetch_coinspot_price(symbol: str) -> float:
    """Return the latest CoinSpot market price for ``symbol``."""

    _require_coinspot_dependency("pricing")
    from coinspot import PublicAPIV2  # type: ignore  # pragma: no cover - optional

    pair = format_coinspot_symbol(symbol)
    resp = PublicAPIV2().latest(pair)
    if resp.get("status") != "ok":
        raise ValueError(f"CoinSpot returned error for {pair}: {resp}")
    prices = resp.get("prices")
    if not prices:
        raise ValueError(f"CoinSpot price data missing for {pair}.")
    return float(prices.get("last"))


def fetch_coinspot_tick_size(_symbol: str) -> float:
    """Return a best-effort tick size for CoinSpot spot markets."""

    return COINSPOT_DEFAULT_TICK_SIZE


def fetch_coinspot_lot_info(_symbol: str) -> Tuple[float, float]:
    """Return ``(min_qty, qty_step)`` defaults for CoinSpot execution."""

    return COINSPOT_DEFAULT_MIN_QTY, COINSPOT_DEFAULT_QTY_STEP


def fetch_coinspot_account_balance(asset: str = "USDT") -> float:
    """Return the CoinSpot wallet balance for ``asset`` using API credentials."""

    _require_coinspot_dependency("balance lookups")
    from coinspot import ReadOnlyAPIV2  # type: ignore  # pragma: no cover - optional

    api_key = os.environ.get("COINSPOT_API_KEY")
    api_secret = os.environ.get("COINSPOT_API_SECRET")
    if not api_key or not api_secret:
        raise EnvironmentError("COINSPOT_API_KEY and COINSPOT_API_SECRET must be set")

    balances = ReadOnlyAPIV2(api_key, api_secret).wallet_balance().get("balances", [])
    target = asset.upper()
    for entry in balances:
        for code, data in entry.items():
            if code.upper() != target:
                continue
            balance = data.get("balance") or data.get("available") or data.get("audbalance")
            if balance is not None:
                return float(balance)
    raise ValueError(f"Balance for {asset} not found on CoinSpot.")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def resolve_price_source(
    price_source: Optional[str],
    execution_exchange: str,
    trade_mode: Optional[str],
) -> str:
    """Resolve *price_source* into one of :data:`PRICE_SOURCES`."""

    alias_map = {
        None: DEFAULT_PRICE_SOURCE,
        "": DEFAULT_PRICE_SOURCE,
        "linear": "bybit_linear",
        "spot": "bybit_spot",
    }

    if price_source:
        key = price_source.lower()
        if key in PRICE_SOURCES:
            return key
        if key in alias_map:
            return alias_map[key]

    if execution_exchange == "coinspot":
        return "coinspot_spot"

    if trade_mode == "spot":
        return "bybit_spot"

    return DEFAULT_PRICE_SOURCE


def _normalise_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a normalised copy of ``config`` with sensible defaults."""

    data = dict(config)
    symbol = data.get("symbol")
    if not symbol:
        raise ValueError("A symbol must be provided in the configuration.")
    data["symbol"] = str(symbol).upper()

    direction = str(data.get("direction", "long")).lower()
    if direction not in {"long", "short"}:
        raise ValueError("Direction must be 'long' or 'short'.")
    data["direction"] = direction

    order_type = str(data.get("order_type", "market")).lower()
    if order_type not in {"market", "limit"}:
        raise ValueError("Order type must be 'market' or 'limit'.")
    data["order_type"] = order_type

    if "entry_price" in data and data["entry_price"] is not None:
        data["entry_price"] = float(data["entry_price"])

    data["risk_percent"] = float(data.get("risk_percent", 1.0))
    data["rr_ratio"] = float(data.get("rr_ratio", 2.0))
    data["stop_loss_ticks"] = float(data.get("stop_loss_ticks", 0.0))

    inferred_quote = _infer_quote_currency(data["symbol"])

    price_quote_asset = data.get("price_quote_asset")
    if price_quote_asset:
        data["price_quote_asset"] = str(price_quote_asset).upper()
    else:
        data["price_quote_asset"] = inferred_quote

    execution_exchange = str(
        data.get("execution_exchange", DEFAULT_EXECUTION_EXCHANGE)
    ).lower()

    execution_quote_asset = data.get("execution_quote_asset")
    if execution_quote_asset:
        data["execution_quote_asset"] = str(execution_quote_asset).upper()
    else:
        if execution_exchange == "coinspot":
            data["execution_quote_asset"] = "AUD"
        else:
            data["execution_quote_asset"] = data.get("price_quote_asset") or inferred_quote

    account_asset = data.get("account_asset")
    if account_asset:
        data["account_asset"] = str(account_asset).upper()
    else:
        if execution_exchange == "coinspot":
            data["account_asset"] = "AUD"
        else:
            data["account_asset"] = data["execution_quote_asset"] or "USDT"

    if data.get("price_to_execution_rate") is not None:
        data["price_to_execution_rate"] = float(data["price_to_execution_rate"])

    balance = data.get("account_balance", 0.0)
    if isinstance(balance, str) and balance.lower() == "auto":
        data["account_balance"] = "auto"
    else:
        data["account_balance"] = float(balance)

    return data


def get_price_source_info(
    price_source: str,
    symbol: str,
    config: Dict[str, Any],
) -> Tuple[str, float, float, Optional[float]]:
    """Return trade mode, market price, tick size and funding rate."""

    meta = PRICE_SOURCES.get(price_source)
    if meta is None:
        raise ValueError(f"Unknown price source '{price_source}'.")

    trade_mode = meta["trade_mode"]
    exchange_name = meta["exchange"]

    if exchange_name == "coinspot":
        market_price = fetch_coinspot_price(symbol)
        tick_size = fetch_coinspot_tick_size(symbol)
        funding_rate: Optional[float] = None
    else:
        adapter = get_exchange_adapter(exchange_name)
        market_price = adapter.get_current_price(symbol, trade_mode, config)
        instrument = adapter.get_instrument_info(symbol, trade_mode, config)
        tick_size = instrument.tick_size
        funding_rate = adapter.get_funding_rate(symbol, trade_mode, config)

    return trade_mode, market_price, tick_size, funding_rate


def get_execution_requirements(
    execution_exchange: str,
    symbol: str,
    trade_mode: str,
    config: Dict[str, Any],
) -> Tuple[float, float, float]:
    """Return minimum quantity, quantity step and fee rate for execution."""

    adapter = get_exchange_adapter(execution_exchange)

    try:
        instrument = adapter.get_instrument_info(symbol, trade_mode, config)
        fee_rate = adapter.get_fee_rate(trade_mode)
    except Exception:  # pragma: no cover - fallback for optional deps
        if execution_exchange == "coinspot":
            min_qty, qty_step = fetch_coinspot_lot_info(symbol)
            return min_qty, qty_step, COINSPOT_MARKET_FEE_RATE
        raise

    return instrument.min_qty, instrument.qty_step, fee_rate


def get_balance_fetcher(execution_exchange: str):
    """Return a callable used to fetch account balances for *execution_exchange*."""

    if execution_exchange == "coinspot":

        def _fetch(asset: str = "AUD", **_kwargs: Any) -> float:
            return fetch_coinspot_account_balance(asset)

        return _fetch

    adapter = get_exchange_adapter(execution_exchange)

    def _fetch(asset: str = "USDT", **kwargs: Any) -> float:
        config = {
            "exchange": execution_exchange,
            "account_coin": asset,
            "account_type": kwargs.get("account_type", "UNIFIED"),
        }
        return adapter.get_account_balance(config)

    return _fetch


def load_config(filename: str) -> Dict[str, Any]:
    """Load and normalise the configuration stored in *filename*."""

    builtins.cfg_file = filename  # type: ignore[attr-defined]

    key = str(filename)

    with open(filename, "r", encoding="utf-8") as handle:
        raw_config = json.load(handle)

    cached = _CONFIG_CACHE.get(key)
    if cached and cached[0] == raw_config:
        return dict(cached[1])

    execution_exchange = str(
        raw_config.get("execution_exchange", DEFAULT_EXECUTION_EXCHANGE)
    ).lower()
    if execution_exchange not in EXECUTION_EXCHANGES:
        raise ValueError(f"Unknown execution exchange '{execution_exchange}'.")

    price_source = resolve_price_source(
        raw_config.get("price_source"), execution_exchange, raw_config.get("trade_mode")
    )

    config: Dict[str, Any] = dict(raw_config)
    config["execution_exchange"] = execution_exchange
    config["price_source"] = price_source

    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    config["trade_mode"] = trade_mode

    normalised = _normalise_config(config)

    if normalised.get("account_balance") == "auto":
        balance_fetcher = get_balance_fetcher(execution_exchange)
        asset = normalised.get("account_asset", "USDT")
        account_type = normalised.get("account_type", "UNIFIED")
        normalised["account_balance"] = balance_fetcher(asset, account_type=account_type)

    source_key = str(normalised.get("price_source", "")).lower()
    aliases = PRICE_SOURCE_ALIASES.get(source_key)
    if aliases:
        normalised["price_source"] = AliasString(str(normalised["price_source"]), aliases)

    _CONFIG_CACHE[key] = (raw_config, dict(normalised))

    return normalised


# ---------------------------------------------------------------------------
# Core calculation logic
# ---------------------------------------------------------------------------


def _validate_tick_multiple(value: float, tick_size: float, label: str) -> None:
    if tick_size <= 0:
        raise ValueError("Tick size must be greater than zero")
    if not math.isclose((value / tick_size) % 1, 0.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"{label} is not aligned with tick size {tick_size}")


def calculate_trade(
    config: Dict[str, Any],
    adapter: Optional[ExchangeAdapter] = None,
) -> Dict[str, Any]:
    """Calculate trade parameters based on ``config``."""

    cfg = _normalise_config(config)
    account_mode = str(cfg.get("account_mode", "live")).lower()
    if account_mode not in {"live", "demo"}:
        raise ValueError("account_mode must be 'live' or 'demo'")

    execution_exchange = cfg.get("execution_exchange", DEFAULT_EXECUTION_EXCHANGE)
    if execution_exchange not in EXECUTION_EXCHANGES:
        raise ValueError(f"Unknown execution exchange '{execution_exchange}'.")

    price_source = resolve_price_source(
        cfg.get("price_source"), execution_exchange, cfg.get("trade_mode")
    )
    trade_mode, market_price, tick_size, funding_rate = get_price_source_info(
        price_source, cfg["symbol"], cfg
    )

    cfg["price_source"] = price_source
    cfg["trade_mode"] = trade_mode

    min_qty, qty_step, fee_rate = get_execution_requirements(
        execution_exchange, cfg["symbol"], trade_mode, cfg
    )

    price_quote_asset = cfg.get("price_quote_asset") or _infer_quote_currency(
        cfg["symbol"]
    )
    execution_quote_asset = cfg.get("execution_quote_asset") or price_quote_asset

    conversion_rate: Optional[float]
    requires_conversion = False
    if execution_quote_asset:
        if price_quote_asset and execution_quote_asset.upper() == price_quote_asset.upper():
            conversion_rate = 1.0
        else:
            requires_conversion = True
    else:
        conversion_rate = float(cfg.get("price_to_execution_rate") or 1.0)

    if requires_conversion:
        rate = cfg.get("price_to_execution_rate")
        if rate is None:
            raise ValueError(
                "A price_to_execution_rate value is required when the execution currency "
                "is known and differs from or cannot be matched to the price quote currency."
            )
        conversion_rate = float(rate)

    if conversion_rate is None:
        conversion_rate = 1.0

    if conversion_rate <= 0:
        raise ValueError("price_to_execution_rate must be greater than zero.")

    account_balance = cfg.get("account_balance", 0.0)
    if isinstance(account_balance, str) and account_balance.lower() == "auto":
        balance_fetcher = get_balance_fetcher(execution_exchange)
        account_type = cfg.get("account_type", "UNIFIED")
        account_asset = cfg.get("account_asset", "USDT")
        account_balance = balance_fetcher(account_asset, account_type=account_type)
    account_balance = float(account_balance)

    risk_amount = account_balance * (cfg["risk_percent"] / 100)

    stop_distance = cfg["stop_loss_ticks"] * tick_size
    _validate_tick_multiple(stop_distance, tick_size, "Stop distance")

    if cfg["direction"] == "long":
        stop_price = cfg.get("entry_price", market_price) - stop_distance
    else:
        stop_price = cfg.get("entry_price", market_price) + stop_distance

    order_type = cfg["order_type"]
    if order_type == "limit" and "entry_price" in cfg and cfg["entry_price"] is not None:
        entry_price = float(cfg["entry_price"])
    else:
        entry_price = float(market_price)

    if cfg["direction"] == "long":
        stop_price = entry_price - stop_distance
    else:
        stop_price = entry_price + stop_distance

    entry_price_execution = entry_price * conversion_rate
    stop_price_execution = stop_price * conversion_rate
    stop_distance_execution = stop_distance * conversion_rate

    net_per_unit_loss_execution = stop_distance_execution + fee_rate * (
        entry_price_execution + stop_price_execution
    )
    if net_per_unit_loss_execution <= 0:
        raise ValueError("Net per unit loss must be greater than zero")

    raw_qty = risk_amount / net_per_unit_loss_execution
    steps = max(1, round(raw_qty / qty_step))
    quantity = steps * qty_step
    if quantity < min_qty:
        raise ValueError(f"Quantity {quantity:.6f} below minimum {min_qty}")

    position_notional_quote = quantity * entry_price
    position_notional_execution = position_notional_quote * conversion_rate

    entry_fee_stop_execution = entry_price_execution * quantity * fee_rate
    exit_fee_stop_execution = stop_price_execution * quantity * fee_rate
    actual_risk_execution = (
        quantity * stop_distance_execution
        + entry_fee_stop_execution
        + exit_fee_stop_execution
    )

    if cfg["direction"] == "long":
        target_base = entry_price + (stop_distance * cfg["rr_ratio"])
    else:
        target_base = entry_price - (stop_distance * cfg["rr_ratio"])

    interest = 0.0

    min_profit_execution = actual_risk_execution * cfg["rr_ratio"]
    diff_required_execution = (
        min_profit_execution + (2 * position_notional_execution * fee_rate) + interest
    ) / (quantity * (1 - fee_rate))
    diff_required = diff_required_execution / conversion_rate
    ticks = math.ceil(diff_required / tick_size)
    if cfg["direction"] == "long":
        target_price = entry_price + ticks * tick_size
    else:
        target_price = entry_price - ticks * tick_size

    target_price_execution = target_price * conversion_rate

    gross_execution = abs(target_price_execution - entry_price_execution) * quantity
    entry_fee = position_notional_execution * fee_rate
    exit_fee = abs(target_price_execution) * quantity * fee_rate
    fees = entry_fee + exit_fee
    net_profit = gross_execution - fees - interest

    gross_quote = gross_execution / conversion_rate if conversion_rate else gross_execution
    fees_quote = fees / conversion_rate if conversion_rate else fees
    net_profit_quote = (
        net_profit / conversion_rate if conversion_rate else net_profit
    )

    _validate_tick_multiple(target_price - entry_price, tick_size, "Target distance")

    alias_price_source = AliasString(
        price_source, PRICE_SOURCE_ALIASES.get(price_source, ())
    )

    return {
        "symbol": cfg["symbol"],
        "price_source": alias_price_source,
        "execution_exchange": execution_exchange,
        "direction": cfg["direction"],
        "order_type": order_type,
        "account_balance": account_balance,
        "risk_percent": cfg["risk_percent"],
        "risk_amount": risk_amount,
        "entry_price": entry_price,
        "entry_price_execution": entry_price_execution,
        "position_usdt": position_notional_quote,
        "position_execution": position_notional_execution,
        "quantity": quantity,
        "quantity_step": qty_step,
        "min_quantity": min_qty,
        "stop_loss_ticks": cfg["stop_loss_ticks"],
        "stop_distance": stop_distance,
        "stop_distance_execution": stop_distance_execution,
        "stop_price": stop_price,
        "stop_price_execution": stop_price_execution,
        "target_price": target_price,
        "target_price_execution": target_price_execution,
        "gross_reward": gross_execution,
        "gross_reward_quote": gross_quote,
        "net_profit": net_profit,
        "net_profit_quote": net_profit_quote,
        "fees": fees,
        "fees_quote": fees_quote,
        "interest_cost": interest,
        "funding_rate": funding_rate,
        "actual_risk": actual_risk_execution,
        "actual_risk_quote": (
            actual_risk_execution / conversion_rate
            if conversion_rate
            else actual_risk_execution
        ),
        "rr_ratio": cfg["rr_ratio"],
        "achieved_rr": (
            net_profit / actual_risk_execution if actual_risk_execution else 0.0
        ),
        "trade_mode": trade_mode,
        "per_unit_risk": net_per_unit_loss_execution,
        "per_unit_risk_quote": (
            net_per_unit_loss_execution / conversion_rate
            if conversion_rate
            else net_per_unit_loss_execution
        ),
        "price_quote_asset": price_quote_asset,
        "execution_quote_asset": execution_quote_asset,
        "price_to_execution_rate": conversion_rate,
        "account_mode": account_mode,
    }


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def format_trade(trade: Dict[str, Any]) -> str:
    """Return a formatted multi-line summary for ``trade``."""

    execution_label = EXECUTION_EXCHANGES.get(trade["execution_exchange"], {}).get(
        "label", trade["execution_exchange"]
    )
    price_label = PRICE_SOURCES.get(trade["price_source"], {}).get(
        "label", trade["price_source"]
    )
    trade_mode_label = TRADE_MODE_LABELS.get(
        trade.get("trade_mode", ""), trade.get("trade_mode", "")
    )

    account_currency = trade.get("execution_quote_asset") or "USDT"
    price_currency = trade.get("price_quote_asset") or account_currency
    conversion_rate = trade.get("price_to_execution_rate", 1.0) or 1.0
    entry_price_execution = trade.get("entry_price_execution")
    stop_price_execution = trade.get("stop_price_execution")
    target_price_execution = trade.get("target_price_execution")
    stop_distance_execution = trade.get("stop_distance_execution")

    try:
        base_asset, _quote = _split_symbol(trade["symbol"])
    except Exception:  # pragma: no cover - fallback for unexpected symbols
        base_asset = trade["symbol"]

    lines = ["=" * 50]
    lines.append("              CRYPTO TRADE CALCULATOR")
    lines.append("=" * 50)
    lines.append("")
    lines.append("                 TRADE PARAMETERS")
    lines.append("-" * 50)
    lines.append(f"Symbol:           {trade['symbol']}")
    lines.append(f"Direction:        {trade['direction'].capitalize()}")
    lines.append(f"Order Type:       {trade['order_type'].capitalize()}")
    lines.append(f"Execution:        {execution_label}")
    lines.append(f"Price Source:     {price_label}")
    lines.append(f"Price Mode:       {trade_mode_label}")
    lines.append(
        f"Account Balance:  {trade['account_balance']:.2f} {account_currency}"
    )
    lines.append(f"Risk Percent:     {trade['risk_percent']}%")
    lines.append(
        f"Risk Amount:      {trade['risk_amount']:.6f} {account_currency}"
    )
    lines.append(
        f"Entry Price:      {trade['entry_price']:.6f} {price_currency}"
    )
    if (
        entry_price_execution is not None
        and (price_currency != account_currency or not math.isclose(conversion_rate, 1.0))
    ):
        lines.append(
            f"Entry Price (exec): {entry_price_execution:.6f} {account_currency}"
        )
    lines.append("")
    lines.append("                 POSITION DETAILS")
    lines.append("-" * 50)
    position_exec = trade.get(
        "position_execution",
        trade["position_usdt"] * conversion_rate,
    )
    lines.append(
        f"Position Size:     {position_exec:.2f} {account_currency}"
    )
    lines.append(f"Quantity:          {trade['quantity']:.3f} {base_asset}")
    lines.append(f"Stop Loss Ticks:   {trade['stop_loss_ticks']}")
    target_distance = trade['target_price'] - trade['entry_price']
    lines.append(
        f"Stop Distance:     {trade['stop_distance']:.6f} {price_currency}"
    )
    if stop_distance_execution is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Stop Distance (exec): {stop_distance_execution:.6f} {account_currency}"
        )
    lines.append(
        f"Target Distance:   {target_distance:.6f} {price_currency}"
    )
    if price_currency != account_currency or not math.isclose(conversion_rate, 1.0):
        target_distance_exec = target_distance * conversion_rate
        lines.append(
            f"Target Distance (exec): {target_distance_exec:.6f} {account_currency}"
        )
    lines.append(f"Stop Price:        {trade['stop_price']:.6f} {price_currency}")
    if stop_price_execution is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Stop Price (exec): {stop_price_execution:.6f} {account_currency}"
        )
    lines.append(f"Target Price:      {trade['target_price']:.6f} {price_currency}")
    if target_price_execution is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Target Price (exec): {target_price_execution:.6f} {account_currency}"
        )
    lines.append("")
    lines.append("                 RISK/REWARD")
    lines.append("-" * 50)
    lines.append(
        f"Gross Reward:     {trade['gross_reward']:.6f} {account_currency}"
    )
    if trade.get("gross_reward_quote") is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Gross Reward (price): {trade['gross_reward_quote']:.6f} {price_currency}"
        )
    lines.append(
        f"Net Profit:       {trade['net_profit']:.6f} {account_currency}"
    )
    if trade.get("net_profit_quote") is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Net Profit (price): {trade['net_profit_quote']:.6f} {price_currency}"
        )
    lines.append(
        f"Actual Risk:      {trade['actual_risk']:.6f} {account_currency}"
    )
    if trade.get("actual_risk_quote") is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Actual Risk (price): {trade['actual_risk_quote']:.6f} {price_currency}"
        )
    lines.append(f"Achieved RR:      {trade['achieved_rr']:.2f}x")
    lines.append("")
    lines.append("                 COST BREAKDOWN")
    lines.append("-" * 50)
    lines.append(f"Fees:             {trade['fees']:.6f} {account_currency}")
    if trade.get("fees_quote") is not None and (
        price_currency != account_currency or not math.isclose(conversion_rate, 1.0)
    ):
        lines.append(
            f"Fees (price):     {trade['fees_quote']:.6f} {price_currency}"
        )
    lines.append(
        f"Interest Cost:    {trade['interest_cost']:.6f} {account_currency}"
    )
    if trade.get("funding_rate") is not None:
        lines.append(f"Funding Rate:     {trade['funding_rate'] * 100:.4f}%")
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
        "account": trade.get("account_mode", "live"),
        "trade_mode": trade.get("trade_mode", "linear"),
    }


def save_summary(text: str) -> None:
    """Write ``text`` to ``trade_summary.txt`` in the current directory."""

    path = os.path.join(os.getcwd(), "trade_summary.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"\n✅ Summary saved as '{path}'")


def save_webhook_json(trade: Dict[str, Any]) -> None:
    """Write a small webhook payload for ``trade``."""

    payload = build_webhook_payload(trade)
    with open("trade_webhook.txt", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write(
            "\n\nWEBHOOK FUTURES:\n"
            "https://codex-juan.onrender.com/webhook/cryptocalculator-clone\n"
        )
    print("\n✅ Webhook JSON saved as 'trade_webhook.txt'")


def main() -> None:
    """Entry point for the command line tool."""

    config = load_config("config.json")
    trade = calculate_trade(config)
    summary = display_trade(trade)
    save_summary(summary)
    save_webhook_json(trade)


if __name__ == "__main__":
    main()
