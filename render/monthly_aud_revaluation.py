from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

def _normalize_bybit_recv_window_ms(raw: Any) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = 15000
    return max(1000, min(60000, value))


def _normalize_max_retries(raw: Any) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = 2
    return max(1, min(3, value))


def _normalize_offset_ttl_seconds(raw: Any) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = 300
    return max(10, value)


BYBIT_RECV_WINDOW_MS = _normalize_bybit_recv_window_ms(__import__("os").getenv("BYBIT_RECV_WINDOW", "15000"))
BYBIT_SIGNED_REQUEST_MAX_RETRIES = _normalize_max_retries(__import__("os").getenv("BYBIT_SIGNED_REQUEST_MAX_RETRIES", "2"))
BYBIT_TIME_OFFSET_TTL_SECONDS = _normalize_offset_ttl_seconds(__import__("os").getenv("BYBIT_TIME_OFFSET_TTL_SECONDS", "300"))
_BYBIT_TIME_OFFSET_CACHE: Dict[str, Dict[str, float]] = {}
BRISBANE_TZ = ZoneInfo("Australia/Brisbane")
SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
TWO_YEARS_MS = 730 * 24 * 60 * 60 * 1000
MANDATORY_MONTH = "2026-03"


class MonthlyAudRevalError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.stage = stage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _build_bybit_query(params: Dict[str, str]) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""


def _normalize_bybit_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _bybit_recv_window_str() -> str:
    return str(BYBIT_RECV_WINDOW_MS)


def _bybit_timestamp_window_error(ret_code: Any, ret_msg: Any) -> bool:
    msg = str(ret_msg or "").lower()
    return str(ret_code) == "10002" or "timestamp" in msg or "recv_window" in msg


def _extract_bybit_timestamp_diag(ret_msg: Any) -> str:
    msg = str(ret_msg or "")
    import re
    req = re.search(r"req_timestamp\[(\d+)\]", msg)
    srv = re.search(r"server_timestamp\[(\d+)\]", msg)
    win = re.search(r"recv_window\[(\d+)\]", msg)
    parts = []
    if req:
        parts.append(f"req_timestamp={req.group(1)}")
    if srv:
        parts.append(f"server_timestamp={srv.group(1)}")
    if win:
        parts.append(f"ret_recv_window={win.group(1)}")
    if req and srv:
        parts.append(f"server_delta_ms={int(req.group(1)) - int(srv.group(1))}")
    return " ".join(parts)


async def _fetch_bybit_server_time_ms(base_url: str) -> int:
    url = f"{_normalize_bybit_base_url(base_url)}/v5/market/time"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("result") if isinstance(payload, dict) else {}
    time_nano = _coerce_int(result.get("timeNano")) if isinstance(result, dict) else None
    if time_nano is not None:
        return time_nano // 1_000_000
    time_second = _coerce_int(result.get("timeSecond")) if isinstance(result, dict) else None
    if time_second is not None:
        return time_second * 1000
    top_time = _coerce_int(payload.get("time")) if isinstance(payload, dict) else None
    if top_time is not None:
        return top_time
    raise ValueError("Bybit server time payload missing parseable timestamp")


async def _refresh_bybit_time_offset_ms(base_url: str, logger=None) -> int:
    normalized = _normalize_bybit_base_url(base_url)
    local_before = int(time.time() * 1000)
    server_ms = await _fetch_bybit_server_time_ms(normalized)
    local_after = int(time.time() * 1000)
    midpoint = (local_before + local_after) // 2
    offset_ms = int(server_ms - midpoint)
    rtt_ms = max(0, local_after - local_before)
    _BYBIT_TIME_OFFSET_CACHE[normalized] = {"synced_at": float(local_after), "offset_ms": float(offset_ms), "rtt_ms": float(rtt_ms)}
    if logger:
        logger.info("MONTHLY_AUD_REVAL_BYBIT_TIME_SYNC base_url=%s offset_ms=%s rtt_ms=%s", normalized, offset_ms, rtt_ms)
        if abs(offset_ms) > 3000:
            logger.warning("MONTHLY_AUD_REVAL_BYBIT_TIME_SYNC_DRIFT base_url=%s offset_ms=%s", normalized, offset_ms)
    return offset_ms


