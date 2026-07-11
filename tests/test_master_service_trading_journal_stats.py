import importlib.util
import importlib.machinery
import sys
import types
import pytest
if "multipart" not in sys.modules:
    multipart_mod = types.ModuleType("multipart")
    multipart_mod.__spec__ = importlib.machinery.ModuleSpec("multipart", loader=None)
    multipart_mod.__version__ = "0.0-test"
    multipart_sub = types.ModuleType("multipart.multipart")
    multipart_sub.__spec__ = importlib.machinery.ModuleSpec("multipart.multipart", loader=None)
    multipart_sub.parse_options_header = lambda value: (value, {})
    sys.modules["multipart"] = multipart_mod
    sys.modules["multipart.multipart"] = multipart_sub

try:
    import requests as _rq  # noqa: F401
    if not hasattr(_rq, "adapters"):
        raise ImportError
except Exception:
    req = types.ModuleType("requests")
    req.__spec__ = importlib.machinery.ModuleSpec("requests", loader=None)
    adapters = types.ModuleType("requests.adapters")
    adapters.__spec__ = importlib.machinery.ModuleSpec("requests.adapters", loader=None)
    adapters.HTTPAdapter = object
    req.adapters = adapters
    sys.modules["requests"] = req
    sys.modules["requests.adapters"] = adapters
try:
    from urllib3.util.retry import Retry  # noqa: F401
except Exception:
    urllib3 = types.ModuleType("urllib3")
    urllib3.__spec__ = importlib.machinery.ModuleSpec("urllib3", loader=None)
    util = types.ModuleType("urllib3.util")
    util.__spec__ = importlib.machinery.ModuleSpec("urllib3.util", loader=None)
    retry = types.ModuleType("urllib3.util.retry")
    retry.__spec__ = importlib.machinery.ModuleSpec("urllib3.util.retry", loader=None)
    retry.Retry = object
    sys.modules["urllib3"] = urllib3
    sys.modules["urllib3.util"] = util
    sys.modules["urllib3.util.retry"] = retry

try:
    _httpx_spec = importlib.util.find_spec("httpx")
except ValueError:
    _httpx_spec = None
if _httpx_spec is None:
    class _HttpxResponse:
        pass
    class _HttpxAsyncClient:
        pass
    httpx_stub = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=_HttpxAsyncClient,
        Response=_HttpxResponse,
        BaseTransport=object,
        TimeoutException=Exception,
        RequestError=Exception,
        HTTPStatusError=Exception,
        ConnectError=Exception,
    )
    httpx_stub.__spec__ = importlib.machinery.ModuleSpec("httpx", loader=None)
    sys.modules["httpx"] = httpx_stub

import render.master_service as master_service
from render.master_service import _compute_journal_stats, _compute_journal_period_stats, _build_journal_balance_timelines
from tools.master_journal_workbook import TARGET_RECOMMENDATION_INSUFFICIENT, _target_r_recommendation, _target_r_realized_from_original_plan


def test_compute_journal_stats_winner_loser_splits_and_durations() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "EURUSD",
            "entry_price": 100,
            "stop_loss": 95,
            "take_profit": 110,
            "result_pct": 2.0,
            "r_multiple": 1.2,
            "net_profit": 10,
            "trade_duration_seconds": 3600,
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-01T01:00:00Z",
        },
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "USDJPY",
            "entry_price": 100,
            "stop_loss": 98,
            "take_profit": 95,
            "result_pct": -1.0,
            "r_multiple": -0.5,
            "net_profit": -5,
            "trade_duration_seconds": 7200,
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-01T02:00:00Z",
        },
        {
            "row_type": "trade",
            "asset_class": "crypto",
            "symbol": "BTCUSDT",
            "entry_price": 200,
            "stop_loss": 180,
            "take_profit": 240,
            "result_pct": 4.0,
            "r_multiple": 2.0,
            "net_profit": 20,
            "trade_duration_seconds": 1800,
            "open_time": "2026-01-02T00:00:00Z",
            "close_time": "2026-01-02T00:30:00Z",
        },
        {
            "row_type": "trade",
            "asset_class": "crypto",
            "symbol": "ETHUSDT",
            "entry_price": 50,
            "stop_loss": 45,
            "take_profit": 40,
            "result_pct": -2.0,
            "r_multiple": -1.0,
            "net_profit": -8,
            "trade_duration_seconds": 10800,
            "open_time": "2026-01-03T00:00:00Z",
            "close_time": "2026-01-03T03:00:00Z",
        },
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "AUDUSD",
            "entry_price": 100,
            "stop_loss": 99,
            "take_profit": 101,
            "result_pct": 0.0,
            "r_multiple": 0.0,
            "net_profit": 0,
            "trade_duration_seconds": 5400,
            "breakeven": "yes",
            "open_time": "2026-01-04T00:00:00Z",
            "close_time": "2026-01-04T01:30:00Z",
        },
    ]

    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]
    duration = stats["groups"]["duration"]

    assert risk["avg_stop_pct_winners"] == 7.5
    assert risk["avg_stop_pct_losers"] == 6.0
    assert risk["avg_target_pct_winners"] == 15.0
    assert risk["avg_target_pct_losers"] == 12.5
    assert risk["avg_result_pct_winners"] == 3.0
    assert risk["avg_result_pct_losers"] == -1.5
    assert risk["avg_r_multiple_winners"] == 1.6
    assert risk["avg_r_multiple_losers"] == -0.75
    by_risk = risk["by_market"]
    assert by_risk["overall"]["avg_stop_pct_winners"] == 7.5
    assert by_risk["fx"]["avg_stop_pct_winners"] == 5.0
    assert by_risk["fx"]["avg_stop_pct_losers"] == 2.0
    assert by_risk["crypto"]["avg_stop_pct_winners"] == 10.0
    assert by_risk["crypto"]["avg_stop_pct_losers"] == 10.0
    assert by_risk["fx"]["avg_target_pct_winners"] == 10.0
    assert by_risk["crypto"]["avg_target_pct_losers"] == 20.0
    assert by_risk["fx"]["avg_result_pct_losers"] == -1.0
    assert by_risk["crypto"]["avg_r_multiple_winners"] == 2.0

    assert duration["overall_avg_winner_seconds"] == 2700
    assert duration["overall_avg_loser_seconds"] == 9000
    assert duration["overall_longest_winner_seconds"] == 3600
    assert duration["overall_longest_loser_seconds"] == 10800

    assert duration["fx_avg_winner_seconds"] == 3600
    assert duration["fx_avg_loser_seconds"] == 7200
    assert duration["fx_shortest_winner_seconds"] == 3600
    assert duration["fx_shortest_loser_seconds"] == 7200
    assert duration["fx_longest_winner_seconds"] == 3600
    assert duration["fx_longest_loser_seconds"] == 7200

    assert duration["crypto_avg_winner_seconds"] == 1800
    assert duration["crypto_avg_loser_seconds"] == 10800
    assert duration["crypto_shortest_winner_seconds"] == 1800
    assert duration["crypto_shortest_loser_seconds"] == 10800
    assert duration["crypto_longest_winner_seconds"] == 1800
    assert duration["crypto_longest_loser_seconds"] == 10800
    by_market = stats["groups"]["by_market"]
    assert by_market["overall"]["trades"] == 5
    assert by_market["fx"]["trades"] == 3
    assert by_market["crypto"]["trades"] == 2
    assert isinstance(stats["groups"]["market_breakdown"], list)
    assert stats["totals"]["gross_gain"] == 30
    assert stats["totals"]["gross_loss"] == 13
    assert stats["totals"]["net_profit_total"] == 17
    assert by_market["overall"]["metric_sources"]["max_result_pct"]["symbol"] == "BTCUSDT"
    assert by_market["overall"]["metric_sources"]["min_result_pct"]["symbol"] == "ETHUSDT"
    assert by_market["overall"]["metric_sources"]["max_r_multiple"]["symbol"] == "BTCUSDT"
    assert duration["metric_sources"]["overall_longest_seconds"]["symbol"] == "ETHUSDT"
    assert duration["metric_sources"]["overall_shortest_seconds"]["symbol"] == "BTCUSDT"
    leaders = stats["groups"]["leaders"]
    assert leaders["fx_most_wins_instrument"]["symbol"] == "EURUSD"
    assert leaders["fx_most_wins_instrument"]["wins"] == 1
    assert leaders["fx_most_losses_instrument"]["losses"] == 1
    assert leaders["crypto_most_wins_instrument"]["symbol"] == "BTCUSDT"
    assert leaders["crypto_most_losses_instrument"]["symbol"] == "ETHUSDT"


