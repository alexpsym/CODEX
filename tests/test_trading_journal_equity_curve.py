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
VERIFIED_BALANCE_SOURCE = "cashflow_anchor_plus_trade_results"


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


def test_every_account_curve_starts_at_exactly_100_percent() -> None:
    rows = [
        {
            "id": f"trade-{account}",
            "account": account,
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "analysis_balance_before_trade": 100,
            "analysis_balance_after_trade": 105,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": 5,
        }
        for account in (
            "BINANCE",
            "BYBIT",
            "OANDA DEMO",
            "OANDA LIVE",
            "PEPPERSTONE DEMO",
            "PEPPERSTONE LIVE",
        )
    ]
    expression = (
        "Object.fromEntries(TradingJournalEquityCurve.ACCOUNT_CHOICES.map("
        "(choice) => [choice.value, TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", choice.value).map((point) => point.value)]))"
    )
    assert _run_node(expression) == {
        "BINANCE": [100, 105],
        "BYBIT": [100, 105],
        "OANDA DEMO": [100, 105],
        "OANDA LIVE": [100, 105],
        "PEPPERSTONE DEMO": [100, 105],
        "PEPPERSTONE LIVE": [100, 105],
    }


def test_equity_normalization_compounds_returns_and_keeps_cashflows_flat() -> None:
    rows = [
        {
            "id": "trade-2",
            "account_label": "Bybit Live",
            "row_type": "trade",
            "close_time": "2026-01-03T00:00:00Z",
            "analysis_balance_before_trade": 1495,
            "analysis_balance_after_trade": 1480.05,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": -14.95,
            "currency": "USDT",
        },
        {
            "id": "test",
            "account": "BYBIT",
            "row_type": "trade",
            "close_time": "2026-01-02T00:00:00Z",
            "analysis_balance_before_trade": 495,
            "analysis_balance_after_trade": 999,
            "currency": "USDT",
            "is_test_trade": True,
        },
        {
            "id": "reval",
            "account": "BYBIT LIVE",
            "row_type": "monthly_aud_reval",
            "close_time": "2026-01-02T00:00:00Z",
            "analysis_balance_before_trade": 495,
            "analysis_balance_after_trade": 888,
            "currency": "AUD",
        },
        {
            "id": "deposit",
            "account": "BYBIT",
            "row_type": "cashflow",
            "open_time": "2026-01-02T00:00:00Z",
            "cashflow_new_balance": 1495,
            "currency": "USDT",
        },
        {
            "id": "deposit",
            "account": "BYBIT",
            "row_type": "cashflow",
            "open_time": "2026-01-02T00:00:00Z",
            "cashflow_new_balance": 1495,
            "currency": "USDT",
        },
        {
            "id": "trade-1",
            "account": "BYBIT",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "analysis_balance_before_trade": 500,
            "analysis_balance_after_trade": 495,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": -5,
            "currency": "USDT",
        },
        {
            "id": "pre-trade-cashflow",
            "account": "BYBIT",
            "row_type": "cashflow",
            "open_time": "2025-12-31T00:00:00Z",
            "cashflow_new_balance": 500,
            "currency": "USDT",
        },
        {
            "id": "other",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2026-01-04T00:00:00Z",
            "analysis_balance_before_trade": 700,
            "analysis_balance_after_trade": 777,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": 77,
            "currency": "AUD",
        },
    ]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'BYBIT').map((point) => [point.timestamp, point.value, point.eventType])"
    )
    points = _run_node(expression)
    assert [point[1] for point in points] == pytest.approx([100, 99, 99, 98.01])
    assert [point[2] for point in points] == ["baseline", "trade", "cashflow", "trade"]
    assert [point[0] for point in points] == sorted(point[0] for point in points)


def test_risk_based_result_pct_is_not_used_as_an_account_equity_return() -> None:
    rows = [
        {
            "id": "risk-return-only",
            "account": "BYBIT LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "balance_after_trade": 105,
            "result_pct": 50,
        },
        {
            "id": "verified-equity-return",
            "account": "BYBIT",
            "row_type": "trade",
            "close_time": "2026-01-02T00:00:00Z",
            "analysis_balance_before_trade": 100,
            "analysis_balance_after_trade": 99,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": -1,
        },
    ]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'BYBIT').map((point) => [point.value, point.eventType])"
    )
    assert _run_node(expression) == [[100, "baseline"], [99, "trade"]]


