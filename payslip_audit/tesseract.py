"""Shared helpers for validating the Tesseract OCR dependency."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

TESSERACT_MISSING_MESSAGE = (
    "Tesseract OCR is required to process timesheet images. "
    "Install the 'tesseract-ocr' system package or add 'tesseract' to your PATH."
)


def _resolve_tesseract_binary() -> Optional[str]:
    """Return the path to a usable Tesseract binary if present.

    Some environments install Tesseract in common locations without exposing it on
    ``PATH``. We check those well-known paths and configure ``pytesseract`` so
    downstream OCR calls succeed even when PATH is missing the binary.
    """

    from pytesseract import pytesseract

    resolved = shutil.which("tesseract")
    if resolved:
        return resolved

    for candidate in ("/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"):
        if Path(candidate).is_file():
            return candidate

    # Some deployments explicitly set pytesseract to the installed path; respect it
    # if the binary exists even when PATH is not configured.
    configured = Path(pytesseract.tesseract_cmd)
    if configured.is_file():
        return str(configured)

    return None


def is_tesseract_available() -> bool:
    """Return True when a Tesseract binary is available and configured."""

    from pytesseract import pytesseract

    binary_path = _resolve_tesseract_binary()
    if not binary_path:
        return False

    # Keep pytesseract pointed at the resolved binary even if PATH was missing.
    try:
        if Path(pytesseract.tesseract_cmd) != Path(binary_path):
            pytesseract.tesseract_cmd = binary_path
    except Exception:
        # Fallback: we still found the binary; let downstream calls surface any
        # configuration errors with fuller context.
        pass

    return True
