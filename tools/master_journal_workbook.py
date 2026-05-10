from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar","Equity Curve","Diagnostics"]
EDITABLE_COLS=["Test","Setup","Timeframe","Breakeven","Notes"]


def stable_row_id(row: Dict[str, Any]) -> str:
    rid=str(row.get('id') or row.get('__row_id') or '').strip()
    if rid:
        return rid
    parts=[str(row.get('account_label') or row.get('account') or ''),str(row.get('symbol') or ''),str(row.get('side') or ''),str(row.get('open_time') or ''),str(row.get('close_time') or ''),str(row.get('qty') or row.get('qty_raw') or ''),str(row.get('entry_price') or ''),str(row.get('exit_price') or ''),str(row.get('net_profit') or ''),str((row.get('raw_refs') or {}).get('source_row') if isinstance(row.get('raw_refs'),dict) else '')]
    return 'sig:'+hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:20]


def read_master_journal_manual_overrides(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    wb=load_workbook(path, data_only=True)
    if 'All Trades' not in wb.sheetnames:
        wb.close(); return out
    ws=wb['All Trades']
    headers=[str(c.value or '').strip() for c in ws[1]]
    idx={h:i for i,h in enumerate(headers)}
    rid_i=idx.get('__row_id')
    if rid_i is None:
        wb.close(); return out
    for r in ws.iter_rows(min_row=2, values_only=True):
        rid=str(r[rid_i] or '').strip()
        if not rid: continue
        edits={}
        for col,field in [('Test','is_test_trade'),('Setup','setup'),('Timeframe','timeframe'),('Breakeven','breakeven'),('Notes','notes')]:
            i=idx.get(col)
            if i is None: continue
            val=r[i]
            if col=='Test':
                t=str(val or '').strip().lower()
                edits[field]= True if t in {'yes','true','1'} else False
            elif val not in (None,''):
                edits[field]=val
        if edits: out[rid]=edits
    wb.close()
    return out


def build_master_journal_workbook(snapshot: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    wb=Workbook(); wb.remove(wb.active)
    for s in SHEET_ORDER: wb.create_sheet(s)
    rows=[r for r in (snapshot.get('items') or []) if isinstance(r,dict) and str(r.get('row_type') or 'trade')=='trade']
    dash=wb['Dashboard']; dash['A1']='Account Balances'; dash['A1'].font=Font(bold=True)
    r=2
    for b in (snapshot.get('balances') or []):
        dash.cell(r,1,str((b or {}).get('label') or (b or {}).get('account') or 'Account')); dash.cell(r,2,(b or {}).get('balance')); r+=1
    totals=((snapshot.get('stats') or {}).get('totals') or {})
    r=max(r+1,4); dash.cell(r,1,'Main Stats').font=Font(bold=True); r+=1
    mapping=[('trades','Total Trades'),('wins','Wins'),('losses','Losses'),('win_rate_pct','Win Rate %'),('net_profit_total','Net P/L'),('gross_gain','Gross Profit'),('gross_loss','Gross Loss')]
    for k,l in mapping:
        if k in totals:
            dash.cell(r,1,l); c=dash.cell(r,2,totals.get(k));
            if isinstance(c.value,(int,float)): c.font=Font(color='008000' if c.value>0 else 'FF0000' if c.value<0 else None)
            r+=1
    ws=wb['All Trades']; headers=['__row_id','Open Time','Close Time','Account','Symbol','Side','Qty','Entry','Exit','Net P/L']+EDITABLE_COLS; ws.append(headers)
    for row in rows:
        ws.append([stable_row_id(row),row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('net_profit'),'Yes' if row.get('is_test_trade') else 'No',row.get('setup'),row.get('timeframe'),row.get('breakeven'),row.get('notes')])
    ws.freeze_panes='A2'; ws.auto_filter.ref=f"A1:O{max(2,ws.max_row)}"; ws.column_dimensions['A'].hidden=True
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"K2:K{max(2,ws.max_row)}")
    for i in range(2,ws.max_row+1):
        c=ws.cell(i,10)
        if isinstance(c.value,(int,float)): c.font=Font(color='008000' if c.value>0 else 'FF0000' if c.value<0 else None)
    for s in ['Instrument Averages','P&L Calendar','Equity Curve']:
        sh=wb[s]; sh['A1']=s; sh['A2']='No data available' if s!='Instrument Averages' else 'No instrument summary available'
    d=wb['Diagnostics']; d['A1']='Workbook Generation Status'; d['B1']='ok'; d['A2']='Sync Timestamp'; d['B2']=snapshot.get('updated_at')
    output_path.parent.mkdir(parents=True, exist_ok=True); wb.save(output_path)
    return {'ok':True,'path':str(output_path)}
