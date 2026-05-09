import asyncio
import importlib.util
import os
import sys
from pathlib import Path
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
SPEC = importlib.util.spec_from_file_location(
    "render_master_service_oanda_history_export", ROOT / "render" / "master_service.py"
)
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_collect_oanda_history_range_uses_base_url_and_splits_windows(monkeypatch: pytest.MonkeyPatch):
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs)
        return [{"id": f"tx-{len(calls)}"}]

    monkeypatch.setattr(master_service, "_fetch_oanda_transactions_window", fake_fetch)

    start = master_service.datetime(2020, 1, 1, tzinfo=master_service.timezone.utc)
    end = master_service.datetime(2023, 1, 1, tzinfo=master_service.timezone.utc)

    transactions = asyncio.run(
        master_service._collect_oanda_history_range(
            account_id="acc",
            api_key="key",
            base_url="https://api-fxpractice.oanda.com/v3",
            start=start,
            end=end,
        )
    )

    assert len(calls) >= 3
    assert all(call["base_url"].endswith("/v3") for call in calls)
    assert [item["id"] for item in transactions] == ["tx-1", "tx-2", "tx-3", "tx-4"][: len(transactions)]


def test_get_oanda_history_config_live_uses_live_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OANDA_API_KEY", "live-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "live-account")
    monkeypatch.setenv("OANDA_API_URL_LIVE", "https://api-fxtrade.oanda.com")
    monkeypatch.setenv("OANDA_BASE_URL_LIVE", "https://ignore-me.example")

    config = master_service._get_oanda_history_config("live")

    assert config["mode"] == "live"
    assert config["base_url"] == "https://api-fxtrade.oanda.com/v3"


