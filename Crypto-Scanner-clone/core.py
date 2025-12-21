"""Core helpers for fetching Bybit market data and calculating indicators."""

import os
import time
import json
import random
import logging
import hashlib
from datetime import datetime, timezone
from bybit_credentials import resolve_bybit_credentials

import asyncio
import requests
from tqdm import tqdm
import httpx
import correlation_math
import percentile_math
from pathlib import Path

BYBIT_LIVE_ENV_PATH = Path(r"E:/ENV/bybit-live.env")

MAX_DUPLICATE_RETRIES = 3


def _load_bybit_live_env() -> bool:
    """Load Bybit API credentials from the shared live env file."""

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_debug_logger() -> logging.Logger:
    """Return a shared debug logger writing to ``logs/scanlog.txt``."""
    if not hasattr(get_debug_logger, "cached_logger"):
        logger = logging.getLogger("debug_logger")
        logger.setLevel(logging.DEBUG)
        log_path = os.path.join(LOG_DIR, "scanlog.txt")
        handler = logging.FileHandler(log_path, mode="a")
        handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(asctime)s %(message)s")
        )
        logger.addHandler(handler)
        get_debug_logger.cached_logger = logger
    return get_debug_logger.cached_logger


def get_auth_headers() -> dict:
    """Return request headers with API key or a generic user agent."""
    _mode, api_key, _secret, _base_url, _key_source = resolve_bybit_credentials()
    if api_key:
        return {"X-BYBIT-API-KEY": api_key}
    return {"User-Agent": "VolumeScannerBot/1.0"}


def get_tradeable_symbols_sorted_by_volume() -> list:
    """Return USDT symbols sorted by 24h turnover descending."""
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    try:
        with tqdm(total=1, desc="Fetching symbols") as pbar:
            response = requests.get(url, headers=get_auth_headers(), timeout=10)
            response.raise_for_status()
            pbar.update(1)
        tickers = response.json().get("result", {}).get("list", [])
        filtered = [
            (item["symbol"], float(item.get("turnover24h", 0)))
            for item in tickers
            if item.get("symbol", "").endswith("USDT")
        ]
        sorted_filtered = sorted(filtered, key=lambda x: x[1], reverse=True)
        return sorted_filtered
    except requests.RequestException as err:
        logging.getLogger("volume_logger").error(
            "Failed to fetch and sort symbols by volume: %s", err
        )
        return []


