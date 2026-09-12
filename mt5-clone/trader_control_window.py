#!/usr/bin/env python3
"""Instance-scoped desktop controls for the Trader MT5 Expert Advisor."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import tkinter as tk
from tkinter import ttk


PROTOCOL_VERSION = 1
ACTIONS = ("market", "limit", "trendline", "ema_bounce")
ACTION_LABELS = {
    "market": "MARKET ORDER",
    "limit": "LIMIT ORDER",
    "trendline": "TRENDLINE",
    "ema_bounce": "EMA BOUNCE",
}
MIN_REFRESH_MS = 100
MAX_REFRESH_MS = 5000
MAX_STATUS_FRESH_SECONDS = 30
COMMAND_PENDING_SECONDS = 20
STARTUP_GRACE_SECONDS = 8


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstanceIdentity:
    instance_id: str
    account_login: int
    account_server: str
    chart_id: int
    symbol: str
    magic_number: int
    ea_version: str


@dataclass(frozen=True)
class ControlState:
    connected: bool
    buttons_enabled: bool
    reason: str
    pending_command_id: str | None
    status: dict[str, Any] | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class JsonRead:
    payload: dict[str, Any] | None
    failure: str | None = None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one canonical command without ever exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class TraderControlProtocol:
    def __init__(self, common_dir: Path, identity: InstanceIdentity) -> None:
        self.common_dir = Path(common_dir)
        self.identity = identity
        namespace = f"TraderControl.v1.{identity.instance_id}"
        self.status_path = self.common_dir / f"{namespace}.status.json"
        self.command_path = self.common_dir / f"{namespace}.command.json"
        self.result_path = self.common_dir / f"{namespace}.result.json"
        self.started_at = time.time()

    @staticmethod
    def _read_json(path: Path) -> JsonRead:
        try:
            encoded = path.read_bytes()
        except FileNotFoundError:
            return JsonRead(None, "missing")
        except OSError:
            return JsonRead(None, "unreadable")
        try:
            text = encoded.decode("ascii")
        except UnicodeError:
            return JsonRead(None, "encoding")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return JsonRead(None, "malformed")
        if not isinstance(payload, dict):
            return JsonRead(None, "non_object")
        return JsonRead(payload)

    def _identity_matches(self, payload: dict[str, Any]) -> bool:
        return (
            payload.get("protocol_version") == PROTOCOL_VERSION
            and payload.get("instance_id") == self.identity.instance_id
            and payload.get("account_login") == self.identity.account_login
            and payload.get("account_server") == self.identity.account_server
            and payload.get("chart_id") == self.identity.chart_id
            and payload.get("symbol") == self.identity.symbol
            and payload.get("magic_number") == self.identity.magic_number
            and payload.get("ea_version") == self.identity.ea_version
        )

    def _pending_command_id(self, now: float) -> str | None:
        command = self._read_json(self.command_path).payload
        if not command:
            return None
        command_id = command.get("command_id")
        created_at = command.get("created_at")
        if (
            command.get("protocol_version") != PROTOCOL_VERSION
            or command.get("instance_id") != self.identity.instance_id
            or command.get("action") not in ACTIONS
            or not isinstance(command_id, str)
            or not isinstance(created_at, int)
            or now - created_at > COMMAND_PENDING_SECONDS
        ):
            return None
        result = self._read_json(self.result_path).payload
        if result and result.get("instance_id") == self.identity.instance_id and result.get("command_id") == command_id:
            return None
        return command_id

    def _status_failure_reason(self, failure: str, now: float) -> str:
        path = str(self.status_path)
        if failure == "missing":
            if now - self.started_at < STARTUP_GRACE_SECONDS:
                return f"Connecting: waiting for EA status at {path}."
            return f"EA status file is missing at {path}. Keep Trader attached and confirm EA/MT5 permissions."
        if failure == "unreadable":
            return f"EA status file is unreadable at {path}. Check FILE_COMMON access; controls remain disabled."
        if failure == "encoding":
            return f"EA status file has an unsupported encoding at {path}. Reload the attached Trader EA."
        if failure == "non_object":
            return f"EA status file is not a JSON object at {path}. Reload the attached Trader EA."
        return f"EA status file contains malformed JSON at {path}. Reload the attached Trader EA."

    def state(self, now: float | None = None) -> ControlState:
        current = time.time() if now is None else now
        status_read = self._read_json(self.status_path)
        status = status_read.payload
        result = self._read_json(self.result_path).payload
        pending = self._pending_command_id(current)
        if not status:
            return ControlState(
                False,
                False,
                self._status_failure_reason(status_read.failure or "malformed", current),
                pending,
                None,
                result,
            )
        if not self._identity_matches(status):
            return ControlState(False, False, "EA instance identity does not match this window.", pending, status, result)
        updated_at = status.get("updated_at")
        freshness = status.get("fresh_for_seconds")
        if not isinstance(updated_at, int) or not isinstance(freshness, int):
            return ControlState(False, False, "EA freshness data is malformed.", pending, status, result)
        freshness = clamp(freshness, 1, MAX_STATUS_FRESH_SECONDS)
        if current < updated_at - 5 or current - updated_at > freshness:
            return ControlState(False, False, "EA state is stale; controls are disconnected.", pending, status, result)
        if not status.get("control_ready"):
            return ControlState(False, False, str(status.get("reason") or "EA desktop controls are not ready."), pending, status, result)
        if not status.get("connected"):
            return ControlState(False, False, "MT5 is not connected.", pending, status, result)
        if not status.get("orders_enabled"):
            return ControlState(True, False, "OrdersEnabled is false.", pending, status, result)
        if pending:
            return ControlState(True, False, "A command is pending; repeated clicks are disabled.", pending, status, result)
        return ControlState(True, True, "Ready for one explicit command.", None, status, result)

    def issue_command(self, action: str, now: float | None = None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ProtocolError(f"Unsupported action: {action}")
        current = time.time() if now is None else now
        state = self.state(current)
        if not state.buttons_enabled:
            raise ProtocolError(state.reason)
        command = {
            "protocol_version": PROTOCOL_VERSION,
            "instance_id": self.identity.instance_id,
            "command_id": str(uuid.uuid4()),
            "action": action,
            "created_at": int(current),
        }
        atomic_write_json(self.command_path, command)
        return command


class InstanceWindowGuard:
    """OS-held lock: stale files are harmless because the lock dies with the process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        try:
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except (ImportError, OSError):
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass
        self.handle.close()
        self.handle = None


