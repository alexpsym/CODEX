import asyncio
import importlib.util
import json
import warnings
import sys
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_bybit_sync", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_save_json_file_retries_transient_permission_error(tmp_path, monkeypatch) -> None:
    from shared import atomic_json

    target = tmp_path / "trading_journal.json"
    attempts = {"count": 0}
    seen_src: list[str] = []
    real_replace = atomic_json.os.replace

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        attempts["count"] += 1
        seen_src.append(Path(src).name)
        if attempts["count"] < 3:
            raise PermissionError("[WinError 5] Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_json.time, "sleep", lambda _n: None)
    monkeypatch.setattr(atomic_json.os, "replace", flaky_replace)

    master_service._save_json_file(target, {"items": [{"id": "r1"}]})
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["items"][0]["id"] == "r1"
    assert attempts["count"] == 3
    assert seen_src
    assert all(name != "trading_journal.json.tmp" for name in seen_src)


def test_save_json_file_surfaces_permanent_permission_error(tmp_path, monkeypatch) -> None:
    from shared import atomic_json

    target = tmp_path / "trading_journal.json"
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _n: None)
    monkeypatch.setattr(
        atomic_json.os,
        "replace",
        lambda _src, _dst: (_ for _ in ()).throw(PermissionError("[WinError 5] Access is denied")),
    )

    with pytest.raises(PermissionError):
        master_service._save_json_file(target, {"items": []})


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
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_SOURCE", "dropbox")
    monkeypatch.setattr(master_service, "DROPBOX_SYNC_ENABLED", True)
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
            "createdTime": 1_000,
            "updatedTime": 5_000,
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


def test_closed_pnl_row_stale_context_falls_back_to_created_time(monkeypatch) -> None:
    stale_ctx = {
        "open_time": "2026-04-11T06:10:46+00:00",
        "created_at": "2026-04-11T06:10:40+00:00",
        "timeframe": "4-hour",
        "stop_loss": "20",
        "take_profit": "30",
    }
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _row: None)
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda _payload: _payload)
    row = master_service._normalize_bybit_closed_pnl_row(
        {
            "symbol": "HYPERUSDT",
            "orderId": "hyper-order-1",
            "createdTime": 1_775_870_000_000,
            "updatedTime": 1_775_884_246_000,
            "avgEntryPrice": "22",
            "avgExitPrice": "23",
            "closedSize": "10",
            "closedPnl": "4.2",
            "side": "Buy",
        },
        account_mode="demo",
        balance_after_trade=1000.0,
        resolved_trade_context=stale_ctx,
    )
    assert row is not None
    assert row["status"] == "closed"
    assert row["open_time"] == "2026-04-11T01:13:20+00:00"


def test_closed_pnl_row_stale_context_without_valid_created_time_is_quarantined(monkeypatch) -> None:
    stale_ctx = {
        "open_time": "2026-04-11T06:10:46+00:00",
        "timeframe": "4-hour",
        "stop_loss": "20",
        "take_profit": "30",
    }
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_journal_row", lambda _row: None)
    row = master_service._normalize_bybit_closed_pnl_row(
        {
            "symbol": "HYPERUSDT",
            "orderId": "hyper-order-2",
            "createdTime": 1_775_884_246_000,
            "updatedTime": 1_775_884_246_000,
            "avgEntryPrice": "22",
            "avgExitPrice": "23",
            "closedSize": "10",
            "closedPnl": "4.2",
            "side": "Buy",
        },
        account_mode="demo",
        balance_after_trade=1000.0,
        resolved_trade_context=stale_ctx,
    )
    assert row is not None
    assert row["status"] == "invalid_time_order"
    assert row["row_type"] == "quarantine"


def test_resolve_bybit_closed_pnl_trade_context_prefers_valid_ref_match(monkeypatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [
            {
                "broker": "bybit",
                "account": "demo",
                "instrument": "HYPERUSDT",
                "side": "Buy",
                "order_id": "oid-1",
                "open_time": "2026-04-11T06:10:46+00:00",
            },
            {
                "broker": "bybit",
                "account": "demo",
                "instrument": "HYPERUSDT",
                "side": "Buy",
                "order_id": "oid-1",
                "open_time": "2026-04-11T01:10:46+00:00",
            },
        ],
    )
    ctx = master_service._resolve_bybit_closed_pnl_trade_context(
        account_mode="demo",
        symbol="HYPERUSDT",
        side="Buy",
        order_id="oid-1",
        close_time="2026-04-11T05:10:46+00:00",
    )
    assert ctx is not None
    assert ctx["open_time"] == "2026-04-11T01:10:46+00:00"


