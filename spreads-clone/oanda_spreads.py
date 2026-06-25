"""OANDA bid/ask candle spread fetcher."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared import oanda_api
from spread_core import TimeframeConfig, lookback_target_for_timeframe, make_sample, parse_time, spread_pct_from_bid_ask


MAX_OANDA_CANDLE_COUNT = 5000
DEFAULT_OANDA_CANDLE_COUNT = 5000


def _mode_from_env() -> str:
    raw = (os.getenv("OANDA_ENV") or os.getenv("OANDA_MODE") or "live").strip().lower()
    if raw in {"demo", "practice", "test"}:
        return "demo"
    return "live"


def _count_from_env() -> int:
    raw = os.getenv("SPREAD_MONITOR_OANDA_CANDLE_COUNT", str(DEFAULT_OANDA_CANDLE_COUNT))
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = DEFAULT_OANDA_CANDLE_COUNT
    return max(1, min(MAX_OANDA_CANDLE_COUNT, count))


def _candle_spread_sample(candle: Dict[str, Any]) -> Optional[Dict[str, object]]:
    bid = candle.get("bid")
    ask = candle.get("ask")
    if not isinstance(bid, dict) or not isinstance(ask, dict):
        return None
    try:
        spread_pct = spread_pct_from_bid_ask(bid.get("c"), ask.get("c"))
    except ValueError:
        return None
    return make_sample(candle.get("time"), spread_pct)


def parse_oanda_bid_ask_candles(payload: Dict[str, Any]) -> Dict[str, object]:
    candles = payload.get("candles")
    if not isinstance(candles, list):
        return {"samples": [], "latest": None}

    complete_samples: List[Dict[str, object]] = []
    latest: Optional[Dict[str, object]] = None
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        sample = _candle_spread_sample(candle)
        if sample is None:
            continue
        latest = sample
        if candle.get("complete") is True:
            complete_samples.append(sample)
    return {"samples": complete_samples, "latest": latest}


def parse_oanda_lookback_candles(payload: Dict[str, Any], target_at: datetime) -> Dict[str, object]:
    candles = payload.get("candles")
    if not isinstance(candles, list):
        return {"samples": [], "latest": None, "target_at": _iso(target_at)}

    complete_samples: List[Dict[str, object]] = []
    selected: Optional[Dict[str, object]] = None
    target = target_at.astimezone(timezone.utc)
    for candle in candles:
        if not isinstance(candle, dict) or candle.get("complete") is not True:
            continue
        sample = _candle_spread_sample(candle)
        if sample is None:
            continue
        complete_samples.append(sample)
        sample_time = parse_time(sample.get("time"))
        if sample_time is not None and sample_time <= target:
            selected = sample
    return {"samples": complete_samples, "latest": selected, "target_at": _iso(target)}


def _iso(value: datetime) -> str:
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _first_price(levels: object) -> Optional[object]:
    if not isinstance(levels, list):
        return None
    for level in levels:
        if isinstance(level, dict) and level.get("price") not in (None, ""):
            return level.get("price")
    return None


def parse_oanda_pricing(payload: Dict[str, Any]) -> Dict[str, Dict[str, object]]:
    prices = payload.get("prices")
    if not isinstance(prices, list):
        return {}

    results: Dict[str, Dict[str, object]] = {}
    for price in prices:
        if not isinstance(price, dict):
            continue
        instrument = str(price.get("instrument") or "").strip().upper()
        if not instrument:
            continue
        bid = _first_price(price.get("bids"))
        ask = _first_price(price.get("asks"))
        if bid is None:
            bid = price.get("closeoutBid")
        if ask is None:
            ask = price.get("closeoutAsk")
        try:
            spread_pct = spread_pct_from_bid_ask(bid, ask)
        except ValueError as exc:
            results[instrument] = {"samples": [], "latest": None, "error": str(exc)}
            continue
        sample = make_sample(price.get("time"), spread_pct)
        if sample is None:
            results[instrument] = {"samples": [], "latest": None, "error": "OANDA current spread is unavailable."}
            continue
        results[instrument] = {"samples": [sample], "latest": sample}
    return results


def fetch_oanda_current_spreads(
    instruments: Iterable[str],
    context: Optional[Dict[str, object]] = None,
    *,
    mode: Optional[str] = None,
    request_func: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, object]]:
    requested = sorted(dict.fromkeys(str(item).strip().upper() for item in instruments if str(item).strip()))
    if not requested:
        return {}

    request = request_func or oanda_api._request
    resolved_mode = mode or _mode_from_env()
    account_id = oanda_api._account_id(resolved_mode)
    timeout = 10
    if isinstance(context, dict):
        try:
            timeout = int(float(context.get("request_timeout_seconds", timeout)))
        except (TypeError, ValueError):
            timeout = 10
    timeout = max(1, min(timeout, 60))

    data = request(
        "GET",
        f"/accounts/{account_id}/pricing",
        mode=resolved_mode,
        account_id=account_id,
        params={"instruments": ",".join(requested)},
        timeout=timeout,
    )
    parsed = parse_oanda_pricing(data)
    for instrument in requested:
        parsed.setdefault(
            instrument,
            {"samples": [], "latest": None, "error": "No OANDA pricing returned for this instrument."},
        )
    return parsed


def fetch_oanda_spread_samples(
    instrument: str,
    timeframe: TimeframeConfig,
    context: Optional[Dict[str, object]] = None,
    *,
    mode: Optional[str] = None,
    request_func: Optional[Callable[..., Dict[str, Any]]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    count: Optional[int] = None,
) -> Dict[str, object]:
    """Fetch bid/ask candles and return normalized spread samples.

    The endpoint intentionally omits a leading ``/v3`` because
    ``shared.oanda_api._request`` normalizes the base URL to include it.
    """

    request = request_func or oanda_api._request
    requested_count = count
    if requested_count is None and isinstance(context, dict):
        raw_count = context.get("requested_count")
        try:
            requested_count = int(raw_count) if raw_count is not None else None
        except (TypeError, ValueError):
            requested_count = None
    reference_end = end
    if reference_end is None and isinstance(context, dict):
        reference_end = parse_time(context.get("started_at"))
    reference_end = reference_end or datetime.now(timezone.utc)
    target_at = lookback_target_for_timeframe(timeframe, reference_end)

    params: Dict[str, object] = {
        "granularity": timeframe.oanda_granularity,
        "price": "BA",
        "count": max(1, min(MAX_OANDA_CANDLE_COUNT, requested_count or _count_from_env())),
        "to": _iso(reference_end),
    }
    if start is not None:
        params["from"] = _iso(start)
    if end is not None:
        params["to"] = _iso(end)
    timeout = 10
    if isinstance(context, dict):
        try:
            timeout = int(float(context.get("request_timeout_seconds", timeout)))
        except (TypeError, ValueError):
            timeout = 10
    timeout = max(1, min(timeout, 60))
    data = request(
        "GET",
        f"/instruments/{instrument}/candles",
        mode=mode or _mode_from_env(),
        params=params,
        timeout=timeout,
    )
    parsed = parse_oanda_lookback_candles(data, target_at)
    if not parsed.get("latest") and not parsed.get("samples"):
        parsed["error"] = "No bid/ask candle spread data returned."
    elif not parsed.get("latest"):
        parsed["error"] = f"No complete bid/ask candle at or before lookback target {_iso(target_at)}."
    return parsed


def fetch_oanda_spread_windowed(
    instrument: str,
    timeframe: TimeframeConfig,
    *,
    start: datetime,
    end: datetime,
    mode: Optional[str] = None,
    request_func: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, object]:
    """Fetch a bounded range, preserving the OANDA count limit per request."""

    parsed = fetch_oanda_spread_samples(
        instrument,
        timeframe,
        mode=mode,
        request_func=request_func,
        start=start,
        end=end,
        count=MAX_OANDA_CANDLE_COUNT,
    )
    return parsed


def get_available_oanda_symbols(mode: Optional[str] = None) -> List[str]:
    try:
        symbols = oanda_api.get_available_instruments(mode or _mode_from_env())
    except Exception:
        return []
    return sorted(str(item) for item in symbols if str(item).strip())


def filter_oanda_symbols(symbols: Iterable[str], available: Iterable[str]) -> List[str]:
    available_set = {str(item).upper() for item in available}
    return [symbol for symbol in symbols if symbol.upper() in available_set]
