#!/usr/bin/env python3
"""
Build a 32-bit-Android-safe Excel replica of the Trading Journal from workbooks in CODEX-master/journal.
No pandas, no FastAPI, no local web server.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except Exception as exc:  # pragma: no cover
    print(f"ERROR: missing openpyxl: {exc}", file=sys.stderr)
    print("Install with: python -m pip install openpyxl xlrd==2.0.1 python-dateutil", file=sys.stderr)
    raise SystemExit(2)

try:
    from dateutil import parser as date_parser
except Exception:  # pragma: no cover
    date_parser = None

try:
    import xlrd
except Exception:  # pragma: no cover
    xlrd = None

OUTPUT_NAME = "TradingJournal_Android_Replica.xlsx"
EXCLUDED_SOURCE_NAMES = {
    OUTPUT_NAME.lower(),
    "account_cashflows.xlsx",
    "bybit demo.xlsx",
}

ALIASES = {
    "open_time": ["opening_time", "open_time", "entry_time", "time_open", "opened", "opened_at", "date_open"],
    "close_time": ["closing_time", "close_time", "exit_time", "time_close", "closed_at", "closed", "date_close"],
    "side": ["type_buy_sell", "side", "direction", "buy_sell", "type", "long_short"],
    "symbol": ["symbol", "instrument", "pair", "market", "ticker"],
    "account": ["account", "account_label", "portfolio", "book"],
    "currency": ["account_currency", "currency", "ccy", "deposit_currency"],
    "setup": ["setup", "strategy", "entry_setup"],
    "qty": ["size_quantity", "qty", "quantity", "size", "units", "volume", "lots"],
    "entry": ["entry_price", "entry", "open_price", "price_open"],
    "exit": ["closing_price", "exit_price", "exit", "close_price", "price_close"],
    "swap": ["swap"],
    "commission": ["commission", "fee", "fees", "cost"],
    "net_profit": ["net_profit", "realized_pnl", "pnl", "profit", "pl", "net_pnl", "result", "p_l"],
    "balance_after": ["balance_after_trade", "bal_after_trade", "balance_after", "bal_after"],
    "stop_loss": ["stop_loss_optional", "stop_loss", "sl"],
    "take_profit": ["take_profit_optional", "take_profit", "tp", "target"],
    "high": ["highest_price_optional", "highest_price", "high"],
    "low": ["lowest_price_optional", "lowest_price", "low"],
    "notes": ["notes", "pre_trade_comments", "entry_comments", "trade_management", "exit_comments"],
    "breakeven": ["breakeven", "break_even", "be"],
    "timeframe": ["timeframe", "tf"],
    "error": ["error"],
    "held_through_news": ["held_through_news"],
    "spiked_out": ["spiked_out"],
    "early_close": ["early_close"],
}

DARK = "111827"
DARK2 = "0F172A"
PANEL = "1F2937"
HEADER = "2563EB"
HEADER2 = "1D4ED8"
TEXT = "E5E7EB"
MUTED = "9CA3AF"
GREEN = "22C55E"
RED = "F87171"
AMBER = "F59E0B"
BORDER = "374151"
WHITE = "FFFFFF"


def norm_col(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "—", "-"}:
        return None
    text = text.replace(",", "")
    text = re.sub(r"[^0-9+\-.]", "", text)
    if not text or text in {"+", "-", "."}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def parse_dt(value: Any, datemode: Optional[int] = None) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, (int, float)) and datemode is not None and xlrd is not None:
        try:
            return xlrd.xldate.xldate_as_datetime(value, datemode).replace(tzinfo=None)
        except Exception:
            pass
    if isinstance(value, (int, float)):
        # Excel serial fallback for xlsx numeric dates.
        try:
            if 20000 < float(value) < 90000:
                return datetime(1899, 12, 30) + timedelta(days=float(value))
        except Exception:
            pass
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        pass
    if date_parser is not None:
        try:
            return date_parser.parse(text, dayfirst=False, fuzzy=True).replace(tzinfo=None)
        except Exception:
            return None
    return None


def first_present(header_map: Dict[str, int], aliases: Iterable[str]) -> Optional[int]:
    for alias in aliases:
        idx = header_map.get(norm_col(alias))
        if idx is not None:
            return idx
    return None


def find_header_row(rows: List[List[Any]]) -> Tuple[int, List[str]]:
    best_i, best_score = 0, -1
    for i, row in enumerate(rows[:20]):
        names = [norm_col(x) for x in row]
        if not any(names):
            continue
        score = 0
        for aliases in ALIASES.values():
            if any(norm_col(a) in names for a in aliases):
                score += 1
        if score > best_score:
            best_i, best_score = i, score
    header = [clean_text(x) or f"Column {i+1}" for i, x in enumerate(rows[best_i])] if rows else []
    return best_i, header


def load_sheet_rows_xlsx(path: Path) -> Iterable[Tuple[str, List[List[Any]], Optional[int]]]:
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            rows = [[cell for cell in row] for row in ws.iter_rows(values_only=True)]
            yield ws.title, rows, None
    finally:
        try:
            wb.close()
        except Exception:
            pass


def load_sheet_rows_xls(path: Path) -> Iterable[Tuple[str, List[List[Any]], Optional[int]]]:
    if xlrd is None:
        raise RuntimeError("xlrd is required for .xls files. Install with: python -m pip install xlrd==2.0.1")
    book = xlrd.open_workbook(str(path))
    for sheet in book.sheets():
        rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        yield sheet.name, rows, book.datemode


def canonical_symbol(value: Any) -> str:
    text = clean_text(value).upper().replace("/", "").replace(" ", "")
    if text.endswith(".A") or text.endswith(".B"):
        text = text[:-2]
    return text


def boolish(value: Any) -> str:
    text = clean_text(value).lower()
    if text in {"yes", "y", "true", "1"}:
        return "Yes"
    if text in {"no", "n", "false", "0"}:
        return "No"
    return clean_text(value)


def infer_asset_class(symbol: str, account: str) -> str:
    s = symbol.upper()
    a = account.lower()
    if "oanda" in a or "forex" in a or re.fullmatch(r"[A-Z]{6}", s):
        return "FX"
    return "Crypto"


def parse_workbook(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    trades: List[Dict[str, Any]] = []
    loader = load_sheet_rows_xls if path.suffix.lower() == ".xls" else load_sheet_rows_xlsx
    account_default = path.stem.strip() or path.name

    for sheet_name, rows, datemode in loader(path):
        non_empty = [r for r in rows if any(clean_text(x) for x in r)]
        if not non_empty:
            continue
        header_i, header = find_header_row(non_empty)
        header_map: Dict[str, int] = {}
        for idx, name in enumerate(header):
            n = norm_col(name)
            if n and n not in header_map:
                header_map[n] = idx
        col = {key: first_present(header_map, aliases) for key, aliases in ALIASES.items()}
        signal_cols = [col.get("symbol"), col.get("side"), col.get("entry"), col.get("exit"), col.get("net_profit"), col.get("open_time"), col.get("close_time")]
        if not col.get("symbol") or sum(x is not None for x in signal_cols) < 3:
            continue
        data_rows = non_empty[header_i + 1:]
        for row_index, row in enumerate(data_rows, start=header_i + 2):
            def cell(key: str) -> Any:
                idx = col.get(key)
                if idx is None or idx >= len(row):
                    return None
                return row[idx]
            symbol = canonical_symbol(cell("symbol"))
            if not symbol:
                continue
            open_dt = parse_dt(cell("open_time"), datemode)
            close_dt = parse_dt(cell("close_time"), datemode)
            pnl = safe_float(cell("net_profit"))
            commission = safe_float(cell("commission"))
            swap = safe_float(cell("swap"))
            if pnl is None and commission is None and cell("exit") is None:
                # Avoid treating account/balance rows as trades.
                continue
            account = clean_text(cell("account")) or account_default
            side = clean_text(cell("side")).upper()
            entry = safe_float(cell("entry"))
            exit_price = safe_float(cell("exit"))
            qty = safe_float(cell("qty"))
            balance_after = safe_float(cell("balance_after"))
            stop_loss = safe_float(cell("stop_loss"))
            take_profit = safe_float(cell("take_profit"))
            breakeven = boolish(cell("breakeven"))
            notes_parts = []
            for key in ["notes", "error", "held_through_news", "spiked_out", "early_close"]:
                val = boolish(cell(key)) if key != "notes" else clean_text(cell(key))
                if val:
                    notes_parts.append(f"{key}: {val}" if key != "notes" else val)
            close_for_id = close_dt.isoformat(sep=" ") if close_dt else ""
            trade = {
                "id": f"{path.name}:{sheet_name}:{row_index}:{symbol}:{close_for_id}",
                "source_file": path.name,
                "sheet": sheet_name,
                "row": row_index,
                "account": account,
                "currency": clean_text(cell("currency")),
                "asset_class": infer_asset_class(symbol, account),
                "symbol": symbol,
                "side": side,
                "setup": clean_text(cell("setup")),
                "timeframe": clean_text(cell("timeframe")),
                "open_time": open_dt,
                "close_time": close_dt or open_dt,
                "qty": qty,
                "entry": entry,
                "exit": exit_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "commission": commission,
                "swap": swap,
                "net_profit": pnl,
                "balance_after": balance_after,
                "breakeven": breakeven,
                "notes": " | ".join(notes_parts),
            }
            trades.append(trade)
    if not trades:
        warnings.append(f"No trade rows parsed from {path.name}")
    return trades, warnings


def find_journal_dir(repo_dir: Optional[str]) -> Path:
    candidates: List[Path] = []
    if repo_dir:
        candidates.append(Path(repo_dir) / "journal")
    env_repo = os.environ.get("CODEX_REPO_DIR")
    if env_repo:
        candidates.append(Path(env_repo) / "journal")
    candidates.extend([
        Path("/storage/emulated/0/Download/CODEX-master (4)/CODEX-master/journal"),
        Path.home() / "storage" / "downloads" / "CODEX-master (4)" / "CODEX-master" / "journal",
    ])
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    download_roots = [Path.home() / "storage" / "downloads", Path("/storage/emulated/0/Download"), Path("/sdcard/Download")]
    for root in download_roots:
        if not root.exists():
            continue
        for found in root.glob("**/CODEX-master/journal"):
            if found.is_dir():
                return found
    raise FileNotFoundError("Could not find CODEX-master/journal under Downloads")


def list_source_workbooks(journal_dir: Path) -> List[Path]:
    out = []
    for path in sorted(journal_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".xls", ".xlsx", ".xlsm"}:
            continue
        if path.name.lower() in EXCLUDED_SOURCE_NAMES:
            continue
        if path.name.startswith("~$"):
            continue
        out.append(path)
    return out


def result_label(pnl: Optional[float], breakeven: str = "") -> str:
    if breakeven.lower() == "yes":
        return "Breakeven"
    if pnl is None:
        return "Unknown"
    if pnl > 0:
        return "Win"
    if pnl < 0:
        return "Loss"
    return "Breakeven"


def pct(num: float, den: float) -> Optional[float]:
    return (num / den) if den else None


def summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = sum(1 for t in trades if result_label(t.get("net_profit"), t.get("breakeven", "")) == "Win")
    losses = sum(1 for t in trades if result_label(t.get("net_profit"), t.get("breakeven", "")) == "Loss")
    bes = sum(1 for t in trades if result_label(t.get("net_profit"), t.get("breakeven", "")) == "Breakeven")
    known = wins + losses
    pnls = [t.get("net_profit") for t in trades if isinstance(t.get("net_profit"), (int, float))]
    gross_gain = sum(x for x in pnls if x > 0)
    gross_loss = sum(x for x in pnls if x < 0)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": bes,
        "win_rate": pct(wins, known),
        "net_pl": sum(pnls),
        "gross_gain": gross_gain,
        "gross_loss": gross_loss,
        "avg_pl": pct(sum(pnls), len(pnls)) if pnls else None,
        "max_win": max(pnls) if pnls else None,
        "max_loss": min(pnls) if pnls else None,
    }


def instrument_stats(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        buckets[t.get("symbol") or "UNKNOWN"].append(t)
    rows = []
    for sym, items in buckets.items():
        s = summarize(items)
        rows.append({
            "Symbol": sym,
            "Asset": items[0].get("asset_class", ""),
            "Trades": s["trades"],
            "Wins": s["wins"],
            "Losses": s["losses"],
            "Breakeven": s["breakeven"],
            "Win Rate": s["win_rate"],
            "Net P/L": s["net_pl"],
            "Avg P/L": s["avg_pl"],
            "Avg Entry": avg([x.get("entry") for x in items]),
            "Avg Exit": avg([x.get("exit") for x in items]),
            "Avg Stop Loss": avg([x.get("stop_loss") for x in items]),
            "Avg Target": avg([x.get("take_profit") for x in items]),
        })
    rows.sort(key=lambda r: (-int(r["Trades"] or 0), str(r["Symbol"])))
    return rows


def avg(values: Iterable[Any]) -> Optional[float]:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not math.isnan(float(v))]
    return sum(nums) / len(nums) if nums else None


def sorted_trades(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(t: Dict[str, Any]) -> Tuple[datetime, str]:
        dt = t.get("close_time") or t.get("open_time") or datetime.min
        return (dt if isinstance(dt, datetime) else datetime.min, str(t.get("id") or ""))
    return sorted(trades, key=key, reverse=True)


def equity_rows(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chronological = list(reversed(sorted_trades(trades)))
    equity = 0.0
    rows = []
    for i, t in enumerate(chronological, start=1):
        pnl = t.get("net_profit") if isinstance(t.get("net_profit"), (int, float)) else 0.0
        if isinstance(t.get("balance_after"), (int, float)):
            equity = float(t["balance_after"])
        else:
            equity += float(pnl)
        rows.append({
            "#": i,
            "Close Time": t.get("close_time"),
            "Symbol": t.get("symbol"),
            "Net P/L": pnl,
            "Equity": equity,
        })
    return rows


def calendar_rows(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_month_day: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for t in trades:
        dt = t.get("close_time") or t.get("open_time")
        pnl = t.get("net_profit")
        if not isinstance(dt, datetime) or not isinstance(pnl, (int, float)):
            continue
        by_month_day[dt.strftime("%Y-%m")][dt.day] += float(pnl)
    out = []
    for month in sorted(by_month_day.keys(), reverse=True):
        row = {"Month": month}
        total = 0.0
        for day in range(1, 32):
            v = by_month_day[month].get(day)
            row[str(day)] = v if v not in (None, 0) else None
            total += float(v or 0)
        row["Monthly Total"] = total
        out.append(row)
    return out


def style_sheet(ws, max_col: int, freeze: Optional[str] = None) -> None:
    ws.sheet_view.showGridLines = False
    if freeze:
        ws.freeze_panes = freeze
    thin = Side(style="thin", color=BORDER)
    for row in ws.iter_rows():
        for cell in row:
            cell.fill = PatternFill("solid", fgColor=DARK)
            cell.font = Font(color=TEXT, name="Calibri", size=11)
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="top")
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=HEADER)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col in range(1, max_col + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = 16
    ws.auto_filter.ref = ws.dimensions


def write_table(ws, headers: List[str], rows: List[Dict[str, Any]], start_row: int = 1, start_col: int = 1) -> None:
    for j, h in enumerate(headers, start=start_col):
        ws.cell(start_row, j, h)
    for i, row in enumerate(rows, start=start_row + 1):
        for j, h in enumerate(headers, start=start_col):
            value = row.get(h)
            c = ws.cell(i, j, value)
            if isinstance(value, datetime):
                c.number_format = "yyyy-mm-dd hh:mm"
            elif h.lower().endswith("rate"):
                c.number_format = "0.00%"
            elif isinstance(value, (int, float)):
                c.number_format = "#,##0.00"


def set_pl_format(ws, ranges: Iterable[str]) -> None:
    for rng in ranges:
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"], font=Font(color=GREEN)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], font=Font(color=RED)))


def write_dashboard(wb: Workbook, trades: List[Dict[str, Any]], sources: List[Path], warnings: List[str]) -> None:
    ws = wb.active
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for row in range(1, 46):
        for col in range(1, 10):
            ws.cell(row, col).fill = PatternFill("solid", fgColor=DARK2)
            ws.cell(row, col).font = Font(color=TEXT, name="Calibri", size=11)
    ws.merge_cells("A1:I2")
    ws["A1"] = "Trading Journal Android Replica"
    ws["A1"].font = Font(color=WHITE, bold=True, size=20)
    ws["A1"].alignment = Alignment(vertical="center")
    ws["A3"] = "Generated"
    ws["B3"] = datetime.now()
    ws["B3"].number_format = "yyyy-mm-dd hh:mm"
    ws["A4"] = "Source workbooks"
    ws["B4"] = len(sources)
    ws["A5"] = "Source folder"
    ws["B5"] = str(sources[0].parent if sources else "")

    s = summarize(trades)
    kpis = [
        ("Trades", s["trades"]),
        ("Wins", s["wins"]),
        ("Losses", s["losses"]),
        ("Break-even", s["breakeven"]),
        ("Win rate", s["win_rate"]),
        ("Net P/L", s["net_pl"]),
        ("Gross gain", s["gross_gain"]),
        ("Gross loss", s["gross_loss"]),
    ]
    row = 8
    for i, (label, value) in enumerate(kpis):
        r = row + (i // 2) * 3
        c = 1 + (i % 2) * 4
        ws.merge_cells(start_row=r, start_column=c, end_row=r+1, end_column=c+2)
        cell = ws.cell(r, c, label)
        cell.fill = PatternFill("solid", fgColor=PANEL)
        cell.font = Font(color=MUTED, bold=True, size=12)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        val = ws.cell(r+2, c, value)
        ws.merge_cells(start_row=r+2, start_column=c, end_row=r+2, end_column=c+2)
        val.fill = PatternFill("solid", fgColor=PANEL)
        val.font = Font(color=GREEN if (isinstance(value, (int, float)) and value > 0 and "loss" not in label.lower()) else RED if ("Loss" in label or (label == "Net P/L" and isinstance(value, (int, float)) and value < 0)) else TEXT, bold=True, size=16)
        val.alignment = Alignment(horizontal="center", vertical="center")
        if label == "Win rate":
            val.number_format = "0.00%"
        elif isinstance(value, float):
            val.number_format = "#,##0.00"

    ws["A23"] = "Top instrument averages"
    ws["A23"].font = Font(color=WHITE, bold=True, size=14)
    top = instrument_stats(trades)[:10]
    headers = ["Symbol", "Asset", "Trades", "Wins", "Losses", "Win Rate", "Net P/L", "Avg P/L"]
    write_table(ws, headers, top, start_row=25, start_col=1)
    for c in range(1, len(headers) + 1):
        ws.cell(25, c).fill = PatternFill("solid", fgColor=HEADER)
        ws.cell(25, c).font = Font(color=WHITE, bold=True)
    for r in range(26, 26 + len(top)):
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).fill = PatternFill("solid", fgColor=DARK)
            ws.cell(r, c).font = Font(color=TEXT)
    if warnings:
        ws["A38"] = "Diagnostics / warnings"
        ws["A38"].font = Font(color=AMBER, bold=True, size=13)
        for i, warning in enumerate(warnings[:6], start=39):
            ws.cell(i, 1, warning)
            ws.cell(i, 1).font = Font(color=AMBER)
    for col in range(1, 10):
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws.column_dimensions["B"].width = 46
    set_pl_format(ws, ["G26:G40", "H26:H40"])


def build_output(journal_dir: Path, output_path: Path) -> Tuple[int, int, List[str]]:
    sources = list_source_workbooks(journal_dir)
    all_trades: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not sources:
        warnings.append(f"No journal workbooks found in {journal_dir}")
    for path in sources:
        try:
            trades, w = parse_workbook(path)
            all_trades.extend(trades)
            warnings.extend(w)
        except Exception as exc:
            warnings.append(f"{path.name}: {exc}")

    # Dedupe by stable trade signature.
    seen = set()
    deduped = []
    for t in all_trades:
        key = (t.get("symbol"), t.get("side"), t.get("open_time"), t.get("close_time"), t.get("qty"), t.get("entry"), t.get("exit"), t.get("net_profit"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    all_trades = deduped

    wb = Workbook()
    write_dashboard(wb, all_trades, sources, warnings)

    trades_ws = wb.create_sheet("All Trades")
    trade_headers = ["Close Time", "Open Time", "Account", "Asset", "Symbol", "Side", "Timeframe", "Setup", "Qty", "Entry", "Exit", "Stop Loss", "Target", "Commission", "Swap", "Net P/L", "Balance After", "Result", "Breakeven", "Source", "Notes"]
    rows = []
    for t in sorted_trades(all_trades):
        rows.append({
            "Close Time": t.get("close_time"),
            "Open Time": t.get("open_time"),
            "Account": t.get("account"),
            "Asset": t.get("asset_class"),
            "Symbol": t.get("symbol"),
            "Side": t.get("side"),
            "Timeframe": t.get("timeframe"),
            "Setup": t.get("setup"),
            "Qty": t.get("qty"),
            "Entry": t.get("entry"),
            "Exit": t.get("exit"),
            "Stop Loss": t.get("stop_loss"),
            "Target": t.get("take_profit"),
            "Commission": t.get("commission"),
            "Swap": t.get("swap"),
            "Net P/L": t.get("net_profit"),
            "Balance After": t.get("balance_after"),
            "Result": result_label(t.get("net_profit"), t.get("breakeven", "")),
            "Breakeven": t.get("breakeven"),
            "Source": f"{t.get('source_file')} / {t.get('sheet')} / row {t.get('row')}",
            "Notes": t.get("notes"),
        })
    write_table(trades_ws, trade_headers, rows)
    style_sheet(trades_ws, len(trade_headers), freeze="A2")
    trades_ws.column_dimensions["A"].width = 20
    trades_ws.column_dimensions["B"].width = 20
    trades_ws.column_dimensions["T"].width = 36
    trades_ws.column_dimensions["U"].width = 44
    set_pl_format(trades_ws, [f"P2:P{max(2, len(rows)+1)}"])

    inst_ws = wb.create_sheet("Instrument Averages")
    inst_headers = ["Symbol", "Asset", "Trades", "Wins", "Losses", "Breakeven", "Win Rate", "Net P/L", "Avg P/L", "Avg Entry", "Avg Exit", "Avg Stop Loss", "Avg Target"]
    inst_rows = instrument_stats(all_trades)
    write_table(inst_ws, inst_headers, inst_rows)
    style_sheet(inst_ws, len(inst_headers), freeze="A2")
    set_pl_format(inst_ws, [f"H2:I{max(2, len(inst_rows)+1)}"])

    cal_ws = wb.create_sheet("PL Calendar")
    cal_headers = ["Month"] + [str(i) for i in range(1, 32)] + ["Monthly Total"]
    cal_data = calendar_rows(all_trades)
    write_table(cal_ws, cal_headers, cal_data)
    style_sheet(cal_ws, len(cal_headers), freeze="B2")
    for c in range(2, 34):
        cal_ws.column_dimensions[get_column_letter(c)].width = 9
    set_pl_format(cal_ws, [f"B2:AF{max(2, len(cal_data)+1)}", f"AG2:AG{max(2, len(cal_data)+1)}"])

    eq_ws = wb.create_sheet("Equity Curve")
    eq_headers = ["#", "Close Time", "Symbol", "Net P/L", "Equity"]
    eq_data = equity_rows(all_trades)
    write_table(eq_ws, eq_headers, eq_data)
    style_sheet(eq_ws, len(eq_headers), freeze="A2")
    eq_ws.column_dimensions["B"].width = 20
    set_pl_format(eq_ws, [f"D2:E{max(2, len(eq_data)+1)}"])
    if len(eq_data) >= 2:
        chart = LineChart()
        chart.title = "Equity Curve"
        chart.y_axis.title = "Equity"
        chart.x_axis.title = "Trade #"
        data = Reference(eq_ws, min_col=5, min_row=1, max_row=len(eq_data)+1)
        cats = Reference(eq_ws, min_col=1, min_row=2, max_row=len(eq_data)+1)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 12
        chart.width = 28
        eq_ws.add_chart(chart, "G2")

    diag_ws = wb.create_sheet("Diagnostics")
    diag_rows = []
    diag_rows.append({"Item": "Journal folder", "Value": str(journal_dir)})
    diag_rows.append({"Item": "Output workbook", "Value": str(output_path)})
    diag_rows.append({"Item": "Source workbooks", "Value": len(sources)})
    diag_rows.append({"Item": "Parsed trades", "Value": len(all_trades)})
    for src in sources:
        diag_rows.append({"Item": "Source file", "Value": src.name})
    for warning in warnings:
        diag_rows.append({"Item": "Warning", "Value": warning})
    write_table(diag_ws, ["Item", "Value"], diag_rows)
    style_sheet(diag_ws, 2, freeze="A2")
    diag_ws.column_dimensions["A"].width = 24
    diag_ws.column_dimensions["B"].width = 90

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(sources), len(all_trades), warnings


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="Path to CODEX-master repo. Defaults to Downloads/CODEX-master (4)/CODEX-master.")
    ap.add_argument("--output", default=None, help="Output .xlsx path. Defaults to repo/journal/TradingJournal_Android_Replica.xlsx")
    args = ap.parse_args(argv)
    try:
        journal_dir = find_journal_dir(args.repo)
        output_path = Path(args.output) if args.output else journal_dir / OUTPUT_NAME
        source_count, trade_count, warnings = build_output(journal_dir, output_path)
        print(f"OK: wrote {output_path}")
        print(f"Source workbooks: {source_count}")
        print(f"Parsed trades: {trade_count}")
        if warnings:
            print("Warnings:")
            for warning in warnings[:20]:
                print(f"- {warning}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
