@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "LOG_DIR=%ROOT%logs"
if not exist "%LOG_DIR%\" mkdir "%LOG_DIR%" >nul 2>nul
if not defined LOCAL_MASTER_WORKER_LOG set "LOCAL_MASTER_WORKER_LOG=%LOG_DIR%\LocalTradingTools-worker-latest.log"
set "MASTER_ENV_DIR=C:\GPT"
set "MASTER_ENV_FILE=C:\GPT\env.env"
set "APP_PROFILE=local"
set "AUTOSTART_SCRIPTS=bybit_monitor,oanda_monitor,fxweekend-clone"
set "PYTHONUNBUFFERED=1"
set "TRADING_JOURNAL_SOURCE=master_journal"
set "TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE=1"
set "TRADING_JOURNAL_ENABLE_LOCAL_IMPORT=0"
set "TRADING_JOURNAL_BROKER_REFRESH_ENABLED=0"
set "ENABLE_BYBIT_FILL_POLL=0"
set "ENABLE_OANDA_FILL_POLL=0"
set "TRADING_JOURNAL_SYNC_CALCULATOR_TRADES_ON_MANUAL=1"
set "TRADING_JOURNAL_BYBIT_DEMO_BALANCE_ANCHOR_ENABLED=1"
set "TRADING_JOURNAL_LOCAL_DIR=%ROOT%journal"
set "MASTER_ENV_PROTECTED_KEYS=APP_PROFILE,TRADING_JOURNAL_SOURCE,TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE,TRADING_JOURNAL_ENABLE_LOCAL_IMPORT,TRADING_JOURNAL_BROKER_REFRESH_ENABLED,TRADING_JOURNAL_BYBIT_DEMO_BALANCE_ANCHOR_ENABLED,TRADING_JOURNAL_LOCAL_DIR,TRADING_JOURNAL_GITHUB_SYNC_ENABLED,TRADING_JOURNAL_GITHUB_SYNC_REMOTE,TRADING_JOURNAL_GITHUB_SYNC_BRANCH,TRADING_JOURNAL_GITHUB_SYNC_INCLUDE_SOURCES,ENABLE_BYBIT_FILL_POLL,ENABLE_OANDA_FILL_POLL,TRADING_JOURNAL_SYNC_CALCULATOR_TRADES_ON_MANUAL,DROPBOX_SYNC_ENABLED,LOCAL_STATE_ONLY,STATE_BACKUP_LOCAL_PATH,BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH"
echo [local-master] TRADING_JOURNAL_LOCAL_DIR=%TRADING_JOURNAL_LOCAL_DIR%
if not exist "%TRADING_JOURNAL_LOCAL_DIR%\" (
  echo [local-master] ERROR: TRADING_JOURNAL_LOCAL_DIR not found at %TRADING_JOURNAL_LOCAL_DIR%
  exit /b 1
)
set "CANONICAL_JOURNAL=%TRADING_JOURNAL_LOCAL_DIR%\Trading Journal.xlsx"
set "LEGACY_JOURNAL=%TRADING_JOURNAL_LOCAL_DIR%\Master Journal.xlsx"
if exist "%CANONICAL_JOURNAL%" (
  if exist "%LEGACY_JOURNAL%" (
    echo [local-master] ERROR: ambiguous workbook names found: %CANONICAL_JOURNAL% and %LEGACY_JOURNAL%
    echo [local-master] Keep only journal\Trading Journal.xlsx in the journal folder. Move backups outside journal\ or rename them so they do not end in .xlsx/.xls/.xlsm.
    exit /b 1
  )
) else (
  if exist "%LEGACY_JOURNAL%" (
    move /Y "%LEGACY_JOURNAL%" "%CANONICAL_JOURNAL%" >nul
    if errorlevel 1 (
      echo [local-master] ERROR: failed migrating legacy workbook to Trading Journal.xlsx
      exit /b 1
    )
    echo [local-master] Migrated legacy workbook: Master Journal.xlsx ^> Trading Journal.xlsx
  ) else (
    echo [local-master] ERROR: required workbook missing: %CANONICAL_JOURNAL%
    echo [local-master] Restore journal\Trading Journal.xlsx before launching Local Trading Tools.
    exit /b 1
  )
)
set "CASHFLOW_CACHE_TTL_SECONDS=3600"
if not defined DROPBOX_SYNC_ENABLED set "DROPBOX_SYNC_ENABLED=0"
if not defined DROPBOX_BACKUP_PATH set "DROPBOX_BACKUP_PATH=/codex/master_control_backup.json"
if not defined STATE_BACKUP_LOCAL_PATH set "STATE_BACKUP_LOCAL_PATH=%ROOT%state_backup.json"
if not defined BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH set "BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH=%ROOT%journal\5-digit-demo-calculation-context.json"
if not defined DROPBOX_STATE_ROOT set "DROPBOX_STATE_ROOT=/codex/tradingtools_state"
if not defined LOCAL_STATE_ONLY set "LOCAL_STATE_ONLY=1"
if not defined TRADING_JOURNAL_GITHUB_SYNC_ENABLED set "TRADING_JOURNAL_GITHUB_SYNC_ENABLED=1"
if not defined TRADING_JOURNAL_GITHUB_SYNC_REMOTE set "TRADING_JOURNAL_GITHUB_SYNC_REMOTE=origin"
if not defined TRADING_JOURNAL_GITHUB_SYNC_INCLUDE_SOURCES set "TRADING_JOURNAL_GITHUB_SYNC_INCLUDE_SOURCES=0"

