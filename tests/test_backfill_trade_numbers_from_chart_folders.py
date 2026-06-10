from pathlib import Path
import zipfile

from tools.backfill_trade_numbers_from_chart_folders import _repo_market, parse_chart_folders, run_backfill
from tools.master_journal_workbook import build_master_journal_workbook


def test_chart_folder_backfill_uses_summary_and_reports_unmatched(tmp_path: Path):
    journal = tmp_path / "Trading Journal.xlsx"
    charts = tmp_path / "charts.zip"
    build_master_journal_workbook({
        "items": [{
            "id": "tai", "row_type": "trade", "account": "BYBIT", "asset_class": "crypto",
            "symbol": "TAIUSDT", "side": "SELL", "open_time": "2025-04-30 02:09:00",
            "close_time": "2025-04-30 04:50:00", "entry_price": 0.101636,
            "stop_loss": 0.106024, "take_profit": 0.09286,
        }],
        "stats": {"totals": {}, "groups": {}},
        "balances": [],
    }, journal)
    with zipfile.ZipFile(charts, "w") as archive:
        archive.writestr("CRYPTO/2025/C351 XRP/1.png", b"")
        archive.writestr(
            "CRYPTO/2025/C352 TAI/trade_summary.txt",
            "Symbol: TAIUSDT\nDirection: Short\nEntry Price: 0.101636 USDT\n"
            "Stop Price: 0.106024 USDT\nTarget Price: 0.09286 USDT\n",
        )
        archive.writestr("CRYPTO/2025/C353 EOS/EOSUSDT_2025-05-18_00-48-28.png", b"")

    summary = run_backfill(journal, charts)
    assert summary["high_confidence_matches"] == 1
    assert summary["matches"][0]["trade_number"] == "C352"
    assert {item["trade_number"] for item in summary["unmatched_folders"]} == {"C351", "C353"}


def test_parse_forex_nested_crypto_and_skip_2026(tmp_path: Path):
    assert _repo_market("OANDA LIVE", "USDCHF") == "forex"
    assert _repo_market("BYBIT LIVE", "USDCUSDT") == "crypto"
    charts = tmp_path / "mixed.zip"
    with zipfile.ZipFile(charts, "w") as archive:
        archive.writestr("FOREX/2025/F1029 EURUSD/EURUSD_2025-11-06.png", b"")
        archive.writestr("CRYPTO/2020/APR/C11/2/chart.png", b"")
        archive.writestr("CRYPTO/2025/C352 TAI/chart.png", b"")
        archive.writestr("FOREX/2026/F1030 GBPUSD/chart.png", b"")

    diagnostics = {}
    folders = parse_chart_folders(charts, diagnostics)
    parsed = {(folder.market, folder.year, folder.trade_number, folder.symbol) for folder in folders}
    assert ("forex", 2025, "F1029", "EURUSD") in parsed
    assert ("crypto", 2020, "C2", "") in parsed
    assert ("crypto", 2025, "C352", "TAIUSDT") in parsed
    assert diagnostics["folders_skipped_2026_plus"] == 1


def test_repeated_crypto_labels_without_symbol_or_date_remain_unmatched(tmp_path: Path):
    journal = tmp_path / "Trading Journal.xlsx"
    charts = tmp_path / "charts.zip"
    build_master_journal_workbook({
        "items": [
            {
                "id": "a", "row_type": "trade", "account": "BYBIT", "asset_class": "crypto",
                "symbol": "BTCUSDT", "side": "BUY", "open_time": "2020-01-01",
                "close_time": "2020-01-01",
            },
            {
                "id": "b", "row_type": "trade", "account": "BYBIT", "asset_class": "crypto",
                "symbol": "ETHUSDT", "side": "BUY", "open_time": "2021-01-01",
                "close_time": "2021-01-01",
            },
        ],
        "stats": {"totals": {}, "groups": {}},
        "balances": [],
    }, journal)
    with zipfile.ZipFile(charts, "w") as archive:
        archive.writestr("CRYPTO/2020/C1/chart.png", b"")
        archive.writestr("CRYPTO/2021/C1/chart.png", b"")

    summary = run_backfill(journal, charts)
    assert summary["high_confidence_matches"] == 0
    assert summary["ambiguous"] == 0
    assert summary["unmatched"] == 2
    assert all(item["reason"] == "insufficient_discriminator" for item in summary["unmatched_folders"])
