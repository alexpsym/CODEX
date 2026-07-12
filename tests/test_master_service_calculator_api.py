import asyncio
import importlib.util
import json
import re
import sys
import types
import importlib.machinery
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "multipart" not in sys.modules:
    multipart_mod = types.ModuleType("multipart")
    multipart_mod.__spec__ = importlib.machinery.ModuleSpec("multipart", loader=None)
    multipart_mod.__version__ = "0.0-test"
    multipart_sub = types.ModuleType("multipart.multipart")
    multipart_sub.__spec__ = importlib.machinery.ModuleSpec("multipart.multipart", loader=None)
    multipart_sub.parse_options_header = lambda value: (value, {})
    sys.modules["multipart"] = multipart_mod
    sys.modules["multipart.multipart"] = multipart_sub
try:
    import requests as _rq  # noqa: F401
    if not hasattr(_rq, "adapters"):
        raise ImportError
except Exception:
    req = types.ModuleType("requests")
    req.__spec__ = importlib.machinery.ModuleSpec("requests", loader=None)
    adapters = types.ModuleType("requests.adapters")
    adapters.__spec__ = importlib.machinery.ModuleSpec("requests.adapters", loader=None)
    adapters.HTTPAdapter = object
    req.adapters = adapters
    sys.modules["requests"] = req
    sys.modules["requests.adapters"] = adapters
try:
    from urllib3.util.retry import Retry  # noqa: F401
except Exception:
    urllib3 = types.ModuleType("urllib3")
    urllib3.__spec__ = importlib.machinery.ModuleSpec("urllib3", loader=None)
    util = types.ModuleType("urllib3.util")
    util.__spec__ = importlib.machinery.ModuleSpec("urllib3.util", loader=None)
    retry = types.ModuleType("urllib3.util.retry")
    retry.__spec__ = importlib.machinery.ModuleSpec("urllib3.util.retry", loader=None)
    retry.Retry = object
    sys.modules["urllib3"] = urllib3
    sys.modules["urllib3.util"] = util
    sys.modules["urllib3.util.retry"] = retry
HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
if not HTTPX_AVAILABLE:
    class _HttpxResponse:
        pass
    class _HttpxAsyncClient:
        pass
    httpx_stub = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=_HttpxAsyncClient,
        Response=_HttpxResponse,
        BaseTransport=object,
        TimeoutException=Exception,
        RequestError=Exception,
        HTTPStatusError=Exception,
        ConnectError=Exception,
    )
    httpx_stub.__spec__ = importlib.machinery.ModuleSpec("httpx", loader=None)
    sys.modules["httpx"] = httpx_stub

SPEC = importlib.util.spec_from_file_location("render_master_service_calculator_api", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)



def _clear_calculator_caches() -> None:
    for name in [
        "_BYBIT_SYMBOL_LIST_CACHE",
        "_BYBIT_INSTRUMENT_CACHE",
        "_BYBIT_TICKER_CACHE",
        "_BYBIT_TICKER_INFLIGHT",
        "_BYBIT_WALLET_BALANCE_CACHE",
        "_BYBIT_WALLET_BALANCE_INFLIGHT",
        "_OANDA_AUD_USD_CACHE",
    ]:
        obj = getattr(master_service, name, None)
        if isinstance(obj, dict):
            obj.clear()


@pytest.fixture(autouse=True)
def _reset_calculator_caches_between_tests():
    for name in [
        "PUBLIC_WEBHOOK_BASE_URL",
        "RENDER_CALCULATOR_BASE_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
        "ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS",
    ]:
        master_service.os.environ.pop(name, None)
    _clear_calculator_caches()
    yield
    for name in [
        "PUBLIC_WEBHOOK_BASE_URL",
        "RENDER_CALCULATOR_BASE_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
        "ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS",
    ]:
        master_service.os.environ.pop(name, None)
    _clear_calculator_caches()



def test_scripts_page_contains_calculator_row() -> None:
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    calc = next((row for row in payload if row.get("name") == "calculator"), None)
    assert calc is not None
    assert calc["open_url"] == "/merged/calculator"


def test_scripts_page_marks_merged_dashboard_views_non_standalone() -> None:
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    merged_names = {"calculator", "history", "monitor"}
    merged_rows = [row for row in payload if row.get("name") in merged_names]
    assert len(merged_rows) == len(merged_names)
    for row in merged_rows:
        assert row.get("standalone") is False
        assert row.get("dashboard_main_view") is True


def test_render_env_hides_local_only_scanner_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    original_manager = master_service.script_manager
    try:
        master_service.script_manager = master_service.ScriptManager(master_service.discover_scripts())
        response = asyncio.run(master_service.list_scripts())
        payload = json.loads(response.body.decode("utf-8"))
        names = {str(row.get("name")) for row in payload}
        assert "bybit_monitor" not in names
        assert "oanda_monitor" not in names
    finally:
        master_service.script_manager = original_manager


def test_scanner_merged_routes_return_gone_on_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    monitor = asyncio.run(master_service.merged_monitor_page())
    scanner = asyncio.run(master_service.merged_scanner_redirect())
    assert monitor.status_code == 410
    assert scanner.status_code == 410
    message = "Scanner is local-only. Run run_scanner_local.bat on your PC."
    assert monitor.body.decode("utf-8") == message
    assert scanner.body.decode("utf-8") == message


def test_scanner_merged_routes_work_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    monitor = asyncio.run(master_service.merged_monitor_page())
    scanner = asyncio.run(master_service.merged_scanner_redirect())
    assert monitor.status_code == 200
    assert scanner.status_code == 307
    assert scanner.headers.get("location") == "/merged/monitor"
    html = monitor.body.decode("utf-8")
    assert "Monitor controls" in html
    assert 'id="monitor-target"' in html
    assert 'id="monitor-status" class="badge">Checking…</span>' in html
    assert "/static/merged_alerts.js?v=" in html
    assert 'id="bybit-start-btn"' not in html
    assert 'id="oanda-start-btn"' not in html
    assert 'id="bybit-log-box"' not in html
    assert 'id="oanda-log-box"' not in html


def test_scripts_page_local_scanner_merged_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    response = asyncio.run(master_service.list_scripts())
    payload = json.loads(response.body.decode("utf-8"))
    names = [str(row.get("name")) for row in payload]
    assert "monitor" in names
    assert "bybit_monitor" not in names
    assert "oanda_monitor" not in names


def test_scripts_page_render_has_no_scanner_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER", "1")
    original_manager = master_service.script_manager
    try:
        master_service.script_manager = master_service.ScriptManager(master_service.discover_scripts())
        response = asyncio.run(master_service.list_scripts())
        payload = json.loads(response.body.decode("utf-8"))
        names = {str(row.get("name")) for row in payload}
        assert "monitor" not in names
        assert "bybit_monitor" not in names
        assert "oanda_monitor" not in names
    finally:
        master_service.script_manager = original_manager


