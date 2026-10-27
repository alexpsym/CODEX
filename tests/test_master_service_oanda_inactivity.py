import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
master_service = None


@pytest.fixture(autouse=True)
def _reset_inactivity_cache(monkeypatch, tmp_path, request):
    global master_service
    import os
    import dotenv
    import shared.env_bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "load_master_env", lambda **kw: {})
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    for key in list(os.environ):
        if any(word in key for word in ("OANDA", "BYBIT", "DROPBOX", "JOURNAL", "MASTER_ENV", "COINSPOT")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCAL_STATE_ONLY", "1")
    monkeypatch.setenv("DROPBOX_SYNC_ENABLED", "0")
    app_path = tmp_path / "render/master_service.py"
    app_path.parent.mkdir()
    (app_path.parent / "static").mkdir()
    app_path.write_text((ROOT / "render/master_service.py").read_text(encoding="utf-8"), encoding="utf-8")
    if request.node.name in {
        "test_oanda_inactivity_assessment_calendar_and_threshold",
        "test_oanda_inactivity_zero_balance_suppresses_and_positive_balance_rearms",
        "test_oanda_inactivity_open_positions_and_unknown_data",
        "test_oanda_inactivity_cache_scope_and_refresh",
        "test_oanda_inactivity_widget_countdown_and_balance_states",
    }:
        # Execute only canonical OANDA functions: no unrelated app initialization.
        import ast
        import types
        import calendar
        import time
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal
        from urllib.parse import quote, urlparse
        from zoneinfo import ZoneInfo
        from starlette.responses import JSONResponse
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        names = {
            "OandaUpstreamHTTPError", "_utc_now_iso", "_parse_oanda_timestamp", "_format_oanda_timestamp",
            "_third_last_weekday", "_fee_charge_date_on_or_after", "_fetch_oanda_last_live_fill_time",
            "_build_oanda_inactivity_status", "oanda_inactivity_status",
        }
        nodes = [node for node in tree.body if getattr(node, "name", None) in names]
        assert {node.name for node in nodes} == names
        for node in nodes:
            node.decorator_list = []
        master_service = types.ModuleType("oanda_job8_isolated")
        master_service.__dict__.update(
            datetime=datetime, timedelta=timedelta, timezone=timezone, Decimal=Decimal,
            calendar=calendar, time=types.SimpleNamespace(time=time.time), ZoneInfo=ZoneInfo,
            quote=quote, urlparse=urlparse, JSONResponse=JSONResponse,
            _OANDA_INACTIVITY_CACHE={}, _OANDA_INACTIVITY_CACHE_TTL_SECONDS=45.0,
            _OANDA_INACTIVITY_ERROR_CACHE_TTL_SECONDS=10.0,
            _fetch_oanda_json=None, _get_oanda_config=None, _fetch_oanda_account_created_time=None,
        )
        code = "from __future__ import annotations\n" + ast.unparse(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(code, str(app_path), "exec"), master_service.__dict__)
    else:
        spec = importlib.util.spec_from_file_location("oanda_job8_" + tmp_path.name, app_path)
        master_service = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, master_service)
        spec.loader.exec_module(master_service)
    async def forbidden(*a, **kw):
        raise AssertionError("Unmocked account transport prohibited")
    monkeypatch.setattr(master_service, "_fetch_oanda_json", forbidden)
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda mode: {
        "mode": "live", "account_id": "fixture-live-001", "base_url": "https://example.test", "token": "fixture-token",
    })
    master_service._OANDA_INACTIVITY_CACHE.clear()
    global _REAL_FILL_SCANNER
    _REAL_FILL_SCANNER = master_service._fetch_oanda_last_live_fill_time


class _FailingResponse:
    def __init__(self, status_code: int, body: str, url: str):
        self.status_code = status_code
        self.text = body
        self.content = body.encode("utf-8")
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("boom", request=self.request, response=self)


class _Always520Client:
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        _Always520Client.call_count += 1
        return _FailingResponse(
            520,
            "<html><head><title>Internal Server Error</title></head><body>oops</body></html>",
            url,
        )


