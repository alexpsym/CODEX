from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"


def _source() -> str:
    return TRADER.read_text(encoding="utf-8")


def _function(source: str, signature: str) -> str:
    """Return a complete MQL function, including nested control-flow blocks."""
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


def test_replacement_uses_persisted_identity_and_prepared_terms_only() -> None:
    trader = _source()
    submit = _function(trader, "ExactReplacementResult SubmitExactTrendlineReplacement")
    replace = _function(trader, "ExactReplacementResult ReplaceExactTrendlinePendingForVolume")
    maintain = _function(trader, "void MaintainTrendlineLifecycle")

    forbidden_mutable_inputs = (
        "Direction",
        "TrendlineArmGeneration",
        "PendingCancelAfterMinutes",
        "ComputeExpireAt",
        "PlaceOrReplacePendingTrendline",
        "g_expireAt",
    )
    for forbidden in forbidden_mutable_inputs:
        assert forbidden not in submit
        assert forbidden not in replace

    assert "record.orderType == ORDER_TYPE_BUY_LIMIT" in submit
    assert submit.count("record.orderComment") == 2
    assert "record.expiration > 0 ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC" in submit
    assert submit.count("record.expiration, record.orderComment") == 2
    assert "TrendlinePendingMatchesPreparedTerms(resultTicket, record, entry, sl, tp, volume)" in submit

    assert "record.orderComment != TrendlineOrderComment(record.generation)" in replace
    assert ".generation =" not in replace
    assert replace.index("PersistTrendlineLifecycleRecord(record, transition, why)") < replace.index(
        "IsExactTrendlinePending(transition.ticket, transition)"
    ) < replace.index("DeleteExactTrendlinePending(transition, why)") < replace.index(
        "SubmitExactTrendlineReplacement(transition, entry, sl, tp, volume, newTicket, why)"
    )
    assert replace.count("SubmitExactTrendlineReplacement(") == 1
    assert replace.index("replaced.ticket = newTicket") < replace.index(
        "PersistTrendlineLifecycleRecord(transition, replaced, why)"
    )

    assert maintain.index("PrepareWorkingTrendlineTerms(record, desiredEntry") < maintain.index(
        "ModifyWorkingTrendlinePending(pendingTicket, record, desiredEntry"
    ) < maintain.index("ReplaceExactTrendlinePendingForVolume(")
    replacement_call = maintain.split("ReplaceExactTrendlinePendingForVolume(", 1)[1].split(");", 1)[0]
    assert "record, desiredEntry, desiredSL, desiredTP, desiredVolume, why" in replacement_call


def test_cancellation_preserves_exact_ticket_and_refuses_ambiguous_identity_matches() -> None:
    trader = _source()
    cancel = _function(trader, "void CancelExactTrendlineLifecyclePending")
    delete = _function(trader, "bool DeleteExactTrendlinePending")
    exact = _function(trader, "bool IsExactTrendlinePending")
    identity = _function(trader, "bool IsTrendlinePendingIdentity")

    assert "FindExactTrendlinePending(record, record.ticket)" not in trader
    assert "CancelAllPendingByMagic" not in cancel
    assert "if(record.ticket > 0)" in cancel
    direct = cancel.split("if(record.ticket > 0)", 1)[1].split("\n   else\n", 1)[0]
    assert "IsExactTrendlinePending(record.ticket, record)" in direct
    assert "record.ticket =" not in direct

    reserved = cancel.split("\n   else\n", 1)[1]
    assert "CountTrendlinePendingIdentity(record, observedTicket)" in reserved
    assert reserved.index("if(matches > 1)") < reserved.index("if(matches == 1)")
    ambiguous = reserved.split("if(matches > 1)", 1)[1].split("if(matches == 1)", 1)[0]
    assert "cancellation is ambiguous" in ambiguous
    assert "return;" in ambiguous
    assert reserved.index("observed.ticket = observedTicket") < reserved.index(
        "PersistTrendlineLifecycleRecord(record, observed, why)"
    ) < reserved.index("record = observed")
    assert cancel.index("record = observed") < cancel.index("DeleteExactTrendlinePending(record, why)")

    assert "record.ticket > 0 && ticket == record.ticket" in exact
    for immutable_check in (
        "ORDER_SYMBOL",
        "ORDER_MAGIC",
        "type != record.orderType",
        "ORDER_COMMENT",
        "record.orderComment",
    ):
        assert immutable_check in identity
    assert "IsExactTrendlinePending(record.ticket, record)" in delete
    assert delete.index("trade.OrderDelete(record.ticket)") < delete.index("OrderSelect(record.ticket)")


