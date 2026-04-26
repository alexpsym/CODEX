@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Downloads"
set "MASTER_ENV_FILE=C:\Users\User\Downloads\env.env"
set "APP_PROFILE=journal"
set "PYTHONUNBUFFERED=1"

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

if not exist "%MASTER_ENV_FILE%" (
  echo [journal-local] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

if /I "%~1"=="__worker" goto worker

echo [journal-local] APP_PROFILE=%APP_PROFILE%
echo [journal-local] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [journal-local] MASTER_ENV_FILE=%MASTER_ENV_FILE%

start "Local Trading Journal" /D "%ROOT%" cmd /d /v:on /k ""%~f0" __worker"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8010/trading-journal"
echo Local trading journal launch requested.
exit /b 0

:worker
cd /d "%ROOT%" || (
  echo [journal-local] ERROR: failed to cd to %ROOT%
  exit /b 1
)

echo [journal-local] worker started at !DATE! !TIME!
echo [journal-local] APP_PROFILE=!APP_PROFILE!
echo [journal-local] MASTER_ENV_DIR=!MASTER_ENV_DIR!
echo [journal-local] MASTER_ENV_FILE=!MASTER_ENV_FILE!

:restart_master
echo [journal-local] starting uvicorn at !DATE! !TIME!
"%PYTHON_EXE%" -m uvicorn render.master_service:app --host 127.0.0.1 --port 8010
set "EXIT_CODE=!ERRORLEVEL!"
echo [journal-local] uvicorn exited with !EXIT_CODE! at !DATE! !TIME!
echo [journal-local] restarting in 3 seconds. Close this window to stop local trading journal.
timeout /t 3 /nobreak >nul
goto restart_master
