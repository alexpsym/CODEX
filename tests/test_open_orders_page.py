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
    assert 'id="open-orders-warnings"' in html
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
        "Broker / Venue",
        "Account",
        "Asset",
        "Category",
        "Side",
        "Order Type",
        "Instrument",
        "Stop-loss Ticks",
        "Target Mode",
        "Take-profit Ticks",
        "Risk Mode",
        "Risk Value",
        "Risk / Reward",
        "Timeframe",
        "Test",
        "Setup",
        "Pattern",
        "EMA",
        "VWAP",
        "ATH / ATL",
        "Round Number",
        "Webhook Mode",
        "Webhook Status",
        "Planned Entry",
        "Planned Stop",
        "Planned Target",
        "Type",
        "Size",
        "Entry / Order",
        "Current / Trigger",
        "Stop Loss",
        "Take Profit",
        "Leverage / Margin",
        "Funding paid",
        "Funding received",
        "Net funding",
        "Opened",
        "Status",
        "Action",
    ]

    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    rendered_values = re.search(
        r"\[(item\.venue\|\|item\.broker,resolveAccountLabel\(item\).*?,item\.status)\]\.forEach",
        js,
    )
    assert rendered_values
    assert rendered_values.group(1).split(",") == [
        "item.venue||item.broker",
        "resolveAccountLabel(item)",
        "item.asset",
        "item.category",
        "item.side",
        "item.order_type",
        "item.instrument",
        "item.stop_loss_ticks",
        "item.target_mode_display",
        "item.take_profit_ticks",
        "item.risk_mode_display",
        "item.risk_value_display",
        "item.risk_reward",
        "item.timeframe",
        "item.is_test_trade",
        "item.setup",
        "item.pattern",
        "item.ema",
        "item.vwap",
        "item.aths_atls",
        "item.round_number",
        "item.webhook_mode",
        "item.webhook_status",
        "item.planned_entry_price",
        "item.planned_stop_price",
        "item.planned_target_price",
        "item.type",
        "item.size",
        "item.entry_price||item.order_price",
        "item.current_price",
        "item.stop_loss",
        "item.take_profit",
        "item.leverage",
        "item.type==='Position'?item.funding_paid:null",
        "item.type==='Position'?item.funding_received:null",
        "item.type==='Position'?item.net_funding:null",
        "formatTimestamp(item.opened_at)",
        "item.status",
    ]
    assert "renderActionCell(item,actionTd,{allowAction:true}); row.appendChild(actionTd); tbody.appendChild(row);" in js
    assert len(headers) == len(rendered_values.group(1).split(",")) + 1 == 39
    assert headers[-1] == "Action"


