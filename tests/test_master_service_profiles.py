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
    assert "calculator" in names
    assert "open-orders" in names