def test_merged_calculator_page_returns_200() -> None:
    response = asyncio.run(master_service.merged_calculator_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Position Size Calculator" in html
    assert "max-width:880px" not in html
    assert 'target-toggle' not in html
    assert 'tp-ticks-wrap' not in html
    assert 'id="calc-rr"' in html
    assert "Type a symbol to load instrument specs." not in html
    assert "calc-grid" in html
    assert "settings-grid" not in html
    assert "calc-table" in html
    assert "Quote Results" in html
    assert html.count('<table class="calc-table">') >= 4
    assert '/static/calculator.js?v=' in html
    import re
    assert re.search(r"/static/calculator\.js\?v=[a-f0-9]{12}", html)
    assert 'id="calc-timeframe"' not in html
    assert 'id="timeframe-toggle"' in html
    assert 'id="test-toggle"' in html
    assert 'id="calc-instrument-specs"' not in html
    assert 'id="calc-journal-summary"' not in html
    assert 'class="card" id="calc-instrument-specs"' not in html
    assert html.find('id="account-toggle"') < html.find('id="asset-toggle"') < html.find('id="side-toggle"')
    assert html.find('id="order-toggle"') < html.find('id="calc-symbol"') < html.find('id="calc-sl-ticks"')
    assert html.find('id="timeframe-toggle"') < html.find('id="setup-toggle"') < html.find('id="pattern-toggle"')
    assert html.find('id="calc-quote"') < html.find('id="calc-results"')
    assert "calc-right-rail" not in html
    assert 'id="calc-webhook-url"' in html
    assert 'id="calc-webhook-copy-url"' in html


def test_calculator_js_net_r_only_and_no_idle_specs_placeholder() -> None:
    script = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    assert "Requested net R" in script
    assert "Effective net R" in script
    assert "Fee buffer (R)" in script
    assert "R:R" not in script
    assert "Type a symbol to load instrument specs." not in script


def test_bybit_place_order_not_modified_with_matching_live_tpsl_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: True)
    monkeypatch.setattr(master_service, "cache_bybit_demo_tpsl_request", lambda **_kwargs: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(
        master_service,
        "_wait_for_position_entry",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "size": "0.01",
                "avgPrice": "100",
                "entryPrice": "100",
                "positionIdx": 0,
                "takeProfit": "110",
                "stopLoss": "95",
            },
        ),
    )
    monkeypatch.setattr(
        master_service,
        "_fetch_bybit_positions",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[{"size": "0.01", "takeProfit": "110", "stopLoss": "95"}],
        ),
    )
    async def fake_trading_stop(**_kwargs):
        raise ValueError("Bybit trading-stop failed: not modified")

    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_trading_stop)
    monkeypatch.setattr(
        master_service,
        "_bybit_lookup_symbol",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}),
    )

    async def fake_get_async(_base_url, path, _params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99.9", "ask1Price": "100.1"}]}}
        if path == "/v5/market/instruments-info":
            return {"result": {"list": [{"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}}]}}
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get_async)

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"retCode": 0, "result": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    payload = {
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": "0.01",
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "stop_loss_price": "95",
        "take_profit_price": "110",
        "timeframe": "1h",
    }
    result = asyncio.run(master_service._place_bybit_order(payload, request_id="rid-1"))
    assert (result.get("order") or {}).get("orderId") == "oid-1"


def test_bybit_quote_uses_tick_step_fee_and_no_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "600", "coin": [{"coin": "USDT", "availableToTrade": "600"}]}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 0.5}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_lookup_linear_symbol_with_fallback", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)

    payload = {
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    qty = float(body["quantity"])
    assert qty > 0
    assert float(body["estimated_total_loss_aud"]) <= 1200.0001
    assert body["display_currency"] == "USDT"
    assert "estimated_fees_or_spread" in body
    assert "estimated_total_loss" in body
    assert "estimated_reward" in body


def test_place_bybit_order_returns_context_failure_warning_on_first_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_position_idx_for_order", lambda **_kwargs: 0)
    monkeypatch.setattr(master_service, "_parse_limit_cancel_settings", lambda _payload: (None, None))
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _id: False)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_signed_post(**_kwargs):
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_signed_post)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ctxfail")))

    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT", "action": "buy", "quantity": "0.01", "account": "demo", "order_type": "market"
    }, request_id="rid-first-fail"))
    assert result.get("journal_context_saved") is False
    assert result.get("context_save_error")
    assert result.get("warnings")


def test_place_bybit_order_secondary_context_failure_adds_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_position_idx_for_order", lambda **_kwargs: 0)
    monkeypatch.setattr(master_service, "_parse_limit_cancel_settings", lambda _payload: (None, None))
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _id: False)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.1"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_signed_post(**_kwargs):
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "oid-2", "orderLinkId": "ol-2"}}

    async def fake_wait_position(**_kwargs):
        return {"avgPrice": "100", "positionIdx": 0, "size": "1"}

    async def fake_set_tpsl(**_kwargs):
        return {"retCode": 0}

    async def fake_fetch_positions(**_kwargs):
        return [{"size": "1", "takeProfit": "110", "stopLoss": "95"}]

    calls = {"n": 0}
    def fake_upsert(_payload, require_durable=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("secondary-fail")
        return _payload

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_lookup_linear_symbol_with_fallback", lambda *_a, **_k: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.1"}}))
    monkeypatch.setattr(master_service, "_bybit_signed_post", fake_signed_post)
    monkeypatch.setattr(master_service, "_wait_for_position_entry", fake_wait_position)
    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_set_tpsl)
    monkeypatch.setattr(master_service, "_fetch_bybit_positions", fake_fetch_positions)
    monkeypatch.setattr(master_service, "_upsert_calculator_trade_context", fake_upsert)

    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT", "action": "buy", "quantity": "0.01", "account": "demo", "order_type": "market",
        "stop_loss_price": "95", "take_profit_price": "110"
    }, request_id="rid-second-fail"))
    assert result.get("journal_context_saved") is True
    assert calls["n"] >= 2

def test_bybit_quote_snaps_price_fields_with_trailing_zero_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.10"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "78032.90", "ask1Price": "78032.96", "lastPrice": "78032.93"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "10000", "totalAvailableBalance": "10000", "coin": [{"coin": "USDT", "availableToTrade": "10000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 0.001, "stop_loss_ticks": 1, "take_profit_ticks": 1,
    })).body.decode("utf-8"))
    assert body["entry_price"] == "78032.9"
    assert body["stop_price"] == "78032.8"
    assert body["target_price"] == "78033"


def test_bybit_quote_webhook_payload_uses_snapped_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-webhook.test")
    monkeypatch.delenv("RENDER_CALCULATOR_BASE_URL", raising=False)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.10"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "5"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "78032.90", "ask1Price": "78032.96", "lastPrice": "78032.93"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "10000", "totalAvailableBalance": "10000", "coin": [{"coin": "USDT", "availableToTrade": "10000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 0.001, "stop_loss_ticks": 1, "take_profit_ticks": 1, "webhook": "yes", "pattern": "range",
    })).body.decode("utf-8"))
    payload = json.loads(body["webhook_payload_json"])
    assert payload["entry_price"] == "78032.9"
    assert payload["stop_loss_price"] == "78032.8"
    assert payload["take_profit_price"] == "78033"
    assert payload["pattern"] == "range"


def test_bybit_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_get(base_url, path, params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        if path.endswith("tickers"):
            return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
        raise AssertionError(path)

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    async def fake_mids(**kwargs):
        return {"AUD_USD": 1}

    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", fake_mids)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "sell", "order_type": "market",
        "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "101"
    assert sell["entry_price"] == "100"


def test_oanda_quote_uses_display_precision_and_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {
            "prices": [{
                "bids": [{"price": "0.65000"}],
                "asks": [{"price": "0.65010"}],
            }],
            "homeConversions": [{"currency": "USD", "accountGain": "1.4", "accountLoss": "1.5", "positionValue": "1.45"}],
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    payload = {
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    }
    response = asyncio.run(master_service.calculator_quote(payload))
    body = json.loads(response.body.decode("utf-8"))
    assert body["tick_size"] == "0.00001"
    assert float(body["quantity"]) >= 1
    assert float(body["quantity"]) < 500000  # spread is included in risk sizing; otherwise this would be larger


def test_oanda_market_uses_side_specific_bid_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))

    async def fake_meta(**kwargs):
        return {"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1"}

    async def fake_fetch_json(**kwargs):
        return {
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}],
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", fake_meta)
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_json)
    buy = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    sell = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert buy["entry_price"] == "1.1002"
    assert sell["entry_price"] == "1.1"


def test_oanda_top_level_home_conversions_sell_market_fixed_aud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "prices": [{"bids": [{"price": "0.61000"}], "asks": [{"price": "0.61020"}]}],
                "homeConversions": [{"currency": "USD", "accountGain": "1.45", "accountLoss": "1.5", "positionValue": "1.47"}],
            },
        ),
    )
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "sell", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["symbol"] == "NZD_USD"
    assert float(body["quantity"]) > 0


def test_oanda_deprecated_quote_home_conversion_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}], "quoteHomeConversionFactors": {"positiveUnits": "1.3", "negativeUnits": "1.7"}}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "target_mode": "rr", "risk_reward": 2,
    })).body.decode("utf-8"))
    assert float(body["estimated_reward_aud"]) > float(body["estimated_total_loss_aud"]) * 1.5


def test_oanda_quote_home_same_currency_shortcut_without_conversion_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "0.91000"}], "asks": [{"price": "0.91020"}]}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "euraud", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert float(body["estimated_total_loss_aud"]) > 0


def test_oanda_gain_loss_conversion_split_applies_to_rr_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "prices": [{"bids": [{"price": "1.20000"}], "asks": [{"price": "1.20020"}]}],
                "homeConversions": [{"currency": "USD", "accountGain": "1.5", "accountLoss": "0.8", "positionValue": "1.2"}],
            },
        ),
    )
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "target_mode": "rr", "risk_reward": 2,
    })).body.decode("utf-8"))
    reward = float(body["estimated_reward_aud"])
    loss = float(body["estimated_total_loss_aud"])
    assert reward > loss * 3


def test_oanda_missing_conversion_returns_schema_aware_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 502
    assert "missing usable home conversion" in str(exc.value.detail).lower()
    assert "top_level_currencies" in str(exc.value.detail)


