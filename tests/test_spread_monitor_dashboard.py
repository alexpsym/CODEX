import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MASTER_SERVICE_PATH = ROOT / "render" / "master_service.py"


def _load_master_service(module_name: str, profile: str):
    old_profile = os.environ.get("APP_PROFILE")
    try:
        os.environ["APP_PROFILE"] = profile
        spec = importlib.util.spec_from_file_location(module_name, MASTER_SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if old_profile is None:
            os.environ.pop("APP_PROFILE", None)
        else:
            os.environ["APP_PROFILE"] = old_profile


def test_dashboard_source_registers_spread_monitor_local_only_web_app():
    source = MASTER_SERVICE_PATH.read_text(encoding="utf-8")
    assert '"spreads-clone"' in source
    assert '"spreads-clone": ["spread_app.py"]' in source
    assert '"spreads-clone": "Oanda Spreads"' in source
    assert 'id="dashboard-scripts-panel"' in source
    assert '.script-toolbar-grid .script-btn[data-script-name="spreads-clone"]' not in source


def test_local_launcher_installs_only_required_spread_dependencies_with_same_python():
    source = (ROOT / "run_local_master_control.bat").read_text(encoding="utf-8")
    assert "spreads-clone\\requirements.txt" in source
    assert '"!PYTHON_EXE!" -m pip install Flask openpyxl requests' in source
    assert '"!PYTHON_EXE!" -m pip install MetaTrader5' in source
    assert 'if /I "!SPREAD_MONITOR_INSTALL_OPTIONAL_MT5!"=="1" (' in source
    assert '-m pip install -r "!ROOT!spreads-clone\\requirements.txt"' not in source
    assert "SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL" in source


def test_spread_monitor_files_are_present_and_tracked():
    required = [
        "spreads-clone/spread_app.py",
        "spreads-clone/spread_core.py",
        "spreads-clone/pepperstone_import.py",
        "tests/test_spread_core.py",
        "tests/test_spread_monitor_dashboard.py",
        "tests/test_mt5_spread_fetch.py",
        "tests/test_oanda_spread_fetch.py",
        "tests/test_pepperstone_spread_import.py",
    ]
    for rel_path in required:
        assert (ROOT / rel_path).exists(), rel_path
    tracked_required = [path for path in required if path not in {
        "spreads-clone/pepperstone_import.py",
        "tests/test_pepperstone_spread_import.py",
    }]
    if not (ROOT / ".git").exists():
        pytest.skip("Git index unavailable in this checkout.")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *tracked_required],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_spread_app_table_layout_prevents_broker_value_overlap():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "min-width: 1620px;" in source
    assert "width: 164px;" in source
    assert "broker-value" in source
    assert "Pepperstone" not in source


def test_spread_app_frontend_normalizes_messages_refresh_and_sorting():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "function scalarMessage(value)" in source
    assert "JSON.stringify(value)" in source
    assert "payloadHasFailures(payload)" in source
    assert "function isRefreshRunning(payload)" in source
    assert "function refreshIntervalSeconds(payload)" in source
    assert "Number.isFinite(seconds) && seconds > 0 ? seconds : 300" in source
    assert "updateLastRefresh: true" in source
    assert "function queueStatusPoll()" in source
    assert "function pollRefreshStatus()" in source
    assert "loadStatus();" not in source
    assert "refreshData({ initial: true })" in source
    assert "hideOandaCacheUntilFresh" in source
    assert "Loading OANDA cache" not in source
    assert "loadStatus().then(() => refreshData())" not in source
    assert "headEl.addEventListener('click'" in source
    assert "sortState.direction === 'asc' ? 'desc' : 'asc'" in source
    assert "function cellSortValue(row, timeframe)" in source
    assert "renderCurrentSpreadTable(payload)" in source
    assert "payload?.current_only" in source
    assert "Current Spread" in source
    assert "spreadNumber(brokerData(cell))" in source
    assert "function spreadNumber(data)" in source
    assert "raw === null || raw === undefined || raw === ''" in source
    assert "value >= 0 ? value : NaN" in source
    assert "function spreadPointsText(data)" not in source
    assert "0 points" not in source
    assert "const unavailable = !Number.isFinite(spreadValue);" in source
    assert ".spread-neutral" in source
    assert "pepperstone_razor" not in source
    assert "function importPepperstone(file)" not in source
    assert "manual import only" not in source
    assert "[object Object]" not in source


