import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "calculator.js"


def _run_usdjpy_auto_route_harness(mode: str) -> dict:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'),mode=process.argv[2];
class Button {
  constructor(v){this.dataset={v};this.listeners={};this.disabled=false;this.attrs={};this.active=false;this.classList={toggle:(name,on)=>{if(name==='active')this.active=!!on;},add:()=>{},remove:()=>{}};}
  addEventListener(e,f){this.listeners[e]=f;} click(){if(!this.disabled&&this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];}
}
class Element extends Button {
  constructor(id){super('');this.id=id;this.value='';this.style={};this.textContent='';this._html='';this.buttons=[];this.checked=false;}
  set innerHTML(v){this._html=String(v||'');this.buttons=[...this._html.matchAll(/data-v="([^"]*)"/g)].map(m=>new Button(m[1]));}
  get innerHTML(){return this._html;} querySelectorAll(s){return s==='button'?this.buttons:[];}
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const el=Object.fromEntries(ids.map(id=>[id,new Element(id)]));
const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
const calls=[];let quotePayload=null,submitPayload=null,intervalClears=0;
const response=data=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
const quote={asset:'fx',broker:'oanda',venue:'OANDA',resolved_venue:'OANDA',symbol:'USD_JPY',tick_size:'0.001',entry_price:'150.000',stop_price:'149.990',target_price:'150.020',target_distance:'0.020',quantity:'1000',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',calculation_context_id:'ctx-usdjpy',quote_created_at_ms:123,quote_valid_for_submit:true};
const fetch=async(url,opts={})=>{
  const body=opts.body?JSON.parse(opts.body):null;calls.push({url,method:opts.method||'GET',body});
  if(url==='/api/calculator/bootstrap')return response({app_profile:'render',trendline_plans_available:false,webhook:{available:true}});
  if(url.startsWith('/api/calculator/prewarm-account'))return response({ready_for_quote:true,asset:'crypto'});
  if(url.startsWith('/api/calculator/instrument?'))return response({asset:'fx',broker:'oanda',symbol:'USD_JPY'});
  if(url==='/api/calculator/quote'){quotePayload=body;return response(quote);}
  if(url==='/api/calculator/submit'){submitPayload=body;return response({ok:true,broker:'oanda',result:{order:{orderId:'test-order'}}});}
  if(url.startsWith('/api/calculator/journal-summary'))return response({status:'no_data'});
  if(url.startsWith('/api/instrument-specs'))return response({source:'oanda',resolved_symbol:'USD_JPY'});
  if(url.startsWith('/api/calculator/prewarm'))throw new Error('FX resolution must not prewarm Bybit quote data');
  return response({status:'no_data'});
};
let timerId=0;const timers=new Map();
const setTimeout=(fn,ms)=>{const id=++timerId;if(ms===250)fn();else timers.set(id,fn);return id;};
const clearTimeout=id=>timers.delete(id);const setInterval=()=>99;const clearInterval=()=>{intervalClears++;};
const context={document:{getElementById:id=>el[id]},fetch,navigator:{clipboard:{writeText:async()=>{}}},setTimeout,clearTimeout,setInterval,clearInterval,console,Date,URL,URLSearchParams,AbortController};
vm.runInNewContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();await flush();
  el['calc-symbol'].value='USDJPY';
  if(mode==='resolved') { el['calc-symbol'].listeners.input(); await flush(); await flush(); await flush(); }
  await el['calc-quote'].listeners.click();await flush();await flush();
  if(mode==='race') { await el['calc-submit'].listeners.click();await flush(); }
  const active=(id,v)=>!!el[id].buttons.find(b=>b.dataset.v===v)?.active;
  console.log(JSON.stringify({calls,quotePayload,submitPayload,canonical:el['calc-canonical-symbol'].textContent,assetFx:active('asset-toggle','fx'),brokerOanda:active('broker-toggle','oanda'),riskVisible:el['risk-toggle-wrap'].style.display!=='none',brokerVisible:el['broker-toggle-wrap'].style.display!=='none',intervalClears,submitEnabled:!el['calc-submit'].disabled}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", harness, str(JS_PATH), mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_usdjpy_default_crypto_input_auto_selects_fx_and_posts_oanda_quote() -> None:
    data = _run_usdjpy_auto_route_harness("resolved")
    assert data["assetFx"] and data["brokerOanda"]
    assert data["riskVisible"] and data["brokerVisible"]
    assert data["canonical"].endswith("USD_JPY")
    assert data["quotePayload"]["asset"] == "fx"
    assert data["quotePayload"]["broker"] == "oanda"
    assert data["quotePayload"]["symbol"] == "USD_JPY"
    urls = [call["url"] for call in data["calls"]]
    assert any("/api/calculator/instrument?asset=crypto&broker=bybit" in url for url in urls)
    assert any("/api/calculator/journal-summary?asset=fx&symbol=USD_JPY" in url for url in urls)
    assert any("/api/instrument-specs?query=USD_JPY&prefer=oanda" in url for url in urls)
    assert not any(call["url"] == "/api/calculator/prewarm" for call in data["calls"])
    assert data["intervalClears"] >= 1


def test_usdjpy_quote_response_reconciles_fx_identity_after_resolution_race() -> None:
    data = _run_usdjpy_auto_route_harness("race")
    assert data["quotePayload"]["asset"] == "crypto"
    assert data["quotePayload"]["broker"] == "bybit"
    assert data["quotePayload"]["symbol"] == "USDJPY"
    assert data["assetFx"] and data["brokerOanda"]
    assert data["canonical"].endswith("USD_JPY")
    assert data["submitPayload"]["asset"] == "fx"
    assert data["submitPayload"]["broker"] == "oanda"
    assert data["submitPayload"]["symbol"] == "USD_JPY"
    urls = [call["url"] for call in data["calls"]]
    assert any("/api/calculator/journal-summary?asset=fx&symbol=USD_JPY" in url for url in urls)
    assert any("/api/instrument-specs?query=USD_JPY&prefer=oanda" in url for url in urls)


def test_risk_toggle_posts_fixed_aud_payload_and_preserves_fx_selection() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

class MockElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.buttons = [];
    this.classList = {
      toggle: () => {},
      add: () => {},
      remove: () => {},
    };
  }
  addEventListener(evt, cb) {
    this.listeners[evt] = cb;
  }
  querySelectorAll(sel) {
    if (sel === 'button') return this.buttons;
    return [];
  }
}

class MockButton {
  constructor(v) {
    this.dataset = { v };
    this.listeners = {};
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} };
    this.disabled = false;
    this.title = '';
    this._attrs = {};
  }
  setAttribute(k,v){ this._attrs[k]=String(v); this[k] = v; }
  getAttribute(k){ return this._attrs[k]; }
  removeAttribute(k){ delete this._attrs[k]; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  click() { if (this.listeners.click) this.listeners.click(); }
}

const ids = [
  'calc-error', 'calc-error-debug', 'calc-success', 'calc-results', 'calc-request-summary',
  'calc-canonical-symbol', 'calc-journal-summary', 'calc-instrument-specs', 'risk-toggle-wrap',
  'calc-webhook-panel', 'calc-webhook-url', 'calc-webhook-json', 'calc-webhook-copy', 'calc-webhook-copy-url', 'risk-toggle', 'calc-risk-label',
  'limit-wrap', 'account-toggle', 'asset-toggle', 'side-toggle', 'order-toggle', 'webhook-toggle',
  'test-toggle', 'timeframe-toggle', 'calc-symbol', 'calc-limit', 'calc-sl-ticks', 'calc-rr',
  'calc-risk', 'calc-quote', 'calc-submit', 'calc-quote-status'
];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));

