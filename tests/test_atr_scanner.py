import asyncio
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from render.atr_scanner import (
    ATRScannerService,
    BYBIT_INTERVAL_BY_KEY,
    DEFAULT_SETTINGS,
    ScannerValidationError,
    TIMEFRAME_KEYS,
    calculate_orderbook_depth,
    calculate_spread_percent,
    format_atr_percent,
    interval_end_ms,
    latest_closed_boundary_ms,
    normalize_manual_exclusions,
    rank_rows,
    validate_settings,
    wilder_atr_percent,
)


def _raw_bar(start_ms: int, open_: float, high: float, low: float, close: float):
    return [str(start_ms), str(open_), str(high), str(low), str(close), "1", "1"]


DEFAULT_NOW_MS = 1_787_558_550_000


def _previous_start(boundary_ms: int, timeframe: str) -> int:
    if timeframe == "1m":
        return boundary_ms - 60_000
    if timeframe == "5m":
        return boundary_ms - 5 * 60_000
    if timeframe == "1h":
        return boundary_ms - 60 * 60_000
    if timeframe == "1D":
        return boundary_ms - 24 * 60 * 60_000
    if timeframe == "1W":
        return boundary_ms - 7 * 24 * 60 * 60_000
    if timeframe == "1Mo":
        boundary = datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc)
        if boundary.month == 1:
            previous = boundary.replace(year=boundary.year - 1, month=12)
        else:
            previous = boundary.replace(month=boundary.month - 1)
        return int(previous.timestamp() * 1000)
    raise AssertionError(timeframe)


def _closed_rows(
    timeframe: str, *, width: float, count: int = 200, server_time_ms: int = DEFAULT_NOW_MS
):
    boundary = latest_closed_boundary_ms(server_time_ms, timeframe)
    rows = []
    for _index in range(count):
        start = _previous_start(boundary, timeframe)
        close = 100.0
        rows.append(_raw_bar(start, close, close + width, close - width, close))
        boundary = start
    return rows


def test_wilder_atr_uses_true_range_gaps_reverse_order_and_last_closed_candle():
    minute = 60_000
    rows = [
        _raw_bar(4 * minute, 100, 110, 90, 105),  # forming and must be ignored
        _raw_bar(3 * minute, 13, 14, 12, 13),
        _raw_bar(2 * minute, 15, 16, 14, 15),
        _raw_bar(1 * minute, 10, 12, 9, 11),
    ]
    result = wilder_atr_percent(
        rows, length=3, timeframe="1m", server_time_ms=4 * minute + 30_000
    )
    assert result is not None
    # TRs are 3, 5 (gap from 11), and 3 (gap from 15); Wilder seed is 11/3.
    assert result["atr"] == pytest.approx(11 / 3)
    assert result["value"] == pytest.approx((11 / 3) / 13 * 100)
    assert result["candle_start_ms"] == 3 * minute
    assert result["closed_candle_count"] == 3


def test_wilder_atr_seed_and_recursion_match_fixed_fixture():
    rows = [
        _raw_bar(60_000, 10, 12, 9, 11),   # TR 3
        _raw_bar(120_000, 15, 16, 14, 15), # TR 5
        _raw_bar(180_000, 13, 14, 12, 13), # TR 3
        _raw_bar(240_000, 16, 18, 15, 17), # TR 5
    ]
    result = wilder_atr_percent(
        list(reversed(rows)),
        length=3,
        timeframe="1m",
        server_time_ms=300_001,
    )
    assert result is not None
    expected_atr = ((11 / 3) * 2 + 5) / 3
    assert result["atr"] == pytest.approx(expected_atr)
    assert result["value"] == pytest.approx(expected_atr / 17 * 100)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_raw_bar(1, 1, 2, 0.5, 1)],
        [["1", "bad", "2", "1", "1"]] * 20,
        [["1", "1", "nan", "1", "1"]] * 20,
        [["1", "1", "inf", "1", "1"]] * 20,
        [["1", "1", "2", "1", "0"]] * 20,
        None,
    ],
)
def test_wilder_atr_invalid_or_insufficient_history_is_unavailable(rows):
    assert wilder_atr_percent(
        rows, length=14, timeframe="1m", server_time_ms=10_000_000
    ) is None


