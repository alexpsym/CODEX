from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_executor():
    name = "fxweekend_liquidate_backoff_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "fxweekend-clone" / "liquidate.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_executor()


def _settings(account_modes=None, **overrides):
    payload = {
        "schema_version": fx.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "account_modes": (
            list(account_modes)
            if account_modes is not None
            else ["demo", "live"]
        ),
    }
    payload.update(overrides)
    return fx.migrate_settings(payload)


def _open_items():
    return {
        "positions": [
            {
                "instrument": "EUR_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            }
        ],
        "trades": [{"id": "42", "instrument": "EUR_USD"}],
        "requests": [
            {"scope": "positions", "http_status": 200, "ok": True},
            {"scope": "trades", "http_status": 200, "ok": True},
        ],
        "errors": [],
    }


def _flat_items():
    return {
        "positions": [],
        "trades": [],
        "requests": [
            {"scope": "positions", "http_status": 200, "ok": True},
            {"scope": "trades", "http_status": 200, "ok": True},
        ],
        "errors": [],
    }


def test_300_second_backoff_refreshes_heartbeat_without_changing_process_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = "2026-07-27T18:06:48+10:00"
    monkeypatch.setattr(
        fx,
        "STATUS",
        {
            **fx._empty_status(),
            "running": True,
            "executor_pid": 24680,
            "executor_instance_id": "instance-24680",
            "executor_started_at": started_at,
            "heartbeat_at": started_at,
            "state": "API failure",
            "consecutive_failures": 7,
        },
    )
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_args, **_kwargs: None)
    sleeps = []
    monkeypatch.setattr(fx.time, "sleep", lambda seconds: sleeps.append(seconds))
    heartbeat_times = iter(
        (
            fx.BRISBANE_TZ.localize(datetime(2026, 7, 27, 18, 7, 18))
            + timedelta(seconds=30 * index)
        ).isoformat()
        for index in range(10)
    )
    monkeypatch.setattr(fx, "_iso_now", lambda: next(heartbeat_times))
    updates = []
    real_update_status = fx.update_status

    def record_update(**kwargs):
        updates.append(dict(kwargs))
        return real_update_status(**kwargs)

    monkeypatch.setattr(fx, "update_status", record_update)

    fx.wait_with_heartbeat(300, "failure backoff after API failure")

    status = fx.status_snapshot()
    assert sleeps == [30.0] * 10
    assert len([item for item in updates if item.get("heartbeat_at")]) == 11
    assert updates[0]["sleeping"] is True
    assert updates[-1]["sleeping"] is False
    assert status["heartbeat_at"] == "2026-07-27T18:11:48+10:00"
    assert status["executor_pid"] == 24680
    assert status["executor_instance_id"] == "instance-24680"
    assert status["executor_started_at"] == started_at
    assert status["running"] is True
    assert status["scheduled_delay_seconds"] == 0.0


def test_scheduler_enters_full_backoff_with_one_stable_pid_and_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopLoop(BaseException):
        pass

    settings = _settings(
        ["demo", "live"],
        check_interval_seconds=60,
        max_retry_backoff_seconds=300,
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setenv(
        "FXWEEKEND_EXECUTOR_INSTANCE_ID",
        "spawn-instance-token",
    )
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx, "load_settings", lambda: settings)
    monkeypatch.setattr(
        fx,
        "scheduler_iteration",
        lambda _settings: {
            "state": "API failure",
            "consecutive_failures": 7,
        },
    )
    monkeypatch.setattr(
        fx,
        "_scheduler_delay_seconds",
        lambda _settings, _status: 300.0,
    )
    observed = {}

    def stop_at_backoff(delay, reason):
        observed.update(
            {
                "delay": delay,
                "reason": reason,
                "status": fx.status_snapshot(),
            }
        )
        raise StopLoop()

    monkeypatch.setattr(fx, "wait_with_heartbeat", stop_at_backoff)

    with pytest.raises(StopLoop):
        fx.scheduler_loop()

    assert observed["delay"] == 300.0
    assert "API failure" in observed["reason"]
    assert observed["status"]["executor_pid"] == os.getpid()
    assert (
        observed["status"]["executor_instance_id"]
        == "spawn-instance-token"
    )
    assert observed["status"]["executor_started_at"]
    assert observed["status"]["running"] is True


