import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MASTER_SERVICE_PATH = ROOT / "render" / "master_service.py"


def _load_master_service(module_name: str, profile: str):
    old_profile = os.environ.get("APP_PROFILE")
    try:
        os.environ["APP_PROFILE"] = profile
        spec = importlib.util.spec_from_file_location(module_name, MASTER_SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if old_profile is None:
            os.environ.pop("APP_PROFILE", None)
        else:
            os.environ["APP_PROFILE"] = old_profile


def test_dashboard_source_registers_spread_monitor_local_only_web_app():
    source = MASTER_SERVICE_PATH.read_text(encoding="utf-8")
    assert '"spreads-clone"' in source
    assert '"spreads-clone": ["spread_app.py"]' in source
    assert '"spreads-clone": "Spreads"' in source
    assert '.script-toolbar-grid .script-btn[data-script-name="spreads-clone"] { max-width: 96px; }' in source


def test_local_launcher_installs_spread_monitor_requirements_with_same_python():
    source = (ROOT / "run_local_master_control.bat").read_text(encoding="utf-8")
    assert "spreads-clone\\requirements.txt" in source
    assert '"!PYTHON_EXE!" -m pip install -r "!ROOT!spreads-clone\\requirements.txt"' in source
    assert "SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL" in source


def test_spread_monitor_files_are_present_and_tracked():
    required = [
        "spreads-clone/spread_app.py",
        "spreads-clone/spread_core.py",
        "spreads-clone/pepperstone_import.py",
        "tests/test_spread_core.py",
        "tests/test_spread_monitor_dashboard.py",
        "tests/test_mt5_spread_fetch.py",
        "tests/test_oanda_spread_fetch.py",
        "tests/test_pepperstone_spread_import.py",
    ]
    for rel_path in required:
        assert (ROOT / rel_path).exists(), rel_path
    tracked_required = [path for path in required if path not in {
        "spreads-clone/pepperstone_import.py",
        "tests/test_pepperstone_spread_import.py",
    }]
    if not (ROOT / ".git").exists():
        pytest.skip("Git index unavailable in this checkout.")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *tracked_required],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_spread_app_table_layout_prevents_broker_value_overlap():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "min-width: 1620px;" in source
    assert "width: 164px;" in source
    assert "broker-value" in source
    assert "Pepperstone" in source


def test_spread_app_frontend_normalizes_messages_refresh_and_sorting():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "function scalarMessage(value)" in source
    assert "JSON.stringify(value)" in source
    assert "payloadHasFailures(payload)" in source
    assert "function isRefreshRunning(payload)" in source
    assert "function refreshIntervalSeconds(payload)" in source
    assert "Number.isFinite(seconds) && seconds > 0 ? seconds : 300" in source
    assert "updateLastRefresh: true" in source
    assert "function queueStatusPoll()" in source
    assert "function pollRefreshStatus()" in source
    assert "loadStatus();" in source
    assert "refreshData({ initial: true })" in source
    assert "hideOandaCacheUntilFresh" in source
    assert "Loading OANDA cache" not in source
    assert "loadStatus().then(() => refreshData())" not in source
    assert "headEl.addEventListener('click'" in source
    assert "sortState.direction === 'asc' ? 'desc' : 'asc'" in source
    assert "function cellSortValue(row, timeframe)" in source
    assert "renderOandaCurrentTable(payload)" in source
    assert "Current Spread" in source
    assert "spreadNumber(brokerData(cell))" in source
    assert "function spreadNumber(data)" in source
    assert "raw === null || raw === undefined || raw === ''" in source
    assert "value > 0 ? value : NaN" in source
    assert "const unavailable = !Number.isFinite(spreadValue);" in source
    assert ".spread-neutral" in source
    assert "pepperstone_razor" in source
    assert "function importPepperstone(file)" in source
    assert "manual import only" in source
    assert "[object Object]" not in source


def test_spread_app_no_longer_imports_live_mt5_fetchers():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "mt5_spreads" not in source
    assert "fetch_mt5_spread_samples" not in source
    assert "available_mt5_symbols" not in source
    assert "preflight_mt5_environment" not in source


def test_spread_app_selector_buttons_and_legend_wording_exist():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert 'data-broker="oanda">Oanda</button>' in source
    assert 'data-broker="pepperstone">Pepperstone</button>' in source
    assert "Low percentile" in source
    assert "Medium percentile" in source
    assert "High percentile" in source
    assert "Unavailable" in source


