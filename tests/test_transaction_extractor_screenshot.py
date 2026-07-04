from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR_DIR = ROOT / "LEDGER-clone" / "transaction_extractor_local_fixed_v7_no_venv"
EXTRACTOR_PATH = EXTRACTOR_DIR / "extract_transactions_local.py"
RUN_DIR = EXTRACTOR_DIR / "outputs" / "run_20260704_120627"
SCREENSHOT = RUN_DIR / "downloads" / "source_01.jpg"


EXPECTED = [
    ("04/07/2026", "RENDER.COM +14158304762 US", -10.17, True),
    ("03/07/2026", "Bridgewater Bake House", -5.50, False),
    ("03/07/2026", "Bridgewater Bake House", -8.50, False),
    ("03/07/2026", "ROBINS PIZZA/SHOP 17/2 YE MEADOWBROOK AU", -19.82, True),
    ("01/07/2026", "Yummy Fried Rice", -15.90, False),
    ("15/06/2026", "Sp Nutra Nourished", -55.12, False),
    ("12/06/2026", "Pizza In A Hurryohm Food", -14.95, False),
    ("09/06/2026", "Foodworks", -6.50, False),
    ("08/06/2026", "Khao Thai Restaurant", -29.72, False),
    ("07/06/2026", "Transfer to Alexander Symoniw", -300.00, False),
    ("07/06/2026", "Transfer from SaveME", 300.00, False),
    ("05/06/2026", "Robins Pizza/Shop 17/2", -19.82, False),
    ("05/06/2026", "Render.Com", -9.83, False),
    ("04/06/2026", "Transfer from SaveME", 21.00, False),
]


@pytest.fixture(scope="module")
def extractor():
    spec = importlib.util.spec_from_file_location("transaction_extractor_local", EXTRACTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["transaction_extractor_local"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    try:
        module.ensure_tesseract()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    return module


@pytest.fixture(scope="module")
def extracted_rows(extractor):
    rows, _debug = extractor.parse_image_spatial(SCREENSHOT, RUN_DIR, SCREENSHOT.name)
    return rows


def _signed_amount(row) -> float | None:
    if row.debit is not None:
        return float(row.debit)
    if row.credit is not None:
        return -float(row.credit)
    return None


def test_supplied_boq_screenshot_transactions(extracted_rows):
    actual = [
        (row.date, row.description, _signed_amount(row), row.pending, row.needs_review, row.review_reason)
        for row in extracted_rows
    ]

    expected = [(date, desc, amount, pending, False, "") for date, desc, amount, pending in EXPECTED]
    assert actual == expected

    # Rows 2-4 share the "Yesterday, 3 July 2026" heading.
    assert [row.date for row in extracted_rows[1:4]] == ["03/07/2026"] * 3


def test_screenshot_writers_preserve_ledger_columns(extractor, extracted_rows, tmp_path):
    xlsx_path = tmp_path / "extracted_transactions_test.xlsx"
    csv_path = tmp_path / "review_extraction_test.csv"

    extractor.write_workbook(extracted_rows, xlsx_path)
    extractor.write_review(extracted_rows, csv_path)

    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb["Transactions"]
    assert [cell.value for cell in ws[1]] == [
        "DATE",
        "ACCOUNT_TYPE",
        "ACCOUNT",
        "DESCRIPTION",
        "DEBIT",
        "CREDIT",
        "NOTES",
        "NOTES",
    ]

    body = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(body) == len(EXPECTED)
    for sheet_row, (date, desc, amount, pending) in zip(body, EXPECTED):
        assert sheet_row[0] == date
        assert sheet_row[3] == desc
        if amount < 0:
            assert sheet_row[4] in (None, "")
            assert float(sheet_row[5]) == abs(amount)
        else:
            assert float(sheet_row[4]) == amount
            assert sheet_row[5] in (None, "")
        assert sheet_row[6] == ("pending" if pending else None)

    with csv_path.open(newline="", encoding="utf-8") as f:
        review_rows = list(csv.DictReader(f))
    assert len(review_rows) == len(EXPECTED)
    assert {row["needs_review"] for row in review_rows} == {"NO"}
    assert [row["pending"] for row in review_rows].count("YES") == 2


def test_amount_normalization_fails_safe(extractor):
    assert extractor.normalize_amount_candidate("$5.0") is None
    assert extractor.normalize_amount_candidate("319.82") is None
    assert extractor.normalize_amount_candidate("+$21L00")[:2] == (21.0, False)
    assert extractor.normalize_amount_candidate("$650")[:2] == (6.5, False)
