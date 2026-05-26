import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_master_service_module():
    """Load master_service even when optional httpx is unavailable."""
    sentinel = object()
    restored_modules = {}

    def _install_stub_if_missing(module_name: str, attrs: dict[str, object]) -> None:
        if importlib.util.find_spec(module_name) is not None:
            return
        restored_modules[module_name] = sys.modules.get(module_name, sentinel)
        module = types.ModuleType(module_name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[module_name] = module

    class _DummyError(Exception):
        pass

    _install_stub_if_missing("httpx", {
        "Client": object,
        "AsyncClient": object,
        "TimeoutException": _DummyError,
        "HTTPError": _DummyError,
        "RequestError": _DummyError,
        "ConnectError": _DummyError,
        "ReadTimeout": _DummyError,
        "HTTPStatusError": _DummyError,
    })
    if importlib.util.find_spec("multipart") is None:
        restored_modules["multipart"] = sys.modules.get("multipart", sentinel)
        restored_modules["multipart.multipart"] = sys.modules.get("multipart.multipart", sentinel)
        multipart_module = types.ModuleType("multipart")
        multipart_module.__version__ = "0.0"
        multipart_submodule = types.ModuleType("multipart.multipart")
        multipart_submodule.parse_options_header = lambda value: (value, {})
        multipart_module.multipart = multipart_submodule
        sys.modules["multipart"] = multipart_module
        sys.modules["multipart.multipart"] = multipart_submodule

    if importlib.util.find_spec("urllib3") is None:
        restored_modules["urllib3"] = sys.modules.get("urllib3", sentinel)
        restored_modules["urllib3.util"] = sys.modules.get("urllib3.util", sentinel)
        restored_modules["urllib3.util.retry"] = sys.modules.get("urllib3.util.retry", sentinel)

        urllib3_module = types.ModuleType("urllib3")
        urllib3_util_module = types.ModuleType("urllib3.util")
        urllib3_retry_module = types.ModuleType("urllib3.util.retry")

        class _DummyRetry:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

        urllib3_retry_module.Retry = _DummyRetry
        urllib3_util_module.retry = urllib3_retry_module
        urllib3_module.util = urllib3_util_module
        sys.modules["urllib3"] = urllib3_module
        sys.modules["urllib3.util"] = urllib3_util_module
        sys.modules["urllib3.util.retry"] = urllib3_retry_module

    if importlib.util.find_spec("requests") is None:
        restored_modules["requests"] = sys.modules.get("requests", sentinel)
        restored_modules["requests.adapters"] = sys.modules.get("requests.adapters", sentinel)

        requests_module = types.ModuleType("requests")
        adapters_module = types.ModuleType("requests.adapters")

        class _DummyHTTPAdapter:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

        requests_module.Session = object
        requests_module.adapters = adapters_module
        adapters_module.HTTPAdapter = _DummyHTTPAdapter
        sys.modules["requests"] = requests_module
        sys.modules["requests.adapters"] = adapters_module

    try:
        spec = importlib.util.spec_from_file_location(
            "render_master_service_bybit_history", ROOT / "render" / "master_service.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for module_name, previous in restored_modules.items():
            if previous is sentinel:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous


master_service = _load_master_service_module()


def test_bybit_window_demo_complete_uses_now_and_clamps_7_days(monkeypatch):
    fixed_now = datetime(2026, 5, 25, 23, 55, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(master_service, "datetime", FixedDateTime)
    start_ms, end_ms, _max_days, meta = master_service._bybit_window_for_request(
        account_mode="demo", period="complete", complete=True, days_value=None
    )
    assert end_ms == int(fixed_now.timestamp() * 1000)
    assert end_ms - start_ms <= master_service.bybit_history_fetcher.SEVEN_DAYS_MS
    assert meta["clamped_days"] == 7



def test_bybit_window_week_period_path_works(monkeypatch):
    fixed_now = datetime(2026, 5, 25, 23, 55, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(master_service, "datetime", FixedDateTime)
    start_ms, end_ms, _max_days, meta = master_service._bybit_window_for_request(
        account_mode="demo", period="week", complete=False, days_value=None
    )
    assert end_ms == int(fixed_now.timestamp() * 1000)
    assert start_ms <= end_ms
    assert meta["requested_days"] >= 7
    assert meta["clamped_days"] == 7


def test_run_bybit_history_export_passes_mode_override_and_epoch_window(monkeypatch, tmp_path):
    fixed_now = datetime(2026, 5, 25, 23, 55, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(master_service, "datetime", FixedDateTime)
    job = master_service.BybitHistoryJob(job_id="job1", params={"account": "demo", "complete": True}, status="queued", created_at=0, updated_at=0)
    monkeypatch.setattr(master_service, "BYBIT_HISTORY_EXPORT_ROOT", tmp_path)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _m: ("demo", "k", "s", "https://api-demo.bybit.com", "env"))
    monkeypatch.setattr(master_service.os, "getcwd", lambda: str(tmp_path))

    called = {}

    def fake_download(*args, **kwargs):
        called.update(kwargs)
        Path("generated.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        diag = kwargs.get("diagnostics_out")
        if isinstance(diag, dict):
            diag.update({"start_ms": kwargs["start_ms_override"], "end_ms": kwargs["end_ms_override"], "base_url": "https://api-demo.bybit.com", "row_count": 1})
        return "generated.csv"

    monkeypatch.setattr(master_service.bybit_history_fetcher, "download_history", fake_download)
    asyncio.run(master_service._run_bybit_history_export(job))
    assert job.status == "done"
    assert called["mode_override"] == "demo"
    assert called["start_ms_override"] <= called["end_ms_override"]
    assert called["end_ms_override"] == int(fixed_now.timestamp() * 1000)
    assert (tmp_path / f"bybit_history_{job.job_id}.csv").exists()


def test_run_bybit_history_export_empty_has_sanitized_window_diagnostics(monkeypatch):
    job = master_service.BybitHistoryJob(job_id="job2", params={"account": "demo", "complete": True}, status="queued", created_at=0, updated_at=0)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _m: ("demo", "k", "s", "https://api-demo.bybit.com", "env"))

    def fake_download(*_args, **kwargs):
        diag = kwargs.get("diagnostics_out")
        if isinstance(diag, dict):
            diag.update({
                "base_url": "https://api-demo.bybit.com",
                "start_ms": 1710000000000,
                "end_ms": 1710003600000,
                "row_count": 0,
            })
        return None

    monkeypatch.setattr(master_service.bybit_history_fetcher, "download_history", fake_download)
    asyncio.run(master_service._run_bybit_history_export(job))
    assert job.status == "error"
    assert "account_mode=demo" in (job.error or "")
    assert "start_utc=" in (job.error or "")
    assert "end_utc=" in (job.error or "")
    assert "row_count=0" in (job.error or "")
    assert "base_host=api-demo.bybit.com" in (job.error or "")
    assert "BYBIT_API_KEY" not in (job.error or "")
    assert "BYBIT_API_SECRET" not in (job.error or "")
