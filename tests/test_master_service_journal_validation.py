from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_trading_journal_symbols_duration_validation_accepts_text_durations():
    source = (ROOT / "render" / "master_service.py").read_text(encoding="utf-8")
    assert "_symbols_duration_seconds" in source
    assert "_parse_duration_text(value)" in source
    assert "_duration_ddhhmmss_cell_to_seconds(value)" in source
    assert "Trading Journal validation failed: SYMBOLS duration columns are blank despite duration stats." in source
    assert "Trading Journal validation failed: SYMBOLS duration order must satisfy Shortest <= Avg <= Longest." in source
    assert "Instrument Averages duration columns are blank despite duration stats." not in source