async def _get_bybit_time_offset_ms(base_url: str, force_refresh: bool = False, logger=None) -> int:
    normalized = _normalize_bybit_base_url(base_url)
    cached = _BYBIT_TIME_OFFSET_CACHE.get(normalized)
    now_ms = int(time.time() * 1000)
    if not force_refresh and cached and (now_ms - int(cached.get("synced_at", 0))) < (BYBIT_TIME_OFFSET_TTL_SECONDS * 1000):
        return int(cached.get("offset_ms", 0))
    try:
        return await _refresh_bybit_time_offset_ms(normalized, logger=logger)
    except Exception as exc:
        if cached:
            if logger:
                logger.warning("MONTHLY_AUD_REVAL_BYBIT_TIME_SYNC_CACHE_FALLBACK base_url=%s error=%s", normalized, exc)
            return int(cached.get("offset_ms", 0))
        if force_refresh:
            raise MonthlyAudRevalError(
                "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
                f"Bybit time sync failed base_url={normalized} error={exc}",
                stage="bybit_time_sync",
            ) from exc
        return 0


async def _bybit_timestamp_ms(base_url: str, force_time_sync: bool = False, logger=None) -> str:
    offset_ms = await _get_bybit_time_offset_ms(base_url, force_refresh=force_time_sync, logger=logger)
    return str(int(time.time() * 1000) + int(offset_ms))


def _bybit_sign_request(timestamp: str, api_key: str, api_secret: str, payload: str, recv_window: str) -> str:
    raw = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


async def _bybit_signed_get(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    path: str,
    params: Dict[str, str],
    logger=None,
) -> Dict[str, Any]:
    normalized_base = _normalize_bybit_base_url(base_url)
    query = _build_bybit_query(params)
    recv_window = _bybit_recv_window_str()
    url = f"{normalized_base}{path}" + (f"?{query}" if query else "")
    for attempt in range(1, BYBIT_SIGNED_REQUEST_MAX_RETRIES + 1):
        timestamp = await _bybit_timestamp_ms(normalized_base, force_time_sync=(attempt > 1), logger=logger)
        signature = _bybit_sign_request(timestamp, api_key, api_secret, query, recv_window)
        headers = {"X-BAPI-API-KEY": api_key, "X-BAPI-SIGN": signature, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN-TYPE": "2"}
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(url, headers=headers)
            status_code = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR", f"Bybit GET failed path={path} query={query} error={exc}", stage="bybit_request") from exc
        ret_code = payload.get("retCode")
        ret_msg = payload.get("retMsg")
        if ret_code in (0, "0"):
            return payload
        if _bybit_timestamp_window_error(ret_code, ret_msg) and attempt < BYBIT_SIGNED_REQUEST_MAX_RETRIES:
            if logger:
                logger.warning("MONTHLY_AUD_REVAL_BYBIT_SIGNED_RETRY path=%s attempt=%s reason=timestamp_window recv_window=%s", path, attempt, recv_window)
            continue
        diag = _extract_bybit_timestamp_diag(ret_msg)
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR", f"Bybit GET retCode failure path={path} status={status_code} retCode={ret_code} retMsg={ret_msg} recv_window={recv_window} {diag}".strip(), stage="bybit_request")
    raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR", f"Bybit GET failed path={path} query={query}", stage="bybit_request")


async def _fetch_transaction_pages(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_time: int,
    end_time: int,
    logger=None,
) -> List[Dict[str, Any]]:
    cursor: Optional[str] = None
    rows: List[Dict[str, Any]] = []
    while True:
        params = {
            "accountType": "UNIFIED",
            "startTime": str(start_time),
            "endTime": str(end_time),
            "limit": "50",
        }
        if cursor:
            params["cursor"] = cursor
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/account/transaction-log",
            params=params,
            logger=logger,
        )
        result = payload.get("result") if isinstance(payload, dict) else {}
        page = result.get("list") if isinstance(result, dict) else []
        if isinstance(page, list):
            rows.extend([r for r in page if isinstance(r, dict)])
        cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
        if not cursor:
            break
    return rows


