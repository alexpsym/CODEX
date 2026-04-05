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


class MonthlyAudRevalError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.stage = stage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_bybit_query(params: Dict[str, str]) -> str:
    if not params:
        return ""
    return "&".join(f"{key}={value}" for key, value in sorted(params.items()))


def _bybit_sign_request(timestamp: str, api_key: str, api_secret: str, body: str) -> str:
    payload = f"{timestamp}{api_key}{BYBIT_RECV_WINDOW}{body}"
    return hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_float(record: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in record:
            parsed = _coerce_float(record.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


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
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers=headers)
        status_code = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            f"Bybit request failed path={path} status={getattr(exc, 'response', None) and exc.response.status_code}: {exc}",
            stage="bybit_request",
        ) from exc

    ret_code = payload.get("retCode") if isinstance(payload, dict) else None
    if ret_code not in (0, "0"):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            f"Bybit retCode failure path={path} status={status_code} retCode={ret_code} retMsg={payload.get('retMsg')}",
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
    out: List[Dict[str, Any]] = []
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
            out.extend([row for row in page if isinstance(row, dict)])
        cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
        if not cursor:
            break
    return out


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


async def _get_balance_before(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    timestamp: int,
    logger,
    month_key: str,
    stage: str,
) -> float:
    look_back_end = timestamp - 1
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - TWO_YEARS_MS

    balances: Dict[str, Dict[str, Optional[float]]] = {}
    rows_fetched = 0
    rows_skipped = 0
    wallet_fallback_used = False

    while look_back_end >= earliest:
        start = max(earliest, look_back_end - SEVEN_DAYS_MS + 1)
        logs = await _fetch_transaction_pages(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start,
            end_time=look_back_end,
        )
        rows_fetched += len(logs)
        if logs:
            def _tx_key(item: Dict[str, Any]) -> int:
                tx = _parse_int(item.get("transactionTime"))
                return tx if tx is not None else 0

            logs.sort(key=_tx_key)
            for log in logs:
                coin = str(log.get("coin") or "").strip().upper()
                tx_time = _parse_int(log.get("transactionTime"))
                if not coin or tx_time is None:
                    rows_skipped += 1
                    continue
                amount = _pick_float(log, ("cashBalance", "walletBalance", "equity"))
                usd_value = _pick_float(log, ("usdValue",))
                if amount is None and usd_value is None:
                    rows_skipped += 1
                    continue
                entry = balances.setdefault(coin, {"amount": None, "usd": None})
                if amount is not None:
                    entry["amount"] = amount
                if usd_value is not None:
                    entry["usd"] = usd_value
            break
        if start == earliest:
            break
        look_back_end = start - 1

    if not balances:
        wallet_fallback_used = True
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
        )
        entries = ((payload.get("result") or {}).get("list") or [])
        coins = entries[0].get("coin", []) if entries and isinstance(entries[0], dict) else []
        for coin_entry in coins:
            if not isinstance(coin_entry, dict):
                rows_skipped += 1
                continue
            coin = str(coin_entry.get("coin") or "").strip().upper()
            if not coin:
                rows_skipped += 1
                continue
            amount = _pick_float(coin_entry, ("cashBalance", "walletBalance", "equity"))
            usd_value = _pick_float(coin_entry, ("usdValue",))
            if amount is None and usd_value is None:
                rows_skipped += 1
                continue
            entry = balances.setdefault(coin, {"amount": None, "usd": None})
            if amount is not None:
                entry["amount"] = amount
            if usd_value is not None:
                entry["usd"] = usd_value

    total = 0.0
    for coin, values in balances.items():
        amount = values.get("amount")
        usd_value = values.get("usd")
        if coin == "USDT":
            total += amount if amount is not None else (usd_value or 0.0)
            continue
        if amount is not None:
            total += amount * await _get_price(base_url=base_url, coin=coin, timestamp=timestamp)
        elif usd_value is not None:
            total += usd_value

    logger.info(
        "MONTHLY_AUD_REVAL_BALANCE_SUMMARY month=%s stage=%s timestamp=%s rows_fetched=%s rows_skipped=%s distinct_coins=%s wallet_fallback=%s total=%s",
        month_key,
        stage,
        timestamp,
        rows_fetched,
        rows_skipped,
        len(balances),
        wallet_fallback_used,
        total,
    )

    if not math.isfinite(total):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            "Computed non-finite balance total.",
            stage=stage,
        )
    return total


