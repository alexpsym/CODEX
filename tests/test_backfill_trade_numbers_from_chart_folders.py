from pathlib import Path
import zipfile

from tools.backfill_trade_numbers_from_chart_folders import run_backfill
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