def test_missing_internal_candle_or_stale_latest_closed_candle_is_unavailable():
    rows = _closed_rows("1m", width=1, count=20)
    with_gap = rows[:8] + rows[9:]
    assert wilder_atr_percent(
        with_gap, length=14, timeframe="1m", server_time_ms=DEFAULT_NOW_MS
    ) is None
    assert wilder_atr_percent(
        rows,
        length=14,
        timeframe="1m",
        server_time_ms=DEFAULT_NOW_MS + 2 * 60_000,
    ) is None


def test_timeframe_mapping_is_unambiguous_and_uses_bybit_intervals():
    assert TIMEFRAME_KEYS == ("1m", "5m", "1h", "1D", "1W", "1Mo")
    assert BYBIT_INTERVAL_BY_KEY == {
        "1m": "1",
        "5m": "5",
        "1h": "60",
        "1D": "D",
        "1W": "W",
        "1Mo": "M",
    }


def test_spread_uses_exact_bid_ask_midpoint_formula_and_fails_crossed_quotes():
    expected = (101 - 99) / ((101 + 99) / 2) * 100
    assert calculate_spread_percent("99", "101") == pytest.approx(expected)
    assert calculate_spread_percent("101", "99") is None
    assert calculate_spread_percent("0", "1") is None
    assert calculate_spread_percent("nan", "1") is None


def test_orderbook_depth_sums_quote_notional_inside_inclusive_band_per_side():
    payload = {
        "result": {
            "ts": 1_000_000,
            "b": [["100", "2"], ["99.91", "3"], ["99.9", "100"]],
            "a": [["100.01", "4"], ["100.1", "5"], ["100.11", "100"]],
        }
    }
    depth = calculate_orderbook_depth(
        payload,
        band_pct=0.1,
        server_time_ms=1_005_000,
        max_age_seconds=30,
    )
    assert depth is not None
    assert depth["best_bid"] == 100
    assert depth["best_ask"] == 100.01
    assert depth["midpoint"] == pytest.approx(100.005)
    assert depth["spread_pct"] == pytest.approx(
        calculate_spread_percent(100, 100.01)
    )
    assert depth["bid_depth_usdt"] == pytest.approx(100 * 2 + 99.91 * 3)
    assert depth["ask_depth_usdt"] == pytest.approx(100.01 * 4 + 100.1 * 5)
    assert depth["book_age_seconds"] == pytest.approx(5)


def test_moved_orderbook_uses_its_own_midpoint_spread_and_quote_depth():
    payload = {
        "result": {
            "ts": 1_000_000,
            "b": [["100.95", "500"]],
            "a": [["101.05", "500"]],
        }
    }
    snapshot = calculate_orderbook_depth(
        payload,
        band_pct=0.1,
        server_time_ms=1_000_000,
        max_age_seconds=30,
    )
    assert snapshot is not None
    assert snapshot["best_bid"] == pytest.approx(100.95)
    assert snapshot["best_ask"] == pytest.approx(101.05)
    assert snapshot["midpoint"] == pytest.approx(101.0)
    assert snapshot["spread_pct"] == pytest.approx((101.05 - 100.95) / 101 * 100)
    assert snapshot["bid_depth_usdt"] == pytest.approx(50_475)
    assert snapshot["ask_depth_usdt"] == pytest.approx(50_525)


