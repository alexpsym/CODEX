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
  }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  click() { if (this.listeners.click) this.listeners.click(); }
}

const ids = [
  'calc-error', 'calc-error-debug', 'calc-success', 'calc-results', 'calc-request-summary',
  'calc-canonical-symbol', 'calc-journal-summary', 'calc-instrument-specs', 'risk-toggle-wrap',
  'calc-webhook-panel', 'calc-webhook-url', 'calc-webhook-json', 'calc-webhook-copy', 'calc-webhook-copy-url', 'risk-toggle', 'calc-risk-label',
  'limit-wrap', 'account-toggle', 'asset-toggle', 'side-toggle', 'order-toggle', 'webhook-toggle',
  'test-toggle', 'timeframe-toggle', 'calc-symbol', 'calc-limit', 'calc-sl-ticks', 'calc-rr',
  'calc-risk', 'calc-quote', 'calc-submit'
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
  'calc-sl-ticks', 'calc-rr', 'calc-risk', 'calc-quote', 'calc-submit'
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
  await quoteClick();
  states.push(submit.style.display === '');
  elements['calc-risk'].value = '2';
  elements['calc-risk'].listeners.input();
  states.push(submit.style.display === 'none');
  await quoteClick();
  states.push(submit.style.display === '');
  elements['calc-sl-ticks'].value = '20';
  elements['calc-sl-ticks'].listeners.change();
  states.push(submit.style.display === 'none');
  nextQuoteShouldFail = true;
  await quoteClick();
  states.push(submit.style.display === 'none');
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
    assert states == [True, True, True, True, True, True, True, True]
