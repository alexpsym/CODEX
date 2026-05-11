from pathlib import Path
from openpyxl import load_workbook
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, SHEET_ORDER
from openpyxl.utils.cell import coordinate_to_tuple

def _cf_ranges(ws):
    return [str(k.sqref) for k in ws.conditional_formatting._cf_rules.keys()]

def _cell_covered(ranges, cell):
    row, col = coordinate_to_tuple(cell)
    from openpyxl.utils.cell import range_boundaries
    for rg in ranges:
        for part in rg.split():
            min_col, min_row, max_col, max_row = range_boundaries(part)
            if min_row <= row <= max_row and min_col <= col <= max_col:
                return True
    return False

def _all_rule_colors(ws):
    out = []
    for rules in ws.conditional_formatting._cf_rules.values():
        for rule in rules:
            dxf = getattr(rule, "dxf", None)
            fill = getattr(getattr(dxf, "fill", None), "fgColor", None)
            font = getattr(getattr(dxf, "font", None), "color", None)
            out.append(((fill.rgb or "") if fill else "", (font.rgb or "") if font else ""))
    return out


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
    assert any(isinstance(wb['Dashboard'].cell(r,c).value, float) for r in range(1,220) for c in range(1,13))
    assert any('AUD' in str(wb['Dashboard'].cell(r,c).number_format or '') for r in range(1,220) for c in range(1,13))
    assert any('hour' in v or 'minute' in v or 'second' in v for v in vals)
    assert any('· 2026-05-0' in v for v in vals)
    assert 'Equity Curve' not in wb.sheetnames
    ranges = _cf_ranges(wb["Dashboard"])
    assert all(not r.startswith("B1:K") for r in ranges)
    assert not _cell_covered(ranges, "B3")  # Trades count should not be profit/loss colored


def test_manual_override_roundtrip(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']; ws['Q2']='Yes'; ws['R2']='AAA'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov['t1']['is_test_trade'] is True and ov['t1']['setup']=='AAA'

def test_calendar_month_conditional_formatting_rows(tmp_path: Path):
    snap=sample_snapshot()
    snap['items']=[
        {'id':'p','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'result_pct':1.2,'is_test_trade':False},
        {'id':'n','row_type':'trade','account':'A','open_time':'2026-06-01','close_time':'2026-06-01','net_profit':-5,'result_pct':-0.4,'is_test_trade':False},
    ]
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(snap,out); wb=load_workbook(out)
    cal=wb['P&L Calendar']
    may=cal['F3']; jun=cal['G3']; mar=cal['D3']
    assert isinstance(may.value, float) and isinstance(jun.value, float)
    assert may.number_format.endswith('%')
    assert jun.number_format.endswith('%')
    ranges = _cf_ranges(cal)
    assert _cell_covered(ranges, "F3")
    assert _cell_covered(ranges, "G3")
    assert not _cell_covered(ranges, "F4")
    assert not _cell_covered(ranges, "G4")
    assert mar.value in ('', None)
    heights=[cal.row_dimensions[r].height for r in range(2, cal.max_row+1)]
    assert len(set(heights)) == 1



def test_no_equity_curve_sheet(tmp_path: Path):
    out=tmp_path/'m.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert 'Equity Curve' not in wb.sheetnames

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


def test_all_trades_hidden_row_id_and_override_after_row_swap(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['All Trades']
    headers=[ws.cell(1,c).value for c in range(1,ws.max_column+1)]
    assert '__row_id' not in headers
    assert ws.max_column == 21
    # swap rows to validate comment-backed row ids preserve manual overrides
    for c in range(1, ws.max_column+1):
        ws.cell(2,c).value, ws.cell(3,c).value = ws.cell(3,c).value, ws.cell(2,c).value
    ws['Q2']='Yes'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert any(v.get('is_test_trade') is True for v in ov.values())
    assert len(ws.conditional_formatting) > 0
    assert ws["M2"].number_format == "0.00%"
    assert ws["M2"].value in (0.023, -0.011)

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
    assert len(wb["Dashboard"].conditional_formatting) > 0
    assert len(wb["Instrument Averages"].conditional_formatting) > 0
    assert len(wb["P&L Calendar"].conditional_formatting) > 0

def test_conditional_format_colors_and_dashboard_semantics(tmp_path: Path):
    out=tmp_path/'cf.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    dash = wb["Dashboard"]
    all_trades = wb["All Trades"]
    # ensure dashboard loss magnitude cells are targeted
    positions={(r,c):str(dash.cell(r,c).value or "").strip().lower() for r in range(1,200) for c in [1,5,9]}
    for (r,c),v in positions.items():
        if v in {"gross loss","max loss","max drawdown","avg drawdown"}:
            value_col = c+1
            cell = f"{chr(64+value_col)}{r}"
            if isinstance(dash.cell(r, value_col).value, (int, float)):
                assert _cell_covered(_cf_ranges(dash), cell)
    # leaders numeric counts should not be targeted
    assert not _cell_covered(_cf_ranges(dash), "K4")
    # losses count cells should never be targeted by account-impact formatting
    loss_labels = {"losses"}
    for r in range(1, 220):
        for lc, vc in ((1, 2), (5, 6), (9, 10)):
            if str(dash.cell(r, lc).value or "").strip().lower() in loss_labels:
                assert not _cell_covered(_cf_ranges(dash), f"{chr(64+vc)}{r}")
    # all trades configured ranges exist
    tr = _cf_ranges(all_trades)
    assert any("K2:K" in r for r in tr)
    assert any("L2:N" in r for r in tr)
    colors = _all_rule_colors(all_trades) + _all_rule_colors(dash) + _all_rule_colors(wb["P&L Calendar"]) + _all_rule_colors(wb["Instrument Averages"])
    assert any("C6EFCE" in f and "006100" in c for f, c in colors)
    assert any("FFC7CE" in f and "9C0006" in c for f, c in colors)

def test_instrument_currency_and_percent_formats(tmp_path: Path):
    out=tmp_path/'fmt.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    inst = wb["Instrument Averages"]
    assert inst["Q2"].number_format == "0.00%"
    assert inst["Q2"].value == 1.0
    assert ("AUD" in (inst["O2"].number_format or "")) or ("UNKNOWN" in (inst["O2"].number_format or ""))
    assert inst["O2"].number_format != "General"

def test_dashboard_layout_style_columns(tmp_path: Path):
    out=tmp_path/'db.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    dash=wb['Dashboard']
    assert dash['A1'].fill.fgColor.type != 'rgb' or dash['A1'].fill.fgColor.rgb != '000B1220'
    assert dash['I2'].value != 'Instrument leaders'
    positions={(r,c):dash.cell(r,c).value for r in range(1,80) for c in range(1,13)}
    duration_pos=[k for k,v in positions.items() if v=='Duration'][0]
    assert any(
        r.min_row == duration_pos[0] and r.min_col == duration_pos[1] and r.max_col == duration_pos[1] + 1
        for r in dash.merged_cells.ranges
    )
