from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PINE_PATH = ROOT / "pinescripts" / "custom_indicator.pine"


def _source() -> str:
    return PINE_PATH.read_text(encoding="utf-8")


def _cross_flags(
    previous_price: float,
    current_price: float,
    previous_offset: float,
    current_offset: float,
    *,
    enabled: bool = True,
) -> tuple[bool, bool, bool]:
    if not enabled:
        return False, False, False
    crossed_above = current_price > current_offset and previous_price <= previous_offset
    crossed_below = current_price < current_offset and previous_price >= previous_offset
    return crossed_above, crossed_below, crossed_above or crossed_below


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


def test_custom_indicator_exposes_three_unique_hidden_offset_alert_series() -> None:
    source = _source()
    expected = {
        "9 EMA Offset": ("ema9OffsetAlertSeries", "showEma9Offset ? ema9Offset : na"),
        "20 EMA Offset": ("ema20OffsetAlertSeries", "showEma20Offset ? ema20Offset : na"),
        "VWAP Offset": ("vwapOffsetAlertSeries", "showVwapOffsetOnChart ? vwapOffset : na"),
    }
    plot_lines = [line.strip() for line in source.splitlines() if line.strip().startswith("plot(")]

    for title, (series_name, assignment) in expected.items():
        assert f"{series_name} = {assignment}" in source
        matching_lines = [line for line in plot_lines if f'"{title}"' in line]
        assert len(matching_lines) == 1
        assert matching_lines[0].startswith(f'plot({series_name}, "{title}"')
        assert "display=display.none" in matching_lines[0]
        assert "format=format.price" in matching_lines[0]

    assert len(set(expected)) == 3


def test_custom_indicator_has_only_dedicated_confirmed_offset_alert_conditions() -> None:
    source = _source()
    expected = {
        "9 EMA Offset - Price Crossing": "ema9OffsetCross",
        "9 EMA Offset - Price Crossing Above": "ema9OffsetCrossAbove",
        "9 EMA Offset - Price Crossing Below": "ema9OffsetCrossBelow",
        "20 EMA Offset - Price Crossing": "ema20OffsetCross",
        "20 EMA Offset - Price Crossing Above": "ema20OffsetCrossAbove",
        "20 EMA Offset - Price Crossing Below": "ema20OffsetCrossBelow",
        "VWAP Offset - Price Crossing": "vwapOffsetCross",
        "VWAP Offset - Price Crossing Above": "vwapOffsetCrossAbove",
        "VWAP Offset - Price Crossing Below": "vwapOffsetCrossBelow",
    }
    conditions = re.findall(
        r'^alertcondition\(([^,]+),\s*"([^"]+)",\s*(.+)\)$',
        source,
        flags=re.MULTILINE,
    )
    actual = {title: condition.strip() for condition, title, _message in conditions}

    assert len(conditions) == 9
    assert actual == expected
    assert "offsetAlertConfirmed = barstate.isconfirmed" in source
    assert re.search(r"^\s*alert\s*\(", source, flags=re.MULTILINE) is None


def test_custom_indicator_offset_conditions_reference_only_their_gated_series() -> None:
    source = _source()
    expected = {
        "ema9Offset": "ema9OffsetAlertSeries",
        "ema20Offset": "ema20OffsetAlertSeries",
        "vwapOffset": "vwapOffsetAlertSeries",
    }

    for prefix, series_name in expected.items():
        assert f"{prefix}CrossAboveRaw = ta.crossover(close, {series_name})" in source
        assert f"{prefix}CrossBelowRaw = ta.crossunder(close, {series_name})" in source
        assert f"{prefix}CrossAbove = offsetAlertConfirmed and {prefix}CrossAboveRaw" in source
        assert f"{prefix}CrossBelow = offsetAlertConfirmed and {prefix}CrossBelowRaw" in source
        assert f"{prefix}Cross = {prefix}CrossAbove or {prefix}CrossBelow" in source

    alert_block = source.split("// OFFSET ALERT SERIES AND CONDITIONS", 1)[1].split(
        "// plot() cannot draw dashed series lines", 1
    )[0]
    assert "ta.crossover(close, ema9)" not in alert_block
    assert "ta.crossunder(close, ema9)" not in alert_block
    assert "ta.crossover(close, ema20)" not in alert_block
    assert "ta.crossunder(close, ema20)" not in alert_block
    assert "ta.crossover(close, vwapValue)" not in alert_block
    assert "ta.crossunder(close, vwapValue)" not in alert_block


