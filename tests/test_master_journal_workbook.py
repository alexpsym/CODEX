from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
import pytest
from tools.master_journal_workbook import build_master_journal_workbook, read_master_journal_manual_overrides, read_master_journal_source, update_master_journal_workbook_data_only, SHEET_ORDER, TRADE_LOG_HEADERS, OLD_TRADE_LOG_HEADERS, PRE_MOVE_TRADE_LOG_HEADERS, MOVE_TO_FIELD_MAP, _ensure_trade_log_schema, _ensure_pnl_calendar_freeze_panes
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

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



def _header_col(ws, name: str) -> int:
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    return headers.index(name) + 1


def _ensure_trade_log_headers(wb) -> None:
    ws = wb["Trade Log"] if "Trade Log" in wb.sheetnames else wb.create_sheet("Trade Log")
    for idx, header in enumerate(TRADE_LOG_HEADERS, start=1):
        ws.cell(1, idx).value = header
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(TRADE_LOG_HEADERS))}1"
    if "Instrument Averages" in wb.sheetnames:
        inst = wb["Instrument Averages"]
        inst_headers = ["Symbol","Class","Trades","Longs","Shorts","Wins","Losses","Break-even","Long wins","Long losses","Short wins","Short losses","Long break-even","Short break-even","Net P/L %","Avg P/L %","Win Rate %","Avg stop % (W)","Avg stop % (L)","Avg target % (W)","Avg target % (L)","Shortest duration (DD:HH:MM:SS)","Avg duration (DD:HH:MM:SS)","Longest duration (DD:HH:MM:SS)"]
        if not any(inst.cell(1, c).value not in (None, "") for c in range(1, inst.max_column + 1)):
            for idx, header in enumerate(inst_headers, start=1):
                inst.cell(1, idx).value = header
        inst.freeze_panes = "A2"
        inst.auto_filter.ref = f"A1:X{max(1, inst.max_row)}"

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
        'balances':[{'account_label':'BYBIT','balance':10.123456789,'currency':'USDT','as_of':'2026-05-10'}]
    }


def test_single_builder_definition():
    src = Path('tools/master_journal_workbook.py').read_text(encoding='utf-8')
    assert src.count('def build_master_journal_workbook') == 1


def test_dashboard_parity_and_equity(tmp_path: Path):
    out=tmp_path/'Trading Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out); wb=load_workbook(out)
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
    out=tmp_path/'Trading Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']; ws.cell(2, _header_col(ws, 'Test')).value='Yes'; ws.cell(2, _header_col(ws, 'Setup')).value='AAA'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov['t1']['is_test_trade'] is True and ov['t1']['setup']=='AAA'


def test_current_pnl_calendar_layout_freezes_months_and_left_axis():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "P&L Calendar"
    for col, month in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], start=3):
        ws.cell(1, col, month)
    ws.merge_cells("A2:A3")
    ws["A2"] = 2026
    ws["B2"] = "P/L %"
    ws["B3"] = "Total Trades"
    _ensure_pnl_calendar_freeze_panes(ws)
    assert ws.freeze_panes == "C2"


def test_pre_move_schema_migrates_stop_out_value_and_hides_row_id():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    for col, header in enumerate(PRE_MOVE_TRADE_LOG_HEADERS, start=1):
        ws.cell(1, col, header)
    ws.cell(2, PRE_MOVE_TRADE_LOG_HEADERS.index("Stop Out") + 1, "Yes")
    ws.cell(2, PRE_MOVE_TRADE_LOG_HEADERS.index("Row ID") + 1, "row-1")
    _ensure_trade_log_schema(ws)
    assert ws.cell(2, _header_col(ws, "Close Stopout")).value == "Yes"
    assert ws.cell(2, _header_col(ws, "Row ID")).value == "row-1"
    assert get_column_letter(_header_col(ws, "Row ID")) == "AV"
    assert ws.column_dimensions["AV"].hidden is True
    assert ws.auto_filter.ref == "A1:AV2"

def test_calendar_month_conditional_formatting_rows(tmp_path: Path):
    snap=sample_snapshot()
    snap['items']=[
        {'id':'p','row_type':'trade','account':'A','open_time':'2026-05-01','close_time':'2026-05-01','net_profit':10,'result_pct':1.2,'is_test_trade':False},
        {'id':'n','row_type':'trade','account':'A','open_time':'2026-06-01','close_time':'2026-06-01','net_profit':-5,'result_pct':-0.4,'is_test_trade':False},
    ]
    out=tmp_path/'Trading Journal.xlsx'; build_master_journal_workbook(snap,out); wb=load_workbook(out)
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
    assert cal.freeze_panes == "B3"
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
    assert ws.cell(2, _header_col(ws, 'Balance After')).value in ('', None)
    assert ws.cell(3, _header_col(ws, 'Balance After')).value in ('', None)


def test_trade_log_hidden_row_id_and_unsorted_override(tmp_path: Path):
    out=tmp_path/'Trading Journal.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']
    headers=[ws.cell(1,c).value for c in range(1,ws.max_column+1)]
    assert '__row_id' not in headers
    assert ws.max_column == len(TRADE_LOG_HEADERS)
    assert ws["A2"].comment is None
    ws.cell(2, _header_col(ws, 'Test')).value='Yes'; ws.cell(2, _header_col(ws, 'Setup')).value='setup-x'; wb.save(out)
    ov=read_master_journal_manual_overrides(out)
    assert ov["t1"]["is_test_trade"] is True
    assert ov["t1"]["setup"] == "setup-x"
    assert len(ws.conditional_formatting) > 0
    assert ws.cell(2, _header_col(ws, "Profit %")).number_format == "0.00%"
    assert ws["A2"].comment is None
    assert ws.cell(2, _header_col(ws, "Profit %")).value in (0.023, -0.011)


def test_legacy_comment_row_id_preferred_over_trade_meta_after_row_move(tmp_path: Path):
    out=tmp_path/'legacy.xlsx'; build_master_journal_workbook(sample_snapshot(), out)
    wb=load_workbook(out); ws=wb['Trade Log']
    from openpyxl.comments import Comment
    ws["A2"].comment = Comment("row_id:t1", "legacy")
    ws["A3"].comment = Comment("row_id:t2", "legacy")
    for c in range(1, ws.max_column+1):
        ws.cell(2,c).value, ws.cell(3,c).value = ws.cell(3,c).value, ws.cell(2,c).value
    # stale _Trade Meta row mapping now conflicts with moved comments
    ws.cell(2, _header_col(ws, "Setup")).value = "moved-comment-target"
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
    assert ws.cell(2, _header_col(ws, 'Balance After')).value == 100
    assert ws.cell(3, _header_col(ws, 'Balance After')).value == 105
    assert ws.cell(4, _header_col(ws, 'Balance After')).value in ("", None)
    assert ws.cell(1, _header_col(ws, 'Trade Duration (DD:HH:MM:SS)')).value == 'Trade Duration (DD:HH:MM:SS)'
    assert ws.cell(2, _header_col(ws, 'Trade Duration (DD:HH:MM:SS)')).value == 41
    assert ws.cell(3, _header_col(ws, 'Trade Duration (DD:HH:MM:SS)')).value == 503
    assert ws.cell(2, _header_col(ws, 'Trade Duration (DD:HH:MM:SS)')).number_format == r'00\:00\:00\:00'
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


