#!/usr/bin/env python3
"""Convert ISO timestamp OHLC CSV to Forex Tester 6-friendly format.

Input format (header optional):
    timestamp,Open,High,Low,Close
    2000-05-30 17:27:00-05:00,0.9302,0.9302,0.9302,0.9302

Output format (no header):
    YYYYMMDD,HHMMSS,Open,High,Low,Close,Volume
"""

from __future__ import annotations

import argparse
import csv
import itertools
from datetime import datetime, timezone
from typing import Iterable, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert ISO timestamp OHLC CSV to Forex Tester 6 format.")
    parser.add_argument("infile", help="Input CSV file with ISO timestamps")
    parser.add_argument("outfile", help="Output CSV file for Forex Tester 6")
    parser.add_argument(
        "--no-utc",
        action="store_true",
        help="Do not convert timestamps to UTC (keep original timezone)",
    )
    return parser.parse_args()


def parse_timestamp(value: str, convert_to_utc: bool) -> datetime:
    dt = datetime.fromisoformat(value)
    if convert_to_utc and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt


def normalize_row(row: List[str]) -> List[str]:
    cleaned = [cell.strip() for cell in row]
    return cleaned


def iter_rows(reader: Iterable[List[str]]) -> Iterable[List[str]]:
    for row in reader:
        if not row:
            continue
        cleaned = normalize_row(row)
        if not cleaned:
            continue
        yield cleaned


def is_header(row: List[str]) -> bool:
    if not row:
        return False
    first_cell = row[0].lower()
    header_cells = {cell.lower() for cell in row[1:5] if cell}
    if {"open", "high", "low", "close"}.issubset(header_cells):
        return True
    return "date" in first_cell or "time" in first_cell or "timestamp" in first_cell


def convert(infile: str, outfile: str, convert_to_utc: bool) -> int:
    written = 0
    with open(infile, "r", newline="", encoding="utf-8") as fin, open(
        outfile, "w", newline="", encoding="utf-8"
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        row_iter = iter_rows(reader)
        first_row = next(row_iter, None)
        if first_row is None:
            return 0

        if not is_header(first_row):
            row_iter = iter_rows(itertools.chain([first_row], row_iter))

        for row in row_iter:
            if len(row) < 5:
                continue
            ts = row[0]
            try:
                dt = parse_timestamp(ts, convert_to_utc)
            except ValueError:
                continue

            date_str = dt.strftime("%Y%m%d")
            time_str = dt.strftime("%H%M%S")
            open_, high, low, close = row[1:5]
            volume = row[5] if len(row) > 5 and row[5] else "0"

            writer.writerow([date_str, time_str, open_, high, low, close, volume])
            written += 1
    return written


def main() -> None:
    args = parse_args()
    convert_to_utc = not args.no_utc
    written = convert(args.infile, args.outfile, convert_to_utc)
    print(f"Done -> {args.outfile} ({written} rows)")


if __name__ == "__main__":
    main()
