from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / 'render' / 'static' / 'history_page.js'


def test_history_page_js_parses_with_node() -> None:
    node = shutil.which('node')
    assert node, 'node is required for JS syntax check'
    subprocess.run([node, '--check', str(JS_PATH)], check=True)


def test_history_page_uses_blob_download_and_no_window_open() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    assert 'window.open(' not in js
    assert 'downloadExportFile' in js
    assert 'URL.createObjectURL' in js
    assert 'URL.revokeObjectURL' in js
    assert 'setTimeout(() => URL.revokeObjectURL' in js
    assert '.download =' in js
    assert 'Export completed but no download URL was returned' in js
    assert 'Automatic download failed' in js
    assert 'Download started.' not in js


def test_oanda_backfill_failure_keeps_completed_export_download_available() -> None:
    js = JS_PATH.read_text(encoding='utf-8')
    done_block = js.split("if (state === 'done')", 1)[1].split("if (state === 'error')", 1)[0]
    assert done_block.index("const dl = st.download_url") < done_block.index("backfill-journal")
    assert "Export completed, but Trading Journal backfill failed:" in done_block
    assert "Export completed. Trading Journal backfill failed. Manual download: " in done_block
    assert done_block.count("showManualDownload(dl, 'Export completed. Trading Journal backfill failed. Manual download: ');") == 2
    assert done_block.index("backfill-journal") < done_block.index("downloadExportFile(dl")