def test_trade_log_preserves_explicit_bybit_execution_row_id(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    snap = sample_snapshot()
    snap["items"] = [
        {
            "id": "bybit:demo:execution:BTCUSDT:E1",
            "row_type": "trade",
            "source": "bybit_execution_history",
            "account": "Bybit Demo",
            "account_label": "Bybit Demo",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "open_time": "2026-05-19T01:13:00+10:00",
            "close_time": "2026-05-19T01:13:00+10:00",
            "qty": 0.1,
            "entry_price": 100000.0,
            "exit_price": 100000.0,
            "commission": 0.01,
            "net_profit": None,
            "currency": "USDT",
            "asset_class": "crypto",
        }
    ]
    build_master_journal_workbook(snap, out)
    ws = load_workbook(out)["Trade Log"]
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    rid_col = headers.index("Row ID") + 1
    assert ws.cell(2, rid_col).value == "bybit:demo:execution:BTCUSDT:E1"

def test_update_data_only_migrates_legacy_all_trades_and_removes_trade_meta(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
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
    out = tmp_path / "Trading Journal.xlsx"
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

def test_update_data_only_survivor_guard_fails_when_row_id_header_missing(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    headers = [str(ws.cell(1, c).value or "") for c in range(1, ws.max_column + 1)]
    ridx = headers.index("Row ID") + 1
    ws.cell(1, ridx).value = "Row ID Removed"
    wb.save(out)
    wb.close()
    with pytest.raises(RuntimeError, match="Trade Log headers cannot be migrated safely"):
        update_master_journal_workbook_data_only(out, snap, expected_survivor_row_ids=["old1", "old2"])

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
    # Trade Log row-level rules cover the full visible row without overlapping value-cell fill rules.
    tr = _cf_ranges(trade_log)
    assert any(r.startswith("A2:AV") for r in tr)
    assert not any("M2:M" in r for r in tr)
    assert not any("N2:P" in r for r in tr)
    colors = _all_rule_colors(trade_log) + _all_rule_colors(dash) + _all_rule_colors(wb["P&L Calendar"]) + _all_rule_colors(wb["Instrument Averages"])
    assert any("C6EFCE" in f and "006100" in c for f, c in colors)
    assert any("FFC7CE" in f and "9C0006" in c for f, c in colors)

def test_read_source_cashflow_new_balance_falls_back_to_balance_after_zero(tmp_path: Path):
    snap = sample_snapshot()
    snap["items"] = [
        {'id':'t1','row_type':'trade','account':'BINANCE','symbol':'BTCUSDT','side':'BUY','open_time':'2026-01-01','close_time':'2026-01-01','net_profit':1.0,'balance_after_trade':396.65720524,'currency':'USDT'},
        {'id':'c1','row_type':'cashflow','account':'BINANCE','symbol':'CASHFLOW','side':'WITHDRAWAL','open_time':'2026-01-02','close_time':'2026-01-02','net_profit':-396.65720524,'balance_after_trade':0,'cashflow_amount':-396.65720524,'cashflow_new_balance':'','currency':'USDT'},
    ]
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    headers = {str(ws.cell(1, c).value): c for c in range(1, ws.max_column + 1)}
    ws.cell(3, headers["Cashflow New Balance"]).value = None
    wb.save(out)
    wb.close()

    parsed = read_master_journal_source(out)
    cashflow_row = next(i for i in parsed["items"] if i.get("row_type") == "cashflow")
    assert cashflow_row["cashflow_new_balance"] == 0
    assert cashflow_row["cashflow_new_balance"] is not None
    assert parsed["cashflow_ledger"]["BINANCE"][-1]["new_balance"] == 0


def test_build_workbook_forces_blank_setup_for_semantic_rows(tmp_path: Path):
    snap = {
        "items": [
            {"id":"m1","row_type":"monthly_aud_reval","account":"BYBIT","symbol":"MONTHLY AUD P/L","side":"","open_time":"2026-01-31","close_time":"2026-01-31","result_cash":1.0,"notes":"monthly","setup":"STALE"},
            {"id":"c1","row_type":"cashflow","account":"BYBIT","symbol":"CASHFLOW","side":"DEPOSIT","open_time":"2026-01-20","close_time":"2026-01-20","cashflow_amount":10.0,"cashflow_new_balance":100.0,"notes":"cash detail","setup":"STALE"},
        ],
        "stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},
        "balances":[],
    }
    out = tmp_path / "semantic_blank_setup.xlsx"
    build_master_journal_workbook(snap, out)
    ws = load_workbook(out)["Trade Log"]
    setup_col = _header_col(ws, 'Setup')
    notes_col = _header_col(ws, 'Notes')
    assert ws.cell(2, setup_col).value in ("", None) and ws.cell(2, notes_col).value == "monthly"
    assert ws.cell(3, setup_col).value in ("", None) and ws.cell(3, notes_col).value == "cash detail"

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
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    parsed = read_master_journal_source(out)
    trades = [r for r in parsed["items"] if r.get("row_type") == "trade"]
    assert trades
    row = trades[0]
    for key in ("id", "qty", "entry_price", "exit_price", "stop_loss", "take_profit", "net_profit", "result_pct", "r_multiple", "trade_duration_seconds", "is_test_trade"):
        assert key in row


def test_read_master_journal_source_parses_result_percent_alias(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / "result_pct_alias.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    ws.append(["Open Time", "Close Time", "Account", "Symbol", "Side", "Result %"])
    ws.append(["2026-01-01 00:00:00", "2026-01-01 01:00:00", "OANDA DEMO", "EURUSD", "BUY", 0.0125])
    wb.save(p)
    out = read_master_journal_source(p)
    trade = next(r for r in out["items"] if r.get("row_type") == "trade")
    assert trade["result_pct"] == pytest.approx(1.25)


def test_read_master_journal_source_parses_numeric_ddhhmmss_duration_as_seconds(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / "duration_numeric.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    ws.append(["Open Time", "Close Time", "Account", "Symbol", "Side", "Trade Duration (DD:HH:MM:SS)", "Row ID"])
    ws.append(["2026-01-01 00:00:00", "2026-01-01 00:00:41", "A", "EURUSD", "BUY", 41, "r1"])
    ws.append(["2026-01-01 00:00:00", "2026-01-01 00:05:03", "A", "EURUSD", "BUY", 503, "r2"])
    ws.append(["2026-01-01 00:00:00", "2026-01-01 01:01:01", "A", "EURUSD", "BUY", 10101, "r3"])
    wb.save(p)
    out = read_master_journal_source(p)
    rows = {r["id"]: r for r in out["items"]}
    assert rows["r1"]["trade_duration_seconds"] == 41
    assert rows["r2"]["trade_duration_seconds"] == 303
    assert rows["r3"]["trade_duration_seconds"] == 3661


def test_read_master_journal_source_derives_duration_when_cell_blank(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / "duration_derived.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    ws.append(["Open Time", "Close Time", "Account", "Symbol", "Side", "Trade Duration (DD:HH:MM:SS)", "Row ID"])
    ws.append(["2026-01-01 00:00:00", "2026-01-01 00:01:07", "A", "EURUSD", "BUY", "", "r1"])
    wb.save(p)
    out = read_master_journal_source(p)
    row = next(r for r in out["items"] if r["id"] == "r1")
    assert row["trade_duration_seconds"] == 67


def test_instrument_averages_op_and_duration_not_blank_after_data_only_update(tmp_path: Path):
    p = tmp_path / "master.xlsx"
    snap = sample_snapshot()
    if snap.get("stats") and snap["stats"].get("by_instrument"):
        for rec in snap["stats"]["by_instrument"]:
            if isinstance(rec, dict):
                rec["net_result_pct"] = rec.get("net_result_pct", 2.5)
                rec["avg_result_pct"] = rec.get("avg_result_pct", 1.25)
                rec["min_trade_duration_seconds"] = rec.get("min_trade_duration_seconds", 41)
                rec["avg_trade_duration_seconds"] = rec.get("avg_trade_duration_seconds", 303)
                rec["max_trade_duration_seconds"] = rec.get("max_trade_duration_seconds", 3661)
    build_master_journal_workbook(snap, p)
    wb = load_workbook(p)
    inst = wb["Instrument Averages"]
    headers = [str(c.value or "") for c in inst[1]]
    # mimic live alias header style
    inst.cell(1, headers.index("Shortest duration (DD:HH:MM:SS)") + 1).value = "Shortest (DD:HH:MM:SS)"
    inst.cell(1, headers.index("Longest duration (DD:HH:MM:SS)") + 1).value = "Longest (DD:HH:MM:SS)"
    _ensure_trade_log_headers(wb); wb.save(p); wb.close()
    res = update_master_journal_workbook_data_only(p, snap)
    Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)
    inst2 = out["Instrument Averages"]
    h2 = [str(c.value or "") for c in inst2[1]]
    net_col = h2.index("Net P/L %") + 1
    avg_col = h2.index("Avg P/L %") + 1
    s_col = h2.index("Shortest (DD:HH:MM:SS)") + 1
    a_col = h2.index("Avg duration (DD:HH:MM:SS)") + 1
    l_col = h2.index("Longest (DD:HH:MM:SS)") + 1
    vals = [(inst2.cell(r, net_col).value, inst2.cell(r, avg_col).value, inst2.cell(r, s_col).value, inst2.cell(r, a_col).value, inst2.cell(r, l_col).value) for r in range(2, inst2.max_row + 1)]
    assert any(isinstance(v[0], (int, float)) or isinstance(v[1], (int, float)) for v in vals)
    assert any(all(isinstance(x, (int, float)) for x in v[2:]) for v in vals)
    out.close()


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
    assert ws.cell(mr, _header_col(ws, "Net P/L")).value == 123.45
    fmt = str(ws.cell(mr, _header_col(ws, "Net P/L")).number_format or "")
    assert "AUD" in fmt
    assert "UNKNOWN" not in fmt
    # metrics remain from trade rows only
    cal = wb["P&L Calendar"]
    assert cal["E4"].value == 1  # April trades count


def test_read_master_journal_source_monthly_aud_roundtrip_fields(tmp_path: Path):
    snapshot = {
        "items": [{
            "id": "monthly_aud_reval:bybit_live:2026-04",
            "row_type": "monthly_aud_reval",
            "account": "Bybit Live",
            "account_label": "Bybit Live",
            "symbol": "MONTHLY AUD P/L",
            "open_time": "2026-04-01T00:00:00+10:00",
            "close_time": "2026-04-30T23:59:59+10:00",
            "result_cash": 123.45,
            "result_currency": "AUD",
        }],
        "stats": {"totals": {}, "groups": {}},
        "balances": [],
    }
    out = tmp_path / "monthly_roundtrip.xlsx"
    build_master_journal_workbook(snapshot, out)
    parsed = read_master_journal_source(out)
    monthly = next(r for r in parsed["items"] if r.get("row_type") == "monthly_aud_reval")
    assert monthly["result_cash"] == pytest.approx(123.45)
    assert monthly["result_currency"] == "AUD"
    assert (monthly.get("raw_refs") or {}).get("period_month") == "2026-04"
    assert monthly.get("net_profit") in (None, "")
    assert monthly.get("realized_pnl") in (None, "")

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
    comm_col = _header_col(ws, 'Commission')
    assert ws.cell(2, comm_col).value in ("", None)
    assert ws.cell(3, comm_col).value in ("", None)
    assert ws.cell(4, comm_col).value in ("", None)
    assert ws.cell(5, comm_col).value == 1.25
    assert "AUD" in str(ws.cell(5, comm_col).number_format or "")

def test_trade_log_currency_inference_avoids_unknown_and_respects_fx_vs_crypto(tmp_path: Path):
    s = sample_snapshot()
    s["items"] = [
        {"id":"o1","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-01","close_time":"2026-01-01","commission":None,"net_profit":1.0,"result_pct":1.0},
        {"id":"p1","row_type":"trade","account":"PEPPERSTONE LIVE","symbol":"GBPUSD","side":"BUY","open_time":"2026-01-02","close_time":"2026-01-02","net_profit":2.0,"result_pct":2.0},
        {"id":"m1","row_type":"monthly_aud_reval","account":"Bybit Live","symbol":"MONTHLY AUD P/L","side":"","open_time":"2026-01-31","close_time":"2026-01-31","result_cash":3.0,"net_profit":3.0},
        {"id":"b1","row_type":"trade","account":"BYBIT","symbol":"BTCUSDT","side":"SELL","open_time":"2026-01-03","close_time":"2026-01-03","net_profit":4.0,"result_pct":4.0},
        {"id":"f1","row_type":"trade","account":"OANDA DEMO","symbol":"USDCAD","side":"BUY","open_time":"2026-01-04","close_time":"2026-01-04","net_profit":5.0,"result_pct":5.0},
        {"id":"f2","row_type":"trade","account":"OANDA DEMO","symbol":"USDCHF","side":"BUY","open_time":"2026-01-05","close_time":"2026-01-05","net_profit":6.0,"result_pct":6.0},
    ]
    out = tmp_path / "currency_infer.xlsx"
    build_master_journal_workbook(s, out)
    ws = load_workbook(out)["Trade Log"]
    headers = [str(c.value or "") for c in ws[1]]
    row_id_col = headers.index("Row ID") + 1
    row_map = {str(ws.cell(r, row_id_col).value): r for r in range(2, ws.max_row + 1)}
    for rid in ("o1","p1","m1","b1","f1","f2"):
        assert rid in row_map
        assert "UNKNOWN" not in str(ws.cell(row_map[rid], _header_col(ws, 'Commission')).number_format or "")
        assert "UNKNOWN" not in str(ws.cell(row_map[rid], _header_col(ws, 'Net P/L')).number_format or "")
    assert "AUD" in str(ws.cell(row_map["o1"], _header_col(ws, 'Net P/L')).number_format or "")
    assert "AUD" in str(ws.cell(row_map["p1"], _header_col(ws, 'Net P/L')).number_format or "")
    assert "AUD" in str(ws.cell(row_map["m1"], _header_col(ws, 'Net P/L')).number_format or "")
    assert "USDT" in str(ws.cell(row_map["b1"], _header_col(ws, 'Net P/L')).number_format or "")
    assert "AUD" in str(ws.cell(row_map["f1"], _header_col(ws, 'Net P/L')).number_format or "")
    assert "AUD" in str(ws.cell(row_map["f2"], _header_col(ws, 'Net P/L')).number_format or "")
    assert ws.cell(row_map["o1"], _header_col(ws, "Commission")).value in ("", None)

def test_update_data_only_repairs_unknown_trade_log_currency_formats(tmp_path: Path):
    s = sample_snapshot()
    s["items"] = [
        {"id":"o1","row_type":"trade","account":"OANDA DEMO","symbol":"EURUSD","side":"BUY","open_time":"2026-01-01","close_time":"2026-01-01","net_profit":1.0,"result_pct":1.0},
        {"id":"b1","row_type":"trade","account":"BYBIT","symbol":"BTCUSDT","side":"SELL","open_time":"2026-01-02","close_time":"2026-01-02","net_profit":2.0,"result_pct":2.0},
        {"id":"m1","row_type":"monthly_aud_reval","account":"Bybit Live","symbol":"MONTHLY AUD P/L","open_time":"2026-01-31","close_time":"2026-01-31","result_cash":3.0,"net_profit":3.0},
    ]
    out = tmp_path / "repair_unknown.xlsx"
    build_master_journal_workbook(s, out)
    wb = load_workbook(out); ws = wb["Trade Log"]
    ws.cell(2, _header_col(ws, "Commission")).number_format = '#,##0.00 "UNKNOWN"'
    ws.cell(2, _header_col(ws, "Net P/L")).number_format = '#,##0.00 "UNKNOWN"'
    ws.cell(3, _header_col(ws, "Net P/L")).number_format = '#,##0.00 "UNKNOWN"'
    ws.cell(4, _header_col(ws, "Net P/L")).number_format = '#,##0.00 "UNKNOWN"'
    wb.save(out); wb.close()
    res = update_master_journal_workbook_data_only(out, s)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(out)
    ws2 = load_workbook(out)["Trade Log"]
    assert ws2.cell(1, 11).value == "Target Price"
    assert ws2.cell(1, 12).value == "Target Distance"
    assert ws2.cell(1, 13).value == "Commission"
    assert ws2.cell(1, 14).value == "Net P/L"
    commission_col = _header_col(ws2, "Commission")
    net_pl_col = _header_col(ws2, "Net P/L")
    for r in range(2, ws2.max_row + 1):
        assert "UNKNOWN" not in str(ws2.cell(r, commission_col).number_format or "")
        assert "UNKNOWN" not in str(ws2.cell(r, net_pl_col).number_format or "")
        assert "UNKNOWN" not in str(ws2.cell(r, _header_col(ws2, "Target Price")).number_format or "")
        assert "UNKNOWN" not in str(ws2.cell(r, _header_col(ws2, "Target Distance")).number_format or "")

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
    ws['P2']='Account'; ws['Q2']='Balance'; ws['R2']='Currency'; ws['S2']='As Of'; ws['P3']='BYBIT'
    ws['B4'].number_format='0.00%'; ws['E4'].number_format='0.00%'; ws['H4'].number_format='0.00%'
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{'trades':1,'avg_r_multiple':2.0,'win_rate_pct':50.0,'min_result_pct':-1.25,'metric_sources':{'min_result_pct':{'symbol':'EURUSD','date':'2026-01-01'}}},'fx':{'trades':2,'avg_r_multiple':3.0,'win_rate_pct':25.0,'min_result_pct':-2.0,'metric_sources':{'min_result_pct':{'symbol':'GBPUSD','date':'2026-01-02'}}},'crypto':{'trades':3,'avg_r_multiple':4.0,'win_rate_pct':75.0,'min_result_pct':-3.0,'metric_sources':{'min_result_pct':{'symbol':'BTCUSDT','date':'2026-01-03'}}}},'risk_expectancy':{},'leaders':{}}},'balances':[{'account_label':'BYBIT','balance':12.5,'currency':'USDT','as_of':'2026-01-04'}]}
    res = update_master_journal_workbook_data_only(p,snap); Path(res["candidate_path"]).replace(p)
    out=load_workbook(p)
    d=out['Dashboard']
    assert d['B2'].value==1 and d['E2'].value==2 and d['H2'].value==3
    assert d['B3'].value==2.0 and d['E3'].value==3.0 and d['H3'].value==4.0
    assert d['B4'].value==0.5 and d['E4'].value==0.25 and d['H4'].value==0.75
    assert d['Q3'].value == 12.5
    assert d['R3'].value == 'USDT'
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
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{'fx_shortest_seconds':10,'fx_longest_seconds':20,'crypto_shortest_seconds':30,'crypto_longest_seconds':40,'metric_sources':{'fx_shortest_seconds':{'symbol':'EURUSD','date':'2026-01-01'},'fx_longest_seconds':{'symbol':'GBPUSD','date':'2026-01-02'},'crypto_shortest_seconds':{'symbol':'BTCUSDT','date':'2026-01-03'},'crypto_longest_seconds':{'symbol':'ETHUSDT','date':'2026-01-04'}}}}},'balances':[{'account_label':'BYBIT','balance':1,'currency':'USDT'}]}
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
    _ensure_trade_log_headers(wb); wb.save(p)
    parsed = read_master_journal_source(p)
    rows = parsed['items']
    assert rows[0]['asset_class'] == ''
    assert rows[1]['asset_class'] == ''
    assert rows[2]['asset_class'] == 'fx'
    assert rows[3]['asset_class'] == 'fx'
    assert rows[4]['asset_class'] == 'crypto'
    assert rows[5]['asset_class'] == 'crypto'


