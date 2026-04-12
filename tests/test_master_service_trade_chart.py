import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_trade_chart", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_extract_trade_timeframe_priority() -> None:
    row = {"timeframe": "1h", "metrics": {"timeframe": "5m"}, "raw_excel": {"timeframe": "15m"}}
    assert master_service._extract_trade_timeframe(row) == "1h"
    row2 = {"metrics": {"timeframe": "5m"}, "raw_excel": {"timeframe": "15m"}}
    assert master_service._extract_trade_timeframe(row2) == "5m"
    row3 = {"raw_excel": {"timeframe": "15m"}}
    assert master_service._extract_trade_timeframe(row3) == "15m"


def test_choose_readable_interval_upscales() -> None:
    row = {"open_time": "2026-01-01T00:00:00Z", "close_time": "2026-03-01T00:00:00Z"}
    chosen, upscaled = master_service._choose_readable_interval(row, "1m")
    assert chosen in {"4h", "1d"}
    assert upscaled is True


def test_trade_chart_marker_does_not_use_event_axvspan() -> None:
    source = inspect.getsource(master_service._render_trade_chart_png)
    assert "ax.axvspan(left, right" not in source
    assert "ax.annotate(" in source


def test_trade_chart_route_404_unknown_row() -> None:
    response = asyncio.run(master_service.trade_chart_page("does-not-exist"))
    assert response.status_code == 404


def test_trade_chart_route_422_for_incomplete_row(monkeypatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_find_trade_row_by_id",
        lambda _row_id: {"id": "t1", "row_type": "trade", "symbol": "BTCUSDT", "open_time": "2026-01-01T00:00:00Z"},
    )
    response = asyncio.run(master_service.trade_chart_page("t1"))
    assert response.status_code == 422
    assert "does not contain enough timing data" in response.body.decode("utf-8")


def test_non_trade_row_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_find_trade_row_by_id",
        lambda _row_id: {"id": "cf1", "row_type": "cashflow", "symbol": "CASHFLOW"},
    )
    response = asyncio.run(master_service.trade_chart_page("cf1"))
    assert response.status_code == 422


def test_recent_trades_payload_exposes_chart_fields(monkeypatch) -> None:
    row = {
        "id": "trade-1",
        "row_type": "trade",
        "status": "closed",
        "source": "bybit",
        "account_label": "Bybit Live",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "open_time": "2026-01-01T00:00:00Z",
        "close_time": "2026-01-01T01:00:00Z",
        "entry_price": 1,
        "exit_price": 2,
        "net_profit": 3,
        "trade_duration_seconds": 3600,
    }
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [row])
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_rows", lambda rows: (rows, {"changed": 0}))
    monkeypatch.setattr(master_service, "_calc_balance_after_trade", lambda rows, _balances: rows)
    monkeypatch.setattr(master_service, "_enrich_trade_row_metrics", lambda rows: rows)
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    monkeypatch.setattr(master_service, "_get_monthly_aud_revaluation_rows", lambda: [])
    payload = json.loads(asyncio.run(master_service.recent_trades(limit=5)).body.decode("utf-8"))
    assert payload["items"][0]["chart_row_id"] == "trade-1"
    assert payload["items"][0]["chart_available"] is True


def test_bybit_candle_window_selection(monkeypatch) -> None:
    async def fake_public(_base, _path, _params):
        return {"result": {"list": [["1000", "1", "2", "0.5", "1.5", "10", "20"], ["2000", "1.5", "2.5", "1", "2", "11", "21"]]}}

    monkeypatch.setattr(master_service, "_bybit_public_get_json", fake_public)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _acct: {"base_url": "https://api.bybit.com"})
    candles = asyncio.run(master_service._fetch_bybit_trade_candles("BTCUSDT", "linear", "1", 1000, 2500))
    assert len(candles) == 2
    assert candles[0]["open"] == 1.0