def test_compute_journal_stats_market_drawdown_uses_cashflow_segments() -> None:
    rows = [
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "account": "FX", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10, "analysis_balance_after_trade": 1000, "close_time": "2026-01-01T00:00:00Z"},
        {"row_type": "trade", "asset_class": "fx", "symbol": "GBPUSD", "account": "FX", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -10, "analysis_balance_after_trade": 900, "close_time": "2026-01-02T00:00:00Z"},
        {"row_type": "cashflow", "account": "FX", "cashflow_amount": 600, "close_time": "2026-01-03T00:00:00Z"},
        {"row_type": "trade", "asset_class": "fx", "symbol": "AUDUSD", "account": "FX", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10, "analysis_balance_after_trade": 1500, "close_time": "2026-01-04T00:00:00Z"},
        {"row_type": "trade", "asset_class": "fx", "symbol": "USDJPY", "account": "FX", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -10, "analysis_balance_after_trade": 1450, "close_time": "2026-01-05T00:00:00Z"},
        {"row_type": "trade", "asset_class": "crypto", "symbol": "BTCUSDT", "account": "CRYPTO", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10, "analysis_balance_after_trade": 2000, "close_time": "2026-01-01T00:00:00Z"},
        {"row_type": "trade", "asset_class": "crypto", "symbol": "ETHUSDT", "account": "CRYPTO", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -10, "analysis_balance_after_trade": 1800, "close_time": "2026-01-02T00:00:00Z"},
        {"row_type": "cashflow", "account": "CRYPTO", "cashflow_amount": -100, "close_time": "2026-01-03T00:00:00Z"},
        {"row_type": "trade", "asset_class": "crypto", "symbol": "SOLUSDT", "account": "CRYPTO", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10, "analysis_balance_after_trade": 1700, "close_time": "2026-01-04T00:00:00Z"},
        {"row_type": "trade", "asset_class": "crypto", "symbol": "XRPUSDT", "account": "CRYPTO", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -10, "analysis_balance_after_trade": 1600, "close_time": "2026-01-05T00:00:00Z"},
    ]

    stats = _compute_journal_stats(rows, balances=[])
    by_market = stats["groups"]["by_market"]
    risk_by_market = stats["groups"]["risk_expectancy"]["by_market"]

    assert by_market["fx"]["max_drawdown_pct"] == pytest.approx(10.0)
    assert by_market["fx"]["avg_drawdown_pct"] == pytest.approx((10.0 + (50 / 1500 * 100)) / 2)
    assert by_market["crypto"]["max_drawdown_pct"] == pytest.approx(10.0)
    assert by_market["crypto"]["avg_drawdown_pct"] == pytest.approx((10.0 + (100 / 1700 * 100)) / 2)
    assert risk_by_market["fx"]["max_drawdown_pct"] == by_market["fx"]["max_drawdown_pct"]
    assert risk_by_market["fx"]["avg_drawdown_pct"] == by_market["fx"]["avg_drawdown_pct"]
    assert risk_by_market["crypto"]["max_drawdown_pct"] == by_market["crypto"]["max_drawdown_pct"]
    assert risk_by_market["crypto"]["avg_drawdown_pct"] == by_market["crypto"]["avg_drawdown_pct"]
    assert by_market["fx"]["drawdown_segments_count"] == 2
    assert by_market["crypto"]["drawdown_segments_count"] == 2


def test_compute_journal_stats_distance_fallback_percent_points_are_not_fraction_scaled() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "EURUSD",
            "result_pct": 1.0,
            "r_multiple": 1.0,
            "net_profit": 10.0,
            "stop_loss_distance_pct": 1.0,
            "target_distance_pct": 2.0,
        }
    ]
    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]
    assert risk["avg_stop_pct_winners"] == pytest.approx(1.0)
    assert risk["avg_target_pct_winners"] == pytest.approx(2.0)
    assert risk["by_market"]["fx"]["avg_stop_pct_winners"] == pytest.approx(1.0)


