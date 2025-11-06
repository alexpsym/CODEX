# Bybit Crypto Scanner

This project scans Bybit USDT perpetual markets.
Results are written to multiple spreadsheets:

* ``Crypto_Volume.xlsx`` containing volume change statistics
* ``Funding_Rates.xlsx`` with the latest funding rate for each pair
* ``Open_Interest.xlsx`` showing percent changes in open interest across several timeframes, now including 1 day, 1 week and 1 month intervals
* ``Correlation.xlsx`` summarising how closely each pair moves with BTCUSDT

The funding and open interest sheets omit the ``24h USD Volume`` column, but rows remain sorted using this metric.

All rows in every sheet are ordered by ``24h USD Volume`` in descending order.

## Installation

Install the required Python packages using `pip`:

```bash
pip install -r requirements.txt
```

## Running Tests

After installing the dependencies, run `run_checks.py` to execute
both pylint and pytest with progress bars:

```bash
python run_checks.py
```

## Running the Scan

Once the lint checks and tests pass, execute the scanner directly to fetch
market data and export `Crypto_Volume.xlsx`:

```bash
python scan.py
```

HTML versions of each table are written to the `html` folder in the project
root. If the scanner is run on Windows, these pages are opened in Microsoft
Edge automatically. Each page opens only once per session and includes a
built-in refresh that updates it when new scans overwrite the file.
The correlation table launches in Edge as soon as its Excel file is written.
Each page now uses a dark theme and includes buttons in the top-left corner for
quickly switching between the different tables. All buttons sit in a single
horizontal row aligned to the far left. They let you sort by any timeframe
column and toggle between largest-to-smallest and smallest-to-largest with each
click. When a table includes the ``24h USD Volume`` column, an extra button
appears so you can sort all rows by volume as well.

## Grouping Debug Logs

Use `group_logs.py` to organise `scanlog.txt` entries so that all lines for
each symbol appear together:

```bash
python group_logs.py /path/to/scanlog.txt
```

The script writes a new file with `_grouped` appended to the original name.

## Log Files

All logs are written to the `logs` directory in the project root:

```
<project_root>/logs
```

`scan.py` writes `scanlog.txt` here, while `run_checks.py` creates
`pylint.log` and `pytest.log` in the same directory.

