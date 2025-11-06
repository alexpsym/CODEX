# earnings_report.py in plain words

**What it does**
- Asks you for a start and end date like `1/7/25-31/7/25`.
- Opens `ENTRIES.xlsx` and reads the `MASTER` sheet.
- Keeps only the revenue (`REV`) and expense (`EXP`) rows that fall within your date range.
- Copies those rows into a fresh workbook based on the template in sheet `1` and saves it as `EARNINGS <MONTH> <YEAR>.xlsx`.

**What happens when I run it**
1. The program asks you to type a date range.
2. Excel starts in the background (Excel must be installed) and the script reads the data.
3. A new workbook is created with just the filtered revenue and expense transactions.
4. The new workbook is saved in the same folder, and the script tells you the file name.
