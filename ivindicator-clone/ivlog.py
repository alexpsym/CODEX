"""Centralized logging configuration for the IV indicator app."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

WINDOWS_LOG_DIR = Path(r"C:\\Users\\User\\Downloads")
DEFAULT_LOG_DIR = Path.home() / "Downloads"
LOG_FILE_NAME = "ivindicator.log"

_LOG_CONFIGURED = False
_LOG_PATH: Optional[Path] = None


def _determine_log_dir() -> Path:
    """Return the preferred log directory for the current platform."""
    return WINDOWS_LOG_DIR if os.name == "nt" else DEFAULT_LOG_DIR


def configure_logging() -> Path:
    """Ensure logging writes to both the console and the Downloads folder."""
    global _LOG_CONFIGURED, _LOG_PATH  # pylint: disable=global-statement

    if _LOG_CONFIGURED and _LOG_PATH:
        return _LOG_PATH

    log_dir = _determine_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / LOG_FILE_NAME
    except OSError:
        log_path = Path.cwd() / LOG_FILE_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("ivindicator").info(
        "Application logs will be saved to %s", log_path
    )

    _LOG_CONFIGURED = True
    _LOG_PATH = log_path
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Return a module-specific logger with the shared configuration."""
    configure_logging()
    return logging.getLogger(name)