function makeToggleButtons(values) {
  return values.map((v) => new MockButton(v));
}

elements['risk-toggle'].buttons = makeToggleButtons(['fixed_aud', 'percent']);
elements['asset-toggle'].buttons = makeToggleButtons(['crypto', 'fx']);
elements['account-toggle'].buttons = makeToggleButtons(['live', 'demo']);
elements['side-toggle'].buttons = makeToggleButtons(['buy', 'sell']);
elements['order-toggle'].buttons = makeToggleButtons(['market', 'limit']);
elements['webhook-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['test-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['timeframe-toggle'].buttons = [];

elements['calc-risk'].value = '10';
elements['calc-symbol'].value = 'NZDUSD';
elements['calc-sl-ticks'].value = '35';
elements['calc-rr'].value = '2';

let lastQuotePayload = null;

global.fetch = async (url, opts = {}) => {
  if (url.includes('/api/calculator/quote')) {
    lastQuotePayload = JSON.parse(opts.body || '{}');
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({
        broker: 'oanda', symbol: 'NZD_USD', tick_size: '0.00001', entry_price: '0.6102', stop_price: '0.60985',
        target_price: '0.6109', target_distance: '0.00070', quantity: '1000', estimated_fees_or_spread: '1',
        estimated_total_loss: '10', estimated_reward: '20', display_currency: 'AUD'
      }),
    };
  }
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => 'application/json' },
    text: async () => JSON.stringify({ status: 'no_data', canonical_symbol: 'NZDUSD' }),
  };
};

global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};

// load script
// eslint-disable-next-line no-eval
eval(source);

const riskFixed = elements['risk-toggle'].buttons.find((b) => b.dataset.v === 'fixed_aud');
const assetCrypto = elements['asset-toggle'].buttons.find((b) => b.dataset.v === 'crypto');
const assetFx = elements['asset-toggle'].buttons.find((b) => b.dataset.v === 'fx');

riskFixed.click();
assetCrypto.click();
assetFx.click();
riskFixed.click();

if (elements['calc-quote'].listeners.click) {
  elements['calc-quote'].listeners.click();
}

setTimeout(() => {
  console.log(JSON.stringify({ payload: lastQuotePayload, summary: elements['calc-request-summary'].textContent }));
}, 0);
'''
    result = subprocess.run(
        [node, "-e", harness, str(JS_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    payload = data["payload"]
    assert payload["risk_mode"] == "fixed_aud"
    assert payload["broker"] == "oanda"
    assert str(payload["risk_value"]) == "10"
    assert "risk_mode=fixed_aud" in data["summary"]
    assert "risk_value=10" in data["summary"]
    assert "webhook=no" in data["summary"]
    assert "test=no" in data["summary"]
    assert "timeframe=15m" in data["summary"]
    assert "pending_webhook_id=" in data["summary"]


def test_fx_broker_toggle_is_hidden_for_crypto_and_supports_pepperstone_market_set() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class E {
  constructor(id) { this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; }
  addEventListener(e,cb){ this.listeners[e]=cb; }
  querySelectorAll(sel){ return sel==='button' ? this.buttons : []; }
}
class B {
  constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; this.title=''; }
  addEventListener(e,cb){ this.listeners[e]=cb; }
  click(){ if(this.listeners.click) this.listeners.click(); }
  setAttribute(k,v){ this._attrs[k]=String(v); }
  getAttribute(k){ return this._attrs[k]; }
  removeAttribute(k){ delete this._attrs[k]; }
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','broker-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','broker-toggle','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-pepperstone-set','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map((id)=>[id,new E(id)]));
const mk=(vals)=>vals.map((v)=>new B(v));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);
el['asset-toggle'].buttons=mk(['crypto','fx']);
el['broker-toggle'].buttons=mk(['oanda','pepperstone']);
el['account-toggle'].buttons=mk(['live','demo']);
el['side-toggle'].buttons=mk(['buy','sell']);
el['order-toggle'].buttons=mk(['market','limit']);
el['webhook-toggle'].buttons=mk(['no','yes']);
el['test-toggle'].buttons=mk(['no','yes']);
el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='EUR_USD';
el['calc-limit'].value='1.1002';
el['calc-sl-ticks'].value='35';
el['calc-rr'].value='2';
el['calc-risk'].value='10';
let quotePayload=null;
let setPayload=null;
global.fetch=async (url,opts={})=>{
  if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'abc123def456',render_calculator_base_url_configured:true,webhook:{available:true}})};
  if(url.includes('/api/calculator/quote')){ quotePayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'pepperstone',venue:'Pepperstone',resolved_venue:'Pepperstone',symbol:'EUR_USD',tick_size:'0.00001',entry_price:'1.1002',stop_price:'1.09985',target_price:'1.10125',target_distance:'0.00105',quantity:'1000',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_total_loss_aud:'10',estimated_reward:'20'})}; }
  if(url.includes('/api/calculator/pepperstone-set')){ setPayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:(k)=>k==='content-disposition'?'attachment; filename="Pepperstone_Trader_EUR_USD_BUY_MARKET_x.set"':'text/plain'},text:async()=> 'Strategy=3\nStandardMarketSide=0\nStandardMarketExecutionToken=mkt_test\n'}; }
  return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};
};
global.document={getElementById:(id)=>el[id]};
global.navigator={clipboard:{writeText:async()=>{}}};
global.setTimeout=(fn)=>{ fn(); return 1; };
global.clearTimeout=()=>{};
global.setInterval=()=>1;
global.clearInterval=()=>{};
eval(source);
(async()=>{
  await Promise.resolve(); await Promise.resolve();
  const hiddenOnCrypto = el['broker-toggle-wrap'].style.display === 'none';
  el['asset-toggle'].buttons.find((b)=>b.dataset.v==='fx').click();
  const visibleOnFx = el['broker-toggle-wrap'].style.display === '';
  el['broker-toggle'].buttons.find((b)=>b.dataset.v==='pepperstone').click();
  await el['calc-quote'].listeners.click();
  await el['calc-pepperstone-set'].listeners.click();
  console.log(JSON.stringify({hiddenOnCrypto,visibleOnFx,broker:quotePayload?.broker,setPayload,setButton:el['calc-pepperstone-set'].style.display,submit:el['calc-submit'].style.display,summary:el['calc-request-summary'].textContent}));
})();
'''
    out = subprocess.check_output([node, "-e", harness, str(JS_PATH)], text=True)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["hiddenOnCrypto"] is True
    assert data["visibleOnFx"] is True
    assert data["broker"] == "pepperstone"
    assert data["setPayload"]["order_type"] == "market"
    assert "entry_price" not in data["setPayload"]
    assert data["setButton"] == ""
    assert data["submit"] == "none"
    assert "broker=pepperstone" in data["summary"]
    assert "venue=Pepperstone" in data["summary"]


