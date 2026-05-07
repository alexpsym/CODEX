TJR (Trading Journal Replica for Phone 2)

Purpose:
Creates a 32-bit Android/Termux-safe Excel workbook replica of the Trading Journal from the Excel files inside:
/Internal storage/Download/CODEX-master/CODEX-master/journal

This is for the replica phone mode only. It does not run the FastAPI/web Trading Journal app.
It generates:
/Internal storage/Download/CODEX-master/CODEX-master/journal/TradingJournal_Android_Replica.xlsx

Install:
1. Ensure this folder exists: /Internal storage/Download/CODEX-master/CODEX-master/TJR
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
