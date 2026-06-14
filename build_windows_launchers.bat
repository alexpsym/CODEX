@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_launchers\build_windows_launchers.ps1"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Failed to build Windows launchers. Exit code: %EXITCODE%
    if not defined CODEX_INSTALLER_NONINTERACTIVE pause
)
exit /b %EXITCODE%
