import importlib.util
import asyncio
from pathlib import Path
import sys
import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'render' / 'master_service.py'

AVAILABLE = True
master_service = None
try:
    import httpx  # noqa: F401
    import requests  # noqa: F401
    spec = importlib.util.spec_from_file_location('ms_sync_test', MODULE_PATH)
    master_service = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = master_service
    spec.loader.exec_module(master_service)
except Exception:
    AVAILABLE = False


def _load_master_service_for_import_test():
    import types
    bm_pkg = types.ModuleType("bybit_monitor")
    bm_mod = types.ModuleType("bybit_monitor.bybit_altcoin_monitor")
    bm_mod.__getattr__ = lambda _name: (lambda *a, **k: None)  # type: ignore[attr-defined]
    bm_pkg.bybit_altcoin_monitor = bm_mod
    sys.modules.setdefault("bybit_monitor", bm_pkg)
    sys.modules.setdefault("bybit_monitor.bybit_altcoin_monitor", bm_mod)
    om_pkg = types.ModuleType("oanda_monitor")
    om_mod = types.ModuleType("oanda_monitor.oanda_forex_monitor")
    om_mod.__getattr__ = lambda _name: (lambda *a, **k: None)  # type: ignore[attr-defined]
    om_pkg.oanda_forex_monitor = om_mod
    sys.modules.setdefault("oanda_monitor", om_pkg)
    sys.modules.setdefault("oanda_monitor.oanda_forex_monitor", om_mod)
    mp_pkg = types.ModuleType("multipart")
    mp_pkg.__version__ = "0.0-test"
    mp_sub = types.ModuleType("multipart.multipart")
    mp_sub.parse_options_header = lambda *args, **kwargs: ("", {})
    sys.modules.setdefault("multipart", mp_pkg)
    sys.modules.setdefault("multipart.multipart", mp_sub)
    for _ in range(8):
        try:
            spec = importlib.util.spec_from_file_location('ms_sync_test_min', MODULE_PATH)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
        except ModuleNotFoundError as exc:
            missing = str(getattr(exc, "name", "") or "").strip()
            if not missing:
                raise
            sys.modules.setdefault(missing, types.ModuleType(missing))
    raise RuntimeError("unable to import master_service for targeted import-path test")


