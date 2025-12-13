"""Shared helpers for validating the Tesseract OCR dependency."""

from __future__ import annotations

import shutil

TESSERACT_MISSING_MESSAGE = (
    "Tesseract OCR is required to process timesheet images. "
    "Install the 'tesseract-ocr' system package or add 'tesseract' to your PATH."
)


def is_tesseract_available() -> bool:
    """Return True when the Tesseract binary is on PATH."""

    return shutil.which("tesseract") is not None
