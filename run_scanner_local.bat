@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"
if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py -3"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"

if not defined PYTHON_EXE (
    echo Python was not found. Install Python or create .venv first.
    pause
    exit /b 1
)

echo Launching local scanner windows...
start "Bybit Scanner" cmd /k %PYTHON_EXE% "bybit_monitor\bybit_altcoin_monitor.py"
start "OANDA Scanner" cmd /k %PYTHON_EXE% "oanda_monitor\oanda_forex_monitor.py"
exit /b 0
