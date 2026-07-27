import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_open_orders", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


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


def test_scripts_listing_excludes_open_orders_button() -> None:
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    row = next((item for item in payload if item.get("name") == "open-orders"), None)
    assert row is None


def test_render_profile_hides_open_orders_and_blocks_routes() -> None:
    render_service = _load_master_service("render_master_service_open_orders_render", "render")
    payload = json.loads(asyncio.run(render_service.list_scripts()).body.decode("utf-8"))
    names = {str(item.get("name")) for item in payload}
    assert "open-orders" not in names
    merged = asyncio.run(render_service.merged_open_orders_page())
    api = asyncio.run(render_service.list_open_orders())
    version = asyncio.run(render_service.open_orders_version())
    assert merged.status_code == 410
    assert api.status_code == 410
    assert version.status_code == 410


def test_merged_open_orders_route_returns_html() -> None:
    response = asyncio.run(master_service.merged_open_orders_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Open Orders and Positions" in html
    assert 'id="refresh-btn"' in html
    assert 'id="open-orders-status"' in html
    assert 'id="open-orders-errors"' in html
    assert 'id="open-orders-empty"' in html
    assert "No open orders or positions." in html
    assert "No open orders, positions, or pending webhooks." not in html
    assert 'id="open-orders-table"' in html
    assert 'id="webhook-attempts-table"' not in html
    assert "<th>Test</th>" in html
    assert "/static/open_orders.js?v=" in html


def test_open_orders_headers_and_rendered_cells_have_exact_alignment() -> None:
    html = asyncio.run(master_service.merged_open_orders_page()).body.decode("utf-8")
    header_row = re.search(r'<table id="open-orders-table">.*?<thead>\s*<tr>(.*?)</tr>', html, re.DOTALL)
    assert header_row
    headers = re.findall(r"<th[^>]*>(.*?)</th>", header_row.group(1), re.DOTALL)
    assert headers == [
        "Broker",
        "Account",
        "Category",
        "Instrument",
        "Timeframe",
        "Test",
        "Type",
        "Side",
        "Size",
        "Entry / Order",
        "Current / Trigger",
        "Stop Loss",
        "Take Profit",
        "Leverage / Margin",
        "Opened",
        "Status",
        "Action",
    ]

    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    rendered_values = re.search(
        r"\[(item\.broker,resolveAccountLabel\(item\).*?,item\.status)\]\.forEach",
        js,
    )
    assert rendered_values
    assert rendered_values.group(1).split(",") == [
        "item.broker",
        "resolveAccountLabel(item)",
        "item.category",
        "item.instrument",
        "item.timeframe",
        "item.is_test_trade",
        "item.type",
        "item.side",
        "item.size",
        "item.entry_price||item.order_price",
        "item.current_price",
        "item.stop_loss",
        "item.take_profit",
        "item.leverage",
        "formatTimestamp(item.opened_at)",
        "item.status",
    ]
    assert "renderActionCell(item,actionTd,{allowAction:true}); row.appendChild(actionTd); tbody.appendChild(row);" in js
    assert len(headers) == len(rendered_values.group(1).split(",")) + 1 == 17
    assert headers[-1] == "Action"


def test_oanda_open_item_values_align_with_open_orders_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_oanda_json(*, endpoint: str, **_kwargs):
        if endpoint.endswith("/openTrades"):
            return {
                "trades": [
                    {
                        "id": "trade-1",
                        "instrument": "EUR_USD",
                        "currentUnits": "1000",
                        "price": "1.0800",
                        "currentPrice": "1.0810",
                        "openTime": "2026-07-25T01:02:03Z",
                        "state": "OPEN",
                    }
                ]
            }
        return {"orders": []}

    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_oanda_json)
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_open_item",
        lambda _item: {"timeframe": "15 minutes", "is_test_trade": True},
    )

    payload = asyncio.run(
        master_service._collect_oanda_open_items(
            base_url="https://api-fxpractice.oanda.test",
            account_id="demo-account",
            api_key="token",
            account_context="demo",
        )
    )

    item = payload["items"][0]
    assert item["category"] == "forex"
    assert item["instrument"] == "EUR_USD"
    assert item["timeframe"] == "15 minutes"
    assert item["is_test_trade"] == "Yes"


