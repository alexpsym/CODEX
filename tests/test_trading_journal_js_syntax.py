import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "trading_journal.js"
ACTIONS_JS_PATH = ROOT / "render" / "static" / "trading_journal_actions.js"


def test_trading_journal_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)


def test_trading_journal_diagnostics_split_balance_anchor_from_parse_sync() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "balance anchor missing" in js
    assert "const isBalanceAnchorWarning" in js
    assert "const isParseSyncError" in js
    assert "if (syncResult?.ok === false)" in js
    assert "snapshotError" in js
    assert "Bybit Demo workbook is blank; old Bybit Demo rows purged" in js
    assert "g?.market_breakdown || []" in js
    assert "avg_result_pct" in js
    assert "avg_r_multiple" in js
    assert "avg_stop_pct_winners" in js
    assert "avg_stop_pct_losers" in js
    assert "stop_loss_distance_pct" in js
    assert "target_distance_pct" in js
    assert "Stop Loss Distance" in js
    assert "Target Distance" in js
    assert "priceDistancePct" in js
    assert "Browser trade columns are intentionally grouped" in js
    assert "return fb;" in js
    assert "fb * 100" not in js
    assert "max_drawdown_pct" in js
    assert "overall_avg_seconds" in js
    assert "tj-stats-table" in js
    assert "fmtStatTradeJump" in js
    assert "jumpToTradeRow" in js
    assert "data-jump-row-id" in js
    assert "tj-stat-jump" in js
    assert "tj-row-highlight" in js
    assert "fmtLeader" in js
    assert "tj-stat-detail" in js
    assert "fx_most_wins_instrument" in js
    assert "fx_most_losses_instrument" in js
    assert "crypto_most_wins_instrument" in js
    assert "crypto_most_losses_instrument" in js
    assert "metric_sources" in js
    assert "escHtml(fmtTradeRef" not in js
    assert 'Min result %' not in js
    assert 'Max result %' not in js
    assert 'Max loss %' in js
    assert 'Max win %' in js
    assert 'Max R loss' in js
    assert 'Max R win' in js
    assert 'Drawdown points' not in js
    assert "Segments" not in js
    assert "wrap.style.display = 'block';" not in js

    assert "tj-stats-column" in js
    assert "const sections = [" in js
    assert "wrap.innerHTML = [" not in js


def test_trading_journal_stats_classes_are_value_only_and_net_pl_is_sign_based() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "const toneBySign = (value) =>" in js
    assert "if (!Number.isFinite(n) || n === 0) return 'tj-stat-neutral';" in js
    assert "return n > 0 ? 'tj-stat-positive' : 'tj-stat-negative';" in js
    assert "const row = (label, value, valueCls='tj-stat-neutral', labelCls='tj-stat-neutral', detail='')" in js
    assert '<td class="tj-stat-label ${labelCls}">' in js
    assert '<td class="tj-stat-value ${valueCls}">' in js
    assert '<td class="tj-stat-detail">' in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct))" in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct), 'tj-stat-winner')" not in js
    assert "row('Avg result %', fmtPctSmall(m?.avg_result_pct), toneBySign(m?.avg_result_pct))" in js


def test_trading_journal_instrument_view_uses_aggregate_safe_dataset_and_load_hides_overlay_on_failure() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    render_rows_scope = js[js.index("function renderRows"):js.index("function renderBalances")]
    inst_scope = js[js.index("function renderInstrumentView"):js.index("function renderCalendarView")]
    assert "tr.setAttribute('data-row-id'" in render_rows_scope
    assert "String(r.id)" in render_rows_scope
    assert "data-row-id" not in inst_scope
    assert "r.id" not in inst_scope
    assert "tr.dataset.symbol = String(item.symbol || '')" in inst_scope
    assert "tr.dataset.assetClass = String(item.asset_class || '')" in inst_scope
    assert "loading?.style?.display === 'flex'" not in js
    assert "if (ownsVisibleOverlay) hideLoading();" in js


