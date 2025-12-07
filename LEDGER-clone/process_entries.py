"""Move rows from sheet "1" into "MASTER" and account workbooks.

Run with ``python process_entries.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import xlwings as xw

def resolve_data_dir() -> Path:
    """Return the folder that stores Excel workbooks.

    By default this points to ``../LEDGER`` relative to the repository layout
    (e.g. ``C:\\Users\\User\\Documents\\LEDGER`` when the scripts live in
    ``...\\GPT\\CODEX\\LEDGER-clone``).  Set ``LEDGER_DATA_DIR`` to override
    this location.
    """

    env_path = os.environ.get("LEDGER_DATA_DIR")
    if env_path:
        return Path(env_path).expanduser()

    default_path = Path(__file__).resolve().parent.parent / "LEDGER"
    if default_path.exists():
        return default_path

    return Path(__file__).resolve().parent


BASE_DIR = resolve_data_dir()
ENTRY_FILE = BASE_DIR / "ENTRIES.xlsx"
# Map column B values to the workbook that stores that type of account
TYPE_TO_BOOK = {
    "ACC PAY": BASE_DIR / "ACC PAY.xlsx",
    "ACC REC": BASE_DIR / "ACC REC.xlsx",
    "ASSET": BASE_DIR / "ASSET.xlsx",
    "EXP": BASE_DIR / "EXPENSES.xlsx",
    "REV": BASE_DIR / "REVENUE.xlsx",
}


def append_to_account_book(app: xw.App, cache: dict, type_name: str, account: str, row: list) -> None:
    """Add ``row`` to the sheet named ``account`` in the correct workbook."""
    book_path = TYPE_TO_BOOK.get(type_name)
    if not book_path:
        return  # Skip unknown types
    book_path_str = str(book_path)
    wb = cache.get(book_path_str)
    if wb is None:
        wb = app.books.open(book_path_str, update_links=False)
        cache[book_path_str] = wb
    if account in [s.name for s in wb.sheets]:
        ws = wb.sheets[account]
    else:
        ws = wb.sheets.add(account)
        ws.range("A1").value = [
            "DATE",
            "TYPE",
            "ACCOUNT",
            "DESCRIPTION",
            "DEBIT",
            "CREDIT",
            "NOTES",
            "NOTES.1",
        ]
    last_row = ws.range("A" + str(ws.cells.last_cell.row)).end("up").row + 1
    date_cell = ws.range(f"A{last_row}")
    date_cell.number_format = "dd/mm/yyyy"  # keep AUS format with 4-digit year
    date_val = row[0].to_pydatetime() if hasattr(row[0], "to_pydatetime") else row[0]
    date_cell.value = date_val
    ws.range(f"B{last_row}").value = row[1:]


def main() -> None:
    print("Starting entry processing...")
    # ``visible=True`` shows the Excel window so the user can see what is
    # happening. We do not close the app at the end, leaving the workbook open
    # for review.
    app = xw.App(visible=True)
    app.display_alerts = False  # avoid Excel pop-ups that halt the script
    print(f"Opening workbook {ENTRY_FILE}")
    wb = app.books.open(str(ENTRY_FILE), update_links=False)
    ws_source = wb.sheets["1"]
    ws_master = wb.sheets["MASTER"]

    print("Reading new entries...")
    all_rows = ws_source.used_range.options(pd.DataFrame, header=False, index=False).value
    header_idx = all_rows[all_rows.iloc[:, 0].astype(str).str.strip().str.upper() == "DATE"].index
    if header_idx.empty:
        raise KeyError("DATE column not found in sheet '1'")
    header_idx = header_idx[0]
    headers = [str(c).strip().upper() for c in all_rows.loc[header_idx]]
    df = all_rows.iloc[header_idx + 1:].copy()
    df.columns = headers
    df = df.iloc[:, :8]
    df["DATE"] = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df = df[df["DATE"].notna()]
    rows_to_clear = (df.index + 1).tolist()
    df = df.reset_index(drop=True)

    account_books: dict[str, xw.Book] = {}
    master_row = ws_master.range("A" + str(ws_master.cells.last_cell.row)).end("up").row + 1
    for row in df.itertuples(index=False, name=None):
        date_cell = ws_master.range(f"A{master_row}")
        date_cell.number_format = "dd/mm/yyyy"  # AUS format with 4-digit year
        date_val = row[0].to_pydatetime() if hasattr(row[0], "to_pydatetime") else row[0]
        date_cell.value = date_val
        ws_master.range(f"B{master_row}").value = list(row[1:])
        master_row += 1
        append_to_account_book(app, account_books, str(row[1]).strip(), str(row[2]).strip(), list(row))

    for row_idx in rows_to_clear:
        ws_source.range(f"A{row_idx}:H{row_idx}").value = None

    wb.save()
    # Save and close any account workbooks that were opened. The main
    # ``ENTRIES`` workbook stays open for the user.
    for book in account_books.values():
        book.save()
        book.close()
    print("Entry processing complete. The ENTRIES workbook remains open.")


if __name__ == "__main__":
    try:
        main()
        print("Done.")
    except Exception as exc:
        print(f"Error: {exc}")
