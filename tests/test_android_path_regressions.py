from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launch_trading_journal_brave_prefers_new_repo_path() -> None:
    text = (ROOT / "LaunchTradingJournalBrave.sh").read_text(encoding="utf-8")
    assert "/storage/emulated/0/Download/CODEX-master/CODEX-master" in text
    assert "CODEX-master (4)" not in text


def test_tjr_setup_shortcuts_prefer_new_repo_path() -> None:
    text = (ROOT / "TJR" / "setup-trading-journal-replica.sh").read_text(encoding="utf-8")
    assert "/storage/emulated/0/Download/CODEX-master/CODEX-master" in text
    assert "CODEX-master (4)" not in text


def test_tjr_copy_paste_uses_repo_tjr_path_and_no_legacy_folder() -> None:
    text = (ROOT / "TJR" / "COPY_PASTE_INTO_TERMUX.txt").read_text(encoding="utf-8")
    assert "CODEX-master/CODEX-master/TJR/setup-trading-journal-replica.sh" in text
    assert "TradingJournalExcelReplica32bit" not in text