def test_trading_journal_stat_trade_filter_wiring_present() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "statTradeFilter" in js
    assert "getFilteredRows" in js
    assert "renderStatTradeFilterButton" in js
    assert "clearStatTradeFilter" in js
    assert "data-jump-row-label" in js
    assert "tj-stat-trade-filter-btn" in js
    assert "jumpToTradeRow(jumpEl.dataset.jumpRowId || '', jumpEl.dataset.jumpRowLabel || '')" in js
    assert "stale_oanda_demo_balance_not_backfilled" in js
    assert "OANDA demo export exists but was not applied. Balance is stale." in js
    assert "Install xlrd in the journal runtime, then rerun OANDA history backfill." in js
    assert "Repair OANDA DEMO" in js
    assert "/api/trading-journal/oanda-demo/repair-balance" in js
    assert "OANDA DEMO balance is stale. Run OANDA demo history export/backfill." in js
    assert "oandaRepairAttempted" in js
    assert "loadData(" not in js
    assert "schedulePostRepairRefresh" in js
    assert "catch (_err) {}" not in js
    assert "TJ_CACHE_SCHEMA_VERSION = 2" in js
    assert "cache_schema_version: TJ_CACHE_SCHEMA_VERSION" in js
    assert "js_version: TJ_JS_VERSION" in js
    assert "payload.cache_schema_version" in js
    assert "payload.js_version" in js

def test_monthly_aud_reval_rendering_hooks_present() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "function isMonthlyAudRevalRow(row)" in js
    assert "if (isMonthlyAud)" in js
    assert "Monthly Bybit Live AUD P/L note; excluded from trading metrics." in js
    assert "const isTrade = rowType === 'trade' || !rowType;" in js

    assert "r?.raw_refs?.period_month" in js
    assert "r?.result_currency" in js
    assert "r?.result_cash" in js
    assert "r?.row_type" in js
    assert "r?.id" in js
    assert "{ key: 'chart', header: 'Chart', value: (r) => { if (isMonthlyAudRevalRow(r)) return '';" in js
    assert "{ key: 'actions', header: 'Actions', value: (r) => { if (isMonthlyAudRevalRow(r)) return '';" in js


def test_trading_journal_actions_import_error_shows_payload_errors_and_keeps_bybit_ambiguity_message() -> None:
    js = ACTIONS_JS_PATH.read_text(encoding="utf-8")
    assert "const formatImportError = (payload, fallback) =>" in js
    assert "payload?.detail || payload?.message || fallback" in js
    assert "Array.isArray(payload?.errors)" in js
    assert "Errors: ${payload.errors" in js
    assert "Array.isArray(payload?.missing_row_ids)" in js
    assert "Missing Row IDs: ${payload.missing_row_ids" in js
    assert "throw new Error(formatImportError(payload, 'Import failed.'))" in js
    assert "const BYBIT_AMBIGUITY_MSG = 'Select Demo or Live in Bybit CSV account, then import this file again.';" in js
    assert "Import is still running longer than expected. Waiting for backend result..." in js
    assert "IMPORT_WATCHDOG_MS" in js
    assert "if (!res.ok || payload.ok !== true)" in js
    assert "} catch (err) {" in js
    assert "clearPendingRetry();" in js


def test_trading_journal_actions_excel_lock_retry_controls_and_guards_present() -> None:
    js = ACTIONS_JS_PATH.read_text(encoding="utf-8")
    assert "payload?.code === 'EXCEL_WORKBOOK_OPEN'" in js
    assert "payload?.retryable === true" not in js
    assert "Resume after closing Excel" in js
    assert "Retry canceled." in js
    assert "let retryInFlight = false;" in js
    assert "if (!pendingRetry.run || retryInFlight) return;" in js
    assert "resumeBtn.disabled = true;" in js
    assert "setPendingRetry('import', () => runImport(file, explicitMode));" in js
    assert "await runImport(file, capturedMode);" in js
    assert "setPendingRetry('bybit_demo_adjustment', () => runBybitAdjust(amount, reason));" in js
    assert "setPendingRetry('crypto', runCryptoMonthly);" in js
    assert "if (importBtn) importBtn.disabled" in js
    assert "if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled" in js
    assert "if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled" in js
    assert "if (status) status.after(resumeBtn, cancelBtn);" in js
    assert "if (!pendingRetry.run && fileInput) fileInput.value = '';" in js


