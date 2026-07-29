from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PINE_PATH = ROOT / "pinescripts" / "custom_indicator.pine"


def _source() -> str:
    return PINE_PATH.read_text(encoding="utf-8")


def test_custom_indicator_removes_atr_inputs_calculation_and_plot() -> None:
    source = _source()
    lower = source.lower()
    assert "ta.atr" not in lower
    assert "show atr" not in lower
    assert "atr (14)" not in lower
    assert "showAtr" not in source


def test_custom_indicator_crypto_only_volume_vwap_and_timing_gates() -> None:
    source = _source()
    assert 'isCrypto = syminfo.type == "crypto"' in source
    assert "allowBybitTimingLines = isCrypto" in source
    assert "showVwapOnChart = showVwap and isCrypto" in source
    assert "showVwapOffsetOnChart = showVwapOffset and isCrypto" in source
    assert "showVolumeInPane = showVolume and isCrypto" in source
    assert "not isForex" not in source


def test_custom_indicator_session_controls_and_timing_labels() -> None:
    source = _source()
    assert "showSessionOpens" in source
    assert "showSessionCloses" in source
    assert "sessionLabels" in source
    assert "timeframe.in_seconds(timeframe.period) <= 30 * 60" in source
    assert "timeframe.in_seconds(timeframe.period) <= 60 * 60" not in source
    assert 'text="Funding"' in source
    assert 'text="OP EX"' in source
    assert "max_labels_count=500" in source


def test_custom_indicator_session_labels_are_abbreviated_horizontal_and_larger() -> None:
    source = _source()
    assert "horizontalSessionText" in source
    assert 'sessionCode + (isOpen ? " O" : " C")' in source
    assert "verticalSessionText" not in source
    assert '"S\\nY\\nD\\n"' not in source
    assert '"T\\nK\\nY\\n"' not in source
    assert '"L\\nD\\nN\\n"' not in source
    assert '"N\\nY\\n"' not in source
    assert '"Sydney Open"' not in source
    assert '"Tokyo Open"' not in source
    assert '"London Open"' not in source
    assert '"New York Open"' not in source
    assert 'sessionName + (isOpen ? " Open" : " Close")' not in source
    assert "textcolor=color.black" in source
    assert "color=color.new(color.white, 100)" in source
    assert "size=size.tiny" not in source
    assert "size=size.huge" not in source
    assert "size=size.large" not in source
    assert "size=size.normal" in source


def test_custom_indicator_session_rendering_uses_dotted_lines_not_arrow_markers() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1]
    assert "drawSessionMarker" in session_block
    assert "sessionMarkerLines" in session_block
    assert "label.style_arrowdown" not in session_block
    assert "label.style_arrowup" not in session_block
    assert "line.new" in session_block
    assert "line.style_dashed" not in session_block
    assert "line.style_dotted" in session_block
    assert "color=color.black" in session_block
    assert "width=2" in session_block
    assert "sessionMarkerWindowBars = 80" in session_block
    assert "sessionMarkerHalfLengthMultiplier = 0.60" in session_block
    assert "sessionCandleGapRangeFraction" in session_block
    assert 'sessionCandleGapRangeFraction = input.float(0.06, "Candle clearance (recent-range fraction)"' in source
    assert "candleGap = math.max(markerRange * sessionCandleGapRangeFraction, syminfo.mintick * 40)" in session_block
    assert "lowerLineEndY = low - candleGap" in session_block
    assert "upperLineStartY = high + candleGap" in session_block
    assert "lowerLineStartY = lowerLineEndY - markerHalfLength" in session_block
    assert "upperLineEndY = upperLineStartY + markerHalfLength" in session_block
    assert "style=label.style_none" in session_block
    assert "pushBoundedSessionMarker(lowerSessionLine, upperSessionLine, sessionLabel, sessionMaxMarkers)" in session_block
    assert "maxMarkers * 2" in session_block
    assert "clearLineArray(sessionMarkerLines)" in session_block
    assert "textalign=text.align_center" in session_block
    assert "extend=extend.both" not in session_block


def test_custom_indicator_suppresses_each_session_event_by_brisbane_weekend() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1]
    assert 'eventDay = dayofweek(eventTs, "Australia/Brisbane")' in session_block
    assert "dayofweek.saturday" in session_block
    assert "dayofweek.sunday" in session_block
    assert session_block.count("not isBrisbaneWeekend(sessionEventTimestamp(") == 8
    assert 'isSessionEvent("America/New_York", 17, 0)' in session_block


def test_brisbane_event_timestamp_model_suppresses_weekend_and_retains_weekday() -> None:
    brisbane = ZoneInfo("Australia/Brisbane")
    new_york = ZoneInfo("America/New_York")
    ny_friday_close = datetime(2026, 7, 24, 17, 0, tzinfo=new_york)
    ny_thursday_close = datetime(2026, 7, 23, 17, 0, tzinfo=new_york)
    assert ny_friday_close.astimezone(brisbane).weekday() == 5
    assert ny_thursday_close.astimezone(brisbane).weekday() == 4


def test_scale_aware_gap_model_is_positive_and_clears_entire_candle() -> None:
    recent_range = 100.0
    minimum_tick = 0.01
    candle_low = 40.0
    candle_high = 60.0
    gap = max(recent_range * 0.06, minimum_tick * 40)
    lower_endpoint = candle_low - gap
    upper_endpoint = candle_high + gap
    assert gap > 0
    assert lower_endpoint < candle_low
    assert upper_endpoint > candle_high


