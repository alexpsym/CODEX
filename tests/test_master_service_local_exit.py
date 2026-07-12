import importlib.util
import json
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