def test_compute_journal_stats_recommends_stop_from_win_loss_distances_but_target_needs_r_data() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "EURUSD",
            "result_pct": 1.0,
            "net_profit": 10.0,
            "stop_loss_distance_pct": 1.0,
            "target_distance_pct": 2.0,
        },
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "EURUSD",
            "result_pct": -1.0,
            "net_profit": -5.0,
            "stop_loss_distance_pct": 2.0,
            "target_distance_pct": 4.0,
        },
    ]

    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]
    instrument = next(item for item in stats["by_instrument"] if item["symbol"] == "EURUSD")

    assert risk["stop_recommendation"] == "Reduce stop loss"
    assert risk["target_recommendation"] == TARGET_RECOMMENDATION_INSUFFICIENT
    assert risk["by_market"]["fx"]["stop_recommendation"] == "Reduce stop loss"
    assert instrument["stop_recommendation"] == "Reduce stop loss"
    assert instrument["target_recommendation"] == TARGET_RECOMMENDATION_INSUFFICIENT


def _target_distribution_trade(
    trade_id: str,
    r_multiple: object,
    *,
    symbol: str = "EURUSD",
    net_profit: float | None = None,
    result_pct: float = 1.0,
    planned_target_price: float = 140.0,
    include_plan: bool = True,
    original_risk_amount: float = 10.0,
    **overrides: object,
) -> dict:
    try:
        realized_r = float(r_multiple)
    except (TypeError, ValueError):
        realized_r = None
    resolved_net_profit = net_profit
    if resolved_net_profit is None:
        resolved_net_profit = original_risk_amount * realized_r if realized_r is not None else 10.0
    row = {
        "id": trade_id,
        "row_type": "trade",
        "asset_class": "fx",
        "account": "OANDA DEMO",
        "symbol": symbol,
        "side": "BUY",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "take_profit": 140.0,
        "net_profit": resolved_net_profit,
        "result_pct": result_pct,
        "r_multiple": r_multiple,
        "currency": "AUD",
    }
    if include_plan:
        row.update({
            "planned_entry_price": 100.0,
            "planned_stop_price": 90.0,
            "planned_target_price": planned_target_price,
        })
    if realized_r is not None:
        row["original_risk_amount"] = original_risk_amount
        row["original_risk_currency"] = "AUD"
    row.update(overrides)
    if "exit_price" not in row:
        if realized_r is not None:
            side = str(row.get("side") or "").strip().upper()
            risk = abs(float(row["planned_entry_price"] if include_plan else row["entry_price"]) - float(row["planned_stop_price"] if include_plan else row["stop_loss"]))
            entry = float(row["planned_entry_price"] if include_plan else row["entry_price"])
            row["exit_price"] = entry - (risk * realized_r) if side.startswith("SELL") else entry + (risk * realized_r)
    return row


def test_compute_journal_stats_target_recommendation_uses_realized_original_r_distribution() -> None:
    rows = [
        _target_distribution_trade(f"win-{idx}", value)
        for idx, value in enumerate([2.4, 2.5, 2.7, 3.0, 3.1, 3.4, 3.6, 4.8], start=1)
    ]
    rows.extend([
        _target_distribution_trade("loss-excluded", -6.0, net_profit=-5.0, result_pct=-1.0),
        _target_distribution_trade("test-excluded", 6.0, is_test_trade=True),
        _target_distribution_trade("be-excluded", 6.0, net_profit=0.0, result_pct=0.0, breakeven="yes"),
        _target_distribution_trade("invalid-r-excluded", None),
        _target_distribution_trade(
            "moved-without-original-excluded",
            6.0,
            include_plan=False,
            move_to_break_even_trigger_price=101.0,
        ),
    ])

    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]
    by_market = stats["groups"]["by_market"]
    instrument = next(item for item in stats["by_instrument"] if item["symbol"] == "EURUSD")
    direct_target = _target_r_recommendation(rows)

    assert risk["target_recommendation"] == "Reduce target — Recommended: 3.25R (current median: 4.0R)"
    assert risk["eligible_target_r_wins"] == 8
    assert risk["target_r_increment"] == pytest.approx(1.0)
    assert risk["target_r_distribution"]["4.0R-5.0R"] == 1
    assert risk["target_r_peak_bucket"] == "3.0R-4.0R"
    assert risk["target_r_peak_bucket_low"] == pytest.approx(3.0)
    assert risk["target_r_peak_bucket_high"] == pytest.approx(4.0)
    assert risk["target_r_peak_instances"] == 4
    assert risk["target_r_recommended"] == pytest.approx(3.25)
    assert risk["current_median_original_planned_target_r"] == pytest.approx(4.0)
    assert risk["current_avg_original_planned_target_r"] == pytest.approx(4.0)
    assert risk["target_r_excluded_reasons"]["not_winning_trade"] >= 2
    assert direct_target["target_r_excluded_reasons"]["test_trade"] == 1
    assert risk["target_r_excluded_reasons"]["missing_independent_original_monetary_risk"] == 1
    assert risk["target_r_excluded_reasons"]["moved_without_original_plan"] == 1
    assert by_market["overall"]["target_recommendation"] == risk["target_recommendation"]
    assert by_market["fx"]["target_recommendation"] == risk["target_recommendation"]
    assert by_market["crypto"]["target_recommendation"] == TARGET_RECOMMENDATION_INSUFFICIENT
    assert stats["groups"]["overview"]["target_recommendation"] == risk["target_recommendation"]
    assert instrument["target_recommendation"] == risk["target_recommendation"]


def test_compute_journal_stats_target_recommendation_allows_moved_trade_with_original_plan() -> None:
    rows = [
        _target_distribution_trade(f"win-{idx}", value)
        for idx, value in enumerate([2.4, 2.5, 2.7, 3.0, 3.1, 3.4, 3.6, 4.8], start=1)
    ]
    rows.append(
        _target_distribution_trade(
            "moved-with-original",
            3.2,
            move_to_profit_trigger_price=130.0,
        )
    )

    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]

    assert risk["eligible_target_r_wins"] == 9
    assert risk["target_r_excluded_reasons"].get("moved_without_original_plan") is None
    assert risk["target_recommendation"].startswith("Reduce target — Recommended: ")


