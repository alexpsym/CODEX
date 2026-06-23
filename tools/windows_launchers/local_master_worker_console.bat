@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "WRAPPER_DIR=%~dp0"
for %%I in ("%WRAPPER_DIR%..\..\") do set "ROOT=%%~fI"
if not "%ROOT:~-1%"=="\" set "ROOT=%ROOT%\"

set "LOG_DIR=%ROOT%logs"
if not exist "%LOG_DIR%\" mkdir "%LOG_DIR%" >nul 2>nul
if not defined LOCAL_MASTER_WORKER_LOG set "LOCAL_MASTER_WORKER_LOG=%LOG_DIR%\LocalTradingTools-worker-latest.log"
if not defined LOCAL_MASTER_WINDOW_TITLE set "LOCAL_MASTER_WINDOW_TITLE=Local Master Control"
if defined LOCAL_MASTER_WINDOW_TITLE title !LOCAL_MASTER_WINDOW_TITLE!

echo [local-master] visible worker console started.
echo [local-master] worker output is being written to:
echo [local-master]   !LOCAL_MASTER_WORKER_LOG!
echo [local-master] If startup fails, this window will stay open and print the latest log lines.

call "%ROOT%run_local_master_control.bat" __worker > "%LOCAL_MASTER_WORKER_LOG%" 2>&1
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
