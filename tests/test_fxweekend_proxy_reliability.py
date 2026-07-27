from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from render import master_service


def test_run_liquidation_proxy_uses_extended_timeout_and_forwards_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScript:
        name = "fxweekend-clone"
        is_running = True
        port = 54321

    captured = {"requests": 0, "timeout": None}

    class FakeUpstreamResponse:
        status_code = 200
        content = b'{"ok":true,"state":"verified flat"}'
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, *, follow_redirects, timeout):
            assert follow_redirects is False
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, content, headers):
            captured["requests"] += 1
            assert method == "POST"
            assert url.endswith("/api/run_now")
            assert content == b""
            assert headers["X-Forwarded-Prefix"] == (
                "/apps/fxweekend-clone"
            )
            await asyncio.sleep(0)
            return FakeUpstreamResponse()

    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }
        delivered = True
        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/apps/fxweekend-clone/api/run_now",
            "raw_path": b"/apps/fxweekend-clone/api/run_now",
            "query_string": b"",
            "headers": [(b"accept", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("example.invalid", 443),
        },
        receive=receive,
    )
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(
        master_service.script_manager,
        "get",
        lambda _name: FakeScript(),
    )
    monkeypatch.setattr(
        master_service.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )
    monkeypatch.setattr(
        master_service,
        "FXWEEKEND_RUN_NOW_PROXY_TIMEOUT_SECONDS",
        45.0,
    )

    response = asyncio.run(
        master_service.proxy_app(
            "fxweekend-clone",
            request,
            "api/run_now",
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body)["state"] == "verified flat"
    assert captured["requests"] == 1
    assert captured["timeout"].read == 45.0
    assert captured["timeout"].read > 30.0
    assert (
        master_service._app_proxy_timeout_seconds(
            "fxweekend-clone",
            "api/run_now",
            "GET",
        )
        == 30.0
    )
    assert (
        master_service._app_proxy_timeout_seconds(
            "spreads-clone",
            "api/run_now",
            "POST",
        )
        == 30.0
    )
