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


def test_closed_pnl_row_backfills_tpsl_from_market_window_context(monkeypatch) -> None:
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _row: None)
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_by_market_window",
        lambda _row, max_window_seconds=5400, include_inactive=False: {"timeframe": "4-hour", "stop_loss": "90", "take_profit": "130"},
    )

    row = master_service._normalize_bybit_closed_pnl_row(
        {
            "symbol": "BTCUSDT",
            "orderId": "abc123",
            "orderLinkId": "",
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
    assert row["stop_loss"] == 90.0
    assert row["take_profit"] == 130.0
    assert row["timeframe"] == "4-hour"
    refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}
    assert refs.get("trade_context_tpsl_fallback_via_window") is True


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
