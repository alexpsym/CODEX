import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_master_service_local_exit", ROOT / "render" / "master_service.py")
master_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = master_service
SPEC.loader.exec_module(master_service)


def _launcher_build_files() -> tuple[str, ...]:
    script = (ROOT / "tools" / "windows_launchers" / "ensure_local_master_server.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$buildFiles\s*=\s*@\((.*?)\)", script, re.DOTALL)
    assert match is not None
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def test_local_build_file_lists_match_launcher_preflight() -> None:
    expected = (
        "render/master_service.py",
        "render/atr_scanner.py",
        "render/static/atr_scanner.js",
        "render/static/calculator.js",
        "render/static/dashboard.js",
        "render/static/history_page.js",
        "render/static/instrument_lookup.js",
        "render/static/open_orders.js",
        "render/static/trading_journal.js",
        "render/static/trading_journal_equity_curve.js",
        "tools/master_journal_workbook.py",
        "run_local_master_control.bat",
        "tools/windows_launchers/local_master_worker_console.bat",
        "tools/windows_launchers/stream_local_master_worker.ps1",
        "tools/windows_launchers/ensure_local_master_server.ps1",
        "tools/windows_launchers/write_local_master_normal_exit_marker.ps1",
    )

    assert master_service.LOCAL_BUILD_FILES == expected
    assert _launcher_build_files() == expected


def test_local_source_stamp_tracks_dashboard_history_and_atr_scanner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    build_files = (
        "render/master_service.py",
        "render/static/dashboard.js",
        "render/static/history_page.js",
        "render/atr_scanner.py",
        "render/static/atr_scanner.js",
    )
    for rel in build_files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"initial {rel}\n", encoding="utf-8")

    monkeypatch.setattr(master_service, "BASE_DIR", tmp_path)
    monkeypatch.setattr(master_service, "LOCAL_BUILD_FILES", build_files)
    baseline = master_service._local_source_stamp()

    dashboard_path = tmp_path / "render" / "static" / "dashboard.js"
    dashboard_path.write_text("dashboard changed\n", encoding="utf-8")
    assert master_service._local_source_stamp() != baseline

    dashboard_path.write_text("initial render/static/dashboard.js\n", encoding="utf-8")
    history_baseline = master_service._local_source_stamp()
    history_path = tmp_path / "render" / "static" / "history_page.js"
    history_path.write_text("history changed\n", encoding="utf-8")
    assert master_service._local_source_stamp() != history_baseline

    history_path.write_text("initial render/static/history_page.js\n", encoding="utf-8")
    for rel in ("render/atr_scanner.py", "render/static/atr_scanner.js"):
        scanner_baseline = master_service._local_source_stamp()
        scanner_path = tmp_path / rel
        scanner_path.write_text(f"changed {rel}\n", encoding="utf-8")
        assert master_service._local_source_stamp() != scanner_baseline
        scanner_path.write_text(f"initial {rel}\n", encoding="utf-8")


def test_local_build_info_exposes_source_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    res = client.get("/api/local-build-info")
    assert res.status_code == 200
    payload = res.json()
    assert payload["root"] == str(ROOT)
    assert isinstance(payload["source_stamp"], str)
    assert len(payload["source_stamp"]) == 16
    assert payload["app_profile"] == "local"
    assert payload["pid"]


def test_local_exit_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.setenv("LOCAL_MASTER_EDGE_DEBUG_PORT", "9222")
    sentinel = tmp_path / "exit.flag"
    normal = tmp_path / "exit.normal"
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", str(sentinel))
    monkeypatch.setenv("LOCAL_MASTER_NORMAL_EXIT_FILE", str(normal))
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: calls.append("close") or {"ok": True})
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: calls.append("markers") or (sentinel, normal))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))

    class FakeLogger:
        def info(self, _msg: str, *_args: object) -> None:
            calls.append("log")

    monkeypatch.setattr(master_service, "APP_LOGGER", FakeLogger())
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code == 200
    assert res.json().get("ok") is True
    assert res.json()["normal_marker"] == str(normal)
    assert calls == ["close", "markers", "log", "schedule"]


