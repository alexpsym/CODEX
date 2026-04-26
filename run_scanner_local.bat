@echo off
setlocal

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Documents\GPT"
set "MASTER_ENV_FILE=C:\Users\User\Documents\GPT\env.env"
set "SCANNER_LOCAL_UI_MODE=1"

if not exist "%MASTER_ENV_FILE%" (
  echo [scanner-local] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

echo [scanner-local] SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE%
echo [scanner-local] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [scanner-local] MASTER_ENV_FILE=%MASTER_ENV_FILE%

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

start "Bybit Scanner" cmd /k "cd /d "%ROOT%" && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && echo MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m bybit_monitor.bybit_altcoin_monitor"
start "OANDA Scanner" cmd /k "cd /d "%ROOT%" && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && echo MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m oanda_monitor.oanda_forex_monitor"
start "Scanner Monitor UI (optional)" cmd /k "cd /d "%ROOT%" && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && echo MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000"

echo Scanner stack launched. Open http://127.0.0.1:8000/merged/monitor
endlocal
