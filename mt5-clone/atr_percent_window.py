#!/usr/bin/env python3
"""Desktop ATR-percentage table for MarketWatchATRPercentFeed.mq5."""

from __future__ import annotations

import argparse
import json
import locale
import math
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import tkinter as tk
from tkinter import ttk


DEFAULT_FEED_NAME = "MarketWatchATRPercentFeed.json"
TIMEFRAMES: tuple[tuple[str, str], ...] = (
    ("1m", "m1"),
    ("5m", "m5"),
    ("1h", "h1"),
    ("1D", "d1"),
    ("1W", "w1"),
    ("1Mo", "mn1"),
)
TIMEFRAME_LABELS = tuple(label for label, _key in TIMEFRAMES)
TIMEFRAME_KEYS = {label: key for label, key in TIMEFRAMES}


def default_feed_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / DEFAULT_FEED_NAME


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank selected MT5 Market Watch forex instruments by last-closed-candle ATR percentage."
    )
    parser.add_argument("--file", default=str(default_feed_path()), help="Path to the ATR feeder JSON file.")
    parser.add_argument("--refresh-ms", type=int, default=500, help="UI refresh interval in milliseconds.")
    parser.add_argument("--decimals", type=int, default=5, help="Decimal places for ATR percentage values.")
    parser.add_argument("--font-size", type=int, default=11, help="Table font size.")
    parser.add_argument("--top-n", type=int, default=10, help="Number of ranked forex rows to display.")
    parser.add_argument(
        "--rank-timeframe",
        choices=TIMEFRAME_LABELS,
        default="1m",
        help="ATR timeframe used for ranking.",
    )
    return parser.parse_args(argv)


@dataclass(frozen=True)
class ATRRow:
    symbol: str
    is_forex: bool
    status: str
    reason: str
    atr_percent: Mapping[str, float | None]
    frame_states: Mapping[str, str]
    spread_percent: float | None = None
    spread_points: float | None = None


def optional_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def parse_feed_rows(feed: Mapping[str, Any]) -> list[ATRRow]:
    raw_rows = feed.get("symbols", [])
    if not isinstance(raw_rows, list):
        return []

    parsed: list[ATRRow] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        if not symbol:
            continue
        values: dict[str, float | None] = {}
        states: dict[str, str] = {}
        for label, key in TIMEFRAMES:
            values[label] = optional_positive_float(raw.get(f"atr_percent_{key}"))
            states[label] = str(raw.get(f"state_{key}") or "N/A")
        parsed.append(
            ATRRow(
                symbol=symbol,
                is_forex=raw.get("is_forex") is True,
                status=str(raw.get("status") or "Unknown"),
                reason=str(raw.get("reason") or ""),
                spread_percent=optional_nonnegative_float(raw.get("spread_percent", raw.get("spread_pct"))),
                spread_points=optional_nonnegative_float(raw.get("spread_points")),
                atr_percent=values,
                frame_states=states,
            )
        )
    return parsed


def optional_nonnegative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def rank_rows(rows: list[ATRRow], timeframe: str = "1m", top_n: int = 10) -> list[ATRRow]:
    if timeframe not in TIMEFRAME_KEYS:
        raise ValueError(f"Unsupported rank timeframe: {timeframe}")
    bounded_top_n = clamp(int(top_n), 1, 100)
    eligible = [row for row in rows if row.is_forex and row.atr_percent.get(timeframe) is not None]
    eligible.sort(key=lambda row: (-float(row.atr_percent[timeframe]), row.symbol.casefold(), row.symbol))
    return eligible[:bounded_top_n]


def diagnostic_rows(rows: list[ATRRow], timeframe: str) -> list[ATRRow]:
    if timeframe not in TIMEFRAME_KEYS:
        raise ValueError(f"Unsupported diagnostic timeframe: {timeframe}")
    diagnostics = [
        row
        for row in rows
        if not row.is_forex or row.atr_percent.get(timeframe) is None
    ]
    diagnostics.sort(key=lambda row: (row.symbol.casefold(), row.symbol))
    return diagnostics


