@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Downloads"
set "MASTER_ENV_FILE=C:\Users\User\Downloads\env.env"
set "APP_PROFILE=local"
set "AUTOSTART_SCRIPTS=bybit_monitor,oanda_monitor,fxweekend-clone"
set "PYTHONUNBUFFERED=1"

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

if not exist "%MASTER_ENV_FILE%" (
  echo [local-master] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

if /I "%~1"=="__worker" goto worker

echo [local-master] APP_PROFILE=%APP_PROFILE%
echo [local-master] AUTOSTART_SCRIPTS=%AUTOSTART_SCRIPTS%
echo [local-master] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [local-master] MASTER_ENV_FILE=%MASTER_ENV_FILE%

start "Local Master Control" /D "%ROOT%" cmd /d /v:on /k ""%~f0" __worker"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000"
echo Local master control launch requested with scanner autostart supervision.
exit /b 0

:worker
cd /d "%ROOT%" || (
  echo [local-master] ERROR: failed to cd to %ROOT%
  exit /b 1
)

echo [local-master] worker started at !DATE! !TIME!
echo [local-master] APP_PROFILE=!APP_PROFILE!
echo [local-master] AUTOSTART_SCRIPTS=!AUTOSTART_SCRIPTS!
echo [local-master] MASTER_ENV_DIR=!MASTER_ENV_DIR!
echo [local-master] MASTER_ENV_FILE=!MASTER_ENV_FILE!

:restart_master
echo [local-master] starting uvicorn at !DATE! !TIME!
"%PYTHON_EXE%" -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000
set "EXIT_CODE=!ERRORLEVEL!"
echo [local-master] uvicorn exited with !EXIT_CODE! at !DATE! !TIME!
echo [local-master] restarting in 3 seconds. Close this window to stop local master.
timeout /t 3 /nobreak >nul
goto restart_master
