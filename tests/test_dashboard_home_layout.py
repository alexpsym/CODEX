import asyncio
import importlib.util
import json
import shutil
import sys

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MASTER_SERVICE_PATH = ROOT / 'render' / 'master_service.py'


def _repo_tmp_dir(name: str) -> Path:
    path = ROOT / f".pytest_tmp_{name}"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _extract_html_template(source: str) -> str:
    match = re.search(r'HTML_TEMPLATE\s*=\s*"""(.*?)"""\n\nINSTRUMENT_SPECS_TEMPLATE', source, re.S)
    assert match, 'HTML_TEMPLATE block not found'
    return match.group(1)


def test_dashboard_home_removes_instrument_specs_recent_trades_open_orders() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    html = _extract_html_template(source)

    assert 'id="instrument-specs-widget"' not in html
    assert 'id="recent-trades-panel"' not in html
    assert 'id="open-orders-panel"' not in html

    assert 'script-toolbar-title' not in html
    assert '>Scripts<' not in html
    assert 'Watchlist' in html
    assert 'OANDA Inactivity' in html
    assert 'id="dashboard-workspace"' in html
    assert 'id="dashboard-workspace-title"' in html
    assert 'id="dashboard-workspace-status"' in html
    assert 'id="dashboard-workspace-empty"' in html
    assert 'id="dashboard-workspace-frame"' in html
    assert 'Select a script from the toolbar above to load it here.' in html
    assert 'Select a script from the left to load it here.' not in html
    assert '.local-exit-btn' in html
    assert 'class="panel dashboard-script-toolbar"' in html
    assert 'class="script-toolbar-row"' in html
    assert 'id="scripts-grid" class="script-stack script-toolbar-grid"' in html
    assert 'id="exit-button-slot" class="exit-button-slot"' in html
    assert 'id="exit-panel"' not in html

    assert 'id="journal-sync-widget"' not in html
    assert 'id="sync-journal-btn"' not in html
    assert 'id="sync-journal-status"' not in html

    watchlist_idx = html.find('Watchlist')
    oanda_idx = html.find('OANDA Inactivity')
    assert watchlist_idx != -1 and oanda_idx != -1
    assert watchlist_idx < oanda_idx

    scripts_grid_idx = html.find('id="scripts-grid"')
    exit_slot_idx = html.find('id="exit-button-slot"')
    watchlist_widget_idx = html.find('id="watchlist-widget"')
    oanda_widget_idx = html.find('id="oanda-inactivity-widget"')
    workspace_idx = html.find('id="dashboard-workspace"')
    assert scripts_grid_idx != -1 and exit_slot_idx != -1
    assert watchlist_widget_idx != -1 and oanda_widget_idx != -1 and workspace_idx != -1
    assert scripts_grid_idx < watchlist_widget_idx
    assert scripts_grid_idx < workspace_idx
    assert watchlist_widget_idx < oanda_widget_idx

    rail_start = html.find('<div class="dashboard-rail">')
    workspace_start = html.find('<section class="panel" id="dashboard-workspace">')
    rail_html = html[rail_start:workspace_start]
    assert '<aside class="panel sidebar">' not in rail_html
    assert 'id="scripts-grid"' not in rail_html


def test_dashboard_toolbar_css_keeps_scripts_single_row() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    html = _extract_html_template(source)

    assert '.dashboard-script-toolbar' in html
    assert '.script-toolbar-row' in html
    assert '.script-toolbar-grid' in html
    assert 'script-toolbar-title' not in html
    assert '>Scripts<' not in html
    assert 'flex-wrap: nowrap;' in html
    assert 'flex: 0 1 auto;' in html
    assert 'flex: 1 1 0;' not in html
    assert 'text-overflow: ellipsis;' in html
    assert 'white-space: nowrap;' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="history"] { max-width: 96px; }' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="trading-journal"] { max-width: 96px; }' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="open-orders"] { max-width: 146px; }' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="spreads-clone"] { max-width: 96px; }' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="mt5"] { max-width: 72px; }' in html
    assert '.script-toolbar-grid .script-btn[data-script-name="pine"] { max-width: 76px; }' in html
    assert '.local-exit-btn{\n            margin-top: 0;' in html


