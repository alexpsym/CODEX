Local Transaction Extractor v7 no-venv

No OpenAI API. No cloud AI API. No virtual environment.

Run Extract_Transactions_Local_OCR.bat. Paste image/PDF URLs or local file paths, one per line. Press Enter on a blank line.

New in v7:
- Handles itemised receipt/detail screenshots as line-item rows.
- Prompts for an optional fallback DATE for screenshots with no visible date. Use dd/mm/yyyy. Leave blank to keep DATE blank and flag those rows for review.
- Removes exact duplicate rows caused by overlapping screenshots.

Output workbook columns are exactly:
DATE | ACCOUNT_TYPE | ACCOUNT | DESCRIPTION | DEBIT | CREDIT | NOTES | NOTES

Positive receipt/item amounts are written to CREDIT. Negative receipt discounts/reductions are written to DEBIT as positive values.