def test_get_oanda_history_config_demo_normalizes_v3(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OANDA_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID_DEMO", "demo-account")
    monkeypatch.setenv("OANDA_API_URL_DEMO", "https://api-fxpractice.oanda.com/v3")

    config = master_service._get_oanda_history_config("demo")

    assert config["mode"] == "demo"
    assert config["base_url"] == "https://api-fxpractice.oanda.com/v3"


def test_run_oanda_history_export_sanitizes_html_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OANDA_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID_DEMO", "demo-account")
    monkeypatch.setenv("OANDA_API_URL_DEMO", "https://api-fxpractice.oanda.com")
    monkeypatch.setattr(master_service, "OANDA_HISTORY_EXPORT_ROOT", tmp_path)

    async def failing_fetch(**kwargs):
        raise RuntimeError("Failed to fetch transactions: 403 <html>Attention Required | Cloudflare</html>")

    monkeypatch.setattr(master_service, "_fetch_oanda_transactions_window", failing_fetch)

    job = master_service.OandaHistoryJob(
        job_id="job1",
        status="queued",
        created_at=0,
        updated_at=0,
        params={"account": "demo", "period": "week", "complete": False},
    )

    asyncio.run(master_service._run_oanda_history_export(job))

    assert job.status == "error"
    assert job.error == (
        "OANDA history export failed with HTTP 403 from upstream. "
        "Check OANDA history base URL and credentials."
    )


def test_oanda_history_export_status_only_returns_download_when_file_exists(tmp_path: Path):
    missing_path = tmp_path / "missing.csv"
    job = master_service.OandaHistoryJob(
        job_id="job-download",
        status="done",
        created_at=0,
        updated_at=0,
        params={},
        output_path=missing_path,
    )
    master_service.OANDA_HISTORY_JOBS[job.job_id] = job
    try:
        response = asyncio.run(master_service.oanda_history_export_status(job.job_id))
        payload = response.body.decode("utf-8")
        assert "download_url" not in payload
    finally:
        master_service.OANDA_HISTORY_JOBS.pop(job.job_id, None)


def test_oanda_history_export_filename_contains_account_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OANDA_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID_DEMO", "demo-account")
    monkeypatch.setenv("OANDA_API_URL_DEMO", "https://api-fxpractice.oanda.com")
    monkeypatch.setenv("OANDA_API_KEY", "live-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "live-account")
    monkeypatch.setenv("OANDA_API_URL_LIVE", "https://api-fxtrade.oanda.com")
    monkeypatch.setattr(master_service, "OANDA_HISTORY_EXPORT_ROOT", tmp_path)

    async def fake_fetch(**kwargs):
        return []

    monkeypatch.setattr(master_service, "_fetch_oanda_transactions_window", fake_fetch)
    monkeypatch.setattr(master_service.oanda_history_exporter, "save_to_csv", lambda tx, path: Path(path).write_text("x"))

    demo_job = master_service.OandaHistoryJob(job_id="demo1", status="queued", created_at=0, updated_at=0, params={"account": "demo", "days": 1})
    live_job = master_service.OandaHistoryJob(job_id="live1", status="queued", created_at=0, updated_at=0, params={"account": "live", "days": 1})
    asyncio.run(master_service._run_oanda_history_export(demo_job))
    asyncio.run(master_service._run_oanda_history_export(live_job))
    assert demo_job.output_path is not None and "demo" in demo_job.output_path.name
    assert live_job.output_path is not None and "live" in live_job.output_path.name


import pandas as pd

def _sample_oanda_history_rows():
    return [
        {"TICKET":588,"TRANSACTION DATE":"2026-04-08 19:50:45 AEST","TRANSACTION TYPE":"MARKET_ORDER","DETAILS":"CLIENT_ORDER","INSTRUMENT":"NZD_USD","PRICE":"","UNITS":2550,"DIRECTION":"Buy","SPREAD COST":"","STOP LOSS":0.57864,"TAKE PROFIT":0.58888,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1493.64},
        {"TICKET":589,"TRANSACTION DATE":"2026-04-08 19:50:46 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER","INSTRUMENT":"NZD_USD","PRICE":0.58217,"UNITS":2550,"DIRECTION":"Buy","SPREAD COST":-0.1,"STOP LOSS":0.57864,"TAKE PROFIT":0.58888,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1493.64},
        {"TICKET":592,"TRANSACTION DATE":"2026-04-09 07:00:00 AEST","TRANSACTION TYPE":"DAILY_FINANCING","DETAILS":"DAILY_FINANCING","INSTRUMENT":"","PRICE":"","UNITS":"","DIRECTION":"","SPREAD COST":"","STOP LOSS":"","TAKE PROFIT":"","FINANCING":-0.1329,"COMMISSION":"","PL":"","BALANCE":1493.51},
        {"TICKET":594,"TRANSACTION DATE":"2026-04-09 19:35:17 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER_TRADE_CLOSE","INSTRUMENT":"NZD_USD","PRICE":0.58308,"UNITS":-2550,"DIRECTION":"Sell","SPREAD COST":-0.1,"STOP LOSS":"","TAKE PROFIT":"","FINANCING":"","COMMISSION":0,"PL":3.2847,"BALANCE":1496.92},
    ]

def test_oanda_transaction_history_allocates_daily_financing_to_single_open_trade():
    df = pd.DataFrame(_sample_oanda_history_rows())
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(df, account_mode='demo', account_label='OANDA DEMO', source_path='/tmp/oanda_history_demo.csv')
    assert len(parsed['rows']) == 1
    row = parsed['rows'][0]
    assert row['swap'] == pytest.approx(-0.1329)
    assert row['metrics']['oanda_export_pl'] == pytest.approx(3.2847)
    assert row['net_profit'] == pytest.approx(3.1518)
    assert row['balance_after_trade'] == pytest.approx(1496.92)

def test_oanda_transaction_history_csv_parses_closed_trades():
    rows = _sample_oanda_history_rows() + [
        {"TICKET":598,"TRANSACTION DATE":"2026-04-09 19:37:46 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER","INSTRUMENT":"NZD_USD","PRICE":0.58323,"UNITS":2916,"DIRECTION":"Buy","SPREAD COST":0,"STOP LOSS":0.58117,"TAKE PROFIT":0.58717,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1496.92},
        {"TICKET":601,"TRANSACTION DATE":"2026-04-10 02:13:06 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER_TRADE_CLOSE","INSTRUMENT":"NZD_USD","PRICE":0.58718,"UNITS":-2916,"DIRECTION":"Sell","SPREAD COST":0,"STOP LOSS":"","TAKE PROFIT":"","FINANCING":"","COMMISSION":0,"PL":16.1655,"BALANCE":1513.09},
        {"TICKET":604,"TRANSACTION DATE":"2026-04-22 20:51:56 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER","INSTRUMENT":"NZD_USD","PRICE":0.59116,"UNITS":30821,"DIRECTION":"Buy","SPREAD COST":0,"STOP LOSS":0.59097,"TAKE PROFIT":0.59166,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1513.09},
        {"TICKET":608,"TRANSACTION DATE":"2026-04-22 20:52:36 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER_TRADE_CLOSE","INSTRUMENT":"NZD_USD","PRICE":0.59112,"UNITS":-30821,"DIRECTION":"Sell","SPREAD COST":0,"STOP LOSS":"","TAKE PROFIT":"","FINANCING":"","COMMISSION":0,"PL":-1.7385,"BALANCE":1511.35},
        {"TICKET":612,"TRANSACTION DATE":"2026-04-28 20:52:52 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER","INSTRUMENT":"USD_JPY","PRICE":159.605,"UNITS":13319,"DIRECTION":"Sell","SPREAD COST":0,"STOP LOSS":159.681,"TAKE PROFIT":159.426,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1511.35},
        {"TICKET":615,"TRANSACTION DATE":"2026-04-28 21:13:44 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER_TRADE_CLOSE","INSTRUMENT":"USD_JPY","PRICE":159.681,"UNITS":-13319,"DIRECTION":"Buy","SPREAD COST":0,"STOP LOSS":"","TAKE PROFIT":"","FINANCING":"","COMMISSION":0,"PL":-8.9385,"BALANCE":1502.41},
        {"TICKET":618,"TRANSACTION DATE":"2026-04-30 19:45:59 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER","INSTRUMENT":"EUR_USD","PRICE":1.16929,"UNITS":6546,"DIRECTION":"Buy","SPREAD COST":0,"STOP LOSS":1.16824,"TAKE PROFIT":1.17148,"FINANCING":"","COMMISSION":"","PL":"","BALANCE":1502.41},
        {"TICKET":622,"TRANSACTION DATE":"2026-04-30 19:46:41 AEST","TRANSACTION TYPE":"ORDER_FILL","DETAILS":"MARKET_ORDER_TRADE_CLOSE","INSTRUMENT":"EUR_USD","PRICE":1.16910,"UNITS":-6546,"DIRECTION":"Sell","SPREAD COST":0,"STOP LOSS":"","TAKE PROFIT":"","FINANCING":"","COMMISSION":0,"PL":-1.7591,"BALANCE":1500.65},
    ]
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(pd.DataFrame(rows), account_mode='demo', account_label='OANDA DEMO', source_path='/tmp/oanda_history_demo.csv')
    assert len(parsed['rows']) == 5
    final = parsed['rows'][-1]
    assert final['raw_refs']['close_ticket'] == '622'
    assert final['balance_after_trade'] == pytest.approx(1500.65)
    assert final['net_profit'] == pytest.approx(-1.7591)
    assert final['account_label'] == 'OANDA DEMO'


def test_oanda_transaction_history_real_csv_blanks_do_not_crash(tmp_path: Path):
    csv_text = """TICKET,TRANSACTION DATE,TRANSACTION TYPE,DETAILS,INSTRUMENT,PRICE,UNITS,DIRECTION,SPREAD COST,STOP LOSS,TAKE PROFIT,FINANCING,COMMISSION,PL,BALANCE
588,2026-04-08 19:50:45 AEST,MARKET_ORDER,CLIENT_ORDER,NZD_USD,,2550,Buy,,0.57864,0.58888,,,,1493.64
589,2026-04-08 19:50:46 AEST,ORDER_FILL,MARKET_ORDER,NZD_USD,0.58217,2550,Buy,-0.1,,,,,1493.64
592,2026-04-09 07:00:00 AEST,DAILY_FINANCING,DAILY_FINANCING,,,,,,,,-0.1329,,,1493.51
594,2026-04-09 19:35:17 AEST,ORDER_FILL,MARKET_ORDER_TRADE_CLOSE,NZD_USD,0.58308,-2550,Sell,-0.1,,,,0,3.2847,1496.92
598,2026-04-09 19:37:46 AEST,ORDER_FILL,MARKET_ORDER,NZD_USD,0.58323,2916,Buy,0,0.58117,0.58717,,, ,1496.92
601,2026-04-10 02:13:06 AEST,ORDER_FILL,MARKET_ORDER_TRADE_CLOSE,NZD_USD,0.58718,-2916,Sell,0,,,,0,16.1655,1513.09
604,2026-04-22 20:51:56 AEST,ORDER_FILL,MARKET_ORDER,NZD_USD,0.59116,30821,Buy,0,0.59097,0.59166,,,,1513.09
608,2026-04-22 20:52:36 AEST,ORDER_FILL,MARKET_ORDER_TRADE_CLOSE,NZD_USD,0.59112,-30821,Sell,0,,,,0,-1.7385,1511.35
612,2026-04-28 20:52:52 AEST,ORDER_FILL,MARKET_ORDER,USD_JPY,159.605,13319,Sell,0,159.681,159.426,,,,1511.35
615,2026-04-28 21:13:44 AEST,ORDER_FILL,MARKET_ORDER_TRADE_CLOSE,USD_JPY,159.681,-13319,Buy,0,,,,0,-8.9385,1502.41
618,2026-04-30 19:45:59 AEST,ORDER_FILL,MARKET_ORDER,EUR_USD,1.16929,6546,Buy,0,1.16824,1.17148,,,,1502.41
622,2026-04-30 19:46:41 AEST,ORDER_FILL,MARKET_ORDER_TRADE_CLOSE,EUR_USD,1.1691,-6546,Sell,0,,,,0,-1.7591,1500.65
"""
    path = tmp_path / 'oanda_history_demo.csv'
    path.write_text(csv_text)
    df = pd.read_csv(path, encoding='utf-8-sig')
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(df, account_mode='demo', account_label='OANDA DEMO', source_path=str(path))
    assert len(parsed['rows']) == 5
    assert parsed['unmatched_open_fills'] == []
    assert parsed['unmatched_close_fills'] == []
    final = parsed['rows'][-1]
    assert final['raw_refs']['close_ticket'] == '622'
    assert final['balance_after_trade'] == pytest.approx(1500.65)


def test_oanda_transaction_history_uses_client_order_stop_target():
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(pd.DataFrame(_sample_oanda_history_rows()), account_mode='demo', account_label='OANDA DEMO', source_path='/tmp/oanda_history_demo.csv')
    row = parsed['rows'][0]
    assert row['stop_loss'] == pytest.approx(0.57864)
    assert row['take_profit'] == pytest.approx(0.58888)
