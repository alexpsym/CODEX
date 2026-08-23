from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_executor():
    name = "fxweekend_liquidate_news_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "fxweekend-clone" / "liquidate.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_executor()
DEFAULT_VERIFIED_AT = "2026-08-24T10:00:00+10:00"


def _settings(releases=(), account_modes=("demo", "live"), **overrides):
    payload = {
        "schema_version": fx.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "account_modes": list(account_modes),
        "news_events": [
            {"release_date": date, "release_time": time}
            for date, time in releases
        ],
    }
    payload.update(overrides)
    return fx.migrate_settings(payload)


def _account(
    mode: str,
    state: str = "verified flat",
    *,
    verified_at: str | None = DEFAULT_VERIFIED_AT,
) -> dict:
    if state != "verified flat":
        verified_at = None
    return {
        "mode": mode,
        "state": state,
        "last_attempt_at": "2026-08-24T10:00:00+10:00",
        "last_verified_flat_at": verified_at,
        "position_count": 0 if state == "verified flat" else 1,
        "trade_count": 0 if state == "verified flat" else 1,
        "open_count": 0 if state == "verified flat" else 1,
        "account_scope_hash": f"scope-{mode}",
        "last_error": None if state == "verified flat" else "close failed",
        "requests": [],
        "closures": [],
    }


