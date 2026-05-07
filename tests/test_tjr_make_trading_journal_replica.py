from pathlib import Path
from openpyxl import Workbook, load_workbook

from TJR.make_trading_journal_replica import list_source_workbooks, parse_workbook, compute_journal_stats_replica, build_output

HEADERS = [
    "opening_time","closing_time","type_buy_sell","symbol","size_quantity","entry_price","closing_price","stop_loss","take_profit","commission","net_profit","balance_after_trade","timeframe","is_test_trade","currency","notes","order_id","fill_count","source"
]

def make_bybit(path: Path):
    wb = Workbook(); ws = wb.active; ws.title = "Trades"; ws.append(HEADERS)
    ws.append(["2026-01-01 10:00","2026-01-01 11:00","Buy","HYPEUSDT",1,10,9,8,12,0.1,-1,"", "1h",False,"USDT","n1","o1",2,"bybit"])
    ws.append(["2026-01-02 10:00","2026-01-02 11:00","Buy","LABUSDT",1,10,11,9,13,0.1,1,101, "1h",False,"USDT","n2","o2",1,"bybit"])
    ws.append(["2026-01-03 10:00","2026-01-03 11:00","Sell","BTCUSDT",1,100,101,102,98,0.1,-1,100, "1h",True,"USDT","n3","o3",3,"bybit"])
    wb.save(path)

def test_bybit_included_and_parse(tmp_path: Path):
    journal = tmp_path / "journal"; journal.mkdir()
    p = journal / "BYBIT DEMO.xlsx"; make_bybit(p)
    files = list_source_workbooks(journal)
    assert p in files
    rows, warnings = parse_workbook(p)
    assert not warnings
    assert len(rows) == 3
    assert all(r["account"] == "Bybit Demo" for r in rows)
    assert all(r["asset_class"] == "Crypto" for r in rows)
    assert all(r["currency"] == "USDT" for r in rows)
    assert rows[0]["order_id"] == "o1"
    assert rows[0]["fill_count"] == "2"
    assert rows[0]["import_source"] == "bybit"

def test_stats_exclude_test_rows(tmp_path: Path):
    p = tmp_path / "BYBIT DEMO.xlsx"; make_bybit(p)
    rows, _ = parse_workbook(p)
    stats = compute_journal_stats_replica(rows)
    assert len(rows) == 3
    assert stats["totals"]["trades"] == 2

def test_output_headers_and_sheets(tmp_path: Path):
    journal = tmp_path / "journal"; journal.mkdir()
    make_bybit(journal / "BYBIT DEMO.xlsx")
    out = tmp_path / "TradingJournal_Android_Replica.xlsx"
    build_output(journal, out)
    wb = load_workbook(out)
    for name in ["Dashboard","All Trades","Instrument Averages","PL Calendar","Equity Curve","Diagnostics"]:
        assert name in wb.sheetnames
    headers = [c.value for c in wb["All Trades"][1]]
    for h in ["Test","Profit %","R-Multiple","Trade Duration","Order ID","Fill Count"]:
        assert h in headers
    iheaders = [c.value for c in wb["Instrument Averages"][1]]
    for h in ["Long Trades","Short Trades","Avg SL W","Avg TP L","Shortest Duration","Longest Duration"]:
        assert h in iheaders
    dvals = [wb["Diagnostics"].cell(r,1).value for r in range(1, wb["Diagnostics"].max_row+1)]
    assert "Bybit Demo parsed row count" in dvals