def test_oanda_candle_window_selection(monkeypatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _mode: {"base_url": "x", "account_id": "a", "token": "t", "mode": "live"})

    async def fake_fetch(**_kwargs):
        return {"candles": [{"complete": True, "time": "2026-01-01T00:00:00.000000000Z", "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"}}]}

    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch)
    candles = asyncio.run(
        master_service._fetch_oanda_trade_candles(
            "EUR_USD",
            "live",
            "M5",
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
        )
    )
    assert len(candles) == 1
    assert candles[0]["close"] == 1.15


def test_trade_chart_html_is_image_only(monkeypatch) -> None:
    row = {
        "id": "trade-1",
        "row_type": "trade",
        "source": "bybit",
        "account": "demo",
        "symbol": "BTCUSDT",
        "open_time": "2026-01-01T00:00:00Z",
        "close_time": "2026-01-01T01:00:00Z",
        "entry_price": 1.0,
        "stop_loss": 0.9,
        "take_profit": 1.2,
        "exit_price": 1.1,
    }
    monkeypatch.setattr(master_service, "_find_trade_row_by_id", lambda _row_id: row)
    monkeypatch.setattr(master_service, "_infer_trade_chart_source", lambda _row: "bybit")
    monkeypatch.setattr(master_service, "_interval_for_provider", lambda _provider, _tf: ("1", 60))
    monkeypatch.setattr(
        master_service,
        "_build_trade_chart_window",
        lambda _row, _interval_seconds, pad_candles=5: (
            master_service.datetime(2026, 1, 1, tzinfo=master_service.timezone.utc),
            master_service.datetime(2026, 1, 1, 1, tzinfo=master_service.timezone.utc),
        ),
    )

    async def fake_candles(*_args, **_kwargs):
        return [{"time": master_service.datetime(2026, 1, 1, tzinfo=master_service.timezone.utc), "open": 1.0, "high": 1.1, "low": 0.95, "close": 1.05}]

    monkeypatch.setattr(master_service, "_fetch_bybit_trade_candles", fake_candles)
    async def fake_lookup(_base, _symbol):
        return {"symbol": "BTCUSDT", "_category": "linear"}
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", fake_lookup)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _mode: {"base_url": "https://api.bybit.com"})
    monkeypatch.setattr(master_service, "_render_trade_chart_png", lambda _row, _candles, _meta: b"png")
    response = asyncio.run(master_service.trade_chart_page("trade-1"))
    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "<img" in html
    assert "Account" not in html
    assert "Symbol" not in html
    assert "Timeframe requested" not in html
    assert "Timeframe rendered" not in html


def test_merged_calculator_contains_embedded_asset_calculators() -> None:
    html = master_service.CALCULATOR_PAGE_TEMPLATE
    assert html.count("Position Size Calculator") >= 2
    assert "Asset:" in html
    assert 'data-asset="crypto"' in html
    assert 'data-asset="fx"' in html
    assert "Open Crypto Calculator" not in html
    assert "Open FX Calculator" not in html
    assert "Crypto Position Size" not in html
    assert "FX Position Size" not in html
    assert "Crypto Position Size Calculator" not in html
    assert "OANDA Position Size Calculator" not in html
    assert "/apps/cryptocalculator-clone/?embedded=1&shell=merged&title=Position+Size+Calculator" in html
    assert "/apps/oanda-calculator-clone/?embedded=1&shell=merged&title=Position+Size+Calculator" in html
    assert "calculator:height" in html
    assert "window.addEventListener('message'" in html


def test_merged_calculator_defaults_to_crypto_with_fx_toggle_wiring() -> None:
    html = master_service.CALCULATOR_PAGE_TEMPLATE
    assert '<section class="asset-panel active" data-panel="crypto">' in html
    assert '<section class="asset-panel" data-panel="fx">' in html
    assert "setAsset('crypto')" in html
    assert "button.dataset.asset" in html
    assert "Open Crypto Calculator" not in html
    assert "Open FX Calculator" not in html
