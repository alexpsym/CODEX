from __future__ import annotations
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib
from openpyxl.styles import PatternFill, Border, Side, Alignment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
import calendar

SHEET_ORDER=["Dashboard","All Trades","Instrument Averages","P&L Calendar"]
EDITABLE_COLS=["Test","Setup","Timeframe","Breakeven","Notes"]
PROFIT_FILL = "C6EFCE"
PROFIT_FONT = "006100"
LOSS_FILL = "FFC7CE"
LOSS_FONT = "9C0006"


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

ZERO_HIDE_FORMAT = "0;-0;;@"

def _currency_code(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().upper()
        if text:
            return text
    return "UNKNOWN"

def _is_crypto_currency(code: str) -> bool:
    c = str(code or "").upper()
    return c in {"USDT", "BTC", "ETH", "SOL", "XRP", "USDC"}

def _currency_number_format(code: str, *, force_decimals: int | None = None) -> str:
    c = _currency_code(code)
    if force_decimals is not None:
        decimals = "0" * max(0, force_decimals)
        return f'#,##0.{decimals} "{c}"'
    if _is_crypto_currency(c):
        return f'#,##0.########## "{c}"'
    return f'#,##0.00 "{c}"'

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




def _all_trades_row_fingerprint_from_map(values: Dict[str, Any]) -> str:
    parts = [str(values.get(k) or '') for k in ['Account','Symbol','Side','Open Time','Close Time','Qty','Entry Price','Exit Price','Net P/L']]
    return 'sig:' + hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:24]
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
        rid_by_row={}
        if '_Trade Meta' in wb.sheetnames:
            meta=wb['_Trade Meta']
            rid_by_row={int(r[0]):str(r[1] or '').strip() for r in meta.iter_rows(min_row=2,values_only=True) if r and r[0] and r[1]}
        for row_num,r in enumerate(ws.iter_rows(min_row=2, values_only=True),start=2):
            comment_rid = ""
            cmt = ws.cell(row_num, 1).comment
            if cmt and isinstance(cmt.text, str) and cmt.text.startswith("row_id:"):
                comment_rid = cmt.text.split("row_id:", 1)[1].strip()
            meta_rid = rid_by_row.get(row_num,'')
            rowid_i = idx.get('Row ID')
            inline_rid = str(r[rowid_i] or '').strip() if rowid_i is not None and rowid_i < len(r) else ''
            rid = inline_rid or comment_rid or meta_rid
            if not rid:
                row_map = {h: (r[i] if i < len(r) else None) for h, i in idx.items()}
                rid = _all_trades_row_fingerprint_from_map(row_map)
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
    rows=[r for r in (snapshot.get('items') or []) if isinstance(r,dict) and str(r.get('row_type') or 'trade') in {'trade','monthly_aud_reval','cashflow'}]
    metric_rows=[r for r in rows if str(r.get('row_type') or 'trade')=='trade']
    non_test=[r for r in metric_rows if not _is_test_trade_value(r.get('is_test_trade'))]
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
            ('Avg result %', mkt.get('avg_result_pct'),'auto','pct',None,None,money_map),('Max loss %', mkt.get('min_result_pct'),'loss','pct',None,_fmt_detail_src(msrc.get('min_result_pct')),money_map),('Max win %', mkt.get('max_result_pct'),'profit','pct',None,_fmt_detail_src(msrc.get('max_result_pct')),money_map),
            ('Avg R', mkt.get('avg_r_multiple'),'auto','r',None,None,money_map),('Max R loss', mkt.get('min_r_multiple'),'loss','r',None,_fmt_detail_src(msrc.get('min_r_multiple')),money_map),('Max R win', mkt.get('max_r_multiple'),'profit','r',None,_fmt_detail_src(msrc.get('max_r_multiple')),money_map),
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
    ]
    fixed_layout = {'Overall': (1,1), 'FX': (1,3), 'Crypto': (1,5), 'Winners': (1,7), 'Losers': (6,7), 'Drawdown': (11,7), 'Duration': (1,9)}
    end_rows = {}
    for title, srows in section_rows:
        sr, sc = fixed_layout[title]
        uses_detail = title == "Duration" or any(((list(r)+[None]*7)[:7][5] not in (None, "", "—")) for r in srows)
        end_rows[title] = sr + _write_stat_section(dash, sr, sc, title, srows, use_detail_col=uses_detail, apply_semantic_cf=True)
    leaders_start = 1
    leaders_end = _write_instrument_leaders_section(dash, leaders_start, 11, leaders)
    balances = snapshot.get('balances') or stats.get('balances') or []
    br = max([leaders_end] + list(end_rows.values()) or [1]) + 2
    dash.cell(br, 1, "Account Balances").font = Font(bold=True)
    dash.merge_cells(start_row=br, start_column=1, end_row=br, end_column=4)
    dash.cell(br+1,1,"Account").font=Font(bold=True)
    dash.cell(br+1,2,"Balance").font=Font(bold=True)
    dash.cell(br+1,3,"Currency").font=Font(bold=True)
    dash.cell(br+1,4,"As Of").font=Font(bold=True)
    cur = br + 2
    for rec in balances:
        if not isinstance(rec, dict):
            continue
        ccy = _currency_code(rec.get("currency"), rec.get("account_currency"))
        dash.cell(cur,1,rec.get("account_label") or rec.get("account") or rec.get("source") or "—")
        bcell = dash.cell(cur,2,_as_float(rec.get("balance")))
        bcell.number_format = '#,##0.0000000000' if _is_crypto_currency(ccy) else '#,##0.00'
        dash.cell(cur,3,ccy)
        dash.cell(cur,4,rec.get("as_of") or "")
        cur += 1

    resolved_balances = _resolved_all_trade_balances(rows)
    ws=wb['All Trades']; headers=['Open Time','Close Time','Account','Symbol','Side','Qty','Entry Price','Exit Price','Stop Loss Price','Target Price','Commission','Net P/L','Profit %','R-Multiple','Balance After','Trade Duration']+EDITABLE_COLS+['Cashflow Amount','Cashflow New Balance','Currency','Row Type','Row ID']; ws.append(headers)
    for i, row in enumerate(rows):
        pct = _as_float(row.get('result_pct'))
        is_monthly = str(row.get("row_type") or "") == "monthly_aud_reval"
        symbol = row.get('symbol') or ("MONTHLY AUD P/L" if is_monthly else "")
        acct = row.get('account_label') or row.get('account') or ("Bybit Live" if is_monthly else "")
        notes = row.get('notes') or ('Monthly Bybit Live AUD P/L bookkeeping note (excluded from metrics).' if is_monthly else '')
        net_pnl = row.get('net_profit') if row.get('net_profit') is not None else row.get('result_cash')
        ws.append([row.get('open_time') or row.get("period_month"),row.get('close_time') or row.get("period_month"),acct,symbol,row.get('side'),row.get('qty'),row.get('entry_price'),row.get('exit_price'),row.get('stop_loss'),row.get('take_profit'),row.get('commission'),net_pnl,(pct/100.0 if pct is not None else ''),row.get('r_multiple'),resolved_balances.get(str(i)),_fmt_duration(row.get('trade_duration_seconds')),'Yes' if _is_test_trade_value(row.get('is_test_trade')) else 'No',row.get('setup') or '',row.get('timeframe') or '',row.get('breakeven') or '',notes,row.get('cashflow_amount'),row.get('cashflow_new_balance'),row.get('currency') or row.get('account_currency') or row.get('result_currency') or '',row.get('row_type') or 'trade', stable_row_id(row)])
    _style_table_sheet(ws,1,'A2',True)
    for rr in range(2, ws.max_row + 1):
        ccy_comm = _currency_code(rows[rr-2].get("commission_currency"), rows[rr-2].get("fee_currency"), rows[rr-2].get("realized_pnl_currency"), rows[rr-2].get("currency"), rows[rr-2].get("account_currency"))
        ccy_pnl = _currency_code(rows[rr-2].get("realized_pnl_currency"), rows[rr-2].get("result_currency"), rows[rr-2].get("currency"), rows[rr-2].get("account_currency"), rows[rr-2].get("balance_after_trade_currency"))
        ccy_bal = _currency_code(rows[rr-2].get("balance_after_trade_currency"), rows[rr-2].get("result_currency"), rows[rr-2].get("currency"), rows[rr-2].get("account_currency"))
        ws.cell(rr, 6).number_format = '#,##0.##########'
        ws.cell(rr, 11).number_format = _currency_number_format(ccy_comm)
        ws.cell(rr, 12).number_format = _currency_number_format(ccy_pnl)
        ws.cell(rr, 14).number_format = '0.00'
        ws.cell(rr, 15).number_format = '#,##0.0000000000' if _is_crypto_currency(ccy_bal) else '#,##0.00'
        ws.cell(rr, 13).number_format = "0.00%"
    ws.column_dimensions['Z'].hidden = True
    dv=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"Q2:Q{max(2,ws.max_row)}")
    _negative_impact_rule(ws, f"K2:K{max(2, ws.max_row)}")
    _profit_loss_rules(ws, f"L2:N{max(2, ws.max_row)}")

    inst=wb['Instrument Averages']; headers=["Symbol","Class","Trades","Longs","Shorts","Wins","Losses","Break-even","Long wins","Long losses","Short wins","Short losses","Long break-even","Short break-even","Net P/L","Avg P/L","Win Rate %","Avg stop % (W)","Avg stop % (L)","Avg target % (W)","Avg target % (L)","Avg duration","Shortest","Longest"]; inst.append(headers)
    for rec in (stats.get('by_instrument') or []):
        cls=str(rec.get("asset_class") or rec.get("class") or "").lower()
        row_idx = inst.max_row + 1
        inst.append([rec.get("symbol"),cls.upper() if cls else None,rec.get("total_trades", rec.get("trades")),rec.get("long_trades", rec.get("longs")),rec.get("short_trades", rec.get("shorts")),rec.get("wins"),rec.get("losses"),rec.get("break_even"),rec.get("long_wins"),rec.get("long_losses"),rec.get("short_wins"),rec.get("short_losses"),rec.get("long_break_even"),rec.get("short_break_even"),rec.get("net_profit_total"),rec.get("avg_net_profit"),rec.get("win_rate_pct"),rec.get('avg_sl_pct_wins'),rec.get('avg_sl_pct_losses'),rec.get('avg_tp_pct_wins'),rec.get('avg_tp_pct_losses'),_fmt_duration(rec.get("avg_trade_duration_seconds", rec.get("avg_duration_seconds"))),_fmt_duration(rec.get("min_trade_duration_seconds", rec.get("shortest_duration_seconds"))),_fmt_duration(rec.get("max_trade_duration_seconds", rec.get("longest_duration_seconds")))])
        for cc in range(17, 22):
            cell = inst.cell(row_idx, cc)
            val = _as_float(cell.value)
            if val is not None:
                cell.value = val / 100.0
                cell.number_format = "0.00%"
        for zc in [4,5,6,7,8,9,10,11,12,13,14]:
            inst.cell(row_idx, zc).number_format = ZERO_HIDE_FORMAT
        money = rec.get("money_by_currency") or {}
        for col, key in ((15, "net_profit_total"), (16, "avg_net_profit")):
            mm = (money.get(key) or {}) if isinstance(money, dict) else {}
            if len(mm) == 1:
                ccy = list(mm.keys())[0]
                inst.cell(row_idx, col).value = list(mm.values())[0]
                inst.cell(row_idx, col).number_format = f'"{ccy}" #,##0.00;[Red]-"{ccy}" #,##0.00'
            elif len(mm) > 1:
                inst.cell(row_idx, col).value = " / ".join(f"{k} {v:.2f}" for k, v in sorted(mm.items()))
            else:
                val = _as_float(inst.cell(row_idx, col).value)
                if val is not None:
                    inst.cell(row_idx, col).value = val
                    inst.cell(row_idx, col).number_format = '"UNKNOWN" #,##0.00;[Red]-"UNKNOWN" #,##0.00'
    _style_table_sheet(inst,1,'A2',True)
    _profit_loss_rules(inst, f"O2:P{max(2, inst.max_row)}")

    cal=wb['P&L Calendar']; cal.append(['Year'] + [f"{calendar.month_name[m]} P/L %" for m in range(1,13)]); cal.append(['Trades'] + [calendar.month_name[m] for m in range(1,13)])
    monthly=defaultdict(lambda:{'pct':0.0,'trades':0})
    for r in non_test:
        d=_as_date(r.get('close_time') or r.get('open_time')); pct=_as_float(r.get('result_pct'))
        if d and pct is not None: monthly[(d.year,d.month)]['pct']+=pct; monthly[(d.year,d.month)]['trades']+=1
    for y in sorted({y for y,_ in monthly.keys()}):
        cal.append([y]+[(monthly[(y,m)]['pct'] / 100.0 if (y,m) in monthly else '') for m in range(1,13)])
        cal.append([f"{y} Trades"]+[(monthly[(y,m)]['trades'] if (y,m) in monthly else '') for m in range(1,13)])
    _style_table_sheet(cal,1,'A3',False)
    _style_header_row(cal, 2)
    _table_border(cal, 1, 1, cal.max_row, cal.max_column)
    for rr in range(3, cal.max_row + 1, 2):
        for cc in range(2, 14):
            cal.cell(rr, cc).number_format = "0.00%"
    for rr in range(4, cal.max_row + 1, 2):
        for cc in range(2, 14):
            cal.cell(rr, cc).number_format = "0"
    for rr in range(3, cal.max_row + 1, 2):
        _profit_loss_rules(cal, f"B{rr}:M{rr}")

    output_path.parent.mkdir(parents=True, exist_ok=True); wb.save(output_path)
    return {'ok':True,'path':str(output_path)}