def test_read_master_journal_source_recomputes_corrupted_monthly_trade_row_id(tmp_path: Path):
    from openpyxl import Workbook

    p = tmp_path / "corrupt_read.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    for idx, header in enumerate(TRADE_LOG_HEADERS, start=1):
        ws.cell(1, idx).value = header
    row = {
        "Open Time": "2026-05-02",
        "Close Time": "2026-05-02",
        "Account": "PEPPERSTONE DEMO",
        "Symbol": "EURUSD",
        "Side": "BUY",
        "Qty": 1,
        "Entry Price": 1.1,
        "Exit Price": 1.2,
        "Net P/L": 10,
        "Row Type": "trade",
        "Row ID": "monthly_aud_reval:bybit_live:2026-05",
    }
    for header, value in row.items():
        ws.cell(2, TRADE_LOG_HEADERS.index(header) + 1).value = value
    wb.save(p)

    parsed = read_master_journal_source(p)
    item = parsed["items"][0]
    assert item["row_type"] == "trade"
    assert item["id"].startswith("sig:")
    assert item["id"] != "monthly_aud_reval:bybit_live:2026-05"
    assert parsed["diagnostics"]["repaired_corrupted_row_ids"][0]["reason"] == "invalid_monthly_aud_reval_row_id"


def test_update_master_journal_workbook_data_only_repairs_corrupted_row_id_cells(tmp_path: Path):
    snapshot = sample_snapshot()
    path = tmp_path / "sync_repair.xlsx"
    build_master_journal_workbook(snapshot, path)
    wb = load_workbook(path)
    ws = wb["Trade Log"]
    row_id_col = _header_col(ws, "Row ID")
    ws.cell(2, row_id_col).value = "monthly_aud_reval:bybit_live:2026-05"
    wb.save(path)

    result = update_master_journal_workbook_data_only(path, snapshot)
    Path(result["candidate_path"]).replace(path)
    repaired = load_workbook(path)["Trade Log"]
    assert repaired.cell(2, row_id_col).value == snapshot["items"][0]["id"]
    assert repaired.cell(2, row_id_col).value != "monthly_aud_reval:bybit_live:2026-05"
    assert result["diagnostics"].get("repaired_trade_log_row_ids", 0) >= 0

def test_instrument_leaders_skips_missing_optional_rows(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p=tmp_path/'leaders.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='Dashboard'; wb.create_sheet('Trade Log'); wb.create_sheet('Instrument Averages')
    ws['A1']='Overall'; ws['D1']='FX'; ws['G1']='Crypto'; ws['J1']='Winners'; ws['J8']='Losers'; ws['J14']='Drawdown'; ws['M1']='Instrument leaders'; ws['T1']='Account Balances'
    ws['M2']='Metric'; ws['N2']='Symbol'; ws['O2']='Wins'; ws['P2']='Losses'; ws['Q2']='Trades'
    ws['M3']='FX most wins'; ws['M4']='FX most losses'; ws['M5']='Crypto most wins'  # missing crypto most losses row intentionally
    ws['T2']='Account'; ws['U2']='Balance'; ws['V2']='Currency'
    ws['T3']='Bybit Live'; ws['U3']='1'; ws['V3']='USDT'
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'duration':{},'leaders':{
        'most_wins_instrument':{'symbol':'EURUSD','wins':4,'losses':1,'trades':5},
        'most_losses_instrument':{'symbol':'GBPUSD','wins':1,'losses':4,'trades':5},
        'fx_most_wins_instrument':{'symbol':'EURUSD','wins':3,'losses':1,'trades':4},
        'fx_most_losses_instrument':{'symbol':'XAUUSD','wins':1,'losses':3,'trades':4},
        'crypto_most_wins_instrument':{'symbol':'BTCUSDT','wins':6,'losses':2,'trades':8},
        'crypto_most_losses_instrument':{'symbol':'ETHUSDT','wins':2,'losses':6,'trades':8},
    }}},'balances':[{'account_label':'BYBIT','balance':2,'currency':'USDT'}]}
    result=update_master_journal_workbook_data_only(p,snap); Path(result["candidate_path"]).replace(p)
    out=load_workbook(p)['Dashboard']
    assert out['M3'].value == 'FX most wins' and out['N3'].value=='EURUSD' and out['O3'].value==3 and out['P3'].value==1 and out['Q3'].value==4
    assert out['M4'].value == 'FX most losses' and out['N4'].value=='XAUUSD' and out['O4'].value==1 and out['P4'].value==3 and out['Q4'].value==4
    assert out['M5'].value == 'Crypto most wins' and out['N5'].value=='BTCUSDT' and out['O5'].value==6 and out['P5'].value==2 and out['Q5'].value==8
    assert out['M6'].value == 'crypto most losses' or out['M6'].value == 'Crypto most losses'
    assert out['N6'].value=='ETHUSDT' and out['O6'].value==2 and out['P6'].value==6 and out['Q6'].value==8
    assert 'overall most wins' in result['diagnostics'].get('skipped_optional_leader_rows', [])
    assert 'overall most losses' in result['diagnostics'].get('skipped_optional_leader_rows', [])
    assert 'crypto most losses' in result['diagnostics'].get('restored_leader_rows', [])

    result2=update_master_journal_workbook_data_only(p,snap); Path(result2["candidate_path"]).replace(p)
    out2=load_workbook(p)['Dashboard']
    labels = [str(out2.cell(r, 13).value or '').strip().lower() for r in range(1, out2.max_row + 1)]
    assert labels.count('crypto most losses') == 1
    assert 'crypto most losses' not in result2['diagnostics'].get('restored_leader_rows', [])