def test_trading_journal_actions_drag_drop_import_timer_and_final_status() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) {
  return {
    id,
    style: {},
    classList: { add: (c) => { listeners[id + ':class'] = c; }, remove: () => {} },
    textContent: '',
    value: '',
    disabled: false,
    files: [],
    focus: () => { listeners.focused = id; },
    click: () => { listeners.clicked = id; },
    after: () => {},
    addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; },
  };
}
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
elements['journal-account-mode'].value = 'demo';
let intervalStarted = 0;
let intervalCleared = 0;
let timeoutCleared = 0;
let fetchCalls = 0;
let importFetchCalls = 0;
const file = {
  name: 'oanda_demo.csv',
  slice: () => ({ text: async () => 'TICKET,TRANSACTION DATE,TRANSACTION TYPE,DETAILS,BALANCE\n' }),
};
class FormData { append(k, v) { this[k] = v; } }
const context = {
  console,
  document: {
    getElementById: (id) => elements[id] || element(id),
    createElement: (tag) => element(tag),
  },
  FormData,
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: (fn) => { intervalStarted += 1; fn(); return 42; },
  clearInterval: () => { intervalCleared += 1; },
  setTimeout: (fn) => { fn(); return 24; },
  clearTimeout: () => { timeoutCleared += 1; },
  fetch: async (url) => {
    fetchCalls += 1;
    if (String(url).includes('/api/trading-journal/import-file')) importFetchCalls += 1;
    if (String(url).includes('/api/trading-journal/import/status')) return { ok: true, json: async () => ({ ok: true, running: true, stage: 'workbook_sync', elapsed_seconds: 2 }) };
    return { ok: true, json: async () => ({ ok: true, message: 'Import complete.', rows_parsed: 1, rows_upserted: 1, warnings: [], missing_row_ids: [], master_journal_path: 'Trading Journal.xlsx' }) };
  },
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
(async () => {
  const drop = listeners['journal-import-drop-zone:drop'];
  if (typeof drop !== 'function') throw new Error('drop handler missing');
  await drop({ preventDefault: () => { listeners.prevented = true; }, dataTransfer: { files: [file] } });
  const status = elements['journal-actions-status'].textContent;
  if (!listeners.prevented) throw new Error('drop default not prevented');
  if (importFetchCalls !== 1) throw new Error('drop did not import exactly once');
  if (intervalStarted < 1) throw new Error('elapsed timer did not start');
  if (intervalCleared < 1 || timeoutCleared < 1) throw new Error('timer/watchdog were not cleared');
  if (!status.includes('Import complete.')) throw new Error('final success not shown: ' + status);
  if (status.includes('longer than expected')) throw new Error('watchdog message was not replaced: ' + status);
  if (!status.includes('Rows parsed: 1')) throw new Error('summary missing: ' + status);
})().catch((err) => { console.error(err); process.exit(1); });
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)


def test_trading_journal_actions_drag_drop_and_elapsed_timer_hooks_present() -> None:
    js = ACTIONS_JS_PATH.read_text(encoding="utf-8")
    assert "journal-import-drop-zone" in js
    assert "dragover" in js
    assert "dragleave" in js
    assert "drop" in js
    assert "await runImport(file, capturedMode);" in js
    assert "Importing... elapsed" in js
    assert "formatElapsed" in js
    assert "window.clearInterval(elapsedTimer)" in js


