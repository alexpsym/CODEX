import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# dependency-tolerant import shims
bm_pkg = types.ModuleType("bybit_monitor")
bm_mod = types.ModuleType("bybit_monitor.bybit_altcoin_monitor")
bm_mod.__getattr__ = lambda _name: (lambda *a, **k: None)
bm_pkg.bybit_altcoin_monitor = bm_mod
sys.modules.setdefault("bybit_monitor", bm_pkg)
sys.modules.setdefault("bybit_monitor.bybit_altcoin_monitor", bm_mod)
om_pkg = types.ModuleType("oanda_monitor")
om_mod = types.ModuleType("oanda_monitor.oanda_forex_monitor")
om_mod.__getattr__ = lambda _name: (lambda *a, **k: None)
om_pkg.oanda_forex_monitor = om_mod
sys.modules.setdefault("oanda_monitor", om_pkg)
sys.modules.setdefault("oanda_monitor.oanda_forex_monitor", om_mod)
try:
    _httpx_spec = importlib.util.find_spec("httpx")
except ValueError:
    _httpx_spec = None
if _httpx_spec is None:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.AsyncClient = object
    httpx_stub.Timeout = lambda *a, **k: None
    httpx_stub.TimeoutException = Exception
    httpx_stub.RequestError = Exception
    httpx_stub.HTTPStatusError = Exception
    httpx_stub.Response = object
    httpx_stub.ConnectError = Exception
    sys.modules.setdefault("httpx", httpx_stub)
mp_pkg = types.ModuleType("multipart")
mp_pkg.__version__ = "0.0-test"
mp_sub = types.ModuleType("multipart.multipart")
mp_sub.parse_options_header = lambda *args, **kwargs: ("", {})
sys.modules.setdefault("multipart", mp_pkg)
sys.modules.setdefault("multipart.multipart", mp_sub)
try:
    _requests_spec = importlib.util.find_spec("requests")
except ValueError:
    _requests_spec = None
if _requests_spec is None:
    requests_stub = types.ModuleType("requests")
    requests_adapters = types.ModuleType("requests.adapters")
    requests_adapters.HTTPAdapter = object
    requests_stub.adapters = requests_adapters
    sys.modules.setdefault("requests", requests_stub)
    sys.modules.setdefault("requests.adapters", requests_adapters)
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
    assert master_service._OANDA_OPEN_TRADE_LEGS["demo"]["t1"]["open_time"] == "2026-04-10T10:00:00+10:00"


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
    assert row["open_time"] == "2026-04-10T10:00:00+10:00"
    assert row["close_time"] == "2026-04-10T11:00:00+10:00"
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


def test_journal_rows_from_oanda_fill_dedupes_missing_context_warning(monkeypatch: pytest.MonkeyPatch):
    warnings = []

    class DummyLogger:
        def warning(self, message, *args):
            warnings.append(message % args if args else message)

    monkeypatch.setattr(master_service, "BYBIT_LOGGER", DummyLogger())
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])

    rows = master_service._journal_rows_from_oanda_order_fill(
        {
            "account": "demo",
            "id": "601",
            "orderID": "599",
            "instrument": "NZD_USD",
            "units": "1000",
            "time": "2026-04-10T01:00:00Z",
            "tradesClosed": [
                {"tradeID": "t1", "units": "-500", "price": "0.6000", "realizedPL": "1"},
                {"tradeID": "t1", "units": "-500", "price": "0.6010", "realizedPL": "1"},
            ],
            "accountCurrency": "AUD",
        }
    )

    assert len(rows) == 2
    assert len([line for line in warnings if "OANDA_CONTEXT_MISSING" in line]) == 2
    assert any("trade_id=t1" in line for line in warnings)
    assert any("trade_id=" in line and "trade_id=t1" not in line for line in warnings)


def test_oanda_missing_context_warns_once_across_repeated_recovery(tmp_path, monkeypatch: pytest.MonkeyPatch):
    warnings = []
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "state.json")

    class DummyLogger:
        def warning(self, message, *args):
            warnings.append(message % args if args else message)

    monkeypatch.setattr(master_service, "BYBIT_LOGGER", DummyLogger())
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])

    payload = {
        "account": "demo",
        "id": "999",
        "orderID": "998",
        "instrument": "NZD_USD",
        "units": "1000",
        "time": "2026-04-10T01:00:00Z",
        "tradesClosed": [{"tradeID": "t9", "units": "-1000", "price": "0.6010", "realizedPL": "1"}],
    }
    master_service._journal_rows_from_oanda_order_fill(payload)
    master_service._journal_rows_from_oanda_order_fill(payload)

    assert len([line for line in warnings if "OANDA_CONTEXT_MISSING" in line]) == 1
    state = master_service._load_trading_journal_state()
    key = "demo|999|998|t9|NZD_USD"
    entry = state.get("unresolved_registry", {}).get("oanda_context", {}).get(key, {})
    assert entry.get("count") == 2
    assert entry.get("resolved") is False


def test_oanda_ambiguous_warns_once(tmp_path, monkeypatch: pytest.MonkeyPatch):
    warnings = []
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "state.json")

    class DummyLogger:
        def warning(self, message, *args):
            warnings.append(message % args if args else message)

    monkeypatch.setattr(master_service, "BYBIT_LOGGER", DummyLogger())
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [
            {"broker": "oanda", "account": "demo", "instrument": "NZD_USD", "side": "buy", "status": "CLOSED"},
            {"broker": "oanda", "account": "demo", "instrument": "NZD_USD", "side": "buy", "status": "ACTIVE"},
        ],
    )

    payload = {
        "account": "demo",
        "id": "2001",
        "orderID": "2000",
        "instrument": "NZD_USD",
        "units": "1000",
        "time": "2026-04-10T01:00:00Z",
        "tradesClosed": [{"tradeID": "t2001", "units": "-1000", "price": "0.6010", "realizedPL": "1"}],
    }
    master_service._journal_rows_from_oanda_order_fill(payload)
    master_service._journal_rows_from_oanda_order_fill(payload)

    assert len([line for line in warnings if "OANDA_CONTEXT_AMBIGUOUS" in line]) == 1


def test_oanda_fallback_resolution_persists_ids_without_warning(tmp_path, monkeypatch: pytest.MonkeyPatch):
    warnings = []
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_STATE_PATH", tmp_path / "state.json")

    class DummyLogger:
        def warning(self, message, *args):
            warnings.append(message % args if args else message)

    monkeypatch.setattr(master_service, "BYBIT_LOGGER", DummyLogger())
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [
            {
                "broker": "oanda",
                "account": "demo",
                "instrument": "NZD_USD",
                "side": "buy",
                "status": "CLOSED",
                "timeframe": "1-hour",
                "stop_loss": "0.5900",
                "take_profit": "0.6200",
                "created_at": "2026-04-10T00:30:00+00:00",
                "updated_at": "2026-04-10T00:30:00+00:00",
            }
        ],
    )
    upserts = []
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: upserts.append(payload) or payload)

    payload = {
        "account": "demo",
        "id": "3001",
        "orderID": "3000",
        "instrument": "NZD_USD",
        "units": "1000",
        "time": "2026-04-10T01:00:00+00:00",
        "tradesClosed": [{"tradeID": "t3001", "units": "-1000", "price": "0.6010", "realizedPL": "1"}],
    }
    rows = master_service._journal_rows_from_oanda_order_fill(payload)

    assert rows and rows[0]["timeframe"] == "1-hour"
    assert not [line for line in warnings if "OANDA_CONTEXT_" in line]
    assert any(item.get("order_id") == "3000" and item.get("trade_id") == "t3001" for item in upserts)