def test_account_balances_restores_missing_rows_without_layout_mutation(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    src = tmp_path / "m.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    _ensure_trade_log_headers(wb); wb.save(src)
    snap = {'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{}}},'balances':[
        {"account_label": "Bybit Demo", "balance": 123.456789, "currency": "USDT", "as_of": "2026-05-16"},
        {"account_label": "BYBIT", "balance": 10.123456789, "currency": "USDT", "as_of": "2026-05-16"},
    ]}
    res = update_master_journal_workbook_data_only(src, snap)
    Path(res["candidate_path"]).replace(src)
    out = load_workbook(src)
    d = out["Dashboard"]
    found = {}
    for r in range(3, d.max_row + 1):
        label = str(d.cell(r, 20).value or "").strip()
        if label in {"Bybit Demo", "BYBIT"}:
            found[label] = r
    assert "Bybit Demo" in found and "BYBIT" in found
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
    ws["T3"]="BYBIT"; ws["U3"]=1.0; ws["V3"]="USDT"
    ws["T4"]=None; ws["U4"]=None; ws["V4"]=None
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={'stats':{'totals':{},'groups':{'by_market':{'overall':{},'fx':{},'crypto':{}},'risk_expectancy':{},'leaders':{},'duration':{}}},'balances':[{'account_label':'Bybit Demo','balance':2.5,'currency':'USDT','as_of':'2026-05-16'}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["T4"].value == "Bybit Demo"
    assert out["U4"].value == 2.5


def test_account_balances_renames_stale_bybit_live_row_to_bybit(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    p = tmp_path / "stale_only.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    ws["T3"]="Bybit Live"; ws["U3"]=1.0; ws["V3"]="USDT"
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[{"account_label":"BYBIT","balance":9.5,"currency":"USDT","as_of":"2026-05-16"}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["T3"].value == "BYBIT"
    assert out["U3"].value == 9.5
    assert out["V3"].value == "USDT"
    labels = [str(out.cell(r, 20).value or "").strip() for r in range(3, out.max_row + 1)]
    assert "Bybit Live" not in labels


def test_account_balances_clears_duplicate_stale_bybit_live_row(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    p = tmp_path / "stale_dup.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    ws["T3"]="BYBIT"; ws["U3"]=3.0; ws["V3"]="USDT"
    ws["T4"]="Bybit Live"; ws["U4"]=2.0; ws["V4"]="USDT"
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[{"account_label":"BYBIT","balance":7.0,"currency":"USDT"}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["T3"].value == "BYBIT"
    assert out["U3"].value == 7.0
    assert out["T4"].value in (None, "")
    assert out["U4"].value in (None, "")


def test_account_balances_clears_duplicate_stale_bybit_live_alias_row(tmp_path: Path):
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    from openpyxl import Workbook
    p = tmp_path / "stale_dup_alias.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    ws["T3"]="BYBIT"; ws["U3"]=3.0; ws["V3"]="USDT"
    ws["T4"]="bybit_live"; ws["U4"]=2.0; ws["V4"]="USDT"
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[{"account_label":"BYBIT","balance":7.0,"currency":"USDT"}]}
    res = update_master_journal_workbook_data_only(p, snap); Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["T3"].value == "BYBIT"
    assert out["T4"].value in (None, "")

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
    _ensure_trade_log_headers(wb); wb.save(p)
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
    assert d["T3"].value == "Bybit Demo"
    assert isinstance(d["U3"].value, (int, float))
    out.close()


def test_update_data_only_fails_on_unrepaired_crypto_zero_qty(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path / "zero_qty_fail.xlsx"
    wb=Workbook(); ws=wb.active; ws.title='Dashboard'; wb.create_sheet('Trade Log'); wb.create_sheet('Instrument Averages'); wb.create_sheet('P&L Calendar')
    ws["A1"]="Overall"; ws["D1"]="FX"; ws["G1"]="Crypto"; ws["J1"]="Winners"; ws["J8"]="Losers"; ws["J14"]="Drawdown"; ws["M1"]="Instrument leaders"; ws["T1"]="Account Balances"
    ws["T2"]="Account"; ws["U2"]="Balance"; ws["V2"]="Currency"; ws["W2"]="As Of"
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={"items":[{"id":"z1","row_type":"trade","account":"BYBIT","symbol":"BTCUSDT","side":"BUY","qty":0,"entry_price":100,"exit_price":100,"net_profit":0,"open_time":"2026-01-01","close_time":"2026-01-01"}],"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[]}
    out = update_master_journal_workbook_data_only(p, snap)
    assert out["ok"] is False
    assert "Unrepaired crypto zero-quantity" in str(out.get("error"))


def test_zero_qty_repair_from_raw_refs_closed_size():
    from tools.master_journal_workbook import _repair_or_flag_zero_trade_qty
    row = {"id":"r1","row_type":"trade","account":"BYBIT","symbol":"BTCUSDT","qty":0,"raw_refs":{"closedSize":"0.015"}}
    fixed = _repair_or_flag_zero_trade_qty(dict(row))
    assert fixed["qty"] == 0.015


def test_zero_qty_repair_from_pnl_inference_crypto():
    from tools.master_journal_workbook import _repair_or_flag_zero_trade_qty
    row = {"id":"r2","row_type":"trade","account":"BYBIT","symbol":"BTCUSDT","side":"BUY","qty":0,"entry_price":100.0,"exit_price":110.0,"commission":1.0,"net_profit":9.0}
    fixed = _repair_or_flag_zero_trade_qty(dict(row))
    assert fixed["qty"] == 1.0


def test_zero_qty_fx_is_diagnosed_not_inferred():
    from tools.master_journal_workbook import _repair_or_flag_zero_trade_qty
    row = {"id":"r3","row_type":"trade","account":"OANDA LIVE","symbol":"EUR/USD","side":"BUY","qty":0,"entry_price":1.1,"exit_price":1.2,"commission":0.1,"net_profit":1.0}
    fixed = _repair_or_flag_zero_trade_qty(dict(row))
    assert fixed["qty"] == 0
    assert "zero_qty_unrepaired_fx" in (fixed.get("diagnostics") or [])

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
    _ensure_trade_log_headers(wb); wb.save(p)
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
    _ensure_trade_log_headers(wb); wb.save(p)
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

def test_legacy_all_trades_migrates_to_trade_log(tmp_path: Path):
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
    wb.create_sheet("Dashboard"); wb.create_sheet("All Trades"); wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    _ensure_trade_log_headers(wb); wb.save(p)
    snap={"items":[],"stats":{"totals":{},"groups":{"by_market":{"overall":{},"fx":{},"crypto":{}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[]}
    with pytest.raises(RuntimeError, match="ambiguous trade sheets"):
        update_master_journal_workbook_data_only(p,snap)


def test_update_data_only_overwrites_stale_dashboard_account_balances_with_zero(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    stale = sample_snapshot()
    stale["balances"] = [
        {"account_label": "PEPPERSTONE DEMO", "balance": 4.78, "currency": "AUD", "as_of": "2026-05-10"},
        {"account_label": "BINANCE", "balance": 396.65720524, "currency": "USDT", "as_of": "2026-05-10"},
    ]
    build_master_journal_workbook(stale, out)
    snap = sample_snapshot()
    snap["balances"] = [
        {"account_label": "PEPPERSTONE DEMO", "balance": 0, "currency": "AUD", "as_of": "2026-05-11", "balance_source": "broker_account_summary"},
        {"account_label": "BINANCE", "balance": 0, "currency": "USDT", "as_of": "2026-05-11", "balance_source": "broker_account_summary"},
    ]
    res = update_master_journal_workbook_data_only(out, snap)
    Path(res["candidate_path"]).replace(out)
    wb = load_workbook(out, data_only=True)
    assert wb.sheetnames == SHEET_ORDER
    dash = wb["Dashboard"]
    vals = {str(dash.cell(r,1).value or "").strip(): dash.cell(r,2).value for r in range(1, dash.max_row+1)}
    assert vals.get("PEPPERSTONE DEMO") == 0
    assert vals.get("BINANCE") == 0
    wb.close()


def test_infer_trade_duration_rounds_to_nearest_second():
    from tools.master_journal_workbook import _infer_trade_duration_seconds
    assert _infer_trade_duration_seconds({"row_type":"trade","trade_duration_seconds":60.1}) == 60
    assert _infer_trade_duration_seconds({"row_type":"trade","trade_duration_seconds":60.5}) == 61
    assert _infer_trade_duration_seconds({"row_type":"trade","trade_duration_seconds":0.2}) == 1
    assert _infer_trade_duration_seconds({"row_type":"trade","trade_duration_seconds":60}) == 60


def test_canonicalize_and_dedupe_balances_prefers_cashflow_anchor():
    from tools.master_journal_workbook import _canonicalize_and_dedupe_balances
    balances = [
        {"account_label": "Bybit Demo", "balance": 369.64962148, "currency": "USDT", "balance_source": "trade_timeline", "as_of": "2026-05-26T01:00:00Z"},
        {"account_label": "Bybit Demo", "balance": 319.8339282399999, "currency": "USDT", "balance_source": "cashflow_anchor_plus_trades", "as_of": "2026-05-26T02:00:00Z"},
    ]
    deduped = _canonicalize_and_dedupe_balances(balances)
    assert len(deduped) == 1
    assert deduped[0]["balance"] == 319.8339282399999


def test_canonicalize_balances_prefers_cashflow_zero_over_stale_authoritative_trade():
    from tools.master_journal_workbook import _canonicalize_and_dedupe_balances
    balances = _canonicalize_and_dedupe_balances([
        {'account_label':'BINANCE','balance':396.65720524,'currency':'USDT','balance_source':'authoritative_trade_balance','as_of':'2020-10-24T02:18:00'},
        {'account_label':'BINANCE','balance':0,'currency':'USDT','balance_source':'cashflow_anchor_plus_trades','as_of':'2020-10-26T00:00:00'},
    ])
    assert len(balances) == 1
    assert balances[0]['balance'] == 0
    assert balances[0]['balance_source'] == 'cashflow_anchor_plus_trades'


def test_canonicalize_balances_prefers_pepperstone_demo_cashflow_zero_over_stale_trade_variants():
    from tools.master_journal_workbook import _canonicalize_and_dedupe_balances
    balances = _canonicalize_and_dedupe_balances([
        {'account_label':'Pepperstone Demo','balance':4.78,'currency':'AUD','balance_source':'authoritative_trade_balance','as_of':'2018-07-25T23:00:00'},
        {'account_label':'pepperstone_demo','balance':0,'currency':'AUD','balance_source':'cashflow_anchor_plus_trades','as_of':'2018-07-26T00:00:00'},
        {'account_label':'PEPPERSTONE-DEMO','balance':None,'currency':'AUD','balance_source':'timeline_missing','as_of':'2018-07-27T00:00:00'},
    ])
    assert len(balances) == 1
    assert balances[0]['account_label'] == 'PEPPERSTONE DEMO'
    assert balances[0]['account'] == 'PEPPERSTONE DEMO'
    assert balances[0]['balance'] == 0
    assert balances[0]['currency'] == 'AUD'
    assert balances[0]['balance_source'] == 'cashflow_anchor_plus_trades'


def test_update_data_only_verifies_dashboard_binance_zero_balance(tmp_path: Path):
    from tools.master_journal_workbook import build_master_journal_workbook, update_master_journal_workbook_data_only
    p = tmp_path / 'Trading Journal.xlsx'
    stale = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'BINANCE','balance':396.65720524,'currency':'USDT','balance_source':'authoritative_trade_balance'}]}
    build_master_journal_workbook(stale, p)
    fresh = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'BINANCE','balance':0,'currency':'USDT','balance_source':'cashflow_anchor_plus_trades','as_of':'2020-10-26T00:00:00'}]}
    result = update_master_journal_workbook_data_only(p, fresh)
    assert result['ok'] is True
    assert 'BINANCE' in result['diagnostics']['account_balance_verified']
    candidate = Path(result['candidate_path'])
    wb = load_workbook(candidate, data_only=True)
    try:
        dash = wb['Dashboard']
        values = {str(dash.cell(r, 1).value or '').strip(): dash.cell(r, 2).value for r in range(1, dash.max_row + 1)}
        assert values['BINANCE'] == 0
    finally:
        wb.close()


def test_update_data_only_overwrites_stale_pepperstone_demo_balance_with_zero(tmp_path: Path):
    from tools.master_journal_workbook import build_master_journal_workbook, update_master_journal_workbook_data_only
    p = tmp_path / 'Trading Journal.xlsx'
    stale = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'Pepperstone Demo','balance':4.78,'currency':'AUD','balance_source':'authoritative_trade_balance'}]}
    build_master_journal_workbook(stale, p)
    fresh = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'pepperstone_demo','balance':0,'currency':'AUD','balance_source':'cashflow_anchor_plus_trades','as_of':'2018-07-26T00:00:00'}]}
    result = update_master_journal_workbook_data_only(p, fresh)
    assert result['ok'] is True
    assert 'PEPPERSTONE DEMO' in result['diagnostics']['account_balance_verified']
    candidate = Path(result['candidate_path'])
    wb = load_workbook(candidate, data_only=True)
    try:
        dash = wb['Dashboard']
        values = {str(dash.cell(r, 1).value or '').strip(): (dash.cell(r, 2).value, dash.cell(r, 3).value) for r in range(1, dash.max_row + 1)}
        assert values['PEPPERSTONE DEMO'] == (0, 'AUD')
    finally:
        wb.close()