def test_orderbook_depth_band_boundaries_are_inclusive():
    snapshot = calculate_orderbook_depth(
        {
            "result": {
                "ts": 1_000_000,
                "b": [["99.95", "1"], ["99.9", "2"], ["99.899", "100"]],
                "a": [["100.05", "1"], ["100.1", "2"], ["100.101", "100"]],
            }
        },
        band_pct=0.1,
        server_time_ms=1_000_000,
        max_age_seconds=30,
    )
    assert snapshot is not None
    assert snapshot["midpoint"] == pytest.approx(100)
    assert snapshot["bid_depth_usdt"] == pytest.approx(99.95 + 99.9 * 2)
    assert snapshot["ask_depth_usdt"] == pytest.approx(100.05 + 100.1 * 2)


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"ts": 100_000, "b": [["101", "1"]], "a": [["100", "1"]]}},
        {"result": {"b": [["99", "1"]], "a": [["101", "1"]]}},
        {"result": {"ts": 1, "b": [["99", "1"]], "a": [["101", "1"]]}},
        {"result": {"ts": 100_000, "b": [["bad", "1"]], "a": [["101", "1"]]}},
        {"result": {"ts": 100_000, "b": [], "a": [["101", "1"]]}},
        {"result": {"ts": 100_000, "b": [["nan", "1"]], "a": [["101", "1"]]}},
        {"result": {"ts": 100_000, "b": [["99", "1"]], "a": [["inf", "1"]]}},
        {"result": {"ts": 100_000, "b": [["0", "1"]], "a": [["101", "1"]]}},
        {"result": {"ts": 100_000, "b": [["99", "-1"]], "a": [["101", "1"]]}},
    ],
)
def test_missing_stale_crossed_or_malformed_orderbook_fails_closed(payload):
    assert calculate_orderbook_depth(
        payload,
        band_pct=0.1,
        server_time_ms=100_000,
        max_age_seconds=30,
    ) is None


def test_raw_numeric_ranking_and_symbol_ties_ignore_display_formatting():
    rows = [
        {"symbol": "ZUSDT", "atr_pct": {"1m": 0.0000049}},
        {"symbol": "BUSDT", "atr_pct": {"1m": 0.0000051}},
        {"symbol": "AUSDT", "atr_pct": {"1m": 0.0000051}},
        {"symbol": "N/A", "atr_pct": {"1m": None}},
    ]
    assert format_atr_percent(rows[0]["atr_pct"]["1m"]) == "0.00000%"
    assert [row["symbol"] for row in rank_rows(rows, "1m")] == [
        "AUSDT",
        "BUSDT",
        "ZUSDT",
    ]


def test_settings_validate_bounds_and_persist_normalized_manual_exclusions(tmp_path: Path):
    path = tmp_path / "settings.json"

    async def unused_fetch(_path, _params):
        raise AssertionError("network should not be used")

    service = ATRScannerService(fetch_json=unused_fetch, settings_path=path)
    saved = service.save_settings(
        {"manual_exclusions": " btcusdt, ETHUSDT\nbtcusdt ", "top_n": 12}
    )
    assert saved["manual_exclusions"] == ["BTCUSDT", "ETHUSDT"]
    assert service.load_settings() == saved
    assert service.reset_settings() == validate_settings(DEFAULT_SETTINGS)
    assert normalize_manual_exclusions(["  btcusdt ", "BTCUSDT"]) == ["BTCUSDT"]

    for field, value in (
        ("top_n", 0),
        ("atr_length", 1),
        ("min_turnover_usdt", -1),
        ("max_spread_pct", float("nan")),
        ("depth_band_pct", float("inf")),
        ("top_n", True),
        ("rank_timeframe", "1M"),
        ("manual_exclusions", "BTC/USDT"),
    ):
        with pytest.raises(ScannerValidationError, match=field):
            validate_settings({field: value})
    with pytest.raises(ScannerValidationError, match="at most 1000 symbols"):
        validate_settings(
            {"manual_exclusions": [f"S{index:04d}USDT" for index in range(1001)]}
        )
    with pytest.raises(ScannerValidationError, match="at most 50000 characters"):
        validate_settings({"manual_exclusions": "A" * 50_001})