def _result(
    modes=("demo", "live"),
    *,
    verified: bool,
    state: str | None = None,
    verification_times: dict[str, str | None] | None = None,
    aggregate_verified_at: str | None = DEFAULT_VERIFIED_AT,
) -> dict:
    resolved_state = state or ("verified flat" if verified else "API failure")
    accounts = {
        mode: _account(
            mode,
            "verified flat" if verified else resolved_state,
            verified_at=(verification_times or {}).get(
                mode, DEFAULT_VERIFIED_AT
            ),
        )
        for mode in modes
    }
    return {
        "state": resolved_state,
        "result": resolved_state,
        "error": None if verified else "selected account close was not verified",
        "last_attempt_at": "2026-08-24T10:00:00+10:00",
        "last_verified_flat_at": aggregate_verified_at if verified else None,
        "accounts": accounts,
        "verified_flat": verified,
    }


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fx, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(fx, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(fx, "LOG_FILE", tmp_path / "trade_closure.log")
    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    monkeypatch.setattr(
        fx,
        "resolve_account_config",
        lambda mode: {
            "mode": mode,
            "account_id": f"{mode}-account",
            "api_key": f"{mode}-key",
            "base_url": f"https://{mode}.example.invalid/v3",
        },
    )
    fx._scheduler_wakeup.clear()
    yield
    fx._scheduler_wakeup.clear()


def test_schema_v1_migration_preserves_weekend_choices_and_adds_empty_news() -> None:
    from shared.oanda_api import upgrade_fxweekend_settings_schema

    original = {
        "schema_version": 1,
        "enabled": False,
        "trigger_weekday": 4,
        "cutoff_time_dst": "05:17",
        "cutoff_time_standard": "06:23",
        "account_modes": ["live"],
        "check_interval_seconds": 17,
        "max_retry_backoff_seconds": 91,
        "close_method": "trades",
        "dry_run": True,
        "instrument_allowlist": ["GBP_USD"],
    }

    upgraded, migrated = upgrade_fxweekend_settings_schema(original)

    assert migrated is True
    assert upgraded["schema_version"] == fx.FXWEEKEND_SETTINGS_SCHEMA_VERSION
    assert upgraded["news_events"] == []
    assert upgraded["account_modes"] == ["live"]
    for key, value in original.items():
        if key != "schema_version":
            assert upgraded[key] == value


def test_brisbane_release_parsing_cutoff_and_utc_conversion_are_exact() -> None:
    release = fx.parse_news_release("2026-08-24", "14:45")
    event = fx.migrate_settings(
        {"news_events": [{"release_date": "2026-08-24", "release_time": "14:45"}]}
    )["news_events"][0]
    normalized_release, cutoff = fx._news_event_times(event)

    assert release.tzinfo is not None
    assert release.isoformat() == "2026-08-24T14:45:00+10:00"
    assert release.astimezone(pytz.utc).isoformat() == "2026-08-24T04:45:00+00:00"
    assert normalized_release == release
    assert cutoff.isoformat() == "2026-08-24T14:30:00+10:00"
    with pytest.raises(ValueError, match="HH:MM"):
        fx.parse_news_release("2026-08-24", "2:45 PM")


def test_duplicate_events_normalize_once_with_stable_id_and_date_time_only_fields() -> None:
    payload = {
        "schema_version": fx.FXWEEKEND_SETTINGS_SCHEMA_VERSION,
        "account_modes": ["demo"],
        "news_events": [
            {
                "release_date": "2026-08-24",
                "release_time": "14:45",
                "symbol": "EUR_USD",
                "category": "highest-risk",
                "impact": "high",
            },
            {"release_date": "2026-08-24", "release_time": "14:45"},
        ],
    }

    first = fx.migrate_settings(payload)
    second = fx.migrate_settings(deepcopy(first))

    assert first == second
    assert len(first["news_events"]) == 1
    assert set(first["news_events"][0]) == {
        "id",
        "release_date",
        "release_time",
        "release_at",
    }
    assert first["news_events"][0]["id"].startswith("news_")


def test_news_add_duplicate_reload_and_delete_retains_audit_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = {
        "value": fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    }
    monkeypatch.setattr(fx, "_now_brisbane", lambda: now["value"])
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("saving an event must not liquidate")
        ),
    )
    fx.save_settings(_settings(enabled=True))
    client = fx.app.test_client()

    first = client.post(
        "/api/news",
        json={
            "release_date": "2026-08-24",
            "release_time": "10:30",
            "symbol": "EUR_USD",
            "category": "highest-risk",
            "impact": "high",
        },
    )
    duplicate = client.post(
        "/api/news",
        json={"release_date": "2026-08-24", "release_time": "10:30"},
    )

    assert first.status_code == 200
    assert fx._scheduler_wakeup.is_set() is True
    assert first.get_json()["state"] == "scheduled"
    assert "did not itself liquidate" in first.get_json()["message"]
    assert duplicate.get_json()["duplicate"] is True
    reloaded = fx.load_settings()
    assert len(reloaded["news_events"]) == 1
    event = reloaded["news_events"][0]
    assert set(event) == {"id", "release_date", "release_time", "release_at"}

    deleted = client.delete(f"/api/news/{event['id']}")

    assert deleted.status_code == 200
    assert deleted.get_json()["audit_retained"] is True
    assert fx.load_settings()["news_events"] == []
    audit = fx.status_snapshot()["news_audit"][event["id"]]
    assert audit["deleted_at"]
    assert audit["release_at"] == "2026-08-24T10:30:00+10:00"

    readded = client.post(
        "/api/news",
        json={"release_date": "2026-08-24", "release_time": "10:30"},
    ).get_json()["event"]
    assert readded["id"] != event["id"]
    assert event["id"] in fx.status_snapshot()["news_audit"]

    now["value"] = fx.BRISBANE_TZ.localize(
        datetime(2026, 8, 24, 10, 15)
    )
    monkeypatch.setattr(
        fx, "run_liquidation", lambda *_args, **_kwargs: _result(verified=True)
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )
    executed = fx.scheduler_iteration(fx.load_settings(), now["value"])

    assert executed["news_audit"][readded["id"]]["verified_flat_at"]
    assert executed["news_audit"][event["id"]]["deleted_at"]


def test_settings_change_interrupts_scheduler_sleep_without_waiting_interval() -> None:
    fx._scheduler_wakeup.set()

    fx.wait_with_heartbeat(300, "scheduled check interval")

    status = fx.status_snapshot()
    assert fx._scheduler_wakeup.is_set() is False
    assert status["sleeping"] is False
    assert status["scheduled_delay_seconds"] == 0.0


