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
    assert 'window.open(' not in js
    assert 'Loaded ${new Date().toLocaleTimeString()}' not in js
    assert "document.getElementById('dashboard-workspace-frame')" in js
    assert "document.getElementById('dashboard-workspace-title')" in js
    assert "document.getElementById('dashboard-workspace-status')" in js
    assert "let activeMainLoadState = 'idle';" in js
    assert "activeMainLoadState = 'loading';" in js
    assert "activeMainLoadState = 'loaded';" in js
    assert "activeMainLoadState = 'error';" in js
    assert "active-script" in js
    assert "const processRunning = script.running === true;" in js
    assert "const processStarting = script.starting === true;" in js
    assert "let dotState = processRunning ? 'running' : (processStarting ? 'starting' : 'stopped');" in js
    assert "if (isFxWeekend && processRunning && !fxEnabled) {" in js
    assert "if (dotState === 'stopped' && stopReason) {" in js
    assert "const isMonitor = String(script.name || '').trim().toLowerCase() === 'monitor';" in js
    assert "processTitle = processRunning ? 'Scanner running' : (processStarting ? 'Scanner starting' : 'Scanner stopped');" in js
    assert "processTitle = `Scanner stopped: ${stopReason}`;" in js
    assert "if (!processRunning && stopReason) {" in js
    assert "const isActiveMainView = isDashboardMainView(script) && String(script.name) === activeMainScriptName;" in js
    assert "if (isActiveMainView) {" in js
    assert "dotState = 'running';" in js
    assert "workspaceTitle = activeMainLoadState === 'loading'" in js
    assert "'Open in workspace: loading'" in js
    assert "'Open in workspace: load failed'" in js
    assert "'Open in workspace'" in js
    assert "dotTitle = workspaceTitle ? `${workspaceTitle}; ${processTitle}` : processTitle;" in js
    assert "syncWorkspaceSelectionFromScripts();" in js
    assert "syncWorkspaceSelectionFromScripts();\n        renderScripts();" in js
    assert "makeExitButton" in js
    assert "local-exit-btn" in js
    assert "scriptsState.forEach((item) => scriptsGrid.appendChild(makeScriptButton(item)));\n    scriptsGrid.appendChild(makeExitButton());" in js
    assert "if (isMonitor) {" in js
    assert "cache: 'no-store'" in js
    assert "Loading state…" in js
    assert "Saved locally only (repo deletion can lose local state)" in js
    assert "State synced" in js
    assert "State sync error" in js
    assert "State sync verification missing; save not confirmed durable." in js
    assert "Watchlist edits blocked until state restore/sync is healthy." in js
    assert "dotTitle = 'Inactive view';" not in js
    assert "dotTitle = 'Active view loaded';" not in js
    assert "Inactive view" not in js


def test_dashboard_js_prefers_post_verified_watchlist_before_remote_summary() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert "if (verifiedAt && verifiedWatchlist.length) {" in js
    assert "const remoteSummary = await fetchRemoteBackupSummary();" in js


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


def test_trading_journal_actions_listener_inside_iife():
    js = (ROOT / "render" / "static" / "trading_journal_actions.js").read_text(encoding="utf-8")
    close_idx = js.rfind('})();')
    listener_idx = js.find('cryptoMonthlyBtn?.addEventListener')
    assert listener_idx != -1 and listener_idx < close_idx
    assert "})();\n\n\ncryptoMonthlyBtn?.addEventListener" not in js



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
