#!/usr/bin/env python3
"""Desktop pop-out spread percentage monitor for the MT5 feeder EA."""

from __future__ import annotations

import argparse
import json
import locale
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tkinter as tk
from tkinter import ttk


DEFAULT_FEED_NAME = "MarketWatchSpreadPercentFeed.json"


def default_feed_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show MT5 Market Watch spread percentages in a desktop window.")
    parser.add_argument("--file", default=str(default_feed_path()), help="Path to the feeder JSON file.")
    parser.add_argument("--refresh-ms", type=int, default=150, help="UI refresh interval in milliseconds.")
    parser.add_argument("--decimals", type=int, default=5, help="Decimal places for spread percent values.")
    parser.add_argument("--font-size", type=int, default=12, help="Table font size.")
    parser.add_argument("--show-points", action="store_true", help="Also show Spread Points.")
    return parser.parse_args()


@dataclass(frozen=True)
class SpreadRow:
    symbol: str
    spread_percent: float | None
    spread_points: float | None


class SpreadPercentWindow:
    def __init__(self, root: tk.Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.feed_path = Path(args.file)
        self.refresh_ms = clamp(args.refresh_ms, 50, 5000)
        self.decimals = clamp(args.decimals, 0, 8)
        self.font_size = clamp(args.font_size, 8, 22)
        self.show_points = bool(args.show_points)
        self.rows: list[SpreadRow] = []
        self.sort_column: str | None = None
        self.sort_desc = False

        self.root.title("Market Watch Spread %")
        self.root.geometry("520x640")
        self.root.minsize(380, 280)

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

        columns = ["symbol", "spread_percent"]
        if self.show_points:
            columns.append("spread_points")

        self.tree = ttk.Treeview(frame, columns=columns, show="headings", style="Spread.Treeview")
        self.tree.heading("symbol", text="Symbol", command=self._toggle_symbol_sort)
        self.tree.heading("spread_percent", text="Spread %", command=self._toggle_spread_sort)
        self.tree.column("symbol", width=220, minwidth=140, anchor="w", stretch=True)
        self.tree.column("spread_percent", width=180, minwidth=140, anchor="e", stretch=True)

        if self.show_points:
            self.tree.heading("spread_points", text="Spread Points")
            self.tree.column("spread_points", width=140, minwidth=120, anchor="e", stretch=True)

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

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        self.root.after(self.refresh_ms if delay_ms is None else delay_ms, self._refresh)

    def _refresh(self) -> None:
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


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    SpreadPercentWindow(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