set "PYTHON_EXE=python"
if defined PYTHON set "PYTHON_EXE=%PYTHON%"

if not exist "%MASTER_ENV_FILE%" (
  echo [local-master] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  echo [local-master] Copy your env file from C:\Users\User\Documents\GPT\env.env to C:\GPT\env.env, then rerun this launcher.
  exit /b 1
)

call :load_master_env_vars

if /I "%~1"=="__worker" goto worker

echo [local-master] launcher starting.
set "MASTER_URL=http://127.0.0.1:8000"
set "MASTER_HEALTH_URL=http://127.0.0.1:8000/health"
set "MASTER_SCRIPTS_URL=http://127.0.0.1:8000/scripts"
for /f %%I in ('powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()"') do set "LOCAL_LAUNCH_TS=%%I"
set "MASTER_BROWSER_URL=%MASTER_URL%/?local_launch=%LOCAL_LAUNCH_TS%"
for /f %%I in ('powershell -NoProfile -Command "try { $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0); $l.Start(); $p = $l.LocalEndpoint.Port; $l.Stop(); if($p -gt 0){Write-Output $p; exit 0}; exit 1 } catch { exit 1 }"') do set "LOCAL_MASTER_EDGE_DEBUG_PORT=%%I"
if not defined LOCAL_MASTER_EDGE_DEBUG_PORT (
  echo [local-master] ERROR: failed to allocate LOCAL_MASTER_EDGE_DEBUG_PORT.
  exit /b 1
)
set "LOCAL_MASTER_EDGE_PROFILE_DIR=%TEMP%\LocalTradingToolsEdge-%LOCAL_LAUNCH_TS%"
set "LOCAL_MASTER_EXIT_REQUEST=%TEMP%\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.flag"
set "LOCAL_MASTER_NORMAL_EXIT_FILE=%TEMP%\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.normal"
set "LOCAL_MASTER_WORKER_FAILED_FILE=%TEMP%\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.failed"
set "LOCAL_MASTER_PREFLIGHT_DECISION=%TEMP%\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.preflight"
set "LOCAL_MASTER_WINDOW_TITLE=Local Master Control - %LOCAL_LAUNCH_TS%"
if exist "%LOCAL_MASTER_EXIT_REQUEST%" del /q "%LOCAL_MASTER_EXIT_REQUEST%" >nul 2>nul
if exist "%LOCAL_MASTER_NORMAL_EXIT_FILE%" del /q "%LOCAL_MASTER_NORMAL_EXIT_FILE%" >nul 2>nul
if exist "%LOCAL_MASTER_WORKER_FAILED_FILE%" del /q "%LOCAL_MASTER_WORKER_FAILED_FILE%" >nul 2>nul
if exist "%LOCAL_MASTER_PREFLIGHT_DECISION%" del /q "%LOCAL_MASTER_PREFLIGHT_DECISION%" >nul 2>nul
echo [local-master] worker log: %LOCAL_MASTER_WORKER_LOG%
start "%LOCAL_MASTER_WINDOW_TITLE%" /D "%ROOT%" "%ROOT%tools\windows_launchers\local_master_worker_console.bat"
set "PREFLIGHT_READY_TIMEOUT_SECONDS=45"
echo [local-master] waiting for launcher preflight to finish ...
set /a PREFLIGHT_WAITED=0