def test_calculator_specs_markup_and_endpoint_are_preserved() -> None:
    master_service_py = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    calculator_js = (ROOT / 'render' / 'static' / 'calculator.js').read_text(encoding='utf-8')

    assert 'id="calc-instrument-specs"' in master_service_py
    assert '/api/instrument-specs?query=' in calculator_js


def test_dashboard_home_removed_legacy_open_trading_journal_panel() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    html = _extract_html_template(source)
    assert 'id="open-master-journal-btn"' not in html


def _load_master_service_module():
    spec = importlib.util.spec_from_file_location("render_master_service_dashboard", ROOT / "render" / "master_service.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dashboard_home_uses_versioned_dashboard_js_url() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert '/static/dashboard.js?v=' in source


def test_local_profile_sets_no_cache_for_home_and_static_assets(monkeypatch) -> None:
    pytest = __import__('pytest')
    try:
        from fastapi.testclient import TestClient
    except Exception:
        pytest.skip('fastapi TestClient is unavailable in this environment')

    module = _load_master_service_module()
    monkeypatch.setattr(module, 'APP_PROFILE', 'local')
    client = TestClient(module.app)

    home = client.get('/')
    cache = home.headers.get('cache-control', '')
    assert 'no-store' in cache or 'no-cache' in cache

    static = client.get('/static/dashboard.js')
    static_cache = static.headers.get('cache-control', '')
    assert 'no-store' in static_cache or 'no-cache' in static_cache


def test_local_profile_buttons_use_trading_journal_page_not_merged_route() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert '"id": "trading-journal"' in source
    assert '"label": "Journal"' in source
    assert '"label": "Orders / Positions"' in source
    assert '"label": "Spreads"' in source
    assert '"label": "MT5"' in source
    assert '"label": "Pine"' in source
    assert '"open_url": "/dashboard/trading-journal"' in source
    assert '"open_url": "/dashboard/mt5"' in source
    assert '"open_url": "/dashboard/pine"' in source
    assert '"open_url": "/trading-journal"' not in source
    assert '"open_url": "/merged/trading-journal"' not in source
    assert '@app.get("/dashboard/trading-journal")' in source
    assert '@app.get("/trading-journal", response_class=HTMLResponse)' in source
    assert '@app.get("/dashboard/mt5", response_class=HTMLResponse)' in source
    assert '@app.get("/dashboard/pine", response_class=HTMLResponse)' in source


def test_mt5_dashboard_api_lists_and_deploys_without_deleting_extras(monkeypatch) -> None:
    module = _load_master_service_module()
    tmp_root = _repo_tmp_dir("dashboard_mt5")
    try:
        source_mql5 = tmp_root / "repo" / "MQL5"
        target_mql5 = tmp_root / "terminal" / "MQL5"
        (source_mql5 / "Experts").mkdir(parents=True)
        (source_mql5 / "Presets").mkdir(parents=True)
        (source_mql5 / "Experts" / "Trader.mq5").write_text("repo trader", encoding="utf-8")
        (source_mql5 / "Presets" / "PullbackEMA_ATR_RR.set").write_text("repo preset", encoding="utf-8")
        (target_mql5 / "Experts").mkdir(parents=True)
        (target_mql5 / "Files").mkdir(parents=True)
        (target_mql5 / "Experts" / "Trader.mq5").write_text("old trader", encoding="utf-8")
        (target_mql5 / "Files" / "extra.txt").write_text("keep me", encoding="utf-8")

        monkeypatch.setattr(module, "APP_PROFILE", "local")
        monkeypatch.setattr(module, "MT5_REPO_MQL5_DIR", source_mql5)
        monkeypatch.setenv("MT5_DATA_MQL5_DIR", str(target_mql5))

        files_response = asyncio.run(module.mt5_files())
        files_payload = json.loads(files_response.body.decode("utf-8"))
        assert files_payload["files"] == ["Experts/Trader.mq5", "Presets/PullbackEMA_ATR_RR.set"]

        deploy_response = asyncio.run(module.mt5_deploy())
        deploy_payload = json.loads(deploy_response.body.decode("utf-8"))
        assert deploy_response.status_code == 200
        assert deploy_payload["ok"] is True
        assert deploy_payload["copied_count"] == 2
        assert deploy_payload["target_path"] == str(target_mql5.resolve())
        assert (target_mql5 / "Experts" / "Trader.mq5").read_text(encoding="utf-8") == "repo trader"
        assert (target_mql5 / "Files" / "extra.txt").read_text(encoding="utf-8") == "keep me"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_pine_dashboard_api_lists_reads_and_blocks_traversal(monkeypatch) -> None:
    module = _load_master_service_module()
    tmp_root = _repo_tmp_dir("dashboard_pine")
    try:
        pine_root = tmp_root / "pinescripts"
        pine_root.mkdir()
        (pine_root / "custom_indicator.pine").write_text("//@version=6\nindicator('x')\n", encoding="utf-8")
        (pine_root / "notes.md").write_text("not a pine script", encoding="utf-8")
        monkeypatch.setattr(module, "APP_PROFILE", "local")
        monkeypatch.setattr(module, "PINE_SCRIPTS_DIR", pine_root)

        files_response = asyncio.run(module.pine_files())
        files_payload = json.loads(files_response.body.decode("utf-8"))
        assert files_payload["files"] == ["custom_indicator.pine"]

        file_response = asyncio.run(module.pine_file("custom_indicator.pine"))
        file_payload = json.loads(file_response.body.decode("utf-8"))
        assert file_payload["display_path"] == "pinescripts/custom_indicator.pine"
        assert file_payload["code"].startswith("//@version=6")

        with pytest.raises(Exception) as excinfo:
            asyncio.run(module.pine_file("../secret.pine"))
        assert getattr(excinfo.value, "status_code", None) == 400
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_pine_dashboard_page_has_clipboard_and_textarea_fallback() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding="utf-8")
    assert "navigator.clipboard.writeText(code)" in source
    assert '<textarea id="fallback" hidden>' in source
    assert "Clipboard unavailable. Use manual copy below." in source


