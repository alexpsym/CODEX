"""Utility functions for the scanning workflow."""

import time
import logging


def wait_for_file_close(path: str, logger: logging.Logger | None = None) -> None:
    """Sleep until ``path`` can be opened for writing."""
    if logger is None:
        logger = logging.getLogger("volume_logger")
    while True:
        try:
            with open(path, "a", encoding="utf-8"):
                return
        except OSError:
            logger.debug("Waiting for %s to be released", path)
            time.sleep(0.1)

