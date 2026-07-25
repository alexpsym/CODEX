import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_executor():
    name = "fxweekend_liquidate_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "fxweekend-clone" / "liquidate.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_executor()


def _flat():
    return {"positions": [], "trades": [], "requests": [{"http_status": 200}]}


def _open():
    return {
        "positions": [
            {
                "instrument": "EUR_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            }
        ],
        "trades": [{"id": "42", "instrument": "EUR_USD"}],
        "requests": [{"http_status": 200}],
    }


def test_error_and_log_sanitization_redacts_realistic_oanda_account_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = "101-001-12345678-001"
    raw = (
        "GET https://api-fxtrade.oanda.com/v3/accounts/"
        f"{account_id}/openTrades failed"
    )
    safe = fx._safe_error(raw)
    assert account_id not in safe
    assert "/accounts/[account]/openTrades" in safe

    log_path = tmp_path / "fxweekend.log"
    monkeypatch.setattr(fx, "LOG_FILE", log_path)
    fx.log(raw)
    logged = log_path.read_text(encoding="utf-8")
    assert account_id not in logged
    assert "/accounts/[account]/openTrades" in logged


def test_enabled_false_survives_startup_and_legacy_hours_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "enabled": False,
                "cutoff_hour_dst": 5,
                "cutoff_hour_standard": 6,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fx, "SETTINGS_PATH", settings_path)
    settings = fx.load_settings()
    assert settings["enabled"] is False
    assert settings["cutoff_time_dst"] == "05:00"
    assert settings["cutoff_time_standard"] == "06:00"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["enabled"] is False


def test_dst_and_standard_cutoffs_use_brisbane_hhmm_precision() -> None:
    settings = fx.migrate_settings(
        {
            "trigger_weekday": 5,
            "cutoff_time_dst": "05:15",
            "cutoff_time_standard": "06:30",
        }
    )
    winter = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 4, 0))
    summer = fx.BRISBANE_TZ.localize(datetime(2026, 1, 24, 4, 0))
    assert fx.compute_next_cutoff(settings, winter).strftime("%Y-%m-%d %H:%M") == "2026-07-25 05:15"
    assert fx.compute_next_cutoff(settings, summer).strftime("%Y-%m-%d %H:%M") == "2026-01-24 06:30"


def test_demo_and_live_credentials_and_urls_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import oanda_api

    for name in (
        "OANDA_API_KEY",
        "OANDA_ACCOUNT_ID",
        "OANDA_API_URL",
        "OANDA_BASE_URL",
        "OANDA_URL",
        "OANDA_API_KEY_DEMO",
        "OANDA_ACCOUNT_ID_DEMO",
        "OANDA_API_URL_DEMO",
        "OANDA_BASE_URL_DEMO",
        "OANDA_URL_DEMO",
        "OANDA_API_URL_PRACTICE",
        "OANDA_BASE_URL_PRACTICE",
        "OANDA_URL_PRACTICE",
        "OANDA_API_KEY_LIVE",
        "OANDA_ACCOUNT_ID_LIVE",
        "OANDA_API_URL_LIVE",
        "OANDA_BASE_URL_LIVE",
        "OANDA_URL_LIVE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OANDA_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID_DEMO", "demo-id")
    monkeypatch.setenv("OANDA_API_KEY_LIVE", "live-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID_LIVE", "live-id")
    demo = oanda_api.resolve_account_config("demo")
    live = oanda_api.resolve_account_config("live")
    assert demo["api_key"] == "demo-key"
    assert demo["account_id"] == "demo-id"
    assert demo["base_url"] == "https://api-fxpractice.oanda.com/v3"
    assert live["api_key"] == "live-key"
    assert live["account_id"] == "live-id"
    assert live["base_url"] == "https://api-fxtrade.oanda.com/v3"


def test_missing_demo_credentials_do_not_hide_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(mode):
        if mode == "demo":
            raise fx.OandaAPIError("OANDA_API_KEY_DEMO is missing")
        return {
            "mode": "live",
            "account_id": "live-id",
            "api_key": "live-key",
            "base_url": "https://api-fxtrade.oanda.com/v3",
        }

    monkeypatch.setattr(fx, "resolve_account_config", resolve)
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())
    result = fx.run_liquidation(
        fx.migrate_settings({"account_modes": ["demo", "live"]}), "test"
    )
    assert result["accounts"]["demo"]["state"] == "credential failure"
    assert result["accounts"]["live"]["state"] == "verified flat"
    assert result["verified_flat"] is False


