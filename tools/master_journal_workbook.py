from __future__ import annotations
from collections import defaultdict
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib
from openpyxl.styles import PatternFill, Border, Side, Alignment, Color

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


def stable_row_id(row: Dict[str, Any]) -> str:
    rid=str(row.get('id') or row.get('__row_id') or '').strip()
    if rid:
        return rid
    refs=row.get('raw_refs') if isinstance(row.get('raw_refs'),dict) else {}
    parts=[
        str(row.get('account_label') or row.get('account') or ''),str(row.get('symbol') or ''),str(row.get('side') or ''),
        str(row.get('open_time') or ''),str(row.get('close_time') or ''),str(row.get('qty') or row.get('qty_raw') or ''),
        str(row.get('entry_price') or ''),str(row.get('exit_price') or ''),str(row.get('net_profit') or ''),
        str(row.get('source') or ''),str(row.get('source_file') or ''),str(row.get('workbook_name') or ''),
        str(refs.get('source_file') or ''),str(refs.get('workbook') or ''),str(refs.get('sheet') or ''),str(refs.get('source_row') or ''),
    ]
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

    dash=wb['Dashboard']; dash['A1']='Account Balances'; dash['A1'].font=Font(bold=True)
    rr=2
    for b in (snapshot.get('balances') or []):
        dash.cell(rr,1,str((b or {}).get('label') or (b or {}).get('account') or 'Account'))
        dash.cell(rr,2,(b or {}).get('balance')); rr+=1
    totals=((snapshot.get('stats') or {}).get('totals') or {})
    rr=max(rr+1,4); dash.cell(rr,1,'Main Stats').font=Font(bold=True); rr+=1
    mapping=[('trades','Total Trades'),('wins','Wins'),('losses','Losses'),('win_rate_pct','Win Rate %'),('net_profit_total','Net P/L'),('gross_gain','Gross Profit'),('gross_loss','Gross Loss')]
    for k,l in mapping:
        if k in totals:
            dash.cell(rr,1,l); c=dash.cell(rr,2,totals.get(k))
            if isinstance(c.value,(int,float)):
                _apply_sign_font(c, loss_label=(k == 'gross_loss'))
            rr+=1

    ws=wb['All Trades']; headers=['__row_id','Open Time','Close Time','Account','Symbol','Side','Qty','Entry','Exit','Stop Loss','Target','Commission','Net P/L','Profit %','R-Multiple','Balance After','Trade Duration (s)','Source','Order ID','Fill Count']+EDITABLE_COLS; ws.append(headers)
    for row in rows:
        ws.append([stable_row_id(row),row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('stop_loss'),row.get('take_profit'),row.get('commission'),row.get('net_profit'),row.get('result_pct'),row.get('r_multiple'),row.get('balance_after_trade'),row.get('trade_duration_seconds'),row.get('source'),row.get('order_id'),row.get('fill_count'),'Yes' if _is_test_trade_value(row.get('is_test_trade')) else 'No',row.get('setup') or '',row.get('timeframe') or '',row.get('breakeven') or '',row.get('notes') or ''])
    ws.column_dimensions['A'].hidden=True
    _style_table_sheet(ws,1,'A2',True)
    ws.column_dimensions['R'].width=24
    ws.column_dimensions['Y'].width=30
    _wrap_columns(ws,['R','Y'])
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"U2:U{max(2,ws.max_row)}")
    for i in range(2,ws.max_row+1):
        c=ws.cell(i,13)
        if isinstance(c.value,(int,float)):
            _apply_sign_font(c)

    # Instrument Averages
    inst=wb['Instrument Averages']; inst.append(['Symbol','Trades','Wins','Losses','Net P/L','Avg P/L'])
    bucket=defaultdict(list)
    for r in rows:
        if _is_test_trade_value(r.get('is_test_trade')):
            continue
        bucket[str(r.get('symbol') or 'UNKNOWN')].append(r)
    for sym, grp in sorted(bucket.items()):
        pnls=[_as_float(x.get('net_profit')) for x in grp]
        vals=[p for p in pnls if p is not None]
        wins=sum(1 for p in vals if p>0); losses=sum(1 for p in vals if p<0); net=sum(vals) if vals else 0.0
        inst.append([sym,len(grp),wins,losses,net,(net/len(vals)) if vals else None])
    if inst.max_row==1: inst.append(['No data available','','','','',''])
    _style_table_sheet(inst,1,'A2',True)

    # P&L Calendar (daily + monthly)
    cal=wb['P&L Calendar']; cal.append(['Type','Date','Net P/L'])
    daily=defaultdict(float)
    for r in rows:
        if _is_test_trade_value(r.get('is_test_trade')):
            continue
        dt=str(r.get('close_time') or r.get('open_time') or '')[:10]
        pnl=_as_float(r.get('net_profit'))
        if dt and pnl is not None: daily[dt]+=pnl
    for d,p in sorted(daily.items()): cal.append(['Daily',d,p])
    monthly=defaultdict(float)
    for d,p in daily.items(): monthly[d[:7]]+=p
    for m,p in sorted(monthly.items()): cal.append(['Monthly',m,p])
    if cal.max_row==1: cal.append(['No data available','',''])
    _style_table_sheet(cal,1,'A2',True)

    # Equity Curve
    eq=wb['Equity Curve']; eq.append(['Date','Delta P/L','Equity'])
    running=0.0
    for d,p in sorted(daily.items()):
        running += p
        eq.append([d,p,running])
    if eq.max_row==1: eq.append(['No data available','',''])
    _style_table_sheet(eq,1,'A2',True)

    for sheet_name, freeze, filt in [("Instrument Averages","A2","A1:F{row}"),("P&L Calendar","A2","A1:C{row}"),("Equity Curve","A2","A1:C{row}")]:
        sh=wb[sheet_name]
        sh.freeze_panes=freeze
        sh.auto_filter.ref=filt.format(row=max(2, sh.max_row))

    diag=wb['Diagnostics']
    diagnostics=snapshot.get('diagnostics') if isinstance(snapshot.get('diagnostics'),dict) else {}
    diag.append(['Key','Value'])
    _style_table_sheet(diag,1,'A2',True)
    diag.column_dimensions['B'].width=60
    _wrap_columns(diag,['B'])
    diag.append(['sync_timestamp',snapshot.get('updated_at') or datetime.utcnow().isoformat()])
    diag.append(['visible_trade_count',len(rows)])
    diag.append(['excluded_test_trade_count',sum(1 for r in rows if _is_test_trade_value(r.get('is_test_trade')))])
    diag.append(['blank_pnl_count',sum(1 for r in rows if _as_float(r.get('net_profit')) is None)])
    for name in (diagnostics.get('local_workbook_names') or []):
        diag.append(['local_workbook',name])
    for w in (snapshot.get('warnings') or diagnostics.get('warnings') or []):
        diag.append(['warning',str(w)])
    for e in (diagnostics.get('errors') or []):
        diag.append(['error',str(e)])
    _wrap_columns(diag,['B'])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return {'ok':True,'path':str(output_path)}


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

def _set_column_widths(ws, widths):
    for k,v in widths.items(): ws.column_dimensions[k].width=v

def _style_table_sheet(ws, header_row=1, freeze='A2', autofilter=True):
    _style_header_row(ws, header_row)
    ws.freeze_panes=freeze
    if autofilter:
        ws.auto_filter.ref=f"A{header_row}:{chr(64+ws.max_column)}{max(header_row+1,ws.max_row)}"
    _apply_data_borders(ws)
