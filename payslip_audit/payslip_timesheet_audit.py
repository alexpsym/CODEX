#!/usr/bin/env python3
"""Compare a payslip PDF with OCR'd timesheet screenshots and produce an audit report."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union
from xml.sax.saxutils import escape

import pdfplumber
import pytesseract
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CURRENCY_RE = re.compile(r"[^\d\-.]")
SPACE_RE = re.compile(r"\s+")
DATE_SUFFIX_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.IGNORECASE)
TIMESHEET_DATE_PATTERNS = [
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})(?:\s+(?P<year>\d{2,4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{2,4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<day>\d{1,2})[/-](?P<month>\d{1,2})(?:[/-](?P<year>\d{2,4}))?",
        re.IGNORECASE,
    ),
]
SHIFT_TOTAL_RE = re.compile(r"Shift\s+Total", re.IGNORECASE)
TIMESHEET_KEYWORD_RE = re.compile(
    r"(total|hours|worked|shift|ordinary|overtime|penalty|allowance)", re.IGNORECASE
)
DATE_TOKEN_RE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")
DATE_FORMATS = [
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d %b %y",
    "%d %B %y",
    "%a %d %b %Y",
    "%a %d %b %y",
]

# File discovery patterns
PAYSLIP_GLOB = "*.pdf"
TIMESHEET_GLOBS = ("*.jpg", "*.jpeg", "*.png")

SHIFT_BREAK_THRESHOLD = Decimal("0.75")


@dataclass
class PayslipItem:
    date: Optional[date]
    category: str
    hours: Decimal
    rate: Optional[Decimal]
    amount: Optional[Decimal]


@dataclass
class PayslipData:
    start: date
    end: date
    hourly_rate: Decimal
    items: List[PayslipItem]
    text: str


@dataclass
class TimesheetEntry:
    hours: Decimal
    label: str
    counts: bool
    raw: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean(value: Optional[str]) -> str:
    return SPACE_RE.sub(" ", value.strip()) if value else ""


def parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    token = CURRENCY_RE.sub("", clean(value).replace(",", ""))
    if not token or token in {"-", "--"}:
        return None
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def parse_hours(value: Optional[str]) -> Decimal:
    text = clean(value).lower()
    if not text:
        return Decimal("0")
    text = text.replace("hrs", "h").replace("hr", "h").replace("hours", "h")
    text = text.replace("minutes", "m").replace("mins", "m")

    match = re.fullmatch(r"(?P<h>\d+):(?P<m>\d{2})", text)
    if match:
        return Decimal(match.group("h")) + Decimal(match.group("m")) / Decimal(60)

    match = re.fullmatch(r"(?:(?P<h>\d+)\s*h)?\s*(?:(?P<m>\d+)\s*m)?", text)
    if match and (match.group("h") or match.group("m")):
        return Decimal(match.group("h") or "0") + Decimal(match.group("m") or "0") / Decimal(60)

    match = re.search(r"(?P<h>\d+)\s*h(?:\s*(?P<m>\d+)\s*m)?", text)
    if match:
        return Decimal(match.group("h")) + Decimal(match.group("m") or "0") / Decimal(60)

    match = re.search(r"(?<!\d)(?P<m>\d+)\s*m(?![a-z])", text)
    if match and "h" not in text:
        return Decimal(match.group("m")) / Decimal(60)

    match = re.search(r"(?<![\d.])(\d+\.\d+)", text)
    if match:
        return Decimal(match.group(1))

    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    token = clean(DATE_SUFFIX_RE.sub(r"\1", value.replace(",", " ")))
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def resolve_partial_date(day: int, month_text: str, pay_period: Tuple[date, date]) -> Optional[date]:
    start, end = pay_period
    for year in {start.year, end.year, start.year - 1, end.year + 1}:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                candidate = datetime.strptime(f"{day} {month_text} {year}", fmt).date()
            except ValueError:
                continue
            if start <= candidate <= end:
                return candidate
    return None


def resolve_partial_numeric_date(day: int, month: int, pay_period: Tuple[date, date]) -> Optional[date]:
    start, end = pay_period
    for year in {start.year, end.year, start.year - 1, end.year + 1}:
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if start <= candidate <= end:
            return candidate
    return None


def resolve_inline_date(text: str, pay_period: Tuple[date, date]) -> Optional[date]:
    parsed = parse_date(text)
    if parsed:
        return parsed
    match = re.search(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})", text)
    if match:
        return resolve_partial_date(int(match.group("day")), match.group("month"), pay_period)
    return None


def parse_timesheet_date(line: str, pay_period: Tuple[date, date]) -> Optional[date]:
    cleaned = clean(line)
    for pattern in TIMESHEET_DATE_PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue

        year_text = match.group("year")
        day_text = match.group("day")
        month_text = match.group("month")
        if not day_text or not month_text:
            continue

        if month_text.isdigit():
            day_val = int(day_text)
            month_val = int(month_text)
            if year_text:
                candidate = parse_date(f"{day_val}/{month_val}/{year_text}")
            else:
                candidate = resolve_partial_numeric_date(day_val, month_val, pay_period)
        else:
            if year_text:
                candidate = parse_date(f"{day_text} {month_text} {year_text}")
            else:
                candidate = resolve_partial_date(int(day_text), month_text, pay_period)

        if candidate and pay_period[0] <= candidate <= pay_period[1]:
            return candidate

    token_match = DATE_TOKEN_RE.search(cleaned)
    if token_match:
        candidate = parse_date(token_match.group(0))
        if candidate and pay_period[0] <= candidate <= pay_period[1]:
            return candidate

    return None


def fmt_hours(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def fmt_signed_hours(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "+" if q >= 0 else "-"
    return f"{sign}{format(abs(q), '.2f')}"


def fmt_currency(value: Decimal) -> str:
    return f"${format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '.2f')}"


def fmt_signed_currency(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "+" if q >= 0 else "-"
    return f"{sign}${format(abs(q), '.2f')}"


def normalize_period_token(token: str) -> str:
    cleaned = clean(token)
    cleaned = re.sub(
        r"^(?:pay\s*period\s*)?(?:from|to|period\s*start|period\s*(?:end|finish)|start|end)\b[:\s-]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


# ---------------------------------------------------------------------------
# Payslip parsing
# ---------------------------------------------------------------------------

def find_pay_period(text: str) -> Tuple[date, date]:
    sanitized_text = text.replace("\xa0", " ")
    patterns = [
        r"Pay\s*Period\s*(?:From\s*)?[:\-]?\s*(?P<start>.+?)\s*(?:to|-|through)\s*(?P<end>.+)",
        r"Period\s*Start\s*[:\-]?\s*(?P<start>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).*?Period\s*(?:End|Finish)\s*[:\-]?\s*(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"From\s*(?P<start>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).*?To\s*(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, sanitized_text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        start = parse_date(normalize_period_token(match.group("start")))
        end = parse_date(normalize_period_token(match.group("end")))
        if start and end:
            return start, end

    start_match = re.search(
        r"Period\s*Start\s*[:\-]?\s*(?P<value>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        sanitized_text,
        re.IGNORECASE,
    )
    end_match = re.search(
        r"Period\s*(?:End|Finish)\s*[:\-]?\s*(?P<value>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        sanitized_text,
        re.IGNORECASE,
    )
    if start_match and end_match:
        start = parse_date(normalize_period_token(start_match.group("value")))
        end = parse_date(normalize_period_token(end_match.group("value")))
        if start and end:
            return start, end

    for match in re.finditer(r"pay\s*period", sanitized_text, re.IGNORECASE):
        window = sanitized_text[match.end() : match.end() + 200]
        tokens = DATE_TOKEN_RE.findall(window)
        if len(tokens) >= 2:
            start = parse_date(tokens[0])
            end = parse_date(tokens[1])
            if start and end:
                return start, end

    lines = sanitized_text.splitlines()
    for idx, line in enumerate(lines):
        if not re.search(r"pay\s*period", line, re.IGNORECASE):
            continue
        snippet = " ".join(lines[idx : idx + 3])
        tokens = DATE_TOKEN_RE.findall(snippet)
        if len(tokens) >= 2:
            start = parse_date(tokens[0])
            end = parse_date(tokens[1])
            if start and end:
                return start, end

    raise ValueError("Pay period could not be found on the payslip.")


def extract_from_tables(tables: List[List[List[str]]], pay_period: Tuple[date, date]) -> List[PayslipItem]:
    items: List[PayslipItem] = []
    for table in tables:
        header_keys: Optional[List[str]] = None
        for row in table:
            if not row:
                continue
            cells = [clean(cell) for cell in row]
            if not any(cells):
                continue
            normalized = [re.sub(r"[^a-z]", "", cell.lower()) for cell in cells]
            if header_keys is None and any("date" in token for token in normalized) and any(
                any(key in token for key in ("unit", "hour", "qty")) for token in normalized
            ):
                header_keys = normalized
                continue
            if header_keys is None:
                continue

            date_val: Optional[date] = None
            category: Optional[str] = None
            hours_text: Optional[str] = None
            rate_text: Optional[str] = None
            amount_text: Optional[str] = None

            for idx, token in enumerate(header_keys):
                value = cells[idx] if idx < len(cells) else ""
                if not value:
                    continue
                if date_val is None and ("date" in token or "period" in token):
                    date_val = resolve_inline_date(value, pay_period)
                elif category is None and any(key in token for key in ("earning", "description", "type", "category", "item")):
                    category = value
                elif hours_text is None and any(key in token for key in ("unit", "hour", "qty")):
                    hours_text = value
                elif rate_text is None and "rate" in token:
                    rate_text = value
                elif amount_text is None and any(key in token for key in ("amount", "total", "value")):
                    amount_text = value

            if category is None:
                for value in cells:
                    if value and parse_decimal(value) is None:
                        category = value
                        break

            if category and category.lower().startswith("total"):
                continue

            items.append(
                PayslipItem(
                    date=date_val,
                    category=category or "Uncategorised",
                    hours=parse_hours(hours_text),
                    rate=parse_decimal(rate_text),
                    amount=parse_decimal(amount_text),
                )
            )
    return items


def extract_from_text(text: str, pay_period: Tuple[date, date]) -> List[PayslipItem]:
    items: List[PayslipItem] = []
    line_pattern = re.compile(
        r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2}(?:\s+\d{2,4})?)\s+"
        r"(?P<category>[A-Za-z0-9 #()/&+,\-]+?)\s+"
        r"(?P<hours>\d+(?::\d{2})?|\d+\.\d+|\d+\s*h\s*\d+\s*m)\s+"
        r"(?P<rate>\$?\d+\.\d{2})?"
        r"(?:\s+(?P<amount>\$?\d+\.\d{2}))?"
    )
    for match in line_pattern.finditer(text):
        items.append(
            PayslipItem(
                date=resolve_inline_date(match.group("date"), pay_period),
                category=clean(match.group("category")) or "Uncategorised",
                hours=parse_hours(match.group("hours")),
                rate=parse_decimal(match.group("rate")),
                amount=parse_decimal(match.group("amount")),
            )
        )

    if items:
        return items

    fallback_pattern = re.compile(
        r"(?P<category>[A-Za-z0-9 #()/&+,\-]+?)\s+"
        r"(?P<hours>\d+(?::\d{2})?|\d+\.\d+|\d+\s*h\s*\d+\s*m)\s+"
        r"(?P<rate>\$?\d+\.\d{2})\s+"
        r"(?P<amount>\$?\d+\.\d{2})"
    )
    for match in fallback_pattern.finditer(text):
        category = clean(match.group("category"))
        if category.lower().startswith("total"):
            continue
        items.append(
            PayslipItem(
                date=None,
                category=category or "Uncategorised",
                hours=parse_hours(match.group("hours")),
                rate=parse_decimal(match.group("rate")),
                amount=parse_decimal(match.group("amount")),
            )
        )
    return items


def determine_rate(text: str, items: List[PayslipItem]) -> Decimal:
    match = re.search(r"Hourly\s+Rate\s*[:$]*\s*(\d+\.\d{2})", text, re.IGNORECASE)
    if match:
        return Decimal(match.group(1))

    for item in items:
        if item.rate and item.category.lower().startswith("base"):
            return item.rate

    for item in items:
        if item.rate:
            return item.rate

    total_amount = Decimal("0")
    total_hours = Decimal("0")
    for item in items:
        if item.amount is not None:
            total_amount += item.amount
        total_hours += item.hours

    if total_hours > 0 and total_amount > 0:
        return (total_amount / total_hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    raise ValueError("Unable to determine hourly rate from the payslip.")


def parse_payslip(path: Path) -> PayslipData:
    with pdfplumber.open(str(path)) as pdf:
        texts: List[str] = []
        tables: List[List[List[str]]] = []
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
            page_tables = page.extract_tables() or []
            tables.extend(page_tables)

    text = "\n".join(texts)
    pay_period = find_pay_period(text)

    items = extract_from_tables(tables, pay_period)
    if not items:
        items = extract_from_text(text, pay_period)
    if not items:
        raise ValueError("No payslip work entries were detected.")

    rate = determine_rate(text, items)
    return PayslipData(start=pay_period[0], end=pay_period[1], hourly_rate=rate, items=items, text=text)


# ---------------------------------------------------------------------------
# Timesheet parsing
# ---------------------------------------------------------------------------

def extract_timesheet_entries(
    text: str, pay_period: Tuple[date, date], entries: Dict[date, List[TimesheetEntry]]
) -> None:
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    current: Optional[date] = None
    pending_label: Optional[str] = None
    pending_force_counts: Optional[bool] = None
    pending_shift_hold = False
    recorded_counts: Dict[date, bool] = {}
    seen_entries: Dict[date, Set[Tuple[str, Decimal, str]]] = {}

    for line in lines:
        dt = parse_timesheet_date(line, pay_period)
        if dt is not None:
            current = dt
            entries.setdefault(dt, [])
            recorded_counts.setdefault(dt, False)
            seen_entries.setdefault(dt, set())
            pending_label = None
            pending_force_counts = None
            pending_shift_hold = False
            continue

        if any(pattern.match(line) for pattern in TIMESHEET_DATE_PATTERNS):
            current = None
            pending_label = None
            pending_force_counts = None
            pending_shift_hold = False
            continue

        if current is None:
            continue

        low = line.lower()
        if low.startswith("lunch break") or low.startswith("unpaid break"):
            pending_label = None
            pending_force_counts = None
            pending_shift_hold = False
            continue

        hours = parse_hours(line)
        if hours > 0:
            label = "Shift Total" if SHIFT_TOTAL_RE.search(line) else (pending_label or line)
            keyword_match = TIMESHEET_KEYWORD_RE.search(label) or TIMESHEET_KEYWORD_RE.search(line)
            counts = bool(keyword_match) or not recorded_counts.get(current, False)

            if pending_force_counts is not None:
                counts = pending_force_counts

            raw_prefix = pending_label if pending_label else label
            raw_text = f"{raw_prefix}: {line}" if pending_label else line
            raw_key = clean(raw_text).lower()

            if (
                pending_shift_hold
                and label.lower().startswith("shift total")
                and counts
                and hours < SHIFT_BREAK_THRESHOLD
            ):
                counts = False

            key = (label.lower(), hours, raw_key)
            if key not in seen_entries[current]:
                entries[current].append(
                    TimesheetEntry(hours=hours, label=label, counts=counts, raw=raw_text)
                )
                seen_entries[current].add(key)
            if counts:
                recorded_counts[current] = True

            if pending_shift_hold and not counts:
                pending_force_counts = True
                pending_label = "Shift Total"
                pending_shift_hold = True
            else:
                pending_label = None
                pending_force_counts = None
                pending_shift_hold = False
        else:
            if TIMESHEET_KEYWORD_RE.search(line):
                pending_label = line
                pending_force_counts = None
                pending_shift_hold = False
            else:
                pending_label = None
                pending_force_counts = None
                pending_shift_hold = False

    if current is not None:
        entries.setdefault(current, [])


def parse_timesheets(paths: Iterable[Path], pay_period: Tuple[date, date]) -> Dict[date, List[TimesheetEntry]]:
    entries: Dict[date, List[TimesheetEntry]] = {}
    for path in paths:
        image = Image.open(path).convert("L")
        text = pytesseract.image_to_string(image, lang="eng", config="--psm 6")
        extract_timesheet_entries(text, pay_period, entries)
    return entries


def discover_files(
    working_dir: Path,
    payslip_arg: Optional[Path],
    timesheet_args: Optional[List[Path]],
) -> Tuple[Path, List[Path]]:
    """Resolve payslip and timesheet paths.

    When arguments are provided, validate them; otherwise, look for a single PDF
    and one or more timesheet images in ``working_dir``.
    """

    if payslip_arg is not None:
        payslip_path = payslip_arg.expanduser().resolve()
        if not payslip_path.exists():
            raise SystemExit(f"Payslip PDF not found: {payslip_path}")
    else:
        candidates = sorted(working_dir.glob(PAYSLIP_GLOB))
        if not candidates:
            raise SystemExit(
                "No payslip PDF supplied and none found in the current directory. "
                "Add a single .pdf file or use --payslip to specify one explicitly."
            )
        if len(candidates) > 1:
            names = ", ".join(str(path.name) for path in candidates)
            raise SystemExit(
                "Multiple PDF files detected in the current directory. "
                "Use --payslip to choose one explicitly. Found: " + names
            )
        payslip_path = candidates[0].resolve()

    if timesheet_args:
        timesheet_paths = []
        for path in timesheet_args:
            resolved = path.expanduser().resolve()
            if not resolved.exists():
                raise SystemExit(f"Timesheet image not found: {resolved}")
            timesheet_paths.append(resolved)
    else:
        discovered: List[Path] = []
        seen: Set[Path] = set()
        for pattern in TIMESHEET_GLOBS:
            for path in sorted(working_dir.glob(pattern)):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    discovered.append(resolved)
        timesheet_paths = discovered

    if not timesheet_paths:
        raise SystemExit(
            "No timesheet images supplied and none found in the current directory. "
            "Add JPG or PNG screenshots or use --timesheet to list them."
        )

    return payslip_path, timesheet_paths


# ---------------------------------------------------------------------------
# Summaries and comparison
# ---------------------------------------------------------------------------

def summarise_payslip(items: List[PayslipItem], pay_period: Tuple[date, date]) -> Tuple[Dict[Union[date, str], Dict[str, object]], Optional[str]]:
    totals: Dict[Union[date, str], Dict[str, object]] = {}
    has_dated_entries = False
    aggregated_details: List[str] = []
    aggregated_hours = Decimal("0")

    for item in items:
        detail = f"{item.category} ({fmt_hours(item.hours)}h"
        if item.rate is not None:
            detail += f" @ {fmt_currency(item.rate)}"
        if item.amount is not None:
            detail += f", {fmt_currency(item.amount)}"
        detail += ")"
        aggregated_details.append(detail)
        aggregated_hours += item.hours

        if item.date is None:
            continue

        has_dated_entries = True
        entry = totals.setdefault(item.date, {"hours": Decimal("0"), "categories": []})
        entry["hours"] += item.hours
        entry["categories"].append(detail)

    if has_dated_entries:
        return totals, None

    label = f"{pay_period[0].strftime('%Y-%m-%d')} to {pay_period[1].strftime('%Y-%m-%d')}"
    totals[label] = {"hours": aggregated_hours, "categories": aggregated_details}
    return totals, label


def summarise_timesheet(entries: Dict[date, List[TimesheetEntry]], aggregated_label: Optional[str]) -> Dict[Union[date, str], Dict[str, object]]:
    def _format_detail(prefix: str, entry: TimesheetEntry, include_date: bool = False) -> str:
        base = entry.raw.strip() if entry.raw else entry.label
        hours_token = fmt_hours(entry.hours)
        if hours_token not in base and f"{hours_token}h" not in base:
            base = f"{base} {hours_token}h"
        if include_date:
            base = f"{prefix}: {base}" if prefix else base
        return base

    if aggregated_label:
        total = Decimal("0")
        details: List[str] = []
        for dt in sorted(entries):
            logs = entries[dt]
            daily_total = Decimal("0")
            for entry in logs:
                if not entry.counts:
                    continue
                formatted = _format_detail(dt.strftime("%Y-%m-%d"), entry, include_date=True)
                daily_total += entry.hours
                details.append(formatted)
            total += daily_total
        return {aggregated_label: {"hours": total, "details": details}}

    totals: Dict[Union[date, str], Dict[str, object]] = {}
    for dt, logs in entries.items():
        total = Decimal("0")
        details: List[str] = []
        for entry in logs:
            if not entry.counts:
                continue
            formatted = _format_detail("", entry)
            total += entry.hours
            details.append(formatted)
        totals[dt] = {"hours": total, "details": details}
    return totals


def compare_dates(payslip_totals: Dict[Union[date, str], Dict[str, object]], timesheet_totals: Dict[Union[date, str], Dict[str, object]], aggregated_label: Optional[str]) -> None:
    if aggregated_label:
        return

    payslip_dates = {dt for dt in payslip_totals if isinstance(dt, date)}
    timesheet_dates = {dt for dt in timesheet_totals if isinstance(dt, date)}

    missing_payslip = sorted(timesheet_dates - payslip_dates)
    if missing_payslip:
        msg = ", ".join(dt.strftime("%Y-%m-%d (%a)") for dt in missing_payslip)
        raise SystemExit(f"Timesheet contains dates not on the payslip: {msg}")

    missing_timesheet = sorted(payslip_dates - timesheet_dates)
    if missing_timesheet:
        msg = ", ".join(dt.strftime("%Y-%m-%d (%a)") for dt in missing_timesheet)
        raise SystemExit(f"Payslip contains dates missing from the timesheet: {msg}")


def build_rows(
    payslip_totals: Dict[Union[date, str], Dict[str, object]],
    timesheet_totals: Dict[Union[date, str], Dict[str, object]],
    hourly_rate: Decimal,
    aggregated_label: Optional[str],
) -> Tuple[List[List[str]], List[List[str]]]:
    rows_pdf: List[List[str]] = []
    rows_console: List[List[str]] = []

    keys: List[Union[date, str]]
    if aggregated_label:
        keys = [aggregated_label]
    else:
        keys = sorted(
            [key for key in payslip_totals.keys() if isinstance(key, date)],
            key=lambda dt: dt,
        )

    for key in keys:
        pay = payslip_totals.get(key, {"hours": Decimal("0"), "categories": []})
        time = timesheet_totals.get(key, {"hours": Decimal("0"), "details": []})
        pay_details = pay["categories"] or ["0.00 h recorded"]
        time_details = time.get("details", []) or ["0.00 h recorded"]
        diff_hours = time["hours"] - pay["hours"]
        diff_aud = diff_hours * hourly_rate

        if isinstance(key, date):
            label = key.strftime("%Y-%m-%d (%a)")
        else:
            label = key

        rows_pdf.append(
            [
                label,
                fmt_hours(pay["hours"]),
                "\n".join(pay_details),
                fmt_hours(time["hours"]),
                "\n".join(time_details),
                fmt_signed_hours(diff_hours),
                fmt_signed_currency(diff_aud),
            ]
        )
        rows_console.append(
            [
                label,
                fmt_hours(pay["hours"]),
                "; ".join(pay_details),
                fmt_hours(time["hours"]),
                "; ".join(time_details),
                fmt_signed_hours(diff_hours),
                fmt_signed_currency(diff_aud),
            ]
        )

    return rows_pdf, rows_console


def collect_undated(items: List[PayslipItem]) -> List[str]:
    details: List[str] = []
    for item in items:
        if item.date is not None:
            continue
        parts = [item.category, f"{fmt_hours(item.hours)}h"]
        if item.rate is not None:
            parts.append(f"rate {fmt_currency(item.rate)}")
        if item.amount is not None:
            parts.append(f"amount {fmt_currency(item.amount)}")
        details.append(" | ".join(parts))
    return details


def determine_status(hours_diff: Decimal, hourly_rate: Decimal) -> Tuple[str, Decimal]:
    aud_diff = (hours_diff * hourly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tolerance = Decimal("0.005")
    if hours_diff > tolerance:
        return f"Underpayment of {fmt_currency(aud_diff)} (worked more hours than paid)", aud_diff
    if hours_diff < -tolerance:
        return f"Overpayment of {fmt_currency(-aud_diff)} (paid for more hours than worked)", aud_diff
    return "No discrepancy (hours match)", aud_diff


def print_console(
    payslip: PayslipData,
    rows_console: List[List[str]],
    totals: Dict[str, str],
    status: str,
    undated: List[str],
    aggregated_label: Optional[str],
) -> None:
    print("Timesheet vs Payslip audit")
    print(f"Pay period: {payslip.start.strftime('%d %b %Y')} - {payslip.end.strftime('%d %b %Y')}")
    headers = [
        "Date" if not aggregated_label else "Period",
        "Payslip Hours",
        "Payslip Categories",
        "Timesheet Hours",
        "Timesheet Details",
        "Hours Δ",
        "AUD Δ",
    ]
    table = [headers] + rows_console
    widths = [max(len(row[col]) for row in table) for col in range(len(headers))]
    for idx, row in enumerate(table):
        print(" | ".join(row[col].ljust(widths[col]) for col in range(len(headers))))
        if idx == 0:
            print("-+-".join("-" * widths[col] for col in range(len(headers))))

    print(f"\nPayslip total hours: {totals['payslip_hours']}")
    print(f"Timesheet total hours: {totals['timesheet_hours']}")
    print(f"Difference in hours: {totals['hours_diff']}")
    print(f"Hourly rate: {totals['hourly_rate']}")
    print(f"Difference in AUD: {totals['aud_diff']}")

    if undated:
        print("\nUndated payslip items included in totals:")
        for line in undated:
            print(f"  - {line}")

    print(f"\nDiscrepancy status: {status}")


def make_pdf(
    path: Path,
    payslip: PayslipData,
    rows_pdf: List[List[str]],
    totals: Dict[str, str],
    status: str,
    undated: List[str],
    aggregated_label: Optional[str],
) -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    table_header_style = ParagraphStyle(
        "AuditTableHeader",
        parent=styles["Heading5"],
        fontSize=9,
        leading=11,
        alignment=1,
    )
    table_body_style = ParagraphStyle(
        "AuditTableBody",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=11,
    )
    totals_label_style = ParagraphStyle(
        "AuditTotalsLabel",
        parent=styles["BodyText"],
        fontSize=9,
        leading=11,
    )
    totals_value_style = ParagraphStyle(
        "AuditTotalsValue",
        parent=styles["BodyText"],
        fontSize=9,
        leading=11,
        alignment=2,
    )

    elements = [
        Paragraph("Timesheet vs Payslip Audit", styles["Title"]),
        Paragraph(
            f"Pay period: {payslip.start.strftime('%d %b %Y')} - {payslip.end.strftime('%d %b %Y')}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    table_headers = [
        "Date" if not aggregated_label else "Period",
        "Payslip Hours",
        "Payslip Categories",
        "Timesheet Hours",
        "Timesheet Details",
        "Hours Δ",
        "AUD Δ",
    ]
    table_data: List[List[Paragraph]] = []
    for idx, row in enumerate([table_headers] + rows_pdf):
        paragraph_row: List[Paragraph] = []
        for cell in row:
            raw_text = cell if isinstance(cell, str) else str(cell)
            text = escape(raw_text).replace("\n", "<br/>")
            style = table_header_style if idx == 0 else table_body_style
            paragraph_row.append(Paragraph(text, style))
        table_data.append(paragraph_row)

    available_width = doc.width
    col_widths = [
        available_width * 0.16,
        available_width * 0.09,
        available_width * 0.24,
        available_width * 0.09,
        available_width * 0.24,
        available_width * 0.08,
        available_width * 0.10,
    ]

    table = LongTable(table_data, repeatRows=1, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    table.hAlign = "LEFT"
    elements.append(table)

    elements.append(Spacer(1, 12))
    totals_table_data: List[List[Paragraph]] = []
    for label, key in (
        ("Payslip total hours", "payslip_hours"),
        ("Timesheet total hours", "timesheet_hours"),
        ("Difference in hours", "hours_diff"),
        ("Hourly rate", "hourly_rate"),
        ("Difference in AUD", "aud_diff"),
    ):
        totals_table_data.append(
            [
                Paragraph(escape(label), totals_label_style),
                Paragraph(escape(totals[key]), totals_value_style),
            ]
        )

    totals_table = Table(totals_table_data, colWidths=[available_width * 0.5, available_width * 0.5])
    totals_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fcfcfc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    totals_table.hAlign = "LEFT"
    elements.append(totals_table)

    if undated:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("Undated payslip items included in totals:", styles["Normal"]))
        for line in undated:
            elements.append(Paragraph(escape(line), styles["Bullet"]))

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(escape(f"Discrepancy status: {status}"), styles["Heading2"]))

    doc.build(elements)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_totals(
    payslip: PayslipData,
    timesheet_totals: Dict[Union[date, str], Dict[str, object]],
) -> Tuple[Decimal, Decimal]:
    payslip_total_hours = sum((item.hours for item in payslip.items), Decimal("0"))
    timesheet_total_hours = sum((info["hours"] for info in timesheet_totals.values()), Decimal("0"))
    return payslip_total_hours, timesheet_total_hours


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compare a payslip PDF against timesheet screenshots and produce an audit report.",
    )
    parser.add_argument(
        "--payslip",
        type=Path,
        help="Payslip PDF path. Defaults to the only .pdf in the current directory if omitted.",
    )
    parser.add_argument(
        "--timesheet",
        nargs="*",
        type=Path,
        help=(
            "Timesheet screenshots (JPG/PNG). Defaults to all matching images in the current directory when not supplied."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("audit_report.pdf"),
        help="Destination PDF for the audit summary.",
    )
    args = parser.parse_args(argv)

    working_dir = Path.cwd()
    payslip_path, timesheet_paths = discover_files(working_dir, args.payslip, args.timesheet)

    output_path = args.output
    if not output_path.is_absolute():
        output_path = (working_dir / output_path).resolve()

    payslip = parse_payslip(payslip_path)
    pay_period = (payslip.start, payslip.end)

    timesheet_entries = parse_timesheets(timesheet_paths, pay_period)
    payslip_totals, aggregated_label = summarise_payslip(payslip.items, pay_period)
    timesheet_totals = summarise_timesheet(timesheet_entries, aggregated_label)

    if not payslip_totals:
        raise SystemExit("No work entries were found on the payslip; comparison cannot proceed.")

    compare_dates(payslip_totals, timesheet_totals, aggregated_label)

    rows_pdf, rows_console = build_rows(payslip_totals, timesheet_totals, payslip.hourly_rate, aggregated_label)

    payslip_hours, timesheet_hours = build_totals(payslip, timesheet_totals)
    hours_diff = timesheet_hours - payslip_hours
    status, aud_diff = determine_status(hours_diff, payslip.hourly_rate)

    totals = {
        "payslip_hours": fmt_hours(payslip_hours),
        "timesheet_hours": fmt_hours(timesheet_hours),
        "hours_diff": fmt_signed_hours(hours_diff),
        "hourly_rate": fmt_currency(payslip.hourly_rate) + " AUD",
        "aud_diff": fmt_signed_currency(aud_diff) + " AUD",
    }
    undated = collect_undated(payslip.items)

    print_console(payslip, rows_console, totals, status, undated, aggregated_label)
    make_pdf(output_path, payslip, rows_pdf, totals, status, undated, aggregated_label)
    print(f"\nAudit PDF saved to: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - surface clear errors to CLI
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

