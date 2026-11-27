"""Read account observations scoped to one verified MT5 process lifetime."""

import json
from pathlib import Path


def account_switched(
    common_dir: Path, pid: int, created: int, login: int, server: str, attached_ms: int
) -> bool:
    if min(pid, created, login, attached_ms) <= 0 or not server:
        return False
    observations = []
    for tool in ("trader", "spread"):
        path = common_dir / f"MT5DesktopAccount.{pid}.{created}.{tool}.json"
        try:
            with path.open("rb") as stream:
                observation = json.loads(stream.read(4096))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(observation, dict):
            continue
        when = observation.get("observed_ms")
        current_login = observation.get("login")
        current_server = observation.get("server")
        if (type(when) is int and when > attached_ms and type(current_login) is int
                and current_login > 0 and isinstance(current_server, str) and current_server):
            observations.append((when, current_login, current_server))
    if not observations:
        return False
    newest = max(row[0] for row in observations)
    # If observations have the same clock tick, wait for another sample to resolve conflict.
    return all((number, name) != (login, server) for when, number, name in observations if when == newest)
