from __future__ import annotations

"""Populate the MONTHLY PROFIT LOSS sheet in ENTRIES.xlsx using data from MASTER.
The script does not rely on CORRECT.xlsx and will work after that file is removed.
Run with ``python populate_monthly_profit_loss.py``.
"""

import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Set, Tuple

import xlwings as xw
from tqdm import tqdm

def resolve_data_dir() -> Path:
    """Return the folder that stores ENTRIES.xlsx.

    Defaults to ``C:\\Users\\User\\Documents\\LEDGER`` (or the current
    user's ``~/Documents/LEDGER``). Set ``LEDGER_DATA_DIR`` to override.
    """

    env_path = os.environ.get("LEDGER_DATA_DIR")
    if env_path:
        return Path(env_path).expanduser()

    documents_path = Path.home() / "Documents" / "LEDGER"
    if documents_path.exists():
        return documents_path

    fallback_path = Path(__file__).resolve().parents[2] / "LEDGER"
    if fallback_path.exists():
        return fallback_path

    return Path(__file__).resolve().parent


BASE_DIR = resolve_data_dir()
ENTRY_FILE = BASE_DIR / "ENTRIES.xlsx"

CURRENCY_FORMAT = "_-\"$\"* #,##0.00_-;\\-\"$\"* #,##0.00_-;_-\"$\"* \"-\"??_-;_-@_-"

# Some categories in MASTER differ from the headers used in MONTHLY PROFIT LOSS.
# Map them to the header spelling so that data lands in the correct column.
CATEGORY_ALIASES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("EXP", "FILM TV"): ("EXP", "FILM/TV"),
    ("EXP", "MISC EXPENSE"): ("EXP", "MISC"),
    ("REV", "MISC REVENUE"): ("REV", "MISC"),
}


def _format_currency_text(value: float) -> str:
    """Return a compact currency string for embedding in a text cell.

    Required format examples:
      - positive: "$7.05"
      - negative: "$-7.05" (minus sign after the dollar symbol)
    """

    if value < 0:
        return f"$-{abs(value):,.2f}"
    return f"${value:,.2f}"


def _pick_high_low_expense(
    month_values: Mapping[Tuple[str, str], float],
) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
    """Return (highest_expense, lowest_expense) for a month.

    We only consider EXP rows. If expenses are stored as negative numbers,
    we treat the *most negative* value as the highest expense and the value
    closest to zero (but still negative) as the lowest expense.

    If expenses are stored as positive numbers, highest=largest positive and
    lowest=smallest positive.
    """

    exp_items = [
        (category, value)
        for (typ, category), value in month_values.items()
        if _normalise_type(typ) == "EXP" and value not in (None, 0, 0.0)
    ]
    if not exp_items:
        return None, None

    negatives = [(cat, val) for cat, val in exp_items if val < 0]
    positives = [(cat, val) for cat, val in exp_items if val > 0]

    if negatives:
        highest = min(negatives, key=lambda x: x[1])  # most negative
        lowest = max(negatives, key=lambda x: x[1])  # closest to 0
        return highest, lowest

    # Fallback: all EXP values are non-negative
    highest = max(positives, key=lambda x: x[1])
    lowest = min(positives, key=lambda x: x[1])
    return highest, lowest


def _ensure_matrix(data) -> List[List]:
    """Return *data* as a list of rows."""

    if not data:
        return []
    if isinstance(data[0], list):
        return data  # already a matrix
    return [data]


def _normalise_type(value) -> str:
    return str(value).strip().upper()


def _normalise_category(value) -> str:
    return str(value).strip()


def build_header_map(ws) -> Dict[Tuple[str, str], int]:
    """Return mapping of (type, category) -> column index from MONTHLY PROFIT LOSS."""

    matrix = _ensure_matrix(ws.used_range.value)
    if not matrix:
        return {}
    row1 = matrix[0]
    row2 = matrix[1] if len(matrix) > 1 else []
    header: Dict[Tuple[str, str], int] = {}
    for idx, (typ, category) in enumerate(zip(row1, row2), start=1):
        if idx < 3:
            continue  # Skip MONTH and TOTAL columns
        if idx in (3, 4):
            continue  # Skip HIGHEST EXPENSE / LOWEST EXPENSE helper columns
        if typ is None or category is None:
            continue
        header[(_normalise_type(typ), _normalise_category(category))] = idx
    return header


