from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OBSOLETE_FILES = [
    "TJR/README.txt",
    "TJR/make_trading_journal_replica.py",
    "TJR/setup-trading-journal-replica.sh",
    "TJR/COPY_PASTE_INTO_TERMUX.txt",
    "LaunchTradingJournalBrave.sh",
    "InstallTradingJournalBraveShortcut.sh",
]

ACTIVE_FILES = [
    "run_trading_journal_local.bat",
    "run_local_master_control.bat",
    "render/master_service.py",
    "render/static/trading_journal.js",
    "tools/master_journal_workbook.py",
]

STALE_STRINGS = [
    "TradingJournal_Android_Replica.xlsx",
    "setup-trading-journal-replica",
    "LaunchTradingJournalBrave",
    "InstallTradingJournalBraveShortcut",
    "CODEX-master (4)",
]


def test_obsolete_android_replica_files_removed() -> None:
    for rel in OBSOLETE_FILES:
        assert not (ROOT / rel).exists(), f"obsolete file still present: {rel}"


def test_tjr_directory_removed_or_empty() -> None:
    tjr = ROOT / "TJR"
    if not tjr.exists():
        return
    leftovers = [child.name for child in tjr.iterdir() if child.name != ".ignore"]
    assert not leftovers, f"unexpected files remain in TJR: {leftovers}"


def test_active_paths_do_not_reference_obsolete_android_replica_strings() -> None:
    corpus = "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in ACTIVE_FILES)
    for stale in STALE_STRINGS:
        assert stale not in corpus


def test_run_trading_journal_local_opens_master_journal_directly() -> None:
    journal = (ROOT / "run_trading_journal_local.bat").read_text(encoding="utf-8")
    assert "Trading Journal.xlsx" in journal
    assert "start \"\" \"%JOURNAL%\"" in journal
    assert "uvicorn render.master_service:app" not in journal
    assert "/trading-journal" not in journal