def test_custom_indicator_offset_alert_messages_identify_the_exact_series() -> None:
    source = _source()
    condition_lines = [line for line in source.splitlines() if line.startswith("alertcondition(")]
    for title in ("9 EMA Offset", "20 EMA Offset", "VWAP Offset"):
        matching_lines = [line for line in condition_lines if f'"{title} - Price Crossing' in line]
        assert len(matching_lines) == 3
        assert all(f'{{{{plot("{title}")}}}}' in line for line in matching_lines)


def test_custom_indicator_preserves_base_and_dashed_offset_visuals() -> None:
    source = _source()
    assert 'plot(showEma9 ? ema9 : na, "9 EMA"' in source
    assert 'plot(showEma20 ? ema20 : na, "20 EMA"' in source
    assert 'plot(showVwapOnChart ? vwapValue : na, "VWAP"' in source
    assert 'plot(showVolumeInPane ? volume : na, "Volume"' in source
    assert "drawDashedOffset(ema9OffsetLines, showEma9Offset, ema9Offset" in source
    assert "drawDashedOffset(ema20OffsetLines, showEma20Offset, ema20Offset" in source
    assert "drawDashedOffset(vwapOffsetLines, showVwapOffsetOnChart, vwapOffset" in source


def test_offset_cross_model_ignores_base_crossings_until_each_offset_is_reached() -> None:
    scenarios = {
        "9 EMA Offset": (99.0, 100.25, 101.0),
        "20 EMA Offset": (99.0, 100.75, 102.0),
        "VWAP Offset": (99.0, 100.5, 103.0),
    }
    for _name, (previous_price, current_price, offset) in scenarios.items():
        assert _cross_flags(previous_price, current_price, offset, offset) == (False, False, False)


def test_offset_cross_model_fires_once_on_each_supported_offset_transition() -> None:
    for offset in (101.0, 102.0, 103.0):
        assert _cross_flags(offset - 0.25, offset + 0.25, offset, offset) == (True, False, True)
        assert _cross_flags(offset + 0.25, offset - 0.25, offset, offset) == (False, True, True)

        prices = (offset - 0.25, offset + 0.25, offset + 0.5)
        events = [
            _cross_flags(prices[index - 1], prices[index], offset, offset)[2]
            for index in range(1, len(prices))
        ]
        assert events == [True, False]


def test_offset_cross_model_never_fires_for_a_disabled_line() -> None:
    assert _cross_flags(99.0, 101.0, 100.0, 100.0, enabled=False) == (False, False, False)


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
    assert "sessionMarkerHalfLengthMultiplier = 0.30" in session_block
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


def test_custom_indicator_filters_each_session_event_by_its_local_weekday() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1]
    assert "isSessionLocalWeekend" in session_block
    assert "eventDay = dayofweek(eventTs, sessionTimezone)" in session_block
    assert "dayofweek.saturday" in session_block
    assert "dayofweek.sunday" in session_block
    assert "isBrisbaneWeekend" not in session_block
    assert session_block.count("not isSessionLocalWeekend(sessionEventTimestamp(") == 8
    assert 'isSessionEvent("America/New_York", 17, 0)' in session_block
    assert (
        'not isSessionLocalWeekend(sessionEventTimestamp("America/New_York", 17, 0), '
        '"America/New_York")'
    ) in session_block