def test_bybit_open_position_funding_uses_current_lifecycle_and_reports_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = asyncio.run(master_service.merged_open_orders_page()).body.decode("utf-8")
    js = (ROOT / "render" / "static" / "open_orders.js").read_text(encoding="utf-8")
    for heading in ("Funding paid", "Funding received", "Net funding"):
        assert f"<th>{heading}</th>" in html
    assert "item.type==='Position'?item.funding_paid:null" in js
    assert "item.type==='Position'?item.funding_received:null" in js
    assert "item.type==='Position'?item.net_funding:null" in js

    monkeypatch.setattr(master_service.time, "time", lambda: 10.0)
    master_service._BYBIT_OPEN_FUNDING_CACHE.clear()
    executions = [
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": "1", "execTime": "1100", "execId": "e1"},
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Sell", "execQty": "1", "execTime": "1200", "execId": "e2"},
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": "1", "execTime": "2000", "execId": "e3"},
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": "1", "execTime": "2100", "execId": "e4"},
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Sell", "execQty": ".5", "execTime": "2200", "execId": "e5"},
        {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": ".5", "execTime": "2300", "execId": "e6"},
    ]

    async def fake_executions(**_kwargs):
        return executions

    transactions = [
        {"transactionId": "old", "transactionTime": "1900", "symbol": "BTCUSDT", "currency": "USDT", "funding": "9"},
        {"transactionId": "received", "transactionTime": "3000", "symbol": "BTCUSDT", "currency": "USDT", "funding": ".30"},
        {"transactionId": "paid", "transactionTime": "4000", "symbol": "BTCUSDT", "currency": "USDT", "funding": "-.10"},
        {"transactionId": "currency", "transactionTime": "5000", "symbol": "BTCUSDT", "currency": "USDC", "funding": "1"},
    ]

    async def fake_transactions(**_kwargs):
        return {"result": {"list": transactions, "nextPageCursor": ""}}

    monkeypatch.setattr(master_service, "_fetch_bybit_executions_chunked", fake_executions)
    monkeypatch.setattr(master_service, "_fetch_bybit_transaction_log", fake_transactions)
    position = {
        "type": "Position", "account": "demo", "category": "linear", "instrument": "BTCUSDT",
        "position_idx": 0, "side": "Buy", "size": 2, "opened_at": 1000, "settlement_currency": "USDT",
    }
    warnings = asyncio.run(
        master_service._enrich_bybit_open_position_funding(
            [position], base_url="https://api-testnet.bybit.com", api_key="KEY", api_secret="SECRET", account_context="demo"
        )
    )
    assert warnings == []
    assert position["funding_lifecycle_started_at"] == 2000
    assert position["funding_paid"] == pytest.approx(0.10)
    assert position["funding_received"] == pytest.approx(0.30)
    assert position["net_funding"] == pytest.approx(0.20)

    master_service._BYBIT_OPEN_FUNDING_CACHE.clear()

    async def failed_transactions(**_kwargs):
        raise RuntimeError("api_key=KEY api_secret=SECRET upstream unavailable")

    monkeypatch.setattr(master_service, "_fetch_bybit_transaction_log", failed_transactions)
    unknown = dict(position)
    unknown.update({"funding_paid": 0, "funding_received": 0, "net_funding": 0})
    warning_rows = asyncio.run(
        master_service._enrich_bybit_open_position_funding(
            [unknown], base_url="https://api-testnet.bybit.com", api_key="KEY", api_secret="SECRET", account_context="demo"
        )
    )
    assert unknown["funding_paid"] is None
    assert unknown["funding_received"] is None
    assert unknown["net_funding"] is None
    assert warning_rows and "KEY" not in str(warning_rows) and "SECRET" not in str(warning_rows)


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


@pytest.mark.parametrize(
    ("failed_suffix", "successful_item_type"),
    [
        ("/openTrades", "Order"),
        ("/pendingOrders", "Position"),
    ],
)
def test_collect_oanda_open_items_reports_each_required_endpoint_failure(
    monkeypatch: pytest.MonkeyPatch,
    failed_suffix: str,
    successful_item_type: str,
) -> None:
    async def fake_fetch_oanda_json(*, endpoint: str, **_kwargs):
        if endpoint.endswith(failed_suffix):
            request = master_service.httpx.Request(
                "GET",
                f"https://demo.oanda.test{endpoint}",
            )
            raise master_service.httpx.ReadTimeout("", request=request)
        if endpoint.endswith("/openTrades"):
            return {
                "trades": [
                    {
                        "id": "trade-success",
                        "instrument": "EUR_USD",
                        "currentUnits": "1000",
                        "state": "OPEN",
                    }
                ]
            }
        return {
            "orders": [
                {
                    "id": "order-success",
                    "instrument": "EUR_USD",
                    "units": "1000",
                    "state": "PENDING",
                }
            ]
        }

    monkeypatch.setattr(master_service, "_fetch_oanda_json", fake_fetch_oanda_json)
    monkeypatch.setattr(
        master_service,
        "_lookup_trade_context_for_open_item",
        lambda _item: None,
    )

    payload = asyncio.run(
        master_service._collect_oanda_open_items(
            base_url="https://demo.oanda.test",
            account_id="CFG-DEMO",
            api_key="token",
            account_context="demo",
        )
    )

    assert [item["type"] for item in payload["items"]] == [successful_item_type]
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["endpoint"].endswith(failed_suffix)
    assert "demo/CFG-DEMO" in payload["errors"][0]["message"]


def _stub_open_orders_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    discovery_results: dict[str, object] | None = None,
    collect_override=None,
) -> list[tuple[str, str]]:
    discovery_results = discovery_results or {}
    collected_accounts: list[tuple[str, str]] = []

    def fake_oanda_config(account: str) -> dict[str, str]:
        return {
            "account_id": f"CFG-{account.upper()}",
            "token": f"TOKEN-{account.upper()}",
            "base_url": f"https://{account}.oanda.test",
        }

    async def fake_discovery(*, base_url: str, **_kwargs):
        account = "demo" if "demo." in base_url else "live"
        result = discovery_results.get(account, [])
        if isinstance(result, Exception):
            raise result
        return result

    async def fake_collect(*, account_context: str, account_id: str, **kwargs):
        collected_accounts.append((account_context, account_id))
        if collect_override is not None:
            overridden = await collect_override(
                account_context=account_context,
                account_id=account_id,
                **kwargs,
            )
            if overridden is not None:
                return overridden
        return {
            "items": [
                {
                    "broker": "OANDA",
                    "account": account_context,
                    "category": "forex",
                    "instrument": f"{account_context.upper()}_{account_id}",
                    "type": "Order",
                    "id": f"oanda-{account_context}-{account_id}",
                    "status": "PENDING",
                }
            ],
            "errors": [],
        }

    async def fake_bybit_collect(*, account_context: str, **_kwargs):
        if account_context != "live":
            return {"items": [], "errors": []}
        return {
            "items": [
                {
                    "broker": "Bybit",
                    "account": "live",
                    "category": "linear",
                    "instrument": "BTCUSDT",
                    "type": "Order",
                    "id": "bybit-live-order",
                    "status": "New",
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(master_service, "_get_oanda_config", fake_oanda_config)
    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", fake_discovery)
    monkeypatch.setattr(master_service, "_collect_oanda_open_items", fake_collect)
    monkeypatch.setattr(
        master_service,
        "resolve_bybit_credentials_for",
        lambda account: (
            account,
            f"KEY-{account}",
            f"SECRET-{account}",
            f"https://{account}.bybit.test",
            "test",
        ),
    )
    monkeypatch.setattr(master_service, "_collect_bybit_open_items", fake_bybit_collect)
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(master_service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(
        master_service.script_manager,
        "get",
        lambda _name: type("S", (), {"is_running": False})(),
    )
    return collected_accounts


def _discovery_timeout() -> Exception:
    return master_service.OandaAccountDiscoveryError(
        failure_kind="timeout",
        attempts=3,
        timeout_s=6.0,
        error_type="ReadTimeout",
    )


def test_discovery_timeout_without_cache_keeps_configured_oanda_and_bybit_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collected = _stub_open_orders_sources(
        monkeypatch,
        discovery_results={"demo": _discovery_timeout()},
    )

    payload = json.loads(
        asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8")
    )

    item_ids = {item["id"] for item in payload["items"]}
    assert "oanda-demo-CFG-DEMO" in item_ids
    assert "bybit-live-order" in item_ids
    assert ("demo", "CFG-DEMO") in collected
    assert payload["stale"] is False
    assert payload["errors"] == []
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 1
    assert payload["source_counts"]["errors"] == 0
    assert payload["source_counts"]["warnings"] == 1
    warning = payload["warnings"][0]
    assert warning["endpoint"] == "/v3/accounts"
    assert "timed out after 3 attempts" in warning["message"]
    assert "only the configured account's current open trades and pending orders were confirmed" in warning["message"]
    assert "TOKEN-DEMO" not in warning["message"]
    assert "https://" not in warning["message"]


def test_discovery_timeout_uses_last_known_good_ids_only_for_current_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_discovery = master_service.OandaAccountDiscoveryResult(
        accounts=[{"id": "SECONDARY-DEMO", "tags": ["MT4"]}],
        cache_state="stale",
        fetched_at=master_service.time.time() - 3600,
        discovery_error=_discovery_timeout(),
    )
    collected = _stub_open_orders_sources(
        monkeypatch,
        discovery_results={"demo": stale_discovery},
    )

    payload = json.loads(
        asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8")
    )

    assert ("demo", "CFG-DEMO") in collected
    assert ("demo", "SECONDARY-DEMO") in collected
    item_ids = {item["id"] for item in payload["items"]}
    assert "oanda-demo-CFG-DEMO" in item_ids
    assert "oanda-demo-SECONDARY-DEMO" in item_ids
    assert "bybit-live-order" in item_ids
    assert payload["stale"] is False
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 1
    assert "last-known-good owned-account list" in payload["warnings"][0]["message"]
    assert "current open trades and pending orders were requested" in payload["warnings"][0]["message"]


def test_oanda_account_cache_returns_bounded_last_known_good_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://demo.oanda.test"
    token = "cache-token-that-must-not-appear"
    cache_key = master_service.hashlib.sha256(
        f"{base_url}|{token}".encode("utf-8")
    ).hexdigest()
    now = master_service.time.time()
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()
    master_service._OANDA_ACCOUNTS_CACHE[cache_key] = {
        "fetched_at": now - 120,
        "fresh_until": now - 1,
        "stale_until": now + 60,
        "accounts": [{"id": "SECONDARY-DEMO"}],
    }

    async def fail_discovery(**_kwargs):
        raise _discovery_timeout()

    monkeypatch.setattr(master_service, "_list_oanda_accounts", fail_discovery)
    result = asyncio.run(
        master_service._get_cached_oanda_accounts(
            base_url=base_url,
            api_key=token,
        )
    )

    assert result.cache_state == "stale"
    assert result.accounts == [{"id": "SECONDARY-DEMO"}]
    assert isinstance(result.discovery_error, master_service.OandaAccountDiscoveryError)
    assert token not in str(result.discovery_error)
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()


def test_oanda_account_cache_does_not_mask_nontransient_discovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://demo.oanda.test"
    token = "cache-token"
    cache_key = master_service.hashlib.sha256(
        f"{base_url}|{token}".encode("utf-8")
    ).hexdigest()
    now = master_service.time.time()
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()
    master_service._OANDA_ACCOUNTS_CACHE[cache_key] = {
        "fetched_at": now - 120,
        "fresh_until": now - 1,
        "stale_until": now + 60,
        "accounts": [{"id": "SECONDARY-DEMO"}],
    }
    auth_failure = master_service.OandaAccountDiscoveryError(
        failure_kind="http",
        attempts=1,
        timeout_s=6.0,
        error_type="HTTPStatusError",
        http_status=401,
    )

    async def fail_discovery(**_kwargs):
        raise auth_failure

    monkeypatch.setattr(master_service, "_list_oanda_accounts", fail_discovery)
    with pytest.raises(master_service.OandaAccountDiscoveryError) as exc_info:
        asyncio.run(
            master_service._get_cached_oanda_accounts(
                base_url=base_url,
                api_key=token,
            )
        )

    assert exc_info.value.http_status == 401
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()


def test_concurrent_oanda_account_discovery_requests_share_one_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()

    async def successful_discovery(**_kwargs):
        calls["count"] += 1
        await asyncio.sleep(0)
        return [{"id": "CFG-DEMO"}]

    async def run_concurrently():
        return await asyncio.gather(
            master_service._get_cached_oanda_accounts(
                base_url="https://demo.oanda.test",
                api_key="token",
            ),
            master_service._get_cached_oanda_accounts(
                base_url="https://demo.oanda.test",
                api_key="token",
            ),
        )

    monkeypatch.setattr(master_service, "_list_oanda_accounts", successful_discovery)
    results = asyncio.run(run_concurrently())

    assert calls["count"] == 1
    assert [result.accounts for result in results] == [
        [{"id": "CFG-DEMO"}],
        [{"id": "CFG-DEMO"}],
    ]
    master_service._OANDA_ACCOUNTS_CACHE.clear()
    master_service._OANDA_ACCOUNTS_INFLIGHT.clear()


@pytest.mark.parametrize(
    ("failed_endpoint", "successful_type"),
    [
        ("/v3/accounts/{accountID}/openTrades", "Order"),
        ("/v3/accounts/{accountID}/pendingOrders", "Position"),
    ],
)
def test_configured_oanda_endpoint_failure_is_blocking_and_preserves_partial_rows(
    monkeypatch: pytest.MonkeyPatch,
    failed_endpoint: str,
    successful_type: str,
) -> None:
    async def collect_override(*, account_context: str, account_id: str, **_kwargs):
        if account_context != "demo" or account_id != "CFG-DEMO":
            return None
        return {
            "items": [
                {
                    "broker": "OANDA",
                    "account": "demo",
                    "category": "forex",
                    "instrument": "EUR_USD",
                    "type": successful_type,
                    "id": "oanda-partial-success",
                    "status": "OPEN",
                }
            ],
            "errors": [
                {
                    "endpoint": failed_endpoint,
                    "message": (
                        f"OANDA {failed_endpoint} timed out for demo/CFG-DEMO "
                        "after bounded retries"
                    ),
                }
            ],
        }

    _stub_open_orders_sources(monkeypatch, collect_override=collect_override)
    payload = json.loads(
        asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8")
    )

    item_ids = {item["id"] for item in payload["items"]}
    assert "oanda-partial-success" in item_ids
    assert "bybit-live-order" in item_ids
    assert payload["stale"] is True
    assert payload["error_count"] == 1
    assert payload["warning_count"] == 0
    assert payload["errors"][0]["account_id"] == "CFG-DEMO"
    assert payload["errors"][0]["endpoint"] == failed_endpoint


def test_discovered_secondary_account_failure_is_blocking_but_preserves_other_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def collect_override(*, account_context: str, account_id: str, **_kwargs):
        if account_context != "demo" or account_id != "SECONDARY-DEMO":
            return None
        return {
            "items": [],
            "errors": [
                {
                    "endpoint": "/v3/accounts/{accountID}/pendingOrders",
                    "message": "OANDA pending-orders refresh failed for demo/SECONDARY-DEMO",
                }
            ],
        }

    _stub_open_orders_sources(
        monkeypatch,
        discovery_results={"demo": [{"id": "SECONDARY-DEMO"}]},
        collect_override=collect_override,
    )
    payload = json.loads(
        asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8")
    )

    item_ids = {item["id"] for item in payload["items"]}
    assert "oanda-demo-CFG-DEMO" in item_ids
    assert "bybit-live-order" in item_ids
    assert payload["stale"] is True
    assert payload["error_count"] == 1
    assert payload["warning_count"] == 0
    assert payload["errors"][0]["account_id"] == "SECONDARY-DEMO"
    assert payload["errors"][0]["endpoint"].endswith("/pendingOrders")


def test_oanda_discovery_timeout_retries_are_bounded_and_diagnostics_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []
    token = "oanda-token-that-must-not-appear"
    base_url = "https://demo.oanda.test"

    class TimeoutClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def get(self, url, **_kwargs):
            calls["count"] += 1
            request = master_service.httpx.Request("GET", url)
            raise master_service.httpx.ReadTimeout(
                f"api_key={token} {base_url}?signature=hidden",
                request=request,
            )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(master_service.httpx, "AsyncClient", TimeoutClient)
    monkeypatch.setattr(master_service.asyncio, "sleep", fake_sleep)

    with pytest.raises(master_service.OandaAccountDiscoveryError) as exc_info:
        asyncio.run(
            master_service._list_oanda_accounts(
                base_url=base_url,
                api_key=token,
            )
        )

    message = str(exc_info.value)
    assert calls["count"] == 3
    assert len(sleeps) == 2
    assert exc_info.value.failure_kind == "timeout"
    assert exc_info.value.attempts == 3
    assert "6.0s timeout per attempt" in message
    assert "after 3 attempts" in message
    assert token not in message
    assert base_url not in message
    assert "signature=hidden" not in message


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


def test_broker_diagnostic_sanitizer_redacts_generic_bearer_and_token_fields() -> None:
    raw = (
        "Authorization: Bearer never-show-this "
        "access_token=also-never-show token='nor-this'"
    )

    sanitized = master_service._safe_bybit_diagnostic_text(raw)

    assert "never-show-this" not in sanitized
    assert "also-never-show" not in sanitized
    assert "nor-this" not in sanitized
    assert sanitized.count("[redacted]") >= 3


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


def test_open_orders_browser_renders_warnings_separately_from_blocking_errors() -> None:
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
  }}
  appendChild(node) {{ this.children.push(node); return node; }}
  addEventListener(event, handler) {{ this.handlers[event] = handler; }}
  querySelector() {{ return null; }}
}}
const refreshButton = new Element();
const statusBadge = new Element();
const tbody = new Element();
const table = new Element();
table.querySelector = () => tbody;
const errorsBox = new Element();
const errorsList = new Element();
errorsBox.querySelector = () => errorsList;
const warningsBox = new Element();
const warningsList = new Element();
warningsBox.querySelector = () => warningsList;
const elements = {{
  'refresh-btn': refreshButton,
  'open-orders-status': statusBadge,
  'open-orders-table': table,
  'open-orders-empty': new Element(),
  'open-orders-errors': errorsBox,
  'open-orders-warnings': warningsBox,
}};
global.window = {{}};
global.document = {{
  hidden: true,
  getElementById(id) {{ return elements[id] || null; }},
  addEventListener() {{}},
  createElement() {{ return new Element(); }},
}};
let mode = 'warning';
global.fetch = async () => ({{
  ok: true,
  status: 200,
  statusText: 'OK',
  text: async () => JSON.stringify(mode === 'warning' ? {{
    items: [],
    errors: [],
    warnings: [{{
      broker: 'OANDA',
      account: 'demo',
      category: 'forex',
      source_type: 'account discovery',
      endpoint: '/v3/accounts',
      message: 'Additional owned-account discovery timed out; only the configured account was confirmed.',
    }}],
    stale: false,
  }} : {{
    items: [],
    errors: [{{
      broker: 'OANDA',
      account: 'demo',
      category: 'forex',
      source_type: 'account data',
      endpoint: '/v3/accounts/{{accountID}}/openTrades',
      message: 'Configured account openTrades timed out.',
    }}],
    warnings: [],
    stale: true,
  }}),
}});
eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
setTimeout(async () => {{
  const warningState = {{
    badge: statusBadge.textContent,
    errorsDisplay: errorsBox.style.display,
    warningsDisplay: warningsBox.style.display,
    warningText: warningsList.children.map((node) => node.textContent),
  }};
  mode = 'error';
  await refreshButton.handlers.click();
  const errorState = {{
    badge: statusBadge.textContent,
    errorsDisplay: errorsBox.style.display,
    warningsDisplay: warningsBox.style.display,
    errorText: errorsList.children.map((node) => node.textContent),
  }};
  console.log(JSON.stringify([warningState, errorState]));
}}, 25);
"""
    result = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    warning_state, error_state = json.loads(result.stdout.strip().splitlines()[-1])

    assert warning_state["badge"] == "Updated (1 warning)"
    assert warning_state["errorsDisplay"] == "none"
    assert warning_state["warningsDisplay"] == "block"
    assert len(warning_state["warningText"]) == 1
    assert not warning_state["warningText"][0].rstrip().endswith(":")
    assert error_state["badge"] == "Stale (1 error)"
    assert error_state["errorsDisplay"] == "block"
    assert error_state["warningsDisplay"] == "none"
    assert len(error_state["errorText"]) == 1
    assert not error_state["errorText"][0].rstrip().endswith(":")


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
    monkeypatch.setattr(master_service, "_delete_pending_webhook", lambda _item_id, **_kwargs: calls.__setitem__("delete", calls["delete"] + 1) or True)
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
    assert "[item.venue||item.broker,resolveAccountLabel(item)" in js
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


def test_bybit_auth_preflight_fails_fast_before_category_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"preflight": 0, "fanout": 0}

    async def failed_preflight(**_kwargs):
        calls["preflight"] += 1
        raise master_service.BybitAuthPreflightError(
            {
                "message": "Bybit auth failed safely",
                "auth_failure": "true",
                "auth_classification": "invalid_credentials_or_auth_headers",
                "mode": "demo",
            }
        )

    async def forbidden_fanout(**_kwargs):
        calls["fanout"] += 1
        raise AssertionError("category fan-out must not run after failed auth preflight")

    monkeypatch.setattr(master_service, "_bybit_read_only_auth_preflight", failed_preflight)
    monkeypatch.setattr(master_service, "_fetch_bybit_positions_for_category", forbidden_fanout)
    monkeypatch.setattr(master_service, "_fetch_bybit_orders_for_category", forbidden_fanout)

    payload = asyncio.run(
        master_service._collect_bybit_open_items(
            base_url="https://api-demo.bybit.test",
            api_key="configured-key",
            api_secret="configured-secret",
            account_context="demo",
            key_source="KEY2",
        )
    )

    assert calls == {"preflight": 1, "fanout": 0}
    assert payload["auth_failed"] is True
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["auth_classification"] == "invalid_credentials_or_auth_headers"


def test_bybit_http_401_empty_body_is_not_retried_and_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"get": 0}

    async def fake_headers(**_kwargs):
        return {"X-BAPI-API-KEY": "header-redacted-by-test"}

    class Response:
        status_code = 401
        content = b""
        text = ""

        def json(self):
            raise ValueError("empty")

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            calls["get"] += 1
            return Response()

    monkeypatch.setattr(master_service, "_build_bybit_signed_headers", fake_headers)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", Client)

    with pytest.raises(master_service.BybitSignedGETError) as exc_info:
        asyncio.run(
            master_service._bybit_signed_get(
                base_url="https://api-demo.bybit.test",
                api_key="key",
                api_secret="secret",
                path="/v5/user/query-api",
                params={},
            )
        )

    assert calls["get"] == 1
    assert exc_info.value.http_status == 401
    assert "empty" in exc_info.value.ret_msg.lower()
    assert master_service._bybit_auth_failure_classification(exc_info.value) == "invalid_credentials_or_auth_headers"


def test_bybit_timestamp_window_error_retries_once_with_forced_time_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_sync_values: list[bool] = []
    responses = [
        {"retCode": 10002, "retMsg": "request time exceeds recv_window"},
        {"retCode": 0, "retMsg": "OK", "result": {"readOnly": 1}},
    ]

    async def fake_headers(*, force_time_sync: bool, **_kwargs):
        force_sync_values.append(force_time_sync)
        return {"X-BAPI-API-KEY": "safe"}

    class Response:
        status_code = 200
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self.payload

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Response(responses.pop(0))

    monkeypatch.setattr(master_service, "_build_bybit_signed_headers", fake_headers)
    monkeypatch.setattr(master_service.httpx, "AsyncClient", Client)

    payload = asyncio.run(
        master_service._bybit_signed_get(
            base_url="https://api-demo.bybit.test",
            api_key="key",
            api_secret="secret",
            path="/v5/user/query-api",
            params={},
        )
    )

    assert payload["retCode"] == 0
    assert force_sync_values == [False, True]


def test_bybit_preflight_diagnostic_redacts_echoed_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api_key = "sensitive-auth-key-ALPHA9876"
    api_secret = "sensitive-auth-secret-OMEGA5432"

    async def failed_signed_get(**_kwargs):
        raise master_service.BybitSignedGETError(
            path="/v5/user/query-api",
            failure_kind="http_error",
            attempts=1,
            http_status=401,
            ret_msg=f"api_key={api_key} secret={api_secret}",
        )

    monkeypatch.setattr(master_service, "_bybit_signed_get", failed_signed_get)
    caplog.set_level("INFO")

    with pytest.raises(master_service.BybitAuthPreflightError) as exc_info:
        asyncio.run(
            master_service._bybit_read_only_auth_preflight(
                base_url="https://api-demo.bybit.com",
                api_key=api_key,
                api_secret=api_secret,
                account_context="demo",
                key_source="KEY2",
            )
        )

    rendered = json.dumps(exc_info.value.diagnostic, sort_keys=True) + " " + caplog.text
    assert api_key not in rendered
    assert api_secret not in rendered
    assert api_key[-4:] not in rendered
    assert api_secret[-4:] not in rendered
    assert "token_last4" not in rendered
    assert "[redacted]" in rendered
    assert master_service._bybit_credential_fingerprint(api_key, api_secret) in rendered


def test_bybit_auth_failure_preserves_last_known_good_rows_as_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_snapshot = dict(master_service._OPEN_ORDERS_CACHE)
    last_good_snapshot = {
        key: [dict(row) for row in rows]
        for key, rows in master_service._OPEN_ORDERS_LAST_GOOD_ITEMS.items()
    }
    try:
        master_service._OPEN_ORDERS_CACHE.update({"payload": None, "expires_at": 0.0, "version": 21})
        master_service._OPEN_ORDERS_LAST_GOOD_ITEMS.clear()
        master_service._OPEN_ORDERS_LAST_GOOD_ITEMS["bybit:demo"] = [
            {
                "broker": "Bybit",
                "account": "demo",
                "category": "linear",
                "instrument": "BTCUSDT",
                "type": "Order",
                "id": "last-good-order",
                "status": "New",
            }
        ]

        monkeypatch.setattr(master_service, "_get_oanda_config", lambda _account: (_ for _ in ()).throw(ValueError("not configured")))
        monkeypatch.setattr(master_service, "resolve_bybit_credentials_for", lambda account: (account, "key", "secret", "https://bybit.test", "KEY2"))

        async def fake_bybit_collect(*, account_context: str, **_kwargs):
            if account_context == "demo":
                return {
                    "items": [],
                    "errors": [{"broker": "Bybit", "account": "demo", "message": "sanitized auth failure"}],
                    "auth_failed": True,
                }
            return {"items": [], "errors": []}

        monkeypatch.setattr(master_service, "_collect_bybit_open_items", fake_bybit_collect)
        monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
        monkeypatch.setattr(master_service, "_load_bounce_traders", lambda: [])
        monkeypatch.setattr(master_service.script_manager, "get", lambda _name: type("S", (), {"is_running": False})())

        payload = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))

        stale_row = next(row for row in payload["items"] if row.get("id") == "last-good-order")
        assert stale_row["source_stale"] is True
        assert "last-known-good" in stale_row["stale_reason"]
        assert any("auth failure" in str(error.get("message")) for error in payload["errors"])
    finally:
        master_service._OPEN_ORDERS_CACHE.clear()
        master_service._OPEN_ORDERS_CACHE.update(cache_snapshot)
        master_service._OPEN_ORDERS_LAST_GOOD_ITEMS.clear()
        master_service._OPEN_ORDERS_LAST_GOOD_ITEMS.update(last_good_snapshot)


def test_oanda_configured_account_success_retries_transient_discovery_401_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collected = _stub_open_orders_sources(monkeypatch)
    calls = {"demo": 0, "live": 0}
    auth_401 = master_service.OandaAccountDiscoveryError(
        failure_kind="http",
        attempts=1,
        timeout_s=6.0,
        error_type="HTTPStatusError",
        http_status=401,
    )

    async def discovery(*, base_url: str, **_kwargs):
        account = "demo" if "demo." in base_url else "live"
        calls[account] += 1
        if account == "demo" and calls[account] == 1:
            raise auth_401
        if account == "demo":
            return master_service.OandaAccountDiscoveryResult(
                accounts=[{"id": "SECONDARY-DEMO", "tags": ["MT4"]}],
                cache_state="refreshed",
                fetched_at=master_service.time.time(),
            )
        return []

    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", discovery)
    monkeypatch.setattr(master_service, "_OANDA_DISCOVERY_AUTH_ANOMALY_RETRY_DELAY_SECONDS", 0.0)

    payload = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))

    assert calls["demo"] == 2
    assert ("demo", "CFG-DEMO") in collected
    assert ("demo", "SECONDARY-DEMO") in collected
    assert not [warning for warning in payload["warnings"] if warning.get("account") == "demo"]
    assert payload["error_count"] == 0


def test_oanda_token_wide_401_is_blocking_and_not_retried_as_discovery_anomaly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"demo": 0}
    auth_401 = master_service.OandaAccountDiscoveryError(
        failure_kind="http",
        attempts=1,
        timeout_s=6.0,
        error_type="HTTPStatusError",
        http_status=401,
    )

    async def collect_override(*, account_context: str, account_id: str, **_kwargs):
        if account_context == "demo" and account_id == "CFG-DEMO":
            return {
                "items": [],
                "errors": [
                    {
                        "endpoint": "/v3/accounts/{accountID}/openTrades",
                        "message": "OANDA HTTP 401 for configured account",
                    }
                ],
            }
        return None

    _stub_open_orders_sources(monkeypatch, collect_override=collect_override)

    async def discovery(*, base_url: str, **_kwargs):
        if "demo." in base_url:
            calls["demo"] += 1
            raise auth_401
        return []

    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", discovery)
    monkeypatch.setattr(master_service, "_OANDA_DISCOVERY_AUTH_ANOMALY_RETRY_DELAY_SECONDS", 0.0)

    payload = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))

    assert calls["demo"] == 1
    assert any(error.get("account") == "demo" and "401" in error.get("message", "") for error in payload["errors"])
    assert any(warning.get("account") == "demo" for warning in payload["warnings"])
    assert payload["stale"] is True


def test_oanda_discovery_warning_clears_after_next_successful_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"demo": 0}
    _stub_open_orders_sources(monkeypatch)

    async def discovery(*, base_url: str, **_kwargs):
        if "demo." not in base_url:
            return []
        calls["demo"] += 1
        if calls["demo"] == 1:
            raise _discovery_timeout()
        return []

    monkeypatch.setattr(master_service, "_get_cached_oanda_accounts", discovery)

    first = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))
    second = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))

    assert any(warning.get("account") == "demo" for warning in first["warnings"])
    assert not [warning for warning in second["warnings"] if warning.get("account") == "demo"]


@pytest.mark.parametrize(
    ("raw_category", "asset", "expected_broker", "expected_category"),
    [
        ("linear", "crypto", "bybit", "linear"),
        ("bybit", "crypto", "bybit", "linear"),
        ("forex", "fx", "oanda", "forex"),
        ("oanda", "fx", "oanda", "forex"),
    ],
)
def test_pending_webhook_context_preserves_broker_and_market_category(
    monkeypatch: pytest.MonkeyPatch,
    raw_category: str,
    asset: str,
    expected_broker: str,
    expected_category: str,
) -> None:
    saved_registry: list[list[dict[str, object]]] = []
    saved_contexts: list[dict[str, object]] = []
    invalidations = {"count": 0}
    monkeypatch.setattr(master_service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(master_service, "_save_pending_webhooks", lambda rows: saved_registry.append([dict(row) for row in rows]))
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda row: saved_contexts.append(dict(row)) or row)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: invalidations.__setitem__("count", invalidations["count"] + 1))

    master_service._upsert_pending_webhook(
        {
            "id": "pending-1",
            "asset": asset,
            "category": raw_category,
            "account": "demo",
            "instrument": "BTCUSDT" if asset == "crypto" else "EUR_USD",
            "side": "buy",
            "order_type": "market",
            "calculation_context_id": "ctx-1",
            "timeframe": "15MIN",
            "is_test_trade": False,
            "risk_mode": "fixed_aud",
            "risk_value": "10",
            "stop_loss_ticks": "35",
            "take_profit_ticks": "70",
            "target_mode": "ticks",
            "risk_reward": "2",
            "planned_entry_price": "1.1",
            "planned_stop_price": "1.0",
            "planned_target_price": "1.2",
        }
    )

    assert len(saved_registry) == 1
    assert len(saved_contexts) == 1
    context = saved_contexts[0]
    assert context["broker"] == expected_broker
    assert context["category"] == expected_category
    assert context["calculation_context_id"] == "ctx-1"
    assert context["timeframe"] == "15MIN"
    assert context["is_test_trade"] is False
    assert context["planned_target_price"] == "1.2"
    assert invalidations["count"] == 1


def test_pending_webhook_consume_invalidates_exactly_once_after_durable_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidations = {"count": 0}
    deleted_flags: list[bool] = []
    contexts: list[dict[str, object]] = []

    def delete(_webhook_id, *, invalidate_cache=True):
        deleted_flags.append(invalidate_cache)
        return True

    monkeypatch.setattr(master_service, "_delete_pending_webhook", delete)
    monkeypatch.setattr(master_service, "_upsert_trade_context", lambda row: contexts.append(dict(row)) or row)
    monkeypatch.setattr(master_service, "_invalidate_open_orders_cache", lambda: invalidations.__setitem__("count", invalidations["count"] + 1))
    monkeypatch.setattr(master_service, "_schedule_dropbox_upload_state_backup", lambda: None)

    assert master_service._consume_pending_webhook("pending-1", request_id="request-1") is True
    assert deleted_flags == [False]
    assert contexts[0]["status"] == "CONSUMED"
    assert invalidations["count"] == 1


def test_open_orders_collection_version_is_not_advanced_without_rendered_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_snapshot = dict(master_service._OPEN_ORDERS_CACHE)
    trigger = {"done": False}

    async def collect_override(*, account_context: str, account_id: str, **_kwargs):
        if not trigger["done"]:
            trigger["done"] = True
            master_service._invalidate_open_orders_cache()
        return {
            "items": [
                {
                    "broker": "OANDA",
                    "account": account_context,
                    "category": "forex",
                    "instrument": "EUR_USD",
                    "type": "Order",
                    "id": f"{account_context}-{account_id}",
                    "status": "PENDING",
                }
            ],
            "errors": [],
        }

    try:
        master_service._OPEN_ORDERS_CACHE.update({"payload": None, "expires_at": 0.0, "version": 40})
        _stub_open_orders_sources(monkeypatch, collect_override=collect_override)

        first = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))
        assert first["version"] == 40
        assert first["refresh_superseded"] is True
        assert master_service._OPEN_ORDERS_CACHE["version"] == 41
        assert master_service._OPEN_ORDERS_CACHE["payload"] is None

        second = json.loads(asyncio.run(master_service.list_open_orders(force=True)).body.decode("utf-8"))
        assert second["version"] == 41
        assert second["refresh_superseded"] is False
        assert master_service._OPEN_ORDERS_CACHE["payload"]["version"] == 41
    finally:
        master_service._OPEN_ORDERS_CACHE.clear()
        master_service._OPEN_ORDERS_CACHE.update(cache_snapshot)


def test_open_orders_browser_handles_refresh_races_and_pending_submit() -> None:
    node = shutil.which("node")
    assert node, "node is required for Open Orders browser-state regression"
    js_path = ROOT / "render" / "static" / "open_orders.js"
    harness = r"""
