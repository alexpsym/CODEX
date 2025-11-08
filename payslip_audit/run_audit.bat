@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"

set "PAYSLIP="
for %%f in ("%SCRIPT_DIR%*.pdf") do (
    set "PAYSLIP=%%~ff"
    goto :found_payslip
)

echo No payslip PDF found alongside %~nx0.
pause
endlocal & exit /b 1

:found_payslip
set "TIMESHEETS="
for %%f in ("%SCRIPT_DIR%*.jpg" "%SCRIPT_DIR%*.jpeg" "%SCRIPT_DIR%*.png") do (
    if exist "%%~ff" (
        set "TIMESHEETS=!TIMESHEETS! ^"%%~ff^""
    )
)

if not defined TIMESHEETS (
    echo No timesheet images (.jpg, .jpeg, .png) found in %SCRIPT_DIR%.
    pause
    endlocal & exit /b 1
)

echo Running audit with:
echo   Payslip   : %PAYSLIP%
echo   Timesheets: %TIMESHEETS%
echo.

python "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet !TIMESHEETS! --output "%SCRIPT_DIR%audit_report.pdf"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if %EXIT_CODE% equ 0 (
    echo Audit completed successfully.
) else (
    echo Audit failed with exit code %EXIT_CODE%.
)

pause
endlocal & exit /b %EXIT_CODE%
