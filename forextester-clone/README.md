# Forex Tester Example Strategy

This repository contains an example MQL4 script for use with Forex Tester 6.
The script allows you to place a market order with ATR-based stop loss and
2R target by pressing the **1** key while in market replay.

## Files

- `ATR2RStrategy.mq4` – MQL4 source code for the strategy
- `BACKUP.bat`, `CLONE.bat`, `CREATE.bat` – Utility scripts unrelated to the strategy

## Usage

1. Open the `ATR2RStrategy.mq4` file in [MetaEditor](https://www.metatrader4.com/en/trading-platform/metaeditor).
2. Compile the script to create an `ATR2RStrategy.ex4` file.
3. Copy the compiled file into Forex Tester 6's `\Files` or `\Indicators` folder.
4. In Forex Tester, load the script and start market replay. Pause the replay
   at the desired price level.
5. Press **1** on your keyboard. A market order of `0.01` lot will be sent,
   with stop loss based on the current ATR and take profit set to twice that
   distance (2R).
6. Unpause replay to watch the trade play out.

This is a simple example and may require adjustments to match your trading
style or risk management rules.

## C++ DLL Version

A simplified C++ implementation is provided in `ATR2RStrategy.cpp` and `ATR2RStrategy.h`. You can compile these files into a Windows DLL using a C++ compiler. On Linux with `g++` installed, run:

```
g++ -shared -fPIC -o ATR2RStrategy.dll ATR2RStrategy.cpp
```

This command creates `ATR2RStrategy.dll`. On Windows, you can build the DLL in Visual Studio by creating a new DLL project and adding the two source files.

The DLL exports two functions:

- `GetTradeSignal` – checks if the **1** key was pressed and calculates stop loss and take profit values.
- `GetLots` – returns the fixed lot size (`0.01`).

You can import these functions from MQL4 using the `import` directive if you wish to call the DLL from a script.

## CSV Converter for Forex Tester 6

If your data uses ISO timestamps like `2000-05-30 17:27:00-05:00`, Forex Tester 6
often fails to import it directly. Use the included converter script to produce
a format FT6 imports reliably.

### Convert to FT6 format

```
python convert_to_ft6.py eurusd.csv eurusd_ft6.csv
```

The output file contains:

```
YYYYMMDD,HHMMSS,Open,High,Low,Close,Volume
```

By default the converter normalizes timestamps to UTC when a timezone offset is
present. To keep the original timezone, pass `--no-utc`.

### Resample to perfect M1 candles

If your data is irregular (missing minutes or tick-like), you can resample into
clean M1 bars and fill missing minutes with flat candles using the previous
close:

```
python resample_to_ft6_m1.py eurusd.csv eurusd_ft6_M1.csv
```

This script uses the same FT6 output format and converts timestamps to UTC by
default. Use `--no-utc` to preserve the original timezone.