def test_custom_indicator_labels_are_centered_at_timestamp_and_outside_segments() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1]
    assert "labelY = isOpen ? upperLineEndY + labelGap : lowerLineStartY - labelGap" in session_block
    assert "label.new(x=eventTs, y=labelY" in session_block
    assert "x1=eventTs" in session_block
    assert "x2=eventTs" in session_block
    assert "textalign=text.align_center" in session_block
    assert "sessionVisibilityKey != previousSessionVisibilityKey" in session_block
    assert "while array.size(sessionMarkerLines) > maxMarkers * 2" in session_block
    assert "while array.size(sessionLabels) > maxMarkers" in session_block


def test_custom_indicator_funding_and_option_expiry_code_remains_unchanged() -> None:
    source = _source()
    assert 'text="Funding"' in source
    assert 'text="OP EX"' in source
    assert "fundingLine = line.new" in source
    assert "expiryLine := line.new" in source
    assert "extend=extend.both" in source


def test_custom_indicator_historical_forex_pair_date_mapping_is_exact() -> None:
    source = _source()
    historical_block = source.split("// HISTORICAL FOREX START-DATE MARKER (UTC)", 1)[1].split(
        "sessionVisibilityKey =", 1
    )[0]
    expected = {
        "USDCAD": (1970, 6, 1),
        "GBPCAD": (1972, 6, 26),
        "GBPUSD": (1972, 6, 26),
        "CADCHF": (1973, 1, 23),
        "GBPCHF": (1973, 1, 23),
        "USDCHF": (1973, 1, 23),
        "CADJPY": (1973, 2, 14),
        "CHFJPY": (1973, 2, 14),
        "GBPJPY": (1973, 2, 14),
        "USDJPY": (1973, 2, 14),
        "AUDCAD": (1983, 12, 12),
        "AUDCHF": (1983, 12, 12),
        "AUDJPY": (1983, 12, 12),
        "AUDSGD": (1983, 12, 12),
        "AUDUSD": (1983, 12, 12),
        "GBPAUD": (1983, 12, 12),
        "AUDNZD": (1985, 3, 4),
        "GBPNZD": (1985, 3, 4),
        "NZDCAD": (1985, 3, 4),
        "NZDCHF": (1985, 3, 4),
        "NZDJPY": (1985, 3, 4),
        "NZDUSD": (1985, 3, 4),
        "EURAUD": (1999, 1, 4),
        "EURCAD": (1999, 1, 4),
        "EURCHF": (1999, 1, 4),
        "EURGBP": (1999, 1, 4),
        "EURJPY": (1999, 1, 4),
        "EURNZD": (1999, 1, 4),
        "EURUSD": (1999, 1, 4),
        "USDCNH": (2010, 8, 23),
    }
    actual = {
        pair: (int(year), int(month), int(day))
        for pair, year, month, day in re.findall(
            r'"([A-Z]{6})"\s*=>\s*timestamp\("GMT",\s*(\d{4}),\s*(\d{1,2}),\s*(\d{1,2}),\s*0,\s*0\)',
            historical_block,
        )
    }
    assert actual == expected


def test_custom_indicator_historical_forex_marker_is_broker_independent_and_exactly_anchored() -> None:
    source = _source()
    historical_block = source.split("// HISTORICAL FOREX START-DATE MARKER (UTC)", 1)[1].split(
        "sessionVisibilityKey =", 1
    )[0]
    assert "syminfo.basecurrency" in historical_block
    assert "syminfo.currency" in historical_block
    assert "syminfo.tickerid" not in historical_block
    assert 'syminfo.type == "forex"' in historical_block
    assert "historicalForexStartTimestampForPair(historicalForexPair)" in historical_block
    assert "time <= historicalForexStartTimestamp and historicalForexStartTimestamp < time_close" in historical_block
    assert historical_block.count("xloc=xloc.bar_time") == 2
    assert historical_block.count("x1=historicalForexStartTimestamp") == 2
    assert historical_block.count("x2=historicalForexStartTimestamp") == 2
    assert "showSessionLines" not in historical_block
    assert "timeframe." not in historical_block


def test_custom_indicator_historical_forex_marker_has_no_reverse_or_unlisted_fallback() -> None:
    source = _source()
    historical_block = source.split("// HISTORICAL FOREX START-DATE MARKER (UTC)", 1)[1].split(
        "sessionVisibilityKey =", 1
    )[0]
    assert '"CADUSD" =>' not in historical_block
    assert '"USDEUR" =>' not in historical_block
    assert '"CNHUSD" =>' not in historical_block
    assert "=> na" in historical_block


def test_custom_indicator_historical_forex_marker_draws_one_split_marker_without_duplicates() -> None:
    source = _source()
    historical_block = source.split("// HISTORICAL FOREX START-DATE MARKER (UTC)", 1)[1].split(
        "sessionVisibilityKey =", 1
    )[0]
    assert "var bool historicalForexStartMarkerDrawn = false" in historical_block
    assert "and not historicalForexStartMarkerDrawn" in historical_block
    assert historical_block.count("line.new(") == 2
    assert historical_block.count("historicalForexStartMarkerDrawn := true") == 1
    assert historical_block.count("style=line.style_dotted") == 2
    assert historical_block.count("color=color.black") == 2
    assert historical_block.count("width=2") == 2
    assert historical_block.count("force_overlay=true") == 2
    assert "historicalCandleGap = math.max(historicalMarkerRange * sessionCandleGapRangeFraction, syminfo.mintick * 40)" in historical_block
    assert "historicalLowerEndY = low - historicalCandleGap" in historical_block
    assert "historicalUpperStartY = high + historicalCandleGap" in historical_block
    assert "label.new" not in historical_block