def test_open_orders_js_uses_version_polling_and_force_query_refresh() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "setInterval(" in js
    assert "visibilitychange" in js
    assert "POLL_MS" in js
    assert "/api/open-orders/version" in js
    assert "/api/open-orders?force=1" in js
    assert "/api/calculator/webhook-attempts?limit=20" not in js
    assert "Unknown source error" not in js
    assert "const formattedErrors=formatSourceErrors(errors);" in js
    assert "retCode=${retCode}" in js
    assert "retMsg=${retMsg}" in js


def test_bybit_signed_get_retries_empty_message_timeout_with_safe_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []
    base_url = "https://api.bybit.test"
    api_key = "test-api-key-that-must-not-appear"
    api_secret = "test-api-secret-that-must-not-appear"
    monkeypatch.setattr(master_service, "BYBIT_SIGNED_REQUEST_MAX_RETRIES", 2)
    monkeypatch.setitem(
        master_service._BYBIT_TIME_OFFSET_CACHE,
        base_url,
        {
            "synced_at": int(master_service.time.time() * 1000),
            "offset_ms": 0,
            "rtt_ms": 0,
        },
    )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    class TimeoutClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def get(self, *_args, **_kwargs):
            calls["count"] += 1
            raise master_service.httpx.ReadTimeout("")

    monkeypatch.setattr(master_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", TimeoutClient)

    with pytest.raises(master_service.BybitSignedGETError) as exc_info:
        asyncio.run(
            master_service._bybit_signed_get(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                path="/v5/position/list",
                params={"category": "linear", "settleCoin": "USDT"},
                timeout_s=4.0,
            )
        )

    message = str(exc_info.value)
    assert calls["count"] == 2
    assert sleeps
    assert "timeout" in message.lower()
    assert "path=/v5/position/list" in message
    assert "ReadTimeout" in message
    assert api_key not in message
    assert api_secret not in message
    assert base_url not in message


def test_bybit_signed_get_http_error_keeps_safe_status_and_api_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://api.bybit.test"
    api_key = "http-test-key"
    api_secret = "http-test-secret"
    monkeypatch.setattr(master_service, "BYBIT_SIGNED_REQUEST_MAX_RETRIES", 1)
    monkeypatch.setitem(
        master_service._BYBIT_TIME_OFFSET_CACHE,
        base_url,
        {
            "synced_at": int(master_service.time.time() * 1000),
            "offset_ms": 0,
            "rtt_ms": 0,
        },
    )

    class ErrorResponse:
        status_code = 503
        content = b"upstream unavailable"

        @property
        def text(self) -> str:
            return json.dumps(self.json())

        def json(self):
            return {
                "retCode": 10000,
                "retMsg": (
                    f"Server unavailable api_key={api_key} secret={api_secret} "
                    "https://api.bybit.test/v5/position/list?signature=hidden"
                ),
            }

    class ErrorClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def get(self, *_args, **_kwargs):
            return ErrorResponse()

    monkeypatch.setattr(master_service.httpx, "AsyncClient", ErrorClient)

    with pytest.raises(master_service.BybitSignedGETError) as exc_info:
        asyncio.run(
            master_service._bybit_signed_get(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                path="/v5/position/list",
                params={"category": "linear", "settleCoin": "USDT"},
            )
        )

    exc = exc_info.value
    message = str(exc)
    assert exc.http_status == 503
    assert exc.ret_code == 10000
    assert "http_status=503" in message
    assert "retCode=10000" in message
    assert "retMsg=Server unavailable" in message
    assert api_key not in message
    assert api_secret not in message
    assert "signature=hidden" not in message
    assert base_url not in message


def test_bybit_diagnostic_sanitizer_redacts_quoted_secret_fields() -> None:
    raw = (
        '{"signature":"quoted-signature", "api_key": "quoted-key", '
        "'api_secret': 'quoted secret value', "
        '"x-bapi-sign":"quoted-header-signature"}'
    )

    sanitized = master_service._safe_bybit_diagnostic_text(raw)

    assert sanitized.count("[redacted]") == 4
    assert "quoted-signature" not in sanitized
    assert "quoted-key" not in sanitized
    assert "quoted secret value" not in sanitized
    assert "quoted-header-signature" not in sanitized


def test_bybit_position_and_order_errors_identify_source_and_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty_timeout(**_kwargs):
        raise master_service.httpx.ReadTimeout("")

    monkeypatch.setattr(master_service, "_bybit_signed_get", empty_timeout)

    _positions, position_errors = asyncio.run(
        master_service._fetch_bybit_positions_for_category(
            base_url="https://api.bybit.test",
            api_key="key",
            api_secret="secret",
            category="linear",
            account_context="live",
        )
    )
    _orders, order_errors = asyncio.run(
        master_service._fetch_bybit_orders_for_category(
            base_url="https://api.bybit.test",
            api_key="key",
            api_secret="secret",
            category="linear",
            account_context="live",
        )
    )

    assert {entry["settlement_coin"] for entry in position_errors} == {"USDT", "USDC"}
    assert {entry["settlement_coin"] for entry in order_errors} == {"USDT", "USDC"}
    assert all(entry["source_type"] == "positions" for entry in position_errors)
    assert all(entry["endpoint"] == "/v5/position/list" for entry in position_errors)
    assert all(entry["source_type"] == "orders" for entry in order_errors)
    assert all(entry["endpoint"] == "/v5/order/realtime" for entry in order_errors)
    for entry in [*position_errors, *order_errors]:
        assert entry["account"] == "live"
        assert entry["category"] == "linear"
        assert "timeout" in entry["message"].lower()
        assert entry["message"].strip()


def test_collect_bybit_open_items_preserves_partial_position_and_order_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def partial_signed_get(*, path: str, params: dict[str, str], **_kwargs):
        if params.get("category") == "linear" and params.get("settleCoin") == "USDC":
            if path == "/v5/position/list":
                raise master_service.httpx.ReadTimeout("")
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "ETHUSDC",
                            "orderId": "order-usdc",
                            "orderStatus": "New",
                            "side": "Buy",
                            "qty": "2",
                        }
                    ]
                }
            }
        if params.get("category") == "linear" and params.get("settleCoin") == "USDT":
            if path == "/v5/order/realtime":
                raise master_service.httpx.ConnectError("")
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "positionId": "position-usdt",
                            "side": "Buy",
                            "size": "1",
                        }
                    ]
                }
            }
        return {"result": {"list": []}}

    monkeypatch.setattr(master_service, "_bybit_signed_get", partial_signed_get)
    monkeypatch.setattr(master_service, "_lookup_trade_context_for_open_item", lambda _item: None)

    payload = asyncio.run(
        master_service._collect_bybit_open_items(
            base_url="https://api.bybit.test",
            api_key="key",
            api_secret="secret",
            account_context="live",
        )
    )

    assert {(row["type"], row["instrument"]) for row in payload["items"]} == {
        ("Position", "BTCUSDT"),
        ("Order", "ETHUSDC"),
    }
    assert {(entry["source_type"], entry["settlement_coin"]) for entry in payload["errors"]} == {
        ("positions", "USDC"),
        ("orders", "USDT"),
    }