const fs = require('fs');
class Element {
  constructor() { this.children=[]; this.handlers={}; this.style={}; this.textContent=''; this._innerHTML=''; this.className=''; this.title=''; }
  set innerHTML(value) { this._innerHTML=value; if(value==='') this.children=[]; }
  get innerHTML() { return this._innerHTML; }
  appendChild(node) { this.children.push(node); return node; }
  addEventListener(event, handler) { this.handlers[event]=handler; }
  querySelector() { return null; }
}
const refreshButton=new Element();
const statusBadge=new Element();
const tbody=new Element();
const table=new Element(); table.querySelector=()=>tbody;
const errorsBox=new Element(); const errorsList=new Element(); errorsBox.querySelector=()=>errorsList;
const warningsBox=new Element(); const warningsList=new Element(); warningsBox.querySelector=()=>warningsList;
const documentHandlers={}; const windowHandlers={};
const elements={
  'refresh-btn':refreshButton,
  'open-orders-status':statusBadge,
  'open-orders-table':table,
  'open-orders-empty':new Element(),
  'open-orders-errors':errorsBox,
  'open-orders-warnings':warningsBox,
};
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(event,handler){windowHandlers[event]=handler;}};
global.document={
  hidden:false,
  getElementById(id){return elements[id]||null;},
  addEventListener(event,handler){documentHandlers[event]=handler;},
  createElement(){return new Element();},
};
global.localStorage={setItem(){}};
global.BroadcastChannel=undefined;
global.setInterval=()=>1; global.clearInterval=()=>{};
const realSetTimeout=global.setTimeout;
global.setTimeout=(fn,delay)=>{if(delay>=15000)return realSetTimeout(fn,delay);queueMicrotask(fn);return 1;};
const makeResponse=(payload)=>{
  const pending=visibilityMode==='missing';
  const normalized={core_complete:!pending,pending,empty_confirmed:!pending&&payload.items?.length===0,
    progress_revision:payload.version,...payload};
  if(Array.isArray(payload.items))normalized.items=payload.items.map(row=>({...row,source_stale:false,collection_version:payload.version}));
  return {ok:true,status:200,statusText:'OK',text:async()=>JSON.stringify(normalized)};
};
let serverVersion=1;
let openItems=[{broker:'Bybit',account:'demo',category:'linear',instrument:'BTCUSDT',type:'Order',id:'initial',status:'New'}];
let openCalls=0; let versionCalls=0; let resolveInitial=null;
let visibilityMode='off'; let visibilityReads=0;
global.fetch=async(url)=>{
  const value=String(url);
  if(value.includes('/api/open-orders/version')){versionCalls+=1;return makeResponse({version:serverVersion});}
  openCalls+=1;
  if(openCalls===1){return await new Promise((resolve)=>{resolveInitial=()=>resolve(makeResponse({items:[{id:'old'}],errors:[],warnings:[],version:1,stale:false}));});}
  if(visibilityMode==='eventual'){
    visibilityReads+=1;
    if(visibilityReads>=2){openItems=[
      {broker:'Bybit',account:'demo',category:'linear',instrument:'BTCUSDT',type:'Order',id:'a',calculation_context_id:'ctx-a',status:'New'},
      {broker:'Bybit',account:'demo',category:'linear',instrument:'ETHUSDT',type:'Order',id:'b',calculation_context_id:'ctx-b',status:'New'},
    ];}
  }
  return makeResponse({items:openItems,errors:[],warnings:[],version:serverVersion,stale:false});
};
eval(fs.readFileSync(__JS_PATH__,'utf8'));
const hooks=window.__openOrdersTestHooks;
const flush=async(count=40)=>{for(let i=0;i<count;i+=1)await new Promise((resolve)=>setImmediate(resolve));};
(async()=>{
  const queued=hooks.refresh('broadcast');
  serverVersion=2;
  openItems=[{broker:'Bybit',account:'demo',category:'linear',instrument:'BTCUSDT',type:'Order',id:'raced',status:'New'}];
  resolveInitial();
  await queued; await flush();
  const race={state:hooks.getState(),ids:(window.__openOrdersItems||[]).map((row)=>row.id),openCalls};

  const unchangedBefore=openCalls;
  await hooks.pollVersion(); await flush();
  const unchangedAfter=openCalls;

  serverVersion=3;
  openItems=[{broker:'Bybit',account:'demo',category:'linear',instrument:'ETHUSDT',type:'Order',id:'changed',status:'New'}];
  await hooks.pollVersion(); await flush();
  const changed={state:hooks.getState(),ids:(window.__openOrdersItems||[]).map((row)=>row.id)};

  document.hidden=true; documentHandlers.visibilitychange();
  serverVersion=4;
  const hiddenBefore=openCalls;
  await hooks.pollVersion(); await flush();
  const hiddenAfter=openCalls;
  openItems=[{broker:'Bybit',account:'demo',category:'linear',instrument:'SOLUSDT',type:'Order',id:'visible',status:'New'}];
  document.hidden=false; documentHandlers.visibilitychange(); await flush();
  const visible={state:hooks.getState(),ids:(window.__openOrdersItems||[]).map((row)=>row.id)};

  serverVersion=5; openItems=[]; visibilityMode='eventual'; visibilityReads=0;
  hooks.handleStateChange({type:'state-changed',action:'submit',eventId:'event-a',submittedAt:Date.now(),broker:'bybit',account:'demo',symbol:'BTCUSDT',calculationContextId:'ctx-a'});
  hooks.handleStateChange({type:'state-changed',action:'submit',eventId:'event-b',submittedAt:Date.now(),broker:'bybit',account:'demo',symbol:'ETHUSDT',calculationContextId:'ctx-b'});
  await flush(100);
  const rapid={state:hooks.getState(),ids:(window.__openOrdersItems||[]).map((row)=>row.calculation_context_id),visibilityReads};

  visibilityMode='missing'; openItems=[];
  hooks.handleStateChange({type:'state-changed',action:'submit',eventId:'event-missing',submittedAt:Date.now(),broker:'bybit',account:'demo',symbol:'XRPUSDT',calculationContextId:'ctx-missing'});
  await flush(140);
  const bounded={state:hooks.getState(),badge:statusBadge.textContent};

  process.stdout.write(JSON.stringify({race,unchangedBefore,unchangedAfter,changed,hiddenBefore,hiddenAfter,visible,rapid,bounded,versionCalls}));
})().catch((error)=>{console.error(error);process.exit(1);});
""".replace("__JS_PATH__", json.dumps(str(js_path)))

    result = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    state = json.loads(result.stdout)

    assert state["race"]["state"]["knownVersion"] == 2
    assert state["race"]["ids"] == ["raced"]
    assert state["race"]["openCalls"] >= 2
    assert state["unchangedAfter"] == state["unchangedBefore"]
    assert state["changed"]["state"]["knownVersion"] == 3
    assert state["changed"]["ids"] == ["changed"]
    assert state["hiddenAfter"] == state["hiddenBefore"]
    assert state["visible"]["state"]["knownVersion"] == 4
    assert state["visible"]["ids"] == ["visible"]
    assert state["rapid"]["state"]["pendingVisibilityChecks"] == 0
    assert sorted(state["rapid"]["ids"]) == ["ctx-a", "ctx-b"]
    assert state["rapid"]["visibilityReads"] >= 2
    assert state["bounded"]["state"]["pendingVisibilityChecks"] == 1
    assert "Loading broker data" in state["bounded"]["badge"]



def _incremental_open_orders_fixture(monkeypatch, handler):
    service = master_service
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"payload": None, "version": 100, "expires_at": 0})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OANDA_ACCOUNTS_CACHE", {})
    monkeypatch.setattr(service, "_OANDA_ACCOUNTS_INFLIGHT", {})
    monkeypatch.setattr(service, "_BYBIT_TIME_OFFSET_CACHE", {})
    monkeypatch.setattr(service, "_BYBIT_OPEN_FUNDING_CACHE", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SOURCE_REFRESH_SECONDS", 1000.0)
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    monkeypatch.setattr(service, "_lookup_trade_context_for_open_item", lambda _row: None)
    monkeypatch.setattr(service, "_record_outbound_traffic", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_get_oanda_config", lambda account: {
        "account_id": "CFG-" + account, "token": "TOKEN-" + account, "base_url": "https://oanda.test"})
    monkeypatch.setattr(service, "resolve_bybit_credentials_for", lambda account:
        (account, "KEY-" + account, "SECRET-" + account, "https://bybit.test", "fixture"))
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [{"id": "pending", "status": "WAITING"}])
    reconciled = []
    monkeypatch.setattr(service, "_clean_pending_webhooks_for_open_items",
                        lambda pending, items, **kwargs: reconciled.append(list(items)) or (pending, False))
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda _name: type("Script", (), {"is_running": False})())
    real_client = service.httpx.AsyncClient
    clients, requests = [], []
    opened = int(service.time.time() * 1000) - 60000

    def client_factory(**kwargs):
        index = len(clients)
        async def transport(request):
            account = str(request.headers.get("X-BAPI-API-KEY", request.headers.get("Authorization", ""))).split("-")[-1]
            record = (index, account, request.url.path, dict(request.url.params))
            requests.append(record)
            override = await handler(request, account, opened)
            if override is not None:
                return override
            path, params = request.url.path, request.url.params
            if path == "/v3/accounts":
                return service.httpx.Response(200, json={"accounts": [{"id": "CFG-" + account, "tags": ["MT4"]}]})
            if path.endswith("openTrades"):
                return service.httpx.Response(200, json={"trades": []})
            if path.endswith("pendingOrders"):
                return service.httpx.Response(200, json={"orders": [{"id": "oanda-" + account,
                    "instrument": "EUR_USD", "units": "1", "type": "LIMIT", "state": "PENDING"}]})
            if path == "/v5/market/time":
                return service.httpx.Response(200, json={"retCode": 0, "result": {"timeSecond": str(service.time.time())}})
            if path == "/v5/user/query-api":
                return service.httpx.Response(200, json={"retCode": 0, "result": {"permissions": {}}})
            position = {"symbol": "BTCUSDT", "size": "1", "side": "Buy", "positionIdx": 0,
                        "createdTime": str(opened), "settleCoin": "USDT"}
            rows = []
            if path == "/v5/position/list" and (params.get("symbol") or params.get("settleCoin") == "USDT"):
                rows = [position]
            if path == "/v5/order/realtime" and params.get("settleCoin") == "USDT":
                rows = [{"symbol": "BTCUSDT", "orderId": "bybit-" + account, "orderStatus": "New", "qty": "1"}]
            if path == "/v5/execution/list":
                rows = [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": "1",
                         "execTime": str(opened), "execId": "opening"}]
            if path == "/v5/account/transaction-log":
                rows = [{"transactionId": "received", "symbol": "BTCUSDT", "currency": "USDT", "funding": ".30", "transactionTime": str(opened + 1000)},
                        {"transactionId": "paid", "symbol": "BTCUSDT", "currency": "USDT", "funding": "-.10", "transactionTime": str(opened + 2000)}]
            return service.httpx.Response(200, json={"retCode": 0, "result": {"list": rows, "nextPageCursor": ""}})
        client = real_client(transport=service.httpx.MockTransport(transport), trust_env=False, **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
    return clock, clients, requests, reconciled


async def _open_orders_eventually(predicate):
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(.005)
    raise AssertionError("Controlled Open Orders outcome did not publish")


def test_open_orders_incremental_collection_accepts_slow_valid_sources_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    service = master_service
    async def scenario():
        auth, position, funding, discovery = (asyncio.Event() for _ in range(4))
        phase = [1]
        obsolete_funding = asyncio.Event()
        obsolete_started, obsolete_cancelled = asyncio.Event(), asyncio.Event()
        async def handler(request, account, opened):
            path, params = request.url.path, request.url.params
            if phase[0] == 1:
                if path == "/v5/user/query-api" and account == "demo":
                    await auth.wait()
                if path == "/v5/position/list" and params.get("settleCoin") == "USDC" and account == "live":
                    await position.wait()
                if path == "/v5/account/transaction-log" and account == "live":
                    await funding.wait()
                if path == "/v3/accounts" and account == "live":
                    await discovery.wait()
            elif phase[0] == 2 and path == "/v5/account/transaction-log" and account == "live":
                obsolete_started.set()
                try:
                    await obsolete_funding.wait()
                finally:
                    obsolete_cancelled.set()
            return None
        clock, clients, requests, reconciled = _incremental_open_orders_fixture(monkeypatch, handler)
        duplicate_discovery = []
        try:
            initial = json.loads((await service.list_open_orders(force=True)).body)
            assert initial["pending"] and not initial["empty_confirmed"] and not initial["items"]
            owner = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()]
            job = owner.job
            await _open_orders_eventually(lambda: job.payload and any(row.get("id") == "bybit-live" for row in job.payload["items"]))
            healthy = job.payload
            assert {"oanda-live", "oanda-demo", "bybit-live"} <= {row.get("id") for row in healthy["items"]}
            assert not any(account == "demo" and path in {"/v5/position/list", "/v5/order/realtime"}
                           for _, account, path, _ in requests)
            assert not reconciled
            # Duplicate cold discovery waits share the existing owned underlying request.
            async def same_discovery():
                token = service._OPEN_ORDERS_JOB_SCOPE.set(job)
                try:
                    return await service._get_cached_oanda_accounts(base_url="https://oanda.test", api_key="TOKEN-live")
                finally:
                    service._OPEN_ORDERS_JOB_SCOPE.reset(token)
            duplicate_discovery = [asyncio.create_task(same_discovery()) for _ in range(2)]
            await asyncio.sleep(.01)
            assert sum(path == "/v3/accounts" and account == "live" for _, account, path, _ in requests) == 1
            before = len(requests)
            # Cross the former 4/26/28/32/60-second thresholds using the injected source clock.
            clock[0] = 65
            snapshots = await asyncio.gather(*(service.list_open_orders(force=True) for _ in range(12)))
            assert all(json.loads(response.body)["pending"] for response in snapshots)
            assert len(requests) == before and owner.job is job and not job.obsolete
            assert not job.payload["errors"] and not job.payload["empty_confirmed"]
            disconnected = asyncio.Event()
            async def consumer():
                await service.list_open_orders(force=True)
                disconnected.set()
                await asyncio.Event().wait()
            request = asyncio.create_task(consumer())
            await disconnected.wait()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert owner.job is job and any(not task.done() for task in job.tasks)

            auth.set(); position.set(); discovery.set()
            await asyncio.gather(*duplicate_discovery)
            duplicate_discovery.clear()
            await _open_orders_eventually(lambda: job.payload["core_complete"])
            assert job.payload["pending"] and not job.payload["empty_confirmed"] and not reconciled
            live_position = next(row for row in job.payload["items"] if row.get("broker") == "Bybit"
                                 and row.get("account") == "live" and row.get("type") == "Position")
            assert all(live_position.get(key) is None for key in ("funding_paid", "funding_received", "net_funding"))
            clock[0] = 130
            await service.list_open_orders(force=True)
            assert job.payload["pending"]
            funding.set()
            await _open_orders_eventually(lambda: not job.payload["pending"])
            loaded = json.loads((await service.list_open_orders()).body)
            assert loaded["core_complete"] and not loaded["errors"]
            live_position = next(row for row in loaded["items"] if row.get("broker") == "Bybit"
                                 and row.get("account") == "live" and row.get("type") == "Position")
            assert live_position["funding_paid"] == pytest.approx(.10)
            assert live_position["funding_received"] == pytest.approx(.30)
            assert live_position["net_funding"] == pytest.approx(.20)
            assert loaded["version"] == 100 and loaded["progress_revision"] > initial["progress_revision"]
            assert job.metrics["client_setups"] == len(clients) == 4
            assert job.metrics["time-sync_setups"] == 1
            assert job.metrics["discovery_setups"] == 2
            assert len(requests) > len(clients) * 3
            assert all(row.get("account_label_suffix") == "MT4" for row in loaded["items"] if row["broker"] == "OANDA")
            first_clients = list(clients)

            # Invalidation owns cancellation/awaiting and cannot let late funding publish.
            phase[0] = 2
            service._BYBIT_OPEN_FUNDING_CACHE.clear()
            service._invalidate_open_orders_cache()
            assert not job.authoritative
            next_generation = json.loads((await service.list_open_orders()).body)
            assert any(row["source_stale"] for row in next_generation["items"])
            assert next_generation["warnings"] and not next_generation["core_complete"]
            await asyncio.wait_for(obsolete_started.wait(), 2)
            old = owner.job
            assert old is not job and all(client.is_closed for client in first_clients)
            assert old.payload["pending"] and not old.payload["empty_confirmed"]
            phase[0] = 3
            service._invalidate_open_orders_cache()
            await service.list_open_orders()
            current = owner.job
            await _open_orders_eventually(lambda: current.payload["core_complete"] and not current.payload["pending"])
            assert obsolete_cancelled.is_set() and not old.tasks and not old.clients and not old.auxiliary
            obsolete_funding.set()
            await asyncio.sleep(0)
            assert service._OPEN_ORDERS_CACHE["payload"]["version"] == 102
            assert current.payload["version"] == 102 and not current.payload["refresh_superseded"]

            # Shutdown cancels genuinely pending service work and closes all pooled clients.
            phase[0] = 2
            obsolete_funding.clear()
            obsolete_started.clear()
            service._BYBIT_OPEN_FUNDING_CACHE.clear()
            service._invalidate_open_orders_cache()
            await service.list_open_orders()
            await asyncio.wait_for(obsolete_started.wait(), 2)
            stopping = owner.job
            await service._shutdown_open_orders_jobs()
            assert not stopping.tasks and not stopping.auxiliary and not stopping.clients
            assert not service._OPEN_ORDERS_SERVICES and all(client.is_closed for client in clients)
        finally:
            await service._shutdown_open_orders_jobs()
            for task in duplicate_discovery:
                task.cancel()
            await asyncio.gather(*duplicate_discovery, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_open_orders_recovery_is_source_scoped_and_diagnostics_remain_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    service = master_service
    async def scenario():
        discovery, recovery = asyncio.Event(), asyncio.Event()
        rate_limit = [True]
        recovering = asyncio.Event()
        async def handler(request, account, opened):
            path, params = request.url.path, request.url.params
            if path == "/v3/accounts":
                await discovery.wait()
            if path == "/v5/order/realtime" and account == "live" and params.get("category") == "inverse":
                if rate_limit[0]:
                    return service.httpx.Response(200, json={"retCode": 10006, "retMsg": "rate limit api_key=KEY-live api_secret=SECRET-live"})
                recovering.set()
                await recovery.wait()
            if path == "/v5/order/realtime" and account == "demo" and params.get("category") == "option":
                return service.httpx.Response(200, json={"retCode": 10005, "retMsg": "permission denied"})
            return None
        clock, clients, requests, reconciled = _incremental_open_orders_fixture(monkeypatch, handler)
        failed_key = "Bybit/live/inverse/n/a/orders"
        definitive_key = "Bybit/demo/option/n/a/orders"
        def count(key):
            account, category = ("live", "inverse") if key == failed_key else ("demo", "option")
            return sum(mode == account and path == "/v5/order/realtime" and params.get("category") == category
                       for _, mode, path, params in requests)
        try:
            await service.list_open_orders()
            job = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: job.sources[failed_key]["state"] == "failed" and job.sources[definitive_key]["state"] == "failed")
            first_errors = json.loads(json.dumps(job.payload["errors"]))
            assert {error["source_key"] for error in first_errors} == {failed_key, definitive_key}
            assert "KEY-live" not in str(first_errors) and "SECRET-live" not in str(first_errors)
            assert not job.payload["empty_confirmed"] and not job.payload["core_complete"] and not reconciled
            await _open_orders_eventually(lambda: all(entry["state"] not in {"pending", "blocked"}
                for entry in job.sources.values() if entry["meta"]["source_type"] != "account discovery"))
            healthy_reads = {(index, mode, path, str(params)): requests.count((index, mode, path, params))
                             for index, mode, path, params in requests
                             if not (path == "/v5/order/realtime" and params.get("category") in {"inverse", "option"})}
            before = len(requests)
            for _ in range(12):
                await service.list_open_orders()
                await service.open_orders_version()
            assert len(requests) == before and count(failed_key) == count(definitive_key) == 1
            discovery.set()
            await _open_orders_eventually(lambda: all(entry["state"] != "pending" for entry in job.sources.values()))
            assert job.payload["errors"] == first_errors
            assert any(row.get("id") == "bybit-live" for row in job.payload["items"])

            # Only transient failures get automatic, bounded, exponential recovery.
            for due, expected in ((5, 2), (15, 3)):
                clock[0] = due
                await service.open_orders_version()
                await _open_orders_eventually(lambda: count(failed_key) == expected and job.sources[failed_key]["state"] == "failed")
                assert count(definitive_key) == 1
            clock[0] = 100
            for _ in range(10):
                await service.list_open_orders()
            assert count(failed_key) == 3 and count(definitive_key) == 1
            assert len(job.payload["errors"]) == 2

            rate_limit[0] = False
            await service.list_open_orders(force=True)
            await asyncio.wait_for(recovering.wait(), 2)
            assert job.sources[failed_key]["state"] == "pending"
            assert failed_key in {error["source_key"] for error in job.payload["errors"]}
            assert not reconciled
            recovery.set()
            await _open_orders_eventually(lambda: job.sources[failed_key]["state"] == "complete" and job.sources[definitive_key]["state"] == "failed")
            assert {error["source_key"] for error in job.payload["errors"]} == {definitive_key}
            assert count(failed_key) == 4 and count(definitive_key) == 2
            for index, mode, path, params in requests:
                if (index, mode, path, str(params)) in healthy_reads:
                    assert requests.count((index, mode, path, params)) == healthy_reads[(index, mode, path, str(params))]
            assert not reconciled and not job.payload["empty_confirmed"]

            # Pagination/lifecycle safeguards from the former cutoff regression still hold.
            async def repeated_page(**kwargs):
                return {"result": {"list": [{"orderId": "page-one", "symbol": "ETHUSDC"}], "nextPageCursor": "same"}}
            monkeypatch.setattr(service, "_bybit_signed_get", repeated_page)
            token = service._OPEN_ORDERS_READ_SCOPE.set(True)
            try:
                rows, errors = await service._fetch_bybit_orders_for_category(base_url="https://fixture.test",
                    api_key="KEY", api_secret="SECRET", category="option", account_context="demo")
            finally:
                service._OPEN_ORDERS_READ_SCOPE.reset(token)
            assert [row["orderId"] for row in rows] == ["page-one"] and "did not progress" in str(errors)
            position = {"type": "Position", "account": "demo", "category": "linear", "instrument": "BTCUSDT",
                        "position_idx": 0, "side": "Buy", "size": 1,
                        "opened_at": int(service.time.time() * 1000) - 10000, "settlement_currency": "USDT"}
            service._BYBIT_OPEN_FUNDING_CACHE.clear()
            warnings = await service._enrich_bybit_open_position_funding([position], base_url="https://fixture.test",
                       api_key="KEY", api_secret="SECRET", account_context="demo")
            assert "did not progress" in str(warnings) and position["net_funding"] is None
            assert not service._BYBIT_OPEN_FUNDING_CACHE
            async def executions(**kwargs):
                return [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": "1",
                         "execTime": str(position["opened_at"])}]
            monkeypatch.setattr(service, "_fetch_bybit_executions_chunked", executions)
            monkeypatch.setattr(service, "_fetch_bybit_transaction_log", repeated_page)
            warnings = await service._enrich_bybit_open_position_funding([position], base_url="https://fixture.test",
                       api_key="KEY", api_secret="SECRET", account_context="demo")
            assert "did not progress" in str(warnings) and position["net_funding"] is None
            assert not service._BYBIT_OPEN_FUNDING_CACHE
        finally:
            await service._shutdown_open_orders_jobs()
            assert all(client.is_closed for client in clients)
    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_open_orders_routine_refresh_keeps_recent_rows_and_source_failures_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    service = master_service

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
        monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 300})
        monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
        # Keep this source-lifecycle regression synthetic, with no reconciliation/state writes.
        reconciled = []
        def payload(items, errors, warnings, version, complete):
            reconciled.append(complete)
            return {"items": items, "errors": errors, "warnings": warnings,
                    "version": version, "updated_at": "fixture"}
        monkeypatch.setattr(service, "_open_orders_payload", payload)
        owner = service._OpenOrdersService()
        job = service._OpenOrdersJob(owner, [], "fixture", 300)
        owner.job = job
        gate = asyncio.Event()
        failing = [False]
        healthy_calls = [0]
        row = {"broker": "Bybit", "account": "demo", "category": "linear",
               "instrument": "BTCUSDT", "type": "Order", "id": "accepted"}
        async def healthy():
            healthy_calls[0] += 1
            await gate.wait()
            return {"items": [row]}
        failure = {"message": "System under maintenance, please try again later.",
                   "http_status": "503", "transient": True}
        retry = asyncio.Event()
        async def affected():
            await retry.wait()
            return {"items": [], "errors": [failure]} if failing[0] else {"items": [dict(row, broker="OANDA", account="live", id="retained-oanda")]}
        metadata = {"broker": "Bybit", "account": "demo", "category": "linear",
                    "source_type": "orders", "endpoint": "/v5/order/realtime"}
        good = job.add("healthy", metadata, healthy)
        bad = job.add("affected", dict(metadata, broker="OANDA", account="live",
                      account_id="account-one", endpoint="/v3/accounts/account-one/pendingOrders"), affected)
        try:
            job.publish()
            assert job.payload["pending"] and not job.payload["empty_confirmed"]
            assert not job.payload["core_complete"] and not job.payload["items"]
            gate.set()
            retry.set()
            await _open_orders_eventually(lambda: not job.pending)
            assert job.payload["core_complete"] and not job.payload["warnings"]
            last_success = good["last_success_at"]
            for now in (60.0, 120.0):
                gate.clear()
                clock[0] = now
                job.poll()
                retained = job.payload["items"][0]
                assert retained["source_pending"] and not retained["source_stale"]
                assert retained["source_last_success_at"] == last_success
                assert not job.payload["warnings"] and not job.payload["empty_confirmed"]
                assert not job.payload["core_complete"] and not reconciled[-1]
                gate.set()
                await _open_orders_eventually(lambda: good["state"] == "complete")
                last_success = good["last_success_at"]

            failing[0] = True
            job.launch(bad)
            await _open_orders_eventually(lambda: bad["state"] == "failed")
            errors = job.payload["errors"]
            bad_success = bad["last_success_at"]
            failed_row = next(item for item in job.payload["items"] if item["id"] == "retained-oanda")
            assert failed_row["source_stale"] and failed_row["source_last_success_at"] == bad_success
            warnings = job.payload["warnings"]
            retry.clear()
            clock[0] = bad["next_retry"]
            job.poll()
            assert bad["state"] == "pending" and job.payload["errors"] == errors
            assert bad["last_success_at"] == bad_success
            assert job.payload["warnings"] == warnings
            assert next(item for item in job.payload["items"] if item["id"] == "retained-oanda")["source_stale"]
            # An unrelated successful publication cannot clear this source's failure.
            job.launch(good)
            await _open_orders_eventually(lambda: good["state"] == "complete")
            assert job.payload["errors"] == errors and not job.payload["empty_confirmed"]
            failing[0] = False
            retry.set()
            await _open_orders_eventually(lambda: bad["state"] == "complete")
            assert not job.payload["errors"]

            # Age changes provenance without canceling a legitimate delayed read.
            gate.clear()
            clock[0] = good["last_success"] + service._OPEN_ORDERS_STALE_AFTER_SECONDS + 1
            job.poll()
            aged = job.payload["items"][0]
            assert aged["source_pending"] and aged["source_stale"] and job.payload["warnings"]
            assert aged["source_last_success_at"] == good["last_success_at"]
            assert not job.payload["empty_confirmed"] and good["task"] is not None
            gate.set()
            await _open_orders_eventually(lambda: good["state"] == "complete")
            assert not job.payload["warnings"] and not job.payload["items"][0]["source_stale"]
            assert healthy_calls[0] == 5
        finally:
            await job.close()
        assert not job.tasks and not job.clients

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_open_orders_browser_preserves_pending_data_and_confirms_matching_submit() -> None:
    node = shutil.which("node")
    assert node, "node is required for the Open Orders browser regression"
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';this.handlers={};this.open=false;this.writes=0;}
  set innerHTML(value){this.children=[];this.writes++;}
  appendChild(value){this.children.push(value);}
  addEventListener(event,handler){this.handlers[event]=handler;}
  querySelector(){return null;}
}
const badge=new Element(),tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const empty=new Element(),elements={'open-orders-status':badge,'open-orders-table':table,'refresh-btn':new Element(),
'open-orders-empty':empty,'open-orders-errors':new Element(),'open-orders-warnings':new Element()};
for(const id of ['open-orders-errors','open-orders-warnings']){
  const box=elements[id];box.list=new Element();box.summary=new Element();
  box.querySelector=selector=>selector==='ul'?box.list:selector==='summary'?box.summary:null;
}
const handlers={};let virtual=0;
global.performance={now:()=>virtual};
global.window={__OPEN_ORDERS_TESTING__:true,__OPEN_ORDERS_GET_TIMEOUT_MS__:25,addEventListener(){}};
global.document={hidden:false,getElementById:id=>elements[id]||null,createElement:()=>new Element(),
addEventListener:(key,fn)=>{handlers[key]=fn;}};
global.BroadcastChannel=undefined;global.localStorage={setItem(){}};
global.setInterval=()=>1;global.clearInterval=()=>{};
const timers=new Set(),nativeTimeout=setTimeout,nativeClear=clearTimeout;
global.setTimeout=(fn,delay)=>{const timer=nativeTimeout(()=>{timers.delete(timer);fn();},delay);timers.add(timer);return timer;};
global.clearTimeout=timer=>{timers.delete(timer);nativeClear(timer);};
const flush=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const reply=payload=>({ok:true,status:200,statusText:'OK',text:async()=>JSON.stringify(payload)});
let revision=1,mode='healthy',aborts=0,mutations=0,active=0,maxActive=0;
const urls=[];
const unrelated={broker:'Bybit',account:'live',category:'inverse',source_key:'live-failed',message:'upstream unavailable'};
const matching={broker:'Bybit',account:'demo',category:'linear',type:'Order',instrument:'BTCUSDT',
id:'submitted',status:'New',collection_version:1,source_key:'demo-healthy',source_stale:false};
let snapshot={version:1,progress_revision:revision,items:[],errors:[],warnings:[],pending:true,core_complete:false,
empty_confirmed:false,sources:[{state:'pending'}]};
global.fetch=async(url,options={})=>{
  urls.push(String(url));if(options.method&&options.method!=='GET')mutations++;
  if(String(url).includes('/version'))return reply({version:1,progress_revision:revision,pending:snapshot.pending});
  if(mode==='body'){
    active++;maxActive=Math.max(maxActive,active);
    return {ok:true,status:200,statusText:'OK',text:()=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>{
      active--;aborts++;reject(new Error('aborted'));},{once:true}))};
  }
  return reply(snapshot);
};
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  const hooks=window.__openOrdersTestHooks;await flush();
  const initial={text:empty.textContent,state:hooks.getState(),badge:badge.textContent};
  const event={type:'state-changed',action:'submit',eventId:'one',broker:'bybit',account:'demo',category:'linear',
orderId:'submitted',symbol:'BTCUSDT',submittedAt:Date.now()};
  const freshDifferent={...matching,id:'different',order_link_id:'different-link',
calculation_context_id:'different-context',opened_at:event.submittedAt};
  const identityChecks=['orderId','orderLinkId','calculationContextId'].map(key=>{
    const supplied={...event,orderId:undefined,[key]:'expected'};
    const rowKey={orderId:'id',orderLinkId:'order_link_id',calculationContextId:'calculation_context_id'}[key];
    return {mismatch:hooks.itemMatchesSubmission(freshDifferent,supplied),
match:hooks.itemMatchesSubmission({...freshDifferent,[rowKey]:'expected'},supplied)};
  });
  const fallback=hooks.itemMatchesSubmission(freshDifferent,{...event,orderId:undefined,symbol:'BTCUSDT'});
  const oneOfSeveral=hooks.itemMatchesSubmission(freshDifferent,{...event,orderLinkId:'different-link'});
  hooks.handleStateChange(event);hooks.handleStateChange(event);await flush();
  virtual=65000;
  const reads=urls.filter(url=>!url.includes('/version')).length;
  for(let i=0;i<10;i++)await hooks.pollVersion();
  const pending={text:empty.textContent,state:hooks.getState(),reads:urls.filter(url=>!url.includes('/version')).length-reads};
  revision=2;snapshot={...snapshot,progress_revision:revision,items:[{...matching,source_stale:true},{...matching,account:'live'}],errors:[unrelated]};
  await hooks.pollVersion();await flush();
  const stale={state:hooks.getState(),ids:window.__openOrdersItems.map(row=>row.id),errors:window.__openOrdersErrors,badge:badge.textContent};
  const errorsBox=elements['open-orders-errors'],warningsBox=elements['open-orders-warnings'];
  const maintenance=[1,2].flatMap(account=>['positions','orders'].map(source=>({
    broker:'OANDA',account:'live',account_id:'account-'+account,source_type:source,
    source_key:'oanda-'+account+'-'+source,endpoint:'/v3/accounts/account-'+account+'/'+(source==='positions'?'openTrades':'pendingOrders'),
    http_status:'503',message:'System under maintenance, please try again later.'
  })));
  revision=3;snapshot={...snapshot,progress_revision:revision,items:[{...matching,source_pending:true}],errors:maintenance};
  await hooks.pollVersion();await flush();
  const initiallyCollapsed=!errorsBox.open;
  errorsBox.open=true;
  const detailsWrites=errorsBox.list.writes;
  const routine={state:hooks.getState(),warnings:warningsBox.style.display,rowClass:tbody.children[0]?.className||'',initiallyCollapsed,
summary:errorsBox.summary.textContent,details:errorsBox.list.children.map(li=>li.textContent)};
  revision=4;snapshot={...snapshot,progress_revision:revision};
  await hooks.pollVersion();await flush();
  const stableDetails={open:errorsBox.open,writes:errorsBox.list.writes-detailsWrites};
  revision=5;snapshot={...snapshot,progress_revision:revision,errors:[{...maintenance[0],message:'Service unavailable'}]};
  await hooks.pollVersion();await flush();
  const generic503=errorsBox.summary.textContent;
  virtual=130000;
  revision=6;snapshot={...snapshot,progress_revision:revision,items:[freshDifferent],errors:[unrelated]};
  await hooks.pollVersion();await flush();
  const mismatched={state:hooks.getState(),ids:window.__openOrdersItems.map(row=>row.id),badge:badge.textContent};
  revision=7;snapshot={...snapshot,progress_revision:revision,items:[matching],errors:[unrelated]};
  await hooks.pollVersion();await flush();
  const confirmed={state:hooks.getState(),ids:window.__openOrdersItems.map(row=>row.id),errors:window.__openOrdersErrors,badge:badge.textContent};
  mode='body';await Promise.all([hooks.refresh('manual'),hooks.refresh('manual')]);
  const failed={state:hooks.getState(),ids:window.__openOrdersItems.map(row=>row.id),text:empty.textContent,aborts};
  mode='healthy';await hooks.pollVersion();await flush();
  const recovered={state:hooks.getState(),ids:window.__openOrdersItems.map(row=>row.id)};
  document.hidden=true;
  const beforeHidden=urls.length;
  hooks.handleStateChange({...event,eventId:'two',orderId:'hidden'});
  await hooks.pollVersion();await flush();
  const hiddenCalls=urls.length-beforeHidden;
  document.hidden=false;
  revision=8;snapshot={...snapshot,progress_revision:revision,items:[{...matching,id:'hidden'}]};
  handlers.visibilitychange();await flush();
  const visible={state:hooks.getState(),badge:badge.textContent};
  revision=9;snapshot={version:1,progress_revision:revision,items:[],errors:[],warnings:[],pending:false,
core_complete:true,empty_confirmed:true,sources:[{state:'complete'}]};
  await hooks.refresh('manual');
  process.stdout.write(JSON.stringify({routine,stableDetails,generic503,identityChecks,fallback,oneOfSeveral,initial,pending,stale,mismatched,confirmed,failed,recovered,hiddenCalls,visible,
emptyText:empty.textContent,mutations,active,maxActive,timers:timers.size,forced:urls.filter(url=>url.includes('force=1')).length}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__JS_PATH__", json.dumps(str(ROOT / "render" / "static" / "open_orders.js")))
    result = subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    state = json.loads(result.stdout)
    assert state["routine"]["state"]["pendingVisibilityChecks"] == 1
    assert state["routine"]["warnings"] == "none" and state["routine"]["rowClass"] == ""
    assert state["routine"]["initiallyCollapsed"] is True
    assert state["routine"]["summary"] == "OANDA live reports maintenance \u2014 4 affected sources"
    assert len(state["routine"]["details"]) == 4
    assert all("System under maintenance" in detail for detail in state["routine"]["details"])
    assert any("account-1" in detail and "openTrades" in detail for detail in state["routine"]["details"])
    assert any("account-2" in detail and "pendingOrders" in detail for detail in state["routine"]["details"])
    assert state["stableDetails"] == {"open": True, "writes": 0}
    assert "maintenance" not in state["generic503"]
    assert state["identityChecks"] == [{"mismatch": False, "match": True}] * 3
    assert state["fallback"] is True and state["oneOfSeveral"] is True
    assert state["initial"]["text"] == "Loading broker data"
    assert "No open" not in state["pending"]["text"]
    assert state["pending"]["state"]["pendingVisibilityChecks"] == 1 and state["pending"]["reads"] == 0
    assert state["stale"]["state"]["pendingVisibilityChecks"] == 1
    assert state["stale"]["errors"][0]["source_key"] == "live-failed"
    assert state["mismatched"]["state"]["pendingVisibilityChecks"] == 1
    assert state["mismatched"]["ids"] == ["different"]
    assert "now visible" not in state["mismatched"]["badge"]
    assert state["confirmed"]["state"]["pendingVisibilityChecks"] == 0
    assert state["confirmed"]["ids"] == ["submitted"] and "now visible" in state["confirmed"]["badge"]
    assert state["confirmed"]["errors"][0]["source_key"] == "live-failed"
    assert state["failed"]["aborts"] == 1 and not state["failed"]["state"]["refreshInFlight"]
    assert state["failed"]["ids"] == ["submitted"] and state["recovered"]["ids"] == ["submitted"]
    assert state["hiddenCalls"] == 0 and state["visible"]["state"]["pendingVisibilityChecks"] == 0
    assert "now visible" in state["visible"]["badge"]
    assert state["emptyText"] == "No open orders or positions."
    assert state["mutations"] == 0 and state["active"] == 0 and state["maxActive"] == 1
    assert state["timers"] == 0 and state["forced"] == 2



def test_open_orders_metadata_survives_reload_and_rejects_unrelated_contexts(monkeypatch, tmp_path):
    from test_master_service_calculator_api import _phase3_accepted_context_fixture
    service = master_service
    cases, posts = _phase3_accepted_context_fixture(monkeypatch, tmp_path, service)
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 400, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([], "metadata-fixture"))

    async def scenario():
        for quote, payload in cases:
            accepted = json.loads((await service.calculator_submit(payload)).body)
            assert accepted["ok"], accepted
        assert len(posts) == 4
        # Force a disk reload, rather than reusing a quote/context object in memory.
        service._TRADE_CONTEXTS_CACHE.clear()
        contexts = service._load_trade_contexts()
        assert len(contexts) == 4 and all(row.get("order_id") for row in contexts)
        now = int(service.time.time() * 1000)
        opened = now - 30000
        async def positions(**kwargs):
            return ([{"symbol": "BTCUSDT", "size": ".01", "side": "Buy", "avgPrice": "100",
                      "positionIdx": 0, "createdTime": str(opened), "settleCoin": "USDT"}], [])
        async def orders(**kwargs):
            return ([{"symbol": "BTCUSDT", "qty": ".01", "side": "Buy", "orderId": "bb-live",
                      "orderLinkId": "link-live", "orderStatus": "New", "orderType": "Limit",
                      "positionIdx": 0, "settleCoin": "USDT", "createdTime": str(opened)}], [])
        monkeypatch.setattr(service, "_fetch_bybit_positions_for_category", positions)
        monkeypatch.setattr(service, "_fetch_bybit_orders_for_category", orders)
        monkeypatch.setattr(service, "_confirm_bybit_position_still_open", lambda **k: asyncio.sleep(0, result=True))
        async def oanda(**kwargs):
            if kwargs["endpoint"].endswith("openTrades"):
                return {"trades": [{"id": "trade-demo", "instrument": "EUR_USD", "currentUnits": "10",
                                    "price": "1.1", "openTime": service._utc_now_iso()}]}
            return {"orders": [{"id": "oa-live", "instrument": "EUR_USD", "units": "10",
                                "price": "1.1", "type": "LIMIT", "state": "PENDING"}]}
        monkeypatch.setattr(service, "_fetch_oanda_json", oanda)
        bybit_position = (await service._collect_bybit_open_items(base_url="https://bybit.test",
            api_key="key-demo", api_secret="secret", account_context="demo", skip_auth=True,
            position_categories=["linear"], order_categories=[], enrich_funding=False))["items"][0]
        assert not bybit_position["context_linked"] and bybit_position["stop_loss_ticks"] is None
        bybit_order = (await service._collect_bybit_open_items(base_url="https://bybit.test",
            api_key="key-live", api_secret="secret", account_context="live", skip_auth=True,
            position_categories=[], order_categories=["linear"], enrich_funding=False))["items"][0]
        oa_position = (await service._collect_oanda_open_items(base_url="https://oanda.test",
            account_id="account-demo", api_key="token", account_context="demo", source_type="positions"))["items"][0]
        oa_order = (await service._collect_oanda_open_items(base_url="https://oanda.test",
            account_id="account-live", api_key="token", account_context="live", source_type="orders"))["items"][0]
        assert oa_position["trade_id"] == "trade-demo" and oa_position["account_id"] == "account-demo"
        for row in (bybit_order, oa_position, oa_order):
            assert row["context_linked"] and row["stop_loss_ticks"] == "35"
            assert row["risk_value"] == "10" and row["is_test_trade"] == "No"
            assert row["pattern"] == "Channel" and row["timeframe"] == "15MIN"
        assert bybit_order["order_type"] == "Limit" and bybit_order["target_mode_display"] == "Ticks"
        assert bybit_order["take_profit_ticks"] == "70" and bybit_order["risk_reward"] is None
        assert bybit_order["risk_value_display"] == "10%" and bybit_order["risk_unit"] == "%"
        assert oa_position["order_type"] == "Market" and oa_position["risk_value_display"] == "AUD 10"
        assert oa_position["risk_unit"] == "AUD" and oa_order["risk_reward"] == "2"
        assert oa_order["target_mode_display"] == "Risk/Reward" and oa_order["take_profit_ticks"] is None
        for changed in (dict(bybit_order, account="demo"), dict(bybit_order, order_link_id="wrong"),
                        dict(oa_position, account_id="another-account"), dict(bybit_order, side="Sell"),
                        dict(bybit_order, category="inverse"), dict(bybit_order, settlement_currency="USDC")):
            changed.pop("calculation_context_id", None)
            assert not service._enrich_open_item_with_trade_context(changed)["context_linked"]
        demo_context = next(row for row in contexts if row.get("calculation_context_id") == "ctx-bybit-demo")
        unrelated = dict(demo_context, calculation_context_id="unrelated-recent", order_id="another-order",
                         order_link_id="another-link", risk_value="1000")
        unrelated.pop("context_id", None)
        service._upsert_trade_context(unrelated)
        unknown = dict(broker="Bybit", account="demo", category="linear", instrument="BTCUSDT",
                       side="Buy", type="Order", id="broker-only", order_type="Limit", opened_at=opened)
        assert not service._enrich_open_item_with_trade_context(unknown)["context_linked"]
        assert unknown["order_type"] == "Limit" and unknown["risk_value"] is None

        execution_rows = [{"symbol": "BTCUSDT", "orderId": "bb-demo", "execId": "fill-bb",
                           "positionIdx": 0, "side": "Buy", "execQty": ".01", "execTime": str(opened)}]
        monkeypatch.setattr(service, "_fetch_bybit_executions_chunked", lambda **k: asyncio.sleep(0, result=list(execution_rows)))
        # Metadata from successful execution reads is independent of unavailable funding.
        async def funding_unavailable(**kwargs):
            raise ValueError("fixture optional funding unavailable")
        monkeypatch.setattr(service, "_fetch_bybit_open_funding_transactions_cached", funding_unavailable)
        enriched_position = dict(bybit_position)
        warnings = await service._enrich_bybit_open_position_funding([enriched_position],
            base_url="https://bybit.test", api_key="key-demo", api_secret="secret", account_context="demo")
        assert warnings and enriched_position["funding_paid"] is None
        assert enriched_position["context_linked"] and enriched_position["order_type"] == "Market"
        assert enriched_position["risk_value_display"] == "AUD 10" and enriched_position["risk_reward"] == "2"
        assert enriched_position["take_profit_ticks"] is None
        hedge = dict(bybit_position, position_idx=1)
        assert not service._enrich_open_item_with_trade_context(dict(hedge, raw_refs=enriched_position["raw_refs"]))["context_linked"]
        # A second contributing order with different calculator intent is not a newest-context choice.
        other = dict(demo_context, calculation_context_id="second-demo", order_id="second-order",
                     order_link_id="second-link", risk_value="20")
        other.pop("context_id", None)
        service._upsert_trade_context(other)
        execution_rows[0]["execQty"] = ".005"
        execution_rows.append(dict(execution_rows[0], orderId="second-order", execId="fill-second"))
        ambiguous = dict(bybit_position)
        await service._enrich_bybit_open_position_funding([ambiguous], base_url="https://bybit.test",
            api_key="key-demo", api_secret="secret", account_context="demo")
        assert not ambiguous["context_linked"] and ambiguous["risk_value"] is None
        assert ambiguous["context_link_reason"] == "Conflicting calculator contexts"

        # Actual incremental payload publication accepts late exact enrichment but retains pending provenance.
        owner = service._OpenOrdersService()
        job = service._OpenOrdersJob(owner, [], "metadata-fixture", 400)
        owner.job = job
        gate = asyncio.Event()
        async def core_read():
            await gate.wait()
            return {"items": [bybit_position, bybit_order, oa_position, oa_order]}
        core = job.add("core", {"broker": "Bybit", "account": "demo", "source_type": "orders"}, core_read, ready=False)
        core.update(state="complete", items=[bybit_position, bybit_order, oa_position, oa_order],
                    previous=[bybit_position, bybit_order, oa_position, oa_order], updated=0.0,
                    last_success=0.0, last_success_at="fixture")
        late = job.add("late", {"broker": "Bybit", "account": "demo", "source_type": "settled funding"},
                       core_read, core=False, ready=False)
        late.update(state="failed", items=[enriched_position], warnings=warnings, updated=0.0)
        try:
            job.publish()
            payload = await owner.snapshot()
            pos = next(row for row in payload["items"] if row.get("type") == "Position" and row["broker"] == "Bybit")
            assert pos["context_linked"] and pos["stop_loss_ticks"] == "35" and not pos["source_stale"]
            assert pos["funding_paid"] is None and not payload["empty_confirmed"]
            assert (await owner.snapshot(deliberate=True))["items"] == payload["items"]
            job.launch(core)
            clock[0] = 1
            pending = await owner.snapshot(deliberate=True)
            retained = next(row for row in pending["items"] if row.get("type") == "Position" and row["broker"] == "Bybit")
            assert retained["source_pending"] and not retained["source_stale"]
            assert retained["stop_loss_ticks"] == "35" and retained["risk_value_display"] == "AUD 10"
            assert pending["pending"] and not pending["core_complete"] and not pending["empty_confirmed"]
            # These flags remain ineligible for the existing strict browser confirmation predicate.
            assert retained["source_pending"] or retained["source_stale"]
        finally:
            await job.close()
        assert len(posts) == 4  # Snapshot/enrichment/Refresh never resubmits a broker order.
    asyncio.run(asyncio.wait_for(scenario(), 15))



def test_open_orders_publishes_accepted_calculator_metadata_through_live_source_pipeline(monkeypatch, tmp_path):
    service = master_service
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "accepted-contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 700, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_record_outbound_traffic", lambda *a, **k: None)
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    phase = [1]
    config = {"base_url": "https://bybit.fixture", "api_key": "fixture", "api_secret": "fixture", "key_source": "fixture"}
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([("Bybit", "demo", config)], "fixture"))
    opened = int(service.time.time() * 1000) - 60000
    context = {
        "broker": "bybit", "asset": "crypto", "account": "demo", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "position_idx": 0, "settlement_currency": "USDT",
        "calculation_context_id": "accepted-quote", "order_id": "accepted-order", "order_link_id": "accepted-link",
        "order_type": "market", "stop_loss_ticks": "5000", "target_mode": "rr", "risk_reward": "2.5",
        "risk_mode": "percent", "risk_value": "1.25", "timeframe": "5MIN", "is_test_trade": 0,
        "setup": "Pullback", "pattern": "channel", "ema": "9", "vwap": "No",
        "aths_atls": "All-Time Low", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
        "quantity": ".005", "status": "ACTIVE",
    }
    service._upsert_calculator_trade_context(context, invalidate_cache=False)
    service._TRADE_CONTEXTS_CACHE.clear()  # The scheduled readers must load durable data.
    calls = {"executions": 0, "funding": 0}
    expected = {
        "broker": "Bybit", "account": "demo", "asset": "crypto", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "size": 0.005, "entry_price": "81541.6",
        "stop_loss": "81041.6", "take_profit": "82835.6",
        "order_type": "Market", "stop_loss_ticks": "5000", "target_mode": "rr",
        "target_mode_display": "Risk/Reward", "risk_reward": "2.5", "risk_mode": "percent",
        "risk_mode_display": "Percent", "risk_value": "1.25", "risk_value_display": "1.25%", "risk_unit": "%",
        "timeframe": "5MIN", "is_test_trade": "No", "setup": "Pullback", "pattern": "Channel", "ema": "9",
        "vwap": "No", "aths_atls": "All-Time Low", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
    }

    async def scenario():
        executions_ready, execution_gate = asyncio.Event(), asyncio.Event()
        funding_ready, funding_gate = asyncio.Event(), asyncio.Event()
        async def auth(**kwargs): return {}
        async def positions(**kwargs):
            if kwargs["category"] != "linear" or service._OPEN_ORDERS_SETTLEMENT_SCOPE.get() != "USDT":
                return [], []
            return [{"symbol": "BTCUSDT", "side": "Buy", "positionIdx": 0, "size": ".005",
                     "createdTime": str(opened if phase[0] == 1 else opened + 30000),
                     "avgPrice": "81541.6", "markPrice": "81550", "stopLoss": "81041.6", "takeProfit": "82835.6", "settleCoin": "USDT"}], []
        async def orders(**kwargs): return [], []
        async def executions(**kwargs):
            calls["executions"] += 1
            executions_ready.set()
            await execution_gate.wait()
            return [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "execQty": ".005",
                     "execTime": str(opened), "execId": "actual-fill", "orderId": "accepted-order"}]
        async def funding(**kwargs):
            calls["funding"] += 1
            funding_ready.set()
            await funding_gate.wait()
            raise ValueError("fixture optional funding unavailable")
        monkeypatch.setattr(service, "_bybit_read_only_auth_preflight", auth)
        monkeypatch.setattr(service, "_fetch_bybit_positions_for_category", positions)
        monkeypatch.setattr(service, "_fetch_bybit_orders_for_category", orders)
        monkeypatch.setattr(service, "_confirm_bybit_position_still_open", lambda **k: asyncio.sleep(0, result=True))
        monkeypatch.setattr(service, "_fetch_bybit_executions_chunked", executions)
        monkeypatch.setattr(service, "_fetch_bybit_open_funding_transactions_cached", funding)
        async def prohibited(*args, **kwargs):
            raise AssertionError("No network, notifications, or broker submissions are permitted")
        monkeypatch.setattr(service, "_bybit_signed_post", prohibited)
        monkeypatch.setattr(service.httpx.AsyncClient, "request", prohibited)

        async def snapshot(force=False):
            return json.loads((await service.list_open_orders(force=force)).body)
        def assert_metadata(payload):
            row = next(item for item in payload["items"] if item.get("broker") == "Bybit" and item.get("type") == "Position")
            for field, value in expected.items():
                assert row.get(field) == value, (field, row.get(field), value)
            assert row["take_profit_ticks"] is None  # Not supplied/applicable in the RR snapshot.
            assert all(row.get(field) is None for field in ("funding_paid", "funding_received", "net_funding"))
            return row
        rendered_payloads = []
        try:
            await snapshot()
            owner = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()]
            job = owner.job
            await asyncio.wait_for(executions_ready.wait(), 2)
            initial = await snapshot()
            assert len(initial["items"]) == 1
            assert not initial["items"][0]["context_linked"] and calls == {"executions": 1, "funding": 0}
            execution_gate.set()
            await asyncio.wait_for(funding_ready.wait(), 2)
            pending = await snapshot()
            row = assert_metadata(pending)
            assert pending["pending"] and not pending["empty_confirmed"] and not pending["errors"]
            assert job.sources["Bybit/demo/funding"]["state"] == "pending"
            assert job.sources["Bybit/demo/funding"]["task"] is not None
            assert job.metrics["enrichment_progress_publications"] == 1
            assert pending["progress_revision"] > initial["progress_revision"] and pending["version"] == initial["version"]
            rendered_payloads.append(pending)
            # Ordinary GET and Refresh join owned work; sparse persistence/reload cannot erase selections.
            service._upsert_calculator_trade_context(dict(context, setup="", pattern="", ema=None, risk_value=""), invalidate_cache=False)
            service._TRADE_CONTEXTS_CACHE.clear()
            for deliberate in (False, True, False):
                assert_metadata(await snapshot(deliberate))
            assert calls == {"executions": 1, "funding": 1}
            funding_gate.set()
            await _open_orders_eventually(lambda: job.sources["Bybit/demo/funding"]["state"] == "failed")
            failed = await snapshot()
            assert_metadata(failed)
            assert failed["warnings"] and calls == {"executions": 1, "funding": 1}
            rendered_payloads.append(failed)
            # A normal core reread retains metadata and cannot falsely confirm a Submit.
            core = job.sources["Bybit/demo/linear/USDT/positions"]
            gate = asyncio.Event()
            original_reader = core["reader"]
            async def delayed_core():
                await gate.wait()
                return await original_reader()
            core["reader"] = delayed_core
            job.launch(core)
            retained = await snapshot()
            retained_row = assert_metadata(retained)
            assert retained_row["source_pending"] and not retained_row["source_stale"]
            rendered_payloads.append(retained)
            gate.set()
            await _open_orders_eventually(lambda: core["task"] is None)

            # Same symbol/account/index but a later broker lifecycle cannot reuse the old fill.
            phase[0] = 2
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            await snapshot()
            replacement = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: replacement.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            new = await snapshot()
            new_row = next(item for item in new["items"] if item.get("type") == "Position")
            assert str(new_row["opened_at"]) != str(row["opened_at"])
            assert not new_row["context_linked"] and new_row["setup"] is None and new_row["risk_value"] is None
        finally:
            await service._shutdown_open_orders_jobs()
        return rendered_payloads

    payloads = asyncio.run(asyncio.wait_for(scenario(), 15))
    node = shutil.which("node")
    assert node
    # Execute the unchanged production renderer against actual API snapshots from the scheduled pipeline.
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';}
  set textContent(value){this._text=String(value);}
  get textContent(){return this._text;}
  set innerHTML(value){this.children=[];}
  appendChild(child){this.children.push(child);}
  addEventListener(){}
  querySelector(){return null;}
}
const tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const elements={'open-orders-table':table};
for(const id of ['open-orders-status','open-orders-empty','refresh-btn','open-orders-errors','open-orders-warnings'])elements[id]=new Element();
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(){}};
global.document={hidden:true,getElementById:id=>elements[id]||null,createElement:()=>new Element(),addEventListener(){}};
global.BroadcastChannel=undefined;global.setInterval=()=>1;global.clearInterval=()=>{};
const payloads=__PAYLOADS__;let current=payloads[0];
global.fetch=async url=>({ok:true,text:async()=>JSON.stringify(String(url).includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current)});
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const rows=[];
  for(const payload of payloads){
    current=payload;
    if(payload.items[0].source_pending)window.__openOrdersTestHooks.handleStateChange({
      type:'state-changed',action:'submit',eventId:'retained',broker:'bybit',account:'demo',
      category:'linear',orderId:'accepted-order',calculationContextId:'accepted-quote',submittedAt:Date.now()});
    await window.__openOrdersTestHooks.refresh('progress');
    rows.push(tbody.children[0].children.map(cell=>cell.textContent));
  }
  process.stdout.write(JSON.stringify({rows,pendingConfirmations:window.__openOrdersTestHooks.getState().pendingVisibilityChecks}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__PAYLOADS__", json.dumps(payloads)).replace("__JS_PATH__", json.dumps(str(ROOT / "render/static/open_orders.js")))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    headers = re.findall(r"<th>(.*?)</th>", service.OPEN_ORDERS_TEMPLATE)
    cells = {
        "Broker / Venue": "Bybit", "Account": "demo", "Asset": "crypto", "Category": "linear",
        "Instrument": "BTCUSDT", "Side": "Buy", "Size": "0.005", "Entry / Order": "81541.6",
        "Stop Loss": "81041.6", "Take Profit": "82835.6",
        "Order Type": "Market", "Stop-loss Ticks": "5000", "Target Mode": "Risk/Reward",
        "Risk / Reward": "2.5", "Risk Mode": "Percent", "Risk Value": "1.25%", "Timeframe": "5MIN",
        "Test": "No", "Setup": "Pullback", "Pattern": "Channel", "EMA": "9", "VWAP": "No",
        "ATH / ATL": "All-Time Low", "Round Number": "Yes", "Webhook Mode": "No", "Webhook Status": "Not used",
        "Planned Entry": "81541.6", "Planned Stop": "81041.6", "Planned Target": "82835.6",
    }
    rendered_state = json.loads(result.stdout)
    assert rendered_state["pendingConfirmations"] == 1
    for rendered in rendered_state["rows"]:
        row = dict(zip(headers, rendered))
        for column, value in cells.items():
            assert row[column] == value, (column, row[column], value)
        assert row["Take-profit Ticks"] == "\u2014"
        assert row["Funding paid"] == row["Funding received"] == row["Net funding"] == "\u2014"


def test_open_orders_links_realistic_demo_position_to_accepted_calculator_context(monkeypatch, tmp_path):
    service = master_service
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "accepted-contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 700, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_record_outbound_traffic", lambda *a, **k: None)
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    phase = [1]
    mark = ["81550"]
    config = {"base_url": "https://bybit.fixture", "api_key": "fixture", "api_secret": "fixture", "key_source": "fixture"}
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([("Bybit", "demo", config)], "fixture"))
    opened = int(service.time.time() * 1000) - 60000
    context = {
        "broker": "bybit", "asset": "crypto", "account": "demo", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "position_idx": 0, "settlement_currency": "USDT",
        "calculation_context_id": "accepted-quote", "order_id": "accepted-order", "order_link_id": "accepted-link",
        "order_type": "market", "stop_loss_ticks": "5000", "target_mode": "rr", "risk_reward": "2",
        "risk_mode": "percent", "risk_value": "1", "timeframe": "5MIN", "is_test_trade": True,
        "setup": "Pullback", "pattern": "range", "ema": "9", "vwap": "Yes",
        "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
        "quantity": ".005", "status": "ACTIVE",
    }
    service._upsert_calculator_trade_context(context, invalidate_cache=False)
    service._TRADE_CONTEXTS_CACHE.clear()  # The scheduled readers must load durable data.
    calls = {"executions": 0, "funding": 0}
    ranges, clients = [], []
    monkeypatch.setattr(service, "_BYBIT_TIME_OFFSET_CACHE", {})
    monkeypatch.setattr(service, "_BYBIT_OPEN_FUNDING_CACHE", {})
    expected = {
        "broker": "Bybit", "account": "demo", "asset": "crypto", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "size": 0.005, "entry_price": "81541.6",
        "stop_loss": "81041.6", "take_profit": "82835.6",
        "order_type": "Market", "stop_loss_ticks": "5000", "target_mode": "rr",
        "target_mode_display": "Risk/Reward", "risk_reward": "2", "risk_mode": "percent",
        "risk_mode_display": "Percent", "risk_value": "1", "risk_value_display": "1%", "risk_unit": "%",
        "timeframe": "5MIN", "is_test_trade": "Yes", "setup": "Pullback", "pattern": "Range", "ema": "9",
        "vwap": "Yes", "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
    }

    async def scenario():
        executions_ready, execution_gate = asyncio.Event(), asyncio.Event()
        funding_ready, funding_gate = asyncio.Event(), asyncio.Event()
        async def auth(**kwargs): return {}
        real_client = service.httpx.AsyncClient
        async def transport(request):
            assert request.method == "GET", "Broker mutations are prohibited"
            path, params = request.url.path, request.url.params
            rows, cursor = [], ""
            if path == "/v5/market/time":
                return service.httpx.Response(200, json={"retCode": 0, "result": {"timeSecond": str(service.time.time())}})
            elif path == "/v5/position/list":
                if params.get("category") == "linear" and (params.get("symbol") or params.get("settleCoin") == "USDT"):
                    rows = [{"symbol": "BTCUSDT", "side": "Buy", "positionIdx": 0, "size": ".005",
                             "createdTime": "1763377208045", "openTime": 0,
                             "seq": "700" if phase[0] == 1 else "900", "updatedTime": str(opened),
                             "avgPrice": "81541.6", "markPrice": mark[0], "stopLoss": "81041.6",
                             "takeProfit": "82835.6", "settleCoin": "USDT"}]
            elif path == "/v5/execution/list":
                calls["executions"] += 1
                ranges.append(dict(params))
                assert params["symbol"] == "BTCUSDT"
                assert int(params["endTime"]) - int(params["startTime"]) <= service._BYBIT_MAX_WINDOW_MS
                executions_ready.set()
                await execution_gate.wait()
                # Bybit execution rows need not contain positionIdx. createdTime is
                # first-ever symbol creation; old unmatched history is not this cycle.
                if not params.get("cursor"):
                    rows = [{"symbol": "BTCUSDT", "execType": "Funding", "side": "Sell",
                             "execQty": ".005", "execTime": str(opened + 1000), "execId": "funding-not-fill"}]
                    cursor = "page-2"
                else:
                    assert params["cursor"] == "page-2"
                    rows = [
                        {"symbol": "BTCUSDT", "execType": "Trade", "side": "Buy", "execQty": ".005",
                         "closedSize": "0", "seq": "700", "execTime": str(opened),
                         "execId": "actual-fill", "orderId": "accepted-order", "orderLinkId": "accepted-link"},
                        {"symbol": "BTCUSDT", "execType": "Trade", "side": "Sell", "execQty": ".023",
                         "closedSize": ".023", "seq": "100", "execTime": str(opened - 100000),
                         "execId": "prior-cycle-close", "orderId": "unrelated-prior-order"},
                    ]
            elif path == "/v5/account/transaction-log":
                calls["funding"] += 1
                funding_ready.set()
                await funding_gate.wait()
                # A definitive read failure stays unknown, without transport retries.
                return service.httpx.Response(200, json={"retCode": 10005, "retMsg": "fixture funding unavailable"})
            else:
                assert path == "/v5/order/realtime", path
            return service.httpx.Response(200, json={"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}})
        def client_factory(**kwargs):
            client = real_client(transport=service.httpx.MockTransport(transport), trust_env=False, **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(service, "_bybit_read_only_auth_preflight", auth)
        monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
        async def prohibited(*args, **kwargs):
            raise AssertionError("No network, notifications, or broker submissions are permitted")
        monkeypatch.setattr(service, "_bybit_signed_post", prohibited)

        async def snapshot(force=False):
            return json.loads((await service.list_open_orders(force=force)).body)
        def assert_metadata(payload):
            row = next(item for item in payload["items"] if item.get("broker") == "Bybit" and item.get("type") == "Position")
            for field, value in expected.items():
                assert row.get(field) == value, (field, row.get(field), value)
            assert row["take_profit_ticks"] is None  # Not supplied/applicable in the RR snapshot.
            assert all(row.get(field) is None for field in ("funding_paid", "funding_received", "net_funding"))
            return row
        rendered_payloads = []
        try:
            await snapshot()
            owner = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()]
            job = owner.job
            await asyncio.wait_for(executions_ready.wait(), 2)
            initial = await snapshot()
            assert len(initial["items"]) == 1
            assert not initial["items"][0]["context_linked"] and calls == {"executions": 1, "funding": 0}
            execution_gate.set()
            await asyncio.wait_for(funding_ready.wait(), 2)
            pending = await snapshot()
            row = assert_metadata(pending)
            assert row["raw_refs"]["order_ids"] == ["accepted-order"]
            assert row["raw_refs"]["order_ids_complete"] and row["context_linked"]
            assert row["execution_seq"] == "700"
            assert pending["pending"] and not pending["empty_confirmed"] and not pending["errors"]
            assert job.sources["Bybit/demo/funding"]["state"] == "pending"
            assert job.sources["Bybit/demo/funding"]["task"] is not None
            assert job.metrics["enrichment_progress_publications"] == 1
            assert pending["progress_revision"] > initial["progress_revision"] and pending["version"] == initial["version"]
            rendered_payloads.append(pending)
            # Ordinary GET and Refresh join owned work; sparse persistence/reload cannot erase selections.
            service._upsert_calculator_trade_context(dict(context, setup="", pattern="", ema=None, risk_value=""), invalidate_cache=False)
            service._TRADE_CONTEXTS_CACHE.clear()
            for deliberate in (False, True, False):
                assert_metadata(await snapshot(deliberate))
            assert calls == {"executions": 2, "funding": 1}
            # Normal price changes do not replace the source or repeat the scan.
            core = job.sources["Bybit/demo/linear/USDT/positions"]
            enrichment = job.sources["Bybit/demo/funding"]
            mark[0] = "81600"
            gate = asyncio.Event()
            original_reader = core["reader"]
            async def delayed_core():
                await gate.wait()
                return await original_reader()
            core["reader"] = delayed_core
            job.launch(core)
            retained = await snapshot()
            retained_row = assert_metadata(retained)
            assert retained_row["source_pending"] and not retained_row["source_stale"]
            rendered_payloads.append(retained)
            gate.set()
            await _open_orders_eventually(lambda: core["task"] is None)
            fresh = await snapshot()
            assert_metadata(fresh)
            assert fresh["items"][0]["current_price"] == "81600"
            assert job.sources["Bybit/demo/funding"] is enrichment
            assert enrichment["state"] == "pending" and enrichment["task"] is not None
            assert calls == {"executions": 2, "funding": 1}

            funding_gate.set()
            await _open_orders_eventually(lambda: job.sources["Bybit/demo/funding"]["state"] == "failed")
            failed = await snapshot()
            assert_metadata(failed)
            assert failed["warnings"] and calls == {"executions": 2, "funding": 1}
            rendered_payloads.append(failed)

            # Scheduled optional retry reuses proven executions within this source.
            job.launch(enrichment)
            await _open_orders_eventually(lambda: enrichment["task"] is None)
            assert_metadata(await snapshot())
            assert calls == {"executions": 2, "funding": 2}
            assert [item.get("cursor") for item in ranges] == [None, "page-2"]
            assert all(int(item["startTime"]) > 1763377208045 for item in ranges)

            # Same symbol/size/index with a new sequence must not inherit the old
            # cycle, even though Bybit createdTime never changes.
            phase[0] = 2
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            replacement = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: replacement.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            new = await snapshot()
            new_row = next(item for item in new["items"] if item.get("type") == "Position")
            assert new_row["opened_at"] == row["opened_at"]
            assert not new_row["context_linked"] and new_row["setup"] is None and new_row["risk_value"] is None
            assert not new_row["raw_refs"]["order_ids_complete"]
            assert new_row["raw_refs"]["execution_provenance_reason"] == "Position/execution sequence not reconciled"
            assert calls == {"executions": 4, "funding": 2}
            # Durable reload of the original sequence resolves the same exact context.
            phase[0] = 1
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            reloaded = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: reloaded.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            assert_metadata(await snapshot())
            assert calls == {"executions": 6, "funding": 3}
        finally:
            await service._shutdown_open_orders_jobs()
        assert clients and all(client.is_closed for client in clients)
        return rendered_payloads

    payloads = asyncio.run(asyncio.wait_for(scenario(), 15))
    node = shutil.which("node")
    assert node
    # Execute the unchanged production renderer against actual API snapshots from the scheduled pipeline.
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';}
  set textContent(value){this._text=String(value);}
  get textContent(){return this._text;}
  set innerHTML(value){this.children=[];}
  appendChild(child){this.children.push(child);}
  addEventListener(){}
  querySelector(){return null;}
}
const tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const elements={'open-orders-table':table};
for(const id of ['open-orders-status','open-orders-empty','refresh-btn','open-orders-errors','open-orders-warnings'])elements[id]=new Element();
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(){}};
global.document={hidden:true,getElementById:id=>elements[id]||null,createElement:()=>new Element(),addEventListener(){}};
global.BroadcastChannel=undefined;global.setInterval=()=>1;global.clearInterval=()=>{};
const payloads=__PAYLOADS__;let current=payloads[0];
global.fetch=async url=>({ok:true,text:async()=>JSON.stringify(String(url).includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current)});
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const rows=[],confirmations=[];
  for(const payload of payloads){
    current=payload;
    if(payload.items[0].source_pending)window.__openOrdersTestHooks.handleStateChange({
      type:'state-changed',action:'submit',eventId:'retained',broker:'bybit',account:'demo',
      category:'linear',orderId:'accepted-order',calculationContextId:'accepted-quote',submittedAt:Date.now()});
    await window.__openOrdersTestHooks.refresh('progress');
    rows.push(tbody.children[0].children.map(cell=>cell.textContent));
    confirmations.push(window.__openOrdersTestHooks.getState().pendingVisibilityChecks);
  }
  process.stdout.write(JSON.stringify({rows,confirmations}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__PAYLOADS__", json.dumps(payloads)).replace("__JS_PATH__", json.dumps(str(ROOT / "render/static/open_orders.js")))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    headers = re.findall(r"<th>(.*?)</th>", service.OPEN_ORDERS_TEMPLATE)
    cells = {
        "Broker / Venue": "Bybit", "Account": "demo", "Asset": "crypto", "Category": "linear",
        "Instrument": "BTCUSDT", "Side": "Buy", "Size": "0.005", "Entry / Order": "81541.6",
        "Stop Loss": "81041.6", "Take Profit": "82835.6",
        "Order Type": "Market", "Stop-loss Ticks": "5000", "Target Mode": "Risk/Reward",
        "Risk / Reward": "2", "Risk Mode": "Percent", "Risk Value": "1%", "Timeframe": "5MIN",
        "Test": "Yes", "Setup": "Pullback", "Pattern": "Range", "EMA": "9", "VWAP": "Yes",
        "ATH / ATL": "All-Time High", "Round Number": "Yes", "Webhook Mode": "No", "Webhook Status": "Not used",
        "Planned Entry": "81541.6", "Planned Stop": "81041.6", "Planned Target": "82835.6",
    }
    rendered_state = json.loads(result.stdout)
    assert rendered_state["confirmations"][1] == 1  # Retained/pending evidence cannot confirm Submit.
    for rendered in rendered_state["rows"]:
        row = dict(zip(headers, rendered))
        for column, value in cells.items():
            assert row[column] == value, (column, row[column], value)
        assert row["Take-profit Ticks"] == "\u2014"
        assert row["Funding paid"] == row["Funding received"] == row["Net funding"] == "\u2014"


def test_open_orders_links_accepted_fill_with_nonzero_position_open_time(monkeypatch, tmp_path):
    import ast
    service = master_service
    real_time = service.time
    class ObservedTime:
        def time(self): return 1792217032.279
        def __getattr__(self, name): return getattr(real_time, name)
    monkeypatch.setattr(service, "time", ObservedTime())
    # Execute only the two named functions from the reviewed failing delivery,
    # in this same test process, against the same observed transport response.
    baseline = subprocess.run(["git", "show", "8c80fee54bf50d58157e19046cada784b593bdcd:render/master_service.py"],
                              capture_output=True, text=True, encoding="utf-8", check=True, timeout=10).stdout
    names = {"_bybit_position_execution_proof", "_fetch_bybit_current_position_executions"}
    definitions = [node for node in ast.parse(baseline).body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(definitions) == 2
    old = dict(vars(service))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "<reviewed-openTime-boundary>", "exec"), old)
    observed_fill = {
        "orderId": "b2a6134e-3c6e-4255-83c2-0a755c20a0ff", "orderLinkId": "938d9ec7a01e4850a619b04538c972a1",
        "execId": "9b689f43-a493-4253-9935-7e0434b603e2", "symbol": "BTCUSDT", "side": "Buy",
        "execQty": "0.005", "orderQty": "0.005", "leavesQty": "0", "execPrice": "81541.6",
        "execType": "Trade", "execTime": "1792212224049", "seq": 140712176282093, "closedSize": "0",
    }
    observed_position = {
        "broker": "Bybit", "account": "demo", "category": "linear", "instrument": "BTCUSDT",
        "side": "Buy", "position_idx": 0, "size": .005, "settlement_currency": "USDT",
        "opened_at": "1763377208045", "position_open_time": 1792212224051,
        "execution_seq": 140712176282093,
    }
    assert old["_bybit_position_execution_proof"](observed_position, [observed_fill]) == (
        None, [], False, "No scoped trade executions")

    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "accepted-contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 700, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_record_outbound_traffic", lambda *a, **k: None)
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    phase = [1]
    mark = ["81550"]
    config = {"base_url": "https://bybit.fixture", "api_key": "fixture", "api_secret": "fixture", "key_source": "fixture"}
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([("Bybit", "demo", config)], "fixture"))
    opened = 1792212224049
    context = {
        "broker": "bybit", "asset": "crypto", "account": "demo", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "position_idx": 0, "settlement_currency": "USDT",
        "calculation_context_id": "calcctx_bybit_demo_133be437ac934f5d9f6c25adcfaec5f8", "order_id": "b2a6134e-3c6e-4255-83c2-0a755c20a0ff", "order_link_id": "938d9ec7a01e4850a619b04538c972a1",
        "order_type": "market", "stop_loss_ticks": "5000", "target_mode": "rr", "risk_reward": "2",
        "risk_mode": "percent", "risk_value": "1", "timeframe": "5MIN", "is_test_trade": True,
        "setup": "Pullback", "pattern": "range", "ema": "9", "vwap": "Yes",
        "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
        "quantity": ".005", "status": "ACTIVE",
    }
    service._upsert_calculator_trade_context(context, invalidate_cache=False)
    service._TRADE_CONTEXTS_CACHE.clear()  # The scheduled readers must load durable data.
    calls = {"executions": 0, "funding": 0}
    ranges, clients = [], []
    monkeypatch.setattr(service, "_BYBIT_TIME_OFFSET_CACHE", {})
    monkeypatch.setattr(service, "_BYBIT_OPEN_FUNDING_CACHE", {})
    expected = {
        "broker": "Bybit", "account": "demo", "asset": "crypto", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "size": 0.005, "entry_price": "81541.6",
        "stop_loss": "81041.6", "take_profit": "82835.6",
        "order_type": "Market", "stop_loss_ticks": "5000", "target_mode": "rr",
        "target_mode_display": "Risk/Reward", "risk_reward": "2", "risk_mode": "percent",
        "risk_mode_display": "Percent", "risk_value": "1", "risk_value_display": "1%", "risk_unit": "%",
        "timeframe": "5MIN", "is_test_trade": "Yes", "setup": "Pullback", "pattern": "Range", "ema": "9",
        "vwap": "Yes", "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
    }

    async def scenario():
        executions_ready, execution_gate = asyncio.Event(), asyncio.Event()
        funding_ready, funding_gate = asyncio.Event(), asyncio.Event()
        legacy = [True]
        async def auth(**kwargs): return {}
        real_client = service.httpx.AsyncClient
        async def transport(request):
            assert request.method == "GET", "Broker mutations are prohibited"
            path, params = request.url.path, request.url.params
            rows, cursor = [], ""
            if path == "/v5/market/time":
                return service.httpx.Response(200, json={"retCode": 0, "result": {"timeSecond": str(service.time.time())}})
            elif path == "/v5/position/list":
                if params.get("category") == "linear" and (params.get("symbol") or params.get("settleCoin") == "USDT"):
                    rows = [{"symbol": "BTCUSDT", "side": "Buy", "positionIdx": 0, "size": ".005",
                             "createdTime": "1763377208045", "openTime": 1792212224051,
                             "seq": 140712176282093 if phase[0] == 1 else 140712176282094, "updatedTime": str(opened),
                             "avgPrice": "81541.6", "markPrice": mark[0], "stopLoss": "81041.6",
                             "takeProfit": "82835.6", "settleCoin": "USDT"}]
            elif path == "/v5/execution/list":
                calls["executions"] += 1
                ranges.append(dict(params))
                assert params["symbol"] == "BTCUSDT"
                assert int(params["endTime"]) - int(params["startTime"]) <= service._BYBIT_MAX_WINDOW_MS
                if not legacy[0]:
                    executions_ready.set()
                    await execution_gate.wait()
                if int(params["startTime"]) <= int(observed_fill["execTime"]) <= int(params["endTime"]):
                    if not params.get("cursor"):
                        rows = [dict(observed_fill)]
                        cursor = "8353478%3A1%2C8353478%3A1"
                    else:
                        assert params["cursor"] == "8353478:1,8353478:1"
                else:
                    assert legacy[0] and int(params["startTime"]) == 1792212224051
                    # The exact live zero-row result: the fill is two ms earlier.
            elif path == "/v5/account/transaction-log":
                calls["funding"] += 1
                funding_ready.set()
                await funding_gate.wait()
                # A definitive read failure stays unknown, without transport retries.
                return service.httpx.Response(200, json={"retCode": 10005, "retMsg": "fixture funding unavailable"})
            else:
                assert path == "/v5/order/realtime", path
            return service.httpx.Response(200, json={"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}})
        def client_factory(**kwargs):
            client = real_client(transport=service.httpx.MockTransport(transport), trust_env=False, **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(service, "_bybit_read_only_auth_preflight", auth)
        monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
        async def prohibited(*args, **kwargs):
            raise AssertionError("No network, notifications, or broker submissions are permitted")
        monkeypatch.setattr(service, "_bybit_signed_post", prohibited)

        async def snapshot(force=False):
            return json.loads((await service.list_open_orders(force=force)).body)
        def assert_metadata(payload):
            row = next(item for item in payload["items"] if item.get("broker") == "Bybit" and item.get("type") == "Position")
            for field, value in expected.items():
                assert row.get(field) == value, (field, row.get(field), value)
            assert row["take_profit_ticks"] is None  # Not supplied/applicable in the RR snapshot.
            assert all(row.get(field) is None for field in ("funding_paid", "funding_received", "net_funding"))
            return row
        rendered_payloads = []
        try:
            original_rows = await old["_fetch_bybit_current_position_executions"](
                observed_position, base_url=config["base_url"], api_key=config["api_key"],
                api_secret=config["api_secret"], end_time=1792217032279)
            assert original_rows == []
            assert calls == {"executions": 1, "funding": 0}
            assert ranges[0]["startTime"] == "1792212224051"
            legacy[0] = False
            calls.update(executions=0, funding=0)
            ranges.clear()
            await snapshot()
            owner = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()]
            job = owner.job
            await asyncio.wait_for(executions_ready.wait(), 2)
            initial = await snapshot()
            assert len(initial["items"]) == 1
            assert not initial["items"][0]["context_linked"] and calls == {"executions": 1, "funding": 0}
            execution_gate.set()
            await asyncio.wait_for(funding_ready.wait(), 2)
            pending = await snapshot()
            row = assert_metadata(pending)
            assert row["raw_refs"]["order_ids"] == ["b2a6134e-3c6e-4255-83c2-0a755c20a0ff"]
            assert row["raw_refs"]["order_ids_complete"] and row["context_linked"]
            assert row["execution_seq"] == 140712176282093
            assert row["position_open_time"] == 1792212224051
            assert pending["pending"] and not pending["empty_confirmed"] and not pending["errors"]
            assert not any("lifecycle start could not" in warning.get("message", "") for warning in pending["warnings"])
            assert job.sources["Bybit/demo/funding"]["state"] == "pending"
            assert job.sources["Bybit/demo/funding"]["task"] is not None
            assert job.metrics["enrichment_progress_publications"] == 1
            assert pending["progress_revision"] > initial["progress_revision"] and pending["version"] == initial["version"]
            rendered_payloads.append(pending)
            # Ordinary GET and Refresh join owned work; sparse persistence/reload cannot erase selections.
            service._upsert_calculator_trade_context(dict(context, setup="", pattern="", ema=None, risk_value=""), invalidate_cache=False)
            service._TRADE_CONTEXTS_CACHE.clear()
            for deliberate in (False, True, False):
                assert_metadata(await snapshot(deliberate))
            assert calls == {"executions": 2, "funding": 1}
            # Normal price changes do not replace the source or repeat the scan.
            core = job.sources["Bybit/demo/linear/USDT/positions"]
            enrichment = job.sources["Bybit/demo/funding"]
            mark[0] = "81600"
            gate = asyncio.Event()
            original_reader = core["reader"]
            async def delayed_core():
                await gate.wait()
                return await original_reader()
            core["reader"] = delayed_core
            job.launch(core)
            retained = await snapshot()
            retained_row = assert_metadata(retained)
            assert retained_row["source_pending"] and not retained_row["source_stale"]
            rendered_payloads.append(retained)
            gate.set()
            await _open_orders_eventually(lambda: core["task"] is None)
            fresh = await snapshot()
            assert_metadata(fresh)
            assert fresh["items"][0]["current_price"] == "81600"
            assert job.sources["Bybit/demo/funding"] is enrichment
            assert enrichment["state"] == "pending" and enrichment["task"] is not None
            assert calls == {"executions": 2, "funding": 1}

            funding_gate.set()
            await _open_orders_eventually(lambda: job.sources["Bybit/demo/funding"]["state"] == "failed")
            failed = await snapshot()
            assert_metadata(failed)
            assert failed["warnings"] and calls == {"executions": 2, "funding": 1}
            assert not any("lifecycle start could not" in warning.get("message", "") for warning in failed["warnings"])
            rendered_payloads.append(failed)

            # Scheduled optional retry reuses proven executions within this source.
            job.launch(enrichment)
            await _open_orders_eventually(lambda: enrichment["task"] is None)
            assert_metadata(await snapshot())
            assert calls == {"executions": 2, "funding": 2}
            assert [item.get("cursor") for item in ranges] == [None, "8353478:1,8353478:1"]
            assert all(1763377208045 < int(item["startTime"]) <= 1792212224049 < 1792212224051
                       for item in ranges)
            assert service._bybit_position_lifecycle_start_ms(observed_position, [observed_fill]) == 1792212224049

            # Same symbol/size/index with a new sequence must not inherit the old
            # cycle, even though Bybit createdTime never changes.
            phase[0] = 2
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            replacement = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: replacement.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            new = await snapshot()
            new_row = next(item for item in new["items"] if item.get("type") == "Position")
            assert new_row["opened_at"] == row["opened_at"]
            assert not new_row["context_linked"] and new_row["setup"] is None and new_row["risk_value"] is None
            assert not new_row["raw_refs"]["order_ids_complete"]
            assert new_row["raw_refs"]["execution_provenance_reason"] == "Position/execution sequence not reconciled"
            assert calls == {"executions": 4, "funding": 2}
            # Durable reload of the original sequence resolves the same exact context.
            phase[0] = 1
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            reloaded = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: reloaded.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            assert_metadata(await snapshot())
            assert calls == {"executions": 6, "funding": 3}
        finally:
            await service._shutdown_open_orders_jobs()
        assert clients and all(client.is_closed for client in clients)
        return rendered_payloads

    payloads = asyncio.run(asyncio.wait_for(scenario(), 15))
    node = shutil.which("node")
    assert node
    # Execute the unchanged production renderer against actual API snapshots from the scheduled pipeline.
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';}
  set textContent(value){this._text=String(value);}
  get textContent(){return this._text;}
  set innerHTML(value){this.children=[];}
  appendChild(child){this.children.push(child);}
  addEventListener(){}
  querySelector(){return null;}
}
const tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const elements={'open-orders-table':table};
for(const id of ['open-orders-status','open-orders-empty','refresh-btn','open-orders-errors','open-orders-warnings'])elements[id]=new Element();
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(){}};
global.document={hidden:true,getElementById:id=>elements[id]||null,createElement:()=>new Element(),addEventListener(){}};
global.BroadcastChannel=undefined;global.setInterval=()=>1;global.clearInterval=()=>{};
const payloads=__PAYLOADS__;let current=payloads[0];
global.fetch=async url=>({ok:true,text:async()=>JSON.stringify(String(url).includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current)});
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const rows=[],confirmations=[];
  for(const payload of payloads){
    current=payload;
    if(payload.items[0].source_pending)window.__openOrdersTestHooks.handleStateChange({
      type:'state-changed',action:'submit',eventId:'retained',broker:'bybit',account:'demo',
      category:'linear',orderId:'b2a6134e-3c6e-4255-83c2-0a755c20a0ff',calculationContextId:'calcctx_bybit_demo_133be437ac934f5d9f6c25adcfaec5f8',submittedAt:Date.now()});
    await window.__openOrdersTestHooks.refresh('progress');
    rows.push(tbody.children[0].children.map(cell=>cell.textContent));
    confirmations.push(window.__openOrdersTestHooks.getState().pendingVisibilityChecks);
  }
  process.stdout.write(JSON.stringify({rows,confirmations}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__PAYLOADS__", json.dumps(payloads)).replace("__JS_PATH__", json.dumps(str(ROOT / "render/static/open_orders.js")))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    headers = re.findall(r"<th>(.*?)</th>", service.OPEN_ORDERS_TEMPLATE)
    cells = {
        "Broker / Venue": "Bybit", "Account": "demo", "Asset": "crypto", "Category": "linear",
        "Instrument": "BTCUSDT", "Side": "Buy", "Size": "0.005", "Entry / Order": "81541.6",
        "Stop Loss": "81041.6", "Take Profit": "82835.6",
        "Order Type": "Market", "Stop-loss Ticks": "5000", "Target Mode": "Risk/Reward",
        "Risk / Reward": "2", "Risk Mode": "Percent", "Risk Value": "1%", "Timeframe": "5MIN",
        "Test": "Yes", "Setup": "Pullback", "Pattern": "Range", "EMA": "9", "VWAP": "Yes",
        "ATH / ATL": "All-Time High", "Round Number": "Yes", "Webhook Mode": "No", "Webhook Status": "Not used",
        "Planned Entry": "81541.6", "Planned Stop": "81041.6", "Planned Target": "82835.6",
    }
    rendered_state = json.loads(result.stdout)
    assert rendered_state["confirmations"][1] == 1  # Retained/pending evidence cannot confirm Submit.
    for rendered in rendered_state["rows"]:
        row = dict(zip(headers, rendered))
        for column, value in cells.items():
            assert row[column] == value, (column, row[column], value)
        assert row["Take-profit Ticks"] == "\u2014"
        assert row["Funding paid"] == row["Funding received"] == row["Net funding"] == "\u2014"


def test_open_orders_funding_sequence_preserves_current_trade_metadata(monkeypatch, tmp_path):
    import ast
    service = master_service
    real_time = service.time
    class ObservedTime:
        def time(self): return 1792237659.753
        def __getattr__(self, name): return getattr(real_time, name)
    monkeypatch.setattr(service, "time", ObservedTime())
    # Execute the exact rejection helper from the reviewed failing delivery,
    # in this same test process, against the same observed transport response.
    baseline = subprocess.run(["git", "show", "97f1a3cd7cca39650d3c0697a369a643649dd7a3:render/master_service.py"],
                              capture_output=True, text=True, encoding="utf-8", check=True, timeout=10).stdout
    names = {"_bybit_position_execution_proof"}
    definitions = [node for node in ast.parse(baseline).body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(definitions) == 1
    old = dict(vars(service))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "<reviewed-openTime-boundary>", "exec"), old)
    observed_executions = [{'symbol': 'BTCUSDT',
      'orderId': 'a58cb616-665f-492e-8343-cf078baad0ac',
      'orderLinkId': '',
      'execId': 'a3d80fc5-0c40-47e7-b69a-a4408952a8c2',
      'execTime': '1792224000000',
      'seq': 140712176966582,
      'side': 'Buy',
      'execQty': '0.005',
      'closedSize': '0',
      'execType': 'Funding',
      'execPrice': '81704.3',
      'execFee': '0.02094899',
      'feeRate': '0.000051'},
     {'symbol': 'BTCUSDT',
      'orderId': 'b2a6134e-3c6e-4255-83c2-0a755c20a0ff',
      'orderLinkId': '938d9ec7a01e4850a619b04538c972a1',
      'execId': '9b689f43-a493-4253-9935-7e0434b603e2',
      'execTime': '1792212224049',
      'seq': 140712176282093,
      'side': 'Buy',
      'execQty': '0.005',
      'closedSize': '0',
      'execType': 'Trade',
      'execPrice': '81541.6',
      'execFee': '0.2242394',
      'feeRate': '0.00055'},
     {'symbol': 'BTCUSDT',
      'orderId': '71b132c2-9792-428e-87d7-43824d57c813',
      'orderLinkId': 'None',
      'execId': '0ff4144c-a429-42fe-88a1-906c98c9062d',
      'execTime': '1792195577346',
      'seq': 140712175282407,
      'side': 'Sell',
      'execQty': '0.005',
      'closedSize': '0.005',
      'execType': 'Trade',
      'execPrice': '81565.5',
      'execFee': '0.22430513',
      'feeRate': '0.00055'},
     {'symbol': 'BTCUSDT',
      'orderId': '62df17b7-1153-4c6a-9a96-070d6bd2a43e',
      'orderLinkId': '5de8745038284938b6c0fafa3711b67d',
      'execId': '27101de5-6a57-415c-9e57-38ea573677a2',
      'execTime': '1792195259425',
      'seq': 140712175263993,
      'side': 'Buy',
      'execQty': '0.005',
      'closedSize': '0',
      'execType': 'Trade',
      'execPrice': '81563.1',
      'execFee': '0.22429853',
      'feeRate': '0.00055'}]
    observed_fill = observed_executions[1]
    observed_position = {
        "broker": "Bybit", "account": "demo", "category": "linear", "instrument": "BTCUSDT",
        "side": "Buy", "position_idx": 0, "size": .005, "settlement_currency": "USDT",
        "opened_at": "1763377208045", "position_open_time": 1792212224051,
        "execution_seq": 140712176966582,
    }
    assert old["_bybit_position_execution_proof"](observed_position, observed_executions) == (
        None, [], False, "Position/execution sequence not reconciled")

    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "accepted-contexts.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 700, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_record_outbound_traffic", lambda *a, **k: None)
    clock = [0.0]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    phase = [1]
    mark = ["81550"]
    config = {"base_url": "https://bybit.fixture", "api_key": "fixture", "api_secret": "fixture", "key_source": "fixture"}
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([("Bybit", "demo", config)], "fixture"))
    opened = 1792212224049
    context = {
        "broker": "bybit", "asset": "crypto", "account": "demo", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "position_idx": 0, "settlement_currency": "USDT",
        "calculation_context_id": "calcctx_bybit_demo_133be437ac934f5d9f6c25adcfaec5f8", "order_id": "b2a6134e-3c6e-4255-83c2-0a755c20a0ff", "order_link_id": "938d9ec7a01e4850a619b04538c972a1",
        "order_type": "market", "stop_loss_ticks": "5000", "target_mode": "rr", "risk_reward": "2",
        "risk_mode": "percent", "risk_value": "1", "timeframe": "5MIN", "is_test_trade": True,
        "setup": "Pullback", "pattern": "range", "ema": "9", "vwap": "Yes",
        "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
        "quantity": ".005", "status": "ACTIVE",
    }
    service._upsert_calculator_trade_context(context, invalidate_cache=False)
    service._TRADE_CONTEXTS_CACHE.clear()  # The scheduled readers must load durable data.
    calls = {"executions": 0, "funding": 0}
    ranges, clients = [], []
    monkeypatch.setattr(service, "_BYBIT_TIME_OFFSET_CACHE", {})
    monkeypatch.setattr(service, "_BYBIT_OPEN_FUNDING_CACHE", {})
    expected = {
        "broker": "Bybit", "account": "demo", "asset": "crypto", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "size": 0.005, "entry_price": "81541.6",
        "stop_loss": "81041.6", "take_profit": "82835.6",
        "order_type": "Market", "stop_loss_ticks": "5000", "target_mode": "rr",
        "target_mode_display": "Risk/Reward", "risk_reward": "2", "risk_mode": "percent",
        "risk_mode_display": "Percent", "risk_value": "1", "risk_value_display": "1%", "risk_unit": "%",
        "timeframe": "5MIN", "is_test_trade": "Yes", "setup": "Pullback", "pattern": "Range", "ema": "9",
        "vwap": "Yes", "aths_atls": "All-Time High", "round_number": "Yes",
        "webhook_mode": "No", "webhook_status": "Not used",
        "planned_entry_price": "81541.6", "planned_stop_price": "81041.6", "planned_target_price": "82835.6",
    }

    async def scenario():
        executions_ready, execution_gate = asyncio.Event(), asyncio.Event()
        funding_ready, funding_gate = asyncio.Event(), asyncio.Event()
        async def auth(**kwargs): return {}
        real_client = service.httpx.AsyncClient
        async def transport(request):
            assert request.method == "GET", "Broker mutations are prohibited"
            path, params = request.url.path, request.url.params
            rows, cursor = [], ""
            if path == "/v5/market/time":
                return service.httpx.Response(200, json={"retCode": 0, "result": {"timeSecond": str(service.time.time())}})
            elif path == "/v5/position/list":
                if params.get("category") == "linear" and (params.get("symbol") or params.get("settleCoin") == "USDT"):
                    rows = [{"symbol": "BTCUSDT", "side": "Buy", "positionIdx": 0, "size": ".005",
                             "createdTime": "1763377208045", "openTime": 1792212224051,
                             "seq": 140712176966582 if phase[0] == 1 else 140712176966583, "updatedTime": str(opened),
                             "avgPrice": "81541.6", "markPrice": mark[0], "stopLoss": "81041.6",
                             "takeProfit": "82835.6", "settleCoin": "USDT"}]
            elif path == "/v5/execution/list":
                calls["executions"] += 1
                ranges.append(dict(params))
                assert params["symbol"] == "BTCUSDT"
                assert int(params["endTime"]) - int(params["startTime"]) <= service._BYBIT_MAX_WINDOW_MS
                executions_ready.set()
                await execution_gate.wait()
                assert int(params["startTime"]) <= 1792195259425
                if not params.get("cursor"):
                    rows = [dict(row) for row in observed_executions]
                    cursor = "8365097%3A0%2C8351975%3A1"
                else:
                    assert params["cursor"] == "8365097:0,8351975:1"
            elif path == "/v5/account/transaction-log":
                calls["funding"] += 1
                funding_ready.set()
                await funding_gate.wait()
                # A definitive read failure stays unknown, without transport retries.
                return service.httpx.Response(200, json={"retCode": 10005, "retMsg": "fixture funding unavailable"})
            else:
                assert path == "/v5/order/realtime", path
            return service.httpx.Response(200, json={"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}})
        def client_factory(**kwargs):
            client = real_client(transport=service.httpx.MockTransport(transport), trust_env=False, **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(service, "_bybit_read_only_auth_preflight", auth)
        monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
        async def prohibited(*args, **kwargs):
            raise AssertionError("No network, notifications, or broker submissions are permitted")
        monkeypatch.setattr(service, "_bybit_signed_post", prohibited)

        async def snapshot(force=False):
            return json.loads((await service.list_open_orders(force=force)).body)
        def assert_metadata(payload):
            row = next(item for item in payload["items"] if item.get("broker") == "Bybit" and item.get("type") == "Position")
            for field, value in expected.items():
                assert row.get(field) == value, (field, row.get(field), value)
            assert row["take_profit_ticks"] is None  # Not supplied/applicable in the RR snapshot.
            assert all(row.get(field) is None for field in ("funding_paid", "funding_received", "net_funding"))
            return row
        rendered_payloads = []
        try:
            await snapshot()
            owner = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()]
            job = owner.job
            await asyncio.wait_for(executions_ready.wait(), 2)
            initial = await snapshot()
            assert len(initial["items"]) == 1
            assert not initial["items"][0]["context_linked"] and calls == {"executions": 1, "funding": 0}
            execution_gate.set()
            await asyncio.wait_for(funding_ready.wait(), 2)
            pending = await snapshot()
            row = assert_metadata(pending)
            assert row["raw_refs"]["order_ids"] == ["b2a6134e-3c6e-4255-83c2-0a755c20a0ff"]
            assert row["raw_refs"]["order_ids_complete"] and row["context_linked"]
            assert row["execution_seq"] == 140712176966582
            assert row["position_open_time"] == 1792212224051
            assert pending["pending"] and not pending["empty_confirmed"] and not pending["errors"]
            assert not any("lifecycle start could not" in warning.get("message", "") for warning in pending["warnings"])
            assert job.sources["Bybit/demo/funding"]["state"] == "pending"
            assert job.sources["Bybit/demo/funding"]["task"] is not None
            assert job.metrics["enrichment_progress_publications"] == 1
            assert pending["progress_revision"] > initial["progress_revision"] and pending["version"] == initial["version"]
            rendered_payloads.append(pending)
            # Ordinary GET and Refresh join owned work; sparse persistence/reload cannot erase selections.
            service._upsert_calculator_trade_context(dict(context, setup="", pattern="", ema=None, risk_value=""), invalidate_cache=False)
            service._TRADE_CONTEXTS_CACHE.clear()
            for deliberate in (False, True, False):
                assert_metadata(await snapshot(deliberate))
            assert calls == {"executions": 2, "funding": 1}
            # Normal price changes do not replace the source or repeat the scan.
            core = job.sources["Bybit/demo/linear/USDT/positions"]
            enrichment = job.sources["Bybit/demo/funding"]
            mark[0] = "81600"
            gate = asyncio.Event()
            original_reader = core["reader"]
            async def delayed_core():
                await gate.wait()
                return await original_reader()
            core["reader"] = delayed_core
            job.launch(core)
            retained = await snapshot()
            retained_row = assert_metadata(retained)
            assert retained_row["source_pending"] and not retained_row["source_stale"]
            rendered_payloads.append(retained)
            gate.set()
            await _open_orders_eventually(lambda: core["task"] is None)
            fresh = await snapshot()
            assert_metadata(fresh)
            assert fresh["items"][0]["current_price"] == "81600"
            assert job.sources["Bybit/demo/funding"] is enrichment
            assert enrichment["state"] == "pending" and enrichment["task"] is not None
            assert calls == {"executions": 2, "funding": 1}

            funding_gate.set()
            await _open_orders_eventually(lambda: job.sources["Bybit/demo/funding"]["state"] == "failed")
            failed = await snapshot()
            assert_metadata(failed)
            assert failed["warnings"] and calls == {"executions": 2, "funding": 1}
            assert not any("lifecycle start could not" in warning.get("message", "") for warning in failed["warnings"])
            rendered_payloads.append(failed)

            # Scheduled optional retry reuses proven executions within this source.
            job.launch(enrichment)
            await _open_orders_eventually(lambda: enrichment["task"] is None)
            assert_metadata(await snapshot())
            assert calls == {"executions": 2, "funding": 2}
            assert [item.get("cursor") for item in ranges] == [None, "8365097:0,8351975:1"]
            assert all(1763377208045 < int(item["startTime"]) <= 1792212224049 < 1792212224051
                       for item in ranges)
            assert service._bybit_position_lifecycle_start_ms(observed_position, observed_executions) == 1792212224049
            # Funding is an inventory/sequence witness, never an opening order.
            assert row["raw_refs"]["order_ids"] == [observed_fill["orderId"]]
            assert service._bybit_position_execution_proof(
                observed_position, [entry for entry in observed_executions
                                    if entry["execId"] != observed_fill["execId"]])[0] is None
            wrong_inventory = [dict(entry, execQty=".01") if entry["execType"] == "Funding" else dict(entry)
                               for entry in observed_executions]
            assert service._bybit_position_execution_proof(observed_position, wrong_inventory)[3] == (
                "Funding execution does not reconcile current position")
            wrong_account = dict(observed_position, account="live", type="Position",
                                 raw_refs=dict(row["raw_refs"]))
            service._enrich_open_item_with_trade_context(wrong_account)
            assert not wrong_account["context_linked"] and wrong_account["setup"] is None

            # Same symbol/size/index with a new sequence must not inherit the old
            # cycle, even though Bybit createdTime never changes.
            phase[0] = 2
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            replacement = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: replacement.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            new = await snapshot()
            new_row = next(item for item in new["items"] if item.get("type") == "Position")
            assert new_row["opened_at"] == row["opened_at"]
            assert not new_row["context_linked"] and new_row["setup"] is None and new_row["risk_value"] is None
            assert not new_row["raw_refs"]["order_ids_complete"]
            assert new_row["raw_refs"]["execution_provenance_reason"] == "Position/execution sequence not reconciled"
            assert calls == {"executions": 4, "funding": 2}
            # Durable reload of the original sequence resolves the same exact context.
            phase[0] = 1
            await service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].close()
            service._OPEN_ORDERS_SERVICES.clear()
            service._OPEN_ORDERS_CACHE["version"] += 1
            service._TRADE_CONTEXTS_CACHE.clear()
            await snapshot()
            reloaded = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: reloaded.sources.get("Bybit/demo/funding", {}).get("state") == "failed")
            assert_metadata(await snapshot())
            assert calls == {"executions": 6, "funding": 3}
        finally:
            await service._shutdown_open_orders_jobs()
        assert clients and all(client.is_closed for client in clients)
        return rendered_payloads

    payloads = asyncio.run(asyncio.wait_for(scenario(), 15))
    node = shutil.which("node")
    assert node
    # Execute the unchanged production renderer against actual API snapshots from the scheduled pipeline.
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';}
  set textContent(value){this._text=String(value);}
  get textContent(){return this._text;}
  set innerHTML(value){this.children=[];}
  appendChild(child){this.children.push(child);}
  addEventListener(){}
  querySelector(){return null;}
}
const tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const elements={'open-orders-table':table};
for(const id of ['open-orders-status','open-orders-empty','refresh-btn','open-orders-errors','open-orders-warnings'])elements[id]=new Element();
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(){}};
global.document={hidden:true,getElementById:id=>elements[id]||null,createElement:()=>new Element(),addEventListener(){}};
global.BroadcastChannel=undefined;global.setInterval=()=>1;global.clearInterval=()=>{};
const payloads=__PAYLOADS__;let current=payloads[0];
global.fetch=async url=>({ok:true,text:async()=>JSON.stringify(String(url).includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current)});
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const rows=[],confirmations=[];
  for(const payload of payloads){
    current=payload;
    if(payload.items[0].source_pending)window.__openOrdersTestHooks.handleStateChange({
      type:'state-changed',action:'submit',eventId:'retained',broker:'bybit',account:'demo',
      category:'linear',orderId:'b2a6134e-3c6e-4255-83c2-0a755c20a0ff',calculationContextId:'calcctx_bybit_demo_133be437ac934f5d9f6c25adcfaec5f8',submittedAt:Date.now()});
    await window.__openOrdersTestHooks.refresh('progress');
    rows.push(tbody.children[0].children.map(cell=>cell.textContent));
    confirmations.push(window.__openOrdersTestHooks.getState().pendingVisibilityChecks);
  }
  process.stdout.write(JSON.stringify({rows,confirmations}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__PAYLOADS__", json.dumps(payloads)).replace("__JS_PATH__", json.dumps(str(ROOT / "render/static/open_orders.js")))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    headers = re.findall(r"<th>(.*?)</th>", service.OPEN_ORDERS_TEMPLATE)
    cells = {
        "Broker / Venue": "Bybit", "Account": "demo", "Asset": "crypto", "Category": "linear",
        "Instrument": "BTCUSDT", "Side": "Buy", "Size": "0.005", "Entry / Order": "81541.6",
        "Stop Loss": "81041.6", "Take Profit": "82835.6",
        "Order Type": "Market", "Stop-loss Ticks": "5000", "Target Mode": "Risk/Reward",
        "Risk / Reward": "2", "Risk Mode": "Percent", "Risk Value": "1%", "Timeframe": "5MIN",
        "Test": "Yes", "Setup": "Pullback", "Pattern": "Range", "EMA": "9", "VWAP": "Yes",
        "ATH / ATL": "All-Time High", "Round Number": "Yes", "Webhook Mode": "No", "Webhook Status": "Not used",
        "Planned Entry": "81541.6", "Planned Stop": "81041.6", "Planned Target": "82835.6",
    }
    rendered_state = json.loads(result.stdout)
    assert rendered_state["confirmations"][1] == 1  # Retained/pending evidence cannot confirm Submit.
    for rendered in rendered_state["rows"]:
        row = dict(zip(headers, rendered))
        for column, value in cells.items():
            assert row[column] == value, (column, row[column], value)
        assert row["Take-profit Ticks"] == "\u2014"
        assert row["Funding paid"] == row["Funding received"] == row["Net funding"] == "\u2014"



def test_open_orders_derives_missing_take_profit_ticks_from_accepted_quote_prices(monkeypatch, tmp_path):
    service = master_service
    monkeypatch.setattr(service, "APP_PROFILE", "local")
    monkeypatch.setattr(service, "TRADE_CONTEXTS_PATH", tmp_path / "accepted.json")
    monkeypatch.setattr(service, "_TRADE_CONTEXTS_CACHE", {})
    monkeypatch.setattr(service, "_calc_context_uses_repo_local", lambda: True)
    monkeypatch.setattr(service, "BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH", tmp_path / "quotes.json")
    monkeypatch.setattr(service, "BYBIT_DEMO_CALC_CONTEXT_LEGACY_LOCAL_PATH", tmp_path / "absent-legacy.json")
    monkeypatch.setattr(service, "_schedule_dropbox_upload_state_backup", lambda: None)
    monkeypatch.setattr(service, "_load_pending_webhooks", lambda: [])
    monkeypatch.setattr(service, "_load_bounce_traders", lambda: [])
    monkeypatch.setattr(service.script_manager, "get", lambda name: type("Script", (), {"is_running": False})())
    monkeypatch.setattr(service, "_OPEN_ORDERS_CACHE", {"version": 900, "payload": None})
    monkeypatch.setattr(service, "_OPEN_ORDERS_SERVICES", {})
    monkeypatch.setattr(service, "_OPEN_ORDERS_LAST_GOOD_ITEMS", {})
    clock = [0.]
    monkeypatch.setattr(service, "_open_orders_now", lambda: clock[0])
    monkeypatch.setattr(service, "_open_orders_configuration", lambda: ([], "derived-ticks"))
    async def prohibited(*args, **kwargs):
        raise AssertionError("No network, broker mutation or external effect")
    monkeypatch.setattr(service.httpx.AsyncClient, "request", prohibited)
    monkeypatch.setattr(service, "_bybit_signed_post", prohibited)
    accepted = {
        "calculation_context_id": "exact-accepted", "quote_created_at_ms": 1792212222060,
        "broker": "bybit", "account": "demo", "asset": "crypto", "category": "linear",
        "instrument": "BTCUSDT", "side": "Buy", "order_id": "accepted-order", "position_idx": 0,
        "settlement_currency": "USDT", "order_type": "market", "stop_loss_ticks": "5000",
        "target_mode": "rr", "take_profit_ticks": None, "risk_reward": "2", "risk_mode": "percent",
        "risk_value": "1", "planned_entry_price": "81541.6", "planned_target_price": "82835.6",
        "planned_stop_price": "81041.6", "setup": "Pullback", "pattern": "range", "timeframe": "5MIN",
        "is_test_trade": False, "ema": "9", "vwap": "No", "aths_atls": "All-Time High", "round_number": "Yes",
    }
    quote = dict(accepted, status="QUOTED", quote_result={
        "calculation_context_id": "exact-accepted", "quote_created_at_ms": 1792212222060,
        "entry_price": "81541.6", "target_price": "82835.6", "tick_size": "0.1", "quote_tick_size": "0.1",
    })
    service._upsert_bybit_demo_calc_context(quote, invalidate_cache=False)
    service._upsert_calculator_trade_context(accepted, invalidate_cache=False)
    # Existing accepted context lacks the unit; exact legacy quote supplies it read-only.
    assert not service._load_trade_contexts()[0].get("calculator_distance_tick_size")
    future = service._accepted_calculator_intent(
        {"calculation_context_id": "exact-accepted", "action": "buy", "calculator_distance_tick_size": "999"},
        broker="bybit", account="demo", category="linear", instrument="BTCUSDT")
    assert future["calculator_distance_tick_size"] == "0.1" and future["take_profit_ticks"] is None
    fx = dict(accepted, broker="oanda", account="live", asset="fx", category="forex", instrument="EUR_USD",
              side="Sell", order_id=None, position_idx=None, settlement_currency=None,
              calculation_context_id="fx-short", planned_entry_price="1.100000",
              planned_target_price="1.098765", planned_stop_price="1.1005")
    fx["quote_result"] = {
        "calculation_context_id": "fx-short", "quote_created_at_ms": 1792212222060,
        "entry_price": "1.100000", "target_price": "1.098765",
        "chart_tick_size": "0.00001", "tick_size": "0.000001",
    }
    service._upsert_calculator_trade_context(fx, invalidate_cache=False)
    fx_intent = service._accepted_calculator_intent({"calculation_context_id": "fx-short", "action": "sell"},
        broker="oanda", account="live", category="forex", instrument="EUR_USD")
    assert fx_intent["calculator_distance_tick_size"] == "0.00001"
    service._upsert_calculator_trade_context(dict(fx, **fx_intent, order_id="fx-order"), invalidate_cache=False)
    persisted = service.TRADE_CONTEXTS_PATH.read_bytes()
    quote_bytes = service.BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH.read_bytes()
    service._TRADE_CONTEXTS_CACHE.clear()
    reads = []
    def start(job):
        for broker, account, symbol, order in (("Bybit", "demo", "BTCUSDT", "accepted-order"),
                                                ("OANDA", "live", "EUR_USD", "fx-order")):
            async def reader(broker=broker, account=account, symbol=symbol, order=order):
                reads.append(symbol)
                row = {"broker": broker, "account": account, "instrument": symbol, "order_id": order,
                       "id": order, "type": "Order", "side": "Sell" if broker == "OANDA" else "Buy",
                       "category": "forex" if broker == "OANDA" else "linear", "size": 1, "status": "New",
                       "current_price": "99999", "stop_loss": "1", "take_profit": "2"}
                return {"items": [service._enrich_open_item_with_trade_context(row)], "errors": []}
            job.add(symbol, {"broker": broker, "account": account, "source_type": "orders",
                             "category": "forex" if broker == "OANDA" else "linear", "endpoint": "/fixture"}, reader)
        job.publish()
    monkeypatch.setattr(service._OpenOrdersJob, "start", start)
    async def scenario():
        async def snapshot(force=False):
            return json.loads((await service.list_open_orders(force=force)).body)
        payloads = []
        try:
            await snapshot()
            job = service._OPEN_ORDERS_SERVICES[asyncio.get_running_loop()].job
            await _open_orders_eventually(lambda: all(entry["task"] is None for entry in job.sources.values()))
            for refresh in (False, True):
                if refresh:
                    clock[0] += 10
                    service._TRADE_CONTEXTS_CACHE.clear()
                    await snapshot(True)
                    await _open_orders_eventually(lambda: all(entry["task"] is None for entry in job.sources.values()))
                payload = await snapshot()
                rows = {row["instrument"]: row for row in payload["items"]}
                main = rows["BTCUSDT"]
                assert main["context_linked"] and main["take_profit_ticks"] == "12940"
                assert main["take_profit_ticks"] != "10000"  # Accepted cost-adjusted/snapped endpoint.
                assert main["take_profit_ticks_source"] == "accepted_quote_prices"
                assert main["take_profit_ticks_submitted"] is None and main["take_profit_ticks_tick_size"] == "0.1"
                assert main["target_mode"] == "rr" and main["risk_reward"] == "2"
                assert main["planned_entry_price"] == "81541.6" and main["planned_target_price"] == "82835.6"
                assert main["setup"] == "Pullback" and main["pattern"] == "Range" and main["is_test_trade"] == "No"
                assert rows["EUR_USD"]["take_profit_ticks"] == "123.5"
                assert rows["EUR_USD"]["take_profit_ticks_tick_size"] == "0.00001"
                payloads.append(payload)
            assert reads.count("BTCUSDT") == reads.count("EUR_USD") == 2
        finally:
            await service._shutdown_open_orders_jobs()
        return payloads
    payloads = asyncio.run(asyncio.wait_for(scenario(), 10))
    assert service.TRADE_CONTEXTS_PATH.read_bytes() == persisted
    assert service.BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH.read_bytes() == quote_bytes
    ctx = service._load_trade_contexts()[0]
    assert ctx.get("take_profit_ticks") is None and ctx["target_mode"] == "rr" and ctx["risk_reward"] == "2"
    for value in ("77.50", 0):
        supplied = service._enrich_open_item_with_trade_context({}, dict(accepted, take_profit_ticks=value))
        assert supplied["take_profit_ticks"] == value and supplied["take_profit_ticks_source"] == "submitted"
    for unit in (None, "0", "-0.1", "NaN", "Infinity"):
        unknown = dict(accepted, calculation_context_id="unavailable", calculator_distance_tick_size=unit)
        row = service._enrich_open_item_with_trade_context({}, unknown)
        assert row["take_profit_ticks"] is None and row["take_profit_ticks_source"] == "unknown"
    for entry, target in (("NaN", "2"), ("1", "Infinity"), ("2", "1"), ("0", "2")):
        invalid = dict(accepted, calculator_distance_tick_size="0.1", planned_entry_price=entry, planned_target_price=target)
        assert service._enrich_open_item_with_trade_context({}, invalid)["take_profit_ticks"] is None
    # Another quote/unit or conflicting exact snapshots cannot silently supply the unit.
    conflict = dict(quote, quote_result=dict(quote["quote_result"], tick_size="0.2", quote_tick_size="0.2"))
    service.BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH.write_text(json.dumps({"items": [quote, conflict]}), encoding="utf-8")
    assert service._enrich_open_item_with_trade_context({}, accepted)["take_profit_ticks"] is None
    other = dict(quote, calculation_context_id="unrelated")
    service.BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH.write_text(json.dumps({"items": [other]}), encoding="utf-8")
    assert service._enrich_open_item_with_trade_context({}, accepted)["take_profit_ticks"] is None

    node = shutil.which("node")
    assert node
    harness = r"""
