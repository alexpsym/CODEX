
import argparse
import os
from pathlib import Path
import sys
import re
import pandas as pd


def resolve_data_dir() -> Path:
    """Return the folder that stores CSV inputs/outputs.

    Defaults to ``C:\\Users\\User\\Documents\\LEDGER`` (or the current
    user's ``~/Documents/LEDGER``), but can be overridden with
    ``LEDGER_DATA_DIR``.
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

    return Path.cwd()

def resolve_downloads_dir() -> Path:
    """Return the folder to search for the BOQ CSV export (input).

    - Windows default: C:\\Users\\User\\Downloads (per your requirement)
    - Otherwise: ~/Downloads
    - Override with BOQ_DOWNLOADS_DIR if needed
    """
    env_path = os.environ.get("BOQ_DOWNLOADS_DIR")
    if env_path:
        return Path(env_path).expanduser()

    windows_default = Path(r"C:\Users\User\Downloads")
    if os.name == "nt" and windows_default.exists():
        return windows_default

    return Path.home() / "Downloads"

BOQ_NAME_RE = re.compile(r"^\d{8}_\d{8}.*\.csv$", re.IGNORECASE)

def pick_boq_csv_from_downloads(downloads_dir: Path) -> Path:
    """
    Select BOQ export CSV by filename pattern like:
      93270421_20260104.csv  (or 93270421_20260104_*.csv)
    This avoids accidentally picking other CSVs (e.g., bybit_history_*.csv).
    """
    all_csvs = sorted(downloads_dir.glob("*.csv"))
    boq_csvs = [p for p in all_csvs if BOQ_NAME_RE.match(p.name)]

    if not boq_csvs:
        sample = "\n".join(f"  - {p.name}" for p in all_csvs[:20]) or "  (none)"
        raise FileNotFoundError(
            f"No BOQ CSV found in {downloads_dir} matching pattern ########_########*.csv\n"
            f"CSV files seen (first 20):\n{sample}"
        )

    if len(boq_csvs) == 1:
        return boq_csvs[0]

    # If multiple BOQ-looking files exist, choose the most recently modified among *matches*.
    return max(boq_csvs, key=lambda p: p.stat().st_mtime)


def main() -> None:
    print("Starting BOQ CSV transformation...")
    data_dir = resolve_data_dir()
    downloads_dir = resolve_downloads_dir()
    parser = argparse.ArgumentParser(description="Transform a BOQ CSV export")
    parser.add_argument(
        "input_path",
        nargs="?",
        help="CSV file to transform. If omitted, the script looks in Downloads for the most recent CSV.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="boq_transformed.csv",
        help="Output CSV filename. If relative, it is written into the Downloads directory.",
    )
    args = parser.parse_args()

    # Decide which CSV file to read
    if args.input_path:
        input_path = Path(args.input_path)
        if not input_path.is_absolute():
            # Required behavior: look in Downloads for the input file
            input_path = downloads_dir / input_path
    else:
        try:
            input_path = pick_boq_csv_from_downloads(downloads_dir)
            print(f"🔍 Using BOQ CSV from Downloads: {input_path}")
        except FileNotFoundError as e:
            print(f"❌ {e}")
            sys.exit(1)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        # Required behavior: save transformed output into Downloads
        output_path = downloads_dir / output_path

    print(f"Reading {input_path}")
    df = pd.read_csv(input_path)

    # Ensure dates use Australian format DD/MM/YYYY
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True).dt.strftime('%d/%m/%Y')

    # Step 1: Drop Balance column
    if 'Balance' in df.columns:
        df.drop(columns=['Balance'], inplace=True)

    # Step 2: Remove 'PENDING' rows
    df = df[~df['Description'].str.contains('PENDING', case=False, na=False)]

    # Step 3: Sort A-Z by Description
    df.sort_values(by='Description', inplace=True)

    # Step 4: Insert 'Type' and 'Account' columns
    df.insert(1, 'Type', 'ASSET')
    df.insert(2, 'Account', 'BOQ')

    # Step 5: Swap Debit and Credit values
    df[['Debit', 'Credit']] = df[['Credit', 'Debit']]

    # Save a copy of the modified asset block
    asset_block = df.copy()

    # Step 6: Create duplicate block
    duplicate_block = asset_block.copy()
    duplicate_block[['Debit', 'Credit']] = duplicate_block[['Credit', 'Debit']]
    duplicate_block['Type'] = ''
    duplicate_block['Account'] = ''

    # Step 7: Assemble final DataFrame
    blank_row = pd.DataFrame([[''] * len(df.columns)], columns=df.columns)
    final_df = pd.concat([asset_block, blank_row, duplicate_block], ignore_index=True)

    # Step 8: Add blank row after header
    final_df_with_header_space = pd.concat([
        pd.DataFrame([final_df.columns], columns=final_df.columns),
        pd.DataFrame([[''] * len(final_df.columns)], columns=final_df.columns),
        final_df
    ], ignore_index=True)

    # Save final output
    final_df_with_header_space.to_csv(output_path, index=False, header=False)

    print(f"✅ Output written to: {output_path}")


if __name__ == "__main__":
    try:
        main()
        print("Done.")
    except Exception as exc:
        print(f"Error: {exc}")