def test_compute_journal_stats_target_planned_r_uses_original_plan_and_validates_side() -> None:
    invalid_long_rows = [
        _target_distribution_trade(
            f"invalid-long-{idx}",
            value,
            planned_target_price=80.0,
        )
        for idx, value in enumerate([2.4, 2.5, 2.7, 3.0, 3.1, 3.4, 3.6, 4.8], start=1)
    ]

    invalid_stats = _compute_journal_stats(invalid_long_rows, balances=[])
    invalid_risk = invalid_stats["groups"]["risk_expectancy"]
    assert invalid_risk["current_avg_original_planned_target_r"] is None
    assert invalid_risk["eligible_target_r_wins"] == 0
    assert invalid_risk["target_recommendation"] == TARGET_RECOMMENDATION_INSUFFICIENT

    short_rows = []
    for idx, value in enumerate([2.4, 2.5, 2.7, 3.0, 3.1, 3.4, 3.6, 4.8], start=1):
        short_rows.append(
            _target_distribution_trade(
                f"short-{idx}",
                value,
                side="SELL",
                planned_stop_price=110.0,
                planned_target_price=60.0,
                stop_loss=110.0,
                take_profit=60.0,
            )
        )

    short_stats = _compute_journal_stats(short_rows, balances=[])
    short_risk = short_stats["groups"]["risk_expectancy"]
    assert short_risk["current_avg_original_planned_target_r"] == pytest.approx(4.0)
    assert short_risk["target_recommendation"] == "Reduce target — Recommended: 3.25R (current median: 4.0R)"


def test_target_recommendation_uses_net_profit_over_original_monetary_risk_not_stored_gross_r() -> None:
    rows = [
        _target_distribution_trade(
            f"stored-r-ignored-{idx}",
            99.0,
            net_profit=10.0 * realized_r,
            exit_price=100.0 + (10.0 * realized_r),
        )
        for idx, realized_r in enumerate([2.4, 2.5, 2.7, 3.0, 3.1, 3.4, 3.6, 4.8], start=1)
    ]

    risk = _compute_journal_stats(rows, balances=[])["groups"]["risk_expectancy"]

    assert risk["target_r_peak_bucket"] == "3.0R-4.0R"
    assert risk["target_r_recommended"] == pytest.approx(3.25)
    assert risk["target_recommendation"] == "Reduce target — Recommended: 3.25R (current median: 4.0R)"


def test_target_recommendation_tied_modal_bucket_selects_lower_target() -> None:
    rows = [
        _target_distribution_trade(f"tie-{idx}", value)
        for idx, value in enumerate([2.0, 2.1, 2.5, 2.6, 5.0], start=1)
    ]

    risk = _compute_journal_stats(rows, balances=[])["groups"]["risk_expectancy"]

    assert risk["target_r_increment"] == pytest.approx(0.5)
    assert risk["target_r_distribution"]["2.0R-2.5R"] == 2
    assert risk["target_r_distribution"]["2.5R-3.0R"] == 2
    assert risk["target_r_peak_bucket"] == "2.0R-2.5R"
    assert risk["target_r_recommended"] == pytest.approx(2.05)
    assert risk["target_recommendation"] == "Reduce target — Recommended: 2.05R (current median: 4.0R)"


def test_target_realized_r_uses_net_profit_after_costs_over_original_monetary_risk() -> None:
    row = _target_distribution_trade(
        "net-after-costs",
        99.0,
        net_profit=8.5,
        commission=1.5,
        original_risk_amount=10.0,
        exit_price=200.0,
    )

    realized_r, reason = _target_r_realized_from_original_plan(row)

    assert reason == ""
    assert realized_r == pytest.approx(0.85)


def _fx_trade_with_independent_conversion(
    trade_id: str,
    *,
    net_profit: float,
    conversion: float | None = 1.5,
    financing: float | None = None,
) -> dict:
    row = {
        "id": trade_id,
        "row_type": "trade",
        "asset_class": "fx",
        "account": "OANDA DEMO",
        "symbol": "EURUSD",
        "side": "BUY",
        "entry_price": 1.1,
        "exit_price": 1.101,
        "stop_loss": 1.099,
        "take_profit": 1.102,
        "planned_entry_price": 1.1,
        "planned_stop_price": 1.099,
        "planned_target_price": 1.102,
        "qty": 0.1,
        "qty_raw": 10000,
        "qty_unit": "lots",
        "net_profit": net_profit,
        "currency": "AUD",
    }
    if conversion is not None:
        row["raw_refs"] = {"homeConversionFactors": {"loss": conversion}}
    if financing is not None:
        row["swap"] = financing
    return row


def test_fx_net_r_changes_when_net_profit_changes_with_fixed_independent_risk() -> None:
    first_r, first_reason = _target_r_realized_from_original_plan(
        _fx_trade_with_independent_conversion("fx-fixed-risk-1", net_profit=15.0)
    )
    second_r, second_reason = _target_r_realized_from_original_plan(
        _fx_trade_with_independent_conversion("fx-fixed-risk-2", net_profit=30.0)
    )

    assert first_reason == ""
    assert second_reason == ""
    assert first_r == pytest.approx(1.0)
    assert second_r == pytest.approx(2.0)


def test_fx_net_profit_cannot_be_cancelled_by_inferred_conversion_rate() -> None:
    first_row = _fx_trade_with_independent_conversion("fx-no-inferred-1", net_profit=15.0, conversion=None)
    second_row = _fx_trade_with_independent_conversion("fx-no-inferred-2", net_profit=30.0, conversion=None)

    assert _target_r_realized_from_original_plan(first_row) == (None, "missing_independent_original_monetary_risk")
    assert _target_r_realized_from_original_plan(second_row) == (None, "missing_independent_original_monetary_risk")


def test_target_recommendation_does_not_use_gross_price_r_when_monetary_risk_unavailable() -> None:
    rows = []
    for idx, value in enumerate([1.2, 1.3, 1.4, 1.5, 1.6], start=1):
        row = _target_distribution_trade(f"gross-only-{idx}", value)
        row.pop("original_risk_amount", None)
        rows.append(row)

    risk = _target_r_recommendation(rows)

    assert risk["eligible_target_r_wins"] == 0
    assert risk["target_recommendation"] == TARGET_RECOMMENDATION_INSUFFICIENT
    assert risk["target_r_excluded_reasons"]["missing_independent_original_monetary_risk"] == 5


