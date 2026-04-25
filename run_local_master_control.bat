@echo off
setlocal

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Downloads"
set "MASTER_ENV_FILE=C:\Users\User\Downloads\env.env"
set "APP_PROFILE=local"
set "SCANNER_LOCAL_UI_MODE=1"

if not exist "%MASTER_ENV_FILE%" (
  echo [local-master] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

echo [local-master] APP_PROFILE=%APP_PROFILE%
echo [local-master] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [local-master] MASTER_ENV_FILE=%MASTER_ENV_FILE%

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

start "Local Master Control" cmd /k "cd /d "%ROOT%" && set APP_PROFILE=%APP_PROFILE% && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && echo MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000"

set "START_SCANNERS=%1"
if /I "%START_SCANNERS%"=="scanners" (
  start "Bybit Scanner" cmd /k "cd /d "%ROOT%" && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m bybit_monitor.bybit_altcoin_monitor"
  start "OANDA Scanner" cmd /k "cd /d "%ROOT%" && set SCANNER_LOCAL_UI_MODE=%SCANNER_LOCAL_UI_MODE% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m oanda_monitor.oanda_forex_monitor"
)

start "" "http://127.0.0.1:8000"
echo Local master control launched. Use "run_local_master_control.bat scanners" to also start scanner scripts.

endlocal