def test_trading_journal_workspace_contains_action_buttons_in_order():
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert "open-journal-btn" in source
    assert "import-journal-btn" in source
    assert "journal-resync-btn" in source
    assert ">Resync<" in source
    assert "crypto-monthly-pnl-btn" in source
    assert "bybit-demo-balance-adjustment-btn" in source
    assert "Bybit Demo Balance Adjustment" in source
    assert source.index("open-journal-btn") < source.index("import-journal-btn") < source.index("journal-resync-btn") < source.index("crypto-monthly-pnl-btn") < source.index("bybit-demo-balance-adjustment-btn")



def test_trading_journal_actions_crypto_monthly_diagnostics_are_rendered() -> None:
    js = (ROOT / 'render' / 'static' / 'trading_journal_actions.js').read_text(encoding='utf-8')
    assert 'renderCryptoMonthlyDiagnostics' in js
    assert 'crypto-monthly-pnl-diagnostics' in js
    assert 'Crypto Monthly P&amp;L diagnostics / raw JSON' in js
    for token in ['now_brisbane', 'current_month', 'last_completed_month', 'state_months', 'workbook_months', 'ignored_invalid_workbook_anchors', 'missing_workbook_months', 'verified_row_ids', 'code_version', 'app_commit']:
        assert token in js
    assert 'payload.rows_inserted || 0' not in js

def test_trading_journal_workspace_bybit_account_dropdown_has_no_auto_option():
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert '>Auto<' not in source
    assert '<option value="">Auto</option>' not in source
    assert '<option value="" selected disabled>Select Demo or Live</option>' in source
    assert '<option value="demo">Demo</option>' in source
    assert '<option value="live">Live</option>' in source