class FakeBybit:
    def __init__(self, now_ref, *, slow_gate=None):
        self.now_ref = now_ref
        self.calls = []
        self.fail_scope = None
        self.slow_gate = slow_gate
        self.fail_kline_interval = None
        self.ticker_quotes = {}
        self.orderbooks = {}

    def ticker_row(self, symbol, *, turnover):
        bid, ask = self.ticker_quotes.get(symbol, ("99.99", "100.01"))
        return _ticker(symbol, turnover=turnover, bid=bid, ask=ask)

    async def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if self.slow_gate is not None and path == "/v5/market/instruments-info":
            await self.slow_gate.wait()
        if self.fail_scope and self.fail_scope in path:
            raise RuntimeError(f"forced {self.fail_scope} failure")
        if path == "/v5/market/kline" and params.get("interval") == self.fail_kline_interval:
            raise RuntimeError(f"forced {self.fail_kline_interval} kline failure")
        if path == "/v5/market/instruments-info":
            return {
                "retCode": 0,
                "time": self.now_ref[0],
                "result": {
                    "list": [
                        _instrument("ALPHAUSDT"),
                        _instrument("BETAUSDT"),
                        _instrument("ILLIQUSDT"),
                        _instrument("OLDUSDT", status="PreLaunch"),
                        _instrument("FUTUREUSDT", contract_type="LinearFutures"),
                    ],
                    "nextPageCursor": "",
                },
            }
        if path == "/v5/market/tickers":
            return {
                "retCode": 0,
                "time": self.now_ref[0],
                "result": {
                    "list": [
                        self.ticker_row("ALPHAUSDT", turnover="50000000"),
                        self.ticker_row("BETAUSDT", turnover="50000000"),
                        self.ticker_row("ILLIQUSDT", turnover="100"),
                    ]
                },
            }
        if path == "/v5/market/orderbook":
            result = self.orderbooks.get(
                params["symbol"],
                {
                    "ts": self.now_ref[0],
                    "b": [["99.99", "1000"]],
                    "a": [["100.01", "1000"]],
                },
            )
            return {
                "retCode": 0,
                "time": self.now_ref[0],
                "result": result,
            }
        if path == "/v5/market/kline":
            symbol = params["symbol"]
            timeframe = next(
                key for key, interval in BYBIT_INTERVAL_BY_KEY.items() if interval == params["interval"]
            )
            width = 5 if symbol in {"ALPHAUSDT", "ILLIQUSDT"} else 1
            forming_start = latest_closed_boundary_ms(self.now_ref[0], timeframe)
            return {
                "retCode": 0,
                "time": self.now_ref[0],
                "result": {
                    "list": [
                        _raw_bar(forming_start, 100, 100 + width * 10, 100 - width * 10, 100)
                    ]
                    + _closed_rows(timeframe, width=width, server_time_ms=self.now_ref[0])
                },
            }
        if path == "/v5/market/time":
            return {"retCode": 0, "time": self.now_ref[0], "result": {}}
        raise AssertionError((path, params))


def _instrument(symbol, *, status="Trading", contract_type="LinearPerpetual"):
    return {
        "symbol": symbol,
        "status": status,
        "contractType": contract_type,
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "isPreListing": status == "PreLaunch",
    }


def _ticker(symbol, *, turnover, bid="99.99", ask="100.01"):
    return {
        "symbol": symbol,
        "turnover24h": turnover,
        "volume24h": "999999999999",  # must never be used for the turnover gate
        "bid1Price": bid,
        "ask1Price": ask,
    }


def _make_service(tmp_path, fake, now_ref):
    return ATRScannerService(
        fetch_json=fake,
        settings_path=tmp_path / "scanner-settings.json",
        now_ms=lambda: now_ref[0],
        request_spacing_seconds=0,
        request_retries=0,
    )


