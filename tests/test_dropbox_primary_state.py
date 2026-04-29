import json
from render import dropbox_state_store as store


def test_download_json_missing_optional_returns_default(monkeypatch):
    monkeypatch.setattr(store, "download_bytes", lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")))
    assert store.download_json("watchlist", default=["BTC"], required=False) == ["BTC"]


def test_download_json_missing_required_raises(monkeypatch):
    monkeypatch.setattr(store, "download_bytes", lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")))
    try:
        store.download_json("watchlist", required=True)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as exc:
        assert "watchlist" in str(exc)


def test_download_json_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(store, "download_bytes", lambda _path: b"{bad")
    try:
        store.download_json("watchlist", required=True)
        assert False, "expected ValueError"
    except ValueError:
        assert True
