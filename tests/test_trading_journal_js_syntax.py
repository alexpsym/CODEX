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
    assert "g?.market_breakdown || []" in js
    assert "avg_result_pct" in js
    assert "avg_r_multiple" in js
    assert "avg_stop_pct_winners" in js
    assert "avg_stop_pct_losers" in js
    assert "max_drawdown_pct" in js
    assert "overall_avg_seconds" in js
    assert "tj-stats-table" in js


def test_trading_journal_stats_classes_are_value_only_and_net_pl_is_sign_based() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "const toneBySign = (value) =>" in js
    assert "if (!Number.isFinite(n) || n === 0) return 'tj-stat-neutral';" in js
    assert "return n > 0 ? 'tj-stat-positive' : 'tj-stat-negative';" in js
    assert "const row = (label, value, valueCls='tj-stat-neutral', labelCls='tj-stat-neutral')" in js
    assert '<td class="tj-stat-label ${labelCls}">' in js
    assert '<td class="tj-stat-value ${valueCls}">' in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct))" in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct), 'tj-stat-winner')" not in js
    assert "row('Net P/L', fmtNum(m?.net_profit_total, 2), toneBySign(m?.net_profit_total))" in js
