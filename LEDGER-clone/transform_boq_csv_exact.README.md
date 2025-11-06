# transform_boq_csv_exact.py in plain words

**What it does**
- Takes a BOQ bank export CSV (you can drag one into this folder).
- Cleans it up: removes the `Balance` column, drops rows marked `PENDING`, sorts by description, and swaps the Debit/Credit numbers into the layout used by your accounting sheets.
- Duplicates the data block so you get both the asset and liability versions, with a blank line between them, matching the format expected by `copy_boq_to_entries.py`.
- Saves the finished file as `boq_may2025_transformed.csv` unless you choose another output name.

**What happens when I run it**
1. If you do not pass a file name, the script tries to find exactly one CSV in the folder. If there are zero or more than one, it stops and tells you what to fix.
2. The CSV is loaded with pandas (so the `pandas` package must be installed).
3. The script performs all the clean-up steps and stitches the final table together with an extra blank header line.
4. A new CSV file is written with the transformed data, and the console prints the output file name.
