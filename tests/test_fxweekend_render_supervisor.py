import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from render import master_service


def _settings(*, enabled: bool = True) -> dict[str, object]:
    return {
        "schema_version": master_service.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "enabled": enabled,
        "account_modes": ["demo", "live"],
        "check_interval_seconds": 60,
        "max_retry_backoff_seconds": 300,
    }


class _ManagedFxScript:
    name = "fxweekend-clone"

    def __init__(
        self,
        *,
        running: bool,
        starting: bool = False,
        startup_age_seconds: float = 600.0,
    ) -> None:
        self.is_running = running
        self.is_starting = starting
        self.startup_task = None
        self.startup_completed_at = None
        self.startup_started_at = (
            time.time() - startup_age_seconds if running else None
        )
        self.last_start_error = None
        self.last_exit_reason = None
        self.pid = 4242 if running else None
        self.executor_instance_id = "current-instance"
        self.heartbeat_confirmed_pid = None
        self.operational_started_pid = None
        self.starts = 0
        self.stops = 0
        self.logs: list[str] = []

    async def start(self, *, ignore_starting: bool = False) -> None:
        self.starts += 1
        self.is_running = True
        self.is_starting = False
        self.pid = 4242

    async def stop(self) -> None:
        self.stops += 1
        self.is_running = False

    def add_log(self, message: str) -> None:
        self.logs.append(message)

    def to_summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "running": self.is_running,
            "starting": self.is_starting,
            "pid": self.pid,
            "executor_instance_id": self.executor_instance_id,
            "startup_started_at": self.startup_started_at,
            "last_start_error": self.last_start_error,
            "last_exit_reason": self.last_exit_reason,
        }


def _mock_fx_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: _ManagedFxScript,
    settings: dict[str, object],
    runtime: dict[str, object],
) -> None:
    def load(path, default):
        if path == master_service.FXWEEKEND_SETTINGS_PATH:
            return settings
        if path == master_service.FXWEEKEND_STATUS_PATH:
            return runtime
        return default

    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "_load_json_file", load)
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: script)
    monkeypatch.setattr(
        master_service,
        "resolve_oanda_account_config",
        lambda mode: {
            "mode": mode,
            "account_id": f"{mode}-account",
            "api_key": f"{mode}-key",
            "base_url": "https://example.invalid/v3",
        },
    )
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {"fxweekend_state_indeterminate": False},
    )


def test_authoritative_restore_at_logical_45_seconds_still_starts_automatically(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LogicalRestoreSignal:
        def __init__(self) -> None:
            self.ready = False
            self.logical_elapsed_seconds = 0.0
            self.event = asyncio.Event()

        async def wait(self) -> None:
            await self.event.wait()

        def advance_and_set(self, seconds: float) -> None:
            self.logical_elapsed_seconds += seconds
            self.ready = True
            self.event.set()

        def is_set(self) -> bool:
            return self.ready

    signal = LogicalRestoreSignal()
    script = _ManagedFxScript(running=False)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_settings()), encoding="utf-8")
    state_sync = {
        "enabled": True,
        "restore_complete": True,
        "restore_status": "done",
        "restore_error": None,
        "per_file_state_ready": True,
        "missing_state_keys": [],
        "fxweekend_state_indeterminate": False,
        "fxweekend_durable_verified": True,
    }

    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "FXWEEKEND_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(master_service, "_STARTUP_STATE_RESTORE_DONE", signal)
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: dict(state_sync),
    )
    async def forbidden_wait_for(*_args, **_kwargs):
        raise AssertionError("FX Weekend startup must not use a fixed timeout")

    monkeypatch.setattr(master_service.asyncio, "wait_for", forbidden_wait_for)

    async def scenario() -> None:
        task = asyncio.create_task(
            master_service._background_start_after_state_restore(script)
        )
        await asyncio.sleep(0)
        assert task.done() is False
        assert script.starts == 0
        signal.advance_and_set(45.0)
        await task

    asyncio.run(scenario())

    assert signal.logical_elapsed_seconds == 45.0
    assert script.starts == 1
    assert script.last_start_error is None
    assert any(
        "waiting for authoritative state restoration" in line
        for line in script.logs
    )


