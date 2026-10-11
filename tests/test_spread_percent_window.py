from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = ROOT / "mt5-clone" / "spread_percent_window.py"
MQL_SOURCE = ROOT / "mt5-clone" / "MQL5" / "Experts" / "MarketWatchSpreadPercentFeed.mq5"


def _load_module():
    spec = importlib.util.spec_from_file_location("spread_percent_window_job9a2", PYTHON_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_auto_start_is_minimized_and_reinitialization_keeps_one_owner_window(tmp_path: Path) -> None:
    spread = _load_module()
    held_guards: set[Path] = set()

    class FakeGuard:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.held = False

        def acquire(self) -> bool:
            if self.path in held_guards:
                return False
            held_guards.add(self.path)
            self.held = True
            return True

        def release(self) -> None:
            if self.held:
                held_guards.remove(self.path)
                self.held = False

    roots = []
    windows = []
    show_calls = []

    class FakeRoot:
        def __init__(self, mainloop_action=None) -> None:
            self.mainloop_action = mainloop_action
            self.withdrawn = False
            self.visibility = "normal"
            self.cancelled = []
            self.destroyed = False
            roots.append(self)

        def withdraw(self) -> None:
            self.withdrawn = True
            self.visibility = "withdrawn"

        def update_idletasks(self) -> None:
            pass

        def winfo_id(self) -> int:
            return 101 + len(roots)

        def mainloop(self) -> None:
            if self.mainloop_action is not None:
                self.mainloop_action()

        def after_cancel(self, callback_id) -> None:
            self.cancelled.append(callback_id)

        def destroy(self) -> None:
            self.destroyed = True

    class FakeUser32:
        def GetParent(self, hwnd: int) -> int:
            return hwnd + 1000

        def ShowWindow(self, hwnd: int, mode: int) -> None:
            show_calls.append((hwnd, mode))
            roots[-1].visibility = "minimized"

    class FakeWindow:
        def __init__(self, root, args, on_close, ownership) -> None:
            self.root = root
            self.args = args
            self.on_close = on_close
            self.ownership = ownership
            self.closed = False
            windows.append(self)

        def close_resources(self) -> None:
            if self.closed:
                return
            self.closed = True
            self.on_close()

    def args_for(
        owner: str,
        feed: Path,
        *,
        refresh: int = 150,
        decimals: int = 5,
        font_size: int = 12,
        show_points: bool = False,
        auto_start: bool = True,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            file=str(feed),
            refresh_ms=refresh,
            decimals=decimals,
            font_size=font_size,
            show_points=show_points,
            owner_id=owner,
            auto_start=auto_start,
        )

    feed_one = tmp_path / "shared feed.json"
    feed_two = tmp_path / "current feed.json"
    first_args = args_for("terminal-a-chart-7", feed_one)
    next_args = args_for(
        "terminal-a-chart-7",
        feed_two,
        refresh=725,
        decimals=3,
        font_size=15,
        show_points=True,
    )
    different_args = args_for("terminal-b-chart-7", feed_one)

    def same_owner_reinitialization() -> None:
        root = roots[0]
        root.visibility = "restored"
        result = spread.run_window(
            next_args,
            root_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate root created")),
            window_factory=FakeWindow,
            guard_factory=FakeGuard,
            user32=FakeUser32(),
        )
        assert result == "handoff"
        assert root.visibility == "restored"
        assert len(windows) == 1

        handed_off = windows[0].ownership.accept_handoff()
        assert handed_off == spread.SpreadWindowConfig.from_args(next_args)

        actual = object.__new__(spread.SpreadPercentWindow)
        actual.feed_path = Path(first_args.file)
        actual.refresh_ms = first_args.refresh_ms
        actual.decimals = first_args.decimals
        actual.font_size = first_args.font_size
        actual.show_points = first_args.show_points
        actual.sort_column = "spread_percent"
        actual.sort_desc = True
        style_updates = []
        column_updates = []
        actual._build_styles = lambda: style_updates.append(actual.font_size)
        actual._configure_tree_columns = lambda: column_updates.append(actual.show_points)
        actual._apply_config(handed_off)
        assert actual.feed_path == feed_two
        assert (actual.refresh_ms, actual.decimals, actual.font_size, actual.show_points) == (725, 3, 15, True)
        assert (actual.sort_column, actual.sort_desc) == ("spread_percent", True)
        assert style_updates == [15]
        assert column_updates == [True]

        rows = actual._parse_rows(
            {
                "symbols": [
                    {"symbol": "EURUSD", "spread_percent": 0.012345, "spread_points": 1.2},
                    {"symbol": "XAUUSD", "spread_percent": None, "spread_points": None},
                ]
            }
        )
        actual.rows = rows
        assert actual._row_values(rows[0]) == ("EURUSD", "0.012%", "1.2")
        assert actual._row_values(rows[1]) == ("XAUUSD", "N/A", "N/A")

        independent = spread.run_window(
            different_args,
            root_factory=FakeRoot,
            window_factory=FakeWindow,
            guard_factory=FakeGuard,
            user32=FakeUser32(),
        )
        assert independent == "started"

    first_root = lambda: FakeRoot(same_owner_reinitialization)
    assert spread.run_window(
        first_args,
        root_factory=first_root,
        window_factory=FakeWindow,
        guard_factory=FakeGuard,
        user32=FakeUser32(),
    ) == "started"
    assert roots[0].withdrawn
    assert show_calls[0][1] == spread.SW_SHOWMINNOACTIVE == 7
    assert roots[0].visibility == "restored"
    assert len(windows) == 2
    assert not held_guards

    manual = spread.parse_args([])
    assert manual.owner_id == "" and not manual.auto_start
    manual_root = FakeRoot()
    assert spread.run_window(
        manual,
        root_factory=lambda: manual_root,
        window_factory=FakeWindow,
        guard_factory=FakeGuard,
        user32=FakeUser32(),
    ) == "started"
    assert not manual_root.withdrawn
    assert len(show_calls) == 2

    failing_args = args_for("terminal-a-chart-startup-failure", feed_one)
    try:
        spread.run_window(
            failing_args,
            root_factory=FakeRoot,
            window_factory=lambda *unused: (_ for _ in ()).throw(RuntimeError("startup failure")),
            guard_factory=FakeGuard,
            user32=FakeUser32(),
        )
    except RuntimeError as exc:
        assert str(exc) == "startup failure"
    else:
        raise AssertionError("startup failure was not propagated")
    assert not held_guards
    assert spread.run_window(
        failing_args,
        root_factory=FakeRoot,
        window_factory=FakeWindow,
        guard_factory=FakeGuard,
        user32=FakeUser32(),
    ) == "started"
    assert not held_guards

    cleanup_releases = []
    cleanup_root = FakeRoot()
    cleanup_guard = FakeGuard(tmp_path / "cleanup-owner.lock")
    assert cleanup_guard.acquire()
    cleanup = object.__new__(spread.SpreadPercentWindow)
    cleanup.root = cleanup_root
    cleanup._closed = False
    cleanup._refresh_after_id = "refresh-1"
    cleanup.on_close = lambda: (cleanup_releases.append("released"), cleanup_guard.release())
    cleanup._close()
    cleanup._close()
    assert cleanup_root.cancelled == ["refresh-1"]
    assert cleanup_root.destroyed
    assert cleanup_releases == ["released"]
    next_cleanup_guard = FakeGuard(tmp_path / "cleanup-owner.lock")
    assert next_cleanup_guard.acquire()
    next_cleanup_guard.release()

    mql = MQL_SOURCE.read_text(encoding="utf-8")
    assert '#property version   "1.12"' in mql
    assert '\\"version\\": \\"1.10\\"' in mql
    assert "TerminalInfoString(TERMINAL_DATA_PATH)" in mql
    assert "(string)ChartID()" in mql
    assert '" --owner-id " + QuoteArg(SpreadWindowOwnerId())' in mql
    assert '" --auto-start"' in mql
    assert "pythonw.exe" in mql
    assert "SW_SHOWMINNOACTIVE = 7" in mql
    assert "DirectoryName(script), SW_SHOWMINNOACTIVE" in mql
    launch = mql[mql.index("bool LaunchWindow()") : mql.index("string JsonEscape")]
    assert launch.index("MQLInfoInteger(MQL_DLLS_ALLOWED)") < launch.index("ConfiguredLaunchFileExists")
    assert "CalculateSpread" not in launch
