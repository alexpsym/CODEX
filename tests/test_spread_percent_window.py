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
        def __init__(self, root, args, on_close, ownership, process_monitor) -> None:
            self.root = root
            self.args = args
            self.on_close = on_close
            self.ownership = ownership
            self.process_monitor = process_monitor
            self.closed = False
            windows.append(self)

        def close_resources(self) -> None:
            if self.closed:
                return
            self.closed = True
            try:
                if self.process_monitor is not None:
                    self.process_monitor.close()
            finally:
                self.on_close()

    process_creations = {101: 10_000_000_001, 202: 20_000_000_002, 303: 30_000_000_003}

    class FakeProcessApi:
        def __init__(self) -> None:
            self.open_handles = set()

        def open_process(self, pid: int):
            handle = (pid, len(self.open_handles) + 1)
            self.open_handles.add(handle)
            return handle

        def creation_time(self, handle) -> int:
            return process_creations[handle[0]]

        def wait_zero(self, handle) -> int:
            assert handle in self.open_handles
            return spread.WAIT_TIMEOUT

        def close_handle(self, handle) -> None:
            self.open_handles.remove(handle)

    process_api = FakeProcessApi()

    def args_for(
        owner: str,
        feed: Path,
        *,
        refresh: int = 150,
        decimals: int = 5,
        font_size: int = 12,
        show_points: bool = False,
        auto_start: bool = True,
        terminal_pid: int = 101,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            file=str(feed),
            refresh_ms=refresh,
            decimals=decimals,
            font_size=font_size,
            show_points=show_points,
            owner_id=owner,
            auto_start=auto_start,
            runtime_dir=str(tmp_path / "runtime"),
            terminal_pid=terminal_pid,
            terminal_created=process_creations[terminal_pid],
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
    different_args = args_for("terminal-b-chart-7", feed_one, terminal_pid=202)

    def same_owner_reinitialization() -> None:
        root = roots[0]
        root.visibility = "restored"
        result = spread.run_window(
            next_args,
            root_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate root created")),
            window_factory=FakeWindow,
            guard_factory=FakeGuard,
            user32=FakeUser32(),
            process_api=process_api,
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
            process_api=process_api,
        )
        assert independent == "started"

    first_root = lambda: FakeRoot(same_owner_reinitialization)
    assert spread.run_window(
        first_args,
        root_factory=first_root,
        window_factory=FakeWindow,
        guard_factory=FakeGuard,
        user32=FakeUser32(),
        process_api=process_api,
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
        process_api=process_api,
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
            process_api=process_api,
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
        process_api=process_api,
    ) == "started"
    assert not held_guards
    assert not process_api.open_handles

    cleanup_releases = []
    cleanup_root = FakeRoot()
    cleanup_guard = FakeGuard(tmp_path / "cleanup-owner.lock")
    assert cleanup_guard.acquire()
    cleanup = object.__new__(spread.SpreadPercentWindow)
    cleanup.root = cleanup_root
    cleanup._closed = False
    cleanup._refresh_after_id = "refresh-1"
    cleanup._owner_after_id = None
    cleanup.process_monitor = None
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
    assert '#property version   "1.13"' in mql
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


def test_spread_window_closes_only_when_verified_owning_terminal_exits(tmp_path: Path) -> None:
    import ctypes
    import json

    spread = _load_module()
    runtime_dir = tmp_path / "common" / "Files"
    feed_a = tmp_path / "directory-a" / "feed.json"
    feed_b = tmp_path / "directory-b" / "feed.json"
    feed_a.parent.mkdir()
    feed_b.parent.mkdir()
    feed_a.write_text(
        json.dumps(
            {
                "version": "1.10",
                "generated_at": "2026-10-11T09:00:00Z",
                "symbols": [
                    {
                        "symbol": "EURUSD",
                        "spread_percent": 0.012345,
                        "spread_points": 1.2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    creations = {
        101: 132_456_789_012_345_678,
        202: 142_456_789_012_345_679,
        303: 152_456_789_012_345_680,
    }

    class FakeProcessApi:
        def __init__(self) -> None:
            self.open_handles: dict[int, int] = {}
            self.closed: list[int] = []
            self.waits: dict[int, object] = {}
            self.fail_open: set[int] = set()
            self.fail_query: set[int] = set()
            self.next_handle = 0x1_0000_1000

        def open_process(self, pid: int) -> int:
            if pid in self.fail_open:
                raise spread.SpreadLifecycleError("OpenProcess test failure")
            handle = self.next_handle
            self.next_handle += 0x100
            self.open_handles[handle] = pid
            return handle

        def creation_time(self, handle: int) -> int:
            pid = self.open_handles[handle]
            if pid in self.fail_query:
                raise spread.SpreadLifecycleError("GetProcessTimes test failure")
            return creations[pid]

        def wait_zero(self, handle: int) -> int:
            pid = self.open_handles[handle]
            result = self.waits.get(pid, spread.WAIT_TIMEOUT)
            if isinstance(result, Exception):
                raise result
            return int(result)

        def close_handle(self, handle: int) -> None:
            assert handle in self.open_handles
            self.open_handles.pop(handle)
            self.closed.append(handle)

    process_api = FakeProcessApi()
    owner_a = spread.SpreadOwnerIdentity("terminal-a-chart-7", 101, creations[101])
    owner_b = spread.SpreadOwnerIdentity("terminal-b-chart-7", 202, creations[202])

    monitor_a = spread.TerminalProcessMonitor.open(owner_a, process_api)
    assert monitor_a.handle > 0xFFFFFFFF
    assert not monitor_a.exited()
    process_api.waits[101] = spread.WAIT_OBJECT_0
    assert monitor_a.exited()
    monitor_a.close()
    monitor_a.close()

    reused_pid = spread.SpreadOwnerIdentity("terminal-a-chart-7", 101, creations[101] + 1)
    closed_before = len(process_api.closed)
    try:
        spread.TerminalProcessMonitor.open(reused_pid, process_api)
    except spread.SpreadLifecycleError as exc:
        assert "creation time does not match" in str(exc)
    else:
        raise AssertionError("PID reuse was accepted")
    assert len(process_api.closed) == closed_before + 1

    process_api.fail_query.add(303)
    query_failure = spread.SpreadOwnerIdentity("terminal-c-chart-7", 303, creations[303])
    closed_before = len(process_api.closed)
    try:
        spread.TerminalProcessMonitor.open(query_failure, process_api)
    except spread.SpreadLifecycleError as exc:
        assert "GetProcessTimes test failure" in str(exc)
    else:
        raise AssertionError("failed creation-time query was accepted")
    assert len(process_api.closed) == closed_before + 1
    process_api.fail_open.add(303)
    try:
        spread.TerminalProcessMonitor.open(query_failure, process_api)
    except spread.SpreadLifecycleError as exc:
        assert "OpenProcess test failure" in str(exc)
    else:
        raise AssertionError("failed process open was accepted")
    process_api.fail_query.clear()
    process_api.fail_open.clear()

    class Status:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    class CallbackRoot:
        def __init__(self, visibility: str) -> None:
            self.visibility = visibility
            self.after_calls: list[tuple[int, object]] = []
            self.cancelled: list[object] = []
            self.destroyed = False
            self.activated = False

        def after(self, delay: int, callback):
            token = f"after-{len(self.after_calls) + 1}"
            self.after_calls.append((delay, callback))
            return token

        def after_cancel(self, token) -> None:
            self.cancelled.append(token)

        def withdraw(self) -> None:
            self.visibility = "withdrawn"

        def destroy(self) -> None:
            self.destroyed = True

    def bare_window(identity, visibility: str):
        root = CallbackRoot(visibility)
        window = object.__new__(spread.SpreadPercentWindow)
        window.root = root
        window._closed = False
        window._refresh_after_id = "slow-feed-refresh"
        window._owner_after_id = None
        window.process_monitor = spread.TerminalProcessMonitor.open(identity, process_api)
        window.owner_monitor_error = None
        window._feed_status_text = "Feed file missing; retrying slowly"
        window.status_var = Status()
        window.on_close = lambda: None
        return window, root

    process_api.waits[101] = spread.WAIT_TIMEOUT
    live_window, live_root = bare_window(owner_a, "restored")
    live_window._check_owner_process()
    assert not live_window._closed
    assert live_root.after_calls[-1][0] == spread.OWNER_PROCESS_CHECK_MS == 500
    assert live_window._refresh_after_id == "slow-feed-refresh"

    process_api.waits[101] = spread.SpreadLifecycleError("WaitForSingleObject test failure")
    live_window._check_owner_process()
    assert not live_window._closed
    assert "Owner verification failed" in live_window.status_var.value
    assert "Feed file missing" in live_window.status_var.value
    process_api.waits[101] = spread.WAIT_OBJECT_0
    live_window._check_owner_process()
    assert live_window._closed and live_root.destroyed
    assert not live_root.activated

    process_api.waits[101] = spread.WAIT_OBJECT_0
    minimized_window, minimized_root = bare_window(owner_a, "minimized")
    process_api.waits[202] = spread.WAIT_TIMEOUT
    other_window, other_root = bare_window(owner_b, "restored")
    minimized_window._check_owner_process()
    other_window._check_owner_process()
    assert minimized_window._closed and minimized_root.destroyed
    assert not other_window._closed and not other_root.destroyed
    other_window._close()
    other_window._close()

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

    def args_for(
        identity,
        feed: Path,
        *,
        refresh_ms: int = 5000,
        decimals: int = 5,
        show_points: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            file=str(feed),
            refresh_ms=refresh_ms,
            decimals=decimals,
            font_size=12,
            show_points=show_points,
            owner_id=identity.owner_id,
            auto_start=True,
            runtime_dir=str(runtime_dir),
            terminal_pid=identity.terminal_pid,
            terminal_created=identity.terminal_creation_time,
        )

    config_a = spread.SpreadWindowConfig.from_args(args_for(owner_a, feed_a))
    owner = spread.SpreadWindowOwnership.claim_or_handoff(
        runtime_dir, owner_a, config_a, guard_factory=FakeGuard
    )
    assert owner is not None
    config_b = spread.SpreadWindowConfig.from_args(
        args_for(owner_a, feed_b, refresh_ms=9000, decimals=3, show_points=True)
    )
    assert (
        spread.SpreadWindowOwnership.claim_or_handoff(
            runtime_dir, owner_a, config_b, guard_factory=FakeGuard
        )
        is None
    )
    assert owner.accept_handoff() == config_b
    assert len(held_guards) == 1
    independent = spread.SpreadWindowOwnership.claim_or_handoff(
        runtime_dir,
        owner_b,
        spread.SpreadWindowConfig.from_args(args_for(owner_b, feed_a)),
        guard_factory=FakeGuard,
    )
    assert independent is not None and len(held_guards) == 2
    independent.release()

    process_api.waits[101] = spread.WAIT_TIMEOUT
    handoff_args = args_for(owner_a, feed_b, refresh_ms=7000, decimals=2)
    roots_created = []
    assert spread.run_window(
        handoff_args,
        root_factory=lambda: roots_created.append(object()),
        window_factory=lambda *unused: (_ for _ in ()).throw(AssertionError("duplicate root")),
        guard_factory=FakeGuard,
        process_api=process_api,
    ) == "handoff"
    assert not roots_created
    assert all(pid != 101 for pid in process_api.open_handles.values())
    owner.release()

    startup_args = args_for(owner_a, feed_a)
    try:
        spread.run_window(
            startup_args,
            root_factory=lambda: CallbackRoot("normal"),
            window_factory=lambda *unused: (_ for _ in ()).throw(RuntimeError("construction failed")),
            guard_factory=FakeGuard,
            process_api=process_api,
        )
    except RuntimeError as exc:
        assert str(exc) == "construction failed"
    else:
        raise AssertionError("startup failure was not propagated")
    assert not held_guards
    retry_owner = spread.SpreadWindowOwnership.claim_or_handoff(
        runtime_dir, owner_a, config_a, guard_factory=FakeGuard
    )
    assert retry_owner is not None
    retry_owner.release()

    manual = spread.parse_args(
        [
            "--file",
            str(feed_a),
            "--refresh-ms",
            "1500",
            "--decimals",
            "4",
            "--font-size",
            "13",
            "--show-points",
        ]
    )
    assert manual.owner_id == "" and manual.terminal_pid == 0
    assert manual.runtime_dir == "" and not manual.auto_start
    assert (manual.refresh_ms, manual.decimals, manual.font_size, manual.show_points) == (
        1500,
        4,
        13,
        True,
    )
    parsed_rows = spread.SpreadPercentWindow._parse_rows(
        object.__new__(spread.SpreadPercentWindow),
        json.loads(feed_a.read_text(encoding="utf-8")),
    )
    formatter = object.__new__(spread.SpreadPercentWindow)
    formatter.decimals = 3
    formatter.show_points = True
    assert spread.SpreadPercentWindow._row_values(formatter, parsed_rows[0]) == (
        "EURUSD",
        "0.012%",
        "1.2",
    )

    class NativeFunction:
        def __init__(self, result) -> None:
            self.result = result
            self.argtypes = None
            self.restype = None
            self.calls = []

        def __call__(self, *args):
            self.calls.append(args)
            return self.result

    class NativeLibrary:
        def __init__(self, parent) -> None:
            self.GetParent = NativeFunction(parent)
            self.ShowWindow = NativeFunction(1)

    wide_hwnd = 0x1_0000_4321

    class NativeRoot:
        def update_idletasks(self) -> None:
            pass

        def winfo_id(self) -> int:
            return wide_hwnd

    null_parent_library = NativeLibrary(0)
    native_api = spread.WindowsUser32Api(null_parent_library)
    assert ctypes.sizeof(native_api._user32.GetParent.argtypes[0]) == ctypes.sizeof(
        ctypes.c_void_p
    )
    spread.show_minimized_no_activate(NativeRoot(), user32=native_api)
    assert null_parent_library.GetParent.calls == [(wide_hwnd,)]
    assert null_parent_library.ShowWindow.calls == [(wide_hwnd, 7)]
    parent_hwnd = 0x2_0000_9876
    parent_library = NativeLibrary(parent_hwnd)
    spread.show_minimized_no_activate(
        NativeRoot(), user32=spread.WindowsUser32Api(parent_library)
    )
    assert parent_library.ShowWindow.calls == [(parent_hwnd, 7)]

    mql = MQL_SOURCE.read_text(encoding="utf-8")
    assert '#property version   "1.13"' in mql
    assert '\\"version\\": \\"1.10\\"' in mql
    assert "uint GetCurrentProcessId();" in mql
    assert "bool GetProcessTimes(long process_handle" in mql
    assert '" --runtime-dir " + QuoteArg(runtime_dir)' in mql
    assert '" --terminal-pid " + (string)terminal_pid' in mql
    assert '" --terminal-created " + (string)terminal_creation_time' in mql
    assert "SW_SHOWMINNOACTIVE = 7" in mql
    launch = mql[mql.index("bool LaunchWindow()") : mql.index("string JsonEscape")]
    assert launch.index("MQLInfoInteger(MQL_DLLS_ALLOWED)") < launch.index(
        "TerminalProcessIdentity"
    )
    assert launch.index("MQLInfoInteger(MQL_DLLS_ALLOWED)") < launch.index(
        "ConfiguredLaunchFileExists"
    )
    assert "DirectoryName(script), SW_SHOWMINNOACTIVE" in launch
    assert not process_api.open_handles
