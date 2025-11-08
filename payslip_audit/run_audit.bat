@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"

set "PAYSLIP="
for %%f in ("%SCRIPT_DIR%*.pdf") do (
    set "PAYSLIP=%%~ff"
    goto :found_payslip
)

echo No payslip PDF found alongside %~nx0.
exit /b 1

:found_payslip
set "TIMESHEETS="
for %%f in ("%SCRIPT_DIR%*.jpg" "%SCRIPT_DIR%*.jpeg" "%SCRIPT_DIR%*.png") do (
    if exist "%%~ff" (
        set "TIMESHEETS=!TIMESHEETS! ^"%%~ff^""
    )
)

if not defined TIMESHEETS (
    echo No timesheet images (.jpg, .jpeg, .png) found in %SCRIPT_DIR%.
    exit /b 1
)

python "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet!TIMESHEETS! --output "%SCRIPT_DIR%audit_report.pdf"
endlocal
