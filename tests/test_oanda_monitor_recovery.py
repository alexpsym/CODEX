"""OANDA-only capture/containment regression; the live Errno 22 cause is still unknown."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_oanda_monitor_reported_failure_recovers_without_restart_loop(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("master_oanda_capture_regression", ROOT / "render/master_service.py")
    service = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, service)
    spec.loader.exec_module(service)
    clock = [1000.0]
    monkeypatch.setattr(service, "time", SimpleNamespace(time=lambda: clock[0]))
    runtime = tmp_path / "runtime.json"
    evidence = tmp_path / "failed-child.json"
    monkeypatch.setattr(service, "OANDA_RUNTIME_STATUS_PATH", runtime)
    monkeypatch.setattr(service, "OANDA_LAST_FAILED_CHILD_PATH", evidence)
    monkeypatch.setattr(service, "_scanner_has_external_live_runtime", lambda name: False)
    monkeypatch.setattr(service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})
    monkeypatch.setenv("OANDA_API_KEY", "fixture-private-key")
    monkeypatch.setenv("OANDA_ACCESS_TOKEN", "fixture-private-token")

    def prohibited(*args, **kwargs):
        raise AssertionError("network/notification/production monitor execution prohibited")
    monkeypatch.setattr(service.httpx.AsyncClient, "request", prohibited)
    monkeypatch.setattr(service.oanda_monitor, "run_monitor", prohibited)
    monkeypatch.setattr(service.oanda_monitor, "send_notification", prohibited)
    monkeypatch.setattr(service.oanda_monitor.requests.Session, "request", prohibited)

    async def scenario():
        children = []
        # Synthetic output tests capture, not an invented diagnosis of the live operation.
        traceback_lines = [
            "Traceback (most recent call last):\n",
            '  File "<isolated capture fixture>", line 1, in fake_child\n',
            "ValueError: original broker read failed\n",
            "During handling of the above exception, another exception occurred:\n",
            "Traceback (most recent call last):\n",
            '  File "<isolated capture fixture>", line 2, in fake_diagnostic\n',
            "OSError: [Errno 22] Invalid argument\n",
            "authorization=fixture-private-token api_key=fixture-private-key\n",
        ]

        class Child:
            def __init__(self, pid):
                self.pid = pid
                self.returncode = None
                self.done = asyncio.Event()
                self.output = list(traceback_lines)
                self.stdout = self
                self.code = 1

            async def readline(self):
                if self.output:
                    return self.output.pop(0).encode()
                await self.done.wait()
                self.returncode = self.code
                return b""

            async def wait(self):
                await self.done.wait()
                self.returncode = self.code
                return self.code

            def terminate(self):
                self.code = 0
                self.done.set()

            kill = terminate

        async def spawn(*args, **kwargs):
            assert args[0] == sys.executable and args[1] == "-u"
            assert kwargs["stderr"] == asyncio.subprocess.STDOUT
            child = Child(4100 + len(children))
            children.append(child)
            return child

        monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(service.os, "kill", lambda pid, signal: next(child for child in children if child.pid == pid).terminate())
        script = service.ManagedScript("oanda_monitor", ROOT / "oanda_monitor/oanda_forex_monitor.py")
        monkeypatch.setattr(service.script_manager, "get", lambda name: script)

        async def until(predicate):
            for _ in range(40):
                if predicate():
                    return
                await asyncio.sleep(0)
            assert predicate()

        await asyncio.gather(script.start(), script.start())
        assert len(children) == 1 and script.is_running
        first = children[0]
        runtime.write_text(json.dumps({"pid": first.pid, "phase": "error", "running": False,
                                      "last_error": "[Errno 22] Invalid argument",
                                      "last_exit_reason": "uncaught_exception"}))
        first.done.set()
        await until(lambda: script.last_exit_code == 1)
        record = script.log_snapshot()["last_failed_child"]
        assert record["pid"] == first.pid and record["exit_code"] == 1
        assert record["command"] == [sys.executable, "-u", str(script.path)]
        assert record["cwd"] == str(script.path.parent)
        assert record["started_at"] == 1000 and record["exited_at"] == 1000
        assert record["runtime_status"]["pid"] == first.pid
        assert record["runtime_status"]["last_error"] == "[Errno 22] Invalid argument"
        assert "original broker read failed" in str(record["output_tail"])
        assert "[Errno 22]" in str(record["output_tail"])
        assert "fixture-private" not in json.dumps(record)
        assert json.loads(evidence.read_text()) == record and evidence.stat().st_size < 200_000
        assert "web app" not in script.last_exit_reason
        assert not script.is_running and script.monitor_restart_not_before == 1015
        restored = service.ManagedScript("oanda_monitor", script.path)
        assert restored.to_summary()["last_failed_child"] == record

        for _ in range(10):
            await service._supervise_autostart_scripts_once(["oanda_monitor"])
        assert len(children) == 1
        clock[0] = 1015
        await service._supervise_autostart_scripts_once(["oanda_monitor"])
        assert len(children) == 2 and script.is_running
        assert script.to_summary()["last_failed_child"] == record
        assert script.last_exit_code == 1  # Starting again does not erase the failed exit.
        second = children[1]
        # A newer runtime file must never be attributed to the exited child.
        runtime.write_text(json.dumps({"pid": 9999, "last_error": "another child"}))
        second.done.set()
        await until(lambda: script.last_failed_child["pid"] == second.pid)
        assert script.last_failed_child["runtime_status_pid"] == 9999
        assert "runtime_status" not in script.last_failed_child
        assert script.monitor_restart_not_before == 1045
        for _ in range(10):
            await service._supervise_autostart_scripts_once(["oanda_monitor"])
        assert len(children) == 2

        # Recorder failure is explicit and does not replace the child's exception/return code.
        saved_writer = service.write_json_file
        def broken_writer(*args, **kwargs):
            raise PermissionError("isolated recorder failure")
        monkeypatch.setattr(service, "write_json_file", broken_writer)
        script._record_oanda_failed_child(second, traceback_lines, record, None)
        assert "recorder failure" in script.last_exit_capture_error
        assert script.last_failed_child["pid"] == second.pid and script.last_failed_child["exit_code"] == 1
        assert "[Errno 22]" in str(script.last_failed_child["output_tail"])
        monkeypatch.setattr(service, "write_json_file", saved_writer)

        clock[0] = 1045
        await service._supervise_autostart_scripts_once(["oanda_monitor"])
        assert len(children) == 3 and script.is_running
        # The stopped worker cannot be relaunched by supervision; explicit start is required.
        await script.stop()
        await until(lambda: script.last_exit_code == 0)
        clock[0] += 10000
        for _ in range(10):
            await service._supervise_autostart_scripts_once(["oanda_monitor"])
        assert len(children) == 3 and not script.is_running and script.monitor_stop_requested
        assert script.last_failed_child["exit_code"] == 1

    asyncio.run(asyncio.wait_for(scenario(), 10))
