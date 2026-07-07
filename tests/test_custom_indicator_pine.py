from pathlib import Path


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
    assert "color=color.white" in source
    assert "size=size.tiny" not in source
    assert "size=size.normal" not in source
    assert "size=size.huge" in source
    assert "sessionMarkerOffsetMultiplier = 1.875" in source


def test_custom_indicator_session_rendering_uses_arrow_markers_not_extend_lines() -> None:
    source = _source()
    session_block = source.split("// TRADING SESSION OPEN/CLOSE LINES", 1)[1]
    assert "drawSessionMarker" in session_block
    assert "label.style_arrowdown" in session_block
    assert "label.style_arrowup" in session_block
    assert "markerY = isOpen ? high + candleRange * sessionMarkerOffsetMultiplier : low - candleRange * sessionMarkerOffsetMultiplier" in session_block
    assert "textalign=text.align_center" in session_block
    assert "extend=extend.both" not in session_block
    assert "line.new" not in session_block


def test_custom_indicator_funding_and_option_expiry_code_remains_unchanged() -> None:
    source = _source()
    assert 'text="Funding"' in source
    assert 'text="OP EX"' in source
    assert "fundingLine = line.new" in source
    assert "expiryLine := line.new" in source
    assert "extend=extend.both" in source