def test_past_event_save_is_explicit_but_does_not_execute_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 12, 0))
    monkeypatch.setattr(fx, "_now_brisbane", lambda: now)
    calls = []
    monkeypatch.setattr(
        fx, "run_liquidation", lambda *_args, **_kwargs: calls.append(True)
    )
    fx.save_settings(_settings(enabled=True))

    response = fx.app.test_client().post(
        "/api/news",
        json={"release_date": "2026-08-24", "release_time": "11:00"},
    )

    assert response.status_code == 200
    assert "release and cutoff have passed" in response.get_json()["state"]
    assert "did not itself liquidate" in response.get_json()["message"]
    assert calls == []


def test_scheduler_coalesces_overlapping_events_and_ignores_weekend_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        [("2026-08-24", "10:15"), ("2026-08-24", "10:16")],
        account_modes=("demo", "live"),
        instrument_allowlist=["EUR_USD"],
        dry_run=True,
        close_method="trades",
    )
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 1))
    calls = []

    def run(captured_settings, reason, **kwargs):
        calls.append((deepcopy(captured_settings), reason, kwargs))
        return _result(verified=True)

    monkeypatch.setattr(fx, "run_liquidation", run)
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    status = fx.scheduler_iteration(settings, now)

    assert len(calls) == 1
    captured_settings, reason, kwargs = calls[0]
    assert reason == "scheduled news release"
    assert captured_settings["account_modes"] == ["demo", "live"]
    assert captured_settings["dry_run"] is True
    assert captured_settings["close_method"] == "trades"
    assert captured_settings["instrument_allowlist"] == []
    assert kwargs["can_close"] is True
    assert set(status["news_last_result"]["event_ids"]) == {
        item["id"] for item in settings["news_events"]
    }
    audit = status["news_audit"]
    first, second = settings["news_events"]
    assert audit[first["id"]]["cutoff_met"] is False
    assert audit[second["id"]]["cutoff_met"] is True
    assert all(audit[item["id"]]["account_outcomes"] for item in (first, second))


def test_verified_event_is_restart_idempotent_for_same_selected_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    event = settings["news_events"][0]
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 5))
    fingerprint = fx._news_scope_fingerprint(settings)
    fx.update_status(
        news_audit={
            event["id"]: {
                "event_id": event["id"],
                "release_at": event["release_at"],
                "liquidation_cutoff": "2026-08-24T10:00:00+10:00",
                "attempt_at": "2026-08-24T10:00:00+10:00",
                "scope_fingerprint": fingerprint,
                "account_outcomes": {"demo": {"state": "verified flat"}},
                "verified_flat_at": "2026-08-24T10:00:00+10:00",
                "cutoff_met": True,
                "state": "verified flat at news cutoff",
            }
        }
    )
    restored = fx._load_status()
    monkeypatch.setattr(fx, "STATUS", restored)
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified event was liquidated twice")
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )
    monkeypatch.setattr(
        fx,
        "run_read_only_account_check",
        lambda *_args, **_kwargs: {
            "state": "before cutoff",
            "checked_at": now.isoformat(),
            "error": None,
            "accounts": {"demo": {"state": "before cutoff"}},
        },
    )

    status = fx.scheduler_iteration(settings, now)

    assert status["news_audit"][event["id"]]["verified_flat_at"]


def test_failed_cutoff_retries_before_release_and_never_retroactively_meets_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    attempts = iter(
        (
            _result(("demo",), verified=False, state="partial closure failure"),
            _result(
                ("demo",),
                verified=True,
                verification_times={
                    "demo": "2026-08-24T10:01:00+10:00"
                },
                aggregate_verified_at="2026-08-24T10:01:00+10:00",
            ),
        )
    )
    monkeypatch.setattr(fx, "run_liquidation", lambda *_args, **_kwargs: next(attempts))
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    first = fx.scheduler_iteration(settings, cutoff)
    second = fx.scheduler_iteration(settings, cutoff + timedelta(minutes=1))
    event_id = settings["news_events"][0]["id"]

    assert first["news_audit"][event_id]["cutoff_met"] is False
    assert "retry pending" in first["news_audit"][event_id]["state"]
    assert second["news_audit"][event_id]["verified_flat_at"]
    assert second["news_audit"][event_id]["cutoff_met"] is False
    assert "cutoff missed" in second["news_audit"][event_id]["state"]
    assert second["news_last_result"]["verified_flat"] is True
    assert second["news_last_result"]["cutoff_met"] is False


