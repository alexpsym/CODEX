@echo off
REM Launch the Bybit altcoin monitor Python script.
cd /d "%~dp0"
echo Starting the Bybit altcoin monitor script...
python bybit_altcoin_monitor.py
if errorlevel 1 (
    echo.
    echo The Python script stopped with an error. Read the details above to troubleshoot.
)
echo.
echo Press any key to close this window.
pause >nul
