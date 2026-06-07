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

from render.master_service import _compute_journal_stats, _build_journal_balance_timelines


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


def test_compute_journal_stats_no_zero_count_leaders() -> None:
    rows = [{"row_type": "trade", "asset_class": "fx", "symbol": "EURUSD", "result_pct": 0.0, "r_multiple": 0.0, "net_profit": 0}]
    stats = _compute_journal_stats(rows, balances=[])
    leaders = stats["groups"]["leaders"]
    assert leaders["most_wins_instrument"] is None
    assert leaders["most_losses_instrument"] is None


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



def test_market_return_percentage_uses_current_balance_and_funded_capital() -> None:
    rows = [
        {
            "row_type": "trade",
            "asset_class": "fx",
            "account": "OANDA DEMO",
            "symbol": "EURUSD",
            "net_profit": -100.0,
            "result_pct": -80.0,
            "currency": "AUD",
        }
    ]
    stats = _compute_journal_stats(
        rows,
        balances=[{"account": "OANDA DEMO", "balance": 900.0, "currency": "AUD"}],
    )
    fx = stats["groups"]["by_market"]["fx"]
    assert fx["starting_or_funded_capital"] == 1000.0
    assert fx["market_return_pct"] == pytest.approx(-10.0)
    assert fx["gross_gain_return_pct"] == pytest.approx(0.0)
    assert fx["gross_loss_return_pct"] == pytest.approx(10.0)


def test_market_return_percentage_can_represent_total_capital_loss() -> None:
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
    assert crypto["starting_or_funded_capital"] == 100.0
    assert crypto["market_return_pct"] == pytest.approx(-100.0)
    assert crypto["gross_loss_return_pct"] == pytest.approx(100.0)


def test_market_return_percentage_is_blank_for_mixed_currencies() -> None:
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
    assert fx["return_unavailable_reason"] == "mixed_currency"