def test_attempt_at_cutoff_with_late_actual_verification_misses_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    event_id = settings["news_events"][0]["id"]
    late_verified_at = "2026-08-24T10:00:05+10:00"
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: _result(
            ("demo",),
            verified=True,
            verification_times={"demo": late_verified_at},
            aggregate_verified_at=late_verified_at,
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    status = fx.scheduler_iteration(settings, cutoff)
    audit = status["news_audit"][event_id]

    assert audit["verified_flat_at"] == late_verified_at
    assert audit["cutoff_met"] is False
    assert "cutoff missed" in audit["state"]


def test_cutoff_met_is_strict_at_equality_and_missed_one_second_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    event_id = settings["news_events"][0]["id"]
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: _result(
            ("demo",),
            verified=True,
            verification_times={"demo": DEFAULT_VERIFIED_AT},
            aggregate_verified_at=DEFAULT_VERIFIED_AT,
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    exact = fx.scheduler_iteration(settings, cutoff)
    assert exact["news_audit"][event_id]["cutoff_met"] is True

    monkeypatch.setattr(fx, "STATUS", fx._empty_status())
    late = fx.scheduler_iteration(settings, cutoff + timedelta(seconds=1))
    assert late["news_audit"][event_id]["cutoff_met"] is False
    assert "cutoff missed" in late["news_audit"][event_id]["state"]


def test_one_late_account_misses_cutoff_for_the_full_selected_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        [("2026-08-24", "10:15")], account_modes=("demo", "live")
    )
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    event_id = settings["news_events"][0]["id"]
    live_verified_at = "2026-08-24T10:00:01+10:00"
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: _result(
            ("demo", "live"),
            verified=True,
            verification_times={
                "demo": DEFAULT_VERIFIED_AT,
                "live": live_verified_at,
            },
            aggregate_verified_at=live_verified_at,
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    status = fx.scheduler_iteration(settings, cutoff)
    audit = status["news_audit"][event_id]

    assert audit["verified_flat_at"] == live_verified_at
    assert audit["cutoff_met"] is False
    assert audit["account_outcomes"]["demo"]["verified_flat_at"] == DEFAULT_VERIFIED_AT
    assert audit["account_outcomes"]["live"]["verified_flat_at"] == live_verified_at


@pytest.mark.parametrize(
    "untrustworthy_timestamp",
    [None, "not-a-timestamp", "2026-08-24T10:00:00"],
)
def test_missing_invalid_or_naive_verification_evidence_never_meets_cutoff(
    monkeypatch: pytest.MonkeyPatch,
    untrustworthy_timestamp: str | None,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    event_id = settings["news_events"][0]["id"]
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: _result(
            ("demo",),
            verified=True,
            verification_times={"demo": untrustworthy_timestamp},
            # A populated aggregate must not replace selected-account evidence.
            aggregate_verified_at=DEFAULT_VERIFIED_AT,
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    status = fx.scheduler_iteration(settings, cutoff)
    audit = status["news_audit"][event_id]

    assert audit["verified_flat_at"] is None
    assert audit["cutoff_met"] is False
    assert "timezone-aware verification timestamp" in audit["last_error"]
    assert status["news_last_result"]["verified_flat_at"] is None
    assert status["news_last_result"]["cutoff_met"] is False


def test_post_release_failure_gets_one_immediate_attempt_and_no_fake_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings([("2026-08-24", "10:15")], account_modes=("live",))
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 16))
    calls = []

    def failed(*_args, **_kwargs):
        calls.append(True)
        return _result(("live",), verified=False, state="API failure")

    monkeypatch.setattr(fx, "run_liquidation", failed)
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )
    monkeypatch.setattr(
        fx,
        "run_read_only_account_check",
        lambda *_args, **_kwargs: {
            "state": "before cutoff",
            "checked_at": now.isoformat(),
            "error": None,
            "accounts": {"live": {"state": "before cutoff"}},
        },
    )

    first = fx.scheduler_iteration(settings, now)
    second = fx.scheduler_iteration(settings, now + timedelta(seconds=30))
    event_id = settings["news_events"][0]["id"]

    assert calls == [True]
    entry = first["news_audit"][event_id]
    assert entry["verified_flat_at"] is None
    assert entry["cutoff_met"] is False
    assert entry["post_release_attempted_at"] == now.isoformat()
    assert first["news_last_result"]["verified_flat"] is False
    assert second["news_audit"][event_id]["attempt_count"] == 1


def test_disabled_news_scheduler_never_calls_liquidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        [("2026-08-24", "10:15")], enabled=False, account_modes=("demo",)
    )
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 5))
    monkeypatch.setattr(
        fx,
        "run_liquidation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled news scheduler reached liquidation")
        ),
    )

    status = fx.scheduler_iteration(settings, now)

    assert status["state"] == "disabled"
    assert "disabled" in status["news_status"].lower()


