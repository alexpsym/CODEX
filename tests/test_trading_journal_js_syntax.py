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
    assert "fmtStatTradeJump" in js
    assert "jumpToTradeRow" in js
    assert "data-jump-row-id" in js
    assert "tj-stat-jump" in js
    assert "tj-row-highlight" in js
    assert "fmtLeader" in js
    assert "tj-stat-detail" in js
    assert "fx_most_wins_instrument" in js
    assert "fx_most_losses_instrument" in js
    assert "crypto_most_wins_instrument" in js
    assert "crypto_most_losses_instrument" in js
    assert "metric_sources" in js
    assert "escHtml(fmtTradeRef" not in js
    assert 'Min result %' not in js
    assert 'Max result %' not in js
    assert 'Max loss %' in js
    assert 'Max win %' in js
    assert 'Max R loss' in js
    assert 'Max R win' in js
    assert 'Drawdown points' not in js
    assert "Segments" not in js
    assert "wrap.style.display = 'block';" not in js

    assert "tj-stats-column" in js
    assert "const sections = [" in js
    assert "wrap.innerHTML = [" not in js


def test_trading_journal_stats_classes_are_value_only_and_net_pl_is_sign_based() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "const toneBySign = (value) =>" in js
    assert "if (!Number.isFinite(n) || n === 0) return 'tj-stat-neutral';" in js
    assert "return n > 0 ? 'tj-stat-positive' : 'tj-stat-negative';" in js
    assert "const row = (label, value, valueCls='tj-stat-neutral', labelCls='tj-stat-neutral', detail='')" in js
    assert '<td class="tj-stat-label ${labelCls}">' in js
    assert '<td class="tj-stat-value ${valueCls}">' in js
    assert '<td class="tj-stat-detail">' in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct))" in js
    assert "row('Win rate', fmtPctSmall(m?.win_rate_pct), 'tj-stat-winner')" not in js
    assert "row('Avg result %', fmtPctSmall(m?.avg_result_pct), toneBySign(m?.avg_result_pct))" in js


def test_trading_journal_instrument_view_uses_aggregate_safe_dataset_and_load_hides_overlay_on_failure() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    render_rows_scope = js[js.index("function renderRows"):js.index("function renderBalances")]
    inst_scope = js[js.index("function renderInstrumentView"):js.index("function renderCalendarView")]
    assert "tr.setAttribute('data-row-id'" in render_rows_scope
    assert "String(r.id)" in render_rows_scope
    assert "data-row-id" not in inst_scope
    assert "r.id" not in inst_scope
    assert "tr.dataset.symbol = String(item.symbol || '')" in inst_scope
    assert "tr.dataset.assetClass = String(item.asset_class || '')" in inst_scope
    assert "loading?.style?.display === 'flex'" not in js
    assert "if (ownsVisibleOverlay) hideLoading();" in js


def test_trading_journal_stat_trade_filter_wiring_present() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "statTradeFilter" in js
    assert "getFilteredRows" in js
    assert "renderStatTradeFilterButton" in js
    assert "clearStatTradeFilter" in js
    assert "data-jump-row-label" in js
    assert "tj-stat-trade-filter-btn" in js
    assert "jumpToTradeRow(jumpEl.dataset.jumpRowId || '', jumpEl.dataset.jumpRowLabel || '')" in js
    assert "stale_oanda_demo_balance_not_backfilled" in js
    assert "OANDA demo export exists but was not applied. Balance is stale." in js
    assert "Install xlrd in the journal runtime, then rerun OANDA history backfill." in js