def _table_border(ws, top_row, left_col, bottom_row, right_col):
    thin=Side(style='thin', color='D1D5DB')
    thick=Side(style='thick', color='D1D5DB')
    for r in range(top_row,bottom_row+1):
        for c in range(left_col,right_col+1):
            ws.cell(r,c).border=Border(
                left=thick if c==left_col else thin,
                right=thick if c==right_col else thin,
                top=thick if r==top_row else thin,
                bottom=thick if r==bottom_row else thin,
            )


def _write_stat_section(ws, start_row, start_col, title, rows, use_detail_col=False, apply_semantic_cf=False):
    right_col = start_col + 1
    ws.merge_cells(start_row=start_row,start_column=start_col,end_row=start_row,end_column=right_col)
    h=ws.cell(start_row,start_col,title); h.font=Font(bold=True,color='00000000'); h.fill=PatternFill('solid',fgColor='00EAF2F8')
    r=start_row+1
    for row in rows:
        label,val,sem,kind,money_key,detail_text,money_map = (list(row)+[None]*7)[:7]
        ws.cell(r,start_col,_excel_scalar(label)).font=Font(color='00000000', bold=True)
        vcell=ws.cell(r,start_col+1)
        if kind=='pct':
            x = _as_float(val); vcell.value = '' if x is None else x/100.0; vcell.number_format="0.00%"
        elif kind=='r':
            x = _as_float(val); vcell.value = '' if x is None else x; vcell.number_format='0.000"R"'
        elif kind=='count':
            vcell.value = _as_float(val) if val is not None else '—'; vcell.number_format='0'
        elif kind=='money':
            mm=(money_map or {}).get(money_key or '') if isinstance(money_map,dict) else {}
            if isinstance(mm, dict) and len(mm)==1:
                ccy = list(mm.keys())[0]; vcell.value=list(mm.values())[0]; vcell.number_format=f'"{ccy}" #,##0.00;[Red]-"{ccy}" #,##0.00'
            elif isinstance(mm, dict) and len(mm)>1:
                vcell.value=' / '.join(f"{k} {float(v):.2f}" for k,v in sorted(mm.items()))
            else:
                vcell.value = _as_float(val) if _as_float(val) is not None else '—'
        elif kind=='duration':
            vcell.value = _fmt_duration_full(val)
        else:
            vcell.value = '—' if val is None else val
        if apply_semantic_cf and isinstance(vcell.value, (int, float)):
            if kind == "count":
                pass
            elif sem == "auto":
                _profit_loss_rules(ws, f"{vcell.coordinate}:{vcell.coordinate}")
            elif sem in {"loss", "drawdown"}:
                _negative_impact_rule(ws, f"{vcell.coordinate}:{vcell.coordinate}")
            elif sem == "profit" and kind in {"pct", "r", "money"}:
                _profit_loss_rules(ws, f"{vcell.coordinate}:{vcell.coordinate}")
        if detail_text not in (None, "", "—"):
            r += 1
            ws.cell(r, start_col, "Source").font = Font(bold=True)
            ws.cell(r, start_col+1, detail_text)
        r+=1
    _table_border(ws,start_row,start_col,r-1,right_col)
    return r - start_row