def test_local_shutdown_success_without_edge_close(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    sentinel = tmp_path / "exit.flag"
    normal = tmp_path / "exit.normal"
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", str(sentinel))
    monkeypatch.setenv("LOCAL_MASTER_NORMAL_EXIT_FILE", str(normal))
    monkeypatch.setattr(
        master_service,
        "_close_local_master_edge_target",
        lambda _url: (_ for _ in ()).throw(AssertionError("edge close should not be called")),
    )
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: calls.append("markers") or (sentinel, normal))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))

    class FakeLogger:
        def info(self, _msg: str, *_args: object) -> None:
            calls.append("log")

    monkeypatch.setattr(master_service, "APP_LOGGER", FakeLogger())
    res = client.post("/api/local-shutdown", json={"reason": "launcher_preflight"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["reason"] == "launcher_preflight"
    assert payload["sentinel"] == str(sentinel)
    assert payload["normal_marker"] == str(normal)
    assert calls == ["markers", "log", "schedule"]


def test_local_exit_markers_are_written_before_sigterm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sentinel = tmp_path / "exit.flag"
    normal = tmp_path / "exit.normal"
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", str(sentinel))
    monkeypatch.setenv("LOCAL_MASTER_NORMAL_EXIT_FILE", str(normal))

    sentinel_path, normal_path = master_service._write_local_exit_markers("launcher_preflight", "local_shutdown")

    assert sentinel_path == sentinel
    assert normal_path == normal
    sentinel_payload = json.loads(sentinel.read_text(encoding="utf-8"))
    normal_payload = json.loads(normal.read_text(encoding="utf-8"))
    assert sentinel_payload == normal_payload
    assert normal_payload["reason"] == "launcher_preflight"
    assert normal_payload["requesting_action"] == "local_shutdown"
    assert normal_payload["server_pid"] == master_service.os.getpid()
    assert normal_payload["timestamp"]


def test_local_exit_rejected_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "render")
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: calls.append("close") or {"ok": True})
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: calls.append("markers"))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code >= 400
    assert calls == []


def test_local_shutdown_rejected_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "render")
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: calls.append("markers"))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-shutdown", json={"reason": "launcher_preflight"})
    assert res.status_code >= 400
    assert calls == []


def test_local_exit_edge_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.setenv("LOCAL_MASTER_EDGE_DEBUG_PORT", "9222")
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", "C:\\temp\\flag.txt")
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: calls.append("markers"))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code >= 400
    assert calls == []


def test_local_exit_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.delenv("LOCAL_MASTER_EDGE_DEBUG_PORT", raising=False)
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", "")
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code >= 400
    assert calls == []


def test_local_shutdown_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.delenv("LOCAL_MASTER_EXIT_REQUEST", raising=False)
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-shutdown", json={"reason": "launcher_preflight"})
    assert res.status_code >= 400
    assert calls == []


def test_local_exit_uses_defined_app_logger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = TestClient(master_service.app)
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.setenv("LOCAL_MASTER_EDGE_DEBUG_PORT", "9222")
    sentinel = tmp_path / "exit.flag"
    normal = tmp_path / "exit.normal"
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", str(sentinel))
    monkeypatch.setenv("LOCAL_MASTER_NORMAL_EXIT_FILE", str(normal))
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: {"ok": True, "target_url": "http://127.0.0.1:8000/"})
    monkeypatch.setattr(master_service, "_write_local_exit_markers", lambda _reason, _action: (sentinel, normal))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: None)

    class FakeLogger:
        def __init__(self) -> None:
            self.logged = False

        def info(self, _msg: str, *_args: object) -> None:
            self.logged = True

    fake_logger = FakeLogger()
    monkeypatch.setattr(master_service, "APP_LOGGER", fake_logger)
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code == 200
    assert fake_logger.logged is True
