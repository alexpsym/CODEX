"""Download Bybit trade history and export to CSV."""
from __future__ import annotations

import csv
import os
import time
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Generator, IO, List, Tuple

import requests

from env_helpers import load_bybit_live_env
from bybit_credentials import resolve_bybit_credentials_for

BRISBANE_TZ = timezone(timedelta(hours=10))

load_bybit_live_env()

try:
    from openpyxl import Workbook  # pylint: disable=import-error
except ImportError:  # pragma: no cover - package not installed during tests
    Workbook = None  # type: ignore

try:
    from pybit.unified_trading import HTTP  # pylint: disable=import-error
except ImportError:  # pragma: no cover - package not installed during tests
    HTTP = None  # type: ignore


def _build_bybit_session(api_key: str, api_secret: str, base_url: str) -> "HTTP":
    """Return a pybit HTTP session across multiple pybit versions.

    The pybit unified_trading HTTP constructor has changed signatures across
    releases (e.g. some accept `endpoint=`, some accept `base_url=` or only
    `testnet=`). Render deployments can therefore fail at runtime with:

        _V5HTTPManager.__init__() got an unexpected keyword argument 'endpoint'

    This helper progressively tries the common signatures and falls back to
    `testnet=` inference when the base URL cannot be injected.
    """

    if HTTP is None:  # pragma: no cover
        raise ImportError("pybit module is required")

    url = (base_url or "").strip().rstrip("/")
    inferred_testnet = any(token in url for token in ("testnet", "demo"))

    # 1) Newer code in this repo historically used endpoint=.
    try:
        return HTTP(api_key=api_key, api_secret=api_secret, endpoint=url)
    except TypeError:
        pass

    # 2) Some versions renamed the parameter.
    try:
        return HTTP(api_key=api_key, api_secret=api_secret, base_url=url)
    except TypeError:
        pass

    # 3) Some versions use a testnet flag and do not allow overriding the URL.
    try:
        return HTTP(api_key=api_key, api_secret=api_secret, testnet=inferred_testnet)
    except TypeError:
        pass

    # 4) Last resort: try without any environment parameter.
    return HTTP(api_key=api_key, api_secret=api_secret)

SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
# Bybit only allows querying executions from roughly the last two years.
# Using a small cushion prevents requests right at the limit from failing.
TWO_YEARS_MS = 730 * 24 * 60 * 60 * 1000
LIMIT_CUSHION_MS = 60 * 1000  # one minute

# Column order and mapping derived from Bybit-UM-USDTPerp-TradeHistory template
TEMPLATE_HEADERS = [
    "contracts",
    "Order No.",
    "Direction",
    "Order Type",
    "Filled Qty",
    "Filled Price",
    "Order Price",
    "Filled Type",
    "Trading Fee Rate",
    "Fees Paid",
    "Trasaction ID",
    "Transaction Time(UTC+10)",
]

HEADER_MAPPING = {
    "contracts": "symbol",
    "Order No.": "orderId",
    "Direction": "side",
    "Order Type": "orderType",
    "Filled Qty": "execQty",
    "Filled Price": "execPrice",
    "Order Price": "orderPrice",
    "Filled Type": "execType",
    "Trading Fee Rate": "feeRate",
    "Fees Paid": "execFee",
    "Trasaction ID": "execId",
    "Transaction Time(UTC+10)": "execTime",
}


def _apply_template(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reorder and rename row keys to match TEMPLATE_HEADERS."""
    formatted = []
    for row in rows:
        new_row: Dict[str, Any] = {}
        for header in TEMPLATE_HEADERS:
            key = HEADER_MAPPING.get(header, header)
            new_row[header] = row.get(key, "")
        formatted.append(new_row)
    return formatted


def _parse_date_start(date_str: str) -> int:
    """Convert YYYY-MM-DD to Brisbane local start-of-day in epoch ms."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=BRISBANE_TZ
    )
    return int(dt.timestamp() * 1000)