def _parse_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_master_from_rows(
    rows: Iterable[Iterable],
    header_map: Mapping[Tuple[str, str], int],
) -> MutableMapping[datetime, MutableMapping[Tuple[str, str], float]]:
    """Return aggregated amounts per (month, header column)."""

    data: MutableMapping[datetime, MutableMapping[Tuple[str, str], float]] = defaultdict(
        lambda: defaultdict(float)
    )
    missing: Set[Tuple[str, str]] = set()
    rows_list: List[Iterable] = list(rows)
    for row in tqdm(rows_list[3:], desc="Reading MASTER"):
        if not row:
            continue
        date, typ, category = row[0], row[1], row[2]
        if typ is None or category is None:
            continue
        norm_type = _normalise_type(typ)
        if norm_type not in {"REV", "EXP"}:
            continue
        norm_category = _normalise_category(category)
        key = CATEGORY_ALIASES.get((norm_type, norm_category), (norm_type, norm_category))
        if key not in header_map:
            missing.add((norm_type, norm_category))
            continue
        debit = _parse_float(row[4]) if len(row) > 4 else 0.0
        credit = _parse_float(row[5]) if len(row) > 5 else 0.0
        if isinstance(date, datetime):
            month = datetime(date.year, date.month, 1)
        else:
            try:
                parsed = datetime.strptime(str(date), "%d/%m/%Y")
            except Exception:
                continue
            month = datetime(parsed.year, parsed.month, 1)
        amount = credit - debit
        data[month][key] += amount
    if missing:
        print(
            "Skipping rows with unmapped categories:",
            ", ".join(sorted(f"{typ} - {cat}" for typ, cat in missing)),
        )
    return data


def read_master(ws, header_map):
    """Return aggregated amounts per (month, header column)."""

    matrix = _ensure_matrix(ws.used_range.value)
    return read_master_from_rows(matrix, header_map)


def populate_monthly_profit_loss(wb):
    ws_master = wb.sheets["MASTER"]
    ws_target = wb.sheets["MONTHLY PROFIT LOSS"]

    header_map = build_header_map(ws_target)
    if not header_map:
        print("MONTHLY PROFIT LOSS sheet headers could not be determined.")
        return

    aggregated = read_master(ws_master, header_map)
    months = sorted(aggregated.keys())

    used_range = ws_target.used_range
    if used_range is not None:
        last_row = used_range.last_cell.row
        last_col = used_range.last_cell.column
        if last_row > 2 and last_col >= 1:
            ws_target.range((3, 1), (last_row, last_col)).clear_contents()

    for idx, month in enumerate(tqdm(months, desc="Populating")):
        row_idx = idx + 3
        cell = ws_target.cells(row_idx, 1)
        cell.value = month
        cell.number_format = "mmm yyyy"  # e.g., "Jan 2025"

        # Populate columns C/D with highest and lowest monthly EXP accounts.
        highest, lowest = _pick_high_low_expense(aggregated[month])
        if highest is not None:
            account, value = highest
            ws_target.cells(row_idx, 3).value = f"{account} - {_format_currency_text(value)}"
        if lowest is not None:
            account, value = lowest
            ws_target.cells(row_idx, 4).value = f"{account} - {_format_currency_text(value)}"

        total = 0.0
        for key, value in aggregated[month].items():
            if not value:
                continue
            col_idx = header_map.get(key)
            if not col_idx:
                continue
            tgt_cell = ws_target.cells(row_idx, col_idx)
            tgt_cell.value = value
            tgt_cell.number_format = CURRENCY_FORMAT
            total += value
        if total:
            tot_cell = ws_target.cells(row_idx, 2)
            tot_cell.value = total
            tot_cell.number_format = CURRENCY_FORMAT


def main() -> None:
    if not ENTRY_FILE.exists():
        print("ENTRIES.xlsx not found in the script directory.")
        return
    with xw.App(visible=False) as app:
        try:
            wb = app.books.open(ENTRY_FILE)
        except Exception as exc:
            print(f"Error opening workbook: {exc}")
            return
        populate_monthly_profit_loss(wb)
        wb.save()
        wb.close()
    print("MONTHLY PROFIT LOSS sheet updated.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # ensure errors are printed
        print(f"Error: {exc}")