def test_update_data_only_fails_if_pepperstone_demo_balance_remains_stale(tmp_path: Path, monkeypatch):
    import tools.master_journal_workbook as mjw
    from tools.master_journal_workbook import build_master_journal_workbook, update_master_journal_workbook_data_only
    p = tmp_path / 'Trading Journal.xlsx'
    stale = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'Pepperstone Demo','balance':4.78,'currency':'AUD','balance_source':'authoritative_trade_balance'}]}
    build_master_journal_workbook(stale, p)
    real_write = mjw._write_value_preserving_cell

    def _block_pepperstone_demo_balance_write(ws, row, col, value):
        if col == 2 and str(ws.cell(row, 1).value or '').strip().upper() == 'PEPPERSTONE DEMO' and value == 0:
            return False
        return real_write(ws, row, col, value)

    monkeypatch.setattr(mjw, '_write_value_preserving_cell', _block_pepperstone_demo_balance_write)
    fresh = {'items': [], 'stats': {'totals': {}, 'groups': {}}, 'balances': [{'account_label':'PEPPERSTONE DEMO','balance':0,'currency':'AUD','balance_source':'cashflow_anchor_plus_trades','as_of':'2018-07-26T00:00:00'}]}
    result = update_master_journal_workbook_data_only(p, fresh)
    assert result['ok'] is False
    assert result['error'] == 'dashboard_account_balance_verification_failed'
    mismatches = result['diagnostics']['account_balance_mismatches']
    assert len(mismatches) == 1
    assert mismatches[0]['account'] == 'PEPPERSTONE DEMO'
    assert mismatches[0]['expected'] == 0.0
    assert mismatches[0]['actual'] == 4.78
    assert isinstance(mismatches[0]['row'], int)

def test_update_data_only_writes_overall_fx_crypto_streak_metrics(tmp_path: Path):
    from openpyxl import Workbook
    from tools.master_journal_workbook import update_master_journal_workbook_data_only
    p = tmp_path / "streaks.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"; wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["A1"] = "Overall"; ws["D1"] = "FX"; ws["G1"] = "Crypto"; ws["J1"] = "Winners"; ws["J8"] = "Losers"; ws["J14"] = "Drawdown"; ws["M1"] = "Instrument leaders"; ws["T1"] = "Account Balances"
    for col in ("A", "D", "G"):
        ws[f"{col}2"] = "Winning Streak"
        ws[f"{col}3"] = "Losing Streak"
    ws["T2"] = "Account"; ws["U2"] = "Balance"; ws["V2"] = "Currency"; ws["T3"] = "BINANCE"
    _ensure_trade_log_headers(wb); wb.save(p); wb.close()
    snap = {"stats":{"totals":{},"groups":{"by_market":{"overall":{"winning_streak":4,"losing_streak":3},"fx":{"winning_streak":2,"losing_streak":1},"crypto":{"winning_streak":5,"losing_streak":6}},"risk_expectancy":{},"leaders":{},"duration":{}}},"balances":[{"account_label":"BINANCE","balance":0,"currency":"USDT"}]}
    res = update_master_journal_workbook_data_only(p, snap)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(p)
    out = load_workbook(p)["Dashboard"]
    assert out["B2"].value == 4 and out["B3"].value == 3
    assert out["E2"].value == 2 and out["E3"].value == 1
    assert out["H2"].value == 5 and out["H3"].value == 6



def test_read_master_journal_source_uses_read_only_workbook(monkeypatch, tmp_path):
    from tools import master_journal_workbook as mjw
    from tools.master_journal_workbook import build_master_journal_workbook
    path = tmp_path / 'Trading Journal.xlsx'
    build_master_journal_workbook({'items': [{'id': 't1', 'row_type': 'trade', 'account': 'BINANCE', 'symbol': 'BTCUSDT', 'side': 'BUY', 'open_time': '2026-01-01', 'close_time': '2026-01-01'}], 'stats': {'totals': {}, 'groups': {}}, 'balances': []}, path)
    real_load = mjw.load_workbook
    calls = []
    def wrapped_load_workbook(*args, **kwargs):
        calls.append(kwargs)
        return real_load(*args, **kwargs)
    monkeypatch.setattr(mjw, 'load_workbook', wrapped_load_workbook)
    payload = mjw.read_master_journal_source(path)
    assert payload['items']
    assert calls and calls[0].get('read_only') is True
    assert calls[0].get('data_only') is True


