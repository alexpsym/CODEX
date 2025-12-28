"""Core utilities for fetching and computing options metrics."""

from datetime import datetime, timezone
from urllib.parse import urlencode
from collections import defaultdict
import math

import requests
import pytz

from ivlog import get_logger

BASE_URL = "https://api.bybit.com"
LOCAL_TZ = pytz.timezone("Australia/Brisbane")
logger = get_logger(__name__)

IV_TIMEFRAMES = {
    "1m": 0.45, "5m": 0.42, "15m": 0.39, "30m": 0.37, "1h": 0.35,
    "4h": 0.33, "1d": 0.30, "1w": 0.28, "1mo": 0.25,
}

TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
    "4h": 240, "1d": 1440, "1w": 10080, "1mo": 43200,
}

MINUTES_PER_YEAR = 52 * 7 * 24 * 60


def safe_get(url, params=None):
    """Perform a GET request and return parsed JSON."""
    try:
        full_url = f"{url}?{urlencode(params)}" if params else url
        resp = requests.get(full_url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("API request failed: %s", exc)
        return {}


def fetch_spot_price(symbol):
    """Return latest spot price for the given symbol."""
    data = safe_get(f"{BASE_URL}/v5/market/tickers", {"symbol": symbol, "category": "spot"})
    if data and 'result' in data and 'list' in data['result']:
        return float(data['result']['list'][0]['lastPrice'])
    return None


def fetch_options(symbol):
    """Fetch option chain for the given symbol."""
    base_coin = symbol.replace("USDT", "")
    data = safe_get(f"{BASE_URL}/v5/market/tickers", {"category": "option", "baseCoin": base_coin})
    if not data:
        return []
    options = []
    for ticker in data.get('result', {}).get('list', []):
        try:
            parts = ticker['symbol'].split('-')
            expiry = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
            option = {
                'expiry': expiry,
                'strike': float(parts[2]),
                'markIv': float(ticker.get('markIv', 0)),
                'delta': float(ticker.get('delta', 0)),
                'type': parts[3],
                'volume': float(ticker.get('volume24h', 0)),
                'openInterest': float(ticker.get('openInterest', 0)),
            }
            options.append(option)
        except Exception:  # pylint: disable=broad-except
            continue
    return options


def select_nearest_expiry_group(options, expiry=None):
    """Return option contracts for the nearest or given expiry."""
    grouped = defaultdict(list)
    for opt in options:
        grouped[opt['expiry']].append(opt)

    future = [e for e in grouped if e > datetime.now(timezone.utc)]
    future.sort()
    if not future:
        return []

    if expiry:
        for e in future:
            if e.date() == expiry.date():
                return grouped[e]
        logger.warning(
            "Requested expiry %s not found, using nearest available option.",
            expiry.date(),
        )

    return grouped[future[0]]


def compute_skew(group):
    """Compute 25-delta call/put skew."""
    call_25 = min(
        (o for o in group if o['type'] == 'C'),
        key=lambda o: abs(abs(o['delta']) - 0.25),
        default=None,
    )
    put_25 = min(
        (o for o in group if o['type'] == 'P'),
        key=lambda o: abs(abs(o['delta']) - 0.25),
        default=None,
    )
    return (call_25['markIv'] - put_25['markIv']) * 100 if call_25 and put_25 else None


def compute_volumes(group):
    """Return total call and put volume."""
    calls = sum(o['volume'] for o in group if o['type'] == 'C')
    puts = sum(o['volume'] for o in group if o['type'] == 'P')
    return int(calls), int(puts)


def compute_open_interest(group):
    """Return total call and put open interest."""
    calls = sum(o['openInterest'] for o in group if o['type'] == 'C')
    puts = sum(o['openInterest'] for o in group if o['type'] == 'P')
    return int(calls), int(puts)


def update_scaled_iv(timeframe):
    """Scale annual IV to the given timeframe."""
    factor = TIMEFRAME_MINUTES[timeframe] / MINUTES_PER_YEAR
    return IV_TIMEFRAMES[timeframe] * math.sqrt(factor)


def compute_snapshot(symbol: str, timeframe: str, expiry: datetime | None = None) -> dict:
    """Return a JSON-serializable snapshot for the IV indicator."""
    spot = fetch_spot_price(symbol)
    if spot is None:
        return {"error": "Spot fetch failed."}

    options = fetch_options(symbol)
    group = select_nearest_expiry_group(options, expiry)
    if not group:
        return {"error": f"No options found for symbol {symbol}."}

    scaled_iv = update_scaled_iv(timeframe)
    move = spot * scaled_iv
    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    expiry_dt = group[0]["expiry"].astimezone(LOCAL_TZ)
    expiry_str = expiry_dt.strftime("%Y-%m-%d")
    skew = compute_skew(group)
    call_vol, put_vol = compute_volumes(group)
    call_oi, put_oi = compute_open_interest(group)

    return {
        "timestamp": now.isoformat(),
        "time_local": now.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "timeframe": timeframe,
        "iv_percent": scaled_iv * 100,
        "spot": spot,
        "upper": spot + move,
        "lower": spot - move,
        "move": move,
        "skew": skew,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "expiry": expiry_str,
    }
