import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "trading_journal_equity_curve.js"
SERVICE_PATH = ROOT / "render" / "master_service.py"


def _run_node(expression: str) -> object:
    node = shutil.which("node")
    assert node, "node is required"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = {
  console,
  document: { getElementById: () => null },
  localStorage: { getItem: () => null, setItem: () => {} },
  devicePixelRatio: 2,
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_equity_curve.js' });
const value = vm.runInContext(process.argv[2], context);
process.stdout.write(JSON.stringify(value));
"""
    completed = subprocess.run(
        [node, "-e", harness, str(JS_PATH), expression],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_fixed_six_account_choices_order_aliases_and_bybit_demo_exclusion() -> None:
    values = _run_node(
        "TradingJournalEquityCurve.ACCOUNT_CHOICES.map((choice) => [choice.label, choice.value])"
    )
    assert values == [
        ["Binance", "BINANCE"],
        ["Bybit", "BYBIT"],
        ["Oanda demo", "OANDA DEMO"],
        ["Oanda live", "OANDA LIVE"],
        ["Pepperstone demo", "PEPPERSTONE DEMO"],
        ["Pepperstone live", "PEPPERSTONE LIVE"],
    ]
    aliases = _run_node(
        "['BINANCE','BYBIT','Bybit Live','BYBIT LIVE','OANDA DEMO','OANDA LIVE','PEPPERSTONE DEMO','PEPPERSTONE LIVE','Bybit Demo'].map(TradingJournalEquityCurve.canonicalAccount)"
    )
    assert aliases == [
        "BINANCE",
        "BYBIT",
        "BYBIT",
        "BYBIT",
        "OANDA DEMO",
        "OANDA LIVE",
        "PEPPERSTONE DEMO",
        "PEPPERSTONE LIVE",
        "",
    ]


def test_equity_normalization_filters_sorts_dedupes_and_honours_balance_precedence() -> None:
    rows = [
        {
            "id": "later",
            "account_label": "Bybit Live",
            "row_type": "trade",
            "close_time": "2026-01-03T00:00:00Z",
            "analysis_balance_after_trade": 130,
            "balance_after_trade": 120,
            "currency": "USDT",
        },
        {
            "id": "test",
            "account": "BYBIT",
            "row_type": "trade",
            "close_time": "2026-01-02T00:00:00Z",
            "balance_after_trade": 999,
            "currency": "USDT",
            "is_test_trade": True,
        },
        {
            "id": "reval",
            "account": "BYBIT LIVE",
            "row_type": "monthly_aud_reval",
            "close_time": "2026-01-02T00:00:00Z",
            "balance_after_trade": 888,
            "currency": "AUD",
        },
        {
            "id": "cash",
            "account": "BYBIT",
            "row_type": "cashflow",
            "open_time": "2026-01-01T00:00:00Z",
            "cashflow_new_balance": 100,
            "currency": "USDT",
        },
        {
            "id": "cash",
            "account": "BYBIT",
            "row_type": "cashflow",
            "open_time": "2026-01-01T00:00:00Z",
            "cashflow_new_balance": 100,
            "currency": "USDT",
        },
        {
            "id": "other",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2026-01-04T00:00:00Z",
            "balance_after_trade": 777,
            "currency": "AUD",
        },
    ]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'BYBIT').map((point) => [point.timestamp, point.balance, point.currency])"
    )
    points = _run_node(expression)
    assert [point[1] for point in points] == [100, 130]
    assert [point[0] for point in points] == sorted(point[0] for point in points)
    assert {point[2] for point in points} == {"USDT"}


def test_missing_row_currency_uses_selected_account_balance_currency() -> None:
    rows = [
        {
            "id": "one",
            "account": "BYBIT LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "balance_after_trade": 100,
            "currency": "",
        },
        {
            "id": "two",
            "account": "BYBIT",
            "row_type": "trade",
            "close_time": "2026-01-02T00:00:00Z",
            "analysis_balance_after_trade": 125,
        },
    ]
    balances = [{"label": "BYBIT", "currency": "USDT", "balance": 125}]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'BYBIT', "
        + json.dumps(balances)
        + ").map((point) => [point.balance, point.currency])"
    )
    assert _run_node(expression) == [[100, "USDT"], [125, "USDT"]]


def test_result_currency_does_not_override_account_balance_currency() -> None:
    rows = [
        {
            "id": "aud-account-usd-pnl",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "balance_after_trade": 1000,
            "result_currency": "USD",
        }
    ]
    balances = [
        {
            "label": "OANDA LIVE",
            "currency": "AUD",
            "balance": 1000,
        }
    ]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'OANDA LIVE', "
        + json.dumps(balances)
        + ").map((point) => [point.balance, point.currency])"
    )
    assert _run_node(expression) == [[1000, "AUD"]]


def test_saved_account_without_points_falls_back_to_first_account_with_data() -> None:
    rows = [
        {
            "id": "bybit-only",
            "account": "BYBIT LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "balance_after_trade": 100,
            "currency": "USDT",
        }
    ]
    expression = (
        "TradingJournalEquityCurve.preferredOrFirstAccountWithData("
        + json.dumps(rows)
        + ", 'OANDA LIVE')"
    )
    assert _run_node(expression) == "BYBIT"
    preferred_expression = (
        "TradingJournalEquityCurve.preferredOrFirstAccountWithData("
        + json.dumps(rows)
        + ", 'BYBIT LIVE')"
    )
    assert _run_node(preferred_expression) == "BYBIT"


def test_chart_y_axis_labels_include_selected_currency_and_dates() -> None:
    expression = r"""
(() => {
  const labels = [];
  const context = {
    setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, arc() {}, fill() {},
    fillText(value) { labels.push(String(value)); },
  };
  const canvas = {
    style: {},
    getBoundingClientRect: () => ({ width: 900, height: 440 }),
    getContext: () => context,
  };
  TradingJournalEquityCurve.drawChart(canvas, [
    { timestamp: Date.parse('2026-01-01T00:00:00Z'), balance: 100, currency: 'USDT' },
    { timestamp: Date.parse('2026-01-03T00:00:00Z'), balance: 125, currency: 'USDT' },
  ]);
  return labels;
})()
"""
    labels = _run_node(expression)
    assert sum(label.endswith(" USDT") for label in labels) == 5
    assert any("Jan" in label for label in labels)


def test_chart_measures_y_labels_and_keeps_them_inside_narrow_canvas() -> None:
    expression = r"""
(() => {
  const labels = [];
  const context = {
    setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, arc() {}, fill() {},
    measureText(value) { return { width: String(value).length * 8 }; },
    fillText(value, x) { labels.push({ value: String(value), x }); },
  };
  const canvas = {
    style: {},
    getBoundingClientRect: () => ({ width: 260, height: 440 }),
    getContext: () => context,
  };
  TradingJournalEquityCurve.drawChart(canvas, [
    { timestamp: Date.parse('2026-01-01T00:00:00Z'), balance: 123456789, currency: 'USDT' },
    { timestamp: Date.parse('2026-01-03T00:00:00Z'), balance: 123556789, currency: 'USDT' },
  ]);
  return {
    width: canvas.width,
    leftEdges: labels
      .filter((item) => item.value.endsWith(' USDT'))
      .map((item) => item.x - context.measureText(item.value).width),
  };
})()
"""
    result = _run_node(expression)
    assert result["width"] == 520
    assert result["leftEdges"]
    assert min(result["leftEdges"]) >= 0


def test_equity_dom_refresh_resize_account_change_empty_and_error_states() -> None:
    node = shutil.which("node")
    assert node, "node is required"
    harness = r"""
const fs = require('fs');
const vm = require('vm');

(async () => {
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const elementListeners = {};
  const windowListeners = {};
  const timers = [];
  let rectWidth = 900;
  let drawCount = 0;
  let fetchCount = 0;
  const makeElement = (id) => ({
    id,
    value: id === 'journal-equity-account' ? 'BINANCE' : '',
    disabled: false,
    innerHTML: '',
    textContent: '',
    style: {},
    classList: {
      error: false,
      toggle(name, enabled) { if (name === 'error') this.error = Boolean(enabled); },
    },
    addEventListener(type, listener) {
      elementListeners[`${id}:${type}`] = listener;
    },
  });
  const account = makeElement('journal-equity-account');
  const refresh = makeElement('journal-equity-refresh-btn');
  const summary = makeElement('journal-equity-summary');
  const state = makeElement('journal-equity-state');
  const context2d = {
    setTransform() { drawCount += 1; },
    clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
    arc() {}, fill() {}, fillText() {},
  };
  const canvas = {
    ...makeElement('journal-equity-canvas'),
    width: 0,
    height: 0,
    clientWidth: 900,
    clientHeight: 440,
    getBoundingClientRect: () => ({ width: rectWidth, height: 440 }),
    getContext: () => context2d,
  };
  const elements = {
    'journal-equity-account': account,
    'journal-equity-refresh-btn': refresh,
    'journal-equity-canvas': canvas,
    'journal-equity-summary': summary,
    'journal-equity-state': state,
  };
  const response = (payload, ok = true) => ({
    ok,
    json: async () => payload,
  });
  const queue = [
    response({
      ok: true,
      items: [{
        id: 'bybit-1',
        account: 'BYBIT LIVE',
        row_type: 'trade',
        close_time: '2026-01-01T00:00:00Z',
        balance_after_trade: 100,
        currency: 'USDT',
      }],
    }),
  ];
  const context = {
    console,
    document: { getElementById: (id) => elements[id] || null },
    localStorage: { getItem: () => null, setItem: () => {} },
    devicePixelRatio: 2,
    fetch: async () => {
      fetchCount += 1;
      if (!queue.length) throw new Error('missing mocked response');
      return queue.shift();
    },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    CustomEvent: class CustomEvent {
      constructor(type, options) { this.type = type; this.detail = options?.detail; }
    },
    addEventListener(type, listener) { windowListeners[type] = listener; },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'trading_journal_equity_curve.js' });
  const flush = async () => {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
  };
  await flush();
  const initial = {
    account: account.value,
    summary: summary.innerHTML,
    stateDisplay: state.style.display,
    canvasWidth: canvas.width,
  };

  account.value = 'OANDA LIVE';
  elementListeners['journal-equity-account:change']();
  const accountEmpty = state.textContent;

  queue.push(response({ ok: true, items: [] }));
  await elementListeners['journal-equity-refresh-btn:click']();
  const refreshEmpty = state.textContent;

  queue.push(response({ ok: false, error: 'authoritative fetch failed' }, false));
  await windowListeners['trading-journal:data-changed']();
  const errorState = { text: state.textContent, error: state.classList.error };

  queue.push(response({
    ok: true,
    items: [{
      id: 'oanda-1',
      account: 'OANDA LIVE',
      row_type: 'trade',
      close_time: '2026-01-02T00:00:00Z',
      balance_after_trade: 1200,
      currency: 'AUD',
    }],
  }));
  await elementListeners['journal-equity-refresh-btn:click']();
  rectWidth = 260;
  windowListeners.resize();
  while (timers.length) timers.shift()();

  process.stdout.write(JSON.stringify({
    initial,
    accountEmpty,
    refreshEmpty,
    errorState,
    finalSummary: summary.innerHTML,
    resizedWidth: canvas.width,
    canvasStyleWidth: canvas.style.width,
    drawCount,
    fetchCount,
    refreshEnabled: !refresh.disabled,
  }));
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exit(1);
});
"""
    completed = subprocess.run(
        [node, "-e", harness, str(JS_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["initial"]["account"] == "BYBIT"
    assert "Current equity" in result["initial"]["summary"]
    assert result["initial"]["stateDisplay"] == "none"
    assert result["initial"]["canvasWidth"] == 1800
    assert "No equity data is available for Oanda live" in result["accountEmpty"]
    assert "No equity data is available for Oanda live" in result["refreshEmpty"]
    assert result["errorState"] == {
        "text": "authoritative fetch failed",
        "error": True,
    }
    assert "1 point" in result["finalSummary"]
    assert "AUD" in result["finalSummary"]
    assert result["resizedWidth"] == 520
    assert result["canvasStyleWidth"] == "100%"
    assert result["drawCount"] >= 3
    assert result["fetchCount"] == 4
    assert result["refreshEnabled"] is True


def test_equity_script_has_one_selected_curve_axes_refresh_resize_and_states() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    service = SERVICE_PATH.read_text(encoding="utf-8")
    assert "accountSelect.value" in source
    assert "drawChart(canvas, points)" in source
    assert "yTicks = 5" in source
    assert "xTicks = Math.min(6" in source
    assert "formatDate(timestamp)" in source
    assert "devicePixelRatio" in source
    assert "window.addEventListener('resize'" in source
    assert "refreshButton.addEventListener('click', load)" in source
    assert "window.addEventListener('trading-journal:data-changed', load)" in source
    assert "Loading authoritative Trading Journal data" in source
    assert "No equity data is available" in source
    assert "setChartState(error?.message" in source
    assert "journal-equity-account" in service
    assert "journal-equity-refresh-btn" in service
    assert "grid-template-columns:minmax(300px,340px) minmax(0,1fr)" in service
    assert ".equity-panel{min-width:0;" in service
    assert ".equity-chart-wrap{position:relative;flex:1;min-width:0;" in service
    assert ".equity-canvas{width:100%;max-width:100%;" in service
    assert ".workspace{grid-template-columns:minmax(0,1fr);padding:10px}" in service
    assert "@media(max-width:780px)" in service
