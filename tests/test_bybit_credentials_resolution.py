import bybit_credentials


def test_resolve_bybit_credentials_for_prefers_key2_for_demo_and_key1_for_live(monkeypatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY1", "live_key")
    monkeypatch.setenv("BYBIT_API_SECRET1", "live_secret")
    monkeypatch.setenv("BYBIT_API_KEY2", "demo_key")
    monkeypatch.setenv("BYBIT_API_SECRET2", "demo_secret")
    monkeypatch.setenv("BYBIT_API_KEY", "legacy_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "legacy_secret")

    demo = bybit_credentials.resolve_bybit_credentials_for("demo")
    live = bybit_credentials.resolve_bybit_credentials_for("live")

    assert demo[1] == "demo_key" and demo[2] == "demo_secret"
    assert "api-demo.bybit.com" in demo[3]
    assert live[1] == "live_key" and live[2] == "live_secret"
    assert "api.bybit.com" in live[3]