const fs=require('fs');
class Element{
  constructor(){this.children=[];this.style={};this.textContent='';}
  set textContent(value){this._text=String(value);}
  get textContent(){return this._text;}
  set innerHTML(value){this.children=[];}
  appendChild(child){this.children.push(child);}
  addEventListener(){}
  querySelector(){return null;}
}
const tbody=new Element(),table=new Element();table.querySelector=()=>tbody;
const elements={'open-orders-table':table};
for(const id of ['open-orders-status','open-orders-empty','refresh-btn','open-orders-errors','open-orders-warnings'])elements[id]=new Element();
global.window={__OPEN_ORDERS_TESTING__:true,addEventListener(){}};
global.document={hidden:true,getElementById:id=>elements[id]||null,createElement:()=>new Element(),addEventListener(){}};
global.BroadcastChannel=undefined;global.setInterval=()=>1;global.clearInterval=()=>{};
const payloads=__PAYLOADS__;let current=payloads[0];
global.fetch=async url=>({ok:true,text:async()=>JSON.stringify(String(url).includes('/version')?{version:current.version,progress_revision:current.progress_revision}:current)});
eval(fs.readFileSync(__JS_PATH__,'utf8'));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const rendered=[];
  for(const payload of payloads){
    current=payload;
    await window.__openOrdersTestHooks.refresh('progress');
    rendered.push(tbody.children.map(row=>row.children.map(cell=>cell.textContent)));
  }
  process.stdout.write(JSON.stringify(rendered));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("__PAYLOADS__", json.dumps(payloads)).replace("__JS_PATH__", json.dumps(str(ROOT / "render/static/open_orders.js")))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    headers = re.findall(r"<th>(.*?)</th>", service.OPEN_ORDERS_TEMPLATE)
    for rendered in json.loads(result.stdout):
        rows = {row["Instrument"]: row for cells in rendered for row in [dict(zip(headers, cells))]}
        assert rows["BTCUSDT"]["Take-profit Ticks"] == "12940"
        assert rows["EUR_USD"]["Take-profit Ticks"] == "123.5"
        assert rows["BTCUSDT"]["Target Mode"] == "Risk/Reward" and rows["BTCUSDT"]["Risk / Reward"] == "2"
        assert rows["BTCUSDT"]["Setup"] == "Pullback" and rows["BTCUSDT"]["Pattern"] == "Range"
