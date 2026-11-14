"""Explicit, bounded fixture regressions; run --help for selection and evidence paths."""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import xml.etree.ElementTree as ET


PYTHON = Path(r"C:\Users\User\miniconda3\python.exe")
ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "startup": "tests/test_windows_launchers.py::test_streamer_uses_core_readiness_as_the_only_startup_completion_gate",
    "csv-import": "tests/test_master_service_trading_journal_crud.py::test_bybit_demo_import_preserves_cached_simulation",
    "resync": "tests/test_master_service_master_journal_sync.py::test_resync_publication_bound_rollback_and_newer_save",
    "workbook-formatting": "tests/test_master_journal_workbook.py::test_saved_journal_future_trade_order_and_authored_layout",
    "dashboard": "tests/test_open_orders_page.py::test_open_orders_browser_preserves_pending_data_and_confirms_matching_submit",
}


# Same direct-log subprocess/normal-exit pattern as the retained Windows runners.
# Register fixture outputs before creation, without importing the application.
WORKER = r'''
import ctypes, faulthandler, json, os, sys, time
from pathlib import Path
from ctypes import wintypes as w
root, manifest, fixture, events, completion, token, timeout = sys.argv[1:8]
root, manifest, fixture = Path(root), Path(manifest), Path(fixture)
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True
started = None
if os.name == 'nt':
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetCurrentProcess.restype = w.HANDLE
    k.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
    times = [w.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(k.GetCurrentProcess(), *[ctypes.byref(x) for x in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    started = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
stream = open(events, 'x', encoding='utf-8')
busy = False
known = {e['path'] for e in json.loads(manifest.read_text())['entries']}
def audit(event, args):
    global busy
    if busy or stream.closed or event not in ('open', 'os.mkdir', 'os.rename'):
        return
    for raw in (args[:2] if event == 'os.rename' else args[:1]):
        if not isinstance(raw, (str, bytes)):
            continue
        path = Path(os.fsdecode(raw)).absolute()
        if path != fixture and fixture not in path.parents:
            continue
        rel = path.relative_to(root).as_posix()
        busy = True
        try:
            stream.write(json.dumps(dict(event=event, path=rel, pid=os.getpid(),
                process_start_filetime=started, at=time.time())) + '\n')
            stream.flush()
            if rel not in known:
                plan = json.loads(manifest.read_text())
                plan['entries'].append(dict(path=rel,
                    type='directory' if event == 'os.mkdir' else 'file',
                    decision='retain', status='registered-before-creation',
                    evidence='Child pre-creation audit with PID/start',
                    reason='Selected fixture verification evidence',
                    release_condition='Normal child completion and evidence review'))
                manifest.write_text(json.dumps(plan, indent=2))
                known.add(rel)
        finally:
            busy = False
sys.addaudithook(audit)
faulthandler.dump_traceback_later(max(1, float(timeout) - 2), repeat=False)
code = 2
try:
    import pytest
    code = int(pytest.main(sys.argv[8:]))
finally:
    faulthandler.cancel_dump_traceback_later()
    Path(completion).write_text(json.dumps(dict(kind='artifact-completion',
        token=token, completed=True, exit=code, pid=os.getpid(),
        process_start_filetime=started)))
    stream.close()
sys.exit(code)
'''


def identity(path):
    stat = path.lstat()
    return dict(dev=stat.st_dev, ino=stat.st_ino, size=stat.st_size,
                mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns,
                attributes=getattr(stat, "st_file_attributes", 0))


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def selected_nodes(repo, profiles):
    if not profiles or len(set(profiles)) != len(profiles):
        raise ValueError("Select at least one profile, without duplicates")
    nodes = []
    for profile in profiles:
        if profile not in PROFILES:
            raise ValueError("Unknown profile: " + profile)
        node = PROFILES[profile]
        filename, function = node.split("::")
        tree = ast.parse((repo / filename).read_text(encoding="utf-8-sig"))
        matches = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == function]
        if len(matches) != 1:
            raise ValueError("Profile must identify one existing test definition: " + node)
        nodes.append(node)
    return nodes


