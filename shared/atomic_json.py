from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any


_WIN_RETRYABLE_CODES = {5, 32}


def _is_retryable_write_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in _WIN_RETRYABLE_CODES:
            return True
    return False


def _log_best_effort_failure(path: Path, message: str, exc: BaseException) -> None:
    print(
        f"[atomic_json] {message}: path={path} error={exc!r}",
        file=sys.stderr,
        flush=True,
    )


def write_json_file(
    path: str | Path,
    payload: Any,
    *,
    best_effort: bool = False,
    retries: int = 10,
    backoff: float = 0.05,
    direct_fallback: bool = False,
) -> None:
    target = Path(path)
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    attempts = max(0, int(retries))
    sleep_seconds = max(0.0, float(backoff))
    last_error: BaseException | None = None

    try:
        tmp_path.write_text(encoded, encoding="utf-8")
        for attempt in range(attempts + 1):
            try:
                os.replace(tmp_path, target)
                return
            except Exception as exc:
                last_error = exc
                if _is_retryable_write_error(exc) and attempt < attempts:
                    time.sleep(sleep_seconds)
                    continue
                break

        if direct_fallback:
            for attempt in range(attempts + 1):
                try:
                    target.write_text(encoded, encoding="utf-8")
                    return
                except Exception as exc:
                    last_error = exc
                    if _is_retryable_write_error(exc) and attempt < attempts:
                        time.sleep(sleep_seconds)
                        continue
                    break

        if best_effort:
            _log_best_effort_failure(target, "best-effort runtime status write failed", last_error or RuntimeError("unknown write failure"))
            return

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to write JSON file: {target}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception as cleanup_exc:
            if best_effort:
                _log_best_effort_failure(target, "best-effort temp cleanup failed", cleanup_exc)
