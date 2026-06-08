from __future__ import annotations
from collections import defaultdict, OrderedDict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
import hashlib
from openpyxl.styles import PatternFill, Border, Side, Alignment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
import calendar
from copy import copy
import math
import re
from zoneinfo import ZoneInfo

TRADE_LOG_SHEET = "Trade Log"
LEGACY_ALL_TRADES_SHEET = "All Trades"
# Backward-compatible aliases (do not remove yet; external imports may still reference these).
ALL_TRADES_SHEET = LEGACY_ALL_TRADES_SHEET
LEGACY_TRADE_LOG_SHEET = LEGACY_ALL_TRADES_SHEET
SHEET_ORDER=["Dashboard","Trade Log","Instrument Averages","P&L Calendar"]
REPORT_YEARLY_SHEET = "YEARLY REPORT"
REPORT_START_YEAR = 2018
REPORT_MIN_END_YEAR = 2026
TRADE_NUMBER_HEADER = "Trade Number"
MOVE_TO_FIELD_MAP = {
    "Move to Break Even Time": "move_to_break_even_time",
    "Move to Break Even Duration": "move_to_break_even_duration",
    "Move to Break Even Trigger Price": "move_to_break_even_trigger_price",
    "Move to Break Even Distance From Entry %": "move_to_break_even_distance_from_entry_pct",
    "Move to Break Even Distance From Exit %": "move_to_break_even_distance_from_exit_pct",
    "Move to Profit Time": "move_to_profit_time",
    "Move to Profit Duration": "move_to_profit_duration",
    "Move to Profit Trigger Price": "move_to_profit_trigger_price",
    "Move to Profit Distance From Entry %": "move_to_profit_distance_from_entry_pct",
    "Move to Profit Distance From Exit %": "move_to_profit_distance_from_exit_pct",
}
QUALITY_ANALYSIS_FIELD_MAP = {
    "Pattern": "pattern",
    "EMA": "ema",
    "ATHS/ATLS": "aths_atls",
    "Order": "order_type",
    "Round Number": "round_number",
    "Spiked Out": "spiked_out",
    "Close Stopout": "close_stopout",
    "Near Perfect Entry": "near_perfect_entry",
    "Near Win": "near_win",
    "Early Close": "early_close",
}
TRADE_NUMBER_FIELD_MAP = {TRADE_NUMBER_HEADER: "trade_number"}
TRADE_LOG_MANUAL_FIELD_MAP = {**TRADE_NUMBER_FIELD_MAP, **MOVE_TO_FIELD_MAP, **QUALITY_ANALYSIS_FIELD_MAP}
EDITABLE_COLS=["Test",*TRADE_LOG_MANUAL_FIELD_MAP.keys(),"Setup","Timeframe","Breakeven","Notes"]

TRADE_LOG_HEADERS_V1 = [
    "Open Time", "Close Time", "Account", "Symbol", "Side", "Qty",
    "Entry Price", "Exit Price", "Stop Loss Price", "Stop Loss Distance",
    "Target Price", "Target Distance", "Commission", "Net P/L",
    "Profit %", "R-Multiple", "Balance After",
    "Trade Duration (DD:HH:MM:SS)", *MOVE_TO_FIELD_MAP.keys(),
    "Test", "Pattern", "EMA", "ATHS/ATLS", "Order", "Round Number",
    "Spiked Out", "Close Stopout", "Near Perfect Entry", "Near Win", "Early Close",
    "Setup", "Timeframe", "Breakeven", "Notes", "Cashflow Amount",
    "Cashflow New Balance", "Currency", "Row Type", "Row ID",
]
TRADE_LOG_HEADERS = [TRADE_NUMBER_HEADER, *TRADE_LOG_HEADERS_V1]
PRE_MOVE_TRADE_LOG_HEADERS = [
    "Open Time", "Close Time", "Account", "Symbol", "Side", "Qty",
    "Entry Price", "Exit Price", "Stop Loss Price", "Stop Loss Distance",
    "Target Price", "Target Distance", "Commission", "Net P/L",
    "Profit %", "R-Multiple", "Balance After",
    "Trade Duration (DD:HH:MM:SS)", "Test", "Pattern", "EMA",
    "ATHS/ATLS", "Order", "Round Number", "Spiked Out", "Close",
    "Stop Out", "Near Perfect Entry", "Near Win", "Early Close",
    "Setup", "Timeframe", "Breakeven", "Notes", "Cashflow Amount",
    "Cashflow New Balance", "Currency", "Row Type", "Row ID",
]
OLD_TRADE_LOG_HEADERS = [
    "Open Time", "Close Time", "Account", "Symbol", "Side", "Qty",
    "Entry Price", "Exit Price", "Stop Loss Price", "Stop Loss Distance",
    "Target Price", "Target Distance", "Commission", "Net P/L",
    "Profit %", "R-Multiple", "Balance After",
    "Trade Duration (DD:HH:MM:SS)", "Test", "Setup", "Timeframe",
    "Breakeven", "Notes", "Cashflow Amount", "Cashflow New Balance",
    "Currency", "Row Type", "Row ID",
]
TRADE_LOG_HEADER_ROWS = 2
TRADE_LOG_FILTER_HEADER_ROW = 2
TRADE_LOG_DATA_START_ROW = 3
MOVE_TO_BREAK_EVEN_HEADERS = list(MOVE_TO_FIELD_MAP.keys())[:5]
MOVE_TO_PROFIT_HEADERS = list(MOVE_TO_FIELD_MAP.keys())[5:]
MOVE_TO_SUBHEADERS = ["Time", "Duration", "Trigger Price", "Distance From Entry %", "Distance From Exit %"]
DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL = "Move to Break Even (DD:HH:MM:SS)"
DASHBOARD_MOVE_TO_PROFIT_LABEL = "Move to Profit (DD:HH:MM:SS)"
REPORT_METRIC_LABELS = [
    "Trades",
    "Wins",
    "Losses",
    "Break-even",
    "Test",
    "Win rate",
    "Net P/L",
    "Gross gain",
    "Gross loss",
    "Best Win Streak",
    "Worst Losing Streak",
    "Avg result %",
    "Avg R",
    "Avg stop %",
    "Avg target %",
    "Max target %",
    "Avg duration (DD:HH:MM:SS)",
    "Move to Break Even (DD:HH:MM:SS)",
    "Move to Profit (DD:HH:MM:SS)",
    "Max win %",
    "Source",
    "Max loss %",
    "Source",
    "Max R loss",
    "Source",
    "Max R win",
    "Source",
    "Shortest (DD:HH:MM:SS)",
    "Source",
    "Longest (DD:HH:MM:SS)",
    "Source",
    "Winners",
    "Avg stop %",
    "Avg target %",
    "Avg result %",
    "Avg R",
    "Losers",
    "Avg stop %",
    "Avg target %",
    "Avg result %",
    "Avg R",
    "Drawdown",
    "Max drawdown",
    "Avg drawdown",
    "Longs",
    "Long wins",
    "Long losses",
    "Long break-even",
    "Shorts",
    "Short wins",
    "Short losses",
    "Short break-even",
]
LIGHT_GREY_FILL_RGB = "FFEAF2F8"
JOURNAL_DISPLAY_TZ = ZoneInfo("Australia/Brisbane")