def _style_header_row(ws, row=1):
    fill=PatternFill('solid', fgColor='E5E7EB')
    thin=Side(style='thin', color='D1D5DB')
    for c in ws[row]:
        c.font=Font(bold=True)
        c.fill=fill
        c.border=Border(left=thin,right=thin,top=thin,bottom=thin)

def _profit_loss_rules(ws, cell_range: str):
    ws.conditional_formatting.add(cell_range, CellIsRule(operator='greaterThan', formula=['0'], fill=PatternFill('solid', fgColor=PROFIT_FILL), font=Font(color=PROFIT_FONT)))
    ws.conditional_formatting.add(cell_range, CellIsRule(operator='lessThan', formula=['0'], fill=PatternFill('solid', fgColor=LOSS_FILL), font=Font(color=LOSS_FONT)))


def _negative_impact_rule(ws, cell_range: str):
    ws.conditional_formatting.add(cell_range, CellIsRule(operator='notEqual', formula=['0'], fill=PatternFill('solid', fgColor=LOSS_FILL), font=Font(color=LOSS_FONT)))


def _style_table_sheet(ws, header_row=1, freeze='A2', autofilter=True):
    _style_header_row(ws, header_row)
    ws.freeze_panes=freeze
    if autofilter:
        ws.auto_filter.ref=f"A{header_row}:{get_column_letter(ws.max_column)}{max(header_row+1,ws.max_row)}"
    _table_border(ws, header_row, 1, ws.max_row, ws.max_column)
    for cell in ws[header_row]:
        cell.font = Font(name='Calibri', size=11, bold=True, color='00000000')
    for r in range(header_row + 1, ws.max_row + 1):
        ws.row_dimensions[r].height = 15