def test_missing_live_credentials_do_not_hide_demo_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(mode):
        if mode == "live":
            raise fx.OandaAPIError("OANDA_API_KEY_LIVE is missing")
        return {
            "mode": "demo",
            "account_id": "demo-id",
            "api_key": "demo-key",
            "base_url": "https://api-fxpractice.oanda.com/v3",
        }

    monkeypatch.setattr(fx, "resolve_account_config", resolve)
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())
    result = fx.run_liquidation(
        fx.migrate_settings({"account_modes": ["demo", "live"]}), "test"
    )
    assert result["accounts"]["demo"]["state"] == "verified flat"
    assert result["accounts"]["live"]["state"] == "credential failure"
    assert result["verified_flat"] is False


def test_partial_close_is_not_success_and_post_close_refetch_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    calls = []

    def get_open(_config):
        calls.append(True)
        return _open()

    monkeypatch.setattr(fx, "_get_open_items", get_open)
    monkeypatch.setattr(
        fx,
        "_close_requested_scope",
        lambda *_a, **_k: [
            {
                "scope": "position",
                "instrument": "EUR_USD",
                "ok": False,
                "http_status": 500,
            }
        ],
    )
    result = fx.process_account("live", fx.migrate_settings({"account_modes": ["live"]}))
    assert len(calls) == 2
    assert result["state"] == "partial closure failure"
    assert result["open_count"] == 1


def test_success_requires_post_close_flat_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    opened = iter([_open(), _flat()])
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: next(opened))
    monkeypatch.setattr(
        fx,
        "_close_requested_scope",
        lambda *_a, **_k: [{"scope": "position", "instrument": "EUR_USD", "ok": True}],
    )
    result = fx.process_account("live", fx.migrate_settings({"account_modes": ["live"]}))
    assert result["state"] == "verified flat"
    assert result["last_verified_flat_at"]


def test_close_network_exception_keeps_per_item_results_and_still_refetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    opened = {
        "positions": [
            {
                "instrument": "EUR_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            },
            {
                "instrument": "GBP_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            },
        ],
        "trades": [],
        "requests": [{"http_status": 200}],
    }
    fetches = iter([opened, _flat()])
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: next(fetches))
    close_calls = []

    class Response:
        status_code = 200

    def close_request(*args, **kwargs):
        close_calls.append((args, kwargs))
        if len(close_calls) == 1:
            raise fx.requests.ConnectionError("temporary connection failure")
        return Response()

    monkeypatch.setattr(fx, "_request", close_request)
    result = fx.process_account(
        "live", fx.migrate_settings({"account_modes": ["live"]})
    )
    assert len(close_calls) == 2
    assert len(result["closures"]) == 2
    assert result["closures"][0]["ok"] is False
    assert "request failed" in result["closures"][0]["error"]
    assert result["closures"][1]["ok"] is True
    assert result["state"] == "verified flat"
    assert result["open_count"] == 0


def test_each_close_item_emits_progress_for_executor_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    opened = {
        "positions": [
            {
                "instrument": "EUR_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            },
            {
                "instrument": "GBP_USD",
                "long": {"units": "10"},
                "short": {"units": "0"},
            },
        ],
        "trades": [],
        "requests": [{"http_status": 200}],
    }
    fetches = iter([opened, _flat()])
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: next(fetches))

    class Response:
        status_code = 200

    monkeypatch.setattr(fx, "_request", lambda *_args, **_kwargs: Response())
    observed_closure_counts = []
    result = fx.process_account(
        "live",
        fx.migrate_settings({"account_modes": ["live"]}),
        on_state_change=lambda state: observed_closure_counts.append(
            len(state.get("closures") or [])
        ),
    )

    assert observed_closure_counts == [0, 1, 2]
    assert result["state"] == "verified flat"


def test_failed_attempt_retries_and_new_position_after_flat_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {"phase": "closure", "cutoff": cutoff},
    )
    attempts = iter(
        [
            {
                "state": "API failure",
                "result": "API failure",
                "error": "temporary",
                "last_attempt_at": "one",
                "accounts": {"live": {"state": "API failure"}},
                "verified_flat": False,
            },
            {
                "state": "verified flat",
                "result": "verified flat",
                "error": None,
                "last_attempt_at": "two",
                "last_verified_flat_at": "two",
                "accounts": {"live": {"state": "verified flat", "open_count": 0}},
                "verified_flat": True,
            },
        ]
    )
    calls = []

    def run(*_a, **_k):
        calls.append(True)
        return next(attempts)

    monkeypatch.setattr(fx, "run_liquidation", run)
    monkeypatch.setattr(fx, "STATUS_PATH", Path(os.devnull))
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    fx.scheduler_iteration(settings, fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 30)))
    fx.scheduler_iteration(settings, fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 31)))
    assert len(calls) == 2
    assert fx.status_snapshot()["state"] == "verified flat"


