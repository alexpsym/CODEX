import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_calc_retired", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"content-type", b"application/json")],
    }
    return Request(scope, receive)


def test_retired_calculator_script_status_returns_410() -> None:
    with pytest.raises(master_service.HTTPException) as bybit_exc:
        master_service.script_manager.get("cryptocalculator-clone")
    assert bybit_exc.value.status_code == 410

    with pytest.raises(master_service.HTTPException) as oanda_exc:
        master_service.script_manager.get("oanda-calculator-clone")
    assert oanda_exc.value.status_code == 410


def test_calculator_endpoints_return_410() -> None:
    with pytest.raises(master_service.HTTPException) as merged_exc:
        asyncio.run(master_service.merged_calculator_page())
    assert merged_exc.value.status_code == 410

    with pytest.raises(master_service.HTTPException) as execute_exc:
        asyncio.run(master_service.execute_now(_json_request({"symbol": "BTCUSDT"})))
    assert execute_exc.value.status_code == 410

    with pytest.raises(master_service.HTTPException) as webhook_exc:
        asyncio.run(master_service.default_webhook(_json_request({"script_name": "anything"})))
    assert webhook_exc.value.status_code == 410

    with pytest.raises(master_service.HTTPException) as webhook_retired_exc:
        asyncio.run(master_service.webhook("cryptocalculator-clone", _json_request({"symbol": "BTCUSDT"})))
    assert webhook_retired_exc.value.status_code == 410


def test_webhook_unsupported_target_is_not_success() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.webhook("not-a-real-script", _json_request({"foo": "bar"})))
    assert exc.value.status_code == 404


def test_scripts_page_excludes_merged_calculator_button() -> None:
    response = asyncio.run(master_service.list_scripts())
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    open_urls = {str(item.get("open_url") or "") for item in payload}

    assert "calculator" not in names
    assert "cryptocalculator-clone" not in names
    assert "oanda-calculator-clone" not in names
    assert "/merged/calculator" not in open_urls