def _parse_date_end(date_str: str) -> int:
    """Convert YYYY-MM-DD to Brisbane local end-of-day in epoch ms (inclusive)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, microsecond=999000, tzinfo=BRISBANE_TZ
    )
    return int(dt.timestamp() * 1000)


def _limit_to_two_years(start: int | None, end: int | None) -> tuple[int, int]:
    """Clip start and end times to the last two years."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - TWO_YEARS_MS + LIMIT_CUSHION_MS
    if end is None:
        end = now_ms
    else:
        end = min(end, now_ms)
    end = max(end, earliest)

    if start is None:
        start = earliest
    else:
        start = max(start, earliest)
    start = min(start, end)
    return start, end


def _normalize_endpoint(mode: str, base_url: str) -> str:
    """Return a safe endpoint for the given Bybit environment.

    This is a defensive guardrail for misconfigured env vars.
    Demo keys must use https://api-demo.bybit.com; testnet uses
    https://api-testnet.bybit.com.
    """

    url = (base_url or "").strip().rstrip("/")
    m = (mode or "live").strip().lower()

    if m == "demo":
        # If someone accidentally points demo at testnet/mainnet, force it.
        if "api-demo.bybit.com" not in url:
            return "https://api-demo.bybit.com"
        return url
    if m == "testnet":
        if "api-testnet.bybit.com" not in url:
            return "https://api-testnet.bybit.com"
        return url
    # live
    if "api.bybit.com" not in url:
        return "https://api.bybit.com"
    return url


def _limit_time_window(mode: str, start: int | None, end: int | None) -> tuple[int, int]:
    """Clip start and end times based on the environment retention rules."""

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    m = (mode or "live").strip().lower()
    if m == "demo":
        # Demo Trading keeps orders for 7 days.
        earliest = now_ms - SEVEN_DAYS_MS + LIMIT_CUSHION_MS
    else:
        earliest = now_ms - TWO_YEARS_MS + LIMIT_CUSHION_MS

    if end is None:
        end = now_ms
    else:
        end = min(end, now_ms)
    end = max(end, earliest)

    if start is None:
        start = earliest
    else:
        start = max(start, earliest)
    start = min(start, end)
    return start, end


def _fetch_pages(session: HTTP, **params: Any) -> Generator[List[Dict[str, Any]], None, None]:
    """Yield pages of execution data from Bybit."""
    cursor: str | None = None
    while True:
        if cursor:
            params["cursor"] = cursor
        response = session.get_executions(**params)
        result = response["result"]
        yield result.get("list", [])
        cursor = result.get("nextPageCursor")
        if not cursor:
            break