def _write_instrument_leaders_section(ws, start_row, start_col, leaders):
    ws.merge_cells(start_row=start_row,start_column=start_col,end_row=start_row,end_column=start_col+4)
    ws.cell(start_row,start_col,"Instrument leaders").font=Font(bold=True)
    headers=["Metric","Symbol","Wins","Losses","Trades"]
    for i,h in enumerate(headers):
        ws.cell(start_row+1,start_col+i,h).font=Font(bold=True)
    rows=[("Overall most wins","most_wins_instrument"),("Overall most losses","most_losses_instrument"),("FX most wins","fx_most_wins_instrument"),("FX most losses","fx_most_losses_instrument"),("Crypto most wins","crypto_most_wins_instrument"),("Crypto most losses","crypto_most_losses_instrument")]
    rr=start_row+2
    for label,key in rows:
        v=leaders.get(key) or {}
        ws.cell(rr,start_col,label).font=Font(bold=True)
        ws.cell(rr,start_col+1,v.get("symbol") or "—")
        ws.cell(rr,start_col+2,v.get("wins"))
        ws.cell(rr,start_col+3,v.get("losses"))
        ws.cell(rr,start_col+4,v.get("total_trades"))
        rr += 1
    _table_border(ws,start_row,start_col,rr-1,start_col+4)
    return rr - 1