def test_fx_home_conversion_factors_loss_produces_original_monetary_risk() -> None:
    realized_r, reason = _target_r_realized_from_original_plan(
        _fx_trade_with_independent_conversion("fx-home-conv", net_profit=22.5)
    )

    assert reason == ""
    assert realized_r == pytest.approx(1.5)


def test_fx_financing_changes_net_r_numerator_without_changing_conversion_factor() -> None:
    base_r, base_reason = _target_r_realized_from_original_plan(
        _fx_trade_with_independent_conversion("fx-no-financing", net_profit=15.0, financing=0.0)
    )
    financed_r, financed_reason = _target_r_realized_from_original_plan(
        _fx_trade_with_independent_conversion("fx-with-financing", net_profit=14.0, financing=-1.0)
    )

    assert base_reason == ""
    assert financed_reason == ""
    assert base_r == pytest.approx(1.0)
    assert financed_r == pytest.approx(14.0 / 15.0)


def test_unknown_original_risk_currency_is_not_compatible() -> None:
    row = _target_distribution_trade("unknown-risk-currency", 1.5)
    row.pop("original_risk_currency", None)

    realized_r, reason = _target_r_realized_from_original_plan(row)

    assert realized_r is None
    assert reason == "missing_independent_original_monetary_risk"


def test_unrelated_max_loss_is_never_used_as_original_risk() -> None:
    row = _fx_trade_with_independent_conversion("fx-max-loss", net_profit=15.0, conversion=None)
    row["max_loss"] = 15.0

    realized_r, reason = _target_r_realized_from_original_plan(row)

    assert realized_r is None
    assert reason == "missing_independent_original_monetary_risk"


def test_crypto_reconstructs_original_monetary_risk_independently() -> None:
    row = {
        "id": "crypto-risk",
        "row_type": "trade",
        "asset_class": "crypto",
        "account": "BYBIT",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "entry_price": 100.0,
        "exit_price": 130.0,
        "stop_loss": 90.0,
        "take_profit": 130.0,
        "planned_entry_price": 100.0,
        "planned_stop_price": 90.0,
        "planned_target_price": 130.0,
        "qty": 0.5,
        "net_profit": 15.0,
        "currency": "USDT",
    }

    realized_r, reason = _target_r_realized_from_original_plan(row)

    assert reason == ""
    assert realized_r == pytest.approx(3.0)


def test_oanda_export_conversion_rate_is_retained_and_used_for_target_r() -> None:
    csv = """TICKET,TRANSACTION DATE,TRANSACTION TYPE,DETAILS,INSTRUMENT,PRICE,UNITS,DIRECTION,SPREAD COST,STOP LOSS,TAKE PROFIT,CONVERSION RATE,FINANCING,COMMISSION,GSL FEE,GSL PREMIUM,PL,BALANCE
100,2026-01-01 10:00:00 AEST,ORDER_FILL,MARKET_ORDER,EUR_USD,1.10000,10000,Buy,0.0,1.09900,1.10200,1.5,0,0,0,0,0,1000
101,2026-01-01 11:00:00 AEST,ORDER_FILL,TAKE_PROFIT_ORDER,EUR_USD,1.10200,10000,Sell,0.0,,,1.6,0,0,0,0,30,1030
"""
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(
        master_service.pd.read_csv(master_service.io.StringIO(csv)),
        account_mode="demo",
        account_label="OANDA DEMO",
        source_path="oanda_conversion.csv",
    )
    row = parsed["rows"][0]

    assert row["metrics"]["oanda_export_conversion_rate"] == pytest.approx(1.5)
    assert row["raw_refs"]["conversion_rate"] == pytest.approx(1.5)
    realized_r, reason = _target_r_realized_from_original_plan(row)
    assert reason == ""
    assert realized_r == pytest.approx(2.0)


def test_target_recommendation_uses_nested_complete_original_plan_after_partial_top_level_plan() -> None:
    rows = []
    for idx, value in enumerate([1.0, 1.1, 1.2, 1.3, 1.4], start=1):
        row = _target_distribution_trade(
            f"nested-plan-{idx}",
            value,
            include_plan=False,
            net_profit=10.0 * value,
        )
        row.pop("original_risk_amount", None)
        row["planned_entry_price"] = 100.0
        row["raw_refs"] = {
            "quote_result": {
                "planned_entry_price": 100.0,
                "planned_stop_price": 90.0,
                "planned_target_price": 140.0,
                "estimated_total_loss": 10.0,
                "display_currency": "AUD",
            }
        }
        rows.append(row)

    risk = _target_r_recommendation(rows)

    assert risk["eligible_target_r_wins"] == 5
    assert risk["target_r_recommended"] == pytest.approx(1.1)
    assert risk["current_median_original_planned_target_r"] == pytest.approx(4.0)


def test_target_recommendation_never_recommends_zero_for_small_positive_modal_bucket() -> None:
    rows = [
        _target_distribution_trade(f"small-{idx}", value)
        for idx, value in enumerate([0.1, 0.2, 0.3, 0.6, 0.7], start=1)
    ]

    risk = _target_r_recommendation(rows)

    assert risk["target_r_peak_bucket"] == "0.0R-0.5R"
    assert risk["target_r_recommended"] == pytest.approx(0.2)
    assert risk["target_r_recommended"] > 0
    assert "Recommended: 0.0R" not in risk["target_recommendation"]


def test_target_recommendation_current_avg_original_planned_target_r_is_arithmetic_mean() -> None:
    rows = [
        _target_distribution_trade(f"avg-{idx}", value, planned_target_price=target)
        for idx, (value, target) in enumerate(
            zip([1.0, 1.1, 1.2, 1.3, 1.4], [120.0, 120.0, 140.0, 200.0, 200.0]),
            start=1,
        )
    ]

    risk = _target_r_recommendation(rows)

    assert risk["current_median_original_planned_target_r"] == pytest.approx(4.0)
    assert risk["current_avg_original_planned_target_r"] == pytest.approx(5.6)


