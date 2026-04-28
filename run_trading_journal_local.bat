@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Documents\GPT"
set "MASTER_ENV_FILE=C:\Users\User\Documents\GPT\env.env"
set "APP_PROFILE=journal"
set "PYTHONUNBUFFERED=1"
set "DROPBOX_SYNC_ENABLED=0"
set "LOCAL_STATE_ONLY=1"
set "TRADING_JOURNAL_SOURCE=local"
set "TRADING_JOURNAL_ENABLE_LOCAL_IMPORT=1"
set "TRADING_JOURNAL_LOCAL_DIR=C:\Users\User\Documents\TRADING"
set "MASTER_ENV_PROTECTED_KEYS=APP_PROFILE,TRADING_JOURNAL_SOURCE,TRADING_JOURNAL_ENABLE_LOCAL_IMPORT,TRADING_JOURNAL_LOCAL_DIR,DROPBOX_SYNC_ENABLED,LOCAL_STATE_ONLY"
set "CASHFLOW_CACHE_TTL_SECONDS=3600"

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
set "JOURNAL_URL=http://127.0.0.1:8010/trading-journal"
set "JOURNAL_HEALTH_URL=http://127.0.0.1:8010/health"
set "JOURNAL_API_URL=http://127.0.0.1:8010/api/trading-journal"
set "JOURNAL_READY_TIMEOUT_SECONDS=90"
echo [journal-local] waiting for %JOURNAL_HEALTH_URL% ...
set /a READY_WAITED=0

:wait_for_journal_health
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%JOURNAL_HEALTH_URL%' -TimeoutSec 1; if ($r.StatusCode -eq 200 -and (($r.Content | Out-String).Trim() -eq 'ok')) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto health_ready
set /a READY_WAITED+=1
if !READY_WAITED! GEQ %JOURNAL_READY_TIMEOUT_SECONDS% goto journal_not_ready
timeout /t 1 /nobreak >nul
goto wait_for_journal_health

:health_ready
echo [journal-local] health ready after !READY_WAITED! seconds.
set /a API_WAITED=0
:wait_for_journal_api
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%JOURNAL_API_URL%' -TimeoutSec 2; if ($r.StatusCode -eq 200 -or $r.StatusCode -eq 202) { exit 0 } else { exit 1 } } catch { if ($_.Exception.Response -and ($_.Exception.Response.StatusCode.value__ -eq 200 -or $_.Exception.Response.StatusCode.value__ -eq 202)) { exit 0 } exit 1 }"
if not errorlevel 1 goto journal_ready
set /a API_WAITED+=1
if !API_WAITED! GEQ %JOURNAL_READY_TIMEOUT_SECONDS% goto journal_not_ready
timeout /t 1 /nobreak >nul
goto wait_for_journal_api

:journal_ready
echo [journal-local] journal endpoint ready after !API_WAITED! seconds.
start "" "%JOURNAL_URL%"
echo Local trading journal launch requested.
exit /b 0

:journal_not_ready
echo [journal-local] ERROR: journal backend did not become ready after %JOURNAL_READY_TIMEOUT_SECONDS% seconds.
echo [journal-local] Check the "Local Trading Journal" window for startup errors.
echo [journal-local] Browser was not opened to avoid a dead-page / manual-refresh failure.
exit /b 1

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
