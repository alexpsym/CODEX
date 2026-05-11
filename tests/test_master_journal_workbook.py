from pathlib import Path
from openpyxl import load_workbook
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, SHEET_ORDER


def sample_snapshot():
    return {
        'updated_at':'2026-05-10T00:00:00Z',
        'items':[
            {'id':'t1','row_type':'trade','symbol':'EURUSD','asset_class':'fx','side':'BUY','open_time':'2026-05-01T00:00:00Z','close_time':'2026-05-01T01:00:00Z','net_profit':120.5,'result_pct':2.3,'r_multiple':1.2,'stop_loss':1.09,'take_profit':1.12,'entry_price':1.1,'trade_duration_seconds':3700,'analysis_balance_after_trade':1000,'account':'OANDA DEMO','setup':'S1'},
            {'id':'t2','row_type':'trade','symbol':'BTCUSDT','asset_class':'crypto','side':'SELL','open_time':'2026-05-02T00:00:00Z','close_time':'2026-05-02T02:00:00Z','net_profit':-50.0,'result_pct':-1.1,'r_multiple':-0.8,'stop_loss':61000,'take_profit':59000,'entry_price':60000,'trade_duration_seconds':7215,'analysis_balance_after_trade':950,'account':'BYBIT','setup':'S2'},
        ],
        'stats':{'totals':{'trades':2,'wins':1,'losses':1,'break_even':0,'win_rate_pct':50.0,'net_profit_total':70.5,'gross_gain':120.5,'gross_loss':50.0,'money_by_currency':{'net_profit_total':{'AUD':70.5},'gross_gain':{'AUD':120.5},'gross_loss':{'AUD':50.0},'max_gain':{'AUD':120.5},'max_loss':{'AUD':50.0}}},'groups':{'by_market':{'overall':{'trades':2,'wins':1,'losses':1,'break_even':0,'win_rate_pct':50.0,'net_profit_total':70.5,'gross_gain':120.5,'gross_loss':50.0,'avg_result_pct':0.6,'min_result_pct':-1.1,'max_result_pct':2.3,'avg_r_multiple':0.2,'min_r_multiple':-0.8,'max_r_multiple':1.2,'max_gain':120.5,'max_loss':50.0,'avg_stop_pct':1.1,'avg_target_pct':2.2,'avg_duration_seconds':5457,'money_by_currency':{'net_profit_total':{'AUD':70.5},'gross_gain':{'AUD':120.5},'gross_loss':{'AUD':50.0},'max_gain':{'AUD':120.5},'max_loss':{'AUD':50.0}},'metric_sources':{'min_result_pct':{'symbol':'BTCUSDT','date':'2026-05-02'},'max_result_pct':{'symbol':'EURUSD','date':'2026-05-01'}}},'fx':{},'crypto':{}},'risk_expectancy':{'avg_stop_pct_winners':1,'avg_stop_pct_losers':2,'avg_target_pct_winners':3,'avg_target_pct_losers':4,'avg_result_pct_winners':2.3,'avg_result_pct_losers':-1.1,'avg_r_multiple_winners':1.2,'avg_r_multiple_losers':-0.8,'max_drawdown_pct':5,'avg_drawdown_pct':2},'duration':{'overall_avg_seconds':5457,'overall_shortest_seconds':3700,'overall_longest_seconds':7215,'fx_shortest_seconds':3700,'fx_longest_seconds':3700,'crypto_shortest_seconds':7215,'crypto_longest_seconds':7215},'leaders':{}},'by_instrument':[{'symbol':'EURUSD','asset_class':'fx','total_trades':1,'long_trades':1,'short_trades':0,'wins':1,'losses':0,'break_even':0,'net_profit_total':120.5,'avg_net_profit':120.5,'win_rate_pct':100,'avg_sl_pct_wins':1,'avg_sl_pct_losses':None,'avg_tp_pct_wins':2,'avg_tp_pct_losses':None,'avg_trade_duration_seconds':3700,'min_trade_duration_seconds':3700,'max_trade_duration_seconds':3700}]}
    }


def test_single_builder_definition():
    src = Path('tools/master_journal_workbook.py').read_text(encoding='utf-8')
    assert src.count('def build_master_journal_workbook') == 1