class TraderControlWindow:
    def __init__(
        self,
        root: tk.Tk,
        protocol: TraderControlProtocol,
        refresh_ms: int,
        on_close: Callable[[], None],
    ) -> None:
        self.root = root
        self.protocol = protocol
        self.refresh_ms = clamp(refresh_ms, MIN_REFRESH_MS, MAX_REFRESH_MS)
        self.on_close = on_close
        self.last_seen_result_id: str | None = None
        self.session_command_ids: set[str] = set()
        self.buttons: list[ttk.Button] = []

        root.title("Trader Controls")
        root.geometry("420x650")
        root.minsize(360, 580)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        root.after(0, self._refresh)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ident = self.protocol.identity
        self.identity_var = tk.StringVar(
            value=(
                f"EA v{ident.ea_version} | Account {ident.account_login}\n"
                f"{ident.account_server}\n"
                f"{ident.symbol} | Magic {ident.magic_number} | Chart {ident.chart_id}\n"
                f"Instance {ident.instance_id}"
            )
        )
        ttk.Label(frame, textvariable=self.identity_var, justify="center", anchor="center").grid(
            row=0, column=0, sticky="ew", pady=(0, 12)
        )

        for row, action in enumerate(ACTIONS, start=1):
            button = ttk.Button(frame, text=ACTION_LABELS[action], command=lambda item=action: self._click(item))
            button.grid(row=row, column=0, sticky="ew", ipady=10, pady=5)
            self.buttons.append(button)
            frame.rowconfigure(row, weight=1)

        guidance = (
            "Use: load the calculated .set into Trader and leave the EA attached; enable the required "
            "EA/MT5 permissions; wait for Connected | Orders enabled | Fresh. Choose only the matching "
            "action, click once, then wait for its structured result. Loading or reloading a .set never submits an order.\n"
            "Market uses the configured side and a fresh Bid/Ask. Limit uses the configured standard-limit "
            "side and entry. Trendline uses the selected, configured, or sole valid chart trendline. EMA Bounce "
            "uses the configured current EMA reference as one fixed limit price."
        )
        ttk.Label(frame, text=guidance, wraplength=380, justify="left").grid(
            row=5, column=0, sticky="ew", pady=(12, 4)
        )
        self.connection_var = tk.StringVar(value="Connecting...")
        self.result_var = tk.StringVar(value="Last command: none")
        ttk.Label(frame, textvariable=self.connection_var, wraplength=380, justify="left").grid(
            row=6, column=0, sticky="ew", pady=(8, 4)
        )
        ttk.Label(frame, textvariable=self.result_var, wraplength=380, justify="left").grid(
            row=7, column=0, sticky="ew", pady=(4, 0)
        )

    def _click(self, action: str) -> None:
        for button in self.buttons:
            button.state(["disabled"])
        try:
            command = self.protocol.issue_command(action)
        except ProtocolError as exc:
            self.result_var.set(f"{ACTION_LABELS[action]} | blocked | {exc}")
        except OSError as exc:
            self.result_var.set(f"{ACTION_LABELS[action]} | blocked | Command write failed: {exc}")
        else:
            self.session_command_ids.add(command["command_id"])
            self.result_var.set(f"{ACTION_LABELS[action]} | pending | {command['command_id']}")
        self._refresh()

    def _refresh(self) -> None:
        state = self.protocol.state()
        status_text = "Connected" if state.connected else "Disconnected"
        orders = "enabled" if state.status and state.status.get("orders_enabled") else "disabled"
        updated_at = state.status.get("updated_at") if state.status else None
        freshness = f"Fresh {max(0.0, time.time() - updated_at):.1f}s" if isinstance(updated_at, int) else "Freshness unavailable"
        self.connection_var.set(f"{status_text} | Orders {orders} | {freshness} | {state.reason}")
        button_state = ["!disabled"] if state.buttons_enabled else ["disabled"]
        for button in self.buttons:
            button.state(button_state)

        result = state.result
        if result and result.get("instance_id") == self.protocol.identity.instance_id:
            command_id = result.get("command_id")
            if (
                isinstance(command_id, str)
                and command_id in self.session_command_ids
                and command_id != self.last_seen_result_id
            ):
                self.last_seen_result_id = command_id
                action = ACTION_LABELS.get(str(result.get("action")), str(result.get("action") or "UNKNOWN"))
                outcome = str(result.get("outcome") or "unknown")
                reason = str(result.get("reason") or "No reason supplied.")
                self.result_var.set(f"{action} | {outcome} | {reason}")
        self.root.after(self.refresh_ms, self._refresh)

    def _close(self) -> None:
        self.on_close()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control one attached Trader EA chart instance.")
    parser.add_argument("--common-dir", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--account-login", required=True, type=int)
    parser.add_argument("--account-server", required=True)
    parser.add_argument("--chart-id", required=True, type=int)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--magic", required=True, type=int)
    parser.add_argument("--ea-version", required=True)
    parser.add_argument("--refresh-ms", type=int, default=250)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    identity = InstanceIdentity(
        instance_id=args.instance_id,
        account_login=args.account_login,
        account_server=args.account_server,
        chart_id=args.chart_id,
        symbol=args.symbol,
        magic_number=args.magic,
        ea_version=args.ea_version,
    )
    common_dir = Path(args.common_dir)
    guard = InstanceWindowGuard(common_dir / f"TraderControl.v1.{identity.instance_id}.window-guard.lock")
    if not guard.acquire():
        raise SystemExit("Trader Controls is already open for this EA chart instance.")
    root = tk.Tk()
    protocol = TraderControlProtocol(common_dir, identity)
    TraderControlWindow(root, protocol, args.refresh_ms, guard.release)
    try:
        root.mainloop()
    finally:
        guard.release()


if __name__ == "__main__":
    main()