def test_bare_or_contradictory_return_fields_cannot_override_verified_pnl() -> None:
    rows = [
        {
            "id": "bare-explicit",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2025-12-31T00:00:00Z",
            "equity_return_pct": 900,
        },
        {
            "id": "loss",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "analysis_balance_before_trade": 100,
            "analysis_balance_after_trade": 1000,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": -1,
            "equity_return_pct": 900,
            "result_currency": "USD",
        },
        {
            "id": "gain",
            "account": "OANDA LIVE",
            "row_type": "trade",
            "close_time": "2026-01-02T00:00:00Z",
            "analysis_balance_before_trade": 99,
            "analysis_balance_after_trade": 1,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": 0.99,
            "equity_return_pct": -50,
            "result_currency": "AUD",
        },
    ]
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(rows)
        + ", 'OANDA LIVE').map((point) => point.value)"
    )
    assert _run_node(expression) == pytest.approx([100, 99, 99.99])


def test_saved_account_without_points_falls_back_to_first_account_with_data() -> None:
    rows = [
        {
            "id": "bybit-only",
            "account": "BYBIT LIVE",
            "row_type": "trade",
            "close_time": "2026-01-01T00:00:00Z",
            "analysis_balance_before_trade": 100,
            "analysis_balance_after_trade": 101,
            "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            "net_profit": 1,
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


def test_chart_y_axis_labels_are_percentages_and_dates_remain_on_x_axis() -> None:
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
    { timestamp: Date.parse('2026-01-01T00:00:00Z'), value: 100 },
    { timestamp: Date.parse('2026-01-03T00:00:00Z'), value: 125 },
  ]);
  return labels;
})()
"""
    labels = _run_node(expression)
    assert sum(label.endswith("%") for label in labels) == 5
    assert not any("USDT" in label or "AUD" in label for label in labels)
    assert any("Jan" in label for label in labels)


def test_invalid_percentage_placeholders_are_single_utf8_em_dashes() -> None:
    code_points = _run_node(
        "[TradingJournalEquityCurve.formatPercentage(NaN), "
        "TradingJournalEquityCurve.formatHoverPercentage(NaN)]"
        ".map((value) => Array.from(value).map((character) => "
        "character.codePointAt(0)))"
    )

    assert code_points == [[0x2014], [0x2014]]


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
    { timestamp: Date.parse('2026-01-01T00:00:00Z'), value: 123456789 },
    { timestamp: Date.parse('2026-01-03T00:00:00Z'), value: 123556789 },
  ]);
  return {
    width: canvas.width,
    leftEdges: labels
      .filter((item) => item.value.endsWith('%'))
      .map((item) => item.x - context.measureText(item.value).width),
  };
})()
"""
    result = _run_node(expression)
    assert result["width"] == 520
    assert result["leftEdges"]
    assert min(result["leftEdges"]) >= 0


