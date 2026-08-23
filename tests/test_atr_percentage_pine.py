from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ATR_PATH = ROOT / "pinescripts" / "atr_percentage.pine"
CUSTOM_PATH = ROOT / "pinescripts" / "custom_indicator.pine"


def _source() -> str:
    return ATR_PATH.read_text(encoding="utf-8")


def test_atr_percentage_is_an_independent_pine_v6_lower_panel() -> None:
    source = _source()
    assert source.startswith("//@version=6\n")
    assert 'indicator("ATR Percentage"' in source
    assert "overlay=false" in source
    assert "format=format.percent" in source
    assert "force_overlay" not in source
    assert ATR_PATH.name == "atr_percentage.pine"


def test_atr_percentage_uses_the_normal_rma_atr_14_and_exact_percent_formula() -> None:
    source = _source()
    assert 'length = input.int(14, "ATR length", minval=1' in source
    assert "atrValue = ta.atr(length)" in source
    assert "atrValue / close * 100.0" in source
    assert "na(close) or close == 0.0 ? na" in source
    assert 'plot(atrPercent, "ATR %"' in source
    assert 'plotColor = input.color(' in source
    assert 'plotWidth = input.int(' in source


def test_atr_percentage_threshold_and_alerts_are_confirmed_and_context_rich() -> None:
    source = _source()
    assert 'thresholdPercent = input.float(' in source
    assert 'plot(thresholdPercent, "Alert threshold %"' in source
    assert "ta.crossover(atrPercent, thresholdPercent)" in source
    assert "ta.crossunder(atrPercent, thresholdPercent)" in source
    assert "barstate.isconfirmed and crossedAboveThresholdRaw" in source
    assert "barstate.isconfirmed and crossedBelowThresholdRaw" in source
    conditions = re.findall(r"^alertcondition\(", source, flags=re.MULTILINE)
    assert len(conditions) == 2
    for placeholder in ('{{exchange}}', '{{ticker}}', '{{interval}}', '{{time}}', '{{plot("ATR %")}}'):
        assert source.count(placeholder) == 2


def test_custom_indicator_remains_atr_free() -> None:
    custom = CUSTOM_PATH.read_text(encoding="utf-8").lower()
    assert "ta.atr" not in custom
    assert "atr percentage" not in custom
    assert "atrpercent" not in custom
