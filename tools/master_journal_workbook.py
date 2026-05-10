from __future__ import annotations
from collections import defaultdict
from copy import copy
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List
from openpyxl import Workbook, load_workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib
from openpyxl.styles import PatternFill, Border, Side, Alignment, Color
import calendar

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar","Equity Curve","Diagnostics"]
EDITABLE_COLS=["Test","Setup","Timeframe","Breakeven","Notes"]


def _is_test_trade_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) == 1.0
    text = str(value or '').strip().lower()
    return text in {'yes','y','true','1'}


def _as_float(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def _as_date(v: Any) -> date | None:
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("Z", "")
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _fmt_duration(seconds: Any) -> str:
    s = int(_as_float(seconds) or 0)
    if s <= 0:
        return "0s"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def stable_row_id(row: Dict[str, Any]) -> str:
    rid=str(row.get('id') or row.get('__row_id') or '').strip()
    if rid:
        return rid
    refs=row.get('raw_refs') if isinstance(row.get('raw_refs'),dict) else {}
    parts=[str(row.get('account_label') or row.get('account') or ''),str(row.get('symbol') or ''),str(row.get('side') or ''),str(row.get('open_time') or ''),str(row.get('close_time') or ''),str(row.get('qty') or row.get('qty_raw') or ''),str(row.get('entry_price') or ''),str(row.get('exit_price') or ''),str(row.get('net_profit') or ''),str(row.get('source') or ''),str(row.get('source_file') or ''),str(row.get('workbook_name') or ''),str(refs.get('source_file') or ''),str(refs.get('workbook') or ''),str(refs.get('sheet') or ''),str(refs.get('source_row') or '')]
    return 'sig:'+hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:24]