def test_dashboard_parity_and_equity(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert wb.sheetnames == SHEET_ORDER
    vals=[str(wb['Dashboard'].cell(r,c).value or '') for r in range(1,220) for c in range(1,13)]
    assert 'Account Balances' not in vals and 'Main Stats' not in vals and 'Label' not in vals
    for label in ['Overall','Winners','Losers','Drawdown','Duration','FX','Crypto','Instrument leaders','Win rate','Avg R','Max R loss','Max R win']:
        assert label in vals
    assert any(v.endswith('%') for v in vals if '50.00%' in v or '2.30%' in v)
    assert any(v.endswith('R') for v in vals if 'R' in v)
    assert any(v.startswith('AUD ') for v in vals)
    assert any('hour' in v or 'minute' in v or 'second' in v for v in vals)
    assert any('· 2026-05-0' in v for v in vals)
    eq=wb['Equity Curve']; assert eq['A1'].value=='Date'; assert eq.max_column>=2


def test_manual_override_roundtrip(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']; ws['Q2']='Yes'; ws['R2']='AAA'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov['t1']['is_test_trade'] is True and ov['t1']['setup']=='AAA'

def test_calendar_month_fill_colors(tmp_path: Path):
    snap=sample_snapshot()
    snap['items']=[
        {'id':'p','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'is_test_trade':False},
        {'id':'n','row_type':'trade','account':'A','open_time':'2026-06-01','close_time':'2026-06-01','net_profit':-5,'is_test_trade':False},
    ]
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(snap,out); wb=load_workbook(out)
    cal=wb['P&L Calendar']
    may=cal['F2']; jun=cal['G2']; mar=cal['D2']
    assert 'P/L' in str(may.value or '') and 'P/L' in str(jun.value or '')
    assert may.fill.fgColor.rgb != jun.fill.fgColor.rgb
    assert may.fill.fgColor.rgb not in {'0014532D', '004F1D1D', '00111C2D'}
    assert jun.fill.fgColor.rgb not in {'0014532D', '004F1D1D', '00111C2D'}
    assert mar.value in ('', None)
    heights=[cal.row_dimensions[r].height for r in range(2, cal.max_row+1)]
    assert len(set(heights)) == 1


def test_equity_curve_carry_forward_and_chart_series(tmp_path: Path):
    s=sample_snapshot()
    s['items']=[
        {'id':'a1','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'analysis_balance_after_trade':100},
        {'id':'b1','row_type':'trade','account':'B','open_time':'2026-05-02','close_time':'2026-05-02','net_profit':5,'balance_after_trade':50},
        {'id':'b2','row_type':'trade','account':'B','open_time':'2026-05-03','close_time':'2026-05-03','net_profit':4},
        {'id':'a2','row_type':'trade','account':'A','open_time':'2026-05-03','close_time':'2026-05-03','net_profit':2},
    ]
    out=tmp_path/'m.xlsx'; build_master_journal_workbook(s,out); wb=load_workbook(out)
    eq=wb['Equity Curve']
    assert eq['A1'].value=='Date' and eq.max_column==3
    assert eq['C2'].value in ('',None)
    assert len(eq._charts)==2
    assert all(len(ch.series)==1 for ch in eq._charts)


def test_equity_curve_insufficient_points_shows_message(tmp_path: Path):
    s=sample_snapshot(); s['items']=[{'id':'a1','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':1,'is_test_trade':False}]
    out=tmp_path/'m2.xlsx'; build_master_journal_workbook(s,out); wb=load_workbook(out)
    eq=wb['Equity Curve']
    assert eq['A3'].value=='Not enough equity data to chart.'
    assert len(eq._charts)==0

def test_unanchored_account_does_not_fabricate_equity(tmp_path: Path):
    s=sample_snapshot()
    s['items']=[
        {'id':'u1','row_type':'trade','account':'U','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10},
        {'id':'u2','row_type':'trade','account':'U','open_time':'2026-05-02','close_time':'2026-05-02','net_profit':5},
    ]
    out=tmp_path/'u.xlsx'; build_master_journal_workbook(s,out); wb=load_workbook(out)
    ws=wb['All Trades']
    assert ws['O2'].value in ('', None)
    assert ws['O3'].value in ('', None)
    eq=wb['Equity Curve']
    assert eq['B2'].value in ('', None)
    assert eq['B3'].value in ('', None)
    assert len(eq._charts)==0


def test_all_trades_hidden_row_id_and_override_after_row_swap(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']
    headers=[ws.cell(1,c).value for c in range(1,ws.max_column+1) if not ws.column_dimensions[ws.cell(1,c).column_letter].hidden]
    assert '__row_id' not in headers
    # swap rows with row_id value to simulate sorted move preserving attached hidden col
    for c in range(1, ws.max_column+1):
        ws.cell(2,c).value, ws.cell(3,c).value = ws.cell(3,c).value, ws.cell(2,c).value
    ws['Q2']='Yes'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert any(v.get('is_test_trade') is True for v in ov.values())

def test_balance_after_resolution_and_duration_display(tmp_path: Path):
    s=sample_snapshot()
    s['items'] = [
        {'id':'t1','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'analysis_balance_after_trade':100,'trade_duration_seconds':41},
        {'id':'t2','row_type':'trade','account':'A','open_time':'2026-05-02','close_time':'2026-05-02','net_profit':5,'trade_duration_seconds':303},
        {'id':'t3','row_type':'trade','account':'B','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':3,'trade_duration_seconds':3661},
    ]
    out=tmp_path/'m3.xlsx'; build_master_journal_workbook(s,out); wb=load_workbook(out)
    ws=wb['All Trades']
    assert ws['O2'].value == 100
    assert ws['O3'].value == 105
    assert ws['O4'].value in ("", None)
    assert ws['P1'].value == 'Trade Duration'
    assert ws['P2'].value == '41 seconds'
    assert ws['P3'].value == '5 minutes, 3 seconds'
    inst=wb['Instrument Averages']
    assert 'hour' in str(inst['V2'].value)
    assert 'hour' in str(inst['W2'].value)
    assert 'hour' in str(inst['X2'].value)

def test_sheet_order_and_hidden_meta(tmp_path: Path):
    out=tmp_path/'x.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert 'Diagnostics' not in SHEET_ORDER
    assert wb.sheetnames == SHEET_ORDER
    assert '_Trade Meta' in wb.sheetnames
    assert wb['_Trade Meta'].sheet_state == 'hidden'

def test_dashboard_layout_style_columns(tmp_path: Path):
    out=tmp_path/'db.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    dash=wb['Dashboard']
    assert dash['A1'].fill.fgColor.type != 'rgb' or dash['A1'].fill.fgColor.rgb != '000B1220'
    assert dash['I2'].value != 'Instrument leaders'
    positions={(r,c):dash.cell(r,c).value for r in range(1,80) for c in range(1,13)}
    duration_pos=[k for k,v in positions.items() if v=='Duration'][0]
    assert any(
        r.min_row == duration_pos[0] and r.min_col == duration_pos[1] and r.max_col == duration_pos[1] + 2
        for r in dash.merged_cells.ranges
    )
