from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

BYBIT_RECV_WINDOW = "5000"
BRISBANE_TZ = ZoneInfo("Australia/Brisbane")
SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
TWO_YEARS_MS = 730 * 24 * 60 * 60 * 1000
MIN_MONTH = "2026-03"


class MonthlyAudRevalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
        if value == "":
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_float(record: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in record:
            value = _coerce_float(record.get(key))
            if value is not None:
                return value
    return None


def _date_range_chunks(start: int, end: int, delta: int) -> Generator[Tuple[int, int], None, None]:
    current = start
    while current <= end:
        chunk_end = min(current + delta - 1, end)
        yield current, chunk_end
        current = chunk_end + 1


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
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    ret_code = payload.get("retCode")
    if ret_code not in (0, "0"):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            f"Bybit request failed: {payload.get('retMsg') or ret_code}",
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
    symbol = f"{coin}USDT"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "1",
        "start": str(timestamp),
        "end": str(timestamp + 60000),
        "limit": "1",
    }
    url = f"{base_url}/v5/market/mark-price-kline"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
    if resp.status_code >= 400:
        return 0.0
    payload = resp.json()
    if payload.get("retCode") not in (0, "0"):
        return 0.0
    rows = ((payload.get("result") or {}).get("list") or [])
    if rows:
        try:
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
) -> float:
    look_back_end = timestamp - 1
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - TWO_YEARS_MS
    balances: Dict[str, Dict[str, Optional[float]]] = {}

    while look_back_end >= earliest:
        start = max(earliest, look_back_end - SEVEN_DAYS_MS + 1)
        logs = await _fetch_transaction_pages(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            start_time=start,
            end_time=look_back_end,
        )
        if logs:
            logs.sort(key=lambda r: int(r.get("transactionTime", 0) or 0))
            for log in logs:
                coin = str(log.get("coin") or "").strip().upper()
                if not coin:
                    continue
                amount = _pick_float(log, ("cashBalance", "walletBalance", "equity"))
                usd_value = _pick_float(log, ("usdValue",))
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
        payload = await _bybit_signed_get(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
        )
        entries = ((payload.get("result") or {}).get("list") or [])
        coins = entries[0].get("coin", []) if entries and isinstance(entries[0], dict) else []
        for coin in coins:
            if not isinstance(coin, dict):
                continue
            name = str(coin.get("coin") or "").strip().upper()
            if not name:
                continue
            amount = _pick_float(coin, ("cashBalance", "walletBalance", "equity"))
            usd_value = _pick_float(coin, ("usdValue",))
            entry = balances.setdefault(name, {"amount": None, "usd": None})
            if amount is not None:
                entry["amount"] = amount
            if usd_value is not None:
                entry["usd"] = usd_value

    total = 0.0
    for coin, values in balances.items():
        amount = values.get("amount")
        usd_value = values.get("usd")
        if coin == "USDT":
            if amount is not None:
                total += amount
            elif usd_value is not None:
                total += usd_value
            continue
        if amount is not None:
            price = await _get_price(base_url=base_url, coin=coin, timestamp=timestamp)
            total += amount * price
        elif usd_value is not None:
            total += usd_value

    if not math.isfinite(total):
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
            "Computed non-finite Bybit balance.",
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
        month_key = f"{year:04d}-{month:02d}"
        if month_key >= current_month_key:
            break
        if month_key not in known:
            out.append(month_key)
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
    if value > 0:
        return math.ceil(value * factor) / factor
    return math.floor(value * factor) / factor


async def _fetch_oanda_candles(
    *,
    base_url: str,
    account_id: str,
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
            f"OANDA candles failed ({resp.status_code}): {resp.text[:250]}",
        )
    payload = resp.json()
    candles = payload.get("candles") if isinstance(payload, dict) else []
    if not isinstance(candles, list):
        candles = []
    return [c for c in candles if isinstance(c, dict)]


async def _resolve_boundary_rate(
    *,
    month_key: str,
    base_url: str,
    account_id: str,
    api_key: str,
) -> Dict[str, float]:
    start_local, _next_local = _month_bounds(month_key)
    prev_from = (start_local - timedelta(days=10)).astimezone(timezone.utc)
    prev_to = start_local.astimezone(timezone.utc)
    next_to = (start_local + timedelta(days=10)).astimezone(timezone.utc)

    candles = await _fetch_oanda_candles(
        base_url=base_url,
        account_id=account_id,
        api_key=api_key,
        instrument="AUD_USD",
        start_utc=prev_from,
        end_utc=next_to,
    )

    prev_close: Optional[float] = None
    next_open: Optional[float] = None
    for candle in candles:
        mid = candle.get("mid") if isinstance(candle.get("mid"), dict) else {}
        candle_time = _parse_oanda_time(str(candle.get("time") or ""))
        candle_local_date = candle_time.astimezone(BRISBANE_TZ).date()
        start_date = start_local.date()
        if candle_local_date < start_date and candle.get("complete"):
            close_val = _coerce_float(mid.get("c"))
            if close_val is not None:
                prev_close = close_val
        elif candle_local_date >= start_date and next_open is None:
            open_val = _coerce_float(mid.get("o"))
            if open_val is not None:
                next_open = open_val
                break

    if prev_close is None:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR",
            f"Missing previous complete close for {month_key}",
        )
    if next_open is None:
        raise MonthlyAudRevalError(
            "MONTHLY_AUD_REVAL_PENDING_NEXT_OPEN",
            f"Missing next-period open candle for {month_key}",
        )

    return {
        "rate": (prev_close + next_open) / 2.0,
        "fx_close": prev_close,
        "fx_next_open": next_open,
    }


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [row for row in items if isinstance(row, dict)]


