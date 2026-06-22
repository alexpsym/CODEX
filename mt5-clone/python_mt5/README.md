# Python MT5 Scripts

These scripts are external Python tools that talk to a locally running MetaTrader 5 terminal through the `MetaTrader5` Python package.

Important differences from the native MQL5 EAs:

- Python MT5 runs externally. It is not an EA attached to an MT5 chart.
- Python cannot read arbitrary chart trendline objects the same way `Trader.mq5` can. Use `trader_py.py --strategy manual-limit --entry-price ...` for the Python equivalent of trendline/manual limit execution.
- Existing files under `mt5-clone/MQL5/Experts/` remain available for native MT5 Strategy Tester and chart-attached EA use.
- Python MT5 requires a local Windows MT5 terminal open, connected, and logged in. Pepperstone symbols may include broker suffixes, so confirm the symbol name shown in Market Watch.

## Files

- `mt5_common.py` contains lazy MT5 import, account/terminal checks, risk sizing, SL/TP distance helpers, AutoTP NetRR, pending-order cancellation, and `order_send()` validation.
- `trader_py.py` mirrors the external behavior of `Trader.mq5` where practical: standard/manual limit orders, EMA bounce market orders, one-position guard, pending cancellation by magic/comment, risk sizing, SL/TP points, and AutoTP NetRR.
- `backtest_py.py` mirrors the Backtest pullback research logic where practical: `copy_rates_range()`, EMA/ATR calculations, blackout and rollover windows, and CSV/summary output. It is not the MT5 Strategy Tester.

## Examples

```powershell
python mt5-clone\python_mt5\trader_py.py --symbol EURUSD --strategy manual-limit --side buy --entry-price 1.08000
python mt5-clone\python_mt5\trader_py.py --symbol EURUSD --strategy ema-bounce
python mt5-clone\python_mt5\backtest_py.py --symbol EURUSD --timeframe M5 --start 2026-01-01T00:00:00Z --end 2026-02-01T00:00:00Z
```
