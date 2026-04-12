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
        {
            "id": "wh1",
            "instrument": "BTCUSDT",
            "timeframe": "5-minute",
            "stop_loss": "10",
            "take_profit": "20",
            "opened_at": 1_744_334_400,
        }
    )
    assert item["timeframe"] == "5-minute"
    contexts = master_service._load_trade_contexts()
    assert contexts and contexts[0].get("timeframe") == "5-minute"
    assert contexts[0].get("stop_loss") == "10"
    assert contexts[0].get("take_profit") == "20"
    assert contexts[0].get("open_time") == "2025-04-11T01:20:00+00:00"


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


def test_trade_context_merge_prevents_duplicate_lifecycle_rows(temp_state_paths):
    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "pw-1",
            "broker": "bybit",
            "account": "demo",
            "category": "linear",
            "instrument": "BTCUSDT",
            "side": "buy",
            "timeframe": "15-minute",
            "stop_loss": "99.1",
            "take_profit": "111.5",
        }
    )
    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "pw-1",
            "order_id": "ord-1",
            "order_link_id": "link-1",
            "trade_id": "tr-1",
            "transaction_id": "tx-1",
            "status": "ACTIVE",
            "timeframe": "",
            "stop_loss": "",
            "take_profit": "",
        }
    )
    master_service._upsert_trade_context(
        {
            "order_id": "ord-1",
            "trade_id": "tr-1",
            "status": "CLOSED",
        }
    )
    contexts = master_service._load_trade_contexts()
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx.get("pending_webhook_id") == "pw-1"
    assert ctx.get("order_id") == "ord-1"
    assert ctx.get("order_link_id") == "link-1"
    assert ctx.get("trade_id") == "tr-1"
    assert ctx.get("transaction_id") == "tx-1"
    assert ctx.get("timeframe") == "15-minute"
    assert ctx.get("stop_loss") == "99.1"
    assert ctx.get("take_profit") == "111.5"


def test_recent_trades_and_journal_backfill_from_trade_context(monkeypatch: pytest.MonkeyPatch):
    journal_row = {
        "id": "bybit:demo:closedpnl:BTCUSDT:ord-2",
        "source": "bybit",
        "account": "demo",
        "account_label": "Bybit Demo",
        "status": "closed",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "open_time": "2026-02-01T00:00:00+00:00",
        "close_time": "2026-02-01T01:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 104.0,
        "realized_pnl": 8.0,
        "fees": 0.2,
        "balance_after_trade": 1008.0,
        "raw_refs": {"orderId": "ord-2", "orderLinkId": "link-2"},
    }
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [journal_row])
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_rows", lambda rows: (rows, {"changed": 0}))
    monkeypatch.setattr(master_service, "_enrich_trade_row_metrics", lambda rows: rows)
    monkeypatch.setattr(master_service, "_calc_balance_after_trade", lambda rows, balances: rows)
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [
            {
                "broker": "bybit",
                "account": "demo",
                "instrument": "BTCUSDT",
                "side": "buy",
                "order_id": "ord-2",
                "order_link_id": "link-2",
                "timeframe": "1-hour",
                "stop_loss": "97.5",
                "take_profit": "110.0",
                "status": "CLOSED",
            }
        ],
    )
    monkeypatch.setattr(master_service, "_cashflow_rows_for_journal", lambda _folder: [])
    monkeypatch.setattr(master_service, "_load_json_file", lambda _path, _default: {})
    monkeypatch.setattr(master_service, "_compute_journal_stats", lambda _rows, _balances: {})

    recent = json.loads(asyncio.run(master_service.recent_trades(limit=5)).body.decode("utf-8"))
    assert recent["items"][0]["timeframe"] == "1-hour"
    assert recent["items"][0]["stop_loss"] == 97.5
    assert recent["items"][0]["take_profit"] == 110.0

    journal = json.loads(asyncio.run(master_service.trading_journal_items()).body.decode("utf-8"))
    assert journal["items"][0]["timeframe"] == "1-hour"
    assert journal["items"][0]["stop_loss"] == 97.5
    assert journal["items"][0]["take_profit"] == 110.0


def test_merge_row_blank_strings_do_not_wipe_populated_fields():
    existing = {
        "timeframe": "15-minute",
        "stop_loss": 10.5,
        "take_profit": 12.5,
        "entry_price": 11.0,
        "open_time": "2026-02-01T00:00:00+00:00",
        "balance_after_trade": 1001.0,
    }
    merged = master_service._merge_trading_journal_row(
        existing,
        {
            "timeframe": "",
            "stop_loss": "",
            "take_profit": "",
            "entry_price": "",
            "open_time": "",
            "balance_after_trade": "",
        },
    )
    assert merged["timeframe"] == "15-minute"
    assert merged["stop_loss"] == 10.5
    assert merged["take_profit"] == 12.5
    assert merged["entry_price"] == 11.0
    assert merged["open_time"] == "2026-02-01T00:00:00+00:00"
    assert merged["balance_after_trade"] == 1001.0