def test_read_master_journal_source_distance_percent_format_is_source_aware(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / "distance_percent.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    ws.append(["Open Time", "Close Time", "Account", "Symbol", "Side", "Stop Loss Distance", "Target Distance", "Row ID"])
    ws.append(["2026-01-01", "2026-01-01", "OANDA DEMO", "EURUSD", "BUY", 0.01, 1.0, "pct-row"])
    ws.cell(2, 6).number_format = "0.00%"
    ws.cell(2, 7).number_format = "General"
    wb.save(p)
    out = read_master_journal_source(p)
    row = next(r for r in out["items"] if r["id"] == "pct-row")
    assert row["stop_loss_distance_pct"] == pytest.approx(1.0)
    assert row["target_distance_pct"] == pytest.approx(1.0)


def test_generated_trade_log_distance_fraction_displays_one_percent(tmp_path: Path):
    out = tmp_path / "one_percent.xlsx"
    snap = sample_snapshot()
    snap["items"] = [{
        "id": "onepct", "row_type": "trade", "account": "OANDA DEMO", "symbol": "EURUSD",
        "side": "BUY", "open_time": "2026-01-01", "close_time": "2026-01-01",
        "entry_price": 100.0, "stop_loss": 99.0, "take_profit": 101.0,
        "net_profit": 1.0, "result_pct": 1.0,
    }]
    build_master_journal_workbook(snap, out)
    ws = load_workbook(out)["Trade Log"]
    assert ws["J2"].value == pytest.approx(0.01)
    assert ws["J2"].number_format == "0.00%"
    assert ws["L2"].value == pytest.approx(0.01)
    assert ws["L2"].number_format == "0.00%"

def test_trade_log_new_schema_distances_and_header_aware_update(tmp_path: Path):
    out = tmp_path / "new_schema.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    assert [ws.cell(1, c).value for c in range(1, len(TRADE_LOG_HEADERS) + 1)] == TRADE_LOG_HEADERS
    assert ws["J2"].number_format == "0.00%"
    assert ws["L2"].number_format == "0.00%"
    assert ws["J2"].value == pytest.approx(abs(1.09 - 1.1) / 1.1)
    assert ws["L2"].value == pytest.approx(abs(1.12 - 1.1) / 1.1)
    assert ws["K1"].value == "Target Price"
    assert ws["M1"].value == "Commission"
    assert ws["N1"].value == "Net P/L"
    assert ws["O1"].value == "Profit %"
    assert ws["Q1"].value == "Balance After"
    wb.save(out); wb.close()

    result = update_master_journal_workbook_data_only(out, snap)
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(out)
    updated = load_workbook(out)
    ws2 = updated["Trade Log"]
    assert [ws2.cell(1, c).value for c in range(1, len(TRADE_LOG_HEADERS) + 1)] == TRADE_LOG_HEADERS
    assert ws2["M2"].value in (None, "")
    assert ws2["N2"].value == 120.5
    assert ws2["O2"].value == pytest.approx(0.023)
    assert ws2["P2"].value == 1.2
    assert ws2["Q2"].value == 1000
    updated.close()


def test_dashboard_market_risk_cells_and_grey_no_metric_cells(tmp_path: Path):
    from openpyxl import Workbook
    out = tmp_path / "dashboard_market.xlsx"
    wb = Workbook()
    dash = wb.active
    dash.title = "Dashboard"
    wb.create_sheet("Trade Log")
    wb.create_sheet("Instrument Averages")
    wb.create_sheet("P&L Calendar")
    dash["B1"] = "Overall"; dash["C1"] = "FX"; dash["D1"] = "Crypto"
    dash["A33"] = "Winners"; dash["A34"] = "Avg result %"; dash["A35"] = "Avg stop %"; dash["A36"] = "Avg target %"; dash["A37"] = "Avg R"
    dash["A38"] = "Losers"; dash["A39"] = "Avg result %"; dash["A40"] = "Avg stop %"; dash["A41"] = "Avg target %"; dash["A42"] = "Avg R"
    dash["A43"] = "Drawdown"; dash["A44"] = "Max drawdown"; dash["A45"] = "Avg drawdown"
    dash["F1"] = "Instrument leaders"; dash["J1"] = "Account Balances"
    dash["J2"] = "Account"; dash["K2"] = "Balance"; dash["L2"] = "Currency"
    dash["D35"].fill = PatternFill("solid", fgColor="EAF2F8")
    _ensure_trade_log_headers(wb)
    wb.save(out); wb.close()

    snap = {
        "items": [],
        "stats": {"totals": {}, "groups": {
            "by_market": {
                "overall": {"max_drawdown_pct": 9.0, "avg_drawdown_pct": 4.5},
                "fx": {"max_drawdown_pct": 10.0, "avg_drawdown_pct": 5.0},
                "crypto": {"max_drawdown_pct": 12.0, "avg_drawdown_pct": 6.0},
            },
            "leaders": {}, "duration": {},
            "risk_expectancy": {
                "avg_result_pct_winners": 1.0,
                "avg_stop_pct_winners": 1.1,
                "avg_target_pct_winners": 2.0,
                "avg_r_multiple_winners": 1.25,
                "avg_result_pct_losers": -3.0,
                "avg_stop_pct_losers": 3.0,
                "avg_target_pct_losers": 4.0,
                "avg_r_multiple_losers": -0.75,
                "max_drawdown_pct": 9.0,
                "avg_drawdown_pct": 4.5,
                "by_market": {
                    "overall": {"avg_result_pct_winners": 1.0, "avg_stop_pct_winners": 1.1, "avg_target_pct_winners": 2.0, "avg_r_multiple_winners": 1.25, "avg_result_pct_losers": -3.0, "avg_stop_pct_losers": 3.0, "avg_target_pct_losers": 4.0, "avg_r_multiple_losers": -0.75, "max_drawdown_pct": 9.0, "avg_drawdown_pct": 4.5},
                    "fx": {"avg_result_pct_winners": 1.5, "avg_stop_pct_winners": 1.5, "avg_target_pct_winners": 2.5, "avg_r_multiple_winners": 1.5, "avg_result_pct_losers": -3.5, "avg_stop_pct_losers": 3.5, "avg_target_pct_losers": 4.5, "avg_r_multiple_losers": -0.5, "max_drawdown_pct": 10.0, "avg_drawdown_pct": 5.0},
                    "crypto": {"avg_result_pct_winners": 5.5, "avg_stop_pct_winners": 5.5, "avg_target_pct_winners": 6.5, "avg_r_multiple_winners": 2.5, "avg_result_pct_losers": -7.5, "avg_stop_pct_losers": 7.5, "avg_target_pct_losers": 8.5, "avg_r_multiple_losers": -1.5, "max_drawdown_pct": 12.0, "avg_drawdown_pct": 6.0},
                },
            },
        }},
        "balances": [],
    }
    result = update_master_journal_workbook_data_only(out, snap)
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(out)
    updated = load_workbook(out)
    d = updated["Dashboard"]
    assert d["C34"].value == pytest.approx(0.015)
    assert d["D34"].value == pytest.approx(0.055)
    assert d["C34"].number_format == "0.00%"
    assert d["D34"].number_format == "0.00%"
    assert d["C35"].value == pytest.approx(0.015)
    assert d["D35"].value in (None, "")
    assert d["C35"].number_format == "0.00%"
    assert d["D35"].number_format != "0.00%"
    assert d["C37"].value == pytest.approx(1.5)
    assert d["D37"].value == pytest.approx(2.5)
    assert d["C37"].number_format == '0.000"R"'
    assert d["D37"].number_format == '0.000"R"'
    assert d["C39"].value == pytest.approx(-0.035)
    assert d["D42"].number_format == '0.000"R"'
    assert d["C44"].value == pytest.approx(0.10)
    assert d["D44"].value == pytest.approx(0.12)
    assert d["C45"].value == pytest.approx(0.05)
    assert d["D45"].value == pytest.approx(0.06)
    assert d["C44"].number_format == "0.00%"
    assert d["D45"].number_format == "0.00%"
    updated.close()


def _old_trade_log_headers():
    return list(OLD_TRADE_LOG_HEADERS)


def _minimal_old_schema_workbook(path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Dashboard"
    ws["A1"] = "Overall"; ws["D1"] = "FX"; ws["G1"] = "Crypto"; ws["J1"] = "Winners"; ws["J8"] = "Losers"; ws["J14"] = "Drawdown"; ws["M1"] = "Instrument leaders"; ws["P1"] = "Account Balances"
    ws["P2"] = "Account"; ws["Q2"] = "Balance"; ws["R2"] = "Currency"; ws["P3"] = "BINANCE"
    tl = wb.create_sheet("Trade Log")
    for c, h in enumerate(_old_trade_log_headers(), start=1):
        tl.cell(1, c, h)
    tl.append(["2026-01-01", "2026-01-01", "BINANCE", "BTCUSDT", "BUY", 1, 10, 11, "", "", "", "", "", 1, 0.01, 1, 101, "00:00:01:00", "No", "Old setup", "1H", "No", "Old notes", "", "", "USDT", "trade", "old-row"])
    tl.freeze_panes = "A2"
    tl.auto_filter.ref = "A1:AB2"
    inst = wb.create_sheet("Instrument Averages")
    inst.append(["Symbol", "Trades"])
    inst.freeze_panes = "A2"
    inst.auto_filter.ref = "A1:B1"
    wb.create_sheet("P&L Calendar")
    wb.save(path)
    wb.close()


def _validation_for_col(ws, col_letter: str):
    col_idx = coordinate_to_tuple(f"{col_letter}2")[1]
    out = []
    for dv in ws.data_validations.dataValidation:
        if any(rng.min_col <= col_idx <= rng.max_col for rng in dv.cells.ranges):
            out.append(dv)
    return out


def test_trade_log_quality_columns_insert_after_test(tmp_path: Path):
    p = tmp_path / "old_schema.xlsx"
    _minimal_old_schema_workbook(p)
    snap = {"items": [{"id": "old-row", "row_type": "trade", "account": "BINANCE", "symbol": "BTCUSDT", "side": "BUY", "open_time": "2026-01-01", "close_time": "2026-01-01", "net_profit": 1}], "stats": {"totals": {}, "groups": {"by_market": {}, "risk_expectancy": {}, "leaders": {}}}, "balances": [{"account_label": "BINANCE", "balance": 0, "currency": "USDT"}]}
    res = update_master_journal_workbook_data_only(p, snap)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(p)
    ws = load_workbook(p)["Trade Log"]
    headers = [ws.cell(1, c).value for c in range(1, len(TRADE_LOG_HEADERS) + 1)]
    assert headers == TRADE_LOG_HEADERS
    duration_col = _header_col(ws, "Trade Duration (DD:HH:MM:SS)")
    assert headers[duration_col:duration_col + 10] == list(MOVE_TO_FIELD_MAP)
    assert "Close" not in headers
    assert _header_col(ws, "Close Stopout") > _header_col(ws, "Pattern")
    assert ws["AV1"].value == "Row ID"
    assert ws.column_dimensions["AV"].hidden is True
    assert ws.auto_filter.ref == f"A1:{get_column_letter(len(TRADE_LOG_HEADERS))}{ws.max_row}"

def test_trade_log_quality_dropdowns(tmp_path: Path):
    p = tmp_path / "quality_dropdowns.xlsx"
    build_master_journal_workbook(sample_snapshot(), p)
    ws = load_workbook(p)["Trade Log"]
    def validations(header):
        return _validation_for_col(ws, get_column_letter(_header_col(ws, header)))
    assert any(dv.formula1 == '"Yes,No"' and dv.allow_blank for dv in validations("Test"))
    assert any(dv.formula1 == '"range,channel"' and dv.allow_blank for dv in validations("Pattern"))
    assert any(dv.formula1 == '"All-Time High,All-Time Low"' and dv.allow_blank for dv in validations("ATHS/ATLS"))
    assert any(dv.formula1 == '"Market,Limit"' and dv.allow_blank for dv in validations("Order"))
    for header in ["Round Number", "Spiked Out", "Close Stopout", "Near Perfect Entry", "Near Win", "Early Close"]:
        assert any(dv.formula1 == '"Yes,No"' and dv.allow_blank for dv in validations(header))
    assert validations("EMA") == []

def test_trade_log_quality_manual_values_survive_resync(tmp_path: Path):
    p = tmp_path / "quality_resync.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, p)
    wb = load_workbook(p)
    ws = wb["Trade Log"]
    vals = {"Pattern": "legacy-manual", "EMA": "20/50", "ATHS/ATLS": "All-Time High", "Order": "Limit", "Round Number": "Yes", "Spiked Out": "No", "Close Stopout": "No", "Near Perfect Entry": "Yes", "Near Win": "No", "Early Close": "Yes", "Move to Break Even Time": "2026-01-01 10:00", "Move to Profit Trigger Price": "123.45"}
    for header, value in vals.items():
        ws.cell(2, _header_col(ws, header), value)
    row_id = ws.cell(2, _header_col(ws, "Row ID")).value
    wb.save(p); wb.close()
    res = update_master_journal_workbook_data_only(p, snap)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(p)
    ws = load_workbook(p)["Trade Log"]
    rid_col = _header_col(ws, "Row ID")
    row_num = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, rid_col).value == row_id)
    for header, value in vals.items():
        assert ws.cell(row_num, _header_col(ws, header)).value == value