def test_due_news_dry_run_sends_no_close_and_cannot_fake_flatness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        [("2026-08-24", "10:15")],
        enabled=True,
        dry_run=True,
        account_modes=("demo",),
    )
    cutoff = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    opened = {
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
    monkeypatch.setattr(fx, "_get_open_items", lambda _config: deepcopy(opened))
    monkeypatch.setattr(
        fx,
        "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry run sent a close request")
        ),
    )
    monkeypatch.setattr(
        fx,
        "closure_window",
        lambda *_args, **_kwargs: {"phase": "before cutoff"},
    )

    status = fx.scheduler_iteration(settings, cutoff)
    event_id = settings["news_events"][0]["id"]

    assert status["news_last_result"]["verified_flat"] is False
    assert status["news_audit"][event_id]["verified_flat_at"] is None
    assert (
        status["news_audit"][event_id]["account_outcomes"]["demo"]["state"]
        == "partial closure failure"
    )


def test_scheduler_delay_uses_earliest_news_or_weekend_deadline() -> None:
    healthy = {**fx._empty_status(), "state": "before cutoff"}
    monday = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 0))
    news_first = _settings(
        [("2026-08-24", "10:25")],
        check_interval_seconds=600,
        max_retry_backoff_seconds=600,
    )
    assert fx._scheduler_delay_seconds(news_first, healthy, monday) == 600.0

    saturday = fx.BRISBANE_TZ.localize(datetime(2026, 8, 29, 4, 59, 30))
    weekend_first = _settings(
        [("2026-09-01", "10:00")],
        check_interval_seconds=600,
        max_retry_backoff_seconds=600,
        cutoff_time_dst="05:00",
        cutoff_time_standard="05:00",
    )
    assert fx._scheduler_delay_seconds(weekend_first, healthy, saturday) == 30.0


def test_retry_backoff_keeps_headroom_before_news_release() -> None:
    settings = _settings(
        [("2026-08-24", "10:15")],
        check_interval_seconds=60,
        max_retry_backoff_seconds=300,
    )
    now = fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 10, 14, 20))
    status = {**fx._empty_status(), "state": "API failure", "consecutive_failures": 7}

    delay = fx._scheduler_delay_seconds(settings, status, now)

    assert delay == pytest.approx(10.0)
    assert 0 < delay < 40


def test_news_html_and_api_are_date_time_only_and_do_not_guess_blackout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = fx.PAGE_TEMPLATE

    assert '<h2>News</h2>' in source
    assert 'name="release_date"' in source
    assert 'name="release_time"' in source
    assert 'name="symbol"' not in source
    assert 'name="category"' not in source
    assert 'name="impact"' not in source
    assert "does not guess a category or enforce either entry blackout" in source
    assert "Australia/Brisbane" in source
    assert "OANDA" in source
    assert "Pepperstone/MT5" in source

    settings = _settings([("2026-08-24", "10:15")])
    event = settings["news_events"][0]
    assert not {"symbol", "category", "impact"}.intersection(event)
    fx.save_settings(settings)
    fx.update_status(
        news_audit={
            event["id"]: {
                "event_id": event["id"],
                "release_at": event["release_at"],
                "liquidation_cutoff": "2026-08-24T10:00:00+10:00",
                "state": "scheduled",
                "cutoff_met": None,
            }
        }
    )
    monkeypatch.setattr(
        fx,
        "_now_brisbane",
        lambda: fx.BRISBANE_TZ.localize(datetime(2026, 8, 24, 9, 0)),
    )

    page = fx.app.test_client().get("/")

    assert page.status_code == 200
    rendered = page.get_data(as_text=True)
    assert "News audit history" in rendered
    assert event["id"] in rendered


