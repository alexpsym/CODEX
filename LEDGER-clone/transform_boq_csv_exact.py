
import argparse
import glob
import os
from pathlib import Path
import sys
import pandas as pd


def resolve_data_dir() -> Path:
    """Return the folder that stores CSV inputs/outputs.

    Defaults to ``../LEDGER`` relative to the repository layout, but can be
    overridden with ``LEDGER_DATA_DIR``.
    """

    env_path = os.environ.get("LEDGER_DATA_DIR")
    if env_path:
        return Path(env_path).expanduser()

    default_path = Path(__file__).resolve().parents[2] / "LEDGER"
    if default_path.exists():
        return default_path

    return Path.cwd()


def main() -> None:
    print("Starting BOQ CSV transformation...")
    data_dir = resolve_data_dir()
    parser = argparse.ArgumentParser(description="Transform a BOQ CSV export")
    parser.add_argument(
        "input_path",
        nargs="?",
        help="CSV file to transform. If omitted, the script looks for the only CSV file in the folder.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(data_dir / "boq_may2025_transformed.csv"),
        help="where to write the transformed CSV",
    )
    args = parser.parse_args()

    # Decide which CSV file to read
    if args.input_path:
        input_path = Path(args.input_path)
        if not input_path.is_absolute():
            input_path = data_dir / input_path
    else:
        csv_files = glob.glob(str(data_dir / "*.csv"))
        if len(csv_files) == 1:
            input_path = Path(csv_files[0])
            print(f"🔍 Found input file: {input_path}")
        else:
            print("❌ Please place exactly one CSV file in this folder or specify the file name.")
            sys.exit(1)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = data_dir / output_path

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
