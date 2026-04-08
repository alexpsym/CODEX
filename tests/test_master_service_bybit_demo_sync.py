import asyncio
import importlib.util
import json
import sys
from pathlib import Path

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
