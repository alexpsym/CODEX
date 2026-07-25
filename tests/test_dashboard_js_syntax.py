import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / 'render' / 'static' / 'dashboard.js'


def test_dashboard_js_parses_with_node() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS syntax check'
    subprocess.run([node, '--check', str(JS_PATH)], check=True)


def test_dashboard_js_no_removed_widget_endpoints_and_keeps_needed_calls() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert '/api/open-orders' not in js
    assert '/api/recent-trades' not in js
    assert '/api/watchlist' in js
    assert '/api/state-sync/status' in js
    assert '/api/state-sync/remote-backup-summary' in js
    assert '/api/oanda-inactivity-status' in js
    assert '/api/local-exit' in js
    assert "const MAIN_WORKSPACE_URL = '/merged/open-orders';" in js
    assert '/merged/open-orders' in js
    assert 'window.open(' in js
    assert 'Loaded ${new Date().toLocaleTimeString()}' not in js
    assert "document.getElementById('dashboard-workspace-frame')" in js
    assert "document.getElementById('dashboard-workspace-title')" in js
    assert "document.getElementById('dashboard-workspace-status')" in js
    assert "document.getElementById('dashboard-instrument-lookup-form')" in js
    assert "document.getElementById('dashboard-instrument-lookup-input')" in js
    assert "ensureOrdersWorkspace();" in js
    assert "installWorkspaceHeightSync();" in js
    assert "submitInlineLookup" in js
    assert "window.open(target, '_blank')" in js
    assert "encodeURIComponent(raw)" in js
    assert "ResizeObserver" in js
    assert "MutationObserver" in js
    assert "const scriptTabWindows = new Map();" in js
    assert "const openScriptTab = (script) => {" in js
    assert "active-script" in js
    assert "const processRunning = script.running === true;" in js
    assert "const processStarting = script.starting === true;" in js
    assert "let dotState = processRunning ? 'running' : (processStarting ? 'starting' : 'stopped');" in js
    assert "if (lowerName === 'monitor') {" in js
    assert "let title = processRunning ? 'Scanner running' : (processStarting ? 'Scanner starting' : 'Scanner stopped');" in js
    assert "title = `Scanner stopped: ${stopReason}`;" in js
    assert "if (!processRunning && stopReason) {" in js
    assert "return { dotState: 'running', dotTitle: 'Shown on dashboard', active: true };" in js
    assert "return { dotState: 'running', dotTitle: 'Open in a tab', active: true };" in js
    assert "return { dotState: 'stopped', dotTitle: 'Not open in a tab', active: false };" in js
    assert "btn.addEventListener('click', () => openScriptTab(script));" in js
    assert "btn.addEventListener('contextmenu', (event) => {" in js
    assert "btn.addEventListener('auxclick', (event) => {" in js
    assert "Select a script from the toolbar above to load it here." not in js
    assert "Select a script from the left to load it here." not in js
    assert "makeExitButton" in js
    assert "local-exit-btn" in js
    assert "const exitButtonSlot = document.getElementById('exit-button-slot');" in js
    assert "btn.dataset.scriptName = String(script.name || '');" in js
    assert "scriptsState.forEach((item) => scriptsGrid.appendChild(makeScriptButton(item)));" in js
    assert "exitButtonSlot.appendChild(makeExitButton());" in js
    assert "scriptsGrid.appendChild(makeExitButton());" in js
    assert "cache: 'no-store'" in js
    assert "Loading state…" in js
    assert "Saved locally only (repo deletion can lose local state)" in js
    assert "State synced" in js
    assert "State sync error" in js
    assert "Durable watchlist verification failed." in js
    assert "Watchlist edits blocked until state restore/sync is healthy." in js
    assert "dotTitle = 'Inactive view';" not in js
    assert "dotTitle = 'Active view loaded';" not in js
    assert "Inactive view" not in js
    assert "activeMainLoadState" not in js
    assert "syncWorkspaceSelectionFromScripts" not in js


def test_dashboard_js_accepts_verified_empty_watchlist_without_remote_fallback() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert "payload?.durable_verified === true" in js
    assert "const verifiedItems = Array.isArray(payload?.verified_items) ? payload.verified_items : [];" in js
    assert "verifiedItems.length" not in js
    assert "const authoritative = await fetchJson('/api/watchlist');" in js
    assert "error.payload = bodyJson;" in js
    assert "failurePayload?.state_sync" in js
    assert "stateSyncState?.watchlist_mutation_blocked === true" in js
    assert "stateSyncState?.watchlist_indeterminate === true" in js


