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


def test_env_bootstrap_candidate_search_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import env_bootstrap

    env_dir = tmp_path / "downloads"
    env_dir.mkdir()
    (env_dir / "scanner.env").write_text("OANDA_API_KEY=scanner_key\n", encoding="utf-8")

    monkeypatch.setenv("MASTER_ENV_DIR", str(env_dir))
    monkeypatch.delenv("MASTER_ENV_FILE", raising=False)
    info = env_bootstrap.load_master_env(base_dir=tmp_path, force_reload=True)
    assert info["loaded_file"].endswith("scanner.env")
    assert info["external_loaded"] == "1"
    checked = info["checked_files"]
    assert str((env_dir / ".env").resolve()) in checked
    assert str((env_dir / "scanner.env").resolve()) in checked
    assert str((env_dir / "master.env").resolve()) in checked


def test_compute_autostart_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_service, "SCANNER_LOCAL_UI_MODE", False)
    monkeypatch.delenv("AUTOSTART_SCRIPTS", raising=False)
    names_default = master_service._compute_autostart_scripts()
    assert "fxweekend-clone" in names_default

    monkeypatch.setenv("AUTOSTART_SCRIPTS", "  ")
    assert master_service._compute_autostart_scripts() == []

    monkeypatch.setenv("AUTOSTART_SCRIPTS", "OFF")
    assert master_service._compute_autostart_scripts() == []

    monkeypatch.setattr(master_service, "SCANNER_LOCAL_UI_MODE", True)
    monkeypatch.delenv("AUTOSTART_SCRIPTS", raising=False)
    assert master_service._compute_autostart_scripts() == []


def test_oanda_config_error_includes_env_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(ValueError) as exc:
        master_service._get_oanda_config("live")
    message = str(exc.value)
    assert "env_loaded_file=" in message
    assert "env_checked=" in message
