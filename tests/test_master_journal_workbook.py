from pathlib import Path
from openpyxl import load_workbook
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, stable_row_id, SHEET_ORDER


def sample_snapshot():
    return {
        'updated_at': '2026-05-10T00:00:00Z',
        'items': [
            {'id':'t1','row_type':'trade','symbol':'EURUSD','side':'BUY','close_time':'2026-05-01T00:00:00Z','open_time':'2026-05-01T00:00:00Z','net_profit':10.0,'entry_price':1.1,'exit_price':1.2,'qty':1,'account':'A','is_test_trade':False,'setup':'S1','timeframe':'H1','breakeven':'No','notes':'N1'},
            {'id':'t2','row_type':'trade','symbol':'BTCUSDT','side':'SELL','close_time':'2026-05-02T00:00:00Z','open_time':'2026-05-02T00:00:00Z','net_profit':-5.0,'entry_price':100,'exit_price':90,'qty':2,'account':'A','is_test_trade':True,'setup':'S2','timeframe':'M15','breakeven':'Yes','notes':'N2'},
        ],
        'balances':[{'label':'A','balance':1000}],
        'diagnostics':{'local_workbook_names':['A.xlsx'],'errors':['e1']},
    }


def test_sheet_order_and_content(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'
    build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out)
    assert wb.sheetnames == SHEET_ORDER
    assert wb['Instrument Averages'].max_row > 1
    assert wb['P&L Calendar'].max_row > 2
    assert wb['Equity Curve'].max_row > 1
    assert wb['Diagnostics'].max_row > 3


def test_all_trades_columns_hidden_id_validation_and_colors(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'
    build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out)
    ws=wb['All Trades']
    headers=[c.value for c in ws[1]]
    for col in ['Test','Setup','Timeframe','Breakeven','Notes','__row_id','Stop Loss','Target','Commission','Profit %','R-Multiple','Balance After','Trade Duration (s)','Source','Order ID','Fill Count']:
        assert col in headers
    assert ws.column_dimensions['A'].hidden is True
    assert ws.data_validations.count >= 1
    pos=str(ws.cell(2,13).font.color.rgb) if ws.cell(2,13).font.color else ''
    neg=str(ws.cell(3,13).font.color.rgb) if ws.cell(3,13).font.color else ''
    assert '008000' in pos
    assert 'FF0000' in neg


def test_manual_overrides_only_whitelist_and_blank_preserved(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'
    build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out)
    ws=wb['All Trades']
    ws['U2']='Yes'; ws['V2']=''; ws['W2']='H4'; ws['X2']=''; ws['Y2']=''
    ws['D2']='CHANGED_ACCOUNT'
    wb.save(out)
    o=read_master_journal_manual_overrides(out)
    assert o['t1']['is_test_trade'] is True
    assert o['t1']['setup'] == ''
    assert o['t1']['timeframe'] == 'H4'
    assert o['t1']['breakeven'] == ''
    assert o['t1']['notes'] == ''
    assert 'account' not in o['t1']


def test_stable_row_id_behavior():
    row={'id':'abc'}
    assert stable_row_id(row)=='abc'
    base={'symbol':'EURUSD','side':'BUY','open_time':'1','close_time':'2','qty':1,'entry_price':1.1,'exit_price':1.2,'net_profit':1,'account':'A','raw_refs':{'source_row':1,'sheet':'S'}}
    a=stable_row_id(base)
    b=stable_row_id(dict(base))
    c=stable_row_id({**base,'raw_refs':{'source_row':2,'sheet':'S'}})
    assert a==b
    assert a!=c


def test_gross_loss_is_red(tmp_path: Path):
    snap=sample_snapshot(); snap['stats']={'totals':{'gross_loss':5,'gross_gain':10,'net_profit_total':5}}
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(snap,out)
    wb=load_workbook(out); ws=wb['Dashboard']
    color=None
    for r in range(1,40):
        if ws.cell(r,1).value=='Gross Loss':
            color = ws.cell(r,2).font.color.rgb if ws.cell(r,2).font.color else ''
            break
    assert 'FF0000' in str(color)


def test_section_sheets_have_filters_and_freeze(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'
    build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out)
    for name in ['All Trades','Instrument Averages','P&L Calendar','Equity Curve']:
        ws=wb[name]
        assert ws.freeze_panes == 'A2'
        assert ws.auto_filter.ref


def test_is_test_trade_string_handling(tmp_path: Path):
    snap=sample_snapshot()
    snap['items'][0]['is_test_trade']='No'
    snap['items'][1]['is_test_trade']='Yes'
    out=tmp_path/'Master Journal.xlsx'
    build_master_journal_workbook(snap, out)
    wb=load_workbook(out)
    ws=wb['All Trades']
    assert ws['U2'].value == 'No'
    assert ws['U3'].value == 'Yes'
    inst=wb['Instrument Averages']
    symbols=[inst.cell(r,1).value for r in range(2,inst.max_row+1)]
    assert 'EURUSD' in symbols
    assert 'BTCUSDT' not in symbols


def test_section_pnl_colors(tmp_path: Path):
    snap=sample_snapshot(); snap['items'].append({'id':'t3','row_type':'trade','symbol':'XAUUSD','side':'BUY','close_time':'2026-05-03T00:00:00Z','open_time':'2026-05-03T00:00:00Z','net_profit':-7.0,'entry_price':1,'exit_price':1,'qty':1,'account':'A','is_test_trade':False})
    outp=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(snap,outp)
    wb=load_workbook(outp)
    inst=wb['Instrument Averages']
    values=[inst.cell(r,5).value for r in range(2,inst.max_row+1)]
    cal=wb['P&L Calendar']
    cal_vals=[cal.cell(r,3).value for r in range(2,cal.max_row+1)]
    eq=wb['Equity Curve']
    eq_vals=[eq.cell(r,2).value for r in range(2,eq.max_row+1)]
    assert any(v < 0 for v in values if isinstance(v,(int,float)))
    assert any(v < 0 for v in cal_vals if isinstance(v,(int,float)))
    assert any(v < 0 for v in eq_vals if isinstance(v,(int,float)))
