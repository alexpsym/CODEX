"""Helpers for loading Optionstrader environment variable files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

try:  # pragma: no cover - import guard for optional dependency
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - handled gracefully at runtime
    load_dotenv = None  # type: ignore

DEFAULT_ENV_FILENAME = "optionstrader.env"
DEFAULT_WINDOWS_ENV_DIR = Path("E:/ENV")
BYBIT_LIVE_ENV_PATH = Path("E:/ENV/bybit-live.env")


def _candidate_paths(script_dir: Path) -> Iterable[Path]:
    """Yield .env locations to probe, ordered by priority."""

    env_path = os.getenv("OPTIONSTRADER_ENV_PATH")
    if env_path:
        yield Path(env_path).expanduser()

    env_dir = os.getenv("OPTIONSTRADER_ENV_DIR")
    if env_dir:
        yield Path(env_dir).expanduser() / DEFAULT_ENV_FILENAME

    yield DEFAULT_WINDOWS_ENV_DIR / DEFAULT_ENV_FILENAME
    yield script_dir / ".env"


def load_optionstrader_env(script_dir: Path, logger: Optional[object] = None) -> List[str]:
    """Load any existing .env files and return their paths."""

    if load_dotenv is None:
        if logger:
            logger.info(
                "python-dotenv is not installed; skipping automatic .env loading."
            )
        return []

    loaded: List[str] = []
    seen = set()
    for candidate in _candidate_paths(script_dir):
        candidate = candidate.expanduser()
        # ``resolve(strict=False)`` preserves non-existent paths while normalising.
        try:
            candidate = candidate.resolve(strict=False)
        except RuntimeError:
            # Some network drives cannot be resolved; best effort fallback.
            candidate = candidate
        key = candidate.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            if load_dotenv(candidate, override=True):
                loaded.append(str(candidate))
                if logger:
                    logger.info("Loaded environment variables from %s", candidate)
    if not loaded and logger:
        logger.info(
            "No .env files were loaded. Expected to find one at %s or set "
            "OPTIONSTRADER_ENV_PATH/OPTIONSTRADER_ENV_DIR.",
            DEFAULT_WINDOWS_ENV_DIR / DEFAULT_ENV_FILENAME,
        )
    return loaded


def load_bybit_live_env(logger: Optional[object] = None) -> List[str]:
    """Load Bybit live credentials from the shared .env file."""

    if not BYBIT_LIVE_ENV_PATH.exists():
        if logger:
            logger.info("Bybit live .env file not found at %s", BYBIT_LIVE_ENV_PATH)
        return []

    loaded = False
    if load_dotenv is not None:
        loaded = load_dotenv(BYBIT_LIVE_ENV_PATH, override=True)
    else:  # pragma: no cover - fallback when python-dotenv is missing
        for line in BYBIT_LIVE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()
            loaded = True

    if loaded and logger:
        logger.info("Loaded Bybit live environment from %s", BYBIT_LIVE_ENV_PATH)
    return [str(BYBIT_LIVE_ENV_PATH)] if loaded else []