def test_failure_backoff_leaves_headroom_for_a_retry_before_market_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        check_interval_seconds=60,
        max_retry_backoff_seconds=300,
    )
    status = {"state": "API failure", "consecutive_failures": 7}
    now = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 6, 59, 25))

    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {
            "phase": "before cutoff",
            "market_close": now + timedelta(seconds=35),
        },
    )
    assert fx._scheduler_delay_seconds(settings, status, now) == 300.0

    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {
            "phase": "closure",
            "market_close": now + timedelta(seconds=35),
        },
    )
    delay = fx._scheduler_delay_seconds(settings, status, now)
    assert delay == pytest.approx(8.75)
    assert 0.0 < delay < 35.0


def test_scheduler_retries_recoverable_failure_before_market_close_with_same_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopLoop(BaseException):
        pass

    settings = _settings(
        ["demo", "live"],
        check_interval_seconds=60,
        max_retry_backoff_seconds=300,
    )
    logical_now = {
        "value": fx.BRISBANE_TZ.localize(
            datetime(2026, 7, 25, 6, 59, 25)
        )
    }
    market_close = logical_now["value"] + timedelta(seconds=35)
    attempts = []
    identities = []

    def iteration(_settings):
        attempts.append(logical_now["value"])
        snapshot = fx.status_snapshot()
        identities.append(
            (
                snapshot.get("executor_pid"),
                snapshot.get("executor_started_at"),
            )
        )
        if len(attempts) == 1:
            return {
                "state": "API failure",
                "consecutive_failures": 7,
            }
        raise StopLoop()

    def window(_settings, current=None):
        observed = current or logical_now["value"]
        return {
            "phase": (
                "closure" if observed < market_close else "missed"
            ),
            "market_close": market_close,
        }

    def advance(delay, _reason):
        assert 0.0 < delay < (
            market_close - logical_now["value"]
        ).total_seconds()
        logical_now["value"] += timedelta(seconds=delay)
        fx.update_status(heartbeat_at=logical_now["value"].isoformat())

    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx, "load_settings", lambda: settings)
    monkeypatch.setattr(fx, "scheduler_iteration", iteration)
    monkeypatch.setattr(fx, "closure_window", window)
    monkeypatch.setattr(fx, "_now_brisbane", lambda: logical_now["value"])
    monkeypatch.setattr(fx, "wait_with_heartbeat", advance)

    with pytest.raises(StopLoop):
        fx.scheduler_loop()

    assert len(attempts) == 2
    assert attempts[0] < attempts[1] < market_close
    assert identities[0] == identities[1]
    assert identities[0][0] == os.getpid()
    assert identities[0][1]


@pytest.mark.parametrize(
    ("close_method", "endpoint_fragment"),
    [
        ("positions", "/positions/EUR_USD/close"),
        ("trades", "/trades/42/close"),
    ],
)
def test_scheduled_liquidation_closes_and_verifies_demo_and_live_flat(
    close_method: str,
    endpoint_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        ["demo", "live"],
        close_method=close_method,
        dry_run=False,
    )
    fetch_counts = {"demo": 0, "live": 0}

    def resolve(mode):
        return {
            "mode": mode,
            "account_id": f"{mode}-account",
            "api_key": f"{mode}-key",
            "base_url": f"https://{mode}.example.invalid/v3",
        }

    def get_open(config):
        mode = config["mode"]
        fetch_counts[mode] += 1
        return _open_items() if fetch_counts[mode] == 1 else _flat_items()

    class Response:
        status_code = 200

    close_urls = []

    def request(method, url, _headers, _payload=None):
        assert method == "PUT"
        close_urls.append(url)
        return Response()

    monkeypatch.setattr(fx, "resolve_account_config", resolve)
    monkeypatch.setattr(fx, "_get_open_items", get_open)
    monkeypatch.setattr(fx, "_request", request)
    monkeypatch.setattr(fx, "log", lambda _message: None)

    result = fx.run_liquidation(settings, "scheduled", can_close=True)

    assert result["verified_flat"] is True
    assert result["state"] == "verified flat"
    assert fetch_counts == {"demo": 2, "live": 2}
    assert list(result["accounts"]) == ["demo", "live"]
    assert all(
        account["state"] == "verified flat"
        and account["position_count"] == 0
        and account["trade_count"] == 0
        for account in result["accounts"].values()
    )
    assert len(close_urls) == 2
    assert any(
        "demo.example.invalid" in url and endpoint_fragment in url
        for url in close_urls
    )
    assert any(
        "live.example.invalid" in url and endpoint_fragment in url
        for url in close_urls
    )