def test_fetch_oanda_json_retries_520_and_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _Always520Client.call_count = 0
    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Always520Client)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(master_service.asyncio, "sleep", _no_sleep)

    with pytest.raises(master_service.OandaUpstreamHTTPError) as exc_info:
        asyncio.run(
            master_service._fetch_oanda_json(
                base_url="https://example.test",
                account_id="001-011-ABC",
                api_key="token",
                endpoint="/accounts/{account_id}/summary",
                mode="live",
            )
        )

    assert _Always520Client.call_count == 3
    err = exc_info.value
    assert err.status_code == 520
    assert err.transient is True
    assert "Internal Server Error (HTML response" in err.body_summary
    assert "<html" not in err.body_summary.lower()


def test_oanda_inactivity_status_returns_503_for_transient_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_upstream() -> dict:
        raise master_service.OandaUpstreamHTTPError(
            status_code=520,
            mode="live",
            account_id="001",
            endpoint="/accounts/{account_id}/summary",
            body_summary="Internal Server Error (HTML response, 123 bytes)",
            transient=True,
        )

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _raise_upstream)

    response = asyncio.run(master_service.oanda_inactivity_status())
    assert response.status_code == 503
    payload = response.body.decode("utf-8")
    assert '"status":"unavailable"' in payload
    assert '"upstream_status":520' in payload
    assert '"transient":true' in payload


def test_oanda_inactivity_status_cached_error_preserves_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    async def _raise_upstream() -> dict:
        state["calls"] += 1
        raise master_service.OandaUpstreamHTTPError(
            status_code=520,
            mode="live",
            account_id="001",
            endpoint="/accounts/{account_id}/summary",
            body_summary="Internal Server Error (HTML response, 123 bytes)",
            transient=True,
        )

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _raise_upstream)

    first = asyncio.run(master_service.oanda_inactivity_status())
    second = asyncio.run(master_service.oanda_inactivity_status())

    assert first.status_code == 503
    assert second.status_code == 503
    assert state["calls"] == 1


def test_oanda_inactivity_status_generic_error_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_generic() -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _raise_generic)

    response = asyncio.run(master_service.oanda_inactivity_status())
    assert response.status_code == 500
    payload = response.body.decode("utf-8")
    assert '"status":"unavailable"' in payload
    assert '"error":"boom"' in payload


def test_oanda_inactivity_status_success_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok_payload() -> dict:
        return {
            "ok": True,
            "status": "countdown",
            "mode": "live",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _ok_payload)

    response = asyncio.run(master_service.oanda_inactivity_status())
    assert response.status_code == 200
    assert '"ok":true' in response.body.decode("utf-8")


# Job 8: exactly five nonparametrized targeted acceptance nodes.
import calendar
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess
from zoneinfo import ZoneInfo


def _fixture_account(monkeypatch, *, now="2026-11-05T02:00:00+00:00", fill="2025-11-05T02:00:00+00:00"):
    state = {
        "now": datetime.fromisoformat(now), "fill": datetime.fromisoformat(fill) if fill else None,
        "summary": {"id": "fixture-live-001", "balance": "25.50", "currency": "USD",
                    "openTradeCount": 0, "openPositionCount": 0, "pendingOrderCount": 3,
                    "NAV": "9000", "marginAvailable": "8000"},
        "calls": [], "history_calls": 0, "error": None,
    }
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz) if tz else state["now"].replace(tzinfo=None)
    monkeypatch.setattr(master_service, "datetime", Clock)
    async def transport(**kw):
        state["calls"].append(kw)
        assert kw["mode"] == "live" and kw["base_url"] == "https://example.test"
        if state["error"]:
            raise state["error"]
        return {"account": dict(state["summary"])}
    async def history(cfg):
        state["history_calls"] += 1
        return state["fill"]
    monkeypatch.setattr(master_service, "_fetch_oanda_json", transport)
    monkeypatch.setattr(master_service, "_fetch_oanda_last_live_fill_time", history)
    return state


def _run_mocked_coroutine(coroutine):
    """Run an actual, fully mocked coroutine without creating an event loop.

    Await chains whose dependencies complete inline terminate on the first send.
    Results and exceptions propagate normally. Any suspension fails immediately;
    this runner cannot silently schedule real I/O or leave a coroutine pending.
    """
    try:
        try:
            suspended = coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise AssertionError(f"Unexpected suspension in isolated OANDA fixture: {suspended!r}")
    finally:
        coroutine.close()


def _build():
    return _run_mocked_coroutine(master_service._build_oanda_inactivity_status())


