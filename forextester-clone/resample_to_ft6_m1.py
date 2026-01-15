#!/usr/bin/env python3
"""Resample ISO timestamp OHLC CSV into perfect M1 candles for Forex Tester 6.

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
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resample ISO timestamp OHLC CSV to M1 candles for FT6.")
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


def floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def normalize_row(row: List[str]) -> List[str]:
    return [cell.strip() for cell in row]


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


def write_bar(writer: csv.writer, dt_minute: datetime, o: str, h: str, l: str,
              c: str, v: str = "0") -> None:
    date_str = dt_minute.strftime("%Y%m%d")
    time_str = dt_minute.strftime("%H%M%S")
    writer.writerow([date_str, time_str, o, h, l, c, v])


def resample(infile: str, outfile: str, convert_to_utc: bool) -> int:
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

        cur_min: Optional[datetime] = None
        cur_o = cur_h = cur_l = cur_c = None
        last_close: Optional[str] = None

        for row in row_iter:
            if len(row) < 5:
                continue
            ts = row[0]
            o, h, l, c = row[1:5]

            try:
                dt = parse_timestamp(ts, convert_to_utc)
            except ValueError:
                continue

            minute = floor_to_minute(dt)

            if cur_min is None:
                cur_min = minute
                cur_o, cur_h, cur_l, cur_c = o, h, l, c
                last_close = c
                continue

            if minute == cur_min:
                cur_h = str(max(float(cur_h), float(h)))
                cur_l = str(min(float(cur_l), float(l)))
                cur_c = c
                last_close = c
                continue

            write_bar(writer, cur_min, cur_o, cur_h, cur_l, cur_c, "0")
            written += 1
            last_close = cur_c

            gap_minute = cur_min + timedelta(minutes=1)
            while gap_minute < minute:
                if last_close is None:
                    break
                write_bar(writer, gap_minute, last_close, last_close, last_close,
                          last_close, "0")
                written += 1
                gap_minute += timedelta(minutes=1)

            cur_min = minute
            cur_o, cur_h, cur_l, cur_c = o, h, l, c
            last_close = c

        if cur_min is not None:
            write_bar(writer, cur_min, cur_o, cur_h, cur_l, cur_c, "0")
            written += 1

    return written


def main() -> None:
    args = parse_args()
    convert_to_utc = not args.no_utc
    written = resample(args.infile, args.outfile, convert_to_utc)
    print(f"Done -> {args.outfile} ({written} rows)")


if __name__ == "__main__":
    main()
