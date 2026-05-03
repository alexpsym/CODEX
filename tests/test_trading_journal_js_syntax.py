import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "trading_journal.js"


def test_trading_journal_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)


def test_trading_journal_diagnostics_split_balance_anchor_from_parse_sync() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "balance anchor missing" in js
    assert "const isBalanceAnchorWarning" in js
    assert "const isParseSyncError" in js
    assert "if (syncResult?.ok === false)" in js
    assert "snapshotError" in js
    assert "Bybit Demo workbook is blank; old Bybit Demo rows purged" in js
