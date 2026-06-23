"""Core spread monitor calculations, cache handling, and payload assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional


LOW_MAX_PERCENTILE = 50
HIGH_MIN_PERCENTILE = 80
REFRESH_INTERVAL_SECONDS = 300
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

BROKERS = ("oanda", "pepperstone")


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


def make_sample(time_value: object, spread_pct: object) -> Optional[Dict[str, object]]:
    spread_value = coerce_float(spread_pct)
    if spread_value is None:
        return None
    dt = parse_time(time_value)
    timestamp = dt.isoformat().replace("+00:00", "Z") if dt else utc_now_iso()
    return {"time": timestamp, "spread_pct": spread_value}


def _sample_sort_key(sample: Dict[str, object]) -> str:
    return str(sample.get("time") or "")


def _merge_samples(existing: Iterable[object], incoming: Iterable[object], limit: int) -> List[Dict[str, object]]:
    by_time: Dict[str, Dict[str, object]] = {}
    for source in (existing, incoming):
        for item in source:
            if not isinstance(item, dict):
                continue
            sample = make_sample(item.get("time"), item.get("spread_pct"))
            if sample is None:
                continue
            by_time[str(sample["time"])] = sample
    ordered = sorted(by_time.values(), key=_sample_sort_key)
    if limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


def broker_cell(record: Optional[Dict[str, object]]) -> Dict[str, object]:
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
    error = str(record.get("error") or "").strip()
    if spread_value is None:
        return {
            "spread_pct": None,
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
    category = classify_spread(spread_value, sample_values)
    return {
        "spread_pct": spread_value,
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


class SpreadMonitorState:
    """Owns the cache and one-at-a-time refresh behavior for the monitor."""

    def __init__(
        self,
        cache_path: Path,
        *,
        symbol_provider: Callable[[], Iterable[str]],
        oanda_fetcher: Optional[Callable[[str, TimeframeConfig, Dict[str, object]], Dict[str, object]]] = None,
        mt5_fetcher: Optional[Callable[[str, TimeframeConfig, Dict[str, object]], Dict[str, object]]] = None,
        mt5_preflight: Optional[Callable[[], Dict[str, object]]] = None,
        max_samples: int = MAX_CACHE_SAMPLES,
        refresh_interval_seconds: int = REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.symbol_provider = symbol_provider
        self.oanda_fetcher = oanda_fetcher
        self.mt5_fetcher = mt5_fetcher
        self.mt5_preflight = mt5_preflight
        self.max_samples = max_samples
        self.refresh_interval_seconds = refresh_interval_seconds
        self.refresh_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._cache = self._load_cache()
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_status: Dict[str, object] = {
            "state": "idle",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "warnings": [],
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
        return payload

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
        record["samples"] = _merge_samples(record.get("samples") or [], samples_list, self.max_samples)

        latest = result.get("latest")
        latest_sample = latest if isinstance(latest, dict) else None
        if latest_sample is None and record["samples"]:
            latest_sample = record["samples"][-1]  # type: ignore[index]
        if latest_sample is not None:
            normalized_latest = make_sample(latest_sample.get("time"), latest_sample.get("spread_pct"))
            if normalized_latest is not None:
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
    ) -> None:
        with self._cache_lock:
            current_started = str(self._refresh_status.get("started_at") or "")
            self._refresh_status = {
                "state": state,
                "started_at": started_at if started_at is not None else current_started,
                "finished_at": finished_at if finished_at is not None else str(self._refresh_status.get("finished_at") or ""),
                "error": error if error is not None else "",
                "warnings": [str(item) for item in (warnings or []) if str(item).strip()],
            }

    def _current_refresh_status(self) -> Dict[str, object]:
        with self._cache_lock:
            return dict(self._refresh_status)

    def _symbols_from_cache(self) -> List[str]:
        symbols = self._cache.get("symbols")
        result = [str(item).strip() for item in symbols if str(item).strip()] if isinstance(symbols, list) else []
        if result:
            return sorted(dict.fromkeys(result))
        records = self._cache.get("records")
        found: List[str] = []
        if isinstance(records, dict):
            for key in records:
                _broker, symbol, _timeframe = split_cache_key(str(key))
                if symbol:
                    found.append(symbol)
        return sorted(dict.fromkeys(found))

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
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return self.status(refresh_in_progress=True)
            started_at = utc_now_iso()
            self._refresh_status = {
                "state": "running",
                "started_at": started_at,
                "finished_at": "",
                "error": "",
                "warnings": [],
            }
            self._refresh_thread = threading.Thread(
                target=self._run_background_refresh,
                name="SpreadMonitorRefresh",
                daemon=True,
            )
            self._refresh_thread.start()
            return self.status(refresh_in_progress=True)

    def _run_background_refresh(self) -> None:
        started_at = str(self._current_refresh_status().get("started_at") or utc_now_iso())
        try:
            payload = self.refresh()
            errors = [str(item) for item in (payload.get("errors") or []) if str(item).strip()]
            warnings = [str(item) for item in (payload.get("warnings") or []) if str(item).strip()]
            finished_at = utc_now_iso()
            if errors:
                self._set_refresh_status("failed", started_at=started_at, finished_at=finished_at, error=" | ".join(errors[:5]), warnings=warnings)
            else:
                self._set_refresh_status("succeeded", started_at=started_at, finished_at=finished_at, warnings=warnings)
        except Exception as exc:
            self._set_refresh_status("failed", started_at=started_at, finished_at=utc_now_iso(), error=str(exc))

    def refresh(self) -> Dict[str, object]:
        if not self.refresh_lock.acquire(blocking=False):
            return self.status(refresh_in_progress=True)
        try:
            return self._refresh_locked()
        finally:
            self.refresh_lock.release()

    def _refresh_locked(self) -> Dict[str, object]:
        started_at = utc_now_iso()
        warnings: List[str] = []
        errors: List[str] = []

        try:
            raw_symbols = [str(item).strip().upper() for item in self.symbol_provider()]
        except Exception as exc:
            raw_symbols = []
            errors.append(f"Symbol discovery failed: {exc}")

        symbols = sorted(dict.fromkeys(symbol for symbol in raw_symbols if symbol))
        if not symbols:
            warnings.append("No spread symbols were discovered.")

        with self._cache_lock:
            self._cache["last_refresh_started_at"] = started_at
            self._cache["symbols"] = symbols
            self._cache["generated_at"] = started_at
            self._cache["warnings"] = []
            self._cache["errors"] = []

        context: Dict[str, object] = {"started_at": started_at}
        mt5_preflight_error = ""
        if self.mt5_fetcher is not None and self.mt5_preflight is not None:
            try:
                preflight = self.mt5_preflight()
            except Exception as exc:
                preflight = {"ok": False, "error": str(exc)}
            if not bool(preflight.get("ok")):
                mt5_preflight_error = str(preflight.get("error") or "MT5 unavailable.").strip()
                warnings.append(f"Pepperstone unavailable: {mt5_preflight_error}")

        for symbol in symbols:
            for timeframe in TIMEFRAMES:
                if self.oanda_fetcher is not None:
                    broker_context = self._fetch_context("oanda", symbol, timeframe.label, context)
                    try:
                        oanda_result = self.oanda_fetcher(symbol, timeframe, broker_context)
                    except Exception as exc:
                        oanda_result = {"error": str(exc)}
                    with self._cache_lock:
                        self._update_record("oanda", symbol, timeframe.label, oanda_result)
                    if oanda_result.get("error"):
                        warnings.append(f"OANDA {symbol} {timeframe.label}: {oanda_result['error']}")

                if self.mt5_fetcher is not None:
                    broker_context = self._fetch_context("pepperstone", symbol, timeframe.label, context)
                    if mt5_preflight_error:
                        mt5_result = {"error": mt5_preflight_error}
                    else:
                        try:
                            mt5_result = self.mt5_fetcher(symbol, timeframe, broker_context)
                        except Exception as exc:
                            mt5_result = {"error": str(exc)}
                    with self._cache_lock:
                        self._update_record("pepperstone", symbol, timeframe.label, mt5_result)
                    if mt5_result.get("error"):
                        warnings.append(f"Pepperstone unavailable: {mt5_result['error']}")

        finished_at = utc_now_iso()
        with self._cache_lock:
            self._cache["generated_at"] = finished_at
            self._cache["last_refresh_finished_at"] = finished_at
            self._cache["warnings"] = sorted(dict.fromkeys(warnings))[:100]
            self._cache["errors"] = sorted(dict.fromkeys(errors))[:50]
            self._save_cache()
            return self._build_payload()

    def _build_payload(self) -> Dict[str, object]:
        symbols = self._symbols_from_cache()
        records = self._cache.get("records")
        record_map = records if isinstance(records, dict) else {}
        rows: List[Dict[str, object]] = []

        for symbol in symbols:
            cells: Dict[str, object] = {}
            symbol_has_data = False
            for timeframe in TIMEFRAME_LABELS:
                cell: Dict[str, object] = {}
                for broker in BROKERS:
                    record = record_map.get(_cache_key(broker, symbol, timeframe))
                    broker_payload = broker_cell(record if isinstance(record, dict) else None)
                    if broker_payload["spread_pct"] is not None:
                        symbol_has_data = True
                    cell[broker] = broker_payload
                    if broker == "pepperstone":
                        cell["pepperstone_razor"] = broker_payload
                cells[timeframe] = cell
            if symbol_has_data:
                rows.append(
                    {
                        "symbol": symbol,
                        "display_symbol": symbol.replace("_", "/"),
                        "cells": cells,
                    }
                )

        errors = list(self._cache.get("errors") or [])
        if not rows and not errors:
            errors.append("No OANDA or Pepperstone spread data is available yet.")

        return {
            "ok": bool(rows) and not bool(errors),
            "generated_at": str(self._cache.get("generated_at") or utc_now_iso()),
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "refresh": self._current_refresh_status(),
            "refresh_state": str(self._current_refresh_status().get("state") or "idle"),
            "symbols": [row["symbol"] for row in rows],
            "timeframes": list(TIMEFRAME_LABELS),
            "rows": rows,
            "warnings": list(self._cache.get("warnings") or []),
            "errors": errors,
            "last_refresh_started_at": str(self._cache.get("last_refresh_started_at") or ""),
            "last_refresh_finished_at": str(self._cache.get("last_refresh_finished_at") or ""),
        }
