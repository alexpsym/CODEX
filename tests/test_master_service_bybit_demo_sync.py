import asyncio
import importlib.util
import json
import warnings
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_bybit_sync", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_manual_demo_sync_uses_7_day_recovery_window(monkeypatch) -> None:
    now_s = 1_700_000_000.0
    now_ms = int(now_s * 1000)
    captured = {}
    monkeypatch.setattr(master_service.time, "time", lambda: now_s)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _mode: ("demo", "k", "s", "https://api.bybit.com", "env"))
    monkeypatch.setattr(master_service, "_persist_bybit_closed_pnl_last_seen", lambda: None)
    master_service._BYBIT_CLOSED_PNL_LAST_SEEN["demo"] = None

    async def fake_sync(**kwargs):
        captured.update(kwargs)
        return kwargs["start_time"] + 1000

    monkeypatch.setattr(master_service, "_sync_bybit_closed_pnl_window", fake_sync)
    result = asyncio.run(master_service._run_bybit_closed_pnl_sync(account_mode="demo", reason="manual"))
    expected = now_ms - master_service._BYBIT_CLOSED_PNL_RECOVERY_WINDOW_MS + master_service._BYBIT_CLOSED_PNL_RECOVERY_SAFETY_MARGIN_MS
    assert result["ok"] is True
    assert captured["start_time"] == expected


def test_startup_recovery_forces_7_day_window_even_with_last_seen(monkeypatch) -> None:
    now_s = 1_700_100_000.0
    now_ms = int(now_s * 1000)
    captured = {}
    monkeypatch.setattr(master_service.time, "time", lambda: now_s)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _mode: ("demo", "k", "s", "https://api.bybit.com", "env"))
    monkeypatch.setattr(master_service, "_persist_bybit_closed_pnl_last_seen", lambda: None)
    master_service._BYBIT_CLOSED_PNL_LAST_SEEN["demo"] = now_ms - (60 * 1000)

    async def fake_sync(**kwargs):
        captured.update(kwargs)
        return kwargs["start_time"] + 2000

    monkeypatch.setattr(master_service, "_sync_bybit_closed_pnl_window", fake_sync)
    asyncio.run(master_service._run_bybit_closed_pnl_sync(account_mode="demo", reason="startup_recovery"))
    expected = now_ms - master_service._BYBIT_CLOSED_PNL_RECOVERY_WINDOW_MS + master_service._BYBIT_CLOSED_PNL_RECOVERY_SAFETY_MARGIN_MS
    assert captured["start_time"] == expected


