from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_launchers_are_deprecated_and_point_to_master_journal() -> None:
    launch = (ROOT / "LaunchTradingJournalBrave.sh").read_text(encoding="utf-8")
    install = (ROOT / "InstallTradingJournalBraveShortcut.sh").read_text(encoding="utf-8")
    setup = (ROOT / "TJR" / "setup-trading-journal-replica.sh").read_text(encoding="utf-8")
    copy = (ROOT / "TJR" / "COPY_PASTE_INTO_TERMUX.txt").read_text(encoding="utf-8")
    readme = (ROOT / "TJR" / "README.txt").read_text(encoding="utf-8")

    for text in (launch, install, setup, copy, readme):
        assert "Master Journal.xlsx" in text

    assert "deprecated" in launch.lower()
    assert "deprecated" in install.lower()
    assert "deprecated" in setup.lower()
    assert "termux" in copy.lower() and "no longer" in copy.lower()


def test_android_docs_do_not_require_legacy_browser_or_termux_workflow() -> None:
    corpus = "\n".join(
        [
            (ROOT / "LaunchTradingJournalBrave.sh").read_text(encoding="utf-8"),
            (ROOT / "InstallTradingJournalBraveShortcut.sh").read_text(encoding="utf-8"),
            (ROOT / "TJR" / "setup-trading-journal-replica.sh").read_text(encoding="utf-8"),
            (ROOT / "TJR" / "COPY_PASTE_INTO_TERMUX.txt").read_text(encoding="utf-8"),
            (ROOT / "TJR" / "README.txt").read_text(encoding="utf-8"),
        ]
    )
    bad_terms = [
        "/trading-journal",
        "Generate Journal Replica",
        "TradingJournal_Android_Replica.xlsx",
        "Termux shortcut",
        "CODEX-master/CODEX-master/TJR/setup-trading-journal-replica.sh",
    ]
    for t in bad_terms:
        assert t not in corpus
