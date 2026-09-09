from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"


def _source() -> str:
    return TRADER.read_text(encoding="utf-8")


def test_working_pending_tracks_new_bar_by_exact_ticket_without_a_second_order() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]
    modify = trader.split("bool ModifyWorkingTrendlinePending", 1)[1].split(
        "void MaintainTrendlineLifecycle", 1
    )[0]

    assert lifecycle.index("FindExactTrendlinePending") < lifecycle.index("FindExactTrendlinePosition")
    assert "if(IsNewBar() && !g_trendlineTrackingFailed" in lifecycle
    assert "ModifyWorkingTrendlinePending(pendingTicket, record, why)" in lifecycle
    assert "trade.OrderModify(ticket, entry, sl, tp, typeTime, record.expiration)" in modify
    assert "no replacement will be sent" in modify
    assert "PlaceOrReplacePendingTrendline()" not in lifecycle.split("if(record.exists && record.working)", 1)[1].split(
        "if(!TrendlineArmGenerationIsValid())", 1
    )[0]


def test_only_exact_lifecycle_comment_ticket_and_position_identifier_are_owned() -> None:
    trader = _source()
    ownership = trader.split("bool IsExactTrendlinePending", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert "ORDER_COMMENT" in ownership and "record.orderComment" in ownership
    assert "record.ticket == 0 || record.ticket == ticket" in ownership
    assert "HistoryOrderGetInteger(record.ticket, ORDER_POSITION_ID)" in ownership
    assert "POSITION_IDENTIFIER" in ownership
    assert "FindAnyPendingLimitForEA(unrelatedPending)" in trader
    assert "blocked_unrelated" in trader
    assert "CancelAllPendingByMagic();" not in ownership


def test_durable_common_file_restores_consumed_state_before_expiring_terminal_global_cache() -> None:
    trader = _source()
    persistence = trader.split("string TrendlineLifecycleFingerprint", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert "TrendlineLifecycleStateFile" in persistence
    assert "FILE_COMMON" in persistence
    assert "FileWriteString" in persistence and "FileFlush(handle)" in persistence
    assert "AcquireTrendlineLifecycleLock" in persistence
    load = persistence.split("bool LoadTrendlineLifecycleRecord", 1)[1].split("bool IsExactTrendlinePending", 1)[0]
    assert load.index("ReadTrendlineLifecycleRecord") < load.index("GlobalVariableCheck")
    assert "Durable state always takes precedence over the expiring cache" in load
    assert "PersistTrendlineLifecycleRecord(empty, record, why)" in load


def test_active_rearm_is_consumed_not_queued_and_completion_cannot_resubmit() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    active = lifecycle.split("if(record.exists && record.working)", 1)[1].split(
        "if(!TrendlineArmGenerationIsValid())", 1
    )[0]
    assert "TrendlineArmGeneration > record.blockedGeneration" in active
    assert "rejected.blockedGeneration = TrendlineArmGeneration" in active
    assert "active_rearm_rejected" in active and "will not queue" in active
    assert active.index("active_rearm_rejected") < active.index("FindExactTrendlinePending")
    assert "TrendlineHighestHandled(record) >= TrendlineArmGeneration" in lifecycle
    assert lifecycle.index('LogTrendlineLifecycle("consumed"') < lifecycle.index("TrendlineHighestHandled(record)")