def _parse_duration_text(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return None
    total = 0.0
    for n,u in __import__('re').findall(r'([0-9]+(?:\.[0-9]+)?)\s*(day|days|hour|hours|minute|minutes|second|seconds)', text):
        num = float(n)
        if u.startswith('day'): total += num*86400
        elif u.startswith('hour'): total += num*3600
        elif u.startswith('minute'): total += num*60
        else: total += num
    return total or None

def _excel_datetime_to_iso(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day).isoformat()
    if isinstance(v, (int,float)):
        try:
            base=datetime(1899,12,30)
            return (base+timedelta(days=float(v))).isoformat()
        except Exception:
            return str(v)
    return str(v or '')

def _alias_index(idx: Dict[str, int], *names: str) -> int | None:
    for n in names:
        if n in idx:
            return idx[n]
    return None


def _is_merged_non_anchor(ws, row: int, col: int) -> bool:
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
            return not (row == merged.min_row and col == merged.min_col)
    return False


def _header_map(ws, header_row: int = 1) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in range(1, ws.max_column + 1):
        key = str(ws.cell(header_row, c).value or "").strip()
        if key and key not in out:
            out[key] = c
    return out




def _snapshot_invariants(wb) -> Dict[str, Any]:
    out: Dict[str, Any] = {"sheetnames": list(wb.sheetnames)}
    dash = wb["Dashboard"] if "Dashboard" in wb.sheetnames else None
    if dash is not None:
        out["dash_merged"] = [str(r) for r in dash.merged_cells.ranges]
        out["dash_row_heights"] = {k: v.height for k, v in dash.row_dimensions.items()}
        out["dash_col_widths"] = {k: v.width for k, v in dash.column_dimensions.items()}
        out["dash_cf"] = [str(k.sqref) for k in dash.conditional_formatting._cf_rules.keys()]
        out["dash_freeze"] = dash.freeze_panes
    for name, key in (("All Trades", "all_trades_filter"), ("Instrument Averages", "instrument_filter")):
        ws = wb[name] if name in wb.sheetnames else None
        out[key] = (ws.auto_filter.ref if ws and ws.auto_filter else None)
    return out


def _assert_invariants_unchanged(before: Dict[str, Any], after: Dict[str, Any]) -> None:
    for key in ("sheetnames","dash_merged","dash_row_heights","dash_col_widths","dash_cf","dash_freeze","all_trades_filter","instrument_filter"):
        if before.get(key) != after.get(key):
            raise RuntimeError(f"Workbook structural invariant changed: {key}")


def _find_anchor_sections(ws, anchors: List[str], optional: List[str] | None = None) -> Dict[str, Dict[str, int]]:
    optional = optional or []
    all_anchors = list(dict.fromkeys([*anchors, *optional]))
    found: Dict[str, Dict[str, int]] = {}
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            text = str(ws.cell(r, c).value or "").strip().lower()
            for a in all_anchors:
                if text == a.lower() and a not in found:
                    found[a] = {"anchor_row": r, "anchor_col": c}
    missing = [a for a in anchors if a not in found]
    if missing:
        raise RuntimeError(f"Dashboard section anchors missing: {', '.join(missing)}")

    for name, meta in list(found.items()):
        ar, ac = meta["anchor_row"], meta["anchor_col"]
        same_row_right = [m["anchor_col"] for n,m in found.items() if m["anchor_row"] == ar and m["anchor_col"] > ac]
        end_col = (min(same_row_right)-1) if same_row_right else ws.max_column
        same_band_below = []
        for n,m in found.items():
            if m["anchor_row"] <= ar:
                continue
            if ac <= m["anchor_col"] <= end_col:
                same_band_below.append(m["anchor_row"])
        end_row = (min(same_band_below)-1) if same_band_below else ws.max_row
        found[name].update({"start_row": ar+1, "end_row": end_row, "start_col": ac, "end_col": end_col})
    return found


def _find_label_in_section(ws, label: str, section: Dict[str, int]) -> Tuple[int, int] | None:
    wanted = str(label or "").strip().lower()
    for r in range(max(1, section.get("start_row",1)), min(ws.max_row, section.get("end_row", ws.max_row))+1):
        for c in range(max(1, section.get("start_col",1)), min(ws.max_column, section.get("end_col", ws.max_column)-1)+1):
            if str(ws.cell(r,c).value or "").strip().lower() == wanted:
                return r,c
    return None
def _find_label_cell(ws, label: str, search_cols: List[int] | None = None) -> tuple[int, int] | None:
    wanted = str(label or "").strip().lower()
    if not wanted:
        return None
    cols = search_cols or list(range(1, ws.max_column + 1))
    for r in range(1, ws.max_row + 1):
        for c in cols:
            if str(ws.cell(r, c).value or "").strip().lower() == wanted:
                return (r, c)
    return None


def _write_value_preserving_cell(ws, row: int, col: int, value: Any) -> bool:
    if _is_merged_non_anchor(ws, row, col):
        return False
    ws.cell(row, col).value = value
    return True

def read_master_journal_source(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Master Journal workbook not found: {path}")
    wb = load_workbook(path, data_only=True)
    try:
        if 'All Trades' not in wb.sheetnames:
            raise RuntimeError('Master Journal is missing required All Trades sheet.')
        ws = wb['All Trades']
        headers = [str(c.value or '').strip() for c in ws[1]]
        idx = {h:i for i,h in enumerate(headers)}
        required = {'Open Time','Close Time','Account','Symbol','Side'}
        if not required.issubset(set(idx.keys())):
            raise RuntimeError('Master Journal All Trades headers are invalid.')
        items=[]; cashflow_ledger=defaultdict(list)
        def _num(v):
            try:
                if v in (None, ""): return None
                return float(v)
            except Exception: return None
        i_stop = _alias_index(idx, 'Stop Loss', 'Stop Loss Price')
        i_tp = _alias_index(idx, 'Take Profit', 'Target Price', 'Target')
        i_pnl = _alias_index(idx, 'Net P/L', 'Net Profit', 'Realized PnL')
        i_dur = _alias_index(idx, 'Trade Duration Seconds', 'Trade Duration')
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not any(v not in (None,'') for v in r):
                continue
            symbol = str(r[idx.get('Symbol',3)] or '').strip()
            side = str(r[idx.get('Side',4)] or '').strip()
            account = str(r[idx.get('Account',2)] or '').strip()
            row_id = str(r[idx.get('Row ID',len(r)-1)] or '').strip() if 'Row ID' in idx else ''
            row_type_raw = str(r[idx.get('Row Type')]).strip().lower() if 'Row Type' in idx and idx.get('Row Type') is not None else ''
            row_type = row_type_raw if row_type_raw in {'cashflow','monthly_aud_reval','trade'} else ('cashflow' if symbol.upper()=='CASHFLOW' else ('monthly_aud_reval' if symbol.upper()=='MONTHLY AUD P/L' else 'trade'))
            open_time = _excel_datetime_to_iso(r[idx.get('Open Time',0)])
            close_time = _excel_datetime_to_iso(r[idx.get('Close Time',1)])
            duration = _num(r[i_dur]) if i_dur is not None else None
            if duration is None and i_dur is not None: duration = _parse_duration_text(r[i_dur])
            account_u = account.upper()
            symbol_u = symbol.upper().replace('_','/').replace('-','/')
            if any(t in account_u for t in ('OANDA','PEPPERSTONE','FOREX',' FX')):
                asset_class = 'fx'
            elif any(t in account_u for t in ('BYBIT','BINANCE','COINSPOT')):
                asset_class = 'crypto'
            elif any(t in symbol_u for t in ('USDT','USDC','BTC','ETH','PERP')):
                asset_class = 'crypto'
            else:
                asset_class = ''
            item={'id': row_id or stable_row_id({'account':account,'symbol':symbol,'side':side,'open_time':open_time,'close_time':close_time}), 'row_type':row_type,'account':account,'symbol':symbol,'side':side,'open_time':open_time,'close_time':close_time,'qty':_num(r[idx.get('Qty')]) if 'Qty' in idx else None,'entry_price':_num(r[idx.get('Entry Price')]) if 'Entry Price' in idx else None,'exit_price':_num(r[idx.get('Exit Price')]) if 'Exit Price' in idx else None,'stop_loss':_num(r[i_stop]) if i_stop is not None else None,'take_profit':_num(r[i_tp]) if i_tp is not None else None,'commission':_num(r[idx.get('Commission')]) if 'Commission' in idx else None,'net_profit':_num(r[i_pnl]) if i_pnl is not None else None,'result_pct':_num(r[idx.get('Profit %')]) if 'Profit %' in idx else None,'r_multiple':_num(r[idx.get('R-Multiple')]) if 'R-Multiple' in idx else None,'balance_after_trade':_num(r[idx.get('Balance After')]) if 'Balance After' in idx else None,'trade_duration_seconds':duration,'is_test_trade':str(r[idx.get('Test')]).strip().lower() in {'yes','y','true','1'} if 'Test' in idx else False,'setup':r[idx.get('Setup',17)] if 'Setup' in idx else '','timeframe':r[idx.get('Timeframe',18)] if 'Timeframe' in idx else '','breakeven':r[idx.get('Breakeven',19)] if 'Breakeven' in idx else '','notes':r[idx.get('Notes',20)] if 'Notes' in idx else '','currency':str(r[idx.get('Currency')] or '').strip() if 'Currency' in idx else '', 'asset_class': asset_class}
            items.append(item)
            if row_type=='cashflow':
                cashflow_ledger[account].append({'account':account,'date':item['close_time'] or item['open_time'],'amount':_num(r[idx.get('Cashflow Amount')]) if 'Cashflow Amount' in idx else _num(r[i_pnl]) if i_pnl is not None else None,'new_balance':_num(r[idx.get('Cashflow New Balance')]) if 'Cashflow New Balance' in idx else item.get('balance_after_trade'),'currency':str(r[idx.get('Currency')] or '').strip() if 'Currency' in idx else '','reason':item.get('notes') or '', 'side':side})
        return {'items':items,'cashflow_ledger':dict(cashflow_ledger)}
    finally:
        wb.close()

def update_master_journal_workbook_data_only(path: Path, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    wb = load_workbook(path)
    diagnostics: Dict[str, Any] = {"missing_accounts": [], "updated_cells": 0}
    try:
        if "Dashboard" not in wb.sheetnames:
            raise RuntimeError("Master Journal missing Dashboard sheet.")
        dash = wb["Dashboard"]
        before = _snapshot_invariants(wb)

        stats = snapshot.get("stats") or {}
        groups = stats.get("groups") or {}
        by_market = groups.get("by_market") or {}
        risk = groups.get("risk_expectancy") or {}
        leaders = groups.get("leaders") or {}
        totals = stats.get("totals") or {}

        anchors = _find_anchor_sections(dash, ["Account Balances", "Instrument leaders", "Overall", "Winners", "Losers", "Drawdown", "FX", "Crypto"], optional=["Duration"])

        def write_metric(section: str, label: str, value: Any, metric_type: str = "raw"):
            if value is None:
                return
            pos = _find_label_in_section(dash, label, anchors[section])
            if not pos:
                return
            out = value
            if metric_type == "pct":
                pct = _as_float(value)
                if pct is None:
                    return
                out = pct / 100.0
            elif metric_type == "duration":
                out = _fmt_duration_full(value)
            elif metric_type == "count":
                f = _as_float(value)
                out = int(f) if f is not None else value
            elif metric_type == "source":
                out = _fmt_detail_src(value)
            if _write_value_preserving_cell(dash, pos[0], pos[1]+1, out):
                diagnostics["updated_cells"] += 1

        def write_source_below(section: str, metric_label: str, source_val: Any):
            if source_val is None:
                return
            pos = _find_label_in_section(dash, metric_label, anchors[section])
            if not pos:
                return
            sr = pos[0] + 1
            if sr > anchors[section]["end_row"]:
                return
            if str(dash.cell(sr, pos[1]).value or "").strip().lower() == "source":
                _write_value_preserving_cell(dash, sr, pos[1] + 1, _fmt_detail_src(source_val))
                diagnostics["updated_cells"] += 1

        section_maps = {
            "Overall": by_market.get("overall") or totals,
            "FX": by_market.get("fx") or {},
            "Crypto": by_market.get("crypto") or {},
        }
        for section, bucket in section_maps.items():
            write_metric(section, "Trades", bucket.get("trades"), "count")
            write_metric(section, "Wins", bucket.get("wins"), "count")
            write_metric(section, "Losses", bucket.get("losses"), "count")
            write_metric(section, "Break-even", bucket.get("break_even"), "count")
            write_metric(section, "Win rate", bucket.get("win_rate_pct"), "pct")
            write_metric(section, "Net P/L", bucket.get("net_profit_total"))
            write_metric(section, "Avg result %", bucket.get("avg_result_pct"), "pct")
            write_metric(section, "Avg R", bucket.get("avg_r_multiple"), "raw")
            write_metric(section, "Gross gain", bucket.get("gross_gain"))
            write_metric(section, "Gross loss", bucket.get("gross_loss"))
            write_metric(section, "Max loss %", bucket.get("min_result_pct"), "pct")
            write_metric(section, "Max win %", bucket.get("max_result_pct"), "pct")
            write_metric(section, "Max R loss", bucket.get("min_r_multiple"))
            write_metric(section, "Max R win", bucket.get("max_r_multiple"))
            write_metric(section, "Max gain", bucket.get("max_gain"))
            write_metric(section, "Max loss", bucket.get("max_loss"))
            write_metric(section, "Avg stop %", bucket.get("avg_stop_pct"), "pct")
            write_metric(section, "Avg target %", bucket.get("avg_target_pct"), "pct")
            write_metric(section, "Avg duration", bucket.get("avg_duration_seconds"), "duration")
            msrc = bucket.get("metric_sources") or {}
            write_source_below(section, "Max loss %", msrc.get("min_result_pct"))
            write_source_below(section, "Max win %", msrc.get("max_result_pct"))
            write_source_below(section, "Max R loss", msrc.get("min_r_multiple"))
            write_source_below(section, "Max R win", msrc.get("max_r_multiple"))
            write_source_below(section, "Max gain", msrc.get("max_gain"))
            write_source_below(section, "Max loss", msrc.get("max_loss"))


        write_metric("Winners", "Avg result %", risk.get("avg_result_pct_winners"), "pct")
        write_metric("Winners", "Avg R", risk.get("avg_r_multiple_winners"))
        write_metric("Winners", "Avg stop %", risk.get("avg_stop_pct_winners"), "pct")
        write_metric("Winners", "Avg target %", risk.get("avg_target_pct_winners"), "pct")
        write_metric("Losers", "Avg result %", risk.get("avg_result_pct_losers"), "pct")
        write_metric("Losers", "Avg R", risk.get("avg_r_multiple_losers"))
        write_metric("Losers", "Avg stop %", risk.get("avg_stop_pct_losers"), "pct")
        write_metric("Losers", "Avg target %", risk.get("avg_target_pct_losers"), "pct")
        write_metric("Drawdown", "Max drawdown", risk.get("max_drawdown_pct"), "pct")
        write_metric("Drawdown", "Avg drawdown", risk.get("avg_drawdown_pct"), "pct")
        duration = groups.get("duration") or {}
        write_metric("FX", "FX shortest", duration.get("fx_shortest_seconds"), "duration")
        write_metric("FX", "FX longest", duration.get("fx_longest_seconds"), "duration")
        write_metric("Crypto", "Crypto shortest", duration.get("crypto_shortest_seconds"), "duration")
        write_metric("Crypto", "Crypto longest", duration.get("crypto_longest_seconds"), "duration")
        dsrc = duration.get("metric_sources") or {}
        write_source_below("FX", "FX shortest", dsrc.get("fx_shortest_seconds"))
        write_source_below("FX", "FX longest", dsrc.get("fx_longest_seconds"))
        write_source_below("Crypto", "Crypto shortest", dsrc.get("crypto_shortest_seconds"))
        write_source_below("Crypto", "Crypto longest", dsrc.get("crypto_longest_seconds"))
        if "Duration" in anchors:
            write_metric("Duration", "Overall avg", duration.get("overall_avg_seconds"), "duration")
            write_metric("Duration", "Overall shortest", duration.get("overall_shortest_seconds"), "duration")
            write_metric("Duration", "Overall longest", duration.get("overall_longest_seconds"), "duration")
            write_metric("Duration", "FX shortest", duration.get("fx_shortest_seconds"), "duration")
            write_metric("Duration", "FX longest", duration.get("fx_longest_seconds"), "duration")
            write_metric("Duration", "Crypto shortest", duration.get("crypto_shortest_seconds"), "duration")
            write_metric("Duration", "Crypto longest", duration.get("crypto_longest_seconds"), "duration")
            dsrc = duration.get("metric_sources") or {}
            write_source_below("Duration", "Overall shortest", dsrc.get("overall_shortest_seconds"))
            write_source_below("Duration", "Overall longest", dsrc.get("overall_longest_seconds"))
            write_source_below("Duration", "FX shortest", dsrc.get("fx_shortest_seconds"))
            write_source_below("Duration", "FX longest", dsrc.get("fx_longest_seconds"))
            write_source_below("Duration", "Crypto shortest", dsrc.get("crypto_shortest_seconds"))
            write_source_below("Duration", "Crypto longest", dsrc.get("crypto_longest_seconds"))

        diagnostics.setdefault("missing_leader_headers", [])
        diagnostics.setdefault("missing_leader_rows", [])
        leader_section = anchors["Instrument leaders"]
        leader_headers = {}
        leader_header_row = None
        for r in range(leader_section["start_row"], leader_section["end_row"] + 1):
            row_map = {}
            for c in range(leader_section["start_col"], leader_section["end_col"] + 1):
                hv = str(dash.cell(r, c).value or "").strip().lower()
                if hv in {"metric", "symbol", "wins", "losses", "trades"}:
                    row_map[hv] = c
            if {"metric", "symbol", "wins", "losses", "trades"}.issubset(row_map.keys()):
                leader_headers = row_map
                leader_header_row = r
                break
        if not leader_headers:
            diagnostics["missing_leader_headers"].append("Metric/Symbol/Wins/Losses/Trades")
        else:
            label_to_key = {
                "overall most wins": "most_wins_instrument",
                "overall most losses": "most_losses_instrument",
                "fx most wins": "fx_most_wins_instrument",
                "fx most losses": "fx_most_losses_instrument",
                "crypto most wins": "crypto_most_wins_instrument",
                "crypto most losses": "crypto_most_losses_instrument",
            }
            metric_rows = {}
            for r in range((leader_header_row or leader_section["start_row"]) + 1, leader_section["end_row"] + 1):
                metric_label = str(dash.cell(r, leader_headers["metric"]).value or "").strip().lower()
                if metric_label:
                    metric_rows[metric_label] = r
            for metric_label, key in label_to_key.items():
                row_idx = metric_rows.get(metric_label)
                if not row_idx:
                    diagnostics["missing_leader_rows"].append(metric_label)
                    continue
                payload = leaders.get(key) or {}
                normalized_payload = dict(payload)
                if normalized_payload.get("trades") is None and normalized_payload.get("total_trades") is not None:
                    normalized_payload["trades"] = normalized_payload.get("total_trades")
                if normalized_payload.get("total_trades") is None and normalized_payload.get("trades") is not None:
                    normalized_payload["total_trades"] = normalized_payload.get("trades")
                for fld, col_name in (("symbol", "symbol"), ("wins", "wins"), ("losses", "losses"), ("trades", "trades")):
                    if fld not in normalized_payload or normalized_payload.get(fld) is None:
                        continue
                    if _write_value_preserving_cell(dash, row_idx, leader_headers[col_name], normalized_payload.get(fld)):
                        diagnostics["updated_cells"] += 1

        balances = snapshot.get("balances") or []
        diagnostics.setdefault("non_numeric_balance_accounts", [])
        section = anchors["Account Balances"]
        header_row = section["start_row"]
        col_map = {}
        for c in range(section["start_col"], section["end_col"] + 1):
            h = str(dash.cell(header_row, c).value or "").strip().lower()
            if h in {"account"}: col_map["account"] = c
            elif h in {"balance"}: col_map["balance"] = c
            elif h in {"currency"}: col_map["currency"] = c
            elif h in {"as of", "as_of"}: col_map["as_of"] = c
        if "account" not in col_map or "balance" not in col_map or "currency" not in col_map:
            raise RuntimeError("Account Balances headers missing in section.")
        account_rows = {}
        for r in range(header_row + 1, section["end_row"] + 1):
            lbl = str(dash.cell(r,col_map["account"]).value or "").strip()
            if lbl:
                account_rows[lbl.upper()] = r
        for b in balances:
            label = str(b.get("account_label") or b.get("account") or "").strip()
            if not label:
                continue
            row = account_rows.get(label.upper())
            if not row:
                diagnostics["missing_accounts"].append(label)
                continue
            bal_num = _as_float(b.get("balance"))
            if bal_num is None:
                diagnostics["non_numeric_balance_accounts"].append(label)
                continue
            if _write_value_preserving_cell(dash, row, col_map["balance"], bal_num):
                diagnostics["updated_cells"] += 1
            curr = str(b.get("currency") or "").strip()
            if curr:
                if _write_value_preserving_cell(dash, row, col_map["currency"], curr):
                    diagnostics["updated_cells"] += 1
            if "as_of" in col_map:
                as_of = str(b.get("as_of") or "").strip()
                if as_of:
                    if _write_value_preserving_cell(dash, row, col_map["as_of"], as_of):
                        diagnostics["updated_cells"] += 1

        after = _snapshot_invariants(wb)
        _assert_invariants_unchanged(before, after)
        wb.save(path)
        return {"ok": True, "path": str(path), "diagnostics": diagnostics}
    finally:
        wb.close()

def refresh_master_journal_derived_sheets(path: Path, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Master Journal workbook not found: {path}")
    wb = load_workbook(path)
    try:
        if 'All Trades' not in wb.sheetnames:
            raise RuntimeError('Master Journal missing All Trades sheet.')
        all_trades = wb['All Trades']
        # remove derived and legacy sheets
        for name in ['Dashboard','Instrument Averages','P&L Calendar','_Trade Meta']:
            if name in wb.sheetnames:
                wb.remove(wb[name])
        wb.create_sheet('Dashboard',0)
        wb._sheets.insert(1, wb._sheets.pop(wb._sheets.index(all_trades)))
        wb.create_sheet('Instrument Averages',2)
        wb.create_sheet('P&L Calendar',3)
        # fill derived sheets by building temp and copying values/styles
        tmp = path.with_suffix('.derived.tmp.xlsx')
        build_master_journal_workbook(snapshot, tmp)
        gen = load_workbook(tmp)
        try:
            for n in ['Dashboard','Instrument Averages','P&L Calendar']:
                src=gen[n]; dst=wb[n]
                for r in src.iter_rows(min_row=1,max_row=src.max_row,min_col=1,max_col=src.max_column):
                    for c in r:
                        d=dst.cell(c.row,c.column,c.value); d.number_format=c.number_format; d.font=c.font.copy(); d.fill=c.fill.copy(); d.border=c.border.copy(); d.alignment=c.alignment.copy()
                dst.freeze_panes = src.freeze_panes
                dst.auto_filter.ref = src.auto_filter.ref
                for k,v in src.column_dimensions.items(): dst.column_dimensions[k].width=v.width
        finally:
            gen.close()
            tmp.unlink(missing_ok=True)
        tmp_out = path.with_suffix('.pending.xlsx')
        wb.save(tmp_out)
        tmp_out.replace(path)
        return {'ok': True, 'path': str(path)}
    finally:
        wb.close()