def test_news_template_uses_valid_utf8_audit_placeholders_and_progress_text() -> None:
    source = fx.PAGE_TEMPLATE

    assert 'audit.get("release_at") or "—"' in source
    assert 'audit.get("liquidation_cutoff") or "—"' in source
    assert 'output.textContent = "Saving news release…";' in source
    assert "â€”" not in source
    assert "â€¦" not in source


def test_render_defaults_status_signature_readiness_and_news_route_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from render import master_service

    assert master_service.FXWEEKEND_DEFAULT_SETTINGS["news_events"] == []
    base_status = {"state": "before cutoff", "news_audit": {}}
    changed_status = {
        **base_status,
        "news_audit": {"news_1": {"state": "verified flat at news cutoff"}},
    }
    assert master_service._fxweekend_status_signature(
        base_status
    ) != master_service._fxweekend_status_signature(changed_status)

    class FakeScript:
        name = "fxweekend-clone"
        last_start_error = None
        last_exit_reason = None

        def to_summary(self):
            return {
                "starting": False,
                "running": True,
                "pid": None,
                "executor_instance_id": None,
            }

    event = _settings([("2026-08-24", "10:15")], account_modes=("demo",))[
        "news_events"
    ][0]
    settings = _settings([("2026-08-24", "10:15")], account_modes=("demo",))
    heartbeat = datetime.now(pytz.utc).isoformat()
    runtime = {
        "state": "before cutoff",
        "heartbeat_at": heartbeat,
        "selected_accounts": ["demo"],
        "accounts": {"demo": {"state": "before cutoff", "open_count": 0}},
        "next_news_release": event["release_at"],
        "next_news_liquidation_cutoff": "2026-08-24T10:00:00+10:00",
        "news_status": "Selected OANDA accounts are verified flat.",
        "news_last_result": {
            "event_ids": [event["id"]],
            "state": "verified flat; one or more news cutoffs were missed",
            "verified_flat": True,
            "cutoff_met": False,
        },
    }
    settings_path = tmp_path / "render-settings.json"
    status_path = tmp_path / "render-status.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    status_path.write_text(json.dumps(runtime), encoding="utf-8")
    monkeypatch.setattr(master_service, "APP_PROFILE", "render")
    monkeypatch.setattr(master_service, "FXWEEKEND_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(master_service, "FXWEEKEND_STATUS_PATH", status_path)
    monkeypatch.setattr(master_service.script_manager, "get", lambda _name: FakeScript())
    monkeypatch.setattr(
        master_service,
        "resolve_oanda_account_config",
        lambda mode: {
            "mode": mode,
            "account_id": "demo-account",
            "api_key": "demo-key",
            "base_url": "https://demo.example.invalid/v3",
        },
    )
    monkeypatch.setattr(
        master_service,
        "_state_sync_status_snapshot",
        lambda: {"fxweekend_state_indeterminate": False},
    )

    component = master_service._autostart_component_readiness("fxweekend-clone")

    assert component["ready"] is True
    assert component["phase"] == "operational_warning"
    assert component["health_state"] == "amber"
    assert component["next_news_release"] == event["release_at"]
    assert "cutoffs were missed" in component["health_reason"]

    import inspect

    proxy_source = inspect.getsource(master_service.proxy_app)
    assert 'path.strip("/") == "api/news"' in proxy_source
    assert 'path.strip("/").startswith("api/news/")' in proxy_source
    assert '"DELETE"' in proxy_source