def test_hover_geometry_maps_plot_edges_and_constant_spans_exactly() -> None:
    result = _run_node(
        """(() => {
          const api = TradingJournalEquityCurve;
          const points = [
            {timestamp: 1000, value: 90, eventType: 'trade'},
            {timestamp: 2000, value: 110, eventType: 'trade'},
          ];
          const geometry = api.buildChartGeometry(points, 500, 400, 40);
          const constant = api.buildChartGeometry([
            {timestamp: 1234, value: 100, eventType: 'trade'},
          ], 260, 360, 120);
          return {
            margin: geometry.margin,
            plotWidth: geometry.plotWidth,
            plotHeight: geometry.plotHeight,
            minTime: geometry.minTime,
            maxTime: geometry.maxTime,
            leftTime: api.timestampForX(geometry, geometry.margin.left),
            rightTime: api.timestampForX(geometry, geometry.margin.left + geometry.plotWidth),
            topValue: api.valueForY(geometry, geometry.margin.top),
            bottomValue: api.valueForY(geometry, geometry.margin.top + geometry.plotHeight),
            xRoundTrip: api.timestampForX(geometry, api.xForTimestamp(geometry, 1500)),
            yRoundTrip: api.valueForY(geometry, api.yForValue(geometry, 100)),
            brisbaneDateTime: api.formatDateTime(Date.parse('2026-01-01T00:00:00Z')),
            constantTimeSpan: constant.timeSpan,
            constantValueSpan: constant.valueSpan,
            constantLeftMargin: constant.margin.left,
          };
        })()"""
    )
    assert result["margin"] == {"left": 64, "right": 28, "top": 24, "bottom": 54}
    assert result["plotWidth"] == 408
    assert result["plotHeight"] == 322
    assert result["minTime"] == result["leftTime"] == 1000
    assert result["maxTime"] == result["rightTime"] == 2000
    assert result["topValue"] == pytest.approx(111.6)
    assert result["bottomValue"] == pytest.approx(88.4)
    assert result["xRoundTrip"] == pytest.approx(1500)
    assert result["yRoundTrip"] == pytest.approx(100)
    assert result["brisbaneDateTime"] == "01 Jan 2026, 10:00:00"
    assert result["constantTimeSpan"] == 1
    assert result["constantValueSpan"] == pytest.approx(1)
    assert result["constantLeftMargin"] == 138


def test_nearest_actual_point_skips_synthetic_baseline_and_handles_edges_and_ties() -> None:
    result = _run_node(
        """(() => {
          const points = [
            {timestamp: 1000, value: 100, eventType: 'baseline', identity: 'baseline'},
            {timestamp: 1000, value: 101, eventType: 'trade', identity: 'first'},
            {timestamp: 2000, value: 102, eventType: 'trade', identity: 'second'},
            {timestamp: 4000, value: 103, eventType: 'cashflow', identity: 'third'},
          ];
          const nearest = TradingJournalEquityCurve.nearestActualPoint;
          return [
            nearest(points, -1)?.identity,
            nearest(points, 1500)?.identity,
            nearest(points, 3999)?.identity,
            nearest([{timestamp: 1, value: 100, eventType: 'baseline'}], 1),
          ];
        })()"""
    )
    assert result == ["first", "first", "third", None]


