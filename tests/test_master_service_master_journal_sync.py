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


def test_master_service_sync_test_bootstrap():
    assert True


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
    wb=load_workbook(mj); ws=wb['All Trades']; ws['U2']='Yes'; ws['V2']='S'; ws['W2']='M5'; ws['X2']='No'; ws['Y2']='note'; wb.save(mj)
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
    seed={'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0,'is_test_trade':False}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}}
    build_master_journal_workbook(seed,mj)
    from openpyxl import load_workbook
    wb=load_workbook(mj); ws=wb['All Trades']; ws['U2']='Yes'; wb.save(mj)
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    rows=[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0}]
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: rows)
    monkeypatch.setattr(master_service, '_set_trading_journal_rows', lambda r: None)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0,'is_test_trade':True}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is True
    out=load_workbook(mj)
    assert out['All Trades']['U2'].value == 'Yes'
    inst_symbols=[out['Instrument Averages'].cell(i,1).value for i in range(2,out['Instrument Averages'].max_row+1)]
    assert 'EURUSD' not in inst_symbols


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_success_reports_existing_file_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'stats': {'totals': {}}, 'balances': [], 'diagnostics': {}})
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is True
    assert result['master_journal_exists'] is True
    assert str(result['master_journal_path']).endswith('Master Journal.xlsx')
    path = Path(result['master_journal_path'])
    assert path.exists()
    assert int(result['master_journal_size_bytes']) > 0


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_replace_without_final_file_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'stats': {'totals': {}}, 'balances': [], 'diagnostics': {}})

    def fake_replace(src, dst):
        Path(src).unlink(missing_ok=True)
        Path(dst).unlink(missing_ok=True)

    monkeypatch.setattr(master_service.os, 'replace', fake_replace)
    result = master_service._sync_master_journal_workbook()
    assert result['master_journal_ok'] is False
    assert result['master_journal_exists'] is False
    assert 'was not created' in str(result['master_journal_error'])


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
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items': [], 'stats': {'totals': {}}, 'balances': [], 'diagnostics': {}})
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