def test_full_service_uses_all_liquidity_gates_then_ranks_only_by_atr(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    fake = FakeBybit(now_ref)
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))

    assert result["ok"] is True
    assert result["ranking_basis"]["candle_basis"] == "last closed candle"
    assert [row["symbol"] for row in result["ranked_rows"]] == [
        "ALPHAUSDT",
        "BETAUSDT",
    ]
    assert result["ranked_rows"][0]["atr_pct"]["1m"] > result["ranked_rows"][1]["atr_pct"]["1m"]
    assert all(set(row["atr_pct"]) == set(TIMEFRAME_KEYS) for row in result["ranked_rows"])
    assert all(row["best_bid"] == 99.99 for row in result["ranked_rows"])
    assert all(row["best_ask"] == 100.01 for row in result["ranked_rows"])
    assert all(row["midpoint"] == 100 for row in result["ranked_rows"])
    assert all(
        row["spread_pct"] == pytest.approx(calculate_spread_percent(99.99, 100.01))
        for row in result["ranked_rows"]
    )
    excluded = {row["symbol"]: row["reasons"] for row in result["excluded_rows"]}
    assert "turnover_below_minimum" in excluded["ILLIQUSDT"]
    assert "inactive" in excluded["OLDUSDT"]
    assert "wrong_product" in excluded["FUTUREUSDT"]
    assert not any(
        path == "/v5/market/kline" and params.get("symbol") == "ILLIQUSDT"
        for path, params in fake.calls
    )
    assert Counter(path for path, _params in fake.calls)["/v5/market/tickers"] == 1
    assert all(
        params.get("limit") == 1000
        for path, params in fake.calls
        if path == "/v5/market/orderbook"
    )
    assert all(
        params.get("limit") == 201
        for path, params in fake.calls
        if path == "/v5/market/kline"
    )
    for path, params in fake.calls:
        assert path.startswith("/v5/market/")
        lowered_keys = {str(key).lower() for key in params}
        assert not lowered_keys.intersection({"api_key", "apikey", "signature", "sign", "secret"})


def test_service_qualifies_liquid_book_after_price_moves_from_ticker(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]
    fake = FakeBybit(now_ref)
    fake.orderbooks["ALPHAUSDT"] = {
        "ts": now_ref[0],
        "b": [["100.95", "500"]],
        "a": [["101.05", "500"]],
    }
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))

    assert [row["symbol"] for row in result["ranked_rows"]] == [
        "ALPHAUSDT",
        "BETAUSDT",
    ]
    alpha = next(row for row in result["ranked_rows"] if row["symbol"] == "ALPHAUSDT")
    assert alpha["best_bid"] == pytest.approx(100.95)
    assert alpha["best_ask"] == pytest.approx(101.05)
    assert alpha["midpoint"] == pytest.approx(101)
    assert alpha["spread_pct"] == pytest.approx((101.05 - 100.95) / 101 * 100)
    assert alpha["bid_depth_usdt"] == pytest.approx(50_475)
    assert alpha["ask_depth_usdt"] == pytest.approx(50_525)


def test_current_book_not_older_ticker_controls_final_spread_gate(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]
    fake = FakeBybit(now_ref)
    fake.ticker_quotes["ALPHAUSDT"] = ("99.8", "100.2")
    service = _make_service(tmp_path, fake, now_ref)
    qualified = asyncio.run(service.refresh(manual=True))
    assert {row["symbol"] for row in qualified["ranked_rows"]} == {
        "ALPHAUSDT",
        "BETAUSDT",
    }
    assert any(
        path == "/v5/market/orderbook" and params["symbol"] == "ALPHAUSDT"
        for path, params in fake.calls
    )

    fake.ticker_quotes["ALPHAUSDT"] = ("99.99", "100.01")
    fake.orderbooks["ALPHAUSDT"] = {
        "ts": now_ref[0],
        "b": [["100.94", "500"]],
        "a": [["101.06", "500"]],
    }
    excluded = asyncio.run(service.refresh(manual=True))
    assert "ALPHAUSDT" not in {
        row["symbol"] for row in excluded["ranked_rows"]
    }
    alpha = next(row for row in excluded["excluded_rows"] if row["symbol"] == "ALPHAUSDT")
    assert alpha["reasons"] == ["spread_above_maximum"]
    assert alpha["midpoint"] == pytest.approx(101)
    assert alpha["spread_pct"] == pytest.approx((101.06 - 100.94) / 101 * 100)
    assert alpha["bid_depth_usdt"] == pytest.approx(50_470)
    assert alpha["ask_depth_usdt"] == pytest.approx(50_530)


