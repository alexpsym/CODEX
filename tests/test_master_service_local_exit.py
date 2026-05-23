import importlib.util
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


def test_local_exit_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.setenv("LOCAL_MASTER_EDGE_DEBUG_PORT", "9222")
    sentinel = tmp_path / "exit.flag"
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", str(sentinel))
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: calls.append("close") or {"ok": True})
    monkeypatch.setattr(master_service, "_write_local_exit_sentinel", lambda: calls.append("sentinel") or sentinel)
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code == 200
    assert res.json().get("ok") is True
    assert calls == ["close", "sentinel", "schedule"]


def test_local_exit_rejected_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "render")
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: calls.append("close") or {"ok": True})
    monkeypatch.setattr(master_service, "_write_local_exit_sentinel", lambda: calls.append("sentinel"))
    monkeypatch.setattr(master_service, "_schedule_local_master_process_exit", lambda delay_seconds=0.75: calls.append("schedule"))
    res = client.post("/api/local-exit", json={"url": "http://127.0.0.1:8000/"})
    assert res.status_code >= 400
    assert calls == []


def test_local_exit_edge_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(master_service.app)
    calls: list[str] = []
    monkeypatch.setattr(master_service, "_resolve_app_profile", lambda: "local")
    monkeypatch.setenv("LOCAL_MASTER_EDGE_DEBUG_PORT", "9222")
    monkeypatch.setenv("LOCAL_MASTER_EXIT_REQUEST", "C:\\temp\\flag.txt")
    monkeypatch.setattr(master_service, "_close_local_master_edge_target", lambda _url: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(master_service, "_write_local_exit_sentinel", lambda: calls.append("sentinel"))
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
