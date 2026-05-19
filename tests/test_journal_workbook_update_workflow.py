from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repo_tracks_trading_journal_not_legacy_master_journal() -> None:
    journal = ROOT / "journal"
    assert (journal / "Trading Journal.xlsx").exists()
    assert not (journal / "Master Journal.xlsx").exists()


def test_extract_latest_preserve_normalizes_legacy_workbook_names() -> None:
    script = (ROOT / "ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "function Resolve-JournalWorkbookCollision" in script
    assert "Master Journal.xlsx" in script
    assert "Trading Journal.xlsx" in script
    assert "Quarantined legacy workbook during" in script
    assert "Migrated legacy workbook during" in script
    assert "Resolve-JournalWorkbookCollision -JournalDir $newJournal -Context \"backup journal preservation\"" in script


def test_extract_latest_never_leaves_both_workbooks_active_after_preserve() -> None:
    script = (ROOT / "ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "if (Test-Path -LiteralPath $canonical -PathType Leaf)" in script
    assert "Master Journal.legacy." in script
    assert "journal_legacy_backups" in script
    assert "outside active journal folder" in script
    assert "Move-Item -LiteralPath $legacy -Destination $legacyBackup -Force -ErrorAction Stop" in script
    assert "Join-Path $journalRoot 'journal_legacy_backups'" in script