async def _fetch_transaction_rows_range(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_ms: int,
    end_ms: int,
    logger=None,
) -> List[Dict[str, Any]]:
    if start_ms > end_ms:
        return []
    rows: List[Dict[str, Any]] = []
    current = start_ms
    while current <= end_ms:
        chunk_end = min(end_ms, current + SEVEN_DAYS_MS - 1)
        part = await _fetch_transaction_pages(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=current,
            end_time=chunk_end,
            logger=logger,
        )
        rows.extend(part)
        current = chunk_end + 1
    return rows


async def _get_price(*, base_url: str, coin: str, timestamp: int) -> float:
    url = f"{base_url}/v5/market/mark-price-kline"
    params = {
        "category": "linear",
        "symbol": f"{coin}USDT",
        "interval": "1",
        "start": str(timestamp),
        "end": str(timestamp + 60000),
        "limit": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code >= 400:
            return 0.0
        payload = resp.json()
        if payload.get("retCode") not in (0, "0"):
            return 0.0
        rows = ((payload.get("result") or {}).get("list") or [])
        if rows:
            return float(rows[0][1])
    except Exception:
        return 0.0
    return 0.0


async def _get_current_unified_balance_usdt_equivalent(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    logger=None,
) -> Tuple[float, str]:
    payload = await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/account/wallet-balance",
        params={"accountType": "UNIFIED"},
        logger=logger,
    )
    entries = ((payload.get("result") or {}).get("list") or [])
    if not entries or not isinstance(entries[0], dict):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            "Wallet balance payload missing UNIFIED list entry.",
            stage="current_balance",
        )
    first = entries[0]
    total_equity = _coerce_float(first.get("totalEquity"))
    if total_equity is not None and math.isfinite(total_equity):
        return total_equity, "total_equity"

    total = 0.0
    used_any = False
    for coin in first.get("coin") or []:
        if not isinstance(coin, dict):
            continue
        coin_name = str(coin.get("coin") or "").strip().upper()
        amount = _coerce_float(coin.get("cashBalance"))
        if amount is None:
            amount = _coerce_float(coin.get("walletBalance"))
        if amount is None:
            amount = _coerce_float(coin.get("equity"))
        usd_value = _coerce_float(coin.get("usdValue"))
        if coin_name == "USDT":
            if amount is not None:
                total += amount
                used_any = True
                continue
            if usd_value is not None:
                total += usd_value
                used_any = True
                continue
        if usd_value is not None:
            total += usd_value
            used_any = True
        elif amount is not None and coin_name:
            total += amount * await _get_price(base_url=base_url, coin=coin_name, timestamp=int(time.time() * 1000))
            used_any = True

    if not used_any or not math.isfinite(total):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            "Unable to resolve current unified balance.",
            stage="current_balance",
        )
    return total, "wallet_fallback"


async def _has_account_activity_since(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    boundary_ms: int,
    now_ms: int,
    logger=None,
) -> bool:
    if boundary_ms > now_ms:
        return False
    rows = await _fetch_transaction_rows_range(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        start_ms=boundary_ms,
        end_ms=now_ms,
        logger=logger,
    )
    return len(rows) > 0


async def _get_latest_balance_on_or_before(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    boundary_ms: int,
    logger=None,
) -> Optional[float]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = max(0, now_ms - TWO_YEARS_MS)
    end = boundary_ms - 1
    while end >= earliest:
        start = max(earliest, end - SEVEN_DAYS_MS + 1)
        rows = await _fetch_transaction_pages(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start,
            end_time=end,
            logger=logger,
        )
        if rows:
            latest_row: Optional[Dict[str, Any]] = None
            latest_time = -1
            for row in rows:
                tx_time = _coerce_int(row.get("transactionTime"))
                if tx_time is None:
                    continue
                if tx_time > latest_time:
                    latest_time = tx_time
                    latest_row = row
            if not latest_row:
                return None
            # requested field priority; keep usdValue as fallback for non-USDT rows
            for key in ("cashBalance", "walletBalance", "equity", "usdValue"):
                parsed = _coerce_float(latest_row.get(key))
                if parsed is not None and math.isfinite(parsed):
                    return parsed
            return None
        if start == earliest:
            break
        end = start - 1
    return None


def _month_bounds(month_key: str) -> Tuple[datetime, datetime]:
    year, month = [int(x) for x in month_key.split("-")]
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    if month == 12:
        next_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    else:
        next_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    return start_local, next_local