def test_limit_requires_entry_price() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "EUR_USD", "side": "buy", "order_type": "limit",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400


def test_missing_env_and_bad_symbols_return_real_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "", "", "https://bybit.test", "KEY1"))
    with pytest.raises(master_service.HTTPException) as bybit_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bybit_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: (_ for _ in ()).throw(ValueError("missing env")))
    with pytest.raises(master_service.HTTPException) as oanda_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "live", "symbol": "EUR_USD", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert oanda_exc.value.status_code == 500

    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    async def no_symbols(*_args, **_kwargs):
        return []
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", no_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(master_service, "_bybit_get_async", lambda *_a, **_k: asyncio.sleep(0, result={"result": {"list": []}}))
    with pytest.raises(master_service.HTTPException) as bad_symbol_exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "NOTREAL", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert bad_symbol_exc.value.status_code == 404


def test_crypto_rejects_fixed_aud() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400


def test_bybit_rejects_min_notional_and_market_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1", "coin": [{"coin": "USDT", "availableToTrade": "1"}]}]}}

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "10", "maxMktOrderQty": "2", "minNotionalValue": "1000"}, "leverageFilter": {"maxLeverage": "2"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "100", "lastPrice": "100"}]}}

    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    with pytest.raises(master_service.HTTPException) as min_notional:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2}))
    assert "minimum notional" in str(min_notional.value.detail).lower()


def test_oanda_rejects_max_units_and_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 1, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "pipLocation": -4, "minimumTradeSize": "1", "maximumOrderUnits": "10", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.1"}], "asks": [{"price": "1.1001"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market", "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2}))
    assert exc.value.status_code == 400


def test_submit_routes_to_existing_order_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"bybit": 0, "oanda": 0}

    async def fake_bybit(payload, *, request_id):
        calls["bybit"] += 1
        assert payload["timeframe"] == "15MIN"
        assert payload["is_test_trade"] is True
        assert payload["stop_loss_price"] == "1"
        assert payload["take_profit_price"] == "2"
        return {"ok": True}

    async def fake_oanda(payload, *, request_id):
        calls["oanda"] += 1
        assert payload["timeframe"] == "1H"
        return {"ok": True}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_place_oanda_order", fake_oanda)

    asyncio.run(master_service.calculator_submit({
        "asset": "crypto", "account": "live", "symbol": "BTCUSDT", "action": "buy", "order_type": "market",
        "entry_price": "100", "stop_loss_price": "1", "take_profit_price": "2", "quantity": "0.01", "timeframe": "15m", "test": "yes",
    }))
    asyncio.run(master_service.calculator_submit({
        "asset": "fx", "account": "demo", "symbol": "EUR_USD", "action": "sell", "order_type": "limit",
        "entry_price": "1.2", "stop_loss_price": "1.3", "take_profit_price": "1.1", "quantity": "1000", "timeframe": "1h",
    }))
    assert calls == {"bybit": 1, "oanda": 1}


def test_submit_and_webhook_bubble_context_warning_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(payload, *, request_id):
        return {"order": {"orderId": "oid"}, "journal_context_saved": False, "warnings": ["warn"], "context_save_error": "boom"}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _payload: None)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda _wid, _updates: {"id": _wid})
    monkeypatch.setattr(master_service, "_update_webhook_attempt", lambda *_a, **_k: None)
    monkeypatch.setattr(master_service, "_consume_pending_webhook", lambda *_a, **_k: True)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": []})))

    submit = json.loads(asyncio.run(master_service.calculator_submit({
        "asset": "crypto", "account": "demo", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"
    })).body.decode("utf-8"))
    assert submit["journal_context_saved"] is False
    assert submit["warnings"] == ["warn"]
    assert submit["context_save_error"] == "boom"

    webhook = json.loads(asyncio.run(master_service.calculator_webhook({
        "asset": "crypto", "pending_webhook_id": "wh-ctx", "account": "demo", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"
    })).body.decode("utf-8"))
    assert webhook["journal_context_saved"] is False
    assert webhook["warnings"] == ["warn"]
    assert webhook["context_save_error"] == "boom"


def test_journal_summary_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_get_trading_journal_rows",
        lambda: [
            {
                "row_type": "trade",
                "symbol": "EURUSD",
                "asset_class": "fx",
                "side": "Buy",
                "close_time": "2026-01-01T01:00:00Z",
                "open_time": "2026-01-01T00:00:00Z",
                "entry_price": 1.1,
                "stop_loss": 1.0,
                "take_profit": 1.2,
                "net_profit": 1,
                "balance_after_trade": 100,
                "is_test_trade": False,
            },
            {
                "row_type": "trade",
                "symbol": "EUR_USD",
                "asset_class": "fx",
                "side": "Sell",
                "close_time": "2026-01-02T01:00:00Z",
                "open_time": "2026-01-02T00:00:00Z",
                "entry_price": 1.2,
                "stop_loss": 1.3,
                "take_profit": 1.1,
                "net_profit": -1,
                "balance_after_trade": 99,
                "is_test_trade": False,
            },
            {
                "row_type": "trade",
                "symbol": "EUR_USD",
                "asset_class": "fx",
                "side": "Buy",
                "close_time": "2026-01-03T01:00:00Z",
                "open_time": "2026-01-03T00:00:00Z",
                "entry_price": 1.2,
                "stop_loss": 1.1,
                "take_profit": 1.3,
                "net_profit": 2,
                "balance_after_trade": 101,
                "is_test_trade": True,
            },
        ],
    )
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    response = asyncio.run(master_service.calculator_journal_summary(asset="fx", symbol="eurusd"))
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "ok"
    assert body["canonical_symbol"] == "EUR_USD"
    assert isinstance(body.get("trades"), list)
    assert len(body["trades"]) == 2
    assert body["trades"][0]["close_time"] == "2026-01-02T01:00:00Z"
    assert body["trades"][1]["close_time"] == "2026-01-01T01:00:00Z"


@pytest.mark.parametrize("query", ["BTCUSDT", "BTC/USDT", "Bitcoin USDT"])
def test_journal_summary_uses_master_workbook_rows_for_bitcoin_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    workbook = tmp_path / "Trading Journal.xlsx"
    workbook.write_bytes(b"placeholder")
    rows = [
        {
            "id": "btc-bybit",
            "row_type": "trade",
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "account": "BYBIT",
            "account_label": "BYBIT",
            "side": "Buy",
            "close_time": "2026-02-01T00:00:00Z",
            "open_time": "2026-02-01T00:00:00Z",
            "entry_price": 100,
            "exit_price": 101,
            "net_profit": 10,
            "result_pct": 1,
            "balance_after_trade": 110,
            "currency": "USDT",
            "is_test_trade": False,
        },
        {
            "id": "btc-binance",
            "row_type": "trade",
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "account": "BINANCE",
            "account_label": "BINANCE",
            "side": "Sell",
            "close_time": "2026-02-02T00:00:00Z",
            "open_time": "2026-02-02T00:00:00Z",
            "entry_price": 102,
            "exit_price": 101,
            "net_profit": 8,
            "result_pct": 0.8,
            "balance_after_trade": 118,
            "currency": "USDT",
            "is_test_trade": False,
        },
        {"id": "btc-test", "row_type": "trade", "symbol": "BTCUSDT", "asset_class": "crypto", "account": "BYBIT", "is_test_trade": True},
    ]
    monkeypatch.setattr(master_service, "_master_journal_single_file_mode", lambda: True)
    monkeypatch.setattr(master_service, "_master_journal_path", lambda: workbook)
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "read_master_journal_source", lambda _path: {"items": rows, "balances": []})
    monkeypatch.setattr(master_service, "_get_excel_account_balances", lambda: [])
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _account: ("live", "k", "s", "https://bybit.test", "KEY"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT", "ETHUSDT"]))
    monkeypatch.setattr(master_service, "_bybit_name_aliases_for_choices", lambda *_args, **_kwargs: asyncio.sleep(0, result={"BITCOIN": "BTC"}))

    response = asyncio.run(master_service.calculator_journal_summary(asset="crypto", symbol=query))
    body = json.loads(response.body.decode("utf-8"))

    assert body["status"] == "ok"
    assert body["canonical_symbol"] == "BTCUSDT"
    assert len(body["trades"]) == 2
    assert {trade["id"] for trade in body["trades"]} == {"btc-bybit", "btc-binance"}


def test_rr_fee_buffer_pushes_target_distance_beyond_plain_rr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "risk_reward": 2,
    })).body.decode("utf-8"))
    stop_distance = abs(float(body["entry_price"]) - float(body["stop_price"]))
    target_distance = abs(float(body["target_price"]) - float(body["entry_price"]))
    assert target_distance > (stop_distance * 2.0)


