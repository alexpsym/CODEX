from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar","Equity Curve","Diagnostics"]
EDITABLE_COLS=["Test","Setup","Timeframe","Breakeven","Notes"]


def _to_rows(snapshot: Dict[str, Any]):
    rows=snapshot.get("items") if isinstance(snapshot,dict) else []
    return [r for r in rows if isinstance(r,dict) and str(r.get("row_type") or "trade")=="trade"]


def build_master_journal_workbook(snapshot: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    wb=Workbook()
    wb.remove(wb.active)
    for s in SHEET_ORDER:
        wb.create_sheet(s)
    dash=wb['Dashboard']
    bal=snapshot.get('balances') if isinstance(snapshot,dict) else []
    dash['A1']='Account Balances'; dash['A1'].font=Font(bold=True)
    r=2
    for b in (bal if isinstance(bal,list) else []):
        dash.cell(r,1,str((b or {}).get('label') or (b or {}).get('account') or 'Account'))
        dash.cell(r,2,(b or {}).get('balance'))
        r+=1
    stats=(snapshot.get('stats') or {}).get('totals') if isinstance(snapshot.get('stats'),dict) else {}
    r=max(r+1,4)
    dash.cell(r,1,'Main Stats').font=Font(bold=True); r+=1
    for key in ['trades','wins','losses','win_rate','net_pl','gross_profit','gross_loss']:
        if key in stats:
            dash.cell(r,1,key.replace('_',' ').title())
            c=dash.cell(r,2,stats.get(key))
            v=stats.get(key)
            if isinstance(v,(int,float)):
                if v>0: c.font=Font(color='008000')
                elif v<0: c.font=Font(color='FF0000')
            r+=1
    ws=wb['All Trades']
    headers=['__row_id','Open Time','Close Time','Account','Symbol','Side','Qty','Entry','Exit','Net P/L']+EDITABLE_COLS
    ws.append(headers)
    green=Font(color='008000'); red=Font(color='FF0000')
    for row in _to_rows(snapshot):
        rid=str(row.get('id') or row.get('__row_id') or '')
        vals=[rid,row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('net_profit'),
              'Yes' if str(row.get('is_test_trade') or '').lower() in {'1','true','yes'} else 'No',row.get('setup'),row.get('timeframe'),row.get('breakeven'),row.get('notes')]
        ws.append(vals)
    ws.freeze_panes='A2'; ws.auto_filter.ref=f"A1:O{max(2,ws.max_row)}"
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True)
    ws.add_data_validation(dv); dv.add(f"K2:K{max(2,ws.max_row)}")
    for rr in range(2,ws.max_row+1):
        c=ws.cell(rr,10); v=c.value
        if isinstance(v,(int,float)):
            c.font=green if v>0 else red if v<0 else Font()
    ws.column_dimensions['A'].hidden=True
    for s in ['Instrument Averages','P&L Calendar','Equity Curve']:
        sh=wb[s]; sh['A1']=s; sh['A1'].font=Font(bold=True)
    d=wb['Diagnostics']
    d['A1']='Workbook Generation Status'; d['B1']='ok'
    d['A2']='Sync Timestamp'; d['B2']=snapshot.get('generated_at') or snapshot.get('updated_at')
    err=(snapshot.get('diagnostics') or {}).get('errors') if isinstance(snapshot.get('diagnostics'),dict) else []
    if err:
        d['A4']='Errors';
        for i,e in enumerate(err,5): d.cell(i,1,str(e))
    output_path.parent.mkdir(parents=True,exist_ok=True)
    wb.save(output_path)
    return {'ok':True,'path':str(output_path)}
