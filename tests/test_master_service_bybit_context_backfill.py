import pytest

from tests.test_master_service_master_journal_sync import _load_master_service_for_import_test


@pytest.fixture(scope="module")
def master_service():
    return _load_master_service_for_import_test()


def test_lookup_trade_context_supports_grouped_order_ids(master_service, monkeypatch):
    contexts = [{"order_id": "oid-2", "stop_loss": 1, "take_profit": 2, "timeframe": "1m", "is_test_trade": True}]
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: contexts)
    row = {"raw_refs": {"order_ids": ["oid-1", "oid-2"]}}
    ctx = master_service._lookup_trade_context_for_journal_row(row)
    assert ctx and ctx.get("order_id") == "oid-2"


def test_lookup_trade_context_for_open_item_supports_order_ids(master_service, monkeypatch):
    contexts = [{"order_id": "oid-2", "timeframe": "1m"}]
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: contexts)
    item = {"raw_refs": {"order_ids": ["oid-1", "oid-2"]}}
    matched = master_service._lookup_trade_context_for_open_item(item)
    assert matched and matched.get("order_id") == "oid-2"


def test_grouped_order_ids_ambiguous_context_refuses_match(master_service, monkeypatch):
    contexts = [{"order_id": "oid-1"}, {"order_id": "oid-2"}]
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: contexts)
    row = {"raw_refs": {"order_ids": ["oid-1", "oid-2"]}}
    assert master_service._lookup_trade_context_for_journal_row(row) is None


def test_backfill_grouped_row_populates_context_and_r_multiple(master_service, monkeypatch):
    row = {
        "id": "bybit:demo:trade:BTCUSDT:x",
        "source": "bybit_execution_history_grouped",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "raw_refs": {"order_ids": ["oid-2"]},
    }
    ctx = {"order_id": "oid-2", "stop_loss": 99.0, "take_profit": 103.0, "timeframe": "1m", "is_test_trade": True, "pattern": "channel"}
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _r: ctx)
    monkeypatch.setattr(master_service, "_lookup_trade_context_by_market_window", lambda *_a, **_k: None)
    patched = master_service._backfill_trade_row_context_fields(row)
    assert patched["stop_loss"] == 99.0
    assert patched["take_profit"] == 103.0
    assert patched["timeframe"] == "1m"
    assert patched["is_test_trade"] is True
    assert patched["pattern"] == "channel"
    assert patched.get("r_multiple") not in (None, "")


def test_backfill_persisted_grouped_row_populates_context_and_is_idempotent(master_service, monkeypatch):
    base_row = {
        "id": "bybit:demo:trade:BTCUSDT:abc",
        "row_type": "trade",
        "source": "bybit_execution_history_grouped",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "open_time": "2026-05-26T09:53:00+10:00",
        "close_time": "2026-05-26T09:54:00+10:00",
        "raw_refs": {"order_ids": ["oid-2"]},
    }
    contexts = [{"order_id": "oid-2", "stop_loss": 99.0, "take_profit": 103.0, "timeframe": "1m", "is_test_trade": True}]
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: contexts)

    first_rows, first_changed = master_service._backfill_persisted_bybit_trade_fields([base_row])
    assert first_changed == 1
    first = first_rows[0]
    assert first["stop_loss"] == 99.0
    assert first["take_profit"] == 103.0
    assert first["timeframe"] == "1m"
    assert first["is_test_trade"] is True
    assert first.get("r_multiple") not in (None, "")

    second_rows, second_changed = master_service._backfill_persisted_bybit_trade_fields(first_rows)
    assert second_changed == 0
    assert second_rows == first_rows


def test_trade_duration_rounding_helper(master_service):
    assert master_service._round_trade_duration_seconds(60.1) == 60
    assert master_service._round_trade_duration_seconds(60.5) == 61
    assert master_service._round_trade_duration_seconds(0.2) == 1
    assert master_service._round_trade_duration_seconds(60) == 60


