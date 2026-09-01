"""Local OANDA FX ATR-percentage scanner.

The tool is deliberately independent of the Bybit scanner and the OANDA spread
monitor.  It uses account-visible OANDA CURRENCY instruments and mid candles.
"""

from __future__ import annotations

import calendar
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, render_template_string

from shared import oanda_api


ATR_LENGTH = 14
CANDLE_COUNT = 201
INSTRUMENT_CACHE_SECONDS = 3600
AUTO_REFRESH_SECONDS = 60
TIMEFRAME_GRANULARITIES: dict[str, str] = {
    "1m": "M1",
    "5m": "M5",
    "1h": "H1",
    "1D": "D",
    "1W": "W",
    "1Mo": "M",
}
TIMEFRAME_SECONDS: dict[str, Optional[int]] = {
    "1m": 60,
    "5m": 300,
    "1h": 3600,
    "1D": 86400,
    "1W": 604800,
    "1Mo": None,
}
TIMEFRAMES = tuple(TIMEFRAME_GRANULARITIES)
MAJOR_FOREX_PAIRS = frozenset(
    {
        "AUD_USD",
        "EUR_USD",
        "GBP_USD",
        "NZD_USD",
        "USD_CAD",
        "USD_CHF",
        "USD_JPY",
    }
)


def _mode_from_env() -> str:
    raw = (os.getenv("OANDA_ENV") or os.getenv("OANDA_MODE") or "live").strip().lower()
    return "demo" if raw in {"demo", "practice", "test"} else "live"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _finite_number(value: object) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def filter_currency_instruments(payload: object) -> list[str]:
    """Return every active account-visible OANDA CURRENCY instrument."""

    raw = payload.get("instruments") if isinstance(payload, Mapping) else payload
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("type") or "").strip().upper() != "CURRENCY":
            continue
        if item.get("tradeable") is False:
            continue
        name = str(item.get("name") or "").strip().upper()
        if name:
            names.add(name)
    return sorted(names)


def _completed_mid_candles(candles: object) -> list[dict[str, object]]:
    if not isinstance(candles, list):
        return []
    parsed: list[dict[str, object]] = []
    for candle in candles:
        if not isinstance(candle, Mapping) or candle.get("complete") is not True:
            continue
        mid = candle.get("mid")
        if not isinstance(mid, Mapping):
            return []
        high = _finite_number(mid.get("h"))
        low = _finite_number(mid.get("l"))
        close = _finite_number(mid.get("c"))
        candle_time = str(candle.get("time") or "").strip()
        if (
            high is None
            or low is None
            or close is None
            or high < low
            or close <= 0
            or not candle_time
        ):
            return []
        parsed.append(
            {"time": candle_time, "high": high, "low": low, "close": close}
        )
    parsed.sort(key=lambda item: str(item["time"]))
    return parsed


def _wilder_atr_result(candles: object, *, length: int = ATR_LENGTH) -> Optional[dict[str, object]]:
    completed = _completed_mid_candles(candles)
    if not isinstance(length, int) or isinstance(length, bool) or length < 2:
        return None
    if len(completed) < length:
        return None

    true_ranges: list[float] = []
    previous_close: Optional[float] = None
    for candle in completed:
        high = float(candle["high"])
        low = float(candle["low"])
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
        if not math.isfinite(true_range) or true_range < 0:
            return None
        true_ranges.append(true_range)
        previous_close = float(candle["close"])

    atr = sum(true_ranges[:length]) / float(length)
    for true_range in true_ranges[length:]:
        atr = ((atr * (length - 1)) + true_range) / float(length)
    close = float(completed[-1]["close"])
    value = atr / close * 100.0
    if not math.isfinite(value) or value < 0:
        return None
    return {
        "value": value,
        "atr": atr,
        "close": close,
        "completed_candle_count": len(completed),
        "completed_candle_time": str(completed[-1]["time"]),
    }


def wilder_atr_percent(candles: object, *, length: int = ATR_LENGTH) -> Optional[float]:
    """Return Wilder ATR / latest completed-candle close * 100."""

    result = _wilder_atr_result(candles, length=length)
    return float(result["value"]) if result is not None else None