def test_save_state_helpers_schedule_dropbox_backup(tmp_path, monkeypatch: pytest.MonkeyPatch):
    pending_path = tmp_path / "pending.json"
    context_path = tmp_path / "contexts.json"
    monkeypatch.setattr(master_service, "PENDING_WEBHOOKS_PATH", pending_path)
    monkeypatch.setattr(master_service, "TRADE_CONTEXTS_PATH", context_path)
    calls = {"count": 0}
    monkeypatch.setattr(
        master_service,
        "_schedule_dropbox_upload_state_backup",
        lambda: calls.update({"count": calls["count"] + 1}),
    )

    master_service._save_pending_webhooks([{"id": "w1"}])
    master_service._save_trade_contexts([{"order_id": "o1"}])
    assert calls["count"] == 2


def test_prune_trade_contexts_invalid_timestamps_are_safe(temp_state_paths):
    pruned = master_service._prune_trade_contexts(
        [
            {"pending_webhook_id": "a", "status": "CLOSED", "updated_at": "not-a-time"},
            {"pending_webhook_id": "b", "status": "CANCELLED", "updated_at": ""},
            {"pending_webhook_id": "c", "status": "ACTIVE", "updated_at": "also-bad"},
        ]
    )
    assert [item.get("pending_webhook_id") for item in pruned] == ["a", "b", "c"]


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


def test_waiting_pending_webhook_hidden_when_matching_live_bybit_position_exists(temp_state_paths):
    pending = {
        "id": "wh-1",
        "status": "WAITING",
        "enabled": True,
        "broker": "WEBHOOK",
        "category": "linear",
        "account": "demo",
        "instrument": "DASHUSDT",
        "side": "Buy",
        "size": "10",
    }
    open_items = [
        {
            "broker": "Bybit",
            "category": "linear",
            "account": "demo",
            "type": "position",
            "instrument": "DASHUSDT",
            "side": "Buy",
            "size": "10",
        }
    ]
    filtered, changed = master_service._clean_pending_webhooks_for_open_items([pending], open_items)
    assert changed is True
    assert filtered == []


def test_trade_context_exact_link_beats_fuzzy_match(temp_state_paths):
    pending = {
        "id": "wh-2",
        "status": "WAITING",
        "enabled": True,
        "broker": "WEBHOOK",
        "category": "linear",
        "account": "demo",
        "instrument": "BTCUSDT",
        "side": "Buy",
        "size": "0.1",
    }
    master_service._upsert_trade_context(
        {
            "pending_webhook_id": "wh-2",
            "order_link_id": "exact-link-id",
            "broker": "bybit",
            "account": "demo",
            "category": "linear",
            "instrument": "BTCUSDT",
            "side": "buy",
            "status": "ACTIVE",
        }
    )
    open_items = [
        {
            "broker": "Bybit",
            "account": "demo",
            "category": "linear",
            "instrument": "BTCUSDT",
            "side": "Buy",
            "size": "0.1",
            "order_link_id": "not-the-right-one",
        }
    ]
    filtered, changed = master_service._clean_pending_webhooks_for_open_items([pending], open_items)
    assert changed is False
    assert len(filtered) == 1


def test_pending_webhook_mutations_invalidate_open_orders_cache(temp_state_paths):
    master_service._OPEN_ORDERS_CACHE["payload"] = {"items": [{"id": "stale"}]}
    master_service._OPEN_ORDERS_CACHE["expires_at"] = 12345.0
    master_service._upsert_pending_webhook({"id": "wh-cache", "instrument": "BTCUSDT"})
    assert master_service._OPEN_ORDERS_CACHE["payload"] is None
    assert master_service._OPEN_ORDERS_CACHE["expires_at"] == 0.0

    master_service._OPEN_ORDERS_CACHE["payload"] = {"items": [{"id": "stale"}]}
    master_service._OPEN_ORDERS_CACHE["expires_at"] = 12345.0
    master_service._update_pending_webhook("wh-cache", {"status": "PENDING"})
    assert master_service._OPEN_ORDERS_CACHE["payload"] is None
    assert master_service._OPEN_ORDERS_CACHE["expires_at"] == 0.0

    master_service._OPEN_ORDERS_CACHE["payload"] = {"items": [{"id": "stale"}]}
    master_service._OPEN_ORDERS_CACHE["expires_at"] = 12345.0
    assert master_service._delete_pending_webhook("wh-cache") is True
    assert master_service._OPEN_ORDERS_CACHE["payload"] is None
    assert master_service._OPEN_ORDERS_CACHE["expires_at"] == 0.0
