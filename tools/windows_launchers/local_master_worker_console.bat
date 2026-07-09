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
echo [local-master] Startup progress will update while dashboard health and scanner readiness are checked.
echo [local-master] If startup fails, this window will stay open and print the latest log lines.

set "PREFLIGHT_ROOT=!ROOT!"
if "!PREFLIGHT_ROOT:~-1!"=="\" set "PREFLIGHT_ROOT=!PREFLIGHT_ROOT:~0,-1!"
echo [local-master] checking for an existing dashboard server on port 8000 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\windows_launchers\ensure_local_master_server.ps1" -Root "!PREFLIGHT_ROOT!" -BaseUrl "http://127.0.0.1:8000" -HealthUrl "http://127.0.0.1:8000/health"
if errorlevel 1 goto preflight_failed

set "STREAM_ROOT=!ROOT!"
if "!STREAM_ROOT:~-1!"=="\" set "STREAM_ROOT=!STREAM_ROOT:~0,-1!"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\windows_launchers\stream_local_master_worker.ps1" -Root "!STREAM_ROOT!" -WorkerLog "!LOCAL_MASTER_WORKER_LOG!"
set "WORKER_EXIT_CODE=!ERRORLEVEL!"

if defined LOCAL_MASTER_NORMAL_EXIT_FILE (
  if exist "!LOCAL_MASTER_NORMAL_EXIT_FILE!" (
    del /q "!LOCAL_MASTER_NORMAL_EXIT_FILE!" >nul 2>nul
    exit
  )
)

if defined LOCAL_MASTER_WORKER_FAILED_FILE (
  > "!LOCAL_MASTER_WORKER_FAILED_FILE!" echo Worker exited before dashboard became ready with exit code !WORKER_EXIT_CODE! at !DATE! !TIME!
)

echo.
if "!WORKER_EXIT_CODE!"=="0" (
  echo [local-master] Worker exited before a normal app Exit request.
) else (
  echo [local-master] Worker failed with exit code !WORKER_EXIT_CODE!.
)
echo [local-master] Startup error log:
echo [local-master]   !LOCAL_MASTER_WORKER_LOG!
echo [local-master] Last worker log lines:
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Get-Content -LiteralPath $env:LOCAL_MASTER_WORKER_LOG -Tail 40 -ErrorAction Stop } catch { Write-Host $_.Exception.Message }"
echo.
echo [local-master] This window is intentionally left open so startup errors stay readable.
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
