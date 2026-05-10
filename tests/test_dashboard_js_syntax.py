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
    assert '/api/state-sync/status' in js
    assert '/api/state-sync/remote-backup-summary' in js
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
    assert "const isMonitor = String(script.name || '').trim().toLowerCase() === 'monitor';" in js
    assert "processTitle = processRunning ? 'Scanner running' : (processStarting ? 'Scanner starting' : 'Scanner stopped');" in js
    assert "processTitle = `Scanner stopped: ${stopReason}`;" in js
    assert "if (!processRunning && stopReason) {" in js
    assert "const isActiveMainView = isDashboardMainView(script) && String(script.name) === activeMainScriptName;" in js
    assert "if (isActiveMainView) {" in js
    assert "dotState = 'running';" in js
    assert "workspaceTitle = activeMainLoadState === 'loading'" in js
    assert "'Open in workspace: loading'" in js
    assert "'Open in workspace: load failed'" in js
    assert "'Open in workspace'" in js
    assert "dotTitle = workspaceTitle ? `${workspaceTitle}; ${processTitle}` : processTitle;" in js
    assert "syncWorkspaceSelectionFromScripts();" in js
    assert "syncWorkspaceSelectionFromScripts();\n        renderScripts();" in js
    assert "if (isMonitor) {" in js
    assert "cache: 'no-store'" in js
    assert "Loading Dropbox state…" in js
    assert "Saved locally only (repo deletion can lose local state)" in js
    assert "Synced with Dropbox" in js
    assert "Dropbox sync error" in js
    assert "Dropbox sync verification missing; save not confirmed durable." in js
    assert "Watchlist edits blocked until Dropbox restore/sync is healthy." in js
    assert "dotTitle = 'Inactive view';" not in js
    assert "dotTitle = 'Active view loaded';" not in js
    assert "Inactive view" not in js


def test_dashboard_js_prefers_post_verified_watchlist_before_remote_summary() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert "if (verifiedAt && verifiedWatchlist.length) {" in js
    assert "const remoteSummary = await fetchRemoteBackupSummary();" in js


def test_dashboard_js_includes_sync_journal_wiring():
    js = (ROOT / 'render' / 'static' / 'dashboard.js').read_text(encoding='utf-8')
    assert '/api/trading-journal/sync' in js
    assert '/api/trading-journal/sync/status' in js
    assert 'sync-journal-btn' in js
    listener = "syncJournalBtn?.addEventListener('click', runSyncJournal);"
    assert listener in js
    assert "if (syncJournalBtn) { syncJournalBtn.addEventListener('click', runSyncJournal); }" not in js
    assert js.index(listener) < js.rindex('})();')
    assert "master_journal_ok !== false" not in js
    assert "const p = statusPayload.result?.master_journal_path || 'journal/Master Journal.xlsx';" not in js
    assert "master_journal_ok === true" in js
    assert "master_journal_path" in js
    assert "master_journal_exists" in js
    assert "github_sync_ok" in js
    assert "github_sync_error" in js
    assert "GitHub updated" in js
    assert "GitHub already up to date" in js
    assert "Master Journal.xlsx created, but GitHub sync failed" in js


def test_dashboard_js_open_master_journal_wiring():
    js = JS_PATH.read_text(encoding='utf-8')
    assert 'open-master-journal-btn' in js
    assert '/api/trading-journal/open-master-journal' in js
    assert 'master_journal_ok === true' in js
    assert 'master_journal_exists' in js
    assert 'setOpenMasterJournalVisible(false)' in js
    assert 'setOpenMasterJournalVisible(true)' in js
    assert "openMasterJournalBtn?.addEventListener('click', openMasterJournal);" in js
    assert 'master_journal_ok !== false' not in js
    assert 'Startup journal sync complete' not in js
