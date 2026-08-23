"""Public Bybit ATR-percentage and liquidity scanner core.

The module is deliberately independent from Bybit account credentials.  Callers
provide a public-market JSON fetcher, which keeps calculation, validation,
caching, and failure behaviour deterministic in tests.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from shared.atomic_json import write_json_file


TIMEFRAMES: tuple[dict[str, object], ...] = (
    {"key": "1m", "label": "1m", "interval": "1", "minutes": 1},
    {"key": "5m", "label": "5m", "interval": "5", "minutes": 5},
    {"key": "1h", "label": "1h", "interval": "60", "minutes": 60},
    {"key": "1D", "label": "1D", "interval": "D", "calendar": "day"},
    {"key": "1W", "label": "1W", "interval": "W", "calendar": "week"},
    {"key": "1Mo", "label": "1Mo", "interval": "M", "calendar": "month"},
)
TIMEFRAME_BY_KEY = {str(item["key"]): item for item in TIMEFRAMES}
TIMEFRAME_KEYS = tuple(TIMEFRAME_BY_KEY)
BYBIT_INTERVAL_BY_KEY = {
    str(item["key"]): str(item["interval"]) for item in TIMEFRAMES
}

DEFAULT_SETTINGS: dict[str, object] = {
    "rank_timeframe": "1m",
    "top_n": 10,
    "atr_length": 14,
    "min_turnover_usdt": 20_000_000.0,
    "max_spread_pct": 0.10,
    "depth_band_pct": 0.10,
    "min_bid_depth_usdt": 25_000.0,
    "min_ask_depth_usdt": 25_000.0,
    "max_book_age_seconds": 30.0,
    "manual_exclusions": [],
    "auto_refresh_seconds": 60,
}

REASON_LABELS = {
    "inactive": "Inactive / not Trading",
    "wrong_product": "Wrong product (requires linear USDT perpetual)",
    "manual_exclusion": "Manual exclusion",
    "turnover_below_minimum": "24h turnover below minimum",
    "spread_above_maximum": "Spread above maximum",
    "bid_depth_below_minimum": "Bid depth below minimum",
    "ask_depth_below_minimum": "Ask depth below minimum",
    "missing_invalid_market_data": "Missing, invalid, crossed, or stale market data",
    "insufficient_atr_history": "Insufficient valid closed-candle ATR history",
    "transient_upstream_failure": "Transient upstream failure",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,40}$")
MAX_MANUAL_EXCLUSIONS = 1000
MAX_MANUAL_EXCLUSION_TEXT_LENGTH = 50_000


class ScannerValidationError(ValueError):
    """A user setting is malformed or outside its supported bound."""


class ScannerUpstreamError(RuntimeError):
    """A required public upstream scope failed."""

    def __init__(self, scope: str, message: str):
        self.scope = str(scope or "upstream")
        super().__init__(str(message or "Public market-data request failed."))


def _finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _bounded_number(
    value: object,
    *,
    field: str,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> int | float:
    number = _finite_number(value)
    if number is None:
        raise ScannerValidationError(f"{field} must be a finite number.")
    if integer and not number.is_integer():
        raise ScannerValidationError(f"{field} must be a whole number.")
    if number < minimum or number > maximum:
        raise ScannerValidationError(
            f"{field} must be between {minimum:g} and {maximum:g}."
        )
    return int(number) if integer else float(number)


def normalize_manual_exclusions(value: object) -> list[str]:
    """Return unique, validated, uppercase Bybit symbols in stable order."""

    if value is None:
        parts: list[object] = []
    elif isinstance(value, str):
        if len(value) > MAX_MANUAL_EXCLUSION_TEXT_LENGTH:
            raise ScannerValidationError(
                f"manual_exclusions text must be at most {MAX_MANUAL_EXCLUSION_TEXT_LENGTH} characters."
            )
        parts = re.split(r"[,\r\n]+", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ScannerValidationError(
            "manual_exclusions must be a comma/newline-separated string or a list."
        )
    if len(parts) > MAX_MANUAL_EXCLUSIONS:
        raise ScannerValidationError(
            f"manual_exclusions must contain at most {MAX_MANUAL_EXCLUSIONS} symbols."
        )

    normalized: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for part in parts:
        symbol = str(part or "").strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_RE.fullmatch(symbol):
            invalid.append(symbol[:60])
            continue
        if symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
            if len(normalized) > MAX_MANUAL_EXCLUSIONS:
                raise ScannerValidationError(
                    f"manual_exclusions must contain at most {MAX_MANUAL_EXCLUSIONS} symbols."
                )
    if invalid:
        raise ScannerValidationError(
            "manual_exclusions contains malformed symbol(s): " + ", ".join(invalid)
        )
    return normalized


def validate_settings(
    payload: Mapping[str, object] | None,
    *,
    base: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate a complete or partial scanner settings payload."""

    incoming = dict(payload or {})
    allowed = set(DEFAULT_SETTINGS)
    unknown = sorted(set(incoming) - allowed)
    if unknown:
        raise ScannerValidationError(
            "Unsupported scanner setting(s): " + ", ".join(unknown)
        )
    merged = dict(DEFAULT_SETTINGS)
    if base:
        merged.update({key: value for key, value in base.items() if key in allowed})
    merged.update(incoming)

    rank_timeframe = str(merged.get("rank_timeframe") or "").strip()
    if rank_timeframe not in TIMEFRAME_BY_KEY:
        raise ScannerValidationError(
            "rank_timeframe must be one of: " + ", ".join(TIMEFRAME_KEYS)
        )
    merged["rank_timeframe"] = rank_timeframe
    merged["top_n"] = _bounded_number(
        merged.get("top_n"), field="top_n", minimum=1, maximum=100, integer=True
    )
    merged["atr_length"] = _bounded_number(
        merged.get("atr_length"),
        field="atr_length",
        minimum=2,
        maximum=100,
        integer=True,
    )
    merged["min_turnover_usdt"] = _bounded_number(
        merged.get("min_turnover_usdt"),
        field="min_turnover_usdt",
        minimum=0,
        maximum=1_000_000_000_000_000,
    )
    merged["max_spread_pct"] = _bounded_number(
        merged.get("max_spread_pct"),
        field="max_spread_pct",
        minimum=0,
        maximum=100,
    )
    merged["depth_band_pct"] = _bounded_number(
        merged.get("depth_band_pct"),
        field="depth_band_pct",
        minimum=0.000001,
        maximum=10,
    )
    for field in ("min_bid_depth_usdt", "min_ask_depth_usdt"):
        merged[field] = _bounded_number(
            merged.get(field),
            field=field,
            minimum=0,
            maximum=1_000_000_000_000_000,
        )
    merged["max_book_age_seconds"] = _bounded_number(
        merged.get("max_book_age_seconds"),
        field="max_book_age_seconds",
        minimum=1,
        maximum=300,
    )
    merged["auto_refresh_seconds"] = _bounded_number(
        merged.get("auto_refresh_seconds"),
        field="auto_refresh_seconds",
        minimum=30,
        maximum=3600,
        integer=True,
    )
    merged["manual_exclusions"] = normalize_manual_exclusions(
        merged.get("manual_exclusions")
    )
    return merged