def test_fee_rate_failure_falls_back_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            raise ValueError("Bybit signed GET failed path=/v5/account/fee-rate retCode=10003 retMsg=API key is invalid")
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    response = asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
    }))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert isinstance(body.get("warnings"), list)
    assert "conservative fallback fees" in body["warnings"][0].lower()
    assert "path=/v5/account/fee-rate" not in body["warnings"][0]


def test_balance_failure_returns_endpoint_specific_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            return {"result": {"list": [{"makerFeeRate": "0.001", "takerFeeRate": "0.002"}]}}
        raise master_service.HTTPException(status_code=502, detail="Bybit balance lookup failed path=/v5/account/wallet-balance: retCode=10003 retMsg=invalid key")

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market",
            "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
        }))
    detail = str(exc.value.detail)
    assert "/v5/account/wallet-balance" in detail
    assert "Bybit request failed" not in detail


def test_submit_translates_bybit_errors_to_http_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(_payload, *, request_id):
        assert request_id
        raise ValueError("retCode=10001 request parameter error")

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({
            "asset": "crypto",
            "account": "live",
            "symbol": "BTCUSDT",
            "action": "buy",
            "order_type": "market",
            "entry_price": "100",
            "stop_loss_price": "90",
            "take_profit_price": "120",
            "quantity": "0.01",
            "timeframe": "15m",
        }))
    assert exc.value.status_code == 400
    assert "Order submit failed:" in str(exc.value.detail)


def test_webhook_uses_keyword_request_id_for_bybit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"request_id": "", "is_test_trade": None}

    async def fake_bybit(_payload, *, request_id):
        seen["request_id"] = request_id
        seen["is_test_trade"] = _payload.get("is_test_trade")
        return {"ok": True}

    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _payload: None)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": [{"broker": "bybit", "account": "live", "category": "linear", "instrument": "BTCUSDT", "id": "oid-1", "type": "order"}]})))
    monkeypatch.setattr(master_service, "_consume_pending_webhook", lambda *_args, **_kwargs: True)
    response = asyncio.run(master_service.calculator_webhook({
        "asset": "crypto",
        "account": "live",
        "symbol": "BTCUSDT",
        "action": "buy",
        "order_type": "market",
        "entry_price": "100",
        "stop_loss_price": "90",
        "take_profit_price": "120",
        "quantity": "0.01",
        "timeframe": "15m",
        "test": "yes",
    }))
    body = json.loads(response.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["request_id"].startswith("calc-webhook-")
    assert seen["is_test_trade"] is True


def test_webhook_consumes_pending_before_order_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"consumed": False, "request_id": ""}

    def fake_assert(_payload):
        return None

    def fake_consume(webhook_id: str, *, request_id: str, reason: str = "webhook_received") -> bool:
        assert webhook_id == "wh-123"
        assert reason == "order_accepted"
        seen["consumed"] = True
        seen["request_id"] = request_id
        return True

    async def fake_bybit(_payload, *, request_id):
        assert seen["consumed"] is False
        assert isinstance(request_id, str) and request_id.startswith("calc-webhook-")
        return {"ok": True, "order": {"orderId": "oid-1", "orderLinkId": "ol-1"}}

    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", fake_assert)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda _wid, _updates: {"id": _wid})
    monkeypatch.setattr(master_service, "_consume_pending_webhook", fake_consume)
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)
    monkeypatch.setattr(master_service, "list_open_orders", lambda force=False: asyncio.sleep(0, result=master_service.JSONResponse({"items": [{"broker": "bybit", "account": "live", "category": "linear", "instrument": "BTCUSDT", "id": "oid-1", "type": "order"}]})))
    response = asyncio.run(
        master_service.calculator_webhook(
            {
                "asset": "crypto",
                "pending_webhook_id": "wh-123",
                "account": "live",
                "symbol": "BTCUSDT",
                "action": "buy",
                "order_type": "market",
                "quantity": "0.01",
            }
        )
    )
    body = json.loads(response.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["consumed"] is True


def test_webhook_attempts_endpoint_returns_recent_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_load_webhook_attempts",
        lambda: [
            {"request_id": "r-1", "status": "BYBIT_REJECTED"},
            {"request_id": "r-2", "status": "BYBIT_ACCEPTED"},
        ],
    )
    response = asyncio.run(master_service.calculator_webhook_attempts(limit=1))
    body = json.loads(response.body.decode("utf-8"))
    assert len(body["items"]) == 1
    assert body["items"][0]["request_id"] == "r-2"


def test_webhook_records_bybit_rejection_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    events = {"record": None, "update": []}

    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: None)
    monkeypatch.setattr(master_service, "_update_pending_webhook", lambda *_args, **_kwargs: {"id": "wh-1"})
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)

    def fake_record(payload):
        events["record"] = dict(payload)
        return payload

    def fake_update(request_id, updates):
        events["update"].append((request_id, dict(updates)))
        return updates

    async def fake_bybit(_payload, *, request_id):
        raise master_service.BybitOrderRejected(
            ret_code=10001,
            ret_msg="request parameter error",
            ret_ext_info={"foo": "bar"},
            result={},
            request_body={"orderType": "Market"},
            http_status=200,
            response_body={"retCode": 10001},
        )

    monkeypatch.setattr(master_service, "_record_webhook_attempt", fake_record)
    monkeypatch.setattr(master_service, "_update_webhook_attempt", fake_update)
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_bybit)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(
            master_service.calculator_webhook(
                {
                    "asset": "crypto",
                    "pending_webhook_id": "wh-1",
                    "account": "demo",
                    "symbol": "BTCUSDT",
                    "action": "sell",
                    "order_type": "market",
                    "quantity": "0.01",
                }
            )
        )
    assert exc.value.status_code == 400
    assert events["record"]["status"] == "RECEIVED"
    assert any(update.get("status") == "BYBIT_REJECTED" for _, update in events["update"])
    rejected = next(update for _, update in events["update"] if update.get("status") == "BYBIT_REJECTED")
    assert rejected["bybit_ret_code"] == 10001
    assert rejected["bybit_ret_msg"] == "request parameter error"
    assert rejected["bybit_request"] == {"orderType": "Market"}


def test_calculator_quote_returns_absolute_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://codex-rdqh.onrender.com")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))
    body = json.loads(asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"})).body.decode("utf-8"))
    assert body["webhook_endpoint_url"] == "https://codex-rdqh.onrender.com/api/calculator/webhook"


def test_calculator_bootstrap_reports_local_webhook_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS", raising=False)
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    webhook = body["webhook"]
    assert webhook["available"] is False
    assert webhook["unavailable_code"] == "LOCAL_WEBHOOK_UNREACHABLE"
    assert webhook["webhook_origin_host"] in {"localhost", "127.0.0.1"}
    assert "RENDER_CALCULATOR_BASE_URL" in str(webhook.get("unavailable_message") or "")


def test_calculator_bootstrap_reports_public_webhook_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-tunnel.test")
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    webhook = body["webhook"]
    assert webhook["available"] is True
    assert webhook["webhook_endpoint_url"] == "https://example-tunnel.test/api/calculator/webhook"


def test_calculator_bootstrap_reports_remote_render_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://render.example.test")
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    webhook = json.loads(response.body.decode("utf-8"))["webhook"]
    assert webhook["available"] is True
    assert webhook["mode"] == "remote_render"
    assert webhook["webhook_endpoint_url"] == "https://render.example.test/api/calculator/webhook"
    assert webhook["pending_owner"] == "remote_render"


def test_calculator_bootstrap_includes_runtime_fingerprint() -> None:
    response = asyncio.run(master_service.calculator_bootstrap(master_service.Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000), "path": "/api/calculator/bootstrap", "headers": []})))
    body = json.loads(response.body.decode("utf-8"))
    for key in ("app_profile", "app_version", "app_build_stamp", "render_git_commit", "calculator_js_sha256_12", "calculator_js_mtime", "master_service_path", "render_calculator_base_url_configured", "render_calculator_base_url_host", "webhook"):
        assert key in body


def test_old_public_webhook_unavailable_string_removed() -> None:
    py = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    js = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    old = "PUBLIC_WEBHOOK_BASE_URL to a public same-instance tunnel"
    assert old not in py
    assert old not in js