def _iter_target_months(existing_valid_months: Iterable[str], *, now_local: datetime) -> List[str]:
    known = {m for m in existing_valid_months if isinstance(m, str) and len(m) == 7}
    year, month = 2026, 3
    current_month = now_local.strftime("%Y-%m")
    out: List[str] = []
    while True:
        key = f"{year:04d}-{month:02d}"
        if key >= current_month:
            break
        if key not in known:
            out.append(key)
        month += 1
        if month > 12:
            month = 1
            year += 1
    if MANDATORY_MONTH not in known and MANDATORY_MONTH not in out and MANDATORY_MONTH < current_month:
        out.insert(0, MANDATORY_MONTH)
    return out


def _parse_oanda_time(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _roundup_away_from_zero(value: float, places: int) -> float:
    factor = 10 ** places
    if value == 0:
        return 0.0
    return math.ceil(value * factor) / factor if value > 0 else math.floor(value * factor) / factor


async def _fetch_oanda_candles(
    *,
    base_url: str,
    api_key: str,
    start_utc: datetime,
    end_utc: datetime,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    now_utc = datetime.now(timezone.utc)
    safe_now = now_utc - timedelta(seconds=5)
    safe_end_utc = min(end_utc, safe_now)
    if safe_end_utc <= start_utc:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_PENDING_NEXT_OPEN",
            (
                "OANDA window not yet available: "
                f"start={start_utc.isoformat()} end={end_utc.isoformat()} "
                f"safe_end={safe_end_utc.isoformat()} now={now_utc.isoformat()}"
            ),
            stage="oanda_candles_window",
        )

    token = (api_key or "").strip().strip('"').strip("'")
    url = f"{base_url.rstrip('/')}/v3/instruments/AUD_USD/candles"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "price": "M",
        "granularity": "D",
        "from": start_utc.isoformat().replace("+00:00", "Z"),
        "to": safe_end_utc.isoformat().replace("+00:00", "Z"),
        "includeFirst": "true",
        "dailyAlignment": "0",
        "alignmentTimezone": "Australia/Brisbane",
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
            f"OANDA candles failed status={resp.status_code}: {resp.text[:250]}",
            stage="oanda_candles",
        )
    payload = resp.json()
    candles = payload.get("candles") if isinstance(payload, dict) else []
    window = {
        "request_start_utc": start_utc.isoformat(),
        "request_end_utc": end_utc.isoformat(),
        "clamped_end_utc": safe_end_utc.isoformat(),
        "now_utc": now_utc.isoformat(),
        "request_clamped": str(safe_end_utc != end_utc).lower(),
    }
    return ([c for c in candles if isinstance(c, dict)] if isinstance(candles, list) else []), window


async def _resolve_boundary_rate(
    *,
    month_key: str,
    base_url: str,
    api_key: str,
    logger,
) -> Dict[str, float]:
    boundary_local, _ = _month_bounds(month_key)
    request_start_utc = (boundary_local - timedelta(days=10)).astimezone(timezone.utc)
    request_end_utc = (boundary_local + timedelta(days=10)).astimezone(timezone.utc)
    candles, window = await _fetch_oanda_candles(
        base_url=base_url,
        api_key=api_key,
        start_utc=request_start_utc,
        end_utc=request_end_utc,
    )
    logger.info(
        "MONTHLY_AUD_REVAL_OANDA_WINDOW month=%s boundary_local=%s request_start_utc=%s request_end_utc=%s clamped_end_utc=%s now_utc=%s clamped=%s",
        month_key,
        boundary_local.isoformat(),
        window.get("request_start_utc"),
        window.get("request_end_utc"),
        window.get("clamped_end_utc"),
        window.get("now_utc"),
        window.get("request_clamped"),
    )

    prev_close: Optional[float] = None
    next_open: Optional[float] = None
    start_date = boundary_local.date()
    for candle in candles:
        mid = candle.get("mid") if isinstance(candle.get("mid"), dict) else {}
        candle_date = _parse_oanda_time(str(candle.get("time") or "")).astimezone(BRISBANE_TZ).date()
        if candle_date < start_date and candle.get("complete"):
            val = _coerce_float(mid.get("c"))
            if val is not None:
                prev_close = val
        elif candle_date >= start_date and next_open is None:
            val = _coerce_float(mid.get("o"))
            if val is not None:
                next_open = val
                break

    if prev_close is None:
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_OANDA_RATE_ERROR", f"Missing previous close for {month_key}", stage="boundary_prev_close")
    if next_open is None:
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_PENDING_NEXT_OPEN", f"Missing next open for {month_key}", stage="boundary_next_open")
    return {
        "rate": (prev_close + next_open) / 2.0,
        "fx_close": prev_close,
        "fx_next_open": next_open,
        "window": window,
    }


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) else payload
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


