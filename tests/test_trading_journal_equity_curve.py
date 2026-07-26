import asyncio
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


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


def _load_master_service_for_equity_integration():
    module_name = "render_master_service_equity_integration"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "render" / "master_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


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


def test_canonical_workbook_snapshot_endpoint_and_actual_js_normalizer_release_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _load_master_service_for_equity_integration()
    workbook = ROOT / "journal" / "Trading Journal.xlsx"
    before_bytes = workbook.read_bytes()
    before_sha256 = hashlib.sha256(before_bytes).hexdigest()
    before_stat = workbook.stat()

    monkeypatch.setenv("TRADING_JOURNAL_SOURCE", "master_journal")
    monkeypatch.setenv("TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE", "1")
    monkeypatch.setattr(service, "TRADING_JOURNAL_SOURCE", "master_journal")
    monkeypatch.setattr(service, "TRADING_JOURNAL_LOCAL_DIR", workbook.parent)
    monkeypatch.setattr(service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(service, "_master_journal_authoritative_enabled", lambda: True)
    monkeypatch.setattr(service, "TRADING_JOURNAL_VIEW_CACHE_PATH", tmp_path / "view-cache.json")
    monkeypatch.setattr(service, "TRADING_JOURNAL_SQLITE_PATH", tmp_path / "view-cache.sqlite")
    monkeypatch.setattr(service, "TRADING_JOURNAL_PATH", tmp_path / "journal-state.json")
    monkeypatch.setattr(service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "journal-source-state.json")
    monkeypatch.setattr(service, "OANDA_FILL_STATE_PATH", tmp_path / "oanda-fill-state.json")
    monkeypatch.setattr(service, "MONTHLY_AUD_REVALUATION_PATH", tmp_path / "monthly-aud.json")
    monkeypatch.setattr(service, "MONTHLY_AUD_REVALUATION_STATE_PATH", tmp_path / "monthly-aud-state.json")
    service._TRADING_JOURNAL_VIEW_CACHE["key"] = None
    service._TRADING_JOURNAL_VIEW_CACHE["payload"] = None
    service.TRADING_JOURNAL_EQUITY_REFRESH_TASK = None
    service.TRADING_JOURNAL_EQUITY_REFRESH_STATE.update(
        {
            "running": False,
            "pending": False,
            "ok": None,
            "error": None,
            "requested_source_fingerprints": None,
        }
    )

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("equity cache refresh must not fetch brokers or rewrite the workbook")

    async def _forbidden_async(*_args, **_kwargs):
        raise AssertionError("equity cache refresh must not fetch brokers")

    monkeypatch.setattr(service, "_get_excel_account_balances", _forbidden)
    monkeypatch.setattr(service, "_fetch_bybit_balance_usdt", _forbidden_async)
    monkeypatch.setattr(service, "_fetch_oanda_account_summary", _forbidden_async)
    monkeypatch.setattr(service, "_run_bybit_closed_pnl_sync", _forbidden_async)
    monkeypatch.setattr(service, "_recover_oanda_recent_fills", _forbidden_async)
    monkeypatch.setattr(service, "_sync_master_journal_workbook", _forbidden)
    monkeypatch.setattr(service, "update_master_journal_workbook_data_only", _forbidden)
    monkeypatch.setattr(service, "build_master_journal_workbook", _forbidden)

    async def _build_and_fetch():
        queued = await service.trading_journal_equity_refresh()
        task = service.TRADING_JOURNAL_EQUITY_REFRESH_TASK
        assert isinstance(task, asyncio.Task)
        await task
        status = await service.trading_journal_equity_refresh_status()
        response = await service.trading_journal_items()
        return queued, status, response

    queued, status, response = asyncio.run(_build_and_fetch())
    assert queued.status_code == 202
    assert status.status_code == 200
    assert response.status_code == 200
    serialized_response = response.body.decode("utf-8")
    payload = json.loads(serialized_response)
    assert payload["ok"] is True
    assert payload["snapshot_current"] is True
    assert payload["snapshot_stale"] is False
    assert payload["equity_cache"]["verified"] is True

    payload_path = tmp_path / "actual-endpoint-response.json"
    payload_path.write_text(serialized_response, encoding="utf-8")
    node = shutil.which("node")
    assert node, "node is required"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const context = {
  console,
  document: { getElementById: () => null },
  localStorage: { getItem: () => null, setItem: () => {} },
  devicePixelRatio: 1,
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'trading_journal_equity_curve.js' });
const counts = Object.fromEntries(
  context.TradingJournalEquityCurve.ACCOUNT_CHOICES.map((choice) => [
    choice.value,
    context.TradingJournalEquityCurve.normalizeEquityPoints(
      payload.items,
      choice.value,
      payload.balances,
    ).length,
  ]),
);
process.stdout.write(JSON.stringify(counts));
"""
    completed = subprocess.run(
        [node, "-e", harness, str(JS_PATH), str(payload_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    counts = json.loads(completed.stdout)
    expected_counts = {
        "BINANCE": 113,
        "BYBIT": 265,
        "OANDA DEMO": 46,
        "OANDA LIVE": 63,
        "PEPPERSTONE DEMO": 824,
        "PEPPERSTONE LIVE": 193,
    }
    assert counts == expected_counts
    assert payload["equity_cache"]["point_counts"] == expected_counts
    assert all(count > 0 for count in counts.values())

    after_stat = workbook.stat()
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == before_sha256
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert service.TRADING_JOURNAL_VIEW_CACHE_PATH.exists()


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
  const response = (payload, ok = true, status = ok ? 200 : 500) => ({
    ok,
    status,
    json: async () => payload,
  });
  const pointCounts = (overrides = {}) => ({
    BINANCE: 0,
    BYBIT: 0,
    'OANDA DEMO': 0,
    'OANDA LIVE': 0,
    'PEPPERSTONE DEMO': 0,
    'PEPPERSTONE LIVE': 0,
    ...overrides,
  });
  const authoritative = (items, counts, balances = []) => response({
    ok: true,
    snapshot_current: true,
    snapshot_stale: false,
    equity_cache: { verified: true, point_counts: counts },
    items,
    balances,
  });
  const queue = [
    authoritative(
      [{
        id: 'bybit-1',
        account: 'BYBIT LIVE',
        row_type: 'trade',
        close_time: '2026-01-01T00:00:00Z',
        balance_after_trade: 100,
        currency: 'USDT',
      }],
      pointCounts({ BYBIT: 1 }),
      [{ label: 'BYBIT', currency: 'USDT' }],
    ),
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
    setTimeout: (fn, delay) => {
      if (delay === 1250) {
        fn();
        return -1;
      }
      timers.push(fn);
      return timers.length;
    },
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

  queue.push(
    response({ ok: false, pending: true }, true, 202),
    response({ ok: false, pending: true }, true, 202),
    response({ ok: true, pending: false }),
    authoritative([], pointCounts()),
  );
  await elementListeners['journal-equity-refresh-btn:click']();
  const refreshEmpty = state.textContent;

  queue.push(
    response({ ok: true, pending: false }),
    response({
      ok: true,
      snapshot_current: false,
      snapshot_stale: true,
      warning: 'Cached equity data is stale.',
      items: [],
    }),
  );
  await windowListeners['trading-journal:data-changed']();
  const staleState = { text: state.textContent, error: state.classList.error };

  queue.push(response({ ok: false, error: 'authoritative fetch failed' }, false, 500));
  await windowListeners['trading-journal:data-changed']();
  const failureState = { text: state.textContent, error: state.classList.error };

  queue.push(
    response({ ok: false, pending: true }, true, 202),
    response({ ok: true, pending: false }),
    authoritative([{
      id: 'oanda-1',
      account: 'OANDA LIVE',
      row_type: 'trade',
      close_time: '2026-01-02T00:00:00Z',
      balance_after_trade: 1200,
      currency: 'AUD',
    }], pointCounts({ 'OANDA LIVE': 1 }), [{ label: 'OANDA LIVE', currency: 'AUD' }]),
  );
  await elementListeners['journal-equity-refresh-btn:click']();
  rectWidth = 260;
  windowListeners.resize();
  while (timers.length) timers.shift()();

  process.stdout.write(JSON.stringify({
    initial,
    accountEmpty,
    refreshEmpty,
    staleState,
    failureState,
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
    assert result["staleState"] == {
        "text": "Cached equity data is stale.",
        "error": True,
    }
    assert result["failureState"] == {
        "text": "authoritative fetch failed",
        "error": True,
    }
    assert "1 point" in result["finalSummary"]
    assert "AUD" in result["finalSummary"]
    assert result["resizedWidth"] == 520
    assert result["canvasStyleWidth"] == "100%"
    assert result["drawCount"] >= 3
    assert result["fetchCount"] == 11
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
    assert "refreshButton.addEventListener('click', () => load({ forceRefresh: true }))" in source
    assert "'trading-journal:data-changed'" in source
    assert "REFRESH_STATUS_URL" in source
    assert "payload?.snapshot_current !== true" in source
    assert "payload?.snapshot_stale === true" in source
    assert "payload?.equity_cache?.verified !== true" in source
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