def test_target_recommendation_compares_modal_bucket_to_median_planned_target_r() -> None:
    realized = [3.0, 3.1, 3.2, 3.3, 3.4]
    increase_rows = [
        _target_distribution_trade(f"increase-{idx}", value, planned_target_price=120.0)
        for idx, value in enumerate(realized, start=1)
    ]
    keep_rows = [
        _target_distribution_trade(f"keep-{idx}", value, planned_target_price=130.0)
        for idx, value in enumerate(realized, start=1)
    ]

    increase_risk = _compute_journal_stats(increase_rows, balances=[])["groups"]["risk_expectancy"]
    keep_risk = _compute_journal_stats(keep_rows, balances=[])["groups"]["risk_expectancy"]

    assert increase_risk["target_recommendation"] == "Increase target — Recommended: 3.1R (current median: 2.0R)"
    assert keep_risk["target_recommendation"] == "Keep target — Recommended: 3.1R (current median: 3.0R)"


def test_compute_journal_stats_no_zero_count_leaders() -> None:
    rows = [{"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 0.0, "r_multiple": 0.0, "net_profit": 0}]
    stats = _compute_journal_stats(rows, balances=[])
    leaders = stats["groups"]["leaders"]
    assert leaders["most_wins_instrument"] is None
    assert leaders["most_losses_instrument"] is None


def test_compute_journal_stats_expectancy_and_r_filters() -> None:
    rows = [
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 9.0, "r_multiple": 0.0, "net_profit": 9},
        {"row_type": "trade", "asset_class": "fx", "symbol": "GBPUSD", "result_pct": 9.0, "r_multiple": 2.0, "net_profit": 9},
        {"row_type": "trade", "asset_class": "fx", "symbol": "USDJPY", "result_pct": -3.0, "r_multiple": 1.0, "net_profit": -3},
        {"row_type": "trade", "asset_class": "fx", "symbol": "AUDUSD", "result_pct": -3.0, "r_multiple": -0.5, "net_profit": -3},
        {"row_type": "trade", "asset_class": "fx", "symbol": "NZDUSD", "result_pct": 0.0, "r_multiple": 0.0, "net_profit": 0, "breakeven": "yes"},
    ]
    stats = _compute_journal_stats(rows, balances=[])
    by_market = stats["groups"]["by_market"]["overall"]
    assert by_market["avg_result_pct"] == pytest.approx(2.4)
    assert by_market["expectancy_pct"] == pytest.approx(3.0)
    assert by_market["min_r_multiple_winners"] == pytest.approx(2.0)
    assert by_market["avg_r_multiple_winners"] == pytest.approx(2.0)
    assert by_market["min_r_multiple_losers"] == pytest.approx(-0.5)
    assert by_market["max_r_multiple_losers"] == pytest.approx(-0.5)
    assert stats["groups"]["risk_expectancy"]["expectancy_pct"] == pytest.approx(3.0)


def test_compute_journal_stats_tracks_test_trades_but_excludes_from_core_metrics() -> None:
    rows = [
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10},
        {"row_type": "trade", "asset_class": "fx", "symbol": "GBPUSD", "result_pct": 2.0, "r_multiple": 1.5, "net_profit": 20, "is_test_trade": True},
        {"row_type": "trade", "asset_class": "crypto", "symbol": "BTCUSDT", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -5, "is_test_trade": "yes"},
    ]
    stats = _compute_journal_stats(rows, balances=[])
    assert stats["totals"]["trades"] == 1
    assert stats["totals"]["test_trades"] == 2
    assert stats["totals"]["net_profit_total"] == 10
    by_market = stats["groups"]["by_market"]
    assert by_market["overall"]["test_trades"] == 2
    assert by_market["fx"]["test_trades"] == 1
    assert by_market["crypto"]["test_trades"] == 1


def test_compute_journal_period_stats_year_month_buckets_and_test_counts() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "crypto",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "open_time": "2025-12-31T23:00:00Z",
            "close_time": "2026-01-01T00:05:00Z",
            "net_profit": 10,
            "result_pct": 1.0,
            "r_multiple": 1.0,
            "move_to_break_even_duration": 300,
        },
        {
            "row_type": "trade",
            "asset_class": "crypto",
            "symbol": "ETHUSDT",
            "side": "SELL",
            "open_time": "2026-01-05T00:00:00Z",
            "close_time": "2026-01-05T00:10:00Z",
            "net_profit": -5,
            "result_pct": -0.5,
            "r_multiple": -0.5,
        },
        {
            "row_type": "trade",
            "asset_class": "fx",
            "symbol": "EURUSD",
            "open_time": "2026-02-01T00:00:00Z",
            "close_time": "2026-02-01T00:05:00Z",
            "net_profit": 99,
            "result_pct": 9,
            "is_test_trade": True,
        },
    ]
    reports = _compute_journal_period_stats(rows, balances=[])
    jan = reports["months"][2026][1]
    feb = reports["months"][2026][2]
    assert jan["totals"]["trades"] == 2
    assert jan["totals"]["test_trades"] == 0
    assert jan["totals"]["net_profit_total"] == 5
    assert jan["groups"]["by_market"]["overall"]["move_to_break_even_duration_seconds"] == 300
    assert feb["totals"]["trades"] == 0
    assert feb["totals"]["test_trades"] == 1
    assert reports["years"][2026]["totals"]["trades"] == 2


def test_compute_journal_stats_drawdown_behaviour() -> None:
    rows = [
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "net_profit": 1, "analysis_balance_after_trade": 1000, "account": "a", "close_time": "2026-01-01T00:00:00Z"},
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "net_profit": 1, "analysis_balance_after_trade": 1100, "account": "a", "close_time": "2026-01-02T00:00:00Z"},
        {"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "net_profit": -1, "analysis_balance_after_trade": 990, "account": "a", "close_time": "2026-01-03T00:00:00Z"},
    ]
    stats = _compute_journal_stats(rows, balances=[])
    assert stats["totals"]["max_drawdown_pct"] == 10.0
    single = _compute_journal_stats(rows[:1], balances=[])
    assert single["totals"]["max_drawdown_pct"] is None
    flat = _compute_journal_stats(rows[:2], balances=[])
    assert flat["totals"]["max_drawdown_pct"] == 0.0