def test_persisted_closed_pnl_last_seen_restored_after_restart(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "trading_journal_state.json"
    state_path.write_text(
        json.dumps({"bybit_closed_pnl_last_seen": {"demo": 111, "live": 222}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", state_path)
    master_service._BYBIT_CLOSED_PNL_LAST_SEEN["demo"] = None
    master_service._BYBIT_CLOSED_PNL_LAST_SEEN["live"] = None

    master_service._restore_bybit_closed_pnl_last_seen_from_state()
    assert master_service._BYBIT_CLOSED_PNL_LAST_SEEN["demo"] == 111
    assert master_service._BYBIT_CLOSED_PNL_LAST_SEEN["live"] == 222


def test_recovered_rows_upsert_without_duplicates(monkeypatch) -> None:
    saved = {"rows": []}
    row = {"id": "bybit:demo:closedpnl:BTCUSDT:123", "symbol": "BTCUSDT", "raw_refs": {"orderId": "123"}}
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: list(saved["rows"]))
    monkeypatch.setattr(master_service, "_save_trading_journal", lambda rows: saved.update({"rows": list(rows)}))
    changed1 = master_service._upsert_trading_journal_rows([row])
    changed2 = master_service._upsert_trading_journal_rows([row])
    assert changed1 == 1
    assert changed2 == 1
    assert len(saved["rows"]) == 1


def test_bybit_demo_poll_waits_for_restore_signal(monkeypatch) -> None:
    poll_called = {"value": False}
    wait_called = {"value": False}

    async def fake_wait_for(awaitable, timeout):
        wait_called["value"] = True
        await awaitable
        return None

    async def fake_poll():
        poll_called["value"] = True

    event = asyncio.Event()
    monkeypatch.setattr(master_service, "_STARTUP_STATE_RESTORE_DONE", event)
    monkeypatch.setattr(master_service.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(master_service, "_poll_bybit_demo_closed_pnl", fake_poll)

    async def runner():
        task = asyncio.create_task(master_service._start_bybit_demo_closed_pnl_poll_after_restore())
        await asyncio.sleep(0)
        assert wait_called["value"] is True
        assert poll_called["value"] is False
        event.set()
        await task

    asyncio.run(runner())


def test_startup_recovery_waits_for_restore_signal(monkeypatch) -> None:
    recovery_called = {"value": False}
    wait_called = {"value": False}

    async def fake_wait_for(awaitable, timeout):
        wait_called["value"] = True
        await awaitable
        return None

    async def fake_recovery():
        recovery_called["value"] = True

    event = asyncio.Event()
    monkeypatch.setattr(master_service, "_STARTUP_STATE_RESTORE_DONE", event)
    monkeypatch.setattr(master_service.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(master_service, "_run_startup_recovery_import_if_needed", fake_recovery)

    async def runner():
        task = asyncio.create_task(master_service._start_startup_recovery_import_after_restore())
        await asyncio.sleep(0)
        assert wait_called["value"] is True
        assert recovery_called["value"] is False
        event.set()
        await task
        assert recovery_called["value"] is True

    asyncio.run(runner())


def test_workbook_upsert_normalizes_blank_numeric_values(monkeypatch) -> None:
    existing = pd.DataFrame(
        [{"order_id": "oid-1", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0}],
        columns=master_service.BYBIT_DEMO_WORKBOOK_COLUMNS,
    )
    captured = {"uploaded": None}

    def fake_read_excel(*_args, **_kwargs):
        return existing.copy()

    class DummyWriter:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: b"dummy")
    monkeypatch.setattr(master_service.pd, "read_excel", fake_read_excel)
    monkeypatch.setattr(master_service.pd, "ExcelWriter", DummyWriter)
    monkeypatch.setattr(master_service.pd.DataFrame, "to_excel", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda _path, payload: captured.update({"uploaded": payload}))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"changed": 0})
    monkeypatch.setattr(
        master_service,
        "_bybit_demo_workbook_row",
        lambda _row: {
            "opening_time": "2026-01-01",
            "closing_time": "2026-01-01",
            "type_buy_sell": "Buy",
            "symbol": "BTCUSDT",
            "size_quantity": "",
            "entry_price": "",
            "closing_price": "",
            "stop_loss": "",
            "take_profit": "",
            "commission": "",
            "net_profit": "",
            "balance_after_trade": "",
            "currency": "USDT",
            "notes": "",
            "fill_count": "",
            "order_id": "oid-1",
            "source": "bybit",
        },
    )

    changed = master_service._append_bybit_demo_rows_to_workbook("/tmp", [{"id": "x"}])
    assert changed == 1
    assert captured["uploaded"] is not None


def test_place_bybit_order_upserts_final_absolute_tpsl_context(monkeypatch) -> None:
    captured_contexts = []

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "oid-123", "orderLinkId": "link-123"}}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _mode: ("demo", "k", "s", "https://api.bybit.com", "env"))
    async def fake_wait_for_position_entry(**_kwargs):
        return {"avgPrice": "100.0", "positionIdx": 0}

    async def fake_set_trading_stop(**_kwargs):
        return {"retCode": 0}

    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_for_position_entry)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_trading_stop)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _id: False)
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: captured_contexts.append(payload) or payload)

    payload = {
        "symbol": "DASHUSDT",
        "action": "buy",
        "quantity": "1",
        "account": "demo",
        "tp_offset": "10",
        "sl_offset": "-5",
        "timeframe": "15-minute",
        "order_type": "market",
    }
    result = asyncio.run(master_service._place_bybit_order(payload, request_id="req-1"))
    assert result["order"]["orderId"] == "oid-123"
    assert len(captured_contexts) >= 2
    assert captured_contexts[0]["timeframe"] == "15-minute"
    assert captured_contexts[-1]["entry_price"] == 100.0
    assert captured_contexts[-1]["stop_loss"] == 95.0
    assert captured_contexts[-1]["take_profit"] == 110.0
    assert captured_contexts[-1]["status"] == "ACTIVE"


