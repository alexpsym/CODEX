import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"
WINDOW = ROOT / "mt5-clone" / "trader_control_window.py"


def _source() -> str:
    return TRADER.read_text(encoding="utf-8")


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _window_module():
    spec = importlib.util.spec_from_file_location("trader_control_window_under_test", WINDOW)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_auto_start_preserves_focus_and_reinitialization_keeps_one_current_window(
    tmp_path: Path,
) -> None:
    window = _window_module()
    owner_id = "OWNER-A"
    original = window.InstanceIdentity(
        "INSTANCE-EURUSD", 123456, "Pepperstone-Demo", 9988,
        "EURUSD.a", 91001, "2.46", owner_id,
    )
    changed = window.InstanceIdentity(
        "INSTANCE-GBPUSD", 123456, "Pepperstone-Demo", 9988,
        "GBPUSD.a", 92002, "2.46", owner_id,
    )
    version_changed = window.InstanceIdentity(
        "INSTANCE-GBPUSD", 123456, "Pepperstone-Demo", 9988,
        "GBPUSD.a", 92002, "2.47", owner_id,
    )
    other_owner = window.InstanceIdentity(
        "INSTANCE-OTHER", 123456, "Pepperstone-Demo", 7788,
        "AUDUSD.a", 93003, "2.46", "OWNER-B",
    )

    class FakeRoot:
        def __init__(self) -> None:
            self.events = []
            self.state = "normal"

        def withdraw(self) -> None:
            self.events.append("withdraw")
            self.state = "withdrawn"

        def update_idletasks(self) -> None:
            self.events.append("update_idletasks")

        def winfo_id(self) -> int:
            self.events.append("winfo_id")
            return 101

        def iconify(self) -> None:
            self.events.append("iconify")
            self.state = "iconic"

        def deiconify(self) -> None:
            self.events.append("deiconify")
            self.state = "normal"

    class FakeUser32:
        def __init__(self) -> None:
            self.calls = []

        def GetParent(self, hwnd: int) -> int:
            self.calls.append(("GetParent", hwnd))
            return 202

        def ShowWindow(self, hwnd: int, show_mode: int) -> int:
            self.calls.append(("ShowWindow", hwnd, show_mode))
            return 1

    root = FakeRoot()
    user32 = FakeUser32()
    root.withdraw()
    window.show_minimized_no_activate(root, user32=user32)
    assert root.events == ["withdraw", "update_idletasks", "winfo_id"]
    assert user32.calls == [("GetParent", 101), ("ShowWindow", 202, window.SW_SHOWMINNOACTIVE)]
    assert not any(event.startswith("focus") for event in root.events)
    root.state = "iconic"
    root.deiconify()
    assert root.state == "normal"

    ownership = window.WindowOwnership.claim_or_handoff(tmp_path, original)
    assert ownership is not None
    other = None
    try:
        # The second launch cannot create another same-chart window; it leaves
        # a complete current-identity request for the existing owner instead.
        assert window.WindowOwnership.claim_or_handoff(tmp_path, changed) is None

        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def set(self, value: str) -> None:
                self.value = value

        class Button:
            def __init__(self) -> None:
                self.states = []

            def state(self, value) -> None:
                self.states.append(tuple(value))

        control = window.TraderControlWindow.__new__(window.TraderControlWindow)
        control.protocol = window.TraderControlProtocol(tmp_path, original)
        control.ownership = ownership
        control.session_command_ids = {"old-window-command"}
        control.last_seen_result_id = "old-window-command"
        control.identity_var = Value(window.TraderControlWindow._identity_text(original))
        control.connection_var = Value("Ready")
        control.result_var = Value("Old result")
        control.buttons = [Button()]

        assert window.TraderControlWindow._accept_handoff(control) is True
        assert control.protocol.identity == changed
        assert ownership.identity == changed
        assert control.session_command_ids == set()
        assert control.last_seen_result_id is None
        assert "GBPUSD.a" in control.identity_var.value
        assert "Connecting to the current EA identity" in control.connection_var.value
        assert control.result_var.value == "Last command: none"
        assert control.buttons[0].states == [("disabled",)]
        assert root.state == "normal"  # handoff never changes the user's visibility choice

        # Until a full fresh snapshot for the new identity exists, commands
        # remain unavailable. A matching snapshot enables only explicit clicks.
        assert control.protocol.state().buttons_enabled is False
        now = int(time.time())
        status = {
            **window._identity_payload(changed),
            "updated_at": now,
            "fresh_for_seconds": 5,
            "connected": True,
            "control_ready": True,
            "orders_enabled": True,
            "reason": "ready",
        }
        control.protocol.status_path.write_text(json.dumps(status), encoding="ascii")
        assert control.protocol.state(now=now).buttons_enabled is True

        # An EA-version-only reinitialization retains the same instance guard
        # but still forces a complete protocol/status rebind.
        assert window.WindowOwnership.claim_or_handoff(tmp_path, version_changed) is None
        assert window.TraderControlWindow._accept_handoff(control) is True
        assert control.protocol.identity == version_changed
        assert control.protocol.state().buttons_enabled is False

        # Repeated launch attempts for the already current identity are no-ops.
        assert window.WindowOwnership.claim_or_handoff(tmp_path, version_changed) is None
        assert window.TraderControlWindow._accept_handoff(control) is False
        assert control.protocol.identity == version_changed

        # A different terminal/account/chart owner has an independent window.
        other = window.WindowOwnership.claim_or_handoff(tmp_path, other_owner)
        assert other is not None
        assert other.identity == other_owner
        assert not list(tmp_path.glob("*.command.json"))

        trader = _source()
        launch = _function(trader, "bool LaunchDesktopTraderControls")
        owner = _function(trader, "string TraderControlOwnerId")
        executable = _function(trader, "string TraderControlLaunchExecutable")
        assert '#property version   "2.46"' in trader and 'EA_VERSION = "2.46"' in trader
        assert 'const int  TRADER_CONTROL_SW_SHOWMINNOACTIVE = 7;' in trader
        assert '" --owner-id " + TraderControlQuoteArg(g_traderControlOwnerId)' in launch
        assert "TRADER_CONTROL_SW_SHOWMINNOACTIVE" in launch
        assert "TraderControlLaunchExecutable(configuredPython)" in launch
        assert '"pythonw.exe"' in executable and "TraderControlConfiguredFileExists(windowed)" in executable
        assert "TERMINAL_DATA_PATH" in owner and "ChartID()" in owner
        assert "_Symbol" not in owner and "MagicNumber" not in owner
        python_source = WINDOW.read_text(encoding="utf-8")
        main_source = python_source[python_source.index("def main() -> None:"):]
        assert main_source.index("root.withdraw()") < main_source.index("TraderControlWindow(")
        assert main_source.index("TraderControlWindow(") < main_source.index("show_minimized_no_activate(root)")
    finally:
        if other is not None:
            other.release()
        ownership.release()