def test_read_master_journal_manual_overrides_reads_quality_columns(tmp_path: Path):
    p = tmp_path / "manual_quality.xlsx"
    build_master_journal_workbook(sample_snapshot(), p)
    wb = load_workbook(p)
    ws = wb["Trade Log"]
    ws.cell(2, _header_col(ws, "Pattern"), "Pullback")
    ws.cell(2, _header_col(ws, "EMA"), "9")
    ws.cell(2, _header_col(ws, "ATHS/ATLS"), "All-Time Low")
    rid = ws.cell(2, _header_col(ws, "Row ID")).value
    wb.save(p); wb.close()
    ov = read_master_journal_manual_overrides(p)
    assert ov[rid]["pattern"] == "Pullback"
    assert ov[rid]["ema"] == "9"
    assert ov[rid]["aths_atls"] == "All-Time Low"


def test_read_master_journal_source_reads_quality_columns(tmp_path: Path):
    p = tmp_path / "source_quality.xlsx"
    build_master_journal_workbook(sample_snapshot(), p)
    wb = load_workbook(p)
    ws = wb["Trade Log"]
    ws.cell(2, _header_col(ws, "Pattern"), "Range")
    ws.cell(2, _header_col(ws, "Order"), "Market")
    ws.cell(2, _header_col(ws, "Near Win"), "Yes")
    rid = ws.cell(2, _header_col(ws, "Row ID")).value
    wb.save(p); wb.close()
    item = next(r for r in read_master_journal_source(p)["items"] if r["id"] == rid)
    assert item["pattern"] == "Range"
    assert item["order_type"] == "Market"
    assert item["near_win"] == "Yes"


def test_update_data_only_writes_dashboard_horizontal_core_metric_aliases(tmp_path: Path):
    from openpyxl import Workbook
    p = tmp_path / "horizontal.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Dashboard"
    wb.create_sheet("Trade Log"); wb.create_sheet("Instrument Averages"); wb.create_sheet("P&L Calendar")
    ws["B1"] = "Overall"; ws["C1"] = "FX"; ws["D1"] = "Crypto"
    ws["A11"] = "Best Win Streak"; ws["A12"] = "Worst Losing Streak"; ws["A17"] = "Max target %"
    ws["F1"] = "Winners"; ws["F8"] = "Losers"; ws["F14"] = "Drawdown"; ws["I1"] = "Instrument leaders"; ws["L1"] = "Account Balances"; ws["L2"] = "Account"; ws["M2"] = "Balance"; ws["N2"] = "Currency"; ws["L3"] = "BINANCE"
    _ensure_trade_log_headers(wb); wb.save(p); wb.close()
    snap = {"stats": {"totals": {}, "groups": {"by_market": {"overall": {"winning_streak": 4, "losing_streak": 3, "max_target_pct": 9.5}, "fx": {"winning_streak": 2, "losing_streak": 1, "max_target_pct": 5.0}, "crypto": {"winning_streak": 6, "losing_streak": 7, "max_target_pct": 12.25}}, "risk_expectancy": {}, "leaders": {}, "duration": {}}}, "balances": [{"account_label": "BINANCE", "balance": 0, "currency": "USDT"}]}
    res = update_master_journal_workbook_data_only(p, snap)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(p)
    ws = load_workbook(p)["Dashboard"]
    assert [ws.cell(11, c).value for c in range(2, 5)] == [4, 2, 6]
    assert [ws.cell(12, c).value for c in range(2, 5)] == [3, 1, 7]
    assert [ws.cell(17, c).value for c in range(2, 5)] == [pytest.approx(0.095), pytest.approx(0.05), pytest.approx(0.1225)]
    assert [ws.cell(17, c).number_format for c in range(2, 5)] == ["0.00%", "0.00%", "0.00%"]


def test_update_data_only_repairs_unknown_currency_formats_after_schema_migration(monkeypatch, tmp_path: Path):
    from tools import master_journal_workbook as mjw

    p = tmp_path / "old_schema_unknown_currency.xlsx"
    _minimal_old_schema_workbook(p)
    snap = {
        "items": [{
            "id": "old-row",
            "row_type": "trade",
            "account": "OANDA DEMO",
            "symbol": "EURUSD",
            "side": "BUY",
            "open_time": "2026-01-01",
            "close_time": "2026-01-01",
            "commission": 1.25,
            "net_profit": 10.0,
            "result_pct": 1.0,
        }],
        "stats": {"totals": {}, "groups": {"by_market": {}, "risk_expectancy": {}, "leaders": {}}},
        "balances": [{"account_label": "BINANCE", "balance": 0, "currency": "USDT"}],
    }
    real_build = mjw.build_master_journal_workbook

    def build_with_unknown_formats(snapshot, output_path):
        result = real_build(snapshot, output_path)
        wb = load_workbook(output_path)
        ws = wb["Trade Log"]
        ws.cell(2, _header_col(ws, "Commission")).number_format = '#,##0.00 "UNKNOWN"'
        ws.cell(2, _header_col(ws, "Net P/L")).number_format = '#,##0.00 "UNKNOWN"'
        wb.save(output_path)
        wb.close()
        return result

    monkeypatch.setattr(mjw, "build_master_journal_workbook", build_with_unknown_formats)
    res = mjw.update_master_journal_workbook_data_only(p, snap)
    assert res["ok"] is True
    Path(res["candidate_path"]).replace(p)
    ws = load_workbook(p)["Trade Log"]
    assert ws.cell(1, 13).value == "Commission"
    assert ws.cell(1, 14).value == "Net P/L"
    commission_format = str(ws.cell(2, _header_col(ws, "Commission")).number_format or "")
    net_pl_format = str(ws.cell(2, _header_col(ws, "Net P/L")).number_format or "")
    assert "UNKNOWN" not in commission_format
    assert "UNKNOWN" not in net_pl_format
    assert "AUD" in commission_format
    assert "AUD" in net_pl_format


def _cf_rule_details(ws):
    details = []
    for key, rules in ws.conditional_formatting._cf_rules.items():
        sqref = str(key.sqref)
        for rule in rules:
            formula = getattr(rule, "formula", None) or []
            dxf = getattr(rule, "dxf", None)
            fill = getattr(getattr(dxf, "fill", None), "fgColor", None)
            details.append((sqref, [str(f) for f in formula], (str(getattr(fill, "rgb", "") or "")[-6:].upper() if fill else "")))
    return details


