TradingJournalExcelReplica32bit

Purpose:
Creates a 32-bit Android/Termux-safe Excel workbook replica of the Trading Journal from the Excel files inside:
/storage/emulated/0/Download/CODEX-master (4)/CODEX-master/journal

This does not run the FastAPI/web Trading Journal and does not use pandas.
It generates:
/storage/emulated/0/Download/CODEX-master (4)/CODEX-master/journal/TradingJournal_Android_Replica.xlsx

Install:
1. Extract this ZIP to /Internal storage/Download/TradingJournalExcelReplica32bit/
2. Open COPY_PASTE_INTO_TERMUX.txt
3. Select All, Copy, Paste into Termux.
4. Refresh the Termux widget.
5. Tap Generate Journal Replica.

Widget shortcuts created:
- Generate Journal Replica
- Open Journal Replica

Sheets generated:
- Dashboard
- All Trades
- Instrument Averages
- PL Calendar
- Equity Curve
- Diagnostics

Limits:
This is an Excel replica, not the same local web app. It approximates the journal layout and statistics using pure Python so it can run on 32-bit ARM Termux.
