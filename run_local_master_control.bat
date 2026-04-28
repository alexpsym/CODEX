@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "MASTER_ENV_DIR=C:\Users\User\Documents\GPT"
set "MASTER_ENV_FILE=C:\Users\User\Documents\GPT\env.env"
set "APP_PROFILE=local"
set "AUTOSTART_SCRIPTS=bybit_monitor,oanda_monitor,fxweekend-clone"
set "PYTHONUNBUFFERED=1"
set "TRADING_JOURNAL_SOURCE=local"
set "TRADING_JOURNAL_ENABLE_LOCAL_IMPORT=1"
set "TRADING_JOURNAL_LOCAL_DIR=C:\Users\User\Documents\TRADING"
set "CASHFLOW_CACHE_TTL_SECONDS=3600"

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
set "MASTER_URL=http://127.0.0.1:8000"
set "MASTER_HEALTH_URL=http://127.0.0.1:8000/health"
set "MASTER_SCRIPTS_URL=http://127.0.0.1:8000/scripts"
set "MASTER_READY_TIMEOUT_SECONDS=60"
set "SCANNER_READY_TIMEOUT_SECONDS=90"
echo [local-master] waiting for %MASTER_HEALTH_URL% ...
set /a READY_WAITED=0

:wait_for_master_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%MASTER_HEALTH_URL%' -TimeoutSec 1; if ($r.StatusCode -eq 200 -and (($r.Content | Out-String).Trim() -eq 'ok')) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto master_ready

set /a READY_WAITED+=1
if !READY_WAITED! GEQ %MASTER_READY_TIMEOUT_SECONDS% goto master_not_ready
timeout /t 1 /nobreak >nul
goto wait_for_master_ready

:master_ready
echo [local-master] dashboard health ready after !READY_WAITED! seconds.
echo [local-master] waiting for scanner readiness via %MASTER_SCRIPTS_URL% ...
set /a SCANNER_READY_WAITED=0

:wait_for_scanner_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; try { $r = Invoke-RestMethod -Uri '%MASTER_SCRIPTS_URL%' -TimeoutSec 2; if ($r -is [System.Array]) { $monitor = $r | Where-Object { $_.name -eq 'monitor' } | Select-Object -First 1 } else { $monitor = $null }; if ($null -ne $monitor -and $monitor.running -eq $true) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto scanner_ready

set /a SCANNER_READY_WAITED+=1
if !SCANNER_READY_WAITED! GEQ %SCANNER_READY_TIMEOUT_SECONDS% goto scanner_not_ready
timeout /t 1 /nobreak >nul
goto wait_for_scanner_ready

:scanner_ready
echo [local-master] scanner ready after !SCANNER_READY_WAITED! seconds.
start "" "%MASTER_URL%"
echo Local master control launch requested with scanner autostart supervision.
exit /b 0

:master_not_ready
echo [local-master] ERROR: dashboard was not ready after %MASTER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Check the "Local Master Control" window for startup errors.
echo [local-master] Browser was not opened to avoid a dead-page / manual-refresh failure.
exit /b 1

:scanner_not_ready
echo [local-master] ERROR: scanner did not become ready after %SCANNER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Scanner startup may have failed. Check the "Local Master Control" window/logs for bybit_monitor/oanda_monitor errors.
echo [local-master] Browser was not opened to avoid showing a misleading dashboard state.
exit /b 1

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