@pytest.mark.parametrize(
    "invalid_book",
    [
        {"ts": DEFAULT_NOW_MS, "b": [["101", "1"]], "a": [["100", "1"]]},
        {"ts": DEFAULT_NOW_MS, "b": [], "a": [["101", "1"]]},
        {"b": [["99", "1"]], "a": [["101", "1"]]},
        {"ts": DEFAULT_NOW_MS - 31_000, "b": [["99", "1"]], "a": [["101", "1"]]},
        {"ts": DEFAULT_NOW_MS, "b": [["bad", "1"]], "a": [["101", "1"]]},
        {"ts": DEFAULT_NOW_MS, "b": [["nan", "1"]], "a": [["101", "1"]]},
        {"ts": DEFAULT_NOW_MS, "b": [["0", "1"]], "a": [["101", "1"]]},
        {"ts": DEFAULT_NOW_MS, "b": [["99", "-1"]], "a": [["101", "1"]]},
    ],
)
def test_invalid_current_book_fails_closed_with_explicit_reason(
    tmp_path: Path, invalid_book
):
    now_ref = [DEFAULT_NOW_MS]
    fake = FakeBybit(now_ref)
    fake.orderbooks["ALPHAUSDT"] = invalid_book
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))

    assert result["ok"] is True
    assert result["state"] == "partial"
    assert [row["symbol"] for row in result["ranked_rows"]] == ["BETAUSDT"]
    alpha = next(row for row in result["excluded_rows"] if row["symbol"] == "ALPHAUSDT")
    assert alpha["reasons"] == ["missing_invalid_market_data"]
    assert {error["scope"] for error in result["errors"]} >= {
        "orderbook:ALPHAUSDT"
    }


def test_each_orderbook_uses_its_own_response_time_for_freshness(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]

    class DelayedBooks(FakeBybit):
        async def __call__(self, path, params):
            if path == "/v5/market/orderbook":
                self.now_ref[0] += 10_000
            return await super().__call__(path, params)

    fake = DelayedBooks(now_ref)
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is True
    assert [row["symbol"] for row in result["ranked_rows"]] == ["ALPHAUSDT", "BETAUSDT"]
    assert all(row["book_age_seconds"] == 0 for row in result["ranked_rows"])


def test_all_orderbook_failures_are_explicit_not_fresh_empty_success(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]
    fake = FakeBybit(now_ref)
    fake.fail_scope = "orderbook"
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is False
    assert result["state"] == "error"
    assert result["refresh_error"]["scope"] == "orderbooks"
    assert result["ranked_rows"] == []