def test_open_orders_browser_renders_meaningful_source_errors_without_secrets() -> None:
    node = shutil.which("node")
    assert node, "node is required for browser rendering regression"
    js_path = ROOT / "render" / "static" / "open_orders.js"
    source_errors = [
        {
            "broker": "Bybit",
            "account": "live",
            "category": "linear",
            "source_type": "positions",
            "settlement_coin": "USDT",
            "endpoint": "https://api.bybit.test/v5/position/list?api_key=should-not-render&signature=hidden",
            "error_type": "ReadTimeout",
            "message": "",
        },
        {
            "broker": "Bybit",
            "account": "demo",
            "category": "inverse",
            "source_type": "orders",
            "settlement_coin": "n/a",
            "endpoint": "/v5/order/realtime",
            "http_status": 403,
            "retCode": 10004,
            "retMsg": "signature error",
            "message": "Bybit API request failed:",
        },
    ]
    harness = f"""
const fs = require('fs');
const errorsList = {{children: [], innerHTML: '', appendChild(node) {{ this.children.push(node); }}}};
const errorsBox = {{style: {{}}, querySelector() {{ return errorsList; }}}};
const tbody = {{innerHTML: '', children: [], appendChild(node) {{ this.children.push(node); }}}};
const table = {{querySelector() {{ return tbody; }}}};
const refreshButton = {{addEventListener() {{}}}};
const statusBadge = {{textContent: ''}};
const emptyState = {{style: {{}}}};
const elements = {{
  'refresh-btn': refreshButton,
  'open-orders-status': statusBadge,
  'open-orders-table': table,
  'open-orders-empty': emptyState,
  'open-orders-errors': errorsBox,
}};
global.window = {{}};
global.document = {{
  hidden: true,
  getElementById(id) {{ return elements[id] || null; }},
  addEventListener() {{}},
  createElement(tag) {{
    return {{
      tagName: tag,
      children: [],
      style: {{}},
      textContent: '',
      appendChild(node) {{ this.children.push(node); }},
      addEventListener() {{}},
    }};
  }},
}};
global.fetch = async () => ({{
  ok: true,
  status: 200,
  statusText: 'OK',
  text: async () => JSON.stringify({{items: [], errors: {json.dumps(source_errors)}}}),
}});
eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
setTimeout(() => {{
  console.log(JSON.stringify(errorsList.children.map((node) => node.textContent)));
}}, 25);
"""
    result = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rendered = json.loads(result.stdout.strip().splitlines()[-1])

    assert len(rendered) == 2
    assert "Bybit live linear positions settleCoin=USDT /v5/position/list" in rendered[0]
    assert "ReadTimeout reported without diagnostic text" in rendered[0]
    assert "Bybit demo inverse orders settleCoin=n/a /v5/order/realtime" in rendered[1]
    assert "HTTP 403" in rendered[1]
    assert "retCode=10004" in rendered[1]
    assert "retMsg=signature error" in rendered[1]
    assert "should-not-render" not in " ".join(rendered)
    assert "signature=hidden" not in " ".join(rendered)
    assert all(not line.rstrip().endswith(":") for line in rendered)