def test_dashboard_js_removed_sync_journal_wiring():
    js = (ROOT / 'render' / 'static' / 'dashboard.js').read_text(encoding='utf-8')
    for token in [
        'sync-journal-btn',
        'syncJournalBtn',
        'runSyncJournal',
        'open-master-journal-btn',
        'openMasterJournalBtn',
        'openMasterJournal',
        '/api/trading-journal/sync',
        '/api/trading-journal/sync/status',
    ]:
        assert token not in js


def test_dashboard_js_runtime_init_smoke() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS runtime smoke test'
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

function element() {
  return {
    addEventListener: () => {},
    removeEventListener: () => {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    style: {},
    dataset: {},
    textContent: '',
    innerHTML: '',
    value: '',
    disabled: false,
    appendChild: () => {},
    remove: () => {},
    querySelector: () => element(),
    querySelectorAll: () => [],
    setAttribute: () => {},
    getAttribute: () => null,
  };
}

const document = {
  visibilityState: 'visible',
  body: element(),
  getElementById: () => element(),
  querySelector: () => element(),
  querySelectorAll: () => [],
  createElement: () => element(),
  addEventListener: () => {},
};

const fetch = async (url) => ({
  ok: true,
  json: async () => {
    if (String(url).includes('/scripts')) return [];
    if (String(url).includes('/api/state-sync/status')) return {};
    if (String(url).includes('/api/watchlist')) return { watchlist: [] };
    if (String(url).includes('/api/oanda-inactivity-status')) return {};
    return {};
  },
});

const context = {
  console,
  document,
  fetch,
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout: (fn) => { if (typeof fn === 'function') fn(); return 1; },
  clearTimeout: () => {},
  URL: URL,
  Date: Date,
  Math: Math,
  Promise: Promise,
  AbortController: class { constructor() { this.signal = {}; } abort() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  location: { href: 'http://127.0.0.1:8000/' },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
context.window = context;
context.window.addEventListener = () => {};
context.window.removeEventListener = () => {};
context.globalThis = context;

vm.createContext(context);
vm.runInContext(source, context, { filename: 'dashboard.js' });
"""
    subprocess.run([node, '-e', harness, str(JS_PATH)], check=True)


def test_dashboard_js_inline_lookup_submit_opens_results_only_for_symbol():
    node = shutil.which('node')
    assert node, 'node is required for JS behavior check'
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

const handlers = {};
let focused = null;
function element(id) {
  const attrs = id === 'dashboard-workspace-frame' ? { src: '/merged/open-orders?_dashboard=1' } : {};
  return {
    id,
    addEventListener: (event, callback) => { handlers[`${id}:${event}`] = callback; },
    removeEventListener: () => {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    style: {},
    dataset: {},
    textContent: '',
    innerHTML: '',
    value: '',
    disabled: false,
    hidden: false,
    appendChild: () => {},
    remove: () => {},
    focus: () => { focused = id; },
    querySelector: () => element(`${id}:child`),
    querySelectorAll: () => [],
    setAttribute: (name, value) => { attrs[name] = String(value); },
    getAttribute: (name) => attrs[name] || null,
  };
}

const ids = [
  'dashboard-workspace-frame',
  'dashboard-instrument-lookup-form',
  'dashboard-instrument-lookup-input',
  'dashboard-instrument-lookup-status',
  'scripts-grid',
  'exit-button-slot',
];
const elements = Object.fromEntries(ids.map((id) => [id, element(id)]));
const opens = [];
const document = {
  visibilityState: 'visible',
  hidden: false,
  body: element('body'),
  getElementById: (id) => elements[id] || element(id),
  querySelector: () => element('query'),
  querySelectorAll: () => [],
  createElement: () => element('created'),
  addEventListener: () => {},
};
const fetch = async (url) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: async () => {
    if (String(url).includes('/scripts')) return '[]';
    if (String(url).includes('/api/pine/files')) return '{"files":[]}';
    if (String(url).includes('/api/watchlist')) return '{"items":[]}';
    if (String(url).includes('/api/state-sync/status')) return '{}';
    if (String(url).includes('/api/oanda-inactivity-status')) return '{}';
    return '{}';
  },
});
const context = {
  console,
  document,
  fetch,
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout: (fn) => { if (typeof fn === 'function') fn(); return 1; },
  clearTimeout: () => {},
  URL: URL,
  Date: Date,
  Math: Math,
  Promise: Promise,
  navigator: { clipboard: { writeText: async () => {} } },
  location: { href: 'http://127.0.0.1:8000/' },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
context.window = context;
context.window.addEventListener = () => {};
context.window.removeEventListener = () => {};
context.window.open = (url, name) => {
  opens.push({ url, name });
  return { focus: () => { opens[opens.length - 1].focused = true; } };
};
context.globalThis = context;

vm.createContext(context);
vm.runInContext(source, context, { filename: 'dashboard.js' });

const submit = handlers['dashboard-instrument-lookup-form:submit'];
if (typeof submit !== 'function') throw new Error('submit handler missing');
const event = { prevented: false, preventDefault() { this.prevented = true; } };
submit(event);
if (!event.prevented) throw new Error('blank submit did not prevent default');
if (opens.length !== 0) throw new Error('blank submit opened a tab');
if (!String(elements['dashboard-instrument-lookup-status'].textContent).includes('Enter a symbol')) throw new Error('blank submit status missing');
if (focused !== 'dashboard-instrument-lookup-input') throw new Error('blank submit did not focus input');

elements['dashboard-instrument-lookup-input'].value = 'BTC/USDT?demo=1';
const event2 = { prevented: false, preventDefault() { this.prevented = true; } };
submit(event2);
if (!event2.prevented) throw new Error('symbol submit did not prevent default');
if (opens.length !== 1) throw new Error(`expected one tab, got ${opens.length}`);
if (opens[0].name !== '_blank') throw new Error('lookup target should open in a new tab');
if (opens[0].url !== '/instrument-lookup?q=BTC%2FUSDT%3Fdemo%3D1') throw new Error(`bad lookup URL ${opens[0].url}`);
if (!opens[0].focused) throw new Error('lookup tab was not focused');
"""
    subprocess.run([node, '-e', harness, str(JS_PATH)], check=True)


def test_dashboard_js_user_facing_trading_journal_wording():
    js = JS_PATH.read_text(encoding='utf-8')
    assert 'Failed to open Master Journal.xlsx' not in js
    assert "'Master Journal.xlsx'" not in js
    assert 'Failed to open Trading Journal.xlsx' not in js


def test_trading_journal_actions_js_parses_with_node():
    node = shutil.which('node')
    assert node
    subprocess.run([node, '--check', str(ROOT / 'render' / 'static' / 'trading_journal_actions.js')], check=True)


def test_trading_journal_actions_js_wiring():
    js = (ROOT / "render" / "static" / "trading_journal_actions.js").read_text(encoding="utf-8")
    assert "/api/trading-journal/open-master-journal" in js
    assert "/api/trading-journal/import-file" in js
    assert "/api/trading-journal/crypto-monthly-pnl" in js
    assert "/api/trading-journal/bybit-demo/balance-adjustment" in js
    assert "account_mode" in js
    assert "Rows parsed:" in js
    assert js.count("bybitDemoBalanceAdjustmentBtn?.addEventListener('click'") == 1
    assert js.count("/api/trading-journal/open-master-journal") == 1
    assert "/api/trading-journal/open-journal" not in js
    assert "includes('demo')" not in js
    assert "includes('live')" not in js


def test_trading_journal_actions_listener_inside_iife():
    js = (ROOT / "render" / "static" / "trading_journal_actions.js").read_text(encoding="utf-8")
    close_idx = js.rfind('})();')
    listener_idx = js.find('cryptoMonthlyBtn?.addEventListener')
    assert listener_idx != -1 and listener_idx < close_idx
    assert "})();\n\n\ncryptoMonthlyBtn?.addEventListener" not in js


def test_trading_journal_actions_bybit_ambiguity_preflight_blocks_without_account_mode():
    node = shutil.which('node')
    assert node
    js_path = ROOT / 'render' / 'static' / 'trading_journal_actions.js'
    harness = r"""
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {};
const status = { textContent: '', style: {}, focus: () => {} };
const importBtn = { disabled: false, addEventListener: () => {} };
const fileInput = { value: '', files: [], addEventListener: (ev, cb) => { handlers[ev] = cb; } };
const account = { value: '', addEventListener: () => {}, focus: () => {} };
const els = { 'open-journal-btn': { addEventListener: () => {} }, 'import-journal-btn': importBtn, 'journal-file-input': fileInput, 'crypto-monthly-pnl-btn': { addEventListener: () => {} }, 'journal-account-mode': account, 'journal-actions-status': status };
const fetchCalls = [];
class FakeFormData { constructor(){ this.entries=[]; } append(k,v){ this.entries.push([k,v]); } }
const context = { console, FormData: FakeFormData, document: { getElementById: (id) => els[id] || null }, fetch: async (...args) => { fetchCalls.push(args); return { ok: true, json: async () => ({ ok: true }) }; }, setTimeout: () => 1, clearTimeout: () => {} };
context.window = context; context.globalThis = context;
vm.createContext(context); vm.runInContext(source, context);
fileInput.files = [{ name: 'bybit.csv', slice: () => ({ text: async () => 'Contracts,Order No.,Direction,Order Type,Filled Qty,Filled Price,Order Price,Filled Type,Trading Fee Rate,Fees Paid,Trasaction ID,Transaction Time,Final Balance' }) }];
Promise.resolve(handlers.change()).then(() => {
  if (fetchCalls.length !== 0) throw new Error('fetch should not be called');
  if (!String(status.textContent).includes('Select Demo or Live')) throw new Error('status missing guidance');
});
"""
    subprocess.run([node, '-e', harness, str(js_path)], check=True)


def test_trading_journal_actions_bybit_preflight_posts_with_explicit_account_mode():
    node = shutil.which('node')
    assert node
    js_path = ROOT / 'render' / 'static' / 'trading_journal_actions.js'
    harness = r"""
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {};
const status = { textContent: '', style: {}, focus: () => {} };
const importBtn = { disabled: false, addEventListener: () => {} };
const fileInput = { value: '', files: [], addEventListener: (ev, cb) => { handlers[ev] = cb; } };
const account = { value: 'demo', addEventListener: () => {}, focus: () => {} };
const els = { 'open-journal-btn': { addEventListener: () => {} }, 'import-journal-btn': importBtn, 'journal-file-input': fileInput, 'crypto-monthly-pnl-btn': { addEventListener: () => {} }, 'journal-account-mode': account, 'journal-actions-status': status };
const fetchCalls = [];
class FakeFormData { constructor(){ this.entries=[]; } append(k,v){ this.entries.push([k,v]); } }
const context = { console, FormData: FakeFormData, document: { getElementById: (id) => els[id] || null }, fetch: async (url, opts) => { fetchCalls.push([url, opts]); return { ok: true, json: async () => ({ ok: true, rows_parsed: 1, rows_upserted: 1 }) }; }, setTimeout: () => 1, clearTimeout: () => {} };
context.window = context; context.globalThis = context;
vm.createContext(context); vm.runInContext(source, context);
fileInput.files = [{ name: 'bybit.csv', slice: () => ({ text: async () => 'Contracts,Order No.,Direction,Order Type,Filled Qty,Filled Price,Order Price,Filled Type,Trading Fee Rate,Fees Paid,Transaction ID,Transaction Time,Final Balance' }) }];
Promise.resolve(handlers.change()).then(() => {
  if (fetchCalls.length !== 1) throw new Error('fetch should be called once');
  const form = fetchCalls[0][1].body;
  const mode = form.entries.find((it) => it[0] === 'account_mode');
  if (!mode || mode[1] !== 'demo') throw new Error('account_mode=demo missing');
});
"""
    subprocess.run([node, '-e', harness, str(js_path)], check=True)



def test_trading_journal_js_removed_retired_auto_sync_block():
    js = (ROOT / "render" / "static" / "trading_journal.js").read_text(encoding="utf-8")
    for token in [
        "localLast",
        "syncStatusPromise",
        "Auto-sync from configured journal sources",
        "manual Sync now remains available",
        "backgroundSyncLabel",
        "syncWatchTimer",
        "const sleep = ",
        "Journal cache is building/syncing",
        "Sync required",
    ]:
        assert token not in js


def test_trading_journal_actions_bybit_demo_balance_adjustment_flows():
    node = shutil.which('node')
    assert node
    js_path = ROOT / 'render' / 'static' / 'trading_journal_actions.js'
    harness = r"""
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {};
const status = { textContent: '', style: {} };
const btn = { disabled: false, addEventListener: (ev, cb) => { if (ev==='click') handlers.click = cb; } };
const els = { 'open-journal-btn': { addEventListener: () => {} }, 'import-journal-btn': { addEventListener: ()=>{} }, 'journal-file-input': { addEventListener: ()=>{}, files:[] }, 'crypto-monthly-pnl-btn': { addEventListener: ()=>{} }, 'journal-account-mode': { addEventListener: ()=>{} }, 'journal-actions-status': status, 'bybit-demo-balance-adjustment-btn': btn };
let promptQueue = [null, 'abc', '0', '-40', 'note', '-40', 'note2'];
const fetchCalls = [];
let failNext = true;
const context = { console, document: { getElementById: (id) => els[id] || null }, window: null, prompt: () => promptQueue.shift(), fetch: async (url, opts) => { fetchCalls.push([url, opts]); if (failNext) { failNext = false; return { ok:false, json: async()=>({ ok:false, message:'bad' }) }; } return { ok:true, json: async()=>({ ok:true, previous_balance:100, adjustment_amount:-40, new_balance:60, currency:'USDT', row_id:'rid', master_journal_path:'/tmp/x.xlsx' }) }; }, setTimeout:()=>1, clearTimeout:()=>{} };
context.window = context; context.globalThis = context;
vm.createContext(context); vm.runInContext(source, context);
Promise.resolve(handlers.click()).then(()=>{
  if (fetchCalls.length!==0) throw new Error('cancel should not fetch');
  return handlers.click();
}).then(()=>{
  if (fetchCalls.length!==0 || !String(status.textContent).includes('finite non-zero')) throw new Error('invalid numeric should block');
  return handlers.click();
}).then(()=>{
  if (fetchCalls.length!==0) throw new Error('zero should not fetch');
  return handlers.click();
}).then(()=>{
  if (fetchCalls.length!==1) throw new Error('valid should fetch once');
  const body = JSON.parse(fetchCalls[0][1].body);
  if (body.amount !== -40) throw new Error('amount mismatch');
  if (btn.disabled) throw new Error('button not re-enabled after failure');
  return handlers.click();
}).then(()=>{
  if (fetchCalls.length!==2) throw new Error('second valid should fetch');
  if (!String(status.textContent).includes('New balance: 60')) throw new Error('success status missing');
  if (btn.disabled) throw new Error('button not re-enabled after success');
});
"""
    subprocess.run([node, '-e', harness, str(js_path)], check=True)


def test_trading_journal_actions_bybit_demo_lock_retry_flow():
    node = shutil.which('node')
    assert node
    js_path = ROOT / 'render' / 'static' / 'trading_journal_actions.js'
    harness = r"""
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {};
const status = { textContent: '', style: {} };
const btn = { disabled:false, addEventListener:(ev,cb)=>{ if(ev==='click') handlers.click=cb; } };
const els = { 'open-journal-btn':{addEventListener:()=>{}}, 'import-journal-btn':{addEventListener:()=>{}}, 'journal-file-input':{addEventListener:()=>{}, files:[]}, 'crypto-monthly-pnl-btn':{addEventListener:()=>{}}, 'journal-account-mode':{addEventListener:()=>{}}, 'journal-actions-status':status, 'bybit-demo-balance-adjustment-btn':btn };
let prompts = ['-40','note','-40','note'];
let confirms = [false, true];
let fetchN = 0;
const context = { console, document:{getElementById:(id)=>els[id]||null}, window:null, prompt:()=>prompts.shift(), confirm:()=>confirms.shift(), fetch:async ()=>{ fetchN++; if (fetchN===1) return { status:423, ok:false, json:async()=>({ok:false, errors:['workbook_locked']})}; if (fetchN===2) return { status:423, ok:false, json:async()=>({ok:false, errors:['excel_open']})}; return { status:200, ok:true, json:async()=>({ok:true, previous_balance:100, adjustment_amount:-40, new_balance:60, currency:'USDT'})}; }, setTimeout:()=>1, clearTimeout:()=>{} };
context.window=context; context.globalThis=context; vm.createContext(context); vm.runInContext(source, context);
Promise.resolve(handlers.click()).then(()=>{
  if (fetchN!==1) throw new Error('cancel path should not retry');
  if (btn.disabled) throw new Error('button stuck disabled cancel');
  return handlers.click();
}).then(()=>{
  if (fetchN!==3) throw new Error('confirm path should retry once');
  if (btn.disabled) throw new Error('button stuck disabled success');
});
"""
    subprocess.run([node, '-e', harness, str(js_path)], check=True)
