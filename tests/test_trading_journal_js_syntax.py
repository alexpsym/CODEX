import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "trading_journal.js"


def test_trading_journal_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)
