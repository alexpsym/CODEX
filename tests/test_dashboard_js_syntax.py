import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / 'render' / 'static' / 'dashboard.js'


def test_dashboard_js_parses_with_node() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS syntax check'
    subprocess.run([node, '--check', str(JS_PATH)], check=True)


def test_dashboard_running_bounce_traders_uses_existing_status_and_stop_routes_without_watchlist() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert '/api/open-orders' not in js
    assert '/api/recent-trades' not in js
    assert '/api/watchlist' not in js
    assert '/api/state-sync/status' not in js
    assert '/api/state-sync/remote-backup-summary' not in js
    assert "BOUNCE_TRADER_BASE + '/status'" in js
    assert "encodeURIComponent(id) + '/stop'" in js
    assert 'td.textContent = String(session?.[key]' in js
    assert 'No active bounce trader sessions.' in js
    assert 'Failed to load running bounce traders.' in js
    assert 'refreshBounceTraders' in js
    assert 'stopBounceTrader' in js
    assert '/api/oanda-inactivity-status' in js
    assert '/api/local-exit' in js
    assert "const MAIN_WORKSPACE_URL = '/merged/open-orders';" in js
    assert '/merged/open-orders' in js
    assert 'window.open(' in js
    assert 'Loaded ${new Date().toLocaleTimeString()}' not in js
    assert "document.getElementById('dashboard-workspace-frame')" in js
    assert "document.getElementById('dashboard-workspace-title')" in js
    assert "document.getElementById('dashboard-workspace-status')" in js
    assert "document.getElementById('dashboard-instrument-lookup-form')" not in js
    assert "document.getElementById('dashboard-instrument-lookup-input')" not in js
    assert "ensureOrdersWorkspace();" in js
    assert "installWorkspaceHeightSync();" in js
    assert "submitInlineLookup" not in js
    assert "ResizeObserver" in js
    assert "MutationObserver" in js
    assert "const scriptTabWindows = new Map();" in js
    assert "const openScriptTab = (script) => {" in js
    assert "active-script" in js
    assert "const processRunning = script.running === true;" in js
    assert "const processStarting = script.starting === true;" in js
    assert "let dotState = processRunning ? 'running' : (processStarting ? 'starting' : 'stopped');" in js
    assert "if (lowerName === 'monitor') {" in js
    assert "let title = processRunning ? 'Alerts running' : (processStarting ? 'Alerts starting' : 'Alerts stopped');" in js
    assert "title = `Alerts stopped: ${stopReason}`;" in js
    assert "if (!processRunning && stopReason) {" in js
    assert "return { dotState: 'running', dotTitle: 'Shown on dashboard', active: true };" in js
    assert "return { dotState: 'running', dotTitle: 'Open in a tab', active: true };" in js
    assert "return { dotState: 'stopped', dotTitle: 'Not open in a tab', active: false };" in js
    assert "if (lowerName === 'fxweekend') {" in js
    assert "String(script?.health_state || '')" in js
    assert "String(script?.health_reason || script?.status_detail || '')" in js
    assert "healthState === 'disabled' ? `${label} (Disabled)` : label" in js
    assert "scriptTabIsOpen(script)" in js
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
    assert "dotTitle = 'Inactive view';" not in js
    assert "dotTitle = 'Active view loaded';" not in js
    assert "Inactive view" not in js
    assert "activeMainLoadState" not in js
    assert "syncWorkspaceSelectionFromScripts" not in js
    assert "if (!oandaHeadline) return null;" in js
    assert "if (!bounceTradersPanel || !bounceTradersBody) return;" not in js
    assert "if (oandaHeadline) {" in js
    assert "if (workspaceFrame) {" in js

    node = shutil.which("node")
    assert node, "node is required for bounce dashboard behavior check"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
