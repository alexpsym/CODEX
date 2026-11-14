import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import run_ltt_regressions as runner


def test_profile_selection_and_bounded_results(tmp_path, monkeypatch, capsys):
    expected = {
        "startup": "tests/test_windows_launchers.py::test_streamer_uses_core_readiness_as_the_only_startup_completion_gate",
        "csv-import": "tests/test_master_service_trading_journal_crud.py::test_bybit_demo_import_preserves_cached_simulation",
        "resync": "tests/test_master_service_master_journal_sync.py::test_resync_publication_bound_rollback_and_newer_save",
        "workbook-formatting": "tests/test_master_journal_workbook.py::test_saved_journal_future_trade_order_and_authored_layout",
        "dashboard": "tests/test_open_orders_page.py::test_open_orders_browser_preserves_pending_data_and_confirms_matching_submit",
    }
    assert runner.PROFILES == expected
    # Static definition checks only: do not collect/import/run product tests.
    assert runner.selected_nodes(runner.ROOT, list(expected)) == list(expected.values())
    monkeypatch.setattr(runner, "run_profiles", lambda *a, **k: pytest.fail("Selection must not execute"))
    assert runner.main(["--list"]) == 0
    listed = capsys.readouterr().out
    assert all(node in listed for node in expected.values())
    for args in ([], ["--profile", "all"], ["--profile", "startup", "--list"],
                 ["--profile", "startup", "--unknown"]):
        with pytest.raises(SystemExit) as exc:
            runner.main(args)
        assert exc.value.code == 2
    monkeypatch.undo()
    for selection in ([], ["all"], ["startup", "startup"]):
        with pytest.raises(ValueError):
            runner.selected_nodes(runner.ROOT, selection)
    with pytest.raises(ValueError):
        runner.run_profiles(tmp_path, ["startup"], timeout=0)
    with pytest.raises(ValueError):
        runner.run_profiles(tmp_path, ["startup"], timeout=301)

    repo = tmp_path / "isolated-repo"
    for node in expected.values():
        file, function = node.split("::")
        path = repo / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def {function}():\n    pass\n", encoding="utf-8")
    chosen = [expected["dashboard"], expected["startup"]]
    observed, basetemps = [], set()
    # Real short-lived Python subprocesses produce isolated JUnit scenarios.
    # They never invoke pytest or import any product code.
    child = r'''
import json, pathlib, sys, time, xml.etree.ElementTree as E
mode, output, nodes = sys.argv[1], pathlib.Path(sys.argv[2]), json.loads(sys.argv[3])
print('isolated child scenario: ' + mode, flush=True)
if mode == 'timeout': time.sleep(2)
if mode == 'missing-file': sys.exit(0)
if mode == 'malformed': output.write_text('<unfinished'); sys.exit(0)
suite = E.Element('testsuite', tests=str(len(nodes)), errors='0', failures='0', skipped='0')
for i, node in enumerate(nodes):
    if mode == 'missing-body' and i == 1: continue
    file, name = node.split('::')
    c = E.SubElement(suite, 'testcase', classname=file[:-3].replace('/', '.'), name=name, time='0.01')
    if i == 0 and mode in ('failure', 'error', 'skipped'): E.SubElement(c, mode)
    if i == 0 and mode == 'duplicate': E.SubElement(suite, 'testcase', classname=c.get('classname'), name=name)
if mode == 'unexpected': E.SubElement(suite, 'testcase', classname='tests.other', name='test_unrequested')
if mode == 'suite-error': suite.set('errors', '1')
E.ElementTree(suite).write(output, encoding='utf-8')
sys.exit(1 if mode == 'nonzero' else 0)
'''
    for mode in ("passed", "failure", "error", "skipped", "missing-body", "duplicate",
                 "unexpected", "suite-error", "nonzero", "missing-file", "malformed", "timeout", "launch-error"):
        def process(command, **kwargs):
            observed.append(command)
            assert command[:4] == [str(runner.PYTHON), "-B", "-u", "-c"]
            assert command[12:14] == chosen
            assert kwargs["stderr"] == subprocess.STDOUT
            assert kwargs["cwd"] == repo
            assert kwargs["env"]["PYTEST_ADDOPTS"] == ""
            assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
            assert not kwargs["stdout"].closed
            xml = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
            if mode == "launch-error":
                raise OSError("isolated launch failure")
            return subprocess.run([sys.executable, "-B", "-c", child, mode, xml, json.dumps(chosen)],
                                  stdout=kwargs["stdout"], stderr=kwargs["stderr"], timeout=kwargs["timeout"])
        result = runner.run_profiles(repo, ["dashboard", "startup"],
                                     timeout=.1 if mode == "timeout" else 5, process_runner=process)
        assert result["nodes"] == chosen
        assert result["elapsed_seconds"] >= 0
        assert result["basetemp"] not in basetemps
        basetemps.add(result["basetemp"])
        assert not Path(result["basetemp"]).exists()  # Runner leaves creation to pytest.
        assert Path(result["log"]).is_file()
        assert json.loads(Path(result["log"]).with_name("result.json").read_text()) == result
        assert result["ok"] == (mode == "passed")
        assert (result["command_status"] == 0) == (mode == "passed")
        if mode == "timeout":
            assert result["timed_out"] and result["actual_child_exit"] is None
            assert result["command_status"] == 124
        elif mode == "launch-error":
            assert result["actual_child_exit"] is None and result["launch_error"]
        else:
            assert result["actual_child_exit"] == (1 if mode == "nonzero" else 0)
    assert len(observed) == 13
    plan = json.loads((repo / ".codex-work/active/ltt-regression-command/manifest.json").read_text())
    registered = {entry["path"] for entry in plan["entries"]}
    assert all(Path(result["log"]).relative_to(repo).as_posix() in registered for result in [result])
    # CLI propagates unsuccessful status rather than hiding it behind printed output.
    monkeypatch.setattr(runner, "run_profiles", lambda *a, **k: {"command_status": 124})
    assert runner.main(["--profile", "startup"]) == 124