def stable_chunk_hash(chunk: list) -> str:
    """Return a deterministic MD5 hash for a kline chunk."""
    return hashlib.md5(
        json.dumps(chunk, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_kline_url(symbol: str, interval: str, start: int) -> str:
    """Construct the kline API URL for a symbol/interval starting time."""
    return (
        "https://api.bybit.com/v5/market/kline?category=linear"
        + f"&symbol={symbol}"
        + f"&interval={interval}"
        + f"&start={start}"
        + "&limit=1000"
    )


def get_kline_end_time() -> int:
    """Return the current UTC timestamp rounded down to the nearest 15m."""
    now = datetime.now(timezone.utc)
    floored = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    return int(floored.timestamp() * 1000)


def fetch_with_backoff(url: str, symbol: str, logger: logging.Logger) -> list:
    """Fetch a URL with basic retry/backoff handling for rate limits."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=get_auth_headers(), timeout=10)
            if response.status_code == 429:
                delay = round(random.uniform(1.0, 2.5), 2)
                logger.warning(
                    "[%s] Rate limit hit (429). Retrying in %.2fs...",
                    symbol,
                    delay
                )
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json().get("result", {}).get("list", [])
        except requests.RequestException as err:
            logger.warning(
                "[%s] Request error on attempt %d: %s",
                symbol,
                attempt + 1,
                err
            )
            time.sleep(1)
    logger.error("[%s] Failed all retries. Giving up.", symbol)
    return []


async def fetch_with_backoff_async(
    url: str, symbol: str, logger: logging.Logger, client: httpx.AsyncClient
) -> list:
    """Asynchronously fetch a URL with retry/backoff for rate limits."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.get(url, headers=get_auth_headers(), timeout=10)
            if response.status_code == 429:
                delay = round(random.uniform(1.0, 2.5), 2)
                logger.warning(
                    "[%s] Rate limit hit (429). Retrying in %.2fs...",
                    symbol,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            return response.json().get("result", {}).get("list", [])
        except httpx.RequestError as err:
            logger.warning(
                "[%s] Request error on attempt %d: %s",
                symbol,
                attempt + 1,
                err,
            )
            await asyncio.sleep(1)
    logger.error("[%s] Failed all retries. Giving up.", symbol)
    return []


def fetch_recent_klines(  # pylint: disable=too-many-locals
    symbol: str,
    interval: str = "1",
    total: int = 10080,
    cache: dict | None = None,
) -> list:
    """Return ``total`` klines for ``symbol`` using backoff retry logic."""
    if cache is not None and symbol in cache:
        return cache[symbol]

    logger = get_debug_logger()
    main_logger = logging.getLogger("volume_logger")

    seen_chunks = set()
    consecutive_duplicates = 0
    all_klines = []
    end_time = get_kline_end_time()

    try:
        while len(all_klines) < total:
            start_time = end_time - (1000 * 60 * 1000)
            url = build_kline_url(symbol, interval, start_time)
            chunk = fetch_with_backoff(url, symbol, logger)

            if not chunk:
                logger.debug("[%s] Empty chunk received. Ending fetch.", symbol)
                break

            chunk_key = stable_chunk_hash(chunk)
            if chunk_key in seen_chunks:
                consecutive_duplicates += 1
                logger.debug("[%s] Duplicate chunk #%d detected.", symbol, consecutive_duplicates)
                if consecutive_duplicates >= MAX_DUPLICATE_RETRIES:
                    logger.warning(
                        "%s: Max duplicate retries hit. Only %d klines gathered, expected %d.",
                        symbol,
                        len(all_klines),
                        total
                    )
                    break
                end_time = start_time
                continue

            seen_chunks.add(chunk_key)
            all_klines = chunk + all_klines
            logger.debug("[%s] Total klines so far: %d", symbol, len(all_klines))
            end_time = start_time
            consecutive_duplicates = 0
    except requests.RequestException as err:
        logger.error("[%s] Failed to fetch klines: %s", symbol, err)

    if len(all_klines) < 315:
        main_logger.warning("%s: Only %d klines returned, skipping.", symbol, len(all_klines))
        return []

    result = all_klines[-total:]
    if cache is not None:
        cache[symbol] = result
    return result


async def fetch_recent_klines_async(  # pylint: disable=too-many-locals
    symbol: str,
    interval: str = "1",
    total: int = 10080,
    cache: dict | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list:
    """Asynchronously fetch ``total`` klines for ``symbol``."""
    if cache is not None and symbol in cache:
        return cache[symbol]

    manage_client = client is None
    if manage_client:
        client = httpx.AsyncClient()

    logger = get_debug_logger()
    main_logger = logging.getLogger("volume_logger")

    seen_chunks: set[str] = set()
    consecutive_duplicates = 0
    all_klines: list = []
    end_time = get_kline_end_time()

    try:
        while len(all_klines) < total:
            start_time = end_time - (1000 * 60 * 1000)
            url = build_kline_url(symbol, interval, start_time)
            chunk = await fetch_with_backoff_async(url, symbol, logger, client)

            if not chunk:
                logger.debug("[%s] Empty chunk received. Ending fetch.", symbol)
                break

            chunk_key = stable_chunk_hash(chunk)
            if chunk_key in seen_chunks:
                consecutive_duplicates += 1
                logger.debug("[%s] Duplicate chunk #%d detected.", symbol, consecutive_duplicates)
                if consecutive_duplicates >= MAX_DUPLICATE_RETRIES:
                    logger.warning(
                        "%s: Max duplicate retries hit. Only %d klines gathered, expected %d.",
                        symbol,
                        len(all_klines),
                        total,
                    )
                    break
                end_time = start_time
                continue

            seen_chunks.add(chunk_key)
            all_klines = chunk + all_klines
            logger.debug("[%s] Total klines so far: %d", symbol, len(all_klines))
            end_time = start_time
            consecutive_duplicates = 0
    except httpx.RequestError as err:
        logger.error("[%s] Failed to fetch klines: %s", symbol, err)
    finally:
        if manage_client:
            await client.aclose()

    if len(all_klines) < 315:
        main_logger.warning("%s: Only %d klines returned, skipping.", symbol, len(all_klines))
        return []

    result = all_klines[-total:]
    if cache is not None:
        cache[symbol] = result
    return result


async def fetch_all_recent_klines_async(
    symbols: list[str],
    interval: str = "1",
    total: int = 10080,
) -> dict[str, list]:
    """Fetch klines for all ``symbols`` concurrently and return a cache."""
    cache: dict[str, list] = {}
    async with httpx.AsyncClient() as client:
        tasks = {
            symbol: fetch_recent_klines_async(
                symbol, interval, total, cache, client=client
            )
            for symbol in symbols
        }
        await asyncio.gather(*tasks.values())
    return cache


def get_funding_rate(symbol: str) -> tuple[float, int]:
    """Return the latest funding rate and timestamp for ``symbol``."""
    url = (
        "https://api.bybit.com/v5/market/tickers"
        f"?category=linear&symbol={symbol}"
    )
    fetch_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        response = requests.get(url, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
        item = data.get("result", {}).get("list", [])[0]
        rate = float(item.get("fundingRate", 0))
        ts = int(data.get("time", fetch_time))
        return rate, ts
    except (IndexError, ValueError, KeyError, requests.RequestException):
        logging.getLogger("volume_logger").warning(
            "Failed to fetch funding rate for %s", symbol
        )
        return 0.0, 0


FUNDING_RATE_INTERVALS = {
    "5M": 5,
    "15M": 15,
    "30M": 30,
    "1H": 60,
    "4H": 240,
    "1D": 1440,
    "1W": 10080,
    "1M": 43200,
}


def get_funding_rate_history(symbol: str, limit: int = 200) -> list:
    """Return historical funding rate rows for ``symbol``."""
    url = (
        "https://api.bybit.com/v5/market/funding/history"
        f"?category=linear&symbol={symbol}&limit={limit}"
    )
    try:
        response = requests.get(url, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        rows = response.json().get("result", {}).get("list", [])
        return sorted(
            rows, key=lambda r: int(r.get("fundingRateTimestamp", 0))
        )
    except requests.RequestException:
        logging.getLogger("volume_logger").warning(
            "Failed to fetch funding rate history for %s", symbol
        )
        return []


def get_open_interest_history(symbol: str, interval: str, limit: int = 200) -> list:
    """Return raw open interest rows for ``symbol``."""
    url = (
        "https://api.bybit.com/v5/market/open-interest"
        f"?category=linear&symbol={symbol}&intervalTime={interval}&limit={limit}"
    )
    try:
        response = requests.get(url, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        rows = response.json().get("result", {}).get("list", [])
        return sorted(rows, key=lambda r: int(r.get("timestamp", 0)))
    except requests.RequestException:
        logging.getLogger("volume_logger").warning(
            "Failed to fetch open interest history for %s", symbol
        )
        return []


def get_open_interest_change(symbol: str, interval: str = "1h", limit: int = 24) -> float:
    """Return the open interest percentage change for ``symbol``."""
    rows_sorted = get_open_interest_history(symbol, interval, limit)
    try:
        if len(rows_sorted) < 2:
            raise ValueError("insufficient data")
        first = float(rows_sorted[0].get("openInterest", 0))
        last = float(rows_sorted[-1].get("openInterest", 0))
        if first == 0:
            return 0.0
        return (last - first) / first * 100
    except (ValueError, KeyError, TypeError):
        logging.getLogger("volume_logger").warning(
            "Failed to fetch open interest change for %s", symbol
        )
        return 0.0

def process_symbol(
    symbol: str,
    logger: logging.Logger,
    klines_cache: dict | None = None,
) -> dict:
    """Return volume percentage change metrics for ``symbol`` with percentiles."""
    klines = fetch_recent_klines(symbol, cache=klines_cache)
    if not klines:
        logger.warning("%s skipped: No valid klines returned.", symbol)
        return None
    sorted_klines = sorted(klines, key=lambda k: int(k[0]))

    def gather_changes(size: int) -> list[float]:
        blocks = [
            sorted_klines[i:i + size]
            for i in range(0, len(sorted_klines) - (size - 1), size)
            if len(sorted_klines[i:i + size]) == size
        ]
        values: list[float] = []
        for i in range(20, len(blocks)):
            latest = blocks[i]
            previous = blocks[i - 20:i]
            sum_latest = sum(float(k[5]) for k in latest)
            avg_previous = sum(
                sum(float(k[5]) for k in blk) for blk in previous
            ) / len(previous)
            if avg_previous == 0:
                values.append(0.0)
            else:
                values.append((sum_latest - avg_previous) / avg_previous * 100)
        return values

    result = {"Symbol": symbol}
    for size, label in [(5, "5M"), (15, "15M"), (30, "30M"), (60, "1H"), (240, "4H")]:
        changes = gather_changes(size)
        latest = changes[-1] if changes else 0.0
        percentile = (
            percentile_math.percentile_rank(changes[:-1], latest)
            if len(changes) > 1
            else 0.0
        )
        result[label] = round(latest, 4)
        result[f"{label} Percentile"] = round(percentile, 4)

    return result

def process_symbol_correlation(
    symbol: str,
    btc_klines: list,
    logger: logging.Logger,
    klines_cache: dict | None = None,
) -> dict:
    """Return correlation metrics for ``symbol`` vs BTCUSDT."""
    klines = fetch_recent_klines(symbol, cache=klines_cache)
    if not klines or not btc_klines:
        logger.warning("%s skipped: No valid klines returned for correlation.", symbol)
        return None
    return {
        "Symbol": symbol,
        "5M": round(correlation_math.calculate_price_correlation(klines, btc_klines, 5), 4),
        "15M": round(correlation_math.calculate_price_correlation(klines, btc_klines, 15), 4),
        "30M": round(correlation_math.calculate_price_correlation(klines, btc_klines, 30), 4),
        "1H": round(correlation_math.calculate_price_correlation(klines, btc_klines, 60), 4),
        "4H": round(correlation_math.calculate_price_correlation(klines, btc_klines, 240), 4),
    }


OPEN_INTEREST_INTERVALS = {
    "5M": ("5min", 2),
    "15M": ("15min", 2),
    "30M": ("30min", 2),
    "1H": ("1h", 2),
    "4H": ("4h", 2),
    "1D": ("1d", 2),
    # 1 week and 1 month data are not provided directly by the API
    # so use daily data and request enough rows to cover the period.
    "1W": ("1d", 7),
    "1M": ("1d", 30),
}


def get_open_interest_changes(symbol: str) -> dict:
    """Return open interest % change for multiple intervals."""
    result: dict[str, float] = {}
    for name, (interval, limit) in OPEN_INTEREST_INTERVALS.items():
        result[name] = round(get_open_interest_change(symbol, interval, limit), 4)
    return result


def _gather_open_interest_changes(rows: list, window: int) -> list[float]:
    values = [float(r.get("openInterest", 0)) for r in rows]
    changes: list[float] = []
    for i in range(window, len(values)):
        first = values[i - window]
        last = values[i]
        if first == 0:
            changes.append(0.0)
        else:
            changes.append((last - first) / first * 100)
    return changes


def process_symbol_open_interest(symbol: str, _logger: logging.Logger) -> dict:
    """Return open interest change metrics with percentiles for ``symbol``."""
    result = {"Symbol": symbol}
    for name, (interval, window) in OPEN_INTEREST_INTERVALS.items():
        rows = get_open_interest_history(symbol, interval, 200)
        changes = _gather_open_interest_changes(rows, window)
        latest = changes[-1] if changes else 0.0
        percentile = (
            percentile_math.percentile_rank(changes[:-1], latest)
            if len(changes) > 1
            else 0.0
        )
        result[name] = round(latest, 4)
        result[f"{name} Percentile"] = round(percentile, 4)
    return result


def process_symbol_funding(symbol: str, _logger: logging.Logger) -> dict:
    """Return funding rate metrics across multiple timeframes for ``symbol``."""
    current_rate, ts = get_funding_rate(symbol)
    history = get_funding_rate_history(symbol, 200)
    history.append({"fundingRate": current_rate, "fundingRateTimestamp": ts})
    rows = sorted(history, key=lambda r: int(r.get("fundingRateTimestamp", 0)))

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    result = {"Symbol": symbol, "Funding Rate": current_rate}
    for name, minutes in FUNDING_RATE_INTERVALS.items():
        target = now_ms - minutes * 60 * 1000
        value = 0.0
        for row in reversed(rows):
            ts_row = int(row.get("fundingRateTimestamp", 0))
            if ts_row <= target:
                try:
                    value = float(row.get("fundingRate", 0))
                except (TypeError, ValueError):
                    value = 0.0
                break
        result[name] = value
    return result