def test_new_york_local_weekday_model_keeps_friday_close_even_on_brisbane_saturday() -> None:
    brisbane = ZoneInfo("Australia/Brisbane")
    new_york = ZoneInfo("America/New_York")
    ny_friday_close = datetime(2026, 7, 24, 17, 0, tzinfo=new_york)
    assert ny_friday_close.astimezone(brisbane).weekday() == 5
    assert ny_friday_close.weekday() == 4
    ny_saturday_event = datetime(2026, 7, 25, 17, 0, tzinfo=new_york)
    assert ny_saturday_event.weekday() == 5


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


def test_custom_indicator_session_label_gap_has_a_configurable_visible_floor() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1].split(
        "// MOVING 100-CANDLE MARKER", 1
    )[0]
    assert (
        'sessionLabelGapRangeFraction = input.float(0.05, '
        '"Label clearance (recent-range fraction)", minval=0.01'
    ) in source
    assert (
        "labelGap = math.max(markerRange * sessionLabelGapRangeFraction, "
        "syminfo.mintick * 50)"
    ) in session_block
    assert "labelY = isOpen ? upperLineEndY + labelGap : lowerLineStartY - labelGap" in session_block


def test_custom_indicator_moving_100_marker_counts_current_forming_bar_exactly() -> None:
    source = _source()
    marker_block = source.split("// MOVING 100-CANDLE MARKER", 1)[1].split(
        "// HISTORICAL FOREX START-DATE MARKER", 1
    )[0]
    assert 'showHundredCandleMarker = input.bool(true, "Show 100-candle marker"' in source
    assert "hundredMarkerHasHistory = bar_index >= 99" in marker_block
    assert "hundredTargetBarIndex = bar_index - 99" in marker_block
    assert "bar_index - 100" not in marker_block
    assert "hundredTargetHigh = high[99]" in marker_block
    assert "hundredTargetLow = low[99]" in marker_block
    assert "timeframe." not in marker_block
    assert "if showHundredCandleMarker and hundredMarkerHasHistory" in marker_block


def test_session_markers_wait_until_event_time_on_5_15_and_30_minute_bars() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1].split(
        "// MOVING 100-CANDLE MARKER", 1
    )[0]
    assert "time < eventTs and eventTs <= time_close" in session_block
    assert "time <= eventTs and eventTs < time_close" not in session_block
    assert "if showSessionLines and barstate.isconfirmed" in source
    assert "if showSessionLines and barstate.isnew" not in source

    event_ts = 17 * 60
    for timeframe_minutes in (5, 15, 30):
        bar_open = event_ts - timeframe_minutes
        bar_close = event_ts
        realtime_updates = [
            (bar_open, False),
            (bar_open + timeframe_minutes / 2, False),
            (bar_close, True),
        ]
        marker_creation_times = [
            update_time
            for update_time, confirmed in realtime_updates
            if bar_open < event_ts <= bar_close and confirmed
        ]
        assert marker_creation_times == [event_ts]


def test_final_friday_new_york_close_uses_confirmed_final_bar_without_a_next_bar() -> None:
    source = _source()
    event_ts = 17 * 60
    final_friday_bar = (16 * 60 + 30, event_ts)
    start, end = final_friday_bar
    realtime_updates = [(start, False), (end, True)]
    marker_creation_times = [
        update_time
        for update_time, confirmed in realtime_updates
        if start < event_ts <= end and confirmed
    ]

    assert "time < eventTs and eventTs <= time_close" in source
    assert "if showSessionLines and barstate.isconfirmed" in source
    assert start < event_ts <= end
    assert end == event_ts
    assert marker_creation_times == [event_ts]


def test_custom_indicator_moving_100_marker_has_one_bounded_split_lifecycle() -> None:
    source = _source()
    marker_block = source.split("// MOVING 100-CANDLE MARKER", 1)[1].split(
        "// HISTORICAL FOREX START-DATE MARKER", 1
    )[0]
    assert marker_block.count("var line hundredMarker") == 2
    assert marker_block.count("var label hundredMarkerLabel") == 1
    assert marker_block.count("line.delete(hundredMarker") == 2
    assert marker_block.count("label.delete(hundredMarkerLabel)") == 1
    assert marker_block.count("line.new(") == 2
    assert marker_block.count("label.new(") == 1
    assert marker_block.count("xloc=xloc.bar_index") == 3
    assert marker_block.count("style=line.style_dotted") == 2
    assert "extend=extend.both" not in marker_block
    assert 'text="100"' in marker_block
    assert "hundredMarkerColor = color.rgb(18, 52, 120)" in marker_block