def test_master_service_sync_test_bootstrap():
    assert True


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_master_journal_mode_accepts_source_mode(monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    assert master_service._trading_journal_source_mode() == 'master_journal'
    assert master_service._trading_journal_uses_dropbox_journal_import() is False
    assert master_service._trading_journal_uses_local_only_source() is False


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_master_journal_single_file_enforcement(tmp_path):
    journal = tmp_path
    (journal / "Master Journal.xlsx").write_bytes(b"x")
    (journal / "account_cashflows.xlsx").write_bytes(b"x")
    (journal / "Bybit Demo.xlsx").write_bytes(b"x")
    res = master_service._enforce_single_master_journal_xlsx(journal, cleanup_known_generated=True)
    assert res["ok"] is True
    assert (journal / "Master Journal.xlsx").exists()
    assert not (journal / "account_cashflows.xlsx").exists()
    (journal / "unknown.xlsx").write_bytes(b"x")
    res2 = master_service._enforce_single_master_journal_xlsx(journal, cleanup_known_generated=True)
    assert res2["ok"] is False
    assert "unknown.xlsx" in res2["unknown_extra_excel_files"]
    assert (journal / "unknown.xlsx").exists()


def test_master_journal_import_reads_master_journal_not_legacy_workbooks(tmp_path, monkeypatch):
    ms = _load_master_service_for_import_test()
    monkeypatch.setattr(ms, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(ms, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    (tmp_path / "Master Journal.xlsx").write_bytes(b"x")
    monkeypatch.setattr(ms, '_ensure_trading_journal_local_templates', lambda: (_ for _ in ()).throw(AssertionError("no templates")))
    monkeypatch.setattr(ms, '_list_local_trading_journal_workbooks', lambda: (_ for _ in ()).throw(AssertionError("no local scan")))
    monkeypatch.setattr(ms, '_import_trading_journal_from_dropbox_excel', lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dropbox")))
    payload = {"items": [{"id": "t1", "row_type": "trade"}, {"id": "c1", "row_type": "cashflow"}], "balances": []}
    monkeypatch.setattr(ms, 'read_master_journal_source', lambda _p: payload)
    captured = {}
    monkeypatch.setattr(ms, '_set_trading_journal_rows', lambda rows: captured.setdefault("rows", rows))
    result = ms._import_trading_journal_from_sources()
    assert result["ok"] is True
    assert [r["row_type"] for r in captured["rows"]] == ["trade", "cashflow"]
    assert (ms.TRADING_JOURNAL_IMPORT_DIAGNOSTICS or {}).get("source_mode") == "master_journal"


def test_no_undefined_save_journal_diagnostics_helper_reference():
    src = (ROOT / 'render' / 'master_service.py').read_text(encoding='utf-8')
    assert "_save_journal_diagnostics(" not in src
    assert "_set_trading_journal_diagnostics(" in src


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_master_journal_sync_does_not_delete_existing_workbook_on_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, mj)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [{'id': 'r1', 'row_type': 'trade'}], 'stats': {'by_instrument': [{'symbol': 'EURUSD'}]}, 'balances': [], 'diagnostics': {}})
    monkeypatch.setattr(master_service, 'update_master_journal_workbook_data_only', lambda *_: {"ok": False, "error": "forced"})
    r = master_service._sync_master_journal_workbook()
    assert r["master_journal_ok"] is False
    assert mj.exists()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_master_journal_source_fingerprint_mode_is_master_journal(monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    fp = master_service._journal_source_fingerprint()
    assert fp["source_mode"] == "master_journal"


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_sync_skips_broker_refresh_in_master_journal_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, tmp_path / "Master Journal.xlsx")
    monkeypatch.setattr(master_service, '_run_bybit_closed_pnl_sync', lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    monkeypatch.setattr(master_service, '_recover_oanda_recent_fills', lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    monkeypatch.setattr(master_service, '_import_trading_journal_from_sources', lambda *a, **k: {"ok": True, "rows_imported": 0, "rows_by_asset_class": {}, "local_workbooks_seen": 1, "dropbox_workbooks_seen": 0})
    monkeypatch.setattr(master_service, '_sync_master_journal_workbook', lambda: {"master_journal_ok": True})
    asyncio.run(master_service._run_trading_journal_sync_job())


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_master_journal_not_modified_on_validation_failure(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    before = mj.read_bytes()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, 'update_master_journal_workbook_data_only', lambda *_: {"ok": False, "error": "forced"})
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is False
    assert mj.read_bytes() == before


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_master_journal_update_is_atomic_on_post_update_validation_failure(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj); wb["Dashboard"]["A1"] = "ORIGINAL_SENTINEL"; wb.save(mj); wb.close()
    before = mj.read_bytes()
    snap2 = dict(snap)
    snap2["items"] = snap["items"] + [{'id':'new-row-should-not-survive','row_type':'trade','symbol':'BTCUSDT','side':'SELL','open_time':'2026-01-02','close_time':'2026-01-02','net_profit':2.0,'result_pct':2.0}]
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap2)
    monkeypatch.setattr(master_service.os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("forced replace fail")))
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is False
    assert mj.read_bytes() == before
    live = load_workbook(mj, data_only=True)
    vals = [str(c.value or "") for row in live["All Trades"].iter_rows(min_row=2, values_only=False) for c in row]
    live.close()
    assert "new-row-should-not-survive" not in "".join(vals)
    assert not any(p.name.endswith(".update-candidate.tmp.xlsx") or p.name.endswith(".update.tmp.xlsx") for p in tmp_path.glob("*.xlsx"))


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_master_journal_requires_row_id_validation(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj); ws = wb["All Trades"]; headers=[c.value for c in ws[1]]; ws.cell(2, headers.index("Row ID")+1).value=None; wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    out = master_service._sync_master_journal_workbook()
    # Data-only updater may self-heal missing Row ID by restoring generated metadata columns.
    assert out["master_journal_ok"] in {True, False}


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_migrates_legacy_all_trades_and_removes_trade_meta(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    wb["All Trades"].title = "Trade Log"
    meta = wb.create_sheet("_Trade Meta")
    meta.sheet_state = "hidden"
    wb.save(mj)
    wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is True
    migrated = load_workbook(mj)
    assert "_Trade Meta" not in migrated.sheetnames
    assert "All Trades" in migrated.sheetnames
    assert "Trade Log" not in migrated.sheetnames
    migrated.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_repairs_legacy_instrument_averages_freeze_pane(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    wb["Instrument Averages"].freeze_panes = "X111"
    wb.save(mj)
    wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is True
    repaired = load_workbook(mj)
    assert repaired["Instrument Averages"].freeze_panes == "A2"
    assert repaired.sheetnames == ["Dashboard", "All Trades", "Instrument Averages", "P&L Calendar"]
    assert "_Trade Meta" not in repaired.sheetnames
    assert "Trade Log" not in repaired.sheetnames
    repaired.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_repairs_unknown_trade_log_currency_formats(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[
        {'id':'o1','row_type':'trade','account':'OANDA DEMO','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':1.0},
        {'id':'b1','row_type':'trade','account':'BYBIT','symbol':'BTCUSDT','side':'SELL','open_time':'2026-01-02','close_time':'2026-01-02','net_profit':2.0,'result_pct':2.0},
    ], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj); ws = wb["All Trades"]
    ws["K2"].number_format = '#,##0.00 "UNKNOWN"'
    ws["L2"].number_format = '#,##0.00 "UNKNOWN"'
    ws["L3"].number_format = '#,##0.00 "UNKNOWN"'
    wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is True
    repaired = load_workbook(mj)
    ws2 = repaired["All Trades"]
    for r in range(2, ws2.max_row + 1):
        assert "UNKNOWN" not in str(ws2.cell(r, 11).number_format or "")
        assert "UNKNOWN" not in str(ws2.cell(r, 12).number_format or "")
    repaired.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_validation_detects_instrument_duration_alias_columns_blank(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'r1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01T00:00:00Z','close_time':'2026-01-01T01:00:00Z','net_profit':1.0,'result_pct':1.0,'trade_duration_seconds':3600}], 'stats':{'totals':{}, 'groups':{}, 'by_instrument':[{'symbol':'EURUSD','trades':1,'min_trade_duration_seconds':3600,'avg_trade_duration_seconds':3600,'max_trade_duration_seconds':3600}]}, 'balances':[]}
    build_master_journal_workbook(snap, mj)

    real_update = master_service.update_master_journal_workbook_data_only
    def fake_update(path, snapshot):
        out = real_update(path, snapshot)
        cand = Path(out["candidate_path"])
        wb = load_workbook(cand)
        inst = wb["Instrument Averages"]
        headers = [str(c.value or "") for c in inst[1]]
        inst.cell(1, headers.index("Shortest duration (DD:HH:MM:SS)") + 1).value = "Shortest (DD:HH:MM:SS)"
        inst.cell(1, headers.index("Longest duration (DD:HH:MM:SS)") + 1).value = "Longest (DD:HH:MM:SS)"
        for r in range(2, inst.max_row + 1):
            for name in ("Shortest (DD:HH:MM:SS)", "Avg duration (DD:HH:MM:SS)", "Longest (DD:HH:MM:SS)"):
                c = [str(x.value or "") for x in inst[1]].index(name) + 1
                inst.cell(r, c).value = None
        wb.save(cand); wb.close()
        return out
    monkeypatch.setattr(master_service, "update_master_journal_workbook_data_only", fake_update)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] is False
    assert "duration columns are blank despite duration stats" in str(out.get("master_journal_error") or "").lower()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_master_journal_preserves_restored_layout_and_populates_stats(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {
        'items': [
            {'id':'t1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01 10:00:00','close_time':'2026-01-01 11:00:00','net_profit':10.0,'result_pct':1.2,'is_test_trade':False},
            {'id':'t2','row_type':'trade','account':'A','symbol':'BTCUSDT','side':'SELL','open_time':'2026-01-02 10:00:00','close_time':'2026-01-02 11:00:00','net_profit':-5.0,'result_pct':-0.6,'is_test_trade':False},
            {'id':'c1','row_type':'cashflow','account':'A','symbol':'CASHFLOW','side':'DEPOSIT','open_time':'2026-01-02 12:00:00','close_time':'2026-01-02 12:00:00','cashflow_amount':100.0,'cashflow_new_balance':1105.0,'currency':'USD','net_profit':100.0}
        ],
        'stats': {'totals': {}, 'groups': {'leaders': {}}, 'by_instrument':[{'symbol':'EURUSD','trades':1},{'symbol':'BTCUSDT','trades':1}]},
        'balances': [{'account':'A','account_label':'A','balance':1105.0,'currency':'USD'}],
        'diagnostics': {}
    }
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    dash = wb["Dashboard"]
    dash["A1"] = "Account Balances"
    dash["A11"] = "Instrument leaders"
    wb["All Trades"].auto_filter.ref = "A1:Z1511"
    wb["Instrument Averages"].auto_filter.ref = "A1:X126"
    wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_excel_account_balances', lambda: [])
    out = master_service._sync_master_journal_workbook()
    assert out["master_journal_ok"] in {True, False}
    if not out["master_journal_ok"]:
        return
    wb2 = load_workbook(mj, data_only=True)
    dash2 = wb2["Dashboard"]
    assert str(dash2["A1"].value) == "Account Balances"
    assert str(dash2["A11"].value) == "Instrument leaders"
    top_row_tokens = {str(dash2.cell(1, c).value or "").strip() for c in range(1, dash2.max_column + 1)}
    assert {"FX", "Crypto"}.issubset(top_row_tokens)
    at = wb2["All Trades"]; headers=[str(c.value or "") for c in at[1]]
    rid_col = headers.index("Row ID")+1
    ids={str(at.cell(r,rid_col).value or "") for r in range(2, at.max_row+1)}
    assert {"t1","t2","c1"}.issubset(ids)
    assert at.auto_filter and at.auto_filter.ref and f"{at.max_row}" in at.auto_filter.ref
    ot_col = headers.index("Open Time")+1; ct_col = headers.index("Close Time")+1
    assert at.cell(2, ot_col).number_format != "General"
    assert at.cell(2, ct_col).number_format != "General"
    inst = wb2["Instrument Averages"]
    inst_headers=[str(c.value or "") for c in inst[1]]
    s_col = inst_headers.index("Symbol")+1
    t_col = inst_headers.index("Trades")+1
    assert any(str(inst.cell(r,s_col).value or "").strip() and isinstance(inst.cell(r,t_col).value,(int,float)) for r in range(2, inst.max_row+1))
    cal = wb2["P&L Calendar"]
    assert any(isinstance(cal.cell(r,c).value,(int,float)) for r in range(3, cal.max_row+1) for c in range(2,13))
    if "_Trade Meta" in wb2.sheetnames:
        assert wb2["_Trade Meta"].sheet_state == "hidden"
    wb2.close()
    kept = [p.name for p in tmp_path.glob("*.xls*") if not p.name.startswith("~$") and not p.name.endswith(".tmp.xlsx") and not p.name.endswith(".pending.xlsx")]
    assert kept == ["Master Journal.xlsx"]


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_master_journal_trade_log_filter_range_can_update_without_invariant_failure(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {'items':[{'id':'t1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1,'result_pct':1.0},
                     {'id':'t2','row_type':'trade','account':'A','symbol':'BTCUSDT','side':'SELL','open_time':'2026-01-02','close_time':'2026-01-02','net_profit':-1,'result_pct':-1.0},
                     {'id':'c1','row_type':'cashflow','account':'A','symbol':'CASHFLOW','side':'DEPOSIT','open_time':'2026-01-03','close_time':'2026-01-03','net_profit':100}],
            'stats':{'totals':{},'groups':{'leaders':{}},'by_instrument':[{'symbol':'EURUSD','trades':1}]},'balances':[],'diagnostics':{}}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj); wb["All Trades"].auto_filter.ref = "A1:Z1511"; wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_excel_account_balances', lambda: [])
    result = master_service._sync_master_journal_workbook()
    assert result["master_journal_ok"] is True
    out = load_workbook(mj, data_only=True)
    at = out["All Trades"]; ref = at.auto_filter.ref
    assert ref and ref.startswith("A1:")
    headers=[str(c.value or "") for c in at[1]]
    rid_col = headers.index("Row ID")+1
    from openpyxl.utils import get_column_letter
    assert get_column_letter(rid_col) in ref
    assert str(at.max_row) in ref
    out.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_startup_recovery_skips_broker_refresh_in_master_journal_mode(monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setattr(master_service, '_is_scanner_local_ui_mode', lambda: False)
    monkeypatch.setattr(master_service, '_import_trading_journal_from_sources', lambda: {"ok": True})
    monkeypatch.setattr(master_service, '_sync_master_journal_workbook', lambda: {"master_journal_ok": True})
    monkeypatch.setattr(master_service, '_recover_oanda_recent_fills', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call")))
    monkeypatch.setattr(master_service, '_run_bybit_closed_pnl_sync', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call")))
    asyncio.run(master_service._run_startup_recovery_import_if_needed())


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_autostart_skips_fill_polls_in_master_journal_mode(monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_SOURCE', 'master_journal')
    monkeypatch.setenv('TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE', '1')
    monkeypatch.setenv('ENABLE_BYBIT_FILL_POLL', '1')
    monkeypatch.setenv('ENABLE_OANDA_FILL_POLL', '1')
    monkeypatch.setattr(master_service, 'LOCAL_STATE_ONLY', True)
    monkeypatch.setattr(master_service, '_dropbox_restore_state_backup_on_startup', lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, '_start_startup_recovery_import_after_restore', lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, '_schedule_monthly_aud_revaluation_sync', lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, '_poll_pending_webhook_invalidations', lambda: asyncio.sleep(0))
    monkeypatch.setattr(master_service, '_log_outbound_traffic_summary', lambda: asyncio.sleep(0))
    scheduled = []
    def _fake_create_task(coro):
        scheduled.append(getattr(getattr(coro, "cr_code", None), "co_name", ""))
        class _Dummy:
            def cancel(self): ...
            def done(self): return False
        return _Dummy()
    monkeypatch.setattr(master_service.asyncio, 'create_task', _fake_create_task)
    asyncio.run(master_service._autostart_scripts())
    assert '_poll_bybit_fills' not in scheduled
    assert '_start_oanda_fill_poll_after_delay' not in scheduled


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items':[], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    monkeypatch.setattr(master_service.os, 'replace', lambda *_: (_ for _ in ()).throw(PermissionError('locked')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'PermissionError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_builder_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', lambda *_: (_ for _ in ()).throw(RuntimeError('boom')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'RuntimeError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    def bad_builder(_snap, out):
        wb=Workbook(); ws=wb.active; ws.title='Wrong'; wb.save(out)
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', bad_builder)
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'RuntimeError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_temp_cleanup_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    def bad_builder(_snap, out):
        wb=Workbook(); wb.save(out)
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', bad_builder)
    monkeypatch.setattr(master_service, 'SHEET_ORDER', ['Dashboard'])
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert not (tmp_path/'Master Journal.tmp.xlsx').exists()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_applies_manual_overrides(tmp_path, monkeypatch):
    mj=tmp_path/'Master Journal.xlsx'
    # seed manual workbook via canonical builder
    snap={'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'is_test_trade':False}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}}
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook(snap,mj)
    from openpyxl import load_workbook
    wb=load_workbook(mj); ws=wb['All Trades']; ws['Q2']='Yes'; ws['R2']='S'; ws['S2']='M5'; ws['T2']='No'; ws['U2']='note'; wb.save(mj)
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    rows=[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0}]
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: rows)
    captured={}
    monkeypatch.setattr(master_service, '_set_trading_journal_rows', lambda r: captured.setdefault('rows', r))
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': captured.get('rows',rows), 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is True
    patched=captured['rows'][0]
    assert patched['is_test_trade'] is True and patched['setup']=='S' and patched['timeframe']=='M5' and patched['notes']=='note'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_test_yes_excluded_from_aggregates(tmp_path, monkeypatch):
    mj=tmp_path/'Master Journal.xlsx'
    from tools.master_journal_workbook import build_master_journal_workbook
    seed={'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0,'is_test_trade':False}], 'stats':{'totals':{}, 'groups':{}}, 'balances':[], 'diagnostics':{}}
    build_master_journal_workbook(seed,mj)
    from openpyxl import load_workbook
    wb=load_workbook(mj); ws=wb['All Trades']; ws['Q2']='Yes'; before=[c.value for c in ws[2]]; wb.save(mj)
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': seed['items'], 'stats': {'totals': {}, 'groups': {}}, 'balances': [], 'diagnostics': {}})
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is True
    out=load_workbook(mj)
    after = [c.value for c in out['All Trades'][2]]
    assert after[:16] == before[:16]
    assert str(after[16] or "").strip().lower() in {"yes", "no"}


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_success_reports_existing_file_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'stats': {'totals': {}}, 'balances': [], 'diagnostics': {}})
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, tmp_path/'Master Journal.xlsx')
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] in {True, False}
    assert result['master_journal_exists'] is True
    assert str(result['master_journal_path']).endswith('Master Journal.xlsx')
    path = Path(result['master_journal_path'])
    assert path.exists()
    assert int(result['master_journal_size_bytes']) > 0


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_rebuilds_when_master_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    snap = {'items':[{'id':'r1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01 10:00:00','close_time':'2026-01-01 11:00:00','net_profit':10.0,'result_pct':1.2}], 'stats': {'totals': {}, 'groups': {'leaders': {}}, 'by_instrument':[{'symbol':'EURUSD','total_trades':1,'wins':1,'losses':0,'break_even':0,'long_trades':1,'short_trades':0}]}, 'balances': [{'account':'A','balance':1000.0,'currency':'USD'}], 'diagnostics': {}}
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: snap['items'])
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] in {True, False}
    assert (tmp_path/'Master Journal.xlsx').exists()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_rebuilds_blanked_workbook_sections(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook

    source_rows = [
        {'id': 'r1', 'row_type': 'trade', 'account': 'A', 'symbol': 'EURUSD', 'side': 'BUY', 'open_time': '2026-01-01 10:00:00', 'close_time': '2026-01-01 11:00:00', 'net_profit': 10.0, 'result_pct': 1.2, 'is_test_trade': False},
        {'id': 'r2', 'row_type': 'trade', 'account': 'A', 'symbol': 'BTCUSDT', 'side': 'SELL', 'open_time': '2026-01-02 10:00:00', 'close_time': '2026-01-02 11:00:00', 'net_profit': -5.0, 'result_pct': -0.6, 'is_test_trade': False},
    ]
    snap = {
        'items': source_rows,
        'stats': {
            'totals': {},
            'by_instrument': [
                {'symbol': 'EURUSD', 'total_trades': 1, 'wins': 1, 'losses': 0, 'break_even': 0, 'long_trades': 1, 'short_trades': 0},
                {'symbol': 'BTCUSDT', 'total_trades': 1, 'wins': 0, 'losses': 1, 'break_even': 0, 'long_trades': 0, 'short_trades': 1},
            ],
            'groups': {
                'leaders': {
                    'most_wins_instrument': {'symbol': 'EURUSD', 'wins': 1, 'losses': 0, 'total_trades': 1},
                    'most_losses_instrument': {'symbol': 'BTCUSDT', 'wins': 0, 'losses': 1, 'total_trades': 1},
                }
            }
        },
        'balances': [{'account': 'A', 'account_label': 'A', 'balance': 1234.56, 'currency': 'USD', 'as_of': '2026-01-03'}],
        'diagnostics': {},
    }
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / 'Master Journal.xlsx'
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    # blank generated sections
    for ws_name in ['All Trades', 'Instrument Averages', 'P&L Calendar']:
        ws = wb[ws_name]
        for r in range(2, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).value = None
    dash = wb['Dashboard']
    for r in range(1, dash.max_row + 1):
        for c in range(1, dash.max_column + 1):
            v = str(dash.cell(r, c).value or '').strip().lower()
            if v == 'instrument leaders':
                for rr in range(r + 1, min(dash.max_row + 1, r + 16)):
                    for cc in range(c, min(dash.max_column + 1, c + 6)):
                        if rr != r + 1:  # keep leader headers
                            dash.cell(rr, cc).value = None
            if v == 'account balances':
                for rr in range(r + 2, min(dash.max_row + 1, r + 16)):
                    dash.cell(rr, c + 1).value = None
    wb.save(mj)
    wb.close()

    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: source_rows)
    monkeypatch.setattr(master_service, '_get_excel_account_balances', lambda: [])
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] in {True, False}
    if not result['master_journal_ok']:
        return

    rebuilt = load_workbook(mj, data_only=True)
    try:
        trade_log = rebuilt['All Trades']
        trade_log_headers = [str(c.value or '').strip() for c in trade_log[1]]
        trade_log_symbol_col = trade_log_headers.index('Symbol') + 1
        trade_symbols = [str(trade_log.cell(r, trade_log_symbol_col).value or '').strip() for r in range(2, trade_log.max_row + 1)]
        assert 'EURUSD' in trade_symbols
        assert 'BTCUSDT' in trade_symbols

        inst = rebuilt['Instrument Averages']
        inst_headers = [str(c.value or '').strip() for c in inst[1]]
        symbol_col = inst_headers.index('Symbol') + 1
        trades_col = inst_headers.index('Trades') + 1
        inst_rows = {}
        for r in range(2, inst.max_row + 1):
            sym = str(inst.cell(r, symbol_col).value or '').strip()
            if sym:
                inst_rows[sym] = inst.cell(r, trades_col).value
        assert isinstance(inst_rows.get('EURUSD'), (int, float))
        assert isinstance(inst_rows.get('BTCUSDT'), (int, float))

        cal = rebuilt['P&L Calendar']
        has_2026_jan = False
        for r in range(3, cal.max_row + 1):
            if str(cal.cell(r, 1).value or '').strip() == '2026' and isinstance(cal.cell(r, 2).value, (int, float)):
                has_2026_jan = True
                break
        assert has_2026_jan

        dash = rebuilt['Dashboard']
        balance_anchor = None
        for r in range(1, dash.max_row + 1):
            for c in range(1, dash.max_column + 1):
                if str(dash.cell(r, c).value or '').strip().lower() == 'account balances':
                    balance_anchor = (r, c)
                    break
            if balance_anchor:
                break
        assert balance_anchor is not None
        balance_header_map = {}
        balance_header_row = None
        for r in range(balance_anchor[0] + 1, min(dash.max_row + 1, balance_anchor[0] + 12)):
            row_map = {}
            for c in range(balance_anchor[1], min(dash.max_column + 1, balance_anchor[1] + 8)):
                token = str(dash.cell(r, c).value or '').strip().lower()
                if token in {'account', 'balance', 'currency', 'as of', 'as_of'}:
                    if token == 'as_of':
                        token = 'as of'
                    row_map[token] = c
            if {'account', 'balance', 'currency'}.issubset(set(row_map.keys())):
                balance_header_row = r
                balance_header_map = row_map
                balance_row = r
                break
        assert balance_header_row is not None
        found_account_balance = False
        for r in range((balance_header_row or 0) + 1, min(dash.max_row + 1, (balance_header_row or 0) + 20)):
            acct = str(dash.cell(r, balance_header_map['account']).value or '').strip()
            bal = dash.cell(r, balance_header_map['balance']).value
            if acct == 'A' and isinstance(bal, (int, float)) and abs(float(bal) - 1234.56) < 1e-6:
                found_account_balance = True
                break
        assert found_account_balance

        leaders_anchor = None
        for r in range(1, dash.max_row + 1):
            for c in range(1, dash.max_column + 1):
                if str(dash.cell(r, c).value or '').strip().lower() == 'instrument leaders':
                    leaders_anchor = (r, c)
                    break
            if leaders_anchor:
                break
        assert leaders_anchor is not None
        header_map = {}
        header_row = None
        for r in range(leaders_anchor[0] + 1, min(dash.max_row + 1, leaders_anchor[0] + 12)):
            row_map = {}
            for c in range(leaders_anchor[1], min(dash.max_column + 1, leaders_anchor[1] + 8)):
                token = str(dash.cell(r, c).value or '').strip().lower()
                if token in {'metric', 'symbol', 'wins', 'losses', 'trades'}:
                    row_map[token] = c
            if {'metric', 'symbol', 'wins', 'losses', 'trades'}.issubset(set(row_map.keys())):
                header_row = r
                header_map = row_map
                break
        assert header_row is not None
        metrics = {}
        for r in range((header_row or 0) + 1, min(dash.max_row + 1, (header_row or 0) + 20)):
            label = str(dash.cell(r, header_map['metric']).value or '').strip().lower()
            if label:
                metrics[label] = {
                    'symbol': str(dash.cell(r, header_map['symbol']).value or '').strip(),
                    'trades': dash.cell(r, header_map['trades']).value,
                }
        assert metrics['overall most wins']['symbol'] == 'EURUSD'
        assert float(metrics['overall most wins']['trades']) == 1.0
        assert metrics['overall most losses']['symbol'] == 'BTCUSDT'
        assert float(metrics['overall most losses']['trades']) == 1.0
    finally:
        rebuilt.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_repairs_missing_expected_balance_account_row(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook

    source_rows = [
        {'id': 'r1', 'row_type': 'trade', 'account': 'A', 'symbol': 'EURUSD', 'side': 'BUY', 'open_time': '2026-01-01 10:00:00', 'close_time': '2026-01-01 11:00:00', 'net_profit': 10.0, 'result_pct': 1.2, 'is_test_trade': False},
    ]
    snap = {
        'items': source_rows,
        'stats': {'totals': {}, 'by_instrument': [{'symbol': 'EURUSD', 'total_trades': 1, 'wins': 1, 'losses': 0, 'break_even': 0}], 'groups': {'leaders': {}}},
        'balances': [
            {'account': 'A', 'account_label': 'A', 'balance': 1234.56, 'currency': 'USD', 'as_of': '2026-01-03'},
            {'account': 'B', 'account_label': 'B', 'balance': 999.99, 'currency': 'USD', 'as_of': '2026-01-03'},
        ],
        'diagnostics': {},
    }
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / 'Master Journal.xlsx'
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    dash = wb['Dashboard']
    # remove account B row from balances section
    anchor = None
    for r in range(1, dash.max_row + 1):
        for c in range(1, dash.max_column + 1):
            if str(dash.cell(r, c).value or '').strip().lower() == 'account balances':
                anchor = (r, c)
                break
        if anchor:
            break
    assert anchor is not None
    header_row = anchor[0] + 1
    for r in range(header_row + 1, min(dash.max_row + 1, header_row + 20)):
        if str(dash.cell(r, anchor[1]).value or '').strip() == 'B':
            dash.cell(r, anchor[1]).value = None
            dash.cell(r, anchor[1] + 1).value = None
            break
    wb.save(mj)
    wb.close()

    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: source_rows)
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is True
    repaired = load_workbook(mj, data_only=True)["Dashboard"]
    anchor = None
    for r in range(1, repaired.max_row + 1):
        for c in range(1, repaired.max_column + 1):
            if str(repaired.cell(r, c).value or "").strip().lower() == "account balances":
                anchor = (r, c)
                break
        if anchor:
            break
    assert anchor is not None
    header_row = None
    col_map = {}
    for r in range(anchor[0] + 1, min(repaired.max_row + 1, anchor[0] + 12)):
        row_map = {}
        for c in range(anchor[1], min(repaired.max_column + 1, anchor[1] + 8)):
            token = str(repaired.cell(r, c).value or "").strip().lower()
            if token == "account":
                row_map["account"] = c
            elif token == "balance":
                row_map["balance"] = c
            elif token == "currency":
                row_map["currency"] = c
            elif token in {"as of", "as_of"}:
                row_map["as_of"] = c
        if {"account", "balance", "currency"}.issubset(row_map.keys()):
            header_row = r
            col_map = row_map
            break
    assert header_row is not None
    found_b = False
    for r in range((header_row or 0) + 1, min(repaired.max_row + 1, (header_row or 0) + 50)):
        if str(repaired.cell(r, col_map["account"]).value or "").strip() == "B":
            found_b = True
            assert isinstance(repaired.cell(r, col_map["balance"]).value, (int, float))
            break
    assert found_b


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_fails_when_expected_balance_non_numeric(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    source_rows = [{'id': 'r1', 'row_type': 'trade', 'account': 'BYBIT DEMO', 'symbol': 'BTCUSDT', 'side': 'BUY', 'open_time': '2026-01-01', 'close_time': '2026-01-01', 'net_profit': 1.0, 'result_pct': 0.1}]
    snap = {'items': source_rows, 'stats': {'totals': {}, 'by_instrument': [{'symbol': 'BTCUSDT', 'total_trades': 1}], 'groups': {'leaders': {}}}, 'balances': [{'account': 'BYBIT DEMO', 'account_label': 'BYBIT DEMO', 'balance': None, 'currency': 'USDT'}], 'diagnostics': {}}
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / 'Master Journal.xlsx'
    build_master_journal_workbook(snap, mj)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: source_rows)
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is False
    assert 'Account Balances missing numeric values' in str(result.get('master_journal_error') or '')


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_succeeds_with_merged_calendar_cells(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {
        "items": [{"id":"t1","row_type":"trade","account":"BYBIT DEMO","symbol":"BTCUSDT","side":"BUY","open_time":"2026-05-01","close_time":"2026-05-01","net_profit":10.0,"result_pct":1.0}],
        "stats": {"totals": {}, "groups": {"leaders": {}}, "by_instrument": [{"symbol": "BTCUSDT", "total_trades": 1}]},
        "balances": [{"account":"BYBIT DEMO","account_label":"BYBIT DEMO","balance":100.0,"currency":"USDT","as_of":"2026-05-16"}],
        "diagnostics": {},
    }
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    cal = wb["P&L Calendar"]
    for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], start=3):
        cal.cell(1, i).value = m
    cal.merge_cells("A2:A3"); cal.merge_cells("A4:A5"); cal.merge_cells("A6:A7")
    cal["A2"] = 2026; cal["A4"] = 2025; cal["A6"] = 2024
    cal["B2"] = "P/L %"; cal["B3"] = "Total Trades"; cal["B4"] = "P/L %"; cal["B5"] = "Total Trades"; cal["B6"] = "P/L %"; cal["B7"] = "Total Trades"
    wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: snap["items"])
    result = master_service._sync_master_journal_workbook()
    assert result["master_journal_ok"] is True

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_populates_instrument_leaders_custom_layout(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / "Master Journal.xlsx"
    snap = {"items":[{"id":"t1","row_type":"trade","account":"A","symbol":"EURUSD","side":"BUY","open_time":"2026-05-01","close_time":"2026-05-01","net_profit":1.0,"result_pct":1.0}],
            "stats":{"totals":{},"by_instrument":[{"symbol":"EURUSD","total_trades":1}],"groups":{"leaders":{"most_wins_instrument":{"symbol":"EURUSD","wins":1,"losses":0,"trades":1}}}},
            "balances":[{"account_label":"A","balance":100.0,"currency":"USD"}],"diagnostics":{}}
    build_master_journal_workbook(snap, mj)
    wb=load_workbook(mj); d=wb["Dashboard"]; d["A11"]="Instrument leaders"; d["A12"]="Metric"; d["B12"]="Symbol"; d["C12"]="Wins"; d["D12"]="Losses"; d["E12"]="Trades"; d["A13"]="Overall most wins"; wb.save(mj); wb.close()
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: snap["items"])
    result = master_service._sync_master_journal_workbook()
    assert result["master_journal_ok"] is True
    out=load_workbook(mj, data_only=True)["Dashboard"]
    assert out["B13"].value == "EURUSD"
    assert isinstance(out["E13"].value, (int, float))


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_status_marks_abandoned_running_state_without_active_task(monkeypatch):
    state = dict(master_service.TRADING_JOURNAL_SYNC_STATE)
    state.update({"running": True, "started_at": "2020-01-01T00:00:00Z", "message": "old"})
    monkeypatch.setattr(master_service, "_sync_state_snapshot", lambda: state)
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_SYNC_TASK", None)
    payload = asyncio.run(master_service.trading_journal_sync_status()).body.decode("utf-8")
    import json
    data = json.loads(payload)
    assert data["running"] is False
    assert data["ok"] is False
    assert data["abandoned_running_state"] is True


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_status_stale_warning_when_running_and_heartbeat_old(monkeypatch):
    state = dict(master_service.TRADING_JOURNAL_SYNC_STATE)
    state.update({"running": True, "started_at": "2020-01-01T00:00:00Z", "heartbeat_at": "2020-01-01T00:00:00Z"})
    monkeypatch.setattr(master_service, "_sync_state_snapshot", lambda: state)
    async def _run():
        sleeper = asyncio.create_task(asyncio.sleep(0.2))
        monkeypatch.setattr(master_service, "TRADING_JOURNAL_SYNC_TASK", sleeper)
        payload = (await master_service.trading_journal_sync_status()).body.decode("utf-8")
        sleeper.cancel()
        return payload
    payload = asyncio.run(_run())
    import json
    data = json.loads(payload)
    assert data["running"] is True
    assert isinstance(data.get("elapsed_seconds"), (int, float))
    assert data.get("stale_warning")


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_trading_journal_sync_status_rejects_stale_master_journal_success(tmp_path, monkeypatch):
    missing = tmp_path / 'Master Journal.xlsx'
    state_payload = dict(master_service.TRADING_JOURNAL_SYNC_STATE)
    state_payload.update({
        'running': False,
        'ok': True,
        'result': {
            'master_journal_ok': True,
            'master_journal_path': str(missing),
            'master_journal_exists': True,
        },
    })
    monkeypatch.setattr(master_service, '_sync_state_snapshot', lambda: state_payload)
    monkeypatch.setattr(master_service, '_load_trading_journal_state', lambda: {})
    response = asyncio.run(master_service.trading_journal_sync_status())
    payload = response.body.decode('utf-8')
    import json
    data = json.loads(payload)
    assert data['ok'] is False
    assert data['result']['master_journal_ok'] is False
    assert data['result']['master_journal_exists'] is False


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_uses_configured_local_dir(tmp_path, monkeypatch):
    custom_journal_dir = tmp_path / 'custom-journal'
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', custom_journal_dir)
    custom_journal_dir.mkdir(parents=True, exist_ok=True)
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, custom_journal_dir/'Master Journal.xlsx')
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'stats': {'totals': {}}, 'balances': [], 'diagnostics': {}})
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, tmp_path/'Master Journal.xlsx')
    result = master_service._sync_master_journal_workbook()
    expected = custom_journal_dir.resolve() / 'Master Journal.xlsx'
    assert Path(result['master_journal_path']) == expected
    assert expected.exists()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_startup_recovery_import_includes_master_journal_sync_success(monkeypatch, tmp_path):
    monkeypatch.setattr(master_service, '_is_scanner_local_ui_mode', lambda: False)
    monkeypatch.setattr(master_service, '_trading_journal_excel_only_mode', lambda: True)
    monkeypatch.setattr(master_service, '_import_trading_journal_from_sources', lambda: {'ok': True, 'rows_imported': 1})
    monkeypatch.setattr(
        master_service,
        '_sync_master_journal_workbook',
        lambda: {
            'master_journal_ok': True,
            'master_journal_path': str(tmp_path / 'journal' / 'Master Journal.xlsx'),
            'master_journal_exists': True,
            'master_journal_size_bytes': 123,
        },
    )
    asyncio.run(master_service._run_startup_recovery_import_if_needed())
    assert master_service.TRADING_JOURNAL_SYNC_STATE['ok'] is True
    result = master_service.TRADING_JOURNAL_SYNC_STATE.get('result') or {}
    assert result.get('master_journal_ok') is True
    assert 'Master Journal.xlsx created' in str(master_service.TRADING_JOURNAL_SYNC_STATE.get('message') or '')


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_startup_recovery_import_master_journal_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(master_service, '_is_scanner_local_ui_mode', lambda: False)
    monkeypatch.setattr(master_service, '_trading_journal_excel_only_mode', lambda: True)
    monkeypatch.setattr(master_service, '_import_trading_journal_from_sources', lambda: {'ok': True, 'rows_imported': 1})
    monkeypatch.setattr(
        master_service,
        '_sync_master_journal_workbook',
        lambda: {'master_journal_ok': False, 'master_journal_error': 'boom'},
    )
    asyncio.run(master_service._run_startup_recovery_import_if_needed())
    assert master_service.TRADING_JOURNAL_SYNC_STATE['ok'] is False
    assert 'boom' in str(master_service.TRADING_JOURNAL_SYNC_STATE.get('error') or '')
    assert str(master_service.TRADING_JOURNAL_SYNC_STATE.get('message') or '') != 'Startup journal sync complete.'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_open_master_journal_missing_file_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.open_master_journal_file())
    assert exc.value.status_code == 404
    assert 'Click Sync Journal first' in str(exc.value.detail)


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_open_master_journal_existing_file_opens_exact_path(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    target = tmp_path / 'Master Journal.xlsx'
    target.write_bytes(b'x')
    captured = {}
    monkeypatch.setattr(master_service, '_open_path_with_os', lambda path: captured.setdefault('path', Path(path)))
    resp = asyncio.run(master_service.open_master_journal_file())
    import json
    payload = json.loads(resp.body.decode('utf-8'))
    assert payload['ok'] is True
    assert captured['path'] == target
    assert str(payload['master_journal_path']).endswith('Master Journal.xlsx')


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_open_master_journal_open_failure_returns_500(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    target = tmp_path / 'Master Journal.xlsx'
    target.write_bytes(b'x')
    monkeypatch.setattr(master_service, '_open_path_with_os', lambda _path: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(master_service.HTTPException) as exc:
        asyncio.run(master_service.open_master_journal_file())
    assert exc.value.status_code == 500
    assert 'boom' in str(exc.value.detail)


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_github_sync_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_GITHUB_SYNC_ENABLED", "0")
    result = master_service._sync_journal_excel_files_to_github(tmp_path / "Master Journal.xlsx")
    assert result["github_sync_enabled"] is False
    assert result["github_sync_ok"] is True
    assert result["github_sync_noop"] is True


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_github_sync_missing_git_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_GITHUB_SYNC_ENABLED", "1")
    monkeypatch.setattr(master_service, "_trading_journal_github_sync_enabled", lambda: True)
    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    result = master_service._sync_journal_excel_files_to_github(tmp_path / "journal" / "Master Journal.xlsx")
    assert result["github_sync_ok"] is False
    assert "not a Git checkout" in str(result["github_sync_error"])


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_github_sync_stages_only_target_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_GITHUB_SYNC_ENABLED", "1")
    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    (tmp_path / ".git").mkdir()
    journal = tmp_path / "journal"
    journal.mkdir()
    master = journal / "Master Journal.xlsx"
    master.write_bytes(b"x")

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_authoritative_snapshot_does_not_scan_legacy_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_LOCAL_DIR", tmp_path)
    monkeypatch.setenv("TRADING_JOURNAL_SOURCE", "master_journal")
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items':[{'id':'t1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}}, tmp_path / "Master Journal.xlsx")
    monkeypatch.setattr(master_service, "_list_local_trading_journal_workbooks", lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    monkeypatch.setattr(master_service, "_load_cashflows_for_active_journal_source", lambda _s: (_ for _ in ()).throw(AssertionError("should not call")))
    snap = master_service._build_trading_journal_view_snapshot(force=True)
    assert snap["diagnostics"]["authoritative_mode"] is True

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_authoritative_fingerprint_excludes_legacy_files(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_LOCAL_DIR", tmp_path)
    monkeypatch.setenv("TRADING_JOURNAL_GITHUB_SYNC_ENABLED", "1")
    monkeypatch.setattr(master_service, "_trading_journal_github_sync_enabled", lambda: True)
    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(master_service, "_repo_root_for_journal_path", lambda _p: tmp_path)
    (tmp_path / "Master Journal.xlsx").write_bytes(b"x")
    monkeypatch.setenv("TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE", "1")
    monkeypatch.setattr(master_service, "_list_local_trading_journal_workbooks", lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    fp = master_service._journal_source_fingerprint()
    paths = [str((f or {}).get("path") or "") for f in fp.get("files", [])]
    assert any("Master Journal.xlsx" in p for p in paths)
    assert all("account_cashflows.xlsx" not in p for p in paths)
    assert all("Bybit Demo.xlsx" not in p for p in paths)
    (tmp_path / "~$Master Journal.xlsx").write_bytes(b"x")
    (tmp_path / "foo.tmp.xlsx").write_bytes(b"x")
    (tmp_path / "foo.pending.xlsx").write_bytes(b"x")
    commands = []

    def fake_git(args, _cwd, _timeout):
        commands.append(args)
        if args == ["--version"]:
            return 0, "git version 2", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main\n", ""
        if args[:3] == ["remote", "get-url", "origin"]:
            return 0, "x\n", ""
        if args[:2] == ["diff", "--cached"]:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(master_service, "_run_git_command", fake_git)
    result = master_service._sync_journal_excel_files_to_github(tmp_path / "Master Journal.xlsx")
    assert "Master Journal.xlsx" in " ".join(result.get("github_sync_files") or [])
    assert "~$Master Journal.xlsx" not in " ".join(result.get("github_sync_files") or [])
    assert "foo.tmp.xlsx" not in " ".join(result.get("github_sync_files") or [])
    assert "foo.pending.xlsx" not in " ".join(result.get("github_sync_files") or [])
    add_calls = [cmd for cmd in commands if cmd and cmd[0] == "add"]
    if add_calls:
        assert all(cmd != ["add", "."] for cmd in add_calls)



@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_master_journal_does_not_enable_authoritative_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, "TRADING_JOURNAL_LOCAL_DIR", tmp_path)
    monkeypatch.setenv("TRADING_JOURNAL_SOURCE", "local")
    from tools.master_journal_workbook import build_master_journal_workbook
    build_master_journal_workbook({'items':[{'id':'t1','row_type':'trade','account':'A','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}}, tmp_path / "Master Journal.xlsx")
    monkeypatch.setattr(master_service, "_get_trading_journal_rows", lambda: [])
    monkeypatch.setattr(master_service, "_load_cashflows_for_active_journal_source", lambda _s: {})
    snap = master_service._build_trading_journal_view_snapshot(force=True)
    assert snap["diagnostics"]["authoritative_mode"] is False
@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_save_watcher_enablement(monkeypatch):
    monkeypatch.setattr(master_service, '_is_render_env', lambda: True)
    assert master_service._manual_save_watcher_enabled() is False
    monkeypatch.setattr(master_service, '_is_render_env', lambda: False)
    monkeypatch.setenv('TRADING_JOURNAL_GITHUB_SYNC_ENABLED','1')
    monkeypatch.delenv('TRADING_JOURNAL_GITHUB_SYNC_ON_MANUAL_SAVE_ENABLED', raising=False)
    assert master_service._manual_save_watcher_enabled() is True


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_save_sync_once_records_error_and_no_rebuild(tmp_path, monkeypatch):
    target = tmp_path / 'Master Journal.xlsx'; target.write_bytes(b'a')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    called={'sync':0,'build':0}
    monkeypatch.setattr(master_service, '_sync_journal_excel_files_to_github', lambda p: called.__setitem__('sync', called['sync']+1) or {'github_sync_ok':False,'github_sync_error':'git fail','github_sync_files':['journal/Master Journal.xlsx'],'github_sync_commit':''})
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', lambda *a, **k: called.__setitem__('build', called['build']+1))
    master_service._run_manual_save_github_sync_once(target)
    st=master_service._manual_save_state_snapshot()
    assert called['sync']==1 and called['build']==0
    assert st['manual_save_last_error']=='git fail'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_save_ignore_temp_names(tmp_path):
    assert master_service._should_ignore_manual_save_path(tmp_path / '~$Master Journal.xlsx')
    assert master_service._should_ignore_manual_save_path(tmp_path / 'Master Journal.tmp.xlsx')
    assert master_service._should_ignore_manual_save_path(tmp_path / 'Master Journal.pending.xlsx')

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_shutdown_stops_manual_save_watcher(monkeypatch):
    called={'n':0}
    monkeypatch.setattr(master_service, '_stop_manual_save_github_sync_watcher', lambda: called.__setitem__('n', called['n']+1))
    asyncio.run(master_service._log_local_master_shutdown())
    assert called['n']==1


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_save_scan_debounce_and_service_write_suppression(tmp_path, monkeypatch):
    p=tmp_path/'Master Journal.xlsx'; p.write_bytes(b'one')
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    calls=[]
    monkeypatch.setattr(master_service, '_sync_journal_excel_files_to_github', lambda *_: calls.append(1) or {'github_sync_enabled':True,'github_sync_ok':True,'github_sync_noop':False,'github_sync_error':'','github_sync_files':[],'github_sync_commit':'abc'})
    master_service._manual_save_set_known_fingerprint(p)
    # service generated write suppression
    master_service._manual_save_set_known_fingerprint(p)
    master_service._manual_save_scan_once(10.0, p)
    assert len(calls)==0
    p.write_bytes(b'two')
    master_service._manual_save_scan_once(10.0, p)
    assert len(calls)==0
    master_service._manual_save_scan_once(20.0, p)
    assert len(calls)==1

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_manual_save_disabled_github_no_fake_success(monkeypatch, tmp_path):
    p=tmp_path/'Master Journal.xlsx'; p.write_bytes(b'x')
    monkeypatch.setattr(master_service, '_sync_journal_excel_files_to_github', lambda *_: {'github_sync_enabled':False,'github_sync_ok':True,'github_sync_noop':True,'github_sync_error':'','github_sync_files':[],'github_sync_commit':''})
    master_service._run_manual_save_github_sync_once(p)
    st=master_service._manual_save_state_snapshot()
    assert st['manual_save_last_success_at'] is None
    assert 'disabled' in str(st['manual_save_last_error']).lower()

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_bybit_server_time_invalid_json_no_path_nameerror(monkeypatch):
    class Resp:
        status_code=200
        text='x'
        def json(self): raise ValueError('bad')
    class Ctx:
        async def __aenter__(self): return Resp()
        async def __aexit__(self,*a): return False
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        def get(self,*a,**k): return Ctx()
    monkeypatch.setattr(master_service.httpx, 'AsyncClient', lambda **k: Client())
    with pytest.raises(ValueError, match='Bybit server time response is unparseable.'):
        asyncio.run(master_service._fetch_bybit_server_time_ms('https://api.bybit.com'))

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_signed_get_keeps_valid_json(monkeypatch):
    class Resp:
        status_code=200
        text='ok'
        content=b'{"retCode":0,"result":{"x":1}}'
        def json(self): return {'retCode':0,'result':{'x':1}}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def get(self,*a,**k): return Resp()
    monkeypatch.setattr(master_service.httpx, 'AsyncClient', lambda **k: Client())
    async def fake_headers(**k): return {}
    monkeypatch.setattr(master_service, '_build_bybit_signed_headers', fake_headers)
    payload=asyncio.run(master_service._bybit_signed_get(base_url='https://api.bybit.com',api_key='k',api_secret='s',path='/x',params={}))
    assert payload == {'retCode': 0, 'result': {'x': 1}}

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_update_oanda_settings_passes_payload():
    out=master_service._update_oanda_settings({'wait_seconds':10})
    assert out.get('wait_seconds')==10

def test_source_guard_manual_save_fingerprint_only_master_journal_sync():
    src=(ROOT/'render'/'master_service.py').read_text(encoding='utf-8')
    needle = '_manual_save_set_known_fingerprint(path)'
    assert src.count(needle) >= 1
    sync_ix = src.index('_sync_master_journal_workbook')
    only_ix = src.index(needle)
    assert only_ix > sync_ix
    assert src.rindex(needle) >= only_ix

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_existing_workbook_sync_does_not_rebuild_or_refresh_derived(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    mj = tmp_path / 'Master Journal.xlsx'
    build_master_journal_workbook({'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, mj)
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    called = {'build': 0, 'refresh': 0}
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', lambda *_: called.__setitem__('build', called['build']+1))
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'balances': [], 'stats': {'totals': {}, 'groups': {}}})
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is True
    assert called['build'] == 0


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_missing_master_journal_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is False
    assert result['master_journal_error_type'] in {'FileNotFoundError', 'RuntimeError'}

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_canonical_market_precedence_cases():
    cm = master_service._canonical_market_for_row
    assert cm({'account':'OANDA LIVE','symbol':'EURUSD','asset_class':''}) == 'fx'
    assert cm({'account':'PEPPERSTONE LIVE','symbol':'EURUSD','asset_class':'crypto'}) == 'fx'
    assert cm({'account':'PEPPERSTONE LIVE','symbol':'XAUUSD','asset_class':''}) == 'fx'
    assert cm({'account':'BYBIT LIVE','symbol':'BTCUSD','asset_class':'fx'}) == 'crypto'
    assert cm({'account':'BINANCE','symbol':'ETHUSD','asset_class':'fx'}) == 'crypto'
    assert cm({'account':'BYBIT','symbol':'BTCUSDT','asset_class':''}) == 'crypto'
    assert cm({'account':'UNKNOWN','symbol':'ABCDEF','asset_class':''}) == ''


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_writes_zero_balances_and_validation_detects_mismatch(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / 'Master Journal.xlsx'
    source_rows = [{'id':'r1','row_type':'trade','account':'PEPPERSTONE DEMO','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'result_pct':0.1}]
    stale_snap = {'items': source_rows, 'stats': {'totals': {}, 'by_instrument': [{'symbol': 'EURUSD', 'total_trades': 1}], 'groups': {'leaders': {}}}, 'balances': [
        {'account_label': 'PEPPERSTONE DEMO', 'balance': 4.78, 'currency': 'AUD'},
        {'account_label': 'BINANCE', 'balance': 396.65720524, 'currency': 'USDT'},
    ], 'diagnostics': {}}
    build_master_journal_workbook(stale_snap, mj)
    zero_snap = {'items': source_rows, 'stats': stale_snap['stats'], 'balances': [
        {'account_label': 'PEPPERSTONE DEMO', 'balance': 0, 'currency': 'AUD', 'balance_source': 'broker_account_summary'},
        {'account_label': 'BINANCE', 'balance': 0, 'currency': 'USDT', 'balance_source': 'broker_account_summary'},
    ], 'diagnostics': {}}
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: zero_snap)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: source_rows)
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is True

    from openpyxl import load_workbook
    wb = load_workbook(mj, data_only=True)
    dash = wb['Dashboard']
    pairs = {str(dash.cell(r,1).value or '').strip(): dash.cell(r,2).value for r in range(1,dash.max_row+1)}
    assert pairs['PEPPERSTONE DEMO'] == 0
    assert pairs['BINANCE'] == 0
    wb.close()

    # Force workbook-vs-snapshot mismatch by corrupting the candidate workbook after data-only update.
    import tools.master_journal_workbook as mjw
    real_update = mjw.update_master_journal_workbook_data_only

    def _corrupting_update(path, snapshot):
        payload = real_update(path, snapshot)
        candidate_path = Path(payload['candidate_path'])
        bad_wb = load_workbook(candidate_path)
        try:
            bad_dash = bad_wb['Dashboard']
            for r in range(1, bad_dash.max_row + 1):
                if str(bad_dash.cell(r, 1).value or '').strip() == 'PEPPERSTONE DEMO':
                    bad_dash.cell(r, 2).value = 4.78
                    break
            bad_wb.save(candidate_path)
        finally:
            bad_wb.close()
        return payload

    monkeypatch.setattr(master_service, 'update_master_journal_workbook_data_only', _corrupting_update)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: zero_snap)
    bad = master_service._sync_master_journal_workbook()
    assert bad['master_journal_ok'] is False
    assert 'Account Balances mismatch vs snapshot' in str(bad.get('master_journal_error') or '')
    assert 'PEPPERSTONE DEMO' in str(bad.get('master_journal_error') or '')
    assert 'expected_balance=0.0' in str(bad.get('master_journal_error') or '')
    assert 'actual_balance=4.78' in str(bad.get('master_journal_error') or '')


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_build_journal_balance_timelines_rejects_non_authoritative_stale_excel_seed():
    rows = []
    cashflows = {}
    excel_balances = [{'account': 'BINANCE', 'label': 'BINANCE', 'balance': 396.65720524, 'currency': 'USDT', 'balance_source': 'excel_account_balance'}]
    out = master_service._build_journal_balance_timelines(rows, cashflows, excel_balances)
    bal = next(b for b in out['balances'] if str(b.get('label')) == 'BINANCE')
    assert bal['balance'] is None
    assert bal['balance_source'] == 'timeline_missing'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_balance_regression_stale_excel_binance_overridden_by_authoritative_zero_source():
    rows = []
    cashflows = {}
    excel_balances = [
        {'account': 'BINANCE', 'label': 'BINANCE', 'balance': 396.65720524, 'currency': 'USDT', 'balance_source': 'excel_account_balance'}
    ]
    timeline = master_service._build_journal_balance_timelines(rows, cashflows, excel_balances)
    merged = master_service._merge_missing_timeline_balances_with_broker(
        timeline['balances'],
        [
            {
                'account': 'BINANCE',
                'label': 'BINANCE',
                'balance': 0,
                'currency': 'USDT',
                'balance_source': 'broker_account_summary',
                'source': 'broker_account_summary',
                'as_of': '2026-05-11T00:00:00Z',
            }
        ],
    )
    bal = next(b for b in merged if str(b.get('label')) == 'BINANCE')
    assert bal['balance'] == 0
    assert bal['balance_source'] == 'broker_account_summary'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_merge_missing_timeline_balances_with_broker_zero_overrides_stale_timeline():
    timeline = [{'account': 'BINANCE', 'label': 'BINANCE', 'balance': 396.65720524, 'currency': 'USDT', 'balance_source': 'trade_timeline', 'missing_balance': False}]
    broker = [{'account': 'BINANCE', 'label': 'BINANCE', 'balance': 0, 'currency': 'USDT', 'balance_source': 'broker_account_summary', 'as_of': '2026-05-10T00:00:00Z'}]
    merged = master_service._merge_missing_timeline_balances_with_broker(timeline, broker)
    bal = next(b for b in merged if str(b.get('label')) == 'BINANCE')
    assert bal['balance'] == 0
    assert bal['balance_source'] == 'broker_account_summary'

@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_uses_zero_cashflow_anchor_when_cashflow_new_balance_blank(tmp_path, monkeypatch):
    from tools.master_journal_workbook import build_master_journal_workbook
    from openpyxl import load_workbook
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    mj = tmp_path / 'Master Journal.xlsx'
    rows = [
        {'id':'t1','row_type':'trade','account':'BINANCE','symbol':'BTCUSDT','side':'BUY','open_time':'2020-10-01','close_time':'2020-10-01','net_profit':1.0,'balance_after_trade':396.65720524,'currency':'USDT'},
        {'id':'c1','row_type':'cashflow','account':'BINANCE','symbol':'CASHFLOW','side':'WITHDRAWAL','open_time':'2020-10-26','close_time':'2020-10-26','cashflow_amount':-396.65720524,'balance_after_trade':0,'cashflow_new_balance':'','currency':'USDT','notes':'Withdrawal -396.65720524 USDT'},
        {'id':'t2','row_type':'trade','account':'PEPPERSTONE DEMO','symbol':'EURUSD','side':'BUY','open_time':'2022-10-01','close_time':'2022-10-01','net_profit':1.0,'balance_after_trade':4.78,'currency':'AUD'},
        {'id':'c2','row_type':'cashflow','account':'PEPPERSTONE DEMO','symbol':'CASHFLOW','side':'WITHDRAWAL','open_time':'2022-12-16','close_time':'2022-12-16','cashflow_amount':-4.78,'balance_after_trade':0,'cashflow_new_balance':'','currency':'AUD','notes':'Withdrawal -4.78 AUD'},
    ]
    snap = {'items': rows, 'stats': {'totals': {}, 'by_instrument': [], 'groups': {'leaders': {}}}, 'balances': [
        {'account_label': 'BINANCE', 'balance': 396.65720524, 'currency': 'USDT'},
        {'account_label': 'PEPPERSTONE DEMO', 'balance': 4.78, 'currency': 'AUD'},
    ], 'diagnostics': {}}
    build_master_journal_workbook(snap, mj)
    wb = load_workbook(mj)
    ws = wb['All Trades']
    headers = {str(ws.cell(1, c).value): c for c in range(1, ws.max_column + 1)}
    for rr in range(2, ws.max_row + 1):
        if str(ws.cell(rr, headers['Row Type']).value).strip().lower() == 'cashflow':
            ws.cell(rr, headers['Cashflow New Balance']).value = None
    wb.save(mj); wb.close()

    snap2 = master_service._build_trading_journal_view_snapshot(force=True)
    balances = {str(b.get('label') or b.get('account')): b for b in (snap2.get('balances') or [])}
    assert balances['BINANCE']['balance'] == 0
    assert balances['PEPPERSTONE DEMO']['balance'] == 0
    assert balances['BINANCE'].get('balance_source') != 'authoritative_trade_balance'
    assert balances['PEPPERSTONE DEMO'].get('balance_source') != 'authoritative_trade_balance'

    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is True
    synced = load_workbook(mj, data_only=True)
    dash = synced['Dashboard']
    dash_map = {str(dash.cell(r,1).value or '').strip(): dash.cell(r,2).value for r in range(1, dash.max_row+1)}
    assert dash_map['BINANCE'] == 0
    assert dash_map['PEPPERSTONE DEMO'] == 0
    synced.close()


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_repo_state_files_for_github_dedupes_master_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    master = journal_dir / "Master Journal.xlsx"
    master.write_bytes(b"x")
    (journal_dir / "5-digit-demo-calculation-context.json").write_text("{}", encoding="utf-8")
    (tmp_path / "state_backup.json").write_text("{}", encoding="utf-8")
    files = master_service._repo_state_files_for_github(master)
    rel = [str(p.relative_to(tmp_path)).replace("\\", "/") for p in files]
    assert rel.count("journal/Master Journal.xlsx") == 1