def test_before_cutoff_read_only_check_exposes_api_failure_and_open_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["demo", "live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {"phase": "before cutoff", "cutoff": cutoff},
    )
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda mode: {
            "mode": mode,
            "account_id": f"{mode}-id",
            "api_key": f"{mode}-key",
            "base_url": "https://example.invalid/v3",
        },
    )

    def get_open(config):
        if config["mode"] == "demo":
            raise fx.requests.HTTPError("401 revoked")
        return _open()

    monkeypatch.setattr(fx, "_get_open_items", get_open)
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 24, 12, 0)),
    )
    assert status["state"] == "API failure"
    assert status["accounts"]["demo"]["state"] == "API failure"
    assert status["accounts"]["live"]["state"] == "before cutoff"
    assert status["accounts"]["live"]["open_count"] == 1
    assert status["last_access_check_at"]


def test_open_item_get_failure_keeps_http_evidence_and_checks_both_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def request(method, url, *_args, **_kwargs):
        calls.append((method, url))
        if url.endswith("/openPositions"):
            return Response(500, {})
        return Response(200, {"trades": []})

    monkeypatch.setattr(fx, "_request", request)
    result = fx.process_account(
        "live",
        fx.migrate_settings({"account_modes": ["live"]}),
    )

    assert [url.rsplit("/", 1)[-1] for _method, url in calls] == [
        "openPositions",
        "openTrades",
    ]
    assert result["state"] == "API failure"
    assert result["closures"] == []
    assert result["requests"] == [
        {
            "scope": "positions",
            "method": "GET",
            "http_status": 500,
            "ok": False,
            "error": "positions GET failed with HTTP 500",
        },
        {
            "scope": "trades",
            "method": "GET",
            "http_status": 200,
            "ok": True,
        },
    ]
    assert result["position_count"] is None
    assert result["trade_count"] == 0
    assert result["open_count"] is None


def test_open_item_network_failure_is_retained_without_suppressing_other_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"trades": []}

    def request(method, url, *_args, **_kwargs):
        calls.append((method, url))
        if url.endswith("/openPositions"):
            raise fx.requests.ConnectionError("temporary network failure")
        return Response()

    monkeypatch.setattr(fx, "_request", request)
    result = fx.process_account(
        "live",
        fx.migrate_settings({"account_modes": ["live"]}),
    )

    assert len(calls) == 2
    assert result["state"] == "API failure"
    assert result["requests"][0]["scope"] == "positions"
    assert result["requests"][0]["http_status"] is None
    assert result["requests"][0]["ok"] is False
    assert "temporary network failure" in result["requests"][0]["error"]
    assert result["requests"][1]["scope"] == "trades"
    assert result["requests"][1]["ok"] is True


def test_closing_state_is_persisted_while_close_call_is_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {"phase": "closure", "cutoff": cutoff},
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    observed = []

    def run(*_args, progress_callback=None, **_kwargs):
        assert progress_callback is not None
        progress_callback(
            "live",
            {
                "live": {
                    "mode": "live",
                    "state": "closing",
                    "open_count": 1,
                }
            },
        )
        observed.append(fx.status_snapshot()["state"])
        return {
            "state": "verified flat",
            "result": "verified flat",
            "error": None,
            "last_attempt_at": "now",
            "last_verified_flat_at": "now",
            "accounts": {"live": {"state": "verified flat", "open_count": 0}},
            "verified_flat": True,
        }

    monkeypatch.setattr(fx, "run_liquidation", run)
    fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 30)),
    )
    assert observed == ["closing"]