def test_sync_bybit_closed_pnl_window_stale_context_does_not_raise(monkeypatch) -> None:
    statuses = []
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
    monkeypatch.setattr(master_service, "_record_bybit_demo_sync_status", lambda **kwargs: statuses.append(kwargs))
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "load_bybit_demo_tpsl_cache", lambda: {})
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [{"order_id": "oid-stale", "open_time": "2026-04-11T06:10:46+00:00"}],
    )
    async def fake_closed_pnl(**_kwargs):
        return {"result": {"list": [{
            "symbol": "HYPERUSDT", "orderId": "oid-stale", "orderLinkId": "", "side": "Buy",
            "createdTime": 1_775_884_246_000, "updatedTime": 1_775_884_246_000,
            "avgEntryPrice": "100", "avgExitPrice": "101", "closedSize": "1",
            "closedPnl": "1", "openFee": "0", "closeFee": "0",
        }]}}

    monkeypatch.setattr(master_service, "_fetch_bybit_closed_pnl", fake_closed_pnl)
    asyncio.run(master_service._sync_bybit_closed_pnl_window(account_mode="demo", base_url="u", api_key="k", api_secret="s", start_time=0, end_time=3000))
    assert statuses
    assert statuses[-1].get("last_error") is None


def test_sync_records_broker_balance_warning_without_failing_import(monkeypatch) -> None:
    monkeypatch.setattr(master_service, "ENABLE_BYBIT_DEMO_JOURNAL", True)
    monkeypatch.setattr(master_service, "_run_bybit_closed_pnl_sync", lambda **_kwargs: asyncio.sleep(0, result={"ok": True}))
    monkeypatch.setattr(master_service, "_import_trading_journal_from_sources", lambda progress_cb=None: {"ok": True, "rows_imported": 1, "diagnostics": {"rows_by_asset_class": {}}})
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda account: (_ for _ in ()).throw(RuntimeError(f"{account} missing creds")))
    saved_state = {}
    monkeypatch.setattr(master_service, "_load_trading_journal_state", lambda: dict(saved_state))
    monkeypatch.setattr(master_service, "_save_trading_journal_state", lambda payload: saved_state.update(payload))
    updates = []
    monkeypatch.setattr(master_service, "_set_trading_journal_sync_state", lambda **kwargs: updates.append(kwargs))
    asyncio.run(master_service._run_trading_journal_sync_job())
    assert isinstance(saved_state.get("broker_balance_diagnostics"), dict)
    warnings = saved_state["broker_balance_diagnostics"].get("warnings") or []
    assert any("Bybit demo balance unavailable" in str(w) for w in warnings)
    assert any(update.get("ok") is True for update in updates)


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
        "is_test_trade": True,
        "raw_refs": {"orderId": "oid-tf", "fillCount": 1, "source": "closed_pnl"},
    }
    wb_row = master_service._bybit_demo_workbook_row(row)
    assert wb_row["timeframe"] == "15-minute"
    assert wb_row["is_test_trade"] == "Yes"

    frame = master_service._coerce_bybit_demo_workbook_frame(pd.DataFrame([wb_row]))
    reparsed = {
        "timeframe": master_service._normalize_timeframe(master_service._excel_cell_to_python(frame.iloc[0].get("timeframe"))),
        "is_test_trade": master_service._normalize_test_trade_flag(master_service._excel_cell_to_python(frame.iloc[0].get("is_test_trade"))),
    }
    assert reparsed["timeframe"] == "15-minute"
    assert reparsed["is_test_trade"] is True


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


def test_bybit_signed_get_retries_after_timestamp_window_error(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    market_time_calls = {"count": 0}
    now = {"value": 1_700_000_000.0}
    master_service._BYBIT_TIME_OFFSET_CACHE.clear()
    master_service._BYBIT_TIME_OFFSET_CACHE["https://api.bybit.com"] = {
        "synced_at": int(now["value"] * 1000),
        "offset_ms": 0,
        "rtt_ms": 0,
    }

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.content = json.dumps(payload).encode("utf-8")
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None):
            headers = headers or {}
            calls.append((url, headers))
            if url.endswith("/v5/market/time"):
                market_time_calls["count"] += 1
                return DummyResponse(200, {"retCode": 0, "result": {"timeSecond": "1700000010"}})
            if len([u for u, _ in calls if "/v5/order/history" in u]) == 1:
                return DummyResponse(
                    200,
                    {
                        "retCode": 10002,
                        "retMsg": "invalid request, req_timestamp[1700000000000],server_timestamp[1700000006000],recv_window[5000]",
                    },
                )
            return DummyResponse(200, {"retCode": 0, "result": {"list": []}})

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(master_service.time, "time", lambda: now["value"])
    payload = asyncio.run(
        master_service._bybit_signed_get(
            base_url="https://api.bybit.com",
            api_key="k",
            api_secret="s",
            path="/v5/order/history",
            params={"category": "linear"},
        )
    )
    order_headers = [headers for url, headers in calls if "/v5/order/history" in url]
    assert payload["retCode"] == 0
    assert market_time_calls["count"] >= 1
    assert len(order_headers) == 2
    assert int(order_headers[1]["X-BAPI-TIMESTAMP"]) >= int(order_headers[0]["X-BAPI-TIMESTAMP"])


