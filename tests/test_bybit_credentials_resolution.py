import pytest

import bybit_credentials


_BYBIT_ENV_NAMES = (
    "BYBIT_ENV",
    "BYBIT_API_KEY",
    "BYBIT_API_SECRET",
    "BYBIT_API_KEY1",
    "BYBIT_API_SECRET1",
    "BYBIT_API_KEY2",
    "BYBIT_API_SECRET2",
    "BYBIT_DEMO_API_KEY",
    "BYBIT_DEMO_API_SECRET",
    "BYBIT_BASE_URL",
    "BYBIT_API_BASE",
    "BYBIT_BASE_URL_LIVE",
    "BYBIT_BASE_URL_DEMO",
    "BYBIT_API_BASE_DEMO",
    "BYBIT_BASE_URL_TESTNET",
    "BYBIT_API_BASE_TESTNET",
)


@pytest.fixture(autouse=True)
def _clear_bybit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _BYBIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("key_name", "secret_name", "expected_source"),
    (
        ("BYBIT_API_KEY2", "BYBIT_API_SECRET2", "KEY2"),
        ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET", "DEMO_EXPLICIT"),
        ("BYBIT_API_KEY", "BYBIT_API_SECRET", "LEGACY"),
    ),
)
def test_demo_supports_each_complete_credential_pair_in_isolation(
    monkeypatch: pytest.MonkeyPatch,
    key_name: str,
    secret_name: str,
    expected_source: str,
) -> None:
    monkeypatch.setenv(key_name, "example-key")
    monkeypatch.setenv(secret_name, "example-secret")

    mode, key, secret, base_url, source = bybit_credentials.resolve_bybit_credentials_for(
        "demo"
    )

    assert (mode, key, secret, source) == (
        "demo",
        "example-key",
        "example-secret",
        expected_source,
    )
    assert base_url == "https://api-demo.bybit.com"


def test_demo_prefers_key2_when_duplicate_complete_pairs_are_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key_name in ("BYBIT_API_KEY2", "BYBIT_DEMO_API_KEY", "BYBIT_API_KEY"):
        monkeypatch.setenv(key_name, "same-key")
    for secret_name in (
        "BYBIT_API_SECRET2",
        "BYBIT_DEMO_API_SECRET",
        "BYBIT_API_SECRET",
    ):
        monkeypatch.setenv(secret_name, "same-secret")

    resolved = bybit_credentials.resolve_bybit_credentials_for("demo")

    assert resolved[1:3] == ("same-key", "same-secret")
    assert resolved[4] == "KEY2"


def test_demo_rejects_conflicting_complete_pairs_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY2", "new-key-sensitive")
    monkeypatch.setenv("BYBIT_API_SECRET2", "new-secret-sensitive")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "old-key-sensitive")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "old-secret-sensitive")

    with pytest.raises(bybit_credentials.BybitCredentialConflictError) as exc_info:
        bybit_credentials.resolve_bybit_credentials_for("demo")

    message = str(exc_info.value)
    assert "KEY2" in message
    assert "DEMO_EXPLICIT" in message
    assert "BYBIT_API_KEY2/BYBIT_API_SECRET2" in message
    assert "new-key-sensitive" not in message
    assert "new-secret-sensitive" not in message
    assert "old-key-sensitive" not in message
    assert "old-secret-sensitive" not in message

    diagnostic = bybit_credentials.describe_bybit_credentials_for("demo")
    assert diagnostic["credential_conflict"] is True
    assert diagnostic["credentials_available"] is False
    assert diagnostic["key_length"] == 0
    assert diagnostic["conflict_sources"] == ["KEY2", "DEMO_EXPLICIT"]


def test_partial_sources_are_never_mixed_into_one_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY2", "key2-only")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "explicit-secret-only")

    resolved = bybit_credentials.resolve_bybit_credentials_for("demo")

    assert resolved[1] == "key2-only"
    assert resolved[2] == ""
    assert resolved[4] == "KEY2"


def test_live_prefers_key1_and_coerces_a_wrong_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY1", "live-key")
    monkeypatch.setenv("BYBIT_API_SECRET1", "live-secret")
    monkeypatch.setenv("BYBIT_API_KEY", "legacy-key")
    monkeypatch.setenv("BYBIT_API_SECRET", "legacy-secret")
    monkeypatch.setenv("BYBIT_BASE_URL_LIVE", "https://api-testnet.bybit.com")

    resolved = bybit_credentials.resolve_bybit_credentials_for("live")

    assert resolved[1:3] == ("live-key", "live-secret")
    assert resolved[3] == "https://api.bybit.com"
    assert resolved[4] == "KEY1"


def test_demo_key2_coerces_live_or_testnet_domain_to_official_demo_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY2", "demo-key")
    monkeypatch.setenv("BYBIT_API_SECRET2", "demo-secret")
    monkeypatch.setenv("BYBIT_BASE_URL_DEMO", "https://api-testnet.bybit.com")

    resolved = bybit_credentials.resolve_bybit_credentials_for("demo")

    assert resolved[0] == "demo"
    assert resolved[1:3] == ("demo-key", "demo-secret")
    assert resolved[3] == "https://api-demo.bybit.com"
    assert resolved[4] == "KEY2"


@pytest.mark.parametrize(
    ("mode", "env_name", "lookalike", "expected"),
    (
        (
            "demo",
            "BYBIT_BASE_URL_DEMO",
            "https://api-demo.bybit.com.example.invalid",
            "https://api-demo.bybit.com",
        ),
        (
            "testnet",
            "BYBIT_BASE_URL_TESTNET",
            "https://api-testnet.bybit.com@example.invalid",
            "https://api-testnet.bybit.com",
        ),
        (
            "live",
            "BYBIT_BASE_URL_LIVE",
            "https://api.bybit.com.example.invalid",
            "https://api.bybit.com",
        ),
    ),
)
def test_bybit_base_url_never_propagates_lookalike_hosts(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    env_name: str,
    lookalike: str,
    expected: str,
) -> None:
    monkeypatch.setenv(env_name, lookalike)

    resolved = bybit_credentials.resolve_bybit_credentials_for(mode)

    assert resolved[3] == expected


@pytest.mark.parametrize("mode", ("testnet", "paper"))
def test_testnet_modes_use_key2_and_never_use_demo_explicit_pair(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY2", "testnet-key")
    monkeypatch.setenv("BYBIT_API_SECRET2", "testnet-secret")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("BYBIT_BASE_URL_TESTNET", "https://api-demo.bybit.com")

    resolved = bybit_credentials.resolve_bybit_credentials_for(mode)

    assert resolved[0] == "testnet"
    assert resolved[1:3] == ("testnet-key", "testnet-secret")
    assert resolved[3] == "https://api-testnet.bybit.com"
    assert resolved[4] == "KEY2"
