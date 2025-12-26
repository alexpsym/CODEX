"""Exchange adapter implementations for the crypto calculator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod
import hashlib
import hmac
import os
from pathlib import Path
import time
from urllib.parse import urlencode

import requests
from bybit_credentials import resolve_bybit_credentials_for

BYBIT_LIVE_ENV_PATH = Path(r"E:/ENV/bybit-live.env")

BYBIT_SPOT_URL = "https://api.bybit.com/v5/market/tickers?category=spot"
BYBIT_LINEAR_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_INSTRUMENT_INFO_SPOT = (
    "https://api.bybit.com/v5/market/instruments-info?category=spot"
)
BYBIT_INSTRUMENT_INFO_LINEAR = (
    "https://api.bybit.com/v5/market/instruments-info?category=linear"
)
BYBIT_BALANCE_URL = "https://api.bybit.com/v5/account/wallet-balance"

SPOT_TRADING_FEE_RATE = 0.001
LINEAR_TRADING_FEE_RATE = 0.0006
SPOT_INTEREST_RATE_PER_HOUR = 0.000084

COINSPOT_SPOT_FEE_RATE = 0.001  # 0.1% maker/taker fee for market orders


def _load_bybit_live_env() -> bool:
    """Load Bybit API credentials from the shared live .env file."""

    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        load_dotenv = None  # type: ignore

    if not BYBIT_LIVE_ENV_PATH.exists():
        return False

    if load_dotenv is not None:
        return bool(load_dotenv(BYBIT_LIVE_ENV_PATH, override=True))

    loaded = False
    for line in BYBIT_LIVE_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()
        loaded = True
    return loaded


_load_bybit_live_env()


@dataclass
class InstrumentInfo:
    """Metadata for an exchange instrument."""

    tick_size: float
    min_qty: float
    qty_step: float


class ExchangeAdapter(ABC):
    """Common interface for exchange integrations."""

    @abstractmethod
    def get_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        """Return the latest traded price for ``symbol``."""

    @abstractmethod
    def get_instrument_info(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> InstrumentInfo:
        """Return tick size and lot size settings for ``symbol``."""

    @abstractmethod
    def get_fee_rate(self, trade_mode: str) -> float:
        """Return the trading fee rate for the selected market."""

    def get_funding_rate(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        """Return the most recent funding rate, if available."""

        return None

    def get_account_balance(self, config: Dict[str, Any]) -> float:
        """Return the spendable balance for the configured account."""

        raise NotImplementedError


class BybitAdapter(ExchangeAdapter):
    """Adapter that speaks to the Bybit REST API."""

    def get_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        url = BYBIT_SPOT_URL if trade_mode == "spot" else BYBIT_LINEAR_URL
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        for ticker in resp.json().get("result", {}).get("list", []):
            if ticker.get("symbol") == symbol:
                return float(ticker.get("lastPrice"))
        raise ValueError(f"Symbol {symbol} not found.")

    def get_instrument_info(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> InstrumentInfo:
        url = (
            BYBIT_INSTRUMENT_INFO_SPOT
            if trade_mode == "spot"
            else BYBIT_INSTRUMENT_INFO_LINEAR
        )
        resp = requests.get(url, params={"symbol": symbol}, timeout=10)
        resp.raise_for_status()
        for instr in resp.json().get("result", {}).get("list", []):
            if instr.get("symbol") == symbol:
                tick_size = float(instr.get("priceFilter", {}).get("tickSize", 0))
                lot = instr.get("lotSizeFilter", {})
                min_qty = float(lot.get("minTrdQty", lot.get("minOrderQty", 0)))
                qty_step = float(lot.get("qtyStep", lot.get("qty_step", min_qty or 1)))
                return InstrumentInfo(tick_size=tick_size, min_qty=min_qty, qty_step=qty_step)
        raise ValueError(f"Instrument {symbol} not found.")

    def get_fee_rate(self, trade_mode: str) -> float:
        return SPOT_TRADING_FEE_RATE if trade_mode == "spot" else LINEAR_TRADING_FEE_RATE

    def get_funding_rate(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        if trade_mode != "linear":
            return None
        resp = requests.get(BYBIT_LINEAR_URL, params={"symbol": symbol}, timeout=10)
        resp.raise_for_status()
        for ticker in resp.json().get("result", {}).get("list", []):
            if ticker.get("symbol") == symbol:
                return float(ticker.get("fundingRate", 0.0))
        raise ValueError(f"Funding rate for {symbol} not found.")

    def get_account_balance(self, config: Dict[str, Any]) -> float:
        coin = config.get("account_coin", "USDT")
        account_type = config.get("account_type", "UNIFIED")
        account_mode = str(config.get("account_mode", "live")).lower()
        if account_mode not in {"live", "demo"}:
            account_mode = "live"

        _mode, api_key, api_secret, base_url, key_source = resolve_bybit_credentials_for(
            "demo" if account_mode == "demo" else "live"
        )
        if not api_key or not api_secret:
            raise EnvironmentError(
                "Bybit API credentials are missing. Provide BYBIT_API_KEY1/BYBIT_API_SECRET1 "
                "(or KEY2 for demo) or legacy BYBIT_API_KEY/BYBIT_API_SECRET."
            )
        print(
            f"Bybit balance request using account_mode={account_mode} "
            f"base_url={base_url} key_source={key_source}",
            flush=True,
        )

        params = {"accountType": account_type}
        if account_mode != "demo":
            params["coin"] = coin
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

        url = f"{base_url.rstrip('/') + '/v5/account/wallet-balance'}?{query}"
        print(
            f"Bybit balance request account_mode={account_mode} base_url={base_url} "
            f"path=/v5/account/wallet-balance params={params} key_source={key_source}",
            flush=True,
        )
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        payload = resp.json()
        results = payload.get("result", {}).get("list", [])
        coin_entries: list[str] = []
        for item in results:
            for bal in item.get("coin", []):
                symbol = bal.get("coin")
                if symbol:
                    coin_entries.append(str(symbol))
                if symbol == coin:
                    return float(
                        bal.get("availableToTrade", bal.get("walletBalance", 0))
                    )
        print(
            "Bybit balance response parsed.",
            f"result_list_count={len(results)}",
            f"coin_entries={coin_entries[:20]}",
            flush=True,
        )
        if results:
            fallback = results[0].get("totalEquity")
            if fallback is not None:
                return float(fallback)
        raise ValueError(f"Balance for {coin} not found.")


class CoinSpotAdapter(ExchangeAdapter):
    """Adapter that relies on the ``coinspot`` package."""

    def __init__(self) -> None:
        self._public_client = None

    def _require_assets(self, config: Optional[Dict[str, Any]]) -> tuple[str, str]:
        if not config:
            raise ValueError(
                "CoinSpot configuration requires 'base_asset' and 'quote_asset' entries."
            )
        base_asset = config.get("base_asset")
        quote_asset = config.get("quote_asset")
        if not base_asset or not quote_asset:
            raise ValueError(
                "CoinSpot configuration requires 'base_asset' and 'quote_asset' entries."
            )
        return str(base_asset).upper(), str(quote_asset).upper()

    def _public(self):
        if self._public_client is None:
            try:
                from coinspot import PublicAPIV2  # type: ignore
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError(
                    "CoinSpot support requires the 'coinspot' package. Install it with 'pip install coinspot'."
                ) from exc
            self._public_client = PublicAPIV2()
        return self._public_client

    def _readonly(self, api_key: str, api_secret: str):
        try:
            from coinspot import ReadOnlyAPIV2  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "CoinSpot support requires the 'coinspot' package. Install it with 'pip install coinspot'."
            ) from exc
        return ReadOnlyAPIV2(api_key, api_secret)

    def get_current_price(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> float:
        base_asset, quote_asset = self._require_assets(config)
        market = quote_asset
        resp = self._public().latest(base_asset, market)
        price_info = resp.get("prices", {}) if isinstance(resp, dict) else {}
        if isinstance(price_info, dict) and "last" in price_info:
            return float(price_info["last"])
        raise ValueError(
            f"CoinSpot price not available for {base_asset}/{quote_asset}: {resp!r}"
        )

    def get_instrument_info(
        self, symbol: str, trade_mode: str, config: Optional[Dict[str, Any]] = None
    ) -> InstrumentInfo:
        if not config:
            raise ValueError(
                "CoinSpot configuration requires manual 'tick_size', 'qty_step', and 'min_qty' values."
            )
        try:
            tick_size = float(config["tick_size"])
            qty_step = float(config.get("qty_step", config["tick_size"]))
            min_qty = float(config.get("min_qty", qty_step))
        except KeyError as exc:
            raise ValueError(
                "CoinSpot configuration requires 'tick_size' (and optionally 'qty_step'/'min_qty')."
            ) from exc
        return InstrumentInfo(tick_size=tick_size, min_qty=min_qty, qty_step=qty_step)

    def get_fee_rate(self, trade_mode: str) -> float:
        return COINSPOT_SPOT_FEE_RATE

    def get_account_balance(self, config: Dict[str, Any]) -> float:
        api_key = os.environ.get("COINSPOT_API_KEY")
        api_secret = os.environ.get("COINSPOT_API_SECRET")
        if not api_key or not api_secret:
            raise EnvironmentError(
                "CoinSpot mode requires COINSPOT_API_KEY and COINSPOT_API_SECRET environment variables."
            )
        coin = config.get("account_coin")
        if not coin:
            _base, coin = self._require_assets(config)
        resp = self._readonly(api_key, api_secret).wallet_balance(coin)
        if isinstance(resp, dict):
            if "balance" in resp and isinstance(resp["balance"], dict):
                info = resp["balance"]
                for key in ("balance", "audbalance"):
                    if key in info:
                        return float(info[key])
            balances = resp.get("balances")
            if isinstance(balances, list):
                for item in balances:
                    info = item.get(coin)
                    if isinstance(info, dict):
                        for key in ("balance", "audbalance"):
                            if key in info:
                                return float(info[key])
        raise ValueError(f"Balance for {coin} not found in CoinSpot response: {resp!r}")


_ADAPTERS: Dict[str, ExchangeAdapter] = {
    "bybit": BybitAdapter(),
    "coinspot": CoinSpotAdapter(),
}


def get_exchange_adapter(name: str) -> ExchangeAdapter:
    """Return a configured adapter for ``name``."""

    try:
        return _ADAPTERS[name.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported exchange '{name}'.") from exc


__all__ = [
    "COINSPOT_SPOT_FEE_RATE",
    "ExchangeAdapter",
    "InstrumentInfo",
    "SPOT_TRADING_FEE_RATE",
    "LINEAR_TRADING_FEE_RATE",
    "SPOT_INTEREST_RATE_PER_HOUR",
    "BybitAdapter",
    "CoinSpotAdapter",
    "get_exchange_adapter",
]