def test_spread_app_no_longer_imports_live_mt5_fetchers():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "mt5_spreads" not in source
    assert "fetch_mt5_spread_samples" not in source
    assert "available_mt5_symbols" not in source
    assert "preflight_mt5_environment" not in source


def test_spread_app_selector_buttons_and_plain_spread_note_exist():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert 'data-broker="oanda">Oanda</button>' in source
    assert 'data-broker="pepperstone"' not in source
    assert "Spread values are shown as percentage of bid/ask midpoint." in source
    assert "Points are shown when available." not in source
    assert "Low percentile" not in source
    assert "Medium percentile" not in source
    assert "High percentile" not in source
    assert "Spread percentile legend" not in source
    assert "Unavailable" in source


def test_spread_app_table_renders_one_selected_broker_line_per_cell():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "brokerLine(label, brokerData(cell))" in source
    assert "brokerLine('OANDA', brokerData(cell, 'oanda'))" not in source
    assert "brokerLine('Pepperstone Razor'" not in source


def test_scripts_endpoint_places_spread_monitor_after_iv_indicator_in_local_profile():
    master_service = _load_master_service("render_master_service_spread_dashboard_local", "local")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = [str(item.get("name")) for item in payload]
    expected = [
        "calculator",
        "bounce-trader",
        "fxweekend",
        "trading-journal",
        "instrument-lookup",
        "history",
        "monitor",
        "atr-scanner",
        "ivindicator-clone",
        "spreads-clone",
    ]
    positions = [names.index(name) for name in expected]
    assert positions == sorted(positions)
    assert names.index("atr-scanner") == names.index("monitor") + 1
    assert names.index("spreads-clone") == names.index("ivindicator-clone") + 1
    by_name = {str(item.get("name")): item for item in payload}
    assert by_name["bounce-trader"]["remote_owned"] is True
    assert by_name["fxweekend"]["remote_owned"] is True
    assert "fxweekend-clone" not in by_name
    assert by_name["spreads-clone"]["label"] == "Oanda Spreads"
    assert by_name["spreads-clone"]["open_url"] == "/apps/spreads-clone"
    assert by_name["spreads-clone"]["dashboard_main_view"] is True
    assert by_name["monitor"]["label"] == "Alerts"
    assert by_name["atr-scanner"]["label"] == "Scanner"
    assert by_name["instrument-lookup"]["open_url"] == "/instrument-lookup"
    assert by_name["history"]["open_url"] == "/merged/history"
    assert "mt5" not in by_name
    assert "open-orders" not in by_name
    assert "pine" not in by_name


def test_render_profile_does_not_expose_spread_monitor_or_pine_app():
    master_service = _load_master_service("render_master_service_spread_dashboard_render", "render")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    assert "spreads-clone" not in names
    assert "mt5" not in names
    assert "pine" not in names
    assert master_service._render_blocks_path("/apps/spreads-clone") is True
    assert master_service._render_blocks_path("/dashboard/pine") is True