def test_scheduler_loop_recovers_after_unexpected_iteration_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopLoop(BaseException):
        pass

    settings = fx.migrate_settings(
        {
            "account_modes": ["live"],
            "check_interval_seconds": 5,
        }
    )
    calls = {"iterations": 0}
    updates = []

    def iteration(_settings):
        calls["iterations"] += 1
        if calls["iterations"] == 1:
            raise RuntimeError("unexpected iteration failure")
        raise StopLoop()

    def update(**kwargs):
        updates.append(dict(kwargs))
        return {
            "state": kwargs.get("state"),
            "consecutive_failures": kwargs.get(
                "consecutive_failures",
                0,
            ),
        }

    monkeypatch.setattr(fx, "load_settings", lambda: settings)
    monkeypatch.setattr(fx, "scheduler_iteration", iteration)
    monkeypatch.setattr(fx, "status_snapshot", lambda: {"consecutive_failures": 0})
    monkeypatch.setattr(fx, "update_status", update)
    monkeypatch.setattr(fx, "log", lambda _message: None)
    monkeypatch.setattr(fx.time, "sleep", lambda _seconds: None)

    with pytest.raises(StopLoop):
        fx.scheduler_loop()

    assert calls["iterations"] == 2
    retry = [item for item in updates if item.get("state") == "retry pending"]
    assert len(retry) == 1
    assert retry[0]["running"] is True
    assert "unexpected iteration failure" in retry[0]["last_error"]


def test_completed_window_requires_fresh_post_window_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    config = {
        "account_id": "live-id",
        "api_key": "key",
        "base_url": "https://example.invalid/v3",
    }
    monkeypatch.setattr(
        fx, "resolve_account_config", lambda _mode: dict(config)
    )
    fx.update_status(
        last_verified_window_cutoff=cutoff.isoformat(),
        last_verified_flat_at="2026-07-25T06:59:00+10:00",
        last_verified_window_scope_fingerprint=(
            fx._coverage_scope_fingerprint(settings)
        ),
        last_verified_window_account_times={
            "live": "2026-07-25T06:59:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": fx._account_scope_hash("live", config),
        },
        state="verified flat",
    )
    calls = []

    def verify(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "state": "missed cutoff/market closed",
            "result": "missed cutoff/market closed",
            "error": "live: 1 open item remains",
            "last_attempt_at": "2026-07-25T07:01:00+10:00",
            "accounts": {
                "live": {
                    "state": "missed cutoff/market closed",
                    "open_count": 1,
                }
            },
            "verified_flat": False,
        }

    monkeypatch.setattr(fx, "run_liquidation", verify)
    status = fx.scheduler_iteration(
        settings, fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1))
    )
    assert len(calls) == 1
    assert calls[0]["can_close"] is False
    assert calls[0]["allow_post_window_flat_verification"] is True
    assert status["state"] == "missed cutoff/market closed"
    assert status["accounts"]["live"]["open_count"] == 1
    assert status["last_verified_window_cutoff"] is None


def test_first_start_after_window_cannot_manufacture_success_from_flat_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "https://example.invalid/v3",
        },
    )
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())
    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1)),
    )

    assert status["state"] == "missed cutoff/market closed"
    assert status["accounts"]["live"]["open_count"] == 0
    assert "guarantee was missed" in status["accounts"]["live"]["last_error"]
    assert status["last_verified_window_cutoff"] is None


def test_post_window_flat_refetch_preserves_exact_previously_covered_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    config = {
        "account_id": "id",
        "api_key": "key",
        "base_url": "https://example.invalid/v3",
    }
    monkeypatch.setattr(
        fx, "resolve_account_config", lambda _mode: dict(config)
    )
    fx.update_status(
        last_verified_window_cutoff=cutoff.isoformat(),
        last_verified_window_scope_fingerprint=(
            fx._coverage_scope_fingerprint(settings)
        ),
        last_verified_window_account_times={
            "live": "2026-07-25T06:59:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": fx._account_scope_hash("live", config),
        },
    )
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())
    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1)),
    )

    assert status["state"] == "verified flat"
    assert status["accounts"]["live"]["open_count"] == 0
    assert status["last_verified_window_cutoff"] == cutoff.isoformat()