def test_local_calculator_blocks_webhook_without_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("ALLOW_LOCAL_TRADINGVIEW_WEBHOOKS", raising=False)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}
    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"}))
    assert exc.value.status_code == 400
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "LOCAL_WEBHOOK_UNREACHABLE"
    debug = exc.value.detail.get("debug") or {}
    assert debug.get("webhook_origin_host") in {"localhost", "127.0.0.1"}
    assert "pending_webhook_id" not in debug


def test_local_webhook_quote_proxies_to_remote_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://render.example.test")
    monkeypatch.setattr(master_service, "_calculator_webhook_capability", lambda _r: {"available": True, "mode": "remote_render"})
    called = {"upsert": 0}
    monkeypatch.setattr(master_service, "_upsert_pending_webhook", lambda _payload: called.__setitem__("upsert", called["upsert"] + 1))

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"pending_webhook_id": "rid-1", "webhook_payload_json": "{\"a\":1}", "webhook_endpoint_url": "https://render.example.test/api/calculator/webhook"}

    class _Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def post(self, *_args, **_kwargs): return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    request = master_service.Request({"type": "http", "method": "POST", "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1234), "path": "/api/calculator/quote", "headers": []})
    response = asyncio.run(master_service.calculator_quote(request, {"asset": "crypto", "account": "live", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1, "take_profit_ticks": 2, "webhook": "yes"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert body["pending_webhook_owner"] == "remote_render"
    assert body["pending_webhook_delete_url"].startswith("/api/calculator/remote-pending-webhooks/")
    assert called["upsert"] == 0


def test_webhook_missing_pending_id_returns_409_and_attempt_row(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"attempt": None, "update": None}
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: (_ for _ in ()).throw(ValueError("Pending webhook missing or no longer active.")))
    monkeypatch.setattr(master_service, "_record_webhook_attempt", lambda payload: seen.__setitem__("attempt", dict(payload)) or dict(payload))
    monkeypatch.setattr(master_service, "_update_webhook_attempt", lambda _rid, updates: seen.__setitem__("update", dict(updates)) or dict(updates))
    response = asyncio.run(master_service.calculator_webhook({"asset": "crypto", "pending_webhook_id": "bogus", "account": "live", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 409
    assert body["code"] == "PENDING_WEBHOOK_NOT_FOUND"
    assert seen["attempt"]["status"] == "RECEIVED"
    assert seen["update"]["status"] == "PENDING_NOT_FOUND"


def test_crypto_demo_skips_fee_rate_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["BTCUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "1"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "50"}}]}}
        return {"result": {"list": [{"bid1Price": "100", "ask1Price": "101", "lastPrice": "100.5"}]}}

    async def fake_signed_get(**kwargs):
        if kwargs.get("path", "").endswith("fee-rate"):
            raise AssertionError("fee-rate should be skipped for demo calculator quotes")
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "1000", "coin": [{"coin": "USDT", "availableToTrade": "1000"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market",
        "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 5, "risk_reward": 2,
    })).body.decode("utf-8"))
    assert "warnings" not in body


def test_price_levels_match_helper() -> None:
    assert master_service._price_levels_match(100.0, 100.0)
    assert master_service._price_levels_match(100.0, 100.000000001)
    assert not master_service._price_levels_match(100.0, 100.1)
    assert master_service._price_levels_match(None, None)
    assert not master_service._price_levels_match(None, 100.0)


def test_snap_to_increment_handles_trailing_zeros_and_milli_ticks() -> None:
    assert str(master_service._snap_to_increment(master_service.Decimal("78032.96"), master_service.Decimal("0.10"))) == "78032.90"
    assert str(master_service._snap_to_increment(master_service.Decimal("1.2349"), master_service.Decimal("0.001"))) == "1.234"


def test_bybit_place_order_normalizes_tpsl_with_tick_size(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"),
    )
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda payload: payload)
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _pid: True)
    monkeypatch.setattr(master_service, "cache_bybit_demo_tpsl_request", lambda **_kwargs: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(
        master_service,
        "_wait_for_position_entry",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={"size": "0.01", "avgPrice": "78032.9", "entryPrice": "78032.9", "positionIdx": 0},
        ),
    )
    monkeypatch.setattr(
        master_service,
        "_fetch_bybit_positions",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[{"size": "0.01", "takeProfit": "78033", "stopLoss": "78032.8"}],
        ),
    )

    async def fake_trading_stop(**kwargs):
        captured["take_profit"] = kwargs.get("take_profit")
        captured["stop_loss"] = kwargs.get("stop_loss")
        return {"status": "ok"}

    monkeypatch.setattr(master_service, "_set_bybit_trading_stop", fake_trading_stop)
    monkeypatch.setattr(
        master_service,
        "_bybit_lookup_symbol",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"priceFilter": {"tickSize": "0.10"}}),
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"retCode": 0, "result": {"orderId": "oid-2", "orderLinkId": "ol-2"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", _Client)
    result = asyncio.run(master_service._place_bybit_order({
        "symbol": "BTCUSDT",
        "action": "buy",
        "quantity": "0.01",
        "account": "demo",
        "trade_mode": "linear",
        "order_type": "market",
        "entry_price": "78032.96",
        "level_anchor_mode": "planned_entry",
        "planned_entry_price": "78032.96",
        "planned_stop_price": "78032.86",
        "planned_target_price": "78033.06",
        "timeframe": "15m",
    }, request_id="rid-2"))
    assert (result.get("order") or {}).get("orderId") == "oid-2"
    assert captured["take_profit"] == 78033.0
    assert captured["stop_loss"] == 78032.8


def test_oanda_margin_uses_position_value_home_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 20, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(0, result={
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "2"}],
        }),
    )
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 10, "take_profit_ticks": 20,
        }))
    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert isinstance(detail, dict)
    debug = detail.get("debug") or {}
    assert debug.get("required_margin_home")
    assert debug.get("position_value_factor") == "2"


def test_oanda_fixed_aud_is_converted_to_home_currency_before_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000, "marginAvailable": 1000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(
        master_service,
        "_fetch_oanda_json",
        lambda **_kwargs: asyncio.sleep(0, result={
            "prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}],
            "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}],
        }),
    )
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 0.5}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["account_currency"] == "USD"
    assert body["risk_input_aud"] == "10"
    assert body["risk_amount_home"] == "5"


def test_oanda_quote_returns_account_currency_not_hardcoded_aud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 0.6}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20,
    })).body.decode("utf-8"))
    assert body["display_currency"] == "USD"


def test_oanda_margin_error_includes_required_and_available_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1000, "marginAvailable": 1, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({
            "asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market",
            "risk_mode": "fixed_aud", "risk_value": 100, "stop_loss_ticks": 1, "take_profit_ticks": 2,
        }))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "oanda_margin_insufficient"
    debug = detail.get("debug") or {}
    assert "required_margin_home" in debug
    assert "margin_available_home" in debug
    assert "margin_rate" in debug
    assert debug.get("submitted_risk_mode") == "fixed_aud"
    assert debug.get("submitted_risk_value") == "100"


def test_oanda_nzdusd_35_ticks_fixed_aud_10_demo_flat_account_quotes_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "AUD", "nav": 1513.09, "marginAvailable": 1513.09, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "0.61000"}], "asks": [{"price": "0.61020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1.6", "accountLoss": "1.6", "positionValue": "1.6"}]}))

    body = json.loads(asyncio.run(master_service.calculator_quote({
        "asset": "fx", "account": "demo", "symbol": "nzdusd", "side": "buy", "order_type": "market",
        "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 35, "take_profit_ticks": 70,
    })).body.decode("utf-8"))
    assert body["symbol"] == "NZD_USD"
    assert float(body["estimated_initial_margin_home"]) < 1513.09

def test_calculator_quote_bybit_success_has_no_logger_nameerror(monkeypatch: pytest.MonkeyPatch) -> None:
    saved_context = {}
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda payload, **_kwargs: saved_context.update(payload))
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result=["LABUSDT"]))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith("instruments-info"):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.0001"}, "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "999999", "maxMktOrderQty": "999999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "10"}}]}}
        return {"result": {"list": [{"bid1Price": "1.0000", "ask1Price": "1.0002", "lastPrice": "1.0001"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_bybit_signed_get", lambda **_kwargs: asyncio.sleep(0, result={"result": {"list": [{"makerFeeRate": "0", "takerFeeRate": "0"}]}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt", lambda *_args, **_kwargs: asyncio.sleep(0, result={"available_usdt": "1000", "total_equity": "1000"}))

    response = asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "LAB", "side": "sell", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20, "pattern": "channel"}))
    assert isinstance(response, master_service.JSONResponse)
    body = json.loads(response.body.decode("utf-8"))
    assert body["broker"] == "bybit"
    assert saved_context["pattern"] == "channel"
    for k in ("quantity", "entry_price", "stop_price", "target_price"):
        assert k in body