def test_bybit_signed_get_persistent_timestamp_error_raises(monkeypatch) -> None:
    class DummyResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"retCode": 10002, "retMsg": "invalid request, recv_window[5000]"}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    with pytest.raises(ValueError, match="path=/v5/order/history"):
        asyncio.run(
            master_service._bybit_signed_get(
                base_url="https://api.bybit.com",
                api_key="k",
                api_secret="s",
                path="/v5/order/history",
                params={"category": "linear"},
            )
        )


def test_bybit_signed_get_non_timestamp_error_does_not_retry(monkeypatch) -> None:
    requests = {"count": 0}

    class DummyResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"retCode": 10004, "retMsg": "signature error"}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            requests["count"] += 1
            return DummyResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    with pytest.raises(ValueError, match="retCode=10004"):
        asyncio.run(
            master_service._bybit_signed_get(
                base_url="https://api.bybit.com",
                api_key="k",
                api_secret="s",
                path="/v5/order/history",
                params={"category": "linear"},
            )
        )
    assert requests["count"] == 1


def test_bybit_signed_post_uses_consistent_recv_window_and_body_on_retry(monkeypatch) -> None:
    sent_headers: list[dict[str, str]] = []
    sent_bodies: list[str] = []
    master_service._BYBIT_TIME_OFFSET_CACHE.clear()

    class DummyResponse:
        def __init__(self, payload: dict[str, object]):
            self.status_code = 200
            self.content = json.dumps(payload).encode("utf-8")
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, headers=None, content=None):
            sent_headers.append(dict(headers or {}))
            sent_bodies.append(str(content))
            if len(sent_bodies) == 1:
                return DummyResponse({"retCode": 10002, "retMsg": "timestamp expired recv_window[5000]"})
            return DummyResponse({"retCode": 0, "result": {"ok": True}})

        async def get(self, _url, headers=None):
            return DummyResponse({"retCode": 0, "result": {"timeSecond": "1700000010"}})

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(master_service, "BYBIT_RECV_WINDOW_MS", 15000)
    body = {"category": "linear", "orderLinkId": "same-link-id"}
    payload = asyncio.run(
        master_service._bybit_signed_post(
            base_url="https://api.bybit.com",
            api_key="k",
            api_secret="s",
            path="/v5/order/create",
            body=body,
        )
    )
    assert payload["retCode"] == 0
    assert len(sent_bodies) == 2
    assert sent_bodies[0] == sent_bodies[1]
    assert sent_headers[0]["X-BAPI-RECV-WINDOW"] == "15000"
    expected_signature = master_service._bybit_sign_request(
        sent_headers[0]["X-BAPI-TIMESTAMP"],
        "k",
        "s",
        sent_bodies[0],
        recv_window="15000",
    )
    assert sent_headers[0]["X-BAPI-SIGN"] == expected_signature


def test_bybit_signed_post_timeout_not_retried(monkeypatch) -> None:
    calls = {"count": 0}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            calls["count"] += 1
            raise master_service.httpx.TimeoutException("timeout")

    monkeypatch.setattr(master_service.httpx, "AsyncClient", DummyClient)
    with pytest.raises(master_service.httpx.TimeoutException):
        asyncio.run(
            master_service._bybit_signed_post(
                base_url="https://api.bybit.com",
                api_key="k",
                api_secret="s",
                path="/v5/order/create",
                body={"category": "linear", "orderLinkId": "timeout-link"},
            )
        )
    assert calls["count"] == 1


def test_run_closed_pnl_sync_records_error_details(monkeypatch) -> None:
    statuses = []
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _mode: ("demo", "k", "s", "https://api.bybit.com", "env"))
    monkeypatch.setattr(master_service.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(master_service, "_record_bybit_demo_sync_status", lambda **kwargs: statuses.append(kwargs))

    async def failing_sync(**_kwargs):
        raise ValueError("Bybit signed GET failed path=/v5/order/history retCode=10002 retMsg=invalid request recv_window[5000]")

    monkeypatch.setattr(master_service, "_sync_bybit_closed_pnl_window", failing_sync)
    with pytest.raises(ValueError):
        asyncio.run(master_service._run_bybit_closed_pnl_sync(account_mode="demo", reason="scheduled"))
    assert statuses
    assert "retCode=10002" in str(statuses[-1].get("last_error"))
    assert "path=/v5/order/history" in str(statuses[-1].get("last_error"))
