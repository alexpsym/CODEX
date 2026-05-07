import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_master_service(module_name: str, profile: str):
    old_profile = os.environ.get("APP_PROFILE")
    try:
        os.environ["APP_PROFILE"] = profile
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "render" / "master_service.py")
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


def test_import_local_profile_without_fastapi_error() -> None:
    master_service = _load_master_service("render_master_service_import_local", "local")
    assert master_service.APP_PROFILE == "local"


def test_import_render_profile_without_fastapi_error() -> None:
    master_service = _load_master_service("render_master_service_import_render", "render")
    assert master_service.APP_PROFILE == "render"


def test_import_journal_profile_without_fastapi_error() -> None:
    master_service = _load_master_service("render_master_service_import_journal", "journal")
    assert master_service.APP_PROFILE == "journal"


def test_home_page_local_profile_returns_dashboard_html() -> None:
    master_service = _load_master_service("render_master_service_home_local", "local")
    response = asyncio.run(master_service.home_page())
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.body.decode("utf-8")
    assert "dashboard-workspace" in body
    assert "scripts-grid" in body


def test_home_page_render_profile_returns_dashboard_html() -> None:
    master_service = _load_master_service("render_master_service_home_render", "render")
    response = asyncio.run(master_service.home_page())
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.body.decode("utf-8")
    assert "dashboard-workspace" in body
    assert "scripts-grid" in body


def test_render_profile_blocks_local_only_routes() -> None:
    master_service = _load_master_service("render_master_service_profile_render", "render")
    assert master_service._render_blocks_path("/merged/history") is True
    assert master_service._render_blocks_path("/trading-journal") is True
    assert master_service._render_blocks_path("/health") is False
    disabled = master_service._local_only_disabled_response("/trading-journal")
    assert disabled.status_code == 410
    assert "run_local_master_control.bat" in disabled.body.decode("utf-8")


def test_render_profile_scripts_hide_local_only_main_views() -> None:
    master_service = _load_master_service("render_master_service_profile_render_scripts", "render")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}

    assert "history" not in names
    assert "monitor" not in names
    assert "trading-journal" not in names
    assert "open-orders" not in names
    assert "bybit_monitor" not in names
    assert "oanda_monitor" not in names
    assert "calculator" in names
    assert "calculator" in names


def test_local_profile_includes_open_orders_but_not_trading_journal() -> None:
    master_service = _load_master_service("render_master_service_profile_local_scripts", "local")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    assert "open-orders" in names
    assert "trading-journal" not in names


def test_journal_profile_redirects_root_and_allows_trading_journal_api() -> None:
    master_service = _load_master_service("render_master_service_profile_journal", "journal")
    root_response = asyncio.run(master_service.home_page())
    assert root_response.status_code == 307
    assert root_response.headers.get("location") == "/trading-journal"
    page = asyncio.run(master_service.trading_journal_page())
    assert "Trading Journal" in page
    api_response = asyncio.run(master_service.trading_journal_sync_status())
    assert api_response.status_code == 200


def test_trading_journal_page_includes_stats_columns_and_wider_wrap() -> None:
    master_service = _load_master_service("render_master_service_trading_journal_layout", "journal")
    page = asyncio.run(master_service.trading_journal_page())
    assert ".tj-stats-dashboard" in page
    assert ".tj-stats-column" in page
    assert "@media (max-width: 1100px)" in page
    assert "@media (max-width: 720px)" in page
    assert "max-width: 1400px" not in page
    assert "width: min(1880px, calc(100vw - 32px));" in page
