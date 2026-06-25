"""Manual Pepperstone spread import for MT5 Trader EA JSON exports."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Dict, Iterable, List, Optional

from spread_core import (
    MAX_CACHE_SAMPLES,
    TIMEFRAME_LABELS,
    _cache_key,
    _merge_samples,
    build_spread_payload,
    default_cache_payload,
    make_sample,
    parse_time,
    spread_pct_from_bid_ask,
    utc_now_iso,
)
from symbols import is_crypto_symbol, normalize_available_mt5_symbols, normalize_oanda_symbol


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_PATH = ROOT_DIR / "mt5-clone" / "pepperstone_spreads_latest.json"
DEFAULT_CACHE_PATH = ROOT_DIR / "render" / "data" / "pepperstone_spread_import_cache.json"
MT5_FALLBACK_EXPORT_HINT = r"%APPDATA%\MetaQuotes\Terminal\<terminal-id>\MQL5\Files\pepperstone_spreads_latest.json"


class PepperstoneImportError(ValueError):
    """Raised when an import file cannot be accepted."""


def _load_json_text(text: str) -> Dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PepperstoneImportError(f"Pepperstone import JSON is malformed: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise PepperstoneImportError("Pepperstone import JSON must be an object.")
    return payload


def _validate_top_level(payload: Dict[str, object]) -> str:
    version = payload.get("version")
    if version not in (1, "1"):
        raise PepperstoneImportError("Pepperstone import version must be 1.")

    broker = str(payload.get("broker") or "").strip().lower()
    if broker != "pepperstone":
        raise PepperstoneImportError("Pepperstone import broker must be pepperstone.")

    generated_at = str(payload.get("generated_at") or "").strip()
    if parse_time(generated_at) is None:
        raise PepperstoneImportError("Pepperstone import generated_at must be an ISO timestamp.")
    return generated_at


def _extract_items(payload: Dict[str, object]) -> List[Dict[str, object]]:
    raw_items = payload.get("symbols")
    if raw_items is None:
        raw_items = payload.get("spreads")
    if raw_items is None:
        raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise PepperstoneImportError("Pepperstone import must include a symbols list.")
    items = [item for item in raw_items if isinstance(item, dict)]
    if not items:
        raise PepperstoneImportError("Pepperstone import symbols list is empty.")
    return items


def _normalize_mt5_symbol(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise PepperstoneImportError("Pepperstone import contains a blank symbol.")
    if is_crypto_symbol(raw):
        raise PepperstoneImportError(f"Unsupported Pepperstone crypto symbol: {raw}.")
    direct = normalize_oanda_symbol(raw)
    if direct:
        return direct
    candidates = normalize_available_mt5_symbols([raw])
    if candidates:
        return candidates[0]
    raise PepperstoneImportError(f"Unsupported Pepperstone symbol: {raw}.")


def _item_symbol(item: Dict[str, object]) -> str:
    for key in ("symbol", "mt5_symbol", "name", "instrument"):
        if item.get(key):
            return _normalize_mt5_symbol(item.get(key))
    raise PepperstoneImportError("Pepperstone import symbol entry is missing a symbol.")


def _item_timestamp(item: Dict[str, object], generated_at: str) -> str:
    value = item.get("timestamp") or item.get("time") or item.get("generated_at") or generated_at
    sample = make_sample(value, 1.0)
    if sample is None:
        return generated_at
    return str(sample["time"])


def normalized_cache_from_export(
    payload: Dict[str, object],
    *,
    previous_cache: Optional[Dict[str, object]] = None,
    source_path: object = "",
    max_samples: int = MAX_CACHE_SAMPLES,
) -> Dict[str, object]:
    generated_at = _validate_top_level(payload)
    items = _extract_items(payload)
    previous_records = {}
    if isinstance(previous_cache, dict) and isinstance(previous_cache.get("records"), dict):
        previous_records = previous_cache["records"]

    imported_at = utc_now_iso()
    cache = default_cache_payload()
    cache["generated_at"] = generated_at
    cache["last_imported_at"] = imported_at
    cache["source_path"] = str(source_path or "")
    cache["source_filename"] = Path(str(source_path)).name if source_path else ""
    cache["warnings"] = []
    cache["errors"] = []

    records: Dict[str, Dict[str, object]] = {}
    symbols: List[str] = []
    for item in items:
        symbol = _item_symbol(item)
        bid = item.get("bid")
        ask = item.get("ask")
        try:
            spread_pct = spread_pct_from_bid_ask(bid, ask)
        except ValueError as exc:
            raise PepperstoneImportError(f"Pepperstone {symbol} bid/ask is invalid: {exc}") from exc
        sample = make_sample(_item_timestamp(item, generated_at), spread_pct)
        if sample is None:
            raise PepperstoneImportError(f"Pepperstone {symbol} spread is unavailable.")
        symbols.append(symbol)
        for timeframe in TIMEFRAME_LABELS:
            key = _cache_key("pepperstone", symbol, timeframe)
            previous = previous_records.get(key) if isinstance(previous_records, dict) else None
            previous_samples = previous.get("samples") if isinstance(previous, dict) else []
            samples = _merge_samples(previous_samples or [], [sample], max_samples)
            records[key] = {
                "broker": "pepperstone",
                "symbol": symbol,
                "timeframe": timeframe,
                "samples": samples,
                "latest": sample,
                "last_success": imported_at,
                "ttl_seconds": 0,
                "error": "",
            }

    cache["symbols"] = sorted(dict.fromkeys(symbols))
    cache["records"] = records
    return cache


class PepperstoneSpreadImportStore:
    """Owns the imported Pepperstone cache and atomic import behavior."""

    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE_PATH,
        *,
        default_source_path: Path = DEFAULT_EXPORT_PATH,
        max_samples: int = MAX_CACHE_SAMPLES,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.default_source_path = Path(default_source_path)
        self.max_samples = max_samples
        self._lock = threading.RLock()
        self._cache = self._load_cache()

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
        payload.setdefault("last_imported_at", "")
        payload.setdefault("source_path", "")
        payload.setdefault("source_filename", "")
        return payload

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cache_path)

    def status(self) -> Dict[str, object]:
        with self._lock:
            payload = build_spread_payload(
                self._cache,
                brokers=("pepperstone",),
                refresh_status={"state": "manual", "started_at": "", "finished_at": "", "error": "", "warnings": []},
                refresh_interval_seconds=0,
                empty_message="No imported Pepperstone spread data is available yet. Import the MT5-generated file to populate this table.",
            )
        payload["broker"] = "pepperstone"
        payload["manual_import_only"] = True
        return payload

    def import_default_file(self) -> Dict[str, object]:
        return self.import_file(self.default_source_path)

    def import_file(self, path: Path) -> Dict[str, object]:
        source = Path(path)
        if not source.exists():
            raise PepperstoneImportError(
                "Pepperstone spread file not found. "
                f"Expected repo import path: {source}. "
                f"Expected MT5 fallback path: {MT5_FALLBACK_EXPORT_HINT}. "
                "Run mt5-clone\\copy_pepperstone_spreads_latest.bat after the Trader EA exports the file."
            )
        text = source.read_text(encoding="utf-8-sig")
        return self.import_text(text, source_path=source)

    def import_text(self, text: str, *, source_path: object = "") -> Dict[str, object]:
        payload = _load_json_text(text)
        with self._lock:
            next_cache = normalized_cache_from_export(
                payload,
                previous_cache=self._cache,
                source_path=source_path,
                max_samples=self.max_samples,
            )
            self._cache = next_cache
            self._save_cache()
            status = self.status()
        status["imported"] = True
        return status
