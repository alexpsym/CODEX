from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = "/storage/emulated/0/Download/CODEX-master/CODEX-master"
REQ_J = REQ + "/journal"
FILES=["TJR/README.txt","TJR/COPY_PASTE_INTO_TERMUX.txt","TJR/setup-trading-journal-replica.sh","LaunchTradingJournalBrave.sh","InstallTradingJournalBraveShortcut.sh","TJR/make_trading_journal_replica.py"]

def test_required_repo_root_paths_present():
    for rel in FILES:
        text=(ROOT/rel).read_text(encoding="utf-8")
        assert REQ in text
    assert REQ_J in (ROOT/"TJR/make_trading_journal_replica.py").read_text(encoding="utf-8")

def test_stale_paths_blocked_only():
    corpus="\n".join((ROOT/r).read_text(encoding="utf-8") for r in FILES)
    for bad in ["CODEX-master (4)","TradingJournal_Android_Replica.xlsx","/trading-journal"]:
        assert bad not in corpus
