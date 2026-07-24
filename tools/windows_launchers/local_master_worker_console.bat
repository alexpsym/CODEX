@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "WRAPPER_DIR=%~dp0"
for %%I in ("%WRAPPER_DIR%..\..\") do set "ROOT=%%~fI"
if not "%ROOT:~-1%"=="\" set "ROOT=%ROOT%\"

set "LOG_DIR=%ROOT%logs"
if not exist "%LOG_DIR%\" mkdir "%LOG_DIR%" >nul 2>nul
if not defined LOCAL_MASTER_WORKER_LOG set "LOCAL_MASTER_WORKER_LOG=%LOG_DIR%\LocalTradingTools-worker-latest.log"
if not defined LOCAL_MASTER_WINDOW_TITLE set "LOCAL_MASTER_WINDOW_TITLE=Local Master Control"
if defined LOCAL_MASTER_WINDOW_TITLE (
  if /I "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" (
    echo [local-master] smoke/test mode: not changing shared console title.
  ) else (
    title !LOCAL_MASTER_WINDOW_TITLE!
  )
)

echo [local-master] visible worker console started.
echo [local-master] worker output will print live below and is also being written to:
echo [local-master]   !LOCAL_MASTER_WORKER_LOG!
echo [local-master] Startup progress follows dashboard health and required core state; background services report separately.
echo [local-master] If startup fails, this window will stay open and print the latest log lines.

set "PREFLIGHT_ROOT=!ROOT!"
if "!PREFLIGHT_ROOT:~-1!"=="\" set "PREFLIGHT_ROOT=!PREFLIGHT_ROOT:~0,-1!"
echo [local-master] checking for an existing dashboard server on port 8000 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\windows_launchers\ensure_local_master_server.ps1" -Root "!PREFLIGHT_ROOT!" -DecisionPath "!LOCAL_MASTER_PREFLIGHT_DECISION!" -BaseUrl "http://127.0.0.1:8000" -HealthUrl "http://127.0.0.1:8000/health"
if errorlevel 1 goto preflight_failed

set "STREAM_ROOT=!ROOT!"
if "!STREAM_ROOT:~-1!"=="\" set "STREAM_ROOT=!STREAM_ROOT:~0,-1!"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\windows_launchers\stream_local_master_worker.ps1" -Root "!STREAM_ROOT!" -WorkerLog "!LOCAL_MASTER_WORKER_LOG!"
set "WORKER_EXIT_CODE=!ERRORLEVEL!"

set "LOCAL_MASTER_STARTED=0"
if exist "!LOCAL_MASTER_WORKER_LOG!" (
  findstr /C:"Application startup complete" "!LOCAL_MASTER_WORKER_LOG!" >nul 2>nul && set "LOCAL_MASTER_STARTED=1"
)

if defined LOCAL_MASTER_NORMAL_EXIT_FILE (
  if exist "!LOCAL_MASTER_NORMAL_EXIT_FILE!" (
    echo.
    findstr /I /C:"launcher_preflight" /C:"replacement" "!LOCAL_MASTER_NORMAL_EXIT_FILE!" >nul 2>nul
    if not errorlevel 1 (
      echo [local-master] Controlled replacement by a new launcher completed.
    ) else (
      findstr /I /C:"exit_button" /C:"local_exit" "!LOCAL_MASTER_NORMAL_EXIT_FILE!" >nul 2>nul
      if not errorlevel 1 (
        echo [local-master] Controlled Exit-button shutdown completed.
      ) else (
        echo [local-master] Controlled local shutdown completed.
      )
    )
    echo [local-master] Normal-exit marker:
    type "!LOCAL_MASTER_NORMAL_EXIT_FILE!" 2>nul
    del /q "!LOCAL_MASTER_NORMAL_EXIT_FILE!" >nul 2>nul
    if /I "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" exit /b 0
    exit
  )
)

if defined LOCAL_MASTER_WORKER_FAILED_FILE if "!LOCAL_MASTER_STARTED!"=="0" (
  > "!LOCAL_MASTER_WORKER_FAILED_FILE!" echo Worker exited before dashboard became ready with exit code !WORKER_EXIT_CODE! at !DATE! !TIME!
)

echo.
if "!WORKER_EXIT_CODE!"=="0" (
  if "!LOCAL_MASTER_STARTED!"=="1" (
    echo [local-master] Worker exited after dashboard startup without a normal-exit marker.
  ) else (
    echo [local-master] Worker exited before a normal app Exit request.
  )
) else (
  if "!LOCAL_MASTER_STARTED!"=="1" (
    echo [local-master] Unexpected runtime worker exit with exit code !WORKER_EXIT_CODE!.
  ) else (
    echo [local-master] Startup failure: worker failed with exit code !WORKER_EXIT_CODE!.
  )
)
if "!LOCAL_MASTER_STARTED!"=="1" (
  echo [local-master] Runtime exit log:
) else (
  echo [local-master] Startup error log:
)
echo [local-master]   !LOCAL_MASTER_WORKER_LOG!
echo [local-master] Last worker log lines:
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Get-Content -LiteralPath $env:LOCAL_MASTER_WORKER_LOG -Tail 40 -ErrorAction Stop } catch { Write-Host $_.Exception.Message }"
echo.
if "!LOCAL_MASTER_STARTED!"=="1" (
  echo [local-master] This window is intentionally left open so runtime exits stay readable.
) else (
  echo [local-master] This window is intentionally left open so startup errors stay readable.
)
echo [local-master] Press any key to close this window.
pause >nul
exit !WORKER_EXIT_CODE!

:preflight_failed
if defined LOCAL_MASTER_WORKER_FAILED_FILE (
  > "!LOCAL_MASTER_WORKER_FAILED_FILE!" echo Launcher preflight failed before dashboard worker started at !DATE! !TIME!
)
echo.
echo [local-master] Launcher preflight failed before the dashboard worker started.
echo [local-master] Close any old Local Master Control window, then run Local Trading Tools again.
echo [local-master] This window is intentionally left open so the failure stays readable.
echo [local-master] Press any key to close this window.
pause >nul
exit 1
