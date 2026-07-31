import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / 'render' / 'static' / 'history_page.js'


HISTORY_PAGE_NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [jsPath, broker, scenario] = process.argv.slice(2);
const source = fs.readFileSync(jsPath, 'utf8');
const events = [];
const errors = [];

class ClassList {
  constructor(...values) {
    this.values = new Set(values);
  }
  add(value) {
    this.values.add(value);
  }
  remove(value) {
    this.values.delete(value);
  }
  contains(value) {
    return this.values.has(value);
  }
}

class Element {
  constructor(id = '', tagName = 'DIV') {
    this.id = id;
    this.tagName = tagName;
    this.value = '';
    this.dataset = {};
    this.style = {};
    this.disabled = false;
    this.listeners = {};
    this.children = [];
    this.parentNode = null;
    this.classList = new ClassList();
    this._textContent = '';
    this.periodButtons = [];
  }
  set textContent(value) {
    this._textContent = String(value || '');
    this.children = [];
  }
  get textContent() {
    return this._textContent;
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (this.id === 'history-result' && child.tagName === 'A') {
      events.push('manual-link');
    }
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }
  click() {
    if (this.tagName === 'A') {
      events.push('download-click');
      return undefined;
    }
    const handler = this.listeners.click;
    return handler ? handler({ target: this }) : undefined;
  }
  querySelectorAll(selector) {
    if (this.id === 'history-periods' && selector === '.period-btn') {
      return this.periodButtons;
    }
    return [];
  }
  querySelector(selector) {
    if (this.id !== 'history-periods') return null;
    if (selector === '.period-btn.active') {
      return this.periodButtons.find((button) => button.classList.contains('active')) || null;
    }
    if (selector === '.period-btn') return this.periodButtons[0] || null;
    return null;
  }
}

const brokerSel = new Element('history-broker', 'SELECT');
brokerSel.value = broker;
const accountWrap = new Element('history-account-wrap');
const accountSel = new Element('history-account', 'SELECT');
accountSel.value = 'demo';
const periodWrap = new Element('history-periods');
const periodButton = new Element('', 'BUTTON');
periodButton.dataset = { kind: 'days', value: '30' };
periodButton.classList.add('period-btn');
periodButton.classList.add('active');
periodWrap.periodButtons = [periodButton];
const exportBtn = new Element('history-export', 'BUTTON');
const statusEl = new Element('history-status');
const resultEl = new Element('history-result');
const body = new Element('body', 'BODY');
const elements = {
  'history-broker': brokerSel,
  'history-account-wrap': accountWrap,
  'history-account': accountSel,
  'history-periods': periodWrap,
  'history-export': exportBtn,
  'history-status': statusEl,
  'history-result': resultEl,
};
const document = {
  body,
  getElementById: (id) => elements[id] || null,
  createElement: (tagName) => new Element('', String(tagName || '').toUpperCase()),
};

const jsonResponse = (data) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: async () => JSON.stringify(data),
  headers: { get: () => null },
});

let downloadCount = 0;
let backfillCount = 0;
const fetch = async (url) => {
  const target = String(url);
  const startUrl = `/api/${broker}-history/export`;
  if (target === startUrl) {
    return jsonResponse({ job_id: 'job-12345678' });
  }
  if (target.includes('/backfill-journal')) {
    events.push('backfill');
    backfillCount += 1;
    if (scenario === 'backfill_failure' || scenario === 'combined_failure') {
      return jsonResponse({ ok: false, error: 'journal exploded' });
    }
    return jsonResponse({ ok: true, oanda_export_trades_seen: 1 });
  }
  if (target === '/download/export-file') {
    events.push('download-fetch');
    downloadCount += 1;
    if (scenario === 'download_failure' || scenario === 'combined_failure') {
      return {
        ok: false,
        status: 500,
        statusText: 'Failed',
        text: async () => 'download exploded',
        headers: { get: () => null },
      };
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => '',
      blob: async () => ({ size: 5 }),
      headers: { get: () => 'attachment; filename="server-export.csv"' },
    };
  }
  if (target.startsWith(`${startUrl}/`)) {
    return jsonResponse({ status: 'done', download_url: '/download/export-file' });
  }
  throw new Error(`Unexpected fetch: ${target}`);
};

const immediateTimeout = (callback) => {
  callback();
  return 1;
};
const context = {
  document,
  fetch,
  console: { error: (error) => errors.push(String(error?.message || error)) },
  setTimeout: immediateTimeout,
  clearTimeout: () => {},
  URL: {
    createObjectURL: () => 'blob:export',
    revokeObjectURL: () => {},
  },
  URLSearchParams,
  encodeURIComponent,
  Promise,
  window: {
    location: { search: '' },
    setTimeout: immediateTimeout,
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: jsPath });

