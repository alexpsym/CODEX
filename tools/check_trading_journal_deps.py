"""Quick import-check for local Trading Journal runtime dependencies."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "pandas",
    "openpyxl",
    "xlrd",
    "dotenv",
    "requests",
    "httpx",
    "PIL",
)


def find_missing_modules(required_modules: tuple[str, ...] = REQUIRED_MODULES) -> list[str]:
    missing: list[str] = []
    for module_name in required_modules:
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)
    return missing


def build_summary(missing: list[str], python_exe: str | None = None) -> dict[str, object]:
    exe = python_exe or sys.executable
    root = Path(__file__).resolve().parents[1]
    requirements_file = str((root / "render" / "requirements.txt").resolve())
    return {
        "ok": not missing,
        "python": exe,
        "missing": missing,
        "requirements_file": requirements_file,
        "local_xls_requires": "xlrd",
        "local_xls_supported": "xlrd" not in missing,
    }


def main() -> int:
    missing = find_missing_modules()
    summary = build_summary(missing)
    print(json.dumps(summary, separators=(",", ":")))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
