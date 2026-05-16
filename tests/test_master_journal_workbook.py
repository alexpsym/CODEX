from pathlib import Path
from openpyxl import load_workbook
import pytest
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, read_master_journal_source, update_master_journal_workbook_data_only, SHEET_ORDER
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
        'stats':{'totals':{'trades':2,'wins':1,'losses':1,'break_even':0,'win_rate_pct':50.0,'net_profit_total':70.5,'gross_gain':120.5,'gross_loss':50.0,'money_by_currency':{'net_profit_total':{'AUD':70.5},'gross_gain':{'AUD':120.5},'gross_loss':{'AUD':50.0},'max_gain':{'AUD':120.5},'max_loss':{'AUD':50.0}}},'groups':{'by_market':{'overall':{'trades':2,'wins':1,'losses':1,'break_even':0,'win_rate_pct':50.0,'net_profit_total':70.5,'gross_gain':120.5,'gross_loss':50.0,'avg_result_pct':0.6,'min_result_pct':-1.1,'max_result_pct':2.3,'avg_r_multiple':0.2,'min_r_multiple':-0.8,'max_r_multiple':1.2,'max_gain':120.5,'max_loss':50.0,'avg_stop_pct':1.1,'avg_target_pct':2.2,'avg_duration_seconds':5457,'money_by_currency':{'net_profit_total':{'AUD':70.5},'gross_gain':{'AUD':120.5},'gross_loss':{'AUD':50.0},'max_gain':{'AUD':120.5},'max_loss':{'AUD':50.0}},'metric_sources':{'min_result_pct':{'symbol':'BTCUSDT','date':'2026-05-02'},'max_result_pct':{'symbol':'EURUSD','date':'2026-05-01'}}},'fx':{},'crypto':{}},'risk_expectancy':{'avg_stop_pct_winners':1,'avg_stop_pct_losers':2,'avg_target_pct_winners':3,'avg_target_pct_losers':4,'avg_result_pct_winners':2.3,'avg_result_pct_losers':-1.1,'avg_r_multiple_winners':1.2,'avg_r_multiple_losers':-0.8,'max_drawdown_pct':5,'avg_drawdown_pct':2},'duration':{'overall_avg_seconds':5457,'overall_shortest_seconds':3700,'overall_longest_seconds':7215,'fx_shortest_seconds':3700,'fx_longest_seconds':3700,'crypto_shortest_seconds':7215,'crypto_longest_seconds':7215},'leaders':{}},'by_instrument':[{'symbol':'EURUSD','asset_class':'fx','total_trades':1,'long_trades':1,'short_trades':0,'wins':1,'losses':0,'break_even':0,'net_profit_total':120.5,'avg_net_profit':120.5,'win_rate_pct':100,'avg_sl_pct_wins':1,'avg_sl_pct_losses':None,'avg_tp_pct_wins':2,'avg_tp_pct_losses':None,'avg_trade_duration_seconds':3700,'min_trade_duration_seconds':3700,'max_trade_duration_seconds':3700}]},
        'balances':[{'account_label':'Bybit Live','balance':10.123456789,'currency':'USDT','as_of':'2026-05-10'}]
    }


def test_single_builder_definition():
    src = Path('tools/master_journal_workbook.py').read_text(encoding='utf-8')
    assert src.count('def build_master_journal_workbook') == 1


