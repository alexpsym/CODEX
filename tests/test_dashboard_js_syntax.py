import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / 'render' / 'static' / 'dashboard.js'


def test_dashboard_js_parses_with_node() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS syntax check'
    subprocess.run([node, '--check', str(JS_PATH)], check=True)


def test_dashboard_js_no_removed_widget_endpoints_and_keeps_needed_calls() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert '/api/open-orders' not in js
    assert '/api/recent-trades' not in js
    assert '/api/watchlist' in js
    assert '/api/oanda-inactivity-status' in js
    assert 'window.open(' not in js
    assert 'Loaded ${new Date().toLocaleTimeString()}' not in js
    assert "document.getElementById('dashboard-workspace-frame')" in js
    assert "document.getElementById('dashboard-workspace-title')" in js
    assert "document.getElementById('dashboard-workspace-status')" in js
    assert "let activeMainLoadState = 'idle';" in js
    assert "activeMainLoadState = 'loading';" in js
    assert "activeMainLoadState = 'loaded';" in js
    assert "activeMainLoadState = 'error';" in js
    assert "active-script" in js
    assert "const processRunning = script.running === true;" in js
    assert "const processStarting = script.starting === true;" in js
    assert "let dotState = processRunning ? 'running' : (processStarting ? 'starting' : 'stopped');" in js
    assert "if (isFxWeekend && processRunning && !fxEnabled) {" in js
    assert "if (dotState === 'stopped' && stopReason) {" in js
    assert "const isActiveMainView = isDashboardMainView(script) && String(script.name) === activeMainScriptName;" in js
    assert "if (isActiveMainView) {" in js
    assert "dotTitle = 'Inactive view';" not in js
    assert "dotTitle = 'Active view loaded';" not in js
