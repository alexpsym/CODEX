"""Core spread monitor calculations, cache handling, and payload assembly."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional


LOW_MAX_PERCENTILE = 50
HIGH_MIN_PERCENTILE = 80
REFRESH_INTERVAL_SECONDS = 300
OANDA_REQUEST_TIMEOUT_SECONDS = 10
OANDA_REFRESH_TIMEOUT_SECONDS = 120
OANDA_REFRESH_CONCURRENCY = 4
OANDA_PRICING_BATCH_SIZE = 50
MAX_CACHE_SAMPLES = 1500
INITIAL_BASELINE_SAMPLES = 750
INCREMENTAL_REFRESH_SAMPLES = 250


@dataclass(frozen=True)
class TimeframeConfig:
    label: str
    oanda_granularity: str
    seconds: Optional[int]
    mt5_lookback_days: int


TIMEFRAMES: tuple[TimeframeConfig, ...] = (
    TimeframeConfig("1M", "M1", 60, 7),
    TimeframeConfig("5M", "M5", 300, 14),
    TimeframeConfig("15M", "M15", 900, 21),
    TimeframeConfig("30M", "M30", 1800, 30),
    TimeframeConfig("1H", "H1", 3600, 60),
    TimeframeConfig("4H", "H4", 14400, 120),
    TimeframeConfig("D", "D", 86400, 365),
    TimeframeConfig("W", "W", 604800, 730),
    TimeframeConfig("M", "M", None, 1095),
)

TIMEFRAME_BY_LABEL = {tf.label: tf for tf in TIMEFRAMES}
TIMEFRAME_LABELS = [tf.label for tf in TIMEFRAMES]
OANDA_CURRENT_TIMEFRAME_LABEL = "CURRENT"
OANDA_CURRENT_COLUMN_KEY = "current_spread"
OANDA_CURRENT_COLUMN_LABEL = "Current Spread"
MIN_CURRENT_SPREAD_PERCENTILE_SAMPLES = 5

BROKERS = ("oanda", "pepperstone")
OANDA_ONLY_BROKERS = ("oanda",)

PEPPERSTONE_LEGACY_ERROR_TOKENS = (
    "pepperstone",
    "mt5",
    "metatrader",
    "terminal initialize",
    "terminal:",
    "authorization failed",
    "import-file",
    "import file",
    "pepperstone|",
)


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def refresh_interval_from_env(env: Optional[Dict[str, str]] = None) -> int:
    source = os.environ if env is None else env
    return _bounded_int(
        source.get("SPREAD_MONITOR_REFRESH_SECONDS", REFRESH_INTERVAL_SECONDS),
        REFRESH_INTERVAL_SECONDS,
        minimum=30,
        maximum=3600,
    )


def _message_mentions_pepperstone_legacy(value: object) -> bool:
    text = str(value or "").casefold()
    return any(token in text for token in PEPPERSTONE_LEGACY_ERROR_TOKENS)


def _oanda_only_brokers(brokers: Iterable[str]) -> bool:
    broker_list = tuple(str(item).strip().lower() for item in brokers if str(item).strip())
    return broker_list == OANDA_ONLY_BROKERS


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def lookback_target_for_timeframe(timeframe: TimeframeConfig, now: Optional[datetime] = None) -> datetime:
    """Return the historical point represented by a table column."""

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    if timeframe.label == "M":
        return reference - timedelta(days=30)
    seconds = timeframe.seconds if timeframe.seconds is not None else 60
    return reference - timedelta(seconds=seconds)


def coerce_float(value: object) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def spread_pct_from_bid_ask(bid: object, ask: object) -> float:
    bid_value = coerce_float(bid)
    ask_value = coerce_float(ask)
    if bid_value is None or ask_value is None:
        raise ValueError("Bid and ask must be numeric.")
    midpoint = (ask_value + bid_value) / 2.0
    if midpoint <= 0:
        raise ValueError("Bid/ask midpoint must be positive.")
    return ((ask_value - bid_value) / midpoint) * 100.0


def format_spread_pct(value: object) -> str:
    spread_value = coerce_float(value)
    if spread_value is None:
        return ""
    places = 5 if abs(spread_value) < 0.01 else 4
    return f"{spread_value:.{places}f}%"


def percentile_rank(latest: object, samples: Iterable[object]) -> Optional[float]:
    latest_value = coerce_float(latest)
    if latest_value is None:
        return None
    values = [v for v in (coerce_float(item) for item in samples) if v is not None]
    if not values:
        return None
    below_or_equal = sum(1 for value in values if value <= latest_value)
    return (below_or_equal / len(values)) * 100.0


def classify_spread(latest: object, samples: Iterable[object]) -> str:
    rank = percentile_rank(latest, samples)
    if rank is None:
        return "unavailable"
    if rank <= LOW_MAX_PERCENTILE:
        return "low"
    if rank >= HIGH_MIN_PERCENTILE:
        return "high"
    return "medium"


def _cache_key(broker: str, symbol: str, timeframe: str) -> str:
    return f"{broker}|{symbol}|{timeframe}"


def split_cache_key(key: str) -> tuple[str, str, str]:
    parts = key.split("|", 2)
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def make_sample(time_value: object, spread_pct: object, spread_points: object = None) -> Optional[Dict[str, object]]:
    spread_value = coerce_float(spread_pct)
    if spread_value is None or spread_value < 0:
        return None
    dt = parse_time(time_value)
    timestamp = dt.isoformat().replace("+00:00", "Z") if dt else utc_now_iso()
    sample = {"time": timestamp, "spread_pct": spread_value}
    points_value = coerce_float(spread_points)
    if points_value is not None and points_value >= 0:
        sample["spread_points"] = points_value
    return sample


def _sample_sort_key(sample: Dict[str, object]) -> str:
    return str(sample.get("time") or "")


def _merge_samples(existing: Iterable[object], incoming: Iterable[object], limit: int) -> List[Dict[str, object]]:
    by_time: Dict[str, Dict[str, object]] = {}
    for source in (existing, incoming):
        for item in source:
            if not isinstance(item, dict):
                continue
            sample = make_sample(item.get("time"), item.get("spread_pct"), item.get("spread_points"))
            if sample is None:
                continue
            by_time[str(sample["time"])] = sample
    ordered = sorted(by_time.values(), key=_sample_sort_key)
    if limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


def broker_cell(record: Optional[Dict[str, object]], *, min_percentile_samples: int = 1) -> Dict[str, object]:
    if not record:
        return {
            "spread_pct": None,
            "display": "",
            "category": "unavailable",
            "updated_at": "",
            "error": "No data cached for this broker/symbol/timeframe.",
        }

    latest = record.get("latest")
    latest_sample = latest if isinstance(latest, dict) else None
    spread_value = coerce_float(latest_sample.get("spread_pct") if latest_sample else None)
    spread_points = coerce_float(latest_sample.get("spread_points") if latest_sample else None)
    error = str(record.get("error") or "").strip()
    if spread_value is None or spread_value < 0:
        return {
            "spread_pct": None,
            "spread_points": None,
            "display": "",
            "category": "unavailable",
            "updated_at": str(record.get("last_success") or ""),
            "error": error or "Spread data unavailable.",
        }

    samples = record.get("samples")
    sample_values = [
        sample.get("spread_pct")
        for sample in samples
        if isinstance(sample, dict)
    ] if isinstance(samples, list) else []
    if spread_value == 0:
        category = "low"
    else:
        category = (
            "neutral"
            if len(sample_values) < max(1, int(min_percentile_samples))
            else classify_spread(spread_value, sample_values)
        )
    return {
        "spread_pct": spread_value,
        "spread_points": spread_points,
        "display": format_spread_pct(spread_value),
        "category": category,
        "updated_at": str(latest_sample.get("time") or record.get("last_success") or ""),
        "error": error,
    }


def default_cache_payload() -> Dict[str, object]:
    return {
        "version": 1,
        "generated_at": utc_now_iso(),
        "last_refresh_started_at": "",
        "last_refresh_finished_at": "",
        "symbols": [],
        "records": {},
        "warnings": [],
        "errors": [],
    }


def _symbols_from_cache_payload(
    cache: Dict[str, object],
    *,
    allowed_brokers: Optional[Iterable[str]] = None,
) -> List[str]:
    allowed = {str(item).strip().lower() for item in (allowed_brokers or []) if str(item).strip()}
    records = cache.get("records")
    if allowed and isinstance(records, dict):
        found: List[str] = []
        for key in records:
            broker, symbol, _timeframe = split_cache_key(str(key))
            if broker in allowed and symbol:
                found.append(symbol)
        if found:
            return sorted(dict.fromkeys(found))

    symbols = cache.get("symbols")
    result = [str(item).strip() for item in symbols if str(item).strip()] if isinstance(symbols, list) else []
    if result:
        return sorted(dict.fromkeys(result))
    found: List[str] = []
    if isinstance(records, dict):
        for key in records:
            broker, symbol, _timeframe = split_cache_key(str(key))
            if symbol and (not allowed or broker in allowed):
                found.append(symbol)
    return sorted(dict.fromkeys(found))


def _filtered_messages_for_brokers(values: object, brokers: Iterable[str]) -> List[str]:
    messages = [str(item).strip() for item in (values or []) if str(item).strip()] if isinstance(values, list) else []
    if _oanda_only_brokers(brokers):
        messages = [message for message in messages if not _message_mentions_pepperstone_legacy(message)]
    return messages


def _record_for_payload(record: object, broker: str) -> Optional[Dict[str, object]]:
    if not isinstance(record, dict):
        return None
    payload = dict(record)
    if broker == "oanda" and _message_mentions_pepperstone_legacy(payload.get("error")):
        payload["error"] = ""
    return payload


def _sanitize_cache_for_brokers(cache: Dict[str, object], brokers: Iterable[str]) -> Dict[str, object]:
    if not _oanda_only_brokers(brokers):
        return cache
    sanitized = dict(cache)
    records = sanitized.get("records")
    if isinstance(records, dict):
        sanitized["records"] = {
            key: value
            for key, value in records.items()
            if split_cache_key(str(key))[0] == "oanda"
        }
    sanitized["warnings"] = _filtered_messages_for_brokers(sanitized.get("warnings"), brokers)
    sanitized["errors"] = _filtered_messages_for_brokers(sanitized.get("errors"), brokers)
    return sanitized


def build_spread_payload(
    cache: Dict[str, object],
    *,
    brokers: Iterable[str] = OANDA_ONLY_BROKERS,
    refresh_status: Optional[Dict[str, object]] = None,
    refresh_interval_seconds: int = REFRESH_INTERVAL_SECONDS,
    empty_message: str = "No OANDA spread data is available yet.",
    current_only: bool = False,
) -> Dict[str, object]:
    broker_list = tuple(dict.fromkeys(str(item).strip().lower() for item in brokers if str(item).strip()))
    current_only = bool(current_only and _oanda_only_brokers(broker_list))
    cache = _sanitize_cache_for_brokers(cache, broker_list)
    symbols = _symbols_from_cache_payload(cache, allowed_brokers=broker_list)
    records = cache.get("records")
    record_map = records if isinstance(records, dict) else {}
    if current_only:
        declared_symbols = [
            str(item).strip()
            for item in (cache.get("symbols") or [])
            if str(item).strip()
        ] if isinstance(cache.get("symbols"), list) else []
        symbols = sorted(dict.fromkeys([*declared_symbols, *symbols]))
    rows: List[Dict[str, object]] = []

    if current_only:
        for symbol in symbols:
            record = record_map.get(_cache_key("oanda", symbol, OANDA_CURRENT_TIMEFRAME_LABEL))
            spread_payload = broker_cell(
                _record_for_payload(record, "oanda"),
                min_percentile_samples=MIN_CURRENT_SPREAD_PERCENTILE_SAMPLES,
            )
            rows.append(
                {
                    "symbol": symbol,
                    "display_symbol": symbol.replace("_", "/"),
                    OANDA_CURRENT_COLUMN_KEY: spread_payload,
                    "cells": {OANDA_CURRENT_TIMEFRAME_LABEL: {"oanda": spread_payload}},
                }
            )
    else:
        for symbol in symbols:
            cells: Dict[str, object] = {}
            symbol_has_data = False
            symbol_has_record = False
            for timeframe in TIMEFRAME_LABELS:
                cell: Dict[str, object] = {}
                for broker in broker_list:
                    record = record_map.get(_cache_key(broker, symbol, timeframe))
                    if isinstance(record, dict):
                        symbol_has_record = True
                    broker_payload = broker_cell(_record_for_payload(record, broker))
                    if broker_payload["spread_pct"] is not None:
                        symbol_has_data = True
                    cell[broker] = broker_payload
                    if broker == "pepperstone":
                        cell["pepperstone_razor"] = broker_payload
                cells[timeframe] = cell
            if symbol_has_data or (broker_list == ("pepperstone",) and symbol_has_record):
                rows.append(
                    {
                        "symbol": symbol,
                        "display_symbol": symbol.replace("_", "/"),
                        "cells": cells,
                    }
                )

    refresh = dict(refresh_status or {"state": "idle", "started_at": "", "finished_at": "", "error": "", "warnings": []})
    refresh_warnings = _filtered_messages_for_brokers(refresh.get("warnings"), broker_list)
    refresh["warnings"] = refresh_warnings
    refresh_error = str(refresh.get("error") or "").strip()
    if _oanda_only_brokers(broker_list) and _message_mentions_pepperstone_legacy(refresh_error):
        refresh["error"] = ""
    errors = _filtered_messages_for_brokers(cache.get("errors"), broker_list)
    warnings = _filtered_messages_for_brokers(cache.get("warnings"), broker_list)
    refresh_state = str(refresh.get("state") or "idle")
    stale_fallback = _oanda_only_brokers(broker_list) and bool(rows) and refresh_state in {"failed", "timed_out"}
    if stale_fallback:
        last_success = str(cache.get("last_refresh_finished_at") or "")
        suffix = f" Last successful refresh: {last_success}." if last_success else ""
        warnings.append(f"Showing stale/fallback cached OANDA spread data.{suffix}")
    elif current_only and rows and refresh_state not in {"running"}:
        last_success = str(cache.get("last_refresh_finished_at") or "")
        last_success_dt = parse_time(last_success)
        if last_success_dt is None:
            warnings.append("Showing cached OANDA current spread data; refresh to update.")
        else:
            age_seconds = (datetime.now(timezone.utc) - last_success_dt).total_seconds()
            if refresh_interval_seconds > 0 and age_seconds > refresh_interval_seconds:
                warnings.append(f"Showing cached OANDA current spread data from {last_success}; refresh to update.")
    if not rows and not errors:
        errors.append(empty_message)

    columns = (
        [
            {"key": "symbol", "label": "Instrument"},
            {"key": OANDA_CURRENT_COLUMN_KEY, "label": OANDA_CURRENT_COLUMN_LABEL},
        ]
        if current_only
        else [{"key": "symbol", "label": "Instrument"}, *({"key": tf, "label": tf} for tf in TIMEFRAME_LABELS)]
    )

    return {
        "ok": bool(rows) and not bool(errors),
        "generated_at": str(cache.get("generated_at") or utc_now_iso()),
        "refresh_interval_seconds": refresh_interval_seconds,
        "refresh": refresh,
        "refresh_state": refresh_state,
        "stale_fallback": stale_fallback,
        "symbols": [row["symbol"] for row in rows],
        "timeframes": [] if current_only else list(TIMEFRAME_LABELS),
        "columns": columns,
        "current_only": current_only,
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
        "last_refresh_started_at": str(cache.get("last_refresh_started_at") or ""),
        "last_refresh_finished_at": str(cache.get("last_refresh_finished_at") or ""),
        "last_imported_at": str(cache.get("last_imported_at") or ""),
        "source_path": str(cache.get("source_path") or ""),
        "source_filename": str(cache.get("source_filename") or ""),
    }


class SpreadMonitorState:
    """Owns the cache and one-at-a-time refresh behavior for the monitor."""

    def __init__(
        self,
        cache_path: Path,
        *,
        symbol_provider: Callable[[], Iterable[str]],
        oanda_fetcher: Optional[Callable[[str, TimeframeConfig, Dict[str, object]], Dict[str, object]]] = None,
        oanda_current_fetcher: Optional[Callable[[Iterable[str], Dict[str, object]], Dict[str, object]]] = None,
        mt5_fetcher: Optional[Callable[[str, TimeframeConfig, Dict[str, object]], Dict[str, object]]] = None,
        mt5_preflight: Optional[Callable[[], Dict[str, object]]] = None,
        brokers: Iterable[str] = OANDA_ONLY_BROKERS,
        max_samples: int = MAX_CACHE_SAMPLES,
        refresh_interval_seconds: Optional[int] = None,
        request_timeout_seconds: int = OANDA_REQUEST_TIMEOUT_SECONDS,
        global_refresh_timeout_seconds: int = OANDA_REFRESH_TIMEOUT_SECONDS,
        oanda_concurrency: int = OANDA_REFRESH_CONCURRENCY,
        oanda_pricing_batch_size: int = OANDA_PRICING_BATCH_SIZE,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.symbol_provider = symbol_provider
        self.oanda_fetcher = oanda_fetcher
        self.oanda_current_fetcher = oanda_current_fetcher
        self.mt5_fetcher = mt5_fetcher
        self.mt5_preflight = mt5_preflight
        self.brokers = tuple(dict.fromkeys(str(item).strip().lower() for item in brokers if str(item).strip()))
        self.max_samples = max_samples
        self.refresh_interval_seconds = refresh_interval_seconds if refresh_interval_seconds is not None else refresh_interval_from_env()
        self.request_timeout_seconds = _bounded_int(request_timeout_seconds, OANDA_REQUEST_TIMEOUT_SECONDS, minimum=1, maximum=60)
        self.global_refresh_timeout_seconds = _bounded_int(global_refresh_timeout_seconds, OANDA_REFRESH_TIMEOUT_SECONDS, minimum=5, maximum=900)
        self.oanda_concurrency = _bounded_int(oanda_concurrency, OANDA_REFRESH_CONCURRENCY, minimum=1, maximum=16)
        self.oanda_pricing_batch_size = _bounded_int(oanda_pricing_batch_size, OANDA_PRICING_BATCH_SIZE, minimum=1, maximum=100)
        self.refresh_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._cache = self._load_cache()
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_generation = 0
        self._last_refresh_duration_seconds: Optional[float] = None
        self._refresh_status: Dict[str, object] = {
            "state": "idle",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "warnings": [],
            "diagnostics": self._base_refresh_diagnostics(),
        }

    def _load_cache(self) -> Dict[str, object]:
        if not self.cache_path.exists():
            return default_cache_payload()
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return default_cache_payload()
        if not isinstance(payload, dict):
            return default_cache_payload()
        payload.setdefault("records", {})
        payload.setdefault("warnings", [])
        payload.setdefault("errors", [])
        payload.setdefault("symbols", [])
        return _sanitize_cache_for_brokers(payload, self.brokers)

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _record_for(self, broker: str, symbol: str, timeframe: str) -> Dict[str, object]:
        records = self._cache.setdefault("records", {})
        if not isinstance(records, dict):
            records = {}
            self._cache["records"] = records
        key = _cache_key(broker, symbol, timeframe)
        record = records.get(key)
        if not isinstance(record, dict):
            record = {
                "broker": broker,
                "symbol": symbol,
                "timeframe": timeframe,
                "samples": [],
                "latest": None,
                "last_success": "",
                "ttl_seconds": self.refresh_interval_seconds,
                "error": "",
            }
            records[key] = record
        return record

    def _fetch_context(self, broker: str, symbol: str, timeframe: str, base: Dict[str, object]) -> Dict[str, object]:
        with self._cache_lock:
            record = self._record_for(broker, symbol, timeframe)
            cached_samples = record.get("samples")
            has_cached_baseline = isinstance(cached_samples, list) and bool(cached_samples)
        context = dict(base)
        context["has_cached_baseline"] = has_cached_baseline
        context["requested_count"] = INCREMENTAL_REFRESH_SAMPLES if has_cached_baseline else INITIAL_BASELINE_SAMPLES
        return context

    def _update_record(
        self,
        broker: str,
        symbol: str,
        timeframe: str,
        result: Dict[str, object],
    ) -> None:
        record = self._record_for(broker, symbol, timeframe)
        error = str(result.get("error") or "").strip()
        if error:
            record["error"] = error
            record["latest"] = None
            record["last_failure"] = utc_now_iso()
            return

        incoming_samples = result.get("samples")
        samples_list = incoming_samples if isinstance(incoming_samples, list) else []
        valid_incoming_samples = _merge_samples([], samples_list, self.max_samples)

        latest = result.get("latest")
        latest_sample = latest if isinstance(latest, dict) else None
        normalized_latest = make_sample(latest_sample.get("time"), latest_sample.get("spread_pct")) if latest_sample is not None else None
        if normalized_latest is None and valid_incoming_samples:
            normalized_latest = valid_incoming_samples[-1]
        if normalized_latest is None:
            record["error"] = "Spread data unavailable."
            record["latest"] = None
            record["last_failure"] = utc_now_iso()
            return

        record["samples"] = _merge_samples(record.get("samples") or [], valid_incoming_samples, self.max_samples)
        record["latest"] = normalized_latest
        record["last_success"] = utc_now_iso()
        record["error"] = ""

    def _set_refresh_status(
        self,
        state: str,
        *,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        error: Optional[str] = None,
        warnings: Optional[Iterable[object]] = None,
        diagnostics: Optional[Dict[str, object]] = None,
    ) -> None:
        with self._cache_lock:
            current_started = str(self._refresh_status.get("started_at") or "")
            merged_diagnostics = dict(self._refresh_status.get("diagnostics") or self._base_refresh_diagnostics())
            if diagnostics:
                merged_diagnostics.update(diagnostics)
            started_value = started_at if started_at is not None else current_started
            finished_value = finished_at if finished_at is not None else str(self._refresh_status.get("finished_at") or "")
            elapsed = self._elapsed_seconds(started_value, finished_value)
            if elapsed is not None:
                merged_diagnostics["elapsed_seconds"] = elapsed
                if state != "running":
                    self._last_refresh_duration_seconds = elapsed
            merged_diagnostics["configured_interval_seconds"] = self.refresh_interval_seconds
            merged_diagnostics["last_refresh_duration_seconds"] = self._last_refresh_duration_seconds
            merged_diagnostics["interval_safe"] = self._interval_safe()
            merged_diagnostics["global_timeout_seconds"] = self.global_refresh_timeout_seconds
            merged_diagnostics["request_timeout_seconds"] = self.request_timeout_seconds
            merged_diagnostics["concurrency"] = self.oanda_concurrency
            self._refresh_status = {
                "state": state,
                "started_at": started_value,
                "finished_at": finished_value,
                "error": error if error is not None else "",
                "warnings": [str(item) for item in (warnings or []) if str(item).strip()],
                "diagnostics": merged_diagnostics,
                "global_timeout_seconds": self.global_refresh_timeout_seconds,
                "request_timeout_seconds": self.request_timeout_seconds,
            }

    def _current_refresh_status(self) -> Dict[str, object]:
        self._mark_stale_running_if_needed()
        with self._cache_lock:
            status = dict(self._refresh_status)
            diagnostics = dict(status.get("diagnostics") or self._base_refresh_diagnostics())
            elapsed = self._elapsed_seconds(status.get("started_at"), status.get("finished_at"))
            if elapsed is not None:
                diagnostics["elapsed_seconds"] = elapsed
            diagnostics["configured_interval_seconds"] = self.refresh_interval_seconds
            diagnostics["last_refresh_duration_seconds"] = self._last_refresh_duration_seconds
            diagnostics["interval_safe"] = self._interval_safe()
            status["diagnostics"] = diagnostics
            if elapsed is not None:
                status["elapsed_seconds"] = elapsed
            return status

    def _base_refresh_diagnostics(self) -> Dict[str, object]:
        selected = ",".join(self.brokers) if self.brokers else ""
        timeframe_count = 1 if self._oanda_uses_current_pricing() else len(TIMEFRAMES)
        return {
            "selected_broker": selected,
            "symbol_count": 0,
            "timeframe_count": timeframe_count,
            "total_requests_planned": 0,
            "completed_request_count": 0,
            "failed_request_count": 0,
            "failed_symbol_count": 0,
            "skipped_request_count": 0,
            "started_at": "",
            "elapsed_seconds": 0.0,
            "current_symbol": "",
            "current_timeframe": "",
            "configured_interval_seconds": self.refresh_interval_seconds,
            "last_refresh_duration_seconds": self._last_refresh_duration_seconds,
            "interval_safe": self._interval_safe(),
            "global_timeout_seconds": self.global_refresh_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "concurrency": self.oanda_concurrency,
            "pricing_batch_size": self.oanda_pricing_batch_size,
        }

    def _interval_safe(self) -> Optional[bool]:
        if self._last_refresh_duration_seconds is None:
            return None
        return self._last_refresh_duration_seconds < self.refresh_interval_seconds

    def _oanda_uses_current_pricing(self) -> bool:
        return "oanda" in self.brokers and self.oanda_current_fetcher is not None

    def _elapsed_seconds(self, started_at: object, finished_at: object = "") -> Optional[float]:
        started = parse_time(started_at)
        if started is None:
            return None
        finished = parse_time(finished_at) or datetime.now(timezone.utc)
        return max(0.0, (finished - started).total_seconds())

    def _running_status_is_stale_locked(self) -> bool:
        if str(self._refresh_status.get("state") or "") != "running":
            return False
        elapsed = self._elapsed_seconds(self._refresh_status.get("started_at"))
        return elapsed is not None and elapsed >= self.global_refresh_timeout_seconds

    def _mark_stale_running_if_needed(self) -> bool:
        with self._cache_lock:
            if not self._running_status_is_stale_locked():
                return False
            elapsed = self._elapsed_seconds(self._refresh_status.get("started_at")) or float(self.global_refresh_timeout_seconds)
            diagnostics = dict(self._refresh_status.get("diagnostics") or {})
            diagnostics["elapsed_seconds"] = elapsed
            diagnostics["timed_out"] = True
            diagnostics["timeout_reason"] = "stale_running_state"
            self._refresh_status = {
                **self._refresh_status,
                "state": "timed_out",
                "finished_at": utc_now_iso(),
                "error": f"OANDA refresh timed out after {self.global_refresh_timeout_seconds} seconds.",
                "diagnostics": diagnostics,
            }
            self._last_refresh_duration_seconds = elapsed
            return True

    def _update_refresh_progress(self, **updates: object) -> None:
        with self._cache_lock:
            if str(self._refresh_status.get("state") or "") != "running":
                return
            diagnostics = dict(self._refresh_status.get("diagnostics") or self._base_refresh_diagnostics())
            diagnostics.update(updates)
            elapsed = self._elapsed_seconds(self._refresh_status.get("started_at"))
            if elapsed is not None:
                diagnostics["elapsed_seconds"] = elapsed
            self._refresh_status["diagnostics"] = diagnostics

    def status(self, *, refresh_in_progress: bool = False) -> Dict[str, object]:
        with self._cache_lock:
            payload = self._build_payload()
        refresh_state = str(payload.get("refresh_state") or "")
        if refresh_in_progress or refresh_state == "running":
            payload["status"] = "refresh_in_progress"
            warnings = list(payload.get("warnings") or [])
            if "Refresh already running; showing the latest cached spread data." not in warnings:
                warnings.append("Refresh already running; showing the latest cached spread data.")
            payload["warnings"] = warnings
        return payload

    def start_refresh(self) -> Dict[str, object]:
        with self._cache_lock:
            stale = self._mark_stale_running_if_needed()
            if self._refresh_thread is not None and self._refresh_thread.is_alive() and not stale:
                return self.status(refresh_in_progress=True)
            if stale:
                self._refresh_thread = None
                if self.refresh_lock.locked():
                    self.refresh_lock = threading.Lock()
            started_at = utc_now_iso()
            self._refresh_generation += 1
            generation = self._refresh_generation
            self._refresh_status = {
                "state": "running",
                "started_at": started_at,
                "finished_at": "",
                "error": "",
                "warnings": [],
                "diagnostics": {
                    **self._base_refresh_diagnostics(),
                    "started_at": started_at,
                    "selected_broker": ",".join(self.brokers),
                },
            }
            self._refresh_thread = threading.Thread(
                target=self._run_background_refresh,
                args=(generation,),
                name="SpreadMonitorRefresh",
                daemon=True,
            )
            self._refresh_thread.start()
            return self.status(refresh_in_progress=True)

    def _run_background_refresh(self, generation: int) -> None:
        started_at = str(self._current_refresh_status().get("started_at") or utc_now_iso())
        try:
            self.refresh(generation=generation)
        except Exception as exc:
            with self._cache_lock:
                if generation != self._refresh_generation:
                    return
            self._set_refresh_status("failed", started_at=started_at, finished_at=utc_now_iso(), error=str(exc))

    def refresh(self, *, generation: Optional[int] = None) -> Dict[str, object]:
        lock = self.refresh_lock
        if not lock.acquire(blocking=False):
            return self.status(refresh_in_progress=True)
        try:
            return self._refresh_locked(generation=generation)
        finally:
            lock.release()

    def _refresh_locked(self, *, generation: Optional[int] = None) -> Dict[str, object]:
        started_at = utc_now_iso()
        warnings: List[str] = []
        errors: List[str] = []
        completed_requests = 0
        failed_requests = 0
        skipped_requests = 0
        timed_out = False

        try:
            raw_symbols = [str(item).strip().upper() for item in self.symbol_provider()]
        except Exception as exc:
            raw_symbols = []
            errors.append(f"Symbol discovery failed: {exc}")

        symbols = sorted(dict.fromkeys(symbol for symbol in raw_symbols if symbol))
        if not symbols:
            warnings.append("No spread symbols were discovered.")

        use_current_oanda = self._oanda_uses_current_pricing()
        oanda_batches = [
            symbols[index : index + self.oanda_pricing_batch_size]
            for index in range(0, len(symbols), self.oanda_pricing_batch_size)
        ] if use_current_oanda else []
        legacy_oanda = (not use_current_oanda) and self.oanda_fetcher is not None and "oanda" in self.brokers
        total_requests = len(oanda_batches) if use_current_oanda else (len(symbols) * len(TIMEFRAMES) if legacy_oanda else 0)
        timeframe_count = 1 if use_current_oanda else len(TIMEFRAMES)
        diagnostics: Dict[str, object] = {
            **self._base_refresh_diagnostics(),
            "selected_broker": ",".join(self.brokers),
            "symbol_count": len(symbols),
            "timeframe_count": timeframe_count,
            "total_requests_planned": total_requests,
            "pricing_batch_count": len(oanda_batches),
            "started_at": started_at,
        }
        self._set_refresh_status("running", started_at=started_at, finished_at="", warnings=[], diagnostics=diagnostics)

        with self._cache_lock:
            self._cache["last_refresh_started_at"] = started_at
            self._cache["symbols"] = symbols
            self._cache["generated_at"] = started_at
            self._cache["warnings"] = []
            self._cache["errors"] = []

        context: Dict[str, object] = {
            "started_at": started_at,
            "request_timeout_seconds": self.request_timeout_seconds,
            "global_timeout_seconds": self.global_refresh_timeout_seconds,
        }

        def _fetch_oanda(symbol: str, timeframe: TimeframeConfig) -> tuple[str, str, Dict[str, object]]:
            self._update_refresh_progress(current_symbol=symbol, current_timeframe=timeframe.label)
            broker_context = self._fetch_context("oanda", symbol, timeframe.label, context)
            broker_context["request_timeout_seconds"] = self.request_timeout_seconds
            try:
                result = self.oanda_fetcher(symbol, timeframe, broker_context) if self.oanda_fetcher is not None else {"error": "OANDA fetcher unavailable."}
            except Exception as exc:
                result = {"error": str(exc)}
            return symbol, timeframe.label, result if isinstance(result, dict) else {"error": "OANDA fetcher returned an invalid payload."}

        def _fetch_oanda_current(batch: List[str], batch_number: int) -> tuple[List[str], Dict[str, object]]:
            batch_label = f"batch {batch_number}/{len(oanda_batches)}"
            self._update_refresh_progress(current_symbol=", ".join(batch[:3]), current_timeframe="Current", current_batch=batch_label)
            broker_context = dict(context)
            broker_context["request_timeout_seconds"] = self.request_timeout_seconds
            broker_context["instruments"] = list(batch)
            broker_context["pricing_batch_number"] = batch_number
            broker_context["pricing_batch_count"] = len(oanda_batches)
            try:
                result = self.oanda_current_fetcher(batch, broker_context) if self.oanda_current_fetcher is not None else {"error": "OANDA pricing fetcher unavailable."}
            except Exception as exc:
                result = {"error": str(exc)}
            if not isinstance(result, dict):
                result = {"error": "OANDA pricing fetcher returned an invalid payload."}
            nested = result.get("results")
            if isinstance(nested, dict):
                result = nested
            if result.get("error"):
                error = str(result.get("error") or "OANDA pricing request failed.")
                result = {symbol: {"error": error} for symbol in batch}
            return batch, result

        futures = []
        executor: Optional[ThreadPoolExecutor] = None
        deadline = time.monotonic() + float(self.global_refresh_timeout_seconds)
        if total_requests:
            executor = ThreadPoolExecutor(max_workers=self.oanda_concurrency, thread_name_prefix="OandaSpreadFetch")
            futures = (
                [
                    executor.submit(_fetch_oanda_current, batch, index + 1)
                    for index, batch in enumerate(oanda_batches)
                ]
                if use_current_oanda
                else [
                    executor.submit(_fetch_oanda, symbol, timeframe)
                    for symbol in symbols
                    for timeframe in TIMEFRAMES
                ]
            )
            pending = set(futures)
            try:
                while pending:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        skipped_requests += len(pending)
                        for future in pending:
                            future.cancel()
                        break
                    done, pending = wait(
                        pending,
                        timeout=min(0.5, remaining),
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        self._update_refresh_progress(
                            completed_request_count=completed_requests,
                            failed_request_count=failed_requests,
                            skipped_request_count=skipped_requests,
                        )
                        continue
                    for future in done:
                        if use_current_oanda:
                            try:
                                batch, result_map = future.result()
                            except Exception as exc:
                                failed_requests += 1
                                warnings.append(f"OANDA pricing request failed: {exc}")
                                continue
                            completed_requests += 1
                            batch_failed_symbols = 0
                            for symbol in batch:
                                oanda_result = result_map.get(symbol) or result_map.get(symbol.upper()) or {"error": "No OANDA pricing returned for this instrument."}
                                if not isinstance(oanda_result, dict):
                                    oanda_result = {"error": "OANDA pricing result was invalid."}
                                if oanda_result.get("error"):
                                    batch_failed_symbols += 1
                                    warnings.append(f"OANDA {symbol} current: {oanda_result['error']}")
                                with self._cache_lock:
                                    self._update_record("oanda", symbol, OANDA_CURRENT_TIMEFRAME_LABEL, oanda_result)
                            if batch_failed_symbols == len(batch) and batch:
                                failed_requests += 1
                            current_failed_symbols = int(diagnostics.get("failed_symbol_count") or 0) + batch_failed_symbols
                            diagnostics["failed_symbol_count"] = current_failed_symbols
                            self._update_refresh_progress(
                                completed_request_count=completed_requests,
                                failed_request_count=failed_requests,
                                failed_symbol_count=current_failed_symbols,
                                skipped_request_count=skipped_requests,
                                current_symbol=", ".join(batch[:3]),
                                current_timeframe="Current",
                            )
                        else:
                            try:
                                symbol, timeframe_label, oanda_result = future.result()
                            except Exception as exc:
                                failed_requests += 1
                                warnings.append(f"OANDA request failed: {exc}")
                                continue
                            completed_requests += 1
                            if oanda_result.get("error"):
                                failed_requests += 1
                                warnings.append(f"OANDA {symbol} {timeframe_label}: {oanda_result['error']}")
                            with self._cache_lock:
                                self._update_record("oanda", symbol, timeframe_label, oanda_result)
                            self._update_refresh_progress(
                                completed_request_count=completed_requests,
                                failed_request_count=failed_requests,
                                skipped_request_count=skipped_requests,
                                current_symbol=symbol,
                                current_timeframe=timeframe_label,
                            )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        if timed_out:
            message = f"OANDA refresh timed out after {self.global_refresh_timeout_seconds} seconds."
            errors.append(message)
            warnings.append(
                f"{message} Completed {completed_requests}/{total_requests} requests before timeout."
            )

        finished_at = utc_now_iso()
        elapsed = self._elapsed_seconds(started_at, finished_at) or 0.0
        final_diagnostics = {
            **diagnostics,
            "completed_request_count": completed_requests,
            "failed_request_count": failed_requests,
            "skipped_request_count": skipped_requests,
            "elapsed_seconds": elapsed,
            "current_symbol": "",
            "current_timeframe": "",
            "timed_out": timed_out,
        }
        with self._cache_lock:
            if generation is not None and generation != self._refresh_generation:
                return self._build_payload()
            self._cache["generated_at"] = finished_at
            self._cache["last_refresh_finished_at"] = finished_at
            self._cache["warnings"] = sorted(dict.fromkeys(warnings))[:100]
            self._cache["errors"] = sorted(dict.fromkeys(errors))[:50]
            self._save_cache()
        final_state = "timed_out" if timed_out else "failed" if errors else "succeeded"
        self._set_refresh_status(
            final_state,
            started_at=started_at,
            finished_at=finished_at,
            error=" | ".join(errors[:5]) if errors else "",
            warnings=warnings,
            diagnostics=final_diagnostics,
        )
        with self._cache_lock:
            return self._build_payload()

    def _build_payload(self) -> Dict[str, object]:
        return build_spread_payload(
            self._cache,
            brokers=self.brokers,
            refresh_status=self._current_refresh_status(),
            refresh_interval_seconds=self.refresh_interval_seconds,
            current_only=self._oanda_uses_current_pricing(),
        )
