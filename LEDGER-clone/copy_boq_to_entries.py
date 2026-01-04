"""Append BOQ transactions to the ENTRIES workbook without breaking links.

This version uses :mod:`xlwings` so that Excel itself opens and saves the file,
which keeps any links to other workbooks intact.  It assumes Excel is installed
on the machine where the script runs.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from sys import exit

import xlwings as xw

def resolve_downloads_dir() -> Path:
    """
    Input location for BOQ CSVs (per requirement).
    - Windows: C:\\Users\\User\\Downloads
    - Fallback: ~/Downloads
    - Optional override: BOQ_DOWNLOADS_DIR env var
    """
    env_path = os.environ.get("BOQ_DOWNLOADS_DIR")
    if env_path:
        return Path(env_path).expanduser()

    windows_default = Path(r"C:\Users\User\Downloads")
    if os.name == "nt" and windows_default.exists():
        return windows_default

    return Path.home() / "Downloads"

def resolve_data_dir() -> Path:
    """Return the folder that stores ENTRIES.xlsx and CSVs.

    Defaults to ``C:\\Users\\User\\Documents\\LEDGER`` (or the current
    user's ``~/Documents/LEDGER``), but can be overridden via the
    ``LEDGER_DATA_DIR`` environment variable.
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


DATA_DIR = resolve_data_dir()
ENTRY_FILE = DATA_DIR / "ENTRIES.xlsx"


def find_csv() -> Path:
    """Return the CSV file containing "transformed" in its name."""
    downloads_dir = resolve_downloads_dir()
    csv_files = list(downloads_dir.glob("*transformed*.csv"))
    if not csv_files:
        print(f"No CSV file with 'transformed' in the name was found in {downloads_dir}.")
        exit(1)

    csv_file = max(csv_files, key=lambda p: p.stat().st_mtime)
    print(f"Using CSV file: {csv_file}")
    return csv_file


def main() -> None:
    """Copy rows from the CSV into sheet '1', starting at row 4.

    Existing rows are preserved and new data is appended only below the headers.
    """
    print("Starting BOQ to ENTRIES copy...")
    print(f"Looking for ENTRIES workbook at {ENTRY_FILE}")

    if not ENTRY_FILE.exists():
        print("ENTRIES.xlsx not found. Make sure it is in the same folder as this script.")
        exit(1)

    csv_file = find_csv()

    print(f"Loading workbook {ENTRY_FILE}")
    # Make Excel visible so any issues (like disabled macros or link prompts)
    # are easier to spot. ``display_alerts`` is disabled so hidden prompts don't
    # block the script and cause the "ding" sound in Windows.
    app = xw.App(visible=True, add_book=False)
    app.display_alerts = False
    try:
        wb = app.books.open(ENTRY_FILE, update_links=False)
    except Exception:
        app.quit()
        raise
    ws = wb.sheets["1"]

    print(f"Reading rows from {csv_file}")
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # openpyxl's ws.append() would place new rows after ws.max_row, which can be
    # extremely large if the sheet has stray formatting or hidden rows. Instead,
    # look for the first empty row in column A (which should contain the date of
    # each entry) and start writing from there.
    #
    # Rows 1–3 contain headers, so always begin copying at row 4.
    start_row = 4
    while ws.range((start_row, 1)).value not in (None, ""):
        start_row += 1

    appended = 0
    for offset, row in enumerate(rows[2:]):  # Skip header and blank line
        if any(cell for cell in row):
            for col_idx, value in enumerate(row, start=1):
                target = ws.range((start_row + offset, col_idx))
                # Column A should be a date in DD/MM/YYYY format.
                if col_idx == 1 and value not in (None, ""):
                    dt = None
                    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
                        try:
                            dt = datetime.strptime(value, fmt)
                            break
                        except ValueError:
                            continue
                    if dt:
                        target.value = dt
                        target.number_format = "dd/mm/yyyy"
                    else:
                        target.value = value
                # Columns E and F (5 and 6) should be numeric, not text.
                elif col_idx in (5, 6) and value not in (None, ""):
                    try:
                        cleaned = value.replace(",", "")
                        num = Decimal(cleaned)
                        target.value = float(num)
                        decimals = (
                            len(cleaned.split(".")[1]) if "." in cleaned else 0
                        )
                        comma_fmt = "#,##0" if "," in value else "0"
                        if decimals:
                            target.number_format = f"{comma_fmt}." + "0" * decimals
                        else:
                            target.number_format = comma_fmt
                    except Exception:
                        target.value = value
                else:
                    target.value = value
            appended += 1

    print(f"Appended {appended} rows.")

    # Saving can fail if ENTRIES.xlsx is open in Excel.
    # In that case, save the data to a new file and tell the user what to do.
    try:
        wb.save()
        print(f"Workbook saved to {ENTRY_FILE}")
    except Exception:
        alt_file = ENTRY_FILE.with_name(
            f"{ENTRY_FILE.stem}_updated{ENTRY_FILE.suffix}"
        )
        wb.save(alt_file)
        print("Couldn't update ENTRIES.xlsx. It might be open in Excel.")
        print(
            f"A copy was saved as {alt_file}. Close ENTRIES.xlsx and replace it with this file."
        )
    finally:
        wb.close()
        app.quit()


if __name__ == "__main__":
    try:
        main()
        print("Done.")
    except Exception as exc:  # Catch-all to ensure errors are shown clearly
        print(f"Error: {exc}")
