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
from openpyxl.utils import get_column_letter
import calendar

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar","Equity Curve","_Trade Meta"]
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
    v = _as_float(seconds)
    if v is None:
        return "—"
    s = max(0, int(v))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day" + ("" if days == 1 else "s"))
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if minutes:
        parts.append(f"{minutes} minute" + ("" if minutes == 1 else "s"))
    if secs or not parts:
        parts.append(f"{secs} second" + ("" if secs == 1 else "s"))
    return ", ".join(parts)





def _fmt_duration_full(seconds: Any) -> str:
    return _fmt_duration(seconds)

def _resolve_balance_after(row: Dict[str, Any]) -> float | None:
    for key in ("analysis_balance_after_trade", "balance_after_trade", "cashflow_new_balance"):
        val = _as_float(row.get(key))
        if val is not None:
            return val
    return None

def _resolved_all_trade_balances(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    indexed = list(enumerate(rows))
    running: Dict[str, float] = {}
    out: Dict[str, float] = {}
    def _sort_key(item):
        i, row = item
        acct = str(row.get("account_label") or row.get("account") or "")
        ts = str(row.get("close_time") or row.get("open_time") or "")
        return (acct, ts, i)
    for i, row in sorted(indexed, key=_sort_key):
        acct = str(row.get("account_label") or row.get("account") or "")
        resolved = _resolve_balance_after(row)
        if resolved is not None:
            running[acct] = resolved
            out[str(i)] = resolved
            continue
        if acct in running:
            pnl = _as_float(row.get("net_profit"))
            if pnl is not None:
                running[acct] = running[acct] + pnl
                out[str(i)] = running[acct]
    return out

def _fmt_pct(v: Any) -> str:
    x=_as_float(v)
    return "—" if x is None else f"{x:.2f}%"

def _fmt_r(v: Any) -> str:
    x=_as_float(v)
    return "—" if x is None else f"{x:.3f}R"

def _fmt_money(v: Any, money_map: Any, key: str) -> str:
    mm=(money_map or {}).get(key) if isinstance(money_map,dict) else None
    if isinstance(mm,dict) and mm:
        return ' / '.join(f"{(k or 'UNKNOWN').upper()} {float(val):.2f}" for k,val in sorted(mm.items()))
    x=_as_float(v)
    return "—" if x is None else f"UNKNOWN {x:.2f}"

def _fmt_leader(v: Any) -> str:
    if not isinstance(v,dict):
        return '—' if not v else str(v)
    sym=v.get('symbol') or '—'
    w=v.get('wins'); l=v.get('losses'); t=v.get('total_trades')
    return f"{sym} — wins {w if w is not None else '—'}, losses {l if l is not None else '—'}, trades {t if t is not None else '—'}"

def _fmt_detail_src(src: Any) -> str:
    if not isinstance(src,dict):
        return '—'
    sym=str(src.get('symbol') or src.get('instrument') or '').strip() or '—'
    d=_as_date(src.get('close_time') or src.get('date') or src.get('open_time'))
    return f"{sym} · {d.isoformat() if d else '—'}"

def _excel_scalar(value: Any) -> Any:
    if value is None:
        return ''
    if isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, dict):
        if 'symbol' in value and any(k in value for k in ('wins','losses','total_trades','trades')):
            symbol = value.get('symbol') or 'N/A'
            wins = value.get('wins', '')
            losses = value.get('losses', '')
            trades = value.get('total_trades', value.get('trades', ''))
            return f"{symbol} — wins {wins}, losses {losses}, trades {trades}"
        return ', '.join(f"{k}={value.get(k)}" for k in sorted(value.keys()))
    if isinstance(value, (list, tuple, set)):
        return ', '.join(str(_excel_scalar(v)) for v in value)
    return str(value)

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
        if 'All Trades' not in wb.sheetnames or '_Trade Meta' not in wb.sheetnames:
            return out
        ws=wb['All Trades']; meta=wb['_Trade Meta']
        headers=[str(c.value or '').strip() for c in ws[1]]
        idx={h:i for i,h in enumerate(headers)}
        rid_by_row={int(r[0]):str(r[1] or '').strip() for r in meta.iter_rows(min_row=2,values_only=True) if r and r[0] and r[1]}
        hidden_i=idx.get('__row_id')
        for row_num,r in enumerate(ws.iter_rows(min_row=2, values_only=True),start=2):
            rid=(str(r[hidden_i] or '').strip() if hidden_i is not None and hidden_i < len(r) else '') or rid_by_row.get(row_num,'')
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

    dash=wb['Dashboard']
    for c,w in [('A',30),('B',22),('C',30),('E',30),('F',22),('G',30),('I',30),('J',22),('K',30)]: dash.column_dimensions[c].width=w

    by_market=(groups.get('by_market') or {})
    risk=(groups.get('risk_expectancy') or {})
    duration=(groups.get('duration') or {})
    leaders=(groups.get('leaders') or {})

    def core_rows(mkt: Dict[str, Any], money_map: Dict[str, Any]):
        msrc=(mkt.get('metric_sources') or {}) if isinstance(mkt,dict) else {}
        return [
            ('Trades', mkt.get('trades'),'neutral','count',None,None,money_map),('Wins', mkt.get('wins'),'profit','count',None,None,money_map),('Losses', mkt.get('losses'),'loss','count',None,None,money_map),('Break-even', mkt.get('break_even'),'neutral','count',None,None,money_map),('Win rate', mkt.get('win_rate_pct'),'neutral','pct',None,None,money_map),
            ('Net P/L', mkt.get('net_profit_total'),'auto','money','net_profit_total',None,money_map),('Gross gain', mkt.get('gross_gain'),'profit','money','gross_gain',None,money_map),('Gross loss', mkt.get('gross_loss'),'loss','money','gross_loss',None,money_map),
            ('Avg result %', mkt.get('avg_result_pct'),'neutral','pct',None,None,money_map),('Max loss %', mkt.get('min_result_pct'),'loss','pct',None,_fmt_detail_src(msrc.get('min_result_pct')),money_map),('Max win %', mkt.get('max_result_pct'),'profit','pct',None,_fmt_detail_src(msrc.get('max_result_pct')),money_map),
            ('Avg R', mkt.get('avg_r_multiple'),'neutral','r',None,None,money_map),('Max R loss', mkt.get('min_r_multiple'),'loss','r',None,_fmt_detail_src(msrc.get('min_r_multiple')),money_map),('Max R win', mkt.get('max_r_multiple'),'profit','r',None,_fmt_detail_src(msrc.get('max_r_multiple')),money_map),
            ('Max gain', mkt.get('max_gain'),'profit','money','max_gain',_fmt_detail_src(msrc.get('max_gain')),money_map),('Max loss', mkt.get('max_loss'),'loss','money','max_loss',_fmt_detail_src(msrc.get('max_loss')),money_map),('Avg stop %', mkt.get('avg_stop_pct'),'neutral','pct',None,None,money_map),('Avg target %', mkt.get('avg_target_pct'),'neutral','pct',None,None,money_map),('Avg duration', mkt.get('avg_duration_seconds'),'neutral','duration',None,None,money_map),
        ]

    overall_bucket=by_market.get('overall') or totals
    section_rows=[
      ('Overall', core_rows(overall_bucket, overall_bucket.get('money_by_currency') or totals.get('money_by_currency') or {})),
      ('Winners', [('Avg stop %',risk.get('avg_stop_pct_winners'),'neutral','pct',None,None,{}),('Avg target %',risk.get('avg_target_pct_winners'),'neutral','pct',None,None,{}),('Avg result %',risk.get('avg_result_pct_winners'),'profit','pct',None,None,{}),('Avg R',risk.get('avg_r_multiple_winners'),'profit','r',None,None,{})]),
      ('Losers', [('Avg stop %',risk.get('avg_stop_pct_losers'),'neutral','pct',None,None,{}),('Avg target %',risk.get('avg_target_pct_losers'),'neutral','pct',None,None,{}),('Avg result %',risk.get('avg_result_pct_losers'),'loss','pct',None,None,{}),('Avg R',risk.get('avg_r_multiple_losers'),'loss','r',None,None,{})]),
      ('Drawdown', [('Max drawdown',risk.get('max_drawdown_pct'),'drawdown','pct',None,None,{}),('Avg drawdown',risk.get('avg_drawdown_pct'),'drawdown','pct',None,None,{})]),
      ('Duration', [('Overall avg',duration.get('overall_avg_seconds'),'neutral','duration',None,None,{}),('Overall shortest',duration.get('overall_shortest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('overall_shortest_seconds')),{}),('Overall longest',duration.get('overall_longest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('overall_longest_seconds')),{}),('FX shortest',duration.get('fx_shortest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('fx_shortest_seconds')),{}),('FX longest',duration.get('fx_longest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('fx_longest_seconds')),{}),('Crypto shortest',duration.get('crypto_shortest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('crypto_shortest_seconds')),{}),('Crypto longest',duration.get('crypto_longest_seconds'),'neutral','duration',None,_fmt_detail_src((duration.get('metric_sources') or {}).get('crypto_longest_seconds')),{})]),
      ('FX', core_rows(by_market.get('fx') or {}, ((by_market.get('fx') or {}).get('money_by_currency') or {}))),
      ('Crypto', core_rows(by_market.get('crypto') or {}, ((by_market.get('crypto') or {}).get('money_by_currency') or {}))),
      ('Instrument leaders', [('Overall most wins',leaders.get('most_wins_instrument'),'neutral','leader',None,None,{}),('Overall most losses',leaders.get('most_losses_instrument'),'neutral','leader',None,None,{}),('FX most wins',leaders.get('fx_most_wins_instrument'),'neutral','leader',None,None,{}),('FX most losses',leaders.get('fx_most_losses_instrument'),'neutral','leader',None,None,{}),('Crypto most wins',leaders.get('crypto_most_wins_instrument'),'neutral','leader',None,None,{}),('Crypto most losses',leaders.get('crypto_most_losses_instrument'),'neutral','leader',None,None,{})])
    ]
    lane_cols=[1,5,9]
    lane_heights=[2,2,2]
    for title,srows in section_rows:
        lane=lane_heights.index(min(lane_heights))
        uses_detail = title == "Duration" or any(((list(r)+[None]*7)[:7][5] not in (None, "", "—")) for r in srows)
        section_height = _write_stat_section(dash, lane_heights[lane], lane_cols[lane], title, srows, use_detail_col=uses_detail)
        lane_heights[lane] += section_height + 1

    resolved_balances = _resolved_all_trade_balances(rows)
    ws=wb['All Trades']; headers=['Open Time','Close Time','Account','Symbol','Side','Qty','Entry Price','Exit Price','Stop Loss Price','Target Price','Commission','Net P/L','Profit %','R-Multiple','Balance After','Trade Duration']+EDITABLE_COLS+['__row_id']; ws.append(headers)
    meta=wb['_Trade Meta']; meta.append(['all_trades_row','row_id'])
    for i, row in enumerate(rows):
        ws.append([row.get('open_time'),row.get('close_time'),row.get('account_label') or row.get('account'),row.get('symbol'),row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('stop_loss'),row.get('take_profit'),row.get('commission'),row.get('net_profit'),row.get('result_pct'),row.get('r_multiple'),resolved_balances.get(str(i)),_fmt_duration(row.get('trade_duration_seconds')),'Yes' if _is_test_trade_value(row.get('is_test_trade')) else 'No',row.get('setup') or '',row.get('timeframe') or '',row.get('breakeven') or '',row.get('notes') or '', stable_row_id(row)])
    _style_table_sheet(ws,1,'A2',True)
    for rr in range(2, ws.max_row + 1):
        ws.cell(rr, 15).number_format = '#,##0.00'
    for i,row in enumerate(rows,start=2): meta.append([i, stable_row_id(row)])
    ws.column_dimensions[get_column_letter(ws.max_column)].hidden=True
    meta.sheet_state='hidden'
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"Q2:Q{max(2,ws.max_row)}")

    inst=wb['Instrument Averages']; headers=["Symbol","Class","Trades","Longs","Shorts","Wins","Losses","Break-even","Long wins","Long losses","Short wins","Short losses","Long break-even","Short break-even","Net P/L","Avg P/L","Win Rate %","Avg stop % (W)","Avg stop % (L)","Avg target % (W)","Avg target % (L)","Avg duration","Shortest","Longest"]; inst.append(headers)
    for rec in (stats.get('by_instrument') or []):
        cls=str(rec.get("asset_class") or rec.get("class") or "").lower(); inst.append([rec.get("symbol"),cls.upper() if cls else None,rec.get("total_trades", rec.get("trades")),rec.get("long_trades", rec.get("longs")),rec.get("short_trades", rec.get("shorts")),rec.get("wins"),rec.get("losses"),rec.get("break_even"),rec.get("long_wins"),rec.get("long_losses"),rec.get("short_wins"),rec.get("short_losses"),rec.get("long_break_even"),rec.get("short_break_even"),rec.get("net_profit_total"),rec.get("avg_net_profit"),rec.get("win_rate_pct"),rec.get('avg_sl_pct_wins'),rec.get('avg_sl_pct_losses'),rec.get('avg_tp_pct_wins'),rec.get('avg_tp_pct_losses'),_fmt_duration(rec.get("avg_trade_duration_seconds", rec.get("avg_duration_seconds"))),_fmt_duration(rec.get("min_trade_duration_seconds", rec.get("shortest_duration_seconds"))),_fmt_duration(rec.get("max_trade_duration_seconds", rec.get("longest_duration_seconds")))])
    _style_table_sheet(inst,1,'A2',True)

    cal=wb['P&L Calendar']; cal.append(['Year'] + [calendar.month_name[m] for m in range(1,13)])
    monthly=defaultdict(lambda:{'pnl':0.0,'trades':0})
    for r in non_test:
        d=_as_date(r.get('close_time') or r.get('open_time')); pnl=_as_float(r.get('net_profit'))
        if d and pnl is not None: monthly[(d.year,d.month)]['pnl']+=pnl; monthly[(d.year,d.month)]['trades']+=1
    for y in sorted({y for y,_ in monthly.keys()}):
        cal.append([y]+[('' if (y,m) not in monthly else f"P/L {monthly[(y,m)]['pnl']:.2f}\nT: {monthly[(y,m)]['trades']}") for m in range(1,13)])
    _style_table_sheet(cal,1,'A2',False)
    for rr in range(2, cal.max_row+1):
        for cc in range(2,14):
            cell=cal.cell(rr,cc); txt=str(cell.value or '')
            pnl=None
            if txt.startswith('P/L '):
                try: pnl=float(txt.split('\n')[0].replace('P/L ','').strip())
                except Exception: pnl=None
            if pnl is None or pnl == 0:
                cell.fill=PatternFill('solid',fgColor='00DDEBF7')
            elif pnl > 0:
                cell.fill=PatternFill('solid',fgColor='00E2F0D9')
            else:
                cell.fill=PatternFill('solid',fgColor='00FCE4D6')
            cell.font = Font(color='00000000', bold=True)
            cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
        cal.row_dimensions[rr].height = 30
    cal.row_dimensions[1].height = 18
    for cc in range(2, 14):
        cal.column_dimensions[get_column_letter(cc)].width = 13

    eq=wb['Equity Curve']; by_date=defaultdict(dict); accounts=[]; carry={}; observed=defaultdict(int)
    for r in sorted(non_test,key=lambda x: str(x.get('close_time') or x.get('open_time') or '')):
        d=_as_date(r.get('close_time') or r.get('open_time')); acct=str(r.get('account_label') or r.get('account') or 'Account')
        if not d: continue
        if acct not in accounts: accounts.append(acct)
        bal=_as_float(r.get('analysis_balance_after_trade'))
        if bal is None: bal=_as_float(r.get('balance_after_trade'))
        if bal is None: bal=_as_float(r.get('cashflow_new_balance'))
        if bal is None and acct in carry:
            pnl = _as_float(r.get('net_profit'))
            if pnl is not None:
                bal = carry[acct] + pnl
        if bal is not None:
            carry[acct]=bal
            by_date[d.isoformat()][acct]=bal
            observed[acct]+=1
    eq.append(['Date']+accounts); points=0; carry={}
    for d in sorted(by_date.keys()):
        row=[d]
        for a in accounts:
            if a in by_date[d]: carry[a]=by_date[d][a]
            row.append(carry.get(a))
            if carry.get(a) is not None: points+=1
        eq.append(row)
    _style_table_sheet(eq,1,'A2',True)
    chart_col = max(2, eq.max_column + 2)
    made = 0
    for idx, acct in enumerate(accounts):
        if observed.get(acct, 0) < 2:
            continue
        col = 2 + idx
        chart=LineChart(); chart.title=f'Equity Curve - {acct}'; chart.y_axis.title='Equity'; chart.x_axis.title='Date'
        chart.add_data(Reference(eq,min_col=col,min_row=1,max_row=eq.max_row),titles_from_data=True)
        chart.set_categories(Reference(eq,min_col=1,min_row=2,max_row=eq.max_row))
        chart.width = 8.5; chart.height = 5.2
        row_offset = (made // 2) * 16 + 2
        col_offset = chart_col + (made % 2) * 8
        eq.add_chart(chart, f"{get_column_letter(col_offset)}{row_offset}")
        made += 1
    if made == 0:
        eq['A3']='Not enough equity data to chart.'

    output_path.parent.mkdir(parents=True, exist_ok=True); wb.save(output_path)
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


def _write_stat_section(ws, start_row, start_col, title, rows, use_detail_col=True):
    right_col = start_col + (2 if use_detail_col else 1)
    ws.merge_cells(start_row=start_row,start_column=start_col,end_row=start_row,end_column=right_col)
    h=ws.cell(start_row,start_col,title); h.font=Font(bold=True,color='00000000'); h.fill=PatternFill('solid',fgColor='00EAF2F8')
    r=start_row+1
    for row in rows:
        label,val,sem,kind,money_key,detail_text,money_map = (list(row)+[None]*7)[:7]
        ws.cell(r,start_col,_excel_scalar(label)).font=Font(color='00000000')
        if kind=='pct': disp=_fmt_pct(val)
        elif kind=='r': disp=_fmt_r(val)
        elif kind=='money': disp=_fmt_money(val, money_map or {}, money_key or '')
        elif kind=='duration': disp=_fmt_duration_full(val)
        elif kind=='leader': disp=_fmt_leader(val)
        elif kind=='count': disp='—' if val is None else str(val)
        else: disp='—' if val is None else str(val)
        vcell=ws.cell(r,start_col+1,_excel_scalar(disp))
        if use_detail_col:
            dcell=ws.cell(r,start_col+2,_excel_scalar(detail_text or '—')); dcell.alignment=Alignment(wrap_text=True,vertical='center')
        if sem in {'profit','loss','drawdown'}:
            f=copy(vcell.font); f.color=Color(rgb='00008000' if sem=='profit' else '00FF0000'); vcell.font=f
        r+=1
    _format_stat_card(ws,start_row,start_col,r-1,right_col)
    return r - start_row


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
        ws.auto_filter.ref=f"A{header_row}:{get_column_letter(ws.max_column)}{max(header_row+1,ws.max_row)}"
    _apply_data_borders(ws)
    for cell in ws[header_row]:
        cell.font = Font(name='Calibri', size=11, bold=True, color='00000000')
    for r in range(header_row + 1, ws.max_row + 1):
        ws.row_dimensions[r].height = 15
