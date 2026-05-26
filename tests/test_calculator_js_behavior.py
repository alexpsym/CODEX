import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "calculator.js"


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
    assert str(payload["risk_value"]) == "10"
    assert "risk_mode=fixed_aud" in data["summary"]
    assert "risk_value=10" in data["summary"]
    assert "webhook=no" in data["summary"]
    assert "test=no" in data["summary"]
    assert "timeframe=15m" in data["summary"]
    assert "pending_webhook_id=" in data["summary"]


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
    assert "blocked" in data["status"].lower() or "build:" in data["status"].lower()


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
    assert "Build:" in data["status"]


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
global.fetch=async (url,opts={})=>{if(url.includes('/quote'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',entry_price:'79300',stop_price:'78784.5',target_price:'79669',quantity:'0.012',calculation_context_id:'ctx1',quote_created_at_ms:123})};if(url.includes('/submit')){submitted=JSON.parse(opts.body||'{}');return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ok:true})};}return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();await el['calc-submit'].listeners.click();console.log(JSON.stringify({submitted,summary:el['calc-request-summary'].textContent}));})();
'''
    out = subprocess.run([node, '-e', harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data['submitted']['planned_entry_price'] == '79300'
    assert data['submitted']['stop_loss_price'] == '78784.5'
    assert 'planned_entry_price=79300' in data['summary']
    assert 'take_profit_price=79669' in data['summary']


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
    assert data["submittingText"] == "Submitting…"
    assert data["submitCallsWhilePending"] == 1
    assert data["pendingStateAfterDuplicates"] == "submitting"
    assert data["pendingTextAfterDuplicates"] == "Submitting…"
    assert data["pendingErrorAfterDuplicates"] == ""
    assert data["staleClearsVisualState"] is True
    assert data["submitCallsAfterInvalidateRecalc"] == 1
    assert data["submitCalls"] == 2
    assert data["successState"] == "success"
    assert data["clearedAfterInvalidate"] is True
    assert data["failCleared"] is True
    assert data["disabledNoHighlight"] is True