def read_master_journal_manual_overrides(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    wb=load_workbook(path, data_only=True)
    try:
        if 'All Trades' not in wb.sheetnames:
            return out
        ws=wb['All Trades']
        headers=[str(c.value or '').strip() for c in ws[1]]
        idx={h:i for i,h in enumerate(headers)}
        rid_i=idx.get('__row_id')
        if rid_i is None:
            return out
        for r in ws.iter_rows(min_row=2, values_only=True):
            rid=str(r[rid_i] or '').strip()
            if not rid:
                continue
            edits={}
            test_i=idx.get('Test')
            if test_i is not None:
                t=str(r[test_i] or '').strip().lower()
                edits['is_test_trade']=t in {'yes','true','1'}
            for col,field in [('Setup','setup'),('Timeframe','timeframe'),('Breakeven','breakeven'),('Notes','notes')]:
                i=idx.get(col)
                if i is None:
                    continue
                edits[field] = '' if r[i] is None else str(r[i])
            out[rid]=edits
    finally:
        wb.close()
    return out


def build_master_journal_workbook(snapshot: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    wb=Workbook(); wb.remove(wb.active)
    for s in SHEET_ORDER: wb.create_sheet(s)
    rows=[r for r in (snapshot.get('items') or []) if isinstance(r,dict) and str(r.get('row_type') or 'trade')=='trade']
    non_test=[r for r in rows if not _is_test_trade_value(r.get('is_test_trade'))]
    stats = snapshot.get('stats') or {}
    totals = stats.get('totals') or {}
    groups = stats.get('groups') or {}

    dash=wb['Dashboard']; dash['A1']='Account Balances'; dash['A1'].font=Font(bold=True)
    rr=2
    for b in (snapshot.get('balances') or []):
        dash.cell(rr,1,str((b or {}).get('label') or (b or {}).get('account') or 'Account'))
        dash.cell(rr,2,(b or {}).get('balance')); rr+=1
    dash.cell(max(rr+1,4),1,'Main Stats').font=Font(bold=True)
    section_rows=[
        ("Overall",[("Trades",totals.get("trades"),"neutral"),("Wins",totals.get("wins"),"profit"),("Losses",totals.get("losses"),"loss"),("Break-even",totals.get("break_even"),"neutral"),("Win rate",totals.get("win_rate_pct"),"neutral"),("Net P/L",totals.get("net_profit_total"),"auto"),("Gross gain",totals.get("gross_gain"),"profit"),("Gross loss",totals.get("gross_loss"),"loss"),("Avg result %",(groups.get("risk_expectancy") or {}).get("avg_result_pct"),"neutral"),("Max loss %",(groups.get("by_market") or {}).get("overall",{}).get("min_result_pct"),"loss"),("Max win %",(groups.get("by_market") or {}).get("overall",{}).get("max_result_pct"),"profit"),("Avg R",(groups.get("risk_expectancy") or {}).get("avg_r_multiple"),"neutral")]),
        ("Winners",[("Winner avg stop %",(groups.get("risk_expectancy") or {}).get("avg_stop_pct_winners"),"neutral"),("Winner avg target %",(groups.get("risk_expectancy") or {}).get("avg_target_pct_winners"),"neutral"),("Winner avg result %",(groups.get("risk_expectancy") or {}).get("avg_result_pct_winners"),"profit"),("Winner avg R",(groups.get("risk_expectancy") or {}).get("avg_r_multiple_winners"),"profit"),("Max R win",(groups.get("by_market") or {}).get("overall",{}).get("max_r_multiple"),"profit"),("Max gain",totals.get("max_gain"),"profit")]),
        ("Losers",[("Loser avg stop %",(groups.get("risk_expectancy") or {}).get("avg_stop_pct_losers"),"neutral"),("Loser avg target %",(groups.get("risk_expectancy") or {}).get("avg_target_pct_losers"),"neutral"),("Loser avg result %",(groups.get("risk_expectancy") or {}).get("avg_result_pct_losers"),"loss"),("Loser avg R",(groups.get("risk_expectancy") or {}).get("avg_r_multiple_losers"),"loss"),("Max R loss",(groups.get("by_market") or {}).get("overall",{}).get("min_r_multiple"),"loss"),("Max loss",totals.get("min_loss"),"loss")]),
        ("Drawdown",[("Max drawdown",(groups.get("risk_expectancy") or {}).get("max_drawdown_pct"),"drawdown"),("Avg drawdown",(groups.get("risk_expectancy") or {}).get("avg_drawdown_pct"),"drawdown")]),
        ("Duration",[("Avg duration",(groups.get("duration") or {}).get("overall_avg_seconds"),"neutral"),("Overall shortest duration",(groups.get("duration") or {}).get("overall_shortest_seconds"),"neutral"),("Overall longest duration",(groups.get("duration") or {}).get("overall_longest_seconds"),"neutral")]),
        ("FX",[("FX shortest",(groups.get("duration") or {}).get("fx_shortest_seconds"),"neutral"),("FX longest",(groups.get("duration") or {}).get("fx_longest_seconds"),"neutral"),("FX most wins",(groups.get("leaders") or {}).get("fx_most_wins_instrument"),"neutral"),("FX most losses",(groups.get("leaders") or {}).get("fx_most_losses_instrument"),"neutral")]),
        ("Crypto",[("Crypto shortest",(groups.get("duration") or {}).get("crypto_shortest_seconds"),"neutral"),("Crypto longest",(groups.get("duration") or {}).get("crypto_longest_seconds"),"neutral"),("Crypto most wins",(groups.get("leaders") or {}).get("crypto_most_wins_instrument"),"neutral"),("Crypto most losses",(groups.get("leaders") or {}).get("crypto_most_losses_instrument"),"neutral")]),
        ("Instrument leaders",[("Overall most wins",(groups.get("leaders") or {}).get("overall_most_wins_instrument"),"neutral"),("Overall most losses",(groups.get("leaders") or {}).get("overall_most_losses_instrument"),"neutral")]),
    ]
    grid_cols=[1,5,9]
    start_row=10
    for i,(title,srows) in enumerate(section_rows):
        col=grid_cols[i%3]; row=start_row + (i//3)*14
        _write_stat_section(dash,row,col,title,srows)

    ws=wb['All Trades']; headers=['__row_id','Open Time','Close Time','Account','Symbol','Side','Qty','Entry','Exit','Stop Loss','Target','Commission','Net P/L','Profit %','R-Multiple','Balance After','Trade Duration (s)','Source','Order ID','Fill Count']+EDITABLE_COLS; ws.append(headers)
    ws.row_dimensions[1].height=24
    for row in rows:
        ws.append([stable_row_id(row),row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('stop_loss'),row.get('take_profit'),row.get('commission'),row.get('net_profit'),row.get('result_pct'),row.get('r_multiple'),row.get('balance_after_trade'),row.get('trade_duration_seconds'),row.get('source'),row.get('order_id'),row.get('fill_count'),'Yes' if _is_test_trade_value(row.get('is_test_trade')) else 'No',row.get('setup') or '',row.get('timeframe') or '',row.get('breakeven') or '',row.get('notes') or ''])
    ws.column_dimensions['A'].hidden=True
    _style_table_sheet(ws,1,'A2',True)
    ws.column_dimensions['R'].width=20
    ws.column_dimensions['Y'].width=40
    for i in range(2,ws.max_row+1):
        ws.row_dimensions[i].height=20
        ws.cell(i,18).alignment=Alignment(wrap_text=False,vertical='center')
        ws.cell(i,25).alignment=Alignment(wrap_text=False,vertical='center')
        c=ws.cell(i,13)
        if isinstance(c.value,(int,float)):
            _apply_sign_font(c)
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"U2:U{max(2,ws.max_row)}")

    inst=wb['Instrument Averages']
    headers=["Symbol","Class","Trades","Longs","Shorts","Wins","Losses","Break-even","Long wins","Long losses","Short wins","Short losses","Net P/L","Avg P/L","Win Rate %","Avg stop dist (W)","Avg stop dist (L)","Avg target dist (W)","Avg target dist (L)","Avg duration","Shortest","Longest"]
    inst.append(headers)
    by_instrument=stats.get("by_instrument") or []
    for rec in by_instrument:
        cls=str(rec.get("asset_class") or rec.get("class") or "").lower()
        is_fx=cls=="fx"
        inst.append([rec.get("symbol"),cls.upper() if cls else None,rec.get("trades"),rec.get("longs"),rec.get("shorts"),rec.get("wins"),rec.get("losses"),rec.get("break_even"),rec.get("long_wins"),rec.get("long_losses"),rec.get("short_wins"),rec.get("short_losses"),rec.get("net_profit_total"),rec.get("avg_net_profit"),rec.get("win_rate_pct"),rec.get("avg_sl_distance_pips_wins") if is_fx else rec.get("avg_sl_distance_quote_wins"),rec.get("avg_sl_distance_pips_losses") if is_fx else rec.get("avg_sl_distance_quote_losses"),rec.get("avg_tp_distance_pips_wins") if is_fx else rec.get("avg_tp_distance_quote_wins"),rec.get("avg_tp_distance_pips_losses") if is_fx else rec.get("avg_tp_distance_quote_losses"),_fmt_duration(rec.get("avg_duration_seconds")),_fmt_duration(rec.get("shortest_duration_seconds")),_fmt_duration(rec.get("longest_duration_seconds"))])
    if inst.max_row==1: inst.append(['No data available']+['']*(len(headers)-1))
    _style_table_sheet(inst,1,'A2',True)

    cal=wb['P&L Calendar']
    daily=defaultdict(lambda:{"pnl":0.0,"trades":0,"fx":0,"crypto":0})
    for r in non_test:
        d=_as_date(r.get('close_time') or r.get('open_time'))
        pnl=_as_float(r.get('net_profit'))
        if not d or pnl is None: continue
        x=daily[d]; x["pnl"]+=pnl; x["trades"]+=1
        cls=str(r.get("asset_class") or "").lower()
        if cls=="fx": x["fx"]+=1
        elif cls=="crypto": x["crypto"]+=1
    months=defaultdict(list)
    for d in sorted(daily): months[(d.year,d.month)].append(d)
    row=1
    cal.append(["Raw Type","Raw Date","Raw Net P/L"])
    for d,v in sorted(daily.items()): cal.append(["Daily",str(d),v["pnl"]])
    for m,v in defaultdict(float,{}).items(): pass
    row=1
    for (y,m) in sorted(months.keys()):
        cal.merge_cells(start_row=row,start_column=1,end_row=row,end_column=7)
        cal.cell(row,1,f"{calendar.month_name[m]} {y}").font=Font(bold=True)
        row+=1
        for i,day in enumerate(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],start=1): cal.cell(row,i,day).font=Font(bold=True)
        row+=1
        month_mat=calendar.monthcalendar(y,m)
        for wk in month_mat:
            cal.row_dimensions[row].height=42
            for c,daynum in enumerate(wk,start=1):
                cell=cal.cell(row,c)
                if daynum==0:
                    cell.fill=PatternFill('solid', fgColor='00F3F4F6'); continue
                d=date(y,m,daynum); info=daily.get(d)
                if info:
                    cell.value=f"{daynum}\nT:{info['trades']} FX:{info['fx']} C:{info['crypto']}\nP/L {round(info['pnl'],2)}"
                    if info['pnl']>0: cell.fill=PatternFill('solid', fgColor='00DCFCE7')
                    elif info['pnl']<0: cell.fill=PatternFill('solid', fgColor='00FEE2E2')
                else:
                    cell.value=str(daynum); cell.fill=PatternFill('solid', fgColor='00FFFFFF')
                cell.alignment=Alignment(wrap_text=True,vertical='top')
            row+=1
        row+=1

    eq=wb['Equity Curve']
    eq.append(["Date","Account","Delta P/L","Equity"])
    acct_running=defaultdict(float)
    series_rows=[]
    for r in sorted(non_test,key=lambda x: str(x.get('close_time') or x.get('open_time') or '')):
        d=_as_date(r.get('close_time') or r.get('open_time'))
        pnl=_as_float(r.get('net_profit'))
        if not d or pnl is None: continue
        acct=str(r.get('account_label') or r.get('account') or 'Account')
        bal=_as_float(r.get('analysis_balance_after_trade')) or _as_float(r.get('balance_after_trade')) or _as_float(r.get('cashflow_new_balance'))
        if bal is None:
            acct_running[acct]+=pnl; bal=acct_running[acct]
        eq.append([str(d),acct,pnl,bal]); series_rows.append((str(d),acct,bal))
    _style_table_sheet(eq,1,'A2',True)
    if eq.max_row>=2:
        chart=LineChart(); chart.title="Equity Curve"; chart.y_axis.title="Equity / Balance"; chart.x_axis.title="Date"
        data=Reference(eq,min_col=4,min_row=1,max_row=eq.max_row)
        chart.add_data(data,titles_from_data=True)
        cats=Reference(eq,min_col=1,min_row=2,max_row=eq.max_row)
        chart.set_categories(cats)
        eq.add_chart(chart,"E2")

    diag=wb['Diagnostics']
    diagnostics=snapshot.get('diagnostics') if isinstance(snapshot.get('diagnostics'),dict) else {}
    diag.append(['Key','Value'])
    diag.append(['sync_timestamp',snapshot.get('updated_at') or datetime.utcnow().isoformat()])
    diag.append(['visible_trade_count',len(rows)])
    diag.append(['excluded_test_trade_count',sum(1 for r in rows if _is_test_trade_value(r.get('is_test_trade')))])
    diag.append(['blank_pnl_count',sum(1 for r in rows if _as_float(r.get('net_profit')) is None)])
    for name in (diagnostics.get('local_workbook_names') or []): diag.append(['local_workbook',name])
    for w in (snapshot.get('warnings') or diagnostics.get('warnings') or []): diag.append(['warning',str(w)])
    for e in (diagnostics.get('errors') or []): diag.append(['error',str(e)])
    _style_table_sheet(diag,1,'A2',True)
    _wrap_columns(diag,['B'])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return {'ok':True,'path':str(output_path)}


def _format_stat_value(cell, semantic='auto'):
    if semantic == 'neutral':
        return
    v=cell.value
    if not isinstance(v,(int,float)):
        return
    if semantic in {'loss','drawdown'} or (semantic=='auto' and v<0):
        f=copy(cell.font); f.color=Color(rgb='00FF0000'); cell.font=f
    elif semantic=='profit' or (semantic=='auto' and v>0):
        f=copy(cell.font); f.color=Color(rgb='00008000'); cell.font=f


def _format_stat_card(ws, top_row, left_col, bottom_row, right_col):
    thin=Side(style='thin', color='D1D5DB')
    for r in range(top_row,bottom_row+1):
        for c in range(left_col,right_col+1):
            ws.cell(r,c).border=Border(left=thin,right=thin,top=thin,bottom=thin)


def _write_stat_section(ws, start_row, start_col, title, rows):
    ws.merge_cells(start_row=start_row,start_column=start_col,end_row=start_row,end_column=start_col+2)
    h=ws.cell(start_row,start_col,title); h.font=Font(bold=True); h.fill=PatternFill('solid',fgColor='00E5E7EB')
    ws.cell(start_row+1,start_col,'Label').font=Font(bold=True)
    ws.cell(start_row+1,start_col+1,'Value').font=Font(bold=True)
    ws.cell(start_row+1,start_col+2,'Detail').font=Font(bold=True)
    r=start_row+2
    for label,val,sem in rows:
        ws.cell(r,start_col,label)
        ws.cell(r,start_col+1,_fmt_duration(val) if 'duration' in label.lower() and isinstance(val,(int,float)) else val)
        _format_stat_value(ws.cell(r,start_col+1),sem)
        r+=1
    _format_stat_card(ws,start_row,start_col,r-1,start_col+2)


def _style_header_row(ws, row=1):
    fill=PatternFill('solid', fgColor='E5E7EB')
    thin=Side(style='thin', color='D1D5DB')
    for c in ws[row]:
        c.font=Font(bold=True)
        c.fill=fill
        c.border=Border(left=thin,right=thin,top=thin,bottom=thin)

def _wrap_columns(ws, letters):
    for l in letters:
        for r in range(2, ws.max_row+1):
            ws[f'{l}{r}'].alignment=Alignment(wrap_text=True, vertical='top')


def _apply_sign_font(cell, *, loss_label: bool = False):
    v = cell.value
    if not isinstance(v, (int, float)):
        return
    f = copy(cell.font)
    if loss_label and v >= 0:
        f.color = Color(rgb='00FF0000')
        cell.font = f
    elif v > 0:
        f.color = Color(rgb='00008000')
        cell.font = f
    elif v < 0:
        f.color = Color(rgb='00FF0000')
        cell.font = f


def _apply_data_borders(ws):
    thin=Side(style='thin', color='E5E7EB')
    for r in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
        for c in r:
            c.border=Border(left=thin,right=thin,top=thin,bottom=thin)

def _style_table_sheet(ws, header_row=1, freeze='A2', autofilter=True):
    _style_header_row(ws, header_row)
    ws.freeze_panes=freeze
    if autofilter:
        ws.auto_filter.ref=f"A{header_row}:{chr(64+ws.max_column)}{max(header_row+1,ws.max_row)}"
    _apply_data_borders(ws)