def _save_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": sorted(rows, key=lambda r: str(r.get("close_time") or ""), reverse=True), "updated_at": _utc_now_iso()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _is_finite(value: Any) -> bool:
    val = _coerce_float(value)
    return val is not None and math.isfinite(val)


def _is_close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    av = _coerce_float(a)
    bv = _coerce_float(b)
    if av is None or bv is None:
        return False
    return abs(av - bv) <= tol


def _row_is_valid_month_row(row: Dict[str, Any], month_key: str) -> bool:
    if str(row.get("id") or "") != f"monthly_aud_reval:bybit_live:{month_key}":
        return False
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    return (
        _is_finite(refs.get("start_balance_usdt"))
        and _is_finite(refs.get("end_balance_usdt"))
        and _is_finite(refs.get("start_rate"))
        and _is_finite(refs.get("end_rate"))
        and _is_finite(row.get("result_cash"))
        and _is_finite(row.get("entry_price"))
        and _is_finite(row.get("exit_price"))
        and _is_close(row.get("entry_price"), refs.get("start_rate"))
        and _is_close(row.get("exit_price"), refs.get("end_rate"))
    )


def _persist_error_state(
    *,
    state: Dict[str, Any],
    state_path: Path,
    month_key: Optional[str],
    stage: Optional[str],
    error_code: str,
    error_detail: str,
    tb: str,
) -> None:
    state.update(
        {
            "ok": False,
            "month_key": month_key,
            "stage": stage,
            "last_error_code": error_code,
            "last_error": error_detail,
            "error_detail": error_detail,
            "traceback": tb,
            "updated_at": _utc_now_iso(),
        }
    )
    _save_state(state_path, state)


async def _resolve_boundary_balance(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    boundary_ms: int,
    boundary_local_iso: str,
    month_key: str,
    label: str,
    logger,
) -> Tuple[float, str, Dict[str, Any]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_balance, current_source = await _get_current_unified_balance_usdt_equivalent(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        logger=logger,
    )

    activity_found = await _has_account_activity_since(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        boundary_ms=boundary_ms,
        now_ms=now_ms,
        logger=logger,
    )

    if not activity_found:
        resolved = current_balance
        source = "current_balance_no_activity_after_boundary"
    else:
        latest = await _get_latest_balance_on_or_before(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            boundary_ms=boundary_ms,
            logger=logger,
        )
        if latest is None:
            resolved = current_balance
            source = "wallet_fallback"
        else:
            resolved = latest
            source = "latest_transaction_log_on_or_before_boundary"

    logger.info(
        "MONTHLY_AUD_REVAL_BOUNDARY month=%s label=%s boundary_utc=%s boundary_brisbane=%s current_balance=%s current_balance_source=%s activity_found_after_boundary=%s balance_source=%s resolved_balance=%s",
        month_key,
        label,
        datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).isoformat(),
        boundary_local_iso,
        current_balance,
        current_source,
        activity_found,
        source,
        resolved,
    )

    return resolved, source, {
        "boundary_utc": datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).isoformat(),
        "boundary_brisbane": boundary_local_iso,
        "current_balance": current_balance,
        "current_balance_source": current_source,
        "activity_found_after_boundary": activity_found,
        "balance_source": source,
        "resolved_balance": resolved,
    }


