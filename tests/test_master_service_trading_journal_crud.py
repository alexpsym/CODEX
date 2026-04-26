import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_journal_crud", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


@pytest.fixture
def temp_state_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_PATH", tmp_path / "trading_journal.json")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "trading_journal_state.json")
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    return tmp_path


def _json(res):
    return json.loads(res.body.decode("utf-8"))


def test_create_manual_trade_row(temp_state_paths):
    res = asyncio.run(
        master_service.trading_journal_create_row(
            {
                "open_time": "2026-04-01T00:00:00Z",
                "close_time": "2026-04-01T01:00:00Z",
                "symbol": "eurusd",
                "side": "buy",
                "qty": "1.5",
                "entry_price": "1.1",
                "exit_price": "1.2",
                "net_profit": "12.3",
                "balance_after_trade": "1012.3",
                "is_test_trade": "true",
            }
        )
    )
    payload = _json(res)
    row = payload["row"]
    assert payload["ok"] is True
    assert row["id"].startswith("manual:")
    assert row["source"] == "manual"
    assert row["row_type"] == "trade"
    assert row["is_manual"] is True
    assert row["symbol"] == "EURUSD"
    assert row["is_test_trade"] is True
    stored = master_service._get_trading_journal_rows()
    assert len(stored) == 1
    assert stored[0]["id"] == row["id"]


def test_patch_existing_trade_row_stores_manual_overrides(temp_state_paths):
    master_service._set_trading_journal_rows(
        [
            {
                "id": "oanda:live:t1",
                "row_type": "trade",
                "source": "oanda",
                "status": "closed",
                "open_time": "2026-04-01T00:00:00+00:00",
                "close_time": "2026-04-01T01:00:00+00:00",
                "symbol": "EUR_USD",
                "net_profit": 10.0,
            }
        ]
    )
    res = asyncio.run(
        master_service.trading_journal_patch_row(
            "oanda:live:t1",
            {"notes": "manual note", "timeframe": "1-hour"},
        )
    )
    row = _json(res)["row"]
    assert row["manual_overrides"]["notes"] == "manual note"
    assert row["manual_overrides"]["timeframe"] == "1-hour"
    asyncio.run(
        master_service.trading_journal_patch_row(
            "oanda:live:t1",
            {"is_test_trade": "false"},
        )
    )
    row2 = master_service._get_trading_journal_rows()[0]
    assert row2["is_test_trade"] is False
    assert "notes" in row["manual_override_fields"]


def test_manual_overrides_survive_later_sync_upsert(temp_state_paths):
    base = {
        "id": "oanda:live:t1",
        "row_type": "trade",
        "source": "oanda",
        "status": "closed",
        "symbol": "EUR_USD",
        "open_time": "2026-04-01T00:00:00+00:00",
        "close_time": "2026-04-01T01:00:00+00:00",
        "notes": "source-note",
        "timeframe": "15-minute",
    }
    base = master_service._apply_trading_journal_manual_overrides(base, {"notes": "edited", "timeframe": "4-hour"})
    master_service._set_trading_journal_rows([base])

    master_service._upsert_trading_journal_rows(
        [
            {
                "id": "oanda:live:t1",
                "row_type": "trade",
                "source": "oanda",
                "status": "closed",
                "notes": "new-source-note",
                "timeframe": "1-minute",
            }
        ]
    )
    row = master_service._get_trading_journal_rows()[0]
    assert row["notes"] == "edited"
    assert row["timeframe"] == "4-hour"


def test_concurrent_upserts_preserve_both_rows(temp_state_paths):
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def worker(row_id: str) -> None:
        try:
            start.wait(timeout=2)
            master_service._upsert_trading_journal_rows(
                [
                    {
                        "id": row_id,
                        "row_type": "trade",
                        "source": "bybit",
                        "status": "closed",
                        "symbol": "BTCUSDT",
                        "close_time": "2026-04-01T01:00:00+00:00",
                    }
                ]
            )
        except BaseException as exc:  # pragma: no cover - diagnostics for thread failures
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("bybit:demo:closed:1",))
    t2 = threading.Thread(target=worker, args=("bybit:demo:closed:2",))
    t1.start()
    t2.start()
    start.wait(timeout=2)
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert not errors
    rows = master_service._get_trading_journal_rows()
    ids = {str((row or {}).get("id") or "") for row in rows}
    assert "bybit:demo:closed:1" in ids
    assert "bybit:demo:closed:2" in ids


