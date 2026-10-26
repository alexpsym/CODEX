"""Focused integration checks; no application startup or real worker/account access."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import types

import pytest

from shared.bybit_order_expiry import ExpiryQueue, credential_scope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def service(monkeypatch, tmp_path):
    # Execute the canonical application from a temporary root so import-time path setup
    # cannot read/write real journal/state. Environment loading and account transports are isolated.
    import shared.env_bootstrap as bootstrap
    import dotenv
    monkeypatch.setattr(bootstrap, "load_master_env", lambda **kw: {})
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    for key in list(os.environ):
        if any(word in key for word in ("BYBIT", "OANDA", "DROPBOX", "JOURNAL", "MASTER_ENV", "COINSPOT")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("LOCAL_STATE_ONLY", "1")
    monkeypatch.setenv("DROPBOX_SYNC_ENABLED", "0")
    if "multipart" not in sys.modules:
        mod = types.ModuleType("multipart"); mod.__version__ = "0.0-test"
        mod.__spec__ = importlib.util.spec_from_loader("multipart", loader=None)
        sub = types.ModuleType("multipart.multipart"); sub.parse_options_header = lambda v: (v, {})
        monkeypatch.setitem(sys.modules, "multipart", mod)
        monkeypatch.setitem(sys.modules, "multipart.multipart", sub)
    app_path = tmp_path / "render/master_service.py"
    app_path.parent.mkdir(); (app_path.parent / "static").mkdir()
    app_path.write_text((ROOT / "render/master_service.py").read_text(encoding="utf-8"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("job7b_" + tmp_path.name.replace("-", "_"), app_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    clock = [1900000000000]
    state = {"healthy": True, "starts": 0, "posts": [], "behavior": "accept", "saved": [], "bind_fail": False}
    class Queue(ExpiryQueue):
        def __init__(self, path): super().__init__(path, clock=lambda: clock[0])
        def worker_status(self):
            result = super().worker_status()
            return dict(result, healthy=state["healthy"])
        def bind(self, *a, **kw):
            if state["bind_fail"]: raise OSError("fixture disk failure")
            return super().bind(*a, **kw)
    path = tmp_path / "expiry.sqlite3"
    monkeypatch.setattr(module, "ExpiryQueue", Queue)
    monkeypatch.setattr(module, "BYBIT_EXPIRY_QUEUE_PATH", path)
    monkeypatch.setattr(module, "_bybit_expiry_now_ms", lambda: clock[0])
    def start(*a):
        state["starts"] += 1
        if not state["healthy"]: raise OSError("fixture startup failure")
        Queue(path).worker_begin("fixture-no-process", tmp_path / "worker.log")
        return {"healthy": True}
    monkeypatch.setattr(module, "_start_bybit_expiry_worker", start)
    monkeypatch.setattr(module, "resolve_bybit_credentials_for", lambda mode: (mode, "fake-key", "fake-secret", "https://api-demo.bybit.com" if mode=="demo" else "https://api.bybit.com", "DEMO_EXPLICIT" if mode=="demo" else "KEY1"))
    for name in ("_log_webhook_event", "_invalidate_open_orders_cache", "cache_bybit_demo_tpsl_request", "_schedule_dropbox_upload_state_backup", "_schedule_early_trade_folders"):
        monkeypatch.setattr(module, name, lambda *a, **kw: None)
    monkeypatch.setattr(module, "_bybit_early_folder_lifecycle", lambda **kw: (True, {}))
    monkeypatch.setattr(module, "_accepted_calculator_intent", lambda payload, **kw: payload)
    monkeypatch.setattr(module, "_upsert_calculator_trade_context", lambda data, **kw: state["saved"].append(data))
    monkeypatch.setattr(module, "_delete_pending_webhook", lambda *a: False)
    monkeypatch.setenv("BYBIT_POSITION_MODE", "one_way")
    async def lookup(base, symbol):
        return {"symbol": symbol, "contractType":"LinearPerpetual", "quoteCoin":"USDT", "priceFilter":{"tickSize":"0.1"}}
    monkeypatch.setattr(module, "_bybit_lookup_linear_symbol_with_fallback", lookup)
    async def get(*a, **kw):
        return {"result":{"list":[{"lastPrice":"105", "bid1Price":"104.9", "ask1Price":"105.1"}]}}
    monkeypatch.setattr(module, "_bybit_get_async", get)
    async def post(**kw):
        body = dict(kw["body"])
        state["posts"].append(body)
        if body["orderLinkId"].startswith("lttx_"):
            rows = Queue(path).list_records()
            match = [r for r in rows if r["order_link_id"] == body["orderLinkId"]]
            assert len(match)==1 and match[0]["state"]=="reserved"
            assert match[0]["symbol"]==body["symbol"] and match[0]["quantity"]==body["qty"]
            assert match[0]["position_idx"]==body["positionIdx"] and match[0]["side"]==body["side"]
        if state["behavior"]=="timeout": raise TimeoutError("raw-secret-body")
        if state["behavior"]=="reject": return {"retCode":110007,"retMsg":"fixture rejection","result":{}}
        if state["behavior"]=="empty": return {}
        return {"retCode":0,"result":{"orderId":"accepted-"+str(len(state["posts"])),"orderLinkId":body["orderLinkId"]}}
    monkeypatch.setattr(module, "_bybit_signed_post", post)
    # No startup hook is run by this fixture. Any unexpected external operation fails closed.
    def forbidden(*a, **kw): raise AssertionError("No real external operations")
    monkeypatch.setattr(module.httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(module, "_wait_for_position_entry", forbidden)
    module.fixture = types.SimpleNamespace(clock=clock, state=state, path=path, Queue=Queue)
    return module


def payload(**changes):
    data = dict(asset="crypto",broker="bybit",account="demo",symbol="BTCUSDT",action="buy",order_type="limit",
                entry_price="100",stop_loss_price="95",take_profit_price="110",quantity="2",level_anchor_mode="planned_entry",
                order_expiry={"mode":"duration","value":"5","unit":"minutes"})
    data.update(changes)
    return data


def submit(service, data):
    return asyncio.run(service.calculator_submit(data))


def test_calculator_expiry_validation_and_brisbane_conversion(service):
    s=service; now=s.fixture.clock[0]
    assert s._parse_bybit_order_expiry({"mode":"duration","value":"1.5","unit":"hours"})==now+5400000
    assert s._parse_bybit_order_expiry({"mode":"at","brisbane":"2030-03-18T09:30"},now_ms=1)==int(datetime(2030,3,17,23,30,tzinfo=timezone.utc).timestamp()*1000)
    for value in (None, "", "NaN", "Infinity", True, 0, -1, "nope"):
        with pytest.raises(ValueError): s._parse_bybit_order_expiry({"mode":"duration","value":value,"unit":"minutes"})
    for spec in ({}, {"mode":"GTD"}, {"mode":"at","brisbane":"2020-01-01T00:00"},
                 {"mode":"at","brisbane":"2030-01-01T00:00Z"}, {"mode":"duration","value":1,"unit":"days"}):
        with pytest.raises(ValueError): s._parse_bybit_order_expiry(spec)
    for changes in ({"asset":"fx","broker":"oanda"},{"order_type":"market"},{"trade_mode":"spot"},
                    {"pending_webhook_id":"pending"},{"webhook_mode":"yes"}):
        with pytest.raises(s.HTTPException): submit(s,payload(**changes))
    assert not s.fixture.state["posts"] and not s.fixture.state["starts"]
    assert s._parse_bybit_order_expiry(None) is None


def test_expiry_submission_requires_readiness_and_reserves_exact_identity_before_create(service, monkeypatch):
    s=service; f=s.fixture
    f.state["healthy"]=False
    with pytest.raises(s.HTTPException,match="") as exc: submit(s,payload())
    assert "no order sent" in str(exc.value.detail) and not f.state["posts"]
    no_expiry=payload(); no_expiry.pop("order_expiry")
    response=submit(s,no_expiry)
    assert response.status_code==200 and len(f.state["posts"])==1 and f.state["starts"]==1
    f.state["healthy"]=True
    # Startup delay must not consume a requested duration before the submission attempt.
    start=s._start_bybit_expiry_worker
    def stale(path):
        start(path)
        f.clock[0]+=30000  # A genuinely stale heartbeat must still block registration.
    monkeypatch.setattr(s,"_start_bybit_expiry_worker",stale)
    before=len(f.state["posts"])
    with pytest.raises(s.HTTPException): submit(s,payload())
    assert len(f.state["posts"])==before
    def delayed(path):
        f.clock[0]+=30000
        start(path)  # The worker publishes readiness after the simulated startup delay.
    monkeypatch.setattr(s,"_start_bybit_expiry_worker",delayed)
    response=submit(s,payload())
    body=json.loads(response.body); row=f.Queue(f.path).list_records()[0]
    assert row["deadline_ms"]==f.clock[0]+300000
    assert row["state"]=="scheduled" and row["order_id"]==body["result"]["order"]["orderId"]
    assert row["mode"]=="demo" and row["category"]=="linear"
    assert f.state["posts"][-1]["takeProfit"]=="110" and f.state["posts"][-1]["stopLoss"]=="95"
    assert f.state["posts"][-1]["timeInForce"]=="GTC"
    assert body["result"]["order_expiry"]["id"]==row["id"]
    scope=credential_scope("demo","https://api-demo.bybit.com","fake-key","fake-secret","DEMO_EXPLICIT")
    assert row["fingerprint"]==scope.fingerprint
    assert s._bybit_expiry_for_order("demo",scope.environment,"fake-key","fake-secret","DEMO_EXPLICIT",{**f.state["posts"][-1],"orderId":row["order_id"]})["id"]==row["id"]
    assert s._bybit_expiry_for_order("live","https://api.bybit.com","fake-key","fake-secret","KEY1",{**f.state["posts"][-1],"orderId":row["order_id"]}) is None
    from zoneinfo import ZoneInfo
    absolute=datetime.fromtimestamp((f.clock[0]+1000)/1000,ZoneInfo("Australia/Brisbane")).strftime("%Y-%m-%dT%H:%M:%S")
    before=len(f.state["posts"])
    with pytest.raises(s.HTTPException): submit(s,payload(order_expiry={"mode":"at","brisbane":absolute}))
    assert len(f.state["posts"])==before
    # Readiness/reservation failure never silently degrades to an unscheduled create.
    count=len(f.state["posts"])
    monkeypatch.setattr(f.Queue,"reserve",lambda *a,**kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(s.HTTPException): submit(s,payload())
    assert len(f.state["posts"])==count


def test_expiry_submission_preserves_uncertain_and_accepted_outcomes(service, monkeypatch):
    s=service; f=s.fixture
    for behavior in ("timeout","empty"):
        f.state["behavior"]=behavior
        response=submit(s,payload()); data=json.loads(response.body)
        assert response.status_code==409 and data["do_not_resubmit"], data
        row=f.Queue(f.path).get(data["order_expiry"]["id"])
        assert row["state"]=="reserved" and row["order_link_id"]==f.state["posts"][-1]["orderLinkId"]
        assert "raw-secret-body" not in response.body.decode()
    f.state["behavior"]="accept"; f.state["bind_fail"]=True
    response=submit(s,payload()); data=json.loads(response.body)
    assert data["ok"] and data["result"]["order"]["orderId"]
    assert "Do not submit another" in data["warnings"][0]
    assert f.Queue(f.path).get(data["result"]["order_expiry"]["id"])["state"]=="reserved"
    f.state["bind_fail"]=False
    def failed_save(*a,**kw): raise OSError("fixture context persistence failed")
    monkeypatch.setattr(s,"_upsert_calculator_trade_context",failed_save)
    response=submit(s,payload()); data=json.loads(response.body)
    assert data["ok"] and not data["journal_context_saved"] and data["result"]["order_expiry"]["state"]=="scheduled"
    f.state["behavior"]="reject"
    response=submit(s,payload()); assert response.status_code==400
    assert any(r["state"]=="rejected" for r in f.Queue(f.path).list_records())
    status=json.loads(asyncio.run(s.bybit_order_expiry_status()).body)
    assert len(status["schedules"])==5
    serialized=json.dumps(status)
    assert "fingerprint" not in serialized and "fake-secret" not in serialized and "raw-secret-body" not in serialized
    f.state["healthy"]=False; f.clock[0]+=600000
    status=s._bybit_expiry_status()
    assert any("Overdue" in r["status_label"] for r in status["schedules"])


def test_local_startup_resumes_pending_expiry_without_shutdown_ownership(service, monkeypatch):
    s=service; f=s.fixture
    asyncio.run(s._resume_bybit_expiry_on_local_startup()); assert f.state["starts"]==0
    submit(s,payload()); before=f.state["starts"]
    asyncio.run(s._resume_bybit_expiry_on_local_startup()); assert f.state["starts"]==before+1
    f.state["healthy"]=False
    asyncio.run(s._resume_bybit_expiry_on_local_startup())
    assert "could not resume" in s._BYBIT_EXPIRY_START_ERROR
    monkeypatch.setattr(s,"APP_PROFILE","render")
    before=f.state["starts"]; asyncio.run(s._resume_bybit_expiry_on_local_startup())
    assert f.state["starts"]==before
    # No worker joins ScriptManager or LTT shutdown markers; inspect exact shutdown wiring.
    import ast
    tree=ast.parse((ROOT/"render/master_service.py").read_text(encoding="utf-8"))
    shutdown=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_log_local_master_shutdown"]
    assert shutdown
    for node in shutdown:
        assert "expiry" not in ast.unparse(node).lower()
    assert "_resume_bybit_expiry_on_local_startup" in [fn.__name__ for fn in s.app.router.on_startup]


def test_expiry_controls_and_durable_status_survive_reload(service, tmp_path):
    s=service; result=json.loads(submit(s,payload()).body)
    assert result.get("ok"), result
    row=result["result"]["order_expiry"]
    # Recreate queue/cache without application memory and publish a terminal broker-confirmed result.
    queue=s.fixture.Queue(s.fixture.path)
    with queue.connection(write=True) as db:
        db.execute("UPDATE schedules SET state='cancelled',revision=revision+1 WHERE id=?",(row["id"],))
    status=json.loads(asyncio.run(s.bybit_order_expiry_status()).body)
    assert status["schedules"][0]["status_label"]=="Broker-confirmed cancellation"
    assert "bybit-expiry-mode" in s.CALCULATOR_TEMPLATE and "Australia/Brisbane" in s.CALCULATOR_TEMPLATE
    node=shutil.which("node"); assert node, "Existing Node runtime required"
    harness=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync(process.argv[1],'utf8');
function run(){
 const elements={},requests=[],intervals=[];
 class E{constructor(){this.value='';this.style={};this.dataset={};this.handlers={};this.children=[];this.classList={toggle(){},add(){},remove(){}};this.textContent='';this.disabled=false;}
 addEventListener(e,f){this.handlers[e]=f;} setAttribute(k,v){this[k]=v;} getAttribute(k){return this[k];} appendChild(e){this.children.push(e);return e;} remove(){} focus(){}
 querySelectorAll(){if(!this.buttons)this.buttons=['no','yes','limit','market'].map(v=>{let e=new E();e.dataset.v=v;return e;});return this.buttons;}}
 const el=id=>elements[id]||(elements[id]=new E());
 el('calc-symbol').value='BTCUSDT';el('bybit-expiry-mode').value='none';el('bybit-expiry-unit').value='minutes';el('bybit-expiry-value').value='5';
 const context={console,Intl,Date,AbortController,URL,Blob,Promise,setImmediate,
  setInterval:f=>{intervals.push(f);return 1;},clearInterval(){},setTimeout:()=>1,clearTimeout(){},
  document:{getElementById:el,createElement:()=>new E(),body:new E()},window:{__CALCULATOR_TESTING__:true,addEventListener(){}},
  fetch:async(url,opts={})=>{requests.push([url,opts]);let body=url.includes('order-expiry/status')?fixture.status:url.includes('/submit')?fixture.result:{app_profile:'local',webhook:{available:false},trendline_plans:{available:false}};return {ok:true,status:200,statusText:'OK',headers:{get:name=>String(name).toLowerCase()==='content-type'?'application/json':null},text:async()=>JSON.stringify(body),json:async()=>body};}};
 vm.createContext(context);vm.runInContext(source,context);
 return {elements,requests,intervals,h:context.window.__expiryTestHooks,el};
}
(async()=>{
 let ui=run();await ui.h.refreshExpiryStatus();await new Promise(r=>setImmediate(r));
 assert(ui.el('bybit-expiry-status').textContent.includes('Broker-confirmed cancellation'));
 assert.strictEqual(ui.h.expiryPayload(),null);
 Object.assign(ui.h.state,{asset:'crypto',broker:'bybit',order_type:'limit',webhook_mode:'no'});
 ui.h.syncExpiryControls();assert.strictEqual(ui.el('bybit-expiry-wrap').style.display,'');
 ui.el('bybit-expiry-mode').value='at';ui.el('bybit-expiry-at').value='2030-03-18T09:30';ui.h.syncExpiryControls();
 assert.strictEqual(ui.h.expiryPreviewDeadline(ui.h.expiryPayload()),Date.parse('2030-03-17T23:30:00Z'));
 assert(ui.el('bybit-expiry-preview').textContent.includes('Australia/Brisbane'));
 ui.h.state.order_type='market';ui.h.syncExpiryControls();assert.strictEqual(ui.h.expiryPayload(),null);assert.strictEqual(ui.el('bybit-expiry-mode').value,'none');
 ui.h.state.order_type='limit';ui.h.state.webhook_mode='yes';ui.h.syncExpiryControls();assert.strictEqual(ui.h.expiryPayload(),null);
 ui.h.state.webhook_mode='no';ui.h.state.asset='fx';ui.h.syncExpiryControls();assert.strictEqual(ui.h.expiryPayload(),null);
 // Exercise mounted calculator submission handler, not a copied payload helper.
 Object.assign(ui.h.state,{asset:'crypto',broker:'bybit',order_type:'limit',webhook_mode:'no',side:'buy',quoteStatus:'ready',quote:{symbol:'BTCUSDT',entry_price:100,stop_price:95,target_price:110,quantity:2},resolvedSymbol:'BTCUSDT'});
 ui.el('calc-submit').disabled=false;ui.el('bybit-expiry-mode').value='duration';ui.h.syncExpiryControls();
 await ui.el('calc-submit').handlers.click();
 const sent=ui.requests.find(r=>r[0].endsWith('/submit'));assert(sent,ui.el('calc-error').textContent);
 assert.strictEqual(JSON.parse(sent[1].body).order_expiry.unit,'minutes');
 assert(ui.el('calc-submit').disabled);
 ui=run();await ui.h.refreshExpiryStatus();assert.strictEqual(ui.h.expiryPayload(),null);
 assert(ui.el('bybit-expiry-status').textContent.includes('Broker-confirmed cancellation'));
 assert(ui.requests.every(r=>!r[0].includes('cancel')&&!r[0].includes('amend')));
 console.log('Calculator controls, actual submit payload and durable reload status passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    completed=subprocess.run([node,"-e",harness,str(ROOT/"render/static/calculator.js")],
        input=json.dumps({"status":status,"result":result}),capture_output=True,text=True,timeout=20)
    assert completed.returncode==0,completed.stdout+completed.stderr

    import ast
    tree=ast.parse((ROOT/"tests/test_open_orders_page.py").read_text(encoding="utf-8"))
    existing=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="test_pepperstone_local_tracker_rows_render_and_refresh")
    existing_harness=next(n.value.value for n in ast.walk(existing) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="harness" for t in n.targets))
    prefix=existing_harness.split("eval(fs.readFileSync")[0]
    prefix=prefix.replace("url.includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current", "url.includes('order-expiry/status')?{schedules:[current.expiry]}:url.includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current")
    open_harness=prefix+r"""
    eval(fs.readFileSync(process.argv[1],'utf8'));
    (async()=>{
      for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
      current=payloads[1];await window.__openOrdersTestHooks.pollVersion();
      assert.strictEqual(window.__openOrdersItems[0].order_expiry.state,'cancelled');
      assert(tbody.textContent.includes('Broker-confirmed cancellation'));
      assert(tbody.textContent.includes('Australia/Brisbane'));
      console.log('Open Orders durable expiry publication passed');
    })().catch(e=>{console.error(e);process.exitCode=1;});
    """
    item={"broker":"Bybit","account":"demo","category":"linear","instrument":"BTCUSDT","side":"Buy",
          "type":"Order","id":row["order_id"],"order_link_id":row["order_link_id"],"status":"New","order_expiry":row}
    payloads=[{"items":[item],"version":1,"progress_revision":1,"expiry":row},
              {"items":[item],"version":1,"progress_revision":1,"expiry":status["schedules"][0]}]
    completed=subprocess.run([node,"-e",open_harness,str(ROOT/"render/static/open_orders.js")],
        input=json.dumps({"headers":[],"payloads":payloads}),capture_output=True,text=True,timeout=20)
    assert completed.returncode==0,completed.stdout+completed.stderr


def test_expiry_repair_status_shows_overdue_error_and_worker_health(service):
    import ast
    s=service;f=s.fixture
    result=json.loads(submit(s,payload()).body);row=result["result"]["order_expiry"]
    queue=f.Queue(f.path)
    f.clock[0]+=600000
    diagnostic={"operation":"realtime","reason":"http_error","exception_type":"BrokerError","http_status":401,"ret_code":10003,
                "retMsg":"DO-NOT-EXPOSE", "headers":"X-BAPI-secret"}
    claimed=queue.claim()
    saved=queue.finish(claimed,"reconciling",diagnostic=diagnostic)
    status=json.loads(asyncio.run(s.bybit_order_expiry_status()).body)
    public=status["schedules"][0]
    assert public["status_label"].startswith("Overdue;") and public["worker_healthy"] is True
    assert public["diagnostic"]["http_status"]==401 and public["diagnostic"]["ret_code"]==10003
    assert "credentials" in public["last_error"] and "cancelled" not in public["status_label"].lower()
    assert "DO-NOT-EXPOSE" not in json.dumps(status) and "X-BAPI" not in json.dumps(status)
    f.state["healthy"]=False;status=json.loads(asyncio.run(s.bybit_order_expiry_status()).body)
    assert "worker unavailable" in status["schedules"][0]["status_label"]
    assert saved["attempts"]==0 and queue.get(row["id"])["deadline_ms"]==row["deadline_ms"]
    tree=ast.parse((ROOT/"tests/test_bybit_order_expiry_integration.py").read_text(encoding="utf-8"))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="test_expiry_controls_and_durable_status_survive_reload")
    harness=next(n.value.value for n in ast.walk(node) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="harness" for t in n.targets))
    prefix=harness.split("(async()=>{")[0]
    script=prefix+r"""
    (async()=>{
      const ui=run();await ui.h.refreshExpiryStatus();
      const text=ui.el('bybit-expiry-status').textContent;
      assert(text.includes('Overdue')&&text.includes('unavailable')&&text.includes('http_status=401')&&text.includes('ret_code=10003'),text);
      assert(!text.includes('DO-NOT-EXPOSE')&&!text.includes('X-BAPI'));
      assert(ui.requests.every(r=>!r[0].includes('/submit')&&!r[0].includes('/cancel')));
      console.log('Truthful overdue/error and worker-health rendering passed');
    })().catch(e=>{console.error(e);process.exitCode=1;});
    """
    node=shutil.which("node");assert node
    completed=subprocess.run([node,"-e",script,str(ROOT/"render/static/calculator.js")],input=json.dumps({"status":status,"result":{}}),capture_output=True,text=True,timeout=20)
    assert completed.returncode==0,completed.stdout+completed.stderr