def test_open_orders_browser_sanitizes_unstructured_fetch_failures_and_surfaces_reason() -> None:
    node = shutil.which("node")
    assert node, "node is required for browser rendering regression"
    js_path = ROOT / "render" / "static" / "open_orders.js"
    harness = f"""
const fs = require('fs');
let mode = 'body';
const refreshButton = {{
  handler: null,
  addEventListener(event, handler) {{ if (event === 'click') this.handler = handler; }},
}};
const statusBadge = {{textContent: ''}};
const tbody = {{innerHTML: '', appendChild() {{}}}};
const table = {{querySelector() {{ return tbody; }}}};
const errorsList = {{innerHTML: '', appendChild() {{}}}};
const errorsBox = {{style: {{}}, querySelector() {{ return errorsList; }}}};
const elements = {{
  'refresh-btn': refreshButton,
  'open-orders-status': statusBadge,
  'open-orders-table': table,
  'open-orders-empty': {{style: {{}}}},
  'open-orders-errors': errorsBox,
}};
global.window = {{}};
global.document = {{
  hidden: true,
  getElementById(id) {{ return elements[id] || null; }},
  addEventListener() {{}},
  createElement() {{ return {{textContent: '', appendChild() {{}}, addEventListener() {{}}}}; }},
}};
global.fetch = async () => mode === 'body' ? ({{
  ok: false,
  status: 502,
  statusText: 'Bad Gateway',
  text: async () => JSON.stringify({{
    detail: 'upstream api_key=body-secret https://api.bybit.test/v5/position/list?signature=body-signature',
  }}),
}}) : ({{
  ok: false,
  status: 504,
  statusText: 'Gateway Timeout api_secret=status-secret https://api.bybit.test/v5/order/realtime?signature=status-signature',
  text: async () => '',
}});
eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
setTimeout(async () => {{
  const bodyBadge = statusBadge.textContent;
  mode = 'status';
  await refreshButton.handler();
  console.log(JSON.stringify([bodyBadge, statusBadge.textContent]));
}}, 25);
"""
    result = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    body_badge, status_badge = json.loads(result.stdout.strip().splitlines()[-1])

    assert body_badge.startswith(
        "Stale (refresh failed: GET /api/open-orders failed: HTTP 502"
    )
    assert "upstream" in body_badge
    assert status_badge.startswith(
        "Stale (refresh failed: GET /api/open-orders failed: HTTP 504"
    )
    assert "Gateway Timeout" in status_badge
    combined = f"{body_badge} {status_badge}"
    assert "[redacted]" in combined
    assert "[redacted URL]" in combined
    assert "body-secret" not in combined
    assert "body-signature" not in combined
    assert "status-secret" not in combined
    assert "status-signature" not in combined
    assert "force=1" not in combined
    assert body_badge != "Stale (refresh failed)"
    assert status_badge != "Stale (refresh failed)"