def test_stale_early_flat_check_does_not_cover_the_completed_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    config = {
        "account_id": "id",
        "api_key": "key",
        "base_url": "https://example.invalid/v3",
    }
    monkeypatch.setattr(
        fx, "resolve_account_config", lambda _mode: dict(config)
    )
    fx.update_status(
        last_verified_window_cutoff=cutoff.isoformat(),
        last_verified_window_scope_fingerprint=(
            fx._coverage_scope_fingerprint(settings)
        ),
        last_verified_window_account_times={
            "live": "2026-07-25T05:01:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": fx._account_scope_hash("live", config),
        },
    )
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())

    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1)),
    )

    assert status["state"] == "missed cutoff/market closed"
    assert status["accounts"]["live"]["open_count"] == 0
    assert status["last_verified_window_cutoff"] is None


def test_changed_oanda_account_identity_invalidates_window_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = fx.migrate_settings({"account_modes": ["live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    old_config = {
        "account_id": "old-live-id",
        "api_key": "old-key",
        "base_url": "https://api-fxtrade.oanda.com/v3",
    }
    new_config = {
        "account_id": "new-live-id",
        "api_key": "new-key",
        "base_url": "https://api-fxtrade.oanda.com/v3",
    }
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    fx.update_status(
        last_verified_window_cutoff=cutoff.isoformat(),
        last_verified_window_scope_fingerprint=(
            fx._coverage_scope_fingerprint(settings)
        ),
        last_verified_window_account_times={
            "live": "2026-07-25T06:59:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": fx._account_scope_hash("live", old_config),
        },
    )
    monkeypatch.setattr(
        fx, "resolve_account_config", lambda _mode: dict(new_config)
    )
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())

    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1)),
    )

    assert status["state"] == "missed cutoff/market closed"
    assert status["accounts"]["live"]["open_count"] == 0
    assert status["last_verified_window_cutoff"] is None


def test_changed_account_scope_invalidates_post_window_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_settings = fx.migrate_settings({"account_modes": ["live"]})
    settings = fx.migrate_settings({"account_modes": ["demo", "live"]})
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 5, 0))
    market_close = fx.BRISBANE_TZ.localize(
        datetime(2026, 7, 25, 7, 0)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {
            "phase": "missed",
            "cutoff": cutoff,
            "market_close": market_close,
        },
    )
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    fx.update_status(
        last_verified_window_cutoff=cutoff.isoformat(),
        last_verified_window_scope_fingerprint=(
            fx._coverage_scope_fingerprint(old_settings)
        ),
        last_verified_window_account_times={
            "live": "2026-07-25T06:59:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": "old-account-scope",
        },
    )
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda mode: {
            "mode": mode,
            "account_id": f"{mode}-id",
            "api_key": f"{mode}-key",
            "base_url": "https://example.invalid/v3",
        },
    )
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: _flat())

    status = fx.scheduler_iteration(
        settings,
        fx.BRISBANE_TZ.localize(datetime(2026, 7, 25, 7, 1)),
    )

    assert status["state"] == "missed cutoff/market closed"
    assert set(status["accounts"]) == {"demo", "live"}
    assert status["last_verified_window_cutoff"] is None


def test_config_update_clears_prior_window_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    status_path = tmp_path / "status.json"
    settings_path.write_text(
        json.dumps(fx.migrate_settings({"account_modes": ["live"]})),
        encoding="utf-8",
    )
    monkeypatch.setattr(fx, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(fx, "STATUS_PATH", status_path)
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    fx.update_status(
        last_verified_window_cutoff="2026-07-25T05:00:00+10:00",
        last_verified_window_scope_fingerprint="old-scope",
        last_verified_window_account_times={
            "live": "2026-07-25T06:59:00+10:00",
        },
        last_verified_window_account_scope_hashes={
            "live": "old-account-scope",
        },
    )

    response = fx.app.test_client().post(
        "/api/config",
        json={"account_modes": ["demo", "live"]},
    )

    assert response.status_code == 200
    saved_status = fx.status_snapshot()
    assert saved_status["last_verified_window_cutoff"] is None
    assert saved_status["last_verified_window_scope_fingerprint"] is None
    assert saved_status["last_verified_window_account_times"] == {}
    assert saved_status["last_verified_window_account_scope_hashes"] == {}


def test_disabled_manual_run_does_not_call_oanda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "load_settings",
        lambda: fx.migrate_settings({"enabled": False, "account_modes": ["live"]}),
    )
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("disabled Run now must not call OANDA")
        ),
    )
    response = fx.app.test_client().post("/api/run_now")
    assert response.status_code == 200
    assert response.get_json()["state"] == "disabled"
    assert response.get_json()["ok"] is False


