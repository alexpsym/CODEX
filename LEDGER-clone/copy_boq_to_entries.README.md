# copy_boq_to_entries.py in plain words

**What it does**
- Looks for `ENTRIES.xlsx` in the same folder as the script.
- Opens Excel and finds the newest CSV file whose name includes the word `transformed`.
- Copies the rows from that CSV into sheet `1` of `ENTRIES.xlsx`, starting under the headers.
- Keeps the date and money columns in the right Excel format so formulas keep working.

**What happens when I run it**
1. Excel pops open on your computer (Excel must be installed).
2. The script shows messages in the console about which files it is using.
3. New rows from the CSV appear at the bottom of sheet `1` in `ENTRIES.xlsx`.
4. The workbook gets saved. If it cannot save because the file was already open, a new copy called `ENTRIES_updated.xlsx` (or similar) is created instead and you are told to replace the original file with it.
