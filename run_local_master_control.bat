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
set "TRADING_JOURNAL_BROKER_REFRESH_ENABLED=0"
set "TRADING_JOURNAL_BYBIT_DEMO_BALANCE_ANCHOR_ENABLED=1"
set "TRADING_JOURNAL_LOCAL_DIR=%ROOT%journal"
set "MASTER_ENV_PROTECTED_KEYS=APP_PROFILE,TRADING_JOURNAL_SOURCE,TRADING_JOURNAL_ENABLE_LOCAL_IMPORT,TRADING_JOURNAL_BROKER_REFRESH_ENABLED,TRADING_JOURNAL_BYBIT_DEMO_BALANCE_ANCHOR_ENABLED,TRADING_JOURNAL_LOCAL_DIR,DROPBOX_SYNC_ENABLED,LOCAL_STATE_ONLY"
echo [local-master] TRADING_JOURNAL_LOCAL_DIR=%TRADING_JOURNAL_LOCAL_DIR%
if not exist "%TRADING_JOURNAL_LOCAL_DIR%\" (
  echo [local-master] ERROR: TRADING_JOURNAL_LOCAL_DIR not found at %TRADING_JOURNAL_LOCAL_DIR%
  exit /b 1
)
if not exist "%TRADING_JOURNAL_LOCAL_DIR%\account_cashflows.xlsx" (
  echo [local-master] ERROR: required workbook missing: %TRADING_JOURNAL_LOCAL_DIR%\account_cashflows.xlsx
  exit /b 1
)
set "CASHFLOW_CACHE_TTL_SECONDS=3600"
if not defined DROPBOX_SYNC_ENABLED set "DROPBOX_SYNC_ENABLED=1"
if not defined DROPBOX_BACKUP_PATH set "DROPBOX_BACKUP_PATH=/codex/master_control_backup.json"
if not defined DROPBOX_STATE_ROOT set "DROPBOX_STATE_ROOT=/codex/tradingtools_state"
if not defined LOCAL_STATE_ONLY set "LOCAL_STATE_ONLY=0"

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

if not exist "%MASTER_ENV_FILE%" (
  echo [local-master] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)

call :load_master_env_vars

if /I "%~1"=="__worker" goto worker

echo [local-master] APP_PROFILE=%APP_PROFILE%
echo [local-master] AUTOSTART_SCRIPTS=%AUTOSTART_SCRIPTS%
echo [local-master] MASTER_ENV_DIR=%MASTER_ENV_DIR%
echo [local-master] MASTER_ENV_FILE=%MASTER_ENV_FILE%
echo [local-master] CWD=%CD%
for %%I in ("%RENDER_CALCULATOR_BASE_URL%") do set "RCB_HOST=%%~nxI"
if defined RENDER_CALCULATOR_BASE_URL (
  echo [local-master] RENDER_CALCULATOR_BASE_URL=present host=!RCB_HOST!
  echo [local-master] Calculator webhook local-to-Render mode: enabled
) else (
  echo [local-master] RENDER_CALCULATOR_BASE_URL=missing
  echo [local-master] Calculator webhook local-to-Render mode: disabled
)
echo [local-master] DROPBOX_SYNC_ENABLED=%DROPBOX_SYNC_ENABLED%
echo [local-master] DROPBOX_BACKUP_PATH=%DROPBOX_BACKUP_PATH%
echo [local-master] DROPBOX_STATE_ROOT=%DROPBOX_STATE_ROOT%
echo [local-master] LOCAL_STATE_ONLY=%LOCAL_STATE_ONLY%
echo [local-master] User state source: Dropbox
if /I "%LOCAL_STATE_ONLY%"=="1" echo [local-master] WARNING: Local-only mode enabled. Repo replacement can lose state.

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
call "%ROOT%tools\open_edge_url.bat" "%MASTER_URL%"
if errorlevel 1 (
  echo [local-master] ERROR: failed to open Microsoft Edge for %MASTER_URL%.
  exit /b 1
)
echo Local master control launch requested with scanner autostart supervision.
exit /b 0

:master_not_ready
echo [local-master] ERROR: dashboard was not ready after %MASTER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Check the "Local Master Control" window for startup errors.
echo [local-master] Browser was not opened to avoid a dead-page / manual-refresh failure.
exit /b 1

:scanner_not_ready
echo [local-master] ERROR: scanner did not become ready after %SCANNER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Alerts startup may have failed. Check the "Local Master Control" window/logs for bybit_monitor/oanda_monitor errors.
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
echo [local-master] CWD=!CD!
for %%I in ("!RENDER_CALCULATOR_BASE_URL!") do set "RCB_HOST=%%~nxI"
if defined RENDER_CALCULATOR_BASE_URL (
  echo [local-master] RENDER_CALCULATOR_BASE_URL=present host=!RCB_HOST!
  echo [local-master] Calculator webhook local-to-Render mode: enabled
) else (
  echo [local-master] RENDER_CALCULATOR_BASE_URL=missing
  echo [local-master] Calculator webhook local-to-Render mode: disabled
)
echo [local-master] DROPBOX_SYNC_ENABLED=!DROPBOX_SYNC_ENABLED!
echo [local-master] DROPBOX_BACKUP_PATH=!DROPBOX_BACKUP_PATH!
echo [local-master] DROPBOX_STATE_ROOT=!DROPBOX_STATE_ROOT!
echo [local-master] LOCAL_STATE_ONLY=!LOCAL_STATE_ONLY!
echo [local-master] User state source: Dropbox
if /I "!LOCAL_STATE_ONLY!"=="1" echo [local-master] WARNING: Local-only mode enabled. Repo replacement can lose state.

:restart_master
echo [local-master] starting uvicorn at !DATE! !TIME!
"%PYTHON_EXE%" -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000
set "EXIT_CODE=!ERRORLEVEL!"
echo [local-master] uvicorn exited with !EXIT_CODE! at !DATE! !TIME!
echo [local-master] restarting in 3 seconds. Close this window to stop local master.
timeout /t 3 /nobreak >nul
goto restart_master

:load_master_env_vars
set "ENV_LOAD_ERROR="
set "ENV_PARSE_HELPER=%ROOT%tools\windows_launchers\parse_master_env.ps1"
set "ENV_PARSE_OUTPUT=%TEMP%\local_master_env_%RANDOM%_%RANDOM%.txt"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ENV_PARSE_HELPER%" -EnvFilePath "%MASTER_ENV_FILE%" -OutputPath "%ENV_PARSE_OUTPUT%" >nul 2>nul
if errorlevel 1 (
  set "ENV_LOAD_ERROR=1"
  echo [local-master] WARNING: failed to parse %MASTER_ENV_FILE% for launcher preflight variables.
) else (
  if exist "%ENV_PARSE_OUTPUT%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_PARSE_OUTPUT%") do (
      if not "%%~A"=="" set "%%A=%%B"
    )
  )
)
if exist "%ENV_PARSE_OUTPUT%" del /q "%ENV_PARSE_OUTPUT%" >nul 2>nul
goto :eof