async def sync_monthly_aud_revaluation(
    *,
    data_path: Path,
    state_path: Path,
    bybit_live_credentials: Tuple[str, str, str, str, str],
    oanda_config_provider,
    logger,
) -> Dict[str, Any]:
    started_at = _utc_now_iso()
    rows = _load_rows(data_path)
    state = _load_state(state_path)

    _mode, api_key, api_secret, base_url, _key_source = bybit_live_credentials
    if not api_key or not api_secret:
        _persist_error_state(
            state=state,
            state_path=state_path,
            month_key=None,
            stage="credentials",
            error_code="MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            error_detail="Bybit live credentials are missing",
            tb="",
        )
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR", "Bybit live credentials are missing", stage="credentials")

    try:
        oanda_cfg = oanda_config_provider("live")
    except Exception as exc:
        _persist_error_state(
            state=state,
            state_path=state_path,
            month_key=None,
            stage="credentials",
            error_code="MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
            error_detail=f"OANDA live credentials are missing: {exc}",
            tb=traceback.format_exc(),
        )
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_OANDA_RATE_ERROR", str(exc), stage="credentials") from exc

    changed = 0
    for idx, existing in enumerate(rows):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("row_type") or "") != "monthly_aud_reval":
            continue
        refs = existing.get("raw_refs") if isinstance(existing.get("raw_refs"), dict) else {}
        start_rate = _coerce_float(refs.get("start_rate"))
        end_rate = _coerce_float(refs.get("end_rate"))
        if start_rate is None or end_rate is None:
            continue
        needs_repair = (
            not _is_close(existing.get("entry_price"), start_rate)
            or not _is_close(existing.get("exit_price"), end_rate)
        )
        if not needs_repair:
            continue
        repaired = dict(existing)
        repaired["entry_price"] = start_rate
        repaired["exit_price"] = end_rate
        repaired["side"] = ""
        repaired["stop_loss"] = ""
        repaired["take_profit"] = ""
        repaired["fees"] = ""
        repaired["outcome"] = ""
        repaired["result_pct"] = ""
        repaired["duration_seconds"] = ""
        repaired["updated_at"] = _utc_now_iso()
        rows[idx] = repaired
        changed += 1

    valid_months: List[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        month_val = str(((r.get("raw_refs") if isinstance(r.get("raw_refs"), dict) else {}).get("period_month") or ""))
        if _row_is_valid_month_row(r, month_val):
            valid_months.append(month_val)
    target_months = _iter_target_months(valid_months, now_local=datetime.now(BRISBANE_TZ))

    by_id = {str(r.get("id") or ""): dict(r) for r in rows if isinstance(r, dict) and str(r.get("id") or "").strip()}
    last_boundary_meta: Dict[str, Any] = {}
    last_oanda_window: Dict[str, Any] = {}

    for month_key in target_months:
        start_local, next_local = _month_bounds(month_key)
        start_ms = int(start_local.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(next_local.astimezone(timezone.utc).timestamp() * 1000)
        try:
            start_balance, start_source, start_meta = await _resolve_boundary_balance(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                boundary_ms=start_ms,
                boundary_local_iso=start_local.isoformat(),
                month_key=month_key,
                label="start_balance",
                logger=logger,
            )
            end_balance, end_source, end_meta = await _resolve_boundary_balance(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                boundary_ms=end_ms,
                boundary_local_iso=next_local.isoformat(),
                month_key=month_key,
                label="end_balance",
                logger=logger,
            )
            start_rate_info = await _resolve_boundary_rate(
                month_key=month_key,
                base_url=oanda_cfg["base_url"],
                api_key=oanda_cfg["token"],
                logger=logger,
            )
            end_month_key = f"{next_local.year:04d}-{next_local.month:02d}"
            end_rate_info = await _resolve_boundary_rate(
                month_key=end_month_key,
                base_url=oanda_cfg["base_url"],
                api_key=oanda_cfg["token"],
                logger=logger,
            )
            last_oanda_window = {
                "month_key": month_key,
                "start_boundary_month": month_key,
                "end_boundary_month": end_month_key,
                "start_window": start_rate_info.get("window"),
                "end_window": end_rate_info.get("window"),
            }

            start_rate = _coerce_float(start_rate_info.get("rate"))
            end_rate = _coerce_float(end_rate_info.get("rate"))
            if start_rate is None or end_rate is None or not math.isfinite(start_rate) or not math.isfinite(end_rate) or start_rate == 0 or end_rate == 0:
                raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_OANDA_RATE_ERROR", f"Invalid rates start={start_rate} end={end_rate}", stage="fx_rates")

            start_aud = start_balance / start_rate
            end_aud = end_balance / end_rate
            delta_aud = end_aud - start_aud
            result_cash = _roundup_away_from_zero(delta_aud, 2)
            result_pct = (delta_aud / start_aud) * 100.0 if start_aud else None

            row_id = f"monthly_aud_reval:bybit_live:{month_key}"
            close_local = next_local - timedelta(seconds=1)
            row = {
                "id": row_id,
                "row_type": "monthly_aud_reval",
                "source": "bybit_monthly_aud_reval",
                "account": "live",
                "account_label": "Bybit Live",
                "symbol": "MONTHLY AUD P/L",
                "side": "",
                "status": "closed",
                "open_time": start_local.isoformat(),
                "close_time": close_local.isoformat(),
                "entry_price": start_rate,
                "exit_price": end_rate,
                "stop_loss": "",
                "take_profit": "",
                "fees": "",
                "result_cash": result_cash,
                "result_currency": "AUD",
                "result_pct": "",
                "outcome": "",
                "duration_seconds": "",
                "raw_refs": {
                    "period_month": month_key,
                    "start_rate": start_rate,
                    "end_rate": end_rate,
                    "fx_close": start_rate_info.get("fx_close"),
                    "fx_next_open": start_rate_info.get("fx_next_open"),
                    "end_fx_close": end_rate_info.get("fx_close"),
                    "end_fx_next_open": end_rate_info.get("fx_next_open"),
                    "start_balance_usdt": start_balance,
                    "end_balance_usdt": end_balance,
                    "start_balance_source": start_source,
                    "end_balance_source": end_source,
                    "computed_result_pct": result_pct,
                },
                "updated_at": _utc_now_iso(),
            }
            if not _row_is_valid_month_row(row, month_key):
                raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_INVALID_ROW", f"Calculated row is invalid for {month_key}", stage="validation")
            if by_id.get(row_id) != row:
                by_id[row_id] = row
                changed += 1
            last_boundary_meta = {"month_key": month_key, "start": start_meta, "end": end_meta}
        except Exception as exc:
            code = exc.code if isinstance(exc, MonthlyAudRevalError) else "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR"
            stage = exc.stage if isinstance(exc, MonthlyAudRevalError) else "monthly_loop"
            detail = str(exc) or repr(exc)
            tb = traceback.format_exc()
            logger.error(
                "MONTHLY_AUD_REVAL_ERROR month=%s stage=%s start_ms=%s end_ms=%s detail=%s traceback=%s",
                month_key,
                stage,
                start_ms,
                end_ms,
                detail,
                tb,
            )
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage=stage,
                error_code=code,
                error_detail=detail,
                tb=tb,
            )
            if last_oanda_window:
                state = _load_state(state_path)
                state["last_oanda_window"] = last_oanda_window
                _save_state(state_path, state)
            raise MonthlyAudRevalError(code, detail, stage=stage) from exc

    rows_out = list(by_id.values())
    mandatory_id = f"monthly_aud_reval:bybit_live:{MANDATORY_MONTH}"
    mandatory_row = by_id.get(mandatory_id)
    if mandatory_row is None or not _row_is_valid_month_row(mandatory_row, MANDATORY_MONTH):
        detail = f"Mandatory month {MANDATORY_MONTH} is still missing or invalid"
        _persist_error_state(
            state=state,
            state_path=state_path,
            month_key=MANDATORY_MONTH,
            stage="mandatory_month_check",
            error_code="MONTHLY_AUD_REVAL_MISSING_MANDATORY_ROW",
            error_detail=detail,
            tb="",
        )
        raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_MISSING_MANDATORY_ROW", detail, stage="mandatory_month_check")

    if changed:
        _save_rows(data_path, rows_out)

    state.update(
        {
            "ok": True,
            "month_key": None,
            "stage": "complete",
            "last_error_code": None,
            "last_error": None,
            "error_detail": None,
            "traceback": None,
            "last_boundary_resolution": last_boundary_meta,
            "last_oanda_window": last_oanda_window,
            "last_synced_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "last_result": {
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
                "target_months": target_months,
                "changed": changed,
                "rows": len(rows_out),
                "mandatory_row_id": mandatory_id,
            },
        }
    )
    _save_state(state_path, state)
    return {"ok": True, "changed": changed, "rows": len(rows_out), "processed_months": target_months}
