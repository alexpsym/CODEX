import asyncio
import importlib.util
import json
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


def test_scripts_listing_includes_open_orders_button() -> None:
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    row = next((item for item in payload if item.get("name") == "open-orders"), None)
    assert row is not None
    assert row.get("open_url") == "/merged/open-orders"
    assert row.get("dashboard_main_view") is True


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
    assert "<th>Test</th>" in html
    assert "/static/open_orders.js?v=" in html


def test_open_orders_js_is_manual_refresh_and_uses_force_query() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "setInterval(" not in js
    assert "visibilitychange" not in js
    assert "POLL_MS" not in js
    assert "HIDDEN_MULTIPLIER" not in js
    assert "/api/open-orders?force=1" in js


def test_open_orders_js_treats_webhook_as_cancelable() -> None:
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    assert "type === 'webhook'" in js
    assert "type === 'order' || type === 'webhook'" in js
    assert "item.is_test_trade" in js


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