def test_live_manual_run_is_blocked_outside_closure_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx,
        "load_settings",
        lambda: fx.migrate_settings(
            {"enabled": True, "account_modes": ["live"]}
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_a, **_k: {"phase": "before cutoff"},
    )
    monkeypatch.setattr(fx, "_atomic_json_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Live Run now must be blocked outside the closure window")
        ),
    )
    response = fx.app.test_client().post("/api/run_now")
    assert response.status_code == 409
    assert response.get_json()["ok"] is False
    assert "blocked outside" in response.get_json()["error"]


def test_status_survives_restart_through_status_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(fx, "STATUS_PATH", status_path)
    fx.update_status(
        state="verified flat",
        last_verified_flat_at="2026-07-25T05:10:00+10:00",
        accounts={"live": {"state": "verified flat", "open_count": 0}},
    )
    restored = fx._load_status()
    assert restored["state"] == "verified flat"
    assert restored["last_verified_flat_at"] == "2026-07-25T05:10:00+10:00"
    assert restored["running"] is False


def test_local_profile_never_autostarts_or_opens_local_fxweekend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setenv("AUTOSTART_SCRIPTS", "bybit_monitor,fxweekend-clone")
    monkeypatch.setenv("RENDER_FXWEEKEND_BASE_URL", "https://render.example.test")
    assert "fxweekend-clone" not in master_service._compute_autostart_scripts()
    button = next(
        item for item in master_service._profile_main_buttons() if item["name"] == "fxweekend"
    )
    assert button["label"] == "FX Weekend (Render)"
    assert button["open_url"] == "https://render.example.test/apps/fxweekend-clone"


def test_local_fxweekend_button_surfaces_missing_render_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.delenv("RENDER_FXWEEKEND_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_CALCULATOR_BASE_URL", raising=False)
    button = next(
        item for item in master_service._profile_main_buttons() if item["name"] == "fxweekend"
    )
    assert button["open_url"] == "/fx-weekend-render-configuration-error"


def test_render_environment_overrides_cannot_remove_fxweekend_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    class FakeScript:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "SCANNER_LOCAL_UI_MODE", False)
    monkeypatch.setattr(master_service, "RENDER_ALLOWED_APPS", set())
    monkeypatch.setenv(
        "AUTOSTART_SCRIPTS",
        "bybit_trigger_bounce_trader",
    )
    monkeypatch.setenv("AUTOSTART_EXCLUDE", "fxweekend-clone")
    monkeypatch.setattr(
        master_service.script_manager,
        "get",
        lambda name: FakeScript(name),
    )

    assert master_service._profile_allows_script("fxweekend-clone") is True
    assert "fxweekend-clone" in master_service._compute_autostart_scripts()
    button = next(
        item
        for item in master_service._profile_main_buttons()
        if item["name"] == "fxweekend"
    )
    assert button["label"] == "FX Weekend"


def test_render_supervisor_restarts_crashed_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    class FakeScript:
        is_running = False

        def __init__(self):
            self.starts = 0

        async def start(self):
            self.starts += 1

    fake = FakeScript()
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: fake)
    monkeypatch.setattr(master_service, "_persist_fxweekend_status_if_changed", lambda: None)
    monkeypatch.setattr(master_service, "_fxweekend_start_gate", lambda: (True, ""))
    master_service._SCANNER_SUPERVISOR_BACKOFF.clear()
    master_service._SCANNER_SUPERVISOR_BACKOFF_WINDOW.clear()
    asyncio.run(master_service._supervise_autostart_scripts_once(["fxweekend-clone"]))
    assert fake.starts == 1


