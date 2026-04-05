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

BYBIT_RECV_WINDOW = "5000"
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


def _bybit_sign_request(timestamp: str, api_key: str, api_secret: str, payload: str) -> str:
    raw = f"{timestamp}{api_key}{BYBIT_RECV_WINDOW}{payload}"
    return hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


async def _bybit_signed_get(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    path: str,
    params: Dict[str, str],
) -> Dict[str, Any]:
    query = _build_bybit_query(params)
    timestamp = str(int(time.time() * 1000))
    signature = _bybit_sign_request(timestamp, api_key, api_secret, query)
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{base_url}{path}" + (f"?{query}" if query else "")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers=headers)
        status_code = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            f"Bybit GET failed path={path} query={query} error={exc}",
            stage="bybit_request",
        ) from exc

    if payload.get("retCode") not in (0, "0"):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            f"Bybit GET retCode failure path={path} status={status_code} retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}",
            stage="bybit_request",
        )
    return payload


async def _fetch_transaction_pages(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    start_time: int,
    end_time: int,
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
) -> Tuple[float, str]:
    payload = await _bybit_signed_get(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        path="/v5/account/wallet-balance",
        params={"accountType": "UNIFIED"},
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
) -> bool:
    if boundary_ms > now_ms:
        return False
    rows = await _fetch_transaction_rows_range(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        start_ms=boundary_ms,
        end_ms=now_ms,
    )
    return len(rows) > 0


async def _get_latest_balance_on_or_before(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    boundary_ms: int,
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
    )

    activity_found = await _has_account_activity_since(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        boundary_ms=boundary_ms,
        now_ms=now_ms,
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

    valid_months: List[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        month_val = str(((r.get("raw_refs") if isinstance(r.get("raw_refs"), dict) else {}).get("period_month") or ""))
        if _row_is_valid_month_row(r, month_val):
            valid_months.append(month_val)
    target_months = _iter_target_months(valid_months, now_local=datetime.now(BRISBANE_TZ))

    by_id = {str(r.get("id") or ""): dict(r) for r in rows if isinstance(r, dict) and str(r.get("id") or "").strip()}
    changed = 0
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