def test_dashboard_parity_and_equity(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert wb.sheetnames == SHEET_ORDER
    vals=[str(wb['Dashboard'].cell(r,c).value or '') for r in range(1,220) for c in range(1,13)]
    assert 'Account Balances' in vals and 'Main Stats' not in vals and 'Label' not in vals
    for label in ['Overall','Winners','Losers','Drawdown','Duration','FX','Crypto','Instrument leaders','Win rate','Avg R','Max R loss','Max R win']:
        assert label in vals
    assert any(isinstance(wb['Dashboard'].cell(r,c).value, float) for r in range(1,220) for c in range(1,13))
    assert any('AUD' in str(wb['Dashboard'].cell(r,c).number_format or '') for r in range(1,220) for c in range(1,13))
    assert any(
        isinstance(wb['Dashboard'].cell(r, c).value, (int, float)) and
        str(wb['Dashboard'].cell(r, c).number_format or "") == r'00\:00\:00\:00'
        for r in range(1,220) for c in range(1,13)
    )
    assert any('· 2026-05-0' in v for v in vals)
    assert 'Equity Curve' not in wb.sheetnames
    ranges = _cf_ranges(wb["Dashboard"])
    assert all(not r.startswith("B1:K") for r in ranges)
    assert not _cell_covered(ranges, "B3")  # Trades count should not be profit/loss colored


def test_manual_override_roundtrip(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']; ws['Q2']='Yes'; ws['R2']='AAA'; wb.save(out)
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
    assert cal.freeze_panes == "A3"
    for c in range(1, 14):
        assert cal.cell(1, c).font.bold is True
        assert cal.cell(2, c).font.bold is True
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
    ws=wb['Trade Log']
    assert ws['O2'].value in ('', None)
    assert ws['O3'].value in ('', None)


def test_trade_log_hidden_row_id_and_unsorted_override(tmp_path: Path):
    out=tmp_path/'Master Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']
    headers=[ws.cell(1,c).value for c in range(1,ws.max_column+1)]
    assert '__row_id' not in headers
    assert ws.max_column == 26
    assert ws["A2"].comment is None
    ws['Q2']='Yes'; ws['R2']='setup-x'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov["t1"]["is_test_trade"] is True
    assert ov["t1"]["setup"] == "setup-x"
    assert len(ws.conditional_formatting) > 0
    assert ws["M2"].number_format == "0.00%"
    assert ws["A2"].comment is None
    assert ws["M2"].value in (0.023, -0.011)


def test_legacy_comment_row_id_preferred_over_trade_meta_after_row_move(tmp_path: Path):
    out=tmp_path/'legacy.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']
    from openpyxl.comments import Comment
    ws["A2"].comment = Comment("row_id:t1", "legacy")
    ws["A3"].comment = Comment("row_id:t2", "legacy")
    for c in range(1, ws.max_column+1):
        ws.cell(2,c).value, ws.cell(3,c).value = ws.cell(3,c).value, ws.cell(2,c).value
    # stale _Trade Meta row mapping now conflicts with moved comments
        ws["R2"] = "moved-comment-target"
    wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov["t1"]["setup"] == "moved-comment-target"

