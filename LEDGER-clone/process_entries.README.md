# process_entries.py in plain words

**What it does**
- Opens `ENTRIES.xlsx` and looks at sheet `1` for any new rows under the header.
- Copies those rows into the `MASTER` sheet so the master log stays up to date.
- Sends each row to a separate account workbook (like `REVENUE.xlsx`, `EXPENSES.xlsx`, etc.) using the account name in column C. Sheets are created if they do not already exist.
- Clears the processed rows from sheet `1` after they have been copied everywhere.

**What happens when I run it**
1. Excel opens visibly on your screen (Excel must be installed).
2. The script reads the new transactions from sheet `1` and keeps only rows with real dates.
3. Every row is appended to the `MASTER` sheet and to the matching account workbook.
4. The now-processed rows are wiped from sheet `1` so it is ready for the next import.
5. All the updated workbooks are saved. The helper account books are closed, but `ENTRIES.xlsx` stays open for you to review.