:wait_for_launcher_preflight
if exist "%LOCAL_MASTER_PREFLIGHT_DECISION%" goto launcher_preflight_ready
if defined LOCAL_MASTER_WORKER_FAILED_FILE (
  if exist "%LOCAL_MASTER_WORKER_FAILED_FILE%" goto worker_failed_before_ready
)
set /a PREFLIGHT_WAITED+=1
if !PREFLIGHT_WAITED! GEQ %PREFLIGHT_READY_TIMEOUT_SECONDS% goto launcher_preflight_not_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 1" >nul 2>nul
goto wait_for_launcher_preflight

:launcher_preflight_ready
set "PREFLIGHT_DECISION="
set /p PREFLIGHT_DECISION=<"%LOCAL_MASTER_PREFLIGHT_DECISION%"
if /I not "!PREFLIGHT_DECISION!"=="start" goto launcher_preflight_not_ready
echo [local-master] launcher preflight ready after !PREFLIGHT_WAITED! seconds.
set "MASTER_READY_TIMEOUT_SECONDS=60"
set "SCANNER_READY_TIMEOUT_SECONDS=90"
echo [local-master] waiting for %MASTER_HEALTH_URL% ...
set /a READY_WAITED=0

:wait_for_master_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%MASTER_HEALTH_URL%' -TimeoutSec 1; if ($r.StatusCode -eq 200 -and (($r.Content | Out-String).Trim() -eq 'ok')) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto master_ready

if defined LOCAL_MASTER_WORKER_FAILED_FILE (
  if exist "%LOCAL_MASTER_WORKER_FAILED_FILE%" goto worker_failed_before_ready
)
set /a READY_WAITED+=1
if !READY_WAITED! GEQ %MASTER_READY_TIMEOUT_SECONDS% goto master_not_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 1" >nul 2>nul
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
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 1" >nul 2>nul
goto wait_for_scanner_ready

:scanner_ready
echo [local-master] scanner ready after !SCANNER_READY_WAITED! seconds.
call "%ROOT%tools\open_edge_url.bat" "%MASTER_BROWSER_URL%" "%LOCAL_MASTER_EDGE_DEBUG_PORT%" "%LOCAL_MASTER_EDGE_PROFILE_DIR%"
if errorlevel 1 (
  echo [local-master] ERROR: failed to open Microsoft Edge for %MASTER_BROWSER_URL%.
  exit /b 1
)
echo Local master control launch requested with scanner autostart supervision.
exit /b 0

:master_not_ready
echo [local-master] ERROR: dashboard was not ready after %MASTER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Check worker startup log: %LOCAL_MASTER_WORKER_LOG%
echo [local-master] Browser was not opened to avoid a dead-page / manual-refresh failure.
exit /b 1

:worker_failed_before_ready
echo [local-master] ERROR: Worker exited before dashboard became ready.
echo [local-master] Worker startup log: %LOCAL_MASTER_WORKER_LOG%
if defined LOCAL_MASTER_WORKER_FAILED_FILE (
  if exist "%LOCAL_MASTER_WORKER_FAILED_FILE%" type "%LOCAL_MASTER_WORKER_FAILED_FILE%"
)
echo [local-master] Browser was not opened because the worker is no longer running.
exit /b 1

:scanner_not_ready
echo [local-master] ERROR: scanner did not become ready after %SCANNER_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Alerts startup may have failed. Check worker startup log: %LOCAL_MASTER_WORKER_LOG%
echo [local-master] Browser was not opened to avoid showing a misleading dashboard state.
exit /b 1

:launcher_preflight_not_ready
echo [local-master] ERROR: launcher preflight did not finish cleanly after %PREFLIGHT_READY_TIMEOUT_SECONDS% seconds.
echo [local-master] Check the visible Local Master Control window and worker log: %LOCAL_MASTER_WORKER_LOG%
echo [local-master] Browser was not opened because an old dashboard server may still be shutting down.
exit /b 1