class Element {
  constructor(id = '') { this.id = id; this.children = []; this.handlers = {}; this.dataset = {}; this.style = {}; this.textContent = ''; this.hidden = false; this.disabled = false; this.classList = { add: () => {}, remove: () => {} }; }
  set innerHTML(value) { this._innerHTML = String(value); if (value === '') this.children = []; }
  get innerHTML() { return this._innerHTML || ''; }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(event, callback) { this.handlers[event] = callback; }
  setAttribute(name, value) { this[name] = String(value); }
}
const elements = {
  'scripts-grid': new Element('scripts-grid'),
  'exit-button-slot': new Element('exit-button-slot'),
  'running-bounce-traders-panel': new Element('running-bounce-traders-panel'),
  'running-bounce-traders-status': new Element('running-bounce-traders-status'),
  'running-bounce-traders-body': new Element('running-bounce-traders-body'),
  'running-bounce-traders-empty': new Element('running-bounce-traders-empty'),
  'running-bounce-traders-table': new Element('running-bounce-traders-table'),
};
const document = { body: { dataset: { dashboardProfile: 'local' } }, visibilityState: 'visible', getElementById: (id) => elements[id] || null, createElement: (tag) => new Element(tag), addEventListener: () => {} };
const session = { id: 'sid<&', broker: 'bybit', instrument: '<BTC>', side: 'Buy', strategy: 'ema', account: 'demo', started_at: '2026-09-12T00:00:00Z' };
const calls = [];
let stopped = false;
const response = (ok, payload, html = false) => ({ ok, status: ok ? 200 : 500, statusText: ok ? 'OK' : 'Error', text: async () => html ? payload : JSON.stringify(payload) });
const fetch = async (url, options = {}) => {
  calls.push({ url: String(url), method: options.method || 'GET' });
  if (String(url) === '/scripts') return response(true, [{ name: 'bybit_trigger_bounce_trader', running: true }]);
  if (String(url).endsWith('/status')) return response(true, { sessions: stopped ? [] : [session] });
  if (String(url).includes('/sessions/') && String(url).endsWith('/stop')) { stopped = true; return response(true, '<html>redirected</html>', true); }
  return response(true, {});
};
const context = { console, document, fetch, setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {}, Date, Map, Promise, URL, encodeURIComponent, navigator: {}, location: { href: 'http://localhost/' } };
context.window = { ...context, addEventListener: () => {}, open: () => null };
context.globalThis = context;
(async () => {
  vm.createContext(context); vm.runInContext(source, context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  if (elements['running-bounce-traders-body'].children.length !== 1) throw new Error('initial running session did not render');
  const stop = elements['running-bounce-traders-body'].children[0].children[7].children[0];
  await stop.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const stopCalls = calls.filter((call) => call.method === 'POST');
  const statusCalls = calls.filter((call) => call.url.endsWith('/status'));
  if (stopCalls.length !== 1 || !stopCalls[0].url.endsWith('/sessions/sid%3C%26/stop')) throw new Error('stop route was not posted exactly once with encoded ID');
  if (statusCalls.length < 2) throw new Error('post-stop status was not freshly requested');
  if (elements['running-bounce-traders-body'].children.length !== 0) throw new Error('stopped session remained visible');
  if (elements['running-bounce-traders-status'].textContent.includes('Failed')) throw new Error('HTML redirect was treated as stop failure');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([node, "-e", harness, str(JS_PATH)], check=True)


def test_render_dashboard_js_does_not_request_or_poll_local_only_sections() -> None:
    node = shutil.which("node")
    assert node, "node is required for Render dashboard request behavior check"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

class Element {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.handlers = {};
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.disabled = false;
    this.closed = false;
    this._innerHTML = '';
    this.classList = {
      values: new Set(),
      add: (...items) => items.forEach((item) => this.classList.values.add(item)),
      remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
    };
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
    if (value === '') this.children = [];
  }
  get innerHTML() { return this._innerHTML; }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(event, callback) { this.handlers[event] = callback; }
  setAttribute(name, value) { this[name] = String(value); }
}

const elements = {
  'scripts-grid': new Element('scripts-grid'),
  'exit-button-slot': new Element('exit-button-slot'),
};
const documentHandlers = {};
const windowHandlers = {};
const document = {
  visibilityState: 'visible',
  getElementById: (id) => elements[id] || null,
  createElement: () => new Element(),
  addEventListener: (event, callback) => { documentHandlers[event] = callback; },
};
const fetchCalls = [];
const fetch = async (url) => {
  fetchCalls.push(String(url));
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify([
      { name: 'calculator', label: 'Calculator', open_url: '/merged/calculator' },
      { name: 'fxweekend', label: 'FX Weekend', open_url: '/apps/fxweekend-clone', running: true },
      { name: 'bounce-trader', label: 'Bounce Trader', open_url: '/merged/bounce-trader' },
    ]),
  };
};
const intervalCallbacks = [];
const context = {
  console,
  document,
  fetch,
  setInterval: (callback) => { intervalCallbacks.push(callback); return intervalCallbacks.length; },
  clearInterval: () => {},
  setTimeout: () => 1,
  clearTimeout: () => {},
  Date,
  Map,
  Promise,
  URL,
};
context.window = {
  addEventListener: (event, callback) => { windowHandlers[event] = callback; },
  setTimeout: context.setTimeout,
  clearTimeout: context.clearTimeout,
  location: { href: 'https://render.example.invalid/' },
  open: () => null,
};
context.globalThis = context;

(async () => {
  vm.createContext(context);
  vm.runInContext(source, context);
  await new Promise((resolve) => setImmediate(resolve));
  intervalCallbacks.forEach((callback) => callback());
  await new Promise((resolve) => setImmediate(resolve));

  if (intervalCallbacks.length !== 1) {
    throw new Error(`expected only shared scripts polling, got ${intervalCallbacks.length} timers`);
  }
  const forbidden = fetchCalls.filter((url) => url !== '/scripts');
  if (forbidden.length) {
    throw new Error(`Render requested local-only URLs: ${forbidden.join(', ')}`);
  }
  if (!fetchCalls.includes('/scripts')) {
    throw new Error('Render dashboard did not preserve shared script loading');
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    subprocess.run([node, "-e", harness, str(JS_PATH)], check=True)


def test_fxweekend_dashboard_health_comes_from_backend_not_browser_tabs() -> None:
    node = shutil.which('node')
    assert node, 'node is required for FX Weekend dashboard behavior check'
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

class Element {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.handlers = {};
    this.className = '';
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this._innerHTML = '';
    this.classList = {
      values: new Set(),
      add: (...items) => items.forEach((item) => this.classList.values.add(item)),
      remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
      toggle: (item, force) => force ? this.classList.values.add(item) : this.classList.values.delete(item),
      contains: (item) => this.classList.values.has(item),
    };
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
    if (value === '') this.children = [];
  }
  get innerHTML() { return this._innerHTML; }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(event, callback) { this.handlers[event] = callback; }
  removeEventListener() {}
  querySelector() { return new Element(); }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name] || null; }
  focus() {}
  remove() {}
}

const scriptsGrid = new Element('scripts-grid');
const exitSlot = new Element('exit-button-slot');
const refreshButton = new Element('refresh-btn');
const elements = {
  'scripts-grid': scriptsGrid,
  'exit-button-slot': exitSlot,
  'refresh-btn': refreshButton,
};
const rows = [
  { name: 'fxweekend', label: 'FX Green', open_url: '/apps/fxweekend-clone', health_state: 'green', health_reason: 'fresh heartbeat', enabled: true, running: true, starting: false, operational: true, heartbeat_fresh: true },
  { name: 'fxweekend', label: 'FX Amber', open_url: '/apps/fxweekend-clone', health_state: 'amber', health_reason: 'healthy missed cutoff', enabled: true, running: true, starting: false, operational: true, heartbeat_fresh: true },
  { name: 'fxweekend', label: 'FX Red', open_url: '/apps/fxweekend-clone', health_state: 'red', health_reason: 'credentials unavailable', enabled: true, running: false, starting: false, operational: false, heartbeat_fresh: false },
  { name: 'fxweekend', label: 'FX Disabled', open_url: '/apps/fxweekend-clone', health_state: 'disabled', health_reason: 'disabled in settings', enabled: false, running: false, starting: false, operational: false, heartbeat_fresh: false },
];
const document = {
  visibilityState: 'visible',
  hidden: false,
  body: new Element('body'),
  getElementById: (id) => elements[id] || null,
  querySelector: () => new Element(),
  querySelectorAll: () => [],
  createElement: () => new Element(),
  addEventListener: () => {},
};
const response = (payload) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: async () => JSON.stringify(payload),
});
const fetch = async (url) => {
  if (String(url) === '/scripts') return response(rows);
    if (String(url).includes('/api/pine/files')) return response({ files: [] });
  return response({});
};
const openedTabs = [];
const context = {
  console,
  document,
  fetch,
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout: () => 1,
  clearTimeout: () => {},
  URL,
  Date,
  Math,
  Promise,
  navigator: { clipboard: { writeText: async () => {} } },
  location: { href: 'https://example.invalid/' },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
context.window = context;
context.window.addEventListener = () => {};
context.window.removeEventListener = () => {};
context.window.open = () => {
  const tab = { closed: false, focus: () => {} };
  openedTabs.push(tab);
  return tab;
};
context.globalThis = context;

const states = () => scriptsGrid.children.map((button) => ({
  label: button.children[0]?.textContent,
  dot: button.children[1]?.className,
  title: button.children[1]?.title,
}));

(async () => {
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'dashboard.js' });
  await new Promise((resolve) => setImmediate(resolve));
  const initial = states();
  scriptsGrid.children[0].handlers.click();
  const afterOpen = states();
  openedTabs[0].closed = true;
  refreshButton.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const afterCloseAndRefresh = states();
  process.stdout.write(JSON.stringify({ initial, afterOpen, afterCloseAndRefresh }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        [node, '-e', harness, str(JS_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = __import__('json').loads(completed.stdout)
    expected_dots = [
        'status-dot running',
        'status-dot starting',
        'status-dot stopped',
        'status-dot disabled',
    ]
    for snapshot_name in ('initial', 'afterOpen', 'afterCloseAndRefresh'):
        snapshot = payload[snapshot_name]
        assert [item['dot'] for item in snapshot] == expected_dots
    assert payload['initial'][1]['title'] == 'healthy missed cutoff'
    assert payload['initial'][3]['label'] == 'FX Disabled (Disabled)'


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


def test_dashboard_js_removes_only_dead_inline_lookup_wiring() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    for token in (
        "dashboard-instrument-lookup-form",
        "dashboard-instrument-lookup-input",
        "dashboard-instrument-lookup-status",
        "submitInlineLookup",
        "setLookupStatus",
    ):
        assert token not in js

    standalone = (ROOT / 'render' / 'static' / 'instrument_lookup.js').read_text(encoding='utf-8')
    assert '/api/instrument-specs?query=' in standalone
    assert "history.replaceState(null, '', `/instrument-lookup?q=" in standalone


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
