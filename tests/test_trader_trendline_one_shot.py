from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"


def _source() -> str:
    return TRADER.read_text(encoding="utf-8")


def test_order_modify_uses_complete_signature_safe_no_change_and_later_new_bar_tracking() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]
    modify = trader.split("bool ModifyWorkingTrendlinePending", 1)[1].split(
        "void MaintainTrendlineLifecycle", 1
    )[0]

    assert lifecycle.index("FindExactTrendlinePending") < lifecycle.index("FindExactTrendlinePosition")
    assert "bool newBar = IsNewBar();" in lifecycle
    assert "ModifyWorkingTrendlinePending(pendingTicket, record, volumeChanged, why)" in lifecycle
    assert "trade.OrderModify(ticket, entry, sl, tp, typeTime, record.expiration, 0.0)" in modify
    assert "TRADE_RETCODE_NO_CHANGES" in modify
    assert "if(volumeChanged || !valuesChanged)" in modify
    assert modify.index("valuesChanged") < modify.index("trade.OrderModify")
    assert "PlaceOrReplacePendingTrendline()" not in lifecycle.split("if(record.exists && record.working)", 1)[1].split(
        "if(!TrendlineArmGenerationIsValid())", 1
    )[0]


def test_exact_lifecycle_only_cancellation_and_immutable_recorded_order_type_ownership() -> None:
    trader = _source()
    ownership = trader.split("bool IsExactTrendlinePending", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert "ORDER_COMMENT" in ownership and "record.orderComment" in ownership
    assert "type != record.orderType" in ownership
    assert "record.ticket == 0 || record.ticket == ticket" in ownership
    assert "HistoryOrderGetInteger(record.ticket, ORDER_POSITION_ID)" in ownership
    assert "POSITION_IDENTIFIER" in ownership
    assert "FindAnyPendingLimitForEA(unrelatedPending)" in trader
    assert "blocked_unrelated" in trader
    assert "CancelAllPendingByMagic();" not in ownership
    cancellation = trader.split("bool DeleteExactTrendlinePending", 1)[1].split(
        "bool ReplaceExactTrendlinePendingForVolume", 1
    )[0]
    assert "IsExactTrendlinePending(record.ticket, record)" in cancellation
    assert "trade.OrderDelete(record.ticket)" in cancellation
    assert "TRADE_RETCODE_DONE" in cancellation
    assert "OrderSelect(record.ticket)" in cancellation


def test_verified_durable_write_failure_blocks_placement_or_lifecycle_advancement() -> None:
    trader = _source()
    persistence = trader.split("string TrendlineLifecycleFingerprint", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    write = persistence.split("bool WriteTrendlineLifecycleRecord", 1)[1].split(
        "void LogTrendlineLifecycle", 1
    )[0]
    assert "ResetLastError()" in write
    assert "uint written = FileWriteString(handle, line)" in write
    assert "FileFlush(handle)" in write and "ReadTrendlineLifecycleRecord(verified, why)" in write
    assert "SameTrendlineLifecycleRecord(verified, record)" in write
    assert "return false" in write
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split("bool IsTransientPendingRetcode", 1)[0]
    assert lifecycle.index("PersistTrendlineLifecycleRecord(record, armed, why)") < lifecycle.index(
        "PlaceOrReplacePendingTrendline()"
    )


def test_volume_change_uses_exact_delete_confirm_replace_without_overlap_and_completion_stays_consumed() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    replace = trader.split("bool ReplaceExactTrendlinePendingForVolume", 1)[1].split(
        "void CancelExactTrendlineLifecyclePending", 1
    )[0]
    assert replace.index("PersistTrendlineLifecycleRecord(record, transition, why)") < replace.index(
        "DeleteExactTrendlinePending(transition, why)"
    ) < replace.index("PlaceOrReplacePendingTrendline()")
    assert "transition.replacing = true" in replace
    assert "replaced.replacing = false" in replace
    assert "PersistTrendlineLifecycleRecord(transition, replaced, why)" in replace
    assert "volumeChanged &&" in lifecycle and "ReplaceExactTrendlinePendingForVolume(record, why)" in lifecycle
    assert "TrendlineHighestHandled(record) >= TrendlineArmGeneration" in lifecycle
    assert lifecycle.index('LogTrendlineLifecycle("consumed"') < lifecycle.index("TrendlineHighestHandled(record)")
