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
    ctx = {"order_id": "oid-2", "stop_loss": 99.0, "take_profit": 103.0, "timeframe": "1m", "is_test_trade": True}
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _r: ctx)
    monkeypatch.setattr(master_service, "_lookup_trade_context_by_market_window", lambda *_a, **_k: None)
    patched = master_service._backfill_trade_row_context_fields(row)
    assert patched["stop_loss"] == 99.0
    assert patched["take_profit"] == 103.0
    assert patched["timeframe"] == "1m"
    assert patched["is_test_trade"] is True
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
