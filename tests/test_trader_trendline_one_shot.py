from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"


def _source() -> str:
    return TRADER.read_text(encoding="utf-8")


def test_trendline_lifecycle_reserves_a_durable_account_symbol_magic_name_generation() -> None:
    trader = _source()
    lifecycle = trader.split("string TrendlineLifecycleGlobalKey()", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert "input long         TrendlineArmGeneration = 0;" in trader
    assert "AccountInfoString(ACCOUNT_SERVER)" in lifecycle
    assert "AccountInfoInteger(ACCOUNT_LOGIN)" in lifecycle
    assert "_Symbol" in lifecycle and "IntegerToString(MagicNumber)" in lifecycle
    assert "TrendlineObjectName" in lifecycle
    assert "GlobalVariableSetOnCondition" in lifecycle
    assert "GlobalVariablesFlush()" in lifecycle
    assert "TrendlineArmGeneration + 0.5" in lifecycle


def test_trendline_completion_and_cancellation_paths_consume_before_any_new_submission() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert lifecycle.index("FindActiveTrendlineLifecycle") < lifecycle.index("TrendlineStateIsWorking")
    assert "StoreTrendlineLifecycleState(storedState, true, (double)completedGeneration" in lifecycle
    assert 'LogTrendlineLifecycle("consumed"' in lifecycle
    assert lifecycle.index('LogTrendlineLifecycle("consumed"') < lifecycle.index(
        "TrendlineArmGenerationIsValid"
    )
    assert trader.count('MaintainTrendlineLifecycle("OnTick")') == 1
    assert trader.count('MaintainTrendlineLifecycle("OnTimer")') == 1
    assert "position just closed -> immediately re-arm" not in trader


def test_same_named_line_requires_an_explicit_new_generation_to_rearm() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]

    assert "completedGeneration >= TrendlineArmGeneration" in lifecycle
    assert "increase TrendlineArmGeneration to re-arm" in lifecycle
    assert "event=manually_rearmed" in lifecycle
    assert "PlaceOrReplacePendingLimitAtEntry(isBuyLimit, entry, false, why)" in trader
    assert "CancelAllPendingByMagic();" not in lifecycle


def test_reinitialization_adopts_existing_work_and_fails_closed_without_a_new_arm() -> None:
    trader = _source()
    lifecycle = trader.split("void MaintainTrendlineLifecycle", 1)[1].split(
        "bool IsTransientPendingRetcode", 1
    )[0]
    on_init = trader.split("int OnInit()", 1)[1].split("void OnDeinit", 1)[0]

    assert "A pre-upgrade active order/position is adopted as generation zero." in lifecycle
    assert "StoreTrendlineLifecycleState(0.0, false, 0.5, why)" in lifecycle
    assert "Set TrendlineArmGeneration to a new positive integer" in lifecycle
    assert 'MaintainTrendlineLifecycle("OnInit")' in on_init
    assert "PlaceOrReplacePendingTrendline();" not in on_init
