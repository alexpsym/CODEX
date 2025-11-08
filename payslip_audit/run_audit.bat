@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo Failed to change directory to %SCRIPT_DIR%.
    pause
    endlocal & exit /b 1
)

set "PAYSLIP="
for %%f in (*.pdf) do (
    set "PAYSLIP=%%~ff"
    goto :found_payslip
)

echo No payslip PDF found alongside %~nx0.
popd
pause
endlocal & exit /b 1

:found_payslip
set "TIMESHEETS="
for %%f in (*.jpg *.jpeg *.png) do (
    call set "TIMESHEETS=%%TIMESHEETS%% ^"%%~ff^""
)

if not defined TIMESHEETS (
    echo No timesheet images (.jpg, .jpeg, .png) found in %SCRIPT_DIR%.
    popd
    pause
    endlocal & exit /b 1
)

echo Running audit with:
echo   Payslip   : %PAYSLIP%
echo   Timesheets: %TIMESHEETS%
echo.

python "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet %TIMESHEETS% --output "%SCRIPT_DIR%audit_report.pdf"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Audit completed successfully.
) else (
    echo Audit failed with exit code %EXIT_CODE%.
)

popd
pause
endlocal & exit /b %EXIT_CODE%