def test_all_missing_tickers_are_explicit_not_fresh_empty_success(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]

    class MissingTickers(FakeBybit):
        async def __call__(self, path, params):
            if path == "/v5/market/tickers":
                self.calls.append((path, dict(params)))
                return {"retCode": 0, "time": self.now_ref[0], "result": {"list": []}}
            return await super().__call__(path, params)

    service = _make_service(tmp_path, MissingTickers(now_ref), now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is False
    assert result["state"] == "error"
    assert result["refresh_error"]["scope"] == "tickers"


def test_all_malformed_klines_are_explicit_not_partial_empty_success(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]

    class MalformedKlines(FakeBybit):
        async def __call__(self, path, params):
            if path == "/v5/market/kline":
                self.calls.append((path, dict(params)))
                return {
                    "retCode": 0,
                    "time": self.now_ref[0],
                    "result": {"list": [["malformed"]]},
                }
            return await super().__call__(path, params)

    service = _make_service(tmp_path, MalformedKlines(now_ref), now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is False
    assert result["state"] == "error"
    assert result["refresh_error"]["scope"] == "klines"


def test_partial_malformed_klines_keep_missing_invalid_reason(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]

    class PartlyMalformedKlines(FakeBybit):
        async def __call__(self, path, params):
            if path == "/v5/market/kline" and params.get("symbol") == "BETAUSDT":
                self.calls.append((path, dict(params)))
                return {
                    "retCode": 0,
                    "time": self.now_ref[0],
                    "result": {"list": [["malformed"]]},
                }
            return await super().__call__(path, params)

    service = _make_service(tmp_path, PartlyMalformedKlines(now_ref), now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is True
    assert result["state"] == "partial"
    assert [row["symbol"] for row in result["ranked_rows"]] == ["ALPHAUSDT"]
    beta = next(row for row in result["excluded_rows"] if row["symbol"] == "BETAUSDT")
    assert beta["reasons"] == ["missing_invalid_market_data"]
    assert beta["atr_reason"]["1m"] == "missing_invalid_market_data"


def test_nonselected_timeframe_failure_is_partial_and_row_remains_rerankable(tmp_path: Path):
    now_ref = [DEFAULT_NOW_MS]
    fake = FakeBybit(now_ref)
    fake.fail_kline_interval = "D"
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is True
    assert result["state"] == "partial"
    assert result["ranked_rows"]
    assert {row["symbol"] for row in result["qualified_rows"]} == {"ALPHAUSDT", "BETAUSDT"}
    assert all(row["atr_pct"]["1D"] is None for row in result["qualified_rows"])
    assert all(row["atr_status"]["1D"] == "error" for row in result["qualified_rows"])
    assert {error["scope"] for error in result["errors"]} == {
        "kline:ALPHAUSDT:1D",
        "kline:BETAUSDT:1D",
    }


def test_threshold_boundaries_pass_and_all_gates_use_and(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    fake = FakeBybit(now_ref)
    service = _make_service(tmp_path, fake, now_ref)
    service.save_settings(
        {
            "min_turnover_usdt": 50_000_000,
            "max_spread_pct": calculate_spread_percent(99.99, 100.01),
            "min_bid_depth_usdt": 99_990,
            "min_ask_depth_usdt": 100_010,
        }
    )
    result = asyncio.run(service.refresh(manual=True))
    assert [row["symbol"] for row in result["ranked_rows"]] == ["ALPHAUSDT", "BETAUSDT"]

    service.save_settings({"min_ask_depth_usdt": 100_010.01})
    result = asyncio.run(service.refresh(manual=True))
    assert result["ranked_rows"] == []
    assert result["excluded_reason_counts"]["ask_depth_below_minimum"] == 2


def test_instrument_pagination_covers_more_than_500_and_filters_products(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    calls = []

    async def fetch(path, params):
        calls.append((path, dict(params)))
        cursor = params.get("cursor")
        if not cursor:
            rows = [_instrument(f"S{index:03d}USDT") for index in range(500)]
            rows += [_instrument("INACTIVEUSDT", status="PreLaunch")]
            return {"retCode": 0, "result": {"list": rows, "nextPageCursor": "page2"}}
        assert cursor == "page2"
        return {
            "retCode": 0,
            "result": {
                "list": [
                    *[_instrument(f"T{index:03d}USDT") for index in range(5)],
                    _instrument("USDC-PERP", contract_type="LinearPerpetual") | {"quoteCoin": "USDC", "settleCoin": "USDC"},
                    _instrument("DATEDUSDT", contract_type="LinearFutures"),
                ],
                "nextPageCursor": "",
            },
        }

    service = ATRScannerService(
        fetch_json=fetch,
        settings_path=tmp_path / "settings.json",
        now_ms=lambda: now_ref[0],
        request_spacing_seconds=0,
        request_retries=0,
    )
    eligible, excluded = asyncio.run(service._fetch_instruments(manual=True))
    assert len(eligible) == 505
    assert [params.get("cursor") for _path, params in calls] == [None, "page2"]
    reasons = {row["symbol"]: row["reasons"] for row in excluded}
    assert reasons["INACTIVEUSDT"] == ["inactive"]
    assert reasons["USDC-PERP"] == ["wrong_product"]
    assert reasons["DATEDUSDT"] == ["wrong_product"]


def test_shared_inflight_refresh_and_manual_overlap_do_not_multiply_calls(tmp_path: Path):
    async def scenario():
        now_ref = [1_787_558_550_000]
        gate = asyncio.Event()
        fake = FakeBybit(now_ref, slow_gate=gate)
        service = _make_service(tmp_path, fake, now_ref)
        first = await service.start_refresh(manual=True)
        second = await service.start_refresh(manual=True)
        assert first["started"] is True
        assert second["shared_in_flight"] is True
        gate.set()
        await service.wait_for_idle()
        assert Counter(path for path, _params in fake.calls)["/v5/market/instruments-info"] == 1

    asyncio.run(scenario())


def test_settings_saved_during_refresh_queue_one_nonoverlapping_rebuild(tmp_path: Path):
    async def scenario():
        now_ref = [DEFAULT_NOW_MS]
        gate = asyncio.Event()
        fake = FakeBybit(now_ref, slow_gate=gate)
        service = _make_service(tmp_path, fake, now_ref)
        first = await service.start_refresh(manual=True)
        assert first["started"] is True
        await asyncio.sleep(0)
        service.save_settings({"min_turnover_usdt": 60_000_000})
        queued = await service.start_refresh(manual=True)
        assert queued["shared_in_flight"] is True
        assert queued["follow_up_queued"] is True
        gate.set()
        result = await service.wait_for_idle()
        assert result["settings"]["min_turnover_usdt"] == 60_000_000
        assert result["ranked_rows"] == []
        assert Counter(path for path, _params in fake.calls)["/v5/market/instruments-info"] == 2

    asyncio.run(scenario())


def test_higher_timeframe_atr_caches_are_not_refetched_each_minute(tmp_path: Path):
    async def scenario():
        now_ref = [1_787_558_550_000]  # deliberately between minute/5-minute boundaries
        fake = FakeBybit(now_ref)
        service = _make_service(tmp_path, fake, now_ref)
        await service.refresh(manual=False)
        first_counts = Counter(
            params["interval"] for path, params in fake.calls if path == "/v5/market/kline"
        )
        now_ref[0] += 61_000
        await service.refresh(manual=False)
        second_counts = Counter(
            params["interval"] for path, params in fake.calls if path == "/v5/market/kline"
        )
        assert second_counts["1"] == first_counts["1"] + 2
        for interval in ("5", "60", "D", "W", "M"):
            assert second_counts[interval] == first_counts[interval]

    asyncio.run(scenario())


def test_transient_failure_preserves_visible_stale_last_known_good(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    fake = FakeBybit(now_ref)
    service = _make_service(tmp_path, fake, now_ref)
    first = asyncio.run(service.refresh(manual=True))
    assert first["ranked_rows"]
    now_ref[0] += 120_000
    fake.fail_scope = "tickers"
    stale = asyncio.run(service.refresh(manual=True))
    assert stale["ok"] is True
    assert stale["state"] == "stale"
    assert stale["stale"] is True
    assert stale["ranked_rows"] == first["ranked_rows"]
    assert stale["refresh_error"]["scope"] == "tickers"
    assert stale["stale_age_seconds"] >= 120
    now_ref[0] += 30_000
    assert service.status_payload()["stale_age_seconds"] >= 150


def test_first_upstream_failure_is_explicit_not_blank_success(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    fake = FakeBybit(now_ref)
    fake.fail_scope = "instruments-info"
    service = _make_service(tmp_path, fake, now_ref)
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is False
    assert result["state"] == "error"
    assert result["ranked_rows"] == []
    assert result["refresh_error"]["scope"] == "instruments"


def test_empty_instrument_universe_is_explicit_not_blank_success(tmp_path: Path):
    async def fetch(path, _params):
        assert path == "/v5/market/instruments-info"
        return {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}}

    service = ATRScannerService(
        fetch_json=fetch,
        settings_path=tmp_path / "settings.json",
        now_ms=lambda: DEFAULT_NOW_MS,
        request_spacing_seconds=0,
        request_retries=0,
    )
    result = asyncio.run(service.refresh(manual=True))
    assert result["ok"] is False
    assert result["state"] == "error"
    assert result["refresh_error"]["scope"] == "instruments"


def test_manual_exclusion_remains_excluded_regardless_of_liquidity_or_atr(tmp_path: Path):
    now_ref = [1_787_558_550_000]
    fake = FakeBybit(now_ref)
    service = _make_service(tmp_path, fake, now_ref)
    service.save_settings({"manual_exclusions": " alphausdt "})
    result = asyncio.run(service.refresh(manual=True))
    assert [row["symbol"] for row in result["ranked_rows"]] == ["BETAUSDT"]
    alpha = next(row for row in result["excluded_rows"] if row["symbol"] == "ALPHAUSDT")
    assert alpha["reasons"] == ["manual_exclusion"]
