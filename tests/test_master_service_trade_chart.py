import asyncio
import importlib.util
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
