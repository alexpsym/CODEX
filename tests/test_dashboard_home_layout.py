import importlib.util
import sys

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER_SERVICE_PATH = ROOT / 'render' / 'master_service.py'


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

    assert 'Scripts' in html
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

    scripts_idx = html.find('Scripts')
    watchlist_idx = html.find('Watchlist')
    oanda_idx = html.find('OANDA Inactivity')
    assert scripts_idx != -1 and watchlist_idx != -1 and oanda_idx != -1
    assert scripts_idx < watchlist_idx < oanda_idx

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
    assert 'flex-wrap: nowrap;' in html
    assert 'text-overflow: ellipsis;' in html
    assert 'white-space: nowrap;' in html
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


def test_local_profile_buttons_exclude_trading_journal_source() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert '"id": "trading-journal"' not in source
    assert '"open_url": "/merged/trading-journal"' not in source
    assert '@app.get("/trading-journal", response_class=HTMLResponse)' in source


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


def test_trading_journal_workspace_bybit_account_dropdown_has_no_auto_option():
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    assert '>Auto<' not in source
    assert '<option value="">Auto</option>' not in source
    assert '<option value="" selected disabled>Select Demo or Live</option>' in source
    assert '<option value="demo">Demo</option>' in source
    assert '<option value="live">Live</option>' in source