def test_strict_v3_parse_and_replacement_outcomes_fail_closed_without_retry() -> None:
    trader = _source()
    parse = _function(trader, "bool ReadTrendlineLifecycleRecord")
    decimal = _function(trader, "bool IsUnsignedDecimalText")
    submit = _function(trader, "ExactReplacementResult SubmitExactTrendlineReplacement")
    replace = _function(trader, "ExactReplacementResult ReplaceExactTrendlinePendingForVolume")

    assert 'fields[0] == "TraderTLV2"' in parse and "return false" in parse.split(
        'fields[0] == "TraderTLV2"', 1
    )[1]
    assert 'fieldCount != 10 || fields[0] != "TraderTLV3"' in parse
    assert "fields[1] != TrendlineLifecycleFingerprint()" in parse
    assert "!atEnd" in parse and 'StringFind(line, "\\r")' in parse and 'StringFind(line, "\\n")' in parse
    assert '(fields[4] != "0" && fields[4] != "1")' in parse
    assert '(fields[5] != "0" && fields[5] != "1")' in parse
    assert parse.count("ParseNonnegativeLongStrict(") == 5
    assert 'StringCompare(value, "9223372036854775807") > 0' in decimal
    assert "ch < 48 || ch > 57" in decimal
    relationships = parse.split("if(record.generation <= 0", 1)[1].split("record.exists = true", 1)[0]
    assert "record.blockedGeneration < record.generation" in relationships
    assert "record.blockedGeneration > TRENDLINE_ARM_GENERATION_MAX" in relationships
    assert "record.orderComment != TrendlineOrderComment(record.generation)" in relationships
    assert "record.orderType != ORDER_TYPE_BUY_LIMIT" in relationships
    assert "record.orderType != ORDER_TYPE_SELL_LIMIT" in relationships
    assert "record.replacing && (!record.working || record.ticket == 0)" in relationships

    accepted = submit.split("ulong observed = 0;", 1)[0]
    assert "if(sendOk && IsTradePlacementAccepted(retcode) && resultTicket > 0)" in accepted
    assert "TrendlinePendingMatchesPreparedTerms(resultTicket, record, entry, sl, tp, volume)" in accepted

    definite = submit.split("if(IsDefinitePendingRejectionRetcode(retcode)", 1)[1].split(
        'why = "Replacement outcome is ambiguous', 1
    )[0]
    assert "sendOk" not in definite
    assert "resultTicket == 0 && visibleMatches == 0" in definite
    assert "return EXACT_REPLACEMENT_CONSUMED" in definite
    unresolved = submit.split(
        'why = "Replacement outcome is ambiguous', 1
    )[1]
    assert "return EXACT_REPLACEMENT_UNRESOLVED" in unresolved
    assert submit.count("EXACT_REPLACEMENT_CONSUMED") == 1
    assert "IsTransientPendingRetcode" not in definite
    assert "uint retcode = trade.ResultRetcode()" in submit
    assert "ulong resultTicket = (ulong)trade.ResultOrder()" in submit
    assert "int visibleMatches = CountTrendlinePendingIdentity(record, observed)" in submit
    assert '#property version   "2.41"' in trader
    assert 'string EA_VERSION = "2.41";' in trader
    assert "Inactive Phase 1 preservation marker" not in trader
    assert '#property version   "2.36"' not in trader
    assert 'EA_VERSION = "2.36"' not in trader

    assert replace.count("SubmitExactTrendlineReplacement(") == 1
    assert replace.index("transition.replacing = true") < replace.index(
        "PersistTrendlineLifecycleRecord(record, transition, why)"
    ) < replace.index("DeleteExactTrendlinePending(transition, why)") < replace.index(
        "SubmitExactTrendlineReplacement(transition, entry, sl, tp, volume, newTicket, why)"
    )
    consumed = replace.split("if(outcome == EXACT_REPLACEMENT_CONSUMED)", 1)[1].split(
        "if(outcome != EXACT_REPLACEMENT_ACCEPTED)", 1
    )[0]
    assert "consumed.working = false" in consumed
    assert "consumed.replacing = false" in consumed
    assert "consumed.ticket = 0" in consumed
    assert "PersistTrendlineLifecycleRecord(transition, consumed, why)" in consumed
    unresolved = replace.split("if(outcome != EXACT_REPLACEMENT_ACCEPTED)", 1)[1].split(
        "TrendlineLifecycleRecord replaced", 1
    )[0]
    assert "return EXACT_REPLACEMENT_UNRESOLVED" in unresolved
    persist_failure = replace.split("if(!PersistTrendlineLifecycleRecord(transition, replaced, why))", 1)[1]
    assert "DeleteObservedExactReplacement(newTicket, transition, entry, sl, tp, volume" in persist_failure
    assert "manual broker reconciliation is required" in persist_failure
