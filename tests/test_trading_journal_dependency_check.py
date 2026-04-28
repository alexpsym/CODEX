import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "tools" / "check_trading_journal_deps.py"


@pytest.fixture
def depcheck_module(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("check_trading_journal_deps", CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_dep_check_reports_ok_when_all_modules_present(depcheck_module):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(depcheck_module.importlib.util, "find_spec", lambda _name: object())
    try:
        missing = depcheck_module.find_missing_modules()
        assert missing == []
        summary = depcheck_module.build_summary(missing, python_exe="python")
        assert summary["ok"] is True
        assert summary["local_xls_supported"] is True
    finally:
        monkeypatch.undo()


def test_dep_check_reports_missing_xlrd(depcheck_module):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        depcheck_module.importlib.util,
        "find_spec",
        lambda name: None if name == "xlrd" else object(),
    )
    try:
        missing = depcheck_module.find_missing_modules()
        assert missing == ["xlrd"]
        summary = depcheck_module.build_summary(missing)
        assert summary["ok"] is False
        assert "xlrd" in summary["missing"]
        assert summary["local_xls_supported"] is False
    finally:
        monkeypatch.undo()


def test_dep_check_reports_multiple_missing_modules(depcheck_module):
    missing_names = {"xlrd", "httpx", "dotenv"}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        depcheck_module.importlib.util,
        "find_spec",
        lambda name: None if name in missing_names else object(),
    )
    try:
        missing = depcheck_module.find_missing_modules()
        assert missing == ["xlrd", "dotenv", "httpx"]
    finally:
        monkeypatch.undo()


def test_dep_check_cli_emits_json():
    completed = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.stdout.strip()
    payload = json.loads(completed.stdout.strip())
    assert "missing" in payload
    assert "local_xls_requires" in payload
    assert payload["local_xls_requires"] == "xlrd"
    assert completed.returncode in {0, 1}