def _month_bounds(month_key: str) -> Tuple[datetime, datetime]:
    year, month = [int(x) for x in month_key.split("-")]
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    if month == 12:
        next_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    else:
        next_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=BRISBANE_TZ)
    return start_local, next_local


def _iter_missing_months(existing_months: Iterable[str], *, now_local: datetime) -> List[str]:
    known = {m for m in existing_months if isinstance(m, str) and len(m) == 7}
    year = 2026
    month = 3
    current_month_key = now_local.strftime("%Y-%m")
    out: List[str] = []
    while True:
        key = f"{year:04d}-{month:02d}"
        if key >= current_month_key:
            break
        if key not in known:
            out.append(key)
        month += 1
        if month > 12:
            month = 1
            year += 1
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
    instrument: str,
    start_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, Any]]:
    token = (api_key or "").strip().strip('"').strip("'")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/v3/instruments/{instrument}/candles"
    params = {
        "price": "M",
        "granularity": "D",
        "from": start_utc.isoformat().replace("+00:00", "Z"),
        "to": end_utc.isoformat().replace("+00:00", "Z"),
        "includeFirst": "true",
        "dailyAlignment": "0",
        "alignmentTimezone": "Australia/Brisbane",
    }
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
            f"OANDA candles failed status={resp.status_code}: {resp.text[:250]}",
            stage="oanda_candles",
        )
    payload = resp.json()
    candles = payload.get("candles") if isinstance(payload, dict) else []
    if not isinstance(candles, list):
        return []
    return [c for c in candles if isinstance(c, dict)]


async def _resolve_boundary_rate(
    *,
    month_key: str,
    base_url: str,
    api_key: str,
) -> Dict[str, float]:
    start_local, _ = _month_bounds(month_key)
    candles = await _fetch_oanda_candles(
        base_url=base_url,
        api_key=api_key,
        instrument="AUD_USD",
        start_utc=(start_local - timedelta(days=10)).astimezone(timezone.utc),
        end_utc=(start_local + timedelta(days=10)).astimezone(timezone.utc),
    )

    prev_close: Optional[float] = None
    next_open: Optional[float] = None
    start_date = start_local.date()
    for candle in candles:
        mid = candle.get("mid") if isinstance(candle.get("mid"), dict) else {}
        candle_time = _parse_oanda_time(str(candle.get("time") or ""))
        local_date = candle_time.astimezone(BRISBANE_TZ).date()
        if local_date < start_date and candle.get("complete"):
            val = _coerce_float(mid.get("c"))
            if val is not None:
                prev_close = val
        elif local_date >= start_date and next_open is None:
            val = _coerce_float(mid.get("o"))
            if val is not None:
                next_open = val
                break

    if prev_close is None:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
            f"Missing previous complete close for {month_key}",
            stage="boundary_prev_close",
        )
    if next_open is None:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_PENDING_NEXT_OPEN",
            f"Missing next open candle for {month_key}",
            stage="boundary_next_open",
        )
    return {"rate": (prev_close + next_open) / 2.0, "fx_close": prev_close, "fx_next_open": next_open}


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) else payload
    return [row for row in items if isinstance(row, dict)] if isinstance(items, list) else []


