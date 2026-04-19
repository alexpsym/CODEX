"""Deterministic environment bootstrap for local scanner workflows."""
from __future__ import annotations

import os
from pathlib import Path
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - fallback in minimal test envs
    def load_dotenv(path: Path, override: bool = False) -> bool:
        loaded = False
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except Exception:
            return False
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
            loaded = True
        return loaded

DEFAULT_MASTER_ENV_DIR = Path(r"C:\Users\User\Downloads")
DEFAULT_MASTER_ENV_FILENAME = ".env"

_ENV_LOADED = False
_ENV_INFO: dict[str, str] = {}


def _resolve_paths(base_dir: Path | None = None) -> tuple[Path, Path | None]:
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parents[1]
    env_dir_raw = os.getenv("MASTER_ENV_DIR", str(DEFAULT_MASTER_ENV_DIR))
    env_dir = Path(env_dir_raw).expanduser()
    if not env_dir.is_absolute():
        env_dir = (root / env_dir).resolve()

    env_file_raw = os.getenv("MASTER_ENV_FILE")
    if env_file_raw:
        env_file = Path(env_file_raw).expanduser()
        if not env_file.is_absolute():
            env_file = (env_dir / env_file).resolve()
    else:
        env_file = (env_dir / DEFAULT_MASTER_ENV_FILENAME).resolve()

    repo_fallback = (root / ".env").resolve()
    return repo_fallback, env_file


def load_master_env(*, base_dir: Path | None = None, force_reload: bool = False) -> dict[str, str]:
    """Load repo fallback env first, then external env with override precedence."""

    global _ENV_LOADED, _ENV_INFO
    if _ENV_LOADED and not force_reload:
        return dict(_ENV_INFO)

    repo_fallback, external_env = _resolve_paths(base_dir=base_dir)

    # Backward-compatible fallback: repo root .env (never required).
    if repo_fallback.exists():
        load_dotenv(repo_fallback, override=False)

    external_loaded = False
    if external_env and external_env.exists():
        load_dotenv(external_env, override=True)
        external_loaded = True

    _ENV_INFO = {
        "repo_env": str(repo_fallback),
        "external_env": str(external_env) if external_env else "",
        "external_loaded": "1" if external_loaded else "0",
    }
    _ENV_LOADED = True
    return dict(_ENV_INFO)
