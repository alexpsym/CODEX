import asyncio
import importlib.util
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
    assert '"spreads-clone": "Spread Monitor"' in source
    assert '.script-toolbar-grid .script-btn[data-script-name="spreads-clone"] { max-width: 148px; }' in source


def test_spread_monitor_files_are_present_and_tracked():
    required = [
        "spreads-clone/spread_app.py",
        "spreads-clone/spread_core.py",
        "tests/test_spread_core.py",
        "tests/test_spread_monitor_dashboard.py",
        "tests/test_mt5_spread_fetch.py",
        "tests/test_oanda_spread_fetch.py",
    ]
    for rel_path in required:
        assert (ROOT / rel_path).exists(), rel_path
    if not (ROOT / ".git").exists():
        pytest.skip("Git index unavailable in this checkout.")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *required],
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
    assert "Pepperstone Razor" in source


def test_spread_app_frontend_normalizes_messages_refresh_and_sorting():
    source = (ROOT / "spreads-clone" / "spread_app.py").read_text(encoding="utf-8")
    assert "function scalarMessage(value)" in source
    assert "JSON.stringify(value)" in source
    assert "payloadHasFailures(payload)" in source
    assert "function refreshIntervalSeconds(payload)" in source
    assert "Number.isFinite(seconds) && seconds > 0 ? seconds : 300" in source
    assert "updateLastRefresh: true" in source
    assert "headEl.addEventListener('click'" in source
    assert "sortState.direction === 'asc' ? 'desc' : 'asc'" in source
    assert "function cellSortValue(row, timeframe)" in source
    assert "Math.max(...values)" in source
    assert "pepperstone_razor" in source
    assert "[object Object]" not in source


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
    ]
    positions = [names.index(name) for name in expected]
    assert positions == sorted(positions)
    assert names.index("spreads-clone") == names.index("ivindicator-clone") + 1
    by_name = {str(item.get("name")): item for item in payload}
    assert by_name["spreads-clone"]["label"] == "Spread Monitor"
    assert by_name["spreads-clone"]["open_url"] == "/apps/spreads-clone"
    assert by_name["spreads-clone"]["dashboard_main_view"] is True


def test_render_profile_does_not_expose_spread_monitor_or_mt5_app():
    master_service = _load_master_service("render_master_service_spread_dashboard_render", "render")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    assert "spreads-clone" not in names
    assert master_service._render_blocks_path("/apps/spreads-clone") is True


def test_spread_app_status_endpoint_returns_honest_payload_without_broker_connections():
    spread_dir = ROOT / "spreads-clone"
    sys.path.insert(0, str(spread_dir))
    spec = importlib.util.spec_from_file_location("spread_app_endpoint_test", spread_dir / "spread_app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    client = module.app.test_client()
    response = client.get("/api/spreads/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, dict)
    assert "ok" in payload
    assert payload["refresh_interval_seconds"] == 300
    assert payload["timeframes"] == ["1M", "5M", "15M", "30M", "1H", "4H", "D", "W", "M"]
    assert isinstance(payload["rows"], list)