def test_close_requests_crossing_market_close_block_live_after_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        ["demo", "live"],
        close_method="trades",
        dry_run=False,
    )
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0, 0)
    )
    close_window_checks = iter((True, False))
    closed_modes = set()
    close_urls = []

    def resolve(mode):
        return {
            "mode": mode,
            "account_id": f"{mode}-account",
            "api_key": f"{mode}-key",
            "base_url": f"https://{mode}.example.invalid/v3",
        }

    def get_open(config):
        return (
            _flat_items()
            if config["mode"] in closed_modes
            else _open_items()
        )

    class Response:
        status_code = 200

    def request(method, url, _headers, _payload=None):
        assert method == "PUT"
        close_urls.append(url)
        closed_modes.add(
            "demo" if "demo.example.invalid" in url else "live"
        )
        return Response()

    monkeypatch.setattr(fx, "resolve_account_config", resolve)
    monkeypatch.setattr(fx, "_get_open_items", get_open)
    monkeypatch.setattr(fx, "_request", request)
    monkeypatch.setattr(
        fx,
        "_close_deadline_is_open",
        lambda deadline: (
            deadline == market_close
            and next(close_window_checks)
        ),
    )
    monkeypatch.setattr(fx, "log", lambda _message: None)

    result = fx.run_liquidation(
        settings,
        "scheduled",
        can_close=True,
        close_deadline=market_close,
    )

    assert result["verified_flat"] is False
    assert result["state"] == "missed cutoff/market closed"
    assert result["accounts"]["demo"]["state"] == "verified flat"
    assert (
        result["accounts"]["live"]["state"]
        == "missed cutoff/market closed"
    )
    assert len(close_urls) == 1
    assert "demo.example.invalid" in close_urls[0]
    assert all("live.example.invalid" not in url for url in close_urls)


def test_manual_liquidation_does_not_overlap_scheduler_or_mutate_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(["demo", "live"], enabled=True)
    scheduler_status = {
        **fx._empty_status(),
        "running": True,
        "executor_pid": 13579,
        "executor_started_at": "2026-07-27T18:06:48+10:00",
        "heartbeat_at": "2026-07-27T18:12:00+10:00",
        "state": "closing",
        "state_detail": "Scheduled liquidation is in progress.",
    }
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0, 0)
    )
    monkeypatch.setattr(fx, "STATUS", scheduler_status)
    monkeypatch.setattr(fx, "load_settings", lambda: settings)
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {
            "phase": "closure",
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(
        fx,
        "process_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("overlapping manual liquidation reached OANDA")
        ),
    )
    before = fx.status_snapshot()

    assert fx._liquidation_lock.acquire(blocking=False)
    try:
        response = fx.app.test_client().post("/api/run_now")
    finally:
        fx._liquidation_lock.release()

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["state"] == "liquidation already in progress"
    assert "No duplicate close request" in payload["error"]
    assert fx.status_snapshot() == before


def test_outside_window_manual_live_block_does_not_mutate_scheduler_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_status = {
        **fx._empty_status(),
        "running": True,
        "executor_pid": 13579,
        "executor_started_at": "2026-07-27T18:06:48+10:00",
        "heartbeat_at": "2026-07-27T18:12:00+10:00",
        "state": "before cutoff",
        "state_detail": "Next cutoff is scheduled.",
        "consecutive_failures": 0,
    }
    monkeypatch.setattr(fx, "STATUS", scheduler_status)
    monkeypatch.setattr(
        fx,
        "load_settings",
        lambda: _settings(["demo", "live"], enabled=True),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )
    monkeypatch.setattr(fx, "log", lambda _message: None)
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("outside-window manual Live call reached OANDA")
        ),
    )
    before = fx.status_snapshot()

    response = fx.app.test_client().post("/api/run_now")

    assert response.status_code == 409
    assert response.get_json()["ok"] is False
    assert "blocked outside" in response.get_json()["error"]
    assert fx.status_snapshot() == before


def test_page_names_and_handles_manual_liquidation_without_json_navigation() -> None:
    source = fx.PAGE_TEMPLATE

    assert "Run liquidation now" in source
    assert "is not a start button" in source
    assert "does not start, enable, or activate" in source
    assert 'id="fxweekend-liquidation-result"' in source
    assert 'form.addEventListener("submit"' in source
    assert "event.preventDefault();" in source
    assert "await fetch(form.action" in source
    assert "output.textContent" in source
    assert ">Run now</button>" not in source
