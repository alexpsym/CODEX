import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
import types
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "httpx" not in sys.modules:
    sys.modules["httpx"] = types.SimpleNamespace(AsyncClient=None)
SPEC = importlib.util.spec_from_file_location("render_monthly_aud_reval", ROOT / "render" / "monthly_aud_revaluation.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"http {self.status_code}")

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        if "/v5/market/time" in url:
            return DummyResponse(200, {"retCode": 0, "result": {"timeSecond": "1700000008"}})
        wallet_calls = len([c for c in self.calls if "/v5/account/wallet-balance" in c[0]])
        if wallet_calls == 1:
            return DummyResponse(200, {"retCode": 10002, "retMsg": "invalid request, req_timestamp[1700000000000],server_timestamp[1700000008000],recv_window[5000]"})
        return DummyResponse(200, {"retCode": 0, "result": {"list": [{"totalEquity": "10"}]}})


def test_signed_get_retries_timestamp_window(monkeypatch):
    mod._BYBIT_TIME_OFFSET_CACHE.clear()
    mod.BYBIT_RECV_WINDOW_MS = 15000
    mod.BYBIT_SIGNED_REQUEST_MAX_RETRIES = 2
    calls = []
    monkeypatch.setattr(mod.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(calls))

    payload = asyncio.run(mod._bybit_signed_get(base_url="https://api.bybit.com", api_key="k", api_secret="s", path="/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
    assert payload["retCode"] == 0
    wallet = [c for c in calls if "/wallet-balance" in c[0]]
    time_calls = [c for c in calls if "/market/time" in c[0]]
    assert len(wallet) == 2
    assert len(time_calls) >= 1
    assert int(wallet[1][1]["X-BAPI-TIMESTAMP"]) >= int(wallet[0][1]["X-BAPI-TIMESTAMP"])
    assert wallet[1][1]["X-BAPI-RECV-WINDOW"] == "15000"


def test_persistent_timestamp_error_raises(monkeypatch):
    mod._BYBIT_TIME_OFFSET_CACHE.clear()
    mod.BYBIT_SIGNED_REQUEST_MAX_RETRIES = 2

    class Always10002(DummyClient):
        async def get(self, url, headers=None):
            if "/v5/market/time" in url:
                return DummyResponse(200, {"retCode": 0, "result": {"timeNano": "1700000008000000000"}})
            return DummyResponse(200, {"retCode": 10002, "retMsg": "invalid request, req_timestamp[1700000000000],server_timestamp[1700000008000],recv_window[5000]"})

    monkeypatch.setattr(mod.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *args, **kwargs: Always10002([]))
    with pytest.raises(mod.MonthlyAudRevalError) as exc:
        asyncio.run(mod._bybit_signed_get(base_url="https://api.bybit.com", api_key="k", api_secret="s", path="/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
    assert exc.value.code == "MONTHLY_AUD_REVAL_BYBIT_BALANCE_ERROR"
    assert exc.value.stage == "bybit_request"
    msg = str(exc.value)
    assert "path=/v5/account/wallet-balance" in msg
    assert "retCode=10002" in msg
    assert "recv_window=" in msg
    assert "server_delta_ms=" in msg


def test_non_timestamp_error_no_retry(monkeypatch):
    calls = []

    class NonTimestamp(DummyClient):
        async def get(self, url, headers=None):
            calls.append((url, headers or {}))
            return DummyResponse(200, {"retCode": 10004, "retMsg": "error sign"})

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *args, **kwargs: NonTimestamp(calls))
    with pytest.raises(mod.MonthlyAudRevalError) as exc:
        asyncio.run(mod._bybit_signed_get(base_url="https://api.bybit.com", api_key="k", api_secret="s", path="/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
    wallet_calls = [c for c in calls if "/wallet-balance" in c[0]]
    assert len(wallet_calls) == 1
    assert "retCode=10004" in str(exc.value)


def test_fetch_server_time_parser_paths(monkeypatch):
    payloads = [
        {"retCode": 0, "result": {"timeNano": "1700000008000000000"}},
        {"retCode": 0, "result": {"timeSecond": "1700000008"}},
        {"retCode": 0, "time": "1700000008000"},
    ]

    class TimeClient(DummyClient):
        async def get(self, url, headers=None):
            return DummyResponse(200, payloads.pop(0))

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *args, **kwargs: TimeClient([]))
    assert asyncio.run(mod._fetch_bybit_server_time_ms("https://api.bybit.com")) == 1700000008000
    assert asyncio.run(mod._fetch_bybit_server_time_ms("https://api.bybit.com")) == 1700000008000
    assert asyncio.run(mod._fetch_bybit_server_time_ms("https://api.bybit.com")) == 1700000008000


def test_signature_uses_header_recv_window(monkeypatch):
    mod.BYBIT_RECV_WINDOW_MS = 15000
    calls = []

    class CaptureClient(DummyClient):
        async def get(self, url, headers=None):
            calls.append((url, headers or {}))
            return DummyResponse(200, {"retCode": 0, "result": {"list": [{"totalEquity": "1"}]}})

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *args, **kwargs: CaptureClient(calls))
    monkeypatch.setattr(mod.time, "time", lambda: 1700000000.0)
    asyncio.run(mod._bybit_signed_get(base_url="https://api.bybit.com", api_key="k", api_secret="s", path="/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
    _, headers = calls[0]
    expected = mod._bybit_sign_request(headers["X-BAPI-TIMESTAMP"], "k", "s", "accountType=UNIFIED", recv_window="15000")
    assert headers["X-BAPI-SIGN"] == expected


def test_iter_target_months_includes_recent_closed_months():
    now_local = datetime(2026, 5, 12, 10, 0, tzinfo=ZoneInfo("Australia/Brisbane"))
    out = mod._iter_target_months([], now_local=now_local)
    assert "2026-03" in out
    assert "2026-04" in out
    assert "2026-05" not in out


def test_iter_target_months_skips_existing_month():
    now_local = datetime(2026, 5, 12, 10, 0, tzinfo=ZoneInfo("Australia/Brisbane"))
    out = mod._iter_target_months(["2026-03"], now_local=now_local)
    assert "2026-03" not in out
    assert "2026-04" in out