def test_open_orders_browser_sanitizes_action_error_details() -> None:
    node = shutil.which("node")
    assert node, "node is required for browser rendering regression"
    js_path = ROOT / "render" / "static" / "open_orders.js"
    harness = f"""
const fs = require('fs');
class Element {{
  constructor() {{
    this.children = [];
    this.handlers = {{}};
    this.style = {{}};
    this.textContent = '';
    this.innerHTML = '';
    this.disabled = false;
  }}
  appendChild(node) {{ this.children.push(node); return node; }}
  addEventListener(event, handler) {{ this.handlers[event] = handler; }}
  querySelector() {{ return null; }}
}}
const tbody = new Element();
const table = new Element();
table.querySelector = () => tbody;
const refreshButton = new Element();
const statusBadge = new Element();
const errorsBox = new Element();
const errorsList = new Element();
errorsBox.querySelector = () => errorsList;
const elements = {{
  'refresh-btn': refreshButton,
  'open-orders-status': statusBadge,
  'open-orders-table': table,
  'open-orders-empty': new Element(),
  'open-orders-errors': errorsBox,
}};
global.window = {{}};
global.document = {{
  hidden: true,
  getElementById(id) {{ return elements[id] || null; }},
  addEventListener() {{}},
  createElement() {{ return new Element(); }},
}};
global.fetch = async (url) => {{
  if (String(url).includes('/api/open-orders/close')) {{
    return {{
      ok: false,
      status: 500,
      statusText: 'Server Error signature=status-signature',
      text: async () => JSON.stringify({{
        detail: 'api_key=action-key api_secret="action secret with spaces" signature=action-signature https://api.bybit.test/v5/order?signature=url-signature',
      }}),
    }};
  }}
  return {{
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify({{
      items: [{{
        broker: 'Bybit',
        account: 'live',
        category: 'linear',
        instrument: 'BTCUSDT',
        id: 'position-1',
        type: 'position',
        side: 'Buy',
        size: '1',
        status: 'Open',
      }}],
      errors: [],
    }}),
  }};
}};
eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
setTimeout(async () => {{
  const row = tbody.children[0];
  const actionCell = row.children[row.children.length - 1];
  const button = actionCell.children[0];
  await button.handlers.click();
  console.log(JSON.stringify(statusBadge.textContent));
}}, 25);
"""
    result = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    badge = json.loads(result.stdout.strip().splitlines()[-1])

    assert "[redacted]" in badge
    assert "[redacted URL]" in badge
    assert "action-key" not in badge
    assert "action secret with spaces" not in badge
    assert "action-signature" not in badge
    assert "url-signature" not in badge
    assert "status-signature" not in badge


def test_open_orders_version_endpoint_returns_cache_version() -> None:
    master_service._OPEN_ORDERS_CACHE["version"] = 7
    response = asyncio.run(master_service.open_orders_version())
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["version"] == 7
    assert "updated_at" in payload


def test_open_orders_js_treats_webhook_as_cancelable() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert ("type === 'webhook'" in js) or ("type==='webhook'" in js)
    assert ("type === 'order' || type === 'webhook'" in js) or ("type==='order'||type==='webhook'" in js)
    assert "item.is_test_trade" in js
    assert ("type==='order'||type==='webhook'" in js) or ("actionLabelFor" in js)
    assert "Failed to load webhook attempts:" not in js