def test_controls_close_only_when_verified_owning_terminal_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_module()

    class FakeProcessApi:
        def __init__(self) -> None:
            self.creation = {101: 13_400_000_000_000_101, 202: 13_400_000_000_000_202}
            self.alive = {101: True, 202: True}
            self.handles = {}
            self.closed = []
            self.next_handle = 1
            self.fail_open = False
            self.fail_creation = False
            self.fail_wait = set()

        def open_process(self, pid: int) -> int:
            if self.fail_open or pid not in self.creation:
                raise window.ProtocolError("OpenProcess diagnostic")
            handle = self.next_handle
            self.next_handle += 1
            self.handles[handle] = pid
            return handle

        def creation_time(self, handle: int) -> int:
            if self.fail_creation:
                raise window.ProtocolError("GetProcessTimes diagnostic")
            return self.creation[self.handles[handle]]

        def wait_zero(self, handle: int) -> int:
            pid = self.handles[handle]
            if pid in self.fail_wait:
                raise window.ProtocolError("WaitForSingleObject diagnostic")
            return window.WAIT_TIMEOUT if self.alive[pid] else window.WAIT_OBJECT_0

        def close_handle(self, handle: int) -> None:
            assert handle not in self.closed
            self.closed.append(handle)

    class Root:
        def __init__(self, state: str) -> None:
            self.state = state
            self.callbacks = {}
            self.cancelled = []
            self.destroyed = 0
            self.next_callback = 1
            self.activation_events = []

        def after(self, delay: int, callback: object) -> str:
            callback_id = f"after-{self.next_callback}"
            self.next_callback += 1
            self.callbacks[callback_id] = (delay, callback)
            return callback_id

        def after_cancel(self, callback_id: str) -> None:
            self.cancelled.append(callback_id)
            self.callbacks.pop(callback_id, None)

        def run(self, callback_id: str) -> None:
            _delay, callback = self.callbacks.pop(callback_id)
            callback()

        def destroy(self) -> None:
            self.destroyed += 1

    class Value:
        def __init__(self, value: str) -> None:
            self.value = value

        def set(self, value: str) -> None:
            self.value = value

    class Button:
        def __init__(self) -> None:
            self.states = []

        def state(self, value) -> None:
            self.states.append(tuple(value))

    def identity(
        owner: str,
        instance: str,
        pid: int,
        creation: int,
        symbol: str,
        magic: int,
        chart: int,
    ) -> object:
        return window.InstanceIdentity(
            instance,
            123456,
            "Pepperstone-Demo",
            chart,
            symbol,
            magic,
            "2.46",
            owner,
            pid,
            creation,
        )

    def panel_for(current_identity, state: str):
        lifetime = window._terminal_lifetime_id(current_identity)
        ownership = window.WindowOwnership.claim_or_handoff(
            tmp_path,
            current_identity,
            lifetime_id=lifetime,
        )
        assert ownership is not None
        monitor = window.TerminalProcessMonitor.open(
            current_identity.terminal_pid,
            current_identity.terminal_creation_time,
            process_api,
        )
        root = Root(state)
        panel = window.TraderControlWindow.__new__(window.TraderControlWindow)
        panel.root = root
        panel.protocol = window.TraderControlProtocol(tmp_path, current_identity)
        panel.refresh_ms = 5_000
        panel.on_close = ownership.release
        panel.ownership = ownership
        panel.process_monitor = monitor
        panel.owner_monitor_error = None
        panel._closed = False
        panel._refresh_after_id = None
        panel._owner_after_id = None
        panel.last_seen_result_id = None
        panel.session_command_ids = set()
        panel.buttons = [Button()]
        panel.identity_var = Value(window.TraderControlWindow._identity_text(current_identity))
        panel.connection_var = Value("Connecting...")
        panel.result_var = Value("Last command: none")
        return panel, root, ownership

    process_api = FakeProcessApi()
    first = identity(
        "OWNER-A", "INSTANCE-EURUSD", 101, process_api.creation[101],
        "EURUSD.a", 91001, 9988,
    )
    changed = identity(
        "OWNER-A", "INSTANCE-GBPUSD", 101, process_api.creation[101],
        "GBPUSD.a", 92002, 9988,
    )
    second = identity(
        "OWNER-B", "INSTANCE-AUDUSD", 202, process_api.creation[202],
        "AUDUSD.a", 93003, 7788,
    )

    first_panel, first_root, first_ownership = panel_for(first, "normal")
    second_panel, second_root, _second_ownership = panel_for(second, "iconic")

    # A stale/disconnected EA snapshot and a same-process identity handoff do
    # not mean the terminal exited and never alter the panel's visibility.
    stale = {
        **window._identity_payload(first),
        "updated_at": 1,
        "fresh_for_seconds": 5,
        "connected": False,
        "control_ready": False,
        "orders_enabled": False,
        "reason": "EA instance disconnected.",
    }
    first_panel.protocol.status_path.write_text(json.dumps(stale), encoding="ascii")
    first_panel._refresh()
    assert not first_panel._closed and first_root.state == "normal"
    assert window.WindowOwnership.claim_or_handoff(
        tmp_path,
        changed,
        lifetime_id=window._terminal_lifetime_id(changed),
    ) is None
    assert first_panel._accept_handoff() is True
    assert first_panel.protocol.identity == changed
    assert first_root.state == "normal"

    first_panel._check_owner_process()
    second_panel._check_owner_process()
    assert first_root.callbacks[first_panel._owner_after_id][0] == window.OWNER_PROCESS_CHECK_MS
    assert window.OWNER_PROCESS_CHECK_MS <= 1_000
    assert not first_panel._closed and not second_panel._closed

    # Confirmed exit closes the restored first panel only. The minimized panel
    # from another verified terminal remains untouched until its own exit.
    process_api.alive[101] = False
    first_root.run(first_panel._owner_after_id)
    assert first_panel._closed and first_root.destroyed == 1
    assert first_root.state == "normal" and first_root.activation_events == []
    assert not second_panel._closed and second_root.destroyed == 0
    process_api.alive[202] = False
    second_root.run(second_panel._owner_after_id)
    assert second_panel._closed and second_root.destroyed == 1
    assert second_root.state == "iconic" and second_root.activation_events == []

    # PID reuse and process-query failures fail closed; they cannot bind to or
    # masquerade as confirmed termination of an unrelated process.
    with pytest.raises(window.ProtocolError, match="PID may have been reused"):
        window.TerminalProcessMonitor.open(101, process_api.creation[101] + 1, process_api)
    process_api.fail_creation = True
    with pytest.raises(window.ProtocolError, match="GetProcessTimes diagnostic"):
        window.TerminalProcessMonitor.open(101, process_api.creation[101], process_api)
    process_api.fail_creation = False
    process_api.alive[101] = True
    error_panel, error_root, _error_ownership = panel_for(first, "normal")
    process_api.fail_wait.add(101)
    error_panel._check_owner_process()
    assert not error_panel._closed and error_root.destroyed == 0
    assert "WaitForSingleObject diagnostic" in error_panel.connection_var.value
    assert error_panel.buttons[0].states[-1] == ("disabled",)
    process_api.fail_wait.clear()

    # User closure uses the same idempotent cleanup without terminating MT5;
    # the released owner/instance guards are immediately acquirable again.
    error_panel._close()
    error_panel._close()
    assert process_api.alive[101] is True
    assert error_root.destroyed == 1
    reacquired = window.WindowOwnership.claim_or_handoff(
        tmp_path,
        first,
        lifetime_id=window._terminal_lifetime_id(first),
    )
    assert reacquired is not None
    reacquired.release()

    # A Tk startup failure runs main's real finally path: the verified process
    # handle and acquired guards are released, allowing an ordinary next start.
    startup = identity(
        "OWNER-C", "INSTANCE-STARTUP", 101, process_api.creation[101],
        "NZDUSD.a", 94004, 6688,
    )
    args = window.argparse.Namespace(
        common_dir=str(tmp_path),
        instance_id=startup.instance_id,
        owner_id=startup.owner_id,
        account_login=startup.account_login,
        account_server=startup.account_server,
        chart_id=startup.chart_id,
        symbol=startup.symbol,
        magic=startup.magic_number,
        ea_version=startup.ea_version,
        terminal_pid=startup.terminal_pid,
        terminal_created=startup.terminal_creation_time,
        refresh_ms=250,
    )
    monkeypatch.setattr(window, "parse_args", lambda: args)
    monkeypatch.setattr(window, "WindowsProcessApi", lambda: process_api)
    monkeypatch.setattr(window.tk, "Tk", lambda: (_ for _ in ()).throw(RuntimeError("startup failed")))
    with pytest.raises(RuntimeError, match="startup failed"):
        window.main()
    startup_reacquired = window.WindowOwnership.claim_or_handoff(
        tmp_path,
        startup,
        lifetime_id=window._terminal_lifetime_id(startup),
    )
    assert startup_reacquired is not None
    startup_reacquired.release()

    assert not list(tmp_path.glob("*.command.json"))
    assert len(process_api.closed) == len(set(process_api.closed))

    trader = _source()
    process_identity = _function(trader, "bool TraderControlTerminalProcessIdentity")
    launch = _function(trader, "bool LaunchDesktopTraderControls")
    status = _function(trader, "bool WriteDesktopTraderStatus")
    assert '#property version   "2.46"' in trader and 'EA_VERSION = "2.46"' in trader
    assert "GetCurrentProcessId()" in process_identity
    assert "GetCurrentProcess()" in process_identity
    assert "GetProcessTimes(processHandle" in process_identity
    assert '" --terminal-pid " + (string)g_traderControlTerminalPid' in launch
    assert '" --terminal-created " + (string)g_traderControlTerminalCreationTime' in launch
    assert '\\"terminal_pid\\":' in status
    assert '\\"terminal_creation_time\\":' in status


