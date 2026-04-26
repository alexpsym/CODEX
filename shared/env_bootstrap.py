"""Deterministic environment bootstrap for local scanner workflows."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

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

DEFAULT_MASTER_ENV_FILE = Path(r"C:\Users\User\Documents\GPT\env.env")
DEFAULT_MASTER_ENV_DIR = DEFAULT_MASTER_ENV_FILE.parent
DEFAULT_ENV_FILENAMES = ("env.env", ".env", "scanner.env", "master.env")

_ENV_LOADED = False
_ENV_INFO: dict[str, str] = {}


def _as_path(raw: str, *, base: Path) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def _resolve_paths(
    base_dir: Path | None = None,
) -> tuple[Path, Path, list[Path], Path | None, Path | None]:
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parents[1]
    env_dir = _as_path(os.getenv("MASTER_ENV_DIR", str(DEFAULT_MASTER_ENV_DIR)), base=root)
    explicit_file_raw = os.getenv("MASTER_ENV_FILE")
    explicit_file = _as_path(explicit_file_raw, base=env_dir) if explicit_file_raw else None

    candidates: list[Path] = []
    if explicit_file is not None:
        candidates.append(explicit_file)
    else:
        for filename in DEFAULT_ENV_FILENAMES:
            candidates.append((env_dir / filename).resolve())

    selected = next((p for p in candidates if p.exists()), None)
    repo_fallback = (root / ".env").resolve()
    return repo_fallback, env_dir, candidates, selected, explicit_file


def _paths_csv(paths: Iterable[Path]) -> str:
    return ";".join(str(p) for p in paths)


def load_master_env(*, base_dir: Path | None = None, force_reload: bool = False) -> dict[str, str]:
    """Load repo fallback env first, then external env with override precedence."""

    global _ENV_LOADED, _ENV_INFO
    if _ENV_LOADED and not force_reload:
        return dict(_ENV_INFO)

    repo_fallback, env_dir, candidates, external_env, explicit_file = _resolve_paths(base_dir=base_dir)

    repo_fallback_used = False
    explicit_missing = explicit_file is not None and not explicit_file.exists()
    if repo_fallback.exists() and not explicit_missing:
        repo_fallback_used = bool(load_dotenv(repo_fallback, override=False))

    external_loaded = False
    if external_env is not None and external_env.exists():
        external_loaded = bool(load_dotenv(external_env, override=True))

    _ENV_INFO = {
        "configured_dir": str(env_dir),
        "configured_file": str(explicit_file) if explicit_file is not None else "",
        "loaded_file": str(external_env) if external_env else "",
        "external_loaded": "1" if external_loaded else "0",
        "repo_fallback_used": "1" if repo_fallback_used else "0",
        "repo_env": str(repo_fallback),
        "checked_files": _paths_csv(candidates),
    }
    _ENV_LOADED = True
    return dict(_ENV_INFO)


def format_env_bootstrap_log(info: dict[str, str] | None = None) -> str:
    payload = info or _ENV_INFO or {}
    loaded_file = payload.get("loaded_file") or "<none>"
    return (
        "MASTER_ENV "
        f"configured_dir={payload.get('configured_dir', '')} "
        f"configured_file={payload.get('configured_file', '') or '<auto>'} "
        f"loaded_file={loaded_file} "
        f"external_loaded={'yes' if payload.get('external_loaded') == '1' else 'no'} "
        f"repo_fallback_used={'yes' if payload.get('repo_fallback_used') == '1' else 'no'} "
        f"checked={payload.get('checked_files', '')}"
    )
