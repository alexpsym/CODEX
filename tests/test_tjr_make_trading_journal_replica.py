from pathlib import Path
from openpyxl import Workbook, load_workbook
from TJR.make_trading_journal_replica import parse_workbook, compute_journal_stats_replica, build_output, instrument_display_rows

H=["opening_time","closing_time","type_buy_sell","symbol","size_quantity","entry_price","closing_price","stop_loss","take_profit","commission","net_profit","balance_after_trade","timeframe","is_test_trade","currency","notes","order_id","fill_count","source"]

def make_file(path, rows):
    wb=Workbook();ws=wb.active;ws.title='Trades';ws.append(H)
    for r in rows: ws.append(r)
    wb.save(path)

def mk(o,c,side,sym,pnl,bal,test=False,cur='USDT',sl=90,tp=110):
    return [o,c,side,sym,1,100,101 if pnl<0 else 110,sl,tp,0,pnl,bal,'1h',test,cur,'','o',1,'s']

def test_long_short_breakdown_is_calculated():
    rows=[
        {"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":10},
        {"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":-2},
        {"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":0},
        {"symbol":"BTCUSDT","asset_class":"Crypto","side":"SELL","net_profit":3},
        {"symbol":"BTCUSDT","asset_class":"Crypto","side":"SELL","net_profit":-1},
        {"symbol":"BTCUSDT","asset_class":"Crypto","side":"SELL","net_profit":0},
    ]
    s=compute_journal_stats_replica(rows)["totals"]
    assert (s["long_wins"],s["long_losses"],s["long_break_even"],s["short_wins"],s["short_losses"],s["short_break_even"])==(1,1,1,1,1,1)

def test_fx_crypto_win_rates_are_calculated():
    rows=[{"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":1},{"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":-1},{"symbol":"BTCUSDT","asset_class":"Crypto","side":"BUY","net_profit":2},{"symbol":"ETHUSDT","asset_class":"Crypto","side":"BUY","net_profit":-2}]
    s=compute_journal_stats_replica(rows)["totals"]
    assert s["fx_win_rate_pct"]==50.0 and s["crypto_win_rate_pct"]==50.0

def test_drawdown_from_balance_after():
    rows=[{"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":1,"balance_after":1000,"close_time":__import__('datetime').datetime(2026,1,1)}, {"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":1,"balance_after":1100,"close_time":__import__('datetime').datetime(2026,1,2)}, {"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":-1,"balance_after":990,"close_time":__import__('datetime').datetime(2026,1,3)}]
    s=compute_journal_stats_replica(rows)["totals"]
    assert s["max_drawdown_pct"]==10.0

def test_streaks_are_real_objects():
    from datetime import datetime,timedelta
    rows=[];t=datetime(2026,1,1)
    for i,p in enumerate([1,1,1,-1,-1]): rows.append({"id":f"t{i}","symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":p,"close_time":t+timedelta(days=i)})
    st=compute_journal_stats_replica(rows)["groups"]["streaks"]
    assert st["longest_winning"]["trade_count"]==3 and st["longest_losing"]["trade_count"]==2 and st["longest_winning"]["trade_ids"]

def test_by_market_buckets_have_core_metrics():
    rows=[{"symbol":"EURUSD","asset_class":"FX","side":"BUY","net_profit":1,"result_pct":1,"r_multiple":1,"trade_duration_seconds":10},{"symbol":"BTCUSDT","asset_class":"Crypto","side":"BUY","net_profit":-1,"result_pct":-1,"r_multiple":-1,"trade_duration_seconds":20}]
    bm=compute_journal_stats_replica(rows)["groups"]["by_market"]
    for k in ["overall","fx","crypto"]:
        for f in ["win_rate_pct","avg_result_pct","avg_r_multiple","money_by_currency","avg_duration_seconds"]: assert f in bm[k]

def test_dashboard_writes_values_not_only_labels(tmp_path: Path):
    j=tmp_path/'journal';j.mkdir();make_file(j/'BYBIT DEMO.xlsx',[mk('2026-01-01','2026-01-01','Buy','BTCUSDT',100,1000),mk('2026-01-02','2026-01-02','Buy','BTCUSDT',100,1100),mk('2026-01-03','2026-01-03','Buy','BTCUSDT',-110,990)])
    out=tmp_path/'o.xlsx';build_output(j,out);ws=load_workbook(out)['Dashboard']
    vals=[ws.cell(r,1).value for r in range(1,ws.max_row+1)]
    assert 'Overall' in vals and 'Drawdown' in vals and 'Money by currency' in vals
    allvals={ws.cell(r,c).value for r in range(1,ws.max_row+1) for c in range(1,5)}
    assert 10.0 in allvals

def test_by_instrument_is_render_compatible(tmp_path: Path):
    p=tmp_path/'BYBIT DEMO.xlsx';make_file(p,[mk('2026-01-01','2026-01-01','Buy','BTCUSDT',10,1000)])
    rows,_=parse_workbook(p);s=compute_journal_stats_replica(rows)
    first=s['by_instrument'][0]
    assert 'symbol' in first and 'asset_class' in first and 'total_trades' in first and 'avg_trade_duration_seconds' in first
    assert ('avg_sl_distance_pips_wins' in first) or ('avg_sl_distance_quote_wins' in first)
    disp=instrument_display_rows(s['by_instrument'])[0]
    assert 'Symbol' in disp and 'Avg Duration' in disp