def test_oanda_inactivity_assessment_calendar_and_threshold(monkeypatch):
    state = _fixture_account(monkeypatch)
    result = _build()
    assert result["inactivity_threshold_at"].startswith("2026-11-05")
    assert result["first_eligible_assessment_date"] == "2026-11-26"
    assert result["next_assessment_date"] == "2026-11-26" and result["days_until_assessment"] == 21
    state["now"] = datetime.fromisoformat("2026-11-26T13:30:00+00:00")
    assert _build()["days_until_assessment"] == 0  # 23:30 Brisbane: still due today
    state["now"] += timedelta(hours=1)
    rolled = _build()
    assert rolled["next_assessment_date"] == "2026-12-29"
    assert rolled["first_eligible_assessment_date"] == "2026-11-26"
    state["fill"] = datetime.fromisoformat("2025-11-27T12:00:00+00:00")
    assert _build()["first_eligible_assessment_date"] == "2026-12-29"
    state["fill"] = datetime.fromisoformat("2023-03-01T01:00:00+00:00")
    assert _build()["inactivity_threshold_at"].startswith("2024-02-29")  # exactly 365 days, not 12 calendar months
    zone = ZoneInfo("Australia/Brisbane")
    for year, month in ((2024, 2), (2026, 2), (2026, 11), (2026, 12), (2027, 1), (2028, 2), (2026, 8)):
        actual = master_service._third_last_weekday(year, month).astimezone(zone).date()
        weekdays = [datetime(year, month, day).date() for day in range(1, calendar.monthrange(year, month)[1] + 1)
                    if datetime(year, month, day).weekday() < 5]
        assert actual == weekdays[-3]
    rollover = master_service._fee_charge_date_on_or_after(datetime(2026, 12, 30, tzinfo=zone))
    assert rollover.astimezone(zone).date().isoformat() == "2027-01-27"


def test_oanda_inactivity_zero_balance_suppresses_and_positive_balance_rearms(monkeypatch):
    state = _fixture_account(monkeypatch)
    positive = _build()
    assert positive["status"] == "fee_eligible" and positive["balance"] == "25.50"
    assert positive["balance_currency"] == "USD" and positive["monthly_fee_aud"] == 20
    state["summary"]["balance"] = "0.0000"
    zero = _build()
    assert zero["status"] == "zero_balance" and zero["balance_verified"]
    state["summary"]["balance"] = "0.000000000000000000001"
    rearmed = _build()
    assert rearmed["status"] == "fee_eligible" and rearmed["balance_state"] == "positive"
    for key in ("last_live_fill_at", "inactivity_threshold_at", "first_eligible_assessment_date"):
        assert positive[key] == zero[key] == rearmed[key]
    for invalid in (None, "", "broken", "NaN", "Infinity", "-Infinity", True):
        state["summary"]["balance"] = invalid
        result = _build()
        assert result["balance_state"] == "unknown" and result["balance"] is None and not result["ok"]
    state["summary"].pop("balance")
    assert _build()["status"] == "unavailable"


def test_oanda_inactivity_open_positions_and_unknown_data(monkeypatch):
    state = _fixture_account(monkeypatch)
    assert _build()["has_open_positions"] is False  # three pending orders do not exempt
    state["summary"]["openTradeCount"] = 1
    assert _build()["status"] == "paused_open_position"
    state["summary"]["openTradeCount"] = 0
    state["summary"]["openPositionCount"] = 1
    assert _build()["status"] == "paused_open_position"
    state["summary"]["openPositionCount"] = 0
    state["summary"].pop("openTradeCount")
    assert _build()["status"] == "unavailable"
    state["summary"]["openTradeCount"] = 0
    state["fill"] = None
    assert _build()["status"] == "unavailable"
    state["summary"]["balance"] = "0"
    assert _build()["status"] == "zero_balance"
    async def failed_history(cfg):
        raise RuntimeError("Fixture history unavailable")
    monkeypatch.setattr(master_service, "_fetch_oanda_last_live_fill_time", failed_history)
    assert _build()["status"] == "zero_balance"
    assert "history unavailable" in _build()["error"]
    # Exercise the real fill scanner: cash movements, financing and fee postings never anchor activity.
    state = _fixture_account(monkeypatch)
    monkeypatch.setattr(master_service, "_fetch_oanda_last_live_fill_time", _REAL_FILL_SCANNER)
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    async def creation(**kw): return created
    monkeypatch.setattr(master_service, "_fetch_oanda_account_created_time", creation)
    fills = [{"id": "1", "type": "ORDER_FILL", "time": "2025-11-05T02:00:00Z",
              "units": "1", "tradeOpened": {"tradeID": "1", "units": "1"}}]
    nontrades = [{"type": kind, "time": "2026-11-01T02:00:00Z"} for kind in
                 ("TRANSFER_FUNDS", "DAILY_FINANCING", "DELAYED_TRADE_CLOSURE", "INACTIVITY_FEE")]
    async def history_transport(**kw): return {"transactions": fills + nontrades}
    monkeypatch.setattr(master_service, "_fetch_oanda_json", history_transport)
    cfg = master_service._get_oanda_config("live")
    assert _run_mocked_coroutine(_REAL_FILL_SCANNER(cfg)) == datetime(2025, 11, 5, 2, tzinfo=timezone.utc)
    fills.clear()
    assert _run_mocked_coroutine(_REAL_FILL_SCANNER(cfg)) is None  # creation/deposit is not invented as a fallback