def test_window_writes_one_atomic_scoped_command_and_blocks_duplicate_pending_clicks(tmp_path: Path) -> None:
    window = _window_module()
    identity = window.InstanceIdentity("A1B2C3D4", 123456, "Pepperstone-Demo", 9988, "EURUSD.a", 91001, "2.40")
    protocol = window.TraderControlProtocol(tmp_path, identity)
    now = int(time.time())
    status = {
        "protocol_version": 1,
        "instance_id": identity.instance_id,
        "account_login": identity.account_login,
        "account_server": identity.account_server,
        "chart_id": identity.chart_id,
        "symbol": identity.symbol,
        "magic_number": identity.magic_number,
        "ea_version": identity.ea_version,
        "updated_at": now,
        "fresh_for_seconds": 5,
        "connected": True,
        "control_ready": True,
        "orders_enabled": True,
        "reason": "ready",
    }
    protocol.status_path.write_text(json.dumps(status), encoding="ascii")

    command = protocol.issue_command("trendline", now=now)
    persisted = json.loads(protocol.command_path.read_text(encoding="ascii"))
    assert persisted == command
    assert set(command) == {"protocol_version", "instance_id", "command_id", "action", "created_at"}
    assert command["protocol_version"] == 1
    assert command["instance_id"] == identity.instance_id
    assert command["action"] == "trendline"
    assert command["created_at"] == now
    assert uuid.UUID(command["command_id"]).version == 4
    assert not list(tmp_path.glob(f".{protocol.command_path.name}.*.tmp"))
    assert not ({"price", "side", "risk", "token", "credentials"} & set(command))

    with pytest.raises(window.ProtocolError, match="pending"):
        protocol.issue_command("trendline", now=now + 1)