def test_render_supervisor_recycles_running_child_with_stale_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    class FakeScript:
        def __init__(self):
            self.is_running = True
            self.starts = 0
            self.stops = 0

        async def stop(self):
            self.stops += 1
            self.is_running = False

        async def start(self):
            self.starts += 1
            self.is_running = True

    fake = FakeScript()
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(
        master_service.script_manager,
        "get",
        lambda _name: fake,
    )
    monkeypatch.setattr(
        master_service,
        "_persist_fxweekend_status_if_changed",
        lambda: None,
    )
    monkeypatch.setattr(
        master_service,
        "_fxweekend_start_gate",
        lambda: (True, ""),
    )
    monkeypatch.setattr(master_service, "_fxweekend_enabled", lambda: True)
    monkeypatch.setattr(
        master_service,
        "_fxweekend_heartbeat_fresh",
        lambda *_args, **_kwargs: False,
    )
    master_service._SCANNER_SUPERVISOR_BACKOFF.clear()
    master_service._SCANNER_SUPERVISOR_BACKOFF_WINDOW.clear()

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert fake.stops == 1
    assert fake.starts == 0

    asyncio.run(
        master_service._supervise_autostart_scripts_once(
            ["fxweekend-clone"]
        )
    )
    assert fake.stops == 1
    assert fake.starts == 1


def test_render_readiness_is_not_fake_when_enabled_executor_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from render import master_service

    class FakeScript:
        name = "fxweekend-clone"
        last_start_error = None
        last_exit_reason = "crashed"

        def to_summary(self):
            return {"starting": False, "running": False}

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"enabled": True, "account_modes": ["live"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "FXWEEKEND_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(master_service, "FXWEEKEND_STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: FakeScript())
    monkeypatch.setattr(
        master_service,
        "resolve_oanda_account_config",
        lambda _mode: {"account_id": "id", "api_key": "key", "base_url": "url"},
    )
    component = master_service._autostart_component_readiness("fxweekend-clone")
    assert component["ready"] is False
    assert component["blocking"] is True


def test_render_readiness_blocks_when_enabled_fxweekend_target_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {
            "enabled": True,
            "restore_complete": True,
            "restore_status": "done",
            "restore_error": "",
        },
    )
    monkeypatch.setattr(
        master_service,
        "_compute_autostart_scripts",
        lambda: [],
    )
    monkeypatch.setattr(
        master_service,
        "_LAST_AUTOSTART_UNAVAILABLE",
        {"fxweekend-clone": "executor script missing"},
    )
    monkeypatch.setattr(
        master_service,
        "_fxweekend_enabled",
        lambda: True,
    )

    payload = master_service._startup_readiness_status()

    assert payload["ready"] is False
    assert payload["blocking_component"] == "fxweekend-clone"
    assert payload["startup_phase"] == "background_executor_failed"
    assert "executor script missing" in payload["failure_reason"]