def test_backend_equity_return_cache_proof_and_browser_coverage_match() -> None:
    service = _load_master_service_for_equity_integration()
    spoofed_timeline = service._build_journal_balance_timelines(
        [
            {
                "id": "spoofed-analysis",
                "row_type": "trade",
                "source": "manual",
                "account": "BINANCE",
                "close_time": "2025-12-31T00:00:00Z",
                "net_profit": 5,
                "analysis_balance_before_trade": 1,
                "analysis_balance_after_trade": 6,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "equity_return_pct": 500,
            }
        ],
        {},
        [],
    )
    spoofed_row = spoofed_timeline["rows"][0]
    assert "analysis_balance_before_trade" not in spoofed_row
    assert "analysis_balance_before_trade_source" not in spoofed_row
    assert service._enrich_trade_row_metrics([spoofed_row])[0][
        "equity_return_pct"
    ] is None

    enriched = service._enrich_trade_row_metrics(
        [
            {
                "id": "verified",
                "row_type": "trade",
                "account": "BINANCE",
                "close_time": "2026-01-01T00:00:00Z",
                "analysis_balance_before_trade": 500,
                "analysis_balance_after_trade": 495,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "net_profit": -5,
                "risk_amount": 1,
            },
            {
                "id": "risk-only",
                "row_type": "trade",
                "account": "BINANCE",
                "close_time": "2026-01-02T00:00:00Z",
                "net_profit": 5,
                "risk_amount": 1,
            },
            {
                "id": "explicit",
                "row_type": "trade",
                "account": "BINANCE",
                "close_time": "2026-01-03T00:00:00Z",
                "equity_return_pct": 2.5,
                "equity_return_basis": "verified_import",
            },
            {
                "id": "missing-pnl",
                "row_type": "trade",
                "account": "BINANCE",
                "close_time": "2026-01-04T00:00:00Z",
                "analysis_balance_before_trade": 495,
                "analysis_balance_after_trade": 495,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
            },
            {
                "id": "inconsistent-after",
                "row_type": "trade",
                "account": "BINANCE",
                "close_time": "2026-01-05T00:00:00Z",
                "analysis_balance_before_trade": 100,
                "analysis_balance_after_trade": 1000,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "net_profit": 5,
                "equity_return_pct": 900,
            },
        ]
    )
    assert enriched[0]["equity_return_pct"] == pytest.approx(-1)
    assert (
        enriched[0]["equity_return_basis"]
        == "net_trade_result_over_analysis_balance_before_trade"
    )
    assert enriched[1]["result_pct"] == pytest.approx(500)
    assert enriched[1]["equity_return_pct"] is None
    assert enriched[1]["equity_return_basis"] is None
    assert enriched[2]["equity_return_pct"] is None
    assert enriched[2]["equity_return_basis"] is None
    assert enriched[3]["equity_return_pct"] is None
    assert enriched[3]["equity_return_basis"] is None
    assert enriched[4]["equity_return_pct"] == pytest.approx(5)
    assert (
        enriched[4]["equity_return_basis"]
        == "net_trade_result_over_analysis_balance_before_trade"
    )

    items = [
        {
            "id": "aaa-same-time-deposit",
            "row_type": "cashflow",
            "account": "BINANCE",
            "close_time": "2026-01-01T00:00:00Z",
            "cashflow_new_balance": 500,
        },
        {
            "id": "aaa-same-time-deposit",
            "row_type": "cashflow",
            "account": "BINANCE",
            "close_time": "2026-01-01T00:00:00Z",
            "cashflow_new_balance": 500,
        },
        enriched[0],
        enriched[1],
        enriched[2],
        enriched[3],
        enriched[4],
    ]
    assert service._equity_point_counts(items, [])["BINANCE"] == 4
    expression = (
        "TradingJournalEquityCurve.normalizeEquityPoints("
        + json.dumps(items)
        + ", 'BINANCE').map((point) => point.eventType)"
    )
    assert _run_node(expression) == ["baseline", "cashflow", "trade", "trade"]

    fingerprints = {"source": "unit-test"}
    snapshot = {
        "cache_version": service.TRADING_JOURNAL_VIEW_CACHE_VERSION,
        "generated_at": "2026-01-04T00:00:00Z",
        "items": items,
        "balances": [],
        "source_fingerprints": fingerprints,
    }
    service._attach_trading_journal_equity_metadata(snapshot)
    assert snapshot["equity_cache"] == {
        "schema_version": 2,
        "verified": True,
        "verified_at": "2026-01-04T00:00:00Z",
        "source_fingerprint_sha256": service._stable_json_sha256(fingerprints),
        "curve_type": "cashflow_neutral_compounded_index",
        "base_index": 100.0,
        "return_field": "equity_return_pct",
        "return_basis": "net_trade_result_over_analysis_balance_before_trade",
        "point_counts": {
            "BINANCE": 4,
            "BYBIT": 0,
            "OANDA DEMO": 0,
            "OANDA LIVE": 0,
            "PEPPERSTONE DEMO": 0,
            "PEPPERSTONE LIVE": 0,
        },
    }
    old_snapshot = json.loads(json.dumps(snapshot))
    old_snapshot["equity_cache"]["schema_version"] = 1
    freshness = service._trading_journal_snapshot_freshness(
        old_snapshot,
        current_fingerprints=fingerprints,
    )
    assert freshness["current"] is False
    assert "equity_schema_incompatible" in freshness["reasons"]
    foreign_snapshot = json.loads(json.dumps(snapshot))
    foreign_snapshot["equity_cache"]["return_basis"] = "arbitrary_equity_return_pct"
    freshness = service._trading_journal_snapshot_freshness(
        foreign_snapshot,
        current_fingerprints=fingerprints,
    )
    assert freshness["current"] is False
    assert "equity_return_basis_incompatible" in freshness["reasons"]