def _utc_datetime_from_ms(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


def interval_end_ms(start_ms: int, timeframe: str) -> int:
    """Return the exclusive end boundary of one Bybit interval."""

    definition = TIMEFRAME_BY_KEY.get(timeframe)
    if not definition:
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    minutes = definition.get("minutes")
    if minutes is not None:
        return int(start_ms) + int(minutes) * 60_000

    start = _utc_datetime_from_ms(int(start_ms))
    calendar_kind = str(definition.get("calendar") or "")
    if calendar_kind == "day":
        end = start + timedelta(days=1)
    elif calendar_kind == "week":
        end = start + timedelta(days=7)
    elif calendar_kind == "month":
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1)
        else:
            end = start.replace(month=start.month + 1, day=1)
    else:  # pragma: no cover - protected by the constant table
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    return int(end.timestamp() * 1000)


def next_interval_boundary_ms(server_time_ms: int, timeframe: str) -> int:
    definition = TIMEFRAME_BY_KEY.get(timeframe)
    if not definition:
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    minutes = definition.get("minutes")
    if minutes is not None:
        size = int(minutes) * 60_000
        return (int(server_time_ms) // size + 1) * size

    now = _utc_datetime_from_ms(int(server_time_ms))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calendar_kind = str(definition.get("calendar") or "")
    if calendar_kind == "day":
        boundary = midnight + timedelta(days=1)
    elif calendar_kind == "week":
        boundary = midnight + timedelta(days=(7 - midnight.weekday()))
    elif calendar_kind == "month":
        if midnight.month == 12:
            boundary = midnight.replace(year=midnight.year + 1, month=1, day=1)
        else:
            boundary = midnight.replace(month=midnight.month + 1, day=1)
    else:  # pragma: no cover - protected by the constant table
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    return int(boundary.timestamp() * 1000)


def latest_closed_boundary_ms(server_time_ms: int, timeframe: str) -> int:
    """Return the UTC end boundary of the newest fully closed candle."""

    definition = TIMEFRAME_BY_KEY.get(timeframe)
    if not definition:
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    minutes = definition.get("minutes")
    if minutes is not None:
        size = int(minutes) * 60_000
        return int(server_time_ms) // size * size

    now = _utc_datetime_from_ms(int(server_time_ms))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calendar_kind = str(definition.get("calendar") or "")
    if calendar_kind == "day":
        boundary = midnight
    elif calendar_kind == "week":
        boundary = midnight - timedelta(days=midnight.weekday())
    elif calendar_kind == "month":
        boundary = midnight.replace(day=1)
    else:  # pragma: no cover - protected by the constant table
        raise ScannerValidationError(f"Unsupported timeframe: {timeframe}")
    return int(boundary.timestamp() * 1000)


def normalize_closed_klines(
    rows: object,
    *,
    timeframe: str,
    server_time_ms: int,
) -> Optional[list[dict[str, float | int]]]:
    """Normalize Bybit newest-first rows and remove every unclosed candle.

    Invalid rows fail the timeframe closed instead of being silently skipped.
    """

    if not isinstance(rows, list):
        return None
    parsed: list[dict[str, float | int]] = []
    starts: set[int] = set()
    for raw in rows:
        if not isinstance(raw, (list, tuple)) or len(raw) < 5:
            return None
        start_number = _finite_number(raw[0])
        values = [_finite_number(raw[index]) for index in range(1, 5)]
        if start_number is None or not start_number.is_integer() or any(
            value is None for value in values
        ):
            return None
        start_ms = int(start_number)
        open_price, high, low, close = (float(value) for value in values)  # type: ignore[arg-type]
        if (
            start_ms <= 0
            or min(open_price, high, low, close) <= 0
            or high < low
            or high < max(open_price, close)
            or low > min(open_price, close)
            or start_ms in starts
        ):
            return None
        starts.add(start_ms)
        if interval_end_ms(start_ms, timeframe) > int(server_time_ms):
            continue
        parsed.append(
            {
                "start_ms": start_ms,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    parsed.sort(key=lambda row: int(row["start_ms"]))
    for previous, current in zip(parsed, parsed[1:]):
        if int(current["start_ms"]) != interval_end_ms(
            int(previous["start_ms"]), timeframe
        ):
            return None
    if parsed and interval_end_ms(int(parsed[-1]["start_ms"]), timeframe) != latest_closed_boundary_ms(
        int(server_time_ms), timeframe
    ):
        return None
    return parsed


def wilder_atr_percent(
    rows: object,
    *,
    length: int = 14,
    timeframe: str = "1m",
    server_time_ms: int,
) -> Optional[dict[str, float | int]]:
    """Calculate Wilder/RMA ATR divided by the latest closed candle close."""

    if not isinstance(length, int) or isinstance(length, bool) or length < 2:
        return None
    candles = normalize_closed_klines(
        rows, timeframe=timeframe, server_time_ms=server_time_ms
    )
    if candles is None or len(candles) < length:
        return None

    true_ranges: list[float] = []
    previous_close: Optional[float] = None
    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        if not math.isfinite(true_range) or true_range < 0:
            return None
        true_ranges.append(true_range)
        previous_close = float(candle["close"])

    atr = sum(true_ranges[:length]) / float(length)
    for true_range in true_ranges[length:]:
        atr = ((atr * (length - 1)) + true_range) / float(length)
    close = float(candles[-1]["close"])
    if close <= 0 or not math.isfinite(close) or not math.isfinite(atr):
        return None
    value = atr / close * 100.0
    if not math.isfinite(value) or value < 0:
        return None
    candle_start_ms = int(candles[-1]["start_ms"])
    return {
        "value": value,
        "atr": atr,
        "close": close,
        "candle_start_ms": candle_start_ms,
        "candle_end_ms": interval_end_ms(candle_start_ms, timeframe),
        "closed_candle_count": len(candles),
    }


def calculate_spread_percent(bid_value: object, ask_value: object) -> Optional[float]:
    bid = _finite_number(bid_value)
    ask = _finite_number(ask_value)
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2.0
    if midpoint <= 0 or not math.isfinite(midpoint):
        return None
    spread = (ask - bid) / midpoint * 100.0
    return spread if math.isfinite(spread) and spread >= 0 else None


def calculate_orderbook_depth(
    payload: object,
    *,
    midpoint: float,
    band_pct: float,
    server_time_ms: int,
    max_age_seconds: float,
) -> Optional[dict[str, float | int]]:
    """Sum bid/ask quote notional inside an inclusive midpoint band."""

    if not isinstance(payload, dict) or midpoint <= 0 or band_pct <= 0:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    book_timestamp = _finite_number(result.get("ts"))
    if book_timestamp is None:
        book_timestamp = _finite_number(result.get("cts"))
    if book_timestamp is None or not book_timestamp.is_integer():
        return None
    age_ms = int(server_time_ms) - int(book_timestamp)
    if age_ms < -5_000 or age_ms > float(max_age_seconds) * 1000.0:
        return None

    def parse_side(raw_side: object) -> Optional[list[tuple[float, float]]]:
        if not isinstance(raw_side, list) or not raw_side:
            return None
        parsed_side: list[tuple[float, float]] = []
        for level in raw_side:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                return None
            price = _finite_number(level[0])
            quantity = _finite_number(level[1])
            if price is None or quantity is None or price <= 0 or quantity <= 0:
                return None
            parsed_side.append((price, quantity))
        return parsed_side

    bids = parse_side(result.get("b"))
    asks = parse_side(result.get("a"))
    if not bids or not asks:
        return None
    best_bid = max(price for price, _quantity in bids)
    best_ask = min(price for price, _quantity in asks)
    if best_bid >= best_ask:
        return None

    band_fraction = band_pct / 100.0
    bid_floor = midpoint * (1.0 - band_fraction)
    ask_ceiling = midpoint * (1.0 + band_fraction)
    bid_depth = sum(
        price * quantity for price, quantity in bids if bid_floor <= price <= midpoint
    )
    ask_depth = sum(
        price * quantity for price, quantity in asks if midpoint <= price <= ask_ceiling
    )
    if not all(math.isfinite(value) and value >= 0 for value in (bid_depth, ask_depth)):
        return None
    return {
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "book_timestamp_ms": int(book_timestamp),
        "book_age_seconds": max(0.0, age_ms / 1000.0),
    }


def rank_rows(
    rows: Iterable[Mapping[str, object]],
    timeframe: str,
    *,
    top_n: Optional[int] = None,
) -> list[dict[str, object]]:
    if timeframe not in TIMEFRAME_BY_KEY:
        raise ScannerValidationError(f"Unsupported rank timeframe: {timeframe}")
    sortable: list[tuple[float, str, dict[str, object]]] = []
    for original in rows:
        row = dict(original)
        atr_values = row.get("atr_pct")
        value = (
            _finite_number(atr_values.get(timeframe))
            if isinstance(atr_values, Mapping)
            else None
        )
        symbol = str(row.get("symbol") or "").strip().upper()
        if value is None or not symbol:
            continue
        sortable.append((value, symbol, row))
    sortable.sort(key=lambda item: (-item[0], item[1]))
    ranked: list[dict[str, object]] = []
    for index, (_value, _symbol, row) in enumerate(sortable, start=1):
        row["rank"] = index
        ranked.append(row)
    if top_n is not None:
        return ranked[: max(0, int(top_n))]
    return ranked


def format_atr_percent(value: object) -> str:
    number = _finite_number(value)
    return "N/A" if number is None else f"{number:.5f}%"


PublicFetcher = Callable[[str, Dict[str, object]], Awaitable[Dict[str, object]]]


class ATRScannerService:
    """Coordinates one shared scanner refresh and last-known-good state."""

    def __init__(
        self,
        *,
        fetch_json: PublicFetcher,
        settings_path: Path,
        source_base_url: str = "https://api.bybit.com",
        request_concurrency: int = 5,
        request_spacing_seconds: float = 0.05,
        request_retries: int = 2,
        now_ms: Optional[Callable[[], int]] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.fetch_json = fetch_json
        self.settings_path = Path(settings_path)
        self.source_base_url = str(source_base_url).rstrip("/")
        self.request_retries = max(0, int(request_retries))
        self.request_spacing_seconds = max(0.0, float(request_spacing_seconds))
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep
        self._request_semaphore = asyncio.Semaphore(max(1, int(request_concurrency)))
        self._rate_lock = asyncio.Lock()
        self._last_request_started = 0.0
        self._refresh_guard = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task[dict[str, object]]] = None
        self._active_settings: Optional[dict[str, object]] = None
        self._queued_settings_refresh = False
        self._settings_lock = threading.RLock()
        self._last_good: Optional[dict[str, object]] = None
        self._last_result: Optional[dict[str, object]] = None
        self._last_success_local_ms = 0
        self._progress: dict[str, object] = {
            "in_progress": False,
            "phase": "idle",
            "completed": 0,
            "total": 0,
            "detail": "No refresh has run yet.",
        }
        self._atr_cache: dict[tuple[str, str, int], dict[str, object]] = {}
        self._instrument_cache: Optional[dict[str, object]] = None

    def load_settings(self) -> dict[str, object]:
        with self._settings_lock:
            if not self.settings_path.exists():
                return validate_settings(DEFAULT_SETTINGS)
            try:
                raw = self.settings_path.read_text(encoding="utf-8")
                decoded = json.loads(raw)
            except Exception as exc:
                raise ScannerValidationError(
                    f"Scanner settings could not be read: {exc}"
                ) from exc
            if not isinstance(decoded, dict):
                raise ScannerValidationError("Scanner settings must be a JSON object.")
            return validate_settings(decoded)

    def save_settings(self, payload: Mapping[str, object]) -> dict[str, object]:
        with self._settings_lock:
            current = self.load_settings()
            validated = validate_settings(payload, base=current)
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_file(self.settings_path, validated, sort_keys=True)
            return validated

    def reset_settings(self) -> dict[str, object]:
        with self._settings_lock:
            validated = validate_settings(DEFAULT_SETTINGS)
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_file(self.settings_path, validated, sort_keys=True)
            return validated

    def status_payload(self) -> dict[str, object]:
        result = copy.deepcopy(self._last_result)
        if result is None:
            result = {
                "ok": False,
                "state": "loading" if self._progress.get("in_progress") else "not_started",
                "stale": False,
                "partial": False,
                "ranked_rows": [],
                "qualified_rows": [],
                "excluded_rows": [],
            }
        if result.get("stale") is True:
            updated_ms = int(result.get("updated_at_ms") or self._last_success_local_ms or self._now_ms())
            result["stale_age_seconds"] = max(
                0.0, (self._now_ms() - updated_ms) / 1000.0
            )
        result["progress"] = copy.deepcopy(self._progress)
        return result

    async def start_refresh(self, *, manual: bool = False) -> dict[str, object]:
        async with self._refresh_guard:
            settings = self.load_settings()
            if self._refresh_task is not None and not self._refresh_task.done():
                settings_changed = settings != self._active_settings
                if settings_changed:
                    self._queued_settings_refresh = True
                return {
                    "started": False,
                    "shared_in_flight": True,
                    "follow_up_queued": settings_changed,
                    "manual": bool(manual),
                    "progress": copy.deepcopy(self._progress),
                }
            if (
                not manual
                and self._last_good is not None
                and self._now_ms() - self._last_success_local_ms
                < int(settings["auto_refresh_seconds"]) * 1000
            ):
                return {
                    "started": False,
                    "shared_in_flight": False,
                    "cached": True,
                    "manual": False,
                    "progress": copy.deepcopy(self._progress),
                }
            self._active_settings = copy.deepcopy(settings)
            self._queued_settings_refresh = False
            self._refresh_task = asyncio.create_task(
                self._refresh_worker(settings=settings, manual=manual)
            )
            return {
                "started": True,
                "shared_in_flight": False,
                "manual": bool(manual),
                "progress": copy.deepcopy(self._progress),
            }

    async def _refresh_worker(
        self, *, settings: dict[str, object], manual: bool
    ) -> dict[str, object]:
        current_settings = copy.deepcopy(settings)
        current_manual = bool(manual)
        try:
            while True:
                result = await self._run_refresh(
                    settings=current_settings, manual=current_manual
                )
                async with self._refresh_guard:
                    if not self._queued_settings_refresh:
                        return result
                    self._queued_settings_refresh = False
                    current_settings = self.load_settings()
                    self._active_settings = copy.deepcopy(current_settings)
                    current_manual = True
                    self._set_progress(
                        "queued_settings",
                        completed=0,
                        total=0,
                        detail="Applying settings saved during the previous refresh.",
                    )
        finally:
            async with self._refresh_guard:
                self._active_settings = None

    async def refresh(self, *, manual: bool = False) -> dict[str, object]:
        await self.start_refresh(manual=manual)
        task = self._refresh_task
        if task is None:
            return self.status_payload()
        return await asyncio.shield(task)

    async def wait_for_idle(self) -> dict[str, object]:
        task = self._refresh_task
        if task is not None and not task.done():
            await asyncio.shield(task)
        return self.status_payload()

    def _set_progress(
        self, phase: str, *, completed: int, total: int, detail: str
    ) -> None:
        self._progress = {
            "in_progress": phase not in {"complete", "failed", "idle"},
            "phase": phase,
            "completed": max(0, int(completed)),
            "total": max(0, int(total)),
            "detail": str(detail),
        }

    async def _paced_request(
        self, path: str, params: Dict[str, object], *, scope: str
    ) -> Dict[str, object]:
        last_error: Optional[BaseException] = None
        for attempt in range(self.request_retries + 1):
            try:
                async with self._request_semaphore:
                    async with self._rate_lock:
                        elapsed = time.monotonic() - self._last_request_started
                        wait_for = self.request_spacing_seconds - elapsed
                        if wait_for > 0:
                            await self._sleep(wait_for)
                        self._last_request_started = time.monotonic()
                    payload = await self.fetch_json(path, dict(params))
                if not isinstance(payload, dict):
                    raise RuntimeError("response was not a JSON object")
                ret_code = payload.get("retCode", 0)
                if str(ret_code) not in {"0", "0.0"}:
                    raise RuntimeError(
                        f"Bybit retCode={ret_code} retMsg={payload.get('retMsg') or 'unknown'}"
                    )
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < self.request_retries:
                    await self._sleep(0.25 * (2**attempt))
        raise ScannerUpstreamError(scope, f"{scope} failed: {last_error}")

    @staticmethod
    def _server_time_ms(payload: Mapping[str, object]) -> Optional[int]:
        direct = _finite_number(payload.get("time"))
        if direct is not None and direct.is_integer() and direct > 0:
            return int(direct)
        result = payload.get("result")
        if isinstance(result, Mapping):
            seconds = _finite_number(result.get("timeSecond"))
            if seconds is not None and seconds > 0:
                return int(seconds * 1000)
            nano = _finite_number(result.get("timeNano"))
            if nano is not None and nano > 0:
                return int(nano / 1_000_000)
        return None

    async def _resolve_server_time_ms(
        self, ticker_payload: Mapping[str, object]
    ) -> int:
        timestamp = self._server_time_ms(ticker_payload)
        if timestamp is not None:
            return timestamp
        payload = await self._paced_request(
            "/v5/market/time", {}, scope="server_time"
        )
        timestamp = self._server_time_ms(payload)
        if timestamp is None:
            raise ScannerUpstreamError(
                "server_time", "Bybit server time was missing or malformed."
            )
        return timestamp

    async def _fetch_instruments(
        self, *, manual: bool
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        now_ms = self._now_ms()
        cached = self._instrument_cache
        if (
            not manual
            and isinstance(cached, dict)
            and now_ms - int(cached.get("cached_at_ms") or 0) < 15 * 60_000
        ):
            return (
                copy.deepcopy(cached.get("eligible") or []),
                copy.deepcopy(cached.get("excluded") or []),
            )

        all_rows: list[dict[str, object]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(100):
            params: Dict[str, object] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await self._paced_request(
                "/v5/market/instruments-info", params, scope="instruments"
            )
            result = payload.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("list"), list):
                raise ScannerUpstreamError(
                    "instruments", "Instrument result list was missing or malformed."
                )
            all_rows.extend(
                dict(item) for item in result["list"] if isinstance(item, dict)
            )
            next_cursor = str(result.get("nextPageCursor") or "").strip()
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ScannerUpstreamError(
                    "instruments", "Instrument pagination cursor repeated."
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise ScannerUpstreamError(
                "instruments", "Instrument pagination exceeded 100 pages."
            )
        if not all_rows:
            raise ScannerUpstreamError(
                "instruments", "Bybit returned an empty linear instrument universe."
            )

        eligible: list[dict[str, object]] = []
        excluded: list[dict[str, object]] = []
        seen_symbols: set[str] = set()
        for item in all_rows:
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            if str(item.get("status") or "") != "Trading":
                excluded.append(_excluded_row(symbol, ["inactive"]))
                continue
            correct_product = (
                str(item.get("contractType") or "") == "LinearPerpetual"
                and str(item.get("quoteCoin") or "").upper() == "USDT"
                and str(item.get("settleCoin") or "").upper() == "USDT"
                and item.get("isPreListing") is not True
            )
            if not correct_product:
                excluded.append(_excluded_row(symbol, ["wrong_product"]))
                continue
            eligible.append(item)
        eligible.sort(key=lambda item: str(item.get("symbol") or ""))
        excluded.sort(key=lambda item: str(item.get("symbol") or ""))
        self._instrument_cache = {
            "cached_at_ms": now_ms,
            "eligible": copy.deepcopy(eligible),
            "excluded": copy.deepcopy(excluded),
        }
        return eligible, excluded

    async def _fetch_atr(
        self,
        symbol: str,
        timeframe: str,
        *,
        length: int,
        server_time_ms: int,
    ) -> dict[str, object]:
        cache_key = (symbol, timeframe, length)
        cached = self._atr_cache.get(cache_key)
        if cached and server_time_ms < int(cached.get("next_refresh_ms") or 0):
            return copy.deepcopy(cached)
        try:
            payload = await self._paced_request(
                "/v5/market/kline",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "interval": BYBIT_INTERVAL_BY_KEY[timeframe],
                    "limit": 201,
                },
                scope=f"kline:{symbol}:{timeframe}",
            )
            result = payload.get("result")
            rows = result.get("list") if isinstance(result, Mapping) else None
            if not isinstance(rows, list):
                return {
                    "value": None,
                    "status": "error",
                    "reason": "missing_invalid_market_data",
                    "error": "Kline result list was missing or malformed.",
                }
            normalized = normalize_closed_klines(
                rows, timeframe=timeframe, server_time_ms=server_time_ms
            )
            if normalized is None:
                return {
                    "value": None,
                    "status": "error",
                    "reason": "missing_invalid_market_data",
                    "error": "Closed-candle kline history was malformed, gapped, or stale.",
                }
            if len(normalized) < length:
                return {
                    "value": None,
                    "status": "unavailable",
                    "reason": "insufficient_atr_history",
                }
            calculated = wilder_atr_percent(
                rows,
                length=length,
                timeframe=timeframe,
                server_time_ms=server_time_ms,
            )
            if calculated is None:
                return {
                    "value": None,
                    "status": "error",
                    "reason": "missing_invalid_market_data",
                    "error": "ATR could not be calculated from otherwise sufficient closed history.",
                }
            entry: dict[str, object] = {
                **calculated,
                "status": "fresh",
                "stale": False,
                "next_refresh_ms": next_interval_boundary_ms(
                    server_time_ms, timeframe
                ),
            }
            self._atr_cache[cache_key] = copy.deepcopy(entry)
            return entry
        except ScannerUpstreamError as exc:
            if cached and _finite_number(cached.get("value")) is not None:
                stale = copy.deepcopy(cached)
                stale.update(
                    {
                        "status": "stale",
                        "stale": True,
                        "error": str(exc),
                    }
                )
                return stale
            return {
                "value": None,
                "status": "error",
                "reason": "transient_upstream_failure",
                "error": str(exc),
            }

    async def _run_refresh(
        self, *, settings: dict[str, object], manual: bool
    ) -> dict[str, object]:
        started_local_ms = self._now_ms()
        self._set_progress(
            "instruments",
            completed=0,
            total=0,
            detail="Discovering Bybit linear instruments.",
        )
        try:
            result = await self._build_snapshot(
                settings=settings, manual=manual, started_local_ms=started_local_ms
            )
        except Exception as exc:
            scope = exc.scope if isinstance(exc, ScannerUpstreamError) else "scanner"
            error = {"scope": scope, "message": str(exc)}
            if self._last_good is not None:
                stale = copy.deepcopy(self._last_good)
                updated_ms = int(stale.get("updated_at_ms") or started_local_ms)
                stale.update(
                    {
                        "ok": True,
                        "state": "stale",
                        "stale": True,
                        "partial": True,
                        "stale_age_seconds": max(
                            0.0, (self._now_ms() - updated_ms) / 1000.0
                        ),
                        "refresh_error": error,
                        "errors": [error],
                    }
                )
                result = stale
            else:
                result = {
                    "ok": False,
                    "state": "error",
                    "stale": False,
                    "partial": False,
                    "source": _source_payload(self.source_base_url),
                    "settings": settings,
                    "ranked_rows": [],
                    "qualified_rows": [],
                    "excluded_rows": [],
                    "errors": [error],
                    "refresh_error": error,
                }
            self._set_progress(
                "failed", completed=0, total=0, detail=f"Refresh failed: {scope}."
            )
            self._last_result = result
            return copy.deepcopy(result)

        self._last_good = copy.deepcopy(result)
        self._last_result = copy.deepcopy(result)
        self._last_success_local_ms = self._now_ms()
        self._set_progress(
            "complete",
            completed=int(result.get("rank_eligible_count") or 0),
            total=int(result.get("rank_eligible_count") or 0),
            detail="Refresh complete.",
        )
        return copy.deepcopy(result)

    async def _build_snapshot(
        self,
        *,
        settings: dict[str, object],
        manual: bool,
        started_local_ms: int,
    ) -> dict[str, object]:
        instruments, excluded = await self._fetch_instruments(manual=manual)
        manual_exclusions = set(settings["manual_exclusions"])
        active_instruments: list[dict[str, object]] = []
        for item in instruments:
            symbol = str(item.get("symbol") or "").upper()
            if symbol in manual_exclusions:
                excluded.append(_excluded_row(symbol, ["manual_exclusion"]))
            else:
                active_instruments.append(item)

        self._set_progress(
            "tickers",
            completed=0,
            total=len(active_instruments),
            detail="Loading one current linear ticker snapshot.",
        )
        ticker_payload = await self._paced_request(
            "/v5/market/tickers", {"category": "linear"}, scope="tickers"
        )
        server_time_ms = await self._resolve_server_time_ms(ticker_payload)
        ticker_result = ticker_payload.get("result")
        ticker_list = (
            ticker_result.get("list") if isinstance(ticker_result, Mapping) else None
        )
        if not isinstance(ticker_list, list):
            raise ScannerUpstreamError("tickers", "Ticker result list was missing.")
        scoped_errors: list[dict[str, str]] = []
        tickers = {
            str(item.get("symbol") or "").upper(): item
            for item in ticker_list
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        }

        liquidity_candidates: list[dict[str, object]] = []
        ticker_data_failures = 0
        for instrument in active_instruments:
            symbol = str(instrument.get("symbol") or "").upper()
            ticker = tickers.get(symbol)
            if not isinstance(ticker, dict):
                excluded.append(_excluded_row(symbol, ["missing_invalid_market_data"]))
                ticker_data_failures += 1
                scoped_errors.append(
                    {
                        "scope": f"ticker:{symbol}",
                        "message": "Ticker was missing for an eligible instrument.",
                    }
                )
                continue
            turnover = _finite_number(ticker.get("turnover24h"))
            bid = _finite_number(ticker.get("bid1Price"))
            ask = _finite_number(ticker.get("ask1Price"))
            spread = calculate_spread_percent(bid, ask)
            reasons: list[str] = []
            if turnover is None or turnover < 0 or spread is None or bid is None or ask is None:
                reasons.append("missing_invalid_market_data")
                ticker_data_failures += 1
                scoped_errors.append(
                    {
                        "scope": f"ticker:{symbol}",
                        "message": "Ticker turnover or bid/ask data was missing or invalid.",
                    }
                )
            else:
                if turnover < float(settings["min_turnover_usdt"]):
                    reasons.append("turnover_below_minimum")
                if spread > float(settings["max_spread_pct"]):
                    reasons.append("spread_above_maximum")
            if reasons:
                excluded.append(
                    _excluded_row(
                        symbol,
                        reasons,
                        turnover24h_usdt=turnover,
                        spread_pct=spread,
                    )
                )
                continue
            liquidity_candidates.append(
                {
                    "symbol": symbol,
                    "turnover24h_usdt": turnover,
                    "spread_pct": spread,
                    "midpoint": (float(bid) + float(ask)) / 2.0,
                }
            )
        if active_instruments and ticker_data_failures == len(active_instruments):
            raise ScannerUpstreamError(
                "tickers",
                "Every eligible instrument ticker was missing or invalid; refusing an empty success.",
            )

        self._set_progress(
            "orderbooks",
            completed=0,
            total=len(liquidity_candidates),
            detail="Checking bid and ask depth for turnover/spread-qualified symbols.",
        )
        progress_lock = asyncio.Lock()
        book_completed = 0

        async def qualify_book(
            candidate: dict[str, object],
        ) -> tuple[str, dict[str, object], Optional[dict[str, str]]]:
            nonlocal book_completed
            symbol = str(candidate["symbol"])
            try:
                payload = await self._paced_request(
                    "/v5/market/orderbook",
                    {"category": "linear", "symbol": symbol, "limit": 1000},
                    scope=f"orderbook:{symbol}",
                )
                book_server_time_ms = self._server_time_ms(payload) or self._now_ms()
                depth = calculate_orderbook_depth(
                    payload,
                    midpoint=float(candidate["midpoint"]),
                    band_pct=float(settings["depth_band_pct"]),
                    server_time_ms=book_server_time_ms,
                    max_age_seconds=float(settings["max_book_age_seconds"]),
                )
                if depth is None:
                    return (
                        "data_failure",
                        _excluded_row(
                            symbol,
                            ["missing_invalid_market_data"],
                            **_market_fields(candidate),
                        ),
                        {
                            "scope": f"orderbook:{symbol}",
                            "message": "Order-book depth or freshness was missing or invalid.",
                        },
                    )
                reasons: list[str] = []
                if float(depth["bid_depth_usdt"]) < float(
                    settings["min_bid_depth_usdt"]
                ):
                    reasons.append("bid_depth_below_minimum")
                if float(depth["ask_depth_usdt"]) < float(
                    settings["min_ask_depth_usdt"]
                ):
                    reasons.append("ask_depth_below_minimum")
                combined = {**candidate, **depth}
                if reasons:
                    return (
                        "excluded",
                        _excluded_row(symbol, reasons, **_market_fields(combined)),
                        None,
                    )
                combined["liquidity_status"] = "Qualified"
                return "qualified", combined, None
            except ScannerUpstreamError as exc:
                return (
                    "data_failure",
                    _excluded_row(
                        symbol,
                        ["transient_upstream_failure"],
                        error=str(exc),
                        **_market_fields(candidate),
                    ),
                    {"scope": f"orderbook:{symbol}", "message": str(exc)},
                )
            finally:
                async with progress_lock:
                    book_completed += 1
                    self._set_progress(
                        "orderbooks",
                        completed=book_completed,
                        total=len(liquidity_candidates),
                        detail=f"Checked {book_completed} of {len(liquidity_candidates)} order books.",
                    )

        book_results = await asyncio.gather(
            *(qualify_book(candidate) for candidate in liquidity_candidates)
        )
        liquidity_qualified: list[dict[str, object]] = []
        book_data_failures = 0
        for state, row, error in book_results:
            if state == "qualified":
                liquidity_qualified.append(row)
            else:
                excluded.append(row)
            if state == "data_failure":
                book_data_failures += 1
            if error is not None:
                scoped_errors.append(error)
        if liquidity_candidates and book_data_failures == len(liquidity_candidates):
            raise ScannerUpstreamError(
                "orderbooks",
                "Every required order-book acquisition was unavailable or invalid; refusing an empty success.",
            )
        base_excluded_rows = copy.deepcopy(excluded)

        self._set_progress(
            "atr",
            completed=0,
            total=len(liquidity_qualified),
            detail="Calculating six closed-candle Wilder ATR readings.",
        )
        atr_completed = 0

        async def calculate_symbol(row: dict[str, object]) -> dict[str, object]:
            nonlocal atr_completed
            symbol = str(row["symbol"])
            results = await asyncio.gather(
                *(
                    self._fetch_atr(
                        symbol,
                        timeframe,
                        length=int(settings["atr_length"]),
                        server_time_ms=server_time_ms,
                    )
                    for timeframe in TIMEFRAME_KEYS
                )
            )
            atr_details = dict(zip(TIMEFRAME_KEYS, results))
            enriched = dict(row)
            enriched["atr_pct"] = {
                key: _finite_number(atr_details[key].get("value"))
                for key in TIMEFRAME_KEYS
            }
            enriched["atr_status"] = {
                key: str(atr_details[key].get("status") or "unavailable")
                for key in TIMEFRAME_KEYS
            }
            enriched["atr_reason"] = {
                key: str(atr_details[key].get("reason") or "")
                for key in TIMEFRAME_KEYS
                if atr_details[key].get("reason")
            }
            enriched["atr_candle_end_ms"] = {
                key: atr_details[key].get("candle_end_ms") for key in TIMEFRAME_KEYS
            }
            enriched["stale"] = any(
                bool(atr_details[key].get("stale")) for key in TIMEFRAME_KEYS
            )
            enriched["errors"] = [
                str(atr_details[key].get("error"))
                for key in TIMEFRAME_KEYS
                if atr_details[key].get("error")
            ]
            enriched["atr_errors"] = {
                key: str(atr_details[key].get("error"))
                for key in TIMEFRAME_KEYS
                if atr_details[key].get("error")
            }
            async with progress_lock:
                atr_completed += 1
                self._set_progress(
                    "atr",
                    completed=atr_completed,
                    total=len(liquidity_qualified),
                    detail=f"Calculated {atr_completed} of {len(liquidity_qualified)} symbols.",
                )
            return enriched

        calculated_rows = await asyncio.gather(
            *(calculate_symbol(row) for row in liquidity_qualified)
        )
        selected_timeframe = str(settings["rank_timeframe"])
        rank_eligible: list[dict[str, object]] = []
        selected_error_count = 0
        any_missing_atr = False
        for row in calculated_rows:
            atr_status = row.get("atr_status") or {}
            atr_errors = row.get("atr_errors") or {}
            atr_pct = row.get("atr_pct") or {}
            if isinstance(atr_status, Mapping):
                for timeframe in TIMEFRAME_KEYS:
                    status = str(atr_status.get(timeframe) or "unavailable")
                    if _finite_number(atr_pct.get(timeframe)) is None:  # type: ignore[union-attr]
                        any_missing_atr = True
                    if status in {"error", "stale"}:
                        message = (
                            str(atr_errors.get(timeframe))  # type: ignore[union-attr]
                            if isinstance(atr_errors, Mapping) and atr_errors.get(timeframe)
                            else f"ATR {status} for {row['symbol']} {timeframe}."
                        )
                        scoped_errors.append(
                            {
                                "scope": f"kline:{row['symbol']}:{timeframe}",
                                "message": message,
                            }
                        )
            selected_value = (row.get("atr_pct") or {}).get(selected_timeframe)  # type: ignore[union-attr]
            if _finite_number(selected_value) is None:
                selected_status = str(
                    (row.get("atr_status") or {}).get(selected_timeframe)  # type: ignore[union-attr]
                    or "unavailable"
                )
                selected_reason = str(
                    (row.get("atr_reason") or {}).get(selected_timeframe)  # type: ignore[union-attr]
                    or ""
                )
                reason = selected_reason or (
                    "transient_upstream_failure"
                    if selected_status == "error"
                    else "insufficient_atr_history"
                )
                excluded.append(
                    _excluded_row(
                        str(row["symbol"]),
                        [reason],
                        **_market_fields(row),
                        atr_pct=row.get("atr_pct"),
                        atr_status=row.get("atr_status"),
                        atr_reason=row.get("atr_reason"),
                    )
                )
                if selected_status == "error":
                    selected_error_count += 1
                continue
            rank_eligible.append(row)

        ranked_all = rank_rows(rank_eligible, selected_timeframe)
        ranked_rows = ranked_all[: int(settings["top_n"])]
        qualified_rows = calculated_rows
        excluded.sort(key=lambda row: str(row.get("symbol") or ""))
        reason_counts: Counter[str] = Counter()
        for row in excluded:
            reason_counts.update(set(row.get("reasons") or []))

        stale_rows = sum(1 for row in calculated_rows if row.get("stale"))
        partial = bool(scoped_errors or stale_rows or any_missing_atr)
        if calculated_rows and selected_error_count == len(calculated_rows):
            raise ScannerUpstreamError(
                "klines",
                "Every rank-eligible ATR acquisition failed; refusing an empty success.",
            )
        updated_iso = datetime.fromtimestamp(
            server_time_ms / 1000.0, tz=timezone.utc
        ).isoformat()
        return {
            "ok": True,
            "state": "partial" if partial else "fresh",
            "stale": False,
            "partial": partial,
            "source": _source_payload(self.source_base_url),
            "settings": copy.deepcopy(settings),
            "ranking_basis": {
                "metric": "Wilder/RMA ATR divided by the same timeframe candle close, multiplied by 100",
                "atr_length": int(settings["atr_length"]),
                "selected_timeframe": selected_timeframe,
                "candle_basis": "last closed candle",
                "timeframes": list(TIMEFRAME_KEYS),
                "liquidity_gate": "All configured turnover, spread, bid-depth, ask-depth, validity, freshness, and manual-exclusion checks use AND.",
            },
            "updated_at": updated_iso,
            "updated_at_ms": server_time_ms,
            "refresh_started_at_ms": started_local_ms,
            "universe_count": len(instruments) + sum(
                1
                for row in excluded
                if "inactive" in row.get("reasons", [])
                or "wrong_product" in row.get("reasons", [])
            ),
            "tradable_usdt_perpetual_count": len(instruments),
            "liquidity_qualified_count": len(liquidity_qualified),
            "rank_eligible_count": len(ranked_all),
            "displayed_count": len(ranked_rows),
            "excluded_count": len(excluded),
            "excluded_reason_counts": dict(sorted(reason_counts.items())),
            "ranked_rows": ranked_rows,
            "qualified_rows": qualified_rows,
            "excluded_rows": excluded,
            "base_excluded_rows": base_excluded_rows,
            "errors": scoped_errors,
            "stale_row_count": stale_rows,
            "help": "Turnover, spread, and order-book depth are liquidity proxies; they cannot guarantee fills or future liquidity.",
        }


def _market_fields(row: Mapping[str, object]) -> dict[str, object]:
    return {
        key: row.get(key)
        for key in (
            "turnover24h_usdt",
            "spread_pct",
            "bid_depth_usdt",
            "ask_depth_usdt",
            "book_timestamp_ms",
            "book_age_seconds",
        )
        if row.get(key) is not None
    }


def _excluded_row(
    symbol: str,
    reasons: Sequence[str],
    **details: object,
) -> dict[str, object]:
    unique_reasons = list(dict.fromkeys(str(reason) for reason in reasons if reason))
    return {
        "symbol": str(symbol or "").strip().upper(),
        "liquidity_status": "Excluded",
        "reasons": unique_reasons,
        "reason_labels": [REASON_LABELS.get(reason, reason) for reason in unique_reasons],
        **details,
    }


def _source_payload(base_url: str) -> dict[str, object]:
    return {
        "name": "Bybit V5 public live market data",
        "base_url": str(base_url).rstrip("/"),
        "category": "linear",
        "authenticated": False,
        "credential_source": None,
    }