def test_trade_log_win_loss_row_conditional_formatting_uses_current_schema(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    ws = load_workbook(out)["Trade Log"]
    row_type_letter = get_column_letter(_header_col(ws, "Row Type"))
    net_pl_letter = get_column_letter(_header_col(ws, "Net P/L"))
    expected_range = f"A2:{get_column_letter(len(TRADE_LOG_HEADERS))}{ws.max_row}"
    row_rules = [d for d in _cf_rule_details(ws) if d[0] == expected_range]
    formulas = {tuple(d[1]) for d in row_rules}
    assert (f'AND(${row_type_letter}2="trade",${net_pl_letter}2>0)',) in formulas
    assert (f'AND(${row_type_letter}2="trade",${net_pl_letter}2<0)',) in formulas
    assert expected_range == "A2:AV3"
    all_rule_text = " ".join(" ".join(formulas) for _range, formulas, _fill in _cf_rule_details(ws))
    assert "$AA" not in all_rule_text
    assert "A2:AB" not in " ".join(_cf_ranges(ws))


def test_trade_log_stale_conditional_formatting_removed_on_update(tmp_path: Path):
    from openpyxl.formatting.rule import FormulaRule

    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    ws.conditional_formatting.add(
        "A2:AB99",
        FormulaRule(formula=['AND($AA2="trade",$N2<0)'], fill=PatternFill("solid", fgColor="FFC7CE")),
    )
    wb.save(out)
    wb.close()

    result = update_master_journal_workbook_data_only(out, sample_snapshot())
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(out)
    ws = load_workbook(out)["Trade Log"]
    all_ranges = " ".join(_cf_ranges(ws))
    all_formulas = " ".join(" ".join(formula) for _range, formula, _fill in _cf_rule_details(ws))
    assert "A2:AB" not in all_ranges
    assert "$AA" not in all_formulas
    assert "A2:AV3" in all_ranges


def test_conditional_formatting_uses_worksheet_api_not_unbound_class_method():
    src = Path("tools/master_journal_workbook.py").read_text(encoding="utf-8")
    test_src = Path("tests/test_master_journal_workbook.py").read_text(encoding="utf-8")
    forbidden = "ConditionalFormattingList" + ".add"
    assert forbidden not in src
    assert forbidden not in test_src
    assert "max_priority" not in src


def test_dashboard_trade_log_and_pnl_calendar_loss_profit_fills_match(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    wb = load_workbook(out)
    dash = wb["Dashboard"]
    trade = wb["Trade Log"]
    cal = wb["P&L Calendar"]

    dashboard_fills = {fill for _range, _formula, fill in _cf_rule_details(dash) if fill}
    assert "FFC7CE" in dashboard_fills
    assert "C6EFCE" in dashboard_fills

    trade_row_rules = [d for d in _cf_rule_details(trade) if d[0] == "A2:AV3"]
    trade_fills = {fill for _range, _formula, fill in trade_row_rules}
    assert trade_fills == {"FFC7CE", "C6EFCE"}

    pnl_fills = {fill for cf_range, _formula, fill in _cf_rule_details(cal) if cf_range.startswith("B")}
    assert pnl_fills == {"FFC7CE", "C6EFCE"}


def test_pnl_calendar_update_removes_duplicate_generated_profit_loss_rules(tmp_path: Path):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule

    p = tmp_path / "calendar_stale_cf.xlsx"
    wb = Workbook()
    dash = wb.active
    dash.title = "Dashboard"
    wb.create_sheet("Trade Log")
    wb.create_sheet("Instrument Averages")
    cal = wb.create_sheet("P&L Calendar")
    dash["A1"] = "Overall"
    dash["D1"] = "FX"
    dash["G1"] = "Crypto"
    dash["J1"] = "Winners"
    dash["J8"] = "Losers"
    dash["J14"] = "Drawdown"
    dash["M1"] = "Instrument leaders"
    dash["T1"] = "Account Balances"
    dash["T2"] = "Account"
    dash["U2"] = "Balance"
    dash["V2"] = "Currency"
    dash["W2"] = "As Of"
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    for col, month in enumerate(months, start=3):
        cal.cell(1, col).value = month
    cal.merge_cells("A2:A3")
    cal["A2"] = 2026
    cal["B2"] = "P/L %"
    cal["B3"] = "Total Trades"
    cal.conditional_formatting.add(
        "C2:N2",
        CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FFFF00")),
    )
    cal.conditional_formatting.add(
        "C2:N2",
        CellIsRule(operator="lessThan", formula=["0"], fill=PatternFill("solid", fgColor="0000FF")),
    )
    _ensure_trade_log_headers(wb)
    wb.save(p)
    wb.close()

    snap = {
        "items": [{"id": "t1", "row_type": "trade", "account": "BYBIT", "symbol": "BTCUSDT", "open_time": "2026-05-01", "close_time": "2026-05-01", "net_profit": 10.0, "result_pct": 1.0}],
        "stats": {"totals": {}, "groups": {"by_market": {"overall": {}, "fx": {}, "crypto": {}}, "risk_expectancy": {}, "leaders": {}, "duration": {}}},
        "balances": [],
    }
    result = update_master_journal_workbook_data_only(p, snap)
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(p)
    wb = load_workbook(p)
    cal = wb["P&L Calendar"]
    details = [d for d in _cf_rule_details(cal) if d[0] == "C2:N2"]
    assert [fill for _range, _formula, fill in details] == ["C6EFCE", "FFC7CE"]
    assert "FFFF00" not in {fill for _range, _formula, fill in _cf_rule_details(cal)}
    assert "0000FF" not in {fill for _range, _formula, fill in _cf_rule_details(cal)}
    assert "A2:AV2" in " ".join(_cf_ranges(wb["Trade Log"]))


def test_generated_pnl_calendar_update_removes_stale_profit_loss_rules(tmp_path: Path):
    from openpyxl.formatting.rule import CellIsRule

    p = tmp_path / "generated_calendar_stale_cf.xlsx"
    build_master_journal_workbook(sample_snapshot(), p)
    wb = load_workbook(p)
    cal = wb["P&L Calendar"]
    cal.conditional_formatting.add(
        "B3:M3",
        CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FFFF00")),
    )
    cal.conditional_formatting.add(
        "B3:M3",
        CellIsRule(operator="lessThan", formula=["0"], fill=PatternFill("solid", fgColor="0000FF")),
    )
    wb.save(p)
    wb.close()

    result = update_master_journal_workbook_data_only(p, sample_snapshot())
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(p)
    wb = load_workbook(p)
    cal = wb["P&L Calendar"]
    details = [d for d in _cf_rule_details(cal) if d[0] == "B3:M3"]
    assert [fill for _range, _formula, fill in details] == ["C6EFCE", "FFC7CE"]
    assert "A2:AV3" in " ".join(_cf_ranges(wb["Trade Log"]))


def _trade_log_row_rule_details(ws):
    row_rules = []
    for cf_range, formulas, fill in _cf_rule_details(ws):
        if cf_range.startswith("A2:AV") and formulas and formulas[0].startswith('AND($'):
            for rules in ws.conditional_formatting._cf_rules.values():
                for rule in rules:
                    rule_formulas = [str(f) for f in (getattr(rule, "formula", None) or [])]
                    if rule_formulas == formulas:
                        row_rules.append((cf_range, formulas, fill, rule))
    return row_rules

def _trade_log_generated_value_fill_ranges(ws):
    out = []
    for key, rules in ws.conditional_formatting._cf_rules.items():
        sqref = str(key.sqref)
        for part in sqref.split():
            try:
                from openpyxl.utils.cell import range_boundaries
                min_col, min_row, max_col, _max_row = range_boundaries(part)
            except ValueError:
                continue
            if min_row >= 2 and 13 <= min_col <= max_col <= 16:
                for rule in rules:
                    formula = " ".join(str(f) for f in (getattr(rule, "formula", None) or []))
                    if getattr(rule, "type", None) == "cellIs" and formula.strip() == "0":
                        out.append((part, getattr(rule, "operator", None)))
    return out

def test_trade_log_row_rules_stop_if_true_and_no_generated_value_fill_overlap(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    row_rules = _trade_log_row_rule_details(ws)
    assert len(row_rules) == 2
    assert {fill for _range, _formulas, fill, _rule in row_rules} == {"C6EFCE", "FFC7CE"}
    assert all(getattr(rule, "stopIfTrue", None) is True for _range, _formulas, _fill, rule in row_rules)
    assert _trade_log_generated_value_fill_ranges(ws) == []

def test_trade_log_update_removes_stale_generated_value_fill_overlap(tmp_path: Path):
    from openpyxl.formatting.rule import CellIsRule

    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    ws.conditional_formatting.add(
        "M2:M99",
        CellIsRule(operator="notEqual", formula=["0"], fill=PatternFill("solid", fgColor="FFFF00")),
    )
    ws.conditional_formatting.add(
        "N2:P99",
        CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FFFF00")),
    )
    ws.conditional_formatting.add(
        "N2:P99",
        CellIsRule(operator="lessThan", formula=["0"], fill=PatternFill("solid", fgColor="0000FF")),
    )
    wb.save(out)
    wb.close()

    result = update_master_journal_workbook_data_only(out, sample_snapshot())
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(out)
    ws = load_workbook(out)["Trade Log"]
    assert _trade_log_generated_value_fill_ranges(ws) == []
    row_rules = _trade_log_row_rule_details(ws)
    assert len(row_rules) == 2
    assert all(getattr(rule, "stopIfTrue", None) is True for _range, _formulas, _fill, rule in row_rules)

def test_trade_log_saved_xml_row_rules_stop_if_true_without_overlap(tmp_path: Path):
    import zipfile
    import xml.etree.ElementTree as ET

    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    wb = load_workbook(out)
    trade_sheet_index = wb.sheetnames.index("Trade Log") + 1
    wb.close()
    with zipfile.ZipFile(out) as zf:
        xml = zf.read(f"xl/worksheets/sheet{trade_sheet_index}.xml")
    root = ET.fromstring(xml)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    overlapping_value_refs = []
    row_rules = []
    for cf in root.findall("x:conditionalFormatting", ns):
        sqref = cf.attrib.get("sqref", "")
        rules = cf.findall("x:cfRule", ns)
        formulas = [rule.findtext("x:formula", default="", namespaces=ns) for rule in rules]
        if sqref.startswith("A2:AV") and any('="trade"' in formula for formula in formulas):
            row_rules.extend(rules)
        if sqref in {"M2:M3", "N2:P3"}:
            overlapping_value_refs.append(sqref)
    assert len(row_rules) == 2
    assert all(rule.attrib.get("stopIfTrue") == "1" for rule in row_rules)
    assert overlapping_value_refs == []


def _cell_fill_rgb(cell) -> str:
    rgb = str(getattr(cell.fill.fgColor, "rgb", "") or "")
    return rgb[-6:].upper() if getattr(cell.fill, "fill_type", None) == "solid" and rgb else ""


def _trade_log_row_by_id(ws, row_id: str) -> int:
    rid_col = _header_col(ws, "Row ID")
    for row in range(2, ws.max_row + 1):
        if str(ws.cell(row, rid_col).value or "") == row_id:
            return row
    raise AssertionError(f"missing row id {row_id!r}")


def test_trade_log_direct_row_fills_cover_winning_and_losing_rows(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(sample_snapshot(), out)
    ws = load_workbook(out)["Trade Log"]
    winning_row = _trade_log_row_by_id(ws, "t1")
    losing_row = _trade_log_row_by_id(ws, "t2")
    checked_cols = [1, 3, _header_col(ws, "Net P/L"), len(TRADE_LOG_HEADERS)]
    assert [get_column_letter(col) for col in checked_cols] == ["A", "C", "N", "AV"]
    assert all(_cell_fill_rgb(ws.cell(winning_row, col)) == "C6EFCE" for col in checked_cols)
    assert all(_cell_fill_rgb(ws.cell(losing_row, col)) == "FFC7CE" for col in checked_cols)


def test_trade_log_direct_row_fills_skip_non_trade_and_zero_rows(tmp_path: Path):
    snap = sample_snapshot()
    snap["items"] = [
        {"id": "win", "row_type": "trade", "account": "A", "symbol": "EURUSD", "open_time": "2026-01-01", "close_time": "2026-01-01", "net_profit": 1.0},
        {"id": "zero", "row_type": "trade", "account": "A", "symbol": "EURUSD", "open_time": "2026-01-02", "close_time": "2026-01-02", "net_profit": 0.0},
        {"id": "cash", "row_type": "cashflow", "account": "A", "symbol": "CASH", "open_time": "2026-01-03", "close_time": "2026-01-03", "net_profit": -50.0, "cashflow_amount": -50.0},
        {"id": "reval", "row_type": "monthly_aud_reval", "account": "A", "period_month": "2026-01-31", "net_profit": 25.0, "result_cash": 25.0},
    ]
    out = tmp_path / "Trading Journal.xlsx"
    build_master_journal_workbook(snap, out)
    ws = load_workbook(out)["Trade Log"]
    assert _cell_fill_rgb(ws.cell(_trade_log_row_by_id(ws, "win"), 1)) == "C6EFCE"
    for row_id in ["zero", "cash", "reval"]:
        row = _trade_log_row_by_id(ws, row_id)
        assert all(_cell_fill_rgb(ws.cell(row, col)) == "" for col in [1, 3, _header_col(ws, "Net P/L"), len(TRADE_LOG_HEADERS)])


def test_update_data_only_restores_trade_log_direct_row_fills(tmp_path: Path):
    out = tmp_path / "Trading Journal.xlsx"
    snap = sample_snapshot()
    build_master_journal_workbook(snap, out)
    wb = load_workbook(out)
    ws = wb["Trade Log"]
    winning_row = _trade_log_row_by_id(ws, "t1")
    for col in range(1, len(TRADE_LOG_HEADERS) + 1):
        ws.cell(winning_row, col).fill = PatternFill()
    wb.save(out)
    wb.close()

    result = update_master_journal_workbook_data_only(out, snap)
    assert result["ok"] is True
    Path(result["candidate_path"]).replace(out)
    ws = load_workbook(out)["Trade Log"]
    winning_row = _trade_log_row_by_id(ws, "t1")
    assert all(_cell_fill_rgb(ws.cell(winning_row, col)) == "C6EFCE" for col in [1, 3, _header_col(ws, "Net P/L"), len(TRADE_LOG_HEADERS)])