def test_calculator_quote_oanda_success_has_no_logger_nameerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"base_url": "https://oanda.test", "account_id": "acct", "token": "tok"})
    monkeypatch.setattr(master_service, "_fetch_oanda_instrument_meta", lambda **_kwargs: asyncio.sleep(0, result={"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.05"}))
    monkeypatch.setattr(master_service, "_fetch_oanda_json", lambda **_kwargs: asyncio.sleep(0, result={"prices": [{"bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}], "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}))
    monkeypatch.setattr(master_service, "_fetch_oanda_account_summary", lambda _a: asyncio.sleep(0, result={"currency": "USD", "nav": 1000000, "marginAvailable": 1000000, "marginRate": 0.05}))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))

    response = asyncio.run(master_service.calculator_quote({"asset": "fx", "account": "demo", "symbol": "eurusd", "side": "buy", "order_type": "market", "risk_mode": "fixed_aud", "risk_value": 10, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    assert isinstance(response, master_service.JSONResponse)
    body = json.loads(response.body.decode("utf-8"))
    assert body["broker"] == "oanda"


def _parse_set_file(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _trader_input_names() -> set[str]:
    source = (ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5").read_text(encoding="utf-8")
    names = set()
    for line in source.splitlines():
        match = re.match(r"\s*input\s+(?!group\b)(?:[A-Za-z_][A-Za-z0-9_<>]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.add(match.group(1))
    return names


def test_pepperstone_set_export_uses_trader_input_names_and_limit_mapping() -> None:
    base_payload = {
        "asset": "fx",
        "broker": "pepperstone",
        "account": "demo",
        "symbol": "EUR_USD",
        "side": "buy",
        "order_type": "limit",
        "entry_price": "1.10020",
        "risk_mode": "fixed_aud",
        "risk_value": "10",
        "stop_loss_ticks": "35",
        "risk_reward": "2",
    }
    response = asyncio.run(master_service.calculator_pepperstone_set(base_payload))
    assert response.media_type.startswith("text/plain")
    assert "Pepperstone_Trader_EUR_USD_BUY_" in response.headers["content-disposition"]
    values = _parse_set_file(response.body.decode("utf-8"))
    assert set(values).issubset(_trader_input_names())
    assert values["Strategy"] == "2"
    assert values["OrdersEnabled"] == "true"
    assert values["RiskAUD_Target"] == "10"
    assert values["RiskAUD_Min"] == "9"
    assert values["RiskAUD_Max"] == "12"
    assert values["IncludeCommissionInRisk"] == "true"
    assert values["CommissionPerLotPerSide"] == "3.5"
    assert values["RiskSlippageBufferPoints"] == "50"
    assert values["SlippagePoints"] == "10"
    assert values["SL_DistancePoints"] == "35"
    assert values["AutoTP_NetRR_Enabled"] == "true"
    assert values["NetRR_Target"] == "2"
    assert values["StandardLimitSide"] == "0"
    assert values["StandardLimitEntryPrice"] == "1.1002"
    assert values["MagicNumber"] == "91001"
    assert values["EnforceOneTradeAtATime"] == "true"

    sell_payload = dict(base_payload, side="sell")
    sell_response = asyncio.run(master_service.calculator_pepperstone_set(sell_payload))
    sell_values = _parse_set_file(sell_response.body.decode("utf-8"))
    assert sell_values["StandardLimitSide"] == "1"


def test_pepperstone_set_export_blocks_market_orders() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(
            master_service.calculator_pepperstone_set(
                {
                    "asset": "fx",
                    "broker": "pepperstone",
                    "symbol": "EUR_USD",
                    "side": "buy",
                    "order_type": "market",
                    "risk_mode": "fixed_aud",
                    "risk_value": "10",
                    "stop_loss_ticks": "35",
                    "risk_reward": "2",
                }
            )
        )
    assert exc.value.status_code == 400
    assert "limit orders only" in str(exc.value.detail)


def test_calculator_webhook_direct_dict_call_uses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_assert_pending_webhook_executable", lambda _p: (_ for _ in ()).throw(ValueError("Pending webhook missing or no longer active.")))
    response = asyncio.run(master_service.calculator_webhook({"asset": "crypto", "pending_webhook_id": "bogus", "symbol": "BTCUSDT", "action": "buy", "order_type": "market", "quantity": "0.01"}))
    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 409
    assert body.get("code") == "PENDING_WEBHOOK_NOT_FOUND"


def test_bybit_signed_get_signature_supports_timeout_args() -> None:
    import inspect
    sig = inspect.signature(master_service._bybit_signed_get)
    assert "timeout_s" in sig.parameters
    assert "connect_s" in sig.parameters
    assert "read_s" in sig.parameters




def test_fetch_bybit_balance_usdt_signature_supports_timeout_args() -> None:
    import inspect
    sig = inspect.signature(master_service._fetch_bybit_balance_usdt)
    assert "timeout_s" in sig.parameters
    assert "connect_s" in sig.parameters
    assert "read_s" in sig.parameters
def test_fetch_bybit_balance_usdt_passes_coin_and_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY1"))
    calls = {}

    async def fake_signed_get(**kwargs):
        calls.update(kwargs)
        return {"result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "900", "coin": [{"coin": "USDT", "availableToTrade": "900"}]}]}}

    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    out = asyncio.run(master_service._fetch_bybit_balance_usdt("demo", timeout_s=2.5, connect_s=1.0, read_s=2.5))
    assert out["available_usdt"] == master_service.Decimal("900")
    assert calls["path"] == "/v5/account/wallet-balance"
    assert calls["params"]["accountType"] == "UNIFIED"
    assert calls["params"]["coin"] == "USDT"
    assert calls["timeout_s"] == 2.5
    assert calls["connect_s"] == 1.0
    assert calls["read_s"] == 2.5


def test_crypto_quote_backend_timeout_beats_frontend_timeout() -> None:
    js = (ROOT / "render" / "static" / "calculator.js").read_text(encoding="utf-8")
    import re
    m = re.search(r"quoteTimeoutMs\s*=\s*(\d+)", js)
    assert m
    frontend_timeout_ms = int(m.group(1))
    backend_timeout_ms = int(master_service.CALCULATOR_QUOTE_TIMEOUT_S * 1000)
    assert frontend_timeout_ms <= 15000
    assert backend_timeout_ms <= 4500
    assert backend_timeout_ms < frontend_timeout_ms


def test_webhook_attempts_filter_by_pending_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [{"pending_webhook_id":"a","status":"X"},{"pending_webhook_id":"b","status":"Y"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_attempts(limit=50,pending_webhook_id="b")).body.decode("utf-8"))
    assert payload["matched_count"] == 1
    assert payload["items"][0]["pending_webhook_id"] == "b"


def test_webhook_diagnostic_status_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [])
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "NO_RENDER_ATTEMPT_RECORDED"

    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [{"id":"pid1"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "WAITING_NO_POST_RECEIVED"

    monkeypatch.setattr(master_service, "_load_webhook_attempts", lambda: [{"pending_webhook_id":"pid1","status":"BYBIT_REJECTED","bybit_ret_code":1001,"bybit_ret_msg":"bad","order_id":"o1","order_link_id":"l1"}])
    payload=json.loads(asyncio.run(master_service.calculator_webhook_diagnostic("pid1")).body.decode("utf-8"))
    assert payload["status"] == "BYBIT_REJECTED"
    assert payload["bybit_ret_code"] == 1001
    assert payload["bybit_ret_msg"] == "bad"


def test_calculator_webhook_capability_remote_render(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setenv("RENDER_CALCULATOR_BASE_URL", "https://codex-rdqh.onrender.com")
    req=Request({"type":"http","method":"GET","path":"/","headers":[],"query_string":b"","client":("127.0.0.1",1),"server":("localhost",80),"scheme":"http"})
    cap=master_service._calculator_webhook_capability(req)
    assert cap["mode"] == "remote_render"

def test_calculator_submit_bybit_rejection_returns_400_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bybit(_payload, request_id):
        raise master_service.BybitOrderRejected(ret_code=10001, ret_msg='request parameter error', ret_ext_info={'x':1}, result={}, request_body={'symbol':'BTCUSDT'}, http_status=200, response_body={'retCode':10001})

    monkeypatch.setattr(master_service, '_place_bybit_order', fake_bybit)
    resp = asyncio.run(master_service.calculator_submit({'asset':'crypto','account':'demo','symbol':'BTCUSDT','side':'buy','order_type':'limit'}))
    assert resp.status_code == 400
    body = json.loads(resp.body.decode('utf-8'))
    assert body['code'] == 'BYBIT_REJECTED'
    assert body['debug']['ret_code'] == 10001


def test_calculator_quote_rejects_buy_limit_above_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _a: ('live','k','s','https://bybit.test','KEY1'))
    monkeypatch.setattr(master_service, '_bybit_get_symbols_by_category_cached', lambda *_a, **_k: asyncio.sleep(0, result=['PARTIUSDT']))
    monkeypatch.setattr(master_service, '_fetch_bybit_balance_usdt', lambda *_a, **_k: asyncio.sleep(0, result={'available_usdt':'1000','total_equity':'1000'}))
    monkeypatch.setattr(master_service, '_fetch_oanda_mid_prices_batch', lambda **_k: asyncio.sleep(0, result={'AUD_USD':0.5}))
    async def fake_inst(*_args, **_kwargs):
        return {'priceFilter': {'tickSize':'0.0001'}, 'lotSizeFilter': {'qtyStep':'1','minOrderQty':'1','maxOrderQty':'999999','maxMktOrderQty':'999999','minNotionalValue':'0'}, 'leverageFilter': {'maxLeverage':'50'}}
    monkeypatch.setattr(master_service, '_bybit_get_instrument_info_cached', fake_inst)
    async def fake_get(base, path, params, **_kwargs):
        if path.endswith('tickers'):
            return {'result': {'list': [{'bid1Price':'0.4938','ask1Price':'0.4940','lastPrice':'0.4939'}]}}
        raise AssertionError(path)
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({'asset':'crypto','account':'live','symbol':'PARTI','side':'buy','order_type':'limit','entry_price':'0.5313','risk_mode':'percent','risk_value':1,'stop_loss_ticks':37,'take_profit_ticks':74}))
    assert exc.value.status_code == 400
    assert exc.value.detail['code'] in {'BYBIT_LIMIT_WOULD_FILL_IMMEDIATELY','BYBIT_STOP_LOSS_INVALID_FOR_BUY'}

def _mock_bybit_quote_env(monkeypatch, *, tick='0.00001', bid='0.04921', ask='0.04923', last='0.04922'):
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _a: ('live','k','s','https://bybit.test','KEY1'))
    monkeypatch.setattr(master_service, '_bybit_get_symbols_by_category_cached', lambda *_args, **_kwargs: asyncio.sleep(0, result=['PARTIUSDT','BTCUSDT']))
    monkeypatch.setattr(master_service, '_fetch_oanda_mid_prices_batch', lambda **_kwargs: asyncio.sleep(0, result={'AUD_USD': 1}))
    async def fake_get(_base, path, _params, **_kwargs):
        if path.endswith('instruments-info'):
            return {'result': {'list': [{'priceFilter': {'tickSize': tick}, 'lotSizeFilter': {'qtyStep': '1', 'minOrderQty': '1', 'maxOrderQty': '999999', 'maxMktOrderQty': '999999', 'minNotionalValue': '1'}, 'leverageFilter': {'maxLeverage': '50'}}]}}
        return {'result': {'list': [{'bid1Price': bid, 'ask1Price': ask, 'lastPrice': last}]}}
    async def fake_signed_get(**kwargs):
        if kwargs.get('path','').endswith('fee-rate'):
            return {'result': {'list': [{'makerFeeRate': '0.0002', 'takerFeeRate': '0.00055'}]}}
        return {'result': {'list': [{'totalEquity': '10000', 'totalAvailableBalance': '10000', 'coin': [{'coin': 'USDT', 'availableToTrade': '10000'}]}]}}
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    monkeypatch.setattr(master_service, '_bybit_signed_get', fake_signed_get)


def test_bybit_sell_limit_auto_adjusts_take_profit_below_last(monkeypatch):
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example-webhook.test")
    monkeypatch.delenv("RENDER_CALCULATOR_BASE_URL", raising=False)
    _mock_bybit_quote_env(monkeypatch)
    body = json.loads(asyncio.run(master_service.calculator_quote({'asset':'crypto','account':'live','symbol':'PARTI','side':'sell','order_type':'limit','entry_price':'0.05313','risk_mode':'percent','risk_value':1,'stop_loss_ticks':37,'take_profit_ticks':94,'webhook':'yes'})).body.decode())
    assert float(body['target_price']) < float(body['last_price'])
    assert float(body['target_price']) < float(body['entry_price'])
    assert body['take_profit_adjusted'] is True
    wh = json.loads(body['webhook_payload_json'])
    assert wh['take_profit_price'] == body['target_price']


def test_bybit_buy_limit_auto_adjusts_take_profit_above_last(monkeypatch):
    _mock_bybit_quote_env(monkeypatch, tick='0.1', bid='99.9', ask='100.1', last='100')
    body = json.loads(asyncio.run(master_service.calculator_quote({'asset':'crypto','account':'live','symbol':'BTC','side':'buy','order_type':'limit','entry_price':'95','risk_mode':'percent','risk_value':1,'stop_loss_ticks':20,'take_profit_ticks':10})).body.decode())
    assert float(body['target_price']) > float(body['last_price'])
    assert float(body['target_price']) > float(body['entry_price'])
    assert body['take_profit_adjusted'] is True

def test_bybit_wallet_retcode_33004_maps_to_api_key_expired_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_signed_get(**_kwargs):
        raise ValueError("Bybit signed GET failed path=/v5/account/wallet-balance retCode=33004 retMsg=Your api key has expired. recv_window=15000")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY2"))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service._fetch_bybit_balance_usdt("demo"))
    d = exc.value.detail
    assert d["code"] == "BYBIT_API_KEY_EXPIRED" and d["account"] == "demo" and d["key_source"] == "KEY2"
    assert "BYBIT_API_KEY2/BYBIT_API_SECRET2" in d["message"]


def test_bybit_wallet_retcode_33004_maps_to_api_key_expired_live(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_signed_get(**_kwargs):
        raise ValueError("retCode=33004 retMsg=Your api key has expired")
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://bybit.test", "KEY1"))
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_signed_get)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service._fetch_bybit_balance_usdt("live"))
    d = exc.value.detail
    assert d["code"] == "BYBIT_API_KEY_EXPIRED" and d["account"] == "live" and d["key_source"] == "KEY1"
    assert "BYBIT_API_KEY1/BYBIT_API_SECRET1" in d["message"]


def test_bybit_expired_key_error_includes_key_source_without_secret() -> None:
    d = master_service._classify_bybit_api_key_expired("retCode=33004 retMsg=Your api key has expired", account="demo", key_source="LEGACY")
    assert d and d["key_source"] == "LEGACY"
    assert "bybit_api_secret2" in str(d.get("message", "")).lower()


def test_bybit_expired_key_error_prioritized_over_ticker_timeout() -> None:
    detail = master_service._calculator_quote_error_detail("BYBIT_API_KEY_EXPIRED", "x", dependency="bybit_wallet_balance", timings_ms={"bybit_ticker": {"status": "timeout"}})
    assert detail["code"] == "BYBIT_API_KEY_EXPIRED"


def test_demo_quote_context_save_failure_returns_structured_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k", "s", "https://bybit.test", "KEY2"))
    monkeypatch.setattr(master_service, "_upsert_bybit_demo_calc_context", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("disk locked")))
    monkeypatch.setattr(master_service, "_fetch_oanda_mid_prices_batch", lambda **_kwargs: asyncio.sleep(0, result={"AUD_USD": 1}))
    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_a, **_k: asyncio.sleep(0, result=("BTCUSDT", {"priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "999", "maxMktOrderQty": "999", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}, {"resolution_status": "exact"})))
    monkeypatch.setattr(master_service, "_fetch_bybit_ticker_cached", lambda *_a, **_k: asyncio.sleep(0, result={"payload": {"result": {"list": [{"bid1Price": "65000", "ask1Price": "65001", "lastPrice": "65000.5"}]}}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", lambda *_a, **_k: asyncio.sleep(0, result=({"available_usdt": "10000", "total_equity": "10000"}, {"wallet_cache_status": "hit", "wallet_cache_age_ms": 1})))

    payload = {"asset": "crypto", "account": "demo", "symbol": "BTCUSDT", "webhook": "no", "test": "yes", "timeframe": "1h", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 1111, "risk_reward": 2, "order_type": "market", "side": "buy"}
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote(payload))
    assert exc.value.status_code == 502
    detail = exc.value.detail
    assert detail["code"] == "BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED"
    assert detail["debug"]["pending_dependencies"] == []
    assert detail["debug"]["submitted_payload"]["account"] == "demo"
    assert detail["debug"]["resolved_symbol"] == "BTCUSDT"
    assert "bybit_wallet_balance" in detail["debug"]["upstream_timings_ms"]
    assert detail["debug"]["upstream_timings_ms"]["bybit_wallet_balance"]["status"] == "ok"

def test_fetch_bybit_ticker_cached_invalid_live_uses_stale_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"{master_service._bybit_base_key('https://api-demo.bybit.com')}:linear:BTCUSDT"
    valid_payload = {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99", "ask1Price": "101"}]}}
    master_service._BYBIT_TICKER_CACHE[key] = {"ts": master_service.time.time(), "payload": valid_payload, "source": "seed"}

    async def fake_get(*_args, **_kwargs):
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    out = asyncio.run(master_service._fetch_bybit_ticker_cached("https://api-demo.bybit.com", "linear", "BTCUSDT", max_age_s=0.0, allow_stale_s=45.0, timeout_s=0.1))
    assert out["cache_status"] == "stale_fallback"
    assert "invalid" in str(out.get("ticker_cache_error", "")).lower()
    assert master_service._BYBIT_TICKER_CACHE[key]["payload"] == valid_payload


def test_fetch_bybit_ticker_cached_invalid_live_uses_public_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_get(base_url, path, params, **_kwargs):
        calls.append(base_url)
        if "demo" in base_url:
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100", "bid1Price": "99", "ask1Price": "101"}]}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    out = asyncio.run(master_service._fetch_bybit_ticker_cached("https://api-demo.bybit.com", "linear", "BTCUSDT", max_age_s=0.0, allow_stale_s=0.0, timeout_s=0.1, public_fallback_base_url="https://api.bybit.com"))
    assert out["cache_status"] == "live_fallback"
    assert out["ticker_source"] == "public_market_fallback"
    assert any("api-demo" in c for c in calls) and any("api.bybit.com" in c for c in calls)


def test_calculator_quote_invalid_ticker_payload_without_fallback_fails_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k2", "s2", "https://api-demo.bybit.com", "KEY2"))

    async def fake_symbols(*_args, **_kwargs):
        return ["BTCUSDT"]

    async def fake_inst(*_args, **_kwargs):
        return {"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1000", "maxMktOrderQty": "1000", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}

    async def fake_ticker(*_args, **_kwargs):
        return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}

    async def fake_wallet(*_args, **_kwargs):
        return ({"available_usdt": master_service.Decimal("1000"), "total_equity": master_service.Decimal("1000")}, {"wallet_cache_status": "live"})

    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_args, **_kwargs: asyncio.sleep(0, result=("BTCUSDT", None, {})))
    monkeypatch.setattr(master_service, "_bybit_get_symbols_by_category_cached", fake_symbols)
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", fake_inst)
    async def fake_ticker_cached(*_args, **_kwargs):
        return {"payload": {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}, "cache_status": "live", "ticker_cache_error": "Bybit ticker payload invalid for BTCUSDT"}

    monkeypatch.setattr(master_service, "_fetch_bybit_ticker_cached", fake_ticker_cached)
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", fake_wallet)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("dependency") == "bybit_ticker"
    assert detail.get("code") in {"BYBIT_TICKER_INVALID", "BYBIT_TICKER_TIMEOUT"}
    assert detail.get("dependency") == "bybit_ticker"


def test_calculator_quote_invalid_ticker_payload_from_real_fetch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("demo", "k2", "s2", "https://api-demo.bybit.com", "KEY2"))

    async def fake_get(base_url, path, params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": []}}

    async def fake_wallet(*_args, **_kwargs):
        return ({"available_usdt": master_service.Decimal("1000"), "total_equity": master_service.Decimal("1000")}, {"wallet_cache_status": "live"})

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    monkeypatch.setattr(master_service, "_resolve_bybit_calculator_symbol_fast", lambda *_args, **_kwargs: asyncio.sleep(0, result=("BTCUSDT", None, {})))
    monkeypatch.setattr(master_service, "_bybit_get_instrument_info_cached", lambda *_args, **_kwargs: asyncio.sleep(0, result={"symbol": "BTCUSDT", "priceFilter": {"tickSize": "0.1"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1000", "maxMktOrderQty": "1000", "minNotionalValue": "1"}, "leverageFilter": {"maxLeverage": "100"}}))
    monkeypatch.setattr(master_service, "_fetch_bybit_balance_usdt_cached", fake_wallet)

    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_quote({"asset": "crypto", "account": "demo", "symbol": "BTC", "side": "buy", "order_type": "market", "risk_mode": "percent", "risk_value": 1, "stop_loss_ticks": 10, "take_profit_ticks": 20}))
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") in {"BYBIT_TICKER_INVALID", "INTERNAL_CALCULATOR_ERROR"}
    if detail.get("dependency") is not None:
        assert detail.get("dependency") == "bybit_ticker"


def test_specs_invalid_ticker_payload_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: {"base_url": "https://api-demo.bybit.com"})
    monkeypatch.setattr(master_service, "_bybit_lookup_symbol", lambda *_args, **_kwargs: asyncio.sleep(0, result={"_category": "linear", "symbol": "BTCUSDT", "baseCoin": "BTC"}))
    monkeypatch.setattr(master_service, "_bybit_avg_7d_turnover_usd_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(master_service, "_bybit_fetch_range_specs_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=({}, [])))

    async def fake_get(base_url, path, params, **_kwargs):
        if path == "/v5/market/tickers":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]}}
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_bybit_get_async", fake_get)
    asyncio.run(master_service._bybit_resolve_and_fetch_specs("BTCUSDT", include_btc_reference=False))
    key = f"{master_service._bybit_base_key('https://api-demo.bybit.com')}:linear:BTCUSDT"
    assert key not in master_service._BYBIT_TICKER_CACHE


def test_no_calculator_ticker_error_strings_in_non_calculator_blocks() -> None:
    src = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    checks = ["BYBIT_TICKER_INVALID", "ticker payload invalid", "submitted_debug", "quote_started"]
    blocks = [
        ("async def script_view_page", '@app.get("/bybit-history")'),
        ("async def trade_chart_page", '@app.get("/api/logs")'),
        ("async def api_logs_root", '@app.get("/api/logs/{script_name:path}")'),
        ("async def read_script_results", '@app.post("/api/oanda-history/export")'),
    ]
    for start, end in blocks:
        i = src.index(start)
        j = src.find(end, i)
        if j == -1:
            j = len(src)
        block = src[i:j]
        for token in checks:
            assert token not in block

def test_calculator_submit_rejects_invalid_setup_422() -> None:
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.calculator_submit({"asset": "crypto", "setup": "bad-setup"}))
    assert exc.value.status_code == 422


def test_calculator_submit_passes_normalized_setup_to_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    async def fake_place(payload, request_id):
        seen["setup"] = payload.get("setup")
        seen["pattern"] = payload.get("pattern")
        seen["ema"] = payload.get("ema")
        seen["aths_atls"] = payload.get("aths_atls")
        seen["round_number"] = payload.get("round_number")
        return {"journal_context_saved": True}
    monkeypatch.setattr(master_service, "_place_bybit_order", fake_place)
    resp = asyncio.run(master_service.calculator_submit({
        "asset":"crypto","account":"demo","symbol":"BTCUSDT","side":"buy","order_type":"market",
        "setup":"news scalp","pattern":"channel","ema":"yes",
        "aths_atls":"ath","round_number":"no",
    }))
    body = json.loads(resp.body.decode("utf-8"))
    assert body["ok"] is True
    assert seen["setup"] == "News Scalp"
    assert seen["pattern"] == "channel"
    assert seen["ema"] == "Yes"
    assert seen["aths_atls"] == "All-Time High"
    assert seen["round_number"] == "No"


def test_trade_context_normalizes_and_backfills_quality_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: [])
    saved = {}
    monkeypatch.setattr(master_service, "_save_trade_contexts", lambda items: saved.setdefault("items", items))
    context = master_service._upsert_trade_context({
        "calculation_context_id": "criteria-1",
        "ema": "y",
        "aths_atls": "all time low",
        "round_number": False,
    })
    assert context["ema"] == "Yes"
    assert context["aths_atls"] == "All-Time Low"
    assert context["round_number"] == "No"
    merged = master_service._merge_bybit_demo_calc_context_into_row({"id": "row-1"}, context)
    assert merged["ema"] == "Yes"
    assert merged["aths_atls"] == "All-Time Low"
    assert merged["round_number"] == "No"