def test_window_only_displays_results_issued_in_its_current_session() -> None:
    window = _window_module()
    identity = window.InstanceIdentity("A1B2C3D4", 123456, "Pepperstone-Demo", 9988, "EURUSD.a", 91001, "2.42")
    now = int(time.time())

    class Value:
        def __init__(self, value: str) -> None:
            self.value = value

        def set(self, value: str) -> None:
            self.value = value

    class Root:
        def after(self, _delay: int, _callback: object) -> None:
            pass

    class Button:
        def state(self, _state: list[str]) -> None:
            pass

    old_result = {
        "instance_id": identity.instance_id,
        "command_id": "prior-window-command",
        "action": "market",
        "outcome": "blocked",
        "reason": "Desktop command is stale or has an invalid future timestamp.",
    }
    current_command = {
        "command_id": "current-window-command",
        "action": "market",
    }
    status = {"orders_enabled": True, "updated_at": now}
    control_state = window.ControlState(True, True, "Ready for one explicit command.", None, status, old_result)

    class Protocol:
        def __init__(self) -> None:
            self.identity = identity

        def state(self) -> object:
            return control_state

        def issue_command(self, action: str) -> dict[str, str]:
            assert action == "market"
            return current_command

    control = object.__new__(window.TraderControlWindow)
    control.root = Root()
    control.protocol = Protocol()
    control.refresh_ms = 250
    control.last_seen_result_id = None
    control.session_command_ids = set()
    control.buttons = [Button()]
    control.connection_var = Value("")
    control.result_var = Value("Last command: none")

    control._refresh()
    assert control.result_var.value == "Last command: none"

    control._click("market")
    assert control.result_var.value == "MARKET ORDER | pending | current-window-command"
    assert control.session_command_ids == {"current-window-command"}

    control_state = window.ControlState(
        True,
        True,
        "Ready for one explicit command.",
        None,
        status,
        {**old_result, **current_command, "outcome": "accepted", "reason": "Submitted."},
    )
    control._refresh()
    assert control.result_var.value == "MARKET ORDER | accepted | Submitted."

    control_state = window.ControlState(True, True, "Ready for one explicit command.", None, status, old_result)
    control._refresh()
    assert control.result_var.value == "MARKET ORDER | accepted | Submitted."


