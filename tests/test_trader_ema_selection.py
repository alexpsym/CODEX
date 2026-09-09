from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "mt5-clone" / "MQL5" / "Experts" / "Trader.mq5"


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


def test_dual_ema_bounce_reference_is_explicit_selectable_and_defaults_slow() -> None:
    trader = _source()
    inputs = trader.split('input group "EMA bounce strategy (derived from Backtest)"', 1)[1].split(
        'input group "Orders"', 1
    )[0]
    selector = _function(trader, "double SelectEmaBounceReference")
    signal = _function(trader, "bool GetEmaBounceSignal")
    dual = signal.split("if(UseDualEMA)", 1)[1].split("\n   else\n", 1)[0]

    assert "enum EmaBounceReference" in inputs
    assert "EMA_BOUNCE_FAST = 0, // Fast EMA" in inputs
    assert "EMA_BOUNCE_SLOW = 1  // Slow EMA" in inputs
    assert "input EmaBounceReference BounceReferenceEMA = EMA_BOUNCE_SLOW" in inputs

    assert "if(!UseDualEMA) return trendValue;" in selector
    assert "BounceReferenceEMA == EMA_BOUNCE_FAST ? fastValue : slowValue" in selector
    assert "GetBufferValue(hFast, 0, 1, fast1)" in dual
    assert "GetBufferValue(hSlow, 0, 1, slow1)" in dual
    assert "reference1 = SelectEmaBounceReference(fast1, slow1, 0.0)" in dual
    assert "up   = (c1 > reference1 && fast1 > slow1);" in dual
    assert "down = (c1 < reference1 && fast1 < slow1);" in dual
    assert "c1 > slow1 && fast1 > slow1" not in dual
    assert "c1 < slow1 && fast1 < slow1" not in dual
    assert "if(up && candleBear)" in signal
    assert "if(down && candleBull)" in signal


def test_single_ema_mode_period_validation_version_and_execution_paths_are_preserved() -> None:
    trader = _source()
    selector = _function(trader, "double SelectEmaBounceReference")
    validation = _function(trader, "bool ValidateEmaBouncePeriods")
    signal = _function(trader, "bool GetEmaBounceSignal")
    init = _function(trader, "int OnInit")
    tick = _function(trader, "void OnTick")
    placement = _function(trader, "bool PlaceMarketEmaBounce")

    single = signal.split("\n   else\n", 1)[1]
    assert selector.index("if(!UseDualEMA) return trendValue;") < selector.index(
        "BounceReferenceEMA == EMA_BOUNCE_FAST"
    )
    assert "GetBufferValue(hTrend, 0, 1, ema1)" in single
    assert "SelectEmaBounceReference(0.0, 0.0, ema1)" in single
    assert "up   = (c1 > reference1);" in single
    assert "down = (c1 < reference1);" in single

    assert "FastEMAPeriod <= 0 || SlowEMAPeriod <= 0" in validation
    assert "TrendEMAPeriod <= 0" in validation
    assert init.index("ValidateEmaBouncePeriods()") < init.index(
        "hFast = iMA(_Symbol, _Period, FastEMAPeriod"
    )
    assert init.index("ValidateEmaBouncePeriods()") < init.index(
        "hTrend = iMA(_Symbol, _Period, TrendEMAPeriod"
    )
    assert "return INIT_PARAMETERS_INCORRECT" in init

    assert '#property version   "2.37"' in trader
    assert 'string EA_VERSION = "2.37";' in trader
    assert 'MaintainTrendlineLifecycle("OnTick")' in tick
    assert 'MaintainStandardLimit("OnTick")' in tick
    assert "if(Strategy == STRAT_STANDARD_MARKET) return;" in tick
    ema_tick = tick.split("if(Strategy == STRAT_EMA_BOUNCE)", 1)[1]
    assert ema_tick.index("if(InPosition()) return;") < ema_tick.index("if(!IsNewBar()) return;") < ema_tick.index(
        "PlaceMarketEmaBounce()"
    )
    assert "trade.Buy(vol, _Symbol, 0.0, sl, tp, EA_COMMENT)" in placement
    assert "trade.Sell(vol, _Symbol, 0.0, sl, tp, EA_COMMENT)" in placement