def test_bybit_repair_backfills_blank_fields_and_persists(monkeypatch) -> None:
    rows = [
        {
            "id": "bybit:demo:closedpnl:DASHUSDT:oid-1",
            "source": "bybit",
            "account": "demo",
            "symbol": "DASHUSDT",
            "side": "Buy",
            "status": "closed",
            "timeframe": "",
            "stop_loss": "",
            "take_profit": "",
            "raw_refs": {"orderId": "oid-1"},
        }
    ]
    saved = {"rows": None}
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: list(rows))
    monkeypatch.setattr(master_service, "_set_trading_journal_rows", lambda updated: saved.update({"rows": list(updated)}))
    monkeypatch.setattr(
        master_service,
        "_backfill_trade_row_context_fields",
        lambda row: {**row, "timeframe": "15-minute", "stop_loss": 90.0, "take_profit": 120.0},
    )
    changed = master_service._repair_persisted_bybit_trade_context_fields()
    assert changed == 1
    assert saved["rows"] is not None
    assert saved["rows"][0]["timeframe"] == "15-minute"
    assert saved["rows"][0]["stop_loss"] == 90.0
    assert saved["rows"][0]["take_profit"] == 120.0


def test_closed_pnl_row_backfills_tpsl_from_context(monkeypatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_journal_row",
        lambda _row: {"timeframe": "1-hour", "stop_loss": "99.5", "take_profit": "110.2"},
    )
    row = master_service._normalize_bybit_closed_pnl_row(
        {
            "symbol": "BTCUSDT",
            "orderId": "abc123",
            "orderLinkId": "link-1",
            "openFee": "0.1",
            "closeFee": "0.2",
            "fillCount": "1",
            "side": "Buy",
            "createdTime": 1,
            "updatedTime": 2,
            "avgEntryPrice": "100",
            "avgExitPrice": "105",
            "closedSize": "0.1",
            "closedPnl": "1.0",
        },
        account_mode="demo",
        balance_after_trade=1000.0,
        stop_loss=None,
        take_profit=None,
    )
    assert row is not None
    assert row["stop_loss"] == 99.5
    assert row["take_profit"] == 110.2
    assert row["timeframe"] == "1-hour"


def test_closed_pnl_row_prefers_context_open_time(monkeypatch) -> None:
    resolved_ctx = {
        "open_time": "2026-04-11T01:20:00+00:00",
        "created_at": "2026-04-11T01:19:00+00:00",
        "timeframe": "4-hour",
    }
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _row: None)
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda _payload: _payload)
    row = master_service._normalize_bybit_closed_pnl_row(
        {
            "symbol": "DASHUSDT",
            "orderId": "dash-order-1",
            "createdTime": 1_775_884_246_000,
            "updatedTime": 1_775_884_246_000,
            "avgEntryPrice": "22",
            "avgExitPrice": "23",
            "closedSize": "10",
            "closedPnl": "4.2",
        },
        account_mode="demo",
        balance_after_trade=1000.0,
        resolved_trade_context=resolved_ctx,
    )
    assert row is not None
    assert row["open_time"] == "2026-04-11T01:20:00+00:00"
    assert row["close_time"] == "2026-04-11T05:10:46+00:00"


