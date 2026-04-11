from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state_path() -> Path:
    return Path(__file__).resolve().parent / "render" / "data" / "trading_journal_state.json"


def _state_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return path
    env = os.getenv("TRADING_JOURNAL_STATE_PATH", "").strip()
    if env:
        return Path(env)
    return _default_state_path()


def _load_state(path: Optional[Path] = None) -> Dict[str, object]:
    state_path = _state_path(path)
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(payload: Dict[str, object], path: Optional[Path] = None) -> None:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, state_path)


def load_bybit_demo_tpsl_cache(path: Optional[Path] = None) -> Dict[str, Dict[str, object]]:
    state = _load_state(path)
    cache = state.get("bybit_demo_tpsl_cache")
    if not isinstance(cache, dict):
        return {}
    out: Dict[str, Dict[str, object]] = {}
    for key, value in cache.items():
        cache_key = str(key or "").strip()
        if cache_key and isinstance(value, dict):
            out[cache_key] = dict(value)
    return out


def save_bybit_demo_tpsl_cache(cache: Dict[str, Dict[str, object]], path: Optional[Path] = None) -> None:
    state = _load_state(path)
    state["bybit_demo_tpsl_cache"] = cache
    _save_state(state, path)


def cache_bybit_demo_tpsl_request(
    *,
    order_id: Optional[str],
    order_link_id: Optional[str],
    parent_order_link_id: Optional[str],
    symbol: str,
    side: str,
    take_profit: Optional[float],
    stop_loss: Optional[float],
    source: str,
    account: str = "demo",
    open_time_ms: Optional[int] = None,
    close_time_ms: Optional[int] = None,
    path: Optional[Path] = None,
) -> None:
    order_id_key = str(order_id or "").strip()
    order_link_key = str(order_link_id or "").strip()
    parent_link_key = str(parent_order_link_id or "").strip()
    if not order_id_key and not order_link_key and not parent_link_key:
        return

    payload = {
        "symbol": str(symbol or "").upper(),
        "side": str(side or "").title(),
        "account": str(account or "demo").lower(),
        "order_id": order_id_key,
        "order_link_id": order_link_key,
        "parent_order_link_id": parent_link_key,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "open_time_ms": open_time_ms,
        "close_time_ms": close_time_ms,
        "source": str(source or "").strip() or "order_create_request",
        "updated_at": _utc_now_iso(),
        "updated_ts": time.time(),
    }

    cache = load_bybit_demo_tpsl_cache(path)
    if order_id_key:
        cache[f"order_id:{order_id_key}"] = dict(payload)
    if order_link_key:
        cache[f"order_link_id:{order_link_key}"] = dict(payload)
    if parent_link_key:
        cache[f"order_link_id:{parent_link_key}"] = dict(payload)
        cache[f"parent_order_link_id:{parent_link_key}"] = dict(payload)

    if len(cache) > 1200:
        sorted_items = sorted(
            cache.items(),
            key=lambda item: float((item[1] or {}).get("updated_ts") or 0.0),
            reverse=True,
        )
        cache = dict(sorted_items[:1200])

    save_bybit_demo_tpsl_cache(cache, path)


def resolve_cached_bybit_demo_tpsl(
    *,
    cache: Dict[str, Dict[str, object]],
    order_id: Optional[str],
    order_link_id: Optional[str],
    parent_order_link_id: Optional[str],
    symbol: Optional[str],
    side: Optional[str],
    open_time_ms: Optional[int],
    close_time_ms: Optional[int],
    account: str = "demo",
) -> Tuple[Optional[Dict[str, object]], str]:
    oid = str(order_id or "").strip()
    olid = str(order_link_id or "").strip()
    parent = str(parent_order_link_id or "").strip()
    for match_type, key in (
        ("order_id", f"order_id:{oid}" if oid else ""),
        ("order_link_id", f"order_link_id:{olid}" if olid else ""),
        ("parent_order_link_id", f"order_link_id:{parent}" if parent else ""),
        ("parent_order_link_id", f"parent_order_link_id:{parent}" if parent else ""),
    ):
        if key and key in cache:
            return dict(cache[key]), match_type

    symbol_norm = str(symbol or "").upper().strip()
    side_norm = str(side or "").title().strip()
    account_norm = str(account or "demo").lower().strip()
    if not symbol_norm or not side_norm:
        return None, "none"

    target_ms = close_time_ms or open_time_ms
    best: Optional[Dict[str, object]] = None
    best_delta: Optional[int] = None
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("account") or "demo").lower().strip() != account_norm:
            continue
        if str(entry.get("symbol") or "").upper().strip() != symbol_norm:
            continue
        if str(entry.get("side") or "").title().strip() != side_norm:
            continue

        if target_ms is None:
            if best is None:
                best = dict(entry)
            continue

        candidates = [entry.get("close_time_ms"), entry.get("open_time_ms")]
        deltas = []
        for raw in candidates:
            try:
                val = int(raw)
            except Exception:
                continue
            deltas.append(abs(val - target_ms))
        if not deltas:
            continue
        delta = min(deltas)
        if best_delta is None or delta < best_delta:
            best = dict(entry)
            best_delta = delta

    if best is None:
        return None, "none"
    return best, "symbol_side_time"