def test_balance_after_resolution_and_duration_display(tmp_path: Path):
    s=sample_snapshot()
    s['items'] = [
        {'id':'t1','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'analysis_balance_after_trade':100,'trade_duration_seconds':41},
        {'id':'t2','row_type':'trade','account':'A','open_time':'2026-05-02','close_time':'2026-05-02','net_profit':5,'trade_duration_seconds':303},
        {'id':'t3','row_type':'trade','account':'B','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':3,'trade_duration_seconds':3661},
    ]
    out=tmp_path/'m3.xlsx'; build_master_journal_workbook(s,out); wb=load_workbook(out)
    ws=wb['Trade Log']
    assert ws['O2'].value == 100
    assert ws['O3'].value == 105
    assert ws['O4'].value in ("", None)
    assert ws['P1'].value == 'Trade Duration (DD:HH:MM:SS)'
    assert ws['P2'].value == 41
    assert ws['P3'].value == 503
    assert ws['P2'].number_format == r'00\:00\:00\:00'
    inst=wb['Instrument Averages']
    assert isinstance(inst['V2'].value, (int, float))
    assert isinstance(inst['W2'].value, (int, float))
    assert isinstance(inst['X2'].value, (int, float))
    assert inst['V2'].number_format == r'00\:00\:00\:00'

def test_sheet_order_and_hidden_meta(tmp_path: Path):
    out=tmp_path/'x.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    assert 'Diagnostics' not in SHEET_ORDER
    assert wb.sheetnames == SHEET_ORDER
    assert '_Trade Meta' not in wb.sheetnames
    assert len(wb["Dashboard"].conditional_formatting) > 0
    assert len(wb["Instrument Averages"].conditional_formatting) > 0
    assert len(wb["P&L Calendar"].conditional_formatting) > 0

def test_update_data_only_migrates_legacy_all_trades_and_removes_trade_meta(tmp_path: Path):
    out = tmp_path / "Master Journal.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    wb["Trade Log"].title = "All Trades"
    meta = wb.create_sheet("_Trade Meta")
    meta.sheet_state = "hidden"
    wb.save(out)
    wb.close()

    result = update_master_journal_workbook_data_only(out, snap)
    assert result["ok"] is True
    assert result["diagnostics"].get("migrated_trade_log_sheet") is True
    assert result["diagnostics"].get("removed_legacy_trade_meta") is True
    candidate = Path(result["candidate_path"])
    candidate.replace(out)

    migrated = load_workbook(out)
    assert migrated.sheetnames == ["Dashboard", "Trade Log", "Instrument Averages", "P&L Calendar"]
    assert "All Trades" not in migrated.sheetnames
    assert "_Trade Meta" not in migrated.sheetnames
    migrated.close()

def test_update_data_only_repairs_legacy_instrument_averages_freeze_pane(tmp_path: Path):
    out = tmp_path / "Master Journal.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    wb["Instrument Averages"].freeze_panes = "X111"
    wb.save(out)
    wb.close()

    result = update_master_journal_workbook_data_only(out, snap)
    assert result["ok"] is True
    assert result["diagnostics"].get("repaired_instrument_averages_freeze_pane") is True
    assert result["diagnostics"].get("previous_instrument_averages_freeze_pane") == "X111"
    Path(result["candidate_path"]).replace(out)

    repaired = load_workbook(out)
    assert repaired["Instrument Averages"].freeze_panes == "A2"
    assert repaired.sheetnames == ["Dashboard", "Trade Log", "Instrument Averages", "P&L Calendar"]
    assert "_Trade Meta" not in repaired.sheetnames
    assert "All Trades" not in repaired.sheetnames
    repaired.close()

def test_conditional_format_colors_and_dashboard_semantics(tmp_path: Path):
    out=tmp_path/'cf.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    dash = wb["Dashboard"]
    trade_log = wb["Trade Log"]
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
    # avg result % and avg R should be sign-formatted for dashboard market sections
    for wanted in {"avg result %", "avg r"}:
        hits = 0
        for r in range(1, 220):
            for lc, vc in ((1, 2), (5, 6), (9, 10)):
                if str(dash.cell(r, lc).value or "").strip().lower() == wanted and isinstance(dash.cell(r, vc).value, (int, float)):
                    hits += 1
                    assert _cell_covered(_cf_ranges(dash), f"{chr(64+vc)}{r}")
        assert hits >= 1
    # neutral stop/target metrics should not be sign-formatted
    for wanted in {"avg stop %", "avg target %"}:
        for r in range(1, 220):
            for lc, vc in ((1, 2), (5, 6), (9, 10)):
                if str(dash.cell(r, lc).value or "").strip().lower() == wanted:
                    assert not _cell_covered(_cf_ranges(dash), f"{chr(64+vc)}{r}")
    # all trades configured ranges exist
    tr = _cf_ranges(trade_log)
    assert any("K2:K" in r for r in tr)
    assert any("L2:N" in r for r in tr)
    colors = _all_rule_colors(trade_log) + _all_rule_colors(dash) + _all_rule_colors(wb["P&L Calendar"]) + _all_rule_colors(wb["Instrument Averages"])
    assert any("C6EFCE" in f and "006100" in c for f, c in colors)
    assert any("FFC7CE" in f and "9C0006" in c for f, c in colors)

def test_instrument_currency_and_percent_formats(tmp_path: Path):
    out=tmp_path/'fmt.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
    inst = wb["Instrument Averages"]
    assert inst["Q2"].number_format == "0.00%"
    assert inst["Q2"].value == 1.0
    assert inst["O2"].number_format == "0.00%"
    assert inst["O2"].number_format != "General"
    assert inst["D2"].number_format == "0;-0;;@"
    assert inst["E2"].number_format == "0;-0;;@"

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

def test_read_master_journal_source_parses_core_fields(tmp_path: Path):
    out = tmp_path / "Master Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    parsed = read_master_journal_source(out)
    trades = [r for r in parsed["items"] if r.get("row_type") == "trade"]
    assert trades
    row = trades[0]
    for key in ("id", "qty", "entry_price", "exit_price", "stop_loss", "take_profit", "net_profit", "result_pct", "r_multiple", "trade_duration_seconds", "is_test_trade"):
        assert key in row


def test_monthly_aud_row_uses_result_currency_and_excluded_from_metrics(tmp_path: Path):
    s = sample_snapshot()
    s["items"] = [
        {
            "id": "t1",
            "row_type": "trade",
            "symbol": "EURUSD",
            "account": "OANDA",
            "open_time": "2026-04-02T00:00:00+10:00",
            "close_time": "2026-04-02T01:00:00+10:00",
            "net_profit": 10,
            "result_pct": 1.0,
        },
        {
            "id": "monthly_aud_reval:bybit_live:2026-04",
            "row_type": "monthly_aud_reval",
            "account": "live",
            "account_label": "Bybit Live",
            "symbol": "MONTHLY AUD P/L",
            "open_time": "2026-04-01T00:00:00+10:00",
            "close_time": "2026-04-30T23:59:59+10:00",
            "result_cash": 123.45,
            "result_currency": "AUD",
        },
    ]
    out = tmp_path / "monthly.xlsx"
    build_master_journal_workbook(s, out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    monthly_rows = [r for r in range(2, ws.max_row + 1) if ws.cell(r, 4).value == "MONTHLY AUD P/L"]
    assert len(monthly_rows) == 1
    mr = monthly_rows[0]
    assert ws.cell(mr, 12).value == 123.45
    fmt = str(ws.cell(mr, 12).number_format or "")
    assert "AUD" in fmt
    assert "UNKNOWN" not in fmt
    # metrics remain from trade rows only
    cal = wb["P&L Calendar"]
    assert cal["E4"].value == 1  # April trades count

def test_trade_log_commission_zero_none_blank_and_nonzero(tmp_path: Path):
    s = sample_snapshot()
    s["items"] = [
        {"id":"c0","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-01","close_time":"2026-01-01","commission":0.0,"net_profit":1.0,"result_pct":1.0,"commission_currency":"AUD"},
        {"id":"c1","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-02","close_time":"2026-01-02","commission":None,"net_profit":1.0,"result_pct":1.0,"commission_currency":"AUD"},
        {"id":"c2","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-03","close_time":"2026-01-03","commission":"","net_profit":1.0,"result_pct":1.0,"commission_currency":"AUD"},
        {"id":"c3","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-04","close_time":"2026-01-04","commission":1.25,"net_profit":1.0,"result_pct":1.0,"commission_currency":"AUD"},
    ]
    out = tmp_path / "commission.xlsx"
    build_master_journal_workbook(s, out)
    ws = load_workbook(out)["Trade Log"]
    assert ws["K2"].value in ("", None)
    assert ws["K3"].value in ("", None)
    assert ws["K4"].value in ("", None)
    assert ws["K5"].value == 1.25
    assert "AUD" in str(ws["K5"].number_format or "")

def test_metric_refresh_same_row_sections_and_non_a_balance_block(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path/'mj.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='Dashboard'; wb.create_sheet('Trade Log'); wb.create_sheet('Instrument Averages')
    # anchors on same row
    ws['A1']='Overall'; ws['D1']='FX'; ws['G1']='Crypto'; ws['J1']='Winners'; ws['J8']='Losers'; ws['J14']='Drawdown'; ws['M1']='Instrument leaders'
    ws['P1']='Account Balances'
    # labels
    for base in ['A','D','G']:
        ws[f'{base}2']='Trades'; ws[f'{base}3']='Avg R'; ws[f'{base}4']='Win rate'; ws[f'{base}5']='Max loss %'; ws[f'{base}6']='Source'
    ws['P2']='Account'; ws['Q2']='Balance'; ws['R2']='Currency'; ws['S2']='As Of'; ws['P3']='Bybit Live'
    ws['B4'].number_format='0.00%'; ws['E4'].number_format='0.00%'; ws['H4'].number_format='0.00%'
    wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{'trades':1,'avg_r_multiple':2.0,'win_rate_pct':50.0,'min_result_pct':-1.25,'metric_sources':{'min_result_pct':{'symbol':'EURUSD','date':'2026-01-01'}}},'fx':{'trades':2,'avg_r_multiple':3.0,'win_rate_pct':25.0,'min_result_pct':-2.0,'metric_sources':{'min_result_pct':{'symbol':'GBPUSD','date':'2026-01-02'}}},'crypto':{'trades':3,'avg_r_multiple':4.0,'win_rate_pct':75.0,'min_result_pct':-3.0,'metric_sources':{'min_result_pct':{'symbol':'BTCUSDT','date':'2026-01-03'}}}},'risk_expectancy':{},'leaders':{}}},'balances':[{'account_label':'Bybit Live','balance':12.5,'currency':'USDT','as_of':'2026-01-04'}]}
    res = update_master_journal_workbook_data_only(p,snap); Path(res["candidate_path"]).replace(p)
    out=load_workbook(p)
    d=out['Dashboard']
    assert d['B2'].value==1 and d['E2'].value==2 and d['H2'].value==3
    assert d['B3'].value==2.0 and d['E3'].value==3.0 and d['H3'].value==4.0
    assert d['B4'].value==0.5 and d['E4'].value==0.25 and d['H4'].value==0.75
    assert d['Q3'].value==12.5 and d['R3'].value=='USDT'
    assert 'GBPUSD' in str(d['E6'].value)

