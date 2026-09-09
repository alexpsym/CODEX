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


def test_window_writes_one_atomic_scoped_command_and_blocks_duplicate_pending_clicks(tmp_path: Path) -> None:
    window = _window_module()
    identity = window.InstanceIdentity("A1B2C3D4", 123456, "Pepperstone-Demo", 9988, "EURUSD.a", 91001, "2.38")
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
    assert '#property version   "2.38"' in trader and 'EA_VERSION = "2.38"' in trader