def test_render_readiness_blocks_before_cutoff_api_access_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from render import master_service

    class FakeScript:
        name = "fxweekend-clone"
        last_start_error = None
        last_exit_reason = None

        def to_summary(self):
            return {"starting": False, "running": True}

    settings_path = tmp_path / "settings.json"
    status_path = tmp_path / "status.json"
    settings_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "account_modes": ["live"],
                "check_interval_seconds": 60,
            }
        ),
        encoding="utf-8",
    )
    status_path.write_text(
        json.dumps(
            {
                "state": "API failure",
                "heartbeat_at": datetime.now(pytz.utc).isoformat(),
                "last_access_check_at": datetime.now(pytz.utc).isoformat(),
                "last_error": "live: openPositions GET failed with HTTP 401",
                "accounts": {
                    "live": {
                        "state": "API failure",
                        "open_count": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "FXWEEKEND_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(master_service, "FXWEEKEND_STATUS_PATH", status_path)
    monkeypatch.setattr(
        master_service.script_manager, "get", lambda _name: FakeScript()
    )
    monkeypatch.setattr(
        master_service,
        "resolve_oanda_account_config",
        lambda _mode: {
            "account_id": "id",
            "api_key": "key",
            "base_url": "url",
        },
    )
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {"fxweekend_state_indeterminate": False},
    )
    component = master_service._autostart_component_readiness(
        "fxweekend-clone"
    )
    assert component["ready"] is False
    assert component["blocking"] is True
    assert component["phase"] == "API_failure"
    assert "401" in component["reason"]


def test_dropbox_state_store_registers_fxweekend_durable_files() -> None:
    from render import dropbox_state_store

    assert dropbox_state_store.STATE_FILES["fxweekend_settings"] == (
        "fxweekend-clone/settings.json"
    )
    assert dropbox_state_store.STATE_FILES["fxweekend_status"] == (
        "fxweekend-clone/status.json"
    )


def test_fxweekend_config_failure_restores_remote_per_file_and_aggregate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from render import master_service

    settings_path = tmp_path / "settings.json"
    status_path = tmp_path / "status.json"
    previous_settings = {"enabled": False, "account_modes": ["demo"]}
    previous_status = {"state": "disabled", "accounts": {}}
    new_settings = {"enabled": True, "account_modes": ["live"]}
    new_status = {"state": "before cutoff", "accounts": {}}
    settings_path.write_text(json.dumps(new_settings), encoding="utf-8")
    status_path.write_text(json.dumps(new_status), encoding="utf-8")
    monkeypatch.setattr(
        master_service, "FXWEEKEND_SETTINGS_PATH", settings_path
    )
    monkeypatch.setattr(master_service, "FXWEEKEND_STATUS_PATH", status_path)
    remote = {}

    def persist(key, payload):
        remote[key] = json.loads(json.dumps(payload))

    aggregate_calls = []

    async def aggregate(*, timeout=10.0, **_kwargs):
        aggregate_calls.append(timeout)
        if len(aggregate_calls) == 1:
            raise master_service.HTTPException(
                status_code=502,
                detail={"error": "dropbox_upload_failed", "message": "temporary"},
            )
        return {"last_verified_at": "now"}

    monkeypatch.setattr(
        master_service, "_persist_fxweekend_state_key", persist
    )
    monkeypatch.setattr(
        master_service, "_upload_and_verify_state_backup_now", aggregate
    )
    result = asyncio.run(
        master_service._persist_fxweekend_config_with_rollback(
            previous_settings, previous_status, timeout=1.0
        )
    )
    assert result["ok"] is False
    assert result["durable_rollback_verified"] is True
    assert json.loads(settings_path.read_text(encoding="utf-8")) == previous_settings
    assert json.loads(status_path.read_text(encoding="utf-8")) == previous_status
    assert remote["fxweekend_settings"] == previous_settings
    assert remote["fxweekend_status"] == previous_status
    assert len(aggregate_calls) == 2


def test_failed_authoritative_restore_blocks_background_and_supervisor_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service

    class FakeScript:
        is_running = False
        startup_task = None
        last_start_error = None

        def __init__(self) -> None:
            self.starts = 0
            self.logs = []

        async def start(self, *args, **kwargs) -> None:
            self.starts += 1

        def add_log(self, message: str) -> None:
            self.logs.append(message)

    fake = FakeScript()
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {
            "enabled": True,
            "restore_complete": True,
            "restore_status": "failed",
            "restore_error": "Dropbox unavailable",
            "per_file_state_ready": False,
        },
    )
    master_service._STARTUP_STATE_RESTORE_DONE.set()

    asyncio.run(master_service._background_start_after_state_restore(fake))
    assert fake.starts == 0
    assert fake.last_start_error == "Dropbox unavailable"

    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: fake)
    asyncio.run(master_service._supervise_autostart_scripts_once(["fxweekend-clone"]))
    assert fake.starts == 0
    assert fake.last_start_error == "Dropbox unavailable"


def test_local_direct_fxweekend_routes_cannot_start_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from render import master_service
    from starlette.requests import Request

    class FakeScript:
        name = "fxweekend-clone"
        is_running = False
        is_starting = False
        startup_task = None
        port = None

    monkeypatch.setattr(master_service, "APP_PROFILE", "local")
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: FakeScript())

    with pytest.raises(master_service.HTTPException) as excinfo:
        asyncio.run(master_service.start_script("fxweekend-clone"))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "fxweekend_start_blocked"

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/apps/fxweekend-clone/",
            "raw_path": b"/apps/fxweekend-clone/",
            "query_string": b"",
            "headers": [(b"accept", b"text/html")],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }
    )
    response = asyncio.run(master_service.proxy_app("fxweekend-clone", request))
    assert response.status_code == 307
    assert response.headers["location"] == "/fx-weekend-render-configuration-error"