def test_embedded_fx_crypto_duration_without_duration_section(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p=tmp_path/'d.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='Dashboard'; wb.create_sheet('Trade Log'); wb.create_sheet('Instrument Averages')
    ws['A1']='Overall'; ws['D1']='FX'; ws['G1']='Crypto'; ws['J1']='Winners'; ws['J8']='Losers'; ws['J14']='Drawdown'; ws['M1']='Instrument leaders'; ws['T1']='Account Balances'
    ws['D2']='FX shortest'; ws['D3']='Source'; ws['D4']='FX longest'; ws['D5']='Source'
    ws['G2']='Crypto shortest'; ws['G3']='Source'; ws['G4']='Crypto longest'; ws['G5']='Source'
    ws['T2']='Account'; ws['U2']='Balance'; ws['V2']='Currency'; ws['T3']='Bybit Live'
    wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{'fx_shortest_seconds':10,'fx_longest_seconds':20,'crypto_shortest_seconds':30,'crypto_longest_seconds':40,'metric_sources':{'fx_shortest_seconds':{'symbol':'EURUSD','date':'2026-01-01'},'fx_longest_seconds':{'symbol':'GBPUSD','date':'2026-01-02'},'crypto_shortest_seconds':{'symbol':'BTCUSDT','date':'2026-01-03'},'crypto_longest_seconds':{'symbol':'ETHUSDT','date':'2026-01-04'}}}}},'balances':[{'account_label':'Bybit Live','balance':1,'currency':'USDT'}]}
    res = update_master_journal_workbook_data_only(p,snap); Path(res["candidate_path"]).replace(p)
    out=load_workbook(p)['Dashboard']
    assert isinstance(out['E2'].value, (int, float)) and out['E2'].number_format == r'00\:00\:00\:00'
    assert isinstance(out['E4'].value, (int, float)) and out['E4'].number_format == r'00\:00\:00\:00'
    assert isinstance(out['H2'].value, (int, float)) and out['H2'].number_format == r'00\:00\:00\:00'
    assert isinstance(out['H4'].value, (int, float)) and out['H4'].number_format == r'00\:00\:00\:00'
    assert 'EURUSD' in str(out['E3'].value) and 'ETHUSDT' in str(out['H5'].value)

