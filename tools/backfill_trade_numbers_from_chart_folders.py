from __future__ import annotations

import argparse
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.master_journal_workbook import (
    TRADE_NUMBER_HEADER,
    _as_datetime,
    _as_float,
    _ensure_trade_log_schema,
    _trade_log_data_start_row,
    _trade_log_header_map,
)


FOLDER_RE = re.compile(r"^CRYPTO/2025/(C\d+)\s+([^/]+)/", re.IGNORECASE)
DATE_RE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
COMPACT_DATE_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})")


@dataclass
class ChartFolder:
    trade_number: str
    token: str
    symbol: str
    entries: List[str] = field(default_factory=list)
    dates: List[date] = field(default_factory=list)
    direction: str = ""
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None


@dataclass
class RepoTrade:
    row: int
    trade_number: str
    symbol: str
    side: str
    open_time: Optional[datetime]
    close_time: Optional[datetime]
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]


def _canon_symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _expected_symbol(token: str) -> str:
    symbol = _canon_symbol(token)
    return symbol if symbol.endswith(("USDT", "USDC")) else f"{symbol}USDT"


def _canon_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"LONG", "BUY"} or text.startswith("BUY"):
        return "BUY"
    if text in {"SHORT", "SELL"} or text.startswith("SELL"):
        return "SELL"
    return text


def _safe_float(value: Any) -> Optional[float]:
    number = _as_float(value)
    return float(number) if number is not None and math.isfinite(number) else None


def _extract_dates(text: str) -> List[date]:
    values: List[date] = []
    for pattern in (DATE_RE, COMPACT_DATE_RE):
        for match in pattern.finditer(text):
            try:
                values.append(date(*(int(part) for part in match.groups())))
            except ValueError:
                continue
    return values


def _summary_value(text: str, label: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(label)}\s*:\s*([^\r\n]+)", text)
    return str(match.group(1)).strip() if match else ""


def _summary_number(text: str, label: str) -> Optional[float]:
    value = _summary_value(text, label)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    return _safe_float(match.group(0)) if match else None


def parse_chart_folders(path: Path) -> List[ChartFolder]:
    folders: Dict[str, ChartFolder] = {}
    summaries: Dict[str, List[str]] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            match = FOLDER_RE.match(normalized)
            if not match:
                continue
            trade_number, token = match.groups()
            folder = folders.setdefault(
                trade_number.upper(),
                ChartFolder(
                    trade_number=trade_number.upper(),
                    token=token.strip(),
                    symbol=_expected_symbol(token),
                ),
            )
            folder.entries.append(normalized)
            folder.dates.extend(_extract_dates(normalized))
            if re.search(r"/trade_summary.*\.txt$", normalized, re.IGNORECASE):
                try:
                    summaries.setdefault(folder.trade_number, []).append(
                        archive.read(info).decode("utf-8", errors="replace")
                    )
                except Exception:
                    pass
    for trade_number, texts in summaries.items():
        folder = folders[trade_number]
        text = "\n".join(texts)
        summary_symbol = _summary_value(text, "Symbol")
        if summary_symbol:
            folder.symbol = _canon_symbol(summary_symbol)
        folder.direction = _canon_side(_summary_value(text, "Direction"))
        folder.entry = _summary_number(text, "Entry Price")
        folder.stop = _summary_number(text, "Stop Price")
        folder.target = _summary_number(text, "Target Price")
    return sorted(folders.values(), key=lambda folder: int(folder.trade_number[1:]))


def parse_repo_trades(ws, account: str) -> Tuple[List[RepoTrade], Dict[str, int]]:
    _ensure_trade_log_schema(ws)
    headers = _trade_log_header_map(ws)
    account_token = account.strip().upper()
    trades: List[RepoTrade] = []
    for row in range(_trade_log_data_start_row(ws), ws.max_row + 1):
        row_type = str(ws.cell(row, headers.get("Row Type", 0)).value or "trade").strip().lower()
        account_value = str(ws.cell(row, headers.get("Account", 0)).value or "").strip().upper()
        if row_type != "trade" or account_token not in account_value:
            continue
        trades.append(RepoTrade(
            row=row,
            trade_number=str(ws.cell(row, headers[TRADE_NUMBER_HEADER]).value or "").strip(),
            symbol=_canon_symbol(ws.cell(row, headers.get("Symbol", 0)).value),
            side=_canon_side(ws.cell(row, headers.get("Side", 0)).value),
            open_time=_as_datetime(ws.cell(row, headers.get("Open Time", 0)).value),
            close_time=_as_datetime(ws.cell(row, headers.get("Close Time", 0)).value),
            entry=_safe_float(ws.cell(row, headers.get("Entry Price", 0)).value),
            stop=_safe_float(ws.cell(row, headers.get("Stop Loss Price", 0)).value),
            target=_safe_float(ws.cell(row, headers.get("Target Price", 0)).value),
        ))
    return trades, headers