def test_oanda_inactivity_cache_scope_and_refresh(monkeypatch):
    state = _fixture_account(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(master_service.time, "time", lambda: clock[0])
    def request(): return _run_mocked_coroutine(master_service.oanda_inactivity_status())
    def body(): return json.loads(request().body)
    assert body()["balance_state"] == "positive"
    state["summary"]["balance"] = "0"
    assert body()["balance_state"] == "positive" and len(state["calls"]) == 1
    clock[0] += 46
    assert body()["status"] == "zero_balance"
    state["summary"]["balance"] = "10"
    clock[0] += 46
    assert body()["status"] == "fee_eligible"
    cfg = {"mode": "live", "account_id": "fixture-live-002", "base_url": "https://example.test", "token": "fixture-token"}
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda mode: dict(cfg))
    state["summary"].update(id=cfg["account_id"], balance="0")
    assert body()["account_id"] == cfg["account_id"] and body()["status"] == "zero_balance"
    # A current HTTP failure replaces the safe snapshot and retains HTTP/diagnostic metadata.
    state["error"] = master_service.OandaUpstreamHTTPError(status_code=520, mode="live", account_id=cfg["account_id"],
        endpoint="/accounts/{account_id}/summary", body_summary="Internal Server Error (HTML response, 123 bytes)", transient=True)
    clock[0] += 46
    first = request(); count = len(state["calls"]); second = request()
    assert first.status_code == second.status_code == 503 and len(state["calls"]) == count
    error = json.loads(second.body)
    assert error["balance_state"] == "unknown" and not error["balance_verified"]
    assert error["monthly_fee_aud"] == 20 and error["upstream_status"] == 520 and "HTML response" in error["body_summary"]
    state["error"] = RuntimeError("fixture failure")
    clock[0] += 11
    assert request().status_code == 500
    state["error"] = master_service.OandaUpstreamHTTPError(status_code=401, mode="live", account_id=cfg["account_id"],
        endpoint="/accounts/{account_id}/summary", body_summary="Unauthorized", transient=False)
    clock[0] += 11
    assert request().status_code == 502
    state["error"] = None
    cfg["token"] = "rotated-fixture-token"  # credential changes also invalidate a cached result
    state["summary"]["id"] = "wrong-account"
    assert request().status_code == 500


def test_oanda_inactivity_widget_countdown_and_balance_states(monkeypatch):
    state = _fixture_account(monkeypatch)
    positive = _build()
    state["summary"]["balance"] = "0"
    zero = _build()
    state["fill"] = None
    zero_unknown = _build()
    node = shutil.which("node")
    assert node, "Existing Node runtime required"
    harness = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const fixtures=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync(process.argv[1],'utf8');
