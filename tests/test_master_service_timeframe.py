import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_timeframe", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


@pytest.fixture
def temp_state_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(master_service, "PENDING_WEBHOOKS_PATH", tmp_path / "pending_webhooks.json")
    monkeypatch.setattr(master_service, "TRADE_CONTEXTS_PATH", tmp_path / "trade_contexts.json")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_PATH", tmp_path / "trading_journal.json")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "trading_journal_state.json")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_IMPORT_CACHE_PATH", tmp_path / "trading_journal_import_cache.json")
    monkeypatch.setattr(master_service, "MONTHLY_AUD_REVALUATION_PATH", tmp_path / "monthly_aud_revaluation.json")
    monkeypatch.setattr(master_service, "MONTHLY_AUD_REVALUATION_STATE_PATH", tmp_path / "monthly_aud_revaluation_state.json")
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.bybit_monitor, "get_custom_alerts", lambda force=True: [])
    monkeypatch.setattr(master_service.oanda_monitor, "get_custom_alerts", lambda force=True: [])
    monkeypatch.setattr(master_service.bybit_monitor, "replace_custom_alerts", lambda alerts, strict=False: list(alerts))
    monkeypatch.setattr(master_service.oanda_monitor, "replace_custom_alerts", lambda alerts: list(alerts))
    monkeypatch.setattr(master_service, "_set_watchlist", lambda items: list(items))
    return tmp_path


def test_pending_webhook_keeps_timeframe(temp_state_paths):
    item = master_service._upsert_pending_webhook(
        {"id": "wh1", "instrument": "BTCUSDT", "timeframe": "5-minute", "stop_loss": "10", "take_profit": "20"}
    )
    assert item["timeframe"] == "5-minute"
    contexts = master_service._load_trade_contexts()
    assert contexts and contexts[0].get("timeframe") == "5-minute"
    assert contexts[0].get("stop_loss") == "10"
    assert contexts[0].get("take_profit") == "20"


def test_context_lookup_attaches_timeframe_and_ambiguous_returns_none(temp_state_paths):
    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "p1",
            "broker": "bybit",
            "account": "demo",
            "instrument": "BTCUSDT",
            "side": "buy",
            "timeframe": "5-minute",
        }
    )
    match = master_service._lookup_trade_context_for_open_item(
        {"broker": "Bybit", "account": "demo", "instrument": "BTCUSDT", "side": "Buy"}
    )
    assert match and match.get("timeframe") == "5-minute"

    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "p2",
            "broker": "bybit",
            "account": "demo",
            "instrument": "BTCUSDT",
            "side": "buy",
            "timeframe": "15-minute",
        }
    )
    ambiguous = master_service._lookup_trade_context_for_open_item(
        {"broker": "Bybit", "account": "demo", "instrument": "BTCUSDT", "side": "Buy"}
    )
    assert ambiguous is None


def test_journal_builder_uses_timeframe_from_context(temp_state_paths):
    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "p3",
            "broker": "bybit",
            "account": "demo",
            "instrument": "BTCUSDT",
            "side": "buy",
            "order_id": "oid1",
            "timeframe": "1-hour",
        }
    )
    rows = master_service._journal_rows_from_bybit_execution(
        {
            "account": "demo",
            "category": "linear",
            "symbol": "BTCUSDT",
            "orderId": "oid1",
            "execId": "e1",
            "execQty": "0.1",
            "execPrice": "100",
            "execFee": "0.1",
            "execPnl": "1.2",
            "side": "Buy",
        }
    )
    assert rows and rows[0]["timeframe"] == "1-hour"
    assert rows[0]["metrics"]["timeframe"] == "1-hour"


def test_recent_trades_includes_timeframe(monkeypatch: pytest.MonkeyPatch):
    row = {
        "id": "r1",
        "status": "closed",
        "close_time": "2026-01-01T00:00:00+00:00",
        "source": "bybit",
        "account_label": "Bybit Demo",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "timeframe": "4-hour",
        "realized_pnl": 10,
        "balance_after_trade": 110,
    }
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [row])
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_rows", lambda rows: (rows, {"changed": 0}))
    monkeypatch.setattr(master_service, "_enrich_trade_row_metrics", lambda rows: rows)
    monkeypatch.setattr(master_service, "_calc_balance_after_trade", lambda rows, balances: rows)
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    response = asyncio.run(master_service.recent_trades(limit=5))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["items"][0]["timeframe"] == "4-hour"


def test_backup_restore_includes_trade_contexts(temp_state_paths):
    master_service._save_trade_contexts([{"pending_webhook_id": "p9", "timeframe": "30-minute"}])
    payload = json.loads(master_service._build_state_backup_payload().decode("utf-8"))
    assert "trade_contexts" in payload
    restored = master_service._restore_alerts_payload(
        {
            "alerts": {"bybit": {"alerts": []}, "oanda": {"alerts": []}},
            "trade_contexts": [{"pending_webhook_id": "p10", "timeframe": "1-day"}],
        }
    )
    assert isinstance(restored, dict)
    contexts = master_service._load_trade_contexts()
    assert any(c.get("pending_webhook_id") == "p10" for c in contexts)


def test_oanda_row_repair_from_context(monkeypatch: pytest.MonkeyPatch):
    row = {
        "id": "o1",
        "source": "oanda",
        "timeframe": "",
        "raw_refs": {"orderId": "ord1", "tradeId": "t1"},
    }
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [row])
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [{"order_id": "ord1", "trade_id": "t1", "timeframe": "4-hour", "stop_loss": "1.1", "take_profit": "1.3"}],
    )
    stored = {"rows": None}
    monkeypatch.setattr(master_service, "_set_trading_journal_rows", lambda rows: stored.update({"rows": rows}))
    changed = master_service._repair_persisted_oanda_trade_rows()
    assert changed == 1
    assert stored["rows"][0]["timeframe"] == "4-hour"
    assert stored["rows"][0]["stop_loss"] == "1.1"