def test_trading_journal_actions_lock_failure_keeps_retry_and_clears_import_state() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) {
  return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } };
}
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
elements['journal-account-mode'].value = 'demo';
const file = { name: 'oanda_demo.csv', slice: () => ({ text: async () => 'TICKET,TRANSACTION DATE,TRANSACTION TYPE,DETAILS,BALANCE\n' }) };
class FormData { append(k, v) { this[k] = v; } }
let intervalCleared = 0;
let timeoutCleared = 0;
const context = {
  console,
  document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) },
  FormData,
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: () => 11,
  clearInterval: () => { intervalCleared += 1; },
  setTimeout: () => 22,
  clearTimeout: () => { timeoutCleared += 1; },
  fetch: async () => ({ ok: false, json: async () => ({ ok: false, code: 'EXCEL_WORKBOOK_OPEN', message: 'Trading Journal.xlsx appears to be open in Excel. Close it, then press Resume.', errors: ['workbook_locked'], import_timings: { parse: 1, workbook_sync: 2 } }) }),
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
elements['journal-file-input'].files = [file];
(async () => {
  await listeners['journal-file-input:change']();
  const status = elements['journal-actions-status'].textContent;
  if (!status.includes('Import failed: Trading Journal.xlsx appears')) throw new Error('lock message missing: ' + status);
  if (status.includes('Importing...') || status.includes('longer than expected')) throw new Error('stale import status remains: ' + status);
  if (elements['open-journal-btn'].disabled !== true) throw new Error('open journal should remain disabled while retry is pending');
  if (elements['import-journal-btn'].disabled !== true) throw new Error('import button should remain disabled while retry is pending');
  if (intervalCleared < 1 || timeoutCleared < 1) throw new Error('timers not cleared');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)


def test_trading_journal_actions_500_json_replaces_watchdog_and_reenables_open() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) {
  return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } };
}
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
elements['journal-account-mode'].value = 'demo';
const file = { name: 'oanda_demo.csv', slice: () => ({ text: async () => 'TICKET,TRANSACTION DATE,TRANSACTION TYPE,DETAILS,BALANCE\n' }) };
class FormData { append(k, v) { this[k] = v; } }
const context = {
  console,
  document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) },
  FormData,
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: () => 11,
  clearInterval: () => {},
  setTimeout: (fn) => { fn(); return 22; },
  clearTimeout: () => {},
  fetch: async () => ({ ok: false, json: async () => ({ ok: false, message: 'Backend exploded.', errors: ['boom'], import_timings: { parse: 1.2, workbook_sync: 15.5 } }) }),
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
elements['journal-file-input'].files = [file];
(async () => {
  await listeners['journal-file-input:change']();
  const status = elements['journal-actions-status'].textContent;
  if (!status.includes('Backend exploded.')) throw new Error('final error missing: ' + status);
  if (!status.includes('Errors: boom')) throw new Error('payload errors missing: ' + status);
  if (!status.includes('Timings: parse=1.2s, workbook_sync=15.5s')) throw new Error('timings missing: ' + status);
  if (status.includes('longer than expected')) throw new Error('watchdog text not replaced: ' + status);
  if (elements['open-journal-btn'].disabled !== false) throw new Error('open journal should be re-enabled after non-retry failure');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)
def test_trading_journal_actions_resync_calls_endpoint_and_disables_buttons() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) { return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } }; }
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-resync-btn': element('journal-resync-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
let urls = [];
const context = {
  console,
  document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) },
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: () => 11,
  clearInterval: () => {},
  setTimeout: () => 22,
  clearTimeout: () => {},
  fetch: async (url) => { urls.push(url); if (elements['journal-resync-btn'].disabled !== true) throw new Error('resync not disabled during fetch'); if (elements['import-journal-btn'].disabled !== true) throw new Error('import not disabled during resync'); return { ok: true, json: async () => ({ ok: true, master_journal_path: '/tmp/Trading Journal.xlsx', master_journal_diagnostics: { workbook_sync_substage_timings: { snapshot_build: 0.1, workbook_sync: 0.2 } } }) }; },
};
context.window = context; context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
(async () => {
  await listeners['journal-resync-btn:click']();
  if (urls[0] !== '/api/trading-journal/resync') throw new Error('wrong endpoint ' + urls[0]);
  const status = elements['journal-actions-status'].textContent;
  if (!status.includes('Resync complete.')) throw new Error('success missing: ' + status);
  if (!status.includes('snapshot_build=0.1s')) throw new Error('timings missing: ' + status);
  if (elements['journal-resync-btn'].disabled !== false) throw new Error('resync should re-enable after success');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)


def test_trading_journal_actions_resync_ignores_duplicate_click_while_running() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) { return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } }; }
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-resync-btn': element('journal-resync-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
let fetchCount = 0;
let releaseFetch;
const context = {
  console,
  document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) },
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: () => 11,
  clearInterval: () => {},
  setTimeout: () => 22,
  clearTimeout: () => {},
  fetch: async () => { fetchCount += 1; await new Promise((resolve) => { releaseFetch = resolve; }); return { ok: true, json: async () => ({ ok: true, master_journal_path: '/tmp/Trading Journal.xlsx', resync_timings: { snapshot_build: 1 } }) }; },
};
context.window = context; context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
(async () => {
  const first = listeners['journal-resync-btn:click']();
  const second = listeners['journal-resync-btn:click']();
  await Promise.resolve();
  if (fetchCount !== 1) throw new Error('duplicate click sent ' + fetchCount + ' requests');
  releaseFetch();
  await first;
  await second;
  if (fetchCount !== 1) throw new Error('duplicate click completed with ' + fetchCount + ' requests');
  if (elements['journal-resync-btn'].disabled !== false) throw new Error('resync should re-enable after duplicate-click run');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)


def test_trading_journal_actions_resync_excel_lock_sets_retry() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) { return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } }; }
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-resync-btn': element('journal-resync-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
const context = { console, document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) }, Date: { now: () => 1000 }, setInterval: () => 11, clearInterval: () => {}, setTimeout: () => 22, clearTimeout: () => {}, fetch: async () => ({ ok: false, json: async () => ({ ok: false, code: 'EXCEL_WORKBOOK_OPEN', message: 'Close Excel then retry', errors: ['workbook_locked'] }) }) };
context.window = context; context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
(async () => {
  await listeners['journal-resync-btn:click']();
  const status = elements['journal-actions-status'].textContent;
  if (!status.includes('Close Excel then retry')) throw new Error('lock message missing: ' + status);
  if (elements['open-journal-btn'].disabled !== true) throw new Error('open should be disabled for retry');
  if (elements['import-journal-btn'].disabled !== true) throw new Error('import should be disabled for retry');
  if (elements['journal-resync-btn'].disabled !== true) throw new Error('resync should be disabled for retry');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)



def test_trading_journal_actions_resync_active_sync_message_includes_caller_and_elapsed() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS runtime smoke test"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function element(id) { return { id, style: {}, classList: { add: () => {}, remove: () => {} }, textContent: '', value: '', disabled: false, files: [], focus: () => {}, click: () => {}, after: () => {}, addEventListener: (type, fn) => { listeners[id + ':' + type] = fn; } }; }
const elements = {
  'open-journal-btn': element('open-journal-btn'),
  'import-journal-btn': element('import-journal-btn'),
  'journal-resync-btn': element('journal-resync-btn'),
  'journal-file-input': element('journal-file-input'),
  'journal-import-drop-zone': element('journal-import-drop-zone'),
  'crypto-monthly-pnl-btn': element('crypto-monthly-pnl-btn'),
  'bybit-demo-balance-adjustment-btn': element('bybit-demo-balance-adjustment-btn'),
  'journal-account-mode': element('journal-account-mode'),
  'journal-actions-status': element('journal-actions-status'),
};
let fetchCount = 0;
const context = {
  console,
  document: { getElementById: (id) => elements[id] || element(id), createElement: (tag) => element(tag) },
  Date: { now: (() => { let n = 0; return () => { n += 1000; return n; }; })() },
  setInterval: () => 11,
  clearInterval: () => {},
  setTimeout: () => 22,
  clearTimeout: () => {},
  fetch: async () => { fetchCount += 1; return { ok: false, json: async () => ({ ok: false, code: 'MASTER_JOURNAL_SYNC_IN_PROGRESS', active_caller: 'manual_import', active_elapsed_seconds: 12.34, message: 'Trading Journal workbook sync is already running.' }) }; },
};
context.window = context; context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_actions.js' });
(async () => {
  await listeners['journal-resync-btn:click']();
  const status = elements['journal-actions-status'].textContent;
  if (fetchCount !== 1) throw new Error('expected one fetch, got ' + fetchCount);
  if (!status.includes('Trading Journal sync already running: caller=manual_import, elapsed=12s')) throw new Error('active sync status missing: ' + status);
  if (status.includes('Trading Journal resync failed')) throw new Error('generic failure should not be shown: ' + status);
  if (elements['journal-resync-btn'].disabled !== false) throw new Error('resync should re-enable after active-sync response');
  if (elements['import-journal-btn'].disabled !== false) throw new Error('import should re-enable after active-sync response');
})();
"""
    subprocess.run([node, "-e", harness, str(ACTIONS_JS_PATH)], check=True)