def test_close_open_order_invalidates_cache_for_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"invalidate": 0, "mark": 0, "delete": 0, "backup": 0}

    monkeypatch.setattr(master_service, "_mark_trade_context_closed_or_cancelled", lambda **_kwargs: calls.__setitem__("mark", calls["mark"] + 1))
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _item_id: calls.__setitem__("delete", calls["delete"] + 1) or True)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: calls.__setitem__("invalidate", calls["invalidate"] + 1))
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: calls.__setitem__("backup", calls["backup"] + 1))

    response = asyncio.run(
        master_service.close_open_order(
            {
                "broker": "WEBHOOK",
                "account": "demo",
                "category": "forex",
                "instrument": "EUR_USD",
                "type": "webhook",
                "id": "wh_123",
            }
        )
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["action"] == "cancel"
    assert payload["action_requested"] is True
    assert calls["mark"] == 1
    assert calls["delete"] == 1
    assert calls["invalidate"] == 1
    assert calls["backup"] == 1


def test_close_open_order_passes_row_account_id_for_oanda(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"account_id": None}

    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _account: {"account_id": "PRIMARY", "token": "T", "base_url": "https://example.test"})

    async def fake_cancel_oanda_order(*, cfg, order_id, mode, account_id=None):
        captured["account_id"] = account_id

    monkeypatch.setattr(master_service, "_cancel_oanda_order", fake_cancel_oanda_order)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)

    response = asyncio.run(
        master_service.close_open_order(
            {
                "broker": "oanda",
                "account": "live",
                "category": "forex",
                "instrument": "EUR_USD",
                "type": "order",
                "id": "123",
                "account_id": "ROW_ACCOUNT",
            }
        )
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert captured["account_id"] == "ROW_ACCOUNT"


def test_list_open_orders_force_bypasses_cache() -> None:
    master_service._OPEN_ORDERS_CACHE["payload"] = {
        "items": [{"id": "cached"}],
        "errors": [],
        "stale": False,
        "updated_at": "cached",
    }
    master_service._OPEN_ORDERS_CACHE["expires_at"] = 32503680000.0
    master_service._OPEN_ORDERS_CACHE["last_success_at"] = "cached-last"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _account: {"account_id": "A1", "token": "T", "base_url": "https://example.test"})
    async def fake_get_cached_oanda_accounts(**_kwargs):
        return []
    async def fake_collect_oanda_open_items(**_kwargs):
        return {"items": [{"id": "fresh-oanda"}], "errors": []}
    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", fake_get_cached_oanda_accounts)
    monkeypatch.setattr(master_service, "_collect_oanda_open_items", fake_collect_oanda_open_items)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _account: ("live", "", "", "", ""))
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(master_service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: type("S", (), {"is_running": False})())

    try:
        response = asyncio.run(master_service.list_open_orders(force=True))
        payload = json.loads(response.body.decode("utf-8"))
        assert payload.get("items") is not None
        assert payload["items"] != [{"id": "cached"}]
        assert "errors" in payload
    finally:
        monkeypatch.undo()


def test_open_orders_hides_failed_pending_webhooks_from_open_items() -> None:
    pending_items = [
        {"id": "wh1", "status": "WAITING", "enabled": True},
        {"id": "wh2", "status": "BYBIT_REJECTED", "enabled": True},
        {"id": "wh3", "status": "FAILED_BEFORE_SUBMIT", "enabled": True},
        {"id": "wh4", "status": "CONSUMED", "enabled": True},
    ]
    filtered, changed = master_service._clean_pending_webhooks_for_open_items(pending_items, [])
    statuses = {row["id"]: row["status"] for row in filtered}
    assert changed is True
    assert statuses["wh1"] == "WAITING"
    assert "wh2" not in statuses
    assert "wh3" not in statuses
    assert "wh4" not in statuses


def test_open_orders_prunes_stale_duplicate_pending_webhooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        master_service,
        "_load_trade_contexts",
        lambda: [{"pending_webhook_id": "stale-context", "status": "CLOSED"}],
    )
    base = {
        "status": "WAITING",
        "enabled": True,
        "account": "demo",
        "category": "linear",
        "instrument": "BTCUSDT",
        "side": "Buy",
        "size": "1",
        "order_type": "Limit",
        "order_price": "100",
    }
    pending_items = [
        {**base, "id": "old-duplicate", "created_at": "2026-01-01T00:00:00Z"},
        {**base, "id": "new-duplicate", "created_at": "2026-01-01T00:01:00Z"},
        {**base, "id": "stale-context", "created_at": "2026-01-01T00:02:00Z"},
        {**base, "id": "orphaned-context", "calculation_context_id": "missing-ctx", "created_at": "2026-01-01T00:03:00Z"},
        {**base, "id": "consumed-waiting", "consumed_at": "2026-01-01T00:04:00Z"},
        {**base, "id": "rejected", "status": "REJECTED"},
        {**base, "created_at": "2026-01-01T00:05:00Z"},
        {**base, "created_at": "2026-01-01T00:06:00Z"},
    ]
    diagnostics: dict[str, int] = {}

    filtered, changed = master_service._clean_pending_webhooks_for_open_items(
        pending_items,
        [],
        diagnostics=diagnostics,
    )

    assert changed is True
    assert [item.get("id") for item in filtered if item.get("id")] == ["old-duplicate", "new-duplicate"]
    idless = [item for item in filtered if not item.get("id")]
    assert len(idless) == 1
    assert idless[0]["created_at"] == "2026-01-01T00:06:00Z"
    assert diagnostics["duplicate_fingerprint_pruned"] == 1
    assert diagnostics["stale_context_pruned"] == 2
    assert diagnostics["consumed_waiting_pruned"] == 1
    assert diagnostics["terminal_pruned"] == 1


