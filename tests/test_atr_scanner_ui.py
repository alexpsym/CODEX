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
MASTER_SERVICE_PATH = ROOT / "render" / "master_service.py"
SCANNER_JS = ROOT / "render" / "static" / "atr_scanner.js"


def _load_master_service(name: str):
    spec = importlib.util.spec_from_file_location(name, MASTER_SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _force_local(monkeypatch, module):
    for name in (
        "RENDER",
        "RENDER_SERVICE_ID",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setattr(module, "APP_PROFILE", "local")


def test_dashboard_has_distinct_alerts_and_atr_scanner_cards_without_collision(monkeypatch):
    module = _load_master_service("render_master_service_atr_cards")
    _force_local(monkeypatch, module)
    buttons = module._profile_main_buttons()
    by_name = {item["name"]: item for item in buttons}
    assert by_name["monitor"] == {
        "id": "monitor",
        "name": "monitor",
        "label": "Alerts",
        "open_url": "/merged/monitor",
        "dashboard_main_view": True,
    }
    assert by_name["atr-scanner"] == {
        "id": "atr-scanner",
        "name": "atr-scanner",
        "label": "Scanner",
        "open_url": "/merged/atr-scanner",
        "dashboard_main_view": True,
    }
    assert len({item["id"] for item in buttons}) == len(buttons)
    assert len({item["open_url"] for item in buttons}) == len(buttons)

    payload = json.loads(asyncio.run(module.list_scripts()).body.decode("utf-8"))
    cards = {item["name"]: item for item in payload}
    assert cards["monitor"]["label"] == "Alerts"
    assert cards["atr-scanner"]["label"] == "Scanner"
    assert cards["atr-scanner"]["open_url"] == "/merged/atr-scanner"


def test_old_alert_routes_and_legacy_scanner_bookmark_remain_compatible():
    module = _load_master_service("render_master_service_atr_routes")
    route_endpoints = {
        route.path: route.endpoint.__name__
        for route in module.app.routes
        if getattr(route, "path", None) and getattr(route, "endpoint", None)
    }
    assert route_endpoints["/merged/monitor"] == "merged_monitor_page"
    assert route_endpoints["/merged/alerts"] == "merged_monitor_page"
    assert route_endpoints["/merged/scanner"] == "merged_scanner_redirect"
    assert route_endpoints["/merged/atr-scanner"] == "atr_scanner_page"
    endpoint_pairs = {
        (route.path, route.endpoint.__name__)
        for route in module.app.routes
        if getattr(route, "path", None) and getattr(route, "endpoint", None)
    }
    assert ("/api/bybit-alerts/settings", "bybit_monitor_settings") in endpoint_pairs
    assert ("/api/bybit-monitor/settings", "bybit_monitor_settings") in endpoint_pairs


def test_scanner_page_has_required_controls_columns_help_and_accessibility(monkeypatch):
    module = _load_master_service("render_master_service_atr_page")
    _force_local(monkeypatch, module)
    response = asyncio.run(module.atr_scanner_page())
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Trading Tools</a> / Scanner" in html
    assert "last closed candle" in html
    assert "cannot guarantee fills or future liquidity" in html
    for element_id in (
        "scanner-rank-timeframe",
        "scanner-top-n",
        "scanner-atr-length",
        "scanner-min-turnover",
        "scanner-max-spread",
        "scanner-depth-band",
        "scanner-min-bid-depth",
        "scanner-min-ask-depth",
        "scanner-exclusions",
        "scanner-save",
        "scanner-reset",
        "scanner-refresh",
        "scanner-qualified-tab",
        "scanner-excluded-tab",
    ):
        assert html.count(f'id="{element_id}"') == 1
    for label in (
        "ATR% 1m",
        "ATR% 5m",
        "ATR% 1h",
        "ATR% 1D",
        "ATR% 1W",
        "ATR% 1Mo",
        "24h turnover (USDT)",
        "Spread %",
        "Bid depth",
        "Ask depth",
        "Liquidity",
        "Data state",
    ):
        assert label in html
    assert html.count('scope="col"') >= 14
    assert 'role="status" aria-live="polite"' in html
    assert "/static/atr_scanner.js?v=" in html


class _StubService:
    def __init__(self):
        self.settings = {"rank_timeframe": "1m", "top_n": 10}
        self.status = {
            "ok": False,
            "state": "error",
            "stale": False,
            "progress": {"in_progress": False},
            "ranked_rows": [],
            "refresh_error": {"scope": "tickers", "message": "upstream unavailable"},
        }
        self.saved = None
        self.started = []

    def load_settings(self):
        return dict(self.settings)

    def save_settings(self, payload):
        self.saved = dict(payload)
        self.settings.update(payload)
        return dict(self.settings)

    def reset_settings(self):
        self.settings = {"rank_timeframe": "1m", "top_n": 10}
        return dict(self.settings)

    async def start_refresh(self, *, manual=False):
        self.started.append(manual)
        return {"started": True, "shared_in_flight": False, "manual": manual}

    def status_payload(self):
        return dict(self.status)


def test_scanner_api_settings_refresh_status_and_failure_codes(monkeypatch):
    module = _load_master_service("render_master_service_atr_api")
    _force_local(monkeypatch, module)
    stub = _StubService()
    monkeypatch.setattr(module, "ATR_SCANNER_SERVICE", stub)

    settings_response = asyncio.run(module.atr_scanner_settings())
    assert settings_response.status_code == 200
    assert json.loads(settings_response.body)["settings"]["top_n"] == 10

    save_response = asyncio.run(
        module.update_atr_scanner_settings({"rank_timeframe": "1D", "top_n": 20})
    )
    assert save_response.status_code == 200
    assert stub.saved == {"rank_timeframe": "1D", "top_n": 20}
    assert stub.started == [True]

    refresh_response = asyncio.run(module.refresh_atr_scanner({"manual": False}))
    assert refresh_response.status_code == 202
    assert stub.started[-1] is False

    failure_response = asyncio.run(module.atr_scanner_status())
    assert failure_response.status_code == 502
    assert json.loads(failure_response.body)["refresh_error"]["scope"] == "tickers"

    stub.status.update(
        {
            "ok": True,
            "state": "stale",
            "stale": True,
            "ranked_rows": [{"symbol": "BTCUSDT"}],
        }
    )
    stale_response = asyncio.run(module.atr_scanner_status())
    assert stale_response.status_code == 200
    assert json.loads(stale_response.body)["ranked_rows"] == [{"symbol": "BTCUSDT"}]


def test_scanner_api_and_card_are_blocked_in_render_profile(monkeypatch):
    module = _load_master_service("render_master_service_atr_render_boundary")
    monkeypatch.setattr(module, "APP_PROFILE", "render")
    monkeypatch.setenv("RENDER", "1")
    assert all(item.get("name") != "atr-scanner" for item in module._profile_main_buttons())
    assert module._render_blocks_path("/merged/atr-scanner") is True
    assert module._render_blocks_path("/api/atr-scanner/status") is True
    assert asyncio.run(module.atr_scanner_page()).status_code == 410
    assert asyncio.run(module.atr_scanner_status()).status_code == 410


def test_scanner_public_market_origin_is_canonical_and_never_uses_credentials(monkeypatch):
    module = _load_master_service("render_master_service_atr_public_origin")
    for unsafe in (
        "http://api.bybit.com",
        "https://api-demo.bybit.com",
        "https://api-testnet.bybit.com",
        "https://api.bybit.com.example.invalid",
        "https://user:secret@api.bybit.com",
        "https://api.bybit.com/private",
        "https://api.bybit.com/?signature=secret",
        "https://api.bybit.com:444",
    ):
        monkeypatch.setenv("BYBIT_PUBLIC_MARKET_BASE_URL", unsafe)
        assert module._atr_scanner_public_base_url() == "https://api.bybit.com"
    monkeypatch.setenv("BYBIT_PUBLIC_MARKET_BASE_URL", "https://API.BYTICK.COM:443/")
    assert module._atr_scanner_public_base_url() == "https://api.bytick.com"

    captured = {}

    async def fake_get(base_url, path, params, **kwargs):
        captured.update(base_url=base_url, path=path, params=dict(params), kwargs=kwargs)
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(module, "_bybit_get_async", fake_get)
    payload = asyncio.run(
        module._atr_scanner_fetch_public_json(
            "/v5/market/tickers", {"category": "linear"}
        )
    )
    assert payload["retCode"] == 0
    assert captured["base_url"] == "https://api.bytick.com"
    assert captured["params"] == {"category": "linear"}
    assert captured["path"] == "/v5/market/tickers"


def test_atr_scanner_javascript_parses_and_contains_progress_stale_settings_contract():
    node = shutil.which("node")
    assert node, "node is required for scanner JavaScript verification"
    syntax = subprocess.run(
        [node, "--check", str(SCANNER_JS)], capture_output=True, text=True
    )
    assert syntax.returncode == 0, syntax.stderr
    source = SCANNER_JS.read_text(encoding="utf-8")
    for token in (
        "/api/atr-scanner/settings",
        "/api/atr-scanner/settings/reset",
        "/api/atr-scanner/refresh",
        "/api/atr-scanner/status",
        "shared",
        "Stale last-known-good result",
        "scanner-excluded-body",
        "manual_exclusions",
        "auto_refresh_seconds",
        "setInterval(pollStatus, 2000)",
        "requestRefresh(false)",
    ):
        assert token in source


def test_atr_scanner_javascript_sorts_raw_each_timeframe_top_n_ties_and_na():
    node = shutil.which("node")
    assert node, "node is required for scanner JavaScript behavior verification"
    harness = r"""
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
function el(){ return { value:'', textContent:'', innerHTML:'', hidden:false, disabled:false, style:{}, addEventListener(){}, setAttribute(){} }; }
const elements = {};
const document = { getElementById(id){ return elements[id] || (elements[id]=el()); } };
const fetch = async (url) => ({ ok:true, status:200, statusText:'OK', text:async()=> String(url).includes('/settings') ? '{"settings":{"rank_timeframe":"1m","top_n":10,"auto_refresh_seconds":60}}' : '{"ok":false,"state":"not_started","progress":{"in_progress":false}}' });
const context = { console, document, fetch, setInterval:()=>1, clearInterval:()=>{}, Number, String, Math, Date, JSON, Promise, Intl };
context.window=context; context.window.addEventListener=()=>{}; context.globalThis=context;
vm.createContext(context); vm.runInContext(source, context);
const hooks=context.__atrScannerTestHooks;
const rows=[
 {symbol:'ZUSDT',atr_pct:{'1m':0.0000049,'5m':6,'1h':1,'1D':4,'1W':2,'1Mo':5}},
 {symbol:'BUSDT',atr_pct:{'1m':0.0000051,'5m':4,'1h':3,'1D':2,'1W':6,'1Mo':1}},
 {symbol:'AUSDT',atr_pct:{'1m':0.0000051,'5m':5,'1h':2,'1D':3,'1W':1,'1Mo':4}},
 {symbol:'NONE',atr_pct:{'1m':null,'5m':9,'1h':9,'1D':9,'1W':9,'1Mo':9},atr_status:{'1m':'error'},atr_reason:{'1m':'missing_invalid_market_data'}},
];
const snapshot={qualified_rows:rows,base_excluded_rows:[{symbol:'ILLIQ',reasons:['turnover_below_minimum'],reason_labels:['24h turnover below minimum']}]};
const out={ rankings:Object.fromEntries(['1m','5m','1h','1D','1W','1Mo'].map(tf=>[tf,hooks.rankRows(rows,tf,10).map(x=>x.symbol)])), topTwo:hooks.rankRows(rows,'1m',2).map(x=>x.symbol), excludedOne:hooks.excludedRowsFor(snapshot,'1m').map(x=>({symbol:x.symbol,reasons:x.reasons})), excludedDay:hooks.excludedRowsFor(snapshot,'1D').map(x=>x.symbol), formatted:hooks.formatAtr(0.0000049), na:hooks.formatAtr(null), states:[hooks.rowDataState({atr_pct:{'1m':1},atr_status:{'1m':'fresh'}},'1m',true),hooks.rowDataState({atr_pct:{'1m':1},atr_status:{'1m':'fresh','1D':'error'}},'1m',false),hooks.rowDataState({atr_pct:{'1m':null},atr_status:{'1m':'unavailable'}},'1m',false)] };
process.stdout.write(JSON.stringify(out));
"""
    completed = subprocess.run(
        [node, "-e", harness, str(SCANNER_JS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload == {
        "rankings": {
            "1m": ["AUSDT", "BUSDT", "ZUSDT"],
            "5m": ["NONE", "ZUSDT", "AUSDT", "BUSDT"],
            "1h": ["NONE", "BUSDT", "AUSDT", "ZUSDT"],
            "1D": ["NONE", "ZUSDT", "AUSDT", "BUSDT"],
            "1W": ["NONE", "BUSDT", "ZUSDT", "AUSDT"],
            "1Mo": ["NONE", "ZUSDT", "AUSDT", "BUSDT"],
        },
        "topTwo": ["AUSDT", "BUSDT"],
        "excludedOne": [
            {"symbol": "ILLIQ", "reasons": ["turnover_below_minimum"]},
            {"symbol": "NONE", "reasons": ["missing_invalid_market_data"]},
        ],
        "excludedDay": ["ILLIQ"],
        "formatted": "0.00000%",
        "na": "N/A",
        "states": ["Stale", "Partial / error", "N/A"],
    }
