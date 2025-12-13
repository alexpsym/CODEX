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
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from payslip_audit.tesseract import TESSERACT_MISSING_MESSAGE, is_tesseract_available

CURRENCY_RE = re.compile(r"[^\d\-.]")
SPACE_RE = re.compile(r"\s+")
DATE_SUFFIX_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.IGNORECASE)
TIMESHEET_DATE_PATTERNS = [
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})(?:\s+(?P<year>\d{2,4})(?!\s*[A-Za-z]))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{2,4})(?!\s*[A-Za-z]))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<dow>[A-Za-z]{3}),?\s+(?P<day>\d{1,2})[/-](?P<month>\d{1,2})(?:[/-](?P<year>\d{2,4})(?!\s*[A-Za-z]))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{2,4})(?!\s*[A-Za-z]))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})(?:\s+(?P<year>\d{2,4})(?!\s*[A-Za-z]))?",
        re.IGNORECASE,
    ),
]
SHIFT_TOTAL_RE = re.compile(r"Shift\s+Total", re.IGNORECASE)
DURATION_ONLY_RE = re.compile(r"^[;:.,\-\s]*\d+\s*h\s*\d+\s*m\b", re.IGNORECASE)
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


def ensure_tesseract_available() -> None:
    """Abort early with a clear error if the Tesseract binary is missing."""

    if not is_tesseract_available():
        raise SystemExit(TESSERACT_MISSING_MESSAGE)


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


def normalize_timesheet_date_text(text: str) -> str:
    normalized = clean(text)
    normalized = re.sub(r"[^A-Za-z0-9/\-\s]+", " ", normalized)
    normalized = re.sub(r"(?<=\d)[|Il](?=\d)", "1", normalized)
    normalized = re.sub(r"(?<=\d)[Oo](?=\d)", "0", normalized)
    normalized = DATE_SUFFIX_RE.sub(r"\1", normalized)
    normalized = re.sub(r"[\s,:;|]+$", "", normalized)
    normalized = re.sub(r"^[\s,:;|]+", "", normalized)
    normalized = re.sub(r"([A-Za-z]{3,9})[-_,.:]+(\d{1,2})", r"\1 \2", normalized)
    normalized = re.sub(r"(\d{1,2})[-_,.:]+([A-Za-z]{3,9})", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Za-z]{3,9})(\d{1,2})", r"\1 \2", normalized)
    normalized = re.sub(r"(\d{1,2})([A-Za-z]{3,9})", r"\1 \2", normalized)
    normalized = re.sub(r"\b0ct\b", "Oct", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bn0v\b", "Nov", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bdecernber\b", "December", normalized, flags=re.IGNORECASE)
    return SPACE_RE.sub(" ", normalized).strip()