def test_spread_app_table_renders_one_selected_broker_line_per_cell():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "brokerLine(label, brokerData(cell))" in source
    assert "brokerLine('OANDA', brokerData(cell, 'oanda'))" not in source
    assert "brokerLine('Pepperstone Razor'" not in source


def test_scripts_endpoint_places_spread_monitor_after_iv_indicator_in_local_profile():
    master_service = _load_master_service("render_master_service_spread_dashboard_local", "local")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = [str(item.get("name")) for item in payload]
    expected = [
        "calculator",
        "trading-journal",
        "open-orders",
        "history",
        "monitor",
        "ivindicator-clone",
        "spreads-clone",
        "mt5",
        "pine",
    ]
    positions = [names.index(name) for name in expected]
    assert positions == sorted(positions)
    assert names.index("spreads-clone") == names.index("ivindicator-clone") + 1
    by_name = {str(item.get("name")): item for item in payload}
    assert by_name["spreads-clone"]["label"] == "Spreads"
    assert by_name["spreads-clone"]["open_url"] == "/apps/spreads-clone"
    assert by_name["spreads-clone"]["dashboard_main_view"] is True
    assert by_name["mt5"]["label"] == "MT5"
    assert by_name["mt5"]["open_url"] == "/dashboard/mt5"
    assert by_name["mt5"]["dashboard_main_view"] is True
    assert by_name["pine"]["label"] == "Pine"
    assert by_name["pine"]["open_url"] == "/dashboard/pine"
    assert by_name["pine"]["dashboard_main_view"] is True


def test_render_profile_does_not_expose_spread_monitor_or_mt5_app():
    master_service = _load_master_service("render_master_service_spread_dashboard_render", "render")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    assert "spreads-clone" not in names
    assert "mt5" not in names
    assert "pine" not in names
    assert master_service._render_blocks_path("/apps/spreads-clone") is True
    assert master_service._render_blocks_path("/dashboard/mt5") is True
    assert master_service._render_blocks_path("/dashboard/pine") is True


def test_spread_app_status_endpoint_returns_honest_payload_without_broker_connections():
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    client = module.app.test_client()
    response = client.get("/api/spreads/oanda/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, dict)
    assert "ok" in payload
    assert payload["refresh_interval_seconds"] == 300
    assert payload["timeframes"] == []
    assert payload["current_only"] is True
    assert payload["columns"] == [
        {"key": "symbol", "label": "Instrument"},
        {"key": "current_spread", "label": "Current Spread"},
    ]
    assert isinstance(payload["rows"], list)
    alias = client.get("/api/spreads/status")
    assert alias.status_code == 200


def test_spread_refresh_endpoint_starts_background_job_without_blocking(monkeypatch):
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_refresh_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class FakeState:
        def status(self):
            return {"ok": True, "refresh_state": "idle", "rows": [], "timeframes": []}

        def start_refresh(self):
            return {"ok": True, "refresh_state": "running", "status": "refresh_in_progress", "rows": [], "timeframes": []}

    monkeypatch.setattr(module, "OANDA_STATE", FakeState())
    client = module.app.test_client()
    response = client.post("/api/spreads/oanda/refresh")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["refresh_state"] == "running"
    assert payload["status"] == "refresh_in_progress"
    alias = client.post("/api/spreads/refresh")
    assert alias.status_code == 200


def test_pepperstone_status_and_import_endpoint_are_manual_only(monkeypatch):
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_pepperstone_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class FakePepperstoneState:
        def status(self):
            return {
                "ok": False,
                "manual_import_only": True,
                "rows": [],
                "timeframes": ["1M"],
                "errors": ["No imported Pepperstone spread data is available yet."],
            }

        def import_text(self, _text, *, source_path):
            return {
                "ok": True,
                "manual_import_only": True,
                "source_filename": str(source_path),
                "rows": [
                    {
                        "symbol": "EUR_USD",
                        "display_symbol": "EUR/USD",
                        "cells": {"1M": {"pepperstone_razor": {"spread_pct": 0.01, "display": "0.0100%"}}},
                    }
                ],
                "timeframes": ["1M"],
                "errors": [],
            }

    monkeypatch.setattr(module, "PEPPERSTONE_STATE", FakePepperstoneState())
    client = module.app.test_client()
    status = client.get("/api/spreads/pepperstone/status")
    assert status.status_code == 200
    assert status.get_json()["manual_import_only"] is True

    response = client.post(
        "/api/spreads/pepperstone/import",
        data={"file": (io.BytesIO(b'{"version":1}'), "pepperstone_spreads_latest.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["rows"][0]["cells"]["1M"]["pepperstone_razor"]["spread_pct"] == 0.01