def test_spread_app_status_endpoint_returns_honest_payload_without_broker_connections():
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    client = module.app.test_client()
    response = client.get("/api/spreads/oanda/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, dict)
    assert "ok" in payload
    assert payload["refresh_interval_seconds"] == 300
    assert payload["timeframes"] == []
    assert payload["current_only"] is True
    assert payload["columns"] == [
        {"key": "symbol", "label": "Instrument"},
        {"key": "current_spread", "label": "Current Spread"},
    ]
    assert isinstance(payload["rows"], list)
    alias = client.get("/api/spreads/status")
    assert alias.status_code == 200


def test_oanda_spreads_launcher_waits_for_real_upstream_and_surfaces_child_failure(
    monkeypatch, tmp_path
):
    master_service = _load_master_service("render_master_service_spread_phase7_launcher", "local")

    child_path = tmp_path / "spread_app.py"
    child_path.write_text("print('unused')\n", encoding="utf-8")
    spawned = {}

    class FakeProcess:
        pid = 43210
        returncode = None
        stdout = None

    async def fake_create_subprocess_exec(*command, **kwargs):
        spawned["command"] = list(command)
        spawned["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(master_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def verify_interpreter():
        script = master_service.ManagedScript("spreads-clone", child_path)
        await script.start()
        await asyncio.sleep(0)
        return script

    managed = asyncio.run(verify_interpreter())
    assert managed.last_spawn_command == [sys.executable, "-u", str(child_path)]
    assert spawned["command"] == managed.last_spawn_command

    class StartingScript:
        name = "spreads-clone"
        process = None
        port = None
        startup_task = None
        last_start_attempt_at = None
        last_start_error = None
        last_exit_reason = None
        last_exit_code = None

        @property
        def is_running(self):
            return False

    starting = StartingScript()
    background_calls = {"count": 0}
    port_calls = {"count": 0}

    async def fake_background_start(script):
        background_calls["count"] += 1
        script.last_start_attempt_at = 1.0
        await release.wait()
        if script.startup_task is asyncio.current_task():
            script.startup_task = None

    def fake_allocate_port():
        port_calls["count"] += 1
        return 45678

    def request(path="/apps/spreads-clone/", query=b"view=current", accept=b"text/html"):
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        return master_service.Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": query,
                "headers": [(b"accept", accept)],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 8000),
            },
            receive=receive,
        )

    release = asyncio.Event()
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: starting)
    monkeypatch.setattr(master_service, "_background_start", fake_background_start)
    monkeypatch.setattr(master_service, "_allocate_port", fake_allocate_port)

    async def verify_coalesced_launcher():
        first = await master_service.proxy_app("spreads-clone", request())
        first_task = starting.startup_task
        second = await master_service.proxy_app("spreads-clone", request())
        await asyncio.sleep(0)
        assert first.headers["x-managed-app-launcher"] == "1"
        assert second.headers["x-managed-app-launcher"] == "1"
        assert starting.startup_task is first_task
        assert b'/apps/spreads-clone/?view=current' in first.body
        assert background_calls["count"] == 1
        assert port_calls["count"] == 1
        release.set()
        await first_task

    asyncio.run(verify_coalesced_launcher())

    class RunningScript:
        name = "spreads-clone"
        port = 45678
        upstream_ready_at = None
        is_starting = True
        startup_completed_at = None

        @property
        def is_running(self):
            return True

        def add_log(self, line):
            self.last_log = line

    running = RunningScript()
    upstream_targets = []

    class FakeUpstreamResponse:
        status_code = 200
        content = b"<html>real child page</html>"
        headers = {"content-type": "text/html; charset=utf-8"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, target, **kwargs):
            upstream_targets.append(target)
            return FakeUpstreamResponse()

    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: running)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", FakeAsyncClient)
    upstream = asyncio.run(master_service.proxy_app("spreads-clone", request()))
    assert upstream.headers["x-managed-app-upstream"] == "1"
    assert upstream.body == b"<html>real child page</html>"
    assert upstream_targets == ["http://127.0.0.1:45678/?view=current"]
    assert running.upstream_ready_at is not None

    canonical = asyncio.run(
        master_service.proxy_app(
            "spreads-clone",
            request(path="/apps/spreads-clone", query=b"view=current"),
        )
    )
    assert canonical.status_code == 307
    assert canonical.headers["location"] == "/apps/spreads-clone/?view=current"

    launcher_html = (
        master_service.LAUNCHER_TEMPLATE.replace("{script_name}", "spreads-clone")
        .replace("{script_name_url}", "spreads-clone")
        .replace("{target_url}", "/apps/spreads-clone/?view=current")
        .replace("{has_ui}", "true")
    )
    assert 'href="/logs/view/spreads-clone"' in launcher_html
    launcher_js = re.search(r"<script>(.*?)</script>", launcher_html, re.S)
    assert launcher_js
    js_path = tmp_path / "launcher.js"
    js_path.write_text(launcher_js.group(1), encoding="utf-8")
    node = shutil.which("node")
    assert node, "node is required for managed launcher behavior coverage"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