def sort_rows(rows: Iterable[Mapping[str, object]], timeframe: str = "1m") -> list[dict[str, object]]:
    """Sort descending by one ATR column, keeping N/A rows by instrument."""

    selected = timeframe if timeframe in TIMEFRAME_GRANULARITIES else "1m"

    def key(row: Mapping[str, object]) -> tuple[int, float, str]:
        values = row.get("atr_pct")
        value = _finite_number(values.get(selected)) if isinstance(values, Mapping) else None
        instrument = str(row.get("instrument") or row.get("symbol") or "").upper()
        return (1, 0.0, instrument) if value is None else (0, -value, instrument)

    return [copy.deepcopy(dict(row)) for row in sorted(rows, key=key)]


def split_currency_rows(
    rows: Iterable[Mapping[str, object]], timeframe: str = "1m"
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split returned currency rows into independently ranked major and other pairs."""

    major_rows: list[Mapping[str, object]] = []
    other_rows: list[Mapping[str, object]] = []
    for row in rows:
        instrument = str(row.get("instrument") or row.get("symbol") or "").upper()
        (major_rows if instrument in MAJOR_FOREX_PAIRS else other_rows).append(row)
    return sort_rows(major_rows, timeframe), sort_rows(other_rows, timeframe)


def _parse_time(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_refresh_epoch(timeframe: str, completed_time: object, fetched_at: float) -> float:
    start = _parse_time(completed_time)
    seconds = TIMEFRAME_SECONDS[timeframe]
    if start is None:
        return fetched_at + float(seconds or 21600)
    if seconds is None:
        next_close = _add_months(start, 2).timestamp() + 2.0
        return max(fetched_at + 21600.0, next_close)
    next_close = start.timestamp() + float(seconds * 2) + 2.0
    return max(fetched_at + min(float(seconds), 300.0), next_close)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OandaVolatilityService:
    """Bounded background refresh with closed-candle-aware result caching."""

    def __init__(
        self,
        *,
        request_func: Optional[Callable[..., dict[str, Any]]] = None,
        mode: Optional[str] = None,
        max_workers: Optional[int] = None,
    ) -> None:
        self._request = request_func or oanda_api._request
        self._mode = mode or _mode_from_env()
        self._max_workers = max_workers or _bounded_int(
            "OANDA_VOLATILITY_CONCURRENCY", 6, 1, 10
        )
        self._request_timeout = _bounded_int(
            "OANDA_VOLATILITY_REQUEST_TIMEOUT_SECONDS", 10, 2, 15
        )
        self._lock = threading.RLock()
        self._instrument_cache: tuple[float, list[str]] = (0.0, [])
        self._atr_cache: dict[tuple[str, str], dict[str, object]] = {}
        self._state: dict[str, object] = {
            "ok": False,
            "state": "idle",
            "updated_at": None,
            "rows": [],
            "timeframes": list(TIMEFRAMES),
            "atr_length": ATR_LENGTH,
            "rank_timeframe": "1m",
            "refresh_error": None,
            "progress": {
                "in_progress": False,
                "completed": 0,
                "total": 0,
                "detail": "Open the tool to load OANDA currency instruments.",
            },
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return copy.deepcopy(self._state)

    def trigger_refresh(self) -> bool:
        with self._lock:
            progress = self._state.get("progress")
            if isinstance(progress, Mapping) and progress.get("in_progress"):
                return False
            self._state["state"] = "loading"
            self._state["refresh_error"] = None
            self._state["progress"] = {
                "in_progress": True,
                "completed": 0,
                "total": 0,
                "detail": "Discovering OANDA currency instruments.",
            }
        threading.Thread(
            target=self._refresh_worker,
            name="oanda-volatility-refresh",
            daemon=True,
        ).start()
        return True

    def _discover_instruments(self) -> list[str]:
        now = time.time()
        cached_at, cached = self._instrument_cache
        if cached and now - cached_at < INSTRUMENT_CACHE_SECONDS:
            return list(cached)
        account_id = oanda_api._account_id(self._mode)
        payload = self._request(
            "GET",
            f"/accounts/{account_id}/instruments",
            mode=self._mode,
            account_id=account_id,
            timeout=self._request_timeout,
        )
        instruments = filter_currency_instruments(payload)
        if not instruments:
            raise RuntimeError("OANDA returned no active CURRENCY instruments for this account.")
        self._instrument_cache = (now, instruments)
        return list(instruments)

    def _fetch_timeframe(self, instrument: str, timeframe: str) -> dict[str, object]:
        payload = self._request(
            "GET",
            f"/instruments/{instrument}/candles",
            mode=self._mode,
            params={
                "granularity": TIMEFRAME_GRANULARITIES[timeframe],
                "price": "M",
                "count": CANDLE_COUNT,
            },
            timeout=self._request_timeout,
        )
        candles = payload.get("candles") if isinstance(payload, Mapping) else None
        result = _wilder_atr_result(candles, length=ATR_LENGTH)
        if result is None:
            raise ValueError(
                f"No valid {ATR_LENGTH}-period completed-candle ATR was returned."
            )
        fetched_at = time.time()
        result.update(
            {
                "status": "fresh",
                "error": None,
                "fetched_at": fetched_at,
                "next_refresh_at": _next_refresh_epoch(
                    timeframe, result.get("completed_candle_time"), fetched_at
                ),
            }
        )
        return result

    def _row_from_results(
        self, instrument: str, results: Mapping[str, Mapping[str, object]]
    ) -> dict[str, object]:
        values: dict[str, Optional[float]] = {}
        statuses: dict[str, str] = {}
        diagnostics: dict[str, str] = {}
        candle_times: dict[str, Optional[str]] = {}
        for timeframe in TIMEFRAMES:
            result = results.get(timeframe, {})
            values[timeframe] = _finite_number(result.get("value"))
            statuses[timeframe] = str(result.get("status") or "loading")
            diagnostics[timeframe] = str(result.get("error") or "")
            candle_times[timeframe] = (
                str(result.get("completed_candle_time"))
                if result.get("completed_candle_time")
                else None
            )
        return {
            "instrument": instrument,
            "symbol": instrument,
            "atr_pct": values,
            "atr_status": statuses,
            "diagnostics": diagnostics,
            "completed_candle_time": candle_times,
        }

    def _refresh_worker(self) -> None:
        try:
            instruments = self._discover_instruments()
            now = time.time()
            results: dict[str, dict[str, dict[str, object]]] = {
                instrument: {} for instrument in instruments
            }
            pending: list[tuple[str, str]] = []
            for instrument in instruments:
                for timeframe in TIMEFRAMES:
                    cached = self._atr_cache.get((instrument, timeframe))
                    if cached and now < float(cached.get("next_refresh_at") or 0):
                        results[instrument][timeframe] = copy.deepcopy(cached)
                    else:
                        pending.append((instrument, timeframe))
                        results[instrument][timeframe] = {
                            "value": None,
                            "status": "loading",
                            "error": None,
                        }

            total = len(instruments) * len(TIMEFRAMES)
            cached_count = total - len(pending)
            with self._lock:
                self._state["rows"] = sort_rows(
                    (self._row_from_results(name, results[name]) for name in instruments),
                    "1m",
                )
                self._state["progress"] = {
                    "in_progress": True,
                    "completed": cached_count,
                    "total": total,
                    "detail": "Loading completed OANDA candles.",
                }

            completed = cached_count
            if pending:
                with ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="oanda-volatility",
                ) as pool:
                    future_keys = {
                        pool.submit(self._fetch_timeframe, instrument, timeframe): (
                            instrument,
                            timeframe,
                        )
                        for instrument, timeframe in pending
                    }
                    for future in as_completed(future_keys):
                        instrument, timeframe = future_keys[future]
                        try:
                            result = future.result()
                            self._atr_cache[(instrument, timeframe)] = copy.deepcopy(result)
                        except Exception as exc:
                            prior = self._atr_cache.get((instrument, timeframe))
                            if prior and _finite_number(prior.get("value")) is not None:
                                result = copy.deepcopy(prior)
                                result.update({"status": "stale", "error": str(exc)})
                            else:
                                result = {
                                    "value": None,
                                    "status": "error",
                                    "error": str(exc),
                                    "completed_candle_time": None,
                                }
                        results[instrument][timeframe] = result
                        completed += 1
                        with self._lock:
                            self._state["rows"] = sort_rows(
                                (
                                    self._row_from_results(name, results[name])
                                    for name in instruments
                                ),
                                "1m",
                            )
                            self._state["progress"] = {
                                "in_progress": True,
                                "completed": completed,
                                "total": total,
                                "detail": f"Loading {instrument} {timeframe}.",
                            }

            rows = sort_rows(
                (self._row_from_results(name, results[name]) for name in instruments),
                "1m",
            )
            has_errors = any(
                status in {"error", "stale"}
                for row in rows
                for status in row["atr_status"].values()
            )
            with self._lock:
                self._state.update(
                    {
                        "ok": True,
                        "state": "partial" if has_errors else "ready",
                        "updated_at": _utc_now_iso(),
                        "rows": rows,
                        "instrument_count": len(instruments),
                        "refresh_error": None,
                        "progress": {
                            "in_progress": False,
                            "completed": total,
                            "total": total,
                            "detail": "Refresh complete.",
                        },
                    }
                )
        except Exception as exc:
            with self._lock:
                existing_rows = self._state.get("rows")
                self._state.update(
                    {
                        "ok": bool(existing_rows),
                        "state": "stale" if existing_rows else "error",
                        "refresh_error": {"message": str(exc)},
                        "progress": {
                            "in_progress": False,
                            "completed": 0,
                            "total": 0,
                            "detail": "Refresh failed.",
                        },
                    }
                )


APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").rstrip("/")
app = Flask(__name__)
SERVICE = OandaVolatilityService()


PAGE_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Oanda Volatility</title>
  <style>
    :root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#07101d;color:#e2e8f0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
    header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:18px 22px;border-bottom:1px solid #243044;background:#111827;flex-wrap:wrap}
    h1{font-size:20px;margin:0}.sub{color:#94a3b8;font-size:13px;margin-top:4px}.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    select,button{border:1px solid #3b82f6;border-radius:6px;background:#1e293b;color:#eff6ff;padding:7px 11px;font-weight:700}button{background:#2563eb;cursor:pointer}button:disabled{opacity:.6;cursor:wait}
    main{padding:18px 22px}.status{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px;color:#cbd5e1;font-size:13px;flex-wrap:wrap}.error{color:#fca5a5}.stale{color:#fde68a}
    .progress{height:7px;background:#1e293b;border-radius:999px;overflow:hidden;margin-bottom:12px}.progress>div{height:100%;width:0;background:#3b82f6;transition:width .2s}
    .pair-section+.pair-section{margin-top:18px}.pair-section h2{font-size:14px;margin:0 0 7px;color:#cbd5e1}.table-wrap{max-height:calc(100vh - 190px);overflow:auto;border:1px solid #243044;border-radius:8px;background:#0f172a}table{width:100%;min-width:820px;border-collapse:collapse}th,td{padding:10px 9px;border-right:1px solid #243044;border-bottom:1px solid #243044;text-align:right;font-size:12px}th{position:sticky;top:0;z-index:1;background:#111827;color:#cbd5e1}th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:#111827;font-weight:900}td.na{color:#94a3b8}tr:hover td{background:#172033}tr:hover td:first-child{background:#172033}
  </style>
</head>
<body>
  <header><div><h1>Oanda Volatility</h1><div class="sub">OANDA FX market proxy · Wilder ATR(14) · completed candles only</div></div>
    <div class="actions"><label>Rank by <select id="rank">{% for tf in timeframes %}<option value="{{ tf }}">{{ tf }}</option>{% endfor %}</select></label><button id="refresh">Refresh</button></div>
  </header>
  <main><div class="status"><span id="status">Loading OANDA currency instruments.</span><span id="updated">Not updated</span></div><div class="progress"><div id="bar"></div></div>
    <div id="sections"><div class="pair-section"><h2>Major Forex Pairs</h2><div class="table-wrap"><table><thead><tr><th>Instrument</th>{% for tf in timeframes %}<th>ATR % {{ tf }}</th>{% endfor %}<th>Data state</th></tr></thead><tbody><tr><td colspan="8">Loading…</td></tr></tbody></table></div></div><div class="pair-section"><h2>Other Forex Pairs</h2><div class="table-wrap"><table><thead><tr><th>Instrument</th>{% for tf in timeframes %}<th>ATR % {{ tf }}</th>{% endfor %}<th>Data state</th></tr></thead><tbody><tr><td colspan="8">Loading…</td></tr></tbody></table></div></div></div>
  </main>
  <script>
  (()=>{const base={{ base_path|tojson }};const tfs={{ timeframes|tojson }};const majorPairs=new Set({{ major_pairs|tojson }});const sections=document.getElementById('sections');const status=document.getElementById('status');const updated=document.getElementById('updated');const bar=document.getElementById('bar');const rank=document.getElementById('rank');const refresh=document.getElementById('refresh');let snapshot=null,timer=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>{if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null};const fmt=v=>{const n=finite(v);return n===null?'N/A':`${n.toFixed(5)}%`};
  const sorted=rows=>[...(Array.isArray(rows)?rows:[])].sort((a,b)=>{const av=finite(a?.atr_pct?.[rank.value]),bv=finite(b?.atr_pct?.[rank.value]);if(av===null&&bv!==null)return 1;if(av!==null&&bv===null)return -1;if(av!==null&&bv!==null&&av!==bv)return bv-av;return String(a.instrument).localeCompare(String(b.instrument));});
  const rowsHtml=rows=>rows.map(row=>{const cells=tfs.map(tf=>{const value=finite(row?.atr_pct?.[tf]);const diagnostic=String(row?.diagnostics?.[tf]||'');return `<td class="${value===null?'na':''}" title="${esc(diagnostic)}">${esc(fmt(value))}</td>`}).join('');const states=Object.values(row.atr_status||{});const state=states.includes('error')?'Partial / error':(states.includes('stale')?'Partial / stale':(states.includes('loading')?'Loading':'Fresh'));return `<tr><td>${esc(row.instrument)}</td>${cells}<td>${esc(state)}</td></tr>`}).join('')||'<tr><td colspan="8">No OANDA currency instruments available.</td></tr>';
  const sectionHtml=(title,rows)=>`<section class="pair-section"><h2>${title}</h2><div class="table-wrap"><table><thead><tr><th>Instrument</th>${tfs.map(tf=>`<th>ATR % ${esc(tf)}</th>`).join('')}<th>Data state</th></tr></thead><tbody>${rowsHtml(rows)}</tbody></table></div></section>`;
  const render=payload=>{snapshot=payload||{};const p=snapshot.progress||{};const total=Number(p.total||0),done=Number(p.completed||0);bar.style.width=`${total?Math.min(100,done/total*100):(p.in_progress?8:0)}%`;refresh.disabled=Boolean(p.in_progress);status.className=snapshot.state==='error'?'error':(snapshot.state==='partial'||snapshot.state==='stale'?'stale':'');status.textContent=snapshot.refresh_error?.message||`${p.detail||snapshot.state||'Idle'}${total?` (${done}/${total})`:''}`;updated.textContent=snapshot.updated_at?`Updated ${new Date(snapshot.updated_at).toLocaleString()} · ${snapshot.instrument_count||0} FX pairs`:'Not updated';const rows=sorted(snapshot.rows);const majors=rows.filter(row=>majorPairs.has(String(row?.instrument||'').toUpperCase()));const others=rows.filter(row=>!majorPairs.has(String(row?.instrument||'').toUpperCase()));sections.innerHTML=sectionHtml('Major Forex Pairs',majors)+sectionHtml('Other Forex Pairs',others);if(p.in_progress){clearTimeout(timer);timer=setTimeout(poll,1000)}else{clearTimeout(timer);timer=setTimeout(poll,30000)}};
  const poll=async()=>{try{const r=await fetch(`${base}/api/status`,{cache:'no-store'});render(await r.json())}catch(e){status.className='error';status.textContent=e.message||'Status request failed.'}};
  refresh.onclick=async()=>{refresh.disabled=true;await fetch(`${base}/api/refresh`,{method:'POST'});poll()};rank.onchange=()=>{if(snapshot)render(snapshot)};poll();setInterval(()=>fetch(`${base}/api/refresh`,{method:'POST'}).catch(()=>{}),{{ auto_refresh_ms }});
  })();
  </script>
</body></html>
"""


@app.get("/")
def index() -> str:
    SERVICE.trigger_refresh()
    return render_template_string(
        PAGE_TEMPLATE,
        base_path=APP_BASE_PATH,
        timeframes=list(TIMEFRAMES),
        major_pairs=sorted(MAJOR_FOREX_PAIRS),
        auto_refresh_ms=AUTO_REFRESH_SECONDS * 1000,
    )


@app.get("/api/status")
def status() -> object:
    snapshot = SERVICE.snapshot()
    if snapshot.get("state") == "idle":
        SERVICE.trigger_refresh()
        snapshot = SERVICE.snapshot()
    return jsonify(snapshot)


@app.post("/api/refresh")
def refresh() -> object:
    started = SERVICE.trigger_refresh()
    return jsonify({"ok": True, "started": started, "shared_in_flight": not started})


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = _bounded_int("PORT", 5058, 1, 65535)
    print(f"Oanda Volatility listening on {host}:{port}", flush=True)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
