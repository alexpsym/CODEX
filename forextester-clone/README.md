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