def _bybit_make_query(params: Dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """Build a query string and an ordered params list.

    Bybit verifies the signature using the exact queryString that arrives.
    Therefore the ordering used for signing must match the ordering used
    in the actual request.
    """

    ordered: list[tuple[str, str]] = []
    for key in sorted(params.keys()):
        value = params.get(key)
        if value is None:
            continue
        ordered.append((str(key), str(value)))

    # Use urlencode so encoding matches what requests will send.
    query = urllib.parse.urlencode(ordered, safe="")
    return query, ordered


def _bybit_signed_get_v5(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    path: str,
    params: Dict[str, Any],
    recv_window: str = "5000",
) -> Dict[str, Any]:
    """Signed V5 GET request for environments pybit cannot target (e.g. api-demo)."""
    timestamp = str(int(time.time() * 1000))
    query, ordered_params = _bybit_make_query(params)
    origin = f"{timestamp}{api_key}{recv_window}{query}"
    signature = hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{base_url.rstrip('/')}{path}"
    # IMPORTANT: send params in the same order used to build the signature.
    resp = requests.get(url, headers=headers, params=ordered_params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if (data or {}).get("retCode") not in (0, "0", None):
        raise RuntimeError(f"Bybit API error: {data.get('retMsg') or data}")
    return data


def _fetch_pages_demo(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    **params: Any,
) -> Generator[List[Dict[str, Any]], None, None]:
    cursor = None
    while True:
        query_params = dict(params)
        if cursor:
            query_params["cursor"] = cursor
        payload = _bybit_signed_get_v5(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            path="/v5/execution/list",
            params=query_params,
        )
        result = (payload.get("result") or {}) if isinstance(payload, dict) else {}
        page = (result.get("list") or []) if isinstance(result, dict) else []
        if not isinstance(page, list):
            page = []
        yield [row for row in page if isinstance(row, dict)]
        cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
        if not cursor:
            break


def _fetch_transaction_pages(
    session: HTTP, **params: Any
) -> Generator[List[Dict[str, Any]], None, None]:
    """Yield pages of transaction log data from Bybit."""
    cursor: str | None = None
    while True:
        if cursor:
            params["cursor"] = cursor
        response = session.get_transaction_log(**params)
        result = response["result"]
        yield result.get("list", [])
        cursor = result.get("nextPageCursor")
        if not cursor:
            break


def _write_csv(filename: str, rows: List[Dict[str, Any]]) -> None:
    """Write rows to a CSV file."""
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        if rows:
            writer = csv.DictWriter(csvfile, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        else:
            csvfile.write("")


def write_blank_trade_history_template(path: str | os.PathLike[str] | IO[str]) -> str | IO[str]:
    """Write a header-only CSV matching Bybit's trade history export template."""
    if hasattr(path, "write"):
        writer = csv.DictWriter(path, fieldnames=TEMPLATE_HEADERS)
        writer.writeheader()
        return path
    with open(path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=TEMPLATE_HEADERS)
        writer.writeheader()
    return path


def _convert_exec_time(row: Dict[str, Any], template: bool = False) -> None:
    """Change execTime from milliseconds to a human readable string."""
    if "execTime" in row:
        try:
            ms = int(row["execTime"])
            fmt = "%H:%M %Y-%m-%d" if template else "%Y-%m-%dT%H:%M:%S"
            row["execTime"] = datetime.fromtimestamp(ms / 1000, tz=BRISBANE_TZ).strftime(
                fmt
            )
        except (ValueError, TypeError):
            pass
    if "side" in row and row["side"] is not None:
        row["side"] = str(row["side"]).upper()
    if "orderType" in row:
        typ = str(row["orderType"])
        row["orderType"] = "--" if typ.upper() == "UNKNOWN" or typ.strip() == "" else typ


def convert_csv_file(in_file: str, out_file: str | None = None) -> str:
    """Reformat a raw Bybit CSV file to match TEMPLATE_HEADERS."""
    with open(in_file, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        rows = list(reader)

    for row in rows:
        _convert_exec_time(row, template=True)

    rows = _apply_template(rows)

    if out_file is None:
        base, _ = os.path.splitext(in_file)
        out_file = f"{base}_formatted.csv"

    _write_csv(out_file, rows)
    return out_file


def _date_range_chunks(start: int, end: int, delta: int) -> Generator[Tuple[int, int], None, None]:
    """Yield start and end timestamps split by delta."""
    current = start
    while current <= end:
        chunk_end = min(current + delta - 1, end)
        yield current, chunk_end
        current = chunk_end + 1


def _add_months(date: datetime, months: int) -> datetime:
    """Return date shifted by a number of months."""
    year = date.year + (date.month - 1 + months) // 12
    month = (date.month - 1 + months) % 12 + 1
    return date.replace(year=year, month=month)


def _coerce_float(value: Any) -> float | None:
    """Best effort conversion of API values to float."""
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


def _pick_float(record: Dict[str, Any], keys: Tuple[str, ...]) -> float | None:
    """Return the first convertible float from record using provided keys."""
    for key in keys:
        if key in record:
            val = _coerce_float(record.get(key))
            if val is not None:
                return val
    return None


def _get_price(session: HTTP, coin: str, timestamp: int) -> float:
    """Return the coin price in USDT near the given timestamp."""
    symbol = f"{coin}USDT"
    try:  # pragma: no cover - network dependent
        resp = session.get_mark_price_kline(
            category="linear",
            symbol=symbol,
            interval="1",
            start=timestamp,
            end=timestamp + 60000,
        )
        kline = resp.get("result", {}).get("list", [])
        if kline:
            return float(kline[0][1])
    except Exception:  # pragma: no cover - best effort  # pylint: disable=broad-exception-caught
        pass
    return 0.0


def _get_balance_before(session: HTTP, timestamp: int) -> float:
    """Return USDT-equivalent balance immediately before the timestamp."""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    look_back_end = timestamp - 1
    months_checked = 0.0
    earliest = int(datetime.now(timezone.utc).timestamp() * 1000) - TWO_YEARS_MS + LIMIT_CUSHION_MS
    balances: Dict[str, Dict[str, float | None]] = {}
    while look_back_end >= earliest and months_checked < 24:
        start = max(earliest, look_back_end - SEVEN_DAYS_MS + 1)
        params = {
            "accountType": "UNIFIED",
            "startTime": start,
            "endTime": look_back_end,
        }
        logs: List[Dict[str, Any]] = []
        for page in _fetch_transaction_pages(session, **params):
            logs.extend(page)
        if logs:
            logs.sort(key=lambda r: int(r.get("transactionTime", 0)))
            for log in logs:
                coin = str(log.get("coin", ""))
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
        months_checked += 7 / 30

    if not balances:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        coins = resp.get("result", {}).get("list", [{}])[0].get("coin", [])
        for coin in coins:
            name = str(coin.get("coin"))
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
        else:
            if amount is not None:
                price = _get_price(session, coin, timestamp)
                total += amount * price
            elif usd_value is not None:
                total += usd_value
    return total


def export_balance_history(months: int = 12) -> str:
    """Export monthly USDT balance history to an XLSX file.

    Returns
    -------
    str
        Name of the saved XLSX file.
    """
    # pylint: disable=too-many-locals,too-many-branches
    mode_env = (os.getenv("BYBIT_ENV", "live") or "live").strip().lower()
    mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(mode_env)
    if not api_key or not api_secret:
        raise EnvironmentError(
            "Bybit API credentials are missing. Provide BYBIT_API_KEY1/BYBIT_API_SECRET1 "
            "(or KEY2 for demo) or legacy BYBIT_API_KEY/BYBIT_API_SECRET."
        )

    if HTTP is None or Workbook is None:  # pragma: no cover - optional deps
        raise ImportError("pybit and openpyxl modules are required")

    if months > 24:
        raise ValueError("Bybit only allows querying up to 24 months of data")

    base_url = _normalize_endpoint(mode, base_url)
    session = _build_bybit_session(api_key, api_secret, base_url)

    end_month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    data: List[Tuple[str, float, float]] = []

    start_first = _add_months(end_month, -months)
    prev_end = _get_balance_before(session, int(start_first.timestamp() * 1000))

    for i in range(months):
        start_dt = _add_months(end_month, -months + i)
        end_dt = _add_months(start_dt, 1) - timedelta(milliseconds=1)

        logs: List[Dict[str, Any]] = []
        for chunk_start, chunk_end in _date_range_chunks(
            int(start_dt.timestamp() * 1000),
            int(end_dt.timestamp() * 1000),
            SEVEN_DAYS_MS,
        ):
            params = {
                "accountType": "UNIFIED",
                "startTime": chunk_start,
                "endTime": chunk_end,
            }
            for page in _fetch_transaction_pages(session, **params):
                logs.extend(page)

        logs.sort(key=lambda r: int(r.get("transactionTime", 0)))
        start_bal = prev_end if prev_end is not None else 0.0
        if logs:
            change_total = 0.0
            for log in logs:
                coin = str(log.get("coin", ""))
                change = float(log.get("change", 0))
                if coin == "USDT":
                    change_total += change
                else:
                    price = _get_price(session, coin, int(log.get("transactionTime", 0)))
                    change_total += change * price
            end_bal = start_bal + change_total
        else:
            if prev_end is None:
                resp = session.get_wallet_balance(accountType="UNIFIED")
                coins = resp.get("result", {}).get("list", [{}])[0].get("coin", [])
                bal = 0.0
                for coin in coins:
                    if coin.get("coin") == "USDT":
                        bal = float(coin.get("cashBalance", coin.get("walletBalance", 0)))
                        break
                start_bal = end_bal = bal
            else:
                end_bal = start_bal
        prev_end = end_bal
        data.append((start_dt.strftime("%Y-%m"), start_bal, end_bal))

    wb = Workbook()
    ws = wb.active
    ws.append(["Month", "Start", "End"])
    for month, s_bal, e_bal in data:
        ws.append([month, s_bal, e_bal])

    filename = "usdt_balance_history.xlsx"
    wb.save(filename)
    print(f"Balance history saved to {filename}")
    return filename


def download_history(
    category: str,
    start_date: str | None = None,
    end_date: str | None = None,
    symbol: str | None = None,
    template: bool | None = True,
    mode_override: str | None = None,
) -> str | None:
    """Download execution history from Bybit and save as CSV.

    Returns
    -------
    str | None
        CSV filename when rows are present; otherwise ``None``.
    """
    # pylint: disable=too-many-locals,too-many-branches
    mode_env = ((mode_override or os.getenv("BYBIT_ENV", "live")) or "live").strip().lower()
    mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(mode_env)
    if not api_key or not api_secret:
        raise EnvironmentError(
            "Bybit API credentials are missing. Provide BYBIT_API_KEY1/BYBIT_API_SECRET1 "
            "(or KEY2 for demo) or legacy BYBIT_API_KEY/BYBIT_API_SECRET."
        )

    base_url = _normalize_endpoint(mode, base_url)
    session = None
    if mode != "demo":
        if HTTP is None:
            raise ImportError("pybit module is required to download history")
        session = _build_bybit_session(api_key, api_secret, base_url)

    params: Dict[str, Any] = {"category": category}
    if symbol:
        params["symbol"] = symbol

    start_ms = _parse_date_start(start_date) if start_date else None
    end_ms = _parse_date_end(end_date) if end_date else None
    # Live/testnet: ~2 years. Demo: 7 days.
    start_ms, end_ms = _limit_time_window(mode, start_ms, end_ms)

    rows: List[Dict[str, Any]] = []

    if start_ms is not None and end_ms is not None:
        for chunk_start, chunk_end in _date_range_chunks(
            start_ms, end_ms, SEVEN_DAYS_MS
        ):
            chunk_params = {
                **params,
                "startTime": chunk_start,
                "endTime": chunk_end,
            }
            if mode == "demo":
                pages = _fetch_pages_demo(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    **chunk_params,
                )
            else:
                pages = _fetch_pages(session, **chunk_params)
            for page in pages:
                for row in page:
                    _convert_exec_time(row, bool(template))
                rows.extend(page)
    else:
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        if mode == "demo":
            pages = _fetch_pages_demo(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                **params,
            )
        else:
            pages = _fetch_pages(session, **params)
        for page in pages:
            for row in page:
                _convert_exec_time(row, bool(template))
            rows.extend(page)

    if template:
        rows = _apply_template(rows)

    if not rows:
        print("No transactions found for the specified date range. No CSV created.")
        return None

    start_epoch = int(start_ms // 1000) if start_ms is not None else 0
    end_epoch = int(end_ms // 1000) if end_ms is not None else 0
    filename = f"Bybit-UM-USDTPerp-TradeHistory-{start_epoch}-{end_epoch}.csv"
    _write_csv(filename, rows)
    print(f"History saved to {filename}")
    return filename


def run_interactive() -> None:
    """Prompt for parameters and download history."""
    category = input("Category [linear]: ") or "linear"
    start = input("Start date YYYY-MM-DD: ")
    end = input("End date YYYY-MM-DD: ")
    sym = input("Symbol (e.g. BTCUSDT, optional): ") or None
    download_history(category, start, end, sym)


if __name__ == "__main__":
    run_interactive()