def test_restore_and_supervisor_race_dispatches_exactly_one_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_start = asyncio.Event()

    class RaceScript:
        name = "fxweekend-clone"
        is_running = False
        is_starting = False
        startup_task = None
        last_start_error = None
        last_exit_reason = None

        def __init__(self) -> None:
            self.starts = 0
            self.logs: list[str] = []

        async def start(self) -> None:
            self.starts += 1
            self.is_starting = True
            await release_start.wait()
            self.is_starting = False
            self.is_running = True

        def add_log(self, message: str) -> None:
            self.logs.append(message)

    script = RaceScript()
    signal = asyncio.Event()
    signal.set()
    monkeypatch.setattr(master_service, "_STARTUP_STATE_RESTORE_DONE", signal)
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: script)
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})

    async def scenario() -> None:
        task = asyncio.create_task(
            master_service._background_start_after_state_restore(script)
        )
        script.startup_task = task
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert script.starts == 1
        await master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
        assert script.starts == 1
        release_start.set()
        await task

    asyncio.run(scenario())
    assert script.starts == 1


def test_managed_fx_spawn_injects_a_unique_executor_instance_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = master_service.ManagedScript(
        name="fxweekend-clone",
        path=master_service.BASE_DIR / "fxweekend-clone" / "liquidate.py",
    )
    captured_envs: list[dict[str, str]] = []

    class FakeProcess:
        stdout = None

        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.returncode = None

    async def fake_spawn(*_args, **kwargs):
        captured_envs.append(dict(kwargs["env"]))
        return FakeProcess(5000 + len(captured_envs))

    def close_background(coro):
        coro.close()
        return None

    monkeypatch.setattr(
        master_service.asyncio,
        "create_subprocess_exec",
        fake_spawn,
    )
    monkeypatch.setattr(master_service.asyncio, "create_task", close_background)

    asyncio.run(script.start())
    first_instance = captured_envs[0]["FXWEEKEND_EXECUTOR_INSTANCE_ID"]
    assert first_instance == script.executor_instance_id
    assert first_instance

    assert script.process is not None
    script.process.returncode = 0
    script.is_starting = False
    asyncio.run(script.start())
    second_instance = captured_envs[1]["FXWEEKEND_EXECUTOR_INSTANCE_ID"]

    assert second_instance == script.executor_instance_id
    assert second_instance != first_instance


def test_managed_stop_serializes_concurrent_start_and_preserves_new_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = master_service.ManagedScript(
        name="fxweekend-clone",
        path=master_service.BASE_DIR / "fxweekend-clone" / "liquidate.py",
    )
    spawned = []

    class FakeProcess:
        stdout = None

        def __init__(
            self,
            pid: int,
            *,
            wait_started: asyncio.Event,
            release_wait: asyncio.Event,
        ) -> None:
            self.pid = pid
            self.returncode = None
            self.wait_started = wait_started
            self.release_wait = release_wait
            self.terminate_calls = 0
            self.kill_calls = 0
            self.wait_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

        async def wait(self) -> int:
            self.wait_calls += 1
            if self.returncode is None:
                # Expose the dangerous interval: is_running is now false, but
                # the original stop() has not finished its awaited cleanup.
                self.returncode = 0
                self.wait_started.set()
                await self.release_wait.wait()
            return int(self.returncode)

    async def scenario() -> None:
        old_wait_started = asyncio.Event()
        release_old_wait = asyncio.Event()
        old_process = FakeProcess(
            8101,
            wait_started=old_wait_started,
            release_wait=release_old_wait,
        )
        script.process = old_process
        script.pid = old_process.pid
        script.executor_instance_id = "old-instance"
        script.port = 58101
        script.last_exit_reason = "test restart"

        async def fake_spawn(*_args, **_kwargs):
            new_process = FakeProcess(
                9202,
                wait_started=asyncio.Event(),
                release_wait=asyncio.Event(),
            )
            spawned.append(new_process)
            return new_process

        monkeypatch.setattr(
            master_service.asyncio,
            "create_subprocess_exec",
            fake_spawn,
        )
        # Avoid sending CTRL_BREAK_EVENT to a synthetic Windows PID.
        monkeypatch.setattr(master_service.os, "kill", lambda *_args: None)

        stop_task = asyncio.create_task(script.stop())
        await old_wait_started.wait()
        start_task = asyncio.create_task(script.start())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # start() must be waiting on the lifecycle lock, not replacing the
        # child while stop() still owns and awaits the original process.
        assert spawned == []
        assert script.process is old_process
        assert script.pid == old_process.pid
        assert script.executor_instance_id == "old-instance"

        release_old_wait.set()
        await stop_task
        await start_task
        await asyncio.sleep(0)

        assert len(spawned) == 1
        new_process = spawned[0]
        assert script.process is new_process
        assert script.pid == new_process.pid
        assert script.executor_instance_id
        assert script.executor_instance_id != "old-instance"
        assert new_process.terminate_calls == 0
        assert new_process.kill_calls == 0
        assert new_process.wait_calls == 0

    asyncio.run(scenario())


