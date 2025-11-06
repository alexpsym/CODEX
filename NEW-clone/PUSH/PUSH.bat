@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist "PUSH.py" (
    echo [ERROR] Could not find PUSH.py in %~dp0
    echo Ensure the script is in the same folder as this batch file.
    echo.
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not available in PATH.
    echo Install Python and make sure it is accessible before running this script.
    echo.
    pause
    exit /b 1
)

echo Launching PUSH.py ...
python "PUSH.py"
set "exit_code=%errorlevel%"

echo.
if not "%exit_code%"=="0" (
    echo Script finished with errors (exit code %exit_code%).
) else (
    echo Script completed successfully.
)

echo.
pause
exit /b %exit_code%