def test_repair_existing_bybit_row_open_time_from_context(monkeypatch) -> None:
    bad_row = {
        "id": "bybit:demo:closedpnl:DASHUSDT:123",
        "source": "bybit",
        "account": "demo",
        "symbol": "DASHUSDT",
        "side": "Buy",
        "open_time": "2026-04-11T05:10:46+00:00",
        "close_time": "2026-04-11T05:10:46+00:00",
        "raw_refs": {"orderId": "123"},
    }
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_journal_row",
        lambda _row: {"open_time": "2026-04-11T01:20:00+00:00", "created_at": "2026-04-11T01:19:30+00:00"},
    )
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])
    repaired, changed = master_service._repair_persisted_bybit_open_times([bad_row])
    assert changed == 1
    assert repaired[0]["open_time"] == "2026-04-11T01:20:00+00:00"
    assert repaired[0]["close_time"] == "2026-04-11T05:10:46+00:00"


def test_repair_existing_bybit_row_open_time_when_open_after_close(monkeypatch) -> None:
    bad_row = {
        "id": "bybit:demo:closedpnl:DASHUSDT:124",
        "source": "bybit",
        "account": "demo",
        "symbol": "DASHUSDT",
        "side": "Buy",
        "open_time": "2026-04-11T06:10:46+00:00",
        "close_time": "2026-04-11T05:10:46+00:00",
        "raw_refs": {"orderId": "124"},
    }
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_journal_row",
        lambda _row: {"open_time": "2026-04-11T01:20:00+00:00"},
    )
    repaired, changed = master_service._repair_persisted_bybit_open_times([bad_row])
    assert changed == 1
    assert repaired[0]["open_time"] == "2026-04-11T01:20:00+00:00"


def test_repair_existing_bybit_row_missing_open_time(monkeypatch) -> None:
    bad_row = {
        "id": "bybit:demo:closedpnl:DASHUSDT:125",
        "source": "bybit",
        "account": "demo",
        "symbol": "DASHUSDT",
        "side": "Buy",
        "open_time": "",
        "close_time": "2026-04-11T05:10:46+00:00",
        "raw_refs": {"orderId": "125"},
    }
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_journal_row",
        lambda _row: {"created_at": "2026-04-11T01:19:30+00:00"},
    )
    repaired, changed = master_service._repair_persisted_bybit_open_times([bad_row])
    assert changed == 1
    assert repaired[0]["open_time"] == "2026-04-11T01:19:30+00:00"


def test_same_id_upsert_overwrites_bad_open_time_without_duplicate(monkeypatch) -> None:
    saved = {"rows": [{"id": "bybit:demo:closedpnl:DASHUSDT:123", "open_time": "2026-04-11T05:10:46+00:00"}]}
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: list(saved["rows"]))
    monkeypatch.setattr(master_service, "_save_trading_journal", lambda rows: saved.update({"rows": list(rows)}))
    master_service._upsert_trading_journal_rows(
        [{"id": "bybit:demo:closedpnl:DASHUSDT:123", "open_time": "2026-04-11T01:20:00+00:00"}]
    )
    assert len(saved["rows"]) == 1
    assert saved["rows"][0]["open_time"] == "2026-04-11T01:20:00+00:00"


def test_health_and_root_head_routes_return_200() -> None:
    health = asyncio.run(master_service.healthcheck())
    root_head = asyncio.run(master_service.root_head_health())
    route_paths = {getattr(route, "path", "") for route in master_service.app.routes}
    assert health.status_code == 200
    assert root_head.status_code == 200
    assert "/health" in route_paths
    assert "/" in route_paths


