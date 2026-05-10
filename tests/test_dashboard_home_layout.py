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

    assert 'id="sync-journal-btn"' in html
    assert 'Sync Journal' in html
    assert 'id="sync-journal-status"' in html

    scripts_idx = html.find('Scripts')
    watchlist_idx = html.find('Watchlist')
    oanda_idx = html.find('OANDA Inactivity')
    assert scripts_idx != -1 and watchlist_idx != -1 and oanda_idx != -1
    assert scripts_idx < watchlist_idx < oanda_idx


def test_calculator_specs_markup_and_endpoint_are_preserved() -> None:
    master_service_py = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    calculator_js = (ROOT / 'render' / 'static' / 'calculator.js').read_text(encoding='utf-8')

    assert 'id="calc-instrument-specs"' in master_service_py
    assert '/api/instrument-specs?query=' in calculator_js


def test_dashboard_home_has_open_master_journal_button_hidden_default() -> None:
    source = MASTER_SERVICE_PATH.read_text(encoding='utf-8')
    html = _extract_html_template(source)
    assert 'id="open-master-journal-btn"' in html
    assert 'Open Master Journal' in html
    assert 'id="sync-journal-btn"' in html
    assert html.index('sync-journal-btn') < html.index('open-master-journal-btn') < html.index('sync-journal-status')
    assert 'id="open-master-journal-btn" hidden disabled' in html