def test_compute_journal_stats_streaks_and_money_by_currency() -> None:
    rows = [
        {"row_type": "trade", "id": "1", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 1.0, "r_multiple": 1.0, "net_profit": 10, "currency": "AUD", "close_time": "2026-01-01T00:00:00Z"},
        {"row_type": "trade", "id": "2", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 1.2, "r_multiple": 1.1, "net_profit": 12, "currency": "AUD", "close_time": "2026-01-02T00:00:00Z"},
        {"row_type": "trade", "id": "3", "asset_class": "fx", "symbol": "USDJPY", "result_pct": 0.8, "r_multiple": 0.9, "net_profit": 8, "currency": "AUD", "close_time": "2026-01-03T00:00:00Z"},
        {"row_type": "trade", "id": "4", "asset_class": "fx", "symbol": "AUDUSD", "result_pct": 0.0, "r_multiple": 0.0, "net_profit": 0, "breakeven": "yes", "currency": "AUD", "close_time": "2026-01-04T00:00:00Z"},
        {"row_type": "trade", "id": "5", "asset_class": "crypto", "symbol": "BTCUSDT", "result_pct": -1.0, "r_multiple": -1.0, "net_profit": -5, "currency": "USDT", "close_time": "2026-01-05T00:00:00Z"},
        {"row_type": "trade", "id": "6", "asset_class": "crypto", "symbol": "ETHUSDT", "result_pct": -1.2, "r_multiple": -1.1, "net_profit": -7, "currency": "USDT", "close_time": "2026-01-06T00:00:00Z"},
        {"row_type": "trade", "id": "7", "asset_class": "crypto", "symbol": "BTCUSDT", "result_pct": -0.4, "r_multiple": -0.5, "net_profit": -2, "currency": "USDT", "close_time": "2026-01-07T00:00:00Z"},
        {"row_type": "trade", "id": "8", "asset_class": "crypto", "symbol": "BTCUSDT", "result_pct": -0.3, "r_multiple": -0.4, "net_profit": -2, "currency": "USDT", "close_time": "2026-01-08T00:00:00Z"},
    ]
    stats = _compute_journal_stats(rows, balances=[])
    streaks = stats["groups"]["streaks"]
    assert streaks["longest_winning"]["trade_count"] == 3
    assert streaks["longest_winning"]["start_time"] == "2026-01-01T00:00:00Z"
    assert streaks["longest_winning"]["end_time"] == "2026-01-03T00:00:00Z"
    assert streaks["longest_winning"]["dominant_symbol"] == "EURUSD"
    assert round(streaks["longest_winning"]["net_r_multiple"], 3) == 3.0
    assert round(streaks["longest_winning"]["net_result_pct"], 3) == 3.0
    assert streaks["longest_losing"]["trade_count"] == 4
    money = stats["totals"]["money_by_currency"]["net_profit_total"]
    assert money["AUD"] == 30
    assert money["USDT"] == -16
    by_market = stats["groups"]["by_market"]
    assert by_market["overall"]["money_by_currency"]["mixed_currency"] is True
    assert by_market["fx"]["money_by_currency"]["currencies"] == ["AUD"]
    assert by_market["crypto"]["money_by_currency"]["currencies"] == ["USDT"]


def test_oanda_demo_newer_authoritative_balance_overrides_stale_cashflow() -> None:
    rows = [
        {
            "row_type": "trade",
            "account_label": "OANDA DEMO",
            "source": "oanda",
            "symbol": "EURUSD",
            "open_time": "2026-05-10T00:00:00Z",
            "close_time": "2026-05-10T01:00:00Z",
            "net_profit": 10.0,
            "balance_after_trade": 1500.65,
        }
    ]
    ledger = {
        "OANDA DEMO": [
            {"account": "OANDA DEMO", "date": "2026-04-01T00:00:00Z", "new_balance": 200.589, "currency": "AUD"}
        ]
    }
    out = _build_journal_balance_timelines(rows, ledger, excel_balances=[])
    bal = next(b for b in out["balances"] if str(b.get("label")).upper() == "OANDA DEMO")
    assert bal["balance"] == 1500.65
    assert bal["balance_source"] != "cashflow_anchor_plus_trades"
    diag = out["diagnostics"]["OANDA DEMO"]
    assert diag["stale_cashflow_overridden"] is True
    assert diag["previous_cashflow_balance"] == 200.589

def test_compute_journal_stats_by_market_streaks_reset_on_break_even() -> None:
    rows = [
        {"row_type":"trade","id":"fx-w1","asset_class":"fx","symbol":"EURUSD","net_profit":10,"close_time":"2026-01-01T00:00:00Z"},
        {"row_type":"trade","id":"fx-w2","asset_class":"fx","symbol":"GBPUSD","net_profit":11,"close_time":"2026-01-02T00:00:00Z"},
        {"row_type":"trade","id":"fx-be","asset_class":"fx","symbol":"AUDUSD","net_profit":0,"result_pct":0,"breakeven":"yes","close_time":"2026-01-03T00:00:00Z"},
        {"row_type":"trade","id":"fx-l1","asset_class":"fx","symbol":"USDJPY","net_profit":-5,"close_time":"2026-01-04T00:00:00Z"},
        {"row_type":"trade","id":"crypto-l1","asset_class":"crypto","symbol":"BTCUSDT","net_profit":-1,"close_time":"2026-01-05T00:00:00Z"},
        {"row_type":"trade","id":"crypto-l2","asset_class":"crypto","symbol":"ETHUSDT","net_profit":-2,"close_time":"2026-01-06T00:00:00Z"},
        {"row_type":"trade","id":"crypto-w1","asset_class":"crypto","symbol":"BTCUSDT","net_profit":3,"close_time":"2026-01-07T00:00:00Z"},
    ]
    by_market = _compute_journal_stats(rows, balances=[])["groups"]["by_market"]
    assert by_market["overall"]["winning_streak"] == 2
    assert by_market["overall"]["losing_streak"] == 3
    assert by_market["fx"]["winning_streak"] == 2
    assert by_market["fx"]["losing_streak"] == 1
    assert by_market["crypto"]["winning_streak"] == 1
    assert by_market["crypto"]["losing_streak"] == 2
    assert by_market["overall"]["longest_losing_streak"]["trade_ids"] == ["fx-l1", "crypto-l1", "crypto-l2"]



