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
from typing import Dict, Iterable, List, Optional, Tuple, Union

import pdfplumber
import pytesseract
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CURRENCY_RE = re.compile(r"[^\d\-.]")
SPACE_RE = re.compile(r"\s+")
DATE_SUFFIX_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.IGNORECASE)
TIMESHEET_DATE_RE = re.compile(r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})", re.IGNORECASE)
SHIFT_TOTAL_RE = re.compile(r"Shift\s+Total", re.IGNORECASE)
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


def resolve_inline_date(text: str, pay_period: Tuple[date, date]) -> Optional[date]:
    parsed = parse_date(text)
    if parsed:
        return parsed
    match = re.search(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})", text)
    if match:
        return resolve_partial_date(int(match.group("day")), match.group("month"), pay_period)
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
    cleaned = re.sub(r"^(?:pay\s+period\s+)?(?:from|to|period\s+start|period\s+(?:end|finish)|start|end)[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    return cleaned


# ---------------------------------------------------------------------------
# Payslip parsing
# ---------------------------------------------------------------------------

def find_pay_period(text: str) -> Tuple[date, date]:
    patterns = [
        r"Pay\s*Period\s*(?:From\s*)?[:\-]?\s*(?P<start>.+?)\s*(?:to|-|through)\s*(?P<end>.+)",
        r"Period\s*Start\s*[:\-]?\s*(?P<start>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).*?Period\s*(?:End|Finish)\s*[:\-]?\s*(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"From\s*(?P<start>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).*?To\s*(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        start = parse_date(normalize_period_token(match.group("start")))
        end = parse_date(normalize_period_token(match.group("end")))
        if start and end:
            return start, end

    start_match = re.search(r"Period\s*Start\s*[:\-]?\s*(?P<value>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.IGNORECASE)
    end_match = re.search(r"Period\s*(?:End|Finish)\s*[:\-]?\s*(?P<value>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.IGNORECASE)
    if start_match and end_match:
        start = parse_date(normalize_period_token(start_match.group("value")))
        end = parse_date(normalize_period_token(end_match.group("value")))
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

def extract_timesheet_entries(text: str, pay_period: Tuple[date, date], entries: Dict[date, List[TimesheetEntry]]) -> None:
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    current: Optional[date] = None
    pending: Optional[str] = None

    for line in lines:
        date_match = TIMESHEET_DATE_RE.match(line)
        if date_match:
            dt = resolve_partial_date(int(date_match.group("day")), date_match.group("month"), pay_period)
            current = dt
            if dt:
                entries.setdefault(dt, [])
            pending = None
            continue

        if current is None:
            continue

        low = line.lower()
        if low.startswith("lunch break") or low.startswith("unpaid break"):
            pending = None
            continue

        if SHIFT_TOTAL_RE.search(line):
            hours = parse_hours(line)
            if hours > 0:
                entries[current].append(TimesheetEntry(hours=hours, label="Shift Total", counts=True, raw=line))
                pending = None
            else:
                pending = "Shift Total"
            continue

        hours = parse_hours(line)
        if hours > 0:
            if pending:
                entries[current].append(
                    TimesheetEntry(hours=hours, label=pending, counts=True, raw=f"{pending} {line}")
                )
                pending = None
            else:
                entries[current].append(
                    TimesheetEntry(hours=hours, label="Timesheet Daily Total", counts=False, raw=line)
                )
        else:
            pending = None

    if current is not None:
        entries.setdefault(current, [])


def parse_timesheets(paths: Iterable[Path], pay_period: Tuple[date, date]) -> Dict[date, List[TimesheetEntry]]:
    entries: Dict[date, List[TimesheetEntry]] = {}
    for path in paths:
        image = Image.open(path).convert("L")
        text = pytesseract.image_to_string(image, lang="eng", config="--psm 6")
        extract_timesheet_entries(text, pay_period, entries)
    return entries


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
    if aggregated_label:
        total = Decimal("0")
        details: List[str] = []
        for dt in sorted(entries):
            logs = entries[dt]
            daily_total = Decimal("0")
            for entry in logs:
                if entry.counts:
                    daily_total += entry.hours
                    details.append(f"{dt.strftime('%Y-%m-%d')}: {entry.label} {fmt_hours(entry.hours)}h")
                else:
                    details.append(
                        f"{dt.strftime('%Y-%m-%d')}: {entry.label} {fmt_hours(entry.hours)}h (ignored)"
                    )
            total += daily_total
        return {aggregated_label: {"hours": total, "details": details}}

    totals: Dict[Union[date, str], Dict[str, object]] = {}
    for dt, logs in entries.items():
        total = Decimal("0")
        details: List[str] = []
        for entry in logs:
            if entry.counts:
                total += entry.hours
                details.append(f"{entry.label}: {fmt_hours(entry.hours)}h")
            else:
                details.append(f"{entry.label}: {fmt_hours(entry.hours)}h (ignored)")
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
    table_data = [table_headers]
    table_data.extend(rows_pdf)

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ]
        )
    )
    elements.append(table)

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Payslip total hours: {totals['payslip_hours']}", styles["Normal"]))
    elements.append(Paragraph(f"Timesheet total hours: {totals['timesheet_hours']}", styles["Normal"]))
    elements.append(Paragraph(f"Difference in hours: {totals['hours_diff']}", styles["Normal"]))
    elements.append(Paragraph(f"Hourly rate: {totals['hourly_rate']}", styles["Normal"]))
    elements.append(Paragraph(f"Difference in AUD: {totals['aud_diff']}", styles["Normal"]))

    if undated:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("Undated payslip items included in totals:", styles["Normal"]))
        for line in undated:
            elements.append(Paragraph(line, styles["Bullet"]))

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Discrepancy status: {status}", styles["Heading2"]))

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
    parser.add_argument("--payslip", required=True, type=Path, help="Payslip PDF path.")
    parser.add_argument(
        "--timesheet",
        required=True,
        nargs="+",
        type=Path,
        help="One or more timesheet screenshots (JPG/PNG).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("audit_report.pdf"),
        help="Destination PDF for the audit summary.",
    )
    args = parser.parse_args(argv)

    payslip = parse_payslip(args.payslip)
    pay_period = (payslip.start, payslip.end)

    timesheet_entries = parse_timesheets(args.timesheet, pay_period)
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
    make_pdf(args.output, payslip, rows_pdf, totals, status, undated, aggregated_label)
    print(f"\nAudit PDF saved to: {args.output.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - surface clear errors to CLI
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
