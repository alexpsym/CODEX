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

    def __init__(self, runtime_dir: Path, owner_id: str) -> None:
        self.owner_id = owner_id
        self.path = Path(runtime_dir) / f"SpreadPercentWindow.owner.v1.{owner_id}.handoff.json"

    def publish(self, config: SpreadWindowConfig, now: float | None = None) -> None:
        payload = config.payload()
        payload.update(
            {
                "owner_id": self.owner_id,
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
            or payload.get("owner_id") != self.owner_id
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
        owner_id: str,
        guard: InstanceWindowGuard,
        mailbox: SpreadHandoffMailbox,
    ) -> None:
        self.owner_id = owner_id
        self.guard = guard
        self.mailbox = mailbox

    @classmethod
    def claim_or_handoff(
        cls,
        runtime_dir: Path,
        owner_id: str,
        config: SpreadWindowConfig,
        guard_factory: Callable[[Path], InstanceWindowGuard] = InstanceWindowGuard,
    ) -> "SpreadWindowOwnership | None":
        if not owner_id:
            raise ValueError("Automatic Spread startup requires an owner identity.")
        mailbox = SpreadHandoffMailbox(runtime_dir, owner_id)
        guard_path = Path(runtime_dir) / f"SpreadPercentWindow.owner.v1.{owner_id}.window-guard.lock"
        guard = guard_factory(guard_path)
        if not guard.acquire():
            mailbox.publish(config)
            return None
        mailbox.clear()
        return cls(owner_id, guard, mailbox)

    def accept_handoff(self, now: float | None = None) -> SpreadWindowConfig | None:
        config = self.mailbox.read(now)
        if config is not None:
            self.mailbox.clear()
        return config

    def release(self) -> None:
        self.guard.release()


def show_minimized_no_activate(root: tk.Tk, user32: Any | None = None) -> None:
    """Map the normal Tk top-level directly as minimized without activation."""
    root.update_idletasks()
    if os.name != "nt" and user32 is None:
        root.iconify()
        return
    if user32 is None:
        import ctypes

        user32 = ctypes.windll.user32
    hwnd = int(root.winfo_id())
    parent = int(user32.GetParent(hwnd))
    if parent:
        hwnd = parent
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
        self._closed = False
        self._refresh_after_id: Any = None
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
        columns = ["symbol", "spread_percent"]
        if self.show_points:
            columns.append("spread_points")
        self.tree.configure(columns=columns)
        self.tree.heading("symbol", text="Symbol", command=self._toggle_symbol_sort)
        self.tree.heading("spread_percent", text="Spread %", command=self._toggle_spread_sort)
        self.tree.column("symbol", width=220, minwidth=140, anchor="w", stretch=True)
        self.tree.column("spread_percent", width=180, minwidth=140, anchor="e", stretch=True)
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
            self.status_var.set(f"Feed file missing. Attach MarketWatchSpreadPercentFeed.mq5 in MT5: {self.feed_path}")
            self._schedule_refresh()
            return
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self.status_var.set("Feed file is being updated; retrying...")
            self._schedule_refresh()
            return

        self.rows = self._parse_rows(feed)
        generated_at = str(feed.get("generated_at") or "unknown")
        self.status_var.set(f"{len(self.rows)} symbols | Updated {generated_at} | {self.feed_path}")
        self._render_rows()
        self._schedule_refresh()

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
        if self._refresh_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_after_id)
            except (AttributeError, tk.TclError):
                pass
            self._refresh_after_id = None
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
) -> str:
    config = SpreadWindowConfig.from_args(args)
    ownership: SpreadWindowOwnership | None = None
    window: SpreadPercentWindow | None = None
    if args.owner_id:
        ownership = SpreadWindowOwnership.claim_or_handoff(
            config.feed_path.parent,
            args.owner_id,
            config,
            guard_factory=guard_factory,
        )
        if ownership is None:
            return "handoff"
    try:
        root = root_factory()
        if args.auto_start:
            root.withdraw()
        window = window_factory(
            root,
            args,
            ownership.release if ownership is not None else (lambda: None),
            ownership,
        )
        if args.auto_start:
            show_minimized_no_activate(root, user32=user32)
        root.mainloop()
        return "started"
    finally:
        if window is not None:
            window.close_resources()
        elif ownership is not None:
            ownership.release()


def main() -> None:
    run_window(parse_args())


if __name__ == "__main__":
    main()