def test_submit_visibility_tracks_quote_validity_and_webhook_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

class MockElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.buttons = [];
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} };
  }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  querySelectorAll(sel) { return sel === 'button' ? this.buttons : []; }
}
class MockButton {
  constructor(v) { this.dataset = { v }; this.listeners = {}; this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  click() { if (this.listeners.click) this.listeners.click(); }
}

const ids = [
  'calc-error', 'calc-error-debug', 'calc-success', 'calc-results', 'calc-request-summary',
  'calc-canonical-symbol', 'calc-journal-summary', 'calc-instrument-specs', 'risk-toggle-wrap',
  'calc-webhook-panel', 'calc-webhook-url', 'calc-webhook-json', 'calc-webhook-copy', 'calc-webhook-copy-url',
  'risk-toggle', 'calc-risk-label', 'limit-wrap', 'account-toggle', 'asset-toggle', 'side-toggle',
  'order-toggle', 'webhook-toggle', 'test-toggle', 'timeframe-toggle', 'calc-symbol', 'calc-limit',
  'calc-sl-ticks', 'calc-rr', 'calc-risk', 'calc-quote', 'calc-submit', 'calc-quote-status', 'calc-webhook-status'
];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));

function makeToggleButtons(values) { return values.map((v) => new MockButton(v)); }
elements['risk-toggle'].buttons = makeToggleButtons(['fixed_aud', 'percent']);
elements['asset-toggle'].buttons = makeToggleButtons(['crypto', 'fx']);
elements['account-toggle'].buttons = makeToggleButtons(['live', 'demo']);
elements['side-toggle'].buttons = makeToggleButtons(['buy', 'sell']);
elements['order-toggle'].buttons = makeToggleButtons(['market', 'limit']);
elements['webhook-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['test-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['timeframe-toggle'].buttons = [];
elements['calc-symbol'].value = 'BTCUSDT';
elements['calc-limit'].value = '0';
elements['calc-sl-ticks'].value = '10';
elements['calc-rr'].value = '2';
elements['calc-risk'].value = '1';

let nextQuoteShouldFail = false;
global.fetch = async (url, opts = {}) => {
  if (url.includes('/api/calculator/quote')) {
    if (nextQuoteShouldFail) {
      nextQuoteShouldFail = false;
      return { ok: false, status: 500, statusText: 'bad', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ detail: 'boom' }) };
    }
    return {
      ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({
        broker: 'bybit', symbol: 'BTCUSDT', tick_size: '0.10', entry_price: '60000', stop_price: '59900',
        target_price: '60200', target_distance: '200', quantity: '1', estimated_fees_or_spread: '1',
        estimated_total_loss: '10', estimated_reward: '20', display_currency: 'AUD',
        webhook_payload_json: '{"a":1}', pending_webhook_id: 'pid-1', webhook_endpoint_url: 'https://example.com'
      }),
    };
  }
  return { ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ status: 'no_data' }) };
};
global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};
eval(source);

const submit = elements['calc-submit'];
const quoteClick = elements['calc-quote'].listeners.click;
const webhookYes = elements['webhook-toggle'].buttons.find((b) => b.dataset.v === 'yes');
const webhookNo = elements['webhook-toggle'].buttons.find((b) => b.dataset.v === 'no');

(async () => {
  const states = [];
  states.push(submit.style.display === 'none');
  let p1 = quoteClick();
  states.push(submit.style.display === '' && submit.disabled === true);
  await p1;
  states.push(submit.style.display === '' && submit.disabled === false);
  states.push(elements['calc-quote'].disabled === false && elements['calc-quote'].textContent !== 'Calculating…');
  elements['calc-risk'].value = '2';
  elements['calc-risk'].listeners.input();
  states.push(submit.style.display === '' && submit.disabled === true);
  p1 = quoteClick();
  states.push(submit.style.display === '' && submit.disabled === true);
  await p1;
  states.push(submit.style.display === '' && submit.disabled === false);
  elements['calc-sl-ticks'].value = '20';
  elements['calc-sl-ticks'].listeners.change();
  states.push(submit.style.display === '' && submit.disabled === true);
  nextQuoteShouldFail = true;
  await quoteClick();
  states.push(submit.disabled === true);
  states.push(elements['calc-quote'].disabled === false && elements['calc-quote'].textContent !== 'Calculating…');
  webhookYes.click();
  await quoteClick();
  states.push(submit.style.display === 'none');
  webhookNo.click();
  await quoteClick();
  states.push(submit.style.display === '');
  console.log(JSON.stringify(states));
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    states = json.loads(result.stdout.strip().splitlines()[-1])
    assert states == [True, True, True, True, True, True, True, True, True, True, True, True]


def test_webhook_copy_button_no_reference_error() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class MockElement {
  constructor(id) { this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  querySelectorAll(sel) { return sel === 'button' ? this.buttons : []; }
}
class MockButton {
  constructor(v) { this.dataset = { v }; this.listeners = {}; this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
}
const ids = ['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status'];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));
for (const id of ['risk-toggle','asset-toggle','account-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle']) {
  elements[id].buttons = [new MockButton('no'), new MockButton('yes')];
}
elements['calc-webhook-json'].textContent = '{"a":1}';
global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ status: 'no_data' }) });
global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};
eval(source);
(async () => {
  await elements['calc-webhook-copy'].listeners.click();
  console.log('ok');
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_webhook_disabled_from_bootstrap_blocks_yes_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(sel){return sel==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v);} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quotePayloads=[];
global.fetch=async (url,opts={})=>{ if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({webhook:{available:false,unavailable_message:'blocked',webhook_origin_host:'127.0.0.1'}})}; if(url.includes('/api/calculator/quote')){ quotePayloads.push(JSON.parse(opts.body||'{}')); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})}; } return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(fn)=>{fn();return 1;}; global.clearTimeout=()=>{};
eval(source);
(async()=>{ await Promise.resolve(); const yes=el['webhook-toggle'].buttons[1]; yes.click(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({yesDisabled:yes.disabled,payloadWebhook:quotePayloads[0]?.webhook,panel:el['calc-webhook-panel'].style.display,status:el['calc-webhook-status'].textContent})); })();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["yesDisabled"] is True
    assert data["payloadWebhook"] == "no"
    assert data["panel"] == "none"
    assert "blocked" in data["status"].lower()
    assert "build:" not in data["status"].lower()


