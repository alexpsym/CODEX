from pathlib import Path
from openpyxl import load_workbook
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, stable_row_id, SHEET_ORDER


def sample_snapshot():
    return {
        'updated_at': '2026-05-10T00:00:00Z',
        'items': [
            {'id':'t1','row_type':'trade','symbol':'EURUSD','asset_class':'fx','side':'BUY','close_time':'2026-05-01T00:00:00Z','open_time':'2026-05-01T00:00:00Z','net_profit':10.0,'entry_price':1.1,'exit_price':1.2,'qty':1,'account':'A','is_test_trade':False,'source':'very long source '*20,'setup':'S1','timeframe':'H1','breakeven':'No','notes':'N1 '*80},
            {'id':'t2','row_type':'trade','symbol':'BTCUSDT','asset_class':'crypto','side':'SELL','close_time':'2026-05-02T00:00:00Z','open_time':'2026-05-02T00:00:00Z','net_profit':-5.0,'entry_price':100,'exit_price':90,'qty':2,'account':'A','is_test_trade':False,'setup':'S2','timeframe':'M15','breakeven':'Yes','notes':'N2'},
        ],
        'balances':[{'label':'A','balance':1000}],
        'stats': {
            'totals': {'trades':2,'wins':1,'losses':1,'break_even':0,'win_rate_pct':50,'net_profit_total':5,'gross_gain':10,'gross_loss':5,'min_loss':-5,'max_gain':10},
            'groups': {'risk_expectancy': {'avg_result_pct':1,'avg_r_multiple':0.5,'avg_stop_pct_winners':1,'avg_stop_pct_losers':2,'avg_target_pct_winners':3,'avg_target_pct_losers':4,'avg_result_pct_winners':2,'avg_result_pct_losers':-1,'avg_r_multiple_winners':1.0,'avg_r_multiple_losers':-1.0,'max_drawdown_pct':3,'avg_drawdown_pct':2},
                       'by_market': {'overall': {'min_result_pct':-3,'max_result_pct':4,'min_r_multiple':-1.5,'max_r_multiple':2.2}},
                       'duration': {'overall_avg_seconds':120,'overall_shortest_seconds':60,'overall_longest_seconds':180,'fx_shortest_seconds':60,'fx_longest_seconds':100,'crypto_shortest_seconds':80,'crypto_longest_seconds':180},
                       'leaders': {'overall_most_wins_instrument':'EURUSD','overall_most_losses_instrument':'BTCUSDT','fx_most_wins_instrument':'EURUSD','fx_most_losses_instrument':'EURUSD','crypto_most_wins_instrument':'BTCUSDT','crypto_most_losses_instrument':'BTCUSDT'}},
            'by_instrument': [{'symbol':'EURUSD','asset_class':'fx','trades':1,'longs':1,'shorts':0,'wins':1,'losses':0,'break_even':0,'long_wins':1,'long_losses':0,'short_wins':0,'short_losses':0,'net_profit_total':10,'avg_net_profit':10,'win_rate_pct':100,'avg_sl_distance_pips_wins':50,'avg_sl_distance_pips_losses':0,'avg_tp_distance_pips_wins':100,'avg_tp_distance_pips_losses':0,'avg_duration_seconds':3600,'shortest_duration_seconds':3600,'longest_duration_seconds':3600}]
        },
        'diagnostics':{'local_workbook_names':['A.xlsx'],'errors':['e1']},
    }


def test_parity_layout_and_features(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert wb.sheetnames == SHEET_ORDER
    dash=wb['Dashboard']
    vals=[str(dash.cell(r,c).value or '') for r in range(1,120) for c in range(1,13)]
    for label in ['Account Balances','Overall','Winners','Losers','Drawdown','Duration','FX','Crypto','Instrument leaders','Avg result %','Avg R','Max drawdown','Avg duration','Overall most wins']:
        assert label in vals
    assert sum(1 for v in vals if v.strip()) > 25

    ws=wb['All Trades']
    assert ws.row_dimensions[2].height <= 24
    assert ws['R2'].alignment.wrap_text is not True
    assert ws['Y2'].alignment.wrap_text is not True
    assert 'N1' in ws['Y2'].value

    inst=wb['Instrument Averages']
    headers=[inst.cell(1,c).value for c in range(1,inst.max_column+1)]
    for h in ['Symbol','Class','Trades','Longs','Shorts','Wins','Losses','Break-even','Long wins','Long losses','Short wins','Short losses','Net P/L','Avg P/L','Win Rate %','Avg stop dist (W)','Avg stop dist (L)','Avg target dist (W)','Avg target dist (L)','Avg duration','Shortest','Longest']:
        assert h in headers
    assert inst['A2'].value == 'EURUSD'

    cal=wb['P&L Calendar']
    cvals=[str(cal.cell(r,c).value or '') for r in range(1,120) for c in range(1,8)]
    for d in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']:
        assert d in cvals
    assert any('2026' in v for v in cvals)
    assert any('P/L' in v for v in cvals)
    fills=[cal.cell(r,c).fill.fgColor.rgb for r in range(1,120) for c in range(1,8)]
    assert any(x in str(f) for f in fills for x in ['DCFCE7','FEE2E2'])

    eq=wb['Equity Curve']
    assert len(eq._charts) >= 1
    assert eq.max_row > 1


def test_manual_overrides_and_stable_id(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']
    ws['U2']='Yes'; ws['V2']=''; ws['W2']='H4'; ws['X2']=''; ws['Y2']=''; ws['D2']='CHANGED_ACCOUNT'; wb.save(out)
    o=read_master_journal_manual_overrides(out)
    assert o['t1']['is_test_trade'] is True
    assert o['t1']['setup'] == ''
    assert o['t1']['timeframe'] == 'H4'
    assert o['t1']['breakeven'] == ''
    assert o['t1']['notes'] == ''
    assert stable_row_id({'id':'abc'}) == 'abc'