def test_status_diagnostics_are_precise_fail_closed_and_snapshot_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _window_module()
    identity = window.InstanceIdentity("A1B2C3D4", 123456, "Pepperstone-Demo", 9988, "EURUSD.a", 91001, "2.40")
    protocol = window.TraderControlProtocol(tmp_path, identity)
    now = 1_700_000_000
    protocol.started_at = now

    connecting = protocol.state(now=now)
    assert not connecting.buttons_enabled
    assert "Connecting" in connecting.reason and str(protocol.status_path) in connecting.reason

    missing = protocol.state(now=now + window.STARTUP_GRACE_SECONDS + 1)
    assert not missing.buttons_enabled
    assert "missing" in missing.reason and str(protocol.status_path) in missing.reason

    protocol.status_path.write_text("{", encoding="ascii")
    malformed = protocol.state(now=now + 10)
    assert not malformed.buttons_enabled and "malformed JSON" in malformed.reason

    protocol.status_path.write_bytes(b"\xff")
    encoding = protocol.state(now=now + 10)
    assert not encoding.buttons_enabled and "unsupported encoding" in encoding.reason

    protocol.status_path.write_text("[]", encoding="ascii")
    non_object = protocol.state(now=now + 10)
    assert not non_object.buttons_enabled and "not a JSON object" in non_object.reason

    original_read_bytes = window.Path.read_bytes

    def unreadable_status(path: Path) -> bytes:
        if path == protocol.status_path:
            raise PermissionError("test-only access denial")
        return original_read_bytes(path)

    monkeypatch.setattr(window.Path, "read_bytes", unreadable_status)
    unreadable = protocol.state(now=now + 10)
    assert not unreadable.buttons_enabled and "unreadable" in unreadable.reason
    monkeypatch.setattr(window.Path, "read_bytes", original_read_bytes)

    status = {
        "protocol_version": 1,
        "instance_id": identity.instance_id,
        "account_login": identity.account_login,
        "account_server": identity.account_server,
        "chart_id": identity.chart_id,
        "symbol": identity.symbol,
        "magic_number": identity.magic_number,
        "ea_version": identity.ea_version,
        "updated_at": now,
        "fresh_for_seconds": 5,
        "connected": True,
        "control_ready": True,
        "orders_enabled": True,
        "reason": "ready",
    }
    protocol.status_path.write_text(json.dumps(status), encoding="ascii")
    stale = protocol.state(now=now + 6)
    assert not stale.buttons_enabled and "stale" in stale.reason

    status["updated_at"] = now + 6
    protocol.status_path.write_text(json.dumps(status), encoding="ascii")
    ready = protocol.state(now=now + 6)
    assert ready.buttons_enabled and ready.reason == "Ready for one explicit command."

    trader = _source()
    publish = _function(trader, "bool PublishVerifiedCommonSnapshot")
    status_writer = _function(trader, "bool WriteDesktopTraderStatus")
    result_writer = _function(trader, "bool WriteDesktopTraderResult")
    assert "TraderControlCommonFilesPath()" in trader
    assert "PublishVerifiedCommonSnapshot(TraderControlStatusFile(), payload, why)" in trader
    assert "MoveFileExW" not in trader
    assert "WriteVerifiedCommonText(temporary, contents, why)" in publish
    assert "FileMove(temporary, FILE_COMMON, fileName, FILE_COMMON | FILE_REWRITE)" in publish
    assert "VerifyCommonText(fileName, contents, why)" in publish
    assert "FileDelete(temporary, FILE_COMMON)" in publish
    assert "WriteVerifiedCommonText(fileName, contents, why)" not in publish
    assert "MQL_DLLS_ALLOWED" not in publish
    assert 'IntegerToString((long)now)' in status_writer
    assert '(string)now' not in status_writer
    assert 'IntegerToString((long)TimeGMT())' in result_writer
    assert '(string)TimeGMT()' not in result_writer
    assert isinstance(json.loads(json.dumps(status))["updated_at"], int)