def test_open_orders_api_excludes_pending_registry_rows_but_keeps_broker_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    pending_items = [
        {
            "id": f"pending-{idx}",
            "status": "WAITING",
            "enabled": True,
            "broker": "WEBHOOK",
            "type": "webhook",
            "account": "demo",
            "category": "linear",
            "instrument": "BTCUSDT",
            "side": "Buy",
            "size": "1",
            "order_type": "Limit",
            "order_price": "100",
            "timeframe": "1H",
            "calculation_context_id": f"ctx-{idx}",
            "created_at": f"2026-07-16T00:{idx:02d}:00Z",
        }
        for idx in range(9)
    ]
    contexts = [
        {
            "pending_webhook_id": item["id"],
            "calculation_context_id": item["calculation_context_id"],
            "status": "ACTIVE",
        }
        for item in pending_items
    ]
    saved_pending: list[list[dict[str, object]]] = []
    bybit_items: list[dict[str, object]] = []

    master_service._OPEN_ORDERS_CACHE["payload"] = None
    master_service._OPEN_ORDERS_CACHE["expires_at"] = 0.0
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda account: {"account_id": f"OANDA-{account}", "token": "T", "base_url": "https://example.test"})

    async def fake_get_cached_oanda_accounts(**_kwargs):
        return []

    async def fake_collect_oanda_open_items(**_kwargs):
        return {"items": [], "errors": []}

    async def fake_collect_bybit_open_items(**kwargs):
        return {"items": list(bybit_items) if kwargs.get("account_context") == "live" else [], "errors": []}

    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", fake_get_cached_oanda_accounts)
    monkeypatch.setattr(master_service, "_collect_oanda_open_items", fake_collect_oanda_open_items)
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _account: ("live", "key", "secret", "https://example.test", "test"))
    monkeypatch.setattr(master_service, "_collect_bybit_open_items", fake_collect_bybit_open_items)
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: list(pending_items))
    monkeypatch.setattr(master_service, "_save_pending_webhooks", lambda items: saved_pending.append(list(items)))
    monkeypatch.setattr(master_service, "_load_trade_contexts", lambda: list(contexts))
    monkeypatch.setattr(master_service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: type("S", (), {"is_running": False})())

    payload = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))
    assert payload["items"] == []
    assert payload["errors"] == []
    assert payload["source_counts"]["active_pending_webhooks"] == 9
    assert payload["source_counts"]["pending_reconciliation"]["active_records"] == 9
    assert saved_pending == []

    bybit_items[:] = [
        {
            "broker": "Bybit",
            "account": "live",
            "category": "linear",
            "instrument": "BTCUSDT",
            "type": "order",
            "side": "Buy",
            "size": "1",
            "id": "broker-order-1",
            "status": "New",
        }
    ]
    payload = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))
    assert [item["id"] for item in payload["items"]] == ["broker-order-1"]
    assert payload["items"][0]["broker"] == "Bybit"
    assert all(str(item.get("broker")).upper() != "WEBHOOK" for item in payload["items"])


def test_open_orders_api_reports_pending_reconciliation_source_counts() -> None:
    source = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    assert '"source_counts": source_counts' in source
    assert '"duplicates_or_stale_pending_pruned"' in source
    assert '"pending_reconciliation"' in source


