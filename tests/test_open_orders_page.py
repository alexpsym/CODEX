import asyncio
import importlib.util
import json
import os
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


def test_scripts_listing_includes_open_orders_button() -> None:
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    row = next((item for item in payload if item.get("name") == "open-orders"), None)
    assert row is not None
    assert row.get("open_url") == "/merged/open-orders"
    assert row.get("dashboard_main_view") is True


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
    assert 'id="open-orders-table"' in html
    assert 'id="webhook-attempts-table"' in html
    assert "<th>Test</th>" in html
    assert "/static/open_orders.js?v=" in html


def test_open_orders_js_uses_version_polling_and_force_query_refresh() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "setInterval(" in js
    assert "visibilitychange" in js
    assert "POLL_MS" in js
    assert "/api/open-orders/version" in js
    assert "/api/open-orders?force=1" in js
    assert "/api/calculator/webhook-attempts?limit=20" in js
    assert "Unknown source error" not in js
    assert "const formattedErrors=formatSourceErrors(errors);" in js
    assert "retCode=${retCode}" in js
    assert "retMsg=${retMsg}" in js


def test_open_orders_version_endpoint_returns_cache_version() -> None:
    master_service._OPEN_ORDERS_CACHE["version"] = 7
    response = asyncio.run(master_service.open_orders_version())
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["version"] == 7
    assert "updated_at" in payload


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
    assert "Failed to load webhook attempts:" in js


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


def test_open_orders_attempt_fetch_error_is_not_rendered_as_empty() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "Failed to load webhook attempts:" in js
    assert ("renderWebhookAttempts([], attemptErr?.message" in js) or ("renderWebhookAttempts([],attemptErr?.message" in js)


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


def test_open_orders_page_contains_webhook_diagnostic_ui() -> None:
    html = asyncio.run(master_service.merged_open_orders_page()).body.decode("utf-8")
    assert 'id="pending-webhook-id-input"' in html
    assert 'id="webhook-diagnostic-card"' in html


def test_open_orders_js_fetches_webhook_diagnostic() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "pending_webhook_id" in js
    assert "/api/calculator/webhook-diagnostic/" in js
