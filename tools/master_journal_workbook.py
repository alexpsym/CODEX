from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar","Equity Curve","Diagnostics"]
EDITABLE_COLS=["Test","Setup","Timeframe","Breakeven","Notes"]


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
            if isinstance(c.value,(int,float)): c.font=Font(color='008000' if c.value>0 else 'FF0000' if c.value<0 else None)
            rr+=1

    ws=wb['All Trades']; headers=['__row_id','Open Time','Close Time','Account','Symbol','Side','Qty','Entry','Exit','Net P/L']+EDITABLE_COLS; ws.append(headers)
    for row in rows:
        ws.append([stable_row_id(row),row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('net_profit'),'Yes' if row.get('is_test_trade') else 'No',row.get('setup') or '',row.get('timeframe') or '',row.get('breakeven') or '',row.get('notes') or ''])
    ws.freeze_panes='A2'; ws.auto_filter.ref=f"A1:O{max(2,ws.max_row)}"; ws.column_dimensions['A'].hidden=True
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"K2:K{max(2,ws.max_row)}")
    for i in range(2,ws.max_row+1):
        c=ws.cell(i,10)
        if isinstance(c.value,(int,float)): c.font=Font(color='008000' if c.value>0 else 'FF0000' if c.value<0 else None)

    # Instrument Averages
    inst=wb['Instrument Averages']; inst.append(['Symbol','Trades','Wins','Losses','Net P/L','Avg P/L'])
    bucket=defaultdict(list)
    for r in rows:
        bucket[str(r.get('symbol') or 'UNKNOWN')].append(r)
    for sym, grp in sorted(bucket.items()):
        pnls=[_as_float(x.get('net_profit')) for x in grp]
        vals=[p for p in pnls if p is not None]
        wins=sum(1 for p in vals if p>0); losses=sum(1 for p in vals if p<0); net=sum(vals) if vals else 0.0
        inst.append([sym,len(grp),wins,losses,net,(net/len(vals)) if vals else None])
    if inst.max_row==1: inst.append(['No data available','','','','',''])

    # P&L Calendar (daily + monthly)
    cal=wb['P&L Calendar']; cal.append(['Type','Date','Net P/L'])
    daily=defaultdict(float)
    for r in rows:
        dt=str(r.get('close_time') or r.get('open_time') or '')[:10]
        pnl=_as_float(r.get('net_profit'))
        if dt and pnl is not None: daily[dt]+=pnl
    for d,p in sorted(daily.items()): cal.append(['Daily',d,p])
    monthly=defaultdict(float)
    for d,p in daily.items(): monthly[d[:7]]+=p
    for m,p in sorted(monthly.items()): cal.append(['Monthly',m,p])
    if cal.max_row==1: cal.append(['No data available','',''])

    # Equity Curve
    eq=wb['Equity Curve']; eq.append(['Date','Delta P/L','Equity'])
    running=0.0
    for d,p in sorted(daily.items()):
        running += p
        eq.append([d,p,running])
    if eq.max_row==1: eq.append(['No data available','',''])

    diag=wb['Diagnostics']
    diagnostics=snapshot.get('diagnostics') if isinstance(snapshot.get('diagnostics'),dict) else {}
    diag.append(['Key','Value'])
    diag.append(['sync_timestamp',snapshot.get('updated_at') or datetime.utcnow().isoformat()])
    diag.append(['visible_trade_count',len(rows)])
    diag.append(['excluded_test_trade_count',sum(1 for r in rows if bool(r.get('is_test_trade')))])
    diag.append(['blank_pnl_count',sum(1 for r in rows if _as_float(r.get('net_profit')) is None)])
    for name in (diagnostics.get('local_workbook_names') or []):
        diag.append(['local_workbook',name])
    for w in (snapshot.get('warnings') or diagnostics.get('warnings') or []):
        diag.append(['warning',str(w)])
    for e in (diagnostics.get('errors') or []):
        diag.append(['error',str(e)])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return {'ok':True,'path':str(output_path)}