def _save_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "items": sorted(rows, key=lambda r: str(r.get("close_time") or ""), reverse=True),
        "updated_at": _utc_now_iso(),
    }
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
        code = "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR"
        detail = "Bybit live credentials are missing"
        _persist_error_state(
            state=state,
            state_path=state_path,
            month_key=None,
            stage="credentials",
            error_code=code,
            error_detail=detail,
            tb="",
        )
        raise MonthlyAudRevalError(code, detail, stage="credentials")

    try:
        oanda_cfg = oanda_config_provider("live")
    except Exception as exc:
        code = "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR"
        detail = f"OANDA live credentials are missing: {exc}"
        _persist_error_state(
            state=state,
            state_path=state_path,
            month_key=None,
            stage="credentials",
            error_code=code,
            error_detail=detail,
            tb=traceback.format_exc(),
        )
        raise MonthlyAudRevalError(code, detail, stage="credentials") from exc

    now_local = datetime.now(BRISBANE_TZ)
    existing_months = [str((row.get("raw_refs") or {}).get("period_month") or "") for row in rows]
    missing_months = _iter_missing_months(existing_months, now_local=now_local)
    by_id = {str(row.get("id") or ""): dict(row) for row in rows if str(row.get("id") or "").strip()}
    changed = 0

    for month_key in missing_months:
        start_local, next_local = _month_bounds(month_key)
        start_ms = int(start_local.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(next_local.astimezone(timezone.utc).timestamp() * 1000)

        logger.info(
            "MONTHLY_AUD_REVAL_STAGE month=%s stage=begin start_ms=%s end_ms=%s",
            month_key,
            start_ms,
            end_ms,
        )

        try:
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=start_balance:begin", month_key)
            start_balance = await _get_balance_before(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                timestamp=start_ms,
                logger=logger,
                month_key=month_key,
                stage="start_balance",
            )
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=start_balance:done value=%s", month_key, start_balance)
        except Exception as exc:
            code = "MONTHLY_AUD_REVAL_START_BALANCE_ERROR"
            tb = traceback.format_exc()
            detail = str(exc) or repr(exc)
            logger.error(
                "MONTHLY_AUD_REVAL_ERROR month=%s stage=start_balance start_ms=%s end_ms=%s error=%s traceback=%s",
                month_key,
                start_ms,
                end_ms,
                detail,
                tb,
            )
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage="start_balance",
                error_code=code,
                error_detail=detail,
                tb=tb,
            )
            raise MonthlyAudRevalError(code, detail, stage="start_balance") from exc

        try:
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=end_balance:begin", month_key)
            end_balance = await _get_balance_before(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                timestamp=end_ms,
                logger=logger,
                month_key=month_key,
                stage="end_balance",
            )
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=end_balance:done value=%s", month_key, end_balance)
        except Exception as exc:
            code = "MONTHLY_AUD_REVAL_END_BALANCE_ERROR"
            tb = traceback.format_exc()
            detail = str(exc) or repr(exc)
            logger.error(
                "MONTHLY_AUD_REVAL_ERROR month=%s stage=end_balance start_ms=%s end_ms=%s error=%s traceback=%s",
                month_key,
                start_ms,
                end_ms,
                detail,
                tb,
            )
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage="end_balance",
                error_code=code,
                error_detail=detail,
                tb=tb,
            )
            raise MonthlyAudRevalError(code, detail, stage="end_balance") from exc

        try:
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=start_rate:begin", month_key)
            start_rate_info = await _resolve_boundary_rate(
                month_key=month_key,
                base_url=oanda_cfg["base_url"],
                api_key=oanda_cfg["token"],
            )
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=start_rate:done rate=%s", month_key, start_rate_info.get("rate"))
        except Exception as exc:
            code = "MONTHLY_AUD_REVAL_START_RATE_ERROR"
            tb = traceback.format_exc()
            detail = str(exc) or repr(exc)
            logger.error(
                "MONTHLY_AUD_REVAL_ERROR month=%s stage=start_rate start_ms=%s end_ms=%s error=%s traceback=%s",
                month_key,
                start_ms,
                end_ms,
                detail,
                tb,
            )
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage="start_rate",
                error_code=code,
                error_detail=detail,
                tb=tb,
            )
            raise MonthlyAudRevalError(code, detail, stage="start_rate") from exc

        end_month_key = f"{next_local.year:04d}-{next_local.month:02d}"
        try:
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=end_rate:begin end_month=%s", month_key, end_month_key)
            end_rate_info = await _resolve_boundary_rate(
                month_key=end_month_key,
                base_url=oanda_cfg["base_url"],
                api_key=oanda_cfg["token"],
            )
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=end_rate:done rate=%s", month_key, end_rate_info.get("rate"))
        except Exception as exc:
            code = "MONTHLY_AUD_REVAL_END_RATE_ERROR"
            tb = traceback.format_exc()
            detail = str(exc) or repr(exc)
            logger.error(
                "MONTHLY_AUD_REVAL_ERROR month=%s stage=end_rate start_ms=%s end_ms=%s error=%s traceback=%s",
                month_key,
                start_ms,
                end_ms,
                detail,
                tb,
            )
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage="end_rate",
                error_code=code,
                error_detail=detail,
                tb=tb,
            )
            raise MonthlyAudRevalError(code, detail, stage="end_rate") from exc

        start_rate = _coerce_float(start_rate_info.get("rate"))
        end_rate = _coerce_float(end_rate_info.get("rate"))
        if not start_rate or not end_rate:
            detail = f"Resolved invalid FX rates start_rate={start_rate} end_rate={end_rate}"
            tb = ""
            _persist_error_state(
                state=state,
                state_path=state_path,
                month_key=month_key,
                stage="calc",
                error_code="MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
                error_detail=detail,
                tb=tb,
            )
            raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_OANDA_RATE_ERROR", detail, stage="calc")

        start_aud = start_balance / start_rate
        end_aud = end_balance / end_rate
        delta_aud = end_aud - start_aud
        monthly_pl_aud = _roundup_away_from_zero(delta_aud, 2)
        result_pct = (delta_aud / start_aud) * 100.0 if start_aud else None
        outcome = "Win" if monthly_pl_aud > 0 else ("Loss" if monthly_pl_aud < 0 else "Breakeven")

        row_id = f"monthly_aud_reval:bybit_live:{month_key}"
        close_local = next_local - timedelta(seconds=1)
        row = {
            "id": row_id,
            "row_type": "monthly_aud_reval",
            "source": "bybit_monthly_aud_reval",
            "account": "live",
            "account_label": "Bybit Live",
            "symbol": "MONTHLY AUD P/L",
            "side": "FX Reval",
            "status": "closed",
            "open_time": start_local.isoformat(),
            "close_time": close_local.isoformat(),
            "entry_price": start_balance,
            "exit_price": end_balance,
            "fees": None,
            "result_cash": monthly_pl_aud,
            "result_currency": "AUD",
            "result_pct": result_pct,
            "outcome": outcome,
            "duration_seconds": int((close_local - start_local).total_seconds()),
            "raw_refs": {
                "start_rate": start_rate,
                "end_rate": end_rate,
                "fx_close": start_rate_info.get("fx_close"),
                "fx_next_open": start_rate_info.get("fx_next_open"),
                "end_fx_close": end_rate_info.get("fx_close"),
                "end_fx_next_open": end_rate_info.get("fx_next_open"),
                "start_balance_usdt": start_balance,
                "end_balance_usdt": end_balance,
                "period_month": month_key,
            },
            "updated_at": _utc_now_iso(),
        }

        if by_id.get(row_id) != row:
            by_id[row_id] = row
            changed += 1
            logger.info("MONTHLY_AUD_REVAL_STAGE month=%s stage=upsert_row id=%s", month_key, row_id)

    if changed:
        _save_rows(data_path, list(by_id.values()))

    state.update(
        {
            "ok": True,
            "month_key": None,
            "stage": "complete",
            "last_error_code": None,
            "last_error": None,
            "error_detail": None,
            "traceback": None,
            "last_synced_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "last_result": {
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
                "missing_months": missing_months,
                "changed": changed,
                "rows": len(by_id),
            },
        }
    )
    _save_state(state_path, state)
    return {"ok": True, "changed": changed, "rows": len(by_id), "processed_months": missing_months}
