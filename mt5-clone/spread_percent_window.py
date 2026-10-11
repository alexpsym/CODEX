#!/usr/bin/env python3
"""Desktop pop-out spread percentage monitor for the MT5 feeder EA."""

from __future__ import annotations

import argparse
import json
import locale
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
import tkinter as tk
from tkinter import ttk


DEFAULT_FEED_NAME = "MarketWatchSpreadPercentFeed.json"
HANDOFF_FRESH_SECONDS = 30
SW_SHOWMINNOACTIVE = 7
OWNER_PROCESS_CHECK_MS = 500
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


class SpreadLifecycleError(RuntimeError):
    pass


def default_feed_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show MT5 Market Watch spread percentages in a desktop window.")
    parser.add_argument("--file", default=str(default_feed_path()), help="Path to the feeder JSON file.")
    parser.add_argument("--refresh-ms", type=int, default=150, help="UI refresh interval in milliseconds.")
    parser.add_argument("--decimals", type=int, default=5, help="Decimal places for spread percent values.")
    parser.add_argument("--font-size", type=int, default=12, help="Table font size.")
    parser.add_argument("--show-points", action="store_true", help="Also show Spread Points.")
    parser.add_argument("--owner-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--auto-start", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--terminal-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--terminal-created", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


@dataclass(frozen=True)
class SpreadWindowConfig:
    feed_path: Path
    refresh_ms: int
    decimals: int
    font_size: int
    show_points: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SpreadWindowConfig":
        return cls(
            feed_path=Path(args.file),
            refresh_ms=clamp(args.refresh_ms, 50, 5000),
            decimals=clamp(args.decimals, 0, 8),
            font_size=clamp(args.font_size, 8, 22),
            show_points=bool(args.show_points),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "file": str(self.feed_path),
            "refresh_ms": self.refresh_ms,
            "decimals": self.decimals,
            "font_size": self.font_size,
            "show_points": self.show_points,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpreadWindowConfig | None":
        if (
            not isinstance(payload.get("file"), str)
            or not isinstance(payload.get("refresh_ms"), int)
            or not isinstance(payload.get("decimals"), int)
            or not isinstance(payload.get("font_size"), int)
            or not isinstance(payload.get("show_points"), bool)
        ):
            return None
        return cls(
            feed_path=Path(payload["file"]),
            refresh_ms=clamp(payload["refresh_ms"], 50, 5000),
            decimals=clamp(payload["decimals"], 0, 8),
            font_size=clamp(payload["font_size"], 8, 22),
            show_points=payload["show_points"],
        )


@dataclass(frozen=True)
class SpreadOwnerIdentity:
    owner_id: str
    terminal_pid: int
    terminal_creation_time: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SpreadOwnerIdentity":
        identity = cls(
            owner_id=str(args.owner_id),
            terminal_pid=int(args.terminal_pid),
            terminal_creation_time=int(args.terminal_created),
        )
        if (
            not identity.owner_id
            or identity.terminal_pid <= 0
            or identity.terminal_creation_time <= 0
        ):
            raise SpreadLifecycleError(
                "Automatic Spread startup requires a valid owning MT5 PID and creation time."
            )
        return identity

    @property
    def lifetime_id(self) -> str:
        return (
            f"{self.owner_id}.{self.terminal_pid}."
            f"{self.terminal_creation_time}"
        )


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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


class InstanceWindowGuard:
    """An OS-held byte lock; an old file cannot keep a later process blocked."""

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


class SpreadHandoffMailbox:
    """Atomic settings handoff for one terminal/chart-owned Spread window."""

    def __init__(self, runtime_dir: Path, identity: SpreadOwnerIdentity) -> None:
        self.identity = identity
        self.path = Path(runtime_dir) / (
            f"SpreadPercentWindow.owner.v1.{identity.lifetime_id}.handoff.json"
        )

    def publish(self, config: SpreadWindowConfig, now: float | None = None) -> None:
        payload = config.payload()
        payload.update(
            {
                "owner_id": self.identity.owner_id,
                "terminal_pid": self.identity.terminal_pid,
                "terminal_creation_time": self.identity.terminal_creation_time,
                "requested_at": int(time.time() if now is None else now),
                "request_id": str(uuid.uuid4()),
            }
        )
        atomic_write_json(self.path, payload)

    def read(self, now: float | None = None) -> SpreadWindowConfig | None:
        try:
            payload = json.loads(self.path.read_text(encoding="ascii"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        current = time.time() if now is None else now
        requested_at = payload.get("requested_at") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("owner_id") != self.identity.owner_id
            or payload.get("terminal_pid") != self.identity.terminal_pid
            or payload.get("terminal_creation_time")
            != self.identity.terminal_creation_time
            or not isinstance(requested_at, int)
            or current < requested_at - 5
            or current - requested_at > HANDOFF_FRESH_SECONDS
        ):
            return None
        return SpreadWindowConfig.from_payload(payload)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class SpreadWindowOwnership:
    """Hold one Spread desktop window for one stable terminal/chart owner."""

    def __init__(
        self,
        identity: SpreadOwnerIdentity,
        guard: InstanceWindowGuard,
        mailbox: SpreadHandoffMailbox,
    ) -> None:
        self.identity = identity
        self.guard = guard
        self.mailbox = mailbox

    @classmethod
    def claim_or_handoff(
        cls,
        runtime_dir: Path,
        identity: SpreadOwnerIdentity,
        config: SpreadWindowConfig,
        guard_factory: Callable[[Path], InstanceWindowGuard] = InstanceWindowGuard,
    ) -> "SpreadWindowOwnership | None":
        mailbox = SpreadHandoffMailbox(runtime_dir, identity)
        guard_path = Path(runtime_dir) / (
            f"SpreadPercentWindow.owner.v1.{identity.lifetime_id}.window-guard.lock"
        )
        guard = guard_factory(guard_path)
        if not guard.acquire():
            mailbox.publish(config)
            return None
        mailbox.clear()
        return cls(identity, guard, mailbox)

    def accept_handoff(self, now: float | None = None) -> SpreadWindowConfig | None:
        config = self.mailbox.read(now)
        if config is not None:
            self.mailbox.clear()
        return config

    def release(self) -> None:
        self.guard.release()


class WindowsProcessApi:
    """Limited-rights, pointer-width-safe process lifetime calls."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        self._ctypes = ctypes
        self._FileTime = FileTime
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        self._kernel32.GetProcessTimes.restype = wintypes.BOOL
        self._kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def _error(self, action: str) -> SpreadLifecycleError:
        return SpreadLifecycleError(
            f"{action} failed with Windows error {self._ctypes.get_last_error()}."
        )

    def open_process(self, pid: int) -> Any:
        handle = self._kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            raise self._error("OpenProcess for the owning MT5 terminal")
        return handle

    def creation_time(self, handle: Any) -> int:
        created = self._FileTime()
        exited = self._FileTime()
        kernel = self._FileTime()
        user = self._FileTime()
        if not self._kernel32.GetProcessTimes(
            handle,
            self._ctypes.byref(created),
            self._ctypes.byref(exited),
            self._ctypes.byref(kernel),
            self._ctypes.byref(user),
        ):
            raise self._error("GetProcessTimes for the owning MT5 terminal")
        return (int(created.high) << 32) | int(created.low)

    def wait_zero(self, handle: Any) -> int:
        result = int(self._kernel32.WaitForSingleObject(handle, 0))
        if result == WAIT_FAILED:
            raise self._error("WaitForSingleObject for the owning MT5 terminal")
        return result

    def close_handle(self, handle: Any) -> None:
        if not self._kernel32.CloseHandle(handle):
            raise self._error("CloseHandle for the owning MT5 terminal")


class TerminalProcessMonitor:
    """Retain and nonblockingly observe one verified MT5 process lifetime."""

    def __init__(
        self,
        identity: SpreadOwnerIdentity,
        handle: Any,
        process_api: Any,
    ) -> None:
        self.identity = identity
        self.handle = handle
        self.process_api = process_api

    @classmethod
    def open(
        cls,
        identity: SpreadOwnerIdentity,
        process_api: Any | None = None,
    ) -> "TerminalProcessMonitor":
        api = WindowsProcessApi() if process_api is None else process_api
        handle = api.open_process(identity.terminal_pid)
        try:
            actual_creation = api.creation_time(handle)
            if actual_creation != identity.terminal_creation_time:
                raise SpreadLifecycleError(
                    "Owning MT5 process creation time does not match; "
                    "the PID may have been reused."
                )
        except Exception:
            try:
                api.close_handle(handle)
            except Exception:
                pass
            raise
        return cls(identity, handle, api)

    def exited(self) -> bool:
        if self.handle is None:
            raise SpreadLifecycleError("Owning MT5 process handle is closed.")
        result = self.process_api.wait_zero(self.handle)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise SpreadLifecycleError(f"Unexpected owning MT5 wait result: {result}.")

    def close(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        self.process_api.close_handle(handle)


class WindowsUser32Api:
    """Pointer-width-safe GetParent and ShowWindow binding."""

    def __init__(self, library: Any | None = None) -> None:
        import ctypes
        from ctypes import wintypes

        self._user32 = (
            ctypes.WinDLL("user32", use_last_error=True)
            if library is None
            else library
        )
        self._user32.GetParent.argtypes = [wintypes.HWND]
        self._user32.GetParent.restype = wintypes.HWND
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL

    def get_parent(self, hwnd: int) -> int:
        return int(self._user32.GetParent(hwnd) or 0)

    def show_window(self, hwnd: int, mode: int) -> None:
        self._user32.ShowWindow(hwnd, mode)


def show_minimized_no_activate(root: tk.Tk, user32: Any | None = None) -> None:
    """Map the normal Tk top-level directly as minimized without activation."""
    root.update_idletasks()
    if os.name != "nt" and user32 is None:
        root.iconify()
        return
    if user32 is None:
        user32 = WindowsUser32Api()
    hwnd = int(root.winfo_id())
    if hasattr(user32, "get_parent"):
        parent = int(user32.get_parent(hwnd) or 0)
    else:
        parent = int(user32.GetParent(hwnd) or 0)
    if parent:
        hwnd = parent
    if hasattr(user32, "show_window"):
        user32.show_window(hwnd, SW_SHOWMINNOACTIVE)
    else:
        user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)


@dataclass(frozen=True)
class SpreadRow:
    symbol: str
    spread_percent: float | None
    spread_points: float | None


class SpreadPercentWindow:
    def __init__(
        self,
        root: tk.Tk,
        args: argparse.Namespace,
        on_close: Callable[[], None] = lambda: None,
        ownership: SpreadWindowOwnership | None = None,
        process_monitor: TerminalProcessMonitor | None = None,
    ) -> None:
        self.root = root
        config = SpreadWindowConfig.from_args(args)
        self.feed_path = config.feed_path
        self.refresh_ms = config.refresh_ms
        self.decimals = config.decimals
        self.font_size = config.font_size
        self.show_points = config.show_points
        self.on_close = on_close
        self.ownership = ownership
        self.process_monitor = process_monitor
        self.owner_monitor_error: str | None = None
        self._feed_status_text = ""
        self._closed = False
        self._refresh_after_id: Any = None
        self._owner_after_id: Any = None
        self.rows: list[SpreadRow] = []
        self.sort_column: str | None = None
        self.sort_desc = False

        self.root.title("Market Watch Spread %")
        self.root.geometry("520x640")
        self.root.minsize(380, 280)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self._build_styles()
        self._build_widgets()
        self._schedule_refresh(0)
        if self.process_monitor is not None:
            self._owner_after_id = self.root.after(0, self._check_owner_process)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        table_font = ("Segoe UI", self.font_size)
        header_font = ("Segoe UI", self.font_size, "bold")
        row_height = max(26, self.font_size + 14)

        style.configure("Spread.Treeview", font=table_font, rowheight=row_height, borderwidth=1)
        style.configure("Spread.Treeview.Heading", font=header_font, padding=(8, 6))
        style.configure("Status.TLabel", font=("Segoe UI", max(9, self.font_size - 2)), padding=(8, 6))

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 10, 10, 8))
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(frame, show="headings", style="Spread.Treeview")
        self._configure_tree_columns()

        self.tree.tag_configure("odd", background="#f7f9fc")
        self.tree.tag_configure("even", background="#ffffff")
        self.tree.tag_configure("na", foreground="#777777")

        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        self.status_var = tk.StringVar(value="")
        status = ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status.grid(row=1, column=0, columnspan=2, sticky="ew")

    def _toggle_symbol_sort(self) -> None:
        if self.sort_column == "symbol":
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = "symbol"
            self.sort_desc = False
        self._update_headings()
        self._render_rows()

    def _toggle_spread_sort(self) -> None:
        if self.sort_column == "spread_percent":
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = "spread_percent"
            self.sort_desc = True
        self._update_headings()
        self._render_rows()

    def _update_headings(self) -> None:
        symbol_suffix = ""
        spread_suffix = ""
        if self.sort_column == "symbol":
            symbol_suffix = " ↓" if self.sort_desc else " ↑"
        elif self.sort_column == "spread_percent":
            spread_suffix = " ↓" if self.sort_desc else " ↑"

        self.tree.heading("symbol", text=f"Symbol{symbol_suffix}", command=self._toggle_symbol_sort)
        self.tree.heading("spread_percent", text=f"Spread %{spread_suffix}", command=self._toggle_spread_sort)

    def _configure_tree_columns(self) -> None:
        existing_widths: dict[str, int] = {}
        for name in ("symbol", "spread_percent"):
            try:
                existing_widths[name] = int(self.tree.column(name, "width"))
            except (KeyError, TypeError, ValueError, tk.TclError):
                pass
        columns = ["symbol", "spread_percent"]
        if self.show_points:
            columns.append("spread_points")
        self.tree.configure(columns=columns)
        self.tree.heading("symbol", text="Symbol", command=self._toggle_symbol_sort)
        self.tree.heading("spread_percent", text="Spread %", command=self._toggle_spread_sort)
        self.tree.column(
            "symbol",
            width=existing_widths.get("symbol", 220),
            minwidth=140,
            anchor="w",
            stretch=True,
        )
        self.tree.column(
            "spread_percent",
            width=existing_widths.get("spread_percent", 180),
            minwidth=140,
            anchor="e",
            stretch=True,
        )
        if self.show_points:
            self.tree.heading("spread_points", text="Spread Points")
            self.tree.column("spread_points", width=140, minwidth=120, anchor="e", stretch=True)
        self._update_headings()

    def _apply_config(self, config: SpreadWindowConfig) -> None:
        font_changed = config.font_size != self.font_size
        columns_changed = config.show_points != self.show_points
        self.feed_path = config.feed_path
        self.refresh_ms = config.refresh_ms
        self.decimals = config.decimals
        self.font_size = config.font_size
        self.show_points = config.show_points
        if font_changed:
            self._build_styles()
        if columns_changed:
            self._configure_tree_columns()

    def _accept_handoff(self) -> bool:
        if self.ownership is None:
            return False
        config = self.ownership.accept_handoff()
        if config is None:
            return False
        self._apply_config(config)
        return True

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        self._refresh_after_id = self.root.after(
            self.refresh_ms if delay_ms is None else delay_ms,
            self._refresh,
        )

    def _set_feed_status(self, text: str) -> None:
        self._feed_status_text = text
        if self.owner_monitor_error:
            self.status_var.set(
                f"Owner verification failed: {self.owner_monitor_error} | {text}"
            )
        else:
            self.status_var.set(text)

    def _refresh(self) -> None:
        if self._closed:
            return
        self._refresh_after_id = None
        self._accept_handoff()
        try:
            feed = self._read_feed()
        except FileNotFoundError:
            self.rows = []
            self._clear_rows()
            self._set_feed_status(
                "Feed file missing. Attach MarketWatchSpreadPercentFeed.mq5 "
                f"in MT5: {self.feed_path}"
            )
            self._schedule_refresh()
            return
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self._set_feed_status("Feed file is being updated; retrying...")
            self._schedule_refresh()
            return

        self.rows = self._parse_rows(feed)
        generated_at = str(feed.get("generated_at") or "unknown")
        self._set_feed_status(
            f"{len(self.rows)} symbols | Updated {generated_at} | {self.feed_path}"
        )
        self._render_rows()
        self._schedule_refresh()

    def _check_owner_process(self) -> None:
        if self._closed or self.process_monitor is None:
            return
        self._owner_after_id = None
        try:
            exited = self.process_monitor.exited()
        except SpreadLifecycleError as exc:
            self.owner_monitor_error = str(exc)
            self._set_feed_status(self._feed_status_text)
        else:
            self.owner_monitor_error = None
            if exited:
                self._close()
                return
            if self._feed_status_text:
                self._set_feed_status(self._feed_status_text)
        if not self._closed:
            self._owner_after_id = self.root.after(
                OWNER_PROCESS_CHECK_MS,
                self._check_owner_process,
            )

    def _read_feed(self) -> dict[str, Any]:
        try:
            text = self.feed_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = self.feed_path.read_text(encoding=locale.getpreferredencoding(False))
        return json.loads(text)

    def _parse_rows(self, feed: dict[str, Any]) -> list[SpreadRow]:
        parsed: list[SpreadRow] = []
        for raw in feed.get("symbols", []):
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "")
            if not symbol:
                continue
            spread_percent = self._optional_float(raw.get("spread_percent"))
            spread_points = self._optional_float(raw.get("spread_points"))
            parsed.append(SpreadRow(symbol=symbol, spread_percent=spread_percent, spread_points=spread_points))
        return parsed

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _sorted_rows(self) -> list[SpreadRow]:
        rows = list(self.rows)
        if self.sort_column == "symbol":
            rows.sort(key=lambda row: row.symbol.casefold(), reverse=self.sort_desc)
            return rows

        if self.sort_column == "spread_percent":
            valid = [row for row in rows if row.spread_percent is not None]
            invalid = [row for row in rows if row.spread_percent is None]
            valid.sort(key=lambda row: row.spread_percent or 0.0, reverse=self.sort_desc)
            invalid.sort(key=lambda row: row.symbol.casefold())
            return valid + invalid

        return rows

    def _format_percent(self, value: float | None) -> str:
        if value is None:
            return "N/A"
        return f"{value:.{self.decimals}f}%"

    @staticmethod
    def _format_points(value: float | None) -> str:
        if value is None:
            return "N/A"
        return f"{value:.1f}"

    def _row_values(self, row: SpreadRow) -> tuple[str, ...]:
        values = [row.symbol, self._format_percent(row.spread_percent)]
        if self.show_points:
            values.append(self._format_points(row.spread_points))
        return tuple(values)

    def _render_rows(self) -> None:
        ordered = self._sorted_rows()
        wanted: set[str] = set()

        for index, row in enumerate(ordered):
            iid = row.symbol
            wanted.add(iid)
            tags = ["even" if index % 2 == 0 else "odd"]
            if row.spread_percent is None:
                tags.append("na")

            if self.tree.exists(iid):
                self.tree.item(iid, values=self._row_values(row), tags=tuple(tags))
            else:
                self.tree.insert("", "end", iid=iid, values=self._row_values(row), tags=tuple(tags))
            self.tree.move(iid, "", index)

        for iid in self.tree.get_children(""):
            if iid not in wanted:
                self.tree.delete(iid)

    def _clear_rows(self) -> None:
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)

    def close_resources(self) -> None:
        if self._closed:
            return
        self._closed = True
        for callback_id in (self._refresh_after_id, self._owner_after_id):
            if callback_id is None:
                continue
            try:
                self.root.after_cancel(callback_id)
            except (AttributeError, tk.TclError):
                pass
        self._refresh_after_id = None
        self._owner_after_id = None
        try:
            if self.process_monitor is not None:
                self.process_monitor.close()
        except SpreadLifecycleError as exc:
            self.owner_monitor_error = str(exc)
        finally:
            self.process_monitor = None
            self.on_close()

    def _close(self) -> None:
        if self._closed:
            return
        self.close_resources()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run_window(
    args: argparse.Namespace,
    root_factory: Callable[[], tk.Tk] = tk.Tk,
    window_factory: Callable[..., SpreadPercentWindow] = SpreadPercentWindow,
    guard_factory: Callable[[Path], InstanceWindowGuard] = InstanceWindowGuard,
    user32: Any | None = None,
    process_api: Any | None = None,
) -> str:
    config = SpreadWindowConfig.from_args(args)
    identity: SpreadOwnerIdentity | None = None
    process_monitor: TerminalProcessMonitor | None = None
    ownership: SpreadWindowOwnership | None = None
    window: SpreadPercentWindow | None = None
    if args.owner_id:
        identity = SpreadOwnerIdentity.from_args(args)
        runtime_dir = Path(args.runtime_dir)
        if not args.runtime_dir:
            raise SpreadLifecycleError(
                "Automatic Spread startup requires the MT5 Common Files runtime directory."
            )
        process_monitor = TerminalProcessMonitor.open(
            identity,
            process_api=process_api,
        )
    try:
        if identity is not None:
            ownership = SpreadWindowOwnership.claim_or_handoff(
                runtime_dir,
                identity,
                config,
                guard_factory=guard_factory,
            )
            if ownership is None:
                return "handoff"
        root = root_factory()
        if args.auto_start:
            root.withdraw()
        window = window_factory(
            root,
            args,
            ownership.release if ownership is not None else (lambda: None),
            ownership,
            process_monitor,
        )
        process_monitor = None
        if args.auto_start:
            show_minimized_no_activate(root, user32=user32)
        root.mainloop()
        return "started"
    finally:
        if window is not None:
            window.close_resources()
        else:
            try:
                if process_monitor is not None:
                    process_monitor.close()
            finally:
                if ownership is not None:
                    ownership.release()


def _startup_diagnostic_path(args: argparse.Namespace) -> Path | None:
    if (
        not args.owner_id
        or not args.runtime_dir
        or args.terminal_pid <= 0
        or args.terminal_created <= 0
    ):
        return None
    identity = SpreadOwnerIdentity.from_args(args)
    return Path(args.runtime_dir) / (
        f"SpreadPercentWindow.owner.v1.{identity.lifetime_id}.startup-error.json"
    )


def _write_startup_diagnostic(args: argparse.Namespace, message: str) -> None:
    path = _startup_diagnostic_path(args)
    if path is None:
        return
    atomic_write_json(
        path,
        {
            "owner_id": args.owner_id,
            "terminal_pid": args.terminal_pid,
            "terminal_creation_time": args.terminal_created,
            "error": message,
        },
    )


def main() -> None:
    args = parse_args()
    try:
        run_window(args)
    except Exception as exc:
        if not args.auto_start:
            raise
        try:
            _write_startup_diagnostic(args, str(exc))
        except OSError:
            pass


if __name__ == "__main__":
    main()
