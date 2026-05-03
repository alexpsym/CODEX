import pytest

pytest.importorskip("httpx")

from render.master_service import _compute_journal_stats


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
        },
    ]

    stats = _compute_journal_stats(rows, balances=[])
    risk = stats["groups"]["risk_expectancy"]
    duration = stats["groups"]["duration"]

    assert risk["avg_stop_pct_winners"] == 7.5
    assert risk["avg_stop_pct_losers"] == 10.0
    assert risk["avg_target_pct_winners"] == 15.0
    assert risk["avg_target_pct_losers"] == 15.0
    assert risk["avg_result_pct_winners"] == 3.0
    assert risk["avg_result_pct_losers"] == -1.5
    assert risk["avg_r_multiple_winners"] == 1.6
    assert risk["avg_r_multiple_losers"] == -0.75

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