:worker
if defined LOCAL_MASTER_WINDOW_TITLE (
  if /I not "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" title !LOCAL_MASTER_WINDOW_TITLE!
)
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
echo [local-master] STATE_BACKUP_LOCAL_PATH=!STATE_BACKUP_LOCAL_PATH!
echo [local-master] BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH=!BYBIT_DEMO_CALC_CONTEXT_LOCAL_PATH!
echo [local-master] User state source: Repo local files
if /I "!LOCAL_STATE_ONLY!"=="1" echo [local-master] Repo-local state enabled. Ensure Git sync succeeds before replacing the repo clone.

if /I not "!SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL!"=="1" (
  if exist "!ROOT!spreads-clone\requirements.txt" (
    echo [local-master] ensuring Spread Monitor Python requirements with !PYTHON_EXE! ...
    "!PYTHON_EXE!" -m pip install -r "!ROOT!spreads-clone\requirements.txt"
    if errorlevel 1 (
      echo [local-master] WARNING: Spread Monitor requirements install failed. Pepperstone/MT5 may show unavailable until installed.
    )
  )
)

:restart_master
if not defined LOCAL_MASTER_UVICORN_GENERATION set "LOCAL_MASTER_UVICORN_GENERATION=0"
set /a LOCAL_MASTER_UVICORN_GENERATION+=1
echo [local-master] starting uvicorn at !DATE! !TIME!
echo [local-master] uvicorn restart generation !LOCAL_MASTER_UVICORN_GENERATION!
"%PYTHON_EXE%" -m uvicorn render.master_service:app --host 127.0.0.1 --port 8000 --log-config "%ROOT%render\local_uvicorn_log_config.json"
set "EXIT_CODE=!ERRORLEVEL!"
echo [local-master] uvicorn exited with !EXIT_CODE! at !DATE! !TIME!
if defined LOCAL_MASTER_EXIT_REQUEST (
  if exist "!LOCAL_MASTER_EXIT_REQUEST!" (
    echo [local-master] local exit requested; closing worker window.
    call :write_normal_exit_marker
    del /q "!LOCAL_MASTER_EXIT_REQUEST!" >nul 2>nul
    if defined LOCAL_MASTER_EDGE_PROFILE_DIR (
      if exist "!LOCAL_MASTER_EDGE_PROFILE_DIR!\" rmdir /s /q "!LOCAL_MASTER_EDGE_PROFILE_DIR!" >nul 2>nul
    )
    if /I "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" (
      echo [local-master] smoke/test mode: not closing shared console window.
      exit 0
    )
    echo [local-master] closing Local Master Control command prompt.
    set "LOCAL_MASTER_SHUTDOWN_PS1=%TEMP%\local_master_shutdown_!RANDOM!_!RANDOM!.ps1"
    > "!LOCAL_MASTER_SHUTDOWN_PS1!" (
      echo $title = $env:LOCAL_MASTER_WINDOW_TITLE
      echo Start-Sleep -Milliseconds 750
      echo if ^([string]::IsNullOrWhiteSpace^($title^)^) { exit 0 }
      echo $allow = @^('WindowsTerminal','wt','OpenConsole','conhost','cmd'^)
      echo Get-Process ^| Where-Object { $_.MainWindowTitle -eq $title -and $allow -contains $_.ProcessName } ^| ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
      echo Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    )
    start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "!LOCAL_MASTER_SHUTDOWN_PS1!"
    exit 0
  )
)
echo [local-master] restarting in 3 seconds. Close this window to stop local master.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 3" >nul 2>nul
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

:write_normal_exit_marker
if not defined LOCAL_MASTER_NORMAL_EXIT_FILE goto :eof
set "LOCAL_MASTER_NORMAL_EXIT_TMP=!LOCAL_MASTER_NORMAL_EXIT_FILE!.tmp"
> "!LOCAL_MASTER_NORMAL_EXIT_TMP!" echo {"reason":"batch_exit_request","timestamp":"!DATE! !TIME!","server_pid":"","requesting_action":"batch_post_uvicorn"}
move /Y "!LOCAL_MASTER_NORMAL_EXIT_TMP!" "!LOCAL_MASTER_NORMAL_EXIT_FILE!" >nul 2>nul
goto :eof
