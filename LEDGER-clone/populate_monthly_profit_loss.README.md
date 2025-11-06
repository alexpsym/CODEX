# populate_monthly_profit_loss.py in plain words

**What it does**
- Opens `ENTRIES.xlsx` and reads the `MASTER` sheet.
- Adds up every revenue and expense category by month, based on the big list of category names baked into the script.
- Clears the old numbers from the `MONTHLY PROFIT LOSS` sheet and fills it with the fresh monthly totals.

**What happens when I run it**
1. Excel starts in the background (Excel must be installed).
2. The script loads `ENTRIES.xlsx` from the same folder.
3. Progress bars appear in the console while it reads the data and writes the monthly results.
4. The `MONTHLY PROFIT LOSS` sheet gets overwritten with the new totals, then the workbook is saved and closed.
5. A "MONTHLY PROFIT LOSS sheet updated." message shows when it is done.
