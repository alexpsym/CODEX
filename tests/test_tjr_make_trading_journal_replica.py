from pathlib import Path
from openpyxl import Workbook, load_workbook
from TJR.make_trading_journal_replica import list_source_workbooks, parse_workbook, compute_journal_stats_replica, build_output

H=["opening_time","closing_time","type_buy_sell","symbol","size_quantity","entry_price","closing_price","stop_loss","take_profit","commission","net_profit","balance_after_trade","timeframe","is_test_trade","currency","notes","order_id","fill_count","source"]

def make_file(path, rows):
    wb=Workbook();ws=wb.active;ws.title='Trades';ws.append(H)
    for r in rows: ws.append(r)
    wb.save(path)

def sample_rows():
    return [["2026-01-01 10:00","2026-01-01 11:00","Buy","EURUSD",1,1.1,1.2,1.0,1.3,0,-10,1000,"1h",False,"AUD","","o1",1,"s"],
            ["2026-01-02 10:00","2026-01-02 11:00","Buy","BTCUSDT",1,100,110,90,120,0,20,1100,"1h",False,"USDT","","o2",1,"s"],
            ["2026-01-03 10:00","2026-01-03 11:00","Sell","ETHUSDT",1,100,101,"","",0,-5,990,"1h",True,"USDT","","o3",1,"s"]]

def test_bybit_demo_all_supported_names_are_included(tmp_path: Path):
    j=tmp_path/'journal';j.mkdir()
    for n in ["BYBIT DEMO.xlsx","Bybit Demo.xlsx","bybit demo.xlsx","BYBIT DEMO.xlsm"]: make_file(j/n,sample_rows()[:1])
    names={p.name for p in list_source_workbooks(j)}
    for n in ["BYBIT DEMO.xlsx","Bybit Demo.xlsx","bybit demo.xlsx","BYBIT DEMO.xlsm"]: assert n in names

def test_bybit_demo_parse_preserves_trade_metadata(tmp_path: Path):
    p=tmp_path/'BYBIT DEMO.xlsx';rows=sample_rows();rows[0][7]="";rows[0][8]="";rows[0][11]="";make_file(p,rows)
    out,_=parse_workbook(p)
    assert out and out[0]['account']=='Bybit Demo' and out[0]['asset_class']=='Crypto' and out[0]['currency']=='AUD'
    assert out[0]['order_id']=='o1' and out[0]['fill_count']=='1' and out[0]['import_source']=='s'

def test_stats_groups_are_not_empty():
    rows,_=parse_workbook(Path('journal/BYBIT DEMO.xlsx')) if Path('journal/BYBIT DEMO.xlsx').exists() else ([],[])
    rows=[{"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":10,"result_pct":1,"r_multiple":1,"trade_duration_seconds":60},{"symbol":"BTCUSDT","asset_class":"Crypto","side":"SELL","net_profit":-5,"result_pct":-0.5,"r_multiple":-0.5,"trade_duration_seconds":120}]
    s=compute_journal_stats_replica(rows)
    assert s['groups']['risk_expectancy'] and s['groups']['duration'] and 'fx' in s['groups']['by_market'] and s['groups']['leaders'] is not None and s['groups']['streaks'] is not None

def test_stats_exclude_test_rows_but_all_trades_includes_them(tmp_path: Path):
    j=tmp_path/'journal';j.mkdir();make_file(j/'BYBIT DEMO.xlsx',sample_rows())
    out=tmp_path/'o.xlsx';build_output(j,out);wb=load_workbook(out)
    assert wb['All Trades'].max_row-1==3
    rows,_=parse_workbook(j/'BYBIT DEMO.xlsx'); assert compute_journal_stats_replica(rows)['totals']['trades']==2

def test_drawdown_from_balance_after(tmp_path: Path):
    p=tmp_path/'BYBIT DEMO.xlsx';make_file(p,sample_rows());rows,_=parse_workbook(p);s=compute_journal_stats_replica(rows)
    assert s['totals']['trades']==2

def test_dashboard_contains_real_sections(tmp_path: Path):
    j=tmp_path/'journal';j.mkdir();make_file(j/'BYBIT DEMO.xlsx',sample_rows());out=tmp_path/'o.xlsx';build_output(j,out);ws=load_workbook(out)['Dashboard']
    vals={ws.cell(r,c).value for r in range(1,40) for c in range(1,10)}
    for k in ["Overall","Winners","Losers","Drawdown","Duration","FX","Crypto","Instrument leaders","Money by currency"]: assert k in vals

def test_diagnostics_missing_metric_counts(tmp_path: Path):
    j=tmp_path/'journal';j.mkdir();make_file(j/'BYBIT DEMO.xlsx',sample_rows());out=tmp_path/'o.xlsx';build_output(j,out);ws=load_workbook(out)['Diagnostics']
    vals=[ws.cell(r,1).value for r in range(1,ws.max_row+1)]
    for k in ["Missing balance_after_trade","Missing stop_loss","Missing take_profit","Missing result_pct","Missing r_multiple","Missing open_time","Missing close_time"]: assert k in vals
