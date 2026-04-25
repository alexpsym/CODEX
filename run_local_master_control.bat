@echo off
setlocal

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Downloads"
set "MASTER_ENV_FILE=C:\Users\User\Downloads\env.env"
set "APP_PROFILE=local"
set "AUTOSTART_SCRIPTS=bybit_monitor,oanda_monitor,fxweekend-clone"

if not exist "%MASTER_ENV_FILE%" (
  echo [local-master] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

echo [local-master] APP_PROFILE=%APP_PROFILE%
echo [local-master] AUTOSTART_SCRIPTS=%AUTOSTART_SCRIPTS%
echo [local-master] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [local-master] MASTER_ENV_FILE=%MASTER_ENV_FILE%

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

start "Local Master Control" cmd /k "cd /d "%ROOT%" && set APP_PROFILE=%APP_PROFILE% && set AUTOSTART_SCRIPTS=%AUTOSTART_SCRIPTS% && set MASTER_ENV_DIR=%MASTER_ENV_DIR% && set MASTER_ENV_FILE=%MASTER_ENV_FILE% && echo MASTER_ENV_FILE=%MASTER_ENV_FILE% && %PYTHON_EXE% -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000"

start "" "http://127.0.0.1:8000"
echo Local master control launched with scanner autostart supervision.

endlocal
