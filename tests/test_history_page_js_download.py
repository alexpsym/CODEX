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
    assert '.download =' in js
    assert 'Export completed but no download URL was returned' in js
    assert 'Automatic download failed' in js
    assert 'Download started.' not in js