def test_reject_cashflow_edit(temp_state_paths):
    master_service._set_trading_journal_rows(
        [{"id": "cf1", "row_type": "cashflow", "source": "dropbox", "close_time": "2026-04-01T00:00:00+00:00"}]
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(master_service.trading_journal_patch_row("cf1", {"notes": "x"}))
    assert exc.value.status_code == 409


def test_reject_protected_field_edit(temp_state_paths):
    with pytest.raises(HTTPException) as exc:
        master_service._normalize_trading_journal_edit_payload(
            {"id": "x", "notes": "abc"},
            for_create=False,
            existing={"id": "abc", "row_type": "trade", "source": "oanda"},
        )
    assert exc.value.status_code == 422


def test_delete_manual_row(temp_state_paths):
    master_service._set_trading_journal_rows(
        [{"id": "manual:r1", "row_type": "trade", "source": "manual", "is_manual": True}]
    )
    res = asyncio.run(master_service.trading_journal_delete_row("manual:r1"))
    payload = _json(res)
    assert payload["ok"] is True
    assert master_service._get_trading_journal_rows() == []


def test_stats_and_balances_still_compute_after_create_and_edit(temp_state_paths, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        master_service,
        "_get_excel_account_balances",
        lambda: [{"account": "Manual Account", "label": "Manual Account", "balance": 1000.0, "currency": "USD"}],
    )
    created = _json(
        asyncio.run(
            master_service.trading_journal_create_row(
                {
                    "open_time": "2026-04-01T00:00:00Z",
                    "close_time": "2026-04-01T01:00:00Z",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "account": "Manual Account",
                    "account_label": "Manual Account",
                    "currency": "USD",
                    "qty": "1",
                    "entry_price": "100",
                    "exit_price": "105",
                    "net_profit": "5",
                    "balance_after_trade": "1005",
                }
            )
        )
    )["row"]
    asyncio.run(master_service.trading_journal_patch_row(created["id"], {"notes": "after edit"}))
    journal = _json(asyncio.run(master_service.trading_journal_items()))
    balances = _json(asyncio.run(master_service.trading_journal_balances()))
    assert journal["count"] >= 1
    assert isinstance(journal.get("stats"), dict)
    assert isinstance(balances.get("items"), list)


def test_trading_journal_js_contains_crud_controls_and_endpoints():
    js = (ROOT / "render" / "static" / "trading_journal.js").read_text(encoding="utf-8")
    assert "/api/trading-journal/rows" in js
    assert "/api/trading-journal/diagnostics" in js
    assert 'data-action="edit"' in js
    assert 'data-action="delete"' in js
    assert "location.reload" not in js


def test_import_from_sources_local_when_dropbox_missing(temp_state_paths, monkeypatch: pytest.MonkeyPatch):
    workbook = temp_state_paths / "edgewonk-export-78784.xls"
    workbook.write_bytes(b"dummy")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_SOURCE", "both")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_LOCAL_DIR", temp_state_paths)
    monkeypatch.setattr(master_service, "_import_trading_journal_from_dropbox_excel", lambda progress_cb=None: {"ok": False, "rows_imported": 0, "workbooks_seen": 0, "errors": []})
    monkeypatch.setattr(
        master_service,
        "_parse_local_trading_journal_workbook",
        lambda path: (
            [{
                "id": f"local:{path.name}:1",
                "source": "local_excel",
                "asset_class": "fx",
                "symbol": "EURUSD",
                "open_time": "2026-04-01T00:00:00+00:00",
                "close_time": "2026-04-01T01:00:00+00:00",
            }],
            None,
        ),
    )
    result = master_service._import_trading_journal_from_sources()
    assert result["ok"] is True
    assert result["local_workbooks_seen"] == 1
    rows = master_service._get_trading_journal_rows()
    assert any(str(r.get("source")) == "local_excel" for r in rows)


def test_import_from_sources_preserves_existing_rows_on_empty_result(temp_state_paths, monkeypatch: pytest.MonkeyPatch):
    master_service._set_trading_journal_rows([{"id": "existing:1", "source": "manual", "open_time": "2026-04-01T00:00:00+00:00"}])
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_SOURCE", "local")
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_LOCAL_DIR", temp_state_paths)
    monkeypatch.setattr(master_service, "_list_local_trading_journal_workbooks", lambda: [])
    result = master_service._import_trading_journal_from_sources()
    assert result["ok"] is False
    rows = master_service._get_trading_journal_rows()
    assert any(str(r.get("id")) == "existing:1" for r in rows)


def test_parse_excel_generic_filename_infers_fx_asset_class(monkeypatch: pytest.MonkeyPatch):
    row = {
        "symbol": "EUR_USD",
        "open_time": "2026-04-01",
        "close_time": "2026-04-01",
        "net_profit": 1.0,
    }
    df = master_service.pd.DataFrame([row])

    class FakeExcel:
        sheet_names = ["Sheet1"]

    monkeypatch.setattr(master_service.pd, "ExcelFile", lambda *_args, **_kwargs: FakeExcel())
    monkeypatch.setattr(master_service.pd, "read_excel", lambda *_args, **_kwargs: df)
    rows, _bal = master_service._parse_excel_account_workbook("edgewonk-export-78784.xls", "/tmp/edgewonk-export-78784.xls", b"x")
    assert rows
    assert rows[0]["asset_class"] == "fx"
