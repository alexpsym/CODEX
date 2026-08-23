# Market Watch spread feed desktop window

`MarketWatchSpreadPercentFeed.mq5` writes the existing shared JSON feed and can launch the existing desktop window. The normal repository installation uses:

`C:\GPT\CODEX-master\mt5-clone\spread_percent_window.py`

Set the EA's `DesktopWindowScriptPath` input to that file and set `PythonExecutable` to an existing `python.exe`. Enable **Allow DLL imports** for the EA if automatic launch is enabled. The feed itself remains display-only and does not send trades.

If the repository is moved, update `DesktopWindowScriptPath` when applying the EA inputs. The EA validates both configured files before requesting a launch and reports which input is missing or invalid; a successful `ShellExecuteW` result means Windows accepted the launch request, not that the Python window has already completed startup.