def test_ea_validates_scope_freshness_consumes_before_dispatch_and_reports_structured_result() -> None:
    trader = _source()
    read = _function(trader, "bool ReadDesktopTraderCommand")
    consume = _function(trader, "bool ConsumeDesktopTraderCommand")
    handle = _function(trader, "void HandleDesktopTraderCommand")
    result = _function(trader, "bool WriteDesktopTraderResult")
    status = _function(trader, "bool WriteDesktopTraderStatus")

    assert "payload != canonical" in read
    assert "command.protocolVersion != TRADER_CONTROL_PROTOCOL_VERSION" in read
    assert "command.instanceId != g_traderControlInstanceId" in read
    assert "IsDesktopCommandIdValid(command.commandId)" in read
    assert "IsDesktopActionAllowed(command.action)" in read
    assert "TRADER_CONTROL_COMMAND_MAX_AGE_SECONDS" in read
    assert "command.createdAt > now + 5" in read

    assert "TraderControlConsumedFile(command.commandId)" in consume
    assert "FileIsExist(marker, FILE_COMMON)" in consume
    assert "alreadyConsumed = true" in consume
    assert "WriteVerifiedCommonText(marker, payload, why)" in consume
    assert handle.index("ConsumeDesktopTraderCommand(command") < handle.index('command.action == "market"')
    assert "Command replay rejected" in handle
    assert handle.count("ExecuteDesktopMarket(") == 1
    assert handle.count("ExecuteDesktopLimit(") == 1
    assert handle.count("ExecuteDesktopTrendline(") == 1
    assert handle.count("ExecuteDesktopEmaBounce(") == 1

    for field in ("command_id", "action", "outcome", "reason", "retcode", "ticket", "instance_id"):
        assert f'\\"{field}\\"' in result
    for field in ("account_login", "chart_id", "symbol", "magic_number", "ea_version", "orders_enabled", "updated_at"):
        assert f'\\"{field}\\"' in status


def test_auto_fit_risk_buffer_preserves_risk_and_submission_gates() -> None:
    trader = _source()
    risk = _function(trader, "bool ComputeVolumeFromRisk")
    market = _function(trader, "void ExecuteDesktopMarket")
    fixed_market = _function(trader, "bool ExecuteStandardMarketOnce")

    assert 'input bool   AutoFitRiskSlippageBuffer = false;' in trader
    assert "int &outBufferPoints" in risk
    assert "if(AutoFitRiskSlippageBuffer && riskWorst > riskMax)" in risk
    assert "int low = minimumBuffer" in risk and "int high = maximumBuffer" in risk
    assert "configuredPreferredBuffer" in risk and "conservativeDistanceCap" in risk
    assert risk.index("minimumBuffer = AutoFitRiskSlippageBuffer") < risk.index("conservativeDistanceCap")
    assert "if(maximumBuffer < minimumBuffer)" in risk
    assert "while(low <= high)" in risk
    assert "chosenBuffer = mid" in risk
    assert "No automatic risk buffer at or above SlippagePoints fits RiskAUD_Max." in risk
    assert "if(riskTotal < riskMin)" in risk and "if(riskTotal > riskMax)" in risk
    assert "if(riskWorst > riskMax)" in risk
    assert "CalcRiskFor1Lot(stopPoints + MathMax(0, bufferPoints)" in trader
    assert "chosenRiskBuffer" in market and "Automatic risk buffer selected" in market
    assert market.index("ComputeVolumeFromRisk") < market.index("trade.Buy(")
    assert fixed_market.index("ComputeVolumeFromRisk") < fixed_market.index("ConsumeStandardMarketToken") < fixed_market.index("trade.Buy(")
    assert '#property version   "2.46"' in trader and 'EA_VERSION = "2.46"' in trader


