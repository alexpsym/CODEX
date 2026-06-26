import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPREAD_DIR = ROOT / "spreads-clone"
sys.path.insert(0, str(SPREAD_DIR))

from pepperstone_import import (  # noqa: E402
    PepperstoneImportError,
    PepperstoneSpreadImportStore,
    normalized_cache_from_export,
)


def _mt5_payload(symbol: str = "EURUSD", bid: float = 1.1, ask: float = 1.1002) -> dict:
    return {
        "version": 1,
        "broker": "pepperstone",
        "generated_at": "2026-01-01T00:00:00Z",
        "account": {"server": "Pepperstone-Demo", "company": "Pepperstone", "login": 12345},
        "symbols": [
            {
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "spread_pct": 999.0,
                "digits": 5,
                "point": 0.00001,
                "timestamp": "2026-01-01T00:00:00Z",
            }
        ],
    }


def _repo_cache_path(name: str) -> Path:
    path = ROOT / f".pytest_tmp_pepperstone_import_{name}.json"
    if path.exists():
        path.unlink()
    return path


def test_pepperstone_import_parses_valid_mt5_json_and_normalizes_symbol():
    cache_path = _repo_cache_path("valid")
    try:
        store = PepperstoneSpreadImportStore(cache_path)
        payload = store.import_text(json.dumps(_mt5_payload()), source_path="pepperstone_spreads_latest.json")
    finally:
        if cache_path.exists():
            cache_path.unlink()

    assert payload["broker"] == "pepperstone"
    assert payload["manual_import_only"] is True
    assert payload["source_filename"] == "pepperstone_spreads_latest.json"
    row = payload["rows"][0]
    assert row["symbol"] == "EUR_USD"
    cell = row["cells"]["1M"]["pepperstone_razor"]
    assert cell["spread_pct"] == pytest.approx(((1.1002 - 1.1) / ((1.1002 + 1.1) / 2.0)) * 100)


def test_pepperstone_import_uses_bid_ask_formula_not_supplied_spread_pct():
    cache = normalized_cache_from_export(_mt5_payload(bid=1.0, ask=1.1), source_path="x.json")
    record = cache["records"]["pepperstone|EUR_USD|1M"]
    assert record["latest"]["spread_pct"] == pytest.approx((0.1 / 1.05) * 100)


def test_pepperstone_import_rejects_malformed_file_without_wiping_previous_cache():
    cache_path = _repo_cache_path("preserve")
    try:
        store = PepperstoneSpreadImportStore(cache_path)
        first = store.import_text(json.dumps(_mt5_payload()), source_path="good.json")

        with pytest.raises(PepperstoneImportError):
            store.import_text("{not json", source_path="bad.json")

        after = store.status()
    finally:
        if cache_path.exists():
            cache_path.unlink()
    assert after["rows"] == first["rows"]
    assert after["last_imported_at"] == first["last_imported_at"]
    assert after["source_filename"] == "good.json"


def test_pepperstone_import_accepts_market_watch_crypto_symbols():
    cache = normalized_cache_from_export(_mt5_payload(symbol="BTCUSD.a"), source_path="crypto.json")
    assert cache["symbols"] == ["BTCUSD.a"]


def test_pepperstone_import_keeps_unavailable_market_watch_rows_visible():
    cache_path = _repo_cache_path("unavailable")
    payload = _mt5_payload(symbol="AUDCAD.a", bid=1.0, ask=1.0003)
    payload["symbols"].append(
        {
            "symbol": "BTCUSD.a",
            "mt5_symbol": "BTCUSD.a",
            "available": False,
            "error": "bid/ask unavailable",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    try:
        store = PepperstoneSpreadImportStore(cache_path)
        status = store.import_text(json.dumps(payload), source_path="pepperstone_spreads_latest.json")
    finally:
        if cache_path.exists():
            cache_path.unlink()

    rows = {row["symbol"]: row for row in status["rows"]}
    assert set(rows) == {"AUDCAD.a", "BTCUSD.a"}
    unavailable = rows["BTCUSD.a"]["cells"]["1M"]["pepperstone_razor"]
    assert unavailable["category"] == "unavailable"
    assert unavailable["spread_pct"] is None
    assert unavailable["error"] == "bid/ask unavailable"


def test_pepperstone_import_keeps_rounded_zero_spread_rows_visible():
    cache_path = _repo_cache_path("rounded_zero")
    payload = _mt5_payload(symbol="EURUSD.a", bid=1.1, ask=1.1)
    payload["symbols"][0]["available"] = True
    payload["symbols"][0]["spread_points"] = 0
    payload["symbols"][0]["spread_note"] = "rounded_to_zero_at_mt5_precision"
    try:
        store = PepperstoneSpreadImportStore(cache_path)
        status = store.import_text(json.dumps(payload), source_path="pepperstone_spreads_latest.json")
    finally:
        if cache_path.exists():
            cache_path.unlink()

    rows = {row["symbol"]: row for row in status["rows"]}
    assert set(rows) == {"EURUSD.a"}
    cell = rows["EURUSD.a"]["cells"]["1M"]["pepperstone_razor"]
    assert cell["category"] == "unavailable"
    assert cell["spread_pct"] is None
    assert cell["error"] == "Spread data unavailable."


def test_pepperstone_import_default_uses_mt5_fallback_when_repo_file_missing():
    cache_path = _repo_cache_path("fallback_cache")
    repo_source = ROOT / ".pytest_tmp_missing_pepperstone_spreads_latest.json"
    fallback_dir = ROOT / ".pytest_tmp_pepperstone_mt5_files"
    fallback_source = fallback_dir / "pepperstone_spreads_latest.json"
    if repo_source.exists():
        repo_source.unlink()
    if fallback_source.exists():
        fallback_source.unlink()
    fallback_dir.mkdir(exist_ok=True)
    try:
        fallback_source.write_text(json.dumps(_mt5_payload()), encoding="utf-8")
        store = PepperstoneSpreadImportStore(
            cache_path,
            default_source_path=repo_source,
            fallback_source_path=fallback_source,
        )
        payload = store.import_default_file()
    finally:
        if cache_path.exists():
            cache_path.unlink()
        if fallback_source.exists():
            fallback_source.unlink()
        if fallback_dir.exists():
            fallback_dir.rmdir()

    assert payload["broker"] == "pepperstone"
    assert payload["source_filename"] == "pepperstone_spreads_latest.json"
    assert payload["rows"][0]["symbol"] == "EUR_USD"


def test_pepperstone_import_does_not_use_metatrader5_python_module():
    source = (SPREAD_DIR / "pepperstone_import.py").read_text(encoding="utf-8")
    assert "MetaTrader5" not in source
    assert "mt5_spreads" not in source