def test_webhook_enabled_from_bootstrap_allows_yes_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(sel){return sel==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v);} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quotePayload;
global.fetch=async (url,opts={})=>{ if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'abc123def456',render_calculator_base_url_configured:true,webhook:{available:true}})}; if(url.includes('/api/calculator/quote')){ quotePayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',webhook_payload_json:'{\"a\":1}',pending_webhook_id:'pid-1',webhook_endpoint_url:'https://example.test/api/calculator/webhook'})}; } return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(fn)=>{fn();return 1;}; global.clearTimeout=()=>{};
eval(source);
 (async()=>{ await Promise.resolve(); await Promise.resolve(); el['webhook-toggle'].buttons[1].disabled=false; el['webhook-toggle'].buttons[1].removeAttribute('aria-disabled'); el['webhook-toggle'].buttons[1].click(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({webhook:quotePayload?.webhook,panel:el['calc-webhook-panel'].style.display,url:el['calc-webhook-url'].textContent,json:el['calc-webhook-json'].textContent,status:el['calc-webhook-status'].textContent})); })();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["webhook"] == "yes"
    assert data["panel"] == ""
    assert "https://example.test" in data["url"]
    assert data["status"] == ""


def test_webhook_stale_runtime_warning_on_old_public_webhook_message() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}setAttribute(k,v){this._attrs[k]=v;}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);el['timeframe-toggle'].buttons=[];global.fetch=async (url)=>url.includes('/api/calculator/bootstrap')?{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'deadbeef1234',render_calculator_base_url_configured:false,webhook:{available:false,unavailable_message:'TradingView webhook is unavailable on localhost unless you use Render or set PUBLIC_WEBHOOK_BASE_URL to a public same-instance tunnel URL.'}})}:{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);(async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();console.log(JSON.stringify({status:el['calc-webhook-status'].textContent,yesDisabled:el['webhook-toggle'].buttons[1].disabled}));})();'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["yesDisabled"] is True
    assert "Stale local server code detected" in data["status"]


def test_abort_error_message_fallback_present() -> None:
    script = JS_PATH.read_text(encoding="utf-8")
    assert "Quote timed out after 15s" in script

