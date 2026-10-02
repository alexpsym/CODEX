import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "instrument_lookup.js"


def _run_harness(mode: str) -> dict:
    node = shutil.which("node")
    assert node, "node is required for Instrument Lookup behavior tests"
    harness = r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const mode = process.argv[2];
class Element {
  constructor(id) {
    this.id = id; this.value = ''; this.textContent = ''; this.innerHTML = '';
    this.dataset = {}; this.style = {}; this.listeners = {}; this.buttons = [];
    this.tagName = 'DIV'; this.parentNode = null; this.scrollWidth = 0;
    this.clientWidth = 0; this.scrollLeft = 0;
    this.classList = { toggle(){}, add(){}, remove(){} };
  }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  querySelectorAll(selector) { return selector === 'button[data-asset]' ? this.buttons : []; }
  closest() { return null; }
  querySelector() { return null; }
  setAttribute() {}
}
const ids = ['q','load','rows','err','asset-toggle','journal-status','journal-metrics','trade-head','trade-body','recent-trades-section','trade-scroll-top','trade-scroll-spacer','trade-table-wrap'];
const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
const historyCalls = [];
const document = {
  head: { appendChild() {} },
  getElementById(id) { return elements[id] || null; },
  createElement(tag) { return new Element(tag); },
};
const response = (payload, ok = true) => ({
  ok, status: ok ? 200 : 500, statusText: ok ? 'OK' : 'FAIL',
  text: async () => JSON.stringify(payload),
});
const deferred = {};
const defer = (key) => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  deferred[key] = { resolve, reject, promise };
  return promise;
};
let fetch;
if (mode === 'render') {
  fetch = async (url) => {
    const query = new URL(url, 'https://local').searchParams.get('query');
    if (url.startsWith('/api/instrument-specs')) {
      if (query === 'AUDUSD') return response({ source: 'oanda', resolved_symbol: 'AUD_USD', lastPrice: 0.66 });
      return response({
        source: 'bybit', resolved_symbol: 'UAIUSDT',
        'range.1m': 0.0123, 'range.5m': 0.0263, 'range.15m': 0.02631,
        'range.30m': 0.02634, 'range.1h': 0,
        _btc_reference: { 'range.1m': 0.111, 'range.5m': 0.222 },
      });
    }
    return response({ status: 'no_data', canonical_symbol: query === 'AUDUSD' ? 'AUD_USD' : 'UAIUSDT', trades: [] });
  };
} else {
  fetch = (url) => {
    const parsed = new URL(url, 'https://local');
    const isSpecs = parsed.pathname === '/api/instrument-specs';
    const key = isSpecs
      ? `specs:${parsed.searchParams.get('query')}`
      : `journal:${parsed.searchParams.get('symbol')}`;
    return defer(key);
  };
}
const context = {
  document, fetch, console, URL, URLSearchParams, Date, Intl,
  history: { replaceState(_a, _b, url) { historyCalls.push(url); } },
  window: { location: { search: '' }, requestAnimationFrame: (fn) => fn(), addEventListener() {} },
  setTimeout, clearTimeout,
};
context.window.window = context.window;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'instrument_lookup.js' });
const flush = async () => { await new Promise((resolve) => setImmediate(resolve)); await new Promise((resolve) => setImmediate(resolve)); };
const start = async (query) => { elements.q.value = query; return elements.load.listeners.click(); };
(async () => {
  if (mode === 'render') {
    await start('UAIUSDT'); await flush();
    const crypto = elements.rows.innerHTML;
    await start('AUDUSD'); await flush();
    console.log(JSON.stringify({ crypto, fx: elements.rows.innerHTML, query: elements.q.value, url: historyCalls.at(-1), asset: elements['asset-toggle'].buttons.length }));
    return;
  }
  if (mode === 'specs') {
    const old = start('OLD');
    const fresh = start('NEW');
    deferred['specs:NEW'].resolve(response({ source: 'bybit', resolved_symbol: 'NEW', 'range.1m': 0.0123 }));
    await flush();
    deferred['journal:NEW'].resolve(response({ status: 'no_data', canonical_symbol: 'NEW', trades: [] }));
    await fresh; await flush();
    deferred['specs:OLD'].reject(new Error('old specs failure'));
    await old; await flush();
  } else {
    const old = start('OLD');
    deferred['specs:OLD'].resolve(response({ source: 'bybit', resolved_symbol: 'OLD', 'range.1m': 0.0123 }));
    await flush();
    const fresh = start('NEW');
    deferred['specs:NEW'].resolve(response({ source: 'bybit', resolved_symbol: 'NEW', 'range.1m': 0.0263 }));
    await flush();
    deferred['journal:NEW'].resolve(response({ status: 'no_data', canonical_symbol: 'NEW', trades: [] }));
    await fresh; await flush();
    deferred['journal:OLD'].reject(new Error('old journal failure'));
    await old; await flush();
  }
  console.log(JSON.stringify({ rows: elements.rows.innerHTML, journal: elements['journal-metrics'].innerHTML, journalStatus: elements['journal-status'].textContent, error: elements.err.textContent, query: elements.q.value, url: historyCalls.at(-1) }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run(
        [node, "-e", harness, str(JS_PATH), mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_instrument_lookup_renders_ranges_without_cross_field_or_btc_reuse() -> None:
    rendered = _run_harness("render")
    assert "1.23%" in rendered["crypto"]
    assert rendered["crypto"].count("2.63%") >= 3
    assert "0.00%" in rendered["crypto"]
    assert "BTC reference: 11.10%" in rendered["crypto"]
    assert "BTC reference: 22.20%" in rendered["crypto"]
    assert "Daily range" not in rendered["crypto"]
    assert "Range 1m" not in rendered["fx"]
    assert "BTC reference" not in rendered["fx"]
    assert "1.23%" not in rendered["fx"]
    assert rendered["query"] == "AUD_USD"
    assert rendered["url"] == "/instrument-lookup?q=AUD_USD"


@pytest.mark.parametrize("phase", ["specs", "journal"])
def test_instrument_lookup_ignores_superseded_responses(phase: str) -> None:
    result = _run_harness(phase)
    assert "NEW" in result["rows"]
    assert "OLD" not in result["rows"]
    assert "NEW" in result["journalStatus"]
    assert "old " not in result["error"].lower()
    assert result["query"] == "NEW"
    assert result["url"] == "/instrument-lookup?q=NEW"