def _canonical_journal_timeframe(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    key = text.lower().replace("-", " ")
    key = " ".join(key.split())
    aliases = {"1m":"1MIN","1 min":"1MIN","1 minute":"1MIN","5m":"5MIN","15m":"15MIN","30m":"30MIN","1h":"1H","4h":"4H","1d":"1D","1w":"1W","1mo":"1MO","1 month":"1MO"}
    return aliases.get(key, text)
PROFIT_FILL = "C6EFCE"
PROFIT_FONT = "006100"
LOSS_FILL = "FFC7CE"
LOSS_FONT = "9C0006"

def _semantic_fill_rgb(cell) -> str:
    fill = getattr(cell, "fill", None)
    color = getattr(fill, "fgColor", None)
    rgb = str(getattr(color, "rgb", "") or "")
    return rgb[-6:].upper() if getattr(fill, "fill_type", None) == "solid" and rgb else ""

def _apply_full_cell_semantic_fill(cell, semantic: str | None) -> None:
    """Apply a direct profit/loss fill while preserving all unrelated cell styling."""
    if semantic not in {"profit", "loss"}:
        return
    fill_rgb, font_rgb = (PROFIT_FILL, PROFIT_FONT) if semantic == "profit" else (LOSS_FILL, LOSS_FONT)
    font = copy(cell.font)
    font.color = font_rgb
    cell.font = font
    cell.fill = PatternFill(fill_type="solid", fgColor=fill_rgb)

def _clear_generated_semantic_fill(cell) -> None:
    if _semantic_fill_rgb(cell) not in {PROFIT_FILL, LOSS_FILL}:
        return
    font = copy(cell.font)
    if getattr(font.color, "type", None) == "rgb" and str(font.color.rgb or "")[-6:].upper() in {PROFIT_FONT, LOSS_FONT}:
        font.color = "000000"
    cell.font = font
    cell.fill = PatternFill()

def _apply_sign_based_full_cell_fill(cell) -> None:
    value = _as_float(cell.value)
    if value is None or value == 0:
        _clear_generated_semantic_fill(cell)
    else:
        _apply_full_cell_semantic_fill(cell, "profit" if value > 0 else "loss")

LEADER_LABEL_TO_KEY = {
    "overall most wins": "most_wins_instrument",
    "overall most losses": "most_losses_instrument",
    "fx most wins": "fx_most_wins_instrument",
    "fx most losses": "fx_most_losses_instrument",
    "crypto most wins": "crypto_most_wins_instrument",
    "crypto most losses": "crypto_most_losses_instrument",
}

def _get_all_trades_sheet(wb: Workbook, *, allow_legacy: bool = True):
    has_trade_log = TRADE_LOG_SHEET in wb.sheetnames
    has_legacy_all_trades = LEGACY_ALL_TRADES_SHEET in wb.sheetnames
    if has_trade_log and has_legacy_all_trades:
        raise RuntimeError("Master Journal has ambiguous trade sheets: both 'Trade Log' and legacy 'All Trades' exist.")
    if has_trade_log:
        return wb[TRADE_LOG_SHEET]
    if allow_legacy and has_legacy_all_trades:
        return wb[LEGACY_ALL_TRADES_SHEET]
    raise RuntimeError("Master Journal is missing required Trade Log sheet.")

def _get_trade_log_sheet(wb: Workbook, *, allow_legacy: bool = True):
    return _get_all_trades_sheet(wb, allow_legacy=allow_legacy)

def _migrate_legacy_trade_log_sheet_name(wb: Workbook, diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    has_trade_log = TRADE_LOG_SHEET in wb.sheetnames
    has_legacy_all_trades = LEGACY_ALL_TRADES_SHEET in wb.sheetnames
    if has_trade_log and has_legacy_all_trades:
        raise RuntimeError("Master Journal has ambiguous trade sheets: both 'Trade Log' and legacy 'All Trades' exist.")
    if has_trade_log:
        return
    if has_legacy_all_trades:
        wb[LEGACY_ALL_TRADES_SHEET].title = TRADE_LOG_SHEET
        diagnostics["migrated_trade_log_sheet"] = True
        return
    raise RuntimeError("Master Journal is missing required Trade Log sheet.")

def _remove_legacy_trade_meta_sheet(wb: Workbook, diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    if "_Trade Meta" in wb.sheetnames:
        wb.remove(wb["_Trade Meta"])
        diagnostics["removed_legacy_trade_meta"] = True

def _repair_legacy_instrument_averages_freeze_pane(wb: Workbook, diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    if "Instrument Averages" not in wb.sheetnames:
        return
    ws = wb["Instrument Averages"]
    previous = str(ws.freeze_panes or "")
    if previous != "B2":
        ws.freeze_panes = "B2"
        diagnostics["repaired_instrument_averages_freeze_pane"] = True
        diagnostics["previous_instrument_averages_freeze_pane"] = previous

def _pct_points_to_excel_fraction(value: Any) -> float | None:
    num = _as_float(value)
    return None if num is None else num / 100.0

def _excel_fraction_to_pct_points(value: Any) -> float | None:
    num = _as_float(value)
    return None if num is None else num * 100.0


def _distance_fraction_from_prices(entry: Any, level: Any) -> float | None:
    entry_num = _as_float(entry)
    level_num = _as_float(level)
    if entry_num is None or level_num is None:
        return None
    if not (math.isfinite(entry_num) and math.isfinite(level_num)) or entry_num <= 0 or level_num <= 0:
        return None
    return abs(level_num - entry_num) / entry_num


def _normalize_pct_distance_cell(value: Any, number_format: Any = None) -> float | None:
    """Return internal percentage points from a workbook distance cell.

    Excel percentage-formatted cells store fractions (0.01 displays as 1%).
    Plain numeric values are already treated as internal percent points.
    """
    num = _as_float(value)
    if num is None:
        return None
    fmt = str(number_format or "")
    return num * 100.0 if "%" in fmt else num


def _cell_fill_rgb(cell) -> str:
    fg = getattr(getattr(cell, "fill", None), "fgColor", None)
    rgb = str(getattr(fg, "rgb", "") or "").upper()
    return rgb


def _is_light_grey_no_metric_cell(cell) -> bool:
    rgb = _cell_fill_rgb(cell)
    return rgb == LIGHT_GREY_FILL_RGB or rgb.endswith(LIGHT_GREY_FILL_RGB[-6:])


def _is_likely_fx_pair(value: str) -> bool:
    token = str(value or '').upper().replace('/','').replace('-','').replace('_','')
    
    if not (len(token) == 6 and token.isalpha()):
        return False
    known = {"USD","EUR","GBP","JPY","AUD","NZD","CAD","CHF"}
    return token[:3] in known and token[3:] in known
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


def _duration_seconds_to_ddhhmmss_number(seconds: Any) -> int | None:
    v = _as_float(seconds)
    if v is None:
        return None
    s = max(0, int(v))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return days * 1000000 + hours * 10000 + minutes * 100 + secs

def _duration_ddhhmmss_cell_to_seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    raw = value
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    try:
        n = int(float(raw))
    except Exception:
        return None
    if n < 0:
        return None
    dd = n // 1_000_000
    hh = (n // 10_000) % 100
    mm = (n // 100) % 100
    ss = n % 100
    if hh >= 24 or mm >= 60 or ss >= 60:
        return None
    return dd * 86400 + hh * 3600 + mm * 60 + ss

def _fmt_duration(seconds: Any) -> str:
    n = _duration_seconds_to_ddhhmmss_number(seconds)
    if n is None:
        return "—"
    return f"{n:08d}"





def _fmt_duration_full(seconds: Any) -> int | None:
    return _duration_seconds_to_ddhhmmss_number(seconds)

def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        raw = raw.replace("Z", "")
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except Exception:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(raw)
            except Exception:
                return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(JOURNAL_DISPLAY_TZ).replace(tzinfo=None)
    return dt

def _round_trade_duration_seconds(delta_seconds: Any) -> int | None:
    val = _as_float(delta_seconds)
    if val is None or not math.isfinite(val) or val < 0:
        return None
    if val < 1:
        return 1
    return max(1, int(val + 0.5))

def _infer_trade_duration_seconds(row: Dict[str, Any]) -> int | None:
    if str(row.get("row_type") or "trade").strip().lower() != "trade":
        return None
    for key in ("trade_duration_seconds", "duration_seconds"):
        val = _as_float(row.get(key))
        if val is not None and val >= 0:
            return _round_trade_duration_seconds(val)
    ot = _as_datetime(row.get("open_time"))
    ct = _as_datetime(row.get("close_time"))
    if not ot or not ct:
        return None
    delta = (ct - ot).total_seconds()
    return _round_trade_duration_seconds(delta)

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

_MONTHLY_AUD_REVAL_ROW_ID_RE = re.compile(r"^monthly_aud_reval:bybit_live:(\d{4}-\d{2})$")

def _monthly_aud_reval_row_id_month(row_id: Any) -> str:
    m = _MONTHLY_AUD_REVAL_ROW_ID_RE.match(str(row_id or "").strip())
    return m.group(1) if m else ""

def _is_monthly_aud_reval_semantic_row(row: Dict[str, Any]) -> bool:
    row_type = str(row.get("row_type") or "").strip().lower()
    symbol = str(row.get("symbol") or "").strip().upper()
    account = _canonical_account_label(row.get("account_label") or row.get("account"))
    return row_type == "monthly_aud_reval" and symbol == "MONTHLY AUD P/L" and account == "BYBIT"

def _canonical_account_label(label: Any) -> str:
    raw = str(label or "").strip()
    low = raw.lower().replace("_", " ").replace("-", " ")
    parts = {p for p in low.split() if p}
    if "bybit" in parts and "demo" in parts:
        return "BYBIT DEMO"
    if "bybit" in parts and ("live" in parts or len(parts) == 1):
        return "BYBIT"
    if "pepperstone" in parts and "demo" in parts:
        return "PEPPERSTONE DEMO"
    if "pepperstone" in parts and "live" in parts:
        return "PEPPERSTONE LIVE"
    return raw

def _repair_or_flag_zero_trade_qty(row: Dict[str, Any]) -> Dict[str, Any]:
    if str(row.get("row_type") or "trade").lower() != "trade":
        return row
    qty=_as_float(row.get("qty"))
    if qty is None or qty!=0:
        return row
    refs=row.get("raw_refs") if isinstance(row.get("raw_refs"),dict) else {}
    for k in ("qty_raw","closedSize","closed_size","execQty","exec_qty","cumExecQty","qty","size","Filled Qty","Size Quantity"):
        cand=_as_float(row.get(k) if k in row else refs.get(k))
        if cand is not None and cand>0:
            row["qty"]=cand; row.setdefault("diagnostics",[]).append("qty_repaired_from_source")
            return row
    sym=str(row.get("symbol") or "").upper()
    acct=str(row.get("account") or row.get("account_label") or "").upper()
    if any(x in acct for x in ("OANDA","PEPPERSTONE")) or ("/" in sym and len(sym)==6):
        row.setdefault("diagnostics",[]).append("zero_qty_unrepaired_fx")
        return row
    ep=_as_float(row.get("entry_price")); xp=_as_float(row.get("exit_price")); np=_as_float(row.get("net_profit")); fee=_as_float(row.get("commission") if row.get("commission") is not None else row.get("fees")) or 0.0
    side=str(row.get("side") or "").upper()
    if (sym.endswith("USDT") or sym.endswith("USDC")) and None not in (ep,xp,np) and side in {"BUY","SELL"}:
        den=(xp-ep) if side=="BUY" else (ep-xp)
        if den and den!=0:
            q=(np+abs(fee))/den
            if q>0 and math.isfinite(q):
                chk=(q*den)-abs(fee)
                if abs(chk-np)<=max(1e-6,abs(np)*1e-5):
                    row["qty"]=q; row.setdefault("diagnostics",[]).append("qty_inferred_from_pnl")
                    return row
    row.setdefault("diagnostics",[]).append("zero_qty_unrepaired")
    return row


def _collect_zero_qty_validation(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out = {"crypto_zero_qty_unrepaired": [], "fx_zero_qty_unrepaired": []}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("row_type") or "trade").lower() != "trade":
            continue
        qty = _as_float(row.get("qty"))
        if qty != 0:
            continue
        diag = row.get("diagnostics") if isinstance(row.get("diagnostics"), list) else []
        entry = {
            "id": row.get("id"),
            "account": row.get("account_label") or row.get("account"),
            "symbol": row.get("symbol"),
            "open_time": row.get("open_time"),
            "close_time": row.get("close_time"),
            "source": row.get("source"),
            "diagnostics": list(diag),
        }
        acct = str(row.get("account") or row.get("account_label") or "").upper()
        sym = str(row.get("symbol") or "").upper()
        is_fx = any(x in acct for x in ("OANDA", "PEPPERSTONE")) or ("/" in sym and len(sym) == 7)
        if is_fx:
            out["fx_zero_qty_unrepaired"].append(entry)
        else:
            out["crypto_zero_qty_unrepaired"].append(entry)
    return out


def _canonicalize_and_dedupe_balances(balances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _source_rank(value: Any) -> int:
        src = str(value or "").strip().lower()
        if src == "cashflow_anchor_plus_trades":
            return 300
        if "broker" in src or "account_summary" in src or "wallet_balance_anchor" in src:
            return 200
        if src in {"authoritative_trade_balance", "trade_timeline", "master_journal"}:
            return 100
        if src == "timeline_missing":
            return 0
        return 50
    def _asof_rank(value: Any) -> float:
        dt = _as_datetime(value)
        return dt.timestamp() if dt else float("-inf")
    def _pick(prev: Dict[str, Any], now: Dict[str, Any]) -> Dict[str, Any]:
        prev_bal = _as_float(prev.get("balance"))
        now_bal = _as_float(now.get("balance"))
        if prev_bal is None and now_bal is not None:
            return now
        if now_bal is None and prev_bal is not None:
            return prev
        prev_score = (_source_rank(prev.get("balance_source") or prev.get("source")), _asof_rank(prev.get("as_of") or prev.get("updated_at")))
        now_score = (_source_rank(now.get("balance_source") or now.get("source")), _asof_rank(now.get("as_of") or now.get("updated_at")))
        return now if now_score >= prev_score else prev

    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in balances or []:
        if not isinstance(rec, dict):
            continue
        label = _canonical_account_label(rec.get("account_label") or rec.get("account") or rec.get("label"))
        if not label:
            continue
        key = label.upper()
        payload = dict(rec)
        payload["account_label"] = label
        payload["account"] = label
        if key not in merged:
            order.append(key)
            merged[key] = payload
            continue
        merged[key] = _pick(merged[key], payload)
    return [merged[k] for k in order]

def _currency_code(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().upper()
        if text:
            return text
    return "UNKNOWN"

def _symbol_quote_currency(symbol: Any) -> str:
    token = str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if not token:
        return ""
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "AUD"):
        if token.endswith(quote) and len(token) > len(quote):
            return quote
    return ""

def _infer_trade_log_currency(row: Dict[str, Any], *, field: str) -> str:
    row_type = str(row.get("row_type") or "").strip().lower()
    symbol = str(row.get("symbol") or "").strip().upper()
    if row_type == "monthly_aud_reval" or symbol == "MONTHLY AUD P/L":
        return "AUD"
    explicit_fields = {
        "commission": ("commission_currency", "fee_currency", "currency", "account_currency"),
        "net_pnl": ("realized_pnl_currency", "result_currency", "currency", "account_currency"),
        "balance_after": ("balance_after_trade_currency", "account_currency", "currency", "result_currency"),
    }.get(field, ())
    explicit = _currency_code(*(row.get(k) for k in explicit_fields))
    if explicit != "UNKNOWN":
        return explicit
    account_fingerprint = " ".join(
        str(row.get(k) or "").upper() for k in ("account", "account_label", "source")
    )
    if any(tok in account_fingerprint for tok in ("OANDA", "PEPPERSTONE", "FOREX", " FX")):
        return "AUD"
    normalized_symbol = symbol.replace("/", "").replace("-", "").replace("_", "")
    if _is_likely_fx_pair(normalized_symbol):
        return "AUD"
    is_crypto_account = any(tok in account_fingerprint for tok in ("BYBIT", "BINANCE", "COINSPOT"))
    is_crypto_row = is_crypto_account or str(row.get("asset_class") or "").strip().lower() == "crypto"
    if is_crypto_row:
        quote = _symbol_quote_currency(symbol)
        if quote in {"USDT", "USDC", "USD", "BTC", "ETH"}:
            return quote
    return ""

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
    if rid and not rid.startswith('monthly_aud_reval:'):
        return rid
    if rid and _monthly_aud_reval_row_id_month(rid) and _is_monthly_aud_reval_semantic_row(row):
        return rid
    refs=row.get('raw_refs') if isinstance(row.get('raw_refs'),dict) else {}
    parts=[str(row.get('account_label') or row.get('account') or ''),str(row.get('symbol') or ''),str(row.get('side') or ''),str(row.get('open_time') or ''),str(row.get('close_time') or ''),str(row.get('qty') or row.get('qty_raw') or ''),str(row.get('entry_price') or ''),str(row.get('exit_price') or ''),str(row.get('net_profit') or row.get('result_cash') or ''),str(row.get('source') or ''),str(row.get('source_file') or ''),str(row.get('workbook_name') or ''),str(refs.get('source_file') or ''),str(refs.get('workbook') or ''),str(refs.get('sheet') or ''),str(refs.get('source_row') or ''),str(refs.get('period_month') or '')]
    return 'sig:'+hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:24]




def _all_trades_row_fingerprint_from_map(values: Dict[str, Any]) -> str:
    parts = [str(values.get(k) or '') for k in ['Account','Symbol','Side','Open Time','Close Time','Qty','Entry Price','Exit Price','Net P/L']]
    return 'sig:' + hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:24]



def _trade_log_two_row_header_values_for(headers: List[str]) -> Tuple[List[str], List[str]]:
    row1: List[str] = []
    row2: List[str] = []
    for header in headers:
        if header in MOVE_TO_BREAK_EVEN_HEADERS:
            row1.append("Move to Break-Even" if header == MOVE_TO_BREAK_EVEN_HEADERS[0] else "")
            row2.append(MOVE_TO_SUBHEADERS[MOVE_TO_BREAK_EVEN_HEADERS.index(header)])
        elif header in MOVE_TO_PROFIT_HEADERS:
            row1.append("Move to Profit" if header == MOVE_TO_PROFIT_HEADERS[0] else "")
            row2.append(MOVE_TO_SUBHEADERS[MOVE_TO_PROFIT_HEADERS.index(header)])
        else:
            row1.append(header)
            row2.append("")
    return row1, row2


def _trade_log_two_row_header_values() -> Tuple[List[str], List[str]]:
    return _trade_log_two_row_header_values_for(TRADE_LOG_HEADERS)


def _trade_log_has_two_row_headers_for(ws, headers: List[str]) -> bool:
    row1, row2 = _trade_log_two_row_header_values_for(headers)
    found1 = [str(ws.cell(1, c).value or "").strip() for c in range(1, len(headers) + 1)]
    found2 = [str(ws.cell(2, c).value or "").strip() for c in range(1, len(headers) + 1)]
    expected_vertical_merges = {
        f"{get_column_letter(col)}1:{get_column_letter(col)}2"
        for col, header in enumerate(headers, start=1)
        if header not in MOVE_TO_FIELD_MAP
    }
    merged_cells = getattr(ws, "merged_cells", None)
    if merged_cells is None:
        return found1 == row1 and found2 == row2
    found_merges = {str(merged) for merged in merged_cells.ranges}
    return found1 == row1 and found2 == row2 and expected_vertical_merges.issubset(found_merges)


def _trade_log_has_two_row_headers(ws) -> bool:
    return _trade_log_has_two_row_headers_for(ws, TRADE_LOG_HEADERS)


def _trade_log_has_legacy_duplicate_two_row_headers_for(ws, headers: List[str]) -> bool:
    row1, row2 = _trade_log_two_row_header_values_for(headers)
    duplicate_row2 = [
        header if header not in MOVE_TO_FIELD_MAP else row2[col - 1]
        for col, header in enumerate(headers, start=1)
    ]
    found1 = [str(ws.cell(1, c).value or "").strip() for c in range(1, len(headers) + 1)]
    found2 = [str(ws.cell(2, c).value or "").strip() for c in range(1, len(headers) + 1)]
    return found1 == row1 and found2 == duplicate_row2


def _trade_log_has_legacy_duplicate_two_row_headers(ws) -> bool:
    return _trade_log_has_legacy_duplicate_two_row_headers_for(ws, TRADE_LOG_HEADERS)


def _trade_log_has_v1_grouped_two_row_headers(ws) -> bool:
    return _trade_log_has_two_row_headers_for(ws, TRADE_LOG_HEADERS_V1) or _trade_log_has_legacy_duplicate_two_row_headers_for(ws, TRADE_LOG_HEADERS_V1)


def _trade_log_uses_grouped_two_row_headers(ws) -> bool:
    return _trade_log_has_two_row_headers(ws) or _trade_log_has_legacy_duplicate_two_row_headers(ws) or _trade_log_has_v1_grouped_two_row_headers(ws)


def _trade_log_header_map(ws) -> Dict[str, int]:
    if _trade_log_has_two_row_headers(ws) or _trade_log_has_legacy_duplicate_two_row_headers(ws):
        return {header: col for col, header in enumerate(TRADE_LOG_HEADERS, start=1)}
    if _trade_log_has_v1_grouped_two_row_headers(ws):
        return {header: col for col, header in enumerate(TRADE_LOG_HEADERS_V1, start=1)}
    return {
        str(ws.cell(1, c).value or "").strip(): c
        for c in range(1, ws.max_column + 1)
        if str(ws.cell(1, c).value or "").strip()
    }


def _trade_log_data_start_row(ws) -> int:
    return TRADE_LOG_DATA_START_ROW if _trade_log_uses_grouped_two_row_headers(ws) else 2


def _trade_log_data_row_count(ws) -> int:
    headers = _trade_log_header_map(ws)
    row_id_col = headers.get("Row ID")
    start_row = _trade_log_data_start_row(ws)
    count = 0
    for row in range(start_row, ws.max_row + 1):
        if row_id_col and ws.cell(row, row_id_col).value not in (None, ""):
            count += 1
        elif any(ws.cell(row, col).value not in (None, "") for col in range(1, min(ws.max_column, len(TRADE_LOG_HEADERS)) + 1)):
            count += 1
    return count


def _set_trade_log_auto_filter(ws) -> None:
    last_col = len(TRADE_LOG_HEADERS)
    last_row = TRADE_LOG_FILTER_HEADER_ROW
    for row in range(TRADE_LOG_DATA_START_ROW, ws.max_row + 1):
        if any(ws.cell(row, col).value not in (None, "") for col in range(1, last_col + 1)):
            last_row = row
    ws.auto_filter.ref = f"A{TRADE_LOG_FILTER_HEADER_ROW}:{get_column_letter(last_col)}{last_row}"


def _hide_trade_log_row_id(ws) -> None:
    headers = _trade_log_header_map(ws)
    row_id_col = headers.get("Row ID")
    if not row_id_col:
        raise RuntimeError("Trade Log schema repair failed: missing Row ID header.")
    ws.column_dimensions[get_column_letter(row_id_col)].hidden = True


def _clear_trade_log_dropdown_validations(ws) -> None:
    keep = []
    editable_cols = {_trade_log_header_map(ws).get(h) for h in ["Test", "Pattern", "ATHS/ATLS", "Order", "Round Number", "Spiked Out", "Close Stopout", "Near Perfect Entry", "Near Win", "Early Close"]}
    editable_cols.discard(None)
    for dv in list(ws.data_validations.dataValidation):
        touches_editable = False
        for sq in dv.cells.ranges:
            if any(sq.min_col <= c <= sq.max_col for c in editable_cols):
                touches_editable = True
                break
        if not touches_editable:
            keep.append(dv)
    ws.data_validations.dataValidation = keep


def _apply_trade_log_dropdown_validations(ws) -> None:
    headers = _trade_log_header_map(ws)
    _clear_trade_log_dropdown_validations(ws)
    max_row = max(TRADE_LOG_DATA_START_ROW, ws.max_row)
    specs = {
        "Test": '"Yes,No"',
        "ATHS/ATLS": '"All-Time High,All-Time Low"',
        "Order": '"Market,Limit"',
        "Round Number": '"Yes,No"',
        "Spiked Out": '"Yes,No"',
        "Pattern": '"range,channel"',
        "Close Stopout": '"Yes,No"',
        "Near Perfect Entry": '"Yes,No"',
        "Near Win": '"Yes,No"',
        "Early Close": '"Yes,No"',
    }
    for header, formula in specs.items():
        col = headers.get(header)
        if not col:
            continue
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        ws.add_data_validation(dv)
        letter = get_column_letter(col)
        dv.add(f"{letter}{TRADE_LOG_DATA_START_ROW}:{letter}{max_row}")


def _copy_cell_style(src, dst) -> None:
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.alignment = copy(src.alignment)
    dst.number_format = src.number_format
    dst.protection = copy(src.protection)


def _snapshot_cell(cell) -> Dict[str, Any]:
    return {
        "value": cell.value,
        "style": copy(cell._style),
        "comment": copy(cell.comment),
        "hyperlink": copy(cell.hyperlink),
    }


def _restore_cell_snapshot(cell, snapshot: Dict[str, Any], *, value: Any = None, use_snapshot_value: bool = True) -> None:
    cell.value = snapshot["value"] if use_snapshot_value else value
    cell._style = copy(snapshot["style"])
    cell.comment = copy(snapshot["comment"])
    cell.hyperlink = copy(snapshot["hyperlink"])


def _write_trade_log_two_row_headers(ws, header_templates: Dict[str, Dict[str, Any]]) -> None:
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row <= 2 and merged.max_row >= 1:
            ws.unmerge_cells(str(merged))
    row1, row2 = _trade_log_two_row_header_values()
    for col, logical_header in enumerate(TRADE_LOG_HEADERS, start=1):
        template = header_templates.get(logical_header) or next(iter(header_templates.values()))
        _restore_cell_snapshot(ws.cell(1, col), template, value=row1[col - 1], use_snapshot_value=False)
        _restore_cell_snapshot(ws.cell(2, col), template, value=row2[col - 1], use_snapshot_value=False)
    for col, logical_header in enumerate(TRADE_LOG_HEADERS, start=1):
        if logical_header not in MOVE_TO_FIELD_MAP:
            ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
            anchor = ws.cell(1, col)
            anchor.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True,
                text_rotation=anchor.alignment.text_rotation,
                shrink_to_fit=anchor.alignment.shrink_to_fit,
                indent=anchor.alignment.indent,
            )
    for label, group_headers in (
        ("Move to Break-Even", MOVE_TO_BREAK_EVEN_HEADERS),
        ("Move to Profit", MOVE_TO_PROFIT_HEADERS),
    ):
        group_cols = [
            col for col, logical_header in enumerate(TRADE_LOG_HEADERS, start=1)
            if logical_header in group_headers
        ]
        if not group_cols:
            continue
        first_col, last_col = min(group_cols), max(group_cols)
        ws.merge_cells(start_row=1, start_column=first_col, end_row=1, end_column=last_col)
        ws.cell(1, first_col).value = label
        for col in group_cols:
            ws.cell(1, col).alignment = copy(ws.cell(2, col).alignment)
            ws.cell(1, col).alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True,
                text_rotation=ws.cell(1, col).alignment.text_rotation,
                shrink_to_fit=ws.cell(1, col).alignment.shrink_to_fit,
                indent=ws.cell(1, col).alignment.indent,
            )


def _ensure_trade_log_schema(ws, diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    before_data_rows = _trade_log_data_row_count(ws)
    already_current = _trade_log_has_two_row_headers(ws)
    legacy_duplicate_headers = _trade_log_has_legacy_duplicate_two_row_headers(ws)
    legacy_v1_grouped_headers = _trade_log_has_two_row_headers_for(ws, TRADE_LOG_HEADERS_V1)
    legacy_v1_duplicate_headers = _trade_log_has_legacy_duplicate_two_row_headers_for(ws, TRADE_LOG_HEADERS_V1)
    if already_current:
        headers = _trade_log_header_map(ws)
        trade_number_col = headers.get(TRADE_NUMBER_HEADER)
        if trade_number_col:
            for row in range(TRADE_LOG_DATA_START_ROW, ws.max_row + 1):
                ws.cell(row, trade_number_col).number_format = "@"
        for header in ("Move to Break Even Duration", "Move to Profit Duration"):
            for row in range(TRADE_LOG_DATA_START_ROW, ws.max_row + 1):
                ws.cell(row, headers[header]).number_format = r'00\:00\:00\:00'
        for header in (
            "Move to Break Even Distance From Entry %", "Move to Break Even Distance From Exit %",
            "Move to Profit Distance From Entry %", "Move to Profit Distance From Exit %",
        ):
            for row in range(TRADE_LOG_DATA_START_ROW, ws.max_row + 1):
                ws.cell(row, headers[header]).number_format = "0.00%"
        ws.freeze_panes = "A3"
        _hide_trade_log_row_id(ws)
        _set_trade_log_auto_filter(ws)
        _apply_trade_log_dropdown_validations(ws)
        _apply_trade_log_win_loss_row_formatting(ws)
        _apply_trade_log_win_loss_direct_row_fills(ws)
        if _trade_log_data_row_count(ws) != before_data_rows:
            raise RuntimeError("Trade Log schema validation changed the data row count unexpectedly.")
        return
    else:
        if legacy_duplicate_headers:
            source_headers = list(TRADE_LOG_HEADERS)
            source_start_row = TRADE_LOG_DATA_START_ROW
        elif legacy_v1_grouped_headers or legacy_v1_duplicate_headers:
            source_headers = list(TRADE_LOG_HEADERS_V1)
            source_start_row = TRADE_LOG_DATA_START_ROW
        else:
            source_headers = [str(ws.cell(1, col).value or "").strip() for col in range(1, ws.max_column + 1)]
            while source_headers and not source_headers[-1]:
                source_headers.pop()
        if source_headers not in (PRE_MOVE_TRADE_LOG_HEADERS, OLD_TRADE_LOG_HEADERS, TRADE_LOG_HEADERS_V1, TRADE_LOG_HEADERS):
            raise RuntimeError(
                "Trade Log headers cannot be migrated safely: "
                f"found {source_headers!r}; expected current two-row headers or one of "
                f"{[PRE_MOVE_TRADE_LOG_HEADERS, OLD_TRADE_LOG_HEADERS, TRADE_LOG_HEADERS_V1, TRADE_LOG_HEADERS]!r}."
            )
        if not (legacy_duplicate_headers or legacy_v1_grouped_headers or legacy_v1_duplicate_headers):
            source_start_row = 2

    source_by_header = {header: idx for idx, header in enumerate(source_headers, start=1)}
    if len(source_by_header) != len(source_headers):
        raise RuntimeError(f"Trade Log headers cannot be migrated safely because duplicate logical headers were found: {source_headers!r}.")

    source_data_rows: List[Dict[str, Dict[str, Any]]] = []
    source_row_heights: List[float | None] = []
    for row in range(source_start_row, ws.max_row + 1):
        if not any(ws.cell(row, col).value not in (None, "") for col in range(1, len(source_headers) + 1)):
            continue
        row_snapshot: Dict[str, Dict[str, Any]] = {}
        for header, col in source_by_header.items():
            row_snapshot[header] = _snapshot_cell(ws.cell(row, col))
        source_data_rows.append(row_snapshot)
        source_row_heights.append(ws.row_dimensions[row].height)

    header_templates: Dict[str, Dict[str, Any]] = {}
    source_header_row = 2 if already_current else 1
    for header in TRADE_LOG_HEADERS:
        source_header = header
        if header == "Close Stopout" and source_header not in source_by_header:
            source_header = "Stop Out"
        template_header = source_header if source_header in source_by_header else ("Open Time" if TRADE_NUMBER_HEADER not in source_by_header else "Test")
        if "Trigger Price" in header:
            template_header = "Entry Price"
        elif "Distance From" in header:
            template_header = "Stop Loss Distance"
        elif header.endswith("Duration"):
            template_header = "Trade Duration (DD:HH:MM:SS)"
        header_templates[header] = _snapshot_cell(ws.cell(source_header_row, source_by_header[template_header]))

    source_dimensions = {
        header: copy(ws.column_dimensions[get_column_letter(col)])
        for header, col in source_by_header.items()
    }
    max_old_row = ws.max_row
    max_old_col = ws.max_column
    for row in range(1, max(max_old_row, TRADE_LOG_DATA_START_ROW + len(source_data_rows) - 1) + 1):
        for col in range(1, max(max_old_col, len(TRADE_LOG_HEADERS)) + 1):
            if _is_merged_non_anchor(ws, row, col):
                continue
            cell = ws.cell(row, col)
            cell.value = None
            cell.comment = None
            cell.hyperlink = None

    _write_trade_log_two_row_headers(ws, header_templates)
    for target_col, header in enumerate(TRADE_LOG_HEADERS, start=1):
        source_header = header if header in source_by_header else ("Stop Out" if header == "Close Stopout" and "Stop Out" in source_by_header else None)
        template_header = source_header or ("Open Time" if TRADE_NUMBER_HEADER not in source_by_header else "Test")
        if "Trigger Price" in header:
            template_header = "Entry Price"
        elif "Distance From" in header:
            template_header = "Stop Loss Distance"
        elif header.endswith("Duration"):
            template_header = "Trade Duration (DD:HH:MM:SS)"
        dimension = source_dimensions.get(source_header or "") or source_dimensions.get(template_header)
        letter = get_column_letter(target_col)
        ws.column_dimensions[letter].width = dimension.width if dimension and dimension.width else 14
        ws.column_dimensions[letter].hidden = bool(dimension.hidden) if dimension else False
        for offset, row_snapshot in enumerate(source_data_rows):
            target_row = TRADE_LOG_DATA_START_ROW + offset
            snapshot = row_snapshot.get(source_header) if source_header else None
            if snapshot:
                _restore_cell_snapshot(ws.cell(target_row, target_col), snapshot)
            else:
                template_snapshot = row_snapshot.get(template_header)
                if template_snapshot:
                    _restore_cell_snapshot(ws.cell(target_row, target_col), template_snapshot, value=None, use_snapshot_value=False)
        if header in ("Move to Break Even Duration", "Move to Profit Duration"):
            for row in range(TRADE_LOG_DATA_START_ROW, TRADE_LOG_DATA_START_ROW + len(source_data_rows)):
                ws.cell(row, target_col).number_format = r'00\:00\:00\:00'
        elif header == TRADE_NUMBER_HEADER:
            for row in range(TRADE_LOG_DATA_START_ROW, TRADE_LOG_DATA_START_ROW + len(source_data_rows)):
                ws.cell(row, target_col).number_format = "@"
        elif "Distance From" in header:
            for row in range(TRADE_LOG_DATA_START_ROW, TRADE_LOG_DATA_START_ROW + len(source_data_rows)):
                ws.cell(row, target_col).number_format = "0.00%"

    for offset, height in enumerate(source_row_heights):
        ws.row_dimensions[TRADE_LOG_DATA_START_ROW + offset].height = height
    ws.row_dimensions[1].height = ws.row_dimensions[1].height or 24
    ws.row_dimensions[2].height = ws.row_dimensions[2].height or 24
    ws.freeze_panes = "A3"
    _hide_trade_log_row_id(ws)
    _set_trade_log_auto_filter(ws)
    _apply_trade_log_dropdown_validations(ws)
    _apply_trade_log_win_loss_row_formatting(ws)
    _apply_trade_log_win_loss_direct_row_fills(ws)

    after_data_rows = _trade_log_data_row_count(ws)
    if after_data_rows != before_data_rows:
        raise RuntimeError(
            "Trade Log schema migration aborted because data row count changed: "
            f"before={before_data_rows}, after={after_data_rows}."
        )
    if before_data_rows and not after_data_rows:
        raise RuntimeError("Trade Log schema migration aborted because it would blank the Trade Log.")
    if not already_current:
        diagnostics["migrated_trade_log_schema"] = True
        diagnostics["migrated_trade_log_from_headers"] = source_headers

def _conditional_formatting_formula_text(rule) -> str:
    formula = getattr(rule, "formula", None) or []
    if isinstance(formula, (list, tuple)):
        return " ".join(str(part or "") for part in formula)
    return str(formula or "")

def _remove_trade_log_win_loss_row_formatting(ws) -> None:
    """Remove generated Trade Log row-level win/loss CF, including stale schemas."""
    cf = ws.conditional_formatting
    stale_refs = []
    for key, rules in list(getattr(cf, "_cf_rules", {}).items()):
        sqref = str(getattr(key, "sqref", key))
        rule_text = " ".join(_conditional_formatting_formula_text(rule) for rule in rules)
        is_generated_row_rule = (
            sqref.startswith(("A2:", "A3:"))
            and '"trade"' in rule_text
            and ("AND(" in rule_text.upper())
            and (">0" in rule_text or "<0" in rule_text)
        )
        is_stale_old_schema = sqref.startswith(("A2:AB", "A3:AB")) or "$AA" in rule_text
        if is_generated_row_rule or is_stale_old_schema:
            stale_refs.append(sqref)
    for sqref in stale_refs:
        del cf[sqref]

def _is_generated_trade_log_value_fill_rule(rule) -> bool:
    formula_text = _conditional_formatting_formula_text(rule).strip()
    return (
        getattr(rule, "type", None) == "cellIs"
        and getattr(rule, "operator", None) in {"greaterThan", "lessThan", "notEqual"}
        and formula_text == "0"
    )

def _range_is_trade_log_generated_value_fill_range(range_ref: str) -> bool:
    try:
        min_col, min_row, max_col, _max_row = range_boundaries(range_ref)
    except ValueError:
        return False
    return min_row >= 2 and 13 <= min_col <= max_col <= 17

def _remove_trade_log_generated_value_fill_formatting(ws) -> None:
    cf = ws.conditional_formatting
    refs_to_remove = []
    for key, rules in list(getattr(cf, "_cf_rules", {}).items()):
        sqref = str(getattr(key, "sqref", key))
        sqref_parts = sqref.split()
        if (
            sqref_parts
            and all(_range_is_trade_log_generated_value_fill_range(part) for part in sqref_parts)
            and all(_is_generated_trade_log_value_fill_rule(rule) for rule in rules)
        ):
            refs_to_remove.append(sqref)
    for sqref in refs_to_remove:
        del cf[sqref]

def _cell_fill_rgb(cell) -> str:
    color = getattr(getattr(cell, "fill", None), "fgColor", None)
    rgb = str(getattr(color, "rgb", "") or "")
    return rgb[-6:].upper() if rgb else ""

def _cell_has_generated_trade_log_win_loss_fill(cell) -> bool:
    return getattr(cell.fill, "fill_type", None) == "solid" and _cell_fill_rgb(cell) in {PROFIT_FILL, LOSS_FILL}

def _apply_trade_log_win_loss_direct_row_fills(ws) -> None:
    headers = _trade_log_header_map(ws)
    row_type_col = headers.get("Row Type")
    net_pl_col = headers.get("Net P/L")
    if not row_type_col or not net_pl_col:
        return
    last_col = max((col for header, col in headers.items() if header), default=ws.max_column)
    profit_fill = PatternFill("solid", fgColor=PROFIT_FILL)
    loss_fill = PatternFill("solid", fgColor=LOSS_FILL)
    for row in range(_trade_log_data_start_row(ws), ws.max_row + 1):
        row_type = str(ws.cell(row, row_type_col).value or "").strip().lower()
        net_pl = _as_float(ws.cell(row, net_pl_col).value)
        fill = None
        if row_type == "trade" and net_pl is not None:
            if net_pl > 0:
                fill = profit_fill
            elif net_pl < 0:
                fill = loss_fill
        for col in range(1, last_col + 1):
            cell = ws.cell(row, col)
            if fill is not None:
                cell.fill = copy(fill)
            elif _cell_has_generated_trade_log_win_loss_fill(cell):
                cell.fill = PatternFill()

def _apply_trade_log_win_loss_row_formatting(ws) -> None:
    headers = _trade_log_header_map(ws)
    row_type_col = headers.get("Row Type")
    net_pl_col = headers.get("Net P/L")
    if not row_type_col or not net_pl_col:
        return
    _remove_trade_log_win_loss_row_formatting(ws)
    _remove_trade_log_generated_value_fill_formatting(ws)
    last_col = max((col for header, col in headers.items() if header), default=ws.max_column)
    start_row = _trade_log_data_start_row(ws)
    last_row = max(start_row, ws.max_row)
    row_type_letter = get_column_letter(row_type_col)
    net_pl_letter = get_column_letter(net_pl_col)
    cell_range = f"A{start_row}:{get_column_letter(last_col)}{last_row}"
    profit_fill = PatternFill("solid", fgColor=PROFIT_FILL)
    loss_fill = PatternFill("solid", fgColor=LOSS_FILL)
    ws.conditional_formatting.add(
        cell_range,
        FormulaRule(formula=[f'AND(${row_type_letter}{start_row}="trade",${net_pl_letter}{start_row}>0)'], fill=profit_fill, stopIfTrue=True),
    )
    ws.conditional_formatting.add(
        cell_range,
        FormulaRule(formula=[f'AND(${row_type_letter}{start_row}="trade",${net_pl_letter}{start_row}<0)'], fill=loss_fill, stopIfTrue=True),
    )

def _is_generated_profit_loss_rule(rule) -> bool:
    formula = getattr(rule, "formula", None) or []
    formula_text = " ".join(str(part or "") for part in formula)
    return (
        getattr(rule, "type", None) == "cellIs"
        and getattr(rule, "operator", None) in {"greaterThan", "lessThan"}
        and formula_text.strip() == "0"
    )

def _pnl_calendar_profit_loss_ranges(ws) -> List[str]:
    month_cols = _detect_calendar_month_columns(ws)
    if month_cols:
        first_col = min(month_cols.values())
        last_col = max(month_cols.values())
        ranges = []
        for row in range(2, ws.max_row + 1):
            label = str(ws.cell(row, 2).value or "").strip().lower()
            if label == "p/l %":
                ranges.append(f"{get_column_letter(first_col)}{row}:{get_column_letter(last_col)}{row}")
        return ranges

    month_names = {calendar.month_name[i].lower() for i in range(1, 13)}
    pnl_cols = []
    for col in range(2, ws.max_column + 1):
        header = str(ws.cell(1, col).value or "").strip().lower()
        subheader = str(ws.cell(2, col).value or "").strip().lower()
        if header.endswith(" p/l %") or subheader in month_names:
            pnl_cols.append(col)
    if not pnl_cols:
        return []
    first_col = min(pnl_cols)
    last_col = max(pnl_cols)
    ranges = []
    for row in range(3, ws.max_row + 1, 2):
        year_value = _as_float(ws.cell(row, 1).value)
        if year_value is not None:
            ranges.append(f"{get_column_letter(first_col)}{row}:{get_column_letter(last_col)}{row}")
    return ranges

def _remove_pnl_calendar_generated_profit_loss_formatting(ws) -> None:
    ranges = set(_pnl_calendar_profit_loss_ranges(ws))
    if not ranges:
        return
    refs_to_remove = []
    for key, rules in list(getattr(ws.conditional_formatting, "_cf_rules", {}).items()):
        sqref = str(getattr(key, "sqref", key))
        sqref_parts = sqref.split()
        if sqref_parts and all(part in ranges for part in sqref_parts) and all(_is_generated_profit_loss_rule(rule) for rule in rules):
            refs_to_remove.append(sqref)
    for sqref in refs_to_remove:
        del ws.conditional_formatting[sqref]

def _apply_pnl_calendar_profit_loss_formatting(ws) -> None:
    _remove_pnl_calendar_generated_profit_loss_formatting(ws)
    for cell_range in _pnl_calendar_profit_loss_ranges(ws):
        _profit_loss_rules(ws, cell_range)
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                _apply_sign_based_full_cell_fill(ws.cell(row, col))

    # Total Trades rows are deliberately neutral, including when repairing stale direct fills.
    for row in range(1, ws.max_row + 1):
        if str(ws.cell(row, 2).value or "").strip().lower() == "total trades":
            for col in range(3, ws.max_column + 1):
                _clear_generated_semantic_fill(ws.cell(row, col))

def _apply_instrument_averages_semantic_fills(ws) -> None:
    headers = {str(ws.cell(1, col).value or "").strip().lower(): col for col in range(1, ws.max_column + 1)}
    profit_headers = {"wins", "long wins", "short wins"}
    loss_headers = {"losses", "long losses", "short losses"}
    signed_headers = {"net p/l %", "avg p/l %"}
    symbol_col = headers.get("symbol", 1)
    for row in range(2, ws.max_row + 1):
        if ws.cell(row, symbol_col).value in (None, ""):
            continue
        for header in profit_headers:
            col = headers.get(header)
            if col:
                _apply_full_cell_semantic_fill(ws.cell(row, col), "profit")
        for header in loss_headers:
            col = headers.get(header)
            if col:
                _apply_full_cell_semantic_fill(ws.cell(row, col), "loss")
        for header in signed_headers:
            col = headers.get(header)
            if col:
                _apply_sign_based_full_cell_fill(ws.cell(row, col))

def _apply_dashboard_source_label_style(cell) -> None:
    font = copy(cell.font)
    font.bold = False
    font.italic = True
    cell.font = font
    alignment = copy(cell.alignment)
    alignment.horizontal = "right"
    cell.alignment = alignment

def _is_generated_dashboard_semantic_rule(rule) -> bool:
    if getattr(rule, "type", None) != "cellIs":
        return False
    if getattr(rule, "operator", None) not in {"greaterThan", "lessThan", "notEqual"}:
        return False
    if [str(value) for value in (getattr(rule, "formula", None) or [])] != ["0"]:
        return False
    dxf = getattr(rule, "dxf", None)
    fill = getattr(getattr(dxf, "fill", None), "fgColor", None)
    font = getattr(getattr(dxf, "font", None), "color", None)
    fill_rgb = str(getattr(fill, "rgb", "") or "")[-6:].upper()
    font_rgb = str(getattr(font, "rgb", "") or "")[-6:].upper()
    return (fill_rgb, font_rgb) in {(PROFIT_FILL, PROFIT_FONT), (LOSS_FILL, LOSS_FONT)}

def _subtract_range_rectangle(cell_range: str, cut_range: str) -> List[str]:
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    cut_min_col, cut_min_row, cut_max_col, cut_max_row = range_boundaries(cut_range)
    inter_min_col = max(min_col, cut_min_col)
    inter_min_row = max(min_row, cut_min_row)
    inter_max_col = min(max_col, cut_max_col)
    inter_max_row = min(max_row, cut_max_row)
    if inter_min_col > inter_max_col or inter_min_row > inter_max_row:
        return [cell_range]
    rectangles = []
    if min_row < inter_min_row:
        rectangles.append((min_col, min_row, max_col, inter_min_row - 1))
    if inter_max_row < max_row:
        rectangles.append((min_col, inter_max_row + 1, max_col, max_row))
    if min_col < inter_min_col:
        rectangles.append((min_col, inter_min_row, inter_min_col - 1, inter_max_row))
    if inter_max_col < max_col:
        rectangles.append((inter_max_col + 1, inter_min_row, max_col, inter_max_row))
    return [
        f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}"
        for left, top, right, bottom in rectangles
    ]

def _sanitize_dashboard_semantic_conditional_formatting(ws) -> None:
    protected_ranges = ("B3:D4", "B11:D12", "C10:D12", "C21:D28")
    sanitized = OrderedDict()

    def add_rules(key, rules) -> None:
        if not rules:
            return
        if key in sanitized:
            sanitized[key].extend(rules)
        else:
            sanitized[key] = list(rules)

    for key, rules in list(getattr(ws.conditional_formatting, "_cf_rules", {}).items()):
        generated = [rule for rule in rules if _is_generated_dashboard_semantic_rule(rule)]
        manual = [rule for rule in rules if not _is_generated_dashboard_semantic_rule(rule)]
        if manual:
            add_rules(copy(key), manual)
        if not generated:
            continue
        remaining_parts: List[str] = []
        for part in str(key.sqref).split():
            pieces = [part]
            for protected in protected_ranges:
                pieces = [piece for current in pieces for piece in _subtract_range_rectangle(current, protected)]
            remaining_parts.extend(pieces)
        if remaining_parts:
            generated_key = copy(key)
            generated_key.sqref = " ".join(remaining_parts)
            add_rules(generated_key, generated)
    ws.conditional_formatting._cf_rules = sanitized

def _apply_dashboard_requested_semantic_fills(ws) -> None:
    _sanitize_dashboard_semantic_conditional_formatting(ws)
    for coordinate in ("B3", "C3", "D3", "B11", "C11", "D11"):
        _apply_full_cell_semantic_fill(ws[coordinate], "profit")
    for coordinate in ("B4", "C4", "D4", "B12", "C12", "D12"):
        _apply_full_cell_semantic_fill(ws[coordinate], "loss")

    semantic_by_label = {
        "max win %": "profit",
        "max loss %": "loss",
        "max r loss": "loss",
        "max r win": "profit",
    }
    for row in range(1, ws.max_row + 1):
        label = str(ws.cell(row, 1).value or "").strip().lower()
        if label == "source":
            _apply_dashboard_source_label_style(ws.cell(row, 1))
        semantic = semantic_by_label.get(label)
        if semantic:
            for col in (3, 4):
                _apply_full_cell_semantic_fill(ws.cell(row, col), semantic)

    for row in range(13, 17):
        _apply_full_cell_semantic_fill(ws.cell(row, 8), "profit")
        _apply_full_cell_semantic_fill(ws.cell(row, 9), "loss")

def read_master_journal_manual_overrides(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    wb=load_workbook(path, data_only=True)
    try:
        try:
            ws=_get_all_trades_sheet(wb)
        except RuntimeError:
            return out
        header_map = _trade_log_header_map(ws)
        idx = {header: col - 1 for header, col in header_map.items()}
        data_start_row = _trade_log_data_start_row(ws)
        rid_by_row={}
        if '_Trade Meta' in wb.sheetnames:
            meta=wb['_Trade Meta']
            rid_by_row={int(r[0]):str(r[1] or '').strip() for r in meta.iter_rows(min_row=2,values_only=True) if r and r[0] and r[1]}
        for row_num,r in enumerate(ws.iter_rows(min_row=data_start_row, values_only=True), start=data_start_row):
            comment_rid = ""
            cmt = ws.cell(row_num, 1).comment
            if cmt and isinstance(cmt.text, str) and cmt.text.startswith("row_id:"):
                comment_rid = cmt.text.split("row_id:", 1)[1].strip()
            meta_rid = rid_by_row.get(row_num,'')
            rowid_i = idx.get('Row ID')
            inline_rid = str(r[rowid_i] or '').strip() if rowid_i is not None and rowid_i < len(r) else ''
            rid = comment_rid or inline_rid or meta_rid
            if not rid:
                row_map = {h: (r[i] if i < len(r) else None) for h, i in idx.items()}
                rid = _all_trades_row_fingerprint_from_map(row_map)
            edits={}
            test_i=idx.get('Test')
            if test_i is not None:
                t=str(r[test_i] or '').strip().lower()
                edits['is_test_trade']=t in {'yes','true','1'}
            for col,field in [('Setup','setup'),('Timeframe','timeframe'),('Breakeven','breakeven'),('Notes','notes'), *TRADE_LOG_MANUAL_FIELD_MAP.items()]:
                i=idx.get(col)
                if i is None:
                    continue
                raw_value = r[i]
                if field in {"move_to_break_even_duration", "move_to_profit_duration"} and raw_value not in (None, ""):
                    number_format = str(ws.cell(row_num, i + 1).number_format or "")
                    parsed_duration = _duration_ddhhmmss_cell_to_seconds(raw_value) if r"\:" in number_format else _parse_duration_text(raw_value)
                    edits[field] = parsed_duration if parsed_duration is not None else raw_value
                elif field in MOVE_TO_FIELD_MAP.values():
                    edits[field] = raw_value
                else:
                    edits[field] = '' if raw_value is None else str(raw_value)
            if 'close_stopout' not in edits and 'Stop Out' in idx:
                i = idx['Stop Out']
                edits['close_stopout'] = '' if r[i] is None else str(r[i])
            out[rid]=edits
    finally:
        wb.close()
    return out


def _trade_row_market(row: Dict[str, Any]) -> str | None:
    asset_class = str(row.get("asset_class") or row.get("class") or "").strip().lower()
    if asset_class in {"fx", "forex"}:
        return "fx"
    if asset_class == "crypto":
        return "crypto"
    account = str(row.get("account_label") or row.get("account") or "").strip().lower()
    if any(token in account for token in ("bybit", "binance", "coinspot", "crypto")):
        return "crypto"
    if any(token in account for token in ("oanda", "pepperstone", "forex", "fx")):
        return "fx"
    if _is_likely_fx_pair(str(row.get("symbol") or "")):
        return "fx"
    return None


def _move_duration_seconds(row: Dict[str, Any], prefix: str) -> float | None:
    duration = _parse_duration_text(row.get(f"{prefix}_duration"))
    if duration is not None and duration >= 0:
        return duration
    move_time = _as_datetime(row.get(f"{prefix}_time"))
    open_time = _as_datetime(row.get("open_time"))
    if move_time is None or open_time is None:
        return None
    seconds = (move_time - open_time).total_seconds()
    return seconds if seconds >= 0 else None


def _trade_move_duration_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float | None]]:
    values: Dict[str, Dict[str, List[float]]] = {
        market: {"move_to_break_even_duration_seconds": [], "move_to_profit_duration_seconds": []}
        for market in ("overall", "fx", "crypto")
    }
    for row in rows:
        if str(row.get("row_type") or "trade") != "trade" or _is_test_trade_value(row.get("is_test_trade")):
            continue
        markets = ["overall"]
        market = _trade_row_market(row)
        if market:
            markets.append(market)
        for prefix, key in (
            ("move_to_break_even", "move_to_break_even_duration_seconds"),
            ("move_to_profit", "move_to_profit_duration_seconds"),
        ):
            seconds = _move_duration_seconds(row, prefix)
            if seconds is None:
                continue
            for market_name in markets:
                values[market_name][key].append(seconds)
    return {
        market: {key: (sum(samples) / len(samples) if samples else None) for key, samples in metrics.items()}
        for market, metrics in values.items()
    }


def _result_percentage_totals_by_market(
    rows: List[Dict[str, Any]], balances: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Calculate currency-safe market returns from current capital and money P/L."""
    trade_money: Dict[str, Dict[str, List[float]]] = {
        market: defaultdict(list) for market in ("overall", "fx", "crypto")
    }
    for row in rows:
        if str(row.get("row_type") or "trade") != "trade" or _is_test_trade_value(row.get("is_test_trade")):
            continue
        pnl = _as_float(row.get("net_profit") if row.get("net_profit") is not None else row.get("result_cash"))
        if pnl is None:
            continue
        currency = _infer_trade_log_currency(row, field="net_pnl") or _currency_code(
            row.get("realized_pnl_currency"), row.get("currency"), row.get("account_currency")
        )
        markets = ["overall"]
        market = _trade_row_market(row)
        if market in {"fx", "crypto"}:
            markets.append(market)
        for market_name in markets:
            trade_money[market_name][currency].append(pnl)

    balance_money: Dict[str, Dict[str, float]] = {
        market: defaultdict(float) for market in ("overall", "fx", "crypto")
    }
    for balance in _canonicalize_and_dedupe_balances(balances or []):
        current = _as_float(balance.get("balance"))
        if current is None:
            continue
        label = balance.get("account_label") or balance.get("account") or balance.get("label")
        currency = _currency_code(balance.get("currency"), balance.get("account_currency"))
        balance_money["overall"][currency] += current
        market = _trade_row_market({"account": label})
        if market in {"fx", "crypto"}:
            balance_money[market][currency] += current

    result: Dict[str, Dict[str, Any]] = {}
    for market in ("overall", "fx", "crypto"):
        currencies = sorted(set(trade_money[market]) | set(balance_money[market]))
        payload: Dict[str, Any] = {
            "market_return_pct": None,
            "gross_gain_return_pct": None,
            "gross_loss_return_pct": None,
            "return_currency": currencies[0] if len(currencies) == 1 else None,
            "return_unavailable_reason": None,
        }
        if len(currencies) != 1:
            payload["return_unavailable_reason"] = "mixed_currency" if len(currencies) > 1 else "missing_currency"
            result[market] = payload
            continue
        currency = currencies[0]
        if currency not in balance_money[market]:
            payload["return_unavailable_reason"] = "missing_current_balance"
            result[market] = payload
            continue
        pnl_values = trade_money[market].get(currency, [])
        net_pnl = sum(pnl_values)
        current_balance = balance_money[market][currency]
        funded_capital = current_balance - net_pnl
        if not math.isfinite(funded_capital) or funded_capital <= 0:
            payload["return_unavailable_reason"] = "invalid_funded_capital"
            result[market] = payload
            continue
        gross_gain = sum(value for value in pnl_values if value > 0)
        gross_loss = abs(sum(value for value in pnl_values if value < 0))
        payload.update({
            "market_return_pct": net_pnl / funded_capital * 100.0,
            "gross_gain_return_pct": gross_gain / funded_capital * 100.0,
            "gross_loss_return_pct": gross_loss / funded_capital * 100.0,
            "starting_or_funded_capital": funded_capital,
        })
        result[market] = payload
    return result

def build_master_journal_workbook(snapshot: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    wb=Workbook(); wb.remove(wb.active)
    for s in SHEET_ORDER: wb.create_sheet(s)
    rows=[_repair_or_flag_zero_trade_qty(dict(r)) for r in (snapshot.get('items') or []) if isinstance(r,dict) and str(r.get('row_type') or 'trade') in {'trade','monthly_aud_reval','cashflow'}]
    metric_rows=[r for r in rows if str(r.get('row_type') or 'trade')=='trade']
    non_test=[r for r in metric_rows if not _is_test_trade_value(r.get('is_test_trade'))]
    stats = snapshot.get('stats') or {}
    totals = stats.get('totals') or {}
    groups = stats.get('groups') or {}

    dash = wb["Dashboard"]
    for col, width in (("A", 32), ("B", 18), ("C", 18), ("D", 18), ("E", 3), ("F", 24), ("G", 20), ("H", 12), ("I", 12), ("J", 12)):
        dash.column_dimensions[col].width = width

    by_market = groups.get("by_market") or {}
    risk = groups.get("risk_expectancy") or {}
    duration = groups.get("duration") or {}
    leaders = groups.get("leaders") or {}
    move_duration_metrics = _trade_move_duration_metrics(metric_rows)
    percentage_totals = _result_percentage_totals_by_market(metric_rows, snapshot.get("balances") or stats.get("balances") or [])
    buckets = {
        market: {**percentage_totals[market], **dict(bucket or {})}
        for market, bucket in {
            "overall": by_market.get("overall") or totals,
            "fx": by_market.get("fx") or {},
            "crypto": by_market.get("crypto") or {},
        }.items()
    }
    market_cols = {"overall": 2, "fx": 3, "crypto": 4}
    for market, col in market_cols.items():
        dash.cell(1, col, {"overall": "Overall", "fx": "FX", "crypto": "Crypto"}[market])

    core_rows = [
        ("Trades", "trades", "count", None),
        ("Wins", "wins", "count", "profit"),
        ("Losses", "losses", "count", "loss"),
        ("Break-even", "break_even", "count", None),
        ("Test", "test_trades", "count", None),
        ("Win rate", "win_rate_pct", "pct", None),
        ("Net P/L", "market_return_pct", "pct", "auto"),
        ("Gross gain", "gross_gain_return_pct", "pct", "profit"),
        ("Gross loss", "gross_loss_return_pct", "pct", "loss"),
        ("Best Win Streak", "winning_streak", "count", "profit"),
        ("Worst Losing Streak", "losing_streak", "count", "loss"),
        ("Avg result %", "avg_result_pct", "pct", "auto"),
        ("Avg R", "avg_r_multiple", "r", "auto"),
        ("Avg stop %", "avg_stop_pct", "pct", None),
        ("Avg target %", "avg_target_pct", "pct", None),
        ("Max target %", "max_target_pct", "pct", None),
        ("Avg duration (DD:HH:MM:SS)", "avg_duration_seconds", "duration", None),
        (DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL, "move_to_break_even_duration_seconds", "duration", None),
        (DASHBOARD_MOVE_TO_PROFIT_LABEL, "move_to_profit_duration_seconds", "duration", None),
        ("Max win %", "max_result_pct", "pct", "profit"),
        ("Max loss %", "min_result_pct", "pct", "loss"),
        ("Max R loss", "min_r_multiple", "r", "loss"),
        ("Max R win", "max_r_multiple", "r", "profit"),
        ("Shortest (DD:HH:MM:SS)", "shortest_duration_seconds", "duration", None),
        ("Longest (DD:HH:MM:SS)", "longest_duration_seconds", "duration", None),
    ]

    row = 2
    for label, key, kind, semantic in core_rows:
        dash.cell(row, 1, label).font = Font(bold=True)
        for market, col in market_cols.items():
            bucket = {**dict(buckets[market] or {}), **move_duration_metrics[market]}
            value = bucket.get(key)
            cell = dash.cell(row, col)
            # Overall money totals can mix currencies; retain the existing neutral blank cells.
            if market == "overall" and key in {"market_return_pct", "gross_gain_return_pct", "gross_loss_return_pct"}:
                cell.fill = PatternFill("solid", fgColor=LIGHT_GREY_FILL_RGB)
                continue
            if kind == "pct":
                number = _as_float(value)
                cell.value = "" if number is None else number / 100.0
                cell.number_format = "0.00%"
            elif kind == "r":
                cell.value = "" if value is None else value
                cell.number_format = '0.000"R"'
            elif kind == "count":
                cell.value = "" if value is None else value
                cell.number_format = "0"
            elif kind == "duration":
                cell.value = _fmt_duration_full(value) if value is not None else ""
                cell.number_format = r'00\:00\:00\:00'
            elif kind == "money":
                money_map = (bucket.get("money_by_currency") or {}).get(key) or {}
                if isinstance(money_map, dict) and len(money_map) == 1:
                    currency, amount = next(iter(money_map.items()))
                    cell.value = amount
                    cell.number_format = _currency_number_format(currency)
                else:
                    cell.value = "" if value is None else value
            if semantic == "profit":
                _apply_full_cell_semantic_fill(cell, "profit")
            elif semantic == "loss":
                _apply_full_cell_semantic_fill(cell, "loss")
            elif semantic == "auto":
                _apply_sign_based_full_cell_fill(cell)
        if key in {"min_result_pct", "max_result_pct", "min_r_multiple", "max_r_multiple", "shortest_duration_seconds", "longest_duration_seconds"}:
            row += 1
            dash.cell(row, 1, "Source")
            _apply_dashboard_source_label_style(dash.cell(row, 1))
            for market, col in market_cols.items():
                source = ((buckets[market] or {}).get("metric_sources") or {}).get(key)
                dash.cell(row, col, _fmt_detail_src(source) if source else "")
        row += 1

    section_start = row
    section_specs = [
        ("Winners", [("Avg stop %", "avg_stop_pct_winners", None), ("Avg target %", "avg_target_pct_winners", None), ("Avg result %", "avg_result_pct_winners", "profit"), ("Avg R", "avg_r_multiple_winners", "profit")]),
        ("Losers", [("Avg stop %", "avg_stop_pct_losers", None), ("Avg target %", "avg_target_pct_losers", None), ("Avg result %", "avg_result_pct_losers", "loss"), ("Avg R", "avg_r_multiple_losers", "loss")]),
        ("Drawdown", [("Max drawdown", "max_drawdown_pct", "loss"), ("Avg drawdown", "avg_drawdown_pct", "loss")]),
    ]
    risk_by_market = risk.get("by_market") or {}
    for title, metrics in section_specs:
        dash.cell(row, 1, title).font = Font(bold=True)
        for col in market_cols.values():
            dash.cell(row, col).fill = PatternFill("solid", fgColor="EAF2F8")
        row += 1
        for label, key, semantic in metrics:
            dash.cell(row, 1, label).font = Font(bold=True)
            for market, col in market_cols.items():
                source = risk_by_market.get(market) or risk
                value = _as_float(source.get(key))
                cell = dash.cell(row, col, "" if value is None else (value / 100.0 if "%" in label.lower() or "drawdown" in label.lower() else value))
                cell.number_format = "0.00%" if "%" in label.lower() or "drawdown" in label.lower() else '0.000"R"'
                if semantic:
                    _apply_full_cell_semantic_fill(cell, semantic)
            row += 1

    dash.cell(row, 1, "Duration").font = Font(bold=True)
    dash.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    row += 1
    for label, key in (("Overall avg", "overall_avg_seconds"), ("Overall shortest", "overall_shortest_seconds"), ("Overall longest", "overall_longest_seconds")):
        dash.cell(row, 1, label).font = Font(bold=True)
        dash.cell(row, 2, _fmt_duration_full(duration.get(key)))
        dash.cell(row, 2).number_format = r'00\:00\:00\:00'
        row += 1

    _style_header_row(dash, 1)
    _table_border(dash, 1, 1, row - 1, 4)
    _profit_loss_rules(dash, f"B13:D14")

    balances = _canonicalize_and_dedupe_balances(snapshot.get("balances") or stats.get("balances") or [])
    dash.cell(1, 6, "Account Balances").font = Font(bold=True)
    for col, header in enumerate(("Account", "Balance", "Currency", "As Of"), start=6):
        dash.cell(2, col, header).font = Font(bold=True)
    for target_row, rec in enumerate(balances, start=3):
        if not isinstance(rec, dict):
            continue
        currency = _currency_code(rec.get("currency"), rec.get("account_currency"))
        dash.cell(target_row, 6, _canonical_account_label(rec.get("account_label") or rec.get("account") or rec.get("source") or "—"))
        dash.cell(target_row, 7, _as_float(rec.get("balance")))
        dash.cell(target_row, 7).number_format = "#,##0.0000000000" if _is_crypto_currency(currency) else "#,##0.00"
        dash.cell(target_row, 8, currency)
        dash.cell(target_row, 9, rec.get("as_of") or "")

    dash.cell(11, 6, "Instrument leaders").font = Font(bold=True)
    for col, header in enumerate(("Metric", "Symbol", "Wins", "Losses", "Trades"), start=6):
        dash.cell(12, col, header).font = Font(bold=True)
    leader_rows = (("FX most wins", "fx_most_wins_instrument"), ("FX most losses", "fx_most_losses_instrument"), ("Crypto most wins", "crypto_most_wins_instrument"), ("Crypto most losses", "crypto_most_losses_instrument"))
    for target_row, (label, key) in enumerate(leader_rows, start=13):
        payload = leaders.get(key) or {}
        dash.cell(target_row, 6, label)
        dash.cell(target_row, 7, payload.get("symbol") or "—")
        dash.cell(target_row, 8, payload.get("wins"))
        dash.cell(target_row, 9, payload.get("losses"))
        dash.cell(target_row, 10, payload.get("trades", payload.get("total_trades")))
    _apply_dashboard_requested_semantic_fills(dash)

    resolved_balances = _resolved_all_trade_balances(rows)
    ws=_get_all_trades_sheet(wb); headers=TRADE_LOG_HEADERS; ws.append(headers)
    for i, row in enumerate(rows):
        pct = _as_float(row.get('result_pct'))
        is_monthly = str(row.get("row_type") or "") == "monthly_aud_reval"
        symbol = row.get('symbol') or ("MONTHLY AUD P/L" if is_monthly else "")
        acct = row.get('account_label') or row.get('account') or ("BYBIT" if is_monthly else "")
        notes = row.get('notes') or ('Monthly BYBIT AUD P/L bookkeeping note (excluded from metrics).' if is_monthly else '')
        net_pnl = row.get('net_profit') if row.get('net_profit') is not None else row.get('result_cash')
        ot = row.get('open_time') or row.get("period_month")
        ct = row.get('close_time') or row.get("period_month")
        otv = datetime.fromisoformat(str(ot).replace("Z","")) if isinstance(ot, str) and ot else ot
        ctv = datetime.fromisoformat(str(ct).replace("Z","")) if isinstance(ct, str) and ct else ct
        if isinstance(otv, datetime) and otv.tzinfo is not None:
            otv = otv.replace(tzinfo=None)
        if isinstance(ctv, datetime) and ctv.tzinfo is not None:
            ctv = ctv.replace(tzinfo=None)
        comm = _as_float(row.get('commission'))
        comm_val = '' if comm in (None, 0.0) else comm
        resolved_balance = resolved_balances.get(str(i))
        cashflow_new_balance = row.get('cashflow_new_balance')
        row_type = str(row.get('row_type') or 'trade').strip().lower()
        if row_type == 'cashflow' and cashflow_new_balance in (None, ''):
            cashflow_new_balance = resolved_balance
        side = str(row.get('side') or '').upper()
        if row_type in {'monthly_aud_reval','cashflow'}:
            setup_val = ''
        else:
            setup_val = row.get('setup') or ''
        stop_loss_distance = ''
        target_distance = ''
        if row_type == 'trade':
            stop_loss_distance = _distance_fraction_from_prices(row.get('entry_price'), row.get('stop_loss'))
            target_distance = _distance_fraction_from_prices(row.get('entry_price'), row.get('take_profit'))
            stop_loss_distance = '' if stop_loss_distance is None else stop_loss_distance
            target_distance = '' if target_distance is None else target_distance
        close_stopout = row.get('close_stopout')
        if close_stopout in (None, ''):
            close_stopout = row.get('close_stop_out')
        if close_stopout in (None, ''):
            close_stopout = row.get('stop_out')
        values = {
            TRADE_NUMBER_HEADER: str(row.get("trade_number") or ""),
            "Open Time": otv, "Close Time": ctv, "Account": acct, "Symbol": symbol, "Side": side,
            "Qty": row.get('qty'), "Entry Price": row.get('entry_price'), "Exit Price": row.get('exit_price'),
            "Stop Loss Price": row.get('stop_loss'), "Stop Loss Distance": stop_loss_distance,
            "Target Price": row.get('take_profit'), "Target Distance": target_distance, "Commission": comm_val,
            "Net P/L": net_pnl, "Profit %": (pct / 100.0 if pct is not None else ''),
            "R-Multiple": row.get('r_multiple'), "Balance After": resolved_balance,
            "Trade Duration (DD:HH:MM:SS)": _fmt_duration_full(_infer_trade_duration_seconds(row)),
            "Test": 'Yes' if _is_test_trade_value(row.get('is_test_trade')) else 'No',
            "Pattern": row.get('pattern') or '', "EMA": row.get('ema') or '', "ATHS/ATLS": row.get('aths_atls') or '',
            "Order": row.get('order_type') or '', "Round Number": row.get('round_number') or '',
            "Spiked Out": row.get('spiked_out') or '', "Close Stopout": close_stopout or '',
            "Near Perfect Entry": row.get('near_perfect_entry') or '', "Near Win": row.get('near_win') or '',
            "Early Close": row.get('early_close') or '', "Setup": setup_val,
            "Timeframe": _canonical_journal_timeframe(row.get('timeframe') or ''),
            "Breakeven": row.get('breakeven') or '', "Notes": notes,
            "Cashflow Amount": row.get('cashflow_amount'), "Cashflow New Balance": cashflow_new_balance,
            "Currency": row.get('currency') or row.get('account_currency') or row.get('result_currency') or '',
            "Row Type": row.get('row_type') or 'trade', "Row ID": stable_row_id(row),
        }
        for header, field in MOVE_TO_FIELD_MAP.items():
            raw_move_value = row.get(field)
            if field in {"move_to_break_even_duration", "move_to_profit_duration"} and raw_move_value not in (None, ""):
                values[header] = _fmt_duration_full(raw_move_value)
            else:
                values[header] = raw_move_value or ''
        ws.append([values.get(header, '') for header in TRADE_LOG_HEADERS])
    _style_table_sheet(ws,1,'A2',True)
    trade_cols = _trade_log_header_map(ws)
    def _fmt_col(row_idx: int, header: str, number_format: str) -> None:
        col = trade_cols.get(header)
        if col:
            ws.cell(row_idx, col).number_format = number_format
    for rr in range(2, ws.max_row + 1):
        row_ctx = rows[rr - 2] if rr - 2 < len(rows) else {}
        ccy_comm = _infer_trade_log_currency(row_ctx, field="commission")
        ccy_pnl = _infer_trade_log_currency(row_ctx, field="net_pnl")
        ccy_bal = _infer_trade_log_currency(row_ctx, field="balance_after")
        _fmt_col(rr, TRADE_NUMBER_HEADER, "@")
        _fmt_col(rr, "Qty", '#,##0.##########')
        _fmt_col(rr, "Open Time", 'yyyy-mm-dd hh:mm:ss')
        _fmt_col(rr, "Close Time", 'yyyy-mm-dd hh:mm:ss')
        _fmt_col(rr, "Stop Loss Distance", "0.00%")
        _fmt_col(rr, "Target Distance", "0.00%")
        if ccy_comm:
            _fmt_col(rr, "Commission", _currency_number_format(ccy_comm))
        if ccy_pnl:
            _fmt_col(rr, "Net P/L", _currency_number_format(ccy_pnl))
        _fmt_col(rr, "Profit %", "0.00%")
        _fmt_col(rr, "R-Multiple", '0.00')
        if ccy_bal:
            _fmt_col(rr, "Balance After", '#,##0.0000000000' if _is_crypto_currency(ccy_bal) else '#,##0.00')
        _fmt_col(rr, "Trade Duration (DD:HH:MM:SS)", r'00\:00\:00\:00')
        _fmt_col(rr, "Move to Break Even Time", 'yyyy-mm-dd hh:mm:ss')
        _fmt_col(rr, "Move to Break Even Duration", r'00\:00\:00\:00')
        _fmt_col(rr, "Move to Profit Time", 'yyyy-mm-dd hh:mm:ss')
        _fmt_col(rr, "Move to Profit Duration", r'00\:00\:00\:00')
        for pct_header in (
            "Move to Break Even Distance From Entry %",
            "Move to Break Even Distance From Exit %",
            "Move to Profit Distance From Entry %",
            "Move to Profit Distance From Exit %",
        ):
            _fmt_col(rr, pct_header, "0.00%")
    _ensure_trade_log_schema(ws)
    trade_cols = _trade_log_header_map(ws)
    last_trade_row = max(TRADE_LOG_DATA_START_ROW, ws.max_row)
    commission_col = trade_cols.get("Commission")
    net_col = trade_cols.get("Net P/L")
    r_col = trade_cols.get("R-Multiple")
    if commission_col:
        letter = get_column_letter(commission_col)
        _negative_impact_rule(ws, f"{letter}{TRADE_LOG_DATA_START_ROW}:{letter}{last_trade_row}")
    if net_col and r_col:
        _profit_loss_rules(ws, f"{get_column_letter(net_col)}{TRADE_LOG_DATA_START_ROW}:{get_column_letter(r_col)}{last_trade_row}")
    _apply_trade_log_win_loss_row_formatting(ws)
    _apply_trade_log_win_loss_direct_row_fills(ws)

    inst=wb['Instrument Averages']; headers=["Symbol","Class","Trades","Longs","Shorts","Wins","Losses","Break-even","Long wins","Long losses","Short wins","Short losses","Long break-even","Short break-even","Net P/L %","Avg P/L %","Win Rate %","Avg stop % (W)","Avg stop % (L)","Avg target % (W)","Avg target % (L)","Shortest duration (DD:HH:MM:SS)","Avg duration (DD:HH:MM:SS)","Longest duration (DD:HH:MM:SS)"]; inst.append(headers)
    for rec in (stats.get('by_instrument') or []):
        cls=str(rec.get("asset_class") or rec.get("class") or "").lower()
        row_idx = inst.max_row + 1
        netp = _as_float(rec.get("net_result_pct"))
        avgp = _as_float(rec.get("avg_result_pct"))
        inst.append([rec.get("symbol"),cls.upper() if cls else None,rec.get("total_trades", rec.get("trades")),rec.get("long_trades", rec.get("longs")),rec.get("short_trades", rec.get("shorts")),rec.get("wins"),rec.get("losses"),rec.get("break_even"),rec.get("long_wins"),rec.get("long_losses"),rec.get("short_wins"),rec.get("short_losses"),rec.get("long_break_even"),rec.get("short_break_even"),(netp/100.0 if netp is not None else ''),(avgp/100.0 if avgp is not None else ''),rec.get("win_rate_pct"),rec.get('avg_sl_pct_wins'),rec.get('avg_sl_pct_losses'),rec.get('avg_tp_pct_wins'),rec.get('avg_tp_pct_losses'),
                     _fmt_duration_full(rec.get("min_trade_duration_seconds", rec.get("shortest_duration_seconds"))),
                     _fmt_duration_full(rec.get("avg_trade_duration_seconds", rec.get("avg_duration_seconds"))),
                     _fmt_duration_full(rec.get("max_trade_duration_seconds", rec.get("longest_duration_seconds")))])
        for cc in range(17, 22):
            cell = inst.cell(row_idx, cc)
            val = _as_float(cell.value)
            if val is not None:
                cell.value = val / 100.0
                cell.number_format = "0.00%"
        for zc in [4,5,6,7,8,9,10,11,12,13,14]:
            inst.cell(row_idx, zc).number_format = ZERO_HIDE_FORMAT
        inst.cell(row_idx, 15).number_format = "0.00%"
        inst.cell(row_idx, 16).number_format = "0.00%"
        for col in (22,23,24):
            inst.cell(row_idx, col).number_format = r'00\:00\:00\:00'
    _style_table_sheet(inst,1,'B2',True)
    _profit_loss_rules(inst, f"O2:P{max(2, inst.max_row)}")
    _apply_instrument_averages_semantic_fills(inst)

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
    _apply_pnl_calendar_profit_loss_formatting(cal)
    _ensure_pnl_calendar_freeze_panes(cal)
    _ensure_report_sheets(wb, snapshot)

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
            vcell.number_format = r'00\:00\:00\:00'
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


_REPORT_SECTION_ROWS = {"Winners", "Losers", "Drawdown", "Longs", "Shorts"}
_REPORT_SPECS = [
    ("Trades", "trades", "count", None),
    ("Wins", "wins", "count", "profit"),
    ("Losses", "losses", "count", "loss"),
    ("Break-even", "break_even", "count", None),
    ("Test", "test_trades", "count", None),
    ("Win rate", "win_rate_pct", "pct", None),
    ("Net P/L", "net_profit_total", "number", "auto"),
    ("Gross gain", "gross_gain", "number", "profit"),
    ("Gross loss", "gross_loss", "number", "loss"),
    ("Best Win Streak", "winning_streak", "count", "profit"),
    ("Worst Losing Streak", "losing_streak", "count", "loss"),
    ("Avg result %", "avg_result_pct", "pct", "auto"),
    ("Avg R", "avg_r_multiple", "r", "auto"),
    ("Avg stop %", "avg_stop_pct", "pct", None),
    ("Avg target %", "avg_target_pct", "pct", None),
    ("Max target %", "max_target_pct", "pct", None),
    ("Avg duration (DD:HH:MM:SS)", "avg_duration_seconds", "duration", None),
    ("Move to Break Even (DD:HH:MM:SS)", "move_to_break_even_duration_seconds", "duration", None),
    ("Move to Profit (DD:HH:MM:SS)", "move_to_profit_duration_seconds", "duration", None),
    ("Max win %", "max_result_pct", "pct", "profit"),
    ("Source", "source:max_result_pct", "source", None),
    ("Max loss %", "min_result_pct", "pct", "loss"),
    ("Source", "source:min_result_pct", "source", None),
    ("Max R loss", "min_r_multiple", "r", "loss"),
    ("Source", "source:min_r_multiple", "source", None),
    ("Max R win", "max_r_multiple", "r", "profit"),
    ("Source", "source:max_r_multiple", "source", None),
    ("Shortest (DD:HH:MM:SS)", "shortest_duration_seconds", "duration", None),
    ("Source", "source:shortest_duration_seconds", "source", None),
    ("Longest (DD:HH:MM:SS)", "longest_duration_seconds", "duration", None),
    ("Source", "source:longest_duration_seconds", "source", None),
    ("Winners", None, "section", None),
    ("Avg stop %", "avg_stop_pct_winners", "pct", None),
    ("Avg target %", "avg_target_pct_winners", "pct", None),
    ("Avg result %", "avg_result_pct_winners", "pct", "profit"),
    ("Avg R", "avg_r_multiple_winners", "r", "profit"),
    ("Losers", None, "section", None),
    ("Avg stop %", "avg_stop_pct_losers", "pct", None),
    ("Avg target %", "avg_target_pct_losers", "pct", None),
    ("Avg result %", "avg_result_pct_losers", "pct", "loss"),
    ("Avg R", "avg_r_multiple_losers", "r", "loss"),
    ("Drawdown", None, "section", None),
    ("Max drawdown", "max_drawdown_pct", "pct", "loss"),
    ("Avg drawdown", "avg_drawdown_pct", "pct", "loss"),
    ("Longs", "long_trades", "count", None),
    ("Long wins", "long_wins", "count", "profit"),
    ("Long losses", "long_losses", "count", "loss"),
    ("Long break-even", "long_break_even", "count", None),
    ("Shorts", "short_trades", "count", None),
    ("Short wins", "short_wins", "count", "profit"),
    ("Short losses", "short_losses", "count", "loss"),
    ("Short break-even", "short_break_even", "count", None),
]


def _report_trade_years_from_snapshot(snapshot: Dict[str, Any]) -> List[int]:
    years = {REPORT_START_YEAR, REPORT_MIN_END_YEAR, datetime.now(JOURNAL_DISPLAY_TZ).year}
    for row in snapshot.get("items") or []:
        if not isinstance(row, dict) or str(row.get("row_type") or "trade") != "trade":
            continue
        dt = _as_date(row.get("close_time") or row.get("open_time"))
        if dt:
            years.add(int(dt.year))
    reports = ((snapshot.get("stats") or {}).get("period_reports") or {})
    for key in (reports.get("years") or {}).keys():
        try:
            years.add(int(key))
        except Exception:
            pass
    end_year = max(max(years), REPORT_MIN_END_YEAR)
    return list(range(REPORT_START_YEAR, end_year + 1))


def expected_report_sheet_names(snapshot: Dict[str, Any] | None = None) -> List[str]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return [REPORT_YEARLY_SHEET, *[str(year) for year in _report_trade_years_from_snapshot(snapshot)]]


def _period_report_lookup(snapshot: Dict[str, Any], *, year: int, month: int | None = None) -> Dict[str, Any]:
    reports = ((snapshot.get("stats") or {}).get("period_reports") or {})
    if month is None:
        years = reports.get("years") if isinstance(reports.get("years"), dict) else {}
        return years.get(year) or years.get(str(year)) or {}
    months = reports.get("months") if isinstance(reports.get("months"), dict) else {}
    year_months = months.get(year) or months.get(str(year)) or {}
    if not isinstance(year_months, dict):
        return {}
    return year_months.get(month) or year_months.get(str(month)) or {}


def _report_bucket_from_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    groups = stats.get("groups") if isinstance(stats.get("groups"), dict) else {}
    totals = stats.get("totals") if isinstance(stats.get("totals"), dict) else {}
    by_market = groups.get("by_market") if isinstance(groups.get("by_market"), dict) else {}
    overall = by_market.get("overall") if isinstance(by_market.get("overall"), dict) else {}
    risk = groups.get("risk_expectancy") if isinstance(groups.get("risk_expectancy"), dict) else {}
    risk_by_market = risk.get("by_market") if isinstance(risk.get("by_market"), dict) else {}
    duration = groups.get("duration") if isinstance(groups.get("duration"), dict) else {}
    bucket: Dict[str, Any] = {}
    bucket.update(totals)
    bucket.update(overall)
    bucket.update(risk)
    if isinstance(risk_by_market.get("overall"), dict):
        bucket.update(risk_by_market["overall"])
    bucket.setdefault("shortest_duration_seconds", duration.get("overall_shortest_seconds"))
    bucket.setdefault("longest_duration_seconds", duration.get("overall_longest_seconds"))
    bucket.setdefault("avg_duration_seconds", duration.get("overall_avg_seconds"))
    metric_sources: Dict[str, Any] = {}
    for source_map in (
        overall.get("metric_sources") if isinstance(overall, dict) else None,
        duration.get("metric_sources") if isinstance(duration, dict) else None,
    ):
        if isinstance(source_map, dict):
            metric_sources.update(source_map)
    if metric_sources:
        bucket["metric_sources"] = metric_sources
    return bucket


def _report_cell_value(bucket: Dict[str, Any], key: str | None, kind: str) -> Any:
    if kind == "section" or key is None:
        return ""
    if kind == "source":
        source_key = key.split(":", 1)[1] if ":" in key else key
        source = (bucket.get("metric_sources") or {}).get(source_key)
        return _fmt_detail_src(source) if source else ""
    value = bucket.get(key)
    if kind == "pct":
        number = _as_float(value)
        return "" if number is None else number / 100.0
    if kind == "r":
        number = _as_float(value)
        return "" if number is None else number
    if kind == "count":
        number = _as_float(value)
        return "" if number is None else int(number)
    if kind == "duration":
        return _fmt_duration_full(value) if value not in (None, "") else ""
    if kind == "number":
        number = _as_float(value)
        return "" if number is None else number
    return "" if value is None else value


def _format_report_sheet(ws, last_col: int) -> None:
    ws.freeze_panes = "B2"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 32
    for col in range(2, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    _style_header_row(ws, 1)
    _table_border(ws, 1, 1, len(REPORT_METRIC_LABELS) + 1, last_col)
    for row in range(2, len(REPORT_METRIC_LABELS) + 2):
        label = str(ws.cell(row, 1).value or "")
        ws.cell(row, 1).font = Font(name="Calibri", size=11, bold=True)
        if label in _REPORT_SECTION_ROWS:
            for col in range(1, last_col + 1):
                ws.cell(row, col).font = Font(name="Calibri", size=11, bold=True)
                ws.cell(row, col).fill = PatternFill("solid", fgColor="EAF2F8")
        if label == "Source":
            _apply_dashboard_source_label_style(ws.cell(row, 1))
    for idx, (_label, _key, kind, semantic) in enumerate(_REPORT_SPECS, start=2):
        for col in range(2, last_col + 1):
            cell = ws.cell(idx, col)
            if kind == "pct":
                cell.number_format = "0.00%"
            elif kind == "r":
                cell.number_format = '0.000"R"'
            elif kind == "count":
                cell.number_format = "0"
            elif kind == "duration":
                cell.number_format = r'00\:00\:00\:00'
            elif kind == "number":
                cell.number_format = '#,##0.00'
            if semantic == "profit":
                _apply_full_cell_semantic_fill(cell, "profit")
            elif semantic == "loss":
                _apply_full_cell_semantic_fill(cell, "loss")
            elif semantic == "auto":
                _apply_sign_based_full_cell_fill(cell)


def _clear_report_sheet(ws) -> None:
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    if ws.max_row:
        ws.delete_rows(1, ws.max_row)
    if ws.max_column:
        ws.delete_cols(1, ws.max_column)
    ws.conditional_formatting._cf_rules.clear()


def _write_report_sheet(ws, headers: List[Any], buckets: List[Dict[str, Any]]) -> None:
    _clear_report_sheet(ws)
    ws.cell(1, 1, "")
    for col, header in enumerate(headers, start=2):
        ws.cell(1, col, header)
    for row_idx, (label, key, kind, _semantic) in enumerate(_REPORT_SPECS, start=2):
        ws.cell(row_idx, 1, label)
        for col, bucket in enumerate(buckets, start=2):
            ws.cell(row_idx, col, _report_cell_value(bucket, key, kind))
    _format_report_sheet(ws, max(1, len(headers) + 1))


def _ensure_report_sheets(wb, snapshot: Dict[str, Any], diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    years = _report_trade_years_from_snapshot(snapshot if isinstance(snapshot, dict) else {})
    expected_names = [REPORT_YEARLY_SHEET, *[str(year) for year in years]]
    for name in expected_names:
        if name not in wb.sheetnames:
            wb.create_sheet(name)
            diagnostics.setdefault("created_report_sheets", []).append(name)
    yearly_buckets = [_report_bucket_from_stats(_period_report_lookup(snapshot, year=year)) for year in years]
    _write_report_sheet(wb[REPORT_YEARLY_SHEET], years, yearly_buckets)
    for year in years:
        month_headers = [calendar.month_name[month] for month in range(1, 13)]
        month_buckets = [
            _report_bucket_from_stats(_period_report_lookup(snapshot, year=year, month=month))
            for month in range(1, 13)
        ]
        _write_report_sheet(wb[str(year)], month_headers, month_buckets)
    ordered = [name for name in SHEET_ORDER if name in wb.sheetnames] + expected_names
    seen = set(ordered)
    remaining = [sheet.title for sheet in wb._sheets if sheet.title not in seen]
    wb._sheets = [wb[name] for name in ordered if name in wb.sheetnames] + [wb[name] for name in remaining]

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
        num = float(value)
        if num >= 1000000:
            n = int(num)
            days = n // 1000000
            n %= 1000000
            hours = n // 10000
            n %= 10000
            minutes = n // 100
            seconds = n % 100
            if 0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60:
                return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
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
    if total:
        return total
    try:
        return float(text)
    except ValueError:
        return None

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




def _style_signature(cell) -> Dict[str, Any]:
    return {
        "fill": copy(cell.fill),
        "font": copy(cell.font),
        "border": copy(cell.border),
        "alignment": copy(cell.alignment),
    }


def _header_style_snapshot(ws) -> List[Dict[str, Any]]:
    return [
        {
            "value": ws.cell(1, c).value,
            "style": _style_signature(ws.cell(1, c)),
        }
        for c in range(1, ws.max_column + 1)
    ]


def _auto_filter_layout_signature(ws) -> Any:
    ref = ws.auto_filter.ref if ws.auto_filter else None
    if not ref:
        return None
    try:
        min_col, min_row, max_col, _max_row = range_boundaries(ref)
        return (min_col, min_row, max_col)
    except Exception:
        return ref


def _worksheet_layout_snapshot(ws) -> Dict[str, Any]:
    return {
        "merged": [str(r) for r in ws.merged_cells.ranges],
        "row_heights": {k: v.height for k, v in ws.row_dimensions.items()},
        "col_widths": {k: v.width for k, v in ws.column_dimensions.items()},
        "hidden_cols": {k: bool(v.hidden) for k, v in ws.column_dimensions.items() if v.hidden},
        "freeze": ws.freeze_panes,
        "auto_filter": _auto_filter_layout_signature(ws),
    }


def _snapshot_invariants(wb) -> Dict[str, Any]:
    out: Dict[str, Any] = {"sheetnames": list(wb.sheetnames)}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        out[f"{sheet_name}_layout"] = _worksheet_layout_snapshot(ws)
    dash = wb["Dashboard"] if "Dashboard" in wb.sheetnames else None
    if dash is not None:
        out["dash_cf"] = [str(k.sqref) for k in dash.conditional_formatting._cf_rules.keys()]
        out["dash_styles"] = {
            (r, c): _style_signature(dash.cell(r, c))
            for r in range(1, dash.max_row + 1)
            for c in range(1, dash.max_column + 1)
        }
    for name, prefix in ((ALL_TRADES_SHEET, "all_trades"), (TRADE_LOG_SHEET, "trade_log"), ("Instrument Averages", "instrument")):
        try:
            ws = _get_all_trades_sheet(wb) if name in {ALL_TRADES_SHEET, TRADE_LOG_SHEET} and prefix in {"all_trades", "trade_log"} else (wb[name] if name in wb.sheetnames else None)
        except Exception:
            ws = None
        if ws is not None:
            out[f"{prefix}_headers"] = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
            out[f"{prefix}_header_styles"] = _header_style_snapshot(ws)
        ref = ws.auto_filter.ref if ws and ws.auto_filter else None
        out[f"{prefix}_filter_present"] = bool(ref)
        if ref:
            min_col, min_row, _, _ = range_boundaries(ref)
            out[f"{prefix}_filter_min_col"] = min_col
            out[f"{prefix}_filter_min_row"] = min_row
        else:
            out[f"{prefix}_filter_min_col"] = None
            out[f"{prefix}_filter_min_row"] = None
    if "P&L Calendar" in wb.sheetnames:
        ws = wb["P&L Calendar"]
        out["pnl_calendar_layout"] = _worksheet_layout_snapshot(ws)
        out["pnl_calendar_dimensions"] = (ws.max_row, ws.max_column)
    return out


def _workbook_content_snapshot(wb) -> Dict[str, int]:
    trade_log = _get_trade_log_sheet(wb)
    instrument = wb["Instrument Averages"] if "Instrument Averages" in wb.sheetnames else None
    calendar_ws = wb["P&L Calendar"] if "P&L Calendar" in wb.sheetnames else None
    instrument_rows = 0
    if instrument is not None:
        instrument_rows = sum(
            1 for row in range(2, instrument.max_row + 1)
            if any(instrument.cell(row, col).value not in (None, "") for col in range(1, instrument.max_column + 1))
        )
    calendar_cells = 0
    if calendar_ws is not None:
        calendar_cells = sum(
            1 for row in calendar_ws.iter_rows()
            for cell in row if cell.value not in (None, "")
        )
    return {
        "trade_log_data_rows": _trade_log_data_row_count(trade_log),
        "instrument_average_data_rows": instrument_rows,
        "pnl_calendar_populated_cells": calendar_cells,
    }


def _assert_workbook_content_not_wiped(before: Dict[str, int], after: Dict[str, int], *, migration_performed: bool) -> None:
    labels = {
        "trade_log_data_rows": "Trade Log data rows",
        "instrument_average_data_rows": "Instrument Averages data rows",
        "pnl_calendar_populated_cells": "P&L Calendar populated cells",
    }
    for key, label in labels.items():
        before_value = int(before.get(key) or 0)
        after_value = int(after.get(key) or 0)
        if before_value > 0 and after_value == 0:
            raise RuntimeError(f"Workbook update aborted because {label} would be wiped (before={before_value}, after=0).")
        if migration_performed and after_value < before_value:
            raise RuntimeError(
                f"Workbook schema migration aborted because {label} dropped: "
                f"before={before_value}, after={after_value}."
            )


def _assert_invariants_unchanged(before: Dict[str, Any], after: Dict[str, Any]) -> None:
    skipped = {"pnl_calendar_layout", "pnl_calendar_dimensions", "P&L Calendar_layout", "dash_styles"}
    for key in before.keys() | after.keys():
        if key in skipped:
            continue
        if before.get(key) != after.get(key):
            raise RuntimeError(f"Workbook structural invariant changed: {key}")


def _assert_filter_covers_data(ws, *, sheet_name: str, header_row: int = 1, required_headers: List[str] | None = None, header_map: Dict[str, int] | None = None) -> None:
    ref = ws.auto_filter.ref if ws.auto_filter else None
    if not ref:
        raise RuntimeError(f"{sheet_name} filter missing.")
    min_col, min_row, max_col, max_row = range_boundaries(ref)
    if min_row != header_row or min_col != 1:
        raise RuntimeError(f"{sheet_name} filter starts at invalid range {ref}.")
    headers = header_map or _header_map(ws, header_row=header_row)
    required_headers = required_headers or []
    for h in required_headers:
        col = headers.get(h)
        if col and col > max_col:
            raise RuntimeError(f"{sheet_name} filter does not include required column '{h}'.")
    last_row = header_row
    for r in range(header_row + 1, ws.max_row + 1):
        if any(ws.cell(r, c).value not in (None, "") for c in range(1, ws.max_column + 1)):
            last_row = r
    if max_row < last_row:
        raise RuntimeError(f"{sheet_name} filter excludes populated rows.")


def _shift_dashboard_range_rows(cell_range: str, start_row: int, amount: int) -> str:
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    if max_row < start_row:
        return cell_range
    if min_row >= start_row:
        min_row += amount
    max_row += amount
    return f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"


def _delete_dashboard_rows_preserving_layout(ws, row_idx: int, amount: int) -> None:
    delete_end = row_idx + amount - 1

    def shifted_range(cell_range: str) -> str | None:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        if max_row < row_idx:
            return cell_range
        if min_row > delete_end:
            min_row -= amount
            max_row -= amount
        elif min_row >= row_idx and max_row <= delete_end:
            return None
        else:
            removed = max(0, min(max_row, delete_end) - max(min_row, row_idx) + 1)
            if min_row >= row_idx:
                min_row = row_idx
            max_row -= removed
            if max_row < min_row:
                return None
        return f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"

    merged_ranges = [str(merged) for merged in ws.merged_cells.ranges]
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))

    shifted_cf = OrderedDict()
    for key, rules in list(getattr(ws.conditional_formatting, "_cf_rules", {}).items()):
        parts = [shifted_range(part) for part in str(key.sqref).split()]
        parts = [part for part in parts if part]
        if not parts:
            continue
        shifted_key = copy(key)
        shifted_key.sqref = " ".join(parts)
        shifted_cf[shifted_key] = rules

    row_heights = {idx: dim.height for idx, dim in ws.row_dimensions.items()}
    ws.delete_rows(row_idx, amount)
    for idx in list(ws.row_dimensions.keys()):
        del ws.row_dimensions[idx]
    for idx, height in row_heights.items():
        if row_idx <= idx <= delete_end:
            continue
        target = idx - amount if idx > delete_end else idx
        ws.row_dimensions[target].height = height

    for cell_range in merged_ranges:
        shifted = shifted_range(cell_range)
        if shifted:
            ws.merge_cells(shifted)
    ws.conditional_formatting._cf_rules = shifted_cf

def _remove_dashboard_metric_pair(
    ws, label: str, diagnostics: Dict[str, Any] | None = None
) -> bool:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    wanted = str(label or "").strip().lower()
    for row in range(1, ws.max_row + 1):
        if str(ws.cell(row, 1).value or "").strip().lower() != wanted:
            continue
        amount = 1
        if row + 1 <= ws.max_row and str(ws.cell(row + 1, 1).value or "").strip().lower() == "source":
            amount = 2
        _delete_dashboard_rows_preserving_layout(ws, row, amount)
        diagnostics.setdefault("removed_dashboard_metric_rows", {})[label] = amount
        return True
    return False

def _dashboard_row_snapshot(ws, row: int) -> Dict[str, Any]:
    cells = []
    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row, col)
        cells.append({
            "value": cell.value,
            "font": copy(cell.font),
            "fill": copy(cell.fill),
            "border": copy(cell.border),
            "alignment": copy(cell.alignment),
            "number_format": cell.number_format,
            "protection": copy(cell.protection),
            "comment": copy(cell.comment),
            "hyperlink": copy(cell.hyperlink),
        })
    return {"cells": cells, "height": ws.row_dimensions[row].height}

def _restore_dashboard_row_snapshot(ws, row: int, snapshot: Dict[str, Any]) -> None:
    ws.row_dimensions[row].height = snapshot.get("height")
    for col, state in enumerate(snapshot["cells"], start=1):
        cell = ws.cell(row, col)
        cell.value = state["value"]
        cell.font = copy(state["font"])
        cell.fill = copy(state["fill"])
        cell.border = copy(state["border"])
        cell.alignment = copy(state["alignment"])
        cell.number_format = state["number_format"]
        cell.protection = copy(state["protection"])
        cell.comment = copy(state["comment"])
        cell.hyperlink = copy(state["hyperlink"])

def _repair_dashboard_extreme_metric_order(ws, diagnostics: Dict[str, Any] | None = None) -> bool:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    positions = {}
    for row in range(1, ws.max_row + 1):
        label = str(ws.cell(row, 1).value or "").strip().lower()
        if label in {"max win %", "max loss %"}:
            positions[label] = row
    win_row = positions.get("max win %")
    loss_row = positions.get("max loss %")
    if not win_row or not loss_row or win_row < loss_row:
        return False
    if win_row != loss_row + 2:
        raise RuntimeError("Dashboard Max win % / Max loss % rows are not a movable adjacent pair.")
    if any(str(ws.cell(row + 1, 1).value or "").strip().lower() != "source" for row in (loss_row, win_row)):
        raise RuntimeError("Dashboard Max win % / Max loss % source rows are missing.")
    loss_pair = [_dashboard_row_snapshot(ws, loss_row), _dashboard_row_snapshot(ws, loss_row + 1)]
    win_pair = [_dashboard_row_snapshot(ws, win_row), _dashboard_row_snapshot(ws, win_row + 1)]
    for offset, snapshot in enumerate(win_pair + loss_pair):
        _restore_dashboard_row_snapshot(ws, loss_row + offset, snapshot)
    diagnostics["reordered_dashboard_extreme_metric_rows"] = True
    return True

def _repair_dashboard_core_layout(ws, diagnostics: Dict[str, Any] | None = None) -> None:
    _remove_dashboard_metric_pair(ws, "Max gain", diagnostics)
    _repair_dashboard_extreme_metric_order(ws, diagnostics)
    _apply_dashboard_requested_semantic_fills(ws)

def _insert_dashboard_rows_preserving_layout(ws, row_idx: int, amount: int, style_row: int) -> None:
    merged_ranges = [str(merged) for merged in ws.merged_cells.ranges]
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    shifted_merges = [_shift_dashboard_range_rows(cell_range, row_idx, amount) for cell_range in merged_ranges]

    shifted_cf = OrderedDict()
    for key, rules in list(getattr(ws.conditional_formatting, "_cf_rules", {}).items()):
        shifted_key = copy(key)
        shifted_key.sqref = " ".join(
            _shift_dashboard_range_rows(part, row_idx, amount) for part in str(key.sqref).split()
        )
        shifted_cf[shifted_key] = rules

    ws.insert_rows(row_idx, amount)
    for cell_range in shifted_merges:
        ws.merge_cells(cell_range)
    ws.conditional_formatting._cf_rules = shifted_cf

    source_height = ws.row_dimensions[style_row].height
    for target_row in range(row_idx, row_idx + amount):
        ws.row_dimensions[target_row].height = source_height
        for col in range(1, ws.max_column + 1):
            source = ws.cell(style_row, col)
            target = ws.cell(target_row, col)
            _copy_cell_style(source, target)
            target.value = None
            target.comment = None
            target.hyperlink = None


def _ensure_dashboard_move_duration_rows(ws, diagnostics: Dict[str, Any] | None = None) -> bool:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    market_cols: Dict[str, int] = {}
    for row in range(1, min(5, ws.max_row) + 1):
        tokens = {str(ws.cell(row, col).value or "").strip().lower(): col for col in range(1, min(8, ws.max_column) + 1)}
        candidate = {
            "overall": tokens.get("overall"),
            "fx": tokens.get("fx") or tokens.get("forex"),
            "crypto": tokens.get("crypto"),
        }
        if all(candidate.values()) and candidate["overall"] + 1 == candidate["fx"] and candidate["fx"] + 1 == candidate["crypto"]:
            market_cols = {key: int(value) for key, value in candidate.items() if value}
            break
    if not market_cols or market_cols["overall"] <= 1:
        return False

    label_col = market_cols["overall"] - 1
    aliases = {
        DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL.lower(): DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL,
        "move to break-even (dd:hh:mm:ss)": DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL,
        DASHBOARD_MOVE_TO_PROFIT_LABEL.lower(): DASHBOARD_MOVE_TO_PROFIT_LABEL,
    }

    def label_rows() -> Dict[str, int]:
        found: Dict[str, int] = {}
        for row in range(1, ws.max_row + 1):
            raw = str(ws.cell(row, label_col).value or "").strip().lower()
            canonical = aliases.get(raw)
            if canonical and canonical not in found:
                found[canonical] = row
        return found

    duration_row = next((
        row for row in range(1, ws.max_row + 1)
        if str(ws.cell(row, label_col).value or "").strip().lower() in {"avg duration", "avg duration (dd:hh:mm:ss)"}
    ), None)
    if duration_row is None:
        diagnostics.setdefault("missing_dashboard_metric_labels", []).append("Avg duration (DD:HH:MM:SS)")
        return False

    changed = False
    rows = label_rows()
    if DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL not in rows:
        insert_at = rows.get(DASHBOARD_MOVE_TO_PROFIT_LABEL, duration_row + 1)
        _insert_dashboard_rows_preserving_layout(ws, insert_at, 1, duration_row)
        ws.cell(insert_at, label_col).value = DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL
        diagnostics.setdefault("inserted_dashboard_metric_rows", []).append(DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL)
        changed = True

    rows = label_rows()
    if DASHBOARD_MOVE_TO_PROFIT_LABEL not in rows:
        break_even_row = rows[DASHBOARD_MOVE_TO_BREAK_EVEN_LABEL]
        insert_at = break_even_row + 1
        _insert_dashboard_rows_preserving_layout(ws, insert_at, 1, duration_row)
        ws.cell(insert_at, label_col).value = DASHBOARD_MOVE_TO_PROFIT_LABEL
        diagnostics.setdefault("inserted_dashboard_metric_rows", []).append(DASHBOARD_MOVE_TO_PROFIT_LABEL)
        changed = True

    return changed


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

def _find_instrument_leaders_table(ws) -> tuple[int | None, Dict[str, int], Dict[str, int], int]:
    anchors: List[tuple[int, int]] = []
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            if str(ws.cell(r, c).value or "").strip().lower() == "instrument leaders":
                anchors.append((r, c))
    if not anchors:
        return None, {}, {}, 1
    candidates: List[tuple[int, int, int, Dict[str, int], Dict[str, int]]] = []
    for ar, ac in anchors:
        header_row = None
        header_map: Dict[str, int] = {}
        for r in range(ar + 1, min(ws.max_row, ar + 12) + 1):
            wanted = ["metric", "symbol", "wins", "losses", "trades"]
            for c in range(ac, min(ws.max_column, ac + 8) - len(wanted) + 3):
                tokens = [str(ws.cell(r, c + offset).value or "").strip().lower() for offset in range(len(wanted))]
                if tokens != wanted:
                    continue
                header_row = r
                header_map = {token: c + offset for offset, token in enumerate(wanted)}
                break
            if header_row:
                break
        if not header_row:
            continue
        metric_rows: Dict[str, int] = {}
        for r in range(header_row + 1, min(ws.max_row, header_row + 24) + 1):
            label = str(ws.cell(r, header_map["metric"]).value or "").strip().lower()
            if label:
                metric_rows[label] = r
        candidates.append((ac, ar, header_row, header_map, metric_rows))
    if candidates:
        ac, ar, header_row, header_map, metric_rows = sorted(candidates, key=lambda t: (t[0], t[1]))[0]
        return header_row, header_map, metric_rows, ac
    first_anchor = sorted(anchors, key=lambda t: (t[1], t[0]))[0]
    return None, {}, {}, first_anchor[1]



def _copy_leader_row_cell_style(ws, source_row: int, target_row: int, header_map: Dict[str, int]) -> None:
    for col in header_map.values():
        src = ws.cell(source_row, col)
        dst = ws.cell(target_row, col)
        if src.has_style:
            dst._style = copy(src._style)
        if src.number_format:
            dst.number_format = src.number_format
        if src.alignment:
            dst.alignment = copy(src.alignment)
        if src.protection:
            dst.protection = copy(src.protection)


def _repair_missing_market_leader_counterpart_row(ws, metric_rows: Dict[str, int], header_map: Dict[str, int], label: str) -> int | None:
    wanted = str(label or "").strip().lower()
    if not wanted or wanted in metric_rows or not header_map:
        return metric_rows.get(wanted)
    parts = wanted.split()
    if len(parts) < 3 or parts[-2:] not in (["most", "wins"], ["most", "losses"]):
        return None
    market = " ".join(parts[:-2])
    counterpart_suffix = "losses" if parts[-1] == "wins" else "wins"
    counterpart = f"{market} most {counterpart_suffix}"
    counterpart_row = metric_rows.get(counterpart)
    if not counterpart_row:
        return None

    metric_col = header_map["metric"]
    table_cols = sorted(header_map.values())
    first_data_row = min(metric_rows.values()) if metric_rows else counterpart_row
    last_scan_row = max(max(metric_rows.values(), default=counterpart_row) + 8, counterpart_row + 1)
    for row in range(counterpart_row + 1, min(ws.max_row + 24, last_scan_row) + 1):
        if any(ws.cell(row, col).value not in (None, "") for col in table_cols):
            continue
        source_row = counterpart_row
        existing = [r for r in metric_rows.values() if first_data_row <= r < row]
        if existing:
            source_row = max(existing)
        _copy_leader_row_cell_style(ws, source_row, row, header_map)
        ws.cell(row, metric_col).value = label
        metric_rows[wanted] = row
        return row
    raise RuntimeError(f"Instrument leaders is missing required row '{label}' and no safe blank row is available to restore it.")

def _write_value_preserving_cell(ws, row: int, col: int, value: Any) -> bool:
    if _is_merged_non_anchor(ws, row, col):
        return False
    ws.cell(row, col).value = value
    return True

def _detect_calendar_month_columns(ws) -> Dict[int, int]:
    month_cols: Dict[int, int] = {}
    names = {calendar.month_name[i].lower(): i for i in range(1, 13)}
    for c in range(1, ws.max_column + 1):
        token = str(ws.cell(1, c).value or "").strip().lower()
        if token in names:
            month_cols[names[token]] = c
    return month_cols


def _ensure_pnl_calendar_freeze_panes(ws) -> None:
    month_cols = _detect_calendar_month_columns(ws)
    if month_cols and min(month_cols.values()) == 3:
        ws.freeze_panes = "C2"
        return
    names = {calendar.month_name[i].lower() for i in range(1, 13)}
    row_two_months = {
        c for c in range(1, ws.max_column + 1)
        if str(ws.cell(2, c).value or "").strip().lower() in names
    }
    row_one_has_month_headers = any(
        str(ws.cell(1, c).value or "").strip().lower().endswith(" p/l %")
        for c in range(1, ws.max_column + 1)
    )
    if row_one_has_month_headers and row_two_months and min(row_two_months) == 2:
        ws.freeze_panes = "B3"

def _update_pnl_calendar_preserving_layout(dst_ws, snapshot: Dict[str, Any], diagnostics: Dict[str, Any] | None = None) -> None:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    _ensure_pnl_calendar_freeze_panes(dst_ws)
    month_cols = _detect_calendar_month_columns(dst_ws)
    if not month_cols:
        return
    year_blocks: Dict[int, Tuple[int, int]] = {}
    for r in range(2, dst_ws.max_row + 1):
        yv = _as_float(dst_ws.cell(r, 1).value)
        if yv is None:
            continue
        y = int(yv)
        lbl = str(dst_ws.cell(r, 2).value or "").strip().lower()
        if lbl == "p/l %":
            trades_row = r + 1
            if str(dst_ws.cell(trades_row, 2).value or "").strip().lower() == "total trades":
                year_blocks[y] = (r, trades_row)
    monthly: Dict[Tuple[int, int], Dict[str, float]] = {}
    for row in (snapshot.get("items") or []):
        if not isinstance(row, dict) or _is_test_trade_value(row.get("is_test_trade")):
            continue
        if str(row.get("row_type") or "trade").strip().lower() != "trade":
            continue
        d = _as_date(row.get("close_time") or row.get("open_time"))
        pct = _as_float(row.get("result_pct"))
        if not d or pct is None:
            continue
        key = (d.year, d.month)
        acc = monthly.setdefault(key, {"pct": 0.0, "count": 0.0})
        acc["pct"] += float(pct) / 100.0
        acc["count"] += 1.0
    years_needed = sorted({y for (y, _m) in monthly.keys()})
    if years_needed and year_blocks:
        for y in years_needed:
            if y in year_blocks:
                continue
            last_year = max(year_blocks.keys())
            p_row, t_row = year_blocks[last_year]
            new_p, new_t = t_row + 1, t_row + 2
            if any(dst_ws.cell(rr, cc).value not in (None, "") for rr in (new_p, new_t) for cc in range(1, max(month_cols.values()) + 1)):
                raise RuntimeError(f"P&L Calendar append unsafe for missing year {y}.")
            dst_ws.merge_cells(start_row=new_p, start_column=1, end_row=new_t, end_column=1)
            dst_ws.cell(new_p, 1).value = y
            dst_ws.cell(new_p, 2).value = "P/L %"
            dst_ws.cell(new_t, 2).value = "Total Trades"
            for c in range(1, max(month_cols.values()) + 1):
                for rr, src_rr in ((new_p, p_row), (new_t, t_row)):
                    dst = dst_ws.cell(rr, c); src = dst_ws.cell(src_rr, c)
                    dst.number_format = src.number_format
                    dst.font = copy(src.font); dst.fill = copy(src.fill); dst.border = copy(src.border); dst.alignment = copy(src.alignment); dst.protection = copy(src.protection)
            year_blocks[y] = (new_p, new_t)
    for y, (p_row, t_row) in year_blocks.items():
        for m, c in month_cols.items():
            if not _is_merged_non_anchor(dst_ws, p_row, c):
                dst_ws.cell(p_row, c).value = None
            if not _is_merged_non_anchor(dst_ws, t_row, c):
                dst_ws.cell(t_row, c).value = None
    for (y, m), vals in monthly.items():
        block = year_blocks.get(y)
        if not block or m not in month_cols:
            continue
        p_row, t_row = block
        c = month_cols[m]
        if not _is_merged_non_anchor(dst_ws, p_row, c):
            dst_ws.cell(p_row, c).value = vals["pct"]
            dst_ws.cell(p_row, c).number_format = "0.00%"
        if not _is_merged_non_anchor(dst_ws, t_row, c):
            dst_ws.cell(t_row, c).value = int(vals["count"])
            dst_ws.cell(t_row, c).number_format = "0"

def _find_dashboard_table_headers(ws, section: Dict[str, int], *, scan_rows: int = 8) -> tuple[int | None, Dict[str, int]]:
    required = {"account", "balance", "currency"}
    header_row = None
    col_map: Dict[str, int] = {}
    start_row = max(1, section.get("start_row", 1))
    end_row = min(section.get("end_row", ws.max_row), start_row + max(1, scan_rows) - 1)
    for r in range(start_row, end_row + 1):
        row_map: Dict[str, int] = {}
        for c in range(section["start_col"], section["end_col"] + 1):
            h = str(ws.cell(r, c).value or "").strip().lower()
            if h == "account":
                row_map["account"] = c
            elif h == "balance":
                row_map["balance"] = c
            elif h == "currency":
                row_map["currency"] = c
            elif h in {"as of", "as_of"}:
                row_map["as_of"] = c
        if required.issubset(row_map.keys()):
            header_row = r
            col_map = row_map
            break
    return header_row, col_map

def _ensure_account_balance_row(ws, section: Dict[str, int], header_row: int, col_map: Dict[str, int], account_label: str) -> int:
    account_col = col_map["account"]
    wanted = _canonical_account_label(account_label)
    for r in range(header_row + 1, section["end_row"] + 1):
        lbl = _canonical_account_label(ws.cell(r, account_col).value)
        if lbl and lbl == wanted:
            return r
    for r in range(header_row + 1, section["end_row"] + 1):
        lbl = str(ws.cell(r, account_col).value or "").strip()
        if lbl:
            continue
        bal_blank = ws.cell(r, col_map["balance"]).value in (None, "")
        cur_blank = ws.cell(r, col_map["currency"]).value in (None, "")
        asof_blank = ("as_of" not in col_map) or (ws.cell(r, col_map["as_of"]).value in (None, ""))
        if bal_blank and cur_blank and asof_blank:
            return r

    if section["end_row"] < ws.max_row:
        raise RuntimeError(f"Account Balances section has no writable row for '{account_label}' without shifting dashboard layout.")

    row = section["end_row"] + 1
    template_row = section["end_row"] if section["end_row"] > header_row else header_row + 1
    for c in range(section["start_col"], section["end_col"] + 1):
        src = ws.cell(template_row, c)
        dst = ws.cell(row, c)
        dst.number_format = src.number_format
        dst.font = copy(src.font)
        dst.fill = copy(src.fill)
        dst.border = copy(src.border)
        dst.alignment = copy(src.alignment)
        dst.protection = copy(src.protection)
    section["end_row"] = row
    return row


def _clear_account_balance_row(ws, row: int, col_map: Dict[str, int]) -> None:
    ws.cell(row, col_map["account"]).value = None
    ws.cell(row, col_map["balance"]).value = None
    ws.cell(row, col_map["currency"]).value = None
    if "as_of" in col_map:
        ws.cell(row, col_map["as_of"]).value = None



def _repair_trade_log_row_ids_from_rows(ws, rows, diagnostics):
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    headers = _trade_log_header_map(ws)
    rid_col = headers.get('Row ID')
    if not rid_col:
        return
    start_row = _trade_log_data_start_row(ws)
    repaired=0
    for rr in range(start_row, ws.max_row+1):
        row_ctx = rows[rr-start_row] if rr-start_row < len(rows) else {}
        expected = str(row_ctx.get('id') or stable_row_id(row_ctx)).strip() if isinstance(row_ctx, dict) else ''
        if not expected:
            continue
        cell=ws.cell(rr, rid_col)
        if str(cell.value or '').strip()!=expected:
            cell.value=expected
            repaired+=1
    if repaired:
        diagnostics['repaired_trade_log_row_ids']=repaired

def read_master_journal_source(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Master Journal workbook not found: {path}")
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        ws = _get_all_trades_sheet(wb)
        header_map = _trade_log_header_map(ws)
        headers = list(header_map.keys())
        idx = {header: col - 1 for header, col in header_map.items()}
        data_start_row = _trade_log_data_start_row(ws)
        required = {'Open Time','Close Time','Account','Symbol','Side'}
        if not required.issubset(set(idx.keys())):
            raise RuntimeError('Master Journal Trade Log headers are invalid.')
        items=[]; cashflow_ledger=defaultdict(list); diagnostics={'repaired_corrupted_row_ids': []}
        def _num(v):
            try:
                if v in (None, ""): return None
                return float(v)
            except Exception: return None
        i_stop = _alias_index(idx, 'Stop Loss', 'Stop Loss Price')
        i_tp = _alias_index(idx, 'Take Profit', 'Target Price', 'Target')
        i_stop_dist = _alias_index(idx, 'Stop Loss Distance', 'Stop Loss Distance %')
        i_target_dist = _alias_index(idx, 'Target Distance', 'Target Distance %')
        i_pnl = _alias_index(idx, 'Net P/L', 'Net Profit', 'Realized PnL')
        i_result_pct = _alias_index(idx, 'Result %', 'Profit %', 'P/L %', 'Result Percent')
        i_dur = _alias_index(idx, 'Trade Duration (DD:HH:MM:SS)', 'Trade Duration', 'Trade Duration Seconds', 'Duration')
        max_col = max(1, len(headers))
        max_row = ws.max_row or 1
        try:
            _min_col, _min_row, dim_max_col, dim_max_row = range_boundaries(ws.calculate_dimension())
            max_col = min(max_col, max(1, dim_max_col))
            max_row = max(1, dim_max_row)
        except Exception:
            pass
        for row_cells in ws.iter_rows(min_row=data_start_row, max_row=max_row, max_col=max_col):
            r = [cell.value for cell in row_cells]
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
            duration = _duration_ddhhmmss_cell_to_seconds(r[i_dur]) if i_dur is not None else None
            if duration is None and i_dur is not None:
                duration = _parse_duration_text(r[i_dur])
            if duration is None and row_type == "trade":
                ot = _as_datetime(open_time)
                ct = _as_datetime(close_time)
                if ot and ct:
                    sec = int((ct - ot).total_seconds())
                    duration = max(1, sec) if sec >= 0 else None
            account_u = account.upper()
            symbol_u = symbol.upper().replace('_','/').replace('-','/')
            if any(t in account_u for t in ('OANDA','PEPPERSTONE','FOREX',' FX')):
                asset_class = 'fx'
            elif any(t in account_u for t in ('BYBIT','BINANCE','COINSPOT')):
                asset_class = 'crypto'
            elif _is_likely_fx_pair(symbol_u):
                asset_class = 'fx'
            elif any(t in symbol_u for t in ('USDT','USDC','BTC','ETH','PERP')):
                asset_class = 'crypto'
            else:
                asset_class = ''
            balance_after = _num(r[idx.get('Balance After')]) if 'Balance After' in idx else None
            cashflow_amount = _num(r[idx.get('Cashflow Amount')]) if 'Cashflow Amount' in idx else (_num(r[i_pnl]) if i_pnl is not None else None)
            cashflow_new_balance = _num(r[idx.get('Cashflow New Balance')]) if 'Cashflow New Balance' in idx else None
            if row_type == 'cashflow' and cashflow_new_balance is None:
                cashflow_new_balance = balance_after
            computed_id = stable_row_id({'account':account,'symbol':symbol,'side':side,'open_time':open_time,'close_time':close_time,'qty':_num(r[idx.get('Qty')]) if 'Qty' in idx else None,'entry_price':_num(r[idx.get('Entry Price')]) if 'Entry Price' in idx else None,'exit_price':_num(r[idx.get('Exit Price')]) if 'Exit Price' in idx else None})
            monthly_like_id = row_id.startswith('monthly_aud_reval:')
            monthly_semantic = _is_monthly_aud_reval_semantic_row({'row_type': row_type, 'symbol': symbol, 'account': account})
            if row_id and (('PEPPERSTONE' in row_id.upper() or 'OANDA' in row_id.upper()) and ('BYBIT' in account_u or ('USDT' in symbol_u))):
                diagnostics['repaired_corrupted_row_ids'].append({'old_row_id': row_id, 'new_row_id': computed_id, 'reason': 'broker_account_mismatch'})
                row_id = computed_id
            elif row_id and monthly_like_id and (row_type != 'monthly_aud_reval' or not monthly_semantic or not _monthly_aud_reval_row_id_month(row_id)):
                diagnostics['repaired_corrupted_row_ids'].append({'old_row_id': row_id, 'new_row_id': computed_id, 'reason': 'invalid_monthly_aud_reval_row_id', 'row_type': row_type, 'symbol': symbol, 'account': account})
                row_id = computed_id
            currency = str(r[idx.get('Currency')] or '').strip() if 'Currency' in idx else ''
            item={'id': row_id or computed_id, 'row_type':row_type,'account':account,'symbol':symbol,'side':side,'open_time':open_time,'close_time':close_time,'qty':_num(r[idx.get('Qty')]) if 'Qty' in idx else None,'entry_price':_num(r[idx.get('Entry Price')]) if 'Entry Price' in idx else None,'exit_price':_num(r[idx.get('Exit Price')]) if 'Exit Price' in idx else None,'stop_loss':_num(r[i_stop]) if i_stop is not None else None,'take_profit':_num(r[i_tp]) if i_tp is not None else None,'stop_loss_distance_pct':_normalize_pct_distance_cell(r[i_stop_dist], row_cells[i_stop_dist].number_format) if i_stop_dist is not None and i_stop_dist < len(row_cells) else None,'target_distance_pct':_normalize_pct_distance_cell(r[i_target_dist], row_cells[i_target_dist].number_format) if i_target_dist is not None and i_target_dist < len(row_cells) else None,'commission':_num(r[idx.get('Commission')]) if 'Commission' in idx else None,'net_profit':_num(r[i_pnl]) if i_pnl is not None else None,'result_pct':_excel_fraction_to_pct_points(r[i_result_pct]) if i_result_pct is not None else None,'r_multiple':_num(r[idx.get('R-Multiple')]) if 'R-Multiple' in idx else None,'balance_after_trade':balance_after,'balance_after_trade_source':'master_journal','trade_duration_seconds':duration,'is_test_trade':str(r[idx.get('Test')]).strip().lower() in {'yes','y','true','1'} if 'Test' in idx else False,'setup':r[idx.get('Setup',17)] if 'Setup' in idx else '','timeframe':r[idx.get('Timeframe',18)] if 'Timeframe' in idx else '','breakeven':r[idx.get('Breakeven',19)] if 'Breakeven' in idx else '','notes':r[idx.get('Notes',20)] if 'Notes' in idx else '','cashflow_amount':cashflow_amount,'cashflow_new_balance':cashflow_new_balance,'currency':currency, 'asset_class': asset_class, 'source':'master_journal'}
            if TRADE_NUMBER_HEADER in idx:
                trade_number_value = r[idx[TRADE_NUMBER_HEADER]] if idx[TRADE_NUMBER_HEADER] < len(r) else ""
                item["trade_number"] = "" if trade_number_value is None else str(trade_number_value).strip()
            for header, field in TRADE_LOG_MANUAL_FIELD_MAP.items():
                if header in idx:
                    field_index = idx[header]
                    raw_value = r[field_index] if field_index < len(r) else ''
                    if field in {"move_to_break_even_duration", "move_to_profit_duration"} and raw_value not in (None, ""):
                        number_format = str(row_cells[field_index].number_format or "")
                        parsed_duration = _duration_ddhhmmss_cell_to_seconds(raw_value) if r"\:" in number_format else _parse_duration_text(raw_value)
                        item[field] = parsed_duration if parsed_duration is not None else raw_value
                    else:
                        item[field] = raw_value
            if "close_stopout" not in item and "Stop Out" in idx:
                item["close_stopout"] = r[idx["Stop Out"]] if idx["Stop Out"] < len(r) else ''
            if row_type == "monthly_aud_reval":
                monthly_currency = (currency or str(item.get("result_currency") or "").strip() or "AUD").upper()
                item["result_cash"] = _num(r[i_pnl]) if i_pnl is not None else None
                item["result_currency"] = monthly_currency
                item["account_label"] = account
                refs: Dict[str, Any] = {}
                month_source = close_time or open_time
                month_dt = _as_datetime(month_source)
                if month_dt is not None:
                    refs["period_month"] = month_dt.strftime("%Y-%m")
                item["raw_refs"] = refs
                item["source"] = str(item.get("source") or "").strip() or "bybit_monthly_aud_reval"
                item.pop("net_profit", None)
                item.pop("realized_pnl", None)
            items.append(item)
            if row_type=='cashflow':
                cashflow_ledger[account].append({'account':account,'date':item['close_time'] or item['open_time'],'amount':cashflow_amount,'new_balance':cashflow_new_balance,'currency':str(r[idx.get('Currency')] or '').strip() if 'Currency' in idx else '','reason':item.get('notes') or '', 'side':side})
        return {'items':items,'cashflow_ledger':dict(cashflow_ledger),'diagnostics':diagnostics}
    finally:
        wb.close()

def update_master_journal_workbook_data_only(path: Path, snapshot: Dict[str, Any], expected_survivor_row_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    wb = load_workbook(path)
    diagnostics: Dict[str, Any] = {"missing_accounts": [], "updated_cells": 0}
    try:
        content_before = _workbook_content_snapshot(wb)
        _migrate_legacy_trade_log_sheet_name(wb, diagnostics)
        _remove_legacy_trade_meta_sheet(wb, diagnostics)
        _repair_legacy_instrument_averages_freeze_pane(wb, diagnostics)
        def _repair_trade_log_unknown_currency_formats(ws, rows: List[Dict[str, Any]], diagnostics: Dict[str, Any] | None = None) -> None:
            diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
            repaired = 0
            headers = _trade_log_header_map(ws)
            repair_cols = (
                (headers.get("Commission"), "commission"),
                (headers.get("Net P/L"), "net_pnl"),
            )
            start_row = _trade_log_data_start_row(ws)
            for rr in range(start_row, ws.max_row + 1):
                row_ctx = rows[rr - start_row] if rr - start_row < len(rows) else {}
                for col, field in repair_cols:
                    if not col:
                        continue
                    cell = ws.cell(rr, col)
                    fmt = str(cell.number_format or "")
                    if "UNKNOWN" not in fmt:
                        continue
                    ccy = _infer_trade_log_currency(row_ctx, field=field)
                    if not ccy:
                        continue
                    cell.number_format = _currency_number_format(ccy)
                    repaired += 1
            if repaired:
                diagnostics["repaired_trade_log_unknown_currency_formats"] = True
                diagnostics["repaired_trade_log_unknown_currency_format_cells"] = repaired
        trade_log_ws = _get_trade_log_sheet(wb, allow_legacy=False)
        _ensure_trade_log_schema(trade_log_ws, diagnostics)
        if "Dashboard" not in wb.sheetnames:
            raise RuntimeError("Master Journal missing Dashboard sheet.")
        dash = wb["Dashboard"]
        _repair_dashboard_core_layout(dash, diagnostics)
        _ensure_dashboard_move_duration_rows(dash, diagnostics)

        stats = snapshot.get("stats") or {}
        rows = [
            _repair_or_flag_zero_trade_qty(dict(r)) for r in (snapshot.get("items") or [])
            if isinstance(r, dict) and str(r.get("row_type") or "trade") in {"trade", "monthly_aud_reval", "cashflow"}
        ]
        existing_manual_overrides = read_master_journal_manual_overrides(path) if path.exists() else {}
        if existing_manual_overrides:
            for row in rows:
                rid = stable_row_id(row)
                overrides = existing_manual_overrides.get(rid) or existing_manual_overrides.get(str(row.get("id") or "").strip())
                if overrides:
                    row.update(overrides)
            snapshot = dict(snapshot)
            snapshot["items"] = rows
        _ensure_report_sheets(wb, snapshot, diagnostics)
        before = _snapshot_invariants(wb)
        groups = stats.get("groups") or {}
        by_market = groups.get("by_market") or {}
        risk = groups.get("risk_expectancy") or {}
        leaders = groups.get("leaders") or {}
        totals = stats.get("totals") or {}
        move_duration_metrics = _trade_move_duration_metrics(rows)

        anchors = _find_anchor_sections(dash, ["Account Balances", "Instrument leaders", "Overall", "Winners", "Losers", "Drawdown", "FX", "Crypto"], optional=["Duration"])

        def _format_metric_value(value: Any, metric_type: str = "raw"):
            if value is None:
                return None
            if metric_type == "pct":
                pct = _as_float(value)
                if pct is None:
                    return None
                return _pct_points_to_excel_fraction(pct)
            if metric_type == "r":
                r_value = _as_float(value)
                return r_value if r_value is not None else value
            if metric_type == "duration":
                return _fmt_duration_full(value)
            if metric_type == "count":
                f = _as_float(value)
                return int(f) if f is not None else value
            if metric_type == "source":
                return _fmt_detail_src(value)
            return value

        def _set_dashboard_metric_number_format(cell, metric_type: str) -> None:
            if metric_type == "pct":
                cell.number_format = "0.00%"
            elif metric_type == "r":
                cell.number_format = '0.000"R"'
            elif metric_type == "count":
                cell.number_format = "0"
            elif metric_type == "duration":
                cell.number_format = r'00\:00\:00\:00'

        def _apply_dashboard_metric_semantic_style(cell, semantic: str | None) -> None:
            value = _as_float(cell.value)
            if value is None or value == 0 or not semantic:
                return
            if semantic == "profit_loss":
                _apply_full_cell_semantic_fill(cell, "profit" if value > 0 else "loss")
            elif semantic in {"loss", "drawdown"}:
                _apply_full_cell_semantic_fill(cell, "loss")
            elif semantic == "profit":
                _apply_full_cell_semantic_fill(cell, "profit")

        def _write_dashboard_metric_cell(row: int, col: int, value: Any, metric_type: str = "raw", semantic: str | None = None) -> bool:
            if value is None or _is_light_grey_no_metric_cell(dash.cell(row, col)):
                return False
            if _write_value_preserving_cell(dash, row, col, value):
                diagnostics["updated_cells"] += 1
                cell = dash.cell(row, col)
                _set_dashboard_metric_number_format(cell, metric_type)
                _apply_dashboard_metric_semantic_style(cell, semantic)
                return True
            return False

        def write_metric(section: str, label: str, value: Any, metric_type: str = "raw"):
            out = _format_metric_value(value, metric_type)
            if out is None:
                return
            pos = _find_label_in_section(dash, label, anchors[section])
            if not pos:
                return
            _write_dashboard_metric_cell(pos[0], pos[1]+1, out, metric_type)

        def _main_dashboard_market_columns() -> Dict[str, int]:
            cols: Dict[str, int] = {}
            for r in range(1, min(5, dash.max_row) + 1):
                for c in range(1, min(8, dash.max_column) + 1):
                    token = str(dash.cell(r, c).value or "").strip().lower()
                    if token == "overall":
                        cols["overall"] = c
                    elif token in {"fx", "forex"}:
                        cols["fx"] = c
                    elif token == "crypto":
                        cols["crypto"] = c
                if {"overall", "fx", "crypto"}.issubset(cols):
                    break
            return cols

        def write_market_metric(section: str, label: str, values_by_market: Dict[str, Any], metric_type: str = "raw", semantic: str | None = None):
            pos = _find_label_in_section(dash, label, anchors[section])
            if not pos:
                return
            market_cols = _main_dashboard_market_columns()
            missing_markets = []
            for market, col in market_cols.items():
                out = _format_metric_value(values_by_market.get(market), metric_type)
                if out is None:
                    if section == "Drawdown" and market in {"fx", "crypto"}:
                        missing_markets.append(market)
                    continue
                _write_dashboard_metric_cell(pos[0], col, out, metric_type, semantic)
            if missing_markets:
                diagnostics.setdefault("missing_market_drawdown_values", []).extend(
                    f"{market} {label}" for market in missing_markets
                )

        def _dashboard_label_rows_by_col(label_col: int = 1) -> Dict[str, List[int]]:
            rows_by_label: Dict[str, List[int]] = defaultdict(list)
            for r in range(1, dash.max_row + 1):
                label = str(dash.cell(r, label_col).value or "").strip().lower()
                if label:
                    rows_by_label[label].append(r)
            return rows_by_label

        def write_horizontal_core_market_metrics() -> None:
            market_cols: Dict[str, int] = {}
            for r in range(1, min(5, dash.max_row) + 1):
                row_tokens = {str(dash.cell(r, c).value or "").strip().lower(): c for c in range(1, min(8, dash.max_column) + 1)}
                candidate = {
                    "overall": row_tokens.get("overall"),
                    "fx": row_tokens.get("fx") or row_tokens.get("forex"),
                    "crypto": row_tokens.get("crypto"),
                }
                if all(candidate.values()) and candidate["overall"] + 1 == candidate["fx"] and candidate["fx"] + 1 == candidate["crypto"] and candidate["overall"] > 1:
                    market_cols = {k: int(v) for k, v in candidate.items() if v}
                    break
            if not {"overall", "fx", "crypto"}.issubset(market_cols):
                return
            label_rows = _dashboard_label_rows_by_col(1)
            metric_specs = [
                (["Trades"], "trades", "count", None),
                (["Wins"], "wins", "count", None),
                (["Losses"], "losses", "count", None),
                (["Break-even"], "break_even", "count", None),
                (["Test"], "test_trades", "count", None),
                (["Win rate"], "win_rate_pct", "pct", None),
                (["Net P/L"], "market_return_pct", "pct", "profit_loss"),
                (["Gross gain"], "gross_gain_return_pct", "pct", "profit_loss"),
                (["Gross loss"], "gross_loss_return_pct", "pct", "loss"),
                (["Best Win Streak", "Winning Streak"], "winning_streak", "count", None),
                (["Worst Losing Streak", "Losing Streak"], "losing_streak", "count", None),
                (["Avg result %"], "avg_result_pct", "pct", "profit_loss"),
                (["Avg R"], "avg_r_multiple", "r", "profit_loss"),
                (["Avg stop %"], "avg_stop_pct", "pct", None),
                (["Avg target %"], "avg_target_pct", "pct", None),
                (["Max target %"], "max_target_pct", "pct", None),
                (["Avg duration", "Avg duration (DD:HH:MM:SS)"], "avg_duration_seconds", "duration", None),
                (["Move to Break Even (DD:HH:MM:SS)", "Move to Break-Even (DD:HH:MM:SS)"], "move_to_break_even_duration_seconds", "duration", None),
                (["Move to Profit (DD:HH:MM:SS)"], "move_to_profit_duration_seconds", "duration", None),
                (["Max loss %"], "min_result_pct", "pct", "loss"),
                (["Max win %"], "max_result_pct", "pct", None),
                (["Max R loss"], "min_r_multiple", "r", "loss"),
                (["Max R win"], "max_r_multiple", "r", None),
            ]
            percentage_totals = _result_percentage_totals_by_market(rows, snapshot.get("balances") or stats.get("balances") or [])
            buckets = {
                market: {**percentage_totals[market], **dict(bucket or {}), **move_duration_metrics[market]}
                for market, bucket in {
                    "overall": by_market.get("overall") or totals,
                    "fx": by_market.get("fx") or {},
                    "crypto": by_market.get("crypto") or {},
                }.items()
            }
            for labels, key, metric_type, semantic in metric_specs:
                rows_for_metric: List[int] = []
                for label in labels:
                    rows_for_metric.extend(label_rows.get(label.lower(), []))
                if not rows_for_metric:
                    diagnostics.setdefault("missing_dashboard_metric_labels", []).append(" / ".join(labels))
                    continue
                for row_num in sorted(set(rows_for_metric)):
                    for market, col in market_cols.items():
                        value = _format_metric_value((buckets.get(market) or {}).get(key), metric_type)
                        if value is None:
                            diagnostics.setdefault("missing_dashboard_metric_values", []).append(f"{market} {labels[0]}")
                            continue
                        _write_dashboard_metric_cell(row_num, col, value, metric_type, semantic)

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

        write_horizontal_core_market_metrics()

        section_maps = {
            "Overall": by_market.get("overall") or totals,
            "FX": by_market.get("fx") or {},
            "Crypto": by_market.get("crypto") or {},
        }
        for section, bucket in section_maps.items():
            write_metric(section, "Trades", bucket.get("trades"), "count")
            write_metric(section, "Wins", bucket.get("wins"), "count")
            write_metric(section, "Losses", bucket.get("losses"), "count")
            write_metric(section, "Best Win Streak", bucket.get("winning_streak"), "count")
            write_metric(section, "Winning Streak", bucket.get("winning_streak"), "count")
            write_metric(section, "Worst Losing Streak", bucket.get("losing_streak"), "count")
            write_metric(section, "Losing Streak", bucket.get("losing_streak"), "count")
            write_metric(section, "Break-even", bucket.get("break_even"), "count")
            write_metric(section, "Test", bucket.get("test_trades"), "count")
            write_metric(section, "Win rate", bucket.get("win_rate_pct"), "pct")
            write_metric(section, "Net P/L", bucket.get("net_profit_total"))
            write_metric(section, "Avg result %", bucket.get("avg_result_pct"), "pct")
            write_metric(section, "Avg R", bucket.get("avg_r_multiple"), "r")
            write_metric(section, "Gross gain", bucket.get("gross_gain"))
            write_metric(section, "Gross loss", bucket.get("gross_loss"))
            write_metric(section, "Max loss %", bucket.get("min_result_pct"), "pct")
            write_metric(section, "Max win %", bucket.get("max_result_pct"), "pct")
            write_metric(section, "Max R loss", bucket.get("min_r_multiple"))
            write_metric(section, "Max R win", bucket.get("max_r_multiple"))
            write_metric(section, "Avg stop %", bucket.get("avg_stop_pct"), "pct")
            write_metric(section, "Avg target %", bucket.get("avg_target_pct"), "pct")
            write_metric(section, "Max target %", bucket.get("max_target_pct"), "pct")
            write_metric(section, "Avg duration", bucket.get("avg_duration_seconds"), "duration")
            msrc = bucket.get("metric_sources") or {}
            write_source_below(section, "Max loss %", msrc.get("min_result_pct"))
            write_source_below(section, "Max win %", msrc.get("max_result_pct"))
            write_source_below(section, "Max R loss", msrc.get("min_r_multiple"))
            write_source_below(section, "Max R win", msrc.get("max_r_multiple"))


        risk_by_market = risk.get("by_market") if isinstance(risk.get("by_market"), dict) else {}
        def _risk_market_values(key: str) -> Dict[str, Any]:
            return {
                "overall": risk.get(key),
                "fx": (risk_by_market.get("fx") or {}).get(key),
                "crypto": (risk_by_market.get("crypto") or {}).get(key),
            }

        write_market_metric("Winners", "Avg result %", _risk_market_values("avg_result_pct_winners"), "pct", "profit_loss")
        write_market_metric("Winners", "Avg R", _risk_market_values("avg_r_multiple_winners"), "r", "profit_loss")
        write_market_metric("Winners", "Avg stop %", _risk_market_values("avg_stop_pct_winners"), "pct")
        write_market_metric("Winners", "Avg target %", _risk_market_values("avg_target_pct_winners"), "pct")
        write_market_metric("Losers", "Avg result %", _risk_market_values("avg_result_pct_losers"), "pct", "loss")
        write_market_metric("Losers", "Avg R", _risk_market_values("avg_r_multiple_losers"), "r", "loss")
        write_market_metric("Losers", "Avg stop %", _risk_market_values("avg_stop_pct_losers"), "pct")
        write_market_metric("Losers", "Avg target %", _risk_market_values("avg_target_pct_losers"), "pct")
        write_market_metric("Drawdown", "Max drawdown", {"overall": risk.get("max_drawdown_pct"), "fx": (by_market.get("fx") or {}).get("max_drawdown_pct"), "crypto": (by_market.get("crypto") or {}).get("max_drawdown_pct")}, "pct", "drawdown")
        write_market_metric("Drawdown", "Avg drawdown", {"overall": risk.get("avg_drawdown_pct"), "fx": (by_market.get("fx") or {}).get("avg_drawdown_pct"), "crypto": (by_market.get("crypto") or {}).get("avg_drawdown_pct")}, "pct", "drawdown")
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
        diagnostics.setdefault("skipped_optional_leader_rows", [])
        diagnostics.setdefault("leader_write_errors", [])
        diagnostics.setdefault("leader_payload_keys", [])
        _, leader_headers, metric_rows, _ = _find_instrument_leaders_table(dash)
        if not leader_headers:
            diagnostics["missing_leader_headers"].append("Metric/Symbol/Wins/Losses/Trades")
        else:
            for metric_label, key in LEADER_LABEL_TO_KEY.items():
                payload = leaders.get(key) or {}
                if not payload:
                    continue
                diagnostics["leader_payload_keys"].append(key)
                row_idx = metric_rows.get(metric_label)
                if not row_idx and metric_label.startswith(("fx ", "crypto ")):
                    row_idx = _repair_missing_market_leader_counterpart_row(dash, metric_rows, leader_headers, metric_label)
                    if row_idx:
                        diagnostics.setdefault("restored_leader_rows", []).append(metric_label)
                        diagnostics["updated_cells"] += 1
                if not row_idx:
                    diagnostics["skipped_optional_leader_rows"].append(metric_label)
                    continue
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

        balances = _canonicalize_and_dedupe_balances(snapshot.get("balances") or [])
        diagnostics.setdefault("non_numeric_balance_accounts", [])
        section = anchors["Account Balances"]
        header_row, col_map = _find_dashboard_table_headers(dash, section)
        if not header_row or "account" not in col_map or "balance" not in col_map or "currency" not in col_map:
            raise RuntimeError("Account Balances headers missing in section.")
        diagnostics.setdefault("stale_account_balance_rows_cleared", 0)
        account_col = col_map["account"]
        existing_rows_by_canonical: Dict[str, List[int]] = {}
        existing_rows_by_raw: Dict[str, List[int]] = {}
        for rr in range(header_row + 1, section["end_row"] + 1):
            raw_label = str(dash.cell(rr, account_col).value or "").strip()
            if not raw_label:
                continue
            canonical_label = _canonical_account_label(raw_label)
            existing_rows_by_raw.setdefault(raw_label, []).append(rr)
            existing_rows_by_canonical.setdefault(canonical_label, []).append(rr)
        account_balance_targets: Dict[str, Dict[str, Any]] = {}
        for b in balances:
            label = _canonical_account_label(b.get("account_label") or b.get("account"))
            if not label:
                continue
            bal_num = _as_float(b.get("balance"))
            if bal_num is None:
                diagnostics["non_numeric_balance_accounts"].append(label)
                continue
            if label == "BYBIT":
                bybit_rows = existing_rows_by_canonical.get("BYBIT", [])
                bybit_live_rows = [rr for rr in range(header_row + 1, section["end_row"] + 1) if _canonical_account_label(dash.cell(rr, account_col).value) == "BYBIT" and str(dash.cell(rr, account_col).value or "").strip() != "BYBIT"]
                if not bybit_rows and bybit_live_rows:
                    target = bybit_live_rows[0]
                    if _write_value_preserving_cell(dash, target, col_map["account"], "BYBIT"):
                        diagnostics["updated_cells"] += 1
                    existing_rows_by_canonical.setdefault("BYBIT", []).append(target)
                    bybit_rows = existing_rows_by_canonical["BYBIT"]
            try:
                row = _ensure_account_balance_row(dash, section, header_row, col_map, label)
            except Exception as exc:
                diagnostics["missing_accounts"].append(label)
                diagnostics.setdefault("account_balance_write_errors", []).append(str(exc))
                continue
            if _write_value_preserving_cell(dash, row, col_map["account"], label):
                diagnostics["updated_cells"] += 1
            if _write_value_preserving_cell(dash, row, col_map["balance"], bal_num):
                diagnostics["updated_cells"] += 1
            curr = str(b.get("currency") or "").strip()
            existing_fmt = str(dash.cell(row, col_map["balance"]).number_format or "")
            if curr:
                if not existing_fmt or existing_fmt == "General":
                    dash.cell(row, col_map["balance"]).number_format = _currency_number_format(curr)
                elif _is_crypto_currency(curr) and "#" not in existing_fmt:
                    dash.cell(row, col_map["balance"]).number_format = _currency_number_format(curr, force_decimals=10)
                elif (not _is_crypto_currency(curr)) and "#" not in existing_fmt:
                    dash.cell(row, col_map["balance"]).number_format = _currency_number_format(curr, force_decimals=2)
            if curr:
                if _write_value_preserving_cell(dash, row, col_map["currency"], curr):
                    diagnostics["updated_cells"] += 1
            account_balance_targets[label] = {"row": row, "balance": bal_num, "currency": curr}
            if "as_of" in col_map:
                as_of = str(b.get("as_of") or "").strip()
                if as_of:
                    if _write_value_preserving_cell(dash, row, col_map["as_of"], as_of):
                        diagnostics["updated_cells"] += 1
            if label == "BYBIT":
                stale_rows = []
                for rr in range(header_row + 1, section["end_row"] + 1):
                    raw_here = str(dash.cell(rr, account_col).value or "").strip()
                    if _canonical_account_label(raw_here) == "BYBIT" and raw_here != "BYBIT" and rr != row:
                        stale_rows.append(rr)
                for stale_row in stale_rows:
                    _clear_account_balance_row(dash, stale_row, col_map)
                    diagnostics["stale_account_balance_rows_cleared"] += 1

        diagnostics.setdefault("account_balance_verified", [])
        diagnostics.setdefault("account_balance_mismatches", [])
        for label, target in account_balance_targets.items():
            row = int(target["row"])
            expected = _as_float(target.get("balance"))
            actual = _as_float(dash.cell(row, col_map["balance"]).value)
            if expected is not None and actual is not None and abs(actual - expected) <= max(1e-9, abs(expected) * 1e-12):
                diagnostics["account_balance_verified"].append(label)
            else:
                diagnostics["account_balance_mismatches"].append({"account": label, "expected": expected, "actual": actual, "row": row})
        if diagnostics["account_balance_mismatches"]:
            return {"ok": False, "error": "dashboard_account_balance_verification_failed", "diagnostics": diagnostics}

        zero_qty = _collect_zero_qty_validation(rows)
        diagnostics.update(zero_qty)
        if zero_qty["crypto_zero_qty_unrepaired"]:
            sample = ", ".join(str(x.get("id") or x.get("symbol") or "?") for x in zero_qty["crypto_zero_qty_unrepaired"][:5])
            return {"ok": False, "error": f"Unrepaired crypto zero-quantity trade rows detected: {sample}", "diagnostics": diagnostics}

        _apply_dashboard_requested_semantic_fills(dash)

        tmp = path.with_suffix(".update.tmp.xlsx")
        build_master_journal_workbook(snapshot, tmp)
        gen = load_workbook(tmp, data_only=False)
        try:
            def _copy_data_rows(src_ws, dst_ws, start_row: int, *, force_all_columns: bool = False):
                if force_all_columns:
                    src_map = _trade_log_header_map(src_ws)
                    dst_map = _trade_log_header_map(dst_ws)
                    missing_src = [header for header in TRADE_LOG_HEADERS if header not in src_map]
                    missing_dst = [header for header in TRADE_LOG_HEADERS if header not in dst_map]
                    if missing_src or missing_dst:
                        raise RuntimeError(
                            "Trade Log logical headers do not match expected template: "
                            f"missing_source={missing_src!r}, missing_destination={missing_dst!r}."
                        )
                    header_pairs = [(src_map[header], dst_map[header], header) for header in TRADE_LOG_HEADERS]
                    max_col = len(TRADE_LOG_HEADERS)
                else:
                    src_headers = [str(src_ws.cell(1, c).value or "").strip() for c in range(1, src_ws.max_column + 1)]
                    dst_headers = [str(dst_ws.cell(1, c).value or "").strip() for c in range(1, dst_ws.max_column + 1)]
                    src_map = {header: idx + 1 for idx, header in enumerate(src_headers) if header}
                    dst_map = {header: idx + 1 for idx, header in enumerate(dst_headers) if header}
                    max_col = min(src_ws.max_column, dst_ws.max_column)
                    header_pairs = [
                        (src_map[header], dst_col, header)
                        for header, dst_col in dst_map.items()
                        if header in src_map and dst_col <= max_col
                    ]
                clear_max_col = max(dst_ws.max_column, max_col) if force_all_columns else max_col
                for row in range(start_row, dst_ws.max_row + 1):
                    for col in range(1, clear_max_col + 1):
                        if _is_merged_non_anchor(dst_ws, row, col):
                            continue
                        cell = dst_ws.cell(row, col)
                        cell.value = None
                        cell.comment = None
                        cell.hyperlink = None
                src_start_row = _trade_log_data_start_row(src_ws) if force_all_columns else start_row
                dst_row = start_row
                for src_row in range(src_start_row, src_ws.max_row + 1):
                    if force_all_columns and not any(src_ws.cell(src_row, col).value not in (None, "") for col in range(1, max_col + 1)):
                        continue
                    for src_col, dst_col, _header in header_pairs:
                        if _is_merged_non_anchor(dst_ws, dst_row, dst_col):
                            continue
                        src_cell = src_ws.cell(src_row, src_col)
                        dst_cell = dst_ws.cell(dst_row, dst_col)
                        dst_cell.value = src_cell.value
                        dst_cell.number_format = src_cell.number_format
                        dst_cell.comment = copy(src_cell.comment) if src_cell.comment else None
                        dst_cell.hyperlink = copy(src_cell.hyperlink) if src_cell.hyperlink else None
                    dst_row += 1
                last_row = max(start_row - 1, dst_row - 1)
                if force_all_columns:
                    _set_trade_log_auto_filter(dst_ws)
                    _hide_trade_log_row_id(dst_ws)
                    _apply_trade_log_dropdown_validations(dst_ws)
                elif dst_ws.auto_filter and dst_ws.auto_filter.ref:
                    last_col_letter = get_column_letter(max_col)
                    dst_ws.auto_filter.ref = f"A1:{last_col_letter}{max(1,last_row)}"


            gen_trade_log = _get_all_trades_sheet(gen, allow_legacy=False)
            live_trade_log = _get_all_trades_sheet(wb, allow_legacy=False)
            _copy_data_rows(gen_trade_log, live_trade_log, TRADE_LOG_DATA_START_ROW, force_all_columns=True)
            _repair_trade_log_row_ids_from_rows(live_trade_log, rows, diagnostics)
            if expected_survivor_row_ids:
                header_map = _trade_log_header_map(live_trade_log)
                ridx = header_map.get("Row ID")
                if not ridx:
                    return {
                        "ok": False,
                        "error": "workbook_row_survivor_verification_failed",
                        "missing_row_ids": sorted([rid for rid in expected_survivor_row_ids if rid]),
                        "reason": "missing_row_id_header",
                        "diagnostics": diagnostics,
                    }
                present = {str(live_trade_log.cell(rr, ridx).value or "").strip() for rr in range(_trade_log_data_start_row(live_trade_log), live_trade_log.max_row + 1)}
                missing = sorted([rid for rid in expected_survivor_row_ids if rid and rid not in present])
                if missing:
                    return {"ok": False, "error": "workbook_row_survivor_verification_failed", "missing_row_ids": missing, "diagnostics": diagnostics}
            _repair_trade_log_unknown_currency_formats(live_trade_log, rows, diagnostics)
            _apply_trade_log_win_loss_row_formatting(live_trade_log)
            _apply_trade_log_win_loss_direct_row_fills(live_trade_log)

            def _copy_instrument_rows_header_aware(src_ws, dst_ws, start_row: int = 2):
                aliases = {
                    'trades': ['trades','total trades','total_trades'],
                    'wins':['wins'],'losses':['losses'],'break-even':['break-even','break even'],
                    'longs':['longs','long trades'],'shorts':['shorts','short trades'],
                    'long wins':['long wins'],'long losses':['long losses'],'long break-even':['long break-even'],
                    'short wins':['short wins'],'short losses':['short losses'],'short break-even':['short break-even'],
                    'net p/l %':['net p/l %'],'avg p/l %':['avg p/l %'],'win rate %':['win rate %'],
                    'avg stop % (w)':['avg stop % (w)'],'avg stop % (l)':['avg stop % (l)'],'avg target % (w)':['avg target % (w)'],'avg target % (l)':['avg target % (l)'],
                    'shortest':['shortest duration (dd:hh:mm:ss)','shortest (dd:hh:mm:ss)'],
                    'avgdur':['avg duration (dd:hh:mm:ss)'],
                    'longest':['longest duration (dd:hh:mm:ss)','longest (dd:hh:mm:ss)'],
                    'symbol':['symbol'],'class':['class']
                }
                src_headers=[str(c.value or '').strip().lower() for c in src_ws[1]]
                dst_headers=[str(c.value or '').strip().lower() for c in dst_ws[1]]
                def find_col(headers, keys):
                    for k in keys:
                        if k in headers:
                            return headers.index(k)+1
                    return None
                pairs=[]
                for _,keys in aliases.items():
                    sc=find_col(src_headers, keys); dc=find_col(dst_headers, keys)
                    if sc and dc:
                        pairs.append((sc,dc))
                if not pairs:
                    return
                max_dst_row = max(dst_ws.max_row, src_ws.max_row)
                for r in range(start_row, max_dst_row + 1):
                    for _,dc in pairs:
                        dst_ws.cell(r,dc).value=None
                for r in range(start_row, src_ws.max_row+1):
                    for sc,dc in pairs:
                        s=src_ws.cell(r,sc); d=dst_ws.cell(r,dc)
                        d.value=s.value; d.number_format=s.number_format
                last_col=dst_ws.max_column
                dst_ws.auto_filter.ref=f"A1:{get_column_letter(last_col)}{max(1,max_dst_row)}"
            if "Instrument Averages" in wb.sheetnames and "Instrument Averages" in gen.sheetnames:
                instrument_ws = wb["Instrument Averages"]
                _copy_instrument_rows_header_aware(gen["Instrument Averages"], instrument_ws, 2)
                instrument_ws.freeze_panes = "B2"
                _apply_instrument_averages_semantic_fills(instrument_ws)
            if "P&L Calendar" in wb.sheetnames and "P&L Calendar" in gen.sheetnames:
                cal_ws = wb["P&L Calendar"]
                if _detect_calendar_month_columns(cal_ws):
                    _update_pnl_calendar_preserving_layout(cal_ws, snapshot, diagnostics)
                else:
                    _copy_data_rows(gen["P&L Calendar"], cal_ws, 3)
                _apply_pnl_calendar_profit_loss_formatting(cal_ws)
                _ensure_pnl_calendar_freeze_panes(cal_ws)
        finally:
            gen.close()
            tmp.unlink(missing_ok=True)

        trade_log = _get_all_trades_sheet(wb, allow_legacy=False)
        content_after = _workbook_content_snapshot(wb)
        _assert_workbook_content_not_wiped(
            content_before,
            content_after,
            migration_performed=bool(diagnostics.get("migrated_trade_log_schema")),
        )
        _assert_filter_covers_data(trade_log, sheet_name="Trade Log", header_row=TRADE_LOG_FILTER_HEADER_ROW, required_headers=["Open Time", "Close Time", "Row ID"], header_map=_trade_log_header_map(trade_log))
        _assert_filter_covers_data(wb["Instrument Averages"], sheet_name="Instrument Averages", header_row=1, required_headers=["Symbol", "Trades"])

        after = _snapshot_invariants(wb)
        _assert_invariants_unchanged(before, after)
        candidate = path.with_suffix(".update-candidate.tmp.xlsx")
        wb.save(candidate)
        return {"ok": True, "path": str(path), "candidate_path": str(candidate), "diagnostics": diagnostics}
    finally:
        wb.close()

def refresh_master_journal_derived_sheets(path: Path, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Master Journal workbook not found: {path}")
    wb = load_workbook(path)
    try:
        all_trades = _get_all_trades_sheet(wb, allow_legacy=False)
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
            _ensure_report_sheets(wb, snapshot)
        finally:
            gen.close()
            tmp.unlink(missing_ok=True)
        tmp_out = path.with_suffix('.pending.xlsx')
        wb.save(tmp_out)
        tmp_out.replace(path)
        return {'ok': True, 'path': str(path)}
    finally:
        wb.close()