def test_render_quote_shows_tp_auto_adjustment() -> None:
    node = shutil.which('node')
    assert node
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E{constructor(){this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,cb){this.listeners[e]=cb;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,cb){this.listeners[e]=cb;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E()]));
const mk=(vals)=>vals.map(v=>new B(v)); el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url,opts={})=> url.includes('/api/calculator/quote')?{ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'0.1',entry_price:'95',stop_price:'94',target_price:'100.1',target_distance:'5.1',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',take_profit_adjusted:true,take_profit_adjustment:{original_take_profit:'96',adjusted_take_profit:'100.1',reason:'bybit_last_price_trigger_side',last_price:'100'},warnings:['Take profit was auto-adjusted to satisfy Bybit LastPrice trigger rules.']})}:{ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({})};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click(); console.log(el['calc-results'].innerHTML);})();
'''
    out = subprocess.run([node, '-e', harness, str(JS_PATH)], check=True, capture_output=True, text=True).stdout
    assert 'TP auto-adjusted' in out

def test_submit_payload_summary_includes_submit_levels() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-risk'].value='1';el['calc-sl-ticks'].value='1999';el['calc-rr'].value='1';
let submitted=null;
let broadcasts=[];let storageEvents=[];
global.BroadcastChannel=class{constructor(name){this.name=name;}postMessage(msg){broadcasts.push({name:this.name,msg});}close(){}};
global.localStorage={setItem:(key,value)=>storageEvents.push({key,value:JSON.parse(value)})};
global.fetch=async (url,opts={})=>{if(url.includes('/quote'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',venue:'Bybit',resolved_venue:'Bybit',symbol:'BTCUSDT',entry_price:'79300',stop_price:'78784.5',target_price:'79669',quantity:'0.012',calculation_context_id:'ctx1',quote_created_at_ms:123})};if(url.includes('/submit')){submitted=JSON.parse(opts.body||'{}');return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ok:true,broker:'bybit',venue:'Bybit',resolved_venue:'Bybit'})};}return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();await el['calc-submit'].listeners.click();console.log(JSON.stringify({submitted,summary:el['calc-request-summary'].textContent,broadcasts,storageEvents}));})();
'''
    out = subprocess.run([node, '-e', harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data['submitted']['planned_entry_price'] == '79300'
    assert data['submitted']['stop_loss_price'] == '78784.5'
    assert data['submitted']['broker'] == 'bybit'
    assert 'planned_entry_price=79300' in data['summary']
    assert 'take_profit_price=79669' in data['summary']
    assert 'broker=bybit' in data['summary']
    assert 'venue=Bybit' in data['summary']
    assert data['broadcasts'][0]['name'] == 'trading-tools-open-orders'
    assert data['broadcasts'][0]['msg']['type'] == 'state-changed'
    assert data['broadcasts'][0]['msg']['action'] == 'submit'
    assert data['storageEvents'][0]['key'] == 'trading-tools-open-orders-event'


def test_crypto_calculate_attempts_quote_when_prewarm_wallet_unavailable() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
let quoteCalls=0;
global.fetch=async (url,opts={})=>{if(url.includes('/instrument'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})};if(url.includes('/prewarm'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'retCode=10003 retMsg=invalid key'})};if(url.includes('/quote')){quoteCalls++;return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'0.1',entry_price:'100',stop_price:'99',target_price:'102',target_distance:'2',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};}return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({quoteCalls,status:el['calc-quote-status'].textContent,err:el['calc-error'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert "please wait for wallet/ticker prewarm" not in (data["status"] + data["err"])


def test_crypto_quote_failure_shows_backend_wallet_error_not_prewarm_gate() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC'; el['calc-sl-ticks'].value='5'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quoteCalls=0;
global.fetch=async (url,opts={})=>{ if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'retCode=10003'})}; if(url.includes('/quote')){quoteCalls++; return {ok:false,status:502,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'QUOTE_FAILED',message:'wallet down',debug:{dependency:'bybit_wallet_balance',path:'/v5/account/wallet-balance'}}})}; } return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input(); await Promise.resolve(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({quoteCalls,err:el['calc-error'].textContent,dbg:el['calc-error-debug'].textContent,status:el['calc-quote-status'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert ("wallet down" in data["err"]) or ("bybit_wallet_balance" in (data["err"] + data["dbg"]))
    assert "please wait for wallet/ticker prewarm" not in (data["err"] + data["dbg"])


def test_stale_prewarm_response_is_ignored_after_symbol_or_account_change() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
let quoteCalls=0; let prewarmResolver=null;
global.fetch=async (url,opts={})=>{if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:()=>new Promise((resolve)=>{prewarmResolver=resolve;})}; if(url.includes('/quote')){quoteCalls++; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'ETHUSDT',tick_size:'0.1',entry_price:'100',stop_price:'99',target_price:'102',target_distance:'2',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};} return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{el['calc-symbol'].listeners.input();await Promise.resolve();el['account-toggle'].buttons.find((b)=>b.dataset.v==='live').click();if(prewarmResolver)prewarmResolver(JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'stale'}));await Promise.resolve();el['calc-symbol'].value='ETH';el['calc-symbol'].listeners.input();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({quoteCalls,status:el['calc-quote-status'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert "stale" not in data["status"].lower()

def test_bybit_expired_demo_key_message_is_actionable() -> None:
    node = shutil.which("node"); assert node
    harness = r'''
const fs=require('fs'); const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(){this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,c){this.listeners[e]=c;}click(){this.listeners.click&&this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E()])); const mk=(v)=>v.map(x=>new B(x)); el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url)=> url.includes('/quote')?{ok:false,status:502,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_API_KEY_EXPIRED',account:'demo',message:'x',debug:{retCode:33004}}})}:{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(fn)=>{fn();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();console.log(el['calc-error'].textContent)})();'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True).stdout
    assert "Bybit Demo API key expired" in out


def test_bybit_expired_live_key_message_is_actionable() -> None:
    assert "BYBIT_API_KEY1/BYBIT_API_SECRET1" in JS_PATH.read_text(encoding='utf-8')


def test_instrument_specs_loading_does_not_remain_forever_after_failure() -> None:
    assert "Instrument specs unavailable for" in JS_PATH.read_text(encoding='utf-8')


def test_journal_summary_default_prompt_replaced_after_resolved_symbol_failure() -> None:
    assert "Journal summary unavailable for" in JS_PATH.read_text(encoding='utf-8')


def test_expired_key_error_does_not_reintroduce_prewarm_gate() -> None:
    assert "please wait for wallet/ticker prewarm" not in JS_PATH.read_text(encoding='utf-8').lower()


def test_render_error_debug_escapes_html_values() -> None:
    script = JS_PATH.read_text(encoding="utf-8")
    assert "replace(/</g, '&lt;')" in script
    assert "replace(/>/g, '&gt;')" in script
    assert "escapeHtml(k)" in script


def test_render_error_debug_escapes_runtime_html_payload() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='1111';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url)=>{ if(url.includes('/quote')) return {ok:false,status:502,statusText:'bad',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED',message:'save failed',debug:{'<img src=x onerror=alert(1)>':{value:'<script>x</script>'}}}})}; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; };
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();const out=el['calc-error-debug'].innerHTML;console.log(JSON.stringify({html:out,hasRawImg:out.includes('<img'),hasRawScript:out.includes('<script'),hasEscapedImg:out.includes('&lt;img'),hasEscapedScript:out.includes('&lt;script&gt;')}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["hasRawImg"] is False
    assert data["hasRawScript"] is False
    assert data["hasEscapedImg"] is True
    assert data["hasEscapedScript"] is True


def test_quote_failure_overwrites_stale_prewarm_ready_status_and_renders_debug() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='1111';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url,opts={})=>{if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ready_for_quote:true,asset:'crypto',account:'demo',symbol:'BTCUSDT'})}; if(url.includes('/quote')) return {ok:false,status:502,statusText:'bad',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED',message:'save failed',debug:{nested:{reason:'disk locked'}}}})}; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({status:el['calc-quote-status'].textContent,error:el['calc-error'].textContent,debug:el['calc-error-debug'].innerHTML}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert "Quote data ready" not in data["status"]
    assert "save failed" in data["error"] or "BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED" in data["error"]
    assert "disk locked" in data["debug"] or "nested" in data["debug"]

def test_submit_button_visual_states_and_duplicate_blocking() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this.title=''; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(s){return s==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; this.title=''; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v); this[k]=v;} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];} }
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));
const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let mode='success'; let submitCalls=0; let resolveSubmit;
const submitPromise = () => new Promise((r)=>{ resolveSubmit = r; });
let gate = null;
let quoteSeq = 0;
global.fetch=async (url,opts={})=>{ if(url.includes('/quote')) { quoteSeq += 1; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',entry_price:String(79300 + quoteSeq),stop_price:String(78784.5 + quoteSeq),target_price:String(79669 + quoteSeq),quantity:'0.012',calculation_context_id:'ctx'+String(quoteSeq),quote_created_at_ms:123 + quoteSeq})}; } if(url.includes('/submit')){ submitCalls += 1; if(mode==='success'){ gate = submitPromise(); await gate; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ok:true})}; } return {ok:false,status:400,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:'fail'})}; } return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(f)=>{f();return 1;}; global.clearTimeout=()=>{};
eval(source);
(async()=>{
  await el['calc-quote'].listeners.click();
  const p1 = el['calc-submit'].listeners.click();
  const submittingState = el['calc-submit'].dataset.submitVisualState;
  const submittingText = el['calc-submit'].textContent;
  const p2 = el['calc-submit'].listeners.click();
  const p3 = el['calc-submit'].listeners.click();
  const submitCallsWhilePending = submitCalls;
  const pendingStateAfterDuplicates = el['calc-submit'].dataset.submitVisualState || '';
  const pendingTextAfterDuplicates = el['calc-submit'].textContent;
  const pendingErrorAfterDuplicates = el['calc-error'].textContent;
  el['calc-risk'].value = '2';
  el['calc-risk'].listeners.input();
  const staleClearsVisualState = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order';
  await el['calc-quote'].listeners.click();
  await el['calc-submit'].listeners.click();
  const submitCallsAfterInvalidateRecalc = submitCalls;
  resolveSubmit();
  await p1; await p2; await p3;
  const successState = el['calc-submit'].dataset.submitVisualState || '';
  el['calc-risk'].listeners.input();
  const clearedAfterInvalidate = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order';
  await el['calc-quote'].listeners.click();
  mode='fail';
  await el['calc-submit'].listeners.click();
  const failCleared = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order' && !!el['calc-error'].textContent;
  el['calc-submit'].disabled = true;
  await el['calc-submit'].listeners.click();
  const disabledNoHighlight = !el['calc-submit'].dataset.submitVisualState;
  console.log(JSON.stringify({submittingState,submittingText,submitCallsWhilePending,pendingStateAfterDuplicates,pendingTextAfterDuplicates,pendingErrorAfterDuplicates,staleClearsVisualState,submitCallsAfterInvalidateRecalc,successState,submitCalls,clearedAfterInvalidate,failCleared,disabledNoHighlight}));
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["submittingState"] == "submitting"
    assert data["submittingText"] == "Submitting..."
    assert data["submitCallsWhilePending"] == 1
    assert data["pendingStateAfterDuplicates"] == "submitting"
    assert data["pendingTextAfterDuplicates"] == "Submitting..."
    assert data["pendingErrorAfterDuplicates"] == ""
    assert data["staleClearsVisualState"] is True
    assert data["submitCallsAfterInvalidateRecalc"] == 1
    assert data["submitCalls"] == 2
    assert data["successState"] == "success"
    assert data["clearedAfterInvalidate"] is True
    assert data["failCleared"] is True
    assert data["disabledNoHighlight"] is True


def test_pattern_selector_buttons_invalidate_quote_and_payloads_include_pattern() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    assert "id=\"pattern-toggle\"" not in source  # UI container is server-rendered.
    assert "function setPatternButtons()" in source
    pattern_block = source.split("function setPatternButtons()", 1)[1].split("async function resolveSymbolAndLoad", 1)[0]
    assert "['','None']" in pattern_block
    assert "['range','range']" in pattern_block
    assert "['channel','channel']" in pattern_block
    assert "invalidateQuote()" in pattern_block
    assert source.count("pattern: state.pattern") >= 2
    assert "`pattern=${state.pattern || payload.pattern || ''}`" in source


def test_quality_criteria_buttons_invalidate_quote_and_are_in_both_payloads() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    for function_name, state_field, container_id in (
        ("setEmaButtons", "ema", "ema-toggle"),
        ("setVwapButtons", "vwap", "vwap-toggle"),
        ("setAthsAtlsButtons", "aths_atls", "aths-atls-toggle"),
        ("setRoundNumberButtons", "round_number", "round-number-toggle"),
    ):
        assert f"function {function_name}()" in source
        block = source.split(f"function {function_name}()", 1)[1].split("\n  function ", 1)[0]
        assert f"$('{container_id}')" in block
        assert "invalidateQuote()" in block
        assert source.count(f"{state_field}: state.{state_field}") >= 2
        assert f"`{state_field}=${{state.{state_field} || payload.{state_field} || ''}}`" in source
    assert "['All-Time High','All-Time High']" in source
    assert "['All-Time Low','All-Time Low']" in source


def test_vwap_toggle_posts_payload_and_invalidates_ready_quote() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this._innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}set innerHTML(v){this._innerHTML=String(v||'');this.buttons=[...this._innerHTML.matchAll(/data-v="([^"]*)"/g)].map((m)=>new B(m[1]));}get innerHTML(){return this._innerHTML;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','vwap-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['broker-toggle'].buttons=mk(['oanda','pepperstone']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
const payloads=[];
global.fetch=async (url,opts={})=>{if(url.includes('/api/calculator/quote')){payloads.push(JSON.parse(opts.body||'{}'));return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};}return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};global.setInterval=()=>1;global.clearInterval=()=>{};
eval(source);
(async()=>{el['vwap-toggle'].buttons.find((b)=>b.dataset.v==='Yes').click();await el['calc-quote'].listeners.click();const ready=el['calc-quote-status'].textContent;el['vwap-toggle'].buttons.find((b)=>b.dataset.v==='No').click();const stale=el['calc-quote-status'].textContent;await el['calc-quote'].listeners.click();console.log(JSON.stringify({payloads,ready,stale,summary:el['calc-request-summary'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["payloads"][0]["vwap"] == "Yes"
    assert data["payloads"][1]["vwap"] == "No"
    assert data["ready"] == "Quote ready."
    assert "Quote changed" in data["stale"]
    assert "vwap=No" in data["summary"]


def test_local_trendline_plan_ui_saves_manual_anchors_and_manages_status() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
process.env.TZ='Australia/Brisbane'; const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class B{constructor(v,a){this.dataset=a?{tlAction:a,tlId:v}:{v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){return this.listeners.click&&this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
class E{constructor(i){this.id=i;this.value='';this.textContent='';this._innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.actions=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this.checked=false;}set innerHTML(v){this._innerHTML=String(v||'');this.buttons=[...this._innerHTML.matchAll(/data-v="([^"]*)"/g)].map(m=>new B(m[1]));this.actions=[...this._innerHTML.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)].map(m=>new B(m[2],m[1]));}get innerHTML(){return this._innerHTML;}addEventListener(e,c){this.listeners[e]=c;}click(){return this.listeners.click&&this.listeners.click();}querySelectorAll(s){if(s==='button')return this.buttons;if(s==='[data-tl-action]')return this.actions;return [];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=v=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['broker-toggle'].buttons=mk(['oanda','pepperstone']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';el['trendline-right-extension'].checked=true;el['trendline-trigger-mode'].value='touch';el['trendline-cross-direction'].value='upward';el['trendline-price-basis'].value='executable';el['trendline-tolerance-ticks'].value='2';el['trendline-expiry'].value='lifetime';
let plans=[];let calls=[];let confirms=0;let allowConfirm=false;global.confirm=()=>{confirms++;return allowConfirm;};
const reply=o=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(o)});
global.fetch=async(url,opts={})=>{calls.push([url,opts.method||'GET',opts.body]);if(url.includes('/api/calculator/bootstrap'))return reply({app_profile:'local',trendline_plans_available:true,webhook:{available:true}});if(url.includes('/api/calculator/instrument'))return reply({symbol:'BTCUSDT'});if(url==='/api/trendline-plans'&&(opts.method||'GET')==='GET')return reply({ok:true,plans,monitoring_active:false,execution_enabled:false});if(url==='/api/trendline-plans'&&opts.method==='POST'){const p=JSON.parse(opts.body);const plan={...p,plan_id:'plan123456',status:'draft',anchors:p.anchors};plans=[plan];return reply({ok:true,plan,monitoring_active:false,execution_enabled:false});}if(url.includes('/arm')){plans[0]={...plans[0],status:'armed'};return reply({ok:true,plan:plans[0]});}if(url.includes('/cancel')){plans[0]={...plans[0],status:'cancelled'};return reply({ok:true,plan:plans[0]});}if(url.includes('/plan123456')&&opts.method==='PATCH'){plans[0]={...plans[0],...JSON.parse(opts.body)};return reply({ok:true,plan:plans[0]});}if(url.includes('/api/calculator/submit')||url.includes('webhook')||url.includes('/api/calculator/quote'))throw new Error('unexpected calculator/broker request');return reply({status:'no_data'});};
global.document={getElementById:id=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(fn)=>{fn();return 1;};global.clearTimeout=()=>{};global.setInterval=()=>1;global.clearInterval=()=>{};
eval(source);
(async()=>{await Promise.resolve();await Promise.resolve();el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await Promise.resolve();el['trendline-anchor-1-time'].value='2025-01-02T03:04';el['trendline-anchor-2-time'].value='2025-01-02T04:04';el['trendline-anchor-1-price'].value='100';el['trendline-anchor-2-price'].value='101';el['trendline-anchor-1-time'].listeners.input();el['trendline-anchor-2-time'].listeners.input();const utcPreview=el['trendline-anchor-1-utc'].textContent;el['trendline-trigger-mode'].listeners.change();const first=el['trendline-save'].click();const duplicate=el['trendline-save'].click();await first;await duplicate;const actions=()=>el['trendline-plan-list'].querySelectorAll('[data-tl-action]');const action=(name)=>{const button=actions().find(b=>b.dataset.tlAction===name);if(!button)throw new Error(`Expected rendered ${name} action; list=${el['trendline-plan-list'].innerHTML}`);return button;};await action('edit').click();el['trendline-tolerance-ticks'].value='3';await el['trendline-save'].click();await action('arm').click();allowConfirm=true;await action('arm').click();await action('cancel').click();console.log(JSON.stringify({panel:el['trendline-plans-panel'].style.display,utc1:utcPreview,postCalls:calls.filter(c=>c[0]==='/api/trendline-plans'&&c[1]==='POST').length,postPayload:JSON.parse(calls.find(c=>c[0]==='/api/trendline-plans'&&c[1]==='POST')[2]),patch:calls.some(c=>c[1]==='PATCH'),armCalls:calls.filter(c=>c[0].includes('/arm')).length,cancelCalls:calls.filter(c=>c[0].includes('/cancel')).length,confirms,status:plans[0].status,noUnexpected:!calls.some(c=>c[0].includes('/api/calculator/submit')||c[0].includes('/webhook')||c[0].includes('/api/calculator/quote'))}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["panel"] == ""
    assert "2025-01-01T17:04:00.000Z" in data["utc1"]
    assert data["postCalls"] == 1
    assert data["postPayload"]["instrument"] == "BTCUSDT"
    assert data["postPayload"]["cross_direction"] == "either"
    assert data["patch"] and data["armCalls"] == 1 and data["cancelCalls"] == 1
    assert data["confirms"] == 2 and data["status"] == "cancelled" and data["noUnexpected"]


def test_trendline_monitor_ui_failures_live_arm_and_limit_round_trip() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
class Button {
  constructor(v, action) { this.dataset=action?{tlAction:action,tlId:v}:{v}; this.listeners={}; this.disabled=false; this.attrs={}; this.classList={toggle(){},add(){},remove(){}}; }
  addEventListener(e,f){this.listeners[e]=f;}
  click(){if(!this.disabled && this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];}
}
class Element extends Button {
  constructor(id){super('');this.id=id;this.value='';this.style={};this.textContent='';this.checked=false;this.buttons=[];this.actions=[];}
  set innerHTML(v){this.html=String(v);this.buttons=[...this.html.matchAll(/data-v="([^"]*)"/g)].map(m=>new Button(m[1]));this.actions=[...this.html.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)].map(m=>new Button(m[2],m[1]));}
  get innerHTML(){return this.html||'';}
  querySelectorAll(s){return s==='button'?this.buttons:s==='[data-tl-action]'?this.actions:[];}
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function run(profile){
 const el=Object.fromEntries(ids.map(id=>[id,new Element(id)])), calls=[];
 const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
 for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
 Object.assign(el['calc-symbol'],{value:'BTCUSDT'});el['calc-risk'].value='1';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';
 let plans=[],confirmed=false,running=false,fail=false,changeOnFailure=false;
 const response=(data,status=200)=>({ok:status<400,status,statusText:'mock',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
 const fetch=async(url,opts={})=>{
  const method=opts.method||'GET';calls.push({url,method,body:opts.body?JSON.parse(opts.body):null});
  if(url==='/api/calculator/bootstrap')return response({app_profile:profile,trendline_plans_available:profile==='local',trendline_monitoring:{running:false},webhook:{available:true}});
  if(url.startsWith('/api/calculator/instrument?'))return response({symbol:'BTCUSDT'});
  if(url==='/api/trendline-plans/monitor/status')return response({ok:true,running});
  if(url.startsWith('/api/trendline-plans/monitor/')){
   if(fail){if(changeOnFailure)running=!running;return response({detail:'mock failure'},503);}
   running=url.endsWith('/start');return response({ok:true,running});
  }
  if(url==='/api/trendline-plans' && method==='GET')return response({ok:true,plans});
  if(url==='/api/trendline-plans' && method==='POST'){
   const p=JSON.parse(opts.body);plans=[{...p,plan_id:'plan-1',status:'draft',order_intent:p.order_type,action:p.side}];return response({ok:true,plan:plans[0]});
  }
  if(url==='/api/trendline-plans/plan-1' && method==='PATCH'){
   plans[0]={...plans[0],...JSON.parse(opts.body)};return response({ok:true,plan:plans[0]});
  }
  if(url==='/api/trendline-plans/plan-1/arm'){plans[0].status='armed';return response({ok:true,plan:plans[0]});}
  throw new Error('Unexpected request '+url);
 };
 // A VM does not inherit browser/Node web globals. Symbol resolution uses
 // AbortController before its first awaited fetch, just as in the browser.
 vm.runInNewContext(source,{document:{getElementById:id=>el[id]},fetch,confirm:()=>confirmed,navigator:{clipboard:{writeText:async()=>{}}},setTimeout:fn=>{fn();return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},console,Date,URL,URLSearchParams,AbortController});
 await flush();
 if(profile!=='local'){assert.equal(el['trendline-plans-panel'].style.display,'none');assert(!calls.some(c=>c.url.startsWith('/api/trendline-plans')));return;}
 assert.equal(el['trendline-plans-panel'].style.display,'');
 el['calc-symbol'].listeners.input();await flush();
 assert(el['calc-canonical-symbol'].textContent.includes('BTCUSDT'),'Canonical resolution must complete before saving');
 el['order-toggle'].buttons.find(b=>b.dataset.v==='limit').click();
 el['calc-limit'].value='99.25';el['trendline-anchor-1-time'].value='2025-01-01T01:00';el['trendline-anchor-2-time'].value='2025-01-01T02:00';el['trendline-anchor-1-price'].value='100';el['trendline-anchor-2-price'].value='101';
 el['trendline-price-basis'].value='executable';
 const save=el['trendline-save'].click();el['trendline-save'].click();await save;
 assert.equal(calls.filter(c=>c.method==='POST' && c.url==='/api/trendline-plans').length,1);
 assert.equal(plans[0].limit_entry_price,'99.25');assert.equal(plans[0].order_intent,'limit');
 const action=name=>{const b=el['trendline-plan-list'].actions.find(b=>b.dataset.tlAction===name);assert(b,'Missing action '+name);return b;};
 el['order-toggle'].buttons.find(b=>b.dataset.v==='market').click();el['calc-limit'].value='123';
 await action('edit').click();assert.equal(el['calc-limit'].value,'99.25');await el['trendline-save'].click();
 const patch=calls.find(c=>c.method==='PATCH');assert.equal(patch.body.limit_entry_price,'99.25');assert.equal(patch.body.order_type,'limit');
 await action('arm').click();assert(!calls.some(c=>c.url.endsWith('/arm')));
 confirmed=true;await action('arm').click();assert.deepEqual(calls.find(c=>c.url.endsWith('/arm')).body,{confirm_live_execution:true});
 assert(!calls.some(c=>c.url.includes('/monitor/') && c.method==='POST'));
 const controls=(startDisabled,stopDisabled)=>{assert.equal(el['trendline-monitor-start'].disabled,startDisabled);assert.equal(el['trendline-monitor-stop'].disabled,stopDisabled);};
 controls(false,true);
 const start=el['trendline-monitor-start'].click();el['trendline-monitor-start'].click();await start;controls(true,false);
 assert.equal(calls.filter(c=>c.url.endsWith('/monitor/start')).length,1);
 await el['trendline-monitor-stop'].click();controls(false,true);
 fail=true;await el['trendline-monitor-start'].click();controls(false,true);assert(el['trendline-plan-status'].textContent.includes('failed'));
 fail=false;await el['trendline-monitor-start'].click();controls(true,false);
 fail=true;await el['trendline-monitor-stop'].click();controls(true,false);
 changeOnFailure=true;await el['trendline-monitor-stop'].click();controls(false,true);
 assert(calls.filter(c=>c.url.endsWith('/monitor/status')).length===3);
 assert(!calls.some(c=>c.url.includes('/quote')||c.url.includes('/submit')||c.url.includes('webhook')));
}
(async()=>{await run('render');await run('local');console.log('ok');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_trendline_reconciliation_warning_is_visible() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
// Execute the production renderer unchanged, isolated from application startup.
const start=source.indexOf('  function renderTrendlineMonitor(status) {');
const end=source.indexOf('  async function trendlineMonitor(action)',start);
assert(start>=0 && end>start,'Production monitor renderer must exist');
const elements=Object.fromEntries(['trendline-monitor-status','trendline-monitor-start','trendline-monitor-stop'].map(id=>[id,{textContent:'',disabled:false}]));
let requests=0;
const context=vm.createContext({$:id=>elements[id],state:{trendlineMonitorPending:false},fetch:()=>{requests++;throw new Error('No request allowed');}});
vm.runInContext(source.slice(start,end),context);
for(const running of [false,true]) {
  for(const count of [0,1,3,null]) {
    const message=count===null?'Reconciliation status unknown: registry unavailable. Automatic retry remains disabled.':count?'Manual broker reconciliation required; automatic retry is disabled.':null;
    context.renderTrendlineMonitor({running,reconciliation_required:count,reconciliation_message:message});
    const text=elements['trendline-monitor-status'].textContent;
    assert(text.includes(running?'Monitoring running':'Monitoring stopped'));
    if(count===null){assert(text.includes(message));assert(!text.includes('0 plans'));}
    else if(count>0){assert(text.includes(`${count} ${count===1?'plan requires':'plans require'} manual broker reconciliation`));assert(text.includes(message));}
    else assert(!text.includes('reconciliation'));
    assert.equal(elements['trendline-monitor-start'].disabled,running);
    assert.equal(elements['trendline-monitor-stop'].disabled,!running);
  }
}
context.renderTrendlineMonitor({running:false,reconciliation_required:null});
assert(elements['trendline-monitor-status'].textContent.includes('Reconciliation status unknown. Automatic retry remains disabled.'));
assert.equal(requests,0);
console.log('ok');
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
