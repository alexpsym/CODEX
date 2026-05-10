import pytest
import importlib.util
from pathlib import Path
import sys
import types
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'render' / 'master_service.py'

for name in ['httpx','requests']:
    if name not in sys.modules:
        sys.modules[name]=types.SimpleNamespace()

spec = importlib.util.spec_from_file_location('ms_sync_test', MODULE_PATH)
master_service = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(master_service)
except Exception as exc:  # pragma: no cover
    pytest.skip(f'master_service import unavailable: {exc}', allow_module_level=True)


def test_sync_master_journal_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    monkeypatch.setattr(master_service, '_get_trading_journal_rows', lambda: [])
    monkeypatch.setattr(master_service, '_build_trading_journal_view_snapshot', lambda force=True: {'items':[], 'stats':{'totals':{}}, 'balances':[], 'diagnostics':{}})
    monkeypatch.setattr(master_service.os, 'replace', lambda *_: (_ for _ in ()).throw(PermissionError('locked')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'PermissionError'


def test_sync_master_journal_builder_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(master_service, 'MASTER_JOURNAL_PATH', tmp_path/'Master Journal.xlsx')
    monkeypatch.setattr(master_service, 'build_master_journal_workbook', lambda *_: (_ for _ in ()).throw(RuntimeError('boom')))
    r=master_service._sync_master_journal_workbook()
    assert r['master_journal_ok'] is False
    assert r['master_journal_error_type'] == 'RuntimeError'
    assert 'boom' in r['master_journal_error']
