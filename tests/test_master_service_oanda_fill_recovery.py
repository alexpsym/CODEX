import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
SPEC = importlib.util.spec_from_file_location(
    "render_master_service_oanda_fill_recovery", ROOT / "render" / "master_service.py"
)
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


@pytest.fixture(autouse=True)
def reset_oanda_state():
    master_service._OANDA_OPEN_TRADE_LEGS["demo"] = {}
    master_service._OANDA_OPEN_TRADE_LEGS["live"] = {}
    master_service._OANDA_TX_LAST_SEEN.clear()
    master_service._OANDA_FILL_DIAGNOSTICS.clear()
    yield


def test_journal_rows_from_oanda_open_fill_only_returns_empty():
    rows = master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "100",
            "instrument": "EUR_USD",
            "time": "2026-04-10T00:00:00Z",
            "tradeOpened": {"tradeID": "t1", "price": "1.2000", "units": "1000"},
        }
    )

    assert rows == []
    assert master_service._OANDA_OPEN_TRADE_LEGS["demo"]["t1"]["entry_price"] == 1.2


def test_journal_rows_from_oanda_close_fill_uses_cached_open_leg():
    master_service._upsert_trade_context(
        {
            "order_id": "ord1",
            "trade_id": "t1",
            "transaction_id": "100",
            "broker": "oanda",
            "account": "demo",
            "instrument": "EUR_USD",
            "side": "buy",
            "timeframe": "1-hour",
            "stop_loss": "1.1900",
            "take_profit": "1.2200",
        }
    )
    master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "100",
            "instrument": "EUR_USD",
            "orderID": "ord1",
            "time": "2026-04-10T00:00:00Z",
            "tradeOpened": {"tradeID": "t1", "price": "1.2000", "units": "1000"},
        }
    )

    rows = master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "101",
            "instrument": "EUR_USD",
            "orderID": "ord1",
            "time": "2026-04-10T01:00:00Z",
            "tradesClosed": [
                {
                    "tradeID": "t1",
                    "units": "-1000",
                    "price": "1.2100",
                    "realizedPL": "12.3",
                    "financing": "-0.1",
                }
            ],
            "halfSpreadCost": "0.4",
            "commission": "0.2",
            "accountBalance": "1001.5",
            "accountCurrency": "AUD",
        }
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["open_time"] == "2026-04-10T00:00:00Z"
    assert row["close_time"] == "2026-04-10T01:00:00Z"
    assert row["entry_price"] == 1.2
    assert row["exit_price"] == 1.21
    assert row["notes"] == ""
    assert row["timeframe"] == "1-hour"
    assert row["stop_loss"] == "1.1900"
    assert row["take_profit"] == "1.2200"


def test_journal_rows_from_oanda_partial_close_keeps_metadata():
    master_service._upsert_trade_context(
        {
            "order_id": "ord2",
            "trade_id": "t2",
            "broker": "oanda",
            "account": "demo",
            "instrument": "EUR_USD",
            "side": "buy",
            "timeframe": "15-minute",
            "stop_loss": "1.0900",
            "take_profit": "1.1300",
        }
    )
    master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "200",
            "orderID": "ord2",
            "instrument": "EUR_USD",
            "time": "2026-04-10T00:00:00Z",
            "tradeOpened": {"tradeID": "t2", "price": "1.1000", "units": "1000"},
        }
    )
    rows = master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "201",
            "orderID": "ord2",
            "instrument": "EUR_USD",
            "time": "2026-04-10T01:00:00Z",
            "tradeReduced": {"tradeID": "t2", "units": "-500", "price": "1.1100", "realizedPL": "3.1"},
            "accountCurrency": "AUD",
        }
    )
    assert rows and rows[0]["timeframe"] == "15-minute"
    assert rows[0]["stop_loss"] == "1.0900"
    assert rows[0]["take_profit"] == "1.1300"


def test_recover_oanda_recent_fills_sorts_and_dedupes(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_get_oanda_config(account: str):
        return {"base_url": "https://api", "account_id": "acc", "token": "tok", "mode": account}

    async def fake_fetch_oanda_transactions(**kwargs):
        calls.append(kwargs)
        return (
            [
                {"id": "11", "time": "2026-04-10T01:00:00Z", "instrument": "EUR_USD", "account": "demo", "tradesClosed": [{"tradeID": "t1", "units": "-1"}]},
                {"id": "10", "time": "2026-04-10T00:00:00Z", "instrument": "EUR_USD", "account": "demo", "tradesClosed": [{"tradeID": "t2", "units": "-1"}]},
                {"id": "11", "time": "2026-04-10T01:00:00Z", "instrument": "EUR_USD", "account": "demo", "tradesClosed": [{"tradeID": "t1", "units": "-1"}]},
            ],
            "12",
        )

    def fake_mapper(entry):
        return [{"id": f"oanda:demo:EUR_USD:{entry['id']}:close", "status": "closed", "raw_refs": {}}]

    upserted = []

    def fake_upsert(rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(master_service, "_get_oanda_config", fake_get_oanda_config)
    monkeypatch.setattr(master_service, "_fetch_oanda_transactions", fake_fetch_oanda_transactions)
    monkeypatch.setattr(master_service, "_journal_rows_from_oanda_order_fill", fake_mapper)
    monkeypatch.setattr(master_service, "_upsert_trading_journal_rows", fake_upsert)

    result = asyncio.run(master_service._recover_oanda_recent_fills("demo", lookback_hours=48))

    assert result["recovered_rows"] == 2
    assert len(upserted) == 2
    assert calls and calls[0].get("since_id") is None
    assert calls[0].get("start_time")
    assert calls[0].get("end_time")
    assert master_service._OANDA_TX_LAST_SEEN["demo"] == "12"


def test_poll_oanda_fills_runs_recovery_on_cold_start(monkeypatch: pytest.MonkeyPatch):
    sleep_calls = {"count": 0}
    recovery_calls = []

    async def fake_sleep(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] > 1:
            raise asyncio.CancelledError()

    def fake_get_oanda_config(account: str):
        return {"base_url": "https://api", "account_id": "acc", "token": "tok", "mode": account}

    async def fake_recover(account: str, lookback_hours: int = 72):
        recovery_calls.append((account, lookback_hours))
        master_service._OANDA_TX_LAST_SEEN[account] = "100"
        return {"ok": True}

    async def should_not_fetch(**_kwargs):
        raise AssertionError("sinceid fetch should not run for cold-start account")

    monkeypatch.setattr(master_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(master_service, "_get_oanda_config", fake_get_oanda_config)
    monkeypatch.setattr(master_service, "_recover_oanda_recent_fills", fake_recover)
    monkeypatch.setattr(master_service, "_fetch_oanda_transactions", should_not_fetch)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(master_service._poll_oanda_fills())

    assert ("live", 72) in recovery_calls
    assert ("demo", 72) in recovery_calls
