import importlib.util
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
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items':[], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    monkeypatch.setattr(master_service.os, 'replace', lambda *_: (_ for _ in ()).throw(PermissionError('locked')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'PermissionError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_builder_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', lambda *_: (_ for _ in ()).throw(RuntimeError('boom')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'RuntimeError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    def bad_builder(_snap, out):
        wb=Workbook(); ws=wb.active; ws.title='Wrong'; wb.save(out)
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', bad_builder)
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'RuntimeError'


@pytest.mark.skipif(not AVAILABLE, reason='master_service optional deps unavailable')
def test_sync_master_journal_temp_cleanup_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    def bad_builder(_snap, out):
        wb=Workbook(); wb.save(out)
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', bad_builder)
    monkeypatch.setattr(master_service, 'SHEET_ORDER', ['Dashboard'])
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert not (tmp_path/'Master Journal.tmp.xlsx').exists()