def _date_distance(folder: ChartFolder, repo: RepoTrade) -> int:
    if not folder.dates:
        return 999999
    repo_dates = [value.date() for value in (repo.open_time, repo.close_time) if value is not None]
    if not repo_dates:
        return 999999
    return min(abs((repo_date - folder_date).days) for repo_date in repo_dates for folder_date in folder.dates)


def _price_distance(folder: ChartFolder, repo: RepoTrade) -> float:
    distances: List[float] = []
    for expected, actual in ((folder.entry, repo.entry), (folder.stop, repo.stop), (folder.target, repo.target)):
        if expected is None or actual is None:
            continue
        distances.append(abs(expected - actual) / max(abs(expected), 1e-9))
    return sum(distances) / len(distances) if distances else 999999.0


def match_chart_folders(
    folders: List[ChartFolder],
    repo_trades: List[RepoTrade],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    matches: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    assigned_rows = {trade.row for trade in repo_trades if trade.trade_number}
    for folder in folders:
        candidates = [
            trade for trade in repo_trades
            if trade.symbol == folder.symbol
            and trade.row not in assigned_rows
            and (not folder.direction or trade.side == folder.direction)
        ]
        if not candidates:
            unmatched.append({"trade_number": folder.trade_number, "symbol": folder.symbol, "reason": "no_repo_candidate"})
            continue
        scored = sorted(
            [(_date_distance(folder, trade), _price_distance(folder, trade), trade.row, trade) for trade in candidates],
            key=lambda item: item[:3],
        )
        best = scored[0]
        has_metadata = bool(folder.dates or folder.entry is not None or folder.stop is not None or folder.target is not None)
        date_confident = best[0] <= 3
        price_confident = best[1] <= 0.08
        unique_candidate = len(scored) == 1
        separated = (
            len(scored) == 1
            or best[0] < scored[1][0]
            or (best[0] == scored[1][0] and best[1] + 0.005 < scored[1][1])
        )
        if not has_metadata or not (date_confident or price_confident) or not (unique_candidate or separated):
            payload = {
                "trade_number": folder.trade_number,
                "symbol": folder.symbol,
                "repo_rows": [item[3].row for item in scored[:5]],
                "reason": "insufficient_discriminator" if not has_metadata else "ambiguous_repo_candidates",
            }
            (unmatched if not has_metadata else ambiguous).append(payload)
            continue
        repo = best[3]
        assigned_rows.add(repo.row)
        matches.append({
            "trade_number": folder.trade_number,
            "symbol": folder.symbol,
            "repo_row": repo.row,
            "date_distance_days": best[0],
            "price_distance": None if best[1] >= 999999 else best[1],
            "confidence": "high",
        })
    return matches, unmatched, ambiguous


def run_backfill(
    journal_path: Path,
    charts_zip: Path,
    *,
    account: str = "BYBIT",
    apply_changes: bool = False,
) -> Dict[str, Any]:
    folders = parse_chart_folders(charts_zip)
    wb = load_workbook(journal_path)
    try:
        ws = wb["Trade Log"]
        repo_trades, headers = parse_repo_trades(ws, account)
        existing_trade_numbers = {trade.trade_number for trade in repo_trades if trade.trade_number}
        already_present = [folder.trade_number for folder in folders if folder.trade_number in existing_trade_numbers]
        pending_folders = [folder for folder in folders if folder.trade_number not in existing_trade_numbers]
        matches, unmatched, ambiguous = match_chart_folders(pending_folders, repo_trades)
        summary: Dict[str, Any] = {
            "folders_parsed": len(folders),
            "folders_already_present": len(already_present),
            "already_present_trade_numbers": already_present,
            "folders_considered": len(pending_folders),
            "high_confidence_matches": len(matches),
            "unmatched": len(unmatched),
            "ambiguous": len(ambiguous),
            "matches": matches,
            "unmatched_folders": unmatched,
            "ambiguous_folders": ambiguous,
            "applied": False,
        }
        if not apply_changes:
            return summary
        trade_col = headers[TRADE_NUMBER_HEADER]
        written = 0
        skipped_nonblank = 0
        for match in matches:
            cell = ws.cell(match["repo_row"], trade_col)
            if cell.value not in (None, ""):
                skipped_nonblank += 1
                continue
            cell.value = match["trade_number"]
            cell.number_format = "@"
            written += 1
        wb.save(journal_path)
        summary.update({"applied": True, "trade_numbers_written": written, "skipped_nonblank": skipped_nonblank})
        return summary
    finally:
        wb.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Trade Number from CRYPTO chart-folder labels.")
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--charts-zip", required=True, type=Path)
    parser.add_argument("--account", default="BYBIT")
    parser.add_argument("--apply", action="store_true", help="Write high-confidence matches. Omit for dry-run.")
    args = parser.parse_args()
    summary = run_backfill(args.journal, args.charts_zip, account=args.account, apply_changes=args.apply)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