def parse_timesheet_date(line: str, pay_period: Tuple[date, date]) -> Optional[date]:
    cleaned = normalize_timesheet_date_text(line)
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
            has_hour_like = any(any(key in token for key in ("unit", "hour", "qty")) for token in normalized)
            has_description_like = any(
                any(key in token for key in ("desc", "earning", "type", "category", "item")) for token in normalized
            )
            has_date_like = any("date" in token for token in normalized)
            if header_keys is None and has_hour_like and (has_date_like or has_description_like):
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

    if PAYSLIP_TABLE_MARKER in text:
        text = text.split(PAYSLIP_TABLE_MARKER, 1)[0].strip()

    # Some text exports break the tabular rows across multiple lines. Look for the
    # DESCRIPTION/HOURS/RATE header and then buffer following lines until we can
    # parse a complete row with category, optional hours, rate, and amount.
    lines = [clean(line) for line in text.splitlines()]
    header_idx = None
    for idx, line in enumerate(lines):
        normalized = line.lower()
        if "description" in normalized and "hour" in normalized and "amount" in normalized:
            header_idx = idx
            break

    row_with_hours_pattern = re.compile(
        r"(?P<category>[A-Za-z][A-Za-z0-9 #()/&+,\-]*?)\s+"
        r"(?P<hours>-?\d+(?::\d{2})?|\d+\.\d+|\d+\s*h\s*\d+\s*m)\s+"
        r"(?P<rate>[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})\s+"
        r"(?P<amount>[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})"
        r"(?:\s+[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})?"  # optional YTD column
        r"(?:\s+[A-Za-z].*)?"  # trailing type/notes columns
    )

    row_without_hours_pattern = re.compile(
        r"(?P<category>[A-Za-z][A-Za-z0-9 #()/&+,\-]*?)\s+"
        r"(?P<rate>[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})\s+"
        r"(?P<amount>[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})"
        r"(?:\s+[-+]?\s*[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})?"  # optional YTD column
        r"(?:\s+[A-Za-z].*)?"  # trailing type/notes columns
    )

    if header_idx is not None:
        buffer = ""
        for line in lines[header_idx + 1 :]:
            if not line:
                continue
            if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", line):
                buffer = ""
                continue
            buffer = f"{buffer} {line}".strip()
            match = row_with_hours_pattern.search(buffer)
            matched_without_hours = False
            if not match:
                match = row_without_hours_pattern.search(buffer)
                matched_without_hours = bool(match)

            if not match:
                # Keep buffering until we can parse a row
                if len(buffer) > 200:
                    buffer = ""
                continue

            category = clean(match.group("category"))
            if category.lower().startswith("total"):
                buffer = ""
                continue

            hours_text = match.group("hours") if not matched_without_hours else None

            items.append(
                PayslipItem(
                    date=None,
                    category=category or "Uncategorised",
                    hours=parse_hours(hours_text),
                    rate=parse_decimal(match.group("rate")),
                    amount=parse_decimal(match.group("amount")),
                )
            )
            buffer = ""

    if items:
        return items

    fallback_pattern = re.compile(
        r"(?P<category>[A-Za-z0-9 #()/&+,\-]+?)\s+"
        r"(?P<hours>\d+(?::\d{2})?|\d+\.\d+|\d+\s*h\s*\d+\s*m)\s+"
        r"(?P<rate>[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})\s+"
        r"(?P<amount>[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})"
        r"(?:\s+[$€£]?\s*-?\d{1,3}(?:,\d{3})*\.\d{2})?"  # optional YTD column
        r"(?:\s+[A-Za-z].*)?"  # trailing type/notes columns
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

    per_hour_match = re.search(r"[$€£]?\s*(\d+\.\d{2})\s*(?:/\s*(?:hr|hour)\b|per\s+hour)", text, re.IGNORECASE)
    if per_hour_match:
        return Decimal(per_hour_match.group(1))

    keyword_rates: List[Decimal] = []
    for line in text.splitlines():
        cleaned = clean(line)
        if not cleaned:
            continue
        if not re.search(r"(hour|rate|pay|wage)", cleaned, re.IGNORECASE):
            continue
        for currency_match in re.finditer(r"[$€£]?\s*(\d+\.\d{2})", cleaned):
            value = parse_decimal(currency_match.group(1))
            if value is not None:
                keyword_rates.append(value)

    if keyword_rates:
        return keyword_rates[0]

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


PAYSLIP_TEXT_MARKER = "---PAYSLIP TEXT---"
PAYSLIP_TABLE_MARKER = "---TABLE"


def extract_payslip_text_and_tables(path: Path) -> Tuple[str, List[List[List[str]]]]:
    with pdfplumber.open(str(path)) as pdf:
        texts: List[str] = []
        tables: List[List[List[str]]] = []
        for page in pdf.pages:
            page_text = (page.extract_text() or "").strip()
            page_tables = page.extract_tables() or []

            texts.append(page_text)
            tables.extend(page_tables)

        text = "\n".join(texts).strip()

        if not text:
            ocr_texts: List[str] = []
            for page in pdf.pages:
                image = page.to_image(resolution=300).original.convert("RGB")
                ocr_texts.append(pytesseract.image_to_string(image) or "")
            text = "\n".join(ocr_texts)
    return text, tables


def write_payslip_sidecar(sidecar: Path, text: str, tables: List[List[List[str]]]) -> None:
    # Persist only the readable payslip text. Table data stays in memory for
    # parsing but is not written to the sidecar so the generated payslip.txt
    # mirrors the trimmed text the user expects.
    if PAYSLIP_TABLE_MARKER in text:
        text = text.split(PAYSLIP_TABLE_MARKER, 1)[0]

    lines: List[str] = [PAYSLIP_TEXT_MARKER, text.strip()]
    sidecar.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def read_payslip_sidecar(sidecar: Path) -> Tuple[str, List[List[List[str]]]]:
    content = sidecar.read_text(encoding="utf-8", errors="ignore")
    if PAYSLIP_TEXT_MARKER not in content and PAYSLIP_TABLE_MARKER not in content:
        return content.strip(), []

    text_lines: List[str] = []
    # Tables are intentionally discarded to keep the sidecar minimal and avoid
    # malformed rows interfering with parsing.
    tables: List[List[List[str]]] = []
    current_table: Optional[List[List[str]]] = None
    for line in content.splitlines():
        if line.startswith(PAYSLIP_TABLE_MARKER):
            break
        if line.startswith(PAYSLIP_TEXT_MARKER):
            if current_table is not None:
                tables.append(current_table)
            current_table = None
            continue

        if current_table is None:
            text_lines.append(line)

    text = "\n".join(text_lines).strip()
    return text, tables


def parse_payslip(path: Path, generated_sidecars: Optional[List[Path]] = None) -> PayslipData:
    sidecar = path.with_suffix(".txt")
    tables: List[List[List[str]]]
    created_sidecar = False
    if sidecar.exists():
        text, tables = read_payslip_sidecar(sidecar)
        if not tables:
            # Extract fresh tables for parsing without writing them to disk.
            _, tables = extract_payslip_text_and_tables(path)
    else:
        text, extracted_tables = extract_payslip_text_and_tables(path)
        write_payslip_sidecar(sidecar, text, extracted_tables)
        created_sidecar = True
        text, tables = read_payslip_sidecar(sidecar)
        if not tables:
            tables = extracted_tables

    if created_sidecar and generated_sidecars is not None:
        generated_sidecars.append(sidecar)
    pay_period = find_pay_period(text)

    # Prefer the text body for hours extraction so multi-line exports that split
    # rows are handled consistently. Table parsing remains as a fallback.
    items = extract_from_text(text, pay_period)
    if not items and tables:
        items = extract_from_tables(tables, pay_period)
    if not items:
        raise ValueError("No payslip work entries were detected.")

    rate = determine_rate(text, items)
    return PayslipData(start=pay_period[0], end=pay_period[1], hourly_rate=rate, items=items, text=text)


# ---------------------------------------------------------------------------
# Timesheet parsing
# ---------------------------------------------------------------------------

def extract_timesheet_entries(
    text: str,
    pay_period: Tuple[date, date],
    best_totals: Dict[date, Tuple[Tuple[bool, Decimal], TimesheetEntry]],
    seen_totals: Dict[date, Set[str]],
    shift_sums: Dict[date, Decimal],
) -> None:
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    current: Optional[date] = None
    date_positions: Dict[int, date] = {}
    date_markers: List[Tuple[int, Optional[date]]] = []
    pending_shift_totals: List[Tuple[int, Decimal, str]] = []

    for idx, line in enumerate(lines):
        dt = parse_timesheet_date(line, pay_period)
        if dt is not None:
            current = dt
            seen_totals.setdefault(dt, set())
            date_positions[idx] = dt
            date_markers.append((idx, dt))

            inline_hours = parse_hours(line)
            if inline_hours > 0:
                normalized = SPACE_RE.sub(" ", line.lower()).strip()
                key = f"{fmt_hours(inline_hours)}|{normalized}"
                if key not in seen_totals[current]:
                    entry = TimesheetEntry(hours=inline_hours, label="Day Total", counts=True, raw=line)
                    score = (True, inline_hours)
                    existing = best_totals.get(current)
                    if existing is None or score > existing[0]:
                        best_totals[current] = (score, entry)
                    seen_totals[current].add(key)
            continue

        if any(pattern.match(normalize_timesheet_date_text(line)) for pattern in TIMESHEET_DATE_PATTERNS):
            date_markers.append((idx, None))
            current = None
            continue

        if current is None:
            if SHIFT_TOTAL_RE.search(line):
                hours = parse_hours(line)
                if hours > 0:
                    normalized = SPACE_RE.sub(" ", line.lower()).strip()
                    pending_shift_totals.append((idx, hours, normalized))
            continue

        low = line.lower()
        if low.startswith("lunch break") or low.startswith("unpaid break"):
            continue

        if SHIFT_TOTAL_RE.search(line):
            hours = parse_hours(line)
            if hours <= 0:
                continue

            normalized = SPACE_RE.sub(" ", line.lower()).strip()
            key = f"{fmt_hours(hours)}|{normalized}"
            if key in seen_totals[current]:
                continue

            shift_sums[current] = shift_sums.get(current, Decimal("0")) + hours
            seen_totals[current].add(key)
            continue

        hours = parse_hours(line)
        if hours <= 0:
            continue

        normalized = SPACE_RE.sub(" ", line.lower()).strip()
        key = f"{fmt_hours(hours)}|{normalized}"
        if key in seen_totals[current]:
            continue

        has_total_keyword = bool(re.search(r"\btotal\b", line, re.IGNORECASE))
        score = (has_total_keyword, hours)

        entry = TimesheetEntry(hours=hours, label="Day Total", counts=True, raw=line)
        existing = best_totals.get(current)
        if existing is None or score > existing[0]:
            best_totals[current] = (score, entry)

        seen_totals[current].add(key)

    resolved_markers: List[Tuple[int, date]] = sorted(date_positions.items())
    if pending_shift_totals and not resolved_markers:
        for marker_idx, marker_dt in date_markers:
            if marker_dt is not None:
                continue
            fallback_dt = resolve_inline_date(normalize_timesheet_date_text(lines[marker_idx]), pay_period)
            if fallback_dt and pay_period[0] <= fallback_dt <= pay_period[1]:
                seen_totals.setdefault(fallback_dt, set())
                resolved_markers.append((marker_idx, fallback_dt))
        resolved_markers.sort()

    unassigned_shift_totals: List[Tuple[int, Decimal, str]] = []
    if pending_shift_totals and resolved_markers:
        indexed_dates = resolved_markers
        for idx, hours, normalized in pending_shift_totals:
            nearest_idx, nearest_dt = min(
                indexed_dates, key=lambda pair: (abs(pair[0] - idx), pair[0])
            )
            seen_totals.setdefault(nearest_dt, set())
            key = f"{fmt_hours(hours)}|{normalized}"
            if key in seen_totals[nearest_dt]:
                continue
            shift_sums[nearest_dt] = shift_sums.get(nearest_dt, Decimal("0")) + hours
            seen_totals[nearest_dt].add(key)
    else:
        unassigned_shift_totals = pending_shift_totals

    if unassigned_shift_totals:
        fallback_hours = sum(hours for _, hours, _ in unassigned_shift_totals)
        if fallback_hours > 0:
            fallback_dt = pay_period[0]
            entry = TimesheetEntry(
                hours=fallback_hours,
                label="Unassigned shift totals",
                counts=True,
                raw="Shift Total hours without detected date",
            )
            score = (False, fallback_hours)
            existing = best_totals.get(fallback_dt)
            if existing is None or score > existing[0]:
                best_totals[fallback_dt] = (score, entry)
            shift_sums[fallback_dt] = shift_sums.get(fallback_dt, Decimal("0")) + fallback_hours


def parse_timesheets(
    paths: Iterable[Path],
    pay_period: Tuple[date, date],
    generated_sidecars: Optional[List[Path]] = None,
) -> Dict[date, List[TimesheetEntry]]:
    best_totals: Dict[date, Tuple[Tuple[bool, Decimal], TimesheetEntry]] = {}
    seen_totals: Dict[date, Set[str]] = {}
    shift_sums: Dict[date, Decimal] = {}

    for path in paths:
        sidecar = path.with_suffix(".txt")
        created_sidecar = False
        if sidecar.exists():
            text = sidecar.read_text(encoding="utf-8", errors="ignore")
        else:
            image = Image.open(path).convert("L")
            inverted = ImageOps.invert(image)
            contrasted = ImageOps.autocontrast(image)
            thresholded = contrasted.point(lambda p: 255 if p > 180 else 0)
            processed = ImageOps.autocontrast(inverted).point(lambda p: 255 if p > 180 else 0)

            text_passes = [
                pytesseract.image_to_string(image, lang="eng", config="--psm 6"),
                pytesseract.image_to_string(inverted, lang="eng", config="--psm 6"),
                pytesseract.image_to_string(contrasted, lang="eng", config="--psm 6"),
                pytesseract.image_to_string(thresholded, lang="eng", config="--psm 6"),
                pytesseract.image_to_string(processed, lang="eng", config="--psm 6"),
            ]
            text = "\n".join(pass_text for pass_text in text_passes if pass_text)

            sidecar.write_text(text, encoding="utf-8")
            created_sidecar = True

        extract_timesheet_entries(text, pay_period, best_totals, seen_totals, shift_sums)

        if created_sidecar and generated_sidecars is not None:
            generated_sidecars.append(sidecar)

    for dt, hours in shift_sums.items():
        entry = TimesheetEntry(hours=hours, label="Sum of shift totals", counts=True, raw="Aggregated shift totals")
        score = (True, hours)
        existing = best_totals.get(dt)
        if existing is None or score > existing[0]:
            best_totals[dt] = (score, entry)

    entries: Dict[date, List[TimesheetEntry]] = {}
    for dt, (_, entry) in best_totals.items():
        entries[dt] = [entry]
    return entries


def discover_files(
    payslip_arg: Optional[Path],
    timesheet_args: Optional[List[Path]],
) -> Tuple[Path, List[Path]]:
    """Resolve payslip and timesheet paths from explicit inputs.

    The audit now runs in a controlled environment with uploaded files, so a
    payslip PDF and at least one timesheet image must be provided directly via
    command-line arguments or API uploads.
    """

    if payslip_arg is None:
        raise SystemExit("Payslip PDF is required. Provide --payslip or upload a PDF.")

    payslip_path = payslip_arg.expanduser().resolve()
    if not payslip_path.exists():
        raise SystemExit(f"Payslip PDF not found: {payslip_path}")

    if not timesheet_args:
        raise SystemExit("At least one timesheet image (JPG/PNG) is required via --timesheet or upload.")

    timesheet_paths: List[Path] = []
    for path in timesheet_args:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            raise SystemExit(f"Timesheet image not found: {resolved}")
        timesheet_paths.append(resolved)

    return payslip_path, timesheet_paths


# ---------------------------------------------------------------------------
# Summaries and comparison
# ---------------------------------------------------------------------------

def counts_as_hours(category: str) -> bool:
    return not re.search(r"\ballowance\b", category, re.IGNORECASE)


def summarise_payslip(items: List[PayslipItem], pay_period: Tuple[date, date]) -> Tuple[Dict[Union[date, str], Dict[str, object]], Optional[str]]:

    totals: Dict[Union[date, str], Dict[str, object]] = {}
    has_dated_entries = False
    aggregated_details: List[str] = []
    aggregated_hours = Decimal("0")

    for item in items:
        counts_hours = counts_as_hours(item.category)
        counted_hours = item.hours if counts_hours else Decimal("0")

        detail = f"{item.category} ({fmt_hours(item.hours)}h"
        if item.rate is not None:
            detail += f" @ {fmt_currency(item.rate)}"
        if item.amount is not None:
            detail += f", {fmt_currency(item.amount)}"
        detail += ")"
        if not counts_hours:
            detail += " [ignored for hour totals]"
        aggregated_details.append(detail)
        aggregated_hours += counted_hours

        if item.date is None:
            continue

        has_dated_entries = True
        entry = totals.setdefault(item.date, {"hours": Decimal("0"), "categories": []})
        entry["hours"] += counted_hours
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
    payslip_total_hours = sum((item.hours for item in payslip.items if counts_as_hours(item.category)), Decimal("0"))
    timesheet_total_hours = sum((info["hours"] for info in timesheet_totals.values()), Decimal("0"))
    return payslip_total_hours, timesheet_total_hours


def cleanup_sidecars(sidecars: Iterable[Path]) -> None:
    for sidecar in {path.resolve() for path in sidecars}:
        try:
            sidecar.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - warn but do not halt
            print(f"Warning: failed to delete {sidecar}: {exc}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compare a payslip PDF against timesheet screenshots and produce an audit report.",
    )
    parser.add_argument(
        "--payslip",
        type=Path,
        required=False,
        help="Payslip PDF path (required unless provided by the upload API).",
    )
    parser.add_argument(
        "--timesheet",
        nargs="*",
        type=Path,
        help="Timesheet screenshots (JPG/PNG). At least one is required unless provided by the upload API.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("audit_report.pdf"),
        help="Destination PDF for the audit summary.",
    )
    args = parser.parse_args(argv)

    working_dir = Path.cwd()
    ensure_tesseract_available()
    payslip_path, timesheet_paths = discover_files(args.payslip, args.timesheet)

    output_path = args.output
    if not output_path.is_absolute():
        output_path = (working_dir / output_path).resolve()

    generated_sidecars: List[Path] = []

    payslip = parse_payslip(payslip_path, generated_sidecars)
    pay_period = (payslip.start, payslip.end)

    timesheet_entries = parse_timesheets(timesheet_paths, pay_period, generated_sidecars)
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

    cleanup_sidecars(generated_sidecars)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - surface clear errors to CLI
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

