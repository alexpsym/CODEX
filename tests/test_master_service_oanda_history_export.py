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