const elements={};
for(const id of ['headline','detail','last-trade','countdown','open-trades','threshold','fee-date','monthly-fee','details','error-detail','toggle','next-assessment','account','balance']) {
 elements['oanda-inactivity-'+id]={textContent:'',hidden:true,handlers:{},style:{},setAttribute(k,v){this[k]=v},addEventListener(k,v){this.handlers[k]=v}};
}
elements['refresh-btn']={handlers:{},addEventListener(k,v){this.handlers[k]=v}};
let now=Date.parse('2026-11-05T02:00:00Z'), current=fixtures.positive, httpStatus=200;
class Clock extends Date { constructor(...args){super(...(args.length?args:[now]))} static now(){return now} }
const timers=new Map(); let timerId=0, calls=0;
const context={console:{error(){}},Date:Clock,Intl,URL,Math,Promise,
 document:{body:{dataset:{}},visibilityState:'visible',getElementById:id=>elements[id]||null,addEventListener(){}},
 setInterval(fn,ms){timers.set(++timerId,{fn,ms});return timerId},clearInterval(id){timers.delete(id)},
 setTimeout(){return 1},clearTimeout(){},addEventListener(){},
 fetch:async url=>{if(url==='/api/oanda-inactivity-status')calls++;return {ok:url==='/api/oanda-inactivity-status'?httpStatus===200:true,status:httpStatus,statusText:'Fixture',text:async()=>JSON.stringify(url==='/scripts'?[]:current)}},
};context.window=context;
const el=id=>elements['oanda-inactivity-'+id];
const settle=()=>new Promise(resolve=>setImmediate(resolve));
// Execute only the captured render timer, never the background polling timers.
const tick=()=>{const renderTimers=[...timers.values()].filter(t=>t.ms===1000);assert.strictEqual(renderTimers.length,1);renderTimers[0].fn()};
const refresh=async(payload,status=200)=>{current=payload;httpStatus=status;elements['refresh-btn'].handlers.click();await settle();tick()};
(async()=>{
 vm.createContext(context);vm.runInContext(source,context,{filename:'dashboard.js'});await settle();
 assert.match(el('countdown').textContent,/21 days/);
 assert.match(el('last-trade').textContent,/2025-11-05.*Australia\/Brisbane/);
 assert.match(el('balance').textContent,/25.50 USD/);
 assert.match(el('monthly-fee').textContent,/AUD 20 per client/);
 assert.match(el('account').textContent,/fixture-live-001.*Other sub-account/);
 assert.strictEqual(el('details').hidden,true);
 el('toggle').handlers.click();await settle();tick();assert.strictEqual(el('details').hidden,false);
 assert.match(el('detail').textContent,/Deposits and withdrawals/);
 await refresh(fixtures.zero);assert.match(el('headline').textContent,/No inactivity fee risk/);assert.doesNotMatch(el('countdown').textContent,/days|due today/);
 await refresh(fixtures.zero_unknown);assert.match(el('headline').textContent,/balance is zero/);
 await refresh(fixtures.positive);assert.match(el('countdown').textContent,/21 days/);
 now=Date.parse('2026-11-26T13:30:00Z'); const callsBefore=calls;
 tick();assert.strictEqual(el('countdown').textContent,'Assessment due today');
 now=Date.parse('2026-11-26T14:01:00Z');tick();assert.match(el('next-assessment').textContent,/2026-12-29/);assert.match(el('countdown').textContent,/32 days/);
 assert.match(el('fee-date').textContent,/2026-11-26/);assert.strictEqual(calls,callsBefore);
 await refresh({...fixtures.positive,has_open_positions:true,status:'paused_open_position'});assert.strictEqual(el('countdown').textContent,'Protected');
 await refresh({...fixtures.positive,ok:false,balance:null,balance_verified:false,balance_state:'unknown',status:'unavailable'});assert.strictEqual(el('countdown').textContent,'Unavailable');
 await refresh(fixtures.zero);await refresh({ok:false,status:'unavailable',error:'Fixture upstream error',upstream_status:520,body_summary:'Sanitized HTML response',monthly_fee_aud:20},503);
 assert.strictEqual(el('countdown').textContent,'Unavailable');assert.strictEqual(el('balance').textContent,'Unavailable');assert.doesNotMatch(el('headline').textContent,/No inactivity fee risk/);assert.match(el('error-detail').textContent,/Fixture upstream error/);
 assert.strictEqual(timers.size,3);console.log('actual dashboard JavaScript: fixture states, Brisbane rollover, refresh failure and timers passed');
})().catch(err=>{console.error(err);process.exitCode=1});
"""
    import os
    env = dict(os.environ, TZ="America/Los_Angeles")
    completed = subprocess.run([node, "-e", harness, str(ROOT / "render/static/dashboard.js")],
        input=json.dumps({"positive": positive, "zero": zero, "zero_unknown": zero_unknown}),
        text=True, capture_output=True, timeout=30, env=env)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "passed" in completed.stdout
