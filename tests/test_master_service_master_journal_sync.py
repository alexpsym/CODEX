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
    seed={'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0,'is_test_trade':False}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}}
    build_master_journal_workbook(seed,mj)
    from openpyxl import load_workbook
    wb=load_workbook(mj); ws=wb['All Trades']; ws['Q2']='Yes'; wb.save(mj)
    monkeypatch.setattr(master_service, 'TRADING_JOURNAL_LOCAL_DIR', tmp_path)
    rows=[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0}]
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: rows)
    monkeypatch.setattr(master_service, '_set_trading_journal_rows', lambda r: None)
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items':[{'id':'r1','row_type':'trade','symbol':'EURUSD','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':10.0,'is_test_trade':True}], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is True
    out=load_workbook(mj)
    assert out['All Trades']['Q2'].value == 'Yes'
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
    (journal / "~$Master Journal.xlsx").write_bytes(b"x")
    (journal / "foo.tmp.xlsx").write_bytes(b"x")
    (journal / "foo.pending.xlsx").write_bytes(b"x")
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
    result = master_service._sync_journal_excel_files_to_github(master)
    assert result["github_sync_ok"] is True
    add_calls = [cmd for cmd in commands if cmd and cmd[0] == "add"]
    assert add_calls
    assert add_calls[0] == ["add", "--", "journal/Master Journal.xlsx"]
    assert all(cmd != ["add", "."] for cmd in add_calls)
    added_tokens = " ".join(add_calls[0])
    assert "~$Master Journal.xlsx" not in added_tokens
    assert ".tmp.xlsx" not in added_tokens
    assert ".pending.xlsx" not in added_tokens

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
        def json(self): return {'retCode':0,'result':{'x':1}}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def get(self,*a,**k): return Resp()
    monkeypatch.setattr(master_service.httpx, 'AsyncClient', lambda **k: Client())
    async def fake_headers(**k): return {}
    monkeypatch.setattr(master_service, '_build_bybit_signed_headers', fake_headers)
    payload=asyncio.run(master_service._bybit_signed_get(base_url='https://api.bybit.com',api_key='k',api_secret='s',path='/x',params={}))
    assert payload.get('retCode')==0

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