def classify_window_state(
    rows: list[ATRRow],
    *,
    feed_age_seconds: float,
    heartbeat_tolerance_seconds: float,
) -> str:
    """Classify feed health from local heartbeat age and explicit ATR states."""

    if feed_age_seconds > heartbeat_tolerance_seconds:
        return "Stale"
    forex_rows = [row for row in rows if row.is_forex]
    if not forex_rows:
        return "Ready"
    states = {
        str(state).strip().casefold()
        for row in forex_rows
        for state in (row.status, *row.frame_states.values())
        if str(state).strip()
    }
    if "stale" in states:
        return "Stale"
    if "error" in states:
        return "Error"
    if "loading" in states:
        return "Loading"
    has_successful_value = any(
        value is not None
        for row in forex_rows
        for value in row.atr_percent.values()
    )
    return "Ready" if has_successful_value else "Loading"


class ATRPercentWindow:
    def __init__(self, root: tk.Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.feed_path = Path(args.file)
        self.refresh_ms = clamp(args.refresh_ms, 100, 5000)
        self.decimals = clamp(args.decimals, 0, 8)
        self.font_size = clamp(args.font_size, 8, 22)
        self.rows: list[ATRRow] = []
        self.sort_column = "symbol"
        self.sort_desc = False
        self.last_good_feed: dict[str, Any] | None = None
        self.rank_timeframe = tk.StringVar(value=args.rank_timeframe if args.rank_timeframe in TIMEFRAME_KEYS else "1m")
        self.top_n = tk.IntVar(value=clamp(args.top_n, 1, 100))

        self.root.title("Unified MT5 Market Watch — Spread and ATR %")
        self.root.geometry("1420x700")
        self.root.minsize(820, 360)

        self._build_styles()
        self._build_widgets()
        self._schedule_refresh(0)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        row_height = max(26, self.font_size + 14)
        style.configure("ATR.Treeview", font=("Segoe UI", self.font_size), rowheight=row_height, borderwidth=1)
        style.configure("ATR.Treeview.Heading", font=("Segoe UI", self.font_size, "bold"), padding=(7, 6))
        style.configure("ATR.Status.TLabel", font=("Segoe UI", max(9, self.font_size - 2)), padding=(8, 6))

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 10, 10, 8))
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(controls, text="Rank by").grid(row=0, column=0, padx=(0, 5))
        rank_select = ttk.Combobox(
            controls,
            textvariable=self.rank_timeframe,
            values=TIMEFRAME_LABELS,
            state="readonly",
            width=6,
        )
        rank_select.grid(row=0, column=1, padx=(0, 14))
        rank_select.bind("<<ComboboxSelected>>", self._controls_changed)
        ttk.Label(controls, text="Top N").grid(row=0, column=2, padx=(0, 5))
        top_n = ttk.Spinbox(controls, from_=1, to=100, textvariable=self.top_n, width=6, command=self._render_rows)
        top_n.grid(row=0, column=3)
        top_n.bind("<Return>", self._controls_changed)
        top_n.bind("<FocusOut>", self._controls_changed)
        ttk.Label(
            controls,
            text="Wilder iATR / same timeframe close; last closed candle; no volume ranking",
        ).grid(row=0, column=4, padx=(18, 0), sticky="w")
        controls.columnconfigure(4, weight=1)

        notebook = ttk.Notebook(frame)
        notebook.grid(row=1, column=0, sticky="nsew")
        ranked_frame = ttk.Frame(notebook)
        diagnostic_frame = ttk.Frame(notebook)
        notebook.add(ranked_frame, text="All Market Watch")
        notebook.add(diagnostic_frame, text="ATR ranking")
        self.ranked_tree = self._build_tree(ranked_frame, include_rank=False)
        self.diagnostic_tree = self._build_tree(diagnostic_frame, include_rank=True)

        self.status_var = tk.StringVar(value="Loading ATR feed...")
        ttk.Label(frame, textvariable=self.status_var, style="ATR.Status.TLabel", anchor="w").grid(
            row=2, column=0, sticky="ew"
        )

    def _build_tree(self, parent: ttk.Frame, *, include_rank: bool) -> ttk.Treeview:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        columns = (["rank"] if include_rank else []) + ["symbol", "spread_percent", "spread_points"] + [key for _label, key in TIMEFRAMES] + ["status"]
        tree = ttk.Treeview(parent, columns=columns, show="headings", style="ATR.Treeview")
        if include_rank:
            tree.heading("rank", text="Rank")
            tree.column("rank", width=54, minwidth=48, anchor="e", stretch=False)
        tree.heading("symbol", text="Instrument", command=lambda: self._toggle_sort("symbol"))
        tree.column("symbol", width=150, minwidth=110, anchor="w", stretch=True)
        tree.heading("spread_percent", text="Spread %", command=lambda: self._toggle_sort("spread_percent"))
        tree.column("spread_percent", width=105, minwidth=85, anchor="e", stretch=False)
        tree.heading("spread_points", text="Spread points", command=lambda: self._toggle_sort("spread_points"))
        tree.column("spread_points", width=110, minwidth=90, anchor="e", stretch=False)
        for label, key in TIMEFRAMES:
            tree.heading(key, text=f"ATR% {label}", command=lambda selected=label: self._toggle_sort(selected))
            tree.column(key, width=100, minwidth=82, anchor="e", stretch=False)
        tree.heading("status", text="Status / reason")
        tree.column("status", width=260, minwidth=180, anchor="w", stretch=True)
        tree.tag_configure("odd", background="#f7f9fc")
        tree.tag_configure("even", background="#ffffff")
        tree.tag_configure("na", foreground="#777777")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        return tree

    def _toggle_sort(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = column != "symbol"
        self._update_headings()
        self._render_rows()

    def _update_headings(self) -> None:
        labels = {"symbol": "Instrument", "spread_percent": "Spread %", "spread_points": "Spread points", **{label: f"ATR% {label}" for label in TIMEFRAME_LABELS}}
        keys = {"symbol": "symbol", "spread_percent": "spread_percent", "spread_points": "spread_points", **{label: TIMEFRAME_KEYS[label] for label in TIMEFRAME_LABELS}}
        suffix = " ↓" if self.sort_desc else " ↑"
        for tree in (self.ranked_tree, self.diagnostic_tree):
            for column, label in labels.items():
                tree.heading(keys[column], text=label + (suffix if self.sort_column == column else ""), command=lambda selected=column: self._toggle_sort(selected))

    def _sorted_rows(self, rows: list[ATRRow]) -> list[ATRRow]:
        if self.sort_column == "symbol":
            return sorted(rows, key=lambda row: (row.symbol.casefold(), row.symbol), reverse=self.sort_desc)
        def value(row: ATRRow) -> float | None:
            if self.sort_column == "spread_percent": return row.spread_percent
            if self.sort_column == "spread_points": return row.spread_points
            return row.atr_percent.get(self.sort_column)
        valid = [row for row in rows if value(row) is not None]
        invalid = [row for row in rows if value(row) is None]
        valid.sort(key=lambda row: float(value(row) or 0.0), reverse=self.sort_desc)
        invalid.sort(key=lambda row: (row.symbol.casefold(), row.symbol))
        return valid + invalid

    def _controls_changed(self, _event: object | None = None) -> None:
        self._render_rows()

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        self.root.after(self.refresh_ms if delay_ms is None else delay_ms, self._refresh)

    def _read_feed(self) -> dict[str, Any]:
        try:
            text = self.feed_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = self.feed_path.read_text(encoding=locale.getpreferredencoding(False))
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("ATR feed root is not an object")
        return parsed

    def _refresh(self) -> None:
        try:
            feed = self._read_feed()
            parsed_rows = parse_feed_rows(feed)
        except FileNotFoundError:
            if self.last_good_feed is None:
                self.status_var.set(f"Loading: attach MarketWatchATRPercentFeed.mq5 in MT5; feed missing: {self.feed_path}")
            else:
                self.status_var.set(f"Stale: last-known-good retained; feed file is missing: {self.feed_path}")
            self._schedule_refresh()
            return
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            state = "Loading" if self.last_good_feed is None else "Stale"
            self.status_var.set(f"{state}: last-known-good retained; feed read failed ({type(exc).__name__}); retrying")
            self._schedule_refresh()
            return

        self.last_good_feed = feed
        self.rows = parsed_rows
        generated_at = str(feed.get("generated_at") or "unknown")
        last_success = str(feed.get("last_successful_refresh") or "none yet")
        atr_length = feed.get("atr_length", "unknown")
        try:
            age_seconds = max(0, int(time.time() - self.feed_path.stat().st_mtime))
        except OSError:
            age_seconds = 0
        forex_count = sum(1 for row in self.rows if row.is_forex)
        state = classify_window_state(
            self.rows,
            feed_age_seconds=age_seconds,
            heartbeat_tolerance_seconds=max(
                5, self.refresh_ms * 6 // 1000
            ),
        )
        self.status_var.set(
            f"{state} | {forex_count}/{len(self.rows)} selected symbols are forex | ATR({atr_length}) | "
            f"Last successful ATR refresh {last_success} | feed written {generated_at} | file age {age_seconds}s"
        )
        self._render_rows()
        self._schedule_refresh()

    def _safe_top_n(self) -> int:
        try:
            value = int(self.top_n.get())
        except (tk.TclError, TypeError, ValueError):
            value = 10
        value = clamp(value, 1, 100)
        self.top_n.set(value)
        return value

    def _format_percent(self, value: float | None) -> str:
        return "N/A" if value is None else f"{value:.{self.decimals}f}%"

    def _row_values(self, row: ATRRow, rank: int | None) -> tuple[str, ...]:
        values = ([] if rank is None else [str(rank)]) + [row.symbol, self._format_percent(row.spread_percent), "N/A" if row.spread_points is None else f"{row.spread_points:.2f}"]
        values.extend(self._format_percent(row.atr_percent.get(label)) for label in TIMEFRAME_LABELS)
        state = row.status
        selected_state = row.frame_states.get(self.rank_timeframe.get(), "N/A")
        if selected_state and selected_state != state:
            state = f"{state}; {self.rank_timeframe.get()} {selected_state}"
        if row.reason:
            state = f"{state}: {row.reason}"
        values.append(state)
        return tuple(values)

    def _replace_tree_rows(self, tree: ttk.Treeview, rows: list[ATRRow], *, ranked: bool) -> None:
        wanted: set[str] = set()
        for index, row in enumerate(rows):
            iid = row.symbol
            wanted.add(iid)
            tags = ["even" if index % 2 == 0 else "odd"]
            if row.atr_percent.get(self.rank_timeframe.get()) is None:
                tags.append("na")
            values = self._row_values(row, index + 1 if ranked else None)
            if tree.exists(iid):
                tree.item(iid, values=values, tags=tuple(tags))
            else:
                tree.insert("", "end", iid=iid, values=values, tags=tuple(tags))
            tree.move(iid, "", index)
        for iid in tree.get_children(""):
            if iid not in wanted:
                tree.delete(iid)

    def _render_rows(self) -> None:
        timeframe = self.rank_timeframe.get()
        if timeframe not in TIMEFRAME_KEYS:
            timeframe = "1m"
            self.rank_timeframe.set(timeframe)
        all_rows = self._sorted_rows(self.rows)
        ranked = self._sorted_rows(rank_rows(self.rows, timeframe, self._safe_top_n()))
        self._replace_tree_rows(self.ranked_tree, all_rows, ranked=False)
        self._replace_tree_rows(self.diagnostic_tree, ranked, ranked=True)


def main(argv: list[str] | None = None) -> None:
    singleton = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        singleton.bind(("127.0.0.1", 41973))
        singleton.listen(1)
    except OSError:
        return
    args = parse_args(argv)
    root = tk.Tk()
    root._unified_market_watch_singleton = singleton  # keep the process lock for the window lifetime
    ATRPercentWindow(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