def _save_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(rows, key=lambda x: str(x.get("close_time") or ""), reverse=True)
    payload = {"items": rows_sorted, "updated_at": _utc_now_iso()}
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
        message = "Bybit live credentials are missing"
        logger.error("%s %s", code, message)
        state.update({"ok": False, "last_error_code": code, "last_error": message, "updated_at": _utc_now_iso()})
        _save_state(state_path, state)
        raise MonthlyAudRevalError(code, message)

    try:
        oanda_cfg = oanda_config_provider("live")
    except Exception as exc:
        code = "MONTHLY_AUD_REVAL_OANDA_RATE_ERROR"
        message = f"OANDA live credentials are missing: {exc}"
        logger.error("%s %s", code, message)
        state.update({"ok": False, "last_error_code": code, "last_error": message, "updated_at": _utc_now_iso()})
        _save_state(state_path, state)
        raise MonthlyAudRevalError(code, message)

    now_local = datetime.now(BRISBANE_TZ)
    existing_months = [str((row.get("raw_refs") or {}).get("period_month") or "") for row in rows]
    missing_months = _iter_missing_months(existing_months, now_local=now_local)

    by_id = {str(row.get("id") or ""): dict(row) for row in rows if str(row.get("id") or "").strip()}
    changed = 0

    for month_key in missing_months:
        try:
            start_local, next_local = _month_bounds(month_key)
            start_ms = int(start_local.astimezone(timezone.utc).timestamp() * 1000)
            end_ms = int(next_local.astimezone(timezone.utc).timestamp() * 1000)

            start_balance = await _get_balance_before(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                timestamp=start_ms,
            )
            end_balance = await _get_balance_before(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                timestamp=end_ms,
            )

            start_rate_info = await _resolve_boundary_rate(
                month_key=month_key,
                base_url=oanda_cfg["base_url"],
                account_id=oanda_cfg["account_id"],
                api_key=oanda_cfg["token"],
            )
            end_year = next_local.year
            end_month = next_local.month
            end_month_key = f"{end_year:04d}-{end_month:02d}"
            end_rate_info = await _resolve_boundary_rate(
                month_key=end_month_key,
                base_url=oanda_cfg["base_url"],
                account_id=oanda_cfg["account_id"],
                api_key=oanda_cfg["token"],
            )

            start_rate = start_rate_info["rate"]
            end_rate = end_rate_info["rate"]
            if not start_rate or not end_rate:
                raise MonthlyAudRevalError("MONTHLY_AUD_REVAL_OANDA_RATE_ERROR", "Resolved zero boundary FX rate")

            start_aud = start_balance / start_rate
            end_aud = end_balance / end_rate
            delta_aud = end_aud - start_aud
            monthly_pl_aud = _roundup_away_from_zero(delta_aud, 2)
            result_pct = (delta_aud / start_aud * 100.0) if start_aud else None
            if monthly_pl_aud > 0:
                outcome = "Win"
            elif monthly_pl_aud < 0:
                outcome = "Loss"
            else:
                outcome = "Breakeven"

            row_id = f"monthly_aud_reval:bybit_live:{month_key}"
            close_local = next_local - timedelta(seconds=1)
            duration_seconds = int((close_local - start_local).total_seconds())
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
                "duration_seconds": duration_seconds,
                "raw_refs": {
                    "start_rate": start_rate,
                    "end_rate": end_rate,
                    "fx_close": start_rate_info["fx_close"],
                    "fx_next_open": start_rate_info["fx_next_open"],
                    "end_fx_close": end_rate_info["fx_close"],
                    "end_fx_next_open": end_rate_info["fx_next_open"],
                    "start_balance_usdt": start_balance,
                    "end_balance_usdt": end_balance,
                    "period_month": month_key,
                },
                "updated_at": _utc_now_iso(),
            }
            previous = by_id.get(row_id)
            if previous != row:
                by_id[row_id] = row
                changed += 1
        except MonthlyAudRevalError:
            raise
        except Exception as exc:
            raise MonthlyAudRevalError(
                "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR",
                f"Monthly sync failed for {month_key}: {exc}",
            ) from exc

    if changed:
        _save_rows(data_path, list(by_id.values()))

    state.update(
        {
            "ok": True,
            "last_error_code": None,
            "last_error": None,
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