def junit_outcomes(path, nodes):
    """Require one completed, unskipped body for every exact requested node."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        return [dict(node=n, outcome="missing") for n in nodes], [str(exc)]
    cases = {}
    problems = []
    for case in tree.iter("testcase"):
        key = (case.get("classname"), case.get("name"))
        cases.setdefault(key, []).append(case)
    outcomes = []
    expected = set()
    for node in nodes:
        filename, function = node.split("::")
        key = (filename[:-3].replace("/", "."), function)
        expected.add(key)
        found = cases.get(key, [])
        outcome = "missing" if not found else "duplicate" if len(found) != 1 else "passed"
        if len(found) == 1:
            for tag in ("failure", "error", "skipped"):
                if found[0].find(tag) is not None:
                    outcome = tag
                    break
        outcomes.append(dict(node=node, outcome=outcome,
                             seconds=found[0].get("time") if len(found) == 1 else None))
    if set(cases) - expected:
        problems.append("JUnit contains unrequested results")
    for suite in tree.iter("testsuite"):
        for field in ("errors", "failures", "skipped"):
            try:
                if int(suite.get(field, "0")) != 0:
                    problems.append("JUnit suite reports " + field)
            except ValueError:
                problems.append("Invalid JUnit suite count: " + field)
    return outcomes, problems


def run_profiles(repo, profiles, timeout=300, *, process_runner=None):
    """One invocation, fresh UUID basetemp, no shell or automatic retry."""
    if not 0 < timeout <= 300:
        raise ValueError("Timeout must be greater than zero and at most 300 seconds")
    nodes = selected_nodes(repo, profiles)
    run = process_runner or subprocess.run
    job = repo / ".codex-work/active/ltt-regression-command"
    # Refuse links/reparse ancestry before creating private evidence.
    for parent in [repo, repo / ".codex-work", repo / ".codex-work/active", job]:
        if parent.exists() and (parent.is_symlink() or identity(parent)["attributes"] & 1024):
            raise ValueError("Linked artifact ancestry is not allowed: " + str(parent))
    job.mkdir(parents=True, exist_ok=True)
    manifest = job / "manifest.json"
    plan = json.loads(manifest.read_text()) if manifest.exists() else dict(
        schema=1, root=str(repo), root_identity=identity(repo), entries=[], reconcile_roots=[])
    if plan["schema"] != 1 or plan["root"] != str(repo):
        raise ValueError("Artifact manifest belongs to a different root/schema")
    token = uuid4().hex
    fixture = job / "fixtures" / token
    log, xml = fixture / "pytest.log", fixture / "junit.xml"
    events, completion, result = (fixture / n for n in ("creation.jsonl", "completion.json", "result.json"))
    for path in [job / "fixtures", fixture, fixture / "basetemp", log, xml, events, completion, result]:
        rel = path.relative_to(repo).as_posix()
        if not any(e["path"] == rel for e in plan["entries"]):
            plan["entries"].append(dict(path=rel, type="directory" if path.suffix == "" else "file",
                decision="retain", status="registered-before-creation",
                evidence="Fresh UUID fixture output registered before launch",
                reason="Selected regression command evidence pending review",
                release_condition="Review compact result; dispose with tools/cleanup_pytest_temp.py exact manifest"))
    save(manifest, plan)
    fixture.mkdir(parents=True)
    plan["reconcile_roots"].append(dict(path=fixture.relative_to(repo).as_posix(), identity=identity(fixture)))
    save(manifest, plan)
    command = [str(PYTHON), "-B", "-u", "-c", WORKER, str(repo), str(manifest), str(fixture),
               str(events), str(completion), token, str(timeout), *nodes,
               "-q", "-s", "--tb=short", "-p", "no:cacheprovider",
               "--basetemp=" + str(fixture / "basetemp"), "--junitxml=" + str(xml)]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_ADDOPTS="")
    started = time.monotonic()
    actual_exit, timed_out, launch_error = None, False, None
    with log.open("x", encoding="utf-8") as stream:
        try:
            child = run(command, cwd=repo, env=environment, stdout=stream,
                        stderr=subprocess.STDOUT, timeout=timeout)
            actual_exit = child.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as exc:
            launch_error = str(exc)
    elapsed = time.monotonic() - started
    outcomes, problems = junit_outcomes(xml, nodes)
    ok = actual_exit == 0 and not timed_out and launch_error is None and not problems \
        and all(item["outcome"] == "passed" for item in outcomes)
    receipt = dict(profiles=profiles, nodes=nodes, elapsed_seconds=elapsed,
                   actual_child_exit=actual_exit, timed_out=timed_out, launch_error=launch_error,
                   outcomes=outcomes, junit_problems=problems, ok=ok,
                   command_status=0 if ok else 124 if timed_out else 1,
                   log=str(log), junit=str(xml), basetemp=str(fixture / "basetemp"),
                   manifest=str(manifest), completion=str(completion))
    save(result, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        r"Example: C:\Users\User\miniconda3\python.exe -B tools/run_ltt_regressions.py "
        "--profile startup. Multiple names select only those nodes in one bounded invocation. "
        "Direct logs/JUnit/result and pre-creation manifest are under "
        ".codex-work/active/ltt-regression-command/fixtures/<fresh-id>/. "
        "Retain failures for diagnosis; use the canonical exact-manifest cleanup after evidence review. "
        "These are fixture checks; desktop Excel acceptance remains separate when AGENTS.md requires it."))
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--list", action="store_true", help="List profiles and exact nodes without running tests")
    selection.add_argument("--profile", nargs="+", choices=PROFILES, help="Explicit profiles to execute; no default/all profile")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Whole selected invocation timeout, 1..300 seconds (default: 300)")
    args = parser.parse_args(argv)
    if args.list:
        for profile, node in PROFILES.items():
            print(profile + "\t" + node)
        return 0
    try:
        receipt = run_profiles(ROOT, args.profile, args.timeout_seconds)
    except (ValueError, OSError, SyntaxError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2))
    return receipt["command_status"]


if __name__ == "__main__":
    raise SystemExit(main())