(async () => {
  await exportBtn.click();
  const manualLinks = resultEl.children.filter((child) => child.tagName === 'A');
  process.stdout.write(JSON.stringify({
    events,
    errors,
    status: statusEl.textContent,
    statusColor: statusEl.style.color || '',
    resultPrefix: resultEl.textContent,
    manualLinkCount: manualLinks.length,
    manualHref: manualLinks[0]?.href || '',
    downloadCount,
    backfillCount,
  }));
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
"""


def _run_history_page_scenario(tmp_path: Path, broker: str, scenario: str) -> dict:
    node = shutil.which('node')
    assert node, 'node is required for JS behavior checks'
    harness = tmp_path / 'history_page_harness.js'
    harness.write_text(HISTORY_PAGE_NODE_HARNESS, encoding='utf-8')
    completed = subprocess.run(
        [node, str(harness), str(JS_PATH), broker, scenario],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_history_page_js_parses_with_node() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS syntax check'
    subprocess.run([node, '--check', str(JS_PATH)], check=True)


def test_history_page_uses_blob_download_and_no_window_open() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert 'window.open(' not in js
    assert 'downloadExportFile' in js
    assert 'URL.createObjectURL' in js
    assert 'URL.revokeObjectURL' in js
    assert 'setTimeout(() => URL.revokeObjectURL' in js
    assert '.download =' in js
    assert 'Export completed but no download URL was returned' in js
    assert 'Automatic download failed' in js
    assert 'Download started.' not in js


def test_oanda_backfill_failure_keeps_completed_export_download_available() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    done_block = js.split("if (state === 'done')", 1)[1].split("if (state === 'error')", 1)[0]
    assert done_block.index("const dl = st.download_url") < done_block.index("showManualDownload(dl")
    assert done_block.index("showManualDownload(dl") < done_block.index("downloadExportFile(dl")
    assert done_block.index("downloadExportFile(dl") < done_block.index("backfill-journal")
    assert "Export created. Starting download..." in done_block
    assert "Download requested. Updating Trading Journal..." in done_block
    assert "Export downloaded and Trading Journal updated." in done_block
    assert "Trading Journal backfill failed:" in done_block
    assert "setResult(" not in done_block


def test_oanda_runtime_download_precedes_backfill_and_keeps_manual_link(tmp_path: Path) -> None:
    payload = _run_history_page_scenario(tmp_path, 'oanda', 'success')
    assert payload['events'].index('manual-link') < payload['events'].index('download-fetch')
    assert payload['events'].index('download-fetch') < payload['events'].index('download-click')
    assert payload['events'].index('download-click') < payload['events'].index('backfill')
    assert payload['downloadCount'] == 1
    assert payload['backfillCount'] == 1
    assert payload['status'] == 'Export downloaded and Trading Journal updated.'
    assert 'Trading Journal updated' in payload['resultPrefix']
    assert payload['manualLinkCount'] == 1
    assert payload['manualHref'] == '/download/export-file'


def test_oanda_runtime_backfill_failure_retains_completed_download(tmp_path: Path) -> None:
    payload = _run_history_page_scenario(tmp_path, 'oanda', 'backfill_failure')
    assert payload['events'].index('download-click') < payload['events'].index('backfill')
    assert payload['downloadCount'] == 1
    assert payload['backfillCount'] == 1
    assert 'Trading Journal backfill failed: journal exploded' in payload['status']
    assert 'Export downloaded' in payload['resultPrefix']
    assert 'Trading Journal backfill failed: journal exploded' in payload['resultPrefix']
    assert payload['manualLinkCount'] == 1


def test_oanda_runtime_download_failure_still_backfills_and_keeps_both_results(tmp_path: Path) -> None:
    payload = _run_history_page_scenario(tmp_path, 'oanda', 'download_failure')
    assert payload['events'].index('manual-link') < payload['events'].index('download-fetch')
    assert payload['events'].index('download-fetch') < payload['events'].index('backfill')
    assert 'download-click' not in payload['events']
    assert payload['downloadCount'] == 1
    assert payload['backfillCount'] == 1
    assert payload['status'] == 'Automatic download failed, but Trading Journal updated.'
    assert 'Trading Journal updated' in payload['resultPrefix']
    assert 'Automatic download failed' in payload['resultPrefix']
    assert payload['manualLinkCount'] == 1


def test_oanda_runtime_combined_failures_keep_both_errors_and_manual_link(tmp_path: Path) -> None:
    payload = _run_history_page_scenario(tmp_path, 'oanda', 'combined_failure')
    assert payload['events'].index('manual-link') < payload['events'].index('download-fetch')
    assert payload['events'].index('download-fetch') < payload['events'].index('backfill')
    assert payload['downloadCount'] == 1
    assert payload['backfillCount'] == 1
    assert 'Automatic download failed' in payload['status']
    assert 'Trading Journal backfill failed' in payload['status']
    assert 'Automatic download failed' in payload['resultPrefix']
    assert 'Trading Journal backfill failed' in payload['resultPrefix']
    assert payload['manualLinkCount'] == 1


@pytest.mark.parametrize('broker', ['bybit', 'coinspot'])
def test_non_oanda_runtime_downloads_once_without_backfill(
    tmp_path: Path,
    broker: str,
) -> None:
    payload = _run_history_page_scenario(tmp_path, broker, 'success')
    assert payload['downloadCount'] == 1
    assert payload['backfillCount'] == 0
    assert 'backfill' not in payload['events']
    assert payload['status'] == 'Export downloaded.'
    assert payload['manualLinkCount'] == 1
