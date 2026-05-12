from __future__ import annotations

import os
import sys
from pathlib import Path

from openpyxl import Workbook

_HEADERS = ("account", "date", "amount", "new_balance", "currency", "reason")


def _create_cashflow_template(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Cashflows"
    ws.append(_HEADERS)
    wb.save(path)


def ensure_local_journal_templates(local_dir: Path) -> int:
    if local_dir.exists() and not local_dir.is_dir():
        print(
            f"ERROR: local journal path exists and is not a directory: {local_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        local_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"ERROR: failed to create local journal directory {local_dir}: {exc}", file=sys.stderr)
        return 1

    target = local_dir / "account_cashflows.xlsx"
    if target.exists():
        print(f"already exists: {target}")
        return 0

    tmp = local_dir / "account_cashflows.tmp.xlsx"
    try:
        _create_cashflow_template(tmp)
        if target.exists():
            tmp.unlink(missing_ok=True)
            print(f"already exists: {target}")
            return 0
        os.replace(tmp, target)
        print(f"created: {target}")
        return 0
    except Exception as exc:
        print(f"ERROR: failed to create template at {target}: {exc}", file=sys.stderr)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python tools/ensure_local_journal_templates.py <local_journal_dir>", file=sys.stderr)
        return 2
    local_dir = Path(argv[1]).expanduser()
    return ensure_local_journal_templates(local_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
