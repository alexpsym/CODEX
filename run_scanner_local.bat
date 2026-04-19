@echo off
setlocal

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Downloads"
if defined MASTER_ENV_FILE (
  echo Using MASTER_ENV_FILE=%MASTER_ENV_FILE%
) else (
  echo Using MASTER_ENV_DIR=%MASTER_ENV_DIR%
)

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

start "Bybit Scanner" cmd /k "cd /d "%ROOT%" && %PYTHON_EXE% -m bybit_monitor.bybit_altcoin_monitor"
start "OANDA Scanner" cmd /k "cd /d "%ROOT%" && %PYTHON_EXE% -m oanda_monitor.oanda_forex_monitor"
start "Scanner Monitor UI" cmd /k "cd /d "%ROOT%" && %PYTHON_EXE% -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000"

echo Scanner stack launched. Open http://127.0.0.1:8000/merged/monitor
endlocal