def test_snapshot_path_backfills_pending_grouped_row(master_service, monkeypatch):
    grouped = {
        "id": "bybit:demo:trade:BTCUSDT:85c1adb0266f56a4",
        "row_type": "trade",
        "source": "bybit_execution_history_grouped",
        "account": "Bybit Demo",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry_price": 76600.6,
        "exit_price": 76570.2,
        "open_time": "2026-05-26T12:42:52+10:00",
        "close_time": "2026-05-26T12:43:30+10:00",
        "trade_duration_seconds": 38,
        "raw_refs": {"order_ids": ["x", "688f8e82-1ab9-4941-a5bb-5d1b4e9f977c"]},
    }
    ctx = {"order_id": "688f8e82-1ab9-4941-a5bb-5d1b4e9f977c", "stop_loss": 76491.2, "take_profit": 77067.3, "timeframe": "1m", "is_test_trade": True}
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [ctx])
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "read_master_journal_source", lambda _p: {"items": [], "cashflow_ledger": {}})
    monkeypatch.setattr(master_service, "_master_journal_path", lambda: __import__("pathlib").Path("journal/Trading Journal.xlsx"))
    monkeypatch.setattr(master_service, "_monthly_aud_revaluation_rows_for_journal_view", lambda: [])
    monkeypatch.setattr(master_service, "_load_json_file", lambda *_a, **_k: {})
    monkeypatch.setattr(master_service, "_compute_journal_stats", lambda items, balances: {})
    monkeypatch.setattr(master_service, "_build_authoritative_trading_journal_diagnostics_snapshot", lambda _items: {})
    master_service._PENDING_MANUAL_SYNC_ROWS = [grouped]
    snap = master_service._build_trading_journal_view_snapshot(force=True)
    row = next(r for r in snap.get("items", []) if r.get("id") == grouped["id"])
    assert row.get("stop_loss") == 76491.2
    assert row.get("take_profit") == 77067.3
    assert row.get("timeframe") == "1m"
    assert row.get("is_test_trade") is True
    assert row.get("r_multiple") not in (None, "")
    assert row.get("trade_duration_seconds") == 38


def test_import_upload_enriches_rows_before_upsert_and_pending(master_service, monkeypatch):
    row = {
        "id": "bybit:demo:trade:BTCUSDT:85c1adb0266f56a4",
        "row_type": "trade",
        "source": "bybit_execution_history_grouped",
        "account": "Bybit Demo",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry_price": 76600.6,
        "exit_price": 76570.2,
        "open_time": "2026-05-26T12:42:52+10:00",
        "close_time": "2026-05-26T12:43:30+10:00",
        "raw_refs": {"order_ids": ["x", "688f8e82-1ab9-4941-a5bb-5d1b4e9f977c"]},
    }
    ctx = {"order_id": "688f8e82-1ab9-4941-a5bb-5d1b4e9f977c", "stop_loss": 76491.2, "take_profit": 77067.3, "timeframe": "1m", "is_test_trade": True}
    monkeypatch.setattr(master_service, "_is_bybit_trade_history_csv", lambda _p: True)
    monkeypatch.setattr(master_service, "_parse_bybit_trade_history_csv_with_diagnostics", lambda *_a, **_k: ([dict(row)], [], {}))
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "_infer_realized_net_profit_from_balance_continuity", lambda rows, _existing: (rows, [], {}))
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [ctx])
    captured = {}
    monkeypatch.setattr(master_service, "_upsert_trading_journal_rows", lambda rows, **_k: captured.setdefault("rows", [dict(r) for r in rows]) or len(rows))
    monkeypatch.setattr(master_service, "_backfill_persisted_bybit_trade_fields", lambda rows: (rows, 0))
    observed = {}
    def _sync_probe(**_k):
        observed["pending"] = [dict(r) for r in (master_service._PENDING_MANUAL_SYNC_ROWS or []) if isinstance(r, dict)]
        return {"ok": True}
    monkeypatch.setattr(master_service, "_sync_master_journal_workbook", _sync_probe)
    monkeypatch.setattr(master_service, "_master_journal_sync_ok", lambda _r: True)
    monkeypatch.setattr(master_service, "_verify_trade_log_row_ids_in_workbook", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(master_service, "_build_trading_journal_view_snapshot", lambda **_k: {"items": []})
    monkeypatch.setattr(master_service, "_persist_trading_journal_sqlite", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "_sync_journal_excel_files_to_github", lambda *_a, **_k: {"github_sync_enabled": False, "github_sync_ok": True})
    monkeypatch.setattr(master_service, "_master_journal_path", lambda: __import__("pathlib").Path("journal/Trading Journal.xlsx"))
    master_service._PENDING_MANUAL_SYNC_ROWS = []
    payload = b"contracts,Order No.,Direction\n"
    result = master_service._import_uploaded_trading_journal_file("demo.csv", payload, account_mode="demo")
    assert result.get("ok") is True
    assert captured["rows"][0].get("stop_loss") == 76491.2
    assert captured["rows"][0].get("take_profit") == 77067.3
    assert captured["rows"][0].get("timeframe") == "1m"
    assert captured["rows"][0].get("is_test_trade") is True
    assert observed["pending"][0].get("stop_loss") == 76491.2
    assert observed["pending"][0].get("take_profit") == 77067.3


def test_load_trade_contexts_falls_back_to_state_backup(master_service, monkeypatch):
    monkeypatch.setattr(master_service, "_load_json_file", lambda path, default=None: {"trade_contexts": [{"order_id": "oid-x", "stop_loss": 1.0}]} if str(path).endswith("state_backup.json") else {"items": []})
    items = master_service._load_trade_contexts()
    assert items and items[0].get("order_id") == "oid-x"