def test_300_second_backoff_keeps_heartbeat_valid_and_preserves_executor_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_now = datetime.now(timezone.utc)
    started_at = (observed_now - timedelta(minutes=20)).isoformat()
    heartbeat_at = (observed_now - timedelta(seconds=300)).isoformat()
    settings = _settings()
    script = _ManagedFxScript(running=True)
    runtime = {
        "state": "retry pending",
        "state_detail": "Recoverable API failure; retry backoff active.",
        "executor_pid": script.pid,
        "executor_instance_id": script.executor_instance_id,
        "heartbeat_at": heartbeat_at,
        "executor_started_at": started_at,
        "selected_accounts": ["demo", "live"],
        "accounts": {
            "demo": {"state": "retry pending", "last_error": "HTTP 503"},
            "live": {"state": "retry pending", "last_error": "HTTP 503"},
        },
    }
    original_pid = script.pid
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})

    heartbeat = master_service._fxweekend_heartbeat_diagnostics(
        settings,
        runtime,
        now=observed_now,
        expected_pid=script.pid,
        expected_instance_id=script.executor_instance_id,
    )
    assert heartbeat["fresh"] is True
    assert heartbeat["heartbeat_age_seconds"] == pytest.approx(300.0)
    assert float(heartbeat["stale_after_seconds"]) > 300.0

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )

    assert script.stops == 0
    assert script.starts == 0
    assert script.pid == original_pid
    assert runtime["executor_started_at"] == started_at
    assert script.heartbeat_confirmed_pid == original_pid
    assert any("Scheduler heartbeat confirmed" in line for line in script.logs)


def test_fresh_heartbeat_from_previous_pid_stays_amber_and_is_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    script = _ManagedFxScript(running=True, startup_age_seconds=30.0)
    runtime = {
        "state": "before cutoff",
        "executor_pid": 3131,
        "executor_instance_id": "previous-instance",
        "executor_started_at": "2026-07-27T07:55:00+00:00",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "selected_accounts": ["demo", "live"],
        "accounts": {
            "demo": {"state": "before cutoff"},
            "live": {"state": "before cutoff"},
        },
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})

    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert component["heartbeat_timestamp_fresh"] is True
    assert component["heartbeat_pid_matches"] is False
    assert component["heartbeat_fresh"] is False
    assert component["health_state"] == "amber"
    assert component["phase"] == "heartbeat_pending_for_process"

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )

    assert script.stops == 0
    assert script.starts == 0
    assert script.pid == 4242
    assert script.heartbeat_confirmed_pid is None


def test_same_pid_previous_instance_restarts_only_after_startup_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    script = _ManagedFxScript(running=True, startup_age_seconds=30.0)
    runtime = {
        "state": "before cutoff",
        "executor_pid": script.pid,
        "executor_instance_id": "previous-instance",
        "executor_started_at": "2026-07-27T07:55:00+00:00",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "selected_accounts": ["demo", "live"],
        "accounts": {
            "demo": {"state": "before cutoff"},
            "live": {"state": "before cutoff"},
        },
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})

    during_grace = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert during_grace["heartbeat_pid_matches"] is True
    assert during_grace["heartbeat_instance_matches"] is False
    assert during_grace["health_state"] == "amber"

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert script.stops == 0
    assert script.heartbeat_confirmed_pid is None

    script.startup_started_at = (
        time.time()
        - master_service.FXWEEKEND_HEARTBEAT_STARTUP_GRACE_SECONDS
        - 1.0
    )
    after_grace = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert after_grace["health_state"] == "red"
    assert after_grace["heartbeat_fresh"] is False

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert script.stops == 1
    assert "instance_matches=False" in str(script.last_exit_reason)


