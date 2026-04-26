"""Utilities for loading Bybit API credentials from the shared env bootstrap."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.env_bootstrap import format_env_bootstrap_log, load_master_env


def load_bybit_live_env(logger: Optional[object] = None) -> bool:
    """Load environment variables via shared bootstrap defaults/overrides."""

    info = load_master_env(base_dir=ROOT_DIR)
    if logger:
        logger.info(format_env_bootstrap_log(info))
    return info.get("external_loaded") == "1"
