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
    assert 'text="Funding"' in source
    assert 'text="OP EX"' in source
    assert 'sessionName + (isOpen ? " Open" : " Close")' in source
    assert "max_labels_count=500" in source