def test_missing_first_heartbeat_gets_bounded_startup_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    script = _ManagedFxScript(running=True, startup_age_seconds=30.0)
    runtime = {
        "state": "checking",
        "executor_pid": None,
        "executor_instance_id": None,
        "executor_started_at": None,
        "heartbeat_at": None,
        "selected_accounts": [],
        "accounts": {},
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF", {})
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BACKOFF_WINDOW", {})

    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert component["health_state"] == "amber"
    assert component["heartbeat_startup_grace_active"] is True

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert script.stops == 0

    script.startup_started_at = (
        time.time()
        - master_service.FXWEEKEND_HEARTBEAT_STARTUP_GRACE_SECONDS
        - 1.0
    )
    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert component["health_state"] == "red"

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert script.stops == 1
    assert "heartbeat_at=None" in str(script.last_exit_reason)


def test_healthy_missed_cutoff_is_operational_amber_in_backend_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    script = _ManagedFxScript(running=True)
    runtime = {
        "state": "missed cutoff/market closed",
        "state_detail": "The prior cutoff was missed because the market was closed.",
        "executor_pid": script.pid,
        "executor_instance_id": script.executor_instance_id,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "executor_started_at": "2026-07-27T08:00:00+00:00",
        "selected_accounts": ["demo", "live"],
        "accounts": {
            "demo": {"state": "missed cutoff/market closed"},
            "live": {"state": "missed cutoff/market closed"},
        },
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )
    monkeypatch.setattr(
        master_service,
        "get_merged_script_buttons",
        lambda: [
            {
                "id": "fxweekend",
                "name": "fxweekend",
                "label": "FX Weekend",
                "open_url": "/apps/fxweekend-clone",
            }
        ],
    )
    monkeypatch.setattr(
        master_service.script_manager,
        "list_scripts",
        lambda: [script.to_summary()],
    )
    monkeypatch.setattr(
        master_service,
        "_compute_autostart_scripts",
        lambda: ["fxweekend-clone"],
    )

    payload = json.loads(
        asyncio.run(master_service.list_scripts()).body.decode("utf-8")
    )
    row = next(item for item in payload if item["name"] == "fxweekend")

    assert row["running"] is True
    assert row["heartbeat_fresh"] is True
    assert row["operational"] is True
    assert row["health_state"] == "amber"
    assert "market was closed" in row["health_reason"]
    assert row["executor_started_at"] == runtime["executor_started_at"]
    assert row["pid"] == script.pid


@pytest.mark.parametrize("state", ["API failure", "retry pending"])
def test_fresh_aggregate_failure_is_red_even_before_account_details_arrive(
    state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    script = _ManagedFxScript(running=True)
    script.is_starting = True
    runtime = {
        "state": state,
        "last_error": "OANDA returned a recoverable 503 response.",
        "executor_pid": script.pid,
        "executor_instance_id": script.executor_instance_id,
        "executor_started_at": "2026-07-27T08:00:00+00:00",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "selected_accounts": [],
        "accounts": {},
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )

    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )

    assert component["heartbeat_fresh"] is True
    assert component["health_state"] == "red"
    assert component["ready"] is False
    assert component["phase"] in {"API_failure", "retry_pending"}
    assert "503" in component["health_reason"]


def test_disabled_backend_status_is_explicit_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(enabled=False)
    script = _ManagedFxScript(running=True)
    runtime = {
        "state": "disabled",
        "executor_pid": script.pid,
        "executor_instance_id": script.executor_instance_id,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "executor_started_at": "2026-07-27T08:00:00+00:00",
        "selected_accounts": ["demo", "live"],
        "accounts": {},
    }
    _mock_fx_runtime(
        monkeypatch,
        script=script,
        settings=settings,
        runtime=runtime,
    )

    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )

    assert component["ready"] is True
    assert component["health_state"] == "disabled"
    assert component["phase"] == "disabled"
