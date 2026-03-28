@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "payslip_timesheet_audit.py" %*
) else (
    python "payslip_timesheet_audit.py" %*
)

set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Audit failed with exit code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