class Element{constructor(){this.textContent='';this.style={};this.disabled=false;this.listeners={};this.visible=false;this.classList={add:n=>{if(n==='visible')this.visible=true;},remove:n=>{if(n==='visible')this.visible=false;}};}addEventListener(e,f){this.listeners[e]=f;}}
const reply=(status,headers={},payload={})=>({ok:status>=200&&status<300,status,headers:{get:name=>headers[name]||headers[String(name).toLowerCase()]||null},json:async()=>payload});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<5;i++)await flush();}
async function run(mode){
 const elements=Object.fromEntries(['status','spinner','failure-actions','retry-button'].map(id=>[id,new Element()]));
 const timers=[],redirects=[],calls=[];let probes=0,statusCalls=0,startCalls=0;
 const fetch=async(url,options={})=>{
  calls.push({url,method:options.method||'GET'});
  if(url==='/apps/spreads-clone/?view=current'){
   probes++;
   if(mode==='delayed'&&probes===3)return reply(200,{'X-Managed-App-Upstream':'1'});
   return reply(200,{'X-Managed-App-Launcher':'1'});
  }
  if(url==='/api/scripts/spreads-clone'){
   statusCalls++;
   if(mode==='failure'&&startCalls===0)return reply(200,{}, {running:false,starting:false,last_start_error:'Missing Flask dependency',last_exit_code:1});
   return reply(200,{}, {running:true,starting:mode!=='timeout'});
  }
  if(url==='/scripts/spreads-clone/start'){startCalls++;return reply(202,{}, {status:'starting',starting:true});}
  throw new Error('unexpected '+url);
 };
 const context={document:{body:{dataset:{scriptName:'spreads-clone',targetUrl:'/apps/spreads-clone/?view=current',hasUi:'true'}},getElementById:id=>elements[id]},window:{location:{replace:url=>redirects.push(url)}},fetch,setTimeout:fn=>{timers.push(fn);return timers.length;},console,Date,encodeURIComponent};
 vm.runInNewContext(source,context);await settle();
 const tick=async()=>{const fn=timers.shift();assert(fn,'missing fake timer');fn();await settle();};
 if(mode==='delayed'){
  assert.equal(redirects.length,0);await tick();assert.equal(redirects.length,0);await tick();assert.equal(redirects.length,1);assert(redirects[0].startsWith('/apps/spreads-clone/?view=current&_launcher_ready='));
 }else if(mode==='failure'){
  assert.equal(redirects.length,0);assert(elements.status.textContent.includes('Missing Flask dependency'));assert(elements['failure-actions'].visible);assert.equal(elements['retry-button'].disabled,false);
  const retry=elements['retry-button'].listeners.click();elements['retry-button'].listeners.click();await settle();assert.equal(startCalls,1);assert.equal(redirects.length,0);void retry;
 }else{
  while(timers.length)await tick();assert.equal(probes,30);assert.equal(redirects.length,0);assert(elements.status.textContent.includes('Timed out'));assert(elements['failure-actions'].visible);
 }
 return {redirects,startCalls,probes,statusCalls};
}
(async()=>{const delayed=await run('delayed');const failure=await run('failure');const timeout=await run('timeout');console.log(JSON.stringify({delayed,failure,timeout}));})().catch(err=>{console.error(err);process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", harness, str(js_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lifecycle = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(lifecycle["delayed"]["redirects"]) == 1
    assert lifecycle["failure"]["startCalls"] == 1
    assert lifecycle["timeout"]["probes"] == 30


def test_oanda_spreads_page_boots_under_proxy_prefix_without_broker_io(
    monkeypatch, tmp_path
):
    spread_dir = ROOT / "spreads-clone"
    broker_calls = {"count": 0}

    def forbidden_broker_call(*_args, **_kwargs):
        broker_calls["count"] += 1
        raise AssertionError("spread-page boot must not call an OANDA fetcher")

    fake_oanda = types.ModuleType("oanda_spreads")
    fake_oanda.fetch_oanda_current_spreads = forbidden_broker_call
    fake_oanda.get_available_oanda_symbols = forbidden_broker_call
    fake_symbols = types.ModuleType("symbols")
    fake_symbols.build_symbol_universe = forbidden_broker_call

    class FakeState:
        def __init__(self, *_args, **_kwargs):
            pass

        def status(self):
            return {"ok": True, "refresh_state": "idle", "rows": [], "timeframes": [], "current_only": True}

        def start_refresh(self):
            return {"ok": True, "refresh_state": "running", "rows": [], "timeframes": [], "current_only": True}

    fake_core = types.ModuleType("spread_core")
    fake_core.SpreadMonitorState = FakeState
    fake_core.refresh_interval_from_env = lambda: 300
    monkeypatch.setitem(sys.modules, "oanda_spreads", fake_oanda)
    monkeypatch.setitem(sys.modules, "spread_core", fake_core)
    monkeypatch.setitem(sys.modules, "symbols", fake_symbols)
    monkeypatch.setenv("APP_BASE_PATH", "/apps/spreads-clone")
    spec = importlib.util.spec_from_file_location(
        "spread_app_phase7_proxy_boot", spread_dir / "spread_app.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    page = module.app.test_client().get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'const appRoot = "/apps/spreads-clone";' in html
    script_match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert script_match
    js_path = tmp_path / "spread-page.js"
    js_path.write_text(script_match.group(1), encoding="utf-8")
    node = shutil.which("node")
    assert node, "node is required for spread-page boot behavior coverage"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');const source=fs.readFileSync(process.argv[1],'utf8');
class Element{constructor(){this.textContent='';this.innerHTML='';this.hidden=false;this.disabled=false;this.style={};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,f){this.listeners[e]=f;}closest(){return null;}}
const ids=['selector-view','monitor-view','page-title','back-btn','refresh-btn','status','last-refresh','next-refresh','messages','spread-table','spread-head','spread-body','broker-selector'];const el=Object.fromEntries(ids.map(id=>[id,new Element()]));
const calls=[],timers=[];let resolveInitial,refreshCount=0;
const response=(status,payload)=>({ok:status<400,status,statusText:'mock',text:async()=>JSON.stringify(payload)});
const fetch=(url,options={})=>{calls.push({url,method:options.method||'GET'});if(url==='/apps/spreads-clone/api/spreads/oanda/refresh'){refreshCount++;if(refreshCount===1)return new Promise(resolve=>{resolveInitial=resolve;});return Promise.resolve(response(200,{ok:true,refresh_state:'running',refresh:{state:'running'},rows:[],timeframes:[],current_only:true}));}if(url==='/apps/spreads-clone/api/spreads/oanda/status')return Promise.resolve(response(200,{ok:true,refresh_state:'idle',rows:[],timeframes:[],current_only:true,refresh_interval_seconds:300}));throw new Error('unexpected '+url);};
const context={document:{getElementById:id=>el[id]},fetch,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{},setInterval:()=>1,console,Date,Intl};vm.runInNewContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{assert.equal(el['page-title'].textContent,'OANDA Spread Monitor');assert.equal(calls[0].url,'/apps/spreads-clone/api/spreads/oanda/refresh');assert.equal(el['status'].textContent,'Refreshing OANDA spreads...');resolveInitial(response(503,{detail:'OANDA refresh unavailable'}));for(let i=0;i<5;i++)await flush();assert(el['status'].textContent.includes('OANDA refresh unavailable'));assert.equal(el['refresh-btn'].disabled,false);await el['refresh-btn'].listeners.click();for(let i=0;i<5;i++)await flush();const poll=timers.find(t=>t.ms===2000);assert(poll,'status poll not scheduled');poll.fn();for(let i=0;i<5;i++)await flush();assert(calls.some(c=>c.url==='/apps/spreads-clone/api/spreads/oanda/status'));assert.equal(el['refresh-btn'].disabled,false);console.log(JSON.stringify({calls,status:el['status'].textContent,error:el['messages'].innerHTML}));})().catch(err=>{console.error(err);process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", harness, str(js_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    browser = json.loads(result.stdout.strip().splitlines()[-1])
    assert browser["calls"][0] == {
        "url": "/apps/spreads-clone/api/spreads/oanda/refresh",
        "method": "POST",
    }
    assert any(
        call == {"url": "/apps/spreads-clone/api/spreads/oanda/status", "method": "GET"}
        for call in browser["calls"]
    )
    assert broker_calls["count"] == 0


def test_spread_refresh_endpoint_starts_background_job_without_blocking(monkeypatch):
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_refresh_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class FakeState:
        def status(self):
            return {"ok": True, "refresh_state": "idle", "rows": [], "timeframes": []}

        def start_refresh(self):
            return {"ok": True, "refresh_state": "running", "status": "refresh_in_progress", "rows": [], "timeframes": []}

    monkeypatch.setattr(module, "OANDA_STATE", FakeState())
    client = module.app.test_client()
    response = client.post("/api/spreads/oanda/refresh")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["refresh_state"] == "running"
    assert payload["status"] == "refresh_in_progress"
    alias = client.post("/api/spreads/refresh")
    assert alias.status_code == 200


def test_pepperstone_status_and_import_endpoints_are_removed():
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_oanda_only_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    client = module.app.test_client()
    assert client.get("/api/spreads/pepperstone/status").status_code == 404
    assert client.post("/api/spreads/pepperstone/import").status_code == 404