def test_read_master_journal_source_asset_class_regressions(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / 'asset_class.xlsx'
    wb = Workbook(); ws = wb.active; ws.title = 'Trade Log'
    headers = ['Open Time','Close Time','Account','Symbol','Side']
    ws.append(headers)
    ws.append(['2026-01-01','2026-01-01','UNKNOWN','ABCDEF','BUY'])
    ws.append(['2026-01-01','2026-01-01','UNKNOWN','ABC/DEF','BUY'])
    ws.append(['2026-01-01','2026-01-01','PEPPERSTONE LIVE','EURUSD','BUY'])
    ws.append(['2026-01-01','2026-01-01','OANDA LIVE','EUR/USD','BUY'])
    ws.append(['2026-01-01','2026-01-01','BYBIT LIVE','BTCUSD','BUY'])
    ws.append(['2026-01-01','2026-01-01','BINANCE LIVE','ETHUSD','BUY'])
    wb.save(p)
    parsed = read_master_journal_source(p)
    rows = parsed['items']
    assert rows[0]['asset_class'] == ''
    assert rows[1]['asset_class'] == ''
    assert rows[2]['asset_class'] == 'fx'
    assert rows[3]['asset_class'] == 'fx'
    assert rows[4]['asset_class'] == 'crypto'
    assert rows[5]['asset_class'] == 'crypto'

def test_instrument_leaders_updates_full_row_and_reports_missing(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p=tmp_path/'leaders.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='Dashboard'; wb.create_sheet('Trade Log'); wb.create_sheet('Instrument Averages')
    ws['A1']='Overall'; ws['D1']='FX'; ws['G1']='Crypto'; ws['J1']='Winners'; ws['J8']='Losers'; ws['J14']='Drawdown'; ws['M1']='Instrument leaders'; ws['T1']='Account Balances'
    ws['M2']='Metric'; ws['N2']='Symbol'; ws['O2']='Wins'; ws['P2']='Losses'; ws['Q2']='Trades'
    ws['M3']='Overall most wins'; ws['M4']='Overall most losses'; ws['M5']='FX most wins'; ws['M6']='FX most losses'; ws['M7']='Crypto most wins'  # missing crypto most losses row intentionally
    ws['T2']='Account'; ws['U2']='Balance'; ws['V2']='Currency'
    ws['T3']='Bybit Live'; ws['U3']='1'; ws['V3']='USDT'
    wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'duration':{},'leaders':{
        'most_wins_instrument':{'symbol':'EURUSD','wins':4,'losses':1,'trades':5},
        'most_losses_instrument':{'symbol':'GBPUSD','wins':1,'losses':4,'trades':5},
        'fx_most_wins_instrument':{'symbol':'EURUSD','wins':3,'losses':1,'trades':4},
        'fx_most_losses_instrument':{'symbol':'XAUUSD','wins':1,'losses':3,'trades':4},
        'crypto_most_wins_instrument':{'symbol':'BTCUSDT','wins':6,'losses':2,'trades':8},
        'crypto_most_losses_instrument':{'symbol':'ETHUSDT','wins':2,'losses':6,'trades':8},
    }}},'balances':[{'account_label':'Bybit Live','balance':2,'currency':'USDT'}]}
    result=update_master_journal_workbook_data_only(p,snap); Path(result["candidate_path"]).replace(p)
    out=load_workbook(p)['Dashboard']
    assert out['N3'].value=='EURUSD' and out['O3'].value==4 and out['P3'].value==1 and out['Q3'].value==5
    assert out['N4'].value=='GBPUSD' and out['O4'].value==1 and out['P4'].value==4 and out['Q4'].value==5
    assert out['N8'].value=='ETHUSDT' and out['O8'].value==2 and out['P8'].value==6 and out['Q8'].value==8
    assert result['diagnostics']['updated_cells'] > 0

