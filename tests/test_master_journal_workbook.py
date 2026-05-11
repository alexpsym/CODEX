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
    assert any('h' in v or 'm' in v or 's' in v for v in vals)
    assert any('· 2026-05-0' in v for v in vals)
    eq=wb['Equity Curve']; assert eq['A1'].value=='Date'; assert eq.max_column>=2


def test_manual_override_roundtrip(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']; ws['Q2']='Yes'; ws['R2']='AAA'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov['t1']['is_test_trade'] is True and ov['t1']['setup']=='AAA'
