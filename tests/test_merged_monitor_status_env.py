import asyncio
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("render_master_service_merged_monitor", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def test_merged_monitor_html_removed_controls_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    response = asyncio.run(master_service.merged_monitor_page())
    html = response.body.decode("utf-8")
    assert 'id="bybit-start-btn"' not in html
    assert 'id="bybit-stop-btn"' not in html
    assert 'id="oanda-start-btn"' not in html
    assert 'id="oanda-stop-btn"' not in html
    assert 'id="bybit-log-box"' not in html
    assert 'id="oanda-log-box"' not in html
    assert 'id="bybit-status" class="badge">Checking…</span>' in html
    assert 'id="oanda-status" class="badge">Checking…</span>' in html
    assert "polls local scanner status every 2 seconds" in html


def test_merged_monitor_js_avoids_script_and_log_endpoints() -> None:
    script = (ROOT / "render" / "static" / "merged_monitor.js").read_text(encoding="utf-8")
    assert "/api/scripts/" not in script
    assert "/api/logs/" not in script


def test_merged_monitor_js_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available in this environment")
    result = subprocess.run([node, "--check", str(ROOT / "render" / "static" / "merged_monitor.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_status_endpoints_fresh_stale_missing_and_malformed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bybit_file = tmp_path / "bybit_runtime_status.json"
    oanda_file = tmp_path / "oanda_runtime_status.json"
    monkeypatch.setattr(master_service, "BYBIT_RUNTIME_STATUS_PATH", bybit_file)
    monkeypatch.setattr(master_service, "OANDA_RUNTIME_STATUS_PATH", oanda_file)

    fresh = {
        "running": True,
        "pid": os.getpid(),
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_heartbeat_at": "2999-01-01T00:00:00+00:00",
        "phase": "waiting",
        "wait_seconds": 300,
    }
    bybit_file.write_text(json.dumps(fresh), encoding="utf-8")
    payload = json.loads(asyncio.run(master_service.bybit_monitor_runtime_status()).body.decode("utf-8"))
    assert payload["ui_status"] == "running"

    stale = dict(fresh)
    stale["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    bybit_file.write_text(json.dumps(stale), encoding="utf-8")
    payload = json.loads(asyncio.run(master_service.bybit_monitor_runtime_status()).body.decode("utf-8"))
    assert payload["ui_status"] == "stopped"

    if oanda_file.exists():
        oanda_file.unlink()
    payload = json.loads(asyncio.run(master_service.oanda_monitor_runtime_status()).body.decode("utf-8"))
    assert payload["ui_status"] == "stopped"

    oanda_file.write_text("{bad json", encoding="utf-8")
    payload = json.loads(asyncio.run(master_service.oanda_monitor_runtime_status()).body.decode("utf-8"))
    assert payload["ui_status"] == "unavailable"


def test_env_bootstrap_prefer_external_and_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import env_bootstrap
    import bybit_credentials
    from oanda_monitor import oanda_forex_monitor

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".env").write_text("BYBIT_API_KEY1=repo_key\nOANDA_API_KEY=repo_oanda\n", encoding="utf-8")

    ext_dir = tmp_path / "downloads"
    ext_dir.mkdir()
    (ext_dir / ".env").write_text(
        "BYBIT_API_KEY1=ext_key\nBYBIT_API_SECRET1=ext_secret\n"
        "OANDA_API_KEY=ext_oanda\nOANDA_ACCOUNT_ID=acc_ext\n"
        "OANDA_BASE_URL=https://example.test\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MASTER_ENV_DIR", str(ext_dir))
    monkeypatch.delenv("MASTER_ENV_FILE", raising=False)
    env_bootstrap.load_master_env(base_dir=repo_root, force_reload=True)

    mode, key, secret, _, _ = bybit_credentials.resolve_bybit_credentials()
    assert mode == "live"
    assert key == "ext_key"
    assert secret == "ext_secret"
    assert oanda_forex_monitor._oanda_token() == "ext_oanda"

    override_file = tmp_path / "custom.env"
    override_file.write_text("BYBIT_API_KEY1=file_key\nBYBIT_API_SECRET1=file_secret\n", encoding="utf-8")
    monkeypatch.setenv("MASTER_ENV_FILE", str(override_file))
    env_bootstrap.load_master_env(base_dir=repo_root, force_reload=True)
    _, key2, secret2, _, _ = bybit_credentials.resolve_bybit_credentials()
    assert key2 == "file_key"
    assert secret2 == "file_secret"


def test_wait_helpers_refresh_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    from bybit_monitor import bybit_altcoin_monitor
    from oanda_monitor import oanda_forex_monitor

    bybit_ticks: list[tuple[str, int]] = []
    oanda_ticks: list[tuple[str, int]] = []

    monkeypatch.setattr(bybit_altcoin_monitor, "_heartbeat", lambda **kwargs: bybit_ticks.append((kwargs.get("phase", ""), int(kwargs.get("wait_seconds", 0)))))
    monkeypatch.setattr(bybit_altcoin_monitor.time, "sleep", lambda _n: None)
    bybit_altcoin_monitor.wait_with_log(12, "test")
    assert len(bybit_ticks) >= 3
    assert all(phase == "waiting" for phase, _ in bybit_ticks)

    monkeypatch.setattr(oanda_forex_monitor, "_heartbeat", lambda **kwargs: oanda_ticks.append((kwargs.get("phase", ""), int(kwargs.get("wait_seconds", 0)))))
    monkeypatch.setattr(oanda_forex_monitor.time, "sleep", lambda _n: None)
    oanda_forex_monitor.wait_with_heartbeat(12, "test")
    assert len(oanda_ticks) >= 3
    assert all(phase == "waiting" for phase, _ in oanda_ticks)


def test_oanda_runtime_status_writer_retries_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from oanda_monitor import oanda_forex_monitor
    from shared import atomic_json

    status_path = tmp_path / "runtime_status.json"
    monkeypatch.setattr(oanda_forex_monitor, "RUNTIME_STATUS_PATH", status_path)
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _n: None)
    attempts = {"count": 0}
    real_replace = atomic_json.os.replace

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("[WinError 5] Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_json.os, "replace", flaky_replace)
    oanda_forex_monitor._write_runtime_status(running=True, phase="waiting", wait_seconds=30)
    written = json.loads(status_path.read_text(encoding="utf-8"))
    assert written["running"] is True
    assert attempts["count"] == 3


def test_bybit_runtime_status_writer_best_effort_permanent_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bybit_monitor import bybit_altcoin_monitor
    from shared import atomic_json

    status_path = tmp_path / "runtime_status.json"
    monkeypatch.setattr(bybit_altcoin_monitor, "RUNTIME_STATUS_PATH", status_path)
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _n: None)
    monkeypatch.setattr(atomic_json.os, "replace", lambda _src, _dst: (_ for _ in ()).throw(PermissionError("[WinError 32] Sharing violation")))
    real_write_text = Path.write_text

    def flaky_write_text(path_obj: Path, data: str, *args: object, **kwargs: object) -> int:
        if path_obj == status_path:
            raise PermissionError("[WinError 5] Access is denied")
        return real_write_text(path_obj, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    bybit_altcoin_monitor._write_runtime_status(running=True, phase="waiting", wait_seconds=30)
    captured = capsys.readouterr()
    assert "best-effort runtime status write failed" in captured.err


def test_scanner_status_payload_retries_transient_malformed_read() -> None:
    class FlakyStatusPath:
        def __init__(self) -> None:
            self.calls = 0

        def exists(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            self.calls += 1
            if self.calls < 3:
                return "{bad json"
            return json.dumps(
                {
                    "running": True,
                    "pid": os.getpid(),
                    "last_heartbeat_at": "2999-01-01T00:00:00+00:00",
                    "wait_seconds": 5,
                }
            )

    payload = master_service._scanner_status_payload(FlakyStatusPath())  # type: ignore[arg-type]
    assert payload["ui_status"] == "running"


def test_env_bootstrap_candidate_search_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import env_bootstrap

    env_dir = tmp_path / "downloads"
    env_dir.mkdir()
    (env_dir / "scanner.env").write_text("OANDA_API_KEY=scanner_key\n", encoding="utf-8")
    (env_dir / "env.env").write_text("OANDA_API_KEY=preferred_key\n", encoding="utf-8")

    monkeypatch.setenv("MASTER_ENV_DIR", str(env_dir))
    monkeypatch.delenv("MASTER_ENV_FILE", raising=False)
    info = env_bootstrap.load_master_env(base_dir=tmp_path, force_reload=True)
    assert info["loaded_file"].endswith("env.env")
    assert info["external_loaded"] == "1"
    checked = info["checked_files"]
    assert str((env_dir / "env.env").resolve()) in checked
    assert str((env_dir / ".env").resolve()) in checked
    assert str((env_dir / "scanner.env").resolve()) in checked
    assert str((env_dir / "master.env").resolve()) in checked


def test_env_bootstrap_explicit_file_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import env_bootstrap

    env_dir = tmp_path / "downloads"
    env_dir.mkdir()
    explicit = env_dir / "env.env"
    explicit.write_text("BYBIT_API_KEY1=explicit_key\n", encoding="utf-8")
    (env_dir / ".env").write_text("BYBIT_API_KEY1=fallback_key\n", encoding="utf-8")
    monkeypatch.setenv("MASTER_ENV_DIR", str(env_dir))
    monkeypatch.setenv("MASTER_ENV_FILE", str(explicit))
    info = env_bootstrap.load_master_env(base_dir=tmp_path, force_reload=True)
    assert info["loaded_file"] == str(explicit.resolve())
    assert info["configured_file"] == str(explicit.resolve())
    assert info["external_loaded"] == "1"


def test_run_scanner_local_bat_sets_explicit_env_file() -> None:
    content = (ROOT / "run_scanner_local.bat").read_text(encoding="utf-8")
    assert 'set "MASTER_ENV_FILE=C:\\Users\\User\\Downloads\\env.env"' in content
    assert 'if not exist "%MASTER_ENV_FILE%" (' in content
    assert "exit /b 1" in content


def test_compute_autostart_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "SCANNER_LOCAL_UI_MODE", False)
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.delenv("AUTOSTART_SCRIPTS", raising=False)
    names_default = master_service._compute_autostart_scripts()
    assert "bybit_monitor" in names_default
    assert "oanda_monitor" in names_default

    monkeypatch.setenv("AUTOSTART_SCRIPTS", "  ")
    assert master_service._compute_autostart_scripts() == []

    monkeypatch.setenv("AUTOSTART_SCRIPTS", "OFF")
    assert master_service._compute_autostart_scripts() == []

    monkeypatch.setattr(master_service, "SCANNER_LOCAL_UI_MODE", True)
    monkeypatch.delenv("AUTOSTART_SCRIPTS", raising=False)
    assert master_service._compute_autostart_scripts() == []


def test_run_local_master_control_bat_uses_local_autostart() -> None:
    content = (ROOT / "run_local_master_control.bat").read_text(encoding="utf-8")
    assert 'set "APP_PROFILE=local"' in content
    assert 'set "AUTOSTART_SCRIPTS=bybit_monitor,oanda_monitor,fxweekend-clone"' in content
    assert 'set "SCANNER_LOCAL_UI_MODE=1"' not in content
    assert 'if /I "%~1"=="__worker" goto worker' in content
    assert ":worker" in content
    assert ":restart_master" in content
    assert "goto restart_master" in content
    assert '"%PYTHON_EXE%" -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000' in content
    assert "cmd /v:on /k ^" not in content
    assert '"set APP_PROFILE=%APP_PROFILE% && ^' not in content
    assert 'cmd /d /v:on /k ""%~f0" __worker"' in content
    assert content.index('cmd /d /v:on /k ""%~f0" __worker"') < content.index('start "" "http://127.0.0.1:8000"')


def test_run_local_master_control_bat_no_caret_continued_quoted_restart_loop() -> None:
    content = (ROOT / "run_local_master_control.bat").read_text(encoding="utf-8")
    forbidden_pattern = 'cmd /k ^\n"set '
    assert forbidden_pattern not in content


def test_oanda_config_error_includes_env_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(ValueError) as exc:
        master_service._get_oanda_config("live")
    message = str(exc.value)
    assert "env_loaded_file=" in message
    assert "env_checked=" in message


def test_bybit_run_monitor_resets_baseline_after_long_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    from bybit_monitor import bybit_altcoin_monitor

    monkeypatch.setattr(
        bybit_altcoin_monitor,
        "get_runtime_settings",
        lambda force=False: {"wait_seconds": 10, "percent_threshold": 5.0},
    )
    monkeypatch.setattr(bybit_altcoin_monitor, "_iter_api_bases", lambda: ["https://example.test"])
    monkeypatch.setattr(bybit_altcoin_monitor, "get_bybit_creds", lambda: ("live", "", "", "https://example.test", "env"))
    monkeypatch.setattr(bybit_altcoin_monitor, "_heartbeat", lambda **kwargs: None)
    monkeypatch.setattr(bybit_altcoin_monitor, "wait_with_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bybit_altcoin_monitor, "_load_state", lambda: {"symbols": {}})
    monkeypatch.setattr(bybit_altcoin_monitor, "get_custom_alerts", lambda force=False: [])
    monkeypatch.setattr(bybit_altcoin_monitor, "evaluate_custom_alerts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bybit_altcoin_monitor, "_save_state", lambda *_args, **_kwargs: None)

    alerts: list[str] = []
    monkeypatch.setattr(bybit_altcoin_monitor, "send_notification", lambda _title, msg: alerts.append(msg))

    prices_sequence = iter(
        [
            {"BTCUSDT": 100.0},
            {"BTCUSDT": 160.0},  # would alert, but must be suppressed after long gap
            {"BTCUSDT": 180.0},  # normal cycle after reset can alert
        ]
    )

    def fake_fetch() -> dict[str, float]:
        try:
            return next(prices_sequence)
        except StopIteration as exc:
            raise KeyboardInterrupt from exc

    monkeypatch.setattr(bybit_altcoin_monitor, "fetch_altcoin_prices", fake_fetch)
    timestamps = iter([0.0, 2000.0, 2010.0])
    monkeypatch.setattr(bybit_altcoin_monitor.time, "time", lambda: next(timestamps))

    with pytest.raises(KeyboardInterrupt):
        bybit_altcoin_monitor.run_monitor()

    assert len(alerts) == 1
    assert "180.000000" in alerts[0]


def test_oanda_run_monitor_resets_baseline_after_long_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    from oanda_monitor import oanda_forex_monitor

    monkeypatch.setenv("OANDA_INSTRUMENTS", "EUR_USD")
    monkeypatch.setattr(oanda_forex_monitor, "_oanda_token", lambda: "token")
    monkeypatch.setattr(oanda_forex_monitor, "_oanda_account_id", lambda: "account")
    monkeypatch.setattr(oanda_forex_monitor, "_oanda_base_url", lambda: "https://example.test")
    monkeypatch.setattr(
        oanda_forex_monitor,
        "get_runtime_settings",
        lambda force=False: {"wait_seconds": 10, "percent_threshold": 0.10},
    )
    monkeypatch.setattr(oanda_forex_monitor, "_heartbeat", lambda **kwargs: None)
    monkeypatch.setattr(oanda_forex_monitor, "wait_with_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(oanda_forex_monitor, "_load_state", lambda: {"symbols": {}})
    monkeypatch.setattr(oanda_forex_monitor, "fetch_pip_locations", lambda *_args, **_kwargs: {"EUR_USD": 4})
    monkeypatch.setattr(oanda_forex_monitor, "get_custom_alerts", lambda force=False: [])
    monkeypatch.setattr(oanda_forex_monitor, "evaluate_custom_alerts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(oanda_forex_monitor, "_save_state", lambda *_args, **_kwargs: None)

    alerts: list[str] = []
    monkeypatch.setattr(oanda_forex_monitor, "send_push_notification", lambda _title, msg: alerts.append(msg))

    prices_sequence = iter(
        [
            ({"EUR_USD": 1.0000}, None),
            ({"EUR_USD": 1.0200}, None),  # would alert, but suppressed after long gap
            ({"EUR_USD": 1.0300}, None),  # should alert on normal cycle
        ]
    )

    def fake_fetch_prices(*_args, **_kwargs):
        try:
            return next(prices_sequence)
        except StopIteration as exc:
            raise KeyboardInterrupt from exc

    monkeypatch.setattr(oanda_forex_monitor, "fetch_prices", fake_fetch_prices)
    timestamps = iter([0.0, 2000.0, 2010.0])
    monkeypatch.setattr(oanda_forex_monitor.time, "time", lambda: next(timestamps))

    with pytest.raises(KeyboardInterrupt):
        oanda_forex_monitor.run_monitor()

    assert len(alerts) == 1
    assert "1.030000" in alerts[0]


def test_supervisor_restarts_stopped_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeScript:
        def __init__(self) -> None:
            self.is_running = False
            self.starts = 0

        async def start(self) -> None:
            self.starts += 1
            self.is_running = True

    fake = FakeScript()
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "_scanner_has_external_live_runtime", lambda _name: False)
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BASE_SECONDS", 0.01)
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: fake)

    async def _run() -> None:
        task = asyncio.create_task(master_service._supervise_autostart_scripts(["bybit_monitor"]))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert fake.starts >= 0


def test_supervisor_skips_restart_when_external_runtime_live(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeScript:
        def __init__(self) -> None:
            self.is_running = False
            self.starts = 0

        async def start(self) -> None:
            self.starts += 1
            self.is_running = True

    fake = FakeScript()
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "_scanner_has_external_live_runtime", lambda _name: True)
    monkeypatch.setattr(master_service, "_SCANNER_SUPERVISOR_BASE_SECONDS", 0.01)
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: fake)

    async def _run() -> None:
        task = asyncio.create_task(master_service._supervise_autostart_scripts(["oanda_monitor"]))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert fake.starts == 0


def test_monitor_running_true_when_runtime_status_is_fresh_without_managed_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_service, "get_merged_script_buttons", lambda: [{"id": "monitor", "name": "monitor", "label": "Scanner", "open_url": "/merged/monitor"}])
    monkeypatch.setattr(master_service.script_manager, "list_scripts", lambda: [])
    monkeypatch.setattr(master_service, "_scanner_runtime_is_live", lambda name: name == "bybit_monitor")
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    monitor_row = next(row for row in payload if row["name"] == "monitor")
    assert monitor_row["running"] is True


def test_monitor_running_false_when_runtime_status_is_stale_without_managed_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_service, "get_merged_script_buttons", lambda: [{"id": "monitor", "name": "monitor", "label": "Scanner", "open_url": "/merged/monitor"}])
    monkeypatch.setattr(master_service.script_manager, "list_scripts", lambda: [])
    monkeypatch.setattr(master_service, "_scanner_runtime_is_live", lambda _name: False)
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    monitor_row = next(row for row in payload if row["name"] == "monitor")
    assert monitor_row["running"] is False


def test_monitor_running_true_when_managed_subprocess_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_service, "get_merged_script_buttons", lambda: [{"id": "monitor", "name": "monitor", "label": "Scanner", "open_url": "/merged/monitor"}])
    monkeypatch.setattr(
        master_service.script_manager,
        "list_scripts",
        lambda: [
            {"name": "bybit_monitor", "running": True, "starting": False},
            {"name": "oanda_monitor", "running": False, "starting": False},
        ],
    )
    monkeypatch.setattr(master_service, "_scanner_runtime_is_live", lambda _name: False)
    payload = json.loads(asyncio.run(master_service.list_scripts()).body.decode("utf-8"))
    monitor_row = next(row for row in payload if row["name"] == "monitor")
    assert monitor_row["running"] is True


def test_trading_journal_row_reflects_sync_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service, "LOCAL_ALLOWED_APPS", {"trading-journal"})
    monkeypatch.setattr(master_service, "_sync_state_snapshot", lambda: {"running": True, "error": "sync failed"})

    manager = master_service.ScriptManager([])
    rows = manager.list_scripts()
    row = next(item for item in rows if item["name"] == "trading-journal")
    assert row["running"] is True
    assert row["starting"] is True
    assert row["last_error"] == "sync failed"


def test_managed_script_start_sets_windows_creationflags(monkeypatch: pytest.MonkeyPatch) -> None:
    script_path = ROOT / "render" / "master_service.py"
    script = master_service.ManagedScript(name="test-script", path=script_path, category="Tests")

    class FakeProcess:
        pid = 12345
        stdout = None

    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(master_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(master_service.os, "name", "nt")
    monkeypatch.setattr(master_service.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    def fake_create_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(master_service.asyncio, "create_task", fake_create_task)

    asyncio.run(script.start())
    assert captured["kwargs"]["creationflags"] == master_service.subprocess.CREATE_NEW_PROCESS_GROUP