def test_workbook_upsert_handles_float_text_column_notes(monkeypatch) -> None:
    existing = pd.DataFrame(
        [{"order_id": "oid-1", "notes": float("nan")}],
        columns=master_service.BYBIT_DEMO_WORKBOOK_COLUMNS,
    )
    existing["notes"] = pd.to_numeric(existing["notes"], errors="coerce")
    captured = {"uploaded": None}

    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: b"dummy")
    monkeypatch.setattr(master_service.pd, "read_excel", lambda *_args, **_kwargs: existing.copy())

    class DummyWriter:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(master_service.pd, "ExcelWriter", DummyWriter)
    monkeypatch.setattr(master_service.pd.DataFrame, "to_excel", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda _path, payload: captured.update({"uploaded": payload}))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"changed": 0})
    monkeypatch.setattr(
        master_service,
        "_bybit_demo_workbook_row",
        lambda _row: {
            "opening_time": "2026-01-01",
            "closing_time": "2026-01-01",
            "type_buy_sell": "Buy",
            "symbol": "BTCUSDT",
            "size_quantity": 0.1,
            "entry_price": 100.0,
            "closing_price": 101.0,
            "stop_loss": "",
            "take_profit": "",
            "commission": 0.02,
            "net_profit": 1.0,
            "balance_after_trade": "",
            "currency": "USDT",
            "notes": "",
            "fill_count": "",
            "order_id": "oid-1",
            "source": "bybit",
        },
    )

    changed = master_service._append_bybit_demo_rows_to_workbook("/tmp", [{"id": "x"}])
    assert changed == 1
    assert captured["uploaded"] is not None


def test_workbook_row_roundtrip_preserves_timeframe(monkeypatch) -> None:
    row = {
        "open_time": "2026-01-01T00:00:00+00:00",
        "close_time": "2026-01-01T01:00:00+00:00",
        "side": "Buy",
        "symbol": "BTCUSDT",
        "qty": 1.0,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "realized_pnl": 10.0,
        "timeframe": "15-minute",
        "raw_refs": {"orderId": "oid-tf", "fillCount": 1, "source": "closed_pnl"},
    }
    wb_row = master_service._bybit_demo_workbook_row(row)
    assert wb_row["timeframe"] == "15-minute"

    frame = master_service._coerce_bybit_demo_workbook_frame(pd.DataFrame([wb_row]))
    reparsed = {
        "timeframe": master_service._normalize_timeframe(master_service._excel_cell_to_python(frame.iloc[0].get("timeframe")))
    }
    assert reparsed["timeframe"] == "15-minute"


def test_workbook_upsert_handles_multiple_float_text_columns(monkeypatch) -> None:
    existing = pd.DataFrame(
        [{"order_id": "oid-1", "notes": float("nan"), "source": float("nan"), "symbol": float("nan")}],
        columns=master_service.BYBIT_DEMO_WORKBOOK_COLUMNS,
    )
    for col in ("notes", "source", "symbol"):
        existing[col] = pd.to_numeric(existing[col], errors="coerce")

    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: b"dummy")
    monkeypatch.setattr(master_service.pd, "read_excel", lambda *_args, **_kwargs: existing.copy())

    class DummyWriter:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(master_service.pd, "ExcelWriter", DummyWriter)
    monkeypatch.setattr(master_service.pd.DataFrame, "to_excel", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"changed": 0})
    monkeypatch.setattr(
        master_service,
        "_bybit_demo_workbook_row",
        lambda _row: {
            "opening_time": "2026-01-01",
            "closing_time": "2026-01-01",
            "type_buy_sell": "Sell",
            "symbol": "ETHUSDT",
            "size_quantity": 0.2,
            "entry_price": 200.0,
            "closing_price": 198.0,
            "stop_loss": "",
            "take_profit": "",
            "commission": 0.02,
            "net_profit": -0.4,
            "balance_after_trade": "",
            "currency": "USDT",
            "notes": "",
            "fill_count": "",
            "order_id": "oid-1",
            "source": "closed_pnl",
        },
    )

    changed = master_service._append_bybit_demo_rows_to_workbook("/tmp", [{"id": "x"}])
    assert changed == 1


