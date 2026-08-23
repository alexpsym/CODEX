# Market Watch forex ATR-percentage feed and desktop window

`MQL5/Experts/MarketWatchATRPercentFeed.mq5` and `atr_percent_window.py` are a separate, display-only companion to the existing Market Watch spread feed. They use their own EA name, JSON file (`MarketWatchATRPercentFeed.json`), desktop script, window title, style names, and timer state, so the ATR and spread windows can run at the same time. Neither component sends or changes trades.

Attach `MarketWatchATRPercentFeed.mq5` to one MT5 chart. The EA enumerates only the symbols currently selected in Market Watch and classifies Forex with MT5's `SYMBOL_TRADE_CALC_MODE` metadata. Non-Forex selections remain visible in the desktop window's diagnostic view. No tick volume, real volume, or centralised-volume claim is used.

For 1m, 5m, 1h, 1D, 1W, and 1Mo, the feed reports:

`MetaTrader iATR(14) at shift 1 / the same timeframe close at shift 1 * 100`

That is Wilder ATR as a percentage of the last fully closed candle's close. ATR length is an EA input. Zero, malformed, non-finite, missing, and not-yet-built history is unavailable rather than `0%`. MT5 may need time to download/build a timeframe; the row shows Loading/N/A and is retried in bounded timer batches. Previously valid readings are retained and marked Stale when a refresh fails.

The normal repository installation uses:

`C:\GPT\CODEX-master\mt5-clone\atr_percent_window.py`

Set the EA's `DesktopWindowScriptPath` input to that file and `PythonExecutable` to an existing `python.exe`. If the repository moves, update the script-path input. Automatic launch also requires **Allow DLL imports** because it uses Windows `ShellExecuteW`; the JSON feed itself does not require desktop launch. A successful `ShellExecuteW` return only means Windows accepted the process request.

The desktop window defaults to Top 10 ranked by 1m ATR%, retains all six columns, allows timeframe and Top N changes, and uses the symbol as the deterministic tie-break. Unavailable and non-Forex rows remain on the Diagnostics / unavailable tab. Values are ranked at full numeric precision before display formatting.

To compile manually in the Pepperstone terminal, open `MarketWatchATRPercentFeed.mq5` in MetaEditor and compile it. The generated `.ex5` is a local build artefact and is not committed by this repository workflow.