def test_market_return_percentage_uses_account_balance_return() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "fx",
            "account": "OANDA DEMO",
            "symbol": "EURUSD",
            "net_profit": -100.0,
            "result_pct": -80.0,
            "r_multiple": -0.8,
            "currency": "AUD",
        }
    ]
    stats = _compute_journal_stats(
        rows,
        balances=[{"account": "OANDA DEMO", "balance": 900.0, "currency": "AUD"}],
    )
    fx = stats["groups"]["by_market"]["fx"]
    assert fx["market_return_pct"] == pytest.approx(-10.0)
    assert fx["gross_gain_return_pct"] == pytest.approx(0.0)
    assert fx["gross_loss_return_pct"] == pytest.approx(80.0)
    assert fx["gross_ir_loss"] == pytest.approx(0.8)
    assert fx["return_method"] == "cashflow_adjusted_account_balance"


def test_market_return_percentage_is_bounded_by_account_capital() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "crypto",
            "account": "BYBIT",
            "symbol": "BTCUSDT",
            "net_profit": -100.0,
            "result_pct": -250.0,
            "currency": "USDT",
        }
    ]
    stats = _compute_journal_stats(
        rows,
        balances=[{"account": "BYBIT", "balance": 0.0, "currency": "USDT"}],
    )
    crypto = stats["groups"]["by_market"]["crypto"]
    assert crypto["market_return_pct"] == pytest.approx(-100.0)
    assert crypto["gross_loss_return_pct"] == pytest.approx(250.0)


def test_market_return_percentage_rejects_mixed_fx_currencies() -> None:
    rows = [
        {"row_type": "trade", "asset_class": "fx", "account": "OANDA DEMO", "symbol": "EURUSD", "net_profit": -100.0, "currency": "AUD"},
        {"row_type": "trade", "asset_class": "fx", "account": "FOREX USD", "symbol": "USDJPY", "net_profit": 20.0, "currency": "USD"},
    ]
    stats = _compute_journal_stats(
        rows,
        balances=[
            {"account": "OANDA DEMO", "balance": 900.0, "currency": "AUD"},
            {"account": "FOREX USD", "balance": 520.0, "currency": "USD"},
        ],
    )
    fx = stats["groups"]["by_market"]["fx"]
    assert fx["market_return_pct"] is None
    assert fx["gross_gain_return_pct"] is None
    assert fx["gross_loss_return_pct"] is None
    assert fx["return_unavailable_reason"] == "unsupported_fx_currency"


def test_risk_of_ruin_is_deterministic_and_clamped() -> None:
    rows = [
        {"row_type": "trade", "account": "OANDA DEMO", "net_profit": 200.0, "analysis_balance_after_trade": 1200.0},
        {"row_type": "trade", "account": "OANDA DEMO", "net_profit": -100.0, "analysis_balance_after_trade": 900.0},
        {"row_type": "trade", "account": "OANDA DEMO", "net_profit": 100.0, "analysis_balance_after_trade": 1000.0},
        {"row_type": "trade", "account": "OANDA DEMO", "net_profit": -100.0, "analysis_balance_after_trade": 900.0},
        {"row_type": "trade", "account": "BYBIT", "net_profit": 10.0, "stop_loss_distance_pct": 2.0},
        {"row_type": "trade", "account": "BYBIT", "net_profit": -100.0, "stop_loss_distance_pct": 2.0},
        {"row_type": "trade", "account": "BINANCE", "net_profit": 10.0, "stop_loss_distance_pct": 1.0},
    ]
    risk = _compute_journal_stats(rows, balances=[])["groups"]["risk_of_ruin_by_account"]
    assert risk["OANDA DEMO"]["risk_of_ruin"] == pytest.approx((0.75 / 1.25) ** 10)
    assert 0 < risk["OANDA DEMO"]["risk_of_ruin"] < 1
    assert risk["OANDA DEMO"]["edge"] > 0
    assert risk["OANDA DEMO"]["trade_count"] == 4
    assert risk["OANDA DEMO"]["win_rate"] == pytest.approx(0.5)
    assert risk["OANDA DEMO"]["payoff_ratio"] == pytest.approx(1.5)
    assert risk["OANDA DEMO"]["risk_per_trade_fraction"] == pytest.approx(0.1)
    assert risk["OANDA DEMO"]["capital_units"] == pytest.approx(10.0)
    assert risk["OANDA DEMO"]["risk_source"] == "median_loss_over_balance"
    assert risk["BYBIT"]["risk_of_ruin"] == 1.0
    assert risk["BYBIT"]["edge"] <= 0
    assert risk["BINANCE"]["risk_of_ruin"] is None
    assert risk["BINANCE"]["reason"] == "requires_wins_and_losses"
    for key in ("win_rate", "payoff_ratio", "risk_per_trade_fraction", "edge", "capital_units", "risk_source", "trade_count"):
        assert key in risk["BINANCE"]


def test_invalid_fx_target_distance_is_ignored_but_large_crypto_target_is_kept() -> None:
    rows = [
        {
            "row_type": "trade", "asset_class": "fx", "account": "OANDA DEMO",
            "symbol": "USDJPY", "entry_price": 153.0, "take_profit": 1.0,
            "stop_loss": 152.0, "result_pct": 1.0, "net_profit": 1.0,
        },
        {
            "row_type": "trade", "asset_class": "crypto", "account": "BYBIT",
            "symbol": "BTCUSDT", "entry_price": 100.0, "take_profit": 174.65,
            "stop_loss": 95.0, "result_pct": 1.0, "net_profit": 1.0,
        },
    ]
    by_market = _compute_journal_stats(rows, balances=[])["groups"]["by_market"]
    assert by_market["fx"]["max_target_pct"] is None
    assert by_market["crypto"]["max_target_pct"] == pytest.approx(74.65)
    assert by_market["overall"]["max_target_pct"] == pytest.approx(74.65)
    assert by_market["overall"]["metric_sources"]["max_target_pct"]["symbol"] == "BTCUSDT"
    assert by_market["overall"]["metric_sources"]["min_stop_pct"]["symbol"] == "USDJPY"