def test_open_orders_js_renders_actual_broker_value() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "[item.broker,resolveAccountLabel(item)" in js
    assert "['broker',resolveAccountLabel(item)" not in js


def test_open_orders_attempt_fetch_path_is_removed() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "Failed to load webhook attempts:" not in js
    assert "renderWebhookAttempts" not in js
    assert "attemptErr" not in js


def test_open_orders_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(ROOT / "render" / "static" / "open_orders.js")], check=True)


def test_open_orders_js_has_pending_registry_and_verify_polling() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "pendingManualActions" in js
    assert "/api/open-orders/verify-action" in js
    assert "await refresh()" not in js.split("const postClose", 1)[1].split("const renderActionCell", 1)[0]
    assert "detail.message" in js or "JSON.stringify(detail)" in js


def test_close_open_order_returns_response_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://example.test", ""))
    async def fake_close(**_kwargs):
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "1", "orderLinkId": "abc"}}
    monkeypatch.setattr(master_service, "_close_bybit_position_market", fake_close)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: None)
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)
    response = asyncio.run(master_service.close_open_order({"broker":"bybit","account":"live","category":"linear","instrument":"BTCUSDT","type":"position","id":"pos1","side":"Buy","size":"1"}))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True and payload["action"] == "close" and payload["action_requested"] is True
    assert payload["response_summary"]["retCode"] == 0


def test_verify_action_oanda_trade_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"account_id":"A","token":"T","base_url":"https://example.test"})
    async def fake_fetch(**kwargs):
        return {"trades": []} if "openTrades" in kwargs["endpoint"] else {"orders": []}
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch)
    payload = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"oanda","account":"live","type":"trade","id":"t1","account_id":"A"})).body.decode("utf-8"))
    assert payload["verified"] is True and payload["still_open"] is False


def test_verify_action_oanda_trade_still_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "_get_oanda_config", lambda _a: {"account_id":"A","token":"T","base_url":"https://example.test"})
    async def fake_fetch(**kwargs):
        return {"trades": [{"id":"t1"}]}
    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch)
    payload = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"oanda","account":"live","type":"trade","id":"t1","account_id":"A"})).body.decode("utf-8"))
    assert payload["verified"] is False and payload["still_open"] is True


def test_verify_action_bybit_position_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda _a: ("live", "k", "s", "https://example.test", ""))
    async def closed_positions(**_kwargs): return ([], [])
    monkeypatch.setattr(master_service, "_fetch_bybit_positions_for_category", closed_positions)
    payload = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"bybit","account":"live","type":"position","id":"p1","instrument":"BTCUSDT","category":"linear"})).body.decode("utf-8"))
    assert payload["verified"] is True
    async def open_positions(**_kwargs): return ([{"symbol":"BTCUSDT","side":"Buy","size":"1"}], [])
    monkeypatch.setattr(master_service, "_fetch_bybit_positions_for_category", open_positions)
    payload2 = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"bybit","account":"live","type":"position","id":"p1","instrument":"BTCUSDT","category":"linear","side":"Buy"})).body.decode("utf-8"))
    assert payload2["still_open"] is True
    async def fake_get(**_kwargs): return {"result":{"list":[{"orderStatus":"Filled"}]}}
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_get)
    payload3 = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"bybit","account":"live","type":"order","id":"o1","instrument":"BTCUSDT","category":"linear"})).body.decode("utf-8"))
    assert payload3["verified"] is True
    async def fake_get_open(**_kwargs): return {"result":{"list":[{"orderStatus":"New"}]}}
    monkeypatch.setattr(master_service, "_bybit_signed_get", fake_get_open)
    payload4 = json.loads(asyncio.run(master_service.verify_open_order_action({"broker":"bybit","account":"live","type":"order","id":"o1","instrument":"BTCUSDT","category":"linear"})).body.decode("utf-8"))
    assert payload4["still_open"] is True


def test_open_orders_page_removes_webhook_diagnostic_ui() -> None:
    html = asyncio.run(master_service.merged_open_orders_page()).body.decode("utf-8")
    assert 'id="pending-webhook-id-input"' not in html
    assert 'id="webhook-diagnostic-card"' not in html


def test_open_orders_js_does_not_fetch_webhook_diagnostic() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "pending_webhook_id" not in js
    assert "/api/calculator/webhook-diagnostic/" not in js