def test_four_actions_reuse_current_inputs_and_one_attempt_trading_protections() -> None:
    trader = _source()
    inputs = trader.split('input group "Desktop Trader Controls"', 1)[1].split('input group "Risk', 1)[0]
    launch = _function(trader, "bool LaunchDesktopTraderControls")
    refresh_button = _function(trader, "void RefreshStandardMarketExecuteButton")
    market = _function(trader, "void ExecuteDesktopMarket")
    pending = _function(trader, "void ExecuteDesktopPendingAtEntry")
    pending_core = _function(
        trader,
        "bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,\n"
        "                                       const double rawEntry,\n"
        "                                       const bool allowReplace,\n"
        "                                       const string orderComment,\n"
        "                                       string &why)\n{",
    )
    limit = _function(trader, "void ExecuteDesktopLimit")
    resolve = _function(trader, "bool ResolveDesktopTrendline")
    trendline = _function(trader, "void ExecuteDesktopTrendline")
    ema = _function(trader, "void ExecuteDesktopEmaBounce")
    init = _function(trader, "int OnInit")
    tick = _function(trader, "void OnTick")
    timer = _function(trader, "void OnTimer")

    assert "UseDesktopTraderControls       = true" in inputs
    assert "LaunchDesktopTraderWindow      = true" in inputs
    assert "trader_control_window.py" in inputs
    assert "MQLInfoInteger(MQL_DLLS_ALLOWED)" in launch
    assert "ShellExecuteW" in launch
    assert "if(UseDesktopTraderControls) return;" in refresh_button

    assert "StandardMarketSide == STD_MARKET_BUY" in market
    assert "SymbolInfoTick(_Symbol, liveTick)" in market
    assert "isBuy ? liveTick.ask : liveTick.bid" in market
    for protection in (
        "DesktopOneTradeBlock",
        "ValidateTradingReadiness",
        "BuildSLFromDistance",
        "ComputeVolumeFromRisk",
        "ValidateVolumeForBroker",
        "ComputeAutoTP_NetRR",
        "ValidateMarketStopsAtLiveQuote",
    ):
        assert protection in market
    assert market.count("trade.Buy(") == 1 and market.count("trade.Sell(") == 1

    assert "DesktopOneTradeBlock" in pending
    assert pending.count("PlaceOrReplacePendingLimitAtEntry(") == 1
    assert "g_lastPendingBrokerAttempted" in pending
    assert "No retry will occur" in pending
    for protection in (
        "ValidateTradingReadiness",
        "IsLimitPriceValid",
        "BuildSLFromDistance",
        "ComputeVolumeFromRisk",
        "ValidateVolumeForBroker",
        "ComputeAutoTP_NetRR",
        "BuildTPManualFromDistance",
    ):
        assert protection in pending_core
    assert pending_core.count("trade.BuyLimit(") == 1 and pending_core.count("trade.SellLimit(") == 1
    assert "IsTradePlacementAccepted(retcode) && orderTicket > 0" in pending_core
    assert "FindMatchingPendingLimit" in pending_core
    assert "StandardLimitSide == STD_BUY_LIMIT" in limit
    assert "StandardLimitEntryPrice" in limit

    assert resolve.index("selectedCount == 1") < resolve.index("TrendlineObjectName") < resolve.index("validCount == 1")
    assert "TrendlineHighestHandled(record) + 1" in trendline
    assert "PersistTrendlineLifecycleRecord(record, armed, why)" in trendline
    assert trendline.index("PersistTrendlineLifecycleRecord(record, armed, why)") < trendline.index(
        "PlacePendingTrendlineGeneration(generation)"
    )
    assert trendline.count("PlacePendingTrendlineGeneration(") == 1
    assert "record.working" in trendline

    assert "SelectEmaBounceReference(fast0, slow0, 0.0)" in ema
    assert "fast0 > slow0" in ema
    assert "close1 > trend1" in ema
    assert "SelectEmaBounceReference(0.0, 0.0, trend0)" in ema
    assert "ExecuteDesktopPendingAtEntry" in ema
    assert "trade.Buy(" not in ema and "trade.Sell(" not in ema

    assert init.index("if(UseDesktopTraderControls)") < init.index("MaintainTrendlineLifecycle(\"OnInit\")")
    assert "return INIT_SUCCEEDED" in init.split("if(UseDesktopTraderControls)", 1)[1].split(
        "// A valid named trendline", 1
    )[0]
    assert tick.index("if(UseDesktopTraderControls)") < tick.index("if(!OrdersEnabled)")
    assert timer.index("if(UseDesktopTraderControls)") < timer.index("if(Strategy == STRAT_STANDARD_LIMIT)")
    assert "HandleDesktopTraderCommand()" in timer
    assert '#property version   "2.46"' in trader and 'EA_VERSION = "2.46"' in trader


