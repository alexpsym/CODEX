"""Sort transactions in all LEDGER workbooks by date.

The script scans every ``.xlsx`` workbook in the LEDGER data directory and
sorts each sheet's transaction rows (starting at row 4) from oldest to newest
based on the dates in column A. Header rows (1-2) remain untouched, and row 3 is
left blank. In the ENTRIES workbook, the "MONTHLY PROFIT LOSS" and "TRIAL
BALANCE" sheets are intentionally skipped. The "CHECKLIST" workbook is skipped
entirely.

Run with ``python sort_transactions.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import xlwings as xw


AUS_DATE_FORMAT = "dd/mm/yyyy"
DATA_START_ROW = 4


def resolve_data_dir() -> Path:
    """Return the folder that stores Excel workbooks.

    By default this points to ``C:\\Users\\User\\Documents\\LEDGER`` (or the
    current user's ``~/Documents/LEDGER`` on other platforms). Set
    ``LEDGER_DATA_DIR`` to override this location.
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


def sort_sheet_transactions(sheet: xw.Sheet) -> bool:
    """Sort transaction rows in ``sheet`` by the dates in column A.

    Returns ``True`` if any rows were processed, otherwise ``False``.
    """

    last_cell = sheet.used_range.last_cell
    last_row = last_cell.row
    last_col = last_cell.column
    if last_row < DATA_START_ROW:
        return False

    data_range = sheet.range((DATA_START_ROW, 1), (last_row, last_col))
    data = data_range.options(pd.DataFrame, header=False, index=False).value
    non_empty_rows = data.dropna(how="all")
    if data.empty or non_empty_rows.empty:
        return False

    sort_dates = pd.to_datetime(data.iloc[:, 0], dayfirst=True, errors="coerce")
    sort_keys = sort_dates.fillna(pd.Timestamp.max)
    sorted_data = (
        data.assign(_sort_key=sort_keys)
        .sort_values("_sort_key", kind="stable")
        .drop(columns="_sort_key")
        .reset_index(drop=True)
    )

    parsed_dates = pd.to_datetime(
        sorted_data.iloc[:, 0], dayfirst=True, errors="coerce"
    )
    sorted_data.iloc[:, 0] = parsed_dates.where(parsed_dates.notna(), sorted_data.iloc[:, 0])

    data_range.value = sorted_data.values
    date_rows = sorted_data.shape[0]
    if date_rows:
        sheet.range((DATA_START_ROW, 1), (DATA_START_ROW + date_rows - 1, 1)).number_format = AUS_DATE_FORMAT

    return True


def sort_workbook_transactions(app: xw.App, workbook_path: Path) -> None:
    """Open ``workbook_path`` and sort transactions in all eligible sheets."""

    print(f"Opening workbook: {workbook_path.name}")
    wb = app.books.open(str(workbook_path), update_links=False)
    processed_any = False

    skip_sheets = set()
    if workbook_path.name.lower() == "entries.xlsx":
        skip_sheets = {"monthly profit loss", "trial balance"}

    try:
        for sheet in wb.sheets:
            if sheet.name.lower() in skip_sheets:
                print(f"  Skipped sheet (no sorting): {sheet.name}")
                continue

            processed = sort_sheet_transactions(sheet)
            processed_any = processed_any or processed
            if processed:
                print(f"  Sorted sheet: {sheet.name}")
        if processed_any:
            wb.save()
            print("  Saved changes.")
        else:
            print("  No transaction rows found to sort.")
    finally:
        wb.close()


def main() -> None:
    base_dir = resolve_data_dir()
    print(f"Sorting transactions in directory: {base_dir}")
    workbooks = [p for p in base_dir.glob("*.xlsx") if not p.name.startswith("~$")]
    if not workbooks:
        print("No Excel workbooks found to process.")
        return

    app = xw.App(visible=False)
    app.display_alerts = False

    try:
        for workbook in workbooks:
            if workbook.stem.lower() == "checklist":
                print(f"Skipping workbook: {workbook.name}")
                continue
            sort_workbook_transactions(app, workbook)
    finally:
        app.quit()
        print("Completed transaction sorting.")


if __name__ == "__main__":
    main()