def test_account_balances_restores_missing_rows_without_layout_mutation(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    src = tmp_path / "m.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    wb.save(src)
    snap = {'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{}}},'balances':[
        {"account_label": "Bybit Demo", "balance": 123.456789, "currency": "USDT", "as_of": "2026-05-16"},
        {"account_label": "Bybit Live", "balance": 10.123456789, "currency": "USDT", "as_of": "2026-05-16"},
    ]}
    res = update_master_journal_workbook_data_only(src, snap)
    Path(res["candidate_path"]).replace(src)
    out = load_workbook(src)
    d = out["Dashboard"]
    found = {}
    for r in range(3, d.max_row + 1):
        label = str(d.cell(r, 20).value or "").strip()
        if label in {"Bybit Demo", "Bybit Live"}:
            found[label] = r
    assert "Bybit Demo" in found and "Bybit Live" in found
    assert isinstance(d.cell(found["Bybit Demo"], 21).value, (int, float))
    assert d.cell(found["Bybit Demo"], 22).value == "USDT"
    assert str(d.cell(found["Bybit Demo"], 23).value) == "2026-05-16"
    assert out.sheetnames == ["Dashboard", "Trade Log", "Instrument Averages", "P&L Calendar"]
    out.close()

def test_account_balances_reuses_blank_row_before_append(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    p = tmp_path / "reuse.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    ws["T3"]="Bybit Live"; ws["U3"]=1.0; ws["V3"]="USDT"
    ws["T4"]=None; ws["U4"]=None; ws["V4"]=None
    wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{}}},'balances':[{'account_label':'Bybit Demo','balance':2.5,'currency':'USDT','as_of':'2026-05-16'}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["T4"].value == "Bybit Demo"
    assert out["U4"].value == 2.5

def test_update_data_only_preserves_calendar_merges_and_skips_non_anchor_writes(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path / "merged-calendar.xlsx"
    wb = Workbook(); dash = wb.active; dash.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); cal = wb.create_sheet("P&L Calendar")
    dash["A1"]="Overall"; dash["D1"]="FX"; dash["G1"]="Crypto"; dash["J1"]="Winners"; dash["J8"]="Losers"; dash["J14"]="Drawdown"; dash["M1"]="Instrument leaders"; dash["T1"]="Account Balances"
    dash["T2"]="Account"; dash["U2"]="Balance"; dash["V2"]="Currency"; dash["W2"]="As Of"
    months = ["January","February","March","April","May","June","July","August","September","October","November","December"]
    for i, m in enumerate(months, start=3):
        cal.cell(1, i).value = m
    cal.merge_cells("A2:A3"); cal.merge_cells("A4:A5"); cal.merge_cells("A6:A7")
    cal["A2"]=2026; cal["A4"]=2025; cal["A6"]=2024
    cal["B2"]="P/L %"; cal["B3"]="Total Trades"; cal["B4"]="P/L %"; cal["B5"]="Total Trades"; cal["B6"]="P/L %"; cal["B7"]="Total Trades"
    wb.save(p)
    snap = {
        "items": [{"id":"t1","row_type":"trade","account":"BYBIT DEMO","symbol":"BTCUSDT","side":"BUY","open_time":"2026-05-01","close_time":"2026-05-01","net_profit":10.0,"result_pct":1.0}],
        "stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},
        "balances":[{"account_label":"BYBIT DEMO","balance":100.0,"currency":"USDT","as_of":"2026-05-16"}],
    }
    res = update_master_journal_workbook_data_only(p, snap)
    Path(res["candidate_path"]).replace(p)
    out = load_workbook(p, data_only=True)
    out_cal = out["P&L Calendar"]
    merged = {str(rng) for rng in out_cal.merged_cells.ranges}
    assert {"A2:A3", "A4:A5", "A6:A7"}.issubset(merged)
    assert out_cal["A3"].value in (None, "")
    assert out_cal["A5"].value in (None, "")
    assert float(out_cal["G2"].value) == 0.01
    assert int(out_cal["G3"].value) == 1
    d = out["Dashboard"]
    assert d["T3"].value == "BYBIT DEMO"
    assert isinstance(d["U3"].value, (int, float))
    out.close()

def test_update_data_only_appends_missing_calendar_year_block(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path / "append-year.xlsx"
    wb = Workbook(); dash = wb.active; dash.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); cal = wb.create_sheet("P&L Calendar")
    dash["A1"]="Overall"; dash["D1"]="FX"; dash["G1"]="Crypto"; dash["J1"]="Winners"; dash["J8"]="Losers"; dash["J14"]="Drawdown"; dash["M1"]="Instrument leaders"; dash["T1"]="Account Balances"
    dash["T2"]="Account"; dash["U2"]="Balance"; dash["V2"]="Currency"; dash["W2"]="As Of"
    for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], start=3):
        cal.cell(1, i).value = m
    cal.merge_cells("A2:A3"); cal["A2"]=2026; cal["B2"]="P/L %"; cal["B3"]="Total Trades"
    wb.save(p)
    snap = {"items":[{"id":"t1","row_type":"trade","account":"BYBIT DEMO","symbol":"BTCUSDT","side":"BUY","open_time":"2027-01-05","close_time":"2027-01-05","result_pct":2.0}],
            "stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},
            "balances":[{"account_label":"BYBIT DEMO","balance":100.0,"currency":"USDT","as_of":"2026-05-16"}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p, data_only=True)["P&L Calendar"]
    assert any(str(rng) == "A4:A5" for rng in out.merged_cells.ranges)
    assert int(out["A4"].value) == 2027
    assert float(out["C4"].value) == 0.02
    assert int(out["C5"].value) == 1

def test_instrument_leaders_custom_layout_populates_values(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path / "leaders-layout.xlsx"
    wb = Workbook(); d = wb.active; d.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); cal = wb.create_sheet("P&L Calendar")
    d["A1"]="Account Balances"; d["A2"]="Account"; d["B2"]="Balance"; d["C2"]="Currency"; d["A3"]="BYBIT DEMO"
    d["G1"]="Overall"; d["J1"]="FX"; d["M1"]="Crypto"; d["A11"]="Instrument leaders"; d["G12"]="Winners"; d["G17"]="Losers"; d["G22"]="Drawdown"
    d["A12"]="Metric"; d["B12"]="Symbol"; d["C12"]="Wins"; d["D12"]="Losses"; d["E12"]="Trades"
    labels=["Overall most wins","Overall most losses","FX most wins","FX most losses","Crypto most wins","Crypto most losses"]
    for i, lbl in enumerate(labels, start=13): d.cell(i,1).value=lbl
    for i,m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], start=3): cal.cell(1,i).value=m
    cal.merge_cells("A2:A3"); cal["A2"]=2026; cal["B2"]="P/L %"; cal["B3"]="Total Trades"
    wb.save(p)
    snap={"items":[{"id":"t1","row_type":"trade","close_time":"2026-05-01","result_pct":1.0}],
          "stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"duration":{},"leaders":{
              "most_wins_instrument":{"symbol":"EURUSD","wins":5,"losses":1,"trades":6},
              "most_losses_instrument":{"symbol":"BTCUSDT","wins":1,"losses":5,"trades":6},
              "fx_most_wins_instrument":{"symbol":"EURUSD","wins":5,"losses":1,"trades":6},
              "fx_most_losses_instrument":{"symbol":"GBPUSD","wins":1,"losses":5,"trades":6},
              "crypto_most_wins_instrument":{"symbol":"ETHUSDT","wins":4,"losses":2,"trades":6},
              "crypto_most_losses_instrument":{"symbol":"SOLUSDT","wins":1,"losses":5,"trades":6},
          }}},
          "balances":[{"account_label":"BYBIT DEMO","balance":100.0,"currency":"USDT"}]}
    res=update_master_journal_workbook_data_only(p,snap); Path(res["candidate_path"]).replace(p)
    out=load_workbook(p)["Dashboard"]
    expected = [
        ("EURUSD", 5, 1, 6),
        ("BTCUSDT", 1, 5, 6),
        ("EURUSD", 5, 1, 6),
        ("GBPUSD", 1, 5, 6),
        ("ETHUSDT", 4, 2, 6),
        ("SOLUSDT", 1, 5, 6),
    ]
    for offset, (sym, wins, losses, trades) in enumerate(expected):
        row = 13 + offset
        assert out.cell(row, 2).value == sym
        assert out.cell(row, 3).value == wins
        assert out.cell(row, 4).value == losses
        assert out.cell(row, 5).value == trades
    assert not res["diagnostics"]["missing_leader_headers"]

def test_legacy_trade_log_sheet_migrates_to_trade_log(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only, build_master_journal_workbook
    p = tmp_path / "legacy.xlsx"
    build_master_journal_workbook(sample_snapshot(), p)
    wb = load_workbook(p)
    wb["Trade Log"].title = "All Trades"
    wb.save(p)
    snap={"items":[],"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[]}
    res=update_master_journal_workbook_data_only(p,snap)
    assert res["ok"] is True
    assert res["diagnostics"].get("migrated_trade_log_sheet") is True
    Path(res["candidate_path"]).replace(p)
    out=load_workbook(p)
    assert "Trade Log" in out.sheetnames and "All Trades" not in out.sheetnames
    out.close()

def test_both_all_trades_and_trade_log_fails_ambiguous(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    p = tmp_path / "ambiguous.xlsx"
    wb = Workbook(); wb.remove(wb.active)
    wb.create_sheet("Dashboard"); wb.create_sheet("Trade Log"); wb.create_sheet("All Trades"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    wb.save(p)
    snap={"items":[],"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[]}
    with pytest.raises(RuntimeError, match="ambiguous trade sheets"):
        update_master_journal_workbook_data_only(p,snap)
