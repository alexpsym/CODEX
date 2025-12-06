"""Create a new workbook of revenue and expense transactions for a date range.

Run with ``python earnings_report.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

try:  # xlwings lets Python control Excel
    import xlwings as xw
except ImportError:  # xlwings is missing
    xw = None  # type: ignore[assignment]


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


BASE_DIR = resolve_data_dir()
ENTRY_FILE = BASE_DIR / "ENTRIES.xlsx"


def parse_range(text: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return start and end dates from a string like ``1/7/25-31/7/25``."""
    start_text, end_text = text.split("-")
    start = pd.to_datetime(start_text.strip(), dayfirst=True)
    end = pd.to_datetime(end_text.strip(), dayfirst=True)
    return start, end


def write_to_sheet(ws: xw.Sheet, df: pd.DataFrame, start_row: int = 4) -> None:
    """Write ``df`` values to ``ws`` starting at ``start_row``.

    The sheet already contains headers and formatting copied from the
    template, so only the transaction rows are written.
    """
    ws.range((start_row, 1)).value = df.values.tolist()


def dedupe_columns(columns: list[str]) -> list[str]:
    """Give repeated names a ".1", ".2", ... suffix."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for col in columns:
        count = seen.get(col, 0)
        result.append(col if count == 0 else f"{col}.{count}")
        seen[col] = count + 1
    return result


def parse_date_value(value: Any) -> pd.Timestamp | pd.NaT:
    """Return a usable ``Timestamp`` even when the sheet stores only a year."""
    if value in (None, ""):
        return pd.NaT
    if isinstance(value, (int, float)) and value < 10_000:
        return pd.Timestamp(year=int(value), month=1, day=1)
    return pd.to_datetime(value, dayfirst=True, errors="coerce")


def main() -> None:
    if xw is None:
        raise RuntimeError(
            "The xlwings package is required. Install it with 'pip install xlwings'."
        )

    print("Starting earnings report...")
    date_text = input("Enter date range (e.g. 1/7/25-31/7/25): ")
    start, end = parse_range(date_text)

    try:
        app = xw.App(visible=False)
    except Exception as exc:
        raise RuntimeError(
            "Excel could not be started. Please make sure Microsoft Excel is installed."
        ) from exc

    with app:
        print(f"Opening workbook {ENTRY_FILE}")
        wb = app.books.open(str(ENTRY_FILE))
        ws_master = wb.sheets["MASTER"]
        print("Reading transactions...")
        raw = ws_master.used_range.value
        df = pd.DataFrame(raw[3:], columns=raw[1])
        df = df.dropna(how="all")
        cols = [str(c).strip().upper() if c else "" for c in df.columns]
        if "" in cols:
            cols[cols.index("")] = "TYPE"
        df.columns = dedupe_columns(cols)
        df = df.loc[:, [c for c in df.columns if c and not c.startswith(".")]]
        df["DATE"] = df["DATE"].apply(parse_date_value)
        df = df[df["DATE"].notna()].copy()
        df = df[df["TYPE"].isin(["REV", "EXP"])]
        df = df[(df["DATE"] >= start) & (df["DATE"] <= end)]
        df = df.sort_values("DATE")
        columns = [
            "DATE",
            "TYPE",
            "ACCOUNT",
            "DESCRIPTION",
            "DEBIT",
            "CREDIT",
            "NOTES",
        ]
        if "NOTES.1" in df.columns:
            columns.append("NOTES.1")
        df = df[columns]

        template = wb.sheets["1"]
        template.api.Copy()
        wb_out = app.books.active
        ws_out = wb_out.sheets[0]
        ws_out.name = "1"
        wb.close()

        write_to_sheet(ws_out, df)
        month_text = start.strftime("%b").upper()
        file_name = f"EARNINGS {month_text} {start.year}.xlsx"
        wb_out.save(file_name)
        wb_out.close()
        print(f"Created {file_name}")


if __name__ == "__main__":
    try:
        main()
        print("Done.")
    except Exception as exc:
        print(f"Error: {exc}")