def test_items_endpoint_recomputes_coverage_after_response_row_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _load_master_service_for_equity_integration()
    stale_counts = {
        account: 99 for account in service.TRADING_JOURNAL_EQUITY_ACCOUNTS
    }
    snapshot = {
        "cache_version": service.TRADING_JOURNAL_VIEW_CACHE_VERSION,
        "items": [
            {
                "id": "local-btc",
                "row_type": "trade",
                "source": "local_excel",
                "account": "BINANCE",
                "symbol": "BTCUSDT",
                "close_time": "2026-01-01T00:00:00Z",
                "analysis_balance_before_trade": 100,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "net_profit": 1,
            },
            {
                "id": "local-eurusd",
                "row_type": "trade",
                "source": "local_excel",
                "account": "OANDA LIVE",
                "symbol": "EURUSD",
                "close_time": "2026-01-02T00:00:00Z",
                "analysis_balance_before_trade": 100,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "net_profit": 1,
            },
            {
                "id": "cached-bybit",
                "row_type": "trade",
                "source": "bybit",
                "account": "BYBIT",
                "symbol": "BTCUSDT",
                "close_time": "2026-01-03T00:00:00Z",
                "analysis_balance_before_trade": 100,
                "analysis_balance_before_trade_source": VERIFIED_BALANCE_SOURCE,
                "net_profit": 1,
            },
        ],
        "stats": {},
        "balances": [],
        "diagnostics": {},
        "equity_cache": {
            "schema_version": 2,
            "verified": True,
            "point_counts": stale_counts,
        },
    }
    service._TRADING_JOURNAL_VIEW_CACHE.update(
        {"key": "snapshot", "payload": snapshot}
    )
    monkeypatch.setattr(service, "_journal_source_fingerprint", lambda: {})
    monkeypatch.setattr(
        service,
        "_trading_journal_snapshot_freshness",
        lambda *_args, **_kwargs: {"current": True, "reasons": []},
    )
    monkeypatch.setattr(
        service,
        "_trading_journal_local_excel_authoritative",
        lambda: True,
    )
    monkeypatch.setattr(
        service,
        "_trading_journal_bybit_demo_balance_anchor_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        service,
        "_compute_journal_stats",
        lambda _items, _balances: {},
    )
    monkeypatch.setattr(
        service,
        "_compute_journal_period_stats",
        lambda _items, _balances: {},
    )

    response = asyncio.run(service.trading_journal_items(filter="btc"))
    payload = json.loads(response.body.decode("utf-8"))
    assert [row["id"] for row in payload["items"]] == ["local-btc"]
    assert payload["equity_cache"]["point_counts"] == {
        "BINANCE": 2,
        "BYBIT": 0,
        "OANDA DEMO": 0,
        "OANDA LIVE": 0,
        "PEPPERSTONE DEMO": 0,
        "PEPPERSTONE LIVE": 0,
    }
    assert snapshot["equity_cache"]["point_counts"] == stale_counts


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
        "OANDA DEMO": 44,
        "OANDA LIVE": 63,
        "PEPPERSTONE DEMO": 823,
        "PEPPERSTONE LIVE": 192,
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
  const fillTextCalls = [];
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
    arc() {}, fill() {}, fillText(...args) { fillTextCalls.push(args); }, fillRect() {}, setLineDash() {},
    measureText(text) { return { width: String(text).length * 7 }; },
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
  const overlay = {
    ...makeElement('journal-equity-overlay-canvas'),
    width: 0,
    height: 0,
    clientWidth: 900,
    clientHeight: 440,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: rectWidth, height: 440 }),
    getContext: () => context2d,
  };
  const hoverLive = makeElement('journal-equity-hover-live');
  const elements = {
    'journal-equity-account': account,
    'journal-equity-refresh-btn': refresh,
    'journal-equity-canvas': canvas,
    'journal-equity-overlay-canvas': overlay,
    'journal-equity-hover-live': hoverLive,
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
      [
        {
          id: 'bybit-1',
          account: 'BYBIT LIVE',
          row_type: 'trade',
          close_time: '2026-01-01T00:00:00Z',
          analysis_balance_before_trade: 100,
          analysis_balance_after_trade: 100,
          analysis_balance_before_trade_source: 'cashflow_anchor_plus_trade_results',
          net_profit: 0,
        },
        {
          id: 'oanda-unverified',
          account: 'OANDA LIVE',
          row_type: 'trade',
          close_time: '2026-01-01T00:00:00Z',
          analysis_balance_before_trade: 100,
          analysis_balance_after_trade: 101,
          analysis_balance_before_trade_source: 'cashflow_anchor_plus_trade_results',
          net_profit: 1,
        },
      ],
      pointCounts({ BYBIT: 2, 'OANDA LIVE': 3 }),
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
  elementListeners['journal-equity-overlay-canvas:pointermove']({ clientX: 300, clientY: 200 });
  const hoverText = hoverLive.textContent;
  const overlayWidth = overlay.width;
  elementListeners['journal-equity-overlay-canvas:pointerleave']();
  const clearedHoverText = hoverLive.textContent;

  account.value = 'OANDA LIVE';
  elementListeners['journal-equity-account:change']();
  const coverageMismatch = {
    text: state.textContent,
    error: state.classList.error,
    summary: summary.innerHTML,
  };

  account.value = 'PEPPERSTONE LIVE';
  elementListeners['journal-equity-account:change']();
  const accountEmpty = state.textContent;

  account.value = 'OANDA LIVE';
  elementListeners['journal-equity-account:change']();

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
      analysis_balance_before_trade: 1200,
      analysis_balance_after_trade: 1188,
      analysis_balance_before_trade_source: 'cashflow_anchor_plus_trade_results',
      net_profit: -12,
    }], pointCounts({ 'OANDA LIVE': 2 }), [{ label: 'OANDA LIVE', currency: 'AUD' }]),
  );
  await elementListeners['journal-equity-refresh-btn:click']();
  rectWidth = 260;
  context.devicePixelRatio = 3;
  windowListeners.resize();
  while (timers.length) timers.shift()();
  const narrowFillStart = fillTextCalls.length;
  elementListeners['journal-equity-overlay-canvas:pointermove']({ clientX: 130, clientY: 200 });
  const narrowHoverText = hoverLive.textContent;
  const narrowFillTextCalls = fillTextCalls.slice(narrowFillStart);
  elementListeners['journal-equity-overlay-canvas:pointercancel']();
  const pointerCancelText = hoverLive.textContent;
  elementListeners['journal-equity-overlay-canvas:touchmove']({ touches: [{ clientX: 130, clientY: 200 }] });
  const touchHoverText = hoverLive.textContent;
  elementListeners['journal-equity-overlay-canvas:touchcancel']();
  const touchCancelText = hoverLive.textContent;

  process.stdout.write(JSON.stringify({
    initial,
    hoverText,
    overlayWidth,
    clearedHoverText,
    coverageMismatch,
    accountEmpty,
    refreshEmpty,
    staleState,
    failureState,
    finalSummary: summary.innerHTML,
    resizedWidth: canvas.width,
    resizedOverlayWidth: overlay.width,
    narrowHoverText,
    narrowFillTextCalls,
    pointerCancelText,
    touchHoverText,
    touchCancelText,
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
    assert "100.00%" in result["initial"]["summary"]
    assert "USDT" not in result["initial"]["summary"]
    assert result["initial"]["stateDisplay"] == "none"
    assert result["initial"]["canvasWidth"] == 1800
    assert "Cursor " in result["hoverText"]
    assert "Nearest actual point" in result["hoverText"]
    assert "Brisbane" in result["hoverText"]
    assert "%" in result["hoverText"]
    assert result["overlayWidth"] == 1800
    assert result["clearedHoverText"] == ""
    assert result["coverageMismatch"] == {
        "text": (
            "Current equity data for Oanda live could not be verified. "
            "Refresh the equity curve."
        ),
        "error": True,
        "summary": "<strong>Oanda live</strong><span>2 unverified points</span>",
    }
    assert "No equity data is available for Pepperstone live" in result["accountEmpty"]
    assert "No equity data is available for Oanda live" in result["refreshEmpty"]
    assert result["staleState"] == {
        "text": "Cached equity data is stale.",
        "error": True,
    }
    assert result["failureState"] == {
        "text": "authoritative fetch failed",
        "error": True,
    }
    assert "2 points" in result["finalSummary"]
    assert "99.00%" in result["finalSummary"]
    assert "AUD" not in result["finalSummary"]
    assert result["resizedWidth"] == 780
    assert result["resizedOverlayWidth"] == 780
    assert "Nearest actual point" in result["narrowHoverText"]
    assert "Brisbane" in result["narrowHoverText"]
    constrained_hover_text = [
        call
        for call in result["narrowFillTextCalls"]
        if call and ("Brisbane" in str(call[0]) or "Equity index:" in str(call[0]))
    ]
    assert constrained_hover_text
    assert all(len(call) == 4 and 0 < call[3] <= 252 for call in constrained_hover_text)
    assert result["pointerCancelText"] == ""
    assert "Nearest actual point" in result["touchHoverText"]
    assert result["touchCancelText"] == ""
    assert result["canvasStyleWidth"] == "100%"
    assert result["drawCount"] >= 3
    assert result["fetchCount"] == 11
    assert result["refreshEnabled"] is True


def test_equity_script_has_one_selected_curve_axes_refresh_resize_and_states() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    service = SERVICE_PATH.read_text(encoding="utf-8")
    assert "accountSelect.value" in source
    assert "drawChart(canvas, points)" in source
    assert "formatPercentage" in source
    assert "analysis_balance_before_trade_source" in source
    assert "equity_return_pct" not in source
    assert "equity_return_pct" in service
    assert "formatBalance" not in source
    assert "yTicks = 5" in source
    assert "xTicks = Math.min(6" in source
    assert "formatDate(timestamp)" in source
    assert "devicePixelRatio" in source
    assert "buildChartGeometry" in source
    assert "nearestActualPoint" in source
    assert "drawHoverOverlay" in source
    assert "journal-equity-overlay-canvas" in source
    assert "journal-equity-hover-live" in source
    assert "addEventListener('pointermove'" in source
    assert "addEventListener('pointerleave'" in source
    assert "addEventListener('pointercancel'" in source
    assert "addEventListener('mousemove'" in source
    assert "addEventListener('touchmove'" in source
    assert "addEventListener('touchcancel'" in source
    assert "window.addEventListener('resize'" in source
    assert "refreshButton.addEventListener('click', () => load({ forceRefresh: true }))" in source
    assert "'trading-journal:data-changed'" in source
    assert "new window.BroadcastChannel(EQUITY_DATA_CHANNEL_NAME)" in source
    assert "equityDataChannel.addEventListener" in source
    assert "equityDataChannel?.close()" in source
    assert "REFRESH_STATUS_URL" in source
    assert "payload?.snapshot_current !== true" in source
    assert "payload?.snapshot_stale === true" in source
    assert "payload?.equity_cache?.verified !== true" in source
    assert "Loading authoritative Trading Journal data" in source
    assert "No equity data is available" in source
    assert "setChartState(error?.message" in source
    assert "journal-equity-account" in service
    assert "journal-equity-refresh-btn" in service
    assert 'TRADING_JOURNAL_EQUITY_CACHE_SCHEMA_VERSION = 2' in service
    assert 'TRADING_JOURNAL_EQUITY_CURVE_TYPE = "cashflow_neutral_compounded_index"' in service
    assert 'id="journal-equity-panel"' in service
    assert ".journal-equity-toolbar{" in service
    assert ".equity-chart-wrap{" in service
    assert ".equity-chart-stack{position:relative;" in service
    assert ".equity-canvas{width:100%;max-width:100%;" in service
    assert ".equity-overlay-canvas{position:absolute;inset:0;" in service
    assert 'id="journal-equity-hover-live"' in service
    assert 'aria-live="polite"' in service
    assert "@media (max-width: 980px)" in service


def test_journal_actions_broadcasts_equity_changes_across_tabs() -> None:
    actions = (ROOT / "render" / "static" / "trading_journal_actions.js").read_text(encoding="utf-8")
    assert "const EQUITY_DATA_CHANNEL_NAME = 'trading-journal-equity-data';" in actions
    assert "new window.BroadcastChannel(EQUITY_DATA_CHANNEL_NAME)" in actions
    assert "equityDataChannel?.postMessage(detail)" in actions
    assert "equityDataChannel?.close()" in actions