def test_price_distance_mode_reuses_shared_builders_and_fails_before_market_token() -> None:
    trader = _source()
    sl_builder = _function(trader, "bool BuildSLFromDistance")
    tp_builder = _function(trader, "bool BuildTPManualFromDistance")
    validate = _function(trader, "bool ValidatePriceDistance")
    symbol_match = _function(trader, "bool DistanceSymbolMatchesChart")
    market = _function(trader, "bool ExecuteStandardMarketOnce")
    init = _function(trader, "int OnInit")
    tick = _function(trader, "void OnTick")
    timer = _function(trader, "void OnTimer")
    read_command = _function(trader, "bool ReadDesktopTraderCommand")
    consume_command = _function(trader, "bool ConsumeDesktopTraderCommand")
    command_handler = _function(trader, "void HandleDesktopTraderCommand")
    control_identity = _function(trader, "string TraderControlInstanceId")
    desktop_maintenance = _function(trader, "void MaintainDesktopTrendlineLifecycle")
    load_desktop_trendline = _function(trader, "void LoadDesktopActiveTrendline")
    standard_limit_maintenance = _function(trader, "void MaintainStandardLimit")
    status = _function(trader, "bool WriteDesktopTraderStatus")

    for declaration in (
        'input bool         UsePriceDistanceInputs = false;',
        'input string       DistanceSymbol = "";',
        'input double       SL_PriceDistance = 0.0;',
        'input double       TP_PriceDistance = 0.0;',
    ):
        assert declaration in trader
    assert "if(UsePriceDistanceInputs)" in sl_builder and "ValidatePriceDistance(SL_PriceDistance" in sl_builder
    assert "SL_DistancePoints <= 0" in sl_builder
    assert "if(UsePriceDistanceInputs)" in tp_builder and "ValidatePriceDistance(TP_PriceDistance" in tp_builder
    assert "TP_DistancePoints <= 0" in tp_builder
    assert "SYMBOL_POINT" in validate and "SYMBOL_TRADE_TICK_SIZE" in validate
    assert "2147483647.0" in validate and "DistanceUnitsAreWhole" in validate
    assert "Broker suffixes are accepted only after an exact canonical pair" in symbol_match
    assert "StringFind" not in symbol_match
    assert market.index("BuildSLFromDistance") < market.index("ConsumeStandardMarketToken") < market.index("trade.Buy(")
    assert "g_portablePresetActionActivated = false;" in init
    assert "g_portablePresetInitializedAt = TimeGMT();" in init
    launch = init.index("g_traderControlReady = LaunchDesktopTraderControls(launchReason)")
    portable_panel_guard = init.index("if(UsePriceDistanceInputs)", launch)
    panel_return = init.index("return INIT_SUCCEEDED;", portable_panel_guard)
    assert launch < portable_panel_guard < panel_return < init.index("LoadDesktopActiveTrendline();")
    assert "WriteDesktopTraderStatus();" in init[portable_panel_guard:panel_return]
    assert "LoadDesktopActiveTrendline" not in init[portable_panel_guard:panel_return]
    assert "MaintainDesktopTrendlineLifecycle" not in init[portable_panel_guard:panel_return]
    assert init.index("if(UsePriceDistanceInputs)", panel_return) < init.index("if(Strategy == STRAT_TRENDLINE_LIMIT)")
    assert 'g_trendName = "";' in init

    assert tick.index("if(UsePriceDistanceInputs &&") < tick.index("if(UseDesktopTraderControls)")
    assert "!UseDesktopTraderControls || !g_portablePresetActionActivated" in tick
    assert tick.index("if(UsePriceDistanceInputs &&") < tick.index("MaintainDesktopTrendlineLifecycle")
    assert tick.index("if(UsePriceDistanceInputs &&") < tick.index("if(!OrdersEnabled)")

    assert "if(UsePriceDistanceInputs && !UseDesktopTraderControls) return;" in timer
    assert timer.index("if(UsePriceDistanceInputs && !UseDesktopTraderControls) return;") < timer.index("if(UseDesktopTraderControls)")
    assert timer.index("if(!UsePriceDistanceInputs || g_portablePresetActionActivated)") < timer.index("MaintainDesktopTrendlineLifecycle")
    assert timer.index("if(!UsePriceDistanceInputs || OrdersEnabled)") < timer.index("HandleDesktopTraderCommand()")
    assert timer.index("if(UsePriceDistanceInputs) return;") < timer.index("if(Strategy == STRAT_STANDARD_LIMIT)")
    assert timer.index("if(UsePriceDistanceInputs && OrdersEnabled && g_portablePresetActionActivated)") < timer.index("if(Strategy == STRAT_STANDARD_LIMIT)")

    assert "if(UsePriceDistanceInputs) return;" in load_desktop_trendline
    assert desktop_maintenance.index("if(UsePriceDistanceInputs && !g_portablePresetActionActivated) return;") < desktop_maintenance.index("CancelExactTrendlineLifecyclePending")
    read_checks = [
        read_command.index("command.protocolVersion != TRADER_CONTROL_PROTOCOL_VERSION"),
        read_command.index("command.instanceId != g_traderControlInstanceId"),
        read_command.index("IsDesktopCommandIdValid(command.commandId)"),
        read_command.index("IsDesktopActionAllowed(command.action)"),
        read_command.index("command.createdAt > now + 5"),
        read_command.index("if(UsePriceDistanceInputs &&"),
    ]
    assert read_checks == sorted(read_checks)
    freshness_check = read_checks[-1]
    success_start = read_command.index('   why = "";', freshness_check)
    success_return = read_command.index("return true;", success_start)
    assert freshness_check < success_start < success_return
    for identity_part in ("ACCOUNT_SERVER", "ACCOUNT_LOGIN", "ChartID()", "_Symbol", "MagicNumber"):
        assert identity_part in control_identity
    assert command_handler.index("if(UsePriceDistanceInputs && !OrdersEnabled) return;") < command_handler.index("ReadDesktopTraderCommand(command, why)")
    assert command_handler.index("ReadDesktopTraderCommand(command, why)") < command_handler.index("ConsumeDesktopTraderCommand") < command_handler.index('else if(command.action == "market")')
    assert command_handler.index('if(command.commandId == lastObserved) return;') < command_handler.index("ConsumeDesktopTraderCommand")
    assert command_handler.index('else if(command.action == "ema_bounce")') < command_handler.index("g_portablePresetActionActivated = true;")
    assert consume_command.index("FileIsExist(marker, FILE_COMMON)") < consume_command.index("WriteVerifiedCommonText(marker, payload, why)")
    assert consume_command.index("FileIsExist(marker, FILE_COMMON)") < consume_command.index("alreadyConsumed = true;") < consume_command.index("return stored;")
    assert 'result.outcome == "accepted"' in command_handler and 'result.outcome == "uncertain"' in command_handler
    assert standard_limit_maintenance.index("if(UsePriceDistanceInputs) return;") < standard_limit_maintenance.index("StandardLimitShouldBeActive")
    assert r'\"orders_enabled\":' in status
    assert "UseDesktopTraderControls" in trader and "if(!OrdersEnabled) return false;" in trader
    assert '#property version   "2.46"' in trader and 'EA_VERSION = "2.46"' in trader
