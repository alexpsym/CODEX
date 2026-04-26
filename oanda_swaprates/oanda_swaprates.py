"""
OANDA swap/financing rate fetcher (Render Master Control standalone script).

Fetches instrument financing (swap) rates via:
  GET /v3/accounts/{accountID}/instruments

Reads each instrument's `financing` object:
  - longRate
  - shortRate
  - financingDaysOfWeek

Env vars (matches existing repo conventions):
  - OANDA_API_KEY (or OANDA_ACCESS_TOKEN)
  - OANDA_ACCOUNT_ID
  - OANDA_ENV = practice|live (optional; default: live)
  - OANDA_BASE_URL (optional override)

Optional selection:
  - OANDA_SWAP_INSTRUMENTS: CSV of instruments (e.g. "EUR_USD,USD_JPY")
    Fallbacks: OANDA_INSTRUMENTS, then render/data/watchlist.json (if present)

Runtime:
  - OANDA_SWAP_POLL_SECONDS: if >0, run continuously (default 0 = run once)
  - OANDA_SWAP_TOP_N: if set, print only top N by abs(long-short)

Notes:
  - OANDA rates are decimals (0.05 = 5%). This script prints both decimal and %.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from tabulate import tabulate
from urllib3.util.retry import Retry

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.env_bootstrap import load_master_env

load_master_env(base_dir=ROOT_DIR)


API_PATH_INSTRUMENTS = "/v3/accounts/{accountID}/instruments"


def _normalize_symbol(raw: object) -> str:
    s = str(raw or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("/", "_").replace("-", "_")
    if "_" not in s and re.fullmatch(r"[A-Z]{6}", s):
        s = f"{s[:3]}_{s[3:]}"
    return s


def _token() -> str:
    return (os.getenv("OANDA_API_KEY") or os.getenv("OANDA_ACCESS_TOKEN") or "").strip()


def _account_id() -> str:
    return (os.getenv("OANDA_ACCOUNT_ID") or "").strip()


def _base_url() -> str:
    env = (os.getenv("OANDA_ENV") or "live").strip().lower()
    override = (os.getenv("OANDA_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if env in ("practice", "fxpractice", "demo"):
        return "https://api-fxpractice.oanda.com"
    return "https://api-fxtrade.oanda.com"


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def _read_watchlist() -> List[str]:
    # Reuse Render Master Control watchlist file if present.
    base_dir = Path(__file__).resolve().parents[1]
    watchlist_path = base_dir / "render" / "data" / "watchlist.json"
    try:
        payload = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out: List[str] = []
    seen = set()
    for item in payload:
        sym = _normalize_symbol(item)
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _resolve_instruments() -> List[str]:
    raw = (os.getenv("OANDA_SWAP_INSTRUMENTS") or os.getenv("OANDA_INSTRUMENTS") or "").strip()

    def clean_token(t: str) -> str:
        # remove common junk from env formatting
        t = t.strip().strip('"').strip("'")
        t = t.strip("[](){}")
        return _normalize_symbol(t)

    fx_re = re.compile(r"^[A-Z]{3}_[A-Z]{3}$")  # FX only

    # 1) env var list (supports JSON array or CSV)
    tokens: List[str] = []
    if raw:
        if raw.lstrip().startswith("["):
            try:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    tokens = [str(x) for x in arr]
            except Exception:
                tokens = []
        if not tokens:
            tokens = [t for t in re.split(r"[,\s]+", raw) if t]

        out: List[str] = []
        seen = set()
        for t in tokens:
            sym = clean_token(t)
            if not sym or sym in seen:
                continue
            if not fx_re.match(sym):
                continue  # skip non-FX so we don't 400 the whole request
            seen.add(sym)
            out.append(sym)
        return out

    # 2) fallback: watchlist (filter FX only)
    out: List[str] = []
    seen = set()
    for item in _read_watchlist():
        sym = clean_token(item)
        if not sym or sym in seen:
            continue
        if not fx_re.match(sym):
            continue
        seen.add(sym)
        out.append(sym)
    return out


@dataclass(frozen=True)
class SwapRate:
    instrument: str
    long_rate: Optional[float]
    short_rate: Optional[float]
    days_of_week: Tuple[str, ...]

    @property
    def spread(self) -> Optional[float]:
        if self.long_rate is None or self.short_rate is None:
            return None
        return self.long_rate - self.short_rate


def _parse_financing_days(financing_days: object) -> Tuple[str, ...]:
    if not isinstance(financing_days, list):
        return tuple()
    days: List[str] = []
    for item in financing_days:
        if not isinstance(item, dict):
            continue
        day = str(item.get("dayOfWeek") or "").strip().upper()
        if day:
            days.append(day)
    return tuple(days)


def fetch_swap_rates(
    *,
    sess: requests.Session,
    base_url: str,
    token: str,
    account_id: str,
    instruments: Optional[List[str]] = None,
    timeout: float = 20.0,
) -> List[SwapRate]:
    params: Dict[str, str] = {}
    if instruments:
        params["instruments"] = ",".join(instruments)

    url = base_url + API_PATH_INSTRUMENTS.format(accountID=account_id)
    resp = sess.get(url, headers=_headers(token), params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    items = data.get("instruments")
    if not isinstance(items, list):
        raise RuntimeError("Unexpected response: missing instruments list")

    out: List[SwapRate] = []
    for inst in items:
        if not isinstance(inst, dict):
            continue
        name = _normalize_symbol(inst.get("name"))
        financing = inst.get("financing")
        long_rate: Optional[float] = None
        short_rate: Optional[float] = None
        days: Tuple[str, ...] = tuple()
        if isinstance(financing, dict):
            try:
                if financing.get("longRate") is not None:
                    long_rate = float(financing.get("longRate"))
            except Exception:
                long_rate = None
            try:
                if financing.get("shortRate") is not None:
                    short_rate = float(financing.get("shortRate"))
            except Exception:
                short_rate = None
            days = _parse_financing_days(financing.get("financingDaysOfWeek"))

        if name:
            out.append(SwapRate(instrument=name, long_rate=long_rate, short_rate=short_rate, days_of_week=days))
    return out


def _print_table(rates: List[SwapRate], *, top_n: Optional[int] = None) -> None:
    def pct(x: Optional[float]) -> str:
        if x is None:
            return "-"
        return f"{x:.8f} ({x*100:.3f}%)"

    rows = []
    for r in rates:
        spread = r.spread
        rows.append([
            r.instrument,
            pct(r.long_rate),
            pct(r.short_rate),
            "-" if spread is None else f"{spread:.8f} ({spread*100:.3f}%)",
            ",".join(r.days_of_week) if r.days_of_week else "-",
        ])

    # sort by abs(spread) desc (missing spread last)
    def key(row):
        s = row[3]
        if s == "-":
            return -1.0
        try:
            return abs(float(s.split()[0]))
        except Exception:
            return -1.0

    rows.sort(key=key, reverse=True)
    if top_n is not None and top_n > 0:
        rows = rows[:top_n]

    headers = ["Instrument", "Long", "Short", "Long-Short", "Days"]
    print(tabulate(rows, headers=headers, tablefmt="grid", stralign="right", disable_numparse=True))


def main() -> int:
    token = _token()
    account_id = _account_id()
    if not token:
        print("Missing OANDA token: set OANDA_API_KEY or OANDA_ACCESS_TOKEN")
        return 2
    if not account_id:
        print("Missing OANDA account id: set OANDA_ACCOUNT_ID")
        return 2

    poll_seconds = int(float(os.getenv("OANDA_SWAP_POLL_SECONDS", "0") or 0))
    top_n_raw = (os.getenv("OANDA_SWAP_TOP_N") or "").strip()
    top_n = int(top_n_raw) if top_n_raw else None

    instruments = _resolve_instruments()
    base_url = _base_url()
    sess = _session()

    def run_once() -> None:
        rates = fetch_swap_rates(
            sess=sess,
            base_url=base_url,
            token=token,
            account_id=account_id,
            instruments=instruments or None,
        )
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        scope = "ALL" if not instruments else ",".join(instruments)
        print(f"[{now}] OANDA swap/financing rates ({scope})")
        _print_table(rates, top_n=top_n)

    if poll_seconds <= 0:
        try:
            run_once()
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1

    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"ERROR: {exc}")
        time.sleep(max(1, poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
