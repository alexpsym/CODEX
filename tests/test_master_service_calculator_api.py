import asyncio
import importlib.util
import json
import re
import sys
import time
import types
import importlib.machinery
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.fixture
def pepperstone_weekend_reference(monkeypatch, tmp_path, pepperstone_account_fixture):
    from datetime import datetime as RealDatetime, timezone
    class Saturday(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 11, 21, 8, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(master_service, 'datetime', Saturday)
    monkeypatch.setattr(master_service, 'APP_PROFILE', 'local')
    monkeypatch.setattr(master_service, '_trade_context_backup_fallback_enabled', lambda: False)
    monkeypatch.setattr(master_service, '_invalidate_open_orders_cache', lambda: None)
    monkeypatch.setattr(master_service, '_OPEN_ORDERS_SERVICES', {})
    monkeypatch.setattr(master_service, '_OPEN_ORDERS_CACHE', {'payload': None})
    monkeypatch.setattr(master_service, '_upsert_calculator_trade_context', pepperstone_account_fixture)
    path = tmp_path / 'Trader-export.json'
    monkeypatch.setenv('PEPPERSTONE_SPREAD_EXPORT_PATH', str(path))
    item = dict(symbol='USDJPY', mt5_symbol='USDJPY.a', point='0.001', trade_tick_size='0.001',
                quote_timestamp='2026-11-20T21:59:58Z', timestamp='2026-11-21T07:59:59Z',
                bid='159.551', ask='159.562', available=True, stops_level=0, freeze_level=0,
                minimum_lot='0.01', volume_step='0.01', maximum_lot='100', tick_value='1.5')
    calls = []
    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise master_service.HTTPException(502, 'Isolated OANDA unavailable')
    monkeypatch.setattr(master_service, '_get_oanda_config', lambda account: dict(base_url='https://fixture.invalid', account_id='fixture', token='fixture'))
    monkeypatch.setattr(master_service, '_fetch_oanda_instrument_meta', unavailable)
    monkeypatch.setattr(master_service, '_fetch_oanda_json', unavailable)
    monkeypatch.setattr(master_service, '_fetch_oanda_account_summary', unavailable)
    def write(**changes):
        data = dict(version=1, broker='pepperstone', generated_at='2026-11-21T08:00:00Z',
                    account=dict(mode=changes.pop('mode', 'demo'), currency=changes.pop('currency', 'AUD')),
                    symbols=changes.pop('symbols', None) or [dict(item, **changes)])
        path.write_text(json.dumps(data), encoding='utf-8')
    write()
    master_service._save_calculator_trade_contexts([])
    return write, calls


def test_pepperstone_mt5_reference_calculate_without_oanda(monkeypatch, tmp_path, pepperstone_weekend_reference):
    write, calls = pepperstone_weekend_reference
    service = master_service
    assert service._pepperstone_local_tracker_rows() == []
    # Execute the two old production functions, with fixture-only dependencies:
    # resolution fails on metadata; Calculate fails on pricing even with metadata.
    import ast, subprocess
    previous = ast.parse(subprocess.check_output([r'C:\Program Files\Git\cmd\git.exe','show','53c57db:render/master_service.py']).decode('utf-8-sig'))
    scope = dict(vars(service))
    nodes = [n for n in previous.body if isinstance(n,ast.AsyncFunctionDef) and n.name in {'calculator_instrument','_calculator_quote_impl'}]
    for node in nodes:node.decorator_list=[]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<53c57db-calculator-fixture>','exec'),scope)
    with pytest.raises(service.HTTPException):
        asyncio.run(scope['calculator_instrument'](asset='fx',account='demo',symbol='USD_JPY',broker='pepperstone'))
    async def old_meta(**kwargs):return dict(displayPrecision=3,pipLocation=-2,tradeUnitsPrecision=0,minimumTradeSize='1')
    scope['_fetch_oanda_instrument_meta']=old_meta
    with pytest.raises(service.HTTPException):
        asyncio.run(scope['_calculator_quote_impl'](_pepperstone_quote_payload(symbol='USD_JPY',account='demo',risk_mode='fixed_aud',risk_value='10',stop_loss_ticks='108')))
    print('BASELINE_53c57db_RESOLUTION_AND_PRICING_FAILURES_REPRODUCED',len(calls))
    calls.clear()
    for kind in ('standard', 'razor'):
        request = _pepperstone_quote_payload(symbol='USD_JPY', account='demo', side='buy',
            pepperstone_account_type=kind, estimate_razor_from_standard=kind == 'razor',
            order_type='market', risk_mode='fixed_aud', risk_value='10', stop_loss_ticks='108',
            chart_tick_size='0.001', target_mode='rr', risk_reward='2.5')
        instrument = json.loads(asyncio.run(service.calculator_instrument(asset='fx', account='demo', symbol='USD_JPY',
            broker='pepperstone', pepperstone_account_type=kind, chart_tick_size='0.001')).body)
        assert instrument['symbol'] == 'USD_JPY' and instrument['mt5_symbol'] == 'USDJPY.a'
        assert instrument['pepperstone_stop_guidance']['state'] == 'pending'
        q = json.loads(asyncio.run(service.calculator_quote(request)).body)
        assert q['calculation_context_saved'] and q['pepperstone_mt5_export_status']['file_ready']
        assert q['pricing_source']['planning'] and q['pricing_source']['quote_timestamp'] == '2026-11-20T21:59:58Z'
        assert 'OANDA' not in q['market_data_source']
        assert Decimal(q['stop_loss_price_distance']) == Decimal('.108')
        assert Decimal(q['estimated_commission_aud']) == (0 if kind == 'standard' else Decimal(q['quantity'])/100000*Decimal('8.4'))
        rows = service._pepperstone_local_tracker_rows()
        row = next(r for r in rows if r['calculation_context_id'] == q['calculation_context_id'])
        assert row['execution_verified'] is False and row['classification_target']
        if kind == 'standard':
            assert Decimal(q['entry_price']) == Decimal('159.562')
            assert Decimal(q['pepperstone_lot_estimate']['nominal_loss_aud']) == Decimal('11.34')
            assert Decimal(q['quantity'])==7000 and Decimal(q['estimated_reward_aud'])==Decimal('28.35')
            assert Decimal(q['stop_price'])==Decimal('159.454') and Decimal(q['target_price'])==Decimal('159.832')
        else:
            assert Decimal(q['entry_price']) == Decimal('159.557')
        preset, filename = service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request, q))
        values = _parse_set_file(preset)
        assert values['UseDesktopTraderControls'] == 'true' and values['UsePriceDistanceInputs'] == 'true'
        assert values['PresetAccountType'] == kind
        assert Decimal(values['SL_PriceDistance']) == Decimal('.108')
        assert tuple(Decimal(values[k]) for k in ('RiskAUD_Min','RiskAUD_Target','RiskAUD_Max')) == (10,10,12)
        # The editable durable row already exists before downloading its preset.
        saved = asyncio.run(service.save_open_order_classification(dict(target=row['classification_target'],
            version=row['classification_version'], changes={'setup':'Breakout'})))
        assert saved['ok']
        service._TRADE_CONTEXTS_CACHE.clear()
        assert next(r for r in service._pepperstone_local_tracker_rows() if r['id']==row['id'])['classification_values']['setup']=='Breakout'
    assert calls == []
    snapshot=service._pepperstone_fx_reference('USD_JPY','demo')[0]
    assert service._pepperstone_snapshot_condition(snapshot,'demo')[0]=='stale_quote'
    assert service._pepperstone_calculator_quote_condition(snapshot,'demo')[0]=='planning'
    write(quote_timestamp='2026-11-20T08:00:00Z')
    earlier=json.loads(asyncio.run(service.calculator_quote(request)).body)
    assert earlier['pricing_source']['planning'] and earlier['pricing_source']['quote_timestamp']=='2026-11-20T08:00:00Z'
    with monkeypatch.context() as weekday:
        from datetime import timezone
        class Monday(service.datetime):
            @classmethod
            def now(cls,tz=None):
                value=cls(2026,11,23,8,tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        weekday.setattr(service,'datetime',Monday)
        assert service._pepperstone_calculator_quote_condition(snapshot,'demo')[0]=='stale_quote'
    # An older tick, wrong account, missing conversion model and missing price refuse.
    for changes, required in [({'mode':'live'}, 'mode'), ({'tick_value':None}, 'tick value'),
            ({'quote_timestamp':'2026-11-13T21:59:58Z'}, 'stale'), ({'bid':None}, 'quote'),
            ({'quote_timestamp':None},'quote_timestamp'),
            ({'bid':'159.560','ask':'159.562'},'cross')]:
        write(**changes)
        with pytest.raises(service.HTTPException) as exc:
            asyncio.run(service.calculator_quote(request))
        assert required in str(exc.value.detail).lower(), exc.value.detail
    # Healthy OANDA remains a usable fallback with no usable Trader snapshot.
    write(tick_value=None)
    async def meta(**kwargs):return dict(displayPrecision=3,pipLocation=-2,tradeUnitsPrecision=0,minimumTradeSize='1',maximumOrderUnits='10000000')
    async def pricing(**kwargs):return dict(prices=[dict(bids=[dict(price='159.400')],asks=[dict(price='159.411')],time='2026-11-20T21:59:58Z')],homeConversions=[dict(currency='JPY',accountGain='.015',accountLoss='.015',positionValue='.015')])
    monkeypatch.setattr(service,'_fetch_oanda_instrument_meta',meta);monkeypatch.setattr(service,'_fetch_oanda_json',pricing)
    q=json.loads(asyncio.run(service.calculator_quote(dict(request,pepperstone_account_type='standard',estimate_razor_from_standard=False))).body)
    assert q['pricing_source']['label']=='OANDA reference' and q['pepperstone_mt5_export_status']['file_ready']
    assert Decimal(q['entry_price'])==Decimal('159.411')  # No stale MT5/OANDA snapshot mixing.
    with monkeypatch.context() as legacy:
        legacy.setattr(service,'_get_oanda_config',lambda mode:(_ for _ in ()).throw(ValueError('fixture metadata unavailable')))
        with pytest.raises(service.HTTPException) as exc:
            asyncio.run(service.calculator_instrument(asset='fx',account='demo',symbol='USD_JPY',broker='oanda'))
        assert exc.value.status_code==400


def test_pepperstone_weekend_calculator_page_and_local_classification(monkeypatch, tmp_path, pepperstone_weekend_reference):
    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference, limit_fill=False)


def test_pepperstone_limit_fill_on_calculator_dashboard(monkeypatch, tmp_path, pepperstone_weekend_reference):
    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference, limit_fill=True)


def test_pepperstone_commission_preview_native_tab(monkeypatch, tmp_path, pepperstone_weekend_reference):
    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference, limit_fill=False, commission_preview=True)


def _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference, *, limit_fill, market_downloads=None, local_preset_save=None, commission_preview=False):
    """Actual isolated pages/HTTP/production handlers; no production service or broker."""
    import http.server
    import threading
    import urllib.request
    import urllib.parse
    import subprocess
    import websocket
    service=master_service
    events=[];failures=[];downloads=[];cancelled_reads=[]
    hold_fill_ack=threading.Event()
    release_fill_ack=threading.Event()
    reject_fill=threading.Event()
    assert service._pepperstone_local_tracker_rows()==[]
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def do_GET(self):self.dispatch()
        def do_POST(self):self.dispatch()
        def dispatch(self):
            url=urllib.parse.urlsplit(self.path);path=url.path
            query={k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items()}
            received=time.monotonic()
            body_watchdog=None
            if path=='/api/calculator/quote':
                import faulthandler
                body_watchdog=threading.Timer(3,lambda:faulthandler.dump_traceback(file=sys.stdout,all_threads=True));body_watchdog.start()
                print('FIXTURE_HTTP_QUOTE_HEADERS',time.time(),dict(self.headers))
            payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}') if self.command=='POST' else {}
            if path=='/api/calculator/quote':print('FIXTURE_HTTP_BODY_READ_SECONDS',time.monotonic()-received)
            events.append((self.command,path))
            try:
                mime='application/json';headers={}
                if path=='/calculator':
                    value=service.CALCULATOR_TEMPLATE.replace('{{CALCULATOR_JS_URL}}','/static/calculator.js')
                    value=value.replace('{{PEPPERSTONE_PRESETS_FOLDER}}', str(service._PEPPERSTONE_PRESETS_FOLDER) if local_preset_save else '')
                    mime='text/html'
                elif path=='/orders':
                    value=service.OPEN_ORDERS_TEMPLATE.replace('{{OPEN_ORDERS_JS_URL}}','/static/open_orders.js');mime='text/html'
                elif path in ('/static/calculator.js','/static/open_orders.js'):
                    value=(ROOT/'render'/path.lstrip('/')).read_text(encoding='utf-8');mime='text/javascript'
                elif path=='/api/calculator/bootstrap':value=dict(app_profile='local',trendline_plans_available=False,webhook=dict(available=False))
                elif path=='/api/calculator/instrument':
                    query['estimate_razor_from_standard']=query.get('estimate_razor_from_standard')=='true'
                    value=json.loads(asyncio.run(service.calculator_instrument(**query)).body)
                elif path=='/api/calculator/commission-preview':
                    value=json.loads(asyncio.run(service.calculator_commission_preview(service.Request({"type":"http","scheme":"http","path":path,"headers":[],"server":self.server.server_address}), payload)).body)
                elif path=='/api/calculator/quote':
                    import faulthandler
                    started=time.monotonic();print('FIXTURE_QUOTE_START',json.dumps(payload))
                    watchdog=threading.Timer(8,lambda:faulthandler.dump_traceback(file=sys.stdout,all_threads=True));watchdog.start()
                    try:value=json.loads(asyncio.run(service.calculator_quote(payload)).body)
                    finally:watchdog.cancel();watchdog.join(timeout=2)
                    print('FIXTURE_QUOTE_DONE',time.monotonic()-started,value.get('timings_ms'))
                elif path=='/api/calculator/pepperstone-set/local' and local_preset_save:
                    request=service.Request(dict(type='http', scheme='http', path=path,
                        headers=[(k.lower().encode(),v.encode()) for k,v in self.headers.items()],
                        client=self.client_address, server=self.server.server_address))
                    try:
                        value=json.loads(service.calculator_pepperstone_set_local(request,payload).body)
                    except service.HTTPException as exc:
                        raw=json.dumps(dict(detail=exc.detail)).encode()
                        self.send_response(exc.status_code);self.send_header('Content-Length',str(len(raw)))
                        self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
                        return
                elif path=='/api/calculator/pepperstone-set':
                    # Durable local row must exist BEFORE any download request.
                    rows=service._pepperstone_local_tracker_rows()
                    assert sum(row['calculation_context_id']==payload['calculation_context_id'] for row in rows)==1
                    value,filename=service._build_pepperstone_trader_set(payload)
                    actual_dir = download_dir if market_downloads is None else download_dir/(payload['symbol'].replace('_','')+'-'+payload['pepperstone_account_type'])
                    sys.audit('ltt.browser_output',str(actual_dir/filename))
                    downloads.append((value,filename));mime='text/plain';headers['Content-Disposition']='attachment; filename="'+filename+'"'
                elif path=='/api/open-orders/classification':value=asyncio.run(service.save_open_order_classification(payload))
                elif path=='/api/open-orders/local-tracking/fill' and limit_fill:
                    if reject_fill.is_set():
                        raw=b'{"detail":"Fixture storage failure"}'
                        self.send_response(500);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                        return
                    value=json.loads(asyncio.run(service.record_pepperstone_local_fill(payload)).body)
                    if hold_fill_ack.is_set():
                        assert release_fill_ack.wait(10), 'Fixture fill acknowledgement was not released'
                elif path.startswith('/api/open-orders'):
                    rows=service._pepperstone_local_tracker_rows()
                    revision=sum(r['classification_version']+r['manual_fill_revision'] for r in rows)
                    value=dict(items=rows,errors=[],warnings=[],version=1,progress_revision=revision,
                               core_complete=True,empty_confirmed=not rows)
                else:value={}
                raw=value.encode('utf-8') if isinstance(value,str) else json.dumps(value).encode('utf-8')
                if path=='/api/calculator/quote':print('FIXTURE_HTTP_RESPONSE_READY',time.monotonic()-received,len(raw),type(value).__name__)
                self.send_response(200)
                for key,val in headers.items():self.send_header(key,val)
                self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.close_connection=True;self.end_headers();self.wfile.write(raw);self.wfile.flush()
            except Exception as exc:
                print('FIXTURE_HTTP_ERROR',path,time.monotonic()-received,repr(exc))
                if isinstance(exc,ConnectionError) and path in {'/api/calculator/instrument','/api/calculator/commission-preview'}:
                    cancelled_reads.append(repr(exc))  # Cancelled read; any quote/save/export error still fails.
                else:failures.append(repr(exc))
                if not isinstance(exc,ConnectionError):
                    raw=json.dumps(dict(detail=str(exc))).encode();self.send_response(500);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            finally:
                if body_watchdog is not None:body_watchdog.cancel();body_watchdog.join(timeout=2)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    process=None;ws=None
    profile=tmp_path/'edge-profile';profile.mkdir();download_dir=tmp_path/'downloads';download_dir.mkdir()
    binary=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe');assert binary.is_file()
    with (tmp_path/'edge.log').open('wb') as log:
        try:
            process=subprocess.Popen([str(binary),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
                '--disable-background-networking','--disable-component-update','--disable-sync','--remote-debugging-port=0',
                '--user-data-dir='+str(profile),'about:blank'],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+30
            while True:
                # Edge can create the file before releasing its exclusive write handle.
                try:
                    port=int((profile/'DevToolsActivePort').read_text().partition('\n')[0])
                    break
                except (FileNotFoundError, PermissionError, ValueError):
                    assert process.poll() is None and time.monotonic()<deadline, 'Private Edge DevTools port unavailable'
                    time.sleep(.05)
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:target=next(t for t in json.load(response) if t['type']=='page')
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=20,suppress_origin=True);sequence=0
            def call(method,params=None):
                nonlocal sequence
                sequence+=1;ws.send(json.dumps(dict(id=sequence,method=method,params=params or {})))
                while True:
                    value=json.loads(ws.recv())
                    if value.get('method')=='Network.requestWillBeSent' and '/api/calculator/quote' in value['params']['request']['url']:
                        print('BROWSER_QUOTE_REQUEST_WALL_TIME',value['params'].get('wallTime'))
                    if value.get('id')==sequence:
                        assert 'error' not in value,value
                        return value.get('result',{})
            def evaluate(script):
                value=call('Runtime.evaluate',dict(expression=script,awaitPromise=True,returnByValue=True))
                assert 'exceptionDetails' not in value,value
                return value.get('result',{}).get('value')
            def wait_for(script):
                deadline=time.monotonic()+20
                while not evaluate(script):
                    if time.monotonic()>=deadline:
                        observed=dict(waiting=script,events=events,failures=failures,page=evaluate("({error:document.getElementById('calc-error')?.textContent,status:document.getElementById('calc-quote-status')?.textContent,canonical:document.getElementById('calc-canonical-symbol')?.textContent,source:document.getElementById('calc-results')?.textContent,active:[...document.querySelectorAll('.toggle .active')].map(e=>[e.parentElement.id,e.dataset.v])})"))
                        (tmp_path/'native-failure.json').write_text(json.dumps(observed,indent=2),encoding='utf-8')
                        print('NATIVE_PAGE_FAILURE',json.dumps(observed))
                        raise AssertionError(script)
                    time.sleep(.1)
            origin=f'http://127.0.0.1:{server.server_port}'
            call('Network.enable')
            def click(id):
                rect=evaluate("(()=>{const e=document.getElementById("+json.dumps(id)+");e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                call('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**rect))
                call('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**rect))
            call('Page.setDownloadBehavior',dict(behavior='allow',downloadPath=str(download_dir)))
            if limit_fill:
                call('Page.navigate',dict(url=origin+'/orders'))
                wait_for("window.__openOrdersCoverage?.empty_confirmed===true && window.__openOrdersItems?.length===0")
            call('Page.navigate',dict(url=origin+'/calculator'));wait_for("!!document.querySelector('#setup-toggle button')")
            if local_preset_save is not None:
                local_preset_save(evaluate, click, wait_for, download_dir)
                assert not failures, failures
                assert all(path in {'/api/calculator/quote', '/api/calculator/pepperstone-set/local',
                                    '/api/calculator/prewarm-account'} for method,path in events if method=='POST')
                return
            if market_downloads is not None:
                market_downloads(call, evaluate, click, wait_for, downloads, download_dir)
                assert not failures, failures
                assert all(path in {'/api/calculator/quote', '/api/calculator/pepperstone-set',
                                    '/api/calculator/prewarm-account'} for method,path in events if method=='POST')
                return
            evaluate("""(()=>{
                const click=(id,v)=>document.querySelector('#'+id+' [data-v="'+v+'"]').click();
                click('asset-toggle','fx');click('broker-toggle','pepperstone');click('account-toggle','demo');click('pepperstone-account-type-toggle','standard');click('risk-toggle','fixed_aud');
                for(const [id,val] of Object.entries({'calc-symbol':'USD_JPY','calc-risk':'10','calc-sl-ticks':'108','calc-rr':'2.5'})){
                    const el=document.getElementById(id);el.value=val;el.dispatchEvent(new Event('input',{bubbles:true}));}
            })()""")
            if limit_fill:
                evaluate("""(()=>{
                    document.querySelector('#order-toggle [data-v="limit"]').click();
                    for(const [id,val] of Object.entries({'calc-limit':'159.360','calc-sl-ticks':'60'})){
                        const el=document.getElementById(id);el.value=val;el.dispatchEvent(new Event('input',{bubbles:true}));}
                    document.querySelector('#timeframe-toggle [data-v="5m"]').click();
                    document.querySelector('#setup-toggle [data-v="Pullback"]').click();
                })()""")
            wait_for("document.getElementById('calc-canonical-symbol').textContent.includes('USD_JPY')")
            if commission_preview:
                evaluate("""(() => {
                    document.querySelector('#pepperstone-account-type-toggle [data-v="razor"]').click();
                    document.getElementById('calc-rr').focus();
                })()""")
                for event_type in ("rawKeyDown", "keyUp"):
                    call("Input.dispatchKeyEvent", dict(type=event_type, key="Tab", code="Tab", windowsVirtualKeyCode=9))
                wait_for("!document.getElementById('calc-commission-chart-r').hidden")
                hint = evaluate("document.getElementById('calc-commission-chart-r').textContent")
                assert service._pepperstone_local_tracker_rows() == []
                assert not service._PEPPERSTONE_QUOTE_SNAPSHOTS
                assert evaluate("document.getElementById('calc-pepperstone-set').disabled")
                assert ('POST','/api/calculator/quote') not in events
                click('calc-quote')
                wait_for("document.getElementById('calc-pepperstone-set').disabled===false")
                assert evaluate("document.getElementById('calc-commission-chart-r').textContent") == hint
                assert evaluate("!document.getElementById('calc-results-disclosure').open")
                assert len(service._pepperstone_local_tracker_rows()) == 1
                assert not failures and not downloads
                print("Native Edge Tab and Calculate:", hint.encode("ascii", "backslashreplace").decode())
                return
            click('calc-quote')
            wait_for("document.getElementById('calc-pepperstone-set').disabled===false")
            rows=service._pepperstone_local_tracker_rows();assert len(rows)==1 and not rows[0]['execution_verified'];identity=rows[0]['id']
            assert downloads==[]
            assert evaluate("document.getElementById('calc-results').textContent.includes('2026-11-20T21:59:58Z')")
            if limit_fill:
                _exercise_limit_fill_editor(call, evaluate, wait_for, origin, service, identity,
                                            hold_fill_ack, release_fill_ack, reject_fill)
                assert not failures,failures
                assert pepperstone_weekend_reference[1]==[]
                assert [path for method,path in events if method=='POST' and not path.endswith('/prewarm-account')]==[
                    '/api/calculator/quote','/api/open-orders/local-tracking/fill',
                    '/api/open-orders/classification','/api/open-orders/local-tracking/fill',
                    '/api/open-orders/local-tracking/fill']
                print('NATIVE_LIMIT_FILL', json.dumps(dict(row_id=identity, requests=events)))
                return
            click('calc-pepperstone-set')
            wait_for("document.getElementById('calc-success').textContent.includes('.set downloaded')")
            assert len(downloads)==1 and 'UseDesktopTraderControls=true' in downloads[0][0]
            downloaded=download_dir/downloads[0][1];deadline=time.monotonic()+15
            while not downloaded.is_file():
                assert time.monotonic()<deadline,'Native .set download did not complete'
                time.sleep(.05)
            assert downloaded.read_bytes()==downloads[0][0].encode('utf-8')
            call('Page.navigate',dict(url=origin+'/orders'))
            wait_for("document.querySelectorAll('#open-orders-table tbody tr').length===1")
            evaluate("""(()=>{const headers=[...document.querySelectorAll('thead th')].map(e=>e.textContent.trim());const td=document.querySelector('#open-orders-table tbody tr').children[headers.indexOf('Setup')];td.querySelector('button').click();const s=td.querySelector('select');s.value='Breakout';s.dispatchEvent(new Event('change'));[...td.querySelectorAll('button')].find(b=>b.textContent==='Save').click();})()""")
            wait_for("document.querySelector('#open-orders-table tbody').textContent.includes('Saved')")
            call('Page.reload');wait_for("document.querySelector('#open-orders-table tbody').textContent.includes('Breakout')")
            service._TRADE_CONTEXTS_CACHE.clear();rows=service._pepperstone_local_tracker_rows()
            assert len(rows)==1 and rows[0]['id']==identity and rows[0]['classification_values']['setup']=='Breakout'
            assert not failures,failures
            assert not any(path.endswith('/submit') for method,path in events)
            assert pepperstone_weekend_reference[1]==[]
            print('NATIVE_PAGE_REAL_FIXTURE_HANDLERS',json.dumps(dict(row_id=identity,requests=events,preset=downloads[0][1],download_bytes=downloaded.stat().st_size,cancelled_instrument_reads=cancelled_reads,quote_time='2026-11-20T21:59:58Z')))
        finally:
            release_fill_ack.set()
            if ws is not None:
                try:ws.send(json.dumps(dict(id=999999,method='Browser.close')))
                finally:ws.close()
            if process is not None:
                code=process.wait(timeout=20);print('PRIVATE_EDGE_ACTUAL_EXIT',code);assert code==0
            server.shutdown();server.server_close();worker.join(timeout=10);assert not worker.is_alive()


def _exercise_limit_fill_editor(call, evaluate, wait_for, origin, service, identity,
                                hold_fill_ack, release_fill_ack, reject_fill):
    from decimal import Decimal as D
    call('Page.navigate',dict(url=origin+'/orders'))
    row_selector = '#open-orders-table tbody tr'
    wait_for("document.querySelectorAll('#open-orders-table tbody tr').length===1")
    def cell(column):
        return ("document.querySelector("+json.dumps(row_selector)+").children["
                "[...document.querySelectorAll('#open-orders-table thead th')].findIndex("
                f"e=>e.textContent.trim()==={json.dumps(column)})]")
    entry=cell('Entry / Order')
    def press(selector,label):
        evaluate(f"[...({selector}).querySelectorAll('button')].find(b=>b.textContent==={json.dumps(label)}).click()")
    def type_fill(value):
        evaluate(f"(()=>{{const input=({entry}).querySelector('input');input.value={json.dumps(value)};"
                 "input.dispatchEvent(new Event('input',{bubbles:true}));})()")
    def assert_prices(price):
        assert D(evaluate(f"({cell('Planned Entry')}).textContent"))==D('159.360')
        assert D(evaluate(f"({cell('Planned Stop')}).textContent"))==D('159.300')
        assert D(evaluate(f"({cell('Planned Target')}).textContent"))==D('159.510')
        visible = evaluate(f"({entry}).querySelector('span').textContent")
        actual_value, actual_label = visible.split(' ', 1)
        expected_value, expected_label = price.split(' ', 1)
        assert (D(actual_value), actual_label) == (D(expected_value), expected_label), visible
        assert evaluate(f"({cell('Timeframe')}).textContent.includes('5MIN')")
        assert evaluate(f"({cell('Setup')}).textContent.includes('Pullback')")
    assert_prices('159.360 (planned)')
    press(entry,'Record MT5 fill');type_fill('159.357');press(entry,'Discard draft')
    assert_prices('159.360 (planned)')
    # Let the fill commit, then edit EMA before the older fill reply reaches the page.
    hold_fill_ack.set()
    press(entry,'Record MT5 fill');type_fill('159.358');press(entry,'Save MT5 fill')
    try:
        deadline=time.monotonic()+5
        while service._pepperstone_local_tracker_rows()[0]['manual_fill_revision']!=1:
            assert time.monotonic()<deadline
            time.sleep(.03)
        ema=cell('EMA')
        evaluate(f"(()=>{{const td={ema};td.querySelector('button').click();const s=td.querySelector('select');"
                 "s.value='20';s.dispatchEvent(new Event('change'));[...td.querySelectorAll('button')].find(b=>b.textContent==='Save').click();})()")
        wait_for(f"({ema}).textContent.includes('Saved')")
    finally:
        hold_fill_ack.clear()
        release_fill_ack.set()
    wait_for(f"({entry}).textContent.includes('Correct MT5 fill')")
    assert_prices('159.358 (manual MT5 fill)')
    assert evaluate(f"({ema}).textContent.includes('20')")
    press(entry,'Correct MT5 fill');type_fill('159.357')
    reject_fill.set()
    press(entry,'Save MT5 fill')
    wait_for(f"({entry}).textContent.includes('Fixture storage failure')")
    assert service._pepperstone_local_tracker_rows()[0]['manual_fill_price']=='159.358'
    reject_fill.clear()
    press(entry,'Save MT5 fill')
    wait_for(f"({entry}).textContent.includes('159.357 (manual MT5 fill)')")
    evaluate("document.getElementById('refresh-btn').click()")
    wait_for("document.getElementById('open-orders-status').textContent==='Updated'")
    wait_for(f"({entry}).textContent.includes('159.357 (manual MT5 fill)')")
    call('Page.reload')
    wait_for(f"!!document.querySelector({json.dumps(row_selector)}) && ({entry}).textContent.includes('159.357 (manual MT5 fill)')")
    assert_prices('159.357 (manual MT5 fill)')
    service._TRADE_CONTEXTS_CACHE.clear()
    rows=service._pepperstone_local_tracker_rows()
    assert len(rows)==1 and rows[0]['id']==identity
    row=rows[0]
    assert row['manual_fill_revision']==2 and not row['execution_verified']
    assert row['classification_values']['ema']=='20' and row['risk_value']=='10' and row['risk_reward']=='2.5'
    assert (D(row['stop_loss']),D(row['take_profit']))==(D('159.300'),D('159.510'))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "multipart" not in sys.modules:
    multipart_mod = types.ModuleType("multipart")
    multipart_mod.__spec__ = importlib.machinery.ModuleSpec("multipart", loader=None)
    multipart_mod.__version__ = "0.0-test"
    multipart_sub = types.ModuleType("multipart.multipart")
    multipart_sub.__spec__ = importlib.machinery.ModuleSpec("multipart.multipart", loader=None)
    multipart_sub.parse_options_header = lambda value: (value, {})
    sys.modules["multipart"] = multipart_mod
    sys.modules["multipart.multipart"] = multipart_sub
try:
    import requests as _rq  # noqa: F401
    if not hasattr(_rq, "adapters"):
        raise ImportError
except Exception:
    req = types.ModuleType("requests")
    req.__spec__ = importlib.machinery.ModuleSpec("requests", loader=None)
    adapters = types.ModuleType("requests.adapters")
    adapters.__spec__ = importlib.machinery.ModuleSpec("requests.adapters", loader=None)
    adapters.HTTPAdapter = object
    req.adapters = adapters
    sys.modules["requests"] = req
    sys.modules["requests.adapters"] = adapters
try:
    from urllib3.util.retry import Retry  # noqa: F401
except Exception:
    urllib3 = types.ModuleType("urllib3")
    urllib3.__spec__ = importlib.machinery.ModuleSpec("urllib3", loader=None)
    util = types.ModuleType("urllib3.util")
    util.__spec__ = importlib.machinery.ModuleSpec("urllib3.util", loader=None)
    retry = types.ModuleType("urllib3.util.retry")
    retry.__spec__ = importlib.machinery.ModuleSpec("urllib3.util.retry", loader=None)
    retry.Retry = object
    sys.modules["urllib3"] = urllib3
    sys.modules["urllib3.util"] = util
    sys.modules["urllib3.util.retry"] = retry


def _write_pepperstone_point_export(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "pepperstone_spreads_fixture.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "broker": "pepperstone",
                "generated_at": "2026-10-03T00:00:00Z",
                "symbol_count": len(entries),
                "symbols": entries,
            }
        ),
        encoding="utf-8",
    )
    return path


def _pepperstone_point_entry(symbol: str, mt5_symbol: str, point: str) -> dict:
    return {
        "symbol": symbol,
        "mt5_symbol": mt5_symbol,
        "available": True,
        "point": point,
        "digits": 5,
        "timestamp": "2026-10-03T00:00:00Z",
    }


def _mock_pepperstone_quote_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls = {"account_summary": 0, "pricing": 0}
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _account: {"base_url": "https://oanda.test", "account_id": "acct", "token": "test-token"})

    async def fake_meta(**kwargs):
        precision = 3 if str(kwargs.get("symbol") or "").endswith("_JPY") else 5
        return {"displayPrecision": precision, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)

    async def fake_pricing(**_kwargs):
        calls["pricing"] += 1
        return {
            "prices": [{"bids": [{"price": "1.30000"}], "asks": [{"price": "1.30020"}]}],
            "homeConversions": [
                {"currency": "USD", "accountGain": "1.5", "accountLoss": "1.5", "positionValue": "1.5"},
                {"currency": "JPY", "accountGain": "0.01", "accountLoss": "0.01", "positionValue": "0.01"},
            ],
        }

    async def forbidden_account_summary(*_args, **_kwargs):
        calls["account_summary"] += 1
        raise AssertionError("Pepperstone quotes must not read OANDA balance, NAV, or margin data")

    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_pricing)
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", forbidden_account_summary)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda payload, **_kwargs: payload)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)
    return calls


def _pepperstone_quote_payload(**overrides: object) -> dict:
    payload: dict = {
        "asset": "fx",
        "broker": "pepperstone",
        "account": "demo",
        "symbol": "GBPUSD",
        "side": "buy",
        "order_type": "market",
        "risk_mode": "fixed_aud",
        "risk_value": "10",
        "stop_loss_ticks": "43",
        "target_mode": "rr",
        "risk_reward": "2",
        "webhook": "no",
    }
    payload.update(overrides)
    return payload


def _pepperstone_set_payload_from_quote(request_payload: dict, quote_body: dict) -> dict:
    payload = {
        "asset": "fx",
        "broker": "pepperstone",
        "pepperstone_account_type": quote_body.get("pepperstone_account_type", "razor"),
        "account": request_payload.get("account", "demo"),
        "symbol": quote_body["symbol"],
        "side": request_payload["side"],
        "order_type": request_payload["order_type"],
        "stop_loss_ticks": request_payload["stop_loss_ticks"],
        "target_mode": quote_body["target_mode"],
        "chart_tick_size_override": request_payload.get("chart_tick_size", ""),
        "quoted_chart_tick_size": quote_body["chart_tick_size"],
        "quoted_chart_tick_profile_id": quote_body.get("chart_tick_profile_id", ""),
        "calculation_context_id": quote_body["calculation_context_id"],
        "risk_mode": request_payload["risk_mode"],
        "risk_value": request_payload["risk_value"],
        "estimated_total_loss_aud": quote_body["estimated_total_loss_aud"],
        "estimated_total_loss": quote_body["estimated_total_loss"],
    }
    if quote_body["target_mode"] == "rr":
        payload["risk_reward"] = request_payload["risk_reward"]
    else:
        payload["take_profit_ticks"] = request_payload["take_profit_ticks"]
    if request_payload["order_type"] == "limit":
        payload["entry_price"] = quote_body["entry_price"]
    return payload
HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
if not HTTPX_AVAILABLE:
    class _HttpxResponse:
        pass
    class _HttpxAsyncClient:
        pass
    httpx_stub = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=_HttpxAsyncClient,
        Response=_HttpxResponse,
        BaseTransport=object,
        TimeoutException=Exception,
        RequestError=Exception,
        HTTPStatusError=Exception,
        ConnectError=Exception,
    )
    httpx_stub.__spec__ = importlib.machinery.ModuleSpec("httpx", loader=None)
    sys.modules["httpx"] = httpx_stub

SPEC = importlib.util.spec_from_file_location("render_master_service_calculator_api", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_pepperstone_ready_quote_persists_local_tracker_intent(monkeypatch, tmp_path):
    import threading
    service = master_service
    path = tmp_path / "contexts.json"
    real_upsert, real_invalidate = service._upsert_calculator_trade_context, service._invalidate_open_orders_cache
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(service, "_upsert_calculator_trade_context", real_upsert)
    monkeypatch.setattr(service, "_invalidate_open_orders_cache", real_invalidate)
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", path)
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_LOCK", threading.RLock())
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 10})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_PEPPERSTONE_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(tmp_path / "missing.json"))
    async def forbidden(*args, **kwargs):
        raise AssertionError("Tracking must not download or execute")
    monkeypatch.setattr(service, "calculator_pepperstone_set", forbidden)
    monkeypatch.setattr(service.httpx.AsyncClient, "request", forbidden)

    async def scenario():
        async def quote(**changes):
            request = _pepperstone_quote_payload(target_mode="ticks", take_profit_ticks="25",
                timeframe="5m", test_trade=True, setup="Pullback", pattern="range", ema="9",
                vwap="Yes", aths_atls="All-Time High", round_number="Yes")
            request.update(changes)
            return json.loads((await service.calculator_quote(request)).body)
        limit = await quote(calculation_context_id="same-id", order_type="limit", entry_price="1.3")
        assert limit["pepperstone_mt5_export_status"]["file_ready"] is True
        assert limit["pepperstone_mt5_export_status"]["broker_check_state"] == "pending"
        assert limit["calculation_context_saved"] is True
        disk = json.loads(path.read_text())["items"]
        assert len(disk) == 1 and disk[0]["local_tracking"] is True  # Before any download.
        row = disk[0]
        assert row["planned_entry_price"] == limit["entry_price"]
        assert row["planned_stop_price"] == limit["stop_price"]
        assert row["planned_target_price"] == limit["target_price"]
        for key in ("risk_mode", "risk_value", "target_mode", "stop_loss_ticks", "take_profit_ticks",
                    "timeframe", "is_test_trade", "setup", "pattern", "ema", "vwap", "aths_atls", "round_number"):
            assert row.get(key) is not None, key
        assert row["chart_tick_size"] == row["calculator_distance_tick_size"] == "0.00001"
        assert Decimal(row["original_stop_distance"]) == abs(Decimal(limit["entry_price"]) - Decimal(limit["stop_price"]))
        assert Decimal(row["original_target_distance"]) == abs(Decimal(limit["target_price"]) - Decimal(limit["entry_price"]))
        await quote(calculation_context_id="same-id", order_type="limit", entry_price="1.3")
        market = await quote(calculation_context_id="same-id", account="live", order_type="market")
        await quote(calculation_context_id="distinct", order_type="market")
        disk = json.loads(path.read_text())["items"]
        assert len(disk) == 3
        actual = next(item for item in disk if item["account"] == "live")
        assert actual["execution_verified"] is False
        assert actual["execution_status"] == "Awaiting MT5 fill price"
        assert not actual.get("execution_price")
        assert actual["planned_entry_price"] == market["entry_price"]
        service._PEPPERSTONE_QUOTE_SNAPSHOTS.clear()
        service._TRADE_CONTEXTS_CACHE.clear()
        assert len(service._pepperstone_local_tracker_rows()) == 3
        assert service._OPEN_ORDERS_CACHE["version"] > 10
        before = path.read_bytes()
        with pytest.raises(service.HTTPException):
            await quote(calculation_context_id="invalid", stop_loss_ticks="0")
        assert path.read_bytes() == before
        export = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("GBPUSD", "GBPUSD.a", "0.00001")])
        monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export))
        blocked = await quote(calculation_context_id="ineligible", stop_loss_ticks="1")
        assert blocked["pepperstone_mt5_export_status"]["file_ready"] is False
        assert len(service._pepperstone_local_tracker_rows()) == 3
        # A durable write failure must propagate instead of claiming success.
        def failed_save(*args, **kwargs):
            raise OSError("fixture disk failure")
        monkeypatch.setattr(service, "_save_trade_contexts", failed_save)
        with pytest.raises(service.HTTPException) as error:
            await quote(calculation_context_id="save-failed", stop_loss_ticks="100")
        assert error.value.status_code == 502
        assert error.value.detail["code"] == "CALC_CONTEXT_SAVE_FAILED"
        assert not any(item["calculation_context_id"] == "save-failed" for item in json.loads(path.read_text())["items"])
    asyncio.run(asyncio.wait_for(scenario(), 15))



def _clear_calculator_caches() -> None:
    for name in [
        "_BYBIT_SYMBOL_LIST_CACHE",
        "_BYBIT_INSTRUMENT_CACHE",
        "_BYBIT_TICKER_CACHE",
        "_BYBIT_TICKER_INFLIGHT",
        "_BYBIT_WALLET_BALANCE_CACHE",
        "_BYBIT_WALLET_BALANCE_INFLIGHT",
        "_OANDA_AUD_USD_CACHE",
    ]:
        obj = getattr(master_service, name, None)
        if isinstance(obj, dict):
            obj.clear()


@pytest.fixture(autouse=True)
def _reset_calculator_caches_between_tests():
    for name in [
        "PUBLIC_WEBHOOK_BASE_URL",
        "RENDER_CALCULATOR_BASE_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
        "ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS",
    ]:
        master_service.os.environ.pop(name, None)
    _clear_calculator_caches()
    yield
    for name in [
        "PUBLIC_WEBHOOK_BASE_URL",
        "RENDER_CALCULATOR_BASE_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
        "ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS",
    ]:
        master_service.os.environ.pop(name, None)
    _clear_calculator_caches()



def test_scripts_page_contains_calculator_row() -> None:
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    calc = next((row for row in payload if row.get("name") == "calculator"), None)
    assert calc is not None
    assert calc["open_url"] == "/merged/calculator"


def test_scripts_page_marks_merged_dashboard_views_non_standalone() -> None:
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    merged_names = {"calculator", "monitor"}
    merged_rows = [row for row in payload if row.get("name") in merged_names]
    assert len(merged_rows) == len(merged_names)
    for row in merged_rows:
        assert row.get("standalone") is False
        assert row.get("dashboard_main_view") is True
    assert all(row.get("name") != "history" for row in payload)
    home = asyncio.run(master_service.home_page()).body.decode("utf-8")
    assert 'id="dashboard-history-panel"' in home
    history_page = asyncio.run(master_service.merged_history_page())
    assert history_page.status_code == 200
    assert "history-export" in history_page.body.decode("utf-8")


def test_render_env_hides_local_only_scanner_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    original_manager = master_service.script_manager
    try:
        master_service.script_manager = master_service.ScriptManager(master_service.discover_scripts())
        response = asyncio.run(master_service.list_scripts())
        payload = json.loads(response.body.decode("utf-8"))
        names = {str(row.get("name")) for row in payload}
        assert "bybit_monitor" not in names
        assert "oanda_monitor" not in names
    finally:
        master_service.script_manager = original_manager


def test_scanner_merged_routes_return_gone_on_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    monitor = asyncio.run(master_service.merged_monitor_page())
    legacy_scanner = asyncio.run(master_service.merged_scanner_redirect())
    assert monitor.status_code == 410
    assert legacy_scanner.status_code == 410
    message = "Alerts are local-only. Run run_local_master_control.bat on your PC."
    assert monitor.body.decode("utf-8") == message
    assert legacy_scanner.body.decode("utf-8") == message


def test_scanner_merged_routes_work_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    monitor = asyncio.run(master_service.merged_monitor_page())
    alerts_alias = asyncio.run(master_service.merged_monitor_page())
    legacy_scanner = asyncio.run(master_service.merged_scanner_redirect())
    assert monitor.status_code == 200
    assert alerts_alias.status_code == 200
    assert legacy_scanner.status_code == 307
    assert legacy_scanner.headers.get("location") == "/merged/monitor"
    html = monitor.body.decode("utf-8")
    assert "Monitor controls" in html
    assert 'id="monitor-target"' in html
    assert 'id="monitor-status" class="badge">Checking…</span>' in html
    assert "/static/merged_alerts.js?v=" in html
    assert 'id="bybit-start-btn"' not in html
    assert 'id="oanda-start-btn"' not in html
    assert 'id="bybit-log-box"' not in html
    assert 'id="oanda-log-box"' not in html


def test_atr_scanner_is_removed_without_affecting_alert_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    dashboard_names = {
        str(item.get("name")) for item in master_service._profile_main_buttons()
    }
    assert "atr-scanner" not in dashboard_names
    assert "monitor" in dashboard_names

    route_paths = {str(route.path) for route in master_service.app.routes}
    assert "/merged/atr-scanner" not in route_paths
    assert not any(path.startswith("/api/atr-scanner") for path in route_paths)
    assert "/merged/monitor" in route_paths
    assert "/merged/alerts" in route_paths
    assert "/merged/scanner" in route_paths

    removed_paths = (
        "render/atr_scanner.py",
        "render/static/atr_scanner.js",
        "run_scanner_local.bat",
        "tests/test_atr_scanner.py",
        "tests/test_atr_scanner_ui.py",
    )
    assert all(not (ROOT / path).exists() for path in removed_paths)
    assert all(path not in master_service.LOCAL_BUILD_FILES for path in removed_paths)
    watcher = (ROOT / "tools" / "windows_launchers" / "ensure_local_master_server.ps1").read_text(
        encoding="utf-8"
    )
    assert all(path not in watcher for path in removed_paths)

    service_source = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    for removed_symbol in (
        "render.atr_scanner",
        "ATRScannerService",
        "ATR_SCANNER_SERVICE",
        "ATR_SCANNER_SETTINGS_PATH",
        "SCANNER_LOCAL_UI_MODE",
        "_is_scanner_local_ui_mode",
    ):
        assert removed_symbol not in service_source

    monkeypatch.setattr(master_service, "_runtime_is_render", lambda: False)
    legacy = asyncio.run(master_service.merged_scanner_redirect())
    assert legacy.status_code == 307
    assert legacy.headers.get("location") == "/merged/monitor"


def test_scripts_page_local_scanner_merged_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    names = [str(row.get("name")) for row in payload]
    assert "monitor" in names
    assert "bybit_monitor" not in names
    assert "oanda_monitor" not in names


def test_scripts_page_render_has_no_scanner_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    original_manager = master_service.script_manager
    try:
        master_service.script_manager = master_service.ScriptManager(master_service.discover_scripts())
        response = asyncio.run(master_service.list_scripts())
        payload = json.loads(response.body.decode("utf-8"))
        names = {str(row.get("name")) for row in payload}
        assert "monitor" not in names
        assert "bybit_monitor" not in names
        assert "oanda_monitor" not in names
    finally:
        master_service.script_manager = original_manager


def test_merged_calculator_page_returns_200() -> None:
    response = asyncio.run(master_service.merged_calculator_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Position Size Calculator" in html
    assert "max-width:880px" not in html
    assert 'target-toggle' not in html
    assert 'tp-ticks-wrap' not in html
    assert 'id="calc-rr"' in html
    assert "Type a symbol to load instrument specs." not in html
    assert "calc-grid" in html
    assert "settings-grid" not in html
    assert "calc-table" in html
    assert "Quote Results" in html
    assert html.count('<table class="calc-table">') >= 4
    assert '/static/calculator.js?v=' in html
    import re
    assert re.search(r"/static/calculator\.js\?v=[a-f0-9]{12}", html)
    assert 'id="calc-timeframe"' not in html
    assert 'id="timeframe-toggle"' in html
    assert 'id="test-toggle"' in html
    assert 'id="calc-instrument-specs"' not in html
    assert 'id="calc-journal-summary"' not in html
    assert 'class="card" id="calc-instrument-specs"' not in html
    assert html.find('id="account-toggle"') < html.find('id="asset-toggle"') < html.find('id="side-toggle"')
    assert html.find('id="order-toggle"') < html.find('id="calc-symbol"') < html.find('id="calc-sl-ticks"')
    assert html.find('id="timeframe-toggle"') < html.find('id="setup-toggle"') < html.find('id="pattern-toggle"')
    assert html.find('id="ema-toggle"') < html.find('id="vwap-toggle"') < html.find('id="aths-atls-toggle"')
    def table_block(caption: str) -> str:
        start = html.index(f"<caption>{caption}</caption>")
        return html[start:html.index("</table>", start)]
    risk_block = table_block("Risk")
    classification_block = table_block("Trade classification")
    assert 'id="webhook-toggle"' not in risk_block
    assert 'id="calc-webhook-status"' not in risk_block
    assert 'id="test-toggle"' not in risk_block
    assert 'id="webhook-toggle"' in classification_block
    assert 'id="calc-webhook-status"' in classification_block
    assert 'id="test-toggle"' in classification_block
    assert classification_block.find('id="round-number-toggle"') < classification_block.find('id="webhook-toggle"') < classification_block.find('id="test-toggle"')
    assert html.count('id="webhook-toggle"') == 1
    assert html.count('id="calc-webhook-status"') == 1
    assert html.count('id="test-toggle"') == 1
    assert html.find('<caption>Risk</caption>') < html.find('<caption>Trade classification</caption>') < html.find('id="calc-quote"')
    assert html.find('id="calc-quote"') < html.find('id="calc-results"')
    assert html.find('id="calc-results"') < html.find('id="calc-webhook-panel"')
    assert "calc-right-rail" not in html
    assert 'id="calc-webhook-url"' in html
    assert 'id="calc-webhook-copy-url"' in html


def test_local_trendline_panel_describes_current_execution_behavior() -> None:
    response = asyncio.run(master_service.merged_calculator_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    panel_start = html.index('id="trendline-plans-panel"')
    panel = html[panel_start:html.index("</section>", panel_start)]
    for guidance in (
        "Armed Bybit and OANDA plans are monitored locally only after you start monitoring.",
        "Keep Local Trading Tools running and the computer awake.",
        "Test plans simulate without sending broker orders; eligible non-test plans can submit once after fresh validation, and live plans require explicit live confirmation.",
        "TradingView drawings are not imported automatically: copy both anchor times and prices manually.",
        "Pepperstone trendline execution remains handled by the existing MT5 trendline EA.",
    ):
        assert guidance in panel
    assert "Stored-plan foundation only" not in panel
    assert "Plans can be stored and armed, but are not yet monitored or executed." not in panel
    assert 'id="trendline-monitor-start"' in panel
    assert 'id="trendline-monitor-stop"' in panel


def test_merged_calculator_page_has_unique_expected_element_ids() -> None:
    response = asyncio.run(master_service.merged_calculator_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    expected_ids = [
        "account-toggle",
        "asset-toggle",
        "broker-toggle-wrap",
        "broker-toggle",
        "side-toggle",
        "order-toggle",
        "calc-symbol",
        "calc-canonical-symbol",
        "limit-wrap",
        "calc-limit",
        "calc-sl-ticks",
        "rr-wrap",
        "calc-rr",
        "risk-toggle-wrap",
        "risk-toggle",
        "calc-risk-label",
        "calc-risk",
        "webhook-toggle",
        "calc-webhook-status",
        "test-toggle",
        "timeframe-toggle",
        "setup-toggle",
        "pattern-toggle",
        "ema-toggle",
        "vwap-toggle",
        "aths-atls-toggle",
        "round-number-toggle",
        "calc-quote",
        "calc-submit",
        "calc-pepperstone-set",
        "calc-quote-status",
        "calc-error",
        "calc-error-debug",
        "calc-success",
        "calc-request-summary",
        "calc-results",
        "calc-webhook-panel",
        "calc-webhook-url",
        "calc-webhook-copy-url",
        "calc-webhook-json",
        "calc-webhook-copy",
    ]
    rendered_ids = re.findall(r'\bid="([^"]+)"', html)
    for element_id in expected_ids:
        assert rendered_ids.count(element_id) == 1, element_id
    calculator_ids = [
        element_id
        for element_id in rendered_ids
        if element_id.startswith("calc-") or element_id.endswith("-toggle") or element_id.endswith("-wrap")
    ]
    assert len(calculator_ids) == len(set(calculator_ids))


def test_calculator_template_has_no_obsolete_duplicate_assignment() -> None:
    source = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    assert source.count('CALCULATOR_TEMPLATE = """<!doctype html>') == 1
    calculator_block = source[source.index('CALCULATOR_TEMPLATE = """<!doctype html>'):source.index('@app.get("/merged/calculator")')]
    assert "settings-grid" not in calculator_block


def test_calculator_js_net_r_only_and_no_idle_specs_placeholder() -> None:
    script = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    assert "Requested net R" in script
    assert "Effective net R" in script
    assert "Fee buffer (R)" in script
    assert "R:R" not in script
    assert "Type a symbol to load instrument specs." not in script


def test_bybit_place_order_not_modified_with_matching_live_tpsl_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: True)
    monkeypatch.setattr(master_service, "cache_bybit_demo_tpsl_request", lambda **_kwargs: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(
        master_service,
        "_wait_for_position_entry",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "size": "0.01",
                "avgPrice": "100",
                "entryPrice": "100",
                "positionIdx": 0,
                "takeProfit": "110",
                "stopLoss": "95",
            },
        ),
    )
    monkeypatch.setattr(
        master_service,
        "_fetch_bybit_positions",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[{"size": "0.01", "takeProfit": "110", "stopLoss": "95"}],
        ),
    )
    async def fake_trading_stop(**_kwargs):
        raise ValueError("Bybit trading-stop failed: not modified")

    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_trading_stop)
    monkeypatch.setattr(
        master_service,
        "_bybit_lookup_symbol",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}),
    )

    async def fake_get_async(_base_url, path, _params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99.9", "ask1Price": "100.1"}]}}
        if path == "/v5/market/instruments-info":
            return {"result": {"list": [{"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}}]}}
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get_async)

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"retCode": 0, "result": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    payload = {
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": "0.01",
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "stop_loss_price": "95",
        "take_profit_price": "110",
        "timeframe": "1h",
    }
    result = asyncio.run(master_service._place_bybit_order(payload, request_id="rid-1"))
    assert (result.get("order") or {}).get("orderId") == "oid-1"


def test_bybit_quote_uses_tick_step_fee_and_no_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "600", "coin": [{"coin": "USDT", "availableToTrade": "600"}]}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 0.5}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_lookup_linear_symbol_with_fallback", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)

    payload = {
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    qty = float(body["quantity"])
    assert qty > 0
    assert float(body["estimated_total_loss_aud"]) <= 1200.0001
    assert body["display_currency"] == "USDT"
    assert "estimated_fees_or_spread" in body
    assert "estimated_total_loss" in body
    assert "estimated_reward" in body


def test_place_bybit_order_returns_context_failure_warning_on_first_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_position_idx_for_order", lambda **_kwargs: 0)
    monkeypatch.setattr(master_service, "_parse_limit_cancel_settings", lambda _payload: (None, None))
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _id: False)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_signed_post(**_kwargs):
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_signed_post)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ctxfail")))

    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT", "action": "buy", "quantity": "0.01", "account": "demo", "order_type": "market"
    }, request_id="rid-first-fail"))
    assert result.get("journal_context_saved") is False
    assert result.get("context_save_error")
    assert result.get("warnings")


def test_place_bybit_order_secondary_context_failure_adds_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_position_idx_for_order", lambda **_kwargs: 0)
    monkeypatch.setattr(master_service, "_parse_limit_cancel_settings", lambda _payload: (None, None))
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _id: False)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_signed_post(**_kwargs):
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "oid-2", "orderLinkId": "ol-2"}}

    async def fake_wait_position(**_kwargs):
        return {"avgPrice": "100", "positionIdx": 0, "size": "1"}

    async def fake_set_tpsl(**_kwargs):
        return {"retCode": 0}

    async def fake_fetch_positions(**_kwargs):
        return [{"size": "1", "takeProfit": "110", "stopLoss": "95"}]

    calls = {"n": 0}
    def fake_upsert(_payload, require_durable=True, invalidate_cache=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("secondary-fail")
        return _payload

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_lookup_linear_symbol_with_fallback", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}))
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_signed_post)
    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_position)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_tpsl)
    monkeypatch.setattr(master_service, "_fetch_bybit_positions", fake_fetch_positions)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", fake_upsert)

    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT", "action": "buy", "quantity": "0.01", "account": "demo", "order_type": "market",
        "stop_loss_price": "95", "take_profit_price": "110"
    }, request_id="rid-second-fail"))
    assert result.get("journal_context_saved") is True
    assert calls["n"] >= 2

def test_bybit_quote_snaps_price_fields_with_trailing_zero_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.10"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "78032.90", "ask1Price": "78032.96", "lastPrice": "78032.93"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "10000", "totalAvailableBalance": "10000", "coin": [{"coin": "USDT", "availableToTrade": "10000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 0.001, "stop_loss_ticks": 1, "take_profit_ticks": 1,
    })).body.decode("utf-8"))
    assert body["entry_price"] == "78032.9"
    assert body["stop_price"] == "78032.8"
    assert body["target_price"] == "78033"


def test_bybit_quote_webhook_payload_uses_snapped_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-webhook.test")
    monkeypatch.delenv("RENDER_CALCULATOR_BASE_URL", raising=False)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.10"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "78032.90", "ask1Price": "78032.96", "lastPrice": "78032.93"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "10000", "totalAvailableBalance": "10000", "coin": [{"coin": "USDT", "availableToTrade": "10000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 0.001, "stop_loss_ticks": 1, "take_profit_ticks": 1, "webhook": "yes", "pattern": "range",
    })).body.decode("utf-8"))
    payload = json.loads(body["webhook_payload_json"])
    assert payload["entry_price"] == "78032.9"
    assert payload["stop_loss_price"] == "78032.8"
    assert payload["take_profit_price"] == "78033"
    assert payload["pattern"] == "range"


def test_bybit_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 1}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "sell", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "101"
    assert sell["entry_price"] == "100"


def test_oanda_quote_uses_display_precision_and_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {
            "prices": [{
                "bids": [{"price": "0.65000"}],
                "asks": [{"price": "0.65010"}],
            }],
            "homeConversions": [{"currency": "USD", "accountGain": "1.4", "accountLoss": "1.5", "positionValue": "1.45"}],
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    payload = {
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    assert body["tick_size"] == "0.00001"
    assert float(body["quantity"]) >= 1
    assert float(body["quantity"]) < 500000  # spread is included in risk sizing; otherwise this would be larger


def test_oanda_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}],
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "1.1002"
    assert sell["entry_price"] == "1.1"


def test_oanda_top_level_home_conversions_sell_market_fixed_aud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "prices": [{"bids": [{"price": "0.61000"}], "asks": [{"price": "0.61020"}]}],
                "homeConversions": [{"currency": "USD", "accountGain": "1.45", "accountLoss": "1.5", "positionValue": "1.47"}],
            },
        ),
    )
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["symbol"] == "NZD_USD"
    assert float(body["quantity"]) > 0


def test_oanda_deprecated_quote_home_conversion_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}], "quoteHomeConversionFactors": {"positiveUnits": "1.3", "negativeUnits": "1.7"}}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "target_mode": "rr", "risk_reward": 2,
    })).body.decode("utf-8"))
    assert float(body["estimated_reward_aud"]) > float(body["estimated_total_loss_aud"]) * 1.5


def test_oanda_quote_home_same_currency_shortcut_without_conversion_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "0.91000"}], "asks": [{"price": "0.91020"}]}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "euraud", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert float(body["estimated_total_loss_aud"]) > 0


def test_oanda_gain_loss_conversion_split_applies_to_rr_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "prices": [{"bids": [{"price": "1.20000"}], "asks": [{"price": "1.20020"}]}],
                "homeConversions": [{"currency": "USD", "accountGain": "1.5", "accountLoss": "0.8", "positionValue": "1.2"}],
            },
        ),
    )
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "target_mode": "rr", "risk_reward": 2,
    })).body.decode("utf-8"))
    reward = float(body["estimated_reward_aud"])
    loss = float(body["estimated_total_loss_aud"])
    assert reward > loss * 3


def test_oanda_missing_conversion_returns_schema_aware_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 502
    assert "missing usable home conversion" in str(exc.value.detail).lower()
    assert "top_level_currencies" in str(exc.value.detail)


def test_limit_requires_entry_price() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "EUR_USD", "side": "buy", "order_type": "limit",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400


def test_missing_env_and_bad_symbols_return_real_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "", "", "https://bybit.test", "KEY1"))
    with pytest.raises(master_service.HTTPException) as bybit_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bybit_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: (_ for _ in ()).throw(ValueError("missing env")))
    with pytest.raises(master_service.HTTPException) as oanda_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "live", "symbol": "EUR_USD", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert oanda_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    async def no_symbols(*_args, **_kwargs):
        return []
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", no_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(master_service, "_bybit_get_async", lambda *_a, **_k: asyncio.sleep(0, result={"result": {"list": []}}))
    with pytest.raises(master_service.HTTPException) as bad_symbol_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "NOTREAL", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bad_symbol_exc.value.status_code == 404


def test_crypto_rejects_fixed_aud() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400


def test_bybit_rejects_min_notional_and_market_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1", "coin": [{"coin": "USDT", "availableToTrade": "1"}]}]}}

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "10", "maxMktOrderQty": "2", "minNotionalValue": "1000"}, "leverageFilter": {"maxLeverage": "2"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "100", "lastPrice": "100"}]}}

    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    with pytest.raises(master_service.HTTPException) as min_notional:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2}))
    assert "minimum notional" in str(min_notional.value.detail).lower()


def test_oanda_rejects_max_units_and_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 1, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1", "maximumOrderUnits": "10", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.1"}], "asks": [{"price": "1.1001"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market", "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2}))
    assert exc.value.status_code == 400


def test_submit_routes_to_existing_order_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"bybit": 0, "oanda": 0}

    async def fake_bybit(payload, *, request_id, **_kwargs):
        calls["bybit"] += 1
        assert payload["timeframe"] == "15MIN"
        assert payload["is_test_trade"] is True
        assert payload["stop_loss_price"] == "1"
        assert payload["take_profit_price"] == "2"
        return {"ok": True}

    async def fake_oanda(payload, *, request_id):
        calls["oanda"] += 1
        assert payload["timeframe"] == "1H"
        return {"ok": True}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_place_oanda_order", fake_oanda)

    crypto_response = asyncio.run(master_service.calculator_submit({
        "asset": "crypto", "broker": "oanda", "account": "live", "symbol": "BTCUSDT", "action": "buy", "order_type": "market",
        "entry_price": "100", "stop_loss_price": "1", "take_profit_price": "2", "quantity": "0.01", "timeframe": "15m", "test": "yes",
    }))
    asyncio.run(master_service.calculator_submit({
        "asset": "fx", "account": "demo", "symbol": "EUR_USD", "action": "sell", "order_type": "limit",
        "entry_price": "1.2", "stop_loss_price": "1.3", "take_profit_price": "1.1", "quantity": "1000", "timeframe": "1h",
    }))
    assert calls == {"bybit": 1, "oanda": 1}
    crypto_body = json.loads(crypto_response.body.decode("utf-8"))
    assert crypto_body["broker"] == "bybit"
    assert crypto_body["venue"] == "Bybit"


def test_submit_never_routes_pepperstone_through_oanda(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden_oanda(*_args, **_kwargs):
        raise AssertionError("Pepperstone must not route through OANDA submit")

    monkeypatch.setattr(master_service, "_place_oanda_order", forbidden_oanda)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({"asset": "fx", "broker": "pepperstone", "symbol": "EUR_USD"}))
    assert exc.value.status_code == 400
    assert "downloaded MT5 .set" in str(exc.value.detail)


def test_submit_and_webhook_bubble_context_warning_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(payload, *, request_id, **_kwargs):
        return {"order": {"orderId": "oid"}, "journal_context_saved": False, "warnings": ["warn"], "context_save_error": "boom"}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _payload: None)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda _wid, _updates: {"id": _wid})
    monkeypatch.setattr(master_service, "_update_webhook_attempt", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "_consume_pending_webhook", lambda *_a, **_k: True)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": []})))

    submit = json.loads(asyncio.run(master_service.calculator_submit({
        "asset": "crypto", "account": "demo", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"
    })).body.decode("utf-8"))
    assert submit["journal_context_saved"] is False
    assert submit["warnings"] == ["warn"]
    assert submit["context_save_error"] == "boom"

    webhook = json.loads(asyncio.run(master_service.calculator_webhook({
        "asset": "crypto", "pending_webhook_id": "wh-ctx", "account": "demo", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"
    })).body.decode("utf-8"))
    assert webhook["journal_context_saved"] is False
    assert webhook["warnings"] == ["warn"]
    assert webhook["context_save_error"] == "boom"


def test_journal_summary_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_get_trading_journal_rows",
        lambda: [
            {
                "row_type": "trade",
                "symbol": "EURUSD",
                "asset_class": "fx",
                "side": "Buy",
                "close_time": "2026-01-01T01:00:00Z",
                "open_time": "2026-01-01T00:00:00Z",
                "entry_price": 1.1,
                "stop_loss": 1.0,
                "take_profit": 1.2,
                "net_profit": 1,
                "balance_after_trade": 100,
                "is_test_trade": False,
            },
            {
                "row_type": "trade",
                "symbol": "EUR_USD",
                "asset_class": "fx",
                "side": "Sell",
                "close_time": "2026-01-02T01:00:00Z",
                "open_time": "2026-01-02T00:00:00Z",
                "entry_price": 1.2,
                "stop_loss": 1.3,
                "take_profit": 1.1,
                "net_profit": -1,
                "balance_after_trade": 99,
                "is_test_trade": False,
            },
            {
                "row_type": "trade",
                "symbol": "EUR_USD",
                "asset_class": "fx",
                "side": "Buy",
                "close_time": "2026-01-03T01:00:00Z",
                "open_time": "2026-01-03T00:00:00Z",
                "entry_price": 1.2,
                "stop_loss": 1.1,
                "take_profit": 1.3,
                "net_profit": 2,
                "balance_after_trade": 101,
                "is_test_trade": True,
            },
        ],
    )
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    response = asyncio.run(master_service.calculator_journal_summary(asset="fx", symbol="eurusd"))
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "ok"
    assert body["canonical_symbol"] == "EUR_USD"
    assert isinstance(body.get("trades"), list)
    assert len(body["trades"]) == 2
    assert body["trades"][0]["close_time"] == "2026-01-02T01:00:00Z"
    assert body["trades"][1]["close_time"] == "2026-01-01T01:00:00Z"


@pytest.mark.parametrize("query", ["BTCUSDT", "BTC/USDT", "Bitcoin USDT"])
def test_journal_summary_uses_master_workbook_rows_for_bitcoin_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    workbook = tmp_path / "Trading Journal.xlsx"
    workbook.write_bytes(b"placeholder")
    rows = [
        {
            "id": "btc-bybit",
            "row_type": "trade",
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "account": "BYBIT",
            "account_label": "BYBIT",
            "side": "Buy",
            "close_time": "2026-02-01T00:00:00Z",
            "open_time": "2026-02-01T00:00:00Z",
            "entry_price": 100,
            "exit_price": 101,
            "net_profit": 10,
            "result_pct": 1,
            "balance_after_trade": 110,
            "currency": "USDT",
            "is_test_trade": False,
        },
        {
            "id": "btc-binance",
            "row_type": "trade",
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "account": "BINANCE",
            "account_label": "BINANCE",
            "side": "Sell",
            "close_time": "2026-02-02T00:00:00Z",
            "open_time": "2026-02-02T00:00:00Z",
            "entry_price": 102,
            "exit_price": 101,
            "net_profit": 8,
            "result_pct": 0.8,
            "balance_after_trade": 118,
            "currency": "USDT",
            "is_test_trade": False,
        },
        {"id": "btc-test", "row_type": "trade", "symbol": "BTCUSDT", "asset_class": "crypto", "account": "BYBIT", "is_test_trade": True},
    ]
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "_master_journal_path", lambda: workbook)
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "read_master_journal_source", lambda _path: {"items": rows, "balances": []})
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _account: ("live", "k", "s", "https://bybit.test", "KEY"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT", "ETHUSDT"]))
    monkeypatch.setattr(master_service, "_bybit_name_aliases_for_choices", lambda *_args, **_kwargs: asyncio.sleep(0, result={"BITCOIN": "BTC"}))

    response = asyncio.run(master_service.calculator_journal_summary(asset="crypto", symbol=query))
    body = json.loads(response.body.decode("utf-8"))

    assert body["status"] == "ok"
    assert body["canonical_symbol"] == "BTCUSDT"
    assert len(body["trades"]) == 2
    assert {trade["id"] for trade in body["trades"]} == {"btc-bybit", "btc-binance"}


def test_journal_summary_json_safes_authoritative_workbook_datetime_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = tmp_path / "Trading Journal.xlsx"
    workbook.write_bytes(b"placeholder")
    move_time = datetime(2026, 2, 1, 12, 34, 56)
    rows = [
        {
            "id": "btc-datetime",
            "row_type": "trade",
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "account": "BYBIT",
            "account_label": "BYBIT",
            "side": "Buy",
            "close_time": "2026-02-01T01:00:00Z",
            "open_time": "2026-02-01T00:00:00Z",
            "move_to_break_even_time": move_time,
            "entry_price": 100,
            "exit_price": 102,
            "stop_loss": 99,
            "take_profit": 103,
            "net_profit": 20,
            "result_pct": 2,
            "balance_after_trade": 120,
            "currency": "USDT",
            "is_test_trade": False,
        }
    ]
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "_master_journal_path", lambda: workbook)
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "read_master_journal_source", lambda _path: {"items": rows, "balances": []})
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _account: ("live", "k", "s", "https://bybit.test", "KEY"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    response = asyncio.run(master_service.calculator_journal_summary(asset="crypto", symbol="BTCUSDT"))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "ok"
    assert body["canonical_symbol"] == "BTCUSDT"
    assert len(body["trades"]) == 1
    assert body["trades"][0]["symbol"] == "BTCUSDT"
    assert body["trades"][0]["move_to_break_even_time"] == move_time.isoformat()


def test_rr_fee_buffer_pushes_target_distance_beyond_plain_rr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "risk_reward": 2,
    })).body.decode("utf-8"))
    stop_distance = abs(float(body["entry_price"]) - float(body["stop_price"]))
    target_distance = abs(float(body["target_price"]) - float(body["entry_price"]))
    assert target_distance > (stop_distance * 2.0)


def test_fee_rate_failure_falls_back_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            raise ValueError("Bybit signed GET failed path=/v5/account/fee-rate retCode=10003 retMsg=API key is invalid")
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    response = asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
    }))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert isinstance(body.get("warnings"), list)
    assert "conservative fallback fees" in body["warnings"][0].lower()
    assert "path=/v5/account/fee-rate" not in body["warnings"][0]


def test_balance_failure_returns_endpoint_specific_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        raise master_service.HTTPException(status_code=502, detail="Bybit balance lookup failed path=/v5/account/wallet-balance: retCode=10003 retMsg=invalid key")

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
        }))
    detail = str(exc.value.detail)
    assert "/v5/account/wallet-balance" in detail
    assert "Bybit request failed" not in detail


def test_submit_translates_bybit_errors_to_http_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(_payload, *, request_id, **_kwargs):
        assert request_id
        raise ValueError("retCode=10001 request parameter error")

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({
            "asset": "crypto",
            "account": "live",
            "symbol": "BTCUSDT",
            "action": "buy",
            "order_type": "market",
            "entry_price": "100",
            "stop_loss_price": "90",
            "take_profit_price": "120",
            "quantity": "0.01",
            "timeframe": "15m",
        }))
    assert exc.value.status_code == 400
    assert "Order submit failed:" in str(exc.value.detail)


def test_webhook_uses_keyword_request_id_for_bybit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"request_id": "", "is_test_trade": None}

    async def fake_bybit(_payload, *, request_id, **_kwargs):
        seen["request_id"] = request_id
        seen["is_test_trade"] = _payload.get("is_test_trade")
        return {"ok": True}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _payload: None)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": [{"broker": "bybit", "account": "live", "category": "linear", "instrument": "BTCUSDT", "id": "oid-1", "type": "order"}]})))
    monkeypatch.setattr(master_service, "_consume_pending_webhook", lambda *_args, **_kwargs: True)
    response = asyncio.run(master_service.calculator_webhook({
        "asset": "crypto",
        "account": "live",
        "symbol": "BTCUSDT",
        "action": "buy",
        "order_type": "market",
        "entry_price": "100",
        "stop_loss_price": "90",
        "take_profit_price": "120",
        "quantity": "0.01",
        "timeframe": "15m",
        "test": "yes",
    }))
    body = json.loads(response.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["request_id"].startswith("calc-webhook-")
    assert seen["is_test_trade"] is True


def test_webhook_consumes_pending_before_order_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"consumed": False, "request_id": "", "submit_invalidate_cache": None}

    def fake_assert(_payload):
        return None

    def fake_consume(webhook_id: str, *, request_id: str, reason: str = "webhook_received") -> bool:
        assert webhook_id == "wh-123"
        assert reason == "order_accepted"
        seen["consumed"] = True
        seen["request_id"] = request_id
        return True

    async def fake_bybit(_payload, *, request_id, **_kwargs):
        assert seen["consumed"] is False
        assert isinstance(request_id, str) and request_id.startswith("calc-webhook-")
        seen["submit_invalidate_cache"] = _kwargs.get("invalidate_cache")
        return {"ok": True, "order": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", fake_assert)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda _wid, _updates: {"id": _wid})
    monkeypatch.setattr(master_service, "_consume_pending_webhook", fake_consume)
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": [{"broker": "bybit", "account": "live", "category": "linear", "instrument": "BTCUSDT", "id": "oid-1", "type": "order"}]})))
    response = asyncio.run(
        master_service.calculator_webhook(
            {
                "asset": "crypto",
                "pending_webhook_id": "wh-123",
                "account": "live",
                "symbol": "BTCUSDT",
                "action": "buy",
                "order_type": "market",
                "quantity": "0.01",
            }
        )
    )
    body = json.loads(response.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["consumed"] is True
    assert seen["submit_invalidate_cache"] is False


def test_webhook_attempts_endpoint_returns_recent_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_load_webhook_attempts",
        lambda: [
            {"request_id": "r-1", "status": "BYBIT_REJECTED"},
            {"request_id": "r-2", "status": "BYBIT_ACCEPTED"},
        ],
    )
    response = asyncio.run(master_service.calculator_webhook_attempts(limit=1))
    body = json.loads(response.body.decode("utf-8"))
    assert len(body["items"]) == 1
    assert body["items"][0]["request_id"] == "r-2"


def test_webhook_records_bybit_rejection_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    events = {"record": None, "update": []}

    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: None)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda *_args, **_kwargs: {"id": "wh-1"})
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)

    def fake_record(payload):
        events["record"] = dict(payload)
        return payload

    def fake_update(request_id, updates):
        events["update"].append((request_id, dict(updates)))
        return updates

    async def fake_bybit(_payload, *, request_id, **_kwargs):
        raise master_service.BybitOrderRejected(
            ret_code=10001,
            ret_msg="request parameter error",
            ret_ext_info={"foo": "bar"},
            result={},
            request_body={"orderType": "Market"},
            http_status=200,
            response_body={"retCode": 10001},
        )

    monkeypatch.setattr(master_service, "_record_webhook_attempt", fake_record)
    monkeypatch.setattr(master_service, "_update_webhook_attempt", fake_update)
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(
            master_service.calculator_webhook(
                {
                    "asset": "crypto",
                    "pending_webhook_id": "wh-1",
                    "account": "demo",
                    "symbol": "BTCUSDT",
                    "action": "sell",
                    "order_type": "market",
                    "quantity": "0.01",
                }
            )
        )
    assert exc.value.status_code == 400
    assert events["record"]["status"] == "RECEIVED"
    assert any(update.get("status") == "BYBIT_REJECTED" for _, update in events["update"])
    rejected = next(update for _, update in events["update"] if update.get("status") == "BYBIT_REJECTED")
    assert rejected["bybit_ret_code"] == 10001
    assert rejected["bybit_ret_msg"] == "request parameter error"
    assert rejected["bybit_request"] == {"orderType": "Market"}


def test_calculator_quote_returns_absolute_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    pending_state = []
    saved_contexts = []
    invalidations = {"count": 0}
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://codex-rdqh.onrender.com")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [dict(row) for row in pending_state])
    monkeypatch.setattr(master_service, "_save_pending_webhooks", lambda rows: pending_state.__setitem__(slice(None), [dict(row) for row in rows]))
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda row: saved_contexts.append(dict(row)) or row)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: invalidations.__setitem__("count", invalidations["count"] + 1))
    body = json.loads(asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"})).body.decode("utf-8"))
    assert body["webhook_endpoint_url"] == "https://codex-rdqh.onrender.com/api/calculator/webhook"
    assert len(pending_state) == 1
    assert invalidations["count"] == 1
    assert saved_contexts[-1]["broker"] == "bybit"
    assert saved_contexts[-1]["category"] == "linear"
    assert saved_contexts[-1]["calculation_context_id"] == body["calculation_context_id"]
    assert saved_contexts[-1]["stop_loss_ticks"] == "1"


def test_calculator_bootstrap_reports_local_webhook_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS", raising=False)
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    webhook = body["webhook"]
    assert webhook["available"] is False
    assert webhook["unavailable_code"] == "LOCAL_WEBHOOK_UNREACHABLE"
    assert webhook["webhook_origin_host"] in {"localhost", "127.0.0.1"}
    assert "RENDER_CALCULATOR_BASE_URL" in str(webhook.get("unavailable_message") or "")


def test_calculator_bootstrap_reports_public_webhook_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-tunnel.test")
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    webhook = body["webhook"]
    assert webhook["available"] is True
    assert webhook["webhook_endpoint_url"] == "https://example-tunnel.test/api/calculator/webhook"


def test_calculator_bootstrap_reports_remote_render_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://render.example.test")
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    webhook = json.loads(response.body.decode("utf-8"))["webhook"]
    assert webhook["available"] is True
    assert webhook["mode"] == "remote_render"
    assert webhook["webhook_endpoint_url"] == "https://render.example.test/api/calculator/webhook"
    assert webhook["pending_owner"] == "remote_render"


def test_calculator_bootstrap_includes_runtime_fingerprint() -> None:
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    for key in ("app_profile", "app_version", "app_build_stamp", "render_git_commit", "calculator_js_sha256_12", "calculator_js_mtime", "master_service_path", "render_calculator_base_url_configured", "render_calculator_base_url_host", "webhook"):
        assert key in body


def test_old_public_webhook_unavailable_string_removed() -> None:
    py = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    js = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    old = "PUBLIC_WEBHOOK_BASE_URL to a public same-instance tunnel"
    assert old not in py
    assert old not in js


def test_local_calculator_blocks_webhook_without_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS", raising=False)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"}))
    assert exc.value.status_code == 400
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "LOCAL_WEBHOOK_UNREACHABLE"
    debug = exc.value.detail.get("debug") or {}
    assert debug.get("webhook_origin_host") in {"localhost", "127.0.0.1"}
    assert "pending_webhook_id" not in debug


def test_local_webhook_quote_proxies_to_remote_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://render.example.test")
    monkeypatch.setattr(master_service, "_calculator_webhook_capability", lambda _r: {"available": True, "mode": "remote_render"})
    called = {"upsert": 0}
    monkeypatch.setattr(master_service, "_upsert_pending_webhook", lambda _payload: called.__setitem__("upsert", called["upsert"] + 1))

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"pending_webhook_id": "rid-1", "webhook_payload_json": "{\"a\":1}", "webhook_endpoint_url": "https://render.example.test/api/calculator/webhook"}

    class _Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def post(self, *_args, **_kwargs): return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    request = master_service.Request({"type": "http", "method": "POST", "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1234), "path": "/api/calculator/quote", "headers": []})
    response = asyncio.run(master_service.calculator_quote(request, {"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert body["pending_webhook_owner"] == "remote_render"
    assert body["pending_webhook_delete_url"].startswith("/api/calculator/remote-pending-webhooks/")
    assert called["upsert"] == 0


def test_webhook_missing_pending_id_returns_409_and_attempt_row(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"attempt": None, "update": None}
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: (_ for _ in ()).throw(ValueError("Pending webhook missing or no longer active.")))
    monkeypatch.setattr(master_service, "_record_webhook_attempt", lambda payload: seen.__setitem__("attempt", dict(payload)) or dict(payload))
    monkeypatch.setattr(master_service, "_update_webhook_attempt", lambda _rid, updates: seen.__setitem__("update", dict(updates)) or dict(updates))
    response = asyncio.run(master_service.calculator_webhook({"asset": "crypto", "pending_webhook_id": "bogus", "account": "live", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 409
    assert body["code"] == "PENDING_WEBHOOK_NOT_FOUND"
    assert seen["attempt"]["status"] == "RECEIVED"
    assert seen["update"]["status"] == "PENDING_NOT_FOUND"


def test_crypto_demo_skips_fee_rate_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            raise AssertionError("fee-rate should be skipped for demo calculator quotes")
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
    })).body.decode("utf-8"))
    assert "warnings" not in body


def test_price_levels_match_helper() -> None:
    assert master_service._price_levels_match(100.0, 100.0)
    assert master_service._price_levels_match(100.0, 100.000000001)
    assert not master_service._price_levels_match(100.0, 100.1)
    assert master_service._price_levels_match(None, None)
    assert not master_service._price_levels_match(None, 100.0)


def test_snap_to_increment_handles_trailing_zeros_and_milli_ticks() -> None:
    assert str(master_service._snap_to_increment(master_service.Decimal("78032.96"), master_service.Decimal("0.10"))) == "78032.90"
    assert str(master_service._snap_to_increment(master_service.Decimal("1.2349"), master_service.Decimal("0.001"))) == "1.234"


def test_bybit_place_order_normalizes_tpsl_with_tick_size(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: True)
    monkeypatch.setattr(master_service, "cache_bybit_demo_tpsl_request", lambda **_kwargs: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(
        master_service,
        "_wait_for_position_entry",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={"size": "0.01", "avgPrice": "78032.9", "entryPrice": "78032.9", "positionIdx": 0},
        ),
    )
    monkeypatch.setattr(
        master_service,
        "_fetch_bybit_positions",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[{"size": "0.01", "takeProfit": "78033", "stopLoss": "78032.8"}],
        ),
    )

    async def fake_trading_stop(**kwargs):
        captured["take_profit"] = kwargs.get("take_profit")
        captured["stop_loss"] = kwargs.get("stop_loss")
        return {"status": "ok"}

    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_trading_stop)
    monkeypatch.setattr(
        master_service,
        "_bybit_lookup_symbol",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.10"}}),
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"retCode": 0, "result": {"orderId": "oid-2", "orderLinkId": "ol-2"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": "0.01",
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "entry_price": "78032.96",
        "level_anchor_mode": "planned_entry",
        "planned_entry_price": "78032.96",
        "planned_stop_price": "78032.86",
        "planned_target_price": "78033.06",
        "timeframe": "15m",
    }, request_id="rid-2"))
    assert (result.get("order") or {}).get("orderId") == "oid-2"
    assert captured["take_profit"] == 78033.0
    assert captured["stop_loss"] == 78032.8


def test_oanda_margin_uses_position_value_home_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 20, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(0, result={
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "2"}],
        }),
    )
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert isinstance(detail, dict)
    debug = detail.get("debug") or {}
    assert debug.get("required_margin_home")
    assert debug.get("position_value_factor") == "2"


def test_oanda_fixed_aud_is_converted_to_home_currency_before_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000, "marginAvailable": 1000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(0, result={
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}],
        }),
    )
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 0.5}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "broker": "oanda", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["account_currency"] == "USD"
    assert body["risk_input_aud"] == "10"
    assert body["risk_amount_home"] == "5"


def test_oanda_quote_returns_account_currency_not_hardcoded_aud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 0.6}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["display_currency"] == "USD"


def test_oanda_margin_error_includes_required_and_available_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 1, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
        }))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "oanda_margin_insufficient"
    debug = detail.get("debug") or {}
    assert "required_margin_home" in debug
    assert "margin_available_home" in debug
    assert "margin_rate" in debug
    assert debug.get("submitted_risk_mode") == "fixed_aud"
    assert debug.get("submitted_risk_value") == "100"


def test_oanda_nzdusd_35_ticks_fixed_aud_10_demo_flat_account_quotes_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1513.09, "marginAvailable": 1513.09, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "0.61000"}], "asks": [{"price": "0.61020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1.6", "accountLoss": "1.6", "positionValue": "1.6"}]}))

    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 35, "take_profit_ticks": 70,
    })).body.decode("utf-8"))
    assert body["symbol"] == "NZD_USD"
    assert float(body["estimated_initial_margin_home"]) < 1513.09

def test_calculator_quote_bybit_success_has_no_logger_nameerror(monkeypatch: pytest.MonkeyPatch, pepperstone_account_fixture) -> None:
    saved_context = {}
    invalidations = {"count": 0}
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda payload, **_kwargs: saved_context.update(payload))
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: invalidations.__setitem__("count", invalidations["count"] + 1))
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["LABUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"retCode": 0, "result": {"list": [{"symbol": _params["symbol"], "priceFilter": {"tickSize": "0.0001"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"retCode": 0, "result": {"list": [{"symbol": _params["symbol"], "bid1Price": "1.0000", "ask1Price": "1.0002", "lastPrice": "1.0001"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))

    monkeypatch.setenv("CALCULATOR_BYBIT_TAKER_FEE_FALLBACK", "0.0006")
    request = {"asset": "crypto", "account": "demo", "symbol": "LAB", "side": "sell",
               "order_type": "market", "risk_mode": "percent", "risk_value": 1,
               "stop_loss_ticks": 10, "risk_reward": "3.5", "webhook": "yes"}
    http_request = master_service.Request({"type": "http", "scheme": "http", "path": "/api/calculator/commission-preview", "headers": [], "server": ("localhost", 80)})
    preview = json.loads(asyncio.run(master_service.calculator_commission_preview(http_request, request)).body)
    assert saved_context == {} and invalidations["count"] == 0
    quote = json.loads(asyncio.run(master_service.calculator_quote(dict(request, webhook="no"))).body)
    hint = preview["commission_chart_r"]
    assert quote["commission_chart_r"] == hint
    entry, stop, target = (Decimal(quote[k]) for k in ("entry_price", "stop_price", "target_price"))
    stop_distance = abs(entry - stop)
    assert hint["chart_r"] == format(abs(entry - target) / stop_distance, ".2f")
    fee, spread, rr = map(Decimal, ("0.0006", "0.0002", "3.5"))
    # At the same size the no-fee target still covers spread at SL and TP.
    before_fees = (stop_distance + spread) * rr + spread
    gross = ((stop_distance + spread + fee * (entry + stop)) * rr + spread + 2 * entry * fee) / (1 + fee)
    assert hint["commission_r"] == format((gross - before_fees) / stop_distance, ".2f")
    assert invalidations["count"] == 1
    saved_context.clear()
    invalidations["count"] = 0
    response = asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "LAB", "side": "sell", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20, "pattern": "channel"}))
    assert isinstance(response, master_service.JSONResponse)
    body = json.loads(response.body.decode("utf-8"))
    assert body["broker"] == "bybit"
    assert saved_context["pattern"] == "channel"
    assert saved_context["category"] == "linear"
    assert saved_context["calculation_context_id"] == body["calculation_context_id"]
    assert invalidations["count"] == 1
    for k in ("quantity", "entry_price", "stop_price", "target_price"):
        assert k in body


def test_calculator_quote_oanda_success_has_no_logger_nameerror(monkeypatch: pytest.MonkeyPatch) -> None:
    saved_context = {}
    invalidations = {"count": 0}
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda payload, **_kwargs: saved_context.update(payload) or payload)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: invalidations.__setitem__("count", invalidations["count"] + 1))

    response = asyncio.run(master_service.calculator_quote({"asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market", "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    assert isinstance(response, master_service.JSONResponse)
    body = json.loads(response.body.decode("utf-8"))
    assert body["broker"] == "oanda"
    assert saved_context["category"] == "forex"
    assert saved_context["calculation_context_id"] == body["calculation_context_id"]
    assert invalidations["count"] == 1


def test_calculator_quote_pepperstone_market_is_supported_and_resolves_venue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_path = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("EURUSD", "EURUSD", "0.00001")])
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    _mock_pepperstone_quote_dependencies(monkeypatch)
    response = asyncio.run(master_service.calculator_quote({"asset": "fx", "broker": "pepperstone", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market", "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 35, "take_profit_ticks": 70}))
    body = json.loads(response.body.decode("utf-8"))
    assert body["broker"] == "pepperstone"
    assert body["venue"] == "Pepperstone"
    assert body["resolved_venue"] == "Pepperstone"
    assert body["market_data_source"] == "OANDA FX pricing/conversion only; no Pepperstone account or margin data."
    assert "margin_available_home" not in body
    assert master_service._calculator_effective_route("fx", "oanda", "eurusd") == ("fx", "oanda", "EUR_USD")
    with pytest.raises(ValueError, match="broker=oanda or broker=pepperstone"):
        master_service._calculator_effective_route("fx", "", "eurusd")


def test_usdjpy_default_crypto_calculator_paths_auto_route_to_oanda_without_bybit(monkeypatch: pytest.MonkeyPatch) -> None:
    bybit_names = (
        "resolve_bybit_credentials_for",
        "_bybit_get_symbols_by_category_cached",
        "_resolve_bybit_calculator_symbol_fast",
        "_bybit_get_instrument_info_cached",
        "_fetch_bybit_ticker_cached",
        "_fetch_bybit_balance_usdt_cached",
        "_bybit_signed_get",
    )

    def unexpected_bybit(*_args, **_kwargs):
        raise AssertionError("USDJPY routing must not touch a Bybit dependency")

    for name in bybit_names:
        monkeypatch.setattr(master_service, name, unexpected_bybit)

    oanda_calls = []
    saved_context = {}
    monkeypatch.setattr(
        master_service,
        "_get_oanda_config",
        lambda mode: {"base_url": "https://oanda.test", "account_id": f"acct-{mode}", "token": "test-token"},
    )

    async def fake_meta(**kwargs):
        oanda_calls.append(("meta", kwargs["symbol"]))
        return {
            "displayPrecision": 3,
            "tradeUnitsPrecision": 0,
            "pipLocation": -2,
            "minimumTradeSize": "1",
            "maximumOrderUnits": "0",
            "maximumPositionSize": "0",
            "marginRate": "0.05",
        }

    async def fake_oanda_json(**kwargs):
        oanda_calls.append(("pricing", kwargs["endpoint"]))
        return {
            "prices": [{"bids": [{"price": "149.998"}], "asks": [{"price": "150.000"}]}],
            "homeConversions": [
                {"currency": "JPY", "accountGain": "0.0067", "accountLoss": "0.0068", "positionValue": "0.0067"}
            ],
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_oanda_json)
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_account_summary",
        lambda _mode: asyncio.sleep(0, result={"currency": "USD", "nav": "100000", "marginAvailable": "1000000", "marginRate": "0.05"}),
    )
    monkeypatch.setattr(
        master_service,
        "_upsert_calculator_trade_context",
        lambda payload, **_kwargs: saved_context.update(payload) or payload,
    )
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)

    for spelling in ("USDJPY", "usdjpy", "USD/JPY", "USD_JPY"):
        instrument = json.loads(
            asyncio.run(master_service.calculator_instrument("crypto", "demo", spelling, "bybit")).body.decode("utf-8")
        )
        assert instrument["asset"] == "fx"
        assert instrument["broker"] == "oanda"
        assert instrument["symbol"] == "USD_JPY"
    assert master_service._calculator_effective_route("fx", "pepperstone", "usd/jpy") == (
        "fx",
        "pepperstone",
        "USD_JPY",
    )

    quote = json.loads(
        asyncio.run(
            master_service.calculator_quote(
                {
                    "asset": "crypto",
                    "broker": "bybit",
                    "account": "demo",
                    "symbol": "USDJPY",
                    "side": "buy",
                    "order_type": "market",
                    "risk_mode": "percent",
                    "risk_value": "1",
                    "stop_loss_ticks": "10",
                    "take_profit_ticks": "20",
                }
            )
        ).body.decode("utf-8")
    )
    assert quote["asset"] == "fx"
    assert quote["broker"] == "oanda"
    assert quote["symbol"] == "USD_JPY"
    assert saved_context["asset"] == "fx"
    assert saved_context["broker"] == "oanda"
    assert saved_context["symbol"] == "USD_JPY"
    assert saved_context["instrument"] == "USD_JPY"
    assert any(kind == "pricing" and "USD_JPY" in value for kind, value in oanda_calls)


def _parse_set_file(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _trader_input_names() -> set[str]:
    source = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    names = set()
    for line in source.splitlines():
        match = re.match(r"\s*input\s+(?!group\b)(?:[A-Za-z_][A-Za-z0-9_<>]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.add(match.group(1))
    return names


def test_pepperstone_set_automatically_derives_risk_buffer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PEPPERSTONE_TRADER_RISK_SLIPPAGE_BUFFER_POINTS", raising=False)
    export_path = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("EURUSD", "EURUSD", "0.00001")])
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    _mock_pepperstone_quote_dependencies(monkeypatch)
    base_payload = {
        "asset": "fx",
        "broker": "pepperstone",
        "account": "demo",
        "symbol": "EUR_USD",
        "side": "buy",
        "order_type": "limit",
        "entry_price": "1.10020",
        "risk_mode": "fixed_aud",
        "risk_value": "10",
        "stop_loss_ticks": "43",
        "risk_reward": "2",
    }
    quote = json.loads(asyncio.run(master_service.calculator_quote(base_payload)).body.decode("utf-8"))
    set_payload = _pepperstone_set_payload_from_quote(base_payload, quote)
    response = asyncio.run(master_service.calculator_pepperstone_set(set_payload))
    assert response.media_type.startswith("text/plain")
    assert "Pepperstone_Trader_EUR_USD_BUY_LIMIT_" in response.headers["content-disposition"]
    values = _parse_set_file(response.body.decode("utf-8"))
    assert set(values).issubset(_trader_input_names())
    assert values["Strategy"] == "2"
    assert values["OrdersEnabled"] == "false"
    assert values["RiskAUD_Target"] == "10"
    assert values["RiskAUD_Min"] == "9"
    assert values["RiskAUD_Max"] == "12"
    assert values["IncludeCommissionInRisk"] == "true"
    assert values["CommissionPerLotPerSide"] == "3.5"
    assert values["RiskSlippageBufferPoints"] == "50"
    assert values["SlippagePoints"] == "10"
    assert values["AutoFitRiskSlippageBuffer"] == "true"
    assert values["UsePriceDistanceInputs"] == "true"
    assert values["SL_PriceDistance"] == "0.00043"
    assert values["SL_DistancePoints"] == "0"
    assert values["TP_DistancePoints"] == "0"
    assert values["AutoTP_NetRR_Enabled"] == "true"
    assert values["NetRR_Target"] == "2"
    assert values["StandardLimitSide"] == "0"
    assert values["StandardLimitEntryPrice"] == "1.1002"
    assert "StandardMarketSide" not in values
    assert "StandardMarketExecutionToken" not in values
    assert values["MagicNumber"] == "91001"
    assert values["EnforceOneTradeAtATime"] == "true"

    sell_request = dict(base_payload, side="sell")
    sell_quote = json.loads(asyncio.run(master_service.calculator_quote(sell_request)).body.decode("utf-8"))
    sell_payload = _pepperstone_set_payload_from_quote(sell_request, sell_quote)
    sell_response = asyncio.run(master_service.calculator_pepperstone_set(sell_payload))
    sell_values = _parse_set_file(sell_response.body.decode("utf-8"))
    assert sell_values["StandardLimitSide"] == "1"

    default_request = dict(base_payload, stop_loss_ticks="200")
    default_quote = json.loads(asyncio.run(master_service.calculator_quote(default_request)).body.decode("utf-8"))
    default_payload = _pepperstone_set_payload_from_quote(default_request, default_quote)
    default_response = asyncio.run(master_service.calculator_pepperstone_set(default_payload))
    default_values = _parse_set_file(default_response.body.decode("utf-8"))
    assert default_values["RiskSlippageBufferPoints"] == "50"


def test_pepperstone_set_export_maps_market_side_and_generates_unique_one_shot_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_path = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("EURUSD", "EURUSD", "0.00001")])
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    _mock_pepperstone_quote_dependencies(monkeypatch)
    base_payload = {
        "asset": "fx",
        "broker": "pepperstone",
        "symbol": "EUR_USD",
        "side": "buy",
        "order_type": "market",
        "risk_mode": "fixed_aud",
        "risk_value": "10",
        "stop_loss_ticks": "35",
        "risk_reward": "2",
    }
    quote = json.loads(asyncio.run(master_service.calculator_quote(base_payload)).body.decode("utf-8"))
    set_payload = _pepperstone_set_payload_from_quote(base_payload, quote)
    first = asyncio.run(master_service.calculator_pepperstone_set(set_payload))
    second = asyncio.run(master_service.calculator_pepperstone_set(set_payload))
    first_values = _parse_set_file(first.body.decode("utf-8"))
    second_values = _parse_set_file(second.body.decode("utf-8"))

    assert "Pepperstone_Trader_EUR_USD_BUY_MARKET_" in first.headers["content-disposition"]
    assert set(first_values).issubset(_trader_input_names())
    assert first_values["Strategy"] == "3"
    assert first_values["StandardMarketSide"] == "0"
    assert len(first_values["StandardMarketExecutionToken"]) >= 16
    assert first_values["StandardMarketExecutionToken"] != second_values["StandardMarketExecutionToken"]
    assert "StandardLimitSide" not in first_values
    assert "StandardLimitEntryPrice" not in first_values
    for key in (
        "OrdersEnabled",
        "RiskAUD_Target",
        "RiskAUD_Min",
        "RiskAUD_Max",
        "IncludeCommissionInRisk",
        "CommissionPerLotPerSide",
        "RiskSlippageBufferPoints",
        "SlippagePoints",
        "SL_DistancePoints",
        "AutoTP_NetRR_Enabled",
        "NetRR_Target",
        "TP_DistancePoints",
        "MagicNumber",
        "EnforceOneTradeAtATime",
    ):
        assert key in first_values

    sell_request = dict(base_payload, side="sell")
    sell_quote = json.loads(asyncio.run(master_service.calculator_quote(sell_request)).body.decode("utf-8"))
    sell = asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(sell_request, sell_quote)))
    sell_values = _parse_set_file(sell.body.decode("utf-8"))
    assert sell_values["StandardMarketSide"] == "1"


def test_pepperstone_set_export_rejects_tight_stop_before_token_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_path = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("USDJPY", "USDJPY", "0.001")])
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    _mock_pepperstone_quote_dependencies(monkeypatch)
    payload = {
        "account": "demo",
        "asset": "fx",
        "broker": "pepperstone",
        "symbol": "USD_JPY",
        "side": "buy",
        "order_type": "market",
        "risk_mode": "fixed_aud",
        "risk_value": "10",
        "stop_loss_ticks": "20",
        "risk_reward": "2",
    }
    quote = json.loads(asyncio.run(master_service.calculator_quote(payload)).body.decode("utf-8"))
    set_payload = _pepperstone_set_payload_from_quote(payload, quote)
    token_calls = []
    monkeypatch.setattr(master_service, "uuid4", lambda: token_calls.append(True) or "should-not-be-created")
    with pytest.raises(master_service.HTTPException) as exc:
        master_service._build_pepperstone_trader_set(set_payload)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "PEPPERSTONE_SET_RISK_BUFFER_INCOMPATIBLE"
    preflight = exc.value.detail["preflight"]
    assert preflight["stop_points"] == 20
    assert preflight["minimum_automatic_buffer_points"] == 10
    assert preflight["conservative_max_buffer_points"] == 6
    assert preflight["planned_buffer_points"] == 6
    assert preflight["minimum_compatible_stop_points"] == 30
    assert preflight["compatible"] is False
    assert token_calls == []


def test_pepperstone_tick_distances_convert_exactly_for_fx_and_jpy_steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_path = _write_pepperstone_point_export(
        tmp_path,
        [
            _pepperstone_point_entry("GBPUSD", "GBPUSD.a", "0.00001"),
            _pepperstone_point_entry("USDJPY", "USDJPY", "0.001"),
        ],
    )
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    gbp = master_service._load_pepperstone_symbol_point("GBP_USD")
    jpy = master_service._load_pepperstone_symbol_point("USD_JPY")
    assert gbp["mt5_symbol"] == "GBPUSD.a"
    assert gbp["point"] == Decimal("0.00001")

    cases = (
        ("43", "0.00001", gbp["point"], 43, "0.00043"),
        ("43", "0.0001", gbp["point"], 430, "0.0043"),
        ("35", "0.001", jpy["point"], 35, "0.035"),
        ("35", "0.01", jpy["point"], 350, "0.35"),
    )
    for count, chart_step, point, expected_points, expected_distance in cases:
        converted = master_service._pepperstone_convert_ticks_to_points(count, chart_step, point, "stop_loss_ticks")
        assert converted["points"] == expected_points
        assert str(converted["price_distance"]) == expected_distance
    target = master_service._pepperstone_convert_ticks_to_points("25", "0.0001", gbp["point"], "take_profit_ticks")
    assert target == {"ticks": 25, "price_distance": Decimal("0.0025"), "points": 250}

    with pytest.raises(master_service.HTTPException) as inexact:
        master_service._pepperstone_convert_ticks_to_points("1", "0.00001", "0.00003", "stop_loss_ticks")
    assert inexact.value.status_code == 422
    assert "not representable as a whole MT5 point count" in str(inexact.value.detail)
    for invalid_count in ("1.5", "NaN", "Infinity", "0"):
        with pytest.raises(master_service.HTTPException, match="finite positive whole number"):
            master_service._pepperstone_positive_tick_count(invalid_count, "stop_loss_ticks")

    ambiguous_path = _write_pepperstone_point_export(
        tmp_path,
        [
            _pepperstone_point_entry("GBPUSD", "GBPUSD.a", "0.00001"),
            _pepperstone_point_entry("GBPUSD", "GBPUSD.b", "0.00001"),
        ],
    )
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(ambiguous_path))
    with pytest.raises(master_service.HTTPException, match="ambiguous"):
        master_service._load_pepperstone_symbol_point("GBP_USD")


def test_pepperstone_manual_ticks_quote_size_and_set_remain_independent_of_inactive_r(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pepperstone_account_fixture) -> None:
    export_path = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("GBPUSD", "GBPUSD", "0.00001")])
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    calls = _mock_pepperstone_quote_dependencies(monkeypatch)

    manual_request = _pepperstone_quote_payload(
        side="sell",
        target_mode="ticks",
        take_profit_ticks="25",
        risk_reward="99",
        chart_tick_size="0.0001",
    )
    manual_quote = json.loads(asyncio.run(master_service.calculator_quote(manual_request)).body.decode("utf-8"))
    conversion = manual_quote["pepperstone_distance_conversion"]
    assert manual_quote["target_mode"] == "ticks"
    assert manual_quote["requested_rr_net"] is None
    assert manual_quote["chart_tick_size"] == "0.0001"
    assert conversion["chart_tick_size_source"] == "TradingView user override"
    assert conversion["mt5_point_size"] == "0.00001"
    assert conversion["stop_loss_price_distance"] == "0.0043"
    assert conversion["stop_loss_mt5_points"] == 430
    assert conversion["take_profit_price_distance"] == "0.0025"
    assert conversion["take_profit_mt5_points"] == 250
    assert manual_quote["pepperstone_risk_buffer_preflight"]["stop_points"] == 430
    assert manual_quote["pepperstone_risk_buffer_preflight"]["minimum_compatible_stop_ticks"] == 0
    assert Decimal(manual_quote["quantity"]) > 0
    assert "estimate from OANDA price/conversion data" in manual_quote["position_size_estimate_note"]
    assert manual_quote["market_data_source"] == "OANDA FX pricing/conversion only; no Pepperstone account or margin data."
    assert "margin_available_home" not in manual_quote
    assert calls["account_summary"] == 0

    manual_set = asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(manual_request, manual_quote)))
    manual_values = _parse_set_file(manual_set.body.decode("utf-8"))
    assert manual_values["UsePriceDistanceInputs"] == "true"
    assert manual_values["DistanceSymbol"] == "GBP_USD"
    assert manual_values["SL_PriceDistance"] == "0.0043"
    assert manual_values["TP_PriceDistance"] == "0.0025"
    assert manual_values["SL_DistancePoints"] == "0"
    assert manual_values["TP_DistancePoints"] == "0"
    assert manual_values["AutoTP_NetRR_Enabled"] == "false"
    assert "NetRR_Target" not in manual_values
    assert manual_values["StandardMarketSide"] == "1"

    net_request = _pepperstone_quote_payload(
        order_type="limit",
        side="buy",
        entry_price="1.30000",
        target_mode="rr",
        risk_reward="2",
        chart_tick_size="0.0001",
    )
    net_quote = json.loads(asyncio.run(master_service.calculator_quote(net_request)).body.decode("utf-8"))
    net_set = asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(net_request, net_quote)))
    net_values = _parse_set_file(net_set.body.decode("utf-8"))
    assert net_values["SL_PriceDistance"] == "0.0043"
    assert net_values["TP_PriceDistance"] == "0"
    assert net_values["SL_DistancePoints"] == "0"
    assert net_values["TP_DistancePoints"] == "0"
    assert net_values["AutoTP_NetRR_Enabled"] == "true"
    assert net_values["NetRR_Target"] == "2"
    assert net_values["StandardLimitSide"] == "0"
    assert net_values["StandardLimitEntryPrice"] == "1.3"
    assert calls["account_summary"] == 0

    # Exact distance guidance uses exported account evidence, not the historical screenshot.
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(master_service.httpx.Client,"request",lambda *a,**k: (_ for _ in ()).throw(AssertionError("No transport")))
    monkeypatch.setattr(master_service.httpx.AsyncClient,"request",lambda *a,**k: (_ for _ in ()).throw(AssertionError("No transport")))
    now = datetime.now(timezone.utc)
    item = dict(_pepperstone_point_entry("USDJPY","USDJPY.a","0.001"),bid="150.000",ask="150.010",stops_level=20,freeze_level=10,
                trade_tick_size="0.001",minimum_lot="0.01",tick_value="1",quote_timestamp=now.isoformat())
    def exported(**changes):
        data=dict(version=1,broker="pepperstone",generated_at=datetime.now(timezone.utc).isoformat(),account=dict(mode="demo",currency="AUD",login="must-not-display"),symbols=[dict(item,**changes)])
        export_path.write_text(json.dumps(data),encoding="utf-8")
    exported()
    common=dict(symbol="USD_JPY",account="demo",chart_tick_size="0.001",side="buy",risk_mode="fixed_aud",risk_value="10",account_type="standard")
    guidance=master_service._pepperstone_stop_guidance(**common)
    assert guidance["state"]=="ready" and guidance["minimum_ticks"]==30 and guidance["step_ticks"]==1
    assert guidance["maximum_ticks"]==1200 and "minimum-lot" in guidance["maximum_source"]
    assert "must-not-display" not in json.dumps(guidance) and guidance["pricing_choice"]=="standard" and guidance["account_variant"]=="Not independently known"
    assert master_service._pepperstone_stop_guidance(**dict(common,side="sell"))["minimum_ticks"]==30
    for ticks in (5,29,30):
        request=_pepperstone_quote_payload(symbol="USDJPY",stop_loss_ticks=str(ticks),pepperstone_account_type="standard")
        quote=json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        g=quote["pepperstone_stop_guidance"]
        assert (ticks>=g["minimum_ticks"]) == (ticks==30)
        assert g["minimum_ticks"]==30 and g["step_ticks"]==1
        assert quote["pepperstone_risk_buffer_preflight"]["compatible"] is True
    instrument=json.loads(asyncio.run(master_service.calculator_instrument("fx","demo","USDJPY","pepperstone",pepperstone_account_type="standard")).body)
    assert instrument["pepperstone_stop_guidance"]["minimum_ticks"]==30
    assert master_service._pepperstone_stop_guidance(**common,order_type="limit",entry_price="150.005")["limit_entry_state"]=="invalid"
    assert master_service._pepperstone_stop_guidance(**common,order_type="limit",entry_price="149.990")["limit_entry_state"]=="valid"
    assert master_service._pepperstone_stop_guidance(**dict(common,side="sell"),order_type="limit",entry_price="150.020")["limit_entry_state"]=="valid"
    exported(trade_tick_size="0.002")
    assert master_service._pepperstone_stop_guidance(**common)["step_ticks"]==2
    exported(trade_tick_size="0.002",ask="150.011")
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="blocked"
    exported(stops_level=0,freeze_level=0)
    with monkeypatch.context() as patch:
        patch.setenv("PEPPERSTONE_TRADER_RISK_SLIPPAGE_BUFFER_POINTS","0")
        zero=master_service._pepperstone_stop_guidance(**common)
        assert zero["minimum_ticks"]==11  # Strict close-side check survives valid zero levels.
    exported(stops_level=None)
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="pending"
    exported(quote_timestamp=(now-timedelta(seconds=120)).isoformat())
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="pending"
    exported()
    assert master_service._pepperstone_stop_guidance(**dict(common,account="live"))["state"]=="pending"
    legacy=json.loads(export_path.read_text());legacy.pop("account");export_path.write_text(json.dumps(legacy))
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="pending"
    export_path.unlink()
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="pending"
    # A different chart grid/override has exact point/trade divisibility.
    gbp=dict(_pepperstone_point_entry("GBPUSD","GBPUSD.a","0.00001"),bid="1.30000",ask="1.30020",stops_level=0,freeze_level=0,
             trade_tick_size="0.00002",quote_timestamp=datetime.now(timezone.utc).isoformat())
    export_path.write_text(json.dumps(dict(version=1,broker="pepperstone",generated_at=now.isoformat(),account=dict(mode="demo",currency="USD"),symbols=[gbp])))
    g=master_service._pepperstone_stop_guidance(symbol="GBP_USD",account="demo",chart_tick_size="0.0001")
    assert g["minimum_ticks"]==3 and g["step_ticks"]==1
    assert master_service._pepperstone_stop_guidance(symbol="GBP_USD",account="demo",chart_tick_size="0.00001")["step_ticks"]==2
    assert master_service._pepperstone_risk_buffer_preflight(stop_points=30,risk_target=Decimal("10"))["minimum_compatible_stop_points"]==0
    assert calls["account_summary"]==0

    # Default selection uses only the two documented paths; explicit configuration is exclusive.
    primary, fallback = tmp_path/"checkout-export.json", tmp_path/"terminal-export.json"
    monkeypatch.setattr(master_service,"DEFAULT_PEPPERSTONE_SPREAD_EXPORT_PATH",primary)
    monkeypatch.setattr(master_service,"PEPPERSTONE_TERMINAL_SPREAD_EXPORT_PATH",fallback)
    monkeypatch.delenv("PEPPERSTONE_SPREAD_EXPORT_PATH",raising=False)
    current=datetime.now(timezone.utc)
    base=dict(item,symbol="USDJPY.a",mt5_symbol="USDJPY.a",quote_timestamp=current.isoformat())
    def write_snapshot(path, *, mode="demo", stamp=None, fields=None):
        data=dict(version=1,broker="pepperstone",generated_at=current.isoformat(),account=dict(currency="AUD",login="never-display"),symbols=[dict(base,**(fields or {}))])
        if mode is not None:data["account"]["mode"]=mode
        if stamp is not None:data["symbols"][0]["quote_timestamp"]=stamp
        path.write_text(json.dumps(data),encoding="utf-8")
    write_snapshot(fallback)
    g=master_service._pepperstone_stop_guidance(**common)
    assert g["state"]=="ready" and g["minimum_ticks"]==30 and g["source"]==str(fallback)
    assert g["mt5_symbol"]=="USDJPY.a" and g["automatic_buffer_floor_points"]==0
    write_snapshot(primary,mode=None)  # New producer time cannot make legacy evidence usable.
    assert master_service._pepperstone_stop_guidance(**common)["source"]==str(fallback)
    write_snapshot(primary,mode="live")
    assert master_service._pepperstone_stop_guidance(**common)["source"]==str(fallback)
    write_snapshot(primary,stamp=(current-timedelta(seconds=20)).isoformat())
    assert master_service._pepperstone_stop_guidance(**common)["source"]==str(fallback)
    write_snapshot(primary,fields={"stops_level":40})
    write_snapshot(fallback,stamp=(current-timedelta(seconds=20)).isoformat())
    bounds=master_service._pepperstone_stop_guidance(**common)
    assert bounds["minimum_ticks"]==50 and bounds["static_minimum_ticks"]==1
    write_snapshot(fallback)
    write_snapshot(primary)  # Exact quote-time tie has deterministic repository precedence.
    assert master_service._pepperstone_stop_guidance(**common)["source"]==str(primary)
    reads=[];original_reader=master_service._load_pepperstone_symbol_point
    with monkeypatch.context() as patch:
        def reader(*a,**kw):reads.append(kw.get("source_path"));return original_reader(*a,**kw)
        patch.setattr(master_service,"_load_pepperstone_symbol_point",reader)
        info=json.loads(asyncio.run(master_service.calculator_instrument("fx","demo","USDJPY","pepperstone")).body)
        assert len(reads)==2 and info["pepperstone_stop_guidance"]["source"]==str(primary)
        reads.clear()
        ready=json.loads(asyncio.run(master_service.calculator_quote(_pepperstone_quote_payload(symbol="USDJPY",stop_loss_ticks="5",pepperstone_account_type="standard"))).body)
        assert len(reads)==2 and ready["pepperstone_stop_guidance"]["minimum_ticks"]==30
        assert ready["pepperstone_distance_conversion"]["mt5_symbol"]=="USDJPY.a"
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH",str(primary))
    write_snapshot(primary,mode="live")
    g=master_service._pepperstone_stop_guidance(**common)
    assert g["state"]=="pending" and g["automatic_buffer_floor_points"]==0 and "does not match" in g["reason"]
    assert len(g["source_diagnostics"])==1 and g["minimum_ticks"] is None
    write_snapshot(primary,fields={"quote_timestamp":None})
    g=master_service._pepperstone_stop_guidance(**common)
    assert g["state"]=="pending" and "quote_timestamp is missing" in g["reason"] and g["minimum_ticks"]==1
    write_snapshot(primary,fields={"stops_level":None})
    assert "stops_level" in master_service._pepperstone_stop_guidance(**common)["reason"]
    write_snapshot(primary,fields={"bid":None})
    assert "Bid/Ask" in master_service._pepperstone_stop_guidance(**common)["reason"]
    write_snapshot(primary,stamp=(current-timedelta(seconds=120)).isoformat(),fields={"trade_tick_size":"0.002"})
    g=master_service._pepperstone_stop_guidance(**common)
    assert g["state"]=="pending" and "stale" in g["reason"] and g["quote_age_seconds"]>=120
    assert g["minimum_ticks"]==2 and g["step_ticks"]==2 and g["broker_minimum_state"]=="pending"
    write_snapshot(primary)
    assert master_service._pepperstone_stop_guidance(**common)["state"]=="ready"
    primary.write_text("{")
    assert "malformed" in master_service._pepperstone_stop_guidance(**common)["reason"]
    write_snapshot(primary,fields={"symbol":"GBPUSD","mt5_symbol":"GBPUSD.a"})
    assert "mapping" in master_service._pepperstone_stop_guidance(**common)["reason"]
    primary.unlink()
    g=master_service._pepperstone_stop_guidance(**common)
    assert g["automatic_buffer_floor_points"]==0 and "missing" in g["reason"] and str(primary) in g["reason"]
    assert "never-display" not in json.dumps(g)

    # Five points can be valid on either pricing choice; the cushion adds no floor.
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH",str(primary))
    write_snapshot(primary,fields={"bid":"150.000","ask":"150.002","stops_level":2,"freeze_level":0})
    for variant,commission in [("standard","0"),("razor","4.2")]:
        request=_pepperstone_quote_payload(symbol="USDJPY",stop_loss_ticks="5",pepperstone_account_type=variant)
        quote=json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        g=quote["pepperstone_stop_guidance"];preview=quote["pepperstone_risk_buffer_preflight"]
        assert g["minimum_ticks"]==4 and g["step_ticks"]==1 and g["automatic_buffer_floor_points"]==0
        assert preview["planned_buffer_points"]==1 and preview["compatible"] is True and preview["request_deviation_points"]==10
        assert quote["pepperstone_mt5_export_status"]["file_ready"] is True
        values=_parse_set_file(asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(request,quote))).body.decode())
        assert values["AutoFitRiskSlippageBuffer"]=="true" and values["UsePriceDistanceInputs"]=="true"
        assert values["RiskSlippageBufferPoints"]=="50" and values["SlippagePoints"]=="10"
        assert values["RiskAUD_Min"]=="9" and values["RiskAUD_Max"]=="12" and values["RiskAUD_Target"]=="10"
        assert Decimal(values["CommissionPerLotPerSide"])==Decimal(commission) and values["SL_PriceDistance"]=="0.005"
        assert values["AutoTP_NetRR_Enabled"]=="true" and values["NetRR_Target"]=="2"
    zero=master_service._pepperstone_risk_buffer_preflight(stop_points=2,risk_target=Decimal("10"))
    assert zero["planned_buffer_points"]==0 and zero["compatible"] is True
    write_snapshot(primary,fields={"bid":"150.000","ask":"150.010","stops_level":2,"freeze_level":0})
    assert master_service._pepperstone_stop_guidance(**common)["minimum_ticks"]==12
    write_snapshot(primary,stamp=(current-timedelta(seconds=120)).isoformat(),fields={"trade_tick_size":"0.002"})
    pending=master_service._pepperstone_stop_guidance(**common)
    assert pending["state"]=="pending" and pending["minimum_ticks"]==2 and pending["step_ticks"]==2
    assert pending["buffer_policy"]["distance_cap_numerator"]==1 and pending["buffer_policy"]["distance_cap_denominator"]==3


    # Opt-in confirms Standard as the source; the coherent adjusted quote is preview-only.
    with monkeypatch.context() as patch:
        async def estimate_meta(**kwargs):
            jpy = kwargs["symbol"].endswith("_JPY")
            return dict(displayPrecision=3 if jpy else 5, pipLocation=-2 if jpy else -4,
                        tradeUnitsPrecision=0, minimumTradeSize="1", marginRate="0.05")
        patch.setattr(master_service, "_fetch_oanda_instrument_meta", estimate_meta)
        write_snapshot(primary, fields={"bid":"159.551", "ask":"159.562", "stops_level":0, "freeze_level":0})
        request = _pepperstone_quote_payload(symbol="USDJPY", stop_loss_ticks="5", pepperstone_account_type="razor", estimate_razor_from_standard=True)
        def calculate(**changes):
            return json.loads(asyncio.run(master_service.calculator_quote(dict(request, **changes))).body)
        info = json.loads(asyncio.run(master_service.calculator_instrument("fx", "demo", "USDJPY", "pepperstone", estimate_razor_from_standard=True)).body)
        quote = calculate()
        estimate = quote["pepperstone_razor_estimate"]
        assert estimate["available"] and estimate["label"] == "Estimated Razor from Standard"
        assert (estimate["bid"], estimate["ask"], estimate["spread"]) == ("159.556", "159.557", "0.001")
        assert info["pepperstone_razor_estimate"] == estimate or info["pepperstone_razor_estimate"]["bid"] == estimate["bid"]
        assert quote["entry_price"] == "159.557" and calculate(side="sell")["entry_price"] == "159.556"
        assert quote["spread_quote"] == "0.001" and quote["stop_price"] == "159.552"
        assert quote["pepperstone_stop_guidance"]["minimum_ticks"] == info["pepperstone_stop_guidance"]["minimum_ticks"] == 2
        assert quote["commission_per_lot_per_side_aud"] == "4.2" and quote["estimated_total_loss_aud"] == "10"
        assert "OANDA reference" in quote["market_data_source"]
        limit = calculate(order_type="limit", entry_price="159.540")
        assert Decimal(limit["entry_price"]) == Decimal("159.540") and Decimal(limit["stop_price"]) == Decimal("159.535")
        assert limit["stop_loss_price_distance"] == "0.005" and limit["spread_quote"] == "0.001"
        assert limit["pepperstone_stop_guidance"]["limit_entry_state"] == "valid"
        values = _parse_set_file(asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(request, quote))).body.decode())
        assert values["SL_PriceDistance"] == "0.005" and Decimal(values["CommissionPerLotPerSide"]) == Decimal("4.2")
        for changes in ({"estimate_razor_from_standard":False}, {"pepperstone_account_type":"standard"}):
            untouched = calculate(**changes)
            assert "pepperstone_razor_estimate" not in untouched and untouched["entry_price"] == "1.3"
        write_snapshot(primary, fields={"bid":"159.551", "ask":"159.561", "stops_level":0, "freeze_level":0})
        zero = calculate()
        assert zero["pepperstone_razor_estimate"]["available"] and zero["spread_quote"] == "0"
        assert zero["pepperstone_stop_guidance"]["minimum_ticks"] == 1
        for kwargs in [dict(fields={"bid":"159.551","ask":"159.560"}), dict(mode="live"),
                       dict(stamp=(current-timedelta(seconds=120)).isoformat()), dict(fields={"bid":None})]:
            write_snapshot(primary, **kwargs)
            unavailable = calculate()
            assert not unavailable["pepperstone_razor_estimate"]["available"]
            assert unavailable["pepperstone_stop_guidance"]["state"] == "pending"
            assert "OANDA reference calculation" in unavailable["market_data_source"]
        primary.unlink()
        assert not calculate()["pepperstone_razor_estimate"]["available"]
        write_snapshot(primary, fields={"symbol":"GBPUSD", "mt5_symbol":"GBPUSD.a", "point":"0.00001", "trade_tick_size":"0.00001",
                                        "bid":"1.30000", "ask":"1.30012", "stops_level":0, "freeze_level":0})
        gbp = calculate(symbol="GBPUSD", stop_loss_ticks="5")
        assert (gbp["pepperstone_razor_estimate"]["bid"],gbp["pepperstone_razor_estimate"]["ask"]) == ("1.30005","1.30007")
        assert gbp["spread_quote"] == "0.00002" and gbp["entry_price"] == "1.30007"
        async def unknown_pip(**kwargs):
            data = await estimate_meta(**kwargs);data.pop("pipLocation");return data
        patch.setattr(master_service, "_fetch_oanda_instrument_meta", unknown_pip)
        unknown = calculate(symbol="GBPUSD")
        assert not unknown["pepperstone_razor_estimate"]["available"] and "pip size" in unknown["pepperstone_razor_estimate"]["reason"]
        assert unknown["pepperstone_stop_guidance"]["state"] == "pending"
    assert calls["account_summary"] == 0


def test_pepperstone_portable_set_survives_missing_metadata_and_rejects_invalid_quote_before_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_path = tmp_path / "not-created.json"
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    calls = _mock_pepperstone_quote_dependencies(monkeypatch)
    instrument = json.loads(asyncio.run(master_service.calculator_instrument("fx", "demo", "GBPUSD", "pepperstone")).body.decode("utf-8"))
    assert instrument["symbol"] == "GBP_USD"
    assert instrument["chart_tick_size_default"] == "0.00001"
    assert instrument["chart_tick_profile_id"] == "pepperstone-gbpusd-tradingview-screenshot-v1"
    assert "Pepperstone GBPUSD TradingView chart" in instrument["chart_tick_size_source"]
    assert instrument["pepperstone_mt5_point_status"]["available"] is False
    assert "mt5_point_size" not in instrument

    manual_request = _pepperstone_quote_payload(target_mode="ticks", take_profit_ticks="25", risk_reward="99")
    manual_quote = json.loads(asyncio.run(master_service.calculator_quote(manual_request)).body.decode("utf-8"))
    assert Decimal(manual_quote["quantity"]) > 0
    assert manual_quote["chart_tick_size"] == "0.00001"
    assert manual_quote["chart_tick_profile_id"] == instrument["chart_tick_profile_id"]
    conversion = manual_quote["pepperstone_distance_conversion"]
    assert conversion["stop_loss_ticks"] == 43
    assert conversion["stop_loss_price_distance"] == "0.00043"
    assert conversion["take_profit_ticks"] == 25
    assert conversion["take_profit_price_distance"] == "0.00025"
    assert "stop_loss_mt5_points" not in conversion
    assert manual_quote["pepperstone_mt5_export_status"]["file_ready"] is True
    assert manual_quote["pepperstone_mt5_export_status"]["broker_check_state"] == "pending"
    assert manual_quote["pepperstone_risk_buffer_preflight"]["state"] == "pending"
    assert "margin_available_home" not in manual_quote
    assert calls["account_summary"] == 0

    set_payload = _pepperstone_set_payload_from_quote(manual_request, manual_quote)
    token_calls = []
    real_uuid4 = master_service.uuid4
    def tracked_uuid4():
        token_calls.append(True)
        return real_uuid4()
    monkeypatch.setattr(master_service, "uuid4", tracked_uuid4)
    forged = dict(set_payload, pepperstone_mt5_export_status={"ready": True}, mt5_point_size="0.00001")
    with pytest.raises(master_service.HTTPException, match="server-owned"):
        master_service._build_pepperstone_trader_set(forged)
    assert token_calls == []
    with pytest.raises(master_service.HTTPException, match="chart profile changed"):
        master_service._build_pepperstone_trader_set(dict(set_payload, quoted_chart_tick_profile_id="forged-profile"))
    with pytest.raises(master_service.HTTPException, match="whole number"):
        master_service._build_pepperstone_trader_set(dict(set_payload, stop_loss_ticks="43.5"))
    with pytest.raises(master_service.HTTPException, match="Calculate a Pepperstone quote"):
        master_service._build_pepperstone_trader_set(dict(set_payload, calculation_context_id=""))
    assert token_calls == []
    edited = dict(set_payload, stop_loss_ticks="44")
    with pytest.raises(master_service.HTTPException, match="no longer match"):
        master_service._build_pepperstone_trader_set(edited)
    assert token_calls == []
    saved_snapshot = master_service._get_pepperstone_quote_snapshot(manual_quote["calculation_context_id"])
    with master_service._PEPPERSTONE_QUOTE_SNAPSHOTS_LOCK:
        master_service._PEPPERSTONE_QUOTE_SNAPSHOTS[manual_quote["calculation_context_id"]]["stored_at"] = time.time() - master_service._PEPPERSTONE_QUOTE_SNAPSHOT_TTL_SECONDS - 1
    with pytest.raises(master_service.HTTPException, match="expired"):
        master_service._build_pepperstone_trader_set(set_payload)
    assert token_calls == []
    master_service._store_pepperstone_quote_snapshot(manual_quote["calculation_context_id"], saved_snapshot)

    manual_response = asyncio.run(master_service.calculator_pepperstone_set(set_payload))
    manual_values = _parse_set_file(manual_response.body.decode("utf-8"))
    assert manual_values["UsePriceDistanceInputs"] == "true"
    assert manual_values["DistanceSymbol"] == "GBP_USD"
    assert manual_values["SL_PriceDistance"] == "0.00043"
    assert manual_values["TP_PriceDistance"] == "0.00025"
    assert manual_values["SL_DistancePoints"] == "0"
    assert manual_values["TP_DistancePoints"] == "0"
    assert manual_values["AutoTP_NetRR_Enabled"] == "false"
    assert manual_values["OrdersEnabled"] == "true"
    assert manual_values["UseDesktopTraderControls"] == "true"
    assert manual_values["TrendlineObjectName"] == ""
    assert manual_values["TrendlineArmGeneration"] == "0"
    assert len(token_calls) == 1

    # Optional metadata appearing or changing after calculation does not invalidate a portable quote.
    changed_fixture = _write_pepperstone_point_export(tmp_path, [_pepperstone_point_entry("GBPUSD", "GBPUSD.a", "0.00002")])
    export_path.write_text(changed_fixture.read_text(encoding="utf-8"), encoding="utf-8")
    again = asyncio.run(master_service.calculator_pepperstone_set(set_payload))
    assert b"SL_PriceDistance=0.00043" in again.body
    assert len(token_calls) == 2

    net_request = _pepperstone_quote_payload(target_mode="rr", risk_reward="2")
    net_quote = json.loads(asyncio.run(master_service.calculator_quote(net_request)).body.decode("utf-8"))
    net_response = asyncio.run(master_service.calculator_pepperstone_set(_pepperstone_set_payload_from_quote(net_request, net_quote)))
    net_values = _parse_set_file(net_response.body.decode("utf-8"))
    assert net_values["SL_PriceDistance"] == "0.00043"
    assert net_values["TP_PriceDistance"] == "0"
    assert net_values["AutoTP_NetRR_Enabled"] == "true"
    assert net_values["NetRR_Target"] == "2"
    assert net_values["OrdersEnabled"] == "true"
    assert calls["account_summary"] == 0


def test_calculator_webhook_direct_dict_call_uses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: (_ for _ in ()).throw(ValueError("Pending webhook missing or no longer active.")))
    response = asyncio.run(master_service.calculator_webhook({"asset": "crypto", "pending_webhook_id": "bogus", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 409
    assert body.get("code") == "PENDING_WEBHOOK_NOT_FOUND"


def test_bybit_signed_get_signature_supports_timeout_args() -> None:
    import inspect
    sig = inspect.signature(master_service._bybit_signed_get)
    assert "timeout_s" in sig.parameters
    assert "connect_s" in sig.parameters
    assert "read_s" in sig.parameters




def test_fetch_bybit_balance_usdt_signature_supports_timeout_args() -> None:
    import inspect
    sig = inspect.signature(master_service._fetch_bybit_balance_usdt)
    assert "timeout_s" in sig.parameters
    assert "connect_s" in sig.parameters
    assert "read_s" in sig.parameters
def test_fetch_bybit_balance_usdt_passes_coin_and_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    calls = {}

    async def fake_signed_get(**kwargs):
        calls.update(kwargs)
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "900", "coin": [{"coin": "USDT", "availableToTrade": "900"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    out = asyncio.run(master_service._fetch_bybit_balance_usdt("demo", timeout_s=2.5, connect_s=1.0, read_s=2.5))
    assert out["available_usdt"] == master_service.Decimal("900")
    assert calls["path"] == "/v5/account/wallet-balance"
    assert calls["params"]["accountType"] == "UNIFIED"
    assert calls["params"]["coin"] == "USDT"
    assert calls["timeout_s"] == 2.5
    assert calls["connect_s"] == 1.0
    assert calls["read_s"] == 2.5


def test_crypto_quote_backend_timeout_beats_frontend_timeout() -> None:
    js = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    import re
    m = re.search(r"quoteTimeoutMs\s*=\s*(\d+)", js)
    assert m
    frontend_timeout_ms = int(m.group(1))
    backend_timeout_ms = int(master_service.CALCULATOR_QUOTE_TIMEOUT_S * 1000)
    assert frontend_timeout_ms <= 15000
    assert backend_timeout_ms <= 4500
    assert backend_timeout_ms < frontend_timeout_ms


def test_webhook_attempts_filter_by_pending_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [{"pending_webhook_id":"a","status":"X"},{"pending_webhook_id":"b","status":"Y"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_attempts(limit=50,pending_webhook_id="b")).body.decode("utf-8"))
    assert payload["matched_count"] == 1
    assert payload["items"][0]["pending_webhook_id"] == "b"


def test_webhook_diagnostic_status_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [])
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "NO_RENDER_ATTEMPT_RECORDED"

    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [{"id":"pid1"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "WAITING_NO_POST_RECEIVED"

    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [{"pending_webhook_id":"pid1","status":"BYBIT_REJECTED","bybit_ret_code":1001,"bybit_ret_msg":"bad","order_id":"o1","order_link_id":"l1"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "BYBIT_REJECTED"
    assert payload["bybit_ret_code"] == 1001
    assert payload["bybit_ret_msg"] == "bad"


def test_calculator_webhook_capability_remote_render(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://codex-rdqh.onrender.com")
    req=Request({"type":"http","method":"GET","path":"/","headers":[],"query_string":b"","client":("127.0.0.1",1),"server":("localhost",80),"scheme":"http"})
    cap=master_service._calculator_webhook_capability(req)
    assert cap["mode"] == "remote_render"

def test_calculator_submit_bybit_rejection_returns_400_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(_payload, request_id):
        raise master_service.BybitOrderRejected(ret_code=10001, ret_msg='request parameter error', ret_ext_info={'x':1}, result={}, request_body={'symbol':'BTCUSDT'}, http_status=200, response_body={'retCode':10001})

    monkeypatch.setattr(master_service, '_place_bybit_order', fake_bybit)
    resp = asyncio.run(master_service.calculator_submit({'asset':'crypto','account':'demo','symbol':'BTCUSDT','side':'buy','order_type':'limit'}))
    assert resp.status_code == 400
    body = json.loads(resp.body.decode('utf-8'))
    assert body['code'] == 'BYBIT_REJECTED'
    assert body['debug']['ret_code'] == 10001


def test_calculator_quote_rejects_buy_limit_above_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _a: ('live','k','s','https://bybit.test','KEY1'))
    monkeypatch.setattr(master_service, '_bybit_get_symbols_by_category_cached', lambda *_a, **_k: asyncio.sleep(0, result=['PARTIUSDT']))
    monkeypatch.setattr(master_service, '_fetch_bybit_balance_usdt', lambda *_a, **_k: asyncio.sleep(0, result={'available_usdt':'1000','total_equity':'1000'}))
    monkeypatch.setattr(master_service, '_fetch_oanda_mid_prices_batch', lambda **_k: asyncio.sleep(0, result={'AUD_USD':0.5}))
    async def fake_inst(*_args, **_kwargs):
        return {'priceFilter': {'tickSize':'0.0001'}, 'lotSizeFilter': {'qtyStep':'1','minOrderQty':'1','maxOrderQty':'999999','maxMktOrderQty':'999999','minNotionalValue':'0'}, 'leverageFilter': {'maxLeverage':'50'}}
    monkeypatch.setattr(master_service, '_bybit_get_instrument_info_cached', fake_inst)
    async def fake_get(base, path, params, **_kwargs):
        if path.endswith('tickers'):
            return {'result': {'list': [{'bid1Price':'0.4938','ask1Price':'0.4940','lastPrice':'0.4939'}]}}
        raise AssertionError(path)
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({'asset':'crypto','account':'live','symbol':'PARTI','side':'buy','order_type':'limit','entry_price':'0.5313','risk_mode':'percent','risk_value':1,'stop_loss_ticks':37,'take_profit_ticks':74}))
    assert exc.value.status_code == 400
    assert exc.value.detail['code'] in {'BYBIT_LIMIT_WOULD_FILL_IMMEDIATELY','BYBIT_STOP_LOSS_INVALID_FOR_BUY'}

def _mock_bybit_quote_env(monkeypatch, *, tick='0.00001', bid='0.04921', ask='0.04923', last='0.04922'):
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _a: ('live','k','s','https://bybit.test','KEY1'))
    monkeypatch.setattr(master_service, '_bybit_get_symbols_by_category_cached', lambda *_args, **_kwargs: asyncio.sleep(0, result=['PARTIUSDT','BTCUSDT']))
    monkeypatch.setattr(master_service, '_fetch_oanda_mid_prices_batch', lambda **_kwargs: asyncio.sleep(0, result={'AUD_USD': 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith('instruments-info'):
            return {'result': {'list': [{'priceFilter': {'tickSize': tick}, 'lotSizeFilter': {'qtyStep': '1', 'minOrderQty': '1', 'maxOrderQty': '999999', 'maxMktOrderQty': '999999', 'minNotionalValue': '1'}, 'leverageFilter': {'maxLeverage': '50'}}]}}
        return {'result': {'list': [{'bid1Price': bid, 'ask1Price': ask, 'lastPrice': last}]}}
    async def fake_signed_get(**kwargs):
        if kwargs.get('path','').endswith('fee-rate'):
            return {'result': {'list': [{'makerFeeRate': '0.0002', 'takerFeeRate': '0.00055'}]}}
        return {'result': {'list': [{'totalEquity': '10000', 'totalAvailableBalance': '10000', 'coin': [{'coin': 'USDT', 'availableToTrade': '10000'}]}]}}
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    monkeypatch.setattr(master_service, '_bybit_signed_get', fake_signed_get)


def _mock_pending_limit_replay(monkeypatch, *, symbol, tick, last):
    # All quote/order dependencies are frozen; no account, market, context or folder IO.
    service = master_service
    state = {"last": last, "posts": [], "reject": False, "contexts": []}
    meta = {"symbol": symbol, "priceFilter": {"tickSize": tick},
            "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999",
                              "maxMktOrderQty": "999999", "minNotionalValue": "0"},
            "leverageFilter": {"maxLeverage": "50"}}
    monkeypatch.setattr(service, "resolve_bybit_credentials_for", lambda a: (a, "mock", "mock", "https://bybit.invalid", "mock"))
    monkeypatch.setenv("CALCULATOR_BYBIT_MAKER_FEE_FALLBACK", "0.0006")
    monkeypatch.setenv("CALCULATOR_BYBIT_TAKER_FEE_FALLBACK", "0.0006")
    async def resolve(*a, **k): return symbol, meta, {"resolution_status": "exact"}
    async def instrument(*a, **k): return meta
    async def ticker(*a, **k):
        # Bid/ask are synthetic, not captured historical QUSDT evidence; limit spread is zero.
        return {"payload": {"result": {"list": [{"bid1Price": state["last"], "ask1Price": state["last"], "lastPrice": state["last"]}]}}}
    async def wallet(*a, **k): return {"available_usdt": "298", "total_equity": "298"}, {}
    async def get(*a, **k):
        assert a[1] == "/v5/market/tickers"
        return (await ticker())["payload"]
    async def post(**k):
        assert k["path"] == "/v5/order/create"
        state["posts"].append(dict(k["body"]))
        return {"retCode": 10001 if state["reject"] else 0, "retMsg": "fixture", "result": {"orderId": "fixture-order"}}
    def forbidden(*a, **k): raise AssertionError("Unexpected external IO")
    for name, fn in (("_resolve_bybit_calculator_symbol_fast", resolve),
                     ("_bybit_lookup_linear_symbol_with_fallback", instrument),
                     ("_fetch_bybit_ticker_cached", ticker), ("_fetch_bybit_balance_usdt_cached", wallet),
                     ("_bybit_get_async", get), ("_bybit_signed_post", post)):
        monkeypatch.setattr(service, name, fn)
    for name in ("_upsert_calculator_trade_context", "_upsert_bybit_demo_calc_context"):
        monkeypatch.setattr(service, name, lambda payload, **k: state["contexts"].append(dict(payload)))
    for name in ("_log_webhook_event", "cache_bybit_demo_tpsl_request", "_invalidate_open_orders_cache", "_schedule_dropbox_upload_state_backup"):
        monkeypatch.setattr(service, name, lambda *a, **k: None)
    monkeypatch.setattr(service, "_bybit_early_folder_lifecycle", lambda **k: (False, {}))
    monkeypatch.setattr(service, "_upsert_pending_webhook", lambda payload, **k: dict(payload))
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_accepted_calculator_intent", lambda payload, **k: dict(payload))
    for name in ("_save_json_file", "_bybit_signed_get", "_bybit_market_get_async", "_fetch_oanda_mid_prices_batch"):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(service.httpx, "AsyncClient", forbidden)
    return state


def test_bybit_sell_limit_preserves_requested_take_profit_or_requires_requote(monkeypatch):
    state = _mock_pending_limit_replay(monkeypatch, symbol="PARTIUSDT", tick="0.00001", last="0.04922")
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-webhook.test")
    payload = dict(asset="crypto", account="demo", symbol="PARTIUSDT", side="sell", order_type="limit",
                   entry_price="0.05313", risk_mode="percent", risk_value=1, stop_loss_ticks=37,
                   take_profit_ticks=94, webhook="yes")
    body = json.loads(asyncio.run(master_service.calculator_quote(payload)).body)
    assert body["target_price"] == "0.05219"  # Above LastPrice, below future sell entry.
    assert body["take_profit_adjusted"] is False
    wh = json.loads(body["webhook_payload_json"])
    assert wh["take_profit_price"] == body["target_price"]
    state["last"] = "0.048"
    result = asyncio.run(master_service._place_bybit_order(wh, request_id="fixture"))
    assert result["tpsl"]["status"] == "attached_to_order_create"
    assert Decimal(state["posts"][-1]["takeProfit"]) == Decimal("0.05219")
    state["last"] = "0.054"  # Entry now crosses market: existing re-quote validation, no create.
    with pytest.raises(master_service.BybitPreSubmitValidationError) as exc:
        asyncio.run(master_service._place_bybit_order(wh, request_id="fixture-stale"))
    assert exc.value.code == "BYBIT_LIMIT_WOULD_FILL_IMMEDIATELY"
    assert len(state["posts"]) == 1


def test_bybit_buy_limit_preserves_requested_take_profit_or_requires_requote(monkeypatch):
    state = _mock_pending_limit_replay(monkeypatch, symbol="BTCUSDT", tick="0.1", last="100")
    body = json.loads(asyncio.run(master_service.calculator_quote(dict(asset="crypto", account="demo", symbol="BTCUSDT", side="buy", order_type="limit", entry_price="95", risk_mode="percent", risk_value=1, stop_loss_ticks=20, take_profit_ticks=10))).body)
    assert body["target_price"] == "96"
    assert body["take_profit_adjusted"] is False
    state["last"] = "105"
    args = dict(symbol="BTCUSDT", side="buy", order_type="limit", entry_price=Decimal("95"),
                stop_loss=Decimal("93"), take_profit=Decimal(body["target_price"]),
                tick_size=Decimal("0.1"), source_context="fixture")
    _, sl, tp, diag = master_service._make_bybit_levels_submit_safe(**args, last_price=Decimal("105"))
    assert (sl, tp) == (Decimal("93"), Decimal("96"))
    assert not diag.get("submit_take_profit_auto_adjusted")
    with pytest.raises(master_service.BybitPreSubmitValidationError) as exc:
        master_service._make_bybit_levels_submit_safe(**args, last_price=Decimal("94"))
    assert exc.value.code == "BYBIT_LIMIT_WOULD_FILL_IMMEDIATELY"
    for invalid_tp in ("95", "94"):
        with pytest.raises(master_service.BybitPreSubmitValidationError):
            master_service._make_bybit_levels_submit_safe(**dict(args, take_profit=Decimal(invalid_tp)), last_price=Decimal("105"))


def test_qusdt_limit_target_replay_preserves_requested_and_submitted_levels(monkeypatch):
    # Sanitized exact saved quote joined through accepted order/link -> calculation_context_id.
    # B independently records these submitted prices and quantity, retCode=0.
    historical = dict(entry="0.10191", stop="0.0944", target="0.13579", quantity="390",
                      before="0.12111", last="0.13578", tick="0.00001",
                      total_loss="2.97483654", reward="13.1575782")
    d = Decimal
    e, sl, tp, qty, tick = (d(historical[k]) for k in ("entry", "stop", "target", "quantity", "tick"))
    loss = d(historical["total_loss"]) / qty
    reward = d(historical["reward"]) / qty
    # Infer effective rates from two independent saved monetary fields, not today's config.
    close_fee = ((tp-e-reward) - (loss-(e-sl))) / (tp-sl)
    open_fee = (loss-(e-sl)-sl*close_fee) / e
    assert (open_fee, close_fee) == (d("0.0006"), d("0.0006"))
    assert d(751) * d("2.5") == d("1877.5")
    raw_tp = (d("2.5")*loss + e*(1+open_fee)) / (1-close_fee)
    assert abs(raw_tp-d("0.1211132789673804282569541725")) < d("1e-16")
    assert (d(historical["before"])-e)/tick == 1920
    assert (tp-d(historical["before"]))/tick == 1468
    assert (tp-e)/tick == 3388
    assert abs((tp-e)/(e-sl)-d("4.5113182423")) < d("1e-10")
    assert abs(reward/loss-d("4.4229583787")) < d("1e-10")
    state = _mock_pending_limit_replay(monkeypatch, symbol="QUSDT", tick=historical["tick"], last=historical["last"])
    payload = dict(asset="crypto", account="demo", symbol="QUSDT", side="buy", order_type="limit",
                   entry_price=historical["entry"], risk_mode="percent", risk_value="1",
                   stop_loss_ticks="751", target_mode="rr", risk_reward="2.5")
    body = json.loads(asyncio.run(master_service.calculator_quote(payload)).body)
    assert body["quantity"] == historical["quantity"]
    assert body["entry_price"] == historical["entry"] and body["stop_price"] == historical["stop"]
    assert body["target_price"] == historical["before"]
    assert body["requested_target_price_before_adjustment"] == body["target_price"]
    assert body["requested_rr_net"] == "2.5" and body["effective_rr_net"] == "2.5"
    assert body["take_profit_adjusted"] is False
    assert d(body["estimated_total_loss"]) == qty*loss  # Fees remain in risk sizing.
    expected_reward = qty*(d(body["target_price"])-e-e*open_fee-d(body["target_price"])*close_fee)
    assert d(body["estimated_reward"]) == expected_reward
    assert abs(expected_reward/(qty*loss)-d("2.4995703865")) < d("1e-10")
    submit = dict(payload, action="buy", quantity=body["quantity"], stop_loss_price=body["stop_price"],
                  take_profit_price=body["target_price"], calculation_context_id=body["calculation_context_id"])
    state["last"] = "0.15"  # Changed market must not relocate the accepted pending TP.
    response = asyncio.run(master_service.calculator_submit(submit))
    assert response.status_code == 200
    posted = state["posts"][-1]
    assert (posted["price"], posted["stopLoss"], posted["takeProfit"]) == (historical["entry"], historical["stop"], historical["before"])
    assert (posted["tpslMode"], posted["tpOrderType"], posted["slOrderType"]) == ("Full", "Market", "Market")
    state["reject"] = True
    response = asyncio.run(master_service.calculator_submit(submit))
    assert response.status_code == 400 and json.loads(response.body)["code"] == "BYBIT_REJECTED"
    assert len(state["posts"]) == 2  # Rejection cannot retry without protection.
    assert all(p["takeProfit"] == historical["before"] and p["stopLoss"] == historical["stop"] for p in state["posts"])


def test_bybit_wallet_retcode_33004_maps_to_api_key_expired_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_signed_get(**_kwargs):
        raise ValueError("Bybit signed GET failed path=/v5/account/wallet-balance retCode=33004 retMsg=Your api key has expired. recv_window=15000")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY2"))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service._fetch_bybit_balance_usdt("demo"))
    d = exc.value.detail
    assert d["code"] == "BYBIT_API_KEY_EXPIRED" and d["account"] == "demo" and d["key_source"] == "KEY2"
    assert "BYBIT_API_KEY2/BYBIT_API_SECRET2" in d["message"]


def test_bybit_wallet_retcode_33004_maps_to_api_key_expired_live(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_signed_get(**_kwargs):
        raise ValueError("retCode=33004 retMsg=Your api key has expired")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service._fetch_bybit_balance_usdt("live"))
    d = exc.value.detail
    assert d["code"] == "BYBIT_API_KEY_EXPIRED" and d["account"] == "live" and d["key_source"] == "KEY1"
    assert "BYBIT_API_KEY1/BYBIT_API_SECRET1" in d["message"]


def test_bybit_expired_key_error_includes_key_source_without_secret() -> None:
    d = master_service._classify_bybit_api_key_expired("retCode=33004 retMsg=Your api key has expired", account="demo", key_source="LEGACY")
    assert d and d["key_source"] == "LEGACY"
    assert "bybit_api_secret2" in str(d.get("message", "")).lower()


def test_bybit_expired_key_error_prioritized_over_ticker_timeout() -> None:
    detail = master_service._calculator_quote_error_detail("BYBIT_API_KEY_EXPIRED", "x", dependency="bybit_wallet_balance", timings_ms={"bybit_ticker": {"status": "timeout"}})
    assert detail["code"] == "BYBIT_API_KEY_EXPIRED"


def test_demo_quote_context_save_failure_returns_structured_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY2"))
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("disk locked")))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_a, **_k: asyncio.sleep(0, result=("BTCUSDT", {"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}, {"resolution_status": "exact"})))
    monkeypatch.setattr(master_service, "_fetch_bybit_ticker_cached", lambda *_a, **_k: asyncio.sleep(0, result={"payload": {"result": {"list": [{"bid1Price": "65000", "ask1Price": "65001", "lastPrice": "65000.5"}]}}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", lambda *_a, **_k: asyncio.sleep(0, result=({"available_usdt": "10000", "total_equity": "10000"}, {"wallet_cache_status": "hit", "wallet_cache_age_ms": 1})))

    payload = {"asset": "crypto", "account": "demo", "symbol": "BTCUSDT", "webhook": "no", "test": "yes", "timeframe": "1h", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1111, "risk_reward": 2, "order_type": "market", "side": "buy"}
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote(payload))
    assert exc.value.status_code == 502
    detail = exc.value.detail
    assert detail["code"] == "BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED"
    assert detail["debug"]["pending_dependencies"] == []
    assert detail["debug"]["submitted_payload"]["account"] == "demo"
    assert detail["debug"]["resolved_symbol"] == "BTCUSDT"
    assert "bybit_wallet_balance" in detail["debug"]["upstream_timings_ms"]
    assert detail["debug"]["upstream_timings_ms"]["bybit_wallet_balance"]["status"] == "ok"

def test_fetch_bybit_ticker_cached_invalid_live_uses_stale_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"{master_service._bybit_base_key('https://api-demo.bybit.com')}:linear:BTCUSDT"
    valid_payload = {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99", "ask1Price": "101"}]}}
    master_service._BYBIT_TICKER_CACHE[key] = {"ts": master_service.time.time(), "payload": valid_payload, "source": "seed"}

    async def fake_get(*_args, **_kwargs):
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    out = asyncio.run(master_service._fetch_bybit_ticker_cached("https://api-demo.bybit.com", "linear", "BTCUSDT", max_age_s=0.0, allow_stale_s=45.0, timeout_s=0.1))
    assert out["cache_status"] == "stale_fallback"
    assert "invalid" in str(out.get("ticker_cache_error", "")).lower()
    assert master_service._BYBIT_TICKER_CACHE[key]["payload"] == valid_payload


def test_fetch_bybit_ticker_cached_invalid_live_uses_public_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_get(base_url, path, params, **_kwargs):
        calls.append(base_url)
        if "demo" in base_url:
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99", "ask1Price": "101"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    out = asyncio.run(master_service._fetch_bybit_ticker_cached("https://api-demo.bybit.com", "linear", "BTCUSDT", max_age_s=0.0, allow_stale_s=0.0, timeout_s=0.1, public_fallback_base_url="https://api.bybit.com"))
    assert out["cache_status"] == "live_fallback"
    assert out["ticker_source"] == "public_market_fallback"
    assert any("api-demo" in c for c in calls) and any("api.bybit.com" in c for c in calls)


def test_calculator_quote_invalid_ticker_payload_without_fallback_fails_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k2", "s2", "https://api-demo.bybit.com", "KEY2"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_inst(*_args, **_kwargs):
        return {"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1000", "maxMktOrderQty": "1000", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}

    async def fake_ticker(*_args, **_kwargs):
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}

    async def fake_wallet(*_args, **_kwargs):
        return ({"available_usdt": master_service.Decimal("1000"), "total_equity": master_service.Decimal("1000")}, {"wallet_cache_status": "live"})

    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_args, **_kwargs: asyncio.sleep(0, result=("BTCUSDT", None, {})))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", fake_inst)
    async def fake_ticker_cached(*_args, **_kwargs):
        return {"payload": {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}, "cache_status": "live", "ticker_cache_error": "Bybit ticker payload invalid for BTCUSDT"}

    monkeypatch.setattr(master_service, "_fetch_bybit_ticker_cached", fake_ticker_cached)
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", fake_wallet)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("dependency") == "bybit_ticker"
    assert detail.get("code") in {"BYBIT_TICKER_INVALID", "BYBIT_TICKER_TIMEOUT"}
    assert detail.get("dependency") == "bybit_ticker"


def test_calculator_quote_invalid_ticker_payload_from_real_fetch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k2", "s2", "https://api-demo.bybit.com", "KEY2"))

    async def fake_get(base_url, path, params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_wallet(*_args, **_kwargs):
        return ({"available_usdt": master_service.Decimal("1000"), "total_equity": master_service.Decimal("1000")}, {"wallet_cache_status": "live"})

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_args, **_kwargs: asyncio.sleep(0, result=("BTCUSDT", None, {})))
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result={"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1000", "maxMktOrderQty": "1000", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", fake_wallet)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") in {"BYBIT_TICKER_INVALID", "INTERNAL_CALCULATOR_ERROR"}
    if detail.get("dependency") is not None:
        assert detail.get("dependency") == "bybit_ticker"


def test_specs_invalid_ticker_payload_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: {"base_url": "https://api-demo.bybit.com"})
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_args, **_kwargs: asyncio.sleep(0, result={"_category": "linear", "symbol": "BTCUSDT", "baseCoin": "BTC"}))
    monkeypatch.setattr(master_service, "_bybit_avg_7d_turnover_usd_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(master_service, "_bybit_fetch_range_specs_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=({}, [])))

    async def fake_get(base_url, path, params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    asyncio.run(master_service._bybit_resolve_and_fetch_specs("BTCUSDT", include_btc_reference=False))
    key = f"{master_service._bybit_base_key('https://api-demo.bybit.com')}:linear:BTCUSDT"
    assert key not in master_service._BYBIT_TICKER_CACHE


def test_no_calculator_ticker_error_strings_in_non_calculator_blocks() -> None:
    src = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    checks = ["BYBIT_TICKER_INVALID", "ticker payload invalid", "submitted_debug", "quote_started"]
    blocks = [
        ("async def script_view_page", '@app.get("/bybit-history")'),
        ("async def trade_chart_page", '@app.get("/api/logs")'),
        ("async def api_logs_root", '@app.get("/api/logs/{script_name:path}")'),
        ("async def read_script_results", '@app.post("/api/oanda-history/export")'),
    ]
    for start, end in blocks:
        i = src.index(start)
        j = src.find(end, i)
        if j == -1:
            j = len(src)
        block = src[i:j]
        for token in checks:
            assert token not in block

def test_calculator_submit_rejects_invalid_setup_422() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({"asset": "crypto", "setup": "bad-setup"}))
    assert exc.value.status_code == 422


def test_calculator_submit_rejects_invalid_vwap_422() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({"asset": "crypto", "vwap": "maybe"}))
    assert exc.value.status_code == 422
    assert "VWAP" in str(exc.value.detail)


def test_calculator_quote_and_webhook_reject_invalid_vwap_422(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(master_service.HTTPException) as quote_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto",
            "account": "demo",
            "symbol": "BTCUSDT",
            "side": "buy",
            "order_type": "market",
            "risk_mode": "percent",
            "risk_value": 1,
            "stop_loss_ticks": 1,
            "risk_reward": 2,
            "vwap": "maybe",
        }))
    assert quote_exc.value.status_code == 422
    assert "VWAP" in str(quote_exc.value.detail)

    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _payload: None)
    with pytest.raises(master_service.HTTPException) as webhook_exc:
        asyncio.run(master_service.calculator_webhook({
            "asset": "crypto",
            "pending_webhook_id": "wh-vwap",
            "account": "demo",
            "symbol": "BTCUSDT",
            "action": "buy",
            "order_type": "market",
            "quantity": "0.01",
            "vwap": "maybe",
        }))
    assert webhook_exc.value.status_code == 422
    assert "VWAP" in str(webhook_exc.value.detail)


def test_calculator_submit_passes_normalized_setup_to_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    async def fake_place(payload, request_id):
        seen["setup"] = payload.get("setup")
        seen["pattern"] = payload.get("pattern")
        seen["ema"] = payload.get("ema")
        seen["vwap"] = payload.get("vwap")
        seen["aths_atls"] = payload.get("aths_atls")
        seen["round_number"] = payload.get("round_number")
        return {"journal_context_saved": True}
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_place)
    resp = asyncio.run(master_service.calculator_submit({
        "asset":"crypto","account":"demo","symbol":"BTCUSDT","side":"buy","order_type":"market",
        "setup":"news scalp","pattern":"channel","ema":"yes",
        "vwap":"true","aths_atls":"ath","round_number":"no",
    }))
    body = json.loads(resp.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["setup"] == "News Scalp"
    assert seen["pattern"] == "channel"
    assert seen["ema"] == "Yes"
    assert seen["vwap"] == "Yes"
    assert seen["aths_atls"] == "All-Time High"
    assert seen["round_number"] == "No"


def test_trade_context_normalizes_and_backfills_quality_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])
    saved = {}
    monkeypatch.setattr(master_service, "_save_trade_contexts", lambda items: saved.setdefault("items", items))
    context = master_service._upsert_trade_context({
        "calculation_context_id": "criteria-1",
        "ema": "y",
        "vwap": False,
        "aths_atls": "all time low",
        "round_number": False,
    })
    assert context["ema"] == "Yes"
    assert context["vwap"] == "No"
    assert context["aths_atls"] == "All-Time Low"
    assert context["round_number"] == "No"
    merged = master_service._merge_bybit_demo_calc_context_into_row({"id": "row-1"}, context)
    assert merged["ema"] == "Yes"
    assert merged["vwap"] == "No"
    assert merged["aths_atls"] == "All-Time Low"
    assert merged["round_number"] == "No"


def test_oanda_rr_floor_survives_costs_conversion_sizing_and_submit_revalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    dec = master_service.Decimal
    fixed = master_service._oanda_rr_floor_levels(
        entry=dec("1.0002"), side="buy", units=dec("8333"), tick_size=dec("0.0001"),
        spread_quote=dec("0.0002"), loss_per_unit_home=dec("0.0012"), gain_factor=dec("0.8"),
        nominated_risk_home=dec("10"), requested_rr=dec("2"),
    )
    assert fixed["estimated_reward"] >= dec("20")
    assert fixed["estimated_total_loss"] <= dec("12")
    assert fixed["effective_rr_net"] >= dec("2")
    percent = master_service._oanda_rr_floor_levels(
        entry=dec("1.0002"), side="sell", units=dec("8333"), tick_size=dec("0.0001"),
        spread_quote=dec("0.0002"), loss_per_unit_home=dec("0.0012"), gain_factor=dec("0.8"),
        nominated_risk_home=dec("10"), requested_rr=dec("2"),
    )
    assert percent["estimated_reward"] >= dec("20")
    with pytest.raises(ValueError, match="120%"):
        master_service._oanda_rr_floor_levels(
            entry=dec("1"), side="buy", units=dec("11"), tick_size=dec("0.0001"),
            spread_quote=dec("0"), loss_per_unit_home=dec("1.1"), gain_factor=dec("1"),
            nominated_risk_home=dec("10"), requested_rr=dec("2"),
        )

    posts = []
    limits = {"maximum_position_size": "0"}
    summary_values = {
        "currency": "AUD",
        "nav": "1000",
        "marginAvailable": "10000",
        "marginRate": "0.10",
    }
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _account: {"base_url": "https://oanda.test", "account_id": "acct", "token": "token"})
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 4, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "maximumOrderUnits": "999999", "maximumPositionSize": limits["maximum_position_size"], "marginRate": "0"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _account: asyncio.sleep(0, result=dict(summary_values)))
    monkeypatch.setattr(master_service, "_get_oanda_quote_home_factors", lambda **_kwargs: (dec("0.8"), dec("1"), dec("1")))
    monkeypatch.setattr(master_service, "_convert_aud_to_home_currency", lambda amount, *_args: asyncio.sleep(0, result=amount))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.0000"}], "asks": [{"price": "1.0002"}]}]}))
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(master_service, "_log_webhook_event", lambda *_args, **_kwargs: None)

    class FakeResponse:
        status_code = 201
        text = "{}"
        def json(self): return {"orderCreateTransaction": {"id": "1"}}
    class FakeClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, url, **kwargs): posts.append((url, kwargs)); return FakeResponse()
    monkeypatch.setattr(master_service.httpx, "AsyncClient", FakeClient)
    payload = {"asset": "fx", "broker": "oanda", "symbol": "EUR_USD", "action": "buy", "quantity": "1000", "account": "demo", "order_type": "market", "stop_loss_price": "0.9990", "take_profit_price": "1.0020", "target_mode": "rr", "risk_reward": "2", "risk_mode": "percent", "risk_value": "1", "stop_loss_ticks": "10"}
    response = asyncio.run(master_service.calculator_submit(payload))
    response_body = json.loads(response.body.decode("utf-8"))
    assert len(posts) == 1
    order = posts[0][1]["json"]["order"]
    assert order["units"] == "8333"
    assert order["stopLossOnFill"]["price"] == "0.9992"
    assert dec(order["takeProfitOnFill"]["price"]) >= dec("1.0035")
    adjustments = response_body["submit_level_adjustments"]
    assert adjustments["fresh_revalidation"] is True
    assert adjustments["original_quantity"] == "1000"
    assert adjustments["adjusted_quantity"] == "8333"
    assert adjustments["original_take_profit_price"] == "1.002"
    assert adjustments["adjusted_take_profit_price"] == order["takeProfitOnFill"]["price"]
    assert adjustments["submit_take_profit_auto_adjusted"] is True
    assert dec(adjustments["minimum_net_reward"]) == dec("20")
    final_reward = (dec(order["takeProfitOnFill"]["price"]) - dec("1.0002") - dec("0.0002")) * dec("0.8") * dec(order["units"])
    assert final_reward >= dec(adjustments["minimum_net_reward"])
    assert response_body["result"]["orderCreateTransaction"]["id"] == "1"

    # Both refreshed capacity limits fail closed before another broker POST.
    posts.clear()
    limits["maximum_position_size"] = "8000"
    with pytest.raises(ValueError, match="maximumPositionSize"):
        asyncio.run(master_service._place_oanda_order(payload, request_id="rr-position-cap", invalidate_cache=False))
    assert posts == []

    limits["maximum_position_size"] = "0"
    summary_values["marginAvailable"] = "1"
    with pytest.raises(ValueError, match="marginAvailable"):
        asyncio.run(master_service._place_oanda_order(payload, request_id="rr-margin-cap", invalidate_cache=False))
    assert posts == []


def _trendline_route_fixture_executor(store):
    executor = master_service.TrendlinePlanExecutor(store, lambda *a: {}, lambda *a: {}, lambda *a: {}, master_service.live_execution_authorized)
    async def idle(): await executor._stop.wait()
    executor._run = idle
    return executor


def test_local_trendline_plan_crud_is_profile_gated_and_preserves_calculator_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = master_service.TrendlinePlanStore(tmp_path / "trendline_plans.json")
    monkeypatch.setattr(master_service, "TRENDLINE_PLAN_STORE", store)
    executor = _trendline_route_fixture_executor(store)
    monkeypatch.setattr(master_service, "TRENDLINE_EXECUTOR", executor)

    async def forbidden_external(*_args, **_kwargs):
        raise AssertionError("Trendline CRUD must not call external services")

    monkeypatch.setattr(master_service, "_fetch_oanda_json", forbidden_external)
    payload = {
        "asset": "fx", "broker": "oanda", "account": "demo", "instrument": "usdjpy",
        "side": "buy", "order_type": "market", "test_trade": True,
        "anchors": [{"timestamp_ms": 1_000, "price": "155.10"}, {"timestamp_ms": 2_000, "price": "155.20"}],
        "right_extension": True, "trigger_mode": "touch", "cross_direction": "either",
        "trigger_price_basis": "executable", "tolerance_ticks": 2, "expiry_at_ms": None,
        "risk_mode": "percent", "risk_value": "1", "stop_loss_ticks": 10, "rr_target": "2",
        "timeframe": "15m", "setup": "Pullback", "pattern": "channel", "ema": "20",
        "vwap": "No", "aths_atls": "No", "round_number": "Yes",
    }
    for profile in ("render", "journal"):
        monkeypatch.setattr(master_service, "APP_PROFILE", profile)
        with pytest.raises(master_service.HTTPException) as exc:
            asyncio.run(master_service.trendline_plan_list())
        assert exc.value.status_code == 410
        assert not store.path.exists()

    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    created_response = asyncio.run(master_service.trendline_plan_create(payload))
    created = json.loads(created_response.body.decode("utf-8"))["plan"]
    assert created["instrument"] == "USD_JPY"
    assert created["action"] == "buy" and created["order_intent"] == "market"
    assert created["test_trade"] is True and created["risk_mode"] == "percent"
    assert created["order_metadata"]["setup"] == "Pullback"
    assert created["max_quote_age_ms"] == 60_000 and created["max_observation_gap_ms"] == 300_000
    assert created["status"] == "draft"
    assert json.loads(asyncio.run(master_service.trendline_plan_list()).body.decode("utf-8"))["monitoring_active"] is False

    updated = json.loads(asyncio.run(master_service.trendline_plan_update(created["plan_id"], {"tolerance_ticks": 3, "risk_value": "1.5"})).body.decode("utf-8"))["plan"]
    assert updated["tolerance_ticks"] == 3 and updated["risk_value"] == "1.5" and updated["instrument"] == "USD_JPY"
    armed = json.loads(asyncio.run(master_service.trendline_plan_arm(created["plan_id"], {"revision":updated["revision"],"monitor_generation":executor.generation})).body.decode("utf-8"))["plan"]
    assert armed["status"] == "armed"
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.trendline_plan_update(created["plan_id"], {"tolerance_ticks": 4}))
    assert exc.value.status_code == 409
    cancelled = json.loads(asyncio.run(master_service.trendline_plan_cancel(created["plan_id"])).body.decode("utf-8"))["plan"]
    assert cancelled["status"] == "cancelled"
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.trendline_plan_arm(created["plan_id"], {"revision":updated["revision"],"monitor_generation":executor.generation}))
    assert exc.value.status_code == 409

    bad_pair = dict(payload, asset="crypto", broker="oanda", instrument="BTCUSDT")
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.trendline_plan_create(bad_pair))
    assert exc.value.status_code == 422
    pepperstone = dict(payload, broker="pepperstone")
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.trendline_plan_create(pepperstone))
    assert exc.value.status_code == 422


def _run_immediate_route(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError("Trendline CRUD routes should complete without awaiting external work in these isolated tests.")


def test_trendline_stop_ticks_accept_browser_values_for_create_and_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = master_service.TrendlinePlanStore(tmp_path / "trendline_plans.json")
    monkeypatch.setattr(master_service, "TRENDLINE_PLAN_STORE", store)
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "_trendline_response_state", lambda: {"monitoring_active": False, "execution_enabled": True})

    def payload(broker: str, instrument: str, ticks: object) -> dict:
        return {
            "asset": "crypto" if broker == "bybit" else "fx", "broker": broker, "account": "demo",
            "instrument": instrument, "side": "buy", "order_type": "market", "test_trade": True,
            "anchors": [{"timestamp_ms": 1_000, "price": "100"}, {"timestamp_ms": 2_000, "price": "101"}],
            "right_extension": True, "trigger_mode": "touch", "cross_direction": "either",
            "trigger_price_basis": "executable", "tolerance_ticks": 2, "expiry_at_ms": None,
            "risk_mode": "percent", "risk_value": "1", "stop_loss_ticks": ticks, "rr_target": "2",
            "timeframe": "15m", "setup": "Pullback", "pattern": "channel", "ema": "20",
            "vwap": "No", "aths_atls": "No", "round_number": "Yes",
        }

    for broker, instrument, ticks in (
        ("bybit", "BTCUSDT", "123"), ("bybit", "ETHUSDT", " 123 "), ("oanda", "USD_JPY", 123),
    ):
        request = payload(broker, instrument, ticks)
        created = json.loads(_run_immediate_route(master_service.trendline_plan_create(request)).body)["plan"]
        assert created["stop_loss_ticks"] == 123 and type(created["stop_loss_ticks"]) is int
        assert created["status"] == "draft"
        expected_metadata = {"setup": "Pullback", "pattern": "channel", "ema": "20", "vwap": "No", "aths_atls": "No", "round_number": "Yes"}
        for key in ("anchors", "risk_mode", "risk_value", "rr_target", "timeframe", "order_metadata"):
            expected = expected_metadata if key == "order_metadata" else request[key]
            assert created[key] == expected
        updated = json.loads(_run_immediate_route(master_service.trendline_plan_update(created["plan_id"], {"stop_loss_ticks": ticks})).body)["plan"]
        assert updated["stop_loss_ticks"] == 123 and type(updated["stop_loss_ticks"]) is int
        assert updated["status"] == "draft"
        for key in ("anchors", "risk_mode", "risk_value", "rr_target", "timeframe", "order_metadata"):
            assert updated[key] == created[key]
        omitted = json.loads(_run_immediate_route(master_service.trendline_plan_update(created["plan_id"], {"tolerance_ticks": 3})).body)["plan"]
        assert omitted["stop_loss_ticks"] == 123 and type(omitted["stop_loss_ticks"]) is int


def test_trendline_stop_ticks_reject_invalid_values_without_changing_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = master_service.TrendlinePlanStore(tmp_path / "trendline_plans.json")
    monkeypatch.setattr(master_service, "TRENDLINE_PLAN_STORE", store)
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "_trendline_response_state", lambda: {"monitoring_active": False, "execution_enabled": True})
    valid = {
        "asset": "fx", "broker": "oanda", "account": "demo", "instrument": "USD_JPY", "side": "buy",
        "order_type": "market", "test_trade": True,
        "anchors": [{"timestamp_ms": 1_000, "price": "100"}, {"timestamp_ms": 2_000, "price": "101"}],
        "right_extension": True, "trigger_mode": "touch", "cross_direction": "either",
        "trigger_price_basis": "executable", "tolerance_ticks": 2, "expiry_at_ms": None,
        "risk_mode": "percent", "risk_value": "1", "stop_loss_ticks": 123, "rr_target": "2", "timeframe": "15m",
    }
    created = json.loads(_run_immediate_route(master_service.trendline_plan_create(valid)).body)["plan"]

    invalid_values = ("", "   ", "0", "-123", "123.5", 123.5, "123ticks", "NaN", float("nan"), float("inf"), None, True, [], {}, (123,))
    for invalid in invalid_values:
        before = store.path.read_bytes()
        revision = store.get(created["plan_id"])["revision"]
        for operation in ("create", "update"):
            request = dict(valid, stop_loss_ticks=invalid)
            with pytest.raises(master_service.HTTPException) as exc:
                if operation == "create":
                    _run_immediate_route(master_service.trendline_plan_create(request))
                else:
                    _run_immediate_route(master_service.trendline_plan_update(created["plan_id"], {"stop_loss_ticks": invalid}))
            assert exc.value.status_code == 422
            assert store.path.read_bytes() == before
            assert store.get(created["plan_id"])["revision"] == revision

    before = store.path.read_bytes()
    with pytest.raises(master_service.HTTPException) as exc:
        _run_immediate_route(master_service.trendline_plan_create({key: value for key, value in valid.items() if key != "stop_loss_ticks"}))
    assert exc.value.status_code == 422
    assert store.path.read_bytes() == before


def test_local_trendline_executor_start_stop_status_is_profile_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeExecutor:
        def __init__(self): self.running = False; self.starts = 0
        def status(self): return {"running": self.running, "last_cycle": None, "last_error": None}
        async def start(self):
            if self.running: return False
            self.running = True; self.starts += 1; return True
        async def stop(self):
            if not self.running: return False
            self.running = False; return True
    fake = FakeExecutor()
    monkeypatch.setattr(master_service, "TRENDLINE_EXECUTOR", fake)
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.trendline_monitor_status())
    assert exc.value.status_code == 410
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    assert json.loads(asyncio.run(master_service.trendline_monitor_start()).body)["running"] is True
    assert json.loads(asyncio.run(master_service.trendline_monitor_start()).body)["started"] is False
    assert fake.starts == 1
    assert json.loads(asyncio.run(master_service.trendline_monitor_stop()).body)["running"] is False


def test_trendline_final_calculator_snapshot_and_literal_live_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = master_service.TrendlinePlanStore(tmp_path / "plans.json")
    monkeypatch.setattr(master_service, "TRENDLINE_PLAN_STORE", store)
    executor = _trendline_route_fixture_executor(store)
    monkeypatch.setattr(master_service, "TRENDLINE_EXECUTOR", executor)
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    calls = []
    async def fake_quote(payload):
        calls.append(dict(payload))
        return master_service.JSONResponse({"entry_price": "100", "_trusted_trendline_quote": {"instrument": "USD_JPY", "bid": "99.9", "ask": "100", "timestamp_ms": 2000, "tick_size": "0.01"}})
    monkeypatch.setattr(master_service, "calculator_quote", fake_quote)
    plan = {"broker":"oanda","account":"live","instrument":"USD_JPY","action":"buy","order_intent":"market","limit_entry_price":None,"risk_mode":"percent","risk_value":"1","stop_loss_ticks":10,"rr_target":"2","timeframe":"1h","order_metadata":{}}
    result = asyncio.run(master_service._trendline_fresh_calculation(plan))
    assert len(calls) == 1 and result["trigger_quote"] == {"instrument":"USD_JPY","bid":"99.9","ask":"100","timestamp_ms":2000,"tick_size":"0.01"}
    payload = {"asset":"fx","broker":"oanda","account":"live","instrument":"USD_JPY","side":"buy","order_type":"market","test_trade":False,"anchors":[{"timestamp_ms":1000,"price":"100"},{"timestamp_ms":2000,"price":"101"}],"right_extension":True,"trigger_mode":"touch","cross_direction":"either","trigger_price_basis":"executable","tolerance_ticks":0,"risk_mode":"percent","risk_value":"1","stop_loss_ticks":10,"rr_target":"2","timeframe":"1h"}
    created = json.loads(asyncio.run(master_service.trendline_plan_create(payload)).body)["plan"]
    for invalid in ({}, {"confirm_live_execution": False}, {"confirm_live_execution": "true"}):
        with pytest.raises(master_service.HTTPException): asyncio.run(master_service.trendline_plan_arm(created["plan_id"], invalid))
    armed = json.loads(asyncio.run(master_service.trendline_plan_arm(created["plan_id"], {"confirm_live_execution": True,"revision":created["revision"],"monitor_generation":executor.generation})).body)["plan"]
    assert armed["live_authorization"]["execution_key"] == armed["execution_key"] and armed["live_authorization"]["plan_revision"] == armed["revision"]


def test_trendline_real_calculator_snapshots_and_broker_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy
    from render.trendline_executor import DefiniteSubmissionFailure, InvalidQuote
    service = master_service
    dec = service.Decimal
    now = 1_750_000_000_000
    monkeypatch.setattr(service.time, "time_ns", lambda: now * 1_000_000)
    monkeypatch.setattr(service, "resolve_bybit_credentials_for", lambda _a: ("demo", "mock-key", "mock-secret", "https://bybit.invalid", "mock"))
    monkeypatch.setattr(service, "_get_oanda_config", lambda _a: {"base_url":"https://oanda.invalid", "account_id":"mock", "token":"mock"})
    for name in ("_upsert_calculator_trade_context", "_upsert_bybit_demo_calc_context", "_schedule_dropbox_upload_state_backup", "_log_webhook_event", "cache_bybit_demo_tpsl_request", "_invalidate_open_orders_cache"):
        monkeypatch.setattr(service, name, lambda *a, **kw: {})
    monkeypatch.setattr(service, "_delete_pending_webhook", lambda *a, **kw: False)
    quotes = {"bybit":0, "oanda":0}
    posts = []
    fault = {"mode":None, "row":None, "transaction":"orderCreateTransaction"}
    meta = {"symbol":"BTCUSDT", "status":"Trading", "priceFilter":{"tickSize":"0.1"},
            "lotSizeFilter":{"qtyStep":"0.001", "minOrderQty":"0.001", "maxOrderQty":"100000", "maxMktOrderQty":"100000", "minNotionalValue":"1"},
            "leverageFilter":{"maxLeverage":"100"}}
    async def fixture_offset(*a,**kw):return 0
    monkeypatch.setattr(service,"_get_bybit_time_offset_ms",fixture_offset)
    monkeypatch.setattr(service,"_BYBIT_TIME_OFFSET_CACHE",{service._normalize_bybit_base_url("https://bybit.invalid"):{"synced_at":now,"offset_ms":0,"rtt_ms":0}})
    async def bybit_get(base, path, params, **kwargs):
        if path == "/v5/market/instruments-info": return {"retCode":0, "result":{"list":[meta]}}
        assert path == "/v5/market/tickers", path
        quotes["bybit"] += 1
        bid = "100.0" if quotes["bybit"] == 1 else "100.2"
        ask = "100.2" if quotes["bybit"] == 1 else "100.4"
        return {"retCode":0, "time":now-50, "result":{"list":[{"symbol":"BTCUSDT", "bid1Price":bid, "ask1Price":ask, "lastPrice":"100.3"}]}}
    async def wallet(*a, **kw): return {"available_usdt":dec("1000"), "total_equity":dec("1000")}
    async def signed_get(**kw):
        assert kw["path"] == "/v5/position/list", kw
        body = posts[-1]
        return {"retCode":0, "result":{"list":[{"size":"1", "avgPrice":"100.4", "positionIdx":0, "takeProfit":body.get("takeProfit"), "stopLoss":body.get("stopLoss")}]}}
    async def signed_post(**kw):
        assert kw["path"] == "/v5/order/create", kw
        posts.append(copy.deepcopy(kw["body"]))
        if fault["mode"] == "timeout": raise service.httpx.ReadTimeout("secret-url")
        if fault["mode"] == "value": raise ValueError("retCode=123 retMsg=secret")
        if fault["mode"] == "negative": return {"retCode":10001, "retMsg":"private", "result":{}}
        return {"retCode":0, "result":{"orderId":"bybit-101", "orderLinkId":kw["body"]["orderLinkId"]}}
    async def oanda_get(**kw):
        assert "/pricing?" in kw["endpoint"], kw
        quotes["oanda"] += 1
        row = {"instrument":"USD_JPY", "status":"tradeable", "time":"2025-06-15T15:06:39.950Z",
               "bids":[{"price":"155.000" if quotes["oanda"] == 1 else "155.010"}],
               "asks":[{"price":"155.002" if quotes["oanda"] == 1 else "155.012"}]}
        if fault["row"]: row.update(fault["row"])
        return {"prices":[row]}
    async def oanda_meta(**kw): return {"displayPrecision":3, "tradeUnitsPrecision":0, "minimumTradeSize":"1", "maximumOrderUnits":"1000000", "maximumPositionSize":"1000000", "marginRate":"0.01"}
    async def summary(*a): return {"currency":"JPY", "nav":"10000", "marginAvailable":"100000", "marginRate":"0.01"}
    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            posts.append(copy.deepcopy(kwargs["json"]["order"]))
            mode = fault["mode"]
            if mode == "timeout": raise service.httpx.ReadTimeout("private")
            status = 503 if mode == "5xx" else 400 if mode == "negative" else 201
            data = [] if mode == "malformed" else {fault["transaction"]:{"id":"701"}}
            return service.httpx.Response(status, json=data, request=service.httpx.Request("POST", url))
    monkeypatch.setattr(service, "_bybit_get_async", bybit_get)
    monkeypatch.setattr(service, "_bybit_signed_get", signed_get)
    monkeypatch.setattr(service, "_bybit_signed_post", signed_post)
    monkeypatch.setattr(service, "_fetch_bybit_balance_usdt", wallet)
    monkeypatch.setattr(service, "_fetch_oanda_json", oanda_get)
    monkeypatch.setattr(service, "_fetch_oanda_instrument_meta", oanda_meta)
    monkeypatch.setattr(service, "_fetch_oanda_account_summary", summary)
    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    plans = {}
    calculations = {}
    async def run():
        for broker in ("bybit", "oanda"):
            for intent in ("market", "limit"):
                quotes[broker] = 0
                plan = {"broker":broker, "account":"demo", "instrument":"BTCUSDT" if broker=="bybit" else "USD_JPY",
                        "action":"buy", "order_intent":intent, "limit_entry_price":"99.9" if broker=="bybit" else "155.000",
                        "risk_mode":"percent", "risk_value":"1", "stop_loss_ticks":100, "rr_target":"2", "timeframe":"1h", "order_metadata":{}, "max_quote_age_ms":60000}
                candidate = await service._trendline_executor_quote(plan)
                assert quotes[broker] == 1
                calc = await service._trendline_fresh_calculation(plan, candidate)
                final = calc["trigger_quote"]
                assert quotes[broker] == 2, "Only candidate and calculator final pricing acquisitions"
                assert final["instrument"] == plan["instrument"] and final["timestamp_ms"] == now-50
                assert final["bid"] == ("100.2" if broker=="bybit" else "155.010")
                assert final["ask"] == ("100.4" if broker=="bybit" else "155.012")
                assert final["tick_size"] == ("0.1" if broker=="bybit" else "0.001")
                entry = dec(final["ask"]) if intent=="market" else dec(plan["limit_entry_price"])
                assert dec(calc["entry_price"]) == entry
                stop_distance = dec(100) * dec(final["tick_size"])
                assert dec(calc["stop_price"]) == entry-stop_distance
                units = dec(calc["quantity"])
                spread = dec(final["ask"])-dec(final["bid"])
                if broker=="oanda":
                    assert dec(calc["estimated_total_loss"]) == units*(stop_distance+spread)
                else:
                    fee = dec(service.os.getenv("CALCULATOR_BYBIT_TAKER_FEE_FALLBACK", "0.0006"))
                    assert dec(calc["estimated_total_loss"]) == units*(stop_distance+(spread if intent=="market" else 0)+entry*fee+(entry-stop_distance)*fee)
                before = len(posts)
                outcome = await service._trendline_executor_submit(plan, calc, "stable-execution-id")
                assert len(posts) == before+1 and outcome == {"accepted":True, "order_id":"bybit-101" if broker=="bybit" else "701"}
                order = posts[-1]
                assert (order.get("orderLinkId") or order.get("clientExtensions", {}).get("id")) == "stable-execution-id"
                if intent == "limit": assert dec(order["price"]) == entry
                else: assert "price" not in order
                plans[broker], calculations[broker] = plan, calc
            # A public caller's body cannot enable private snapshot behavior.
            public = service._trendline_calculator_payload(plan)
            public.update({"_trendline_internal":True, "_include_trendline_snapshot":True, "bid":"9999"})
            body = json.loads((await service.calculator_quote(public)).body)
            assert "_trusted_trendline_quote" not in body and "trigger_quote" not in body
        for row in ({"instrument":"EUR_USD"}, {"status":"non-tradeable"}, {"time":None}):
            fault["row"] = row
            with pytest.raises((InvalidQuote, service.HTTPException)):
                await service._trendline_executor_quote(plans["oanda"])
            with pytest.raises(service.HTTPException):
                await service._trendline_fresh_calculation(plans["oanda"])
        fault["row"] = None
        for tx in ("orderCreateTransaction", "orderFillTransaction"):
            fault["transaction"] = tx
            assert (await service._trendline_executor_submit(plans["oanda"], calculations["oanda"], "stable"))["order_id"] == "701"
        for broker, mode, error in (("bybit","negative",DefiniteSubmissionFailure), ("bybit","timeout",RuntimeError), ("bybit","value",RuntimeError), ("oanda","negative",DefiniteSubmissionFailure), ("oanda","5xx",RuntimeError), ("oanda","malformed",RuntimeError), ("oanda","timeout",RuntimeError)):
            fault["mode"] = mode
            with pytest.raises(error): await service._trendline_executor_submit(plans[broker], calculations[broker], "stable")
        fault["mode"] = None
        before = len(posts)
        with pytest.raises(DefiniteSubmissionFailure):
            await service._trendline_executor_submit(plans["oanda"], {**calculations["oanda"], "quantity":None}, "stable")
        assert len(posts) == before
    asyncio.run(run())



def _phase3_accepted_context_fixture(monkeypatch, tmp_path, service):
    """Real placement/persistence with every broker/backup/state effect isolated."""
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {"fingerprint": None, "items": None})
    monkeypatch.setattr(service, "_calc_context_uses_repo_local", lambda: True)
    monkeypatch.setattr(service, "BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH", tmp_path / "demo-quotes.json")
    monkeypatch.setattr(service, "BYBIT_DEMO_CALC_CONTEXT_LEGACY_LOCAL_PATH", tmp_path / "absent.json")
    for name in ("_schedule_dropbox_upload_state_backup", "_invalidate_open_orders_cache", "_log_webhook_event",
                 "_record_outbound_traffic", "cache_bybit_demo_tpsl_request"):
        monkeypatch.setattr(service, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_delete_pending_webhook", lambda *args, **kwargs: False)
    monkeypatch.setattr(service, "_parse_limit_cancel_settings", lambda payload: (None, None))
    monkeypatch.setattr(service, "_bybit_position_idx_for_order", lambda **kwargs: 0)
    monkeypatch.setattr(service, "resolve_bybit_credentials_for",
                        lambda account: (account, "key-" + account, "secret", "https://bybit.test", "fixture"))
    posts = []
    async def bybit_get(base, path, params, **kwargs):
        return {"result": {"list": [{"lastPrice": "101", "priceFilter": {"tickSize": "0.1"}}]}}
    async def bybit_post(**kwargs):
        assert kwargs["path"] == "/v5/order/create"
        account = kwargs["api_key"].split("-")[-1]
        posts.append(("bybit", account))
        return {"retCode": 0, "result": {"orderId": "bb-" + account, "orderLinkId": "link-" + account}}
    async def position(**kwargs):
        return {"avgPrice": "100", "positionIdx": 0, "size": "0.01", "side": "Buy",
                "takeProfit": "110", "stopLoss": "95"}
    monkeypatch.setattr(service, "_bybit_get_async", bybit_get)
    monkeypatch.setattr(service, "_bybit_signed_post", bybit_post)
    monkeypatch.setattr(service, "_bybit_lookup_linear_symbol_with_fallback", lambda *a, **k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}))
    monkeypatch.setattr(service, "_wait_for_position_entry", position)
    monkeypatch.setattr(service, "_set_bybit_trading_stop", lambda **k: asyncio.sleep(0, result={"retCode": 0}))
    monkeypatch.setattr(service, "_fetch_bybit_positions", lambda **k: asyncio.sleep(0, result=[{"size": "0.01", "takeProfit": "110", "stopLoss": "95"}]))
    monkeypatch.setattr(service, "_get_oanda_config", lambda account: {
        "base_url": "https://oanda.test", "account_id": "account-" + account, "token": "token-" + account})
    monkeypatch.setattr(service, "_fetch_oanda_instrument_meta", lambda **k: asyncio.sleep(0, result={
        "displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0"}))
    monkeypatch.setattr(service, "_fetch_oanda_account_summary", lambda account: asyncio.sleep(0, result={
        "currency": "USD", "nav": "10000", "marginAvailable": "1000000"}))
    monkeypatch.setattr(service, "_convert_aud_to_home_currency", lambda risk, *a: asyncio.sleep(0, result=risk))
    monkeypatch.setattr(service, "_fetch_oanda_json", lambda **k: asyncio.sleep(0, result={
        "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10002"}]}]}))
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            account = "demo" if "account-demo" in url else "live"
            posts.append(("oanda", account))
            result = {"orderFillTransaction": {"id": "fill-" + account, "orderID": "oa-" + account,
                      "tradeOpened": {"tradeID": "trade-" + account}}, "lastTransactionID": "fill-" + account}
            if account == "live":
                result["orderCreateTransaction"] = {"id": "oa-" + account}
            return service.httpx.Response(201, json=result)
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)
    cases = []
    for broker, account, order_type, target, risk in (
        ("bybit", "demo", "market", "rr", "fixed_aud"),
        ("bybit", "live", "limit", "ticks", "percent"),
        ("oanda", "demo", "market", "ticks", "fixed_aud"),
        ("oanda", "live", "limit", "rr", "percent"),
    ):
        crypto = broker == "bybit"
        quote = {"broker": broker, "account": account, "category": "linear" if crypto else "forex",
                 "instrument": "BTCUSDT" if crypto else "EUR_USD", "side": "buy",
                 "calculation_context_id": "ctx-" + broker + "-" + account, "status": "QUOTED",
                 "order_type": order_type, "risk_mode": risk, "risk_value": "10",
                 "stop_loss_ticks": "35", "target_mode": target, "take_profit_ticks": "70" if target == "ticks" else None,
                 "risk_reward": "2" if target == "rr" else None, "is_test_trade": False,
                 "timeframe": "15MIN", "pattern": "channel", "ema": "Yes",
                 "planned_entry_price": "100" if crypto else "1.1",
                 "planned_stop_price": "95" if crypto else "1.09965",
                 "planned_target_price": "110" if crypto else "1.1007", "quote_created_at_ms": 12345}
        if crypto and account == "demo":
            service._upsert_bybit_demo_calc_context(quote)
        else:
            service._upsert_calculator_trade_context(quote, invalidate_cache=False)
        payload = dict(quote, asset="crypto" if crypto else "fx", symbol=quote["instrument"], action="buy",
                       quantity="0.01" if crypto else "10", entry_price=quote["planned_entry_price"],
                       stop_loss_price=quote["planned_stop_price"], take_profit_price=quote["planned_target_price"])
        if crypto:
            payload["risk_value"] = "999"  # Later form metadata cannot replace the exact durable quote.
        cases.append((quote, payload))
    return cases, posts


def test_calculator_context_binds_accepted_order_identifiers_without_losing_settings(monkeypatch, tmp_path):
    service = master_service
    cases, posts = _phase3_accepted_context_fixture(monkeypatch, tmp_path, service)
    async def scenario():
        for quote, payload in cases:
            response = json.loads((await service.calculator_submit(payload)).body)
            assert response["ok"] and response["journal_context_saved"], response
            saved = next(row for row in service._load_trade_contexts()
                         if row.get("calculation_context_id") == quote["calculation_context_id"])
            for field in ("order_type", "risk_mode", "risk_value", "stop_loss_ticks", "target_mode",
                          "take_profit_ticks", "risk_reward", "is_test_trade", "pattern"):
                assert saved.get(field) == quote.get(field)
            if quote["broker"] == "bybit":
                assert saved["order_id"] == "bb-" + quote["account"]
                assert saved["order_link_id"] == "link-" + quote["account"]
                assert saved["position_idx"] == 0 and saved["settlement_currency"] == "USDT"
            else:
                assert saved["account_id"] == "account-" + quote["account"]
                assert saved["order_id"] == "oa-" + quote["account"]
                assert saved["transaction_id"] == "fill-" + quote["account"]
                assert saved["trade_id"] == "trade-" + quote["account"]
            service._upsert_trade_context(dict(broker=quote["broker"], account=quote["account"],
                category=quote["category"], instrument=quote["instrument"], calculation_context_id=quote["calculation_context_id"],
                risk_value=None, stop_loss_ticks="", pattern="", is_test_trade=None, take_profit_ticks="-"))
            reloaded = next(row for row in service._load_trade_contexts()
                            if row.get("calculation_context_id") == quote["calculation_context_id"])
            assert reloaded["risk_value"] == "10" and reloaded["stop_loss_ticks"] == "35"
            assert reloaded["is_test_trade"] is False and reloaded["pattern"] == "channel"
        assert len(posts) == 4
        # Acceptance is not undone or retried if durable metadata saving subsequently fails.
        original_save = service._save_trade_contexts
        def unavailable(rows): raise OSError("fixture context disk unavailable")
        monkeypatch.setattr(service, "_save_trade_contexts", unavailable)
        for quote, payload in (cases[0], cases[2]):
            before = len(posts)
            result = json.loads((await service.calculator_submit(payload)).body)
            assert result["ok"] and result["journal_context_saved"] is False
            assert result["warnings"] and result["context_save_error"]
            assert len(posts) == before + 1
        monkeypatch.setattr(service, "_save_trade_contexts", original_save)
    asyncio.run(scenario())


def test_accepted_orders_schedule_early_trade_folders_without_blocking_submit(tmp_path: Path, monkeypatch):
    import threading
    from tools import master_journal_workbook as folders

    service = master_service
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"payload": None})
    monkeypatch.setattr(folders, "REPO_ROOT", tmp_path)
    data = tmp_path / "render" / "data"
    data.mkdir(parents=True)
    for name, file in (("TRADE_CONTEXTS_PATH", "trade_contexts.json"),
                       ("TRADING_JOURNAL_PATH", "journal.json"),
                       ("WEBHOOK_ATTEMPTS_PATH", "webhook_attempts.json"),
                       ("PENDING_WEBHOOKS_PATH", "pending_webhooks.json")):
        monkeypatch.setattr(service, name, data / file)
    monkeypatch.setattr(service, "MASTER_JOURNAL_PATH", tmp_path / "missing-workbook.xlsx")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_OANDA_OPEN_TRADE_LEGS", {})
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_WAKE", threading.Event())
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_STOP", threading.Event())
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_THREAD", None)
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_ERROR", None)
    monkeypatch.setattr(service, "_utc_now_iso", lambda: "2026-01-31T23:00:00+00:00")
    for name in ("_schedule_dropbox_upload_state_backup", "_invalidate_open_orders_cache", "_invalidate_trading_journal_view_snapshot"):
        monkeypatch.setattr(service, name, lambda *a, **k: None)
    monkeypatch.setattr(service, "_log_webhook_event", lambda *a, **k: None)
    monkeypatch.setattr(service, "cache_bybit_demo_tpsl_request", lambda **k: None)
    monkeypatch.setattr(service, "_load_bybit_demo_calc_contexts", lambda *a: {"items": []})
    monkeypatch.setattr(service, "_upsert_bybit_demo_calc_context", lambda *a, **k: None)
    monkeypatch.setattr(service, "_delete_pending_webhook", lambda *a, **k: False)
    monkeypatch.setattr(service, "_assert_pending_webhook_executable", lambda *a: None)
    monkeypatch.setattr(service, "_update_pending_webhook", lambda *a, **k: {})
    monkeypatch.setattr(service, "_consume_pending_webhook", lambda *a, **k: True)
    save_json = service._save_json_file
    def isolated_save(path, payload):
        assert Path(path).is_relative_to(tmp_path), f"unexpected production write: {path}"
        return save_json(path, payload)
    monkeypatch.setattr(service, "_save_json_file", isolated_save)
    crypto, forex = tmp_path / "Crypto", tmp_path / "Forex"
    (crypto / "2025" / "DECEMBER" / "C7 BTC").mkdir(parents=True)
    (forex / "2025" / "DECEMBER" / "F3 EURUSD").mkdir(parents=True)
    monkeypatch.setenv("TRADING_JOURNAL_CRYPTO_ROOT", str(crypto))
    monkeypatch.setenv("TRADING_JOURNAL_FOREX_ROOT", str(forex))
    monkeypatch.setattr(service, "resolve_bybit_credentials_for", lambda a: (a, "key", "secret", "https://bybit.test", "KEY"))
    monkeypatch.setattr(service, "_get_oanda_config", lambda a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "test"})
    async def meta(**k):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0}
    monkeypatch.setattr(service, "_fetch_oanda_instrument_meta", meta)
    broker = {"id": "pending-1", "reject": False, "fx_fill": True}
    async def bybit_post(**k):
        assert k["path"] == "/v5/order/create"
        return {"retCode": 10001 if broker["reject"] else 0, "retMsg": "stub", "result": {"orderId": broker["id"], "orderLinkId": "link-" + broker["id"]}}
    monkeypatch.setattr(service, "_bybit_signed_post", bybit_post)
    class Response:
        status_code = 200
        def json(self):
            if broker["reject"]:
                return {"orderRejectTransaction": {"id": "reject"}}
            result = {"orderCreateTransaction": {"id": broker["id"]}}
            if broker["fx_fill"]:
                result["orderFillTransaction"] = {"id": "fill-" + broker["id"], "orderID": broker["id"], "tradeOpened": {"tradeID": "trade-" + broker["id"]}}
            return result
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            assert url.startswith("https://oanda.test/")
            return Response()
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)
    async def open_orders(**k):
        return service.JSONResponse({"items": [{"broker": "oanda", "account": "demo", "category": "forex", "instrument": "EUR_USD", "id": broker["id"], "type": "order"}]})
    monkeypatch.setattr(service, "list_open_orders", open_orders)

    entered, release = threading.Event(), threading.Event()
    materialize = folders._materialize_trade_folder
    def held_io(record):
        entered.set()
        assert release.wait(15), "test failed to release folder I/O"
        materialize(record)
    monkeypatch.setattr(folders, "_materialize_trade_folder", held_io)
    crypto_payload = {"asset": "crypto", "account": "demo", "symbol": "BTCUSDT", "action": "buy", "order_type": "limit", "entry_price": "100", "quantity": "0.01", "is_test_trade": False}
    async def accept():
        response = await asyncio.wait_for(service.calculator_submit(dict(crypto_payload)), 3)
        assert json.loads(response.body)["ok"] is True
        assert await asyncio.to_thread(entered.wait, 3)
        assert not release.is_set()
        assert not list(crypto.glob("2026/*/C*"))
        # Same accepted order delivered again: exact identity must reuse it.
        await service._place_bybit_order(dict(crypto_payload), request_id="replay")
        broker["id"] = "market-2"
        market = dict(crypto_payload, order_type="market")
        market.pop("entry_price")
        await service._place_bybit_order(market, request_id="market")
        broker["id"] = "fx-limit"
        broker["fx_fill"] = False
        response = await service.calculator_submit({"asset": "fx", "account": "demo", "symbol": "EUR_USD", "action": "buy", "order_type": "limit", "entry_price": "1.1", "quantity": "100", "is_test_trade": False})
        assert json.loads(response.body)["ok"] is True
        broker["id"] = "webhook-fx"
        broker["fx_fill"] = True
        response = await service.calculator_webhook({"asset": "fx", "account": "demo", "symbol": "EUR_USD", "action": "buy", "order_type": "market", "quantity": "100", "pending_webhook_id": "registered-webhook", "is_test_trade": False})
        assert json.loads(response.body)["ok"] is True
        # Current-inventory proof binds additions to the original accepted entry.
        service._OPEN_ORDERS_CACHE["payload"] = {"items": [{"broker": "bybit", "account": "demo", "category": "linear", "instrument": "BTCUSDT", "type": "Position", "side": "Buy", "size": "0.02", "position_idx": 0, "source_pending": False, "source_stale": False, "raw_refs": {"order_ids": ["pending-1"], "order_ids_complete": True}}]}
        broker["id"] = "addition-3"
        await service._place_bybit_order(dict(crypto_payload), request_id="addition")
        broker["id"] = "net-reduction"
        await service._place_bybit_order(dict(crypto_payload, action="sell"), request_id="reduce")
        # A saved webhook registration is not broker acceptance.
        service._upsert_calculator_trade_context({"broker": "bybit", "account": "demo", "category": "linear", "instrument": "BTCUSDT", "pending_webhook_id": "unexecuted", "status": "WAITING"})
        broker["id"] = "test-entry"
        await service._place_bybit_order(dict(crypto_payload, is_test_trade="Yes"), request_id="test")
        broker["reject"] = True
        rejected = await service.calculator_submit(dict(crypto_payload))
        assert json.loads(rejected.body)["ok"] is False
    try:
        asyncio.run(accept())
    finally:
        release.set()
        service._EARLY_TRADE_FOLDER_STOP.set()
        service._EARLY_TRADE_FOLDER_WAKE.set()
        if service._EARLY_TRADE_FOLDER_THREAD:
            service._EARLY_TRADE_FOLDER_THREAD.join(10)
            assert not service._EARLY_TRADE_FOLDER_THREAD.is_alive()
    monkeypatch.setattr(folders, "_materialize_trade_folder", materialize)
    # Simulated restart loads only persisted requests/state, including retained closures.
    service._TRADE_CONTEXTS_CACHE.clear()
    contexts = service._load_trade_contexts()
    requests = [c for c in contexts if c.get("early_trade_folder_request")]
    assert {c["order_id"] for c in requests} == {"pending-1", "market-2", "fx-limit", "webhook-fx", "addition-3"}
    for context in requests:
        context.update(status="CANCELLED", updated_at="2000-01-01T00:00:00Z")
    assert all(c in service._prune_trade_contexts(contexts) for c in requests)
    service._save_trade_contexts(contexts)
    service._drain_early_trade_folders()
    state = folders._load_trade_allocations()
    assert len(state) == 4 and all(r["status"] == "ready" for r in state.values())
    pending = next(r for r in state.values() if "order:pending-1" in r["identities"])
    assert pending["trade_number"] == "C8"
    assert Path(pending["folder"]) == crypto / "2026" / "FEBRUARY" / "C8 BTC"
    # Pending order-to-trade conversion adds an exact alias through the existing context path.
    service._upsert_trade_context({"broker": "oanda", "account": "demo", "account_id": "acct", "category": "forex", "instrument": "EUR_USD", "order_id": "fx-limit", "trade_id": "filled-fx-limit"})
    service._drain_early_trade_folders()
    # Normal source persistence receives the exact allocation before workbook numbering.
    row = {"id": "new-journal-row", "source": "bybit", "account": "demo", "asset_class": "crypto", "symbol": "BTCUSDT", "open_time": "2027-02-01", "close_time": "2027-03-02", "is_test_trade": False, "raw_refs": {"order_ids": ["pending-1"]}}
    fx_row = {"id": "fx-close", "source": "oanda", "account": "demo", "account_id": "acct", "asset_class": "forex", "symbol": "EUR_USD", "open_time": "2027-03-01", "raw_refs": {"tradeId": "filled-fx-limit"}}
    service._set_trading_journal_rows([row, fx_row], invalidate_view_snapshot=False, schedule_state_backup=False)
    persisted = json.loads(service.TRADING_JOURNAL_PATH.read_text())["items"]
    assert persisted[0]["trade_number"] == "C8"
    assert persisted[0]["trade_folder"] == pending["folder"]
    assert persisted[1]["trade_number"] == "F4"
    result = folders.assign_trade_numbers_and_create_folders(persisted)
    assert result["assigned_count"] == 0
    assert len(folders._load_trade_allocations()) == 4
    service._drain_early_trade_folders()
    assert len(list(crypto.glob("2026/*/C*"))) == 2
    assert len(list(forex.glob("2026/*/F*"))) == 2


def test_early_trade_folders_reuse_actual_bybit_csv_account_aliases(tmp_path: Path, monkeypatch, caplog):
    import copy
    import csv
    import logging
    from tools import master_journal_workbook as folders

    service = master_service
    monkeypatch.setattr(folders, "REPO_ROOT", tmp_path)
    data = tmp_path / "render" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", data / "trade_contexts.json")
    monkeypatch.setattr(service, "TRADING_JOURNAL_PATH", data / "journal.json")
    monkeypatch.setattr(service, "MASTER_JOURNAL_PATH", tmp_path / "no-workbook.xlsx")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"payload": None})
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_ERROR", None)
    scheduled = []
    monkeypatch.setattr(service, "_schedule_early_trade_folders", lambda: scheduled.append(True))
    monkeypatch.setattr(service, "_utc_now_iso", lambda: "2026-10-16T02:00:00+00:00")
    for name in ("_schedule_dropbox_upload_state_backup", "_invalidate_open_orders_cache", "_invalidate_trading_journal_view_snapshot", "_log_webhook_event", "cache_bybit_demo_tpsl_request"):
        monkeypatch.setattr(service, name, lambda *a, **k: None)
    monkeypatch.setattr(service, "_delete_pending_webhook", lambda *a, **k: False)
    monkeypatch.setattr(service, "resolve_bybit_credentials_for", lambda a: (a, "fixture-key", "fixture-secret", "https://bybit.invalid", "FIXTURE"))
    def forbidden(*a, **k):
        raise AssertionError("External I/O is forbidden in this regression")
    monkeypatch.setattr(service.httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(service.requests.sessions.Session, "request", forbidden)
    async def accepted(**kwargs):
        assert kwargs["path"] == "/v5/order/create"
        return {"retCode": 0, "result": {"orderId": "shared-opening-order", "orderLinkId": "shared-accepted-link"}}
    monkeypatch.setattr(service, "_bybit_signed_post", accepted)
    save_json = service._save_json_file
    def isolated_save(path, payload):
        assert Path(path).is_relative_to(tmp_path), f"unexpected production write: {path}"
        save_json(path, payload)
    monkeypatch.setattr(service, "_save_json_file", isolated_save)
    crypto, forex = tmp_path / "Crypto", tmp_path / "Forex"
    (crypto / "2025" / "OCTOBER" / "C7 BTC").mkdir(parents=True)
    forex.mkdir()
    monkeypatch.setenv("TRADING_JOURNAL_CRYPTO_ROOT", str(crypto))
    monkeypatch.setenv("TRADING_JOURNAL_FOREX_ROOT", str(forex))

    # Real placement -> accepted-context outbox -> explicit production worker drain.
    for mode in ("demo", "live"):
        result = asyncio.run(service._place_bybit_order({"symbol": "BTCUSDT", "action": "buy", "quantity": "0.01", "account": mode, "order_type": "limit", "entry_price": "100", "is_test_trade": False}, request_id="alias-" + mode))
        assert result["order"]["orderId"] == "shared-opening-order"
        assert result["journal_context_saved"] is True
    assert len(scheduled) == 2
    service._drain_early_trade_folders()
    original = folders._load_trade_allocations()
    assert len(original) == 2
    by_mode = {record["scope"][1]: (key, copy.deepcopy(record)) for key, record in original.items()}
    assert set(by_mode) == {"demo", "live"}
    assert by_mode["demo"][1]["trade_number"] != by_mode["live"][1]["trade_number"]
    for _, record in by_mode.values():
        (Path(record["folder"]) / "chart.png").write_bytes(b"preserved screenshot")

    # Actual valid CSV exporter format and parser/grouping, never a synthetic mode row.
    parsed = []
    for mode, label in (("demo", "Bybit Demo"), ("live", "BYBIT")):
        csv_path = tmp_path / (mode + "-execution-history.csv")
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=service.BYBIT_TRADE_HISTORY_REQUIRED_HEADERS)
            writer.writeheader()
            for direction, order, execution, price, timestamp in (
                ("Buy", "shared-opening-order", "opening-exec", "100", "12:00:00 2026-10-16"),
                ("Sell", "shared-closing-order", "closing-exec", "110", "12:05:00 2026-10-16"),
            ):
                writer.writerow({"contracts": "BTCUSDT", "Order No.": order, "Direction": direction, "Order Type": "Limit", "Filled Qty": "0.01", "Filled Price": price, "Order Price": price, "Filled Type": "Trade", "Trading Fee Rate": "0.0006", "Fees Paid": "0.001", "Trasaction ID": execution, "Transaction Time(UTC+10)": timestamp, "Final Balance (USDT)": ""})
        assert service._is_bybit_trade_history_csv(csv_path)
        rows, unmatched, diagnostics = service._parse_bybit_trade_history_csv_with_diagnostics(csv_path, account_mode=mode)
        assert unmatched == [] and len(rows) == 1
        assert diagnostics["bybit_completed_trades_imported"] == 1
        assert rows[0]["account"] == rows[0]["account_label"] == label
        assert rows[0]["source"] == "bybit_execution_history_grouped"
        assert set(rows[0]["raw_refs"]["order_ids"]) == {"shared-opening-order", "shared-closing-order"}
        parsed.append(rows[0])
    service._set_trading_journal_rows(parsed, invalidate_view_snapshot=False, schedule_state_backup=False)
    saved = json.loads(service.TRADING_JOURNAL_PATH.read_text())["items"]
    assert [row["account"] for row in saved] == ["Bybit Demo", "BYBIT"]
    for mode, row in zip(("demo", "live"), saved):
        record = by_mode[mode][1]
        assert row["trade_number"] == record["trade_number"]
        assert row["trade_folder"] == record["folder"]
        target, reason = folders.resolve_trade_folder_link(row["trade_number"], crypto_root=crypto)
        assert reason is None and target == Path(record["folder"]).resolve().as_uri()
    result = folders.assign_trade_numbers_and_create_folders(saved)
    assert result["assigned_count"] == 0
    assert len(folders._load_trade_allocations()) == len(list(crypto.glob("2026/*/C*"))) == 2

    # Explicit account IDs remain exact, including missing versus present.
    service._upsert_calculator_trade_context({"broker": "bybit", "account": "demo", "account_id": "account-A", "category": "linear", "instrument": "BTCUSDT", "asset": "crypto", "order_id": "explicit-id-opening", "open_time": "2026-10-16T12:00:00+10:00", "is_test_trade": False}, accepted_entry=True)
    service._drain_early_trade_folders()
    state = folders._load_trade_allocations()
    assert len(state) == 3
    explicit = {"source": "bybit_execution_history_grouped", "account": "Bybit Demo", "account_id": "account-A", "symbol": "BTCUSDT", "raw_refs": {"order_ids": ["explicit-id-opening"]}}
    matched = copy.deepcopy(explicit)
    folders.apply_early_trade_folder_allocations([matched])
    assert matched.get("trade_number")
    for account_id in ("account-B", ""):
        other = dict(explicit, account_id=account_id)
        folders.apply_early_trade_folder_allocations([other])
        assert "trade_number" not in other
    for changes in ({"account": "my Bybit Demo"}, {"account": "Bybit Demo extra"}, {"account": "unknown"}, {"source": "oanda"}, {"source": "unproven_bybit_source"}, {"category": "inverse"}, {"symbol": "ETHUSDT"}):
        other = dict(parsed[0], **changes)
        folders.apply_early_trade_folder_allocations([other])
        assert "trade_number" not in other
        assert not folders.trade_folder_allocation_ready(other, state)

    # Reload legacy scope spellings without migrating their persisted representation.
    for record in state.values():
        record["scope"][1] = "  bYbIt \t DeMo  " if record["scope"][1] == "demo" else " ByBit-LiVe "
    with folders._trade_allocation_lock():
        folders._save_trade_allocations(state)
    ownership = {key: {field: copy.deepcopy(record[field]) for field in ("scope", "owner", "trade_number", "folder", "root")} for key, record in state.items()}
    before = folders._trade_allocation_state_path().read_bytes()
    service._TRADE_CONTEXTS_CACHE.clear()
    service._drain_early_trade_folders()
    assert folders._trade_allocation_state_path().read_bytes() == before, "ready legacy scopes must not become recurring pending work"
    service._set_trading_journal_rows(parsed, invalidate_view_snapshot=False, schedule_state_backup=False)
    for mode, row in zip(("demo", "live"), json.loads(service.TRADING_JOURNAL_PATH.read_text())["items"]):
        assert row["account"] == ("Bybit Demo" if mode == "demo" else "BYBIT")
        assert row["trade_number"] == by_mode[mode][1]["trade_number"]
    replay = copy.deepcopy(parsed)
    assert folders.assign_trade_numbers_and_create_folders(replay)["assigned_count"] == 0
    for key, record in folders._load_trade_allocations().items():
        assert {field: record[field] for field in ownership[key]} == ownership[key]
    assert len(list(crypto.glob("2026/*/C*"))) == 3
    for mode in ("demo", "live"):
        assert (Path(by_mode[mode][1]["folder"]) / "chart.png").read_bytes() == b"preserved screenshot"
    for account, mode in ((" BYBIT_DEMO ", "demo"), (" ByBit   LiVe ", "live"), (" bybit ", "live")):
        row = dict(parsed[0], account=account)
        folders.apply_early_trade_folder_allocations([row])
        assert row["account"] == account and row["trade_number"] == by_mode[mode][1]["trade_number"]

    # Two real owned allocations become conflicting under equivalent legacy scopes.
    conflict_row = dict(parsed[0], id="legacy-conflict", account="retired-account-label")
    assert folders.assign_trade_numbers_and_create_folders([conflict_row])["assigned_count"] == 1
    state = folders._load_trade_allocations()
    conflict_key = next(key for key, record in state.items() if record["scope"][1] == "retired-account-label")
    state[conflict_key]["scope"][1] = "Bybit Demo"
    with folders._trade_allocation_lock():
        folders._save_trade_allocations(state)
    ownership = {key: {field: record[field] for field in ("scope", "owner", "trade_number", "folder")} for key, record in state.items()}
    assert not folders.trade_folder_allocation_ready(parsed[0], state)
    unresolved = copy.deepcopy(parsed[0])
    folders.apply_early_trade_folder_allocations([unresolved])
    assert "trade_number" not in unresolved
    result = folders.assign_trade_numbers_and_create_folders([unresolved])
    assert result["assigned_count"] == 0 and unresolved["id"] in result["skipped_unresolved_root_or_market"]
    assert "trade_number" not in unresolved
    # Run one worker cycle deterministically to exercise the existing quiet diagnostic.
    class OneCycleStop:
        checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 2
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_STOP", OneCycleStop())
    monkeypatch.setattr(service, "_EARLY_TRADE_FOLDER_WAKE", types.SimpleNamespace(wait=lambda timeout: None, clear=lambda: None))
    with caplog.at_level(logging.INFO, logger=service.APP_LOGGER.name):
        service.APP_LOGGER.addHandler(caplog.handler)
        try:
            service._early_trade_folder_worker()
        finally:
            service.APP_LOGGER.removeHandler(caplog.handler)
    assert "early_trade_folder_pending" in caplog.text
    assert "remains pending" in service._EARLY_TRADE_FOLDER_ERROR
    final = folders._load_trade_allocations()
    assert len(final) == len(list(crypto.glob("2026/*/C*"))) == 4
    for key, record in final.items():
        assert {field: record[field] for field in ownership[key]} == ownership[key]
        assert folders._owned_trade_folder(record)
def test_post_entry_classification_persists_for_exact_trade_and_reaches_journal(tmp_path, monkeypatch):
    import copy
    import csv
    import httpx
    from tools import master_journal_workbook as folders

    service = master_service
    data = tmp_path / "render" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(folders, "REPO_ROOT", tmp_path)
    for name, file in (("TRADE_CONTEXTS_PATH", "trade_contexts.json"), ("TRADING_JOURNAL_PATH", "journal.json")):
        monkeypatch.setattr(service, name, data / file)
    monkeypatch.setattr(service, "MASTER_JOURNAL_PATH", tmp_path / "absent.xlsx")
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 5, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_OANDA_OPEN_TRADE_LEGS", {})
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_persist_oanda_fill_state", lambda: None)
    monkeypatch.setattr(service, "_update_unresolved_registry", lambda **k: (False, {}))
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    scheduled = []
    monkeypatch.setattr(service, "_schedule_early_trade_folders", lambda: scheduled.append(True))
    original_save = service._save_json_file
    def isolated_save(path, payload):
        assert Path(path).is_relative_to(tmp_path), path
        return original_save(path, payload)
    monkeypatch.setattr(service, "_save_json_file", isolated_save)
    def forbidden(*a, **k):
        raise AssertionError("A classification save must never trade, notify, or change a webhook")
    for name in ("_place_bybit_order", "_place_oanda_order", "_bybit_signed_post", "_save_pending_webhooks", "_delete_pending_webhook", "_log_webhook_event"):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(service.requests.sessions.Session, "request", forbidden)
    crypto, forex = tmp_path / "Crypto", tmp_path / "Forex"
    (crypto / "2025" / "OCTOBER" / "C7 BTC").mkdir(parents=True)
    (forex / "2025" / "OCTOBER" / "F3 EURUSD").mkdir(parents=True)
    monkeypatch.setenv("TRADING_JOURNAL_CRYPTO_ROOT", str(crypto))
    monkeypatch.setenv("TRADING_JOURNAL_FOREX_ROOT", str(forex))
    accepted = dict(broker="bybit", account="demo", category="linear", instrument="BTCUSDT", asset="crypto", side="Buy",
                    order_id="entry", calculation_context_id="quote", position_idx=0, order_type="limit",
                    open_time="2026-10-16T12:00:00+10:00", is_test_trade=True, timeframe="15m", setup="Pullback",
                    risk_value="1", stop_loss_ticks="50", raw_execution={"unknown": 0}, webhook_mode="No")
    service._upsert_calculator_trade_context(accepted, accepted_entry=True, invalidate_cache=False)
    # An identical broker ID on live is deliberately a different accepted entry.
    service._upsert_calculator_trade_context(dict(accepted, account="live", calculation_context_id="live-quote"), accepted_entry=True, invalidate_cache=False)
    service._drain_early_trade_folders()
    assert not folders._load_trade_allocations()

    async def scenario():
        owner = service._OpenOrdersService()
        job = service._OpenOrdersJob(owner, [], "fixture", 5)
        owner.job = job
        service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()] = owner
        source = job.add("fixture", {"broker": "Bybit", "account": "demo"}, None, ready=False)
        source.update(state="complete", last_success=service._open_orders_now(), last_success_at="2026-10-16T02:00:00Z")
        row = dict(broker="Bybit", account="demo", category="linear", instrument="BTCUSDT", side="Buy", type="Order", id="entry", position_idx=0)
        source["items"] = [row]
        job.publish()
        def current():
            return job.payload["items"][0]
        transport = httpx.ASGITransport(app=service.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
            async def save(changes, *, target=None, version=None, expected=200):
                item = current()
                response = await client.post("/api/open-orders/classification", json={"target": target or item.get("classification_target"), "version": item["classification_version"] if version is None else version, "changes": changes})
                assert response.status_code == expected, response.text
                return response.json()
            initial_target = current()["classification_target"]
            complete = dict(timeframe="5m", setup="Breakout", pattern="range", ema="20", vwap=False,
                            aths_atls="All-Time Low", round_number=0, is_test_trade=False, webhook_classification="Yes")
            revision = job.payload["progress_revision"]
            result = await save(complete)
            assert result["values"] == dict(complete, vwap="No", round_number="No")
            assert current()["timeframe"] == "5MIN" and current()["is_test_trade"] == "No"
            assert current()["webhook_mode"] == "No" and current()["webhook_status"] == "Not used"
            assert job.payload["progress_revision"] > revision and not job.tasks
            service._TRADE_CONTEXTS_CACHE.clear()
            saved = next(c for c in service._load_trade_contexts() if c["account"] == "demo")
            assert saved["is_test_trade"] is True and saved["timeframe"] == "15m"
            assert saved["raw_execution"] == {"unknown": 0} and saved["risk_value"] == "1"
            assert saved["classification_version"] == 1
            service._drain_early_trade_folders()
            allocations = copy.deepcopy(folders._load_trade_allocations())
            assert len(allocations) == 1
            allocation = next(iter(allocations.values()))
            screenshot = Path(allocation["folder"]) / "chart.png"
            screenshot.write_bytes(b"keep screenshot")
            before = service.TRADE_CONTEXTS_PATH.read_bytes()
            for changes, version, target in (({"setup": "News Scalp"}, 0, None), ({"risk_value": "999"}, None, None), ({"order_id": "other"}, None, None), ({"setup": "Breakout"}, None, "forged")):
                await save(changes, version=version, target=target, expected=400 if "risk_value" in changes or "order_id" in changes else 409)
                assert service.TRADE_CONTEXTS_PATH.read_bytes() == before
            # Price revisions are not classification conflicts. Explicit clears survive.
            row["current_price"] = "999"
            job.publish()
            await save({"timeframe": None, "setup": "", "pattern": "", "ema": "", "aths_atls": "", "is_test_trade": True, "webhook_classification": "No"}, version=1)
            service._drain_early_trade_folders()
            assert folders._load_trade_allocations() == allocations and screenshot.read_bytes() == b"keep screenshot"
            # Repeated accepted/webhook updates do not own the explicit overlay.
            service._upsert_calculator_trade_context(dict(accepted, timeframe="1h", setup="Pullback", is_test_trade=False), accepted_entry=True, invalidate_cache=False)
            service._TRADE_CONTEXTS_CACHE.clear()
            job.publish()
            assert current()["timeframe"] == "" and current()["setup"] is None
            assert current()["is_test_trade"] == "Yes"
            # Exact order -> position conversion; the old draft target cannot move.
            source["items"] = [dict(row, type="Position", id="position-0", raw_refs={"order_ids": ["entry"], "order_ids_complete": True})]
            job.publish()
            await save({"setup": "Breakout"}, target=initial_target, expected=409)
            assert current()["classification_target"] != initial_target
            await save({"is_test_trade": False})
            # Scope, lifecycle and contradictory exact IDs reject without a write.
            baseline_row = copy.deepcopy(source["items"][0])
            valid_target = current()["classification_target"]
            for change in ({"account_id": "wrong"}, {"account": "live"}, {"trade_id": "unrelated", "order_id": "wrong"}, {"raw_refs": {"order_ids": ["entry", "unlinked"], "order_ids_complete": True}}):
                source["items"] = [dict(baseline_row, **change)]
                job.publish()
                before = service.TRADE_CONTEXTS_PATH.read_bytes()
                await save({"setup": "Breakout"}, target=valid_target, expected=409)
                assert service.TRADE_CONTEXTS_PATH.read_bytes() == before
            source["items"] = [baseline_row]
            job.publish()
            # Real CSV exporter grouping retains its visible Bybit Demo account label.
            csv_path = tmp_path / "demo.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=service.BYBIT_TRADE_HISTORY_REQUIRED_HEADERS)
                writer.writeheader()
                for order, direction, price, execution, timestamp in (("entry", "Buy", "100", "open-fill", "2026-10-16 12:00:00"), ("exit", "Sell", "110", "close-fill", "2026-10-16 12:05:00")):
                    writer.writerow({"contracts": "BTCUSDT", "Order No.": order, "Direction": direction, "Order Type": "Limit", "Filled Qty": "0.01", "Filled Price": price, "Order Price": price, "Filled Type": "Trade", "Trading Fee Rate": "0.0006", "Fees Paid": "0.001", "Trasaction ID": execution, "Transaction Time(UTC+10)": timestamp, "Final Balance (USDT)": ""})
            rows, unmatched, _ = service._parse_bybit_trade_history_csv_with_diagnostics(csv_path, account_mode="demo")
            assert len(rows) == 1 and not unmatched and rows[0]["account"] == "Bybit Demo"
            patched = service._backfill_trade_row_context_fields(rows[0])
            assert patched["timeframe"] == "" and patched["setup"] == "" and patched["pattern"] == ""
            assert patched["vwap"] == "No" and patched["round_number"] == "No" and patched["is_test_trade"] is False
            assert patched["webhook_classification"] == "No"
            service._set_trading_journal_rows([patched], invalidate_view_snapshot=False, schedule_state_backup=False)
            persisted = json.loads(service.TRADING_JOURNAL_PATH.read_text())["items"][0]
            assert persisted["trade_number"] == allocation["trade_number"]
            merged = service._merge_trading_journal_row(persisted, dict(rows[0], setup="Pullback", timeframe="1h"))
            assert merged["setup"] == "" and merged["timeframe"] == ""
            manual = service._merge_trading_journal_row(dict(merged, manual_overrides={"setup": "News Scalp", "notes": "user note"}), rows[0])
            normalized, _ = folders._normalize_master_journal_rows([manual])
            assert normalized[0]["setup"] == "News Scalp" and normalized[0]["notes"] == "user note"
            assert not folders._target_r_row_is_test_trade(normalized[0])
            # Pending OANDA acceptance, exact fill alias, executed-webhook provenance.
            fx = dict(broker="oanda", account="demo", account_id="acct", category="forex", instrument="EUR_USD", asset="fx", side="Buy", order_id="fx-order", calculation_context_id="fx-quote", is_test_trade=True, open_time="2026-10-16T02:00:00Z", pending_webhook_id="actually-executed", webhook_status="Triggered")
            service._upsert_calculator_trade_context(fx, accepted_entry=True, invalidate_cache=False)
            fxrow = dict(broker="OANDA", account="demo", account_id="acct", category="forex", instrument="EUR_USD", side="Buy", type="Order", id="fx-order")
            source["items"] = [fxrow]
            job.publish()
            await save({"is_test_trade": False, "setup": "Pullback", "webhook_classification": "No"})
            service._upsert_trade_context(dict(fx, trade_id="fx-trade"))
            source["items"] = [dict(fxrow, type="Position", id="fx-trade", trade_id="fx-trade")]
            job.publish()
            assert current()["setup"] == "Pullback" and current()["webhook_mode"] == "Yes"
            assert current()["webhook_status"] == "Triggered" and current()["webhook_classification"] == "No"
            opening = dict(account="demo", accountID="acct", id="open-tx", orderID="fx-order", instrument="EUR_USD", units="100", price="1.1", time="2026-10-16T02:00:00Z", tradeOpened={"tradeID": "fx-trade", "units": "100", "price": "1.1"})
            service._journal_rows_from_oanda_order_fill(opening)
            closed = service._journal_rows_from_oanda_order_fill(dict(opening, id="close-tx", orderID="fx-close", units="-100", price="1.2", time="2026-10-16T03:00:00Z", tradeOpened=None, tradesClosed=[{"tradeID": "fx-trade", "units": "100", "realizedPL": "10"}]))
            assert len(closed) == 1 and closed[0]["raw_refs"]["opening_order_id"] == "fx-order"
            assert closed[0]["setup"] == "Pullback" and closed[0]["is_test_trade"] is False
            assert closed[0]["webhook_classification"] == "No"
            service._TRADE_CONTEXTS_CACHE.clear()
            service._drain_early_trade_folders()
            assert len(folders._load_trade_allocations()) == 2
            assert screenshot.read_bytes() == b"keep screenshot"
        await owner.close()
    asyncio.run(scenario())


def test_unused_trendline_plan_delete_route_is_permanent_and_protected(tmp_path, monkeypatch):
    import copy
    import runpy
    service = master_service
    store = service.TrendlinePlanStore(tmp_path / "delete-registry.json")
    monkeypatch.setattr(service, "TRENDLINE_PLAN_STORE", store)
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "_trendline_response_state", lambda: {})
    external = []
    def forbidden(*args, **kwargs):
        external.append(True)
        raise AssertionError("Deletion must not contact external services")
    monkeypatch.setattr(service.httpx.Client, "request", forbidden)
    monkeypatch.setattr(service.httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(service, "_bybit_signed_post", forbidden)
    payload = runpy.run_path(str(ROOT / "tests/test_trendline_plans.py"))["_payload"]()
    def create(**overrides):
        settings = copy.deepcopy(payload)
        settings.update(overrides)
        return store.create(settings, now_ms=5000)
    def delete(pid):
        return json.loads(_run_immediate_route(service.trendline_plan_delete(pid)).body)
    keep = create()
    draft = create()
    cancelled = create()
    store.cancel(cancelled["plan_id"], now_ms=7000)
    expired_draft = create(expiry_at_ms=30000)
    expired_draft = store.expire(expired_draft["plan_id"], now_ms=30000)
    expired_armed = create(expiry_at_ms=30000)
    store.arm(expired_armed["plan_id"], now_ms=6000)
    expired_armed = store.expire(expired_armed["plan_id"], now_ms=31000)
    assert expired_armed["execution_key"] and expired_armed["execution_policy"] == "bybit-broker-v1"
    assert expired_armed["trigger_claim"] is None and expired_armed["live_authorization"] is None
    listed = json.loads(_run_immediate_route(service.trendline_plan_list()).body)["plans"]
    capabilities = {plan["plan_id"]: plan["can_delete"] for plan in listed}
    assert capabilities[expired_draft["plan_id"]] and capabilities[expired_armed["plan_id"]]
    assert service._trendline_plan_client_view(draft)["can_delete"] is True
    assert "can_delete" not in store.get(draft["plan_id"])
    for pid in (draft["plan_id"], cancelled["plan_id"], expired_draft["plan_id"], expired_armed["plan_id"]):
        before = store.load()
        result = delete(pid)
        assert result["ok"] is True and result["deleted_plan_id"] == pid
        fresh = service.TrendlinePlanStore(store.path).load()
        assert fresh["schema_version"] == before["schema_version"]
        assert fresh["plans"] == {key: value for key, value in before["plans"].items() if key != pid}
        assert store.path.exists() and store.path.with_name(store.path.name + ".lock").exists()
        with pytest.raises(service.HTTPException) as missing:
            delete(pid)
        assert missing.value.status_code == 404
    assert store.get(keep["plan_id"]) == keep
    with pytest.raises(service.HTTPException) as missing:
        delete("never-existed")
    assert missing.value.status_code == 404
    for profile in ("render", "journal"):
        monkeypatch.setattr(service, "APP_PROFILE", profile)
        before = store.path.read_bytes()
        with pytest.raises(service.HTTPException) as restricted:
            delete(keep["plan_id"])
        assert restricted.value.status_code == 410 and store.path.read_bytes() == before
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    # Capability is advisory. Another store arms a previously deletable draft;
    # deletion must read/check the now-active record inside its own lock.
    stale = create()
    assert service._trendline_plan_client_view(stale)["can_delete"]
    service.TrendlinePlanStore(store.path).arm(stale["plan_id"], now_ms=6000)
    protected = [stale["plan_id"]]
    armed_past_deadline = create(expiry_at_ms=30000)
    store.arm(armed_past_deadline["plan_id"], now_ms=6000)
    assert store.get(armed_past_deadline["plan_id"])["status"] == "armed"
    protected.append(armed_past_deadline["plan_id"])
    # Only a persisted expiry transition is proof; neither the status label nor
    # wall-clock passage can erase execution/submission evidence.
    for kind in ("lifecycle", "missing_expiry", "early_transition", "submission_refs", "execution_result", "live_authorization"):
        record = create(expiry_at_ms=30000)
        record = store.expire(record["plan_id"], now_ms=30000)
        if kind == "lifecycle":record["lifecycle"]["last_transition"] = "cancelled"
        elif kind == "missing_expiry":record["expiry_at_ms"] = None
        elif kind == "early_transition":record["lifecycle"]["last_transition_at_ms"] = 29999
        elif kind == "submission_refs":record["submission_refs"] = {"order_id":"synthetic-order"}
        elif kind == "execution_result":record["execution_result"] = {"outcome":"executed","recorded_at_ms":30000,"order_id":"synthetic-order"}
        else:record["live_authorization"] = {"execution_key":record["execution_key"],"plan_revision":record["revision"],"authorized_at_ms":30000}
        with store._locked():
            registry = store._read();registry["plans"][record["plan_id"]] = record;store._write(registry)
        protected.append(record["plan_id"])
    # Invalid claim/closure combinations cannot be canonicalized; the capability
    # also refuses these raw records before any persistence is attempted.
    for field in ("trigger_claim", "broker_closure", "submission_refs"):
        malformed = copy.deepcopy(expired_draft);malformed[field] = {}
        assert store.can_delete(malformed) is False
    for expiry,transitioned in [(None,30000),(True,30000),(30000,"30000"),(30000,29999)]:
        malformed = copy.deepcopy(expired_draft);malformed["expiry_at_ms"] = expiry;malformed["lifecycle"]["last_transition_at_ms"] = transitioned
        assert store.can_delete(malformed) is False
    stale_expired = create(expiry_at_ms=30000)
    stale_expired = store.expire(stale_expired["plan_id"], now_ms=30000)
    assert service._trendline_plan_client_view(stale_expired)["can_delete"]
    other_store = service.TrendlinePlanStore(store.path)
    with other_store._locked():
        registry = other_store._read();registry["plans"][stale_expired["plan_id"]]["submission_refs"] = {"order_link_id":"synthetic-submission"};other_store._write(registry)
    protected.append(stale_expired["plan_id"])
    for status in ("claimed", "submitting", "submitted", "failed", "uncertain", "disabled", "expired"):
        record = create()
        if status in {"claimed", "submitting", "submitted", "failed", "uncertain"}:
            store.arm(record["plan_id"], now_ms=6000)
            record = store.claim_trigger(record["plan_id"], {"trigger_timestamp_ms":20000,"trigger_price":"1.2","trigger_kind":"entered_band"}, now_ms=20000)["plan"]
        record["status"] = status
        record["lifecycle"]["last_transition"] = "claim_" + status
        with store._locked():
            registry = store._read()
            registry["plans"][record["plan_id"]] = record
            store._write(registry)
        protected.append(record["plan_id"])
    # Even a draft/cancelled label cannot erase execution/auth/lifecycle evidence.
    for kind in ("execution_result", "live_authorization", "lifecycle"):
        record = create()
        if kind == "execution_result":
            record[kind] = {"outcome":"executed","recorded_at_ms":6000,"order_id":"synthetic-order"}
        elif kind == "live_authorization":
            record[kind] = {"execution_key":record["execution_key"],"plan_revision":1,"authorized_at_ms":6000}
        else:
            record[kind] = {"last_transition":"claim_submitted","last_transition_at_ms":6000}
        with store._locked():
            registry = store._read()
            registry["plans"][record["plan_id"]] = record
            store._write(registry)
        protected.append(record["plan_id"])
    for pid in protected:
        assert service._trendline_plan_client_view(store.get(pid))["can_delete"] is False
        before = store.path.read_bytes()
        with pytest.raises(service.HTTPException) as blocked:
            delete(pid)
        assert blocked.value.status_code == 409 and store.path.read_bytes() == before
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", lambda data: (_ for _ in ()).throw(service.TrendlinePlanPersistenceError("synthetic disk failure")))
        with pytest.raises(service.HTTPException) as failure:
            delete(keep["plan_id"])
        assert failure.value.status_code == 500
    assert store.path.read_bytes() == before
    assert not external and "can_delete" not in store.path.read_text()

    # Finished cards retain the complete durable execution and dashboard history.
    def finish(*, simulated=False, closed=False, reference=True):
        record = create(broker="oanda" if simulated else "bybit", test_trade=True)
        store.arm(record["plan_id"], now_ms=6000)
        record = store.claim_trigger(record["plan_id"], {"trigger_timestamp_ms":20000,"trigger_price":"1.2","trigger_kind":"entered_band"}, now_ms=20000)["plan"]
        if not simulated:
            store.begin_submission(record["plan_id"], execution_key=record["execution_key"], now_ms=21000,
                submission_refs={"order_link_id":record["execution_key"],"category":"linear"})
        outcome = {"outcome":"simulated" if simulated else "executed"}
        if reference and not simulated:outcome["order_id"] = "accepted-" + record["plan_id"]
        record = store.resolve_claim(record["plan_id"], status="submitted", outcome=outcome, now_ms=22000)
        if closed:
            proof = dict(kind="bybit_full_execution_ledger",account=record["account"],instrument=record["instrument"],category="linear",
                settlement_currency="USDT",position_idx=0,order_id=record["execution_result"]["order_id"],order_link_id=record["execution_key"],
                closed_at_ms=23000,closing_order_id="close-"+record["plan_id"],opening_quantity="1")
            record = store.record_broker_closure(record["plan_id"], expected_revision=record["revision"], execution_key=record["execution_key"], order_id=proof["order_id"], proof=proof)
        return record
    def dismiss(record, **extra):
        return json.loads(_run_immediate_route(service.trendline_plan_dismiss_calculator(record["plan_id"], {"revision":record["revision"], **extra})).body)
    closed, simulated, accepted, missing = finish(closed=True), finish(simulated=True), finish(), finish(reference=False)
    assert accepted["test_trade"] is True and accepted["execution_result"]["outcome"] == "executed"
    assert service._trendline_plan_client_view(closed)["calculator_finish_kind"] == "closed"
    assert service._trendline_plan_client_view(simulated)["calculator_finish_kind"] == "simulated"
    assert service._trendline_plan_client_view(accepted)["calculator_finish_kind"] == "confirm_finished"
    assert "calculator_dismissal" not in keep and "calculator_dismissal" not in store.get(keep["plan_id"])
    for record,extra in [(accepted,{}),(accepted,{"confirm_finished":"true"}),(closed,{"revision":closed["revision"]-1}),(missing,{"confirm_finished":True})]:
        before = store.path.read_bytes()
        with pytest.raises(service.HTTPException) as blocked:dismiss(record, **extra)
        assert blocked.value.status_code == 409 and store.path.read_bytes() == before
    for pid in protected:
        record = store.get(pid)
        assert not service._trendline_plan_client_view(record)["can_dismiss_calculator"]
        before = store.path.read_bytes()
        with pytest.raises(service.HTTPException) as blocked:dismiss(record, confirm_finished=True)
        assert blocked.value.status_code == 409 and store.path.read_bytes() == before
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", lambda data: (_ for _ in ()).throw(service.TrendlinePlanPersistenceError("synthetic disk failure")))
        with pytest.raises(service.HTTPException) as failure:dismiss(accepted, confirm_finished=True)
        assert failure.value.status_code == 500
    assert store.path.read_bytes() == before
    for record in (closed,simulated,accepted):
        result = dismiss(record, **({"confirm_finished":True} if record is accepted else {}))
        assert result["ok"] and result["dismissed_plan_id"] == record["plan_id"]
        saved = service.TrendlinePlanStore(store.path).get(record["plan_id"])
        assert {k:v for k,v in saved.items() if k != "calculator_dismissal"} == record
        assert saved["calculator_dismissal"]["plan_revision"] == record["revision"]
        before = store.path.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(store,"_write",lambda data: (_ for _ in ()).throw(AssertionError("Duplicate must not write")))
            assert dismiss(record, **({"confirm_finished":True} if record is accepted else {}))["ok"]
        assert store.path.read_bytes() == before
    listed = json.loads(_run_immediate_route(service.trendline_plan_list()).body)["plans"]
    assert any(p["plan_id"] == accepted["plan_id"] and p["calculator_dismissal"] for p in listed)
    # Native broker rows still project, and simulation dashboard dismissal is independent.
    native = dict(broker="Bybit",account=accepted["account"],instrument=accepted["instrument"],category="linear",side=accepted["action"],type="order",id=accepted["execution_result"]["order_id"])
    projected = service._trendline_dashboard_rows([native],plans=[store.get(accepted["plan_id"])],contexts=[])
    assert any(row.get("id") == native["id"] for row in projected)
    assert service._trendline_dashboard_rows([],plans=[store.get(simulated["plan_id"])],contexts=[])
    assert store.get(simulated["plan_id"])["dashboard_dismissal"] is None
    # Exact later broker closure is still recorded on a manually dismissed plan.
    proof = dict(closed["broker_closure"],order_id=accepted["execution_result"]["order_id"],order_link_id=accepted["execution_key"],closing_order_id="later-close")
    later = store.record_broker_closure(accepted["plan_id"],expected_revision=accepted["revision"],execution_key=accepted["execution_key"],order_id=proof["order_id"],proof=proof)
    assert later["broker_closure"] == proof and later["calculator_dismissal"] == store.get(accepted["plan_id"])["calculator_dismissal"]
    for action in (lambda:store.create({**payload,"calculator_dismissal":{}},now_ms=5000),lambda:store.update(keep["plan_id"],{"calculator_dismissal":{}},now_ms=6000),lambda:_run_immediate_route(service.trendline_plan_create({"calculator_dismissal":{}})),lambda:_run_immediate_route(service.trendline_plan_update(keep["plan_id"],{"calculator_dismissal":{}}))):
        before=store.path.read_bytes()
        with pytest.raises((service.TrendlinePlanError,service.HTTPException)):action()
        assert store.path.read_bytes()==before
    assert not external


@pytest.fixture
def pepperstone_account_fixture(monkeypatch, tmp_path):
    import selectors
    import threading
    service = master_service
    # Single-thread fixture loop: no socket self-pipe or external I/O, real task/lock semantics.
    class FixtureSelector(selectors.SelectSelector):
        def select(self, timeout=None):
            if not self.get_map():
                if timeout:
                    time.sleep(timeout)
                return []
            return super().select(timeout)
    class FixtureLoop(asyncio.SelectorEventLoop):
        def _make_self_pipe(self):
            self._ssock = self._csock = None
        def _close_self_pipe(self):
            pass
        def _write_to_self(self):
            pass
    def run(coro):
        loop = FixtureLoop(FixtureSelector())
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    monkeypatch.setattr(asyncio, "run", run)
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_LOCK", threading.RLock())
    monkeypatch.setattr(service, "_PEPPERSTONE_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(service, "_PEPPERSTONE_QUOTE_SNAPSHOTS_LOCK", threading.RLock())
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(tmp_path / "no-broker-metadata.json"))
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "4.20")
    return service._upsert_calculator_trade_context


def test_pepperstone_commission_chart_r_preview(monkeypatch, tmp_path, pepperstone_account_fixture):
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "6.00")
    saved = []
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda data, **kwargs: saved.append(data))
    request = _pepperstone_quote_payload(symbol="GBPUSD", side="buy", account="demo",
        pepperstone_account_type="razor", risk_mode="fixed_aud", risk_value="10.20",
        chart_tick_size="0.00001", stop_loss_ticks="60", target_mode="rr", risk_reward="3.50")

    http_request = master_service.Request({"type": "http", "scheme": "http", "path": "/api/calculator/commission-preview", "headers": [], "server": ("localhost", 80)})
    def preview(**changes):
        result = asyncio.run(master_service.calculator_commission_preview(http_request, dict(request, **changes)))
        return json.loads(result.body)

    expected = {"chart_r": "4.10", "commission_r": "0.60"}
    assert preview(webhook="yes", pending_webhook_id="must-not-mutate") == {"commission_chart_r": expected}
    assert preview(pepperstone_account_type="standard") == {"commission_chart_r": None}
    assert preview(target_mode="ticks", take_profit_ticks="90") == {"commission_chart_r": None}
    assert preview(target_basis="price_r") == {"commission_chart_r": None}
    assert not saved and master_service._PEPPERSTONE_QUOTE_SNAPSHOTS == {}
    quote = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
    assert quote["commission_chart_r"] == expected
    assert Decimal(quote["quantity"]) == Decimal("10000")
    assert Decimal(quote["estimated_total_loss_aud"]) == Decimal("10.20")
    assert Decimal(quote["estimated_reward_aud"]) + Decimal(quote["estimated_commission_aud"]) == Decimal("36.90")
    assert len(saved) == 1
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "0")
    assert preview() == {"commission_chart_r": None}


def test_pepperstone_account_quote_costs(monkeypatch, tmp_path, pepperstone_account_fixture):
    monkeypatch.setattr(asyncio, "run", asyncio.runners.run)
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", pepperstone_account_fixture)
    D = Decimal
    for symbol, factor, step in [("GBPUSD", D("1.5"), D("0.00001")), ("USDJPY", D("0.01"), D("0.001"))]:
        for side in ("buy", "sell"):
            for kind in ("razor", "standard"):
                for mode in ("ticks", "rr"):
                    request = _pepperstone_quote_payload(symbol=symbol, side=side, pepperstone_account_type=kind,
                        target_mode=mode, take_profit_ticks="25", risk_reward="2", chart_tick_size=str(step))
                    q = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
                    cost = D("8.40") / D("100000") if kind == "razor" else D(0)
                    loss = 43 * step * factor + cost
                    units = D(10) / loss
                    assert abs(D(q["quantity"]) - units) < D("0.000001")
                    assert abs(D(q["estimated_commission_aud"]) - units * cost) < D("0.000001")
                    assert abs(D(q["estimated_spread_aud"]) - units * D("0.00020") * factor) < D("0.000001")
                    assert abs(D(q["estimated_total_loss_aud"]) - 10) < D("0.000001")
                    distance = 25 * step if mode == "ticks" else (D(2) * loss + cost) / factor
                    assert abs(D(q["target_distance"]) - distance) < D("0.000001")
                    reward = (distance * factor - cost) * units
                    assert abs(D(q["estimated_reward_aud"]) - reward) < D("0.000001")
                    assert D(q["effective_rr_net"]) == (reward / D(10)).quantize(D("0.01"), rounding=master_service.ROUND_HALF_UP)
                    assert q["commission_currency"] == "AUD"
                    assert "not measured Pepperstone" in q["spread_estimate_note"]
    legacy = json.loads(asyncio.run(master_service.calculator_quote(_pepperstone_quote_payload())).body)
    assert legacy["pepperstone_account_type"] == "razor"
    for invalid in ("invalid", "", None):
        with pytest.raises(master_service.HTTPException):
            asyncio.run(master_service.calculator_quote(_pepperstone_quote_payload(pepperstone_account_type=invalid)))
    for invalid in ("NaN", "Infinity", "-1", "bad"):
        monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", invalid)
        with pytest.raises(master_service.HTTPException):
            asyncio.run(master_service.calculator_quote(_pepperstone_quote_payload()))
    rows = master_service._load_trade_contexts()
    assert any(row.get("pepperstone_account_type") == "standard" for row in rows)


def test_pepperstone_account_export_integrity(monkeypatch, tmp_path, pepperstone_account_fixture):
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", pepperstone_account_fixture)
    for kind in ("razor", "standard"):
        monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "4.20")
        request = _pepperstone_quote_payload(pepperstone_account_type=kind)
        q = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        payload = _pepperstone_set_payload_from_quote(request, q)
        monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "99.00")
        text, _ = master_service._build_pepperstone_trader_set(payload)
        values = _parse_set_file(text)
        assert Decimal(values["CommissionPerLotPerSide"]) == (Decimal("4.20") if kind == "razor" else 0)
        assert values["IncludeCommissionInRisk"] == "true"
        assert values["AutoTP_NetRR_Enabled"] == "true"
        assert values["AutoFitRiskSlippageBuffer"] == "true"
        assert values["EnforceOneTradeAtATime"] == "true"
        assert Decimal(values["RiskAUD_Min"]) == 9 and Decimal(values["RiskAUD_Max"]) == 12
        def forbidden():
            raise AssertionError("must reject before generating token")
        with monkeypatch.context() as guard:
            guard.setattr(master_service, "uuid4", forbidden)
            for field in ("commission_per_lot_per_side_aud", "estimated_commission_aud", "CommissionPerLotPerSide", "estimated_fees_or_spread"):
                with pytest.raises(master_service.HTTPException):
                    master_service._build_pepperstone_trader_set(dict(payload, **{field: "0"}))
            with pytest.raises(master_service.HTTPException):
                master_service._build_pepperstone_trader_set(dict(payload, pepperstone_account_type="standard" if kind == "razor" else "razor"))
            omitted = dict(payload); omitted.pop("pepperstone_account_type")
            if kind == "standard":
                with pytest.raises(master_service.HTTPException):
                    master_service._build_pepperstone_trader_set(omitted)
        if kind == "razor":
            snap = master_service._get_pepperstone_quote_snapshot(q["calculation_context_id"])
            for field in ("pepperstone_account_type", "commission_per_lot_per_side_aud", "commission_currency"):
                snap.pop(field, None)
            master_service._store_pepperstone_quote_snapshot(q["calculation_context_id"], snap)
            omitted = dict(payload); omitted.pop("pepperstone_account_type")
            text, _ = master_service._build_pepperstone_trader_set(omitted)
            assert Decimal(_parse_set_file(text)["CommissionPerLotPerSide"]) == Decimal("3.50")


def test_pepperstone_executable_price_rr_does_not_double_count_spread(monkeypatch, tmp_path, pepperstone_account_fixture):
    # Cash ledger from signed entry/exit prices and two separate commission charges.
    # Benchmarks: 1 lot loses AUD15 at 10 ticks; Razor adds AUD7 per lot.
    # Keep the existing Windows-safe fixture loop; real asyncio tasks/locks remain active.
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", pepperstone_account_fixture)
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "3.50")
    D = Decimal
    cases = [("EURUSD", "1.20000", "0.00001", "1.5", "1.5"),
             ("EURUSD", "1.20000", "0.00001", "1.4", "1.5"),
             ("USDJPY", "150.000", "0.001", "0.01", "0.011")]
    for symbol, entry_text, tick_text, gain_text, loss_text in cases:
        entry, tick, gain, loss = map(D, (entry_text, tick_text, gain_text, loss_text))
        for side in ("buy", "sell"):
            direction = D(1) if side == "buy" else D(-1)
            for account in ("demo", "live"):
                for kind in ("standard", "razor"):
                    unchanged = None
                    for spread_ticks in (1, 5, 9):
                        spread = tick * spread_ticks
                        async def prices(**kwargs):
                            bid = entry if side == "sell" else entry - spread
                            ask = entry if side == "buy" else entry + spread
                            return {"prices": [{"bids": [{"price": str(bid)}], "asks": [{"price": str(ask)}]}],
                                "homeConversions": [{"currency": "JPY" if symbol.endswith("JPY") else "USD",
                                  "accountGain": str(gain), "accountLoss": str(loss), "positionValue": str(loss)}]}
                        monkeypatch.setattr(master_service, "_fetch_oanda_json", prices)
                        request = _pepperstone_quote_payload(symbol=symbol, side=side, account=account,
                            pepperstone_account_type=kind, stop_loss_ticks="10", chart_tick_size=tick_text,
                            target_mode="rr", risk_reward="2")
                        q = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
                        qty, actual_entry, stop, target = map(D, (q["quantity"], q["entry_price"], q["stop_price"], q["target_price"]))
                        assert actual_entry == entry and stop == entry - direction * 10 * tick
                        lots = qty / D(100000)
                        opening = lots * (D("3.50") if kind == "razor" else D(0))
                        closing = opening
                        stop_cash = direction * (stop - actual_entry) * qty * loss - opening - closing
                        target_cash = direction * (target - actual_entry) * qty * gain - opening - closing
                        assert abs(stop_cash + 10) < D("1e-18")
                        assert abs(target_cash - 20) < D("1e-18")
                        assert abs(D(q["estimated_total_loss_aud"]) + stop_cash) < D("1e-18")
                        assert abs(D(q["estimated_reward_aud"]) - target_cash) < D("1e-18")
                        assert abs(D(q["estimated_fees_or_spread_aud"]) - opening - closing) < D("1e-18")
                        assert D(q["estimated_spread_aud"]) > 0 and D(q["spread_quote"]) == spread
                        ledger = (qty, stop, target, stop_cash, target_cash)
                        if unchanged is not None:
                            assert ledger == unchanged
                        unchanged = ledger
                        if gain == loss == D("1.5"):
                            assert abs(target - entry) / tick == (20 if kind == "standard" else 34)
                            expected_qty = D(200000) / 3 if kind == "standard" else D(500000) / 11
                            assert abs(qty - expected_qty) < D("1e-18")
                    # Manual distances remain literal; an inactive R cannot affect the ledger.
                    request.update(target_mode="ticks", take_profit_ticks="1", risk_reward="inactive")
                    manual = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
                    assert D(manual["stop_price"]) == entry - direction * 10 * tick
                    assert D(manual["target_price"]) == entry + direction * tick
                    qty = D(manual["quantity"])
                    fees = qty / 100000 * (D(7) if kind == "razor" else D(0))
                    profit = direction * (D(manual["target_price"]) - entry) * qty * gain - fees
                    assert abs(D(manual["estimated_reward_aud"]) - profit) < D("1e-18")
                    if kind == "razor" and symbol == "EURUSD":
                        assert profit < 0
                    text, _ = master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request, manual))
                    preset = _parse_set_file(text)
                    assert D(preset["CommissionPerLotPerSide"]) == (D("3.50") if kind == "razor" else 0)
                    assert preset["PresetAccountType"]==kind
                    assert preset["IncludeCommissionInRisk"] == "true" and preset["AutoTP_NetRR_Enabled"] == "false"
                    assert D(preset["SL_PriceDistance"]) == 10 * tick
                    assert D(preset["TP_PriceDistance"]) == tick
    # Fresh matching AUD Trader snapshots: real Calculate -> trusted portable preset.
    from datetime import datetime, timezone, timedelta
    stamp = datetime.now(timezone.utc).isoformat()
    export_path = tmp_path / "sizing-export.json"
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export_path))
    item = dict(symbol="USDJPY", mt5_symbol="USDJPY", point="0.001", trade_tick_size="0.001",
                stops_level=0, freeze_level=0, bid="159.551", ask="159.552", available=True,
                timestamp=stamp, quote_timestamp=stamp, minimum_lot="0.01", volume_step="0.01",
                maximum_lot="100", tick_value="1.5")
    def write_snapshot(**changes):
        export_path.write_text(json.dumps(dict(version=1, broker="pepperstone", generated_at=stamp,
            account=dict(mode=changes.pop("mode", "demo"), currency=changes.pop("currency", "AUD")),
            symbols=changes.pop('symbols', None) or [dict(item, **changes)])), encoding="utf-8")
    request = _pepperstone_quote_payload(symbol="USDJPY", side="buy", account="demo",
        pepperstone_account_type="standard", stop_loss_ticks="10", chart_tick_size="0.001",
        target_mode="rr", risk_reward="2", risk_mode="fixed_aud", risk_value="10")
    for changes, lots, nominal in [({}, ".67", "10.05"),
            ({"minimum_lot":".1","volume_step":".1","tick_value":"3.5"}, ".3", "10.5"),
            ({"minimum_lot":".1","volume_step":".1","tick_value":"12"}, ".1", "12"),
            ({"tick_value":"2"}, ".5", "10"),
            ({"tick_value":"2.8"}, ".36", "10.08"),
            ({"minimum_lot":".25","volume_step":".25"}, ".75", "11.25"),
            ({"tick_value":"1.99999999999999999999"}, ".51", "10.199999999999999999949"),
            ({"minimum_lot":".2","volume_step":".2"}, ".8", "12"),
            ({"minimum_lot":".001","volume_step":".001"}, ".667", "10.005"),
            ({"minimum_lot":".7"}, ".7", "10.5")]:
        write_snapshot(**changes)
        q = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        model = q["pepperstone_lot_estimate"]
        assert model["state"] == "ready" and D(model["lots"]) == D(lots)
        assert D(q["quantity"]) == D(lots)*100000 and D(model["nominal_loss_aud"]) == D(nominal)
        assert D(q["entry_price"]) == D("159.552") and D(q["stop_loss_price_distance"]) == D(".010")
        assert D(q["estimated_total_loss_aud"]) == D(nominal).quantize(D(".01"),rounding="ROUND_HALF_UP") and D(q["estimated_commission_aud"]) == 0
        assert D(model["lots"]) % D(model["volume_step"]) == 0
        assert D(model["volume_min"]) <= D(model["lots"]) <= D(model["volume_max"])
        previous = D(model["lots"])-D(model["volume_step"])
        assert previous < D(model["volume_min"]) or previous*D(model["loss_per_lot_aud"]) < D(10)
        assert D(10) <= D(nominal) <= D(model["buffered_loss_aud"]) <= D(12)
        r_base = D(q["estimated_total_loss_aud"])
        actual_profit=abs(D(q["target_price"])-D(q["entry_price"]))/D(model["trade_tick_size"])*D(model["tick_value_aud"])*D(model["lots"])
        assert abs(actual_profit-D(model["target_valuation_aud"]))<D("1e-20")
        assert q["pepperstone_mt5_export_status"]["file_ready"] is True
        assert abs(D(q["target_price"])-D(q["entry_price"]))==D('.020')
        assert D(q["estimated_reward_aud"])==(r_base*2).quantize(D(".01"),rounding="ROUND_HALF_UP")
        assert abs(D(q["effective_rr_net"])-D(q["estimated_reward_aud"])/r_base) <= D(".01")
        assert D(q["spread_quote"]) == D(".001")
        assert model["buffer_points"] <= int(D(model["conservative_buffer_cap_points"]))
        text, _ = master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request,q))
        preset = _parse_set_file(text)
        assert tuple(D(preset[k]) for k in ("RiskAUD_Min","RiskAUD_Target","RiskAUD_Max")) == (D(10),D(10),D(12))
        assert preset["AutoFitRiskSlippageBuffer"] == "true" and preset["SlippagePoints"] == "10"
        assert D(preset["CommissionPerLotPerSide"]) == 0 and D(preset["SL_PriceDistance"]) == D(".010")
        assert preset["AutoTP_NetRR_Enabled"] == "true" and D(preset["NetRR_Target"]) == 2
    write_snapshot()
    for limit_side,limit_entry in [("buy","159.540"),("sell","159.570")]:
        manual_request = dict(request, side=limit_side, order_type="limit", entry_price=limit_entry,
                              target_mode="ticks", take_profit_ticks="25", risk_reward="inactive")
        q=json.loads(asyncio.run(master_service.calculator_quote(manual_request)).body)
        assert D(q["entry_price"])==D(limit_entry) and D(q["stop_loss_price_distance"])==D(".010")
        assert abs(D(q["target_price"])-D(limit_entry))==D(".025")
        text,_=master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(manual_request,q))
        preset=_parse_set_file(text);assert D(preset["StandardLimitEntryPrice"])==D(limit_entry)
        assert preset["AutoTP_NetRR_Enabled"]=="false" and D(preset["TP_PriceDistance"])==D(".025")
    for changes in [{"minimum_lot":".1","volume_step":".1","tick_value":"15"},
                    {"minimum_lot":".1","volume_step":".1","tick_value":"9"},
                    {"maximum_lot":".66"}, {"maximum_lot":".5"}, {"minimum_lot":"1"}]:
        write_snapshot(**changes);q=json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        assert q["pepperstone_lot_estimate"]["state"]=="no_fit" and D(q["quantity"])==0
        assert q["pepperstone_mt5_export_status"]["file_ready"] is False
        with pytest.raises(master_service.HTTPException) as error:
            master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request,q))
        assert error.value.status_code==422
    for changes in [{"volume_step":None}, {"tick_value":"0"}, {"mode":"live"}, {"currency":"USD"},
                    {"quote_timestamp":(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()}]:
        write_snapshot(**changes);q=json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        assert q["pepperstone_lot_estimate"]["state"]=="pending" and D(q["quantity"])>0
        assert q["pepperstone_mt5_export_status"]["file_ready"] is True
        text,_=master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request,q))
        assert D(_parse_set_file(text)["RiskAUD_Max"]) == D(12)
    assert master_service._pepperstone_risk_band(D("10.01"))==(D("10.01"),D("10.01"),D("12.01"))
    assert master_service._pepperstone_risk_band(D("10.001"))==(D("10.001"),D("10.001"),D("12"))
    assert master_service._pepperstone_risk_buffer_preflight(stop_points=30,risk_target=D(10))["conservative_max_buffer_points"]==6
    write_snapshot(minimum_lot=".001", volume_step=".001")
    fractional_request=dict(request,risk_value="10.001",target_mode="ticks",take_profit_ticks="25",risk_reward="inactive")
    fractional=json.loads(asyncio.run(master_service.calculator_quote(fractional_request)).body)
    assert D(fractional["pepperstone_lot_estimate"]["nominal_loss_aud"])==D("10.005")
    assert D(fractional["pepperstone_lot_estimate"]["lots"])==D(".667")
    text,_=master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(fractional_request,fractional))
    preset=_parse_set_file(text)
    assert D(preset["RiskAUD_Min"])==D(preset["RiskAUD_Target"])==D("10.001")

    # Independently derive P&L from final executable price; cushion must not pad TP.
    money=lambda value:D(value).quantize(D('.01'),rounding='ROUND_HALF_UP')
    for side in ('buy','sell'):
        for order in ('market','limit'):
            results=[]
            for preferred in ('0','50'):
                monkeypatch.setenv('PEPPERSTONE_TRADER_RISK_SLIPPAGE_BUFFER_POINTS',preferred)
                write_snapshot(bid='159.358',ask='159.359',tick_value='.34',minimum_lot='1',volume_step='1')
                req=dict(request,side=side,order_type=order,stop_loss_ticks='30',risk_reward='2.5')
                if order=='limit':req['entry_price']='159.350' if side=='buy' else '159.370'
                result=json.loads(asyncio.run(master_service.calculator_quote(req)).body);model=result['pepperstone_lot_estimate']
                assert D(result['estimated_total_loss_aud'])==D('10.20') and D(result['estimated_reward_aud'])==D('25.50')
                assert D(model['nominal_loss_aud'])==D('10.20') and D(model['net_r_base_aud'])==D('10.20')
                assert D(result['effective_rr_net'])==D('2.50')
                actual=abs(D(result['target_price'])-D(result['entry_price']))/D(model['trade_tick_size'])*D(model['tick_value_aud'])*D(model['lots'])
                assert actual==D('25.50')
                assert abs(D(result['target_price'])-D(result['entry_price']))==D('.075')!=D('.088')
                assert abs(D(result['stop_price'])-D(result['entry_price']))==D('.030')
                if order=='limit':assert D(result['entry_price'])==D(req['entry_price'])
                if preferred=='50':assert model['buffer_points']>0 and D(model['buffered_loss_aud'])>D(model['nominal_loss_aud'])
                preset=_parse_set_file(master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(req,result))[0])
                assert preset['AutoTP_NetRR_Enabled']=='true' and preset['AutoTP_SafetyPoints']=='0'
                assert D(preset['NetRR_Target'])==D('2.5') and D(preset['TP_PriceDistance'])==D('.075') and D(preset['SL_PriceDistance'])==D('.030')
                results.append((result['target_price'],result['estimated_reward_aud'],result['effective_rr_net']))
            assert results[0]==results[1]
    for point,tick_value in [('0.000001','.0003401333333333333333333333333'),('.001','.3401333333333333333333333333')]:
        write_snapshot(point=point,trade_tick_size=point,bid='159.358',ask='159.359',tick_value=tick_value,minimum_lot='1',volume_step='1')
        req=dict(request,stop_loss_ticks='30',risk_reward='2.5')
        result=json.loads(asyncio.run(master_service.calculator_quote(req)).body);model=result['pepperstone_lot_estimate']
        assert money(model['nominal_loss_aud'])==D('10.20') and money(D(model['nominal_loss_aud'])*D('2.5'))==D('25.51')
        actual=abs(D(result['target_price'])-D(result['entry_price']))/D(point)*D(tick_value)*D(model['lots'])
        assert abs(actual-D(model['target_valuation_aud']))<D('1e-20') and money(actual)==D('25.51')
        assert D(result['target_price'])%D(point)==0
        assert D(result['estimated_reward_aud'])==D('25.50') and result['pepperstone_mt5_export_status']['file_ready'] is True
        assert abs(D(result['target_price'])-D(result['entry_price']))==D('.075')
        preset=_parse_set_file(master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(req,result))[0])
        assert D(preset['TP_PriceDistance'])==D('.075')

    contexts = master_service._load_trade_contexts()
    assert any(row.get("planned_quantity_estimate") and row.get("original_target_distance") for row in contexts)




def test_arm_route_selected_revision_and_monitoring_view(tmp_path, monkeypatch):
    from render.trendline_executor import TrendlinePlanExecutor
    from render.trendline_plans import live_execution_authorized
    from test_trendline_executor import _payload, _run_fixture_loop
    store = master_service.TrendlinePlanStore(tmp_path / "route.json")
    e = TrendlinePlanExecutor(store, lambda p: {}, lambda p,q: {}, lambda *a: {}, live_execution_authorized, now_ms=lambda:2500)
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "TRENDLINE_PLAN_STORE", store)
    monkeypatch.setattr(master_service, "TRENDLINE_EXECUTOR", e)
    async def idle(): await e._stop.wait()
    monkeypatch.setattr(e, "_run", idle)
    assert 'id="trendline-monitor-start"' not in master_service.CALCULATOR_TEMPLATE
    assert 'Arm starts watching' in master_service.CALCULATOR_TEMPLATE
    async def run():
        d=store.create({**_payload("bybit"), "account":"live"}, now_ms=1000)
        args={"revision":d["revision"], "monitor_generation":e.generation}
        with pytest.raises(master_service.HTTPException): await master_service.trendline_plan_arm(d["plan_id"], args)
        assert store.get(d["plan_id"]) == d
        response=json.loads((await master_service.trendline_plan_arm(d["plan_id"], {**args,"confirm_live_execution":True})).body)
        assert response["monitoring_active"] and response["plan"]["monitoring_eligible"]
        with pytest.raises(master_service.HTTPException): await master_service.trendline_plan_arm(d["plan_id"], {**args,"confirm_live_execution":True})
        await master_service.trendline_monitor_stop()
        response=json.loads((await master_service.trendline_plan_list()).body)
        assert not response["plans"][0]["monitoring_eligible"]
        assert response["plans"][0]["monitor_generation"] != args["monitor_generation"]
        # The same confirmed payload cannot be rebound to a changed revision.
        fresh=store.create({**_payload("bybit"),"account":"live"},now_ms=1000)
        stale={"revision":fresh["revision"],"monitor_generation":e.generation,"confirm_live_execution":True}
        changed=store.update(fresh["plan_id"],{"rr_target":"3"},now_ms=2000)
        with pytest.raises(master_service.HTTPException): await master_service.trendline_plan_arm(fresh["plan_id"],stale)
        assert store.get(fresh["plan_id"]) == changed
        with monkeypatch.context() as patch:
            patch.setattr(master_service,"APP_PROFILE","render")
            with pytest.raises(master_service.HTTPException) as denied:
                await master_service.trendline_plan_arm(fresh["plan_id"],stale)
            assert denied.value.status_code == 410
        for test_flag in (True,False):
            selected=store.create({**_payload("bybit",test_trade=test_flag),"account":"live"},now_ms=1000)
            request={"revision":selected["revision"],"monitor_generation":e.generation}
            for confirmation in (None,False,"true",1):
                with pytest.raises(master_service.HTTPException):
                    await master_service.trendline_plan_arm(selected["plan_id"],{**request,"confirm_live_execution":confirmation})
            assert store.get(selected["plan_id"])==selected
            for wrong in ({**request,"revision":0},{**request,"monitor_generation":"stale"}):
                with pytest.raises(master_service.HTTPException):
                    await master_service.trendline_plan_arm(selected["plan_id"],{**wrong,"confirm_live_execution":True})
            accepted=json.loads((await master_service.trendline_plan_arm(selected["plan_id"],{**request,"confirm_live_execution":True})).body)["plan"]
            assert accepted["test_trade"] is test_flag and live_execution_authorized(accepted)
            assert master_service._trendline_calculator_payload(accepted)["test_trade"] is test_flag
            await e.stop()
        async def fail_worker(): raise RuntimeError("watcher failed")
        with monkeypatch.context() as patch:
            patch.setattr(e,"_run",fail_worker)
            with pytest.raises(master_service.HTTPException) as failed:
                await master_service.trendline_plan_arm(fresh["plan_id"],{**stale,"revision":changed["revision"]})
            assert failed.value.status_code == 409
        assert store.get(fresh["plan_id"]) == changed and not e.status()["running"]
    _run_fixture_loop(run())


def test_missed_touch_quote_rejection_is_safe_and_plan_specific(tmp_path, monkeypatch):
    from test_trendline_executor import _payload, _run_fixture_loop
    from render.trendline_executor import InvalidQuote, TrendlinePlanExecutor
    service=master_service;now=1794111207000
    monkeypatch.setattr(service.time,"time_ns",lambda:now*1000000)
    monkeypatch.setattr(service,"resolve_bybit_credentials_for",lambda a:("demo","private-key","private-secret","https://mock.invalid","mock"))
    quote={"symbol":"XRPUSDT","bid1Price":"1.15","ask1Price":"1.1501","lastPrice":"1.15"};stamp=[now]
    async def fixture_offset(*a,**kw):return 0
    monkeypatch.setattr(service,"_get_bybit_time_offset_ms",fixture_offset)
    monkeypatch.setattr(service,"_BYBIT_TIME_OFFSET_CACHE",{service._normalize_bybit_base_url("https://mock.invalid"):{"synced_at":now,"offset_ms":0,"rtt_ms":0}})
    async def ticker(base,category,symbol,**kw):
        assert kw["max_age_s"]==0 and kw["allow_stale_s"]==0
        return {"payload":{"time":stamp[0],"result":{"list":[quote]}}}
    async def meta(*args):return {"status":"Trading","priceFilter":{"tickSize":"0.0001"}}
    monkeypatch.setattr(service,"_fetch_bybit_ticker_cached",ticker);monkeypatch.setattr(service,"_bybit_get_instrument_info_cached",meta)
    store=service.TrendlinePlanStore(tmp_path/'quote.json');plan=store.arm(store.create({**_payload("bybit",test_trade=True),"instrument":"XRPUSDT"},now_ms=1000)["plan_id"],now_ms=2000)
    e=TrendlinePlanExecutor(store,service._trendline_executor_quote,lambda *a:{},lambda *a:{},lambda p:True,now_ms=lambda:now);e._eligible.add(plan["plan_id"])
    monkeypatch.setattr(service,"TRENDLINE_PLAN_STORE",store);monkeypatch.setattr(service,"TRENDLINE_EXECUTOR",e);monkeypatch.setattr(service,"APP_PROFILE","local")
    async def run():
        for value,reason in [(None,"quote_timestamp_invalid"),(str(now),"quote_timestamp_invalid"),(now+2,"quote_future"),(now-60001,"quote_stale")]:
            stamp[0]=value;await e.cycle();latest=e.latest_evaluations[plan["plan_id"]]
            assert latest["stage"]=="candidate_quote" and latest["reason"]==reason
            assert e.last_error=="QUOTE_INVALID: Fresh valid broker quote required."
            listed=json.loads((await service.trendline_plan_list()).body)["plans"][0]
            assert listed["latest_evaluation"]==latest
        stamp[0]=now;quote["ask1Price"]="1.15";await e.cycle()
        assert e.latest_evaluations[plan["plan_id"]]["reason"]=="quote_spread_invalid"
        # Unknown broker errors are sanitized, not exposed by status or cards.
        async def bad(p):raise RuntimeError("private-key https://private.invalid")
        e.quote=bad;await e.cycle();assert e.latest_evaluations[plan["plan_id"]]["reason"]=="executor_error"
        assert "private" not in json.dumps(e.status())
        quote["ask1Price"]="1.1501";fresh=await service._trendline_executor_quote(plan)
        assert fresh["clock"]["offset_ms"]==0
        assert {k:v for k,v in fresh.items() if k!="clock"}=={"instrument":"XRPUSDT","timestamp_ms":now,"bid":"1.15","ask":"1.1501","tick_size":"0.0001"}
    _run_fixture_loop(run())


def test_fresh_xrp_clock_refresh_is_base_scoped_and_fails_closed(monkeypatch):
    from test_trendline_executor import _run_fixture_loop
    from render.trendline_executor import InvalidQuote
    service=master_service;now=[1794128670000];demo='https://api-demo.bybit.com';live='https://api.bybit.com'
    monkeypatch.setattr(service.time,'time',lambda:now[0]/1000);monkeypatch.setattr(service.time,'time_ns',lambda:now[0]*1000000)
    monkeypatch.setattr(service,'_BYBIT_TIME_OFFSET_CACHE',{});monkeypatch.setattr(service,'_open_orders_authoritative',lambda:True)
    calls=[]
    async def server(base):
        calls.append(base);before=now[0];now[0]+=692;return before+346+797
    monkeypatch.setattr(service,'_fetch_bybit_server_time_ms',server)
    async def run():
        clock=await service._trendline_bybit_clock(demo)
        assert clock['offset_ms']==797 and clock['rtt_ms']==692 and clock['source']=='api-demo.bybit.com'
        assert calls==[demo]
        await service._trendline_bybit_clock(demo);assert calls==[demo]
        async def unavailable(*a):raise RuntimeError('synthetic outage')
        monkeypatch.setattr(service,'_fetch_bybit_server_time_ms',unavailable)
        with pytest.raises(InvalidQuote) as missing:await service._trendline_bybit_clock(live)
        assert missing.value.reason=='clock_sync_unavailable'
        now[0]+=300001
        with pytest.raises(InvalidQuote) as stale:await service._trendline_bybit_clock(demo)
        assert stale.value.reason=='clock_sync_stale'
        key=service._normalize_bybit_base_url(demo)
        service._BYBIT_TIME_OFFSET_CACHE[key]={'synced_at':now[0],'offset_ms':797,'rtt_ms':9999}
        with pytest.raises(InvalidQuote) as uncertain:await service._trendline_bybit_clock(demo)
        assert uncertain.value.reason=='clock_sync_uncertain'
        service._BYBIT_TIME_OFFSET_CACHE[key]={'synced_at':'invalid','offset_ms':797,'rtt_ms':10}
        with pytest.raises(InvalidQuote) as malformed:await service._trendline_bybit_clock(demo)
        assert malformed.value.reason=='clock_metadata_invalid'
    _run_fixture_loop(run())


def test_fresh_xrp_canonical_buy_sell_clock_and_simulated_outcome(tmp_path,monkeypatch):
    from test_trendline_executor import _payload,_run_fixture_loop
    from render.trendline_plans import projected_price,live_execution_authorized
    from render.trendline_executor import TrendlinePlanExecutor, DefiniteSubmissionFailure
    from decimal import Decimal as D
    service=master_service;now=[1794126200000];base='https://api-demo.bybit.com';current={};counts={};post=[]
    monkeypatch.setattr(service.time,'time',lambda:now[0]/1000);monkeypatch.setattr(service.time,'time_ns',lambda:now[0]*1000000)
    monkeypatch.setattr(service,'resolve_bybit_credentials_for',lambda a:('demo','fixture-key','fixture-secret',base,'fixture'))
    monkeypatch.setattr(service,'_BYBIT_TIME_OFFSET_CACHE',{});monkeypatch.setattr(service,'_BYBIT_TICKER_CACHE',{});monkeypatch.setattr(service,'_BYBIT_TICKER_INFLIGHT',{})
    meta={'symbol':'XRPUSDT','status':'Trading','priceFilter':{'tickSize':'0.0001'},'lotSizeFilter':{'qtyStep':'0.001','minOrderQty':'0.001','maxOrderQty':'1000000','maxMktOrderQty':'1000000','minNotionalValue':'1'},'leverageFilter':{'maxLeverage':'100'}}
    async def instrument(*a,**kw):return meta
    async def resolve(*a,**kw):return 'XRPUSDT',meta,{}
    async def wallet(*a,**kw):return {'available_usdt':D('1000'),'total_equity':D('1000')}
    async def ticker(*a,**kw):
        assert a[1]=='/v5/market/tickers'
        p=current['plan'];stamp=now[0]+797
        if current.get('final') and current['mode']=='reverse_final':stamp-=200
        line=projected_price(p,stamp);offset=current['offset']
        if current.get('final') and current['mode']=='away':offset=D('-0.001')
        executable=line+offset;tick=D('0.0001');bid=executable if p['action']=='sell' else executable-tick;ask=bid+tick
        counts[p['plan_id']]=counts.get(p['plan_id'],0)+1
        return {'retCode':0,'time':stamp,'result':{'list':[{'symbol':'XRPUSDT','bid1Price':str(bid),'ask1Price':str(ask),'lastPrice':str((bid+ask)/2)}]}}
    def forbidden(*a,**kw):post.append(True);raise AssertionError('No broker POST or real state')
    monkeypatch.setattr(service,'_bybit_get_async',ticker);monkeypatch.setattr(service,'_bybit_get_instrument_info_cached',instrument);monkeypatch.setattr(service,'_resolve_bybit_calculator_symbol_fast',resolve);monkeypatch.setattr(service,'_fetch_bybit_balance_usdt_cached',lambda *a,**kw:wallet_result())
    async def wallet_result():return await wallet(),{}
    for name in ('_upsert_calculator_trade_context','_upsert_bybit_demo_calc_context','_schedule_dropbox_upload_state_backup','_log_webhook_event','cache_bybit_demo_tpsl_request','_invalidate_open_orders_cache'):
        monkeypatch.setattr(service,name,lambda *a,**kw:{})
    async def no_time_request(*a,**kw):raise RuntimeError('fixture clock refresh unavailable')
    monkeypatch.setattr(service,'_fetch_bybit_server_time_ms',no_time_request)
    monkeypatch.setattr(service,'_bybit_signed_post',forbidden);monkeypatch.setattr(service,'_bybit_signed_get',forbidden)
    async def actual_submission(payload, *, request_id, invalidate_cache, _request_state):
        p=current['plan'];assert payload['account']==p['account']=='demo'
        assert payload['test_trade'] is p['test_trade'] and request_id==p['execution_key']
        assert payload['calculation_context_id'] and payload['quantity'] and payload['stop_loss_price']
        post.append((p['plan_id'],p['test_trade'],request_id))
        if current['mode']=='rejected':raise DefiniteSubmissionFailure('mock rejection')
        if current['mode']=='uncertain':_request_state['posted']=True;raise RuntimeError('mock lost response')
        return {'order':{'orderId':'demo-'+request_id}}
    monkeypatch.setattr(service,'_place_bybit_order',actual_submission)

    monkeypatch.setattr(service,'_OANDA_AUD_USD_CACHE',{'value':0.66,'ts':now[0]/1000})
    anchors={'sell':[{'timestamp_ms':1794125340000,'price':'1.1644010536093532'},{'timestamp_ms':1794125700000,'price':'1.1639763460829737'}],'buy':[{'timestamp_ms':1794125640000,'price':'1.1617029116770599'},{'timestamp_ms':1794125820000,'price':'1.1618028428597373'}]}
    async def scenario(mode, test):
        before_posts=len(post)
        store=service.TrendlinePlanStore(tmp_path/(mode+str(test))/'plans.json');plans=[];seen={};claims=[]
        for side in ('sell','buy'):
            p=store.create({**_payload('bybit',test_trade=test),'instrument':'XRPUSDT','action':side,'anchors':anchors[side],'stop_loss_ticks':10,'rr_target':'1','risk_mode':'percent','risk_value':'1'},now_ms=1794125930000)
            plans.append(store.arm(p['plan_id'],now_ms=1794125931000))
        async def candidate(p):
            now[0]+=100;seen[p['plan_id']]=seen.get(p['plan_id'],0)+1
            current.update(plan=p,mode=mode,final=False,offset=D('-0.001') if mode=='gap' and seen[p['plan_id']]==1 else D('0.001') if mode=='gap' else D('0'))
            q=await service._trendline_executor_quote(p)
            assert q['timestamp_ms']==now[0]+797 and q['clock']['offset_ms']==797
            return q
        async def calculate(p,q):
            now[0]+=2;current['final']=True
            service._OANDA_AUD_USD_CACHE.update(value=0.66,ts=now[0]/1000)
            calc=await service._trendline_fresh_calculation(p,q)
            assert calc['trigger_quote']['timestamp_ms']==now[0]+797-(200 if mode=='reverse_final' else 0)
            assert D(calc['entry_price'])==service._snap_to_increment(D(calc['trigger_quote']['ask' if p['action']=='buy' else 'bid']),D('0.0001'))
            if mode=='clock_expired':now[0]+=300001
            return calc
        e=TrendlinePlanExecutor(store,candidate,calculate,service._trendline_executor_submit,live_execution_authorized,now_ms=lambda:now[0]);e._eligible.update(p['plan_id'] for p in plans)
        original=store.claim_trigger
        def claiming(pid,trigger,**kw):
            claims.append(pid);assert kw['now_ms']>=trigger['trigger_timestamp_ms'];return original(pid,trigger,**kw)
        monkeypatch.setattr(store,'claim_trigger',claiming)
        if mode=='claim_stale':
            get=store.get
            def delayed(pid):
                p=get(pid)
                if current.get('final'):now[0]+=60001
                return p
            monkeypatch.setattr(store,'get',delayed)
        key=service._normalize_bybit_base_url(base)
        service._BYBIT_TIME_OFFSET_CACHE[key]={'synced_at':now[0],'offset_ms':797,'rtt_ms':692}
        # Keep this independent clock fresh at the scenario boundary only.
        await e.cycle()
        if mode=='gap':await e.cycle()
        for p in plans:
            final=original_get(store,p['plan_id'])
            if mode in ('inside','gap'):
                assert final['status']=='submitted' and final['execution_result']['outcome']=='executed'
                assert final['execution_result']['order_id']=='demo-'+p['execution_key']
                assert final['test_trade'] is test
                assert claims.count(p['plan_id'])==1
                assert original(p['plan_id'],{},now_ms=now[0])['duplicate'] is True
                assert final['anchors']==anchors[p['action']]
                assert final['trigger_claim']['trigger_kind']==('entered_band' if mode=='inside' else 'gap_through')
            elif mode in ('rejected','uncertain'):
                assert final['status']==('failed' if mode=='rejected' else 'uncertain') and final['trigger_claim'] is not None
            else:
                assert final['status']=='armed' and final['trigger_claim'] is None
        before=dict(counts)
        if mode in ('inside','gap','rejected','uncertain'):await e.cycle();assert counts==before
        assert len(post)-before_posts==(2 if mode in ('inside','gap','rejected','uncertain') else 0)
    original_get=service.TrendlinePlanStore.get
    async def run():
        for test in (True,False):
            for mode in ('inside','gap','away','clock_expired','claim_stale','reverse_final','rejected','uncertain'):await scenario(mode,test)
    _run_fixture_loop(run())



def test_armed_risk_update_transaction_route_and_projection(tmp_path, monkeypatch):
    from render.trendline_executor import TrendlinePlanExecutor
    from render.trendline_plans import TrendlinePlanError, TrendlinePlanPersistenceError, live_execution_authorized
    from test_trendline_executor import _payload, _run_fixture_loop
    service=master_service;store=service.TrendlinePlanStore(tmp_path/'risk.json');clock=[2500]
    calls=[]
    async def forbidden(*args):calls.append(args);raise AssertionError('No quote/broker IO for editing')
    e=TrendlinePlanExecutor(store,forbidden,forbidden,forbidden,live_execution_authorized,now_ms=lambda:clock[0])
    async def idle():await e._stop.wait()
    monkeypatch.setattr(e,'_run',idle)
    monkeypatch.setattr(service,'APP_PROFILE','local');monkeypatch.setattr(service,'TRENDLINE_PLAN_STORE',store);monkeypatch.setattr(service,'TRENDLINE_EXECUTOR',e)
    async def patch(plan,changes,**controls):
        payload={'revision':plan['revision'],'monitor_generation':e.generation,**changes,**controls}
        return json.loads((await service.trendline_plan_update_risk(plan['plan_id'],payload)).body)['plan']
    async def scenario():
        try:
            draft=store.create(_payload('bybit',test_trade=True),display_snapshot={'quantity':'2','entry_price':'100','stop_price':'99','target_price':'102'},now_ms=1000)
            with pytest.raises(service.HTTPException):await patch(draft,{'rr_target':'3'})
            plan=await e.arm(draft['plan_id'],revision=draft['revision'],generation=e.generation)
            worker=e._task;key=plan['execution_key'];before_row=service._trendline_dashboard_rows([],plans=[plan],executor=e,contexts=[])[0]
            with pytest.raises(TrendlinePlanError):store.update(plan['plan_id'],{'rr_target':'4'},now_ms=2500)
            for changes in ({},{'anchors':[]},{'risk_value':'90'},{'risk_update':{}},{'stop_loss_ticks':True},{'stop_loss_ticks':1.5},{'stop_loss_ticks':'1.0'},{'stop_loss_ticks':0},{'rr_target':'NaN'},{'rr_target':'Infinity'},{'rr_target':True},{'rr_target':'0'}):
                with pytest.raises(service.HTTPException):await patch(plan,changes)
            for controls in ({'revision':0},{'revision':True},{'monitor_generation':'old'}):
                with pytest.raises(service.HTTPException):await patch(plan,{'rr_target':'3'},**controls)
            assert store.get(plan['plan_id'])==plan
            original_bytes=store.path.read_bytes()
            with monkeypatch.context() as scope:
                scope.setattr(store,'_write',lambda _:(_ for _ in ()).throw(TrendlinePlanPersistenceError('fixture write failure')))
                with pytest.raises(service.HTTPException):await patch(plan,{'stop_loss_ticks':'20','rr_target':'3'})
            assert store.path.read_bytes()==original_bytes
            other=await e.arm((other:=store.create(_payload('bybit'),now_ms=1000))['plan_id'],revision=other['revision'],generation=e.generation)
            e._previous[other['plan_id']]={'timestamp_ms':2000,'bid':'98','ask':'99'};observation=dict(e._previous[other['plan_id']])
            changed=await patch(plan,{'stop_loss_ticks':'20','rr_target':'3.50'})
            allowed={'revision','updated_at_ms','lifecycle','display_snapshot','stop_loss_ticks','rr_target','risk_update'}
            assert all(changed[k]==v for k,v in plan.items() if k not in allowed)
            assert changed['rr_target']=='3.5' and changed['stop_loss_ticks']==20 and changed['revision']==plan['revision']+1
            assert changed['risk_update']['from_revision']==plan['revision'] and changed['execution_key']==key
            assert e._task is worker and e.eligible(plan['plan_id']) and e._previous[other['plan_id']]==observation
            row=service._trendline_dashboard_rows([],plans=[store.get(plan['plan_id'])],executor=e,contexts=[])[0]
            assert row['display_id']==before_row['display_id'] and row['stop_loss_ticks']==20 and row['risk_reward']=='3.5'
            assert row['size'] is None and row['planned_stop_price'] is None and row['planned_target_price'] is None
            native={'broker':'Bybit','account':'demo','category':'linear','instrument':'AKEUSDT','type':'Position','id':'real-ake','size':'7','status':'Open'}
            rows=service._trendline_dashboard_rows([native],plans=[store.get(plan['plan_id'])],executor=e,contexts=[])
            assert len(rows)==2 and rows[0] is native
            assert service._trendline_calculator_payload(changed)['stop_loss_ticks']==20 and service._trendline_calculator_payload(changed)['risk_reward']=='3.5'
            stop_only=await patch(changed,{'stop_loss_ticks':21})
            assert stop_only['rr_target']=='3.5'
            for flag in (True,False):
                live=store.create({**_payload('bybit',test_trade=flag),'account':'live'},now_ms=1000)
                live=await e.arm(live['plan_id'],revision=live['revision'],generation=e.generation,confirm_live=True)
                for invalid in (None,False,'true',1):
                    with pytest.raises(service.HTTPException):await patch(live,{'rr_target':'4'},confirm_live_execution=invalid)
                assert store.get(live['plan_id'])['live_authorization']==live['live_authorization']
                revised=await patch(live,{'rr_target':'4'},confirm_live_execution=True)
                assert revised['live_authorization']['plan_revision']==revised['revision'] and revised['live_authorization']['execution_key']==live['execution_key']
                assert live_execution_authorized(revised) and revised['test_trade'] is flag
            oanda=store.create({**_payload('oanda',test_trade=True),'account':'live'},now_ms=1000)
            oanda=await e.arm(oanda['plan_id'],revision=oanda['revision'],generation=e.generation)
            assert (await patch(oanda,{'rr_target':'5'}))['live_authorization'] is None
            expiring=store.create({**_payload('bybit'),'expiry_at_ms':3000},now_ms=1000)
            expiring=await e.arm(expiring['plan_id'],revision=expiring['revision'],generation=e.generation);clock[0]=3500
            with pytest.raises(service.HTTPException):await patch(expiring,{'rr_target':'3'})
            clock[0]=2500
            claim=store.claim_trigger(stop_only['plan_id'],{'trigger_timestamp_ms':2500,'trigger_price':'100','trigger_kind':'touch'},now_ms=2500)
            with pytest.raises(service.HTTPException):await patch(claim['plan'],{'rr_target':'9'})
            await e.stop()
            with pytest.raises(service.HTTPException):await patch(other,{'rr_target':'9'})
            with monkeypatch.context() as scope:
                scope.setattr(service,'APP_PROFILE','render')
                with pytest.raises(service.HTTPException) as denied:await patch(other,{'rr_target':'3'})
                assert denied.value.status_code==410
            assert not calls
        finally:await e.stop()
    _run_fixture_loop(scenario())



def test_trendline_price_r_canonical_bracket_and_submission(tmp_path, monkeypatch):
    from test_trendline_executor import _payload, _run_fixture_loop
    from decimal import Decimal as D
    service=master_service
    state=_mock_pending_limit_replay(monkeypatch,symbol='XRPUSDT',tick='0.0001',last='1.1006')
    original_resolve=service._resolve_bybit_calculator_symbol_fast
    async def valid_instrument(*a,**k):
        symbol,meta,debug=await original_resolve(*a,**k);return symbol,{**meta,'status':'Trading'},debug
    monkeypatch.setattr(service,'_resolve_bybit_calculator_symbol_fast',valid_instrument)
    now=1794268800000
    monkeypatch.setattr(service.time,'time',lambda:now/1000)
    monkeypatch.setattr(service.time,'time_ns',lambda:now*1000000)
    monkeypatch.setattr(service,'_OANDA_AUD_USD_CACHE',{'value':1,'ts':now/1000})
    async def ticker(*a,**k):
        return {'retCode':0,'time':now,'result':{'list':[{'symbol':'XRPUSDT','bid1Price':'1.1006','ask1Price':'1.1007','lastPrice':state['last']}]}}
    monkeypatch.setattr(service,'_bybit_get_async',ticker)
    async def cached(*a,**k):return {'payload':await ticker()}
    monkeypatch.setattr(service,'_fetch_bybit_ticker_cached',cached)
    async def clock(*a):return {'offset_ms':0,'uncertainty_ms':1,'synced_at_ms':now,'rtt_ms':2,'base_url':'https://bybit.invalid'}
    # Existing clock cache contract, same exact adapter/final quote, no network.
    monkeypatch.setattr(service,'_BYBIT_TIME_OFFSET_CACHE',{service._normalize_bybit_base_url('https://bybit.invalid'):{'synced_at':now,'offset_ms':0,'rtt_ms':2}})
    # Canonical post-create protection readback is not a new order/amendment.
    # Simulate the already-attached bracket; any attempt to move it is forbidden.
    def position():
        body=state['posts'][-1]
        return {'symbol':'XRPUSDT','size':body['qty'],'side':body['side'],'positionIdx':0,
                'avgPrice':'1.1009','takeProfit':body['takeProfit'],'stopLoss':body['stopLoss']}
    async def opened(**kw):return position()
    async def positions(**kw):return [position()]
    async def no_amend(**kw):raise AssertionError('Price R must retain create-time protection')
    monkeypatch.setattr(service,'_wait_for_position_entry',opened)
    monkeypatch.setattr(service,'_fetch_bybit_positions',positions)
    monkeypatch.setattr(service,'_set_bybit_trading_stop',no_amend)
    store=service.TrendlinePlanStore(tmp_path/'plans.json')
    async def run():
        for side,order,entry in [('sell','market',None),('buy','market',None),('sell','limit','1.11'),('buy','limit','1.09')]:
            p=store.create({**_payload('bybit',test_trade=True),'instrument':'XRPUSDT','action':side,'order_intent':order,'limit_entry_price':entry,'risk_mode':'percent','risk_value':'1','stop_loss_ticks':10,'rr_target':'2','target_basis':'price_r'},now_ms=now-100)
            p=store.arm(p['plan_id'],now_ms=now-90)
            p=store.update_armed_risk(p['plan_id'],{'stop_loss_ticks':25,'rr_target':'3'},expected_revision=p['revision'],confirm_live=False,now_ms=now-80)
            q=await service._trendline_fresh_calculation(p)
            assert q['target_basis']=='price_r' and q['requested_price_r']=='3'
            ref=D(entry or ('1.1006' if side=='sell' else '1.1007'));direction=D(1 if side=='buy' else -1)
            assert D(q['entry_price'])==ref
            assert D(q['stop_price'])==ref-direction*D('.0025')
            assert D(q['target_price'])==ref+direction*D('.0075')
            assert D(q['rr'])==3 and q['requested_rr_net'] is None
            context=state['contexts'][-1];assert context['target_basis']=='price_r' and D(context['take_profit_ticks'])==75 and D(context['risk_reward'])==3
            legacy={**p,'target_basis':'net_r'}
            ordinary=json.loads((await service._calculator_quote_impl(service._trendline_calculator_payload(legacy))).body)
            assert D(ordinary['quantity'])==D(q['quantity']) and ordinary['target_price']!=q['target_price']
            if side=='sell' and order=='market':assert ordinary['target_price']=='1.0874'
            claim=store.claim_trigger(p['plan_id'],{'trigger_timestamp_ms':now,'trigger_price':str(ref),'trigger_kind':'entered_band'},now_ms=now,expected_revision=p['revision'],expected_execution_key=p['execution_key'])
            claimed=store.begin_submission(p['plan_id'],execution_key=p['execution_key'],now_ms=now)
            result=await service._trendline_executor_submit(claimed,q,p['execution_key']);assert result['accepted']
            body=state['posts'][-1];assert D(body['stopLoss'])==D(q['stop_price']) and D(body['takeProfit'])==D(q['target_price'])
            assert body['orderLinkId']==p['execution_key'] and body['side']==side.title()
            # LastPrice moving through TP rejects instead of extending Price R.
            if order=='market':
                state['last']=q['target_price'];before=len(state['posts'])
                with pytest.raises(service.DefiniteSubmissionFailure):await service._trendline_executor_submit(claimed,q,p['execution_key'])
                assert len(state['posts'])==before;state['last']='1.1006'
        legacy=store.create({**_payload('bybit'),'instrument':'XRPUSDT'},now_ms=now);assert legacy['target_basis']=='net_r'
        assert 'target_basis' in service._trendline_calculator_payload(p)
    _run_fixture_loop(run())



def test_placeholder_metadata_canonical_submit_and_native_override(tmp_path,monkeypatch):
    from test_trendline_executor import _payload,_run_fixture_loop
    from render.trendline_executor import TrendlinePlanExecutor
    from render.trendline_plans import live_execution_authorized
    from types import SimpleNamespace
    import copy
    import tools.master_journal_workbook as workbook
    service=master_service;upsert=service._upsert_calculator_trade_context;intent=service._accepted_calculator_intent;json_save=service._save_json_file
    state=_mock_pending_limit_replay(monkeypatch,symbol='XRPUSDT',tick='0.0001',last='1.1006')
    monkeypatch.setattr(service,'_upsert_calculator_trade_context',upsert);monkeypatch.setattr(service,'_accepted_calculator_intent',intent)
    monkeypatch.setattr(service,'TRADE_CONTEXTS_PATH',tmp_path/'accepted.json');monkeypatch.setattr(service,'_TRADE_CONTEXTS_CACHE',{})
    def isolated_save(path,payload,*a,**kw):
        assert Path(path).resolve()==(tmp_path/'accepted.json').resolve(),'No runtime/journal writes'
        return json_save(path,payload,*a,**kw)
    monkeypatch.setattr(service,'_save_json_file',isolated_save)
    monkeypatch.setattr(service,'_calc_context_uses_repo_local',lambda:False);monkeypatch.setattr(service,'_load_bybit_demo_calc_contexts',lambda:{'items':copy.deepcopy(state['contexts'])})
    monkeypatch.setattr(service,'_schedule_early_trade_folders',lambda:None);monkeypatch.setattr(workbook,'_trade_allocation_state_path',lambda:tmp_path/'allocations.json')
    monkeypatch.setattr(service,'APP_PROFILE','local');monkeypatch.setattr(service,'_OPEN_ORDERS_SERVICES',{});monkeypatch.setattr(service,'_OPEN_ORDERS_CACHE',{'version':7,'payload':None})
    now=10000;monkeypatch.setattr(service.time,'time',lambda:now/1000);monkeypatch.setattr(service.time,'time_ns',lambda:now*1000000)
    monkeypatch.setattr(service,'_OANDA_AUD_USD_CACHE',{'value':1,'ts':10})
    original=service._resolve_bybit_calculator_symbol_fast
    async def resolve(*a,**k):
        symbol,meta,debug=await original(*a,**k);return symbol,{**meta,'status':'Trading'},debug
    monkeypatch.setattr(service,'_resolve_bybit_calculator_symbol_fast',resolve)
    async def ticker(*a,**k):return {'retCode':0,'time':now,'result':{'list':[{'symbol':'XRPUSDT','bid1Price':'1.1006','ask1Price':'1.1007','lastPrice':state['last']}]}}
    async def cached(*a,**k):return {'payload':await ticker()}
    monkeypatch.setattr(service,'_bybit_get_async',ticker);monkeypatch.setattr(service,'_fetch_bybit_ticker_cached',cached)
    monkeypatch.setattr(service,'_BYBIT_TIME_OFFSET_CACHE',{service._normalize_bybit_base_url('https://bybit.invalid'):{'synced_at':now,'offset_ms':0,'rtt_ms':2}})
    async def post(**kw):
        assert kw['path']=='/v5/order/create';state['posts'].append(copy.deepcopy(kw['body']))
        return {'retCode':0,'result':{'orderId':'accepted-'+str(len(state['posts'])),'orderLinkId':kw['body']['orderLinkId']}}
    monkeypatch.setattr(service,'_bybit_signed_post',post)
    def position():
        b=state['posts'][-1];return {'symbol':'XRPUSDT','size':b['qty'],'side':b['side'],'positionIdx':0,'avgPrice':'1.1009','takeProfit':b['takeProfit'],'stopLoss':b['stopLoss']}
    async def opened(**kw):return position()
    async def positions(**kw):return [position()]
    async def forbidden(**kw):raise AssertionError('No protective amendment')
    monkeypatch.setattr(service,'_wait_for_position_entry',opened);monkeypatch.setattr(service,'_fetch_bybit_positions',positions);monkeypatch.setattr(service,'_set_bybit_trading_stop',forbidden)
    captured=[]
    def recording(payload,**kw):
        if kw.get('_initial_classifications'):captured.append((copy.deepcopy(payload),copy.deepcopy(kw)))
        return upsert(payload,**kw)
    monkeypatch.setattr(service,'_upsert_calculator_trade_context',recording)
    store=service.TrendlinePlanStore(tmp_path/'plans.json');calls=[]
    async def quote(p):return {'instrument':'XRPUSDT','timestamp_ms':now,'bid':'1.1006','ask':'1.1007','tick_size':'.0001','clock':await service._trendline_bybit_clock('https://bybit.invalid')}
    async def calculation(p,q):
        calls.append(p['revision'])
        if len(calls)==1:
            store.update_classifications(p['plan_id'],{'setup':'Pullback'},version=1,expected_revision=p['revision'],execution_key=p['execution_key'],broker='bybit',account='demo',now_ms=now)
            await e.update_risk(p['plan_id'],{'stop_loss_ticks':25,'rr_target':'3'},revision=p['revision'],generation=e.generation)
        try:
            return await service._trendline_fresh_calculation(p,q)
        except Exception as exc:
            print("FIXTURE_CALCULATION_FAILURE", repr(exc), getattr(exc, "detail", None), flush=True)
            raise
    e=TrendlinePlanExecutor(store,quote,calculation,service._trendline_executor_submit,live_execution_authorized,now_ms=lambda:now)
    async def idle():await e._stop.wait()
    monkeypatch.setattr(e,'_run',idle)
    async def run():
        p=store.create({**_payload('bybit',test_trade=True),'instrument':'XRPUSDT','action':'sell','risk_mode':'percent','risk_value':'1','target_basis':'price_r','anchors':[{'timestamp_ms':1000,'price':'1.1006'},{'timestamp_ms':2000,'price':'1.1006'}],'timeframe':'1m','order_metadata':{'pattern':'channel'}},now_ms=1000)
        p=await e.arm(p['plan_id'],revision=p['revision'],generation=e.generation)
        store.update_classifications(p['plan_id'],{'timeframe':'5m','pattern':None,'ema':'9'},version=0,expected_revision=p['revision'],execution_key=p['execution_key'],broker='bybit',account='demo',now_ms=now)
        try:
            await e.cycle();final=store.get(p['plan_id']);assert final['status']=='submitted' and len(state['posts'])==1 and len(calls)==2,(final['status'],len(calls),e.latest_evaluations)
            assert final['classification_version']==2 and final['stop_loss_ticks']==25 and final['timeframe']=='1m' and final['test_trade'] is True
            body=state['posts'][0];assert body['stopLoss']=='1.1031' and body['takeProfit']=='1.0931' and body['orderLinkId']==p['execution_key']
            ctx=next(c for c in service._load_trade_contexts() if c.get('order_id')=='accepted-1')
            assert ctx['classification_overrides']=={'timeframe':'5m','pattern':None,'ema':'9','setup':'Pullback'}
            assert ctx['timeframe'] in ('1m','1MIN') and ctx['is_test_trade'] is True and ctx['classification_inheritance']['metadata_version']==2
            native=dict(broker='Bybit',account='demo',category='linear',instrument='XRPUSDT',side='Sell',type='Order',id='accepted-1',order_link_id=p['execution_key'],position_idx=0,status='New',size=body['qty'],entry_price='1.1009')
            monitor=SimpleNamespace(status=lambda:{'running':False},eligible=lambda _:False)
            def rows(native):return service._trendline_dashboard_rows([native],plans=[store.get(p['plan_id'])],executor=monitor,contexts=service._load_trade_contexts())
            orderrow=rows(native)[0];assert orderrow['classification_values']['ema']=='9' and orderrow['classification_values']['pattern'] is None and orderrow['timeframe']=='5MIN'
            positionrow=rows(dict(native,type='Position',id=0,raw_refs={'order_ids_complete':True,'order_ids':['accepted-1'],'lifecycle_started_at_ms':9000}))[0]
            assert positionrow['display_id']==orderrow['display_id'] and positionrow['entry_price']=='1.1009' and positionrow['classification_values']['ema']=='9'
            service._OPEN_ORDERS_CACHE['payload']={'items':[positionrow],'progress_revision':1}
            result=await service.save_open_order_classification({'target':positionrow['classification_target'],'version':ctx['classification_version'],'changes':{'ema':''}})
            assert result['values']['ema']==''
            payload,kw=captured[0];upsert(payload,**kw)
            service._TRADE_CONTEXTS_CACHE.clear();latest=next(c for c in service._load_trade_contexts() if c.get('order_id')=='accepted-1')
            assert latest['classification_overrides']['ema']=='' and latest['classification_version']==result['version']
            for change in ({'account':'live'},{'order_id':'unrelated'},{'order_link_id':'unrelated'},{'position_idx':1},{'account_id':'unrelated'}):
                with pytest.raises(ValueError):upsert({**payload,**change},**kw)
            await e.cycle();assert len(state['posts'])==1
            # A metadata persistence error after acknowledgement must not replay the broker order.
            p2=store.create({**_payload('bybit',test_trade=True),'instrument':'XRPUSDT','action':'sell','risk_mode':'percent','risk_value':'1','target_basis':'price_r','anchors':p['anchors']},now_ms=1000)
            p2=await e.arm(p2['plan_id'],revision=p2['revision'],generation=e.generation)
            store.update_classifications(p2['plan_id'],{'ema':'20'},version=0,expected_revision=p2['revision'],execution_key=p2['execution_key'],broker='bybit',account='demo',now_ms=now)
            save_contexts=service._save_trade_contexts
            def fail_inheritance(items):
                if any(c.get('order_id')=='accepted-2' and c.get('classification_inheritance') for c in items):raise OSError('fixture metadata write failure')
                return save_contexts(items)
            with monkeypatch.context() as patch:
                patch.setattr(service,'_save_trade_contexts',fail_inheritance)
                await e.cycle();assert store.get(p2['plan_id'])['status']=='submitted'
                await e.cycle();assert len(state['posts'])==2
            payload2,kw2=next((a,b) for a,b in reversed(captured) if a.get('order_id')=='accepted-2')
            upsert(payload2,**kw2);recovered=next(c for c in service._load_trade_contexts() if c.get('order_id')=='accepted-2')
            assert recovered['classification_overrides']['ema']=='20' and len(state['posts'])==2
        finally:await e.stop()
    _run_fixture_loop(run())


def test_pepperstone_standard_limit_controls_preserve_ticket_prices(monkeypatch, tmp_path, pepperstone_account_fixture):
    """Executed extracted MQL control flow in a C++ host; MT5 APIs/transport are doubles."""
    import re
    import subprocess
    import os
    from datetime import datetime, timezone
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(master_service, '_upsert_calculator_trade_context', pepperstone_account_fixture)
    stamp=datetime.now(timezone.utc).isoformat()
    export=tmp_path/'ticket-export.json'
    export.write_text(json.dumps(dict(version=1,broker='pepperstone',generated_at=stamp,
        account=dict(mode='demo',currency='AUD'),symbols=[dict(symbol='USDJPY',mt5_symbol='USDJPY.a',point='.001',
        trade_tick_size='.001',stops_level=0,freeze_level=0,bid='159.500',ask='159.501',available=True,
        timestamp=stamp,quote_timestamp=stamp,minimum_lot='.01',volume_step='.01',maximum_lot='100',
        tick_value='.937962962962962962962962963')])) )
    monkeypatch.setenv('PEPPERSTONE_SPREAD_EXPORT_PATH',str(export))
    request=_pepperstone_quote_payload(symbol='USDJPY',side='buy',account='demo',pepperstone_account_type='standard',
        order_type='limit',entry_price='159.360',stop_loss_ticks='60',chart_tick_size='.001',target_mode='rr',risk_reward='2.5',risk_mode='fixed_aud',risk_value='10')
    quote=json.loads(asyncio.run(master_service.calculator_quote(request)).body)
    assert Decimal(quote['quantity'])==18000 and Decimal(quote['estimated_total_loss_aud'])==Decimal('10.13')
    assert Decimal(quote['estimated_reward_aud'])==Decimal('25.33')
    assert (Decimal(quote['entry_price']),Decimal(quote['stop_price']),Decimal(quote['target_price']))==tuple(map(Decimal,('159.360','159.300','159.510')))
    preset_text,_=master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request,quote));preset=_parse_set_file(preset_text)
    assert Decimal(preset['TP_PriceDistance'])==Decimal('.150') and preset['AutoTP_SafetyPoints']=='0'
    assert preset['PresetAccountType']=='standard'
    with pytest.raises(master_service.HTTPException) as forged:
        master_service._build_pepperstone_trader_set(dict(_pepperstone_set_payload_from_quote(request,quote),PresetAccountType='razor'))
    assert forged.value.status_code==400
    assert preset['AutoTP_NetRR_Enabled']=='true' and Decimal(preset['NetRR_Target'])==Decimal('2.5')
    (tmp_path/'ticket.set').write_text(preset_text)
    _run_trader_controls_host(tmp_path, preset)


def test_pepperstone_nzdusd_limit_placement(monkeypatch, tmp_path, pepperstone_account_fixture):
    """Real quote/export; host runs both EA revisions against identical synthetic broker data."""
    from datetime import datetime, timezone
    _mock_pepperstone_quote_dependencies(monkeypatch)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", pepperstone_account_fixture)
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "3.50")
    stamp = datetime.now(timezone.utc).isoformat()
    symbol = dict(symbol="NZDUSD", mt5_symbol="NZDUSD.a", point=".00001", trade_tick_size=".00001",
                  minimum_lot=".01", volume_step=".01", maximum_lot="100", tick_value="1.51",
                  bid=".55435", ask=".55447", stops_level=10, freeze_level=30,
                  available=True, timestamp=stamp, quote_timestamp=stamp)
    export = tmp_path / "spread.json"
    export.write_text(json.dumps(dict(version=1, broker="pepperstone", generated_at=stamp,
                                     account=dict(mode="demo", currency="AUD"), symbols=[symbol])))
    monkeypatch.setenv("PEPPERSTONE_SPREAD_EXPORT_PATH", str(export))
    presets = {}
    for profile in ("standard", "razor"):
        request = _pepperstone_quote_payload(symbol="NZDUSD", side="buy", account="demo", order_type="limit",
            pepperstone_account_type=profile, estimate_razor_from_standard=profile=="razor", entry_price="0.55387",
            stop_loss_ticks="16", chart_tick_size="0.00001", target_mode="rr", risk_reward="3.5",
            risk_mode="fixed_aud", risk_value="10")
        quote = json.loads(asyncio.run(master_service.calculator_quote(request)).body)
        guidance = quote["pepperstone_stop_guidance"]
        assert (guidance["state"], guidance["limit_entry_state"], guidance["minimum_ticks"]) == ("ready", "valid", 10)
        assert Decimal(quote["stop_price"]) == Decimal(".55371")
        text, _ = master_service._build_pepperstone_trader_set(_pepperstone_set_payload_from_quote(request, quote))
        presets[profile] = _parse_set_file(text)
        assert presets[profile]["PresetAccountType"] == profile
        assert Decimal(presets[profile]["SL_PriceDistance"]) == Decimal(".00016")
        assert Decimal(presets[profile]["CommissionPerLotPerSide"]) == (0 if profile == "standard" else Decimal("3.5"))
        if profile == "standard":
            assert Decimal(quote["target_price"]) == Decimal(".55443")
    for baseline in (True, False):
        work = tmp_path / ("before" if baseline else "after")
        work.mkdir()
        _run_trader_controls_host(work, presets["standard"], limit_cases=presets, baseline=baseline)


def _run_trader_controls_host(tmp_path, preset, market_cases=(), *, limit_cases=None, baseline=False, strategy_cases=None):
    import os
    import subprocess
    source=(Path(__file__).resolve().parents[1]/'mt5-clone/MQL5/Experts/Trader.mq5').read_text(encoding='utf-8')
    if baseline:
        source = subprocess.check_output([r'C:\Program Files\Git\cmd\git.exe','show',
            '6665477e83cee6915d63550bcdf7bffe2bf693de:mt5-clone/MQL5/Experts/Trader.mq5'], cwd=ROOT).decode("utf-8-sig")
    def extract(name, text=source):
        match=re.search(r'\b(?:bool|void|double|string|int)\s+'+re.escape(name)+r'\s*\([^;{]*\)\s*\{',text)
        assert match,name
        # Mask strings/comments for brace accounting, preserving source positions.
        masked=re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',lambda m:' '*len(m.group()),text)
        level=1;i=match.end()
        while level:
            level+=(masked[i]=='{')-(masked[i]=='}');i+=1
        return text[match.start():i]
    names=['NormalizePrice','NormalizeVolume','CalcRiskFor1Lot','CompactDistanceSymbol','DistanceSymbolMatchesChart',
        'DistanceUnitsAreWhole','ValidatePriceDistance','PriceLevelIsOnTradeGrid','BrokerMinimumDistancePoints','BrokerDistanceAtLeast',
        'BuildSLFromDistance','BuildTPManualFromDistance','IsPortableFXDistanceSymbol','ResolveStandardFXPreset','AutoTPDecimalParts','AutoTPAUDCents','ComputeAutoTP_NetRR',
        'IsLimitPriceValid','CalcBufferedRiskForVolume','ComputeVolumeFromRisk','ValidateVolumeForBroker',
        'PlaceOrReplacePendingLimitAtEntry','ConsumeDesktopTraderCommand','SetDesktopResult','ExecuteDesktopPendingAtEntry',
        'ExecuteDesktopLimit','ValidateMarketStopsAtLiveQuote','ExecuteDesktopMarket','HandleDesktopTraderCommand']
    if strategy_cases:
        names.extend(["TrendlineExists", "GetTrendlinePriceAtTime", "IsValidDesktopTrendline",
                      "ResolveDesktopTrendline", "SaveDesktopActiveTrendline", "ExecuteDesktopTrendline",
                      "PlacePendingTrendlineGeneration", "SelectEmaBounceReference", "ValidateEmaBouncePeriods",
                      "InitializeEmaBounce", "PricesMateriallyDiffer", "ExecuteDesktopEmaBounce"])
    functions=[extract(name) for name in names]
    historical=subprocess.check_output([r'C:\Program Files\Git\cmd\git.exe','show','5ffe30f0673d651bddc7164172af534afc4cf075:mt5-clone/MQL5/Experts/Trader.mq5'],cwd=Path(__file__).resolve().parents[1]).decode('utf-8-sig')
    legacy=extract('ComputeAutoTP_NetRR',historical).replace('bool ComputeAutoTP_NetRR(', 'bool LegacyComputeAutoTP_NetRR(',1)
    functions=[f.replace('bool ComputeAutoTP_NetRR(', 'bool CurrentComputeAutoTP_NetRR(',1) for f in functions]+[legacy]
    if market_cases:
        delivered=subprocess.check_output([r'C:\Program Files\Git\cmd\git.exe','show',
            'ae4679275788bf416aa2230ddbb28b5dddfd9908:mt5-clone/MQL5/Experts/Trader.mq5'],cwd=ROOT).decode('utf-8-sig')
        functions.append(extract('ComputeAutoTP_NetRR',delivered).replace('bool ComputeAutoTP_NetRR(', 'bool Replay254AutoTP(',1))
    def adapt(text):
        for old,new in [('ulong','uint64_t'),('uint','uint32_t'),('ushort','uint16_t'),('datetime','int64_t'),('long','int64_t')]:
            text=re.sub(r'\b'+old+r'\b',new,text)
        text=re.sub('why="([^"]*)"\\s*\\+\\s*\\(','why=string("\\1")+(',text)
        return re.sub(r'\(string\)(\w+(?:\.\w+)*)',r'std::to_string(\1)',text)
    header=r"""
#include <string>
#include <vector>
#include <set>
#include <cmath>
#include <sstream>
#include <iomanip>
#include <cstdio>
#include <cstdint>
#include <algorithm>
#include <iostream>
#include <stdexcept>
using string=std::string;
using ENUM_ORDER_TYPE=int; using ENUM_ORDER_TYPE_TIME=int;
const int ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,ORDER_TIME_GTC=0,ORDER_TIME_SPECIFIED=1,STD_BUY_LIMIT=0,FILE_COMMON=1,INVALID_HANDLE=-1;
const int SYMBOL_DIGITS=1,SYMBOL_TRADE_STOPS_LEVEL=2,SYMBOL_TRADE_FREEZE_LEVEL=3,SYMBOL_TRADE_TICK_SIZE=4,SYMBOL_TRADE_TICK_VALUE=5,
 SYMBOL_VOLUME_MIN=6,SYMBOL_VOLUME_MAX=7,SYMBOL_VOLUME_STEP=8,SYMBOL_TRADE_CALC_MODE=9,SYMBOL_CALC_MODE_FOREX=0,SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE=5,SYMBOL_CALC_MODE_CFD=3;
string _Symbol="USDJPY.a",EA_COMMENT="offline",g_traderControlInstanceId="offline";int _Digits=3,TRADER_CONTROL_PROTOCOL_VERSION=1;
double _Point=.001,volumeMax=100,minimumLot=.01,volumeStep=.01,tickValue=.93796296296296296;
int64_t stopLevel=0,freezeLevel=0;bool readiness=true,busyPosition=false,persistence=true;
double bid=159.500,ask=159.501,tradeTick=.001; bool linearValuation=false; int StandardMarketSide=0; const int STD_MARKET_BUY=0;
bool replay254=false,replay253=false,razorControl=false;int64_t calculationMode=SYMBOL_CALC_MODE_CFD;
int p1Calls=0,fullCalls=0;double pointValuation=.166,fullValuation=25.32;
uint64_t g_ticket=0;int64_t g_armStartTime=0,g_expireAt=0;
bool g_lastPendingFailureStructural=false,g_lastPendingAcceptanceMismatch=false,g_lastPendingBrokerAttempted=false;
bool g_traderControlReady=true,g_portablePresetActionActivated=false;
int PendingCancelAfterMinutes=0;
struct MqlTick{double bid,ask;};
struct DesktopTraderCommand{string commandId,action;};
struct DesktopTraderResult{string commandId,action,outcome,reason;uint32_t retcode=0;uint64_t ticket=0;};
DesktopTraderCommand incoming;DesktopTraderResult lastResult;std::set<string> markers;
struct Request{double volume,entry,sl,tp;string symbol;};std::vector<Request> requests;
struct FakeTrade{
 bool marketBuy=false;
 bool Buy(double v,string symbol,double price,double sl,double tp,string){if(price!=0)throw std::runtime_error("not at market");marketBuy=true;requests.push_back({v,ask,sl,tp,symbol});return true;}
 bool Sell(double v,string symbol,double price,double sl,double tp,string){if(price!=0)throw std::runtime_error("not at market");marketBuy=false;requests.push_back({v,bid,sl,tp,symbol});return true;}
 uint64_t ResultDeal(){return 0;}
 bool BuyLimit(double volume,double entry,string symbol,double sl,double tp,int,int64_t,string){requests.push_back({volume,entry,sl,tp,symbol});return true;}
 bool SellLimit(double,double,string,double,double,int,int64_t,string){throw std::runtime_error("unexpected sell");}
 uint32_t ResultRetcode(){return 10009;}uint64_t ResultOrder(){return requests.empty()?0:100;}
 string ResultRetcodeDescription(){return "offline accepted";}
}trade;
template<class A,class B> double MathMax(A a,B b){return std::max(double(a),double(b));}
template<class A,class B> double MathMin(A a,B b){return std::min(double(a),double(b));}
double MathAbs(double x){return std::abs(x);}double MathRound(double x){return std::round(x);}double MathFloor(double x){return std::floor(x);}
double MathCeil(double x){return std::ceil(x);}double MathLog10(double x){return std::log10(x);}bool MathIsValidNumber(double x){return std::isfinite(x);}
double NormalizeDouble(double x,int digits){double s=std::pow(10.,digits);return std::round(x*s)/s;}
string IntegerToString(int64_t x){return std::to_string(x);}string DoubleToString(double x,int d){std::ostringstream s;s<<std::fixed<<std::setprecision(d)<<x;return s.str();}
int StringLen(string s){return int(s.size());}string StringSubstr(string s,int p,int n=-1){return s.substr(p,n<0?string::npos:n);}
int StringFind(string s,string v){auto p=s.find(v);return p==string::npos?-1:int(p);}
int StringReplace(string &s,string from,string to){int n=0;size_t p=0;while((p=s.find(from,p))!=string::npos){s.replace(p,from.size(),to);p+=to.size();n++;}return n;}
void StringToUpper(string&s){std::transform(s.begin(),s.end(),s.begin(),::toupper);}void StringToLower(string&s){std::transform(s.begin(),s.end(),s.begin(),::tolower);}
void StringTrimLeft(string&s){s.erase(0,s.find_first_not_of(" \t\r\n"));}void StringTrimRight(string&s){s.erase(s.find_last_not_of(" \t\r\n")+1);}
uint16_t StringGetCharacter(string s,int i){return (unsigned char)s.at(i);}double StringToDouble(string s){return std::stod(s);}int64_t StringToInteger(string s){return std::stoll(s);}
template<class...A> string StringFormat(string fmt,A...args){char b[200];std::snprintf(b,sizeof b,fmt.c_str(),args...);return b;}
template<class...A> void Print(A...args){}
int64_t SymbolInfoInteger(string,int prop){if(prop==SYMBOL_DIGITS)return _Digits;if(prop==SYMBOL_TRADE_STOPS_LEVEL)return stopLevel;if(prop==SYMBOL_TRADE_FREEZE_LEVEL)return freezeLevel;if(prop==SYMBOL_TRADE_CALC_MODE)return calculationMode;throw std::runtime_error("unhandled integer symbol property");}
bool SymbolInfoInteger(string s,int prop,int64_t &out){out=SymbolInfoInteger(s,prop);return true;}
double SymbolInfoDouble(string,int prop){if(prop==SYMBOL_TRADE_TICK_SIZE)return tradeTick;if(prop==SYMBOL_TRADE_TICK_VALUE)return tickValue;
 if(prop==SYMBOL_VOLUME_MIN)return minimumLot;if(prop==SYMBOL_VOLUME_MAX)return volumeMax;if(prop==SYMBOL_VOLUME_STEP)return volumeStep;
 if(prop==102)return _Point;if(prop==100)return ask;if(prop==101)return bid;throw std::runtime_error("unknown property");}
const int SYMBOL_ASK=100,SYMBOL_BID=101,SYMBOL_POINT=102;
bool SymbolInfoTick(string,MqlTick&t){t={bid,ask};return true;}
bool OrderCalcProfit(int direction,string,double volume,double entry,double target,double &out){
 if(linearValuation){out=NormalizeDouble((direction==ORDER_TYPE_BUY?target-entry:entry-target)/tradeTick*tickValue*volume,2);return true;}
 if(std::abs(std::abs(target-entry)-.001)<1e-8){p1Calls++;out=pointValuation;}
 else {fullCalls++;if(std::abs(volume-(razorControl?.16:.18))>1e-8||std::abs(entry-159.360)>1e-8||(!replay253&&!razorControl&&std::abs(target-159.510)>1e-8))throw std::runtime_error("wrong full valuation operands");out=fullValuation;}return true;}
bool ValidateTradingReadiness(bool,string &why){why=readiness?"":"offline margin/readiness blocked";return readiness;}
bool DesktopOneTradeBlock(string &why,uint64_t &ticket){why="offline position block";ticket=0;return busyPosition;}
void ResetTrendlinePlacementMetadata(){}void CancelAllPendingByMagic(){}
int64_t TimeCurrent(){return 1;}int64_t TimeGMT(){return 1;}int64_t ComputeExpireAt(){return 0;}
const int TIME_DATE=1,TIME_SECONDS=2;string TimeToString(int64_t,int){return "offline";}
bool IsTradePlacementAccepted(uint32_t code){return code==10009;}bool IsTransientPendingRetcode(uint32_t){return false;}
bool IsDefinitePendingRejectionRetcode(uint32_t){return true;}
bool IsPendingLimitTicketMatching(uint64_t ticket,bool,double entry,string){return ticket==100&&!requests.empty()&&std::abs(requests.back().entry-entry)<1e-8;}
void FindMatchingPendingLimit(bool,double,string,uint64_t&t){t=requests.empty()?0:100;}
bool AcquireDesktopCommandGate(int &gate,string&){gate=1;return true;}void FileClose(int){}
string TraderControlConsumedFile(string id){return "offline/"+id;}bool FileIsExist(string path,int){return markers.count(path)>0;}
bool WriteVerifiedCommonText(string path,string,string&why){if(!persistence){why="offline persistence failure";return false;}markers.insert(path);return true;}
bool ReadDesktopTraderCommand(DesktopTraderCommand &out,string&){out=incoming;return !out.commandId.empty();}
bool IsDesktopCommandIdValid(string id){return !id.empty();}bool IsDesktopActionAllowed(string action){return action=="limit"||action=="market"||action=="trendline"||action=="ema_bounce";}
bool WriteDesktopTraderResult(const DesktopTraderResult &r){lastResult=r;return true;}
string ShortStableFingerprint(string s){return s;}

void ExecuteDesktopTrendline(DesktopTraderResult&){throw std::runtime_error("wrong action");}
void ExecuteDesktopEmaBounce(DesktopTraderResult&){throw std::runtime_error("wrong action");}
"""
    numeric=['RiskAUD_Min','RiskAUD_Target','RiskAUD_Max','CommissionPerLotPerSide','RiskSlippageBufferPoints','SlippagePoints',
        'NetRR_Target','AutoTP_SafetyPoints','SL_PriceDistance','TP_PriceDistance','SL_DistancePoints','TP_DistancePoints','StandardLimitSide','StandardLimitEntryPrice','MagicNumber']
    booleans=['IncludeCommissionInRisk','AutoFitRiskSlippageBuffer','UsePriceDistanceInputs','AutoTP_NetRR_Enabled','OrdersEnabled','UseDesktopTraderControls']
    config='\n'.join('double '+k+'='+preset.get(k,'0')+';' for k in numeric)+'\n'
    config+='\n'.join('bool '+k+'='+preset[k]+';' for k in booleans)+'\nstring DistanceSymbol='+json.dumps(preset['DistanceSymbol'])+';\nstring PresetAccountType='+json.dumps(preset['PresetAccountType'])+';\n'
    if strategy_cases:
        header = header[:header.index("void ExecuteDesktopTrendline(DesktopTraderResult&)")]
        header += _TRADER_STRATEGY_HOST_DOUBLES
    definitions=[];prototypes=['bool ComputeAutoTP_NetRR(double,bool,double,double,double,double&,int&,double&,string&,bool market=false);']
    for function in functions:
        function=adapt(function);at=function.index('{');signature=function[:at].strip();prototypes.append(signature+';')
        signature=re.sub(r'=\s*(?:0|false)\b','',signature);definitions.append(signature+'\n'+function[at:])
    main=r"""
void check(bool ok,const char*why){if(!ok)throw std::runtime_error(why);}
const double fixtureEntry=StandardLimitEntryPrice,fixtureSL=SL_PriceDistance,fixtureR=NetRR_Target,fixtureCap=RiskAUD_Max;
const string fixtureSymbol=DistanceSymbol,fixturePricing=PresetAccountType;
void reset(){replay253=razorControl=false;calculationMode=SYMBOL_CALC_MODE_CFD;PresetAccountType=fixturePricing;UsePriceDistanceInputs=true;CommissionPerLotPerSide=0;requests.clear();g_ticket=0;p1Calls=fullCalls=0;readiness=persistence=true;busyPosition=false;stopLevel=freezeLevel=0;volumeMax=100;minimumLot=.01;StandardLimitEntryPrice=fixtureEntry;SL_PriceDistance=fixtureSL;NetRR_Target=fixtureR;DistanceSymbol=fixtureSymbol;RiskAUD_Max=fixtureCap;}
void run(string id){incoming={id,"limit"};HandleDesktopTraderCommand();}
int main(){try{
 bool unhandledFailed=false;try{SymbolInfoInteger(_Symbol,999999);}catch(const std::exception&){unhandledFailed=true;}check(unhandledFailed,"unhandled property defaulted");
 reset();PresetAccountType="";replay253=true;run("old253-cfd");
 check(lastResult.outcome=="blocked"&&requests.empty(),"old2.53 unexpectedly sent");
 check(lastResult.reason=="Final normalized TP cannot satisfy the nominal Net R cash target on broker grids.","old-case was not the actual Trader cash rejection");
 check(p1Calls==1&&fullCalls==1,"old-case did not execute cash valuation check");
 reset();PresetAccountType="";run("fixed-identical-legacy-cfd");check(lastResult.outcome=="accepted"&&requests.size()==1,"identical legacy CFD preset not fixed");
 for(int scenario=0;scenario<2;scenario++){
  reset();pointValuation=scenario==0?.166:.170;check(std::round(25.33/pointValuation)!=150,"bad fixture wrong-point estimate");
  run("accept-"+std::to_string(scenario));check(lastResult.outcome=="accepted","limit blocked");check(requests.size()==1,"not exactly one request");
  auto p=requests[0];check(p.symbol=="USDJPY.a"&&std::abs(p.volume-.18)<1e-8&&std::abs(p.entry-159.360)<1e-8&&std::abs(p.sl-159.300)<1e-8&&std::abs(p.tp-159.510)<1e-8,"wrong prepared ticket");
  check(fullCalls==1&&p1Calls==0,"one-point chase or missing full valuation");
  run("accept-"+std::to_string(scenario));check(requests.size()==1,"duplicate same command");
  StandardLimitEntryPrice=159.600;g_ticket=0;run("wrong-side-"+std::to_string(scenario));check(lastResult.outcome=="blocked"&&requests.size()==1,"invalid entry sent");
  StandardLimitEntryPrice=159.360;run("accept-"+std::to_string(scenario));check(lastResult.outcome=="blocked"&&requests.size()==1,"durable duplicate bypass");
 }
 for(int fault=0;fault<11;fault++){
  reset();if(fault==0)DistanceSymbol="GBPUSD";if(fault==1)SL_PriceDistance=.0605;if(fault==2)NetRR_Target=2.505;if(fault==3)stopLevel=100;
  if(fault==4)volumeMax=.17;if(fault==5)RiskAUD_Max=10.12;if(fault==6)readiness=false;if(fault==7)persistence=false;if(fault==8)UsePriceDistanceInputs=false;if(fault==9)CommissionPerLotPerSide=3.5;if(fault==10)PresetAccountType="unknown";
  run("fault-"+std::to_string(fault));check(lastResult.outcome=="blocked"&&requests.empty(),"protection bypass");
  if(fault==8||fault==9)check(lastResult.reason.find("Conflicting Standard FX preset")!=string::npos,"contradictory fields not reported");
 }
 reset();PresetAccountType="razor";CommissionPerLotPerSide=3.5;razorControl=true;UsePriceDistanceInputs=false;SL_DistancePoints=60;run("nonportable-razor-control");
 check(lastResult.outcome=="blocked"&&requests.empty()&&p1Calls==1&&fullCalls==1,"Razor commission path was bypassed");
 check(lastResult.reason=="Final normalized TP cannot satisfy the nominal Net R cash target on broker grids.","Razor outcome not from original cash path");
 reset();busyPosition=true;run("position-busy");check(lastResult.outcome=="blocked"&&requests.empty(),"position block bypass");
 std::cout<<"old253_cash_rejection_reproduced; fixed_legacy_and_identified_CFD; razor_control_retained; executed_extracted_production_controls: USDJPY.a buy_limit volume=0.18 entry=159.360 sl=159.300 tp=159.510 full_valuation=25.32 requested_estimate=25.33 p1_calls=0; duplicate/invalid/margin/readiness/persistence guards PASS\n";
 return 0;}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
"""
    if market_cases:
        main = _market_controls_cases(market_cases, numeric, booleans)
    cpp=tmp_path/'limit-host.cpp';exe=tmp_path/'limit-host.exe';obj=tmp_path/'limit-host.obj'
    wrapper='bool ComputeAutoTP_NetRR(double entry,bool buy,double vol,double nominal,double buffered,double &tp,int &points,double &rr,string &why,bool market){return replay253?LegacyComputeAutoTP_NetRR(entry,buy,vol,nominal,buffered,tp,points,rr,why):CurrentComputeAutoTP_NetRR(entry,buy,vol,nominal,buffered,tp,points,rr,why,market);}\n'
    if market_cases:
        wrapper=wrapper.replace('return replay253?', 'if(replay254)return Replay254AutoTP(entry,buy,vol,nominal,buffered,tp,points,rr,why);return replay253?')
    if not baseline:
        prototypes[0] = prototypes[0].replace("bool market=false)", "bool market=false,bool initialLimit=false)")
        wrapper = wrapper.replace("bool market){", "bool market,bool initialLimit){").replace("why,market);", "why,market,initialLimit);")
    if limit_cases:
        main = _nzd_limit_host_cases(limit_cases, numeric, booleans, baseline)
    if strategy_cases:
        main = _strategy_controls_cases(strategy_cases, numeric, booleans)
    cpp.write_text(header+config+'\n'.join(prototypes)+'\n'+'\n'.join(definitions)+wrapper+main)
    exe.write_bytes(b'');obj.write_bytes(b'') # manifest precreation audit covers native outputs
    vc=Path(r'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.43.34808')
    sdk=Path(r'C:\Program Files (x86)\Windows Kits\10');version='10.0.22621.0'
    cl=vc/'bin/Hostx64/x64/cl.exe';assert cl.is_file(),'Recorded offline host compiler unavailable'
    env=dict(os.environ,PATH=str(cl.parent)+os.pathsep+os.environ.get('PATH',''))
    args=[str(cl),'/nologo','/EHsc','/std:c++17','/W0','/MT','/I'+str(vc/'include'),'/I'+str(sdk/'Include'/version/'ucrt'),'/I'+str(sdk/'Include'/version/'um'),'/I'+str(sdk/'Include'/version/'shared'),str(cpp),'/Fo'+str(obj),'/Fe'+str(exe),'/link','/INCREMENTAL:NO','/MANIFEST:NO','/PDB:NONE','/LIBPATH:'+str(vc/'lib/x64'),'/LIBPATH:'+str(sdk/'Lib'/version/'ucrt/x64'),'/LIBPATH:'+str(sdk/'Lib'/version/'um/x64')]
    built=subprocess.run(args,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=60)
    (tmp_path/'host-compile.log').write_text(built.stdout+built.stderr)
    assert built.returncode==0,built.stdout+built.stderr
    ran=subprocess.run([str(exe)],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert ran.returncode==0,ran.stdout+ran.stderr
    if not market_cases and not limit_cases and not strategy_cases:
        assert 'entry=159.360 sl=159.300 tp=159.510' in ran.stdout
    print(ran.stdout.strip())


# Only MT5 platform and durable storage are doubled. The handlers and placement
# routines are extracted from the same production EA compiled for delivery.
_TRADER_STRATEGY_HOST_DOUBLES = r"""
const int _Period=15, MODE_EMA=1, PRICE_CLOSE=2, OBJ_TREND=3, OBJ_HLINE=4, OBJPROP_TYPE=5, OBJPROP_SELECTED=6,
          TL_BUY_LIMIT=0, ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3, POSITION_TICKET=9,
          INIT_SUCCEEDED=0, INIT_PARAMETERS_INCORRECT=1, INIT_FAILED=2, EMA_BOUNCE_FAST=0;
const int64_t TRENDLINE_ARM_GENERATION_MAX=100000;
int Direction=0, TrendEMAPeriod=20, FastEMAPeriod=9, SlowEMAPeriod=20, BounceReferenceEMA=1;
int hTrend=-1, hFast=-1, hSlow=-1, lifecycleWrites=0;
bool UseDualEMA=true, RequireNamedTrendline=false, EnforceOneTradeAtATime=true;
string g_trendName, TrendlineObjectName;
std::vector<int> initializedPeriods;
struct ChartObject { string name; int type; double price; bool selected; };
std::vector<ChartObject> objects;
const ChartObject* findObject(const string& name) {
  for (const auto& object : objects) if (object.name==name) return &object;
  return nullptr;
}
int ObjectsTotal(int,int,int type) {
  int count=0;
  for (const auto& object : objects) if (type<0 || object.type==type) ++count;
  return count;
}
string ObjectName(int,int index,int,int type) {
  for (const auto& object : objects) if ((type<0 || object.type==type) && index--==0) return object.name;
  return "";
}
int ObjectFind(int,const string& name){return findObject(name)?0:-1;}
int ObjectGetInteger(int,const string& name,int property){
  const auto* object=findObject(name);
  if(!object)throw std::runtime_error("unknown chart object");
  if(property==OBJPROP_TYPE)return object->type;
  if(property==OBJPROP_SELECTED)return object->selected;
  throw std::runtime_error("unknown object property");
}
double ObjectGetValueByTime(int,const string& name,int64_t time,int) {
  if(time!=100)throw std::runtime_error("wrong chart bar");
  const auto* object=findObject(name);return object?object->price:0.0;
}
int64_t iTime(const string& symbol,int timeframe,int shift) {
  if(symbol!=_Symbol || timeframe!=_Period || shift!=0)throw std::runtime_error("wrong trendline chart context");
  return 100;
}
double iClose(const string& symbol,int timeframe,int shift) {
  if(symbol!=_Symbol || timeframe!=_Period || shift!=1)throw std::runtime_error("wrong EMA chart context");
  return 159.560;
}
int iMA(const string& symbol,int timeframe,int period,int shift,int mode,int applied) {
  if(symbol!=_Symbol || timeframe!=_Period || shift!=0 || mode!=MODE_EMA || applied!=PRICE_CLOSE)
    throw std::runtime_error("wrong EMA initializer context");
  initializedPeriods.push_back(period);
  return period;
}
bool GetBufferValue(int handle,int buffer,int shift,double& price) {
  if(buffer!=0 || (shift!=0 && shift!=1))throw std::runtime_error("wrong EMA buffer");
  if(handle==20)price=159.350;
  else if(handle==13)price=159.365;
  else if(handle==9)price=159.370;
  else return false;
  return true;
}
struct TrendlineLifecycleRecord {
  bool exists=false,working=false,replacing=false;
  uint64_t ticket=0;
  int64_t generation=0,blockedGeneration=0,expiration=0;
  int orderType=0;
  string orderComment;
};
bool LoadTrendlineLifecycleRecord(TrendlineLifecycleRecord& record,string&) {
  record={};
  return true;
}
bool PersistTrendlineLifecycleRecord(const TrendlineLifecycleRecord&,const TrendlineLifecycleRecord&,string&) {
  ++lifecycleWrites;
  return true;
}
int64_t TrendlineHighestHandled(const TrendlineLifecycleRecord& record){return record.generation;}
string TrendlineOrderComment(int64_t){return "offline trendline";}
string TraderControlActiveTrendlineFile(){return "offline active trendline";}
void MaintainTrendlineLifecycle(string,bool){throw std::runtime_error("unexpected reconciliation");}
bool FindAnyPendingLimitForEA(uint64_t&){return false;}
bool PositionSelect(string){return busyPosition;}
bool OrderSelect(uint64_t){return true;}
const int ORDER_TIME_EXPIRATION=1;
int64_t PositionGetInteger(int){return 0;}
int64_t OrderGetInteger(int){return 0;}
"""


def _strategy_controls_cases(cases, numeric, booleans):
    expected = {
        "market": ("market", "159.562", ".12"),
        "limit": ("limit", "159.360", ".12"),
        "trendline": ("trendline", "159.360", ".12"),
        "ema20": ("ema_bounce", "159.350", ".12"),
        "ema13": ("ema_bounce", "159.365", ".11"),
    }
    fragments = []
    for case, preset in cases.items():
        action, entry, volume = expected[case]
        settings = "\n".join(f"{key}={preset.get(key, '0')};" for key in numeric+booleans)
        name = json.dumps(preset.get("TrendlineObjectName", ""))
        strict = preset.get("RequireNamedTrendline", "false")
        period = preset.get("TrendEMAPeriod", "20")
        fragments.append(f"""
        {settings}
        DistanceSymbol={json.dumps(preset['DistanceSymbol'])}; PresetAccountType={json.dumps(preset['PresetAccountType'])};
        TrendlineObjectName={name}; RequireNamedTrendline={strict}; Direction={preset.get('Direction','0')};
        UseDualEMA={preset.get('UseDualEMA','true')}; TrendEMAPeriod={period};
        StandardMarketSide={preset.get('StandardMarketSide','0')};
        requests.clear(); g_ticket=0; g_portablePresetActionActivated=false; lifecycleWrites=0; initializedPeriods.clear();
        incoming={{"",""}};
        require(InitializeEmaBounce()==INIT_SUCCEEDED,"EMA initialization");
        require(initializedPeriods=={('std::vector<int>{9,20}' if preset.get('UseDualEMA','true')=='true' else f'std::vector<int>{{{period}}}')},
                "wrong EMA period initialized");
        HandleDesktopTraderCommand();
        require(requests.empty()&&lifecycleWrites==0&&!g_portablePresetActionActivated,"loading armed or placed");
        observe("{case}","{action}",{entry},{volume});
        """)
        if case == "trendline":
            fragments.append("""
            objects[0].name="alex Trend  One"; // Case-only rename.
            block("missing","Named trendline is missing or was renamed");
            objects[0].name=TrendlineObjectName; objects[0].type=OBJ_HLINE;
            block("wrong-type","Named object is not an MT5 trendline");
            objects[0].type=OBJ_TREND;
            RequireNamedTrendline=false;
            requests.clear(); g_ticket=0;
            observe("legacy selected","trendline",159.370,.12);
            """)
    return r"""
void require(bool ok,const string& reason){if(!ok)throw std::runtime_error(reason);}
void observe(const string& scenario,const string& action,double entry,double volume) {
  incoming={scenario,action};HandleDesktopTraderCommand();
  require(lastResult.outcome=="accepted" && requests.size()==1,scenario+": "+lastResult.reason);
  const auto &request=requests.front();
  require(request.symbol=="USDJPY.a" && std::abs(request.entry-entry)<1e-8 &&
          std::abs(request.volume-volume)<1e-8 && std::abs(request.sl-(entry-.060))<1e-8 &&
          std::abs(request.tp-(entry+.150))<1e-8,scenario+": incorrect prepared request");
  if(action=="trendline") require(g_trendName==(RequireNamedTrendline?TrendlineObjectName:"Other Line"),"wrong chart object");
  std::cout<<scenario<<" "<<(action=="trendline"?g_trendName:action)<<" "<<std::fixed<<std::setprecision(3)
           <<request.entry<<" SL "<<request.sl<<" TP "<<request.tp<<" lots "<<request.volume<<std::endl;
  HandleDesktopTraderCommand();
  require(requests.size()==1,scenario+": duplicate request");
}
void block(const string& scenario,const string& reason) {
  requests.clear();lifecycleWrites=0;g_ticket=0;
  incoming={scenario,"trendline"};HandleDesktopTraderCommand();
  require(requests.empty()&&lifecycleWrites==0&&lastResult.outcome=="blocked"&&
          lastResult.reason.find(reason)!=string::npos,scenario+": "+lastResult.reason);
  std::cout<<scenario<<": "<<lastResult.reason<<std::endl;
}
int main(){try{
  linearValuation=true;tickValue=1.5;bid=159.551;ask=159.562;
  objects={{"Alex Trend  One",OBJ_TREND,159.360,false},{"Other Line",OBJ_TREND,159.370,true}};
""" + "".join(fragments) + r"""
  return 0;
}catch(const std::exception& error){std::cerr<<error.what();return 1;}}
"""


def _nzd_limit_host_cases(presets, numeric, booleans, baseline):
    body = []
    for profile, preset in presets.items():
        settings = "\n".join(f"{key}={preset.get(key, '0')};" for key in numeric + booleans)
        # AUD 1.51/point/lot. Standard: ceil(10/24.16) on .01 steps = .42 lots.
        # Razor: .33*(24.16+7) = 10.2828, requested net 35.98, commission 2.31.
        # Nearest legal gross target is 77 points, net 36.06 at the fixture valuation.
        for stops, freeze in [(10, 30), (10, 0), (17, 0)]:
            old_freeze_block = baseline and freeze == 30
            old_cash_block = baseline and profile == "razor"
            if stops == 17 or old_freeze_block:
                reason = "SL too close to entry" if baseline else "SL distance 16.00 points; minimum 17 points."
            elif old_cash_block:
                reason = "Final normalized TP cannot satisfy the nominal Net R cash target"
            else:
                reason = None
            command_id = f"nzd-{profile}-{stops}-{freeze}"
            body.append(f"""
            {settings}
            DistanceSymbol={json.dumps(preset["DistanceSymbol"])}; PresetAccountType="{profile}";
            stopLevel={stops}; freezeLevel={freeze}; g_ticket=0; requests.clear();
            incoming={{"{command_id}","limit"}}; HandleDesktopTraderCommand();
            """)
            if reason:
                body.append(f"""
                if(lastResult.outcome!="blocked" || lastResult.reason.find("{reason}")==string::npos || !requests.empty())
                    throw std::runtime_error("Expected local block; observed "+lastResult.outcome+" "+lastResult.reason);
                std::cout<<"{command_id} blocked: "<<lastResult.reason<<std::endl;
                """)
            else:
                volume, tp = (".42", ".55443") if profile == "standard" else (".33", ".55464")
                body.append(f"""
                if(lastResult.outcome!="accepted" || requests.size()!=1)throw std::runtime_error("Limit blocked: "+lastResult.reason);
                {{const auto &p=requests.at(0); // SellLimit throws, so this can only be BuyLimit.
                  if(p.symbol!="NZDUSD.a" || std::abs(p.volume-{volume})>1e-9 || std::abs(p.entry-.55387)>1e-9 ||
                     std::abs(p.sl-.55371)>1e-9 || std::abs(p.tp-{tp})>1e-9)throw std::runtime_error("Incorrect limit ticket");
                  std::cout<<"{command_id} "<<p.symbol<<" buy limit "<<p.volume<<" "<<p.entry<<" "<<p.sl<<" "<<p.tp<<std::endl;}}
                HandleDesktopTraderCommand(); if(requests.size()!=1)throw std::runtime_error("duplicate sent");
                """)
    return ('int main(){try{linearValuation=true; _Symbol="NZDUSD.a"; _Point=tradeTick=.00001; _Digits=5;'
            'tickValue=1.51; bid=.55435; ask=.55447;' + "".join(body) +
            'return 0;}catch(const std::exception&e){std::cerr<<e.what();return 1;}}')


def test_pepperstone_market_download_prepares_fx_orders(monkeypatch, tmp_path, pepperstone_weekend_reference):
    """Download through the actual calculator. Replay the downloaded inputs with no broker transport."""
    import os
    from decimal import Decimal as D
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "3.50")
    write, _ = pepperstone_weekend_reference
    # symbol, Bid, Ask, point, trade tick, volume step, AUD per trade tick/lot, side, pricing
    cases = [
        ('EURUSD','1.08500','1.08512','.00001','.00001','.01','1.155172414','buy','razor'),
        ('EURUSD','1.08500','1.08512','.00001','.00001','.01','1.155172414','buy','standard'),
        ('GBPUSD','1.27000','1.27014','.00001','.00002','.02','2.25','sell','standard'),
        ('USDCHF','.88100','.88112','.00001','.00001','.05','2.065','buy','standard'),
        ('USDJPY','159.400','159.412','.001','.001','.01','1.03','sell','standard'),
    ]
    symbols={}
    for symbol,bid,ask,point,tick,step,value,side,profile in cases:
        symbols[symbol]=dict(symbol=symbol, mt5_symbol=symbol+'.a', point=point, trade_tick_size=tick,
            quote_timestamp='2026-11-21T07:59:59Z', timestamp='2026-11-21T07:59:59Z', bid=bid, ask=ask,
            minimum_lot=step, volume_step=step, maximum_lot='100', tick_value=value, available=True,
            stops_level=0, freeze_level=0)
    write(symbols=list(symbols.values()))
    prepared = []
    def download(call, evaluate, click, wait_for, downloads, directory):
        def select(group, value):
            evaluate(f"document.querySelector('#{group} [data-v={json.dumps(value)}]').click()")
        def field(element, value):
            evaluate(f"(()=>{{const e=document.getElementById('{element}');e.value={json.dumps(value)};"
                     "e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        select('asset-toggle','fx')
        select('broker-toggle','pepperstone')
        select('account-toggle','demo')
        select('risk-toggle','fixed_aud')
        select('timeframe-toggle','15m')
        for case in cases:
            symbol,bid,ask,point,tick,step,value,side,profile=case
            select('pepperstone-account-type-toggle',profile)
            select('side-toggle',side)
            field('calc-symbol',symbol)
            field('calc-risk','10')
            field('calc-rr','6')
            field('calc-sl-ticks','58')
            assert evaluate("document.getElementById('calc-pepperstone-set').disabled")
            canonical=symbol[:3]+'_'+symbol[3:]
            wait_for(f"document.getElementById('calc-canonical-symbol').textContent.includes('{canonical}')")
            click('calc-quote')
            wait_for("!document.getElementById('calc-pepperstone-set').disabled")
            assert evaluate("document.getElementById('calc-quote-status').textContent")==f"{canonical.replace('_','/')} \u00b7 {profile.title()} \u00b7 Preset ready"
            details=evaluate("document.getElementById('calc-results').textContent")
            assert 'Request deviation' in details and 'cushion' in details
            case_dir=directory/(symbol+'-'+profile)
            case_dir.mkdir()
            call('Page.setDownloadBehavior',dict(behavior='allow', downloadPath=str(case_dir)))
            click('calc-pepperstone-set')
            wait_for("document.getElementById('calc-success').textContent.includes('.set downloaded')")
            deadline=time.monotonic()+10
            while not (files:=list(case_dir.glob('*.set'))):
                assert time.monotonic()<deadline, 'Downloaded file missing'
                time.sleep(.05)
            downloaded,=files
            assert downloaded.read_bytes()==downloads[-1][0].encode()
            preset=_parse_set_file(downloaded.read_text())
            assert preset['PresetAccountType']==profile and D(preset['CommissionPerLotPerSide'])==(D('3.5') if profile=='razor' else 0)
            assert preset['DistanceSymbol']==canonical and D(preset['SL_PriceDistance'])==58*D(point)
            assert preset['AutoTP_SafetyPoints']=='0' and D(preset['NetRR_Target'])==6
            prepared.append((case,preset))
            if profile=='razor':
                # Delay a real export response until after the visible pricing profile changes.
                evaluate("""window.originalFetch=window.fetch;window.fetch=async(...args)=>{
                  const response=await window.originalFetch(...args);
                  if(args[0]==='/api/calculator/pepperstone-set')await new Promise(resolve=>window.releasePreset=resolve);
                  return response}""")
                click('calc-pepperstone-set')
                wait_for("typeof window.releasePreset==='function'")
                select('pepperstone-account-type-toggle','standard')
                evaluate("window.fetch=window.originalFetch;window.releasePreset()")
                wait_for("document.getElementById('calc-pepperstone-set').disabled")
                assert not evaluate("document.getElementById('calc-success').textContent.includes('.set downloaded')")
        assert len(list(directory.glob('*/*.set')))==len(cases)
    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference, limit_fill=False, market_downloads=download)
    original=os.environ.get('LTT_FIXTURE_INCIDENT_PRESET')
    if original:
        import hashlib
        raw=Path(original).read_bytes()
        assert hashlib.sha256(raw).hexdigest()=='9f081cce207ae53b3e6a0517815f400d822e5a7f3542d988e82972831da00eb3'
        prepared.append((cases[0],_parse_set_file(raw.decode())))
    _run_trader_controls_host(tmp_path, prepared[0][1], prepared)


def _market_controls_cases(cases, numeric, booleans):
    from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP
    body=[]
    for index,(case,preset) in enumerate(cases):
        symbol,bid,ask,point,tick,step,value,side,profile=case
        entry=D(ask if side=='buy' else bid)
        sign=1 if side=='buy' else -1
        stop=D(preset['SL_PriceDistance'])
        commission=2*D(preset['CommissionPerLotPerSide'])
        per_lot=stop/D(tick)*D(value)+commission
        volume=(D(10)/(per_lot*D(step))).to_integral_value(rounding=ROUND_CEILING)*D(step)
        nominal=per_lot*volume
        assert (volume-D(step))*per_lot<10<=nominal<=12
        money=lambda x:x.quantize(D('.01'),rounding=ROUND_HALF_UP)
        reward=money(money(nominal)*D(preset['NetRR_Target']))
        gross=reward+commission*volume
        distance=stop*6 if not commission else (gross/money(stop/D(tick)*D(value)*volume)*stop/D(tick)).to_integral_value(rounding=ROUND_HALF_UP)*D(tick)
        sl=entry-sign*stop
        tp=entry+sign*distance
        settings=''.join(f'{key}={preset.get(key,"0")};\n' for key in numeric+booleans)
        id=f'market-{index}-'+__import__('uuid').uuid4().hex
        old_check = ""
        if profile=="razor":
            old_check = f'''replay254=true; incoming={{"{id}-254","market"}}; requests.clear();HandleDesktopTraderCommand();
            if(lastResult.outcome!="blocked" || lastResult.reason!="Final normalized TP cannot satisfy the nominal Net R cash target on broker grids." || !requests.empty())
               throw std::runtime_error("old-case reason: "+lastResult.reason);
            replay254=false;'''
        body.append(f"""
        {settings}
        DistanceSymbol={json.dumps(preset['DistanceSymbol'])}; PresetAccountType="{profile}";
        _Symbol="{symbol}.a"; _Point={point}; tradeTick={tick}; _Digits={-D(point).as_tuple().exponent};
        minimumLot=volumeStep={step}; tickValue={value}; ask={ask}; bid={bid}; stopLevel=0;
        StandardMarketSide={int(preset['StandardMarketSide'])};
        {old_check}
        incoming={{"{id}","market"}}; requests.clear(); HandleDesktopTraderCommand();
        if(requests.size()!=1 || lastResult.outcome!="accepted") throw std::runtime_error("market blocked: "+lastResult.reason);
        {{const auto &p=requests[0];
          if(p.symbol!=_Symbol || trade.marketBuy!={str(side=='buy').lower()} || std::abs(p.volume-{volume})>1e-9 ||
             std::abs(p.entry-{entry})>1e-9 || std::abs(p.sl-{sl})>1e-9 || std::abs(p.tp-{tp})>1e-9)
             throw std::runtime_error("incorrect market parameters");
          std::cout<<_Symbol<<" {profile} {side} "<<p.volume<<" "<<std::fixed<<std::setprecision(_Digits)
                   <<p.entry<<" "<<p.sl<<" "<<p.tp<<" nominal={nominal:.4f} estimated_reward={reward}"<<std::endl;}}
        HandleDesktopTraderCommand();
        if(requests.size()!=1)throw std::runtime_error("duplicate sent");
        stopLevel=100; incoming={{"{id}-invalid","market"}}; HandleDesktopTraderCommand();
        if(lastResult.outcome!="blocked" || requests.size()!=1)throw std::runtime_error("invalid stop sent");
        """)
    return 'int main(){try{linearValuation=true;'+''.join(body)+'return 0;}catch(const std::exception&e){std::cerr<<e.what();return 1;}}'


def test_pepperstone_strategy_presets_in_edge(monkeypatch, tmp_path, pepperstone_weekend_reference):
    service = master_service
    folder = tmp_path / "Presets"
    folder.mkdir()
    monkeypatch.setattr(service, "_PEPPERSTONE_PRESETS_FOLDER", folder)
    monkeypatch.setenv("PEPPERSTONE_TRADER_COMMISSION_PER_LOT_PER_SIDE", "3.50")
    exported = []
    real_export = service._build_pepperstone_trader_set

    def capture(payload):
        content, filename = real_export(payload)
        exported.append((dict(payload), content.encode("utf-8"), filename))
        return content, filename

    monkeypatch.setattr(service, "_build_pepperstone_trader_set", capture)
    cases = {}

    def exercise(evaluate, click, wait_for, downloads):
        def select(group, value):
            evaluate(f"document.querySelector('#{group} [data-v={json.dumps(value)}]').click()")

        def change(element, value, event="input"):
            evaluate(f"""(() => {{
                const field = document.getElementById({json.dumps(element)});
                field.value = {json.dumps(value)};
                field.dispatchEvent(new Event({json.dumps(event)}, {{bubbles:true}}));
            }})()""")

        def strategy(value):
            change("calc-pepperstone-strategy", value, "change")

        def visible(element):
            return evaluate(f"getComputedStyle(document.getElementById({json.dumps(element)})).display !== 'none'")

        def calculate():
            click("calc-quote")
            wait_for("!document.getElementById('calc-pepperstone-set').disabled")

        def save(case):
            before = set(folder.iterdir())
            click("calc-pepperstone-set")
            wait_for("document.getElementById('calc-success').textContent.includes('Saved Pepperstone_Trader_')")
            (path,) = set(folder.iterdir()) - before
            payload, content, filename = exported[-1]
            expected_stem = Path(filename).stem
            assert path.name == filename or re.fullmatch(re.escape(expected_stem) + r"_\d+\.set", path.name)
            assert path.read_bytes() == content
            cases[case] = _parse_set_file(path.read_text(encoding="utf-8"))
            assert cases[case]["OrdersEnabled"] == cases[case]["UseDesktopTraderControls"] == "true"
            assert cases[case]["TrendlineArmGeneration"] == "0"
            assert cases[case]["SL_PriceDistance"] == "0.06"
            assert cases[case]["TP_PriceDistance"] == "0.15"
            return payload

        assert not visible("pepperstone-strategy-wrap")
        select("asset-toggle", "fx")
        select("broker-toggle", "pepperstone")
        select("account-toggle", "demo")
        select("risk-toggle", "fixed_aud")
        select("pepperstone-account-type-toggle", "standard")
        for field, value in [("calc-symbol", "USD_JPY"), ("calc-sl-ticks", "60"), ("calc-risk", "10"),
                             ("calc-tp-ticks", "150")]:
            change(field, value)
        change("calc-target-mode", "ticks", "change")
        wait_for("document.getElementById('calc-canonical-symbol').textContent.includes('USD_JPY')")
        assert visible("pepperstone-strategy-wrap") and visible("calc-order-type-wrap")
        assert evaluate("document.getElementById('calc-pepperstone-strategy').value") == "none"
        assert not visible("pepperstone-trendline-wrap") and not visible("pepperstone-ema-wrap")
        calculate()
        assert not list(folder.iterdir())
        market_payload = save("market")
        assert cases["market"]["Strategy"] == "3"
        assert cases["market"]["StandardMarketExecutionToken"].startswith("mkt_")
        legacy_payload = dict(market_payload)
        legacy_payload.pop("pepperstone_strategy")
        assert _parse_set_file(real_export(legacy_payload)[0])["Strategy"] == "3"

        select("order-toggle", "limit")
        change("calc-limit", "159.360")
        calculate()
        save("limit")
        assert cases["limit"]["Strategy"] == "2" and cases["limit"]["StandardLimitEntryPrice"] == "159.36"

        strategy("trendline")
        assert visible("pepperstone-trendline-wrap") and not visible("pepperstone-ema-wrap")
        assert not visible("limit-wrap") and not visible("calc-order-type-wrap")
        change("calc-pepperstone-trendline-name", "Alex Trend  One")
        calculate()
        before = set(folder.iterdir())
        evaluate("""window.originalFetch=window.fetch;
            window.fetch=async(...args)=>{
                const response=await window.originalFetch(...args);
                if(args[0]==='/api/calculator/pepperstone-set/local')
                    await new Promise(resolve=>window.releasePreset=resolve);
                return response;
            }""")
        click("calc-pepperstone-set")
        wait_for("typeof window.releasePreset==='function'")
        assert set(folder.iterdir()) == before
        strategy("ema_bounce")
        evaluate("""(async()=>{window.fetch=window.originalFetch;window.releasePreset();
                    await new Promise(resolve=>setTimeout(resolve,250));})()""")
        assert evaluate("document.getElementById('calc-pepperstone-set').disabled")
        assert set(folder.iterdir()) == before

        strategy("trendline")
        calculate()
        change("calc-pepperstone-trendline-name", "Other Line")
        assert evaluate("document.getElementById('calc-pepperstone-set').disabled")
        change("calc-pepperstone-trendline-name", "Alex Trend  One")
        calculate()
        trend_payload = save("trendline")
        assert cases["trendline"]["Strategy"] == "0"
        assert cases["trendline"]["RequireNamedTrendline"] == "true"
        assert cases["trendline"]["TrendlineObjectName"] == "Alex Trend  One"
        assert cases["trendline"]["Direction"] == "0"
        for value, status in [("alex Trend  One", 409), ("Alex\nStrategy=3", 400), ("", 400)]:
            with pytest.raises(service.HTTPException) as error:
                real_export(dict(trend_payload, pepperstone_trendline_name=value))
            assert error.value.status_code == status

        strategy("ema_bounce")
        assert visible("pepperstone-ema-wrap") and not visible("pepperstone-trendline-wrap")
        assert evaluate("document.getElementById('calc-side-heading').textContent") == "Estimate side"
        calculate()
        save("ema20")
        select("pepperstone-account-type-toggle", "razor")
        calculate()
        change("calc-pepperstone-ema-period", "13")
        assert evaluate("document.getElementById('calc-pepperstone-set').disabled")
        calculate()
        ema_payload = save("ema13")
        for case, period in [("ema20", "20"), ("ema13", "13")]:
            assert cases[case]["Strategy"] == "1"
            assert cases[case]["UseDualEMA"] == "false" and cases[case]["TrendEMAPeriod"] == period
            assert cases[case]["RequireNamedTrendline"] == "false"
        assert Decimal(cases["ema13"]["CommissionPerLotPerSide"]) == Decimal("3.50")
        for value, status in [("20", 409), ("3.5", 400), ("0", 400), ("2147483648", 400)]:
            with pytest.raises(service.HTTPException) as error:
                real_export(dict(ema_payload, pepperstone_ema_period=value))
            assert error.value.status_code == status
        # A repeat save of the unchanged bound quote reuses its existing file.
        before = set(folder.iterdir())
        click("calc-pepperstone-set")
        wait_for("document.getElementById('calc-success').textContent.includes('Saved Pepperstone_Trader_')")
        assert set(folder.iterdir()) == before and len(list(folder.iterdir())) == 5
        assert not list(downloads.iterdir())
        assert len(service._pepperstone_local_tracker_rows()) == 2  # None Market and Limit only.
        strategy("none")
        assert visible("calc-order-type-wrap") and visible("limit-wrap")
        assert evaluate("document.getElementById('calc-side-heading').textContent") == "Side"

    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference,
                                         limit_fill=False, local_preset_save=exercise)
    _run_trader_controls_host(tmp_path, cases["limit"], strategy_cases=cases)


def test_pepperstone_local_preset_save_in_edge(monkeypatch, tmp_path, pepperstone_weekend_reference):
    service=master_service
    folder=tmp_path/'Presets'
    monkeypatch.setattr(service, '_PEPPERSTONE_PRESETS_FOLDER', folder)
    original=service._build_pepperstone_trader_set
    built=[]

    def build(payload):
        content,filename=original(payload)
        built.append((content.encode('utf-8'),filename))
        return content,filename

    monkeypatch.setattr(service, '_build_pepperstone_trader_set', build)

    for scheme_host, client, origin in [
        ('127.0.0.1', '192.0.2.1', 'http://127.0.0.1'),
        ('127.0.0.1', '127.0.0.1', 'https://example.test'),
        ('example.test', '127.0.0.1', 'http://example.test'),
    ]:
        request=service.Request(dict(type='http', scheme='http', path='/api/calculator/pepperstone-set/local',
            headers=[(b'host',scheme_host.encode()),(b'origin',origin.encode())], client=(client,1234)))
        with pytest.raises(service.HTTPException) as denied:
            service.calculator_pepperstone_set_local(request,{})
        assert denied.value.status_code==403
    assert not built and not folder.exists()

    def exercise(evaluate, click, wait_for, downloads):
        def select(group,value):
            evaluate(f"document.querySelector('#{group} [data-v={json.dumps(value)}]').click()")

        def field(element,value):
            evaluate(f"(()=>{{const el=document.getElementById('{element}');el.value={json.dumps(value)};"
                     "el.dispatchEvent(new Event('input',{bubbles:true}));})()")

        def calculate():
            click('calc-quote')
            wait_for("!document.getElementById('calc-pepperstone-set').disabled")

        ready="document.getElementById('calc-success').textContent.includes('Saved Pepperstone_Trader_')"
        select('asset-toggle','fx')
        select('broker-toggle','pepperstone')
        select('account-toggle','demo')
        select('risk-toggle','fixed_aud')
        select('pepperstone-account-type-toggle','razor')
        for name,value in [('calc-symbol','USD_JPY'),('calc-sl-ticks','60'),('calc-risk','10'),('calc-rr','2.5')]:
            field(name,value)
        wait_for("document.getElementById('calc-canonical-symbol').textContent.includes('USD_JPY')")
        calculate()

        # Hold the prepare response across a real profile change. Even with a writable
        # directory, the obsolete Razor result must never publish.
        folder.mkdir()
        evaluate("""window.originalFetch=window.fetch;
          window.fetch=async(...args)=>{
            const response=await window.originalFetch(...args);
            if(args[0]==='/api/calculator/pepperstone-set/local') await new Promise(resolve=>window.releasePreset=resolve);
            return response;
          }""")
        click('calc-pepperstone-set')
        wait_for("typeof window.releasePreset==='function'")
        assert list(folder.iterdir())==[] and len(built)==1
        select('pepperstone-account-type-toggle','standard')
        evaluate("""(async()=>{window.fetch=window.originalFetch;window.releasePreset();
          await new Promise(resolve=>setTimeout(resolve,250));})()""")
        assert list(folder.iterdir())==[] and not evaluate(ready)
        select('pepperstone-account-type-toggle','razor')
        calculate()

        # A genuine missing-directory failure leaves the quote reusable and preserves
        # the prepared token. A pre-existing collision must then survive both saves.
        folder.rmdir()
        click('calc-pepperstone-set')
        wait_for("document.getElementById('calc-error').textContent.includes('Could not save in')")
        error=evaluate("document.getElementById('calc-error').textContent")
        assert str(folder) in error and ('No such file' in error or 'cannot find' in error)
        assert len(built)==2
        folder.mkdir()
        content,filename=built[1]
        collision=folder/filename
        collision.write_bytes(b'existing preset')
        click('calc-pepperstone-set')
        wait_for(ready)
        saved=folder/(collision.stem+'_2.set')
        assert saved.read_bytes()==content and collision.read_bytes()==b'existing preset'
        assert str(folder) in evaluate("document.getElementById('calc-success').textContent")
        assert saved.name in evaluate("document.getElementById('calc-success').textContent")
        assert _parse_set_file(content.decode())['StandardMarketExecutionToken'].startswith('mkt_')
        assert Decimal(_parse_set_file(content.decode())['CommissionPerLotPerSide'])>0
        click('calc-pepperstone-set')
        wait_for(ready)
        assert len(built)==2 and sorted(folder.iterdir())==sorted([collision,saved])

        saved.write_bytes(b'externally changed preset')
        click('calc-pepperstone-set')
        wait_for("document.getElementById('calc-error').textContent.includes('The saved preset was changed')")
        assert saved.read_bytes()==b'externally changed preset' and len(built)==2

        select('pepperstone-account-type-toggle','standard')
        select('order-toggle','limit')
        field('calc-limit','159.360')
        calculate()
        click('calc-pepperstone-set')
        wait_for(ready)
        content,filename=built[2]
        assert (folder/filename).read_bytes()==content
        preset=_parse_set_file(content.decode())
        assert preset['PresetAccountType']=='standard' and Decimal(preset['CommissionPerLotPerSide'])==0
        assert preset['SL_PriceDistance']=='0.06' and preset['TP_PriceDistance']=='0.15'
        assert Decimal(preset['StandardLimitEntryPrice'])==Decimal('159.360')
        assert 'StandardMarketExecutionToken' not in preset
        assert sorted(folder.iterdir())==sorted([collision,saved,folder/filename])
        assert list(downloads.iterdir())==[]

    _exercise_pepperstone_calculator_page(tmp_path, pepperstone_weekend_reference,
                                         limit_fill=False, local_preset_save=exercise)