def test_workbook_upsert_raises_no_future_warning(monkeypatch) -> None:
    existing = pd.DataFrame(
        [{"order_id": "oid-1", "notes": float("nan")}],
        columns=master_service.BYBIT_DEMO_WORKBOOK_COLUMNS,
    )
    existing["notes"] = pd.to_numeric(existing["notes"], errors="coerce")

    monkeypatch.setattr(master_service, "_dropbox_download_bytes", lambda _path: b"dummy")
    monkeypatch.setattr(master_service.pd, "read_excel", lambda *_args, **_kwargs: existing.copy())

    class DummyWriter:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(master_service.pd, "ExcelWriter", DummyWriter)
    monkeypatch.setattr(master_service.pd.DataFrame, "to_excel", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(master_service, "_dropbox_upload_bytes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda _folder: {"changed": 0})
    monkeypatch.setattr(master_service, "_bybit_demo_workbook_row", lambda _row: {
        "opening_time": "2026-01-01", "closing_time": "2026-01-01", "type_buy_sell": "Buy",
        "symbol": "BTCUSDT", "size_quantity": "", "entry_price": "", "closing_price": "",
        "stop_loss": "", "take_profit": "", "commission": "", "net_profit": "",
        "balance_after_trade": "", "currency": "USDT", "notes": "", "order_id": "oid-1",
        "fill_count": "", "source": "closed_pnl",
    })

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        changed = master_service._append_bybit_demo_rows_to_workbook("/tmp", [{"id": "x"}])
    assert changed == 1


def test_bybit_unresolved_tpsl_warns_once_and_persists_registry(tmp_path, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "state.json")
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    class DummyLogger:
        def warning(self, message, *args):
            warnings.append(message % args if args else message)

    monkeypatch.setattr(master_service, "BYBIT_LOGGER", DummyLogger())
    monkeypatch.setattr(master_service, "_resolve_trading_journal_dropbox_folder", lambda: ("/tmp", []))
    monkeypatch.setattr(master_service, "_ensure_bybit_demo_dropbox_files", lambda _folder: None)
    async def fake_empty_payload(**_kwargs):
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_fetch_bybit_order_history", fake_empty_payload)
    monkeypatch.setattr(master_service, "_fetch_bybit_order_realtime", fake_empty_payload)
    monkeypatch.setattr(master_service, "_fetch_bybit_transaction_log", fake_empty_payload)
    monkeypatch.setattr(master_service, "_upsert_trading_journal_rows", lambda rows: len(rows))
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_rows", lambda rows: (rows, {"changed": 0}))
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "_append_bybit_demo_rows_to_workbook", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(master_service, "_sanitize_bybit_demo_workbook", lambda *_args, **_kwargs: {"deduped_by_order_id": 0, "deduped_by_fingerprint": 0})
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_record_bybit_demo_sync_status", lambda **_kwargs: None)
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "load_bybit_demo_tpsl_cache", lambda: {})
    async def fake_closed_pnl(**_kwargs):
        return {"result": {"list": [{
            "symbol": "BTCUSDT", "orderId": "oid-1", "orderLinkId": "", "side": "Buy",
            "createdTime": 1000, "updatedTime": 2000, "avgEntryPrice": "100", "avgExitPrice": "101",
            "closedSize": "1", "closedPnl": "1", "openFee": "0", "closeFee": "0",
        }]}}

    monkeypatch.setattr(master_service, "_fetch_bybit_closed_pnl", fake_closed_pnl)

    asyncio.run(master_service._sync_bybit_closed_pnl_window(account_mode="demo", base_url="u", api_key="k", api_secret="s", start_time=0, end_time=3000))
    asyncio.run(master_service._sync_bybit_closed_pnl_window(account_mode="demo", base_url="u", api_key="k", api_secret="s", start_time=0, end_time=3000))

    assert len([line for line in warnings if "BYBIT_DEMO_TPSL unresolved" in line]) == 1
    state = master_service._load_trading_journal_state()
    key = "demo|oid-1|||BTCUSDT|"
    entry = state.get("unresolved_registry", {}).get("bybit_demo_tpsl", {}).get(key, {})
    assert entry.get("count") == 2
    assert entry.get("resolved") is False


def test_parent_order_link_id_lookup_supported(monkeypatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [{"parent_order_link_id": "parent-1", "timeframe": "1-hour"}],
    )
    row = master_service._lookup_trade_context_for_journal_row({"raw_refs": {"parentOrderLinkId": "parent-1"}})
    assert row and row.get("timeframe") == "1-hour"