def test_custom_indicator_100_marker_gap_exceeds_session_marker_gap() -> None:
    source = _source()
    marker_block = source.split("// MOVING 100-CANDLE MARKER", 1)[1].split(
        "// HISTORICAL FOREX START-DATE MARKER", 1
    )[0]
    assert (
        "hundredEffectiveGapFraction = math.max(hundredMarkerGapRangeFraction, "
        "sessionCandleGapRangeFraction + 0.02)"
    ) in marker_block
    assert "syminfo.mintick * 80" in marker_block
    assert "syminfo.mintick * 60" in marker_block
    assert "hundredTargetSessionRecentHigh = sessionMarkerRecentHigh[99]" in marker_block
    assert "hundredTargetSessionRecentLow = sessionMarkerRecentLow[99]" in marker_block
    assert "hundredHistoricalSessionGap + hundredGapClearance" in marker_block
    assert "hundredLowerEndY = hundredTargetLow - hundredCandleGap" in marker_block
    assert "hundredUpperStartY = hundredTargetHigh + hundredCandleGap" in marker_block
    assert "hundredLabelY = hundredUpperEndY + hundredLabelGap" in marker_block

    # A contracted current range must still clear the larger historical
    # session gap that was used when the target candle was current.
    mintick = 0.00001
    current_range = 0.002
    historical_session_range = 0.020
    session_gap = max(historical_session_range * 0.06, mintick * 40)
    clearance = max(current_range * 0.01, mintick * 10)
    hundred_gap = max(
        max(current_range * 0.12, mintick * 80),
        session_gap + clearance,
    )
    assert hundred_gap > session_gap


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
    assert "historicalForexStartMarkerTimestamp = historicalForexStartBarSpansTimestamp ? historicalForexStartTimestamp : time" in historical_block
    assert historical_block.count("xloc=xloc.bar_time") == 2
    assert historical_block.count("x1=historicalForexStartMarkerTimestamp") == 2
    assert historical_block.count("x2=historicalForexStartMarkerTimestamp") == 2
    assert "showSessionLines" not in historical_block
    assert "timeframe." not in historical_block


def test_custom_indicator_historical_forex_marker_falls_back_to_earliest_available_candle() -> None:
    source = _source()
    historical_block = source.split("// HISTORICAL FOREX START-DATE MARKER (UTC)", 1)[1].split(
        "sessionVisibilityKey =", 1
    )[0]
    assert (
        "historicalForexStartFallbackBar = not na(historicalForexStartTimestamp) and "
        "time >= historicalForexStartTimestamp"
    ) in historical_block
    assert (
        "historicalForexStartBar = historicalForexStartBarSpansTimestamp or "
        "historicalForexStartFallbackBar"
    ) in historical_block

    configured_timestamp = datetime(1999, 1, 4, tzinfo=ZoneInfo("UTC"))
    loaded_candles = [
        (datetime(2001, 5, 7, tzinfo=ZoneInfo("UTC")), datetime(2001, 5, 8, tzinfo=ZoneInfo("UTC"))),
        (datetime(2001, 5, 8, tzinfo=ZoneInfo("UTC")), datetime(2001, 5, 9, tzinfo=ZoneInfo("UTC"))),
    ]
    marker_timestamps = []
    marker_drawn = False
    for candle_time, candle_time_close in loaded_candles:
        spans_timestamp = candle_time <= configured_timestamp < candle_time_close
        fallback_bar = candle_time >= configured_timestamp
        if (spans_timestamp or fallback_bar) and not marker_drawn:
            marker_timestamps.append(configured_timestamp if spans_timestamp else candle_time)
            marker_drawn = True

    assert marker_timestamps == [loaded_candles[0][0]]


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
