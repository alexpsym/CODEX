# Market Watch forex ATR-percentage feed and desktop window

`MQL5/Experts/MarketWatchATRPercentFeed.mq5` and `atr_percent_window.py` are a separate, display-only ATR tool. They use their own feed file (`MarketWatchATRPercentFeed.json`) and desktop window, independently of both `Trader.mq5` and the separate Market Watch spread tool. Neither component sends or changes trades.

Attach one `MarketWatchATRPercentFeed.mq5` instance to an MT5 chart. It enumerates the symbols selected in Market Watch, identifies Forex with MT5's `SYMBOL_TRADE_CALC_MODE` metadata, and keeps non-Forex selections visible as diagnostics.

The active ATR timeframes are exactly 1m, 5m, 1h and 1D. For each, the feed reports:

`MetaTrader iATR(14) at shift 1 / the same timeframe close at shift 1 * 100`

This is Wilder ATR as a percentage of the last fully closed candle's close. Values are cached per symbol and timeframe, so a valid reading is not recalculated until a new fully closed candle arrives. Missing or invalid history remains unavailable rather than `0%`; bounded timer batches retry loading/error states and retain last-known values as Stale when refresh fails.

The normal desktop script is:

`C:\GPT\CODEX-master\mt5-clone\atr_percent_window.py`

Set the EA's `DesktopWindowScriptPath` to that file and `PythonExecutable` to an existing `python.exe`. Automatic launch requires **Allow DLL imports** because it uses `ShellExecuteW`; the JSON feed itself does not. The standalone window reads the ATR feed's own file freshness/mtime, retains last-known-good data during read failures, and has no spread fields or unified heartbeat dependency.

The window defaults to Top 10 ranked by 1m ATR%, has the four ATR columns, supports timeframe and Top N ranking controls, and uses the symbol as a deterministic tie-break at full numeric precision before display formatting.

To compile manually in the Pepperstone terminal, open `MarketWatchATRPercentFeed.mq5` in MetaEditor and compile it. The generated `.ex5` is a local build artefact and is not committed by this repository workflow.
