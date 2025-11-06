# Bybit History Downloader

This project provides a simple script to download your Bybit trade history and save it as a CSV file.

## Usage

1. Set your Bybit API credentials as environment variables before running the script. On Windows you can do this in a command prompt:
   ```cmd
   set BYBIT_API_KEY=your_api_key
   set BYBIT_API_SECRET=your_api_secret
   ```
2. Run `python fetch_history.py` and follow the prompts. The program asks for
   the product type, start and end dates, and an optional symbol. Times in the
   CSV are converted to Brisbane time (UTC+10) and the layout matches what
   Bybit produces. The Bybit API only provides the last two years of
   data, so older dates are adjusted automatically.

3. After downloading, the script saves a file named like
   `Bybit-UM-USDTPerp-TradeHistory-1747058400-1749736799.csv` in the same
  directory. Times in the CSV are converted to Brisbane time (UTC+10). The
  layout matches the raw export that Bybit returns.

### Graphical Interface

Run `python app.py` to start a local web server. Your browser will open a dark
themed page where you can generate trade and balance history without typing
command-line options. Each form now includes optional start and end date pickers
so you can request an exact window; leave them blank to fall back to the quick
range presets. The **All Time** option in this interface downloads the last two
years of data.

### Reconstructing Historical Balances Offline

If your exported balance history shows a flat line you can rebuild it locally
with:

```bash
python reconstruct_balances.py \
    --trade-file Bybit-UM-USDTPerp-TradeHistory-1688688000-1751760000.csv \
    --ledger-file your_usdt_transaction_log.csv \
    --balance-file your_usdt_balance_history.csv \
    --current-balance 238.821415 \
    --output corrected_balance_history.csv
```

Replace the file names with the exports you downloaded. Supplying more than
one ``--trade-file`` or ``--ledger-file`` is fine; the script will read them
all. The utility adds together:

* Realised profit and loss from the trade CSV (it uses Bybit's own "Realized
  P&L" column when it exists or recalculates it when it does not).
* Trading fees and funding payments using the exact sign recorded in the CSV
  so charges reduce the balance and rebates increase it.
* Deposits, withdrawals and other cash movements from the transaction log
  (``--ledger-file``).

It then walks backwards from the supplied current balance to fill in the
``Period``/``Balance`` columns in the output file.

If you omit ``--balance-file`` you can now skip ``--start-date`` and
``--end-date``—the program will infer the earliest and latest dates from the
trade and ledger history you provide. You still need to pass the
``--current-balance`` so it knows the closing balance for the final day.

## Testing

To run the lint check and tests on Linux:
```bash
pylint fetch_history.py
pytest
```

`pylint` should report a score of 10/10.
