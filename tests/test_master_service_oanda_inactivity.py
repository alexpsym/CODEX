import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_oanda_inactivity", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


@pytest.fixture(autouse=True)
def _reset_inactivity_cache() -> None:
    master_service._OANDA_INACTIVITY_CACHE["payload"] = None
    master_service._OANDA_INACTIVITY_CACHE["expires_at"] = 0.0
    master_service._OANDA_INACTIVITY_CACHE["status_code"] = 200


class _FailingResponse:
    def __init__(self, status_code: int, body: str, url: str):
        self.status_code = status_code
        self.text = body
        self.content = body.encode("utf-8")
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("boom", request=self.request, response=self)


class _Always520Client:
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        _Always520Client.call_count += 1
        return _FailingResponse(
            520,
            "<html><head><title>Internal Server Error</title></head><body>oops</body></html>",
            url,
        )


def test_fetch_oanda_json_retries_520_and_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _Always520Client.call_count = 0
    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Always520Client)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(master_service.asyncio, "sleep", _no_sleep)

    with pytest.raises(master_service.OandaUpstreamHTTPError) as exc_info:
        asyncio.run(
            master_service._fetch_oanda_json(
                base_url="https://example.test",
                account_id="001-011-ABC",
                api_key="token",
                endpoint="/accounts/{account_id}/summary",
                mode="live",
            )
        )

    assert _Always520Client.call_count == 3
    err = exc_info.value
    assert err.status_code == 520
    assert err.transient is True
    assert "Internal Server Error (HTML response" in err.body_summary
    assert "<html" not in err.body_summary.lower()


def test_oanda_inactivity_status_returns_503_for_transient_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_upstream() -> dict:
        raise master_service.OandaUpstreamHTTPError(
            status_code=520,
            mode="live",
            account_id="001",
            endpoint="/accounts/{account_id}/summary",
            body_summary="Internal Server Error (HTML response, 123 bytes)",
            transient=True,
        )

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _raise_upstream)

    response = asyncio.run(master_service.oanda_inactivity_status())
    assert response.status_code == 503
    payload = response.body.decode("utf-8")
    assert '"status":"unavailable"' in payload
    assert '"upstream_status":520' in payload
    assert '"transient":true' in payload


def test_oanda_inactivity_status_cached_error_preserves_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    async def _raise_upstream() -> dict:
        state["calls"] += 1
        raise master_service.OandaUpstreamHTTPError(
            status_code=520,
            mode="live",
            account_id="001",
            endpoint="/accounts/{account_id}/summary",
            body_summary="Internal Server Error (HTML response, 123 bytes)",
            transient=True,
        )

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _raise_upstream)

    first = asyncio.run(master_service.oanda_inactivity_status())
    second = asyncio.run(master_service.oanda_inactivity_status())

    assert first.status_code == 503
    assert second.status_code == 503
    assert state["calls"] == 1


def test_oanda_inactivity_status_success_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok_payload() -> dict:
        return {
            "ok": True,
            "status": "countdown",
            "mode": "live",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(master_service, "_build_oanda_inactivity_status", _ok_payload)

    response = asyncio.run(master_service.oanda_inactivity_status())
    assert response.status_code == 200
    assert '"ok":true' in response.body.decode("utf-8")
